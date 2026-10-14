"""Observed transfer failures: physical counts, guarded edits and honest UI evidence."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from eda_agent.core.backends import AltiumBackend, BackendUnavailableError
from eda_agent.tools import eda, pcb, generic, project, application, uiauto
from eda_agent.ui import eco_report, menu, windows, controls


def register(module):
    api = {}
    class MCP:
        def tool(self, *args, **kwargs):
            def add(fn):
                api[fn.__name__] = fn
                return fn
            return add
    getattr(module, 'register_' + module.__name__.split('.')[-1] + '_tools')(MCP())
    return api


@pytest.mark.asyncio
async def test_empty_physical_board_cannot_inherit_272_schematic_parts(monkeypatch):
    class Bridge:
        async def send_command_async(self, command, params):
            if command == 'project.get_bom':
                return {'components': [{'designator': 'U' + str(i)} for i in range(272)]}
            return {'data_domain': 'physical_pcb', 'board_path': 'empty.PcbDoc', 'counts': dict(components=0, nets=0, pads=0, tracks=0, vias=0)}
    backend = AltiumBackend()
    monkeypatch.setattr(backend, '_bridge', AsyncMock(return_value=(Bridge(), RuntimeError)))
    monkeypatch.setattr(eda, 'resolve_backend', lambda _: backend)
    result = await register(eda)['get_board_info']('altium')
    assert result['stats']['part_count'] == result['stats']['net_count'] == 0
    assert result['data_domain'] == 'physical_pcb' and result['eco_verified'] is False


@pytest.mark.asyncio
async def test_missing_native_counts_fail_without_bom_fallback(monkeypatch):
    backend = AltiumBackend()
    bridge = SimpleNamespace(send_command_async=AsyncMock(return_value={'board_path': 'b.PcbDoc', 'counts': {}}))
    monkeypatch.setattr(backend, '_bridge', AsyncMock(return_value=(bridge, RuntimeError)))
    with pytest.raises(BackendUnavailableError, match='incomplete'):
        await backend.board_info()
    assert bridge.send_command_async.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('domain,path', [('schematic','b.PcbDoc'), ('physical_pcb','b.PcbLib')])
async def test_wrong_native_domain_or_library_is_not_board_info(monkeypatch, domain, path):
    backend = AltiumBackend()
    bridge = SimpleNamespace(send_command_async=AsyncMock(return_value={
        'data_domain':domain, 'board_path':path,
        'counts':dict(components=272,nets=243,pads=0,tracks=0,vias=0)}))
    monkeypatch.setattr(backend, '_bridge', AsyncMock(return_value=(bridge, RuntimeError)))
    with pytest.raises(BackendUnavailableError):
        await backend.board_info()


@pytest.mark.asyncio
async def test_pad_zero_limit_explicit_board_and_truncation_are_preserved(monkeypatch):
    bridge = SimpleNamespace(send_command_async=AsyncMock(return_value={
        'board_path': 'empty.PcbDoc', 'pads': [], 'count': 0, 'matched': 0, 'truncated': False}))
    monkeypatch.setattr(pcb, 'get_bridge', lambda: bridge)
    api = register(pcb)
    result = await api['pcb_get_pad_connectivity']('empty.PcbDoc', limit=0)
    assert result['pads'] == [] and result['truncated'] is False
    assert bridge.send_command_async.await_args.args[1]['board_path'] == 'empty.PcbDoc'
    assert bridge.send_command_async.await_args.args[1]['limit'] == 0
    bridge.send_command_async.reset_mock()
    assert not (await api['pcb_get_pad_connectivity'](limit=-1))['ok']
    bridge.send_command_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_clearing_model_location_is_not_an_omitted_parameter(monkeypatch):
    bridge = SimpleNamespace(send_command_async=AsyncMock(return_value={'verified': True}))
    monkeypatch.setattr(generic, 'get_bridge', lambda: bridge)
    api = register(generic)
    await api['sch_set_placed_model_source']('scratch.SchDoc', 'UID', 'D_SMA', '', True)
    params = bridge.send_command_async.await_args.args[1]
    assert params['source_library'] == '' and params['replace_links'] is True
    bridge.send_command_async.reset_mock()
    assert not (await api['sch_set_placed_model_source']('scratch.SchDoc', 'UID', 'D_SMA', 'relative.PcbLib'))['ok']
    assert not (await api['obj_zoom']('fit-ish'))['ok']
    bridge.send_command_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_library_save_is_an_explicit_opt_in(monkeypatch):
    save = AsyncMock(return_value={'success': True})
    monkeypatch.setattr(project, 'get_bridge', lambda: object())
    monkeypatch.setattr(project, 'verified_save', save)
    api = register(project)
    await api['proj_save']('scratch.PrjPcb')
    assert save.await_args.kwargs['include_libraries'] is False
    await api['proj_save']('scratch.PrjPcb', include_libraries=True)
    assert save.await_args.kwargs['include_libraries'] is True


def test_blank_eco_provider_exports_exact_window_without_claiming_pass(tmp_path, monkeypatch):
    window = SimpleNamespace(title='Engineering Change Order', hwnd=44)
    monkeypatch.setattr(eco_report.windows, 'dialogs', lambda _: [window])
    monkeypatch.setattr(eco_report, 'read_tables', lambda _: {'tables': [], 'grid_read_complete': False})
    capture = MagicMock(return_value=True)
    monkeypatch.setattr(eco_report.ocr, 'capture_png', capture)
    path = tmp_path/'eco.json'
    result = eco_report.export(123, str(path), 'pre_execution')
    assert result['ok'] and not result['grid_read_complete'] and not result['eco_verified']
    assert capture.call_args.args[0] == 44
    assert 'blank nodes' in json.loads(path.read_text())['grid_warning']
    assert not eco_report.export(123, str(path))['ok']


def test_eco_export_refuses_main_window_and_ambiguous_dialogs(tmp_path, monkeypatch):
    monkeypatch.setattr(eco_report.windows, 'dialogs', lambda _: [SimpleNamespace(title='Altium Designer', hwnd=1)])
    assert not eco_report.export(123, str(tmp_path/'eco.json'))['ok']
    assert not list(tmp_path.iterdir())
    monkeypatch.setattr(eco_report.windows, 'dialogs', lambda _: [SimpleNamespace(title='Engineering Change Order', hwnd=i) for i in (1, 2)])
    assert not eco_report.export(123, str(tmp_path/'eco.json'))['ok']


def test_failed_eco_capture_leaves_no_partial_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(eco_report.windows, 'dialogs', lambda _: [SimpleNamespace(title='Engineering Change Order', hwnd=44)])
    monkeypatch.setattr(eco_report, 'read_tables', lambda _: {'tables': [], 'grid_read_complete': False})
    monkeypatch.setattr(eco_report.ocr, 'capture_png', MagicMock(side_effect=OSError('capture failed')))
    assert not eco_report.export(123, str(tmp_path/'eco.json'))['ok']
    assert not list(tmp_path.iterdir())


def test_zero_column_grid_is_incomplete_without_iterating_rows(monkeypatch):
    import sys
    pattern = SimpleNamespace(CurrentRowCount=10**9, CurrentColumnCount=0, GetItem=MagicMock())
    grid = MagicMock(); grid.GetCurrentPattern.return_value.QueryInterface.return_value = pattern
    grids = SimpleNamespace(Length=1, GetElement=lambda _: grid)
    automation = MagicMock(); automation.ElementFromHandle.return_value.FindAll.return_value = grids
    client = SimpleNamespace(GetModule=MagicMock(), CreateObject=lambda *a, **kw: automation)
    com = SimpleNamespace(client=client, CoInitialize=MagicMock(), CoUninitialize=MagicMock())
    uia = SimpleNamespace(CUIAutomation=object(), IUIAutomation=object(), IUIAutomationGridPattern=object())
    monkeypatch.setitem(sys.modules, 'comtypes', com)
    monkeypatch.setitem(sys.modules, 'comtypes.client', client)
    monkeypatch.setitem(sys.modules, 'comtypes.gen', SimpleNamespace(UIAutomationClient=uia))
    result = eco_report.read_tables(44)
    assert not result['grid_read_complete'] and 'dimensions' in result['tables'][0]['error']
    pattern.GetItem.assert_not_called()


@pytest.mark.asyncio
async def test_health_preserves_ownership_error_and_never_advises_second_launch(monkeypatch):
    bridge = SimpleNamespace(is_altium_running=lambda: True,
                             send_command_async=AsyncMock(side_effect=TimeoutError('shutdown is not confirmed')))
    monkeypatch.setattr(application, 'get_bridge', lambda: bridge)
    api = register(application)
    result = await api['app_ping']()
    assert not result['success'] and 'shutdown is not confirmed' in result['message']
    context = await api['app_context']()
    assert not context['bridge_answering'] and 'shutdown is not confirmed' in context['warnings'][0]
    assert 'do not start a second script' in context['next_step']
    assert 'Run Script' not in context['next_step']


@pytest.mark.parametrize('style', [0xC, 0xD, 0xE, 0xF])
def test_file_picker_split_and_command_buttons_remain_pressable(style):
    control = windows.Control(hwnd=1, class_name='Button', text='&Open', style=0x50000000|style)
    dialog = windows.Window(hwnd=2, class_name='#32770', title='Choose Document to Open', pid=3, controls=[control])
    assert dialog.find_button('Open') is control
    for toggle in (0x2, 0x3, 0x4, 0x5, 0x6, 0x7, 0x9):
        control.style = 0x50000000|toggle
        assert dialog.find_button('Open') is None


@pytest.mark.parametrize('role,readonly', [('statictext',False), ('combobox',False), ('text',True)])
def test_text_setter_refuses_labels_and_readonly_fields(monkeypatch, role, readonly):
    monkeypatch.setattr(controls,'read',lambda _:dict(ok=True,role=role,read_only=readonly,enabled=True))
    message=MagicMock()
    monkeypatch.setattr(controls,'win32gui',SimpleNamespace(SendMessage=message),raising=False)
    assert not controls.set_text(1,'new')['ok']
    message.assert_not_called()


@pytest.mark.asyncio
async def test_text_dialog_write_selects_the_edit_instead_of_its_label(monkeypatch):
    dialog=SimpleNamespace(title='Choose Document to Open')
    monkeypatch.setattr(controls,'available',lambda:True)
    monkeypatch.setattr(uiauto,'_altium_pid',lambda:(3,None))
    monkeypatch.setattr(windows,'dialogs',lambda _:[dialog])
    find=MagicMock(return_value={'hwnd':42})
    monkeypatch.setattr(controls,'find',find)
    write=MagicMock(return_value={'ok':True,'text':'path'})
    monkeypatch.setattr(controls,'set_text',write)
    result=await register(uiauto)['app_set_dialog_control']('File name:',text='path')
    assert result['ok']
    find.assert_called_once_with(dialog,'File name:',role='text')
    write.assert_called_once_with(42,'path')


@pytest.mark.asyncio
async def test_context_dirty_flags_do_not_claim_clean_or_advise_global_save(monkeypatch):
    bridge=SimpleNamespace(is_altium_running=lambda:True,send_command_async=AsyncMock(side_effect=[
        {'script_version':'test'}, {'file_path':'b.PcbDoc'},
        {'documents':[{'file_path':'inspected.PcbLib','modified':True}]}]))
    monkeypatch.setattr(application,'get_bridge',lambda:bridge)
    monkeypatch.setattr(application,'_bundled_script_version',lambda:'test')
    result=await register(application)['app_context']()
    assert result['unsaved']==['inspected.PcbLib'] and result['unsaved_detection_complete'] is False
    assert 'proj_save' in result['next_step'] and 'app_save_all' not in result['next_step']


@pytest.mark.parametrize('operation', ['click_path', 'list_path'])
def test_failed_worker_com_initialization_cannot_click(operation, monkeypatch):
    monkeypatch.setattr(menu, 'available', lambda: True)
    fake = SimpleNamespace(CoInitialize=MagicMock(side_effect=RuntimeError('COM refused')))
    monkeypatch.setattr(menu, 'comtypes', fake, raising=False)
    action = MagicMock()
    monkeypatch.setattr(menu, '_' + operation, action)
    result = getattr(menu, operation)(123, 'View|Fit Document')
    assert not result['ok'] and 'COM refused' in result['reason']
    action.assert_not_called()
