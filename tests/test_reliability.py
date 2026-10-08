import ast, asyncio, contextlib, importlib.util, pathlib, sys, tempfile, threading, types, unittest
from unittest.mock import patch, MagicMock
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import bridge_coordination as coord
from eda_agent.bridge.process_manager import AltiumProcessManager
from eda_agent.ui import windows

class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root=pathlib.Path(self.tmp.name); work=root/'workspace';work.mkdir()
        script=root/'Altium_API.PrjScr';script.write_text('script'); exe=root/'X2.exe';exe.write_text('fake')
        for key,value in {'ROOT':root,'RUNTIME':root,'WORKSPACE':work,'LAUNCH_STATE':root/'state.json','SCRIPT':script,'EXE':exe}.items():
            p=patch.object(coord,key,value);p.start();self.addCleanup(p.stop)
        p=patch.object(coord,'_editor_state',return_value={'pid':77,'blocked':False});self.state=p.start();self.addCleanup(p.stop)
        p=patch.object(coord.subprocess,'Popen');self.launch=p.start();self.launch.return_value.pid=123;self.addCleanup(p.stop)
        p=patch.object(coord.time,'sleep');p.start();self.addCleanup(p.stop)
        p=patch.object(coord,'_session_started_at',return_value=0);p.start();self.addCleanup(p.stop)
    def test_healthy_loop_is_not_relaunched(self):
        probe=MagicMock(return_value={'pong':True});coord._ensure(probe)
        probe.assert_called_once_with(2.0);self.launch.assert_not_called()
    def test_one_slow_probe_does_not_spawn_second_altium(self):
        probe=MagicMock(side_effect=[TimeoutError(),{'pong':True}]);coord._ensure(probe)
        self.assertEqual(probe.call_count,2);self.launch.assert_not_called()
    def test_modal_is_not_mistaken_for_dead_loop(self):
        self.state.return_value={'pid':77,'blocked':True};probe=MagicMock()
        with self.assertRaisesRegex(RuntimeError,'modal'):coord._ensure(probe)
        probe.assert_not_called();self.launch.assert_not_called()
    def test_live_handler_is_not_interrupted(self):
        (coord.WORKSPACE/'progress_live.json').write_text('{}');probe=MagicMock()
        with self.assertRaisesRegex(RuntimeError,'No new command'):
            coord._ensure(probe)
        probe.assert_not_called();self.launch.assert_not_called()
    def test_no_editor_never_launches_an_unrelated_instance(self):
        self.state.side_effect=RuntimeError('Expected one Altium editor');probe=MagicMock()
        with self.assertRaises(RuntimeError):coord._ensure(probe)
        self.launch.assert_not_called()
    def test_dead_loop_is_started_once_and_confirmed(self):
        probe=MagicMock(side_effect=[TimeoutError(),TimeoutError(),{'pong':True}]);coord._ensure(probe)
        self.launch.assert_called_once();self.assertEqual(probe.call_count,3)
        self.assertFalse(coord.LAUNCH_STATE.exists())
    def test_failed_launch_cooldown_prevents_duplicate_process(self):
        import json,time
        coord.LAUNCH_STATE.write_text(json.dumps({'attempted_at':time.time()}))
        with self.assertRaisesRegex(TimeoutError,'already attempted'):
            coord._ensure(MagicMock(side_effect=TimeoutError()))
        self.launch.assert_not_called()
    def test_legacy_handover_refuses_running_handler(self):
        (coord.WORKSPACE/'progress_live.json').write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'handler'):coord.stop_eda()
        self.assertFalse((coord.WORKSPACE/'stop').exists())
    def test_stopped_loop_does_not_leave_stop_sentinel(self):
        with patch.object(coord,'_file_ping',side_effect=TimeoutError()):coord.stop_eda()
        self.assertFalse((coord.WORKSPACE/'stop').exists())

def load_function(path,name,namespace):
    tree=ast.parse(path.read_text())
    node=next(n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),namespace)
    return namespace[name]

class WrapperTests(unittest.TestCase):
    def namespace(self):
        return {'threading':threading,'engine_lock':contextlib.nullcontext,'ensure_eda':MagicMock(),'original':MagicMock(return_value={'ok':True})}
    def test_user_command_is_dispatched_once_after_health_check(self):
        n=self.namespace();fn=load_function(ROOT/'eda_stdio.py','coordinated',n)
        fn(object(),'generic.modify_objects',{},10)
        n['ensure_eda'].assert_called_once();n['original'].assert_called_once()
    def test_mutation_timeout_is_never_replayed(self):
        n=self.namespace();n['original'].side_effect=TimeoutError();fn=load_function(ROOT/'eda_stdio.py','coordinated',n)
        with self.assertRaises(TimeoutError):fn(object(),'generic.modify_objects',{},10)
        n['original'].assert_called_once()
    def test_keepalive_never_resurrects_detached_loop(self):
        n=self.namespace();fn=load_function(ROOT/'eda_stdio.py','coordinated',n)
        with patch.object(threading,'current_thread',return_value=types.SimpleNamespace(name='altium-keepalive')):
            fn(object(),'application.ping',{},5)
        n['ensure_eda'].assert_not_called();n['original'].assert_called_once()
    def test_stop_does_not_restart_a_stopped_bridge(self):
        n=self.namespace();fn=load_function(ROOT/'eda_stdio.py','coordinated',n)
        fn(object(),'application.stop_server',{},10);n['ensure_eda'].assert_not_called()
    def test_legacy_handover_restores_engine_even_when_tool_fails(self):
        events=[]
        @contextlib.asynccontextmanager
        async def lock():yield
        async def original(*a,**kw):events.append('tool');raise ValueError('tool failure')
        n={'OFFLINE':{'get_server_status'},'original':original,'async_engine_lock':lock,'asyncio':asyncio,
           'stop_eda':lambda:events.append('stop'),'start_eda':lambda:events.append('restore'),'log_event':MagicMock(),'sys':sys}
        fn=load_function(ROOT/'coffeenmusic/server/codex_stdio.py','coordinated',n)
        with self.assertRaises(ValueError):asyncio.run(fn('get_schematic_data',{}))
        self.assertEqual(events,['stop','tool','restore'])
    def test_offline_legacy_status_does_not_stop_engine(self):
        @contextlib.asynccontextmanager
        async def lock():yield
        async def original(*a,**kw):return {'ok':True}
        n={'OFFLINE':{'get_server_status'},'original':original,'async_engine_lock':lock,'asyncio':asyncio,
           'stop_eda':MagicMock(),'start_eda':MagicMock(),'log_event':MagicMock(),'sys':sys}
        fn=load_function(ROOT/'coffeenmusic/server/codex_stdio.py','coordinated',n)
        asyncio.run(fn('get_server_status',{}));n['stop_eda'].assert_not_called()

class ProcessTests(unittest.TestCase):
    def test_editor_is_selected_instead_of_earlier_headless_launcher(self):
        import win32gui,win32process
        def enumerate_windows(callback,arg):callback(456,arg)
        with patch.object(win32gui,'EnumWindows',side_effect=enumerate_windows),patch.object(win32gui,'IsWindowVisible',return_value=True),patch.object(win32gui,'GetClassName',return_value='TDocumentForm'),patch.object(win32gui,'GetForegroundWindow',return_value=456),patch.object(win32process,'GetWindowThreadProcessId',return_value=(1,7668)):
            self.assertEqual(AltiumProcessManager()._choose_editor_pid([7280,7668,14524]),7668)
    def test_headless_launcher_alone_is_not_a_targetable_editor(self):
        import win32gui
        with patch.object(win32gui,'EnumWindows',return_value=None),patch.object(win32gui,'GetForegroundWindow',return_value=0):
            self.assertIsNone(AltiumProcessManager()._choose_editor_pid([7280]))

class MousePrimitiveTests(unittest.TestCase):
    def test_ctypes_imports_are_defined_for_all_mouse_paths(self):
        self.assertTrue(hasattr(windows,'wintypes'));self.assertTrue(hasattr(windows,'byref'))
    def test_click_and_drag_use_mocked_input_without_name_error(self):
        import ctypes
        user32=MagicMock();user32.GetSystemMetrics.return_value=1920
        with patch.object(ctypes.windll,'user32',user32),patch.object(windows,'_require'),patch.object(windows,'_gate'),patch.object(windows.time,'sleep'):
            # Mocked Win32 input only; no event is sent to the real desktop.
            self.assertTrue(windows.click_at(1,2)['ok'])
            self.assertTrue(windows.drag(1,2,3,4,steps=2,hold=0)['ok'])
            self.assertGreater(user32.mouse_event.call_count,0)

class PascalTests(unittest.TestCase):
    def test_close_monitor_hides_form_and_keeps_loop_running(self):
        text=(ROOT/'eda-agent/scripts/altium/StatusForm.pas').read_text()
        block=text.split('Procedure StatusFormClose',1)[1].split('End;',1)[0]
        self.assertIn('Action := caHide;',block);self.assertNotIn('Running := False',block)
    def test_explicit_detach_still_stops_loop(self):
        text=(ROOT/'eda-agent/scripts/altium/StatusForm.pas').read_text()
        block=text.split('Procedure btn_DetachClick',1)[1].split('End;',1)[0]
        self.assertIn('Running := False',block)
    def test_stop_reasons_are_logged(self):
        text=(ROOT/'eda-agent/scripts/altium/Dispatcher.pas').read_text()
        for reason in ['coordinator_stop_file','idle_timeout','dispatcher_exception','reason=']:
            self.assertIn(reason,text)

if __name__=='__main__':unittest.main(verbosity=2)
