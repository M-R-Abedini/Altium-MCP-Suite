"""Offline protocol and behavioral compatibility with the MIT legacy API."""
import ast
import asyncio
import copy
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, Dict, Optional

from mcp import types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp import FastMCP, Context
import pytest

import suite_legacy_compat as legacy
import suite_script_overlay as overlay

ROOT = Path(__file__).resolve().parents[1]


def original_wrappers(bridge):
    """Read only the MIT decorators/functions; never import its retired runner."""
    namespace = dict(Context=Context, Dict=Dict, Any=Any, Optional=Optional,
        json=json, logger=legacy.logger, altium_bridge=bridge,
        MCP_DIR=legacy.MCP_DIR, Path=Path)
    tree = ast.parse((ROOT / 'coffeenmusic/server/main.py').read_text(encoding='utf-8'))
    server = FastMCP('original API contract')
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.decorator_list:
            node = copy.deepcopy(node)
            node.decorator_list = []
            exec(compile(ast.Module(body=[node], type_ignores=[]), '<MIT legacy wrapper>', 'exec'), namespace)
            server.add_tool(namespace[node.name])
    return server, namespace


def text_result(result):
    return json.loads(result.content[0].text)


def test_all_37_names_schemas_and_descriptions_match_offline_original():
    async def run():
        original, _ = original_wrappers(None)
        expected = [t.model_dump(mode='json', exclude_none=True) for t in await original.list_tools()]
        actual = [t.model_dump(mode='json', exclude_none=True) for t in await legacy.LegacyCompat(None).list_tools()]
        fixture = json.loads((ROOT / 'contracts/legacy-tools.json').read_text(encoding='utf-8'))['tools']
        assert len(actual) == 37
        assert actual == expected == fixture
    asyncio.run(run())


@pytest.mark.parametrize('name,args,eda_name,data,expected', [
    ('get_all_nets', {}, 'pcb_get_nets', {'nets': ['GND', 'VCC']}, ['GND', 'VCC']),
    ('create_net_class', {'class_name': 'Power', 'net_names': ['GND']}, 'pcb_create_net_class',
     {'class_name': 'Power', 'class_created': True, 'nets_added': 1},
     {'class_name': 'Power', 'class_created': True, 'nets_added': 1, 'success': True}),
])
def test_aliases_use_existing_eda_tools_and_preserve_legacy_json(name,args,eda_name,data,expected):
    async def run():
        calls = []
        async def dispatch(tool, arguments):
            calls.append((tool, arguments))
            return types.CallToolResult(content=[types.TextContent(type='text', text=json.dumps(data))],
                                        structuredContent=data)
        response = await legacy.LegacyCompat(dispatch).call_tool(name, args)
        assert not response.isError and text_result(response) == expected
        assert calls[0][0] == eda_name
    asyncio.run(run())


def test_net_class_names_with_delimiters_remain_single_members():
    async def run():
        import csv
        calls = []
        async def dispatch(tool, args):
            assert tool == 'pcb_create_net_class'
            calls.extend(next(csv.reader([args['nets']])))
            return {'class_name': args['name'], 'class_created': True, 'nets_added': len(calls)}
        names = ['NET,1', 'NET"2', 'space net']
        response = await legacy.LegacyCompat(dispatch).call_tool('create_net_class',
            {'class_name': 'Quoted', 'net_names': names})
        assert calls == names
        assert text_result(response)['nets_added'] == 3
    asyncio.run(run())


def component(name='R1', selected=True, x=20, y=30):
    compat = dict(designator=name, name='Resistor', description='10k', footprint='0603',
        layer='Top Layer', x=x, y=y, width=40, height=20, rotation=90, selected=selected)
    return dict(designator=name, x=x + 1000, y=y + 2000, rotation=90,
                layer='TopLayer', footprint='0603', legacy_data=compat)


@pytest.mark.parametrize('name,args,expected', [
    ('get_all_designators', {}, ['R1', 'R2']),
    ('get_component_property_values', {'property_name': 'description'},
        [{'designator': 'R1', 'value': '10k'}, {'designator': 'R2', 'value': '10k'}]),
    ('get_selected_components_coordinates', {},
        [{k:v for k,v in component()['legacy_data'].items() if k not in {'name','description','selected'}}]),
])
def test_component_alias_preserves_relative_origin_fields_and_selection(name,args,expected):
    async def run():
        async def dispatch(tool, arguments):
            assert tool == 'pcb_get_components' and arguments == {}
            return {'components': [component(),component('R2',selected=False)]}
        result = await legacy.LegacyCompat(dispatch).call_tool(name,args)
        assert text_result(result) == expected
    asyncio.run(run())


def test_relative_move_calls_existing_placement_with_absolute_coordinates():
    async def run():
        calls = []
        async def dispatch(tool, args):
            calls.append((tool,args))
            if tool == 'pcb_get_components':
                return {'components': [component()]}
            assert tool == 'pcb_import_placement'
            return {'applied':1,'failed':0}
        result = await legacy.LegacyCompat(dispatch).call_tool('move_components',
            dict(cmp_designators=['R1','missing'],x_offset=5.5,y_offset=-10,rotation=0))
        assert calls[1] == ('pcb_import_placement', {'placements':[dict(designator='R1',x=1025.5,y=2020)]})
        assert text_result(result) == {'success':True,'result':{'moved_count':1,'missing_designators':['missing']}}
    asyncio.run(run())


def test_unique_command_uses_hidden_tool_and_keeps_original_text_output():
    async def run():
        data = {'success':True,'result':{'symbol_name':'R','pins':[]}}
        async def dispatch(tool,args):
            assert tool == '_suite_legacy_execute'
            assert args == {'command':'get_library_symbol_reference','params':{}}
            return data
        service = legacy.LegacyCompat(dispatch)
        result = await service.call_tool('get_library_symbol_reference',{})
        original, ns = original_wrappers(SimpleNamespace(execute_command=lambda *args: None))
        async def command(*args): return data
        ns['altium_bridge'] = SimpleNamespace(execute_command=command)
        expected = await original.call_tool('get_library_symbol_reference',{})
        assert result.content == (expected[0] if isinstance(expected, tuple) else expected)
    asyncio.run(run())


def test_error_from_eda_is_not_relabelled_as_success_or_retried():
    async def run():
        calls=[]
        async def dispatch(tool,args):
            calls.append(tool)
            return types.CallToolResult(isError=True,content=[types.TextContent(type='text',text='editor unavailable')])
        result = await legacy.LegacyCompat(dispatch).call_tool('get_all_nets',{})
        assert text_result(result) == {'error':'Failed to get nets: editor unavailable'}
        assert calls == ['pcb_get_nets']
    asyncio.run(run())


def test_script_is_dispatched_to_same_hidden_native_transport():
    async def run():
        calls=[]
        async def dispatch(tool,args):
            calls.append((tool,args))
            return {'success':True,'result':'native result','steps':['sandbox start','sandbox end'],'dialogs_dismissed':0}
        result = await legacy.LegacyCompat(dispatch).call_tool('run_altium_script',{'script':"ResultText := 'native result';"})
        assert calls == [('_suite_legacy_execute', {'command':'run_altium_script','params':
            {'script':"ResultText := 'native result';",'timeout_seconds':120}})]
        assert text_result(result)['result'] == 'native result'
    asyncio.run(run())


def test_overlay_uses_coordinator_confirmed_stop_then_same_persistent_dispatcher(monkeypatch,tmp_path):
    import bridge_coordination as coordination
    import suite_config
    calls=[]
    project=tmp_path/'overlay.PrjScr'
    monkeypatch.setattr(coordination,'SCRIPT',tmp_path/'base.PrjScr')
    monkeypatch.setenv('ALTIUM_MCP_EDA_SCRIPT',str(coordination.SCRIPT))
    monkeypatch.setattr(suite_config,'prepare_runtime',lambda **kw: {'eda':project})
    monkeypatch.setattr(coordination,'stop_eda',lambda: calls.append(('stop',coordination.SCRIPT)))
    monkeypatch.setattr(coordination,'ensure_eda',lambda *a: calls.append(('ensure',coordination.SCRIPT)))
    def execute(bridge,command,params,timeout):
        calls.append((command,coordination.SCRIPT))
        return {'success':True,'result':'read only'}
    result=overlay.execute_overlay(object(),execute,{'script':"ResultText := 'read only';"},10)
    assert result['result']=='read only'
    assert calls == [('stop',tmp_path/'base.PrjScr'),('ensure',project),('legacy.run_altium_script',project),('stop',project)]
    assert coordination.SCRIPT == tmp_path/'base.PrjScr'


def test_timed_out_overlay_never_stops_or_replays_unresolved_handler(monkeypatch,tmp_path):
    import bridge_coordination as coordination
    import suite_config
    calls=[]
    monkeypatch.setattr(coordination,'SCRIPT',tmp_path/'base.PrjScr')
    monkeypatch.setattr(suite_config,'prepare_runtime',lambda **kw:{'eda':tmp_path/'overlay.PrjScr'})
    monkeypatch.setattr(coordination,'stop_eda',lambda:calls.append('stop'))
    monkeypatch.setattr(coordination,'ensure_eda',lambda *a:None)
    def timeout(*args): raise TimeoutError('native result unresolved')
    with pytest.raises(TimeoutError,match='unresolved'):
        overlay.execute_overlay(object(),timeout,{'script':"ResultText := 'test';"},1)
    assert calls == ['stop']


def test_generated_script_unit_contains_body_and_original_scratch_types():
    unit=overlay.render_script_unit("SandboxLog('read');\nResultText := IntToStr(GetWorkspace.DM_ProjectCount);")
    assert "ResultText := IntToStr(GetWorkspace.DM_ProjectCount);" in unit
    assert 'Function LegacySandboxExecute' in unit
    assert 'IIntegratedLibraryManager' in unit and 'IDatabaseLibDocument' in unit
    assert 'SaveToFile(SuiteSandboxLogPath)' in unit
    assert 'RunScript(' not in unit and 'Procedure Run;' not in unit


@pytest.mark.parametrize('encoded', [False, True])
def test_schematic_data_filters_native_components_and_reports_missing(encoded):
    async def run():
        components = [{'designator': 'R1', 'comment': '10k'},
                      {'designator': 'C1', 'comment': '100n'}]
        async def dispatch(tool, arguments):
            assert tool == '_suite_legacy_execute'
            assert arguments == {'command': 'get_schematic_data', 'params': {}}
            return {'success': True, 'result': json.dumps(components) if encoded else components}
        result = await legacy.LegacyCompat(dispatch).call_tool(
            'get_schematic_data', {'cmp_designators': ['C1', 'U9']})
        assert text_result(result) == {'components': [components[1]], 'missing_designators': ['U9']}
    asyncio.run(run())


def test_screenshot_returns_native_mcp_image_and_legacy_metadata(tmp_path,monkeypatch):
    """Exercise the original GDI-to-PNG wrapper without touching the desktop."""
    from types import SimpleNamespace
    import base64
    import io
    from PIL import Image
    class DC:
        def CreateCompatibleDC(self): return self
        def SelectObject(self,*args): pass
        def BitBlt(self,*args): pass
        def DeleteDC(self): pass
    class Bitmap:
        def CreateCompatibleBitmap(self,*args): pass
        def GetInfo(self): return {'bmWidth':2,'bmHeight':1}
        def GetBitmapBits(self,*args): return bytes([0,0,255,0,0,255,0,0])
        def GetHandle(self): return 4
    gui=SimpleNamespace(IsWindowVisible=lambda handle:True,
        GetWindowText=lambda handle:'Altium project.PrjPcb',GetClassName=lambda handle:'TAltium',
        GetWindowRect=lambda handle:(0,0,2,1),EnumWindows=lambda cb,arg:cb(1,arg),
        SetForegroundWindow=lambda handle:None,GetWindowDC=lambda handle:3,
        DeleteObject=lambda handle:None,ReleaseDC=lambda *a:None)
    monkeypatch.setattr(legacy,'win32gui',gui,raising=False)
    monkeypatch.setattr(legacy,'win32ui',SimpleNamespace(CreateDCFromHandle=lambda handle:DC(),CreateBitmap=Bitmap),raising=False)
    monkeypatch.setattr(legacy,'win32con',SimpleNamespace(SRCCOPY=0),raising=False)
    monkeypatch.setattr(legacy,'MCP_DIR',tmp_path)
    monkeypatch.setattr(legacy.time,'sleep',lambda duration:None)
    async def run():
        async def dispatch(tool,args):
            assert tool=='_suite_legacy_execute'
            assert args=={'command':'take_view_screenshot','params':{'view_type':'pcb','designators':['R1']}}
            return {'success':True,'result':{'zoomed_component_count':1}}
        result=await legacy.LegacyCompat(dispatch, config={'runtime_dir': str(tmp_path / 'private')}).call_tool('get_screenshot',{'zoom_to':['R1']})
        assert [item.type for item in result.content]==['text','image']
        metadata=json.loads(result.content[0].text)
        assert metadata['width']==2 and metadata['height']==1 and metadata['zoomed_component_count']==1
        bitmap=Image.open(io.BytesIO(base64.b64decode(result.content[1].data)))
        assert bitmap.getpixel((0,0))==(255,0,0)
        assert bitmap.getpixel((1,0))==(0,255,0)
        assert (tmp_path / 'private/legacy-data/screenshot_pcb.png').exists()
    asyncio.run(run())


@pytest.mark.parametrize('class_name,names,error', [
    ('', ['GND'], 'No class name provided'), ('Power', [], 'No net names provided')])
def test_empty_net_class_arguments_preserve_native_business_refusal_without_dispatch(class_name,names,error):
    async def run():
        async def dispatch(*args):
            raise AssertionError('Invalid legacy net class must never reach the editor')
        response = await legacy.LegacyCompat(dispatch).call_tool('create_net_class',
            {'class_name': class_name, 'net_names': names})
        assert text_result(response) == {'success': False, 'error': error}
    asyncio.run(run())


def test_instance_configuration_keeps_status_and_build_exchange_separate(tmp_path,monkeypatch):
    async def run():
        monkeypatch.setenv('ALTIUM_EXE', 'wrong-global.exe')
        monkeypatch.setenv('ALTIUM_MCP_RUNTIME', str(tmp_path / 'wrong-global'))
        captured = []
        services = []
        for name in ('one', 'two'):
            runtime = tmp_path / name
            exe, project = runtime / 'X2.exe', runtime / 'Altium_API.PrjScr'
            runtime.mkdir()
            exe.write_bytes(b'')
            project.write_text('test configuration', encoding='utf-8')
            async def dispatch(tool, arguments):
                assert tool == '_suite_legacy_execute' and arguments['command'] == 'build_circuit'
                spec = Path(arguments['params']['spec_file'])
                captured.append((spec, spec.read_text(encoding='cp1252')))
                return {'success': True, 'result': {'placed_count': 1}}
            service = legacy.LegacyCompat(dispatch, config={'altium_exe': str(exe),
                'runtime_dir': str(runtime), 'script_path': str(project)})
            services.append(service)
            status = text_result(await service.call_tool('get_server_status', {}))
            assert status['altium_exe'] == str(exe) and status['script_path'] == str(project)
            assert status['altium_found'] and status['script_found']
        arguments = {'parts': [{'designator': 'R1', 'symbol_library': 'Synthetic.SchLib',
                               'symbol': 'R', 'x': 0, 'y': 0}]}
        replies = await asyncio.gather(*(service.call_tool('build_schematic', arguments) for service in services))
        assert all(not reply.isError for reply in replies)
        assert {spec.parent for spec, _ in captured} == {tmp_path / 'one/legacy-data', tmp_path / 'two/legacy-data'}
        assert all('PART|R1|Synthetic.SchLib|R|' in text for _, text in captured)
        assert all(not spec.exists() for spec, _ in captured)
        assert not (tmp_path / 'wrong-global').exists()
        assert os.environ['ALTIUM_EXE'] == 'wrong-global.exe'
    asyncio.run(run())


def test_real_eda_protocol_hides_internal_adapter_and_refuses_without_editor(tmp_path):
    async def run():
        environment=dict(os.environ,ALTIUM_MCP_RUNTIME=str(tmp_path/'runtime'),
            EDA_AGENT_WORKSPACE=str(tmp_path/'workspace'),EDA_AGENT_POINTER_FILE=str(tmp_path/'pointer'),
            ALTIUM_EXE=str(tmp_path/'not-installed/X2.EXE'),EDA_AGENT_BACKEND='altium',EDA_AGENT_TOOLSET='full')
        # A nonexistent ALTIUM_EXE does not isolate process discovery: a real
        # editor can already be open. Refuse the editor at the bridge boundary
        # in this child process before any tool can probe or bind its runtime.
        offline_worker = ('import eda_stdio; '
            'eda_stdio.AltiumBridge.is_altium_running = lambda self: False; '
            'eda_stdio.main()')
        parameters=StdioServerParameters(command=sys.executable,
            args=['-B','-c',offline_worker,'--no-dashboard'],cwd=str(ROOT),env=environment)
        with (tmp_path/'worker.log').open('w',encoding='utf-8') as log:
            async with stdio_client(parameters,errlog=log) as streams:
                async with ClientSession(*streams,read_timeout_seconds=timedelta(seconds=30)) as client:
                    await client.initialize()
                    tools=(await client.list_tools()).tools
                    assert len(tools)==457
                    assert not any(t.name.startswith('_suite_') for t in tools)
                    catalog=await client.call_tool('tool_catalog',{'query':'_suite_legacy_execute'})
                    assert catalog.structuredContent['count']==0
                    response=await client.call_tool('_suite_legacy_execute',
                        {'command':'get_library_symbol_reference','params':{}})
                    assert response.isError
                    assert 'Altium' in response.content[0].text
        assert not list((tmp_path/'runtime/scripts').glob('legacy-*'))
    asyncio.run(run())
