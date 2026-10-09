"""Acceptance tests for the five transport findings, with no live Altium."""
import ast
import contextlib
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock
import uuid

import pytest
import bridge_coordination as coord
from eda_agent.bridge.altium_bridge import AltiumBridge, CommandResponse
from eda_agent.bridge.exceptions import AltiumProtocolError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'coffeenmusic/server'))
from batch_safety import validate_batch_fields, validate_legacy_json_text


def extract(path,name,namespace):
    tree=ast.parse(path.read_text(encoding='utf-8'))
    node=next(n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
    node.decorator_list=[]
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),namespace)
    return namespace[name]


@pytest.fixture
def ownership(tmp_path,monkeypatch):
    monkeypatch.setattr(coord,'RUNTIME',tmp_path)
    monkeypatch.setattr(coord,'WORKSPACE',tmp_path)
    monkeypatch.setattr(coord,'_editor_state',lambda:dict(pid=77,blocked=False))
    monkeypatch.setattr(coord,'_session_started_at',lambda pid:100)
    return tmp_path


def legacy_method(folder,clock=time):
    namespace=dict(Dict=dict,Any=object,REQUEST_FILE=folder/'request.json',RESPONSE_FILE=folder/'response.json',
                   json=json,uuid=uuid,time=clock,logger=MagicMock(),asyncio=asyncio,
                   validate_legacy_json_text=validate_legacy_json_text,
                   begin_legacy=coord.begin_legacy,abandon_unlaunched_legacy=coord.abandon_unlaunched_legacy)
    return extract(ROOT/'coffeenmusic/server/main.py','_execute_command_locked',namespace)


def test_timeout_withdraws_request_and_blocks_all_new_engine_launches(ownership,monkeypatch):
    clock=iter([0,121]);fn=legacy_method(ownership,SimpleNamespace(monotonic=lambda:next(clock)))
    async def launch():return True
    result=asyncio.run(fn(SimpleNamespace(run_altium_script=launch),'modify',{}))
    assert result['success'] is False
    assert not (ownership/'request.json').exists()
    assert coord._legacy_marker().exists()
    launcher=MagicMock();monkeypatch.setattr(coord.subprocess,'Popen',launcher)
    with pytest.raises(RuntimeError,match='not confirmed completion'):coord.start_eda()
    with pytest.raises(RuntimeError,match='not confirmed completion'):coord.stop_eda()
    launcher.assert_not_called()


def test_cancellation_withdraws_request_but_keeps_ownership(ownership):
    fn=legacy_method(ownership)
    async def scenario():
        started=asyncio.Event()
        async def launch():started.set();await asyncio.Future()
        task=asyncio.create_task(fn(SimpleNamespace(run_altium_script=launch),'modify',{}))
        await started.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(scenario())
    assert not (ownership/'request.json').exists()
    assert coord._legacy_marker().exists()


def test_unlaunched_failure_does_not_leave_ownership_blocked(ownership):
    async def launch():return False
    result=asyncio.run(legacy_method(ownership)(SimpleNamespace(run_altium_script=launch),'modify',{}))
    assert result['success'] is False
    assert not coord._legacy_marker().exists()
    assert not (ownership/'request.json').exists()


def test_late_completion_releases_ownership_without_replaying_operation(ownership):
    request=ownership/'request.json';done=ownership/'completed_id.json'
    coord.begin_legacy('id',request,done)
    done.write_text(json.dumps({'request_id':'old-id'}))
    with pytest.raises(RuntimeError):coord._guard_editor()
    done.write_text(json.dumps({'request_id':'id'}))
    coord._guard_editor()
    assert not coord._legacy_marker().exists()


def test_editor_restart_retires_previous_legacy_ownership(ownership,monkeypatch):
    request=ownership/'request.json';request.touch()
    coord.begin_legacy('id',request,ownership/'done.json')
    monkeypatch.setattr(coord,'_session_started_at',lambda pid:200)
    coord._guard_editor()
    assert not coord._legacy_marker().exists() and not request.exists()


def test_switching_to_another_live_editor_does_not_retire_ownership(ownership,monkeypatch):
    coord.begin_legacy('id',ownership/'request.json',ownership/'done.json')
    monkeypatch.setattr(coord,'_editor_state',lambda:dict(pid=88,blocked=False))
    with pytest.raises(RuntimeError,match='another running Altium'):coord._guard_editor()
    assert coord._legacy_marker().exists()


def test_explicit_manual_reset_withdraws_requests_without_launch(ownership,monkeypatch):
    legacy=ownership/'request.json';legacy.touch()
    coord.begin_legacy('id',legacy,ownership/'done.json')
    (ownership/'progress_work.json').touch();(ownership/'request_edit.json').touch()
    monkeypatch.setattr(coord,'engine_lock',contextlib.nullcontext)
    launcher=MagicMock();monkeypatch.setattr(coord.subprocess,'Popen',launcher)
    coord.reset_after_manual_stop()
    assert not coord._legacy_marker().exists() and not legacy.exists()
    assert not list(ownership.glob('progress_*')) and not list(ownership.glob('request_*'))
    launcher.assert_not_called()


def test_response_alone_does_not_prove_native_completion(ownership):
    ticks=iter([0,1,121]);fn=legacy_method(ownership,SimpleNamespace(monotonic=lambda:next(ticks)))
    async def launch():
        request=json.loads((ownership/'request.json').read_text())
        (ownership/'response.json').write_text(json.dumps({'request_id':request['request_id'],'success':True}))
        return True
    result=asyncio.run(fn(SimpleNamespace(run_altium_script=launch),'modify',{}))
    assert result['success'] is False and coord._legacy_marker().exists()


def test_confirmed_native_completion_allows_normal_handback(ownership):
    async def launch():
        request=json.loads((ownership/'request.json').read_text());rid=request['request_id']
        (ownership/'response.json').write_text(json.dumps({'request_id':rid,'success':True,'result':'done'}))
        (ownership/f'completed_{rid}.json').write_text(json.dumps({'request_id':rid}))
        return True
    result=asyncio.run(legacy_method(ownership)(SimpleNamespace(run_altium_script=launch),'modify',{}))
    assert result=={'success':True,'result':'done'}
    coord._guard_editor()
    assert not coord._legacy_marker().exists()


@pytest.mark.parametrize('payload',[
    {'notes':[{'text':'label\nWIRE|0|0|10|10'}]}, {'comment':'A\rB'},
    {'parameters':{'bad|name':'value'}}, {'symbol_library':'bad|path'},
    {'net_labels':[{'text':'10\u03a9'}]}, {'notes':[{'text':'\u4e2d'}]}, {'description':'A\x00B'},
])
def test_batch_fields_fail_without_silent_replacement(payload):
    with pytest.raises(ValueError):validate_batch_fields(payload)


def build_method(exchange,backend):
    return extract(ROOT/'coffeenmusic/server/main.py','build_schematic',dict(
        Context=object,logger=MagicMock(),validate_batch_fields=validate_batch_fields,
        EXCHANGE_DIR=exchange,uuid=uuid,json=json,altium_bridge=SimpleNamespace(execute_command=backend)))


def test_bad_annotation_never_writes_spec_or_calls_bridge(tmp_path):
    calls=[]
    async def backend(*args):calls.append(args)
    result=json.loads(asyncio.run(build_method(tmp_path,backend)(None,[],notes=[{'x':0,'y':0,'text':'X\nWIRE|0|0|1|1'}])))
    assert result['command_sent'] is False and not calls
    assert not list(tmp_path.iterdir())


def test_each_build_uses_configured_unique_input_and_pin_map(tmp_path):
    calls=[]
    (tmp_path/'pin_map.txt').write_text('PIN|WRONG|1|999|999')
    async def backend(command,params):
        spec=Path(params['spec_file']);pins=Path(params['pin_map_file'])
        assert spec.parent==pins.parent==tmp_path
        assert spec.read_text(encoding='cp1252')=='NOTE|1|2|10\u00b5F\n'
        pins.write_text('PIN|U1|1|100|200',encoding='cp1252')
        calls.append(params)
        return {'success':True,'result':{}}
    for _ in range(2):
        result=json.loads(asyncio.run(build_method(tmp_path,backend)(None,[],notes=[{'x':1,'y':2,'text':'10\u00b5F'}])))
        assert result['pin_map']=={'U1':{'1':[100,200]}}
    assert calls[0]!=calls[1]
    assert list(tmp_path.iterdir())==[tmp_path/'pin_map.txt']


@pytest.mark.parametrize('data',[
    [], {}, {'id':'other','success':True}, {'id':'expected','success':'false'},
    {'id':'expected','success':1}, {'id':'expected','success':True,'protocol_version':True},
    {'id':'expected','success':False,'error':'bad'}, {'id':'expected','success':True,'error':{'code':'BAD'}},
])
def test_malformed_response_is_structured_protocol_error(data):
    with pytest.raises(AltiumProtocolError):CommandResponse.from_dict(data,expected_id='expected')


def test_polling_rejects_wrong_body_id(tmp_path):
    bridge=AltiumBridge.__new__(AltiumBridge)
    bridge.config=SimpleNamespace(workspace_dir=tmp_path,poll_interval=.001)
    bridge._dialog_probe=lambda:None
    bridge._response_path('expected').write_text(json.dumps({'id':'other','success':True}))
    with pytest.raises(AltiumProtocolError):bridge._poll_response('expected',.1)


def test_long_current_session_handler_is_busy_regardless_of_age(ownership):
    marker=ownership/'progress_long.json';marker.write_text('{}');os.utime(marker,(101,101))
    assert time.time()-marker.stat().st_mtime>600
    assert coord._handler_busy() and marker.exists()


def test_previous_editor_session_marker_is_retired(ownership):
    marker=ownership/'progress_old.json';marker.write_text('{}');os.utime(marker,(99,99))
    assert not coord._handler_busy() and not marker.exists()


def test_unreadable_ownership_is_not_treated_as_free_engine(ownership):
    coord._legacy_marker().write_text('{')
    with pytest.raises(RuntimeError,match='cannot be verified'):coord._guard_editor()


def test_native_completion_is_after_cleanup_and_paths_are_parameters():
    native=(ROOT/'coffeenmusic/server/AltiumScript/Altium_API.pas').read_text(encoding='utf-8')
    run=native.split('procedure Run;',1)[1]
    assert run.index('Params.Free;')<run.index("'completed_'")
    assert "BuildCircuitFromSpec(Params.Values['spec_file']" in native
    utils=(ROOT/'coffeenmusic/server/AltiumScript/schematic_utils.pas').read_text(encoding='utf-8')
    assert 'PinMap.SaveToFile(PinMapPath)' in utils
    assert 'C:\\Users\\Public\\altium_mcp\\pin_map.txt' not in utils
