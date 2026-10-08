"""Regression tests for delayed edits, lossy strings and modal ECO dispatch."""
import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import bridge_coordination as coord
from eda_agent.bridge.altium_bridge import AltiumBridge, CommandRequest, CommandResponse
from eda_agent.bridge.exceptions import AltiumCommandError, AltiumTimeoutError


@pytest.fixture
def bridge(tmp_path):
    instance = AltiumBridge.__new__(AltiumBridge)
    instance.config = SimpleNamespace(workspace_dir=tmp_path)
    instance._ensure_workspace_fast = MagicMock()
    instance._clear_fault_if_any = MagicMock()
    instance._maybe_attach_detach_hint = lambda command, data: data
    return instance


@pytest.mark.parametrize("params", [
    {"text": "10\u03a9"}, {"ops": [{"label": "\u4e2d\u6587"}]},
    {"\u062a\u0633\u062a": "value"}, {"payload": "text=\u03a9;name=R1"},
])
def test_lossy_text_cannot_reach_request_file(bridge, tmp_path, params):
    with pytest.raises(AltiumCommandError) as failure:
        bridge._publish_request(CommandRequest(command="generic.modify_objects", params=params))
    assert failure.value.code == "INVALID_PARAMETER"
    assert failure.value.details["request_sent"] is False
    bridge._ensure_workspace_fast.assert_not_called()
    assert not list(tmp_path.glob("request*"))


def test_supported_labels_and_delimiters_are_not_transliterated(bridge, tmp_path):
    params = {"name": "~{RESET}", "value": "10\u00b5F", "label": "EN=1"}
    request = CommandRequest(command="generic.modify_objects", params=params)
    bridge._publish_request(request)
    assert json.loads(bridge._request_path(request.id).read_text())["params"] == params


@pytest.mark.parametrize("allow", [None, False, "true", 1])
def test_direct_eco_dispatch_requires_explicit_boolean_opt_in(bridge, tmp_path, allow):
    with pytest.raises(AltiumCommandError):
        bridge._publish_request(CommandRequest(command="project.update_pcb", params={"allow_modal": allow}))
    assert not list(tmp_path.glob("request*"))


def test_opted_in_eco_is_published(bridge):
    request = CommandRequest(command="project.update_pcb", params={"allow_modal": True})
    bridge._publish_request(request)
    assert bridge._request_path(request.id).exists()


@pytest.mark.parametrize("consumed", [False, True])
def test_timeout_withdraws_queued_edit_but_keeps_inflight_evidence(bridge, tmp_path, consumed):
    def timeout(request_id, seconds):
        if consumed:
            bridge._request_path(request_id).unlink()
            bridge._progress_path(request_id).write_text('{}')
        raise AltiumTimeoutError("modal or dead engine")
    bridge._poll_response = timeout
    with pytest.raises(AltiumTimeoutError):
        bridge._execute_command("generic.modify_objects", {"text": "R1"}, 1)
    # A later dispatcher scan cannot apply an edit abandoned by this caller.
    assert not list(tmp_path.glob('request_*.json'))
    assert bool(list(tmp_path.glob('progress_*.json'))) is consumed


def test_successful_operation_is_dispatched_once(bridge):
    calls=[]
    def reply(request_id, timeout):
        calls.append(request_id)
        bridge._request_path(request_id).unlink()
        return CommandResponse(id=request_id, success=True, data={"ok": True})
    bridge._poll_response=reply
    assert bridge._execute_command("generic.modify_objects", {}, 1)=={"ok": True}
    assert len(calls)==1


def test_handler_becoming_busy_during_probe_prevents_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(coord, '_guard_editor', lambda: None)
    busy=iter([False, True])
    monkeypatch.setattr(coord, '_handler_busy', lambda: next(busy))
    launcher=MagicMock();monkeypatch.setattr(coord.subprocess, 'Popen', launcher)
    def probe(timeout): raise TimeoutError()
    with pytest.raises(RuntimeError, match='during the health check'):
        coord._ensure(probe)
    launcher.assert_not_called()


def test_eco_tool_default_does_not_even_acquire_bridge():
    source=Path(__file__).resolve().parents[1]/'eda-agent/src/eda_agent/tools/project.py'
    tree=ast.parse(source.read_text(encoding='utf-8'))
    node=next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name=='proj_sync_pcb')
    node.decorator_list=[]
    acquire=MagicMock()
    namespace={'Any': object, 'get_bridge': acquire}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),namespace)
    result=asyncio.run(namespace['proj_sync_pcb']())
    assert result['command_sent'] is False
    acquire.assert_not_called()
