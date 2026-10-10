"""Snippet result publication must not release a still-running native engine."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import uuid
import pytest

import bridge_coordination as coord
from script_projects import register_sandbox_project
from test_review_fixes import ROOT, extract, ownership, validate_legacy_json_text


def functions(folder, clock, launcher):
    source = ROOT / 'coffeenmusic/server/main.py'
    namespace = dict(EXCHANGE_DIR=folder, SANDBOX_PAS=ROOT/'coffeenmusic/server/SandboxScript/Sandbox.pas',
                     SANDBOX_PRJ=ROOT/'coffeenmusic/server/SandboxScript/Sandbox.PrjScr',
                     SANDBOX_DIR=folder, SANDBOX_BEGIN='// === BEGIN EXPERIMENT', SANDBOX_END='// === END EXPERIMENT',
                     validate_legacy_json_text=validate_legacy_json_text, uuid=uuid, json=json,
                     logger=MagicMock(), Context=object, asyncio=asyncio, time=clock,
                     subprocess=SimpleNamespace(Popen=launcher, CREATE_NO_WINDOW=0),
                     altium_bridge=SimpleNamespace(config=SimpleNamespace(altium_exe_path='X2.EXE')),
                     begin_legacy=coord.begin_legacy, abandon_unlaunched_legacy=coord.abandon_unlaunched_legacy,
                     register_sandbox_project=register_sandbox_project)
    extract(source, 'prepare_sandbox', namespace)
    return extract(source, 'run_altium_script', namespace), namespace


def test_private_generation_keeps_template_unchanged_and_escapes_paths(tmp_path):
    folder = tmp_path / "owner's directory"
    folder.mkdir()
    _, namespace = functions(folder, SimpleNamespace(), MagicMock())
    template = namespace['SANDBOX_PAS'].read_bytes()
    first = namespace['prepare_sandbox']("ResultText := 'ok';", uuid.uuid4().hex)
    second = namespace['prepare_sandbox']("ResultText := 'next';", uuid.uuid4().hex)
    assert first['project'] != second['project']
    assert namespace['SANDBOX_PAS'].read_bytes() == template
    generated = (first['project'].parent/'Sandbox.pas').read_text()
    assert 'owner\'\'s directory' in generated
    assert 'Users\\Public' not in generated
    assert '__ALTIUM_MCP_SANDBOX_' not in generated


def test_result_without_ack_keeps_engine_owned(ownership):
    ticks = iter([0, 0, 2])
    def launch(command, **options):
        assert options['shell'] is False
        pending = json.loads(coord._legacy_marker().read_text())
        completion = Path(pending['completion_path'])
        completion.with_name('result.json').write_text('partial result')
    fn, _ = functions(ownership, SimpleNamespace(monotonic=lambda: next(ticks)), launch)
    result = json.loads(asyncio.run(fn(None, "ResultText := 'ok';", timeout_seconds=1)))
    assert result['success'] is False
    with pytest.raises(RuntimeError, match='not confirmed completion'):
        coord._guard_editor()


def test_matching_ack_authorizes_success(ownership):
    def launch(command, **options):
        pending = json.loads(coord._legacy_marker().read_text())
        completion = Path(pending['completion_path'])
        completion.with_name('result.json').write_text('native result')
        completion.write_text(json.dumps({'request_id': pending['request_id']}))
    fn, _ = functions(ownership, SimpleNamespace(monotonic=lambda: 0), launch)
    result = json.loads(asyncio.run(fn(None, "ResultText := 'ok';")))
    assert result['success'] is True
    assert result['result'] == 'native result'
    coord._guard_editor()
    assert not coord._legacy_marker().exists()


def test_cancellation_keeps_ownership(ownership):
    fn, _ = functions(ownership, SimpleNamespace(monotonic=lambda: 0), MagicMock())
    async def run():
        task = asyncio.create_task(fn(None, "ResultText := 'ok';"))
        await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    with pytest.raises(RuntimeError, match='not confirmed completion'):
        coord._guard_editor()


def test_failed_launch_retires_only_unlaunched_ownership(ownership):
    def launch(*args, **kwargs):
        raise OSError('launch failed')
    fn, _ = functions(ownership, SimpleNamespace(), launch)
    with pytest.raises(OSError, match='launch failed'):
        asyncio.run(fn(None, "ResultText := 'ok';"))
    assert not coord._legacy_marker().exists()
