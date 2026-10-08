import ast
import asyncio
import json
import os
from pathlib import Path
import subprocess
import types
import uuid
from unittest.mock import MagicMock, patch
import pytest
import suite_config
import bridge_coordination as coord

ROOT = Path(__file__).resolve().parents[1]


def method(name, namespace):
    tree = ast.parse((ROOT / 'coffeenmusic/server/main.py').read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AltiumBridge')
    node = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<legacy-test>', 'exec'), namespace)
    return namespace[name]


def test_legacy_reads_partial_response_without_replaying_command(tmp_path):
    request, response = tmp_path / 'request.json', tmp_path / 'response.json'
    count = []
    published = []
    async def run():
        count.append(1)
        published.append(json.loads(request.read_text()))
        response.write_text('{', encoding='utf-8')
        return True
    async def finish_write(_):
        request_id = json.loads(request.read_text())['request_id']
        response.write_text(json.dumps({'success': True, 'value': 'دوربین', 'request_id': request_id}), encoding='utf-8')
        (tmp_path / f'completed_{request_id}.json').write_text(json.dumps({'request_id': request_id}))
    namespace = dict(Dict=dict, Any=object, REQUEST_FILE=request, RESPONSE_FILE=response,
                     json=json, uuid=uuid, time=__import__('time'), logger=MagicMock(),
                     asyncio=types.SimpleNamespace(sleep=finish_write), begin_legacy=MagicMock(), abandon_unlaunched_legacy=MagicMock())
    fn = method('_execute_command_locked', namespace)
    result = asyncio.run(fn(types.SimpleNamespace(run_altium_script=run), 'safe.command', {'command': 'wrong.command'}))
    assert result == {'success': True, 'value': 'دوربین'}
    assert count == [1]
    assert published[0]['command'] == 'safe.command'
    assert not request.exists()
    assert not request.with_suffix('.json.tmp').exists()


def test_late_response_from_another_command_is_not_accepted(tmp_path):
    request, response = tmp_path / 'request.json', tmp_path / 'response.json'
    count = []
    async def run():
        count.append(1)
        response.write_text(json.dumps({'request_id':'old-request','success':True,'result':'wrong'}))
        return True
    async def reply(_):
        request_id = json.loads(request.read_text())['request_id']
        response.write_text(json.dumps({'request_id':request_id,'success':True,'result':'correct'}))
        (tmp_path / f'completed_{request_id}.json').write_text(json.dumps({'request_id': request_id}))
    namespace = dict(Dict=dict, Any=object, REQUEST_FILE=request, RESPONSE_FILE=response,
                     json=json, uuid=uuid, time=__import__('time'), logger=MagicMock(),
                     asyncio=types.SimpleNamespace(sleep=reply), begin_legacy=MagicMock(), abandon_unlaunched_legacy=MagicMock())
    fn = method('_execute_command_locked', namespace)
    result = asyncio.run(fn(types.SimpleNamespace(run_altium_script=run), 'read', {}))
    assert result['result'] == 'correct'
    assert len(count) == 1


def test_legacy_launcher_does_not_use_shell(tmp_path):
    exe, script = tmp_path / 'X2.EXE', tmp_path / 'script & spaces.PrjScr'
    exe.touch(); script.touch()
    process = MagicMock()
    namespace = dict(os=os, logger=MagicMock(), subprocess=process)
    fn = method('run_altium_script', namespace)
    bridge = types.SimpleNamespace(config=types.SimpleNamespace(altium_exe_path=str(exe), script_path=str(script)), _resolve_msix_path=lambda p:p)
    assert asyncio.run(fn(bridge)) is True
    assert process.Popen.call_args.kwargs['shell'] is False
    assert '^|' not in process.Popen.call_args.args[0]
    assert str(script) in process.Popen.call_args.args[0]


def test_diagnostics_do_not_write_to_protocol_stdout():
    tree = ast.parse((ROOT / 'coffeenmusic/server/main.py').read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'print':
            assert any(k.arg == 'file' and ast.unparse(k.value) == 'sys.stderr' for k in node.keywords)


def test_runtime_is_per_user_and_templates_are_resolved(tmp_path, monkeypatch):
    monkeypatch.setenv('ALTIUM_MCP_RUNTIME', str(tmp_path / "user's runtime"))
    scripts = suite_config.prepare_runtime()
    content = (scripts['legacy'].parent / 'Altium_API.pas').read_text(encoding='utf-8')
    assert '__ALTIUM_MCP_EXCHANGE_DIR__' not in content
    assert "user''s runtime" in content
    assert scripts == suite_config.prepare_runtime()
    for project in scripts.values():
        for line in project.read_text().splitlines():
            if line.startswith('DocumentPath='):
                assert (project.parent / line.split('=', 1)[1]).is_file()


def test_healthy_probe_clears_failed_start_cooldown(tmp_path, monkeypatch):
    marker = tmp_path / 'launch.json'; marker.write_text('{}')
    monkeypatch.setattr(coord, 'LAUNCH_STATE', marker)
    monkeypatch.setattr(coord, '_guard_editor', lambda: None)
    monkeypatch.setattr(coord, '_handler_busy', lambda: False)
    coord._ensure(lambda _: True)
    assert not marker.exists()


def test_log_failure_does_not_replace_tool_error(tmp_path, monkeypatch):
    file = tmp_path / 'not-a-directory'; file.touch()
    monkeypatch.setattr(coord, 'RUNTIME', file)
    coord.log_event('test')
