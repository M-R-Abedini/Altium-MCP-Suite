"""A native clean-stop acknowledgement saves probes, never grants ownership."""
import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock
from pathlib import Path

import pytest
import bridge_coordination as coord

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(coord.subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    work = tmp_path / 'work'; work.mkdir()
    script = tmp_path / 'script.PrjScr'; script.touch()
    exe = tmp_path / 'X2.exe'; exe.touch()
    for key, value in {'WORKSPACE': work, 'RUNTIME': tmp_path,
                       'LAUNCH_STATE': tmp_path / 'launch.json', 'SCRIPT': script, 'EXE': exe}.items():
        monkeypatch.setattr(coord, key, value)
    monkeypatch.setattr(coord, '_editor_state', lambda: {'pid': 77, 'blocked': False})
    monkeypatch.setattr(coord, '_session_started_at', lambda pid: 100)
    monkeypatch.setattr(coord.time, 'sleep', lambda _: None)
    owner = {'pid': 77, 'session_started_at': 100, 'session_id': 'native-1', 'script_version': 'v1'}
    (work / 'bridge-owner.json').write_text(json.dumps(owner))
    (work / 'bridge-stopped.json').write_text(json.dumps({
        'session_id': 'native-1', 'script_version': 'v1', 'reason': 'engine_idle_release'}))
    def launch(*a, **kw):
        assert not (work / 'bridge-stopped.json').exists()
        (work / 'bridge-ready.json').write_text(json.dumps({'session_id': 'native-2', 'script_version': 'v2'}))
        return SimpleNamespace(pid=123)
    launched = MagicMock(side_effect=launch)
    monkeypatch.setattr(coord.subprocess, 'Popen', launched)
    return work, launched


def test_confirmed_release_skips_dead_probes_and_records_new_session(session):
    work, launch = session
    probe = MagicMock(return_value={'pong': True})
    assert coord._ensure(probe) == {'pong': True}
    probe.assert_called_once_with(2.0)
    launch.assert_called_once()
    owner = json.loads((work / 'bridge-owner.json').read_text())
    assert owner == {'pid': 77, 'session_started_at': 100, 'session_id': 'native-2', 'script_version': 'v2'}


@pytest.mark.parametrize('damage', ['missing_stop', 'missing_owner', 'malformed_stop', 'malformed_owner',
    'different_session', 'different_version', 'different_pid', 'different_process_start',
    'error_stop', 'malformed_reason', 'ready_present', 'legacy_ready'])
def test_uncertain_stop_still_requires_both_probes(session, damage):
    work, launch = session
    if damage.startswith('missing_'):
        (work / ('bridge-stopped.json' if damage == 'missing_stop' else 'bridge-owner.json')).unlink()
    elif damage in ('malformed_stop', 'malformed_owner'):
        (work / ('bridge-stopped.json' if damage == 'malformed_stop' else 'bridge-owner.json')).write_text('[')
    elif damage in ('ready_present', 'legacy_ready'):
        (work / 'bridge-ready.json').write_text('{"session_id":"native-live"}' if damage == 'ready_present' else '{}')
    else:
        changes = {'different_session': ('bridge-stopped.json', 'session_id', 'another'),
                   'different_version': ('bridge-stopped.json', 'script_version', 'v0'),
                   'different_pid': ('bridge-owner.json', 'pid', 88),
                   'different_process_start': ('bridge-owner.json', 'session_started_at', 99),
                   'error_stop': ('bridge-stopped.json', 'reason', 'dispatcher_exception'),
                   'malformed_reason': ('bridge-stopped.json', 'reason', [])}
        filename, key, value = changes[damage]
        marker = json.loads((work / filename).read_text()); marker[key] = value
        (work / filename).write_text(json.dumps(marker))
    probe = MagicMock(side_effect=[TimeoutError(), TimeoutError(), {'pong': True}])
    coord._ensure(probe)
    assert probe.call_args_list == [((2.0,),), ((3.0,),), ((2.0,),)]
    launch.assert_called_once()


@pytest.mark.parametrize('guard', ['modal', 'handler', 'legacy', 'cooldown'])
def test_release_proof_does_not_bypass_guards(session, monkeypatch, guard):
    work, launch = session
    if guard == 'modal':
        monkeypatch.setattr(coord, '_editor_state', lambda: {'pid': 77, 'blocked': True})
    elif guard == 'handler':
        (work / 'progress_live.json').write_text('{}')
    elif guard == 'legacy':
        (coord.RUNTIME / 'legacy-operation.json').write_text(json.dumps({
            'pid': 77, 'session_started_at': 100, 'request_path': str(work / 'request.json'),
            'completion_path': str(work / 'missing.json'), 'request_id': 'old'}))
    else:
        coord.LAUNCH_STATE.write_text(json.dumps({'attempted_at': time.time()}))
    probe = MagicMock()
    with pytest.raises((RuntimeError, TimeoutError)):
        coord._ensure(probe)
    probe.assert_not_called(); launch.assert_not_called()


def test_handler_started_during_second_probe_prevents_launch(session):
    work, launch = session
    (work / 'bridge-stopped.json').unlink()
    calls = []
    def probe(timeout):
        calls.append(timeout)
        if len(calls) == 2:
            (work / 'progress_live.json').write_text('{}')
        raise TimeoutError()
    with pytest.raises(RuntimeError, match='handler started'):
        coord._ensure(probe)
    assert calls == [2.0, 3.0]; launch.assert_not_called()


def test_identity_recording_failure_does_not_discard_health_result(session, monkeypatch):
    work, launch = session
    (work / 'bridge-ready.json').write_text('{"session_id":"native-live","script_version":"v2"}')
    monkeypatch.setattr(coord, '_session_started_at', MagicMock(side_effect=RuntimeError('editor vanished')))
    assert coord._ensure(lambda _: {'pong': True}) == {'pong': True}
    launch.assert_not_called()


def test_native_proof_is_published_after_cleanup_and_invalidated_before_startup():
    source = (ROOT / 'eda-agent/scripts/altium/Dispatcher.pas').read_text(encoding='utf-8')
    start = source.split('Procedure StartMCPServer;', 1)[1].split('Procedure StopMCPServer', 1)[0]
    assert start.index("DeleteFile(WorkspaceDir + 'bridge-stopped.json')") < start.index('CleanupOrphanRequests(0)')
    assert start.rindex('CleanupMCPServer(0)') < start.index("WriteFileContent(WorkspaceDir + 'bridge-stopped.json'")
    assert 'dispatcher_exception' not in start.split('CleanupMCPServer(0);', 1)[1]


def test_background_lock_never_waits_for_a_busy_foreground(monkeypatch):
    monkeypatch.setattr(coord, 'try_lock', lambda: None)
    unlock = MagicMock(); monkeypatch.setattr(coord, 'unlock', unlock)
    with coord.background_engine_lock() as acquired:
        assert acquired is False
    unlock.assert_not_called()


def test_background_lock_releases_even_after_a_failed_ping(monkeypatch):
    handle = object()
    monkeypatch.setattr(coord, 'try_lock', lambda: handle)
    unlock = MagicMock(); monkeypatch.setattr(coord, 'unlock', unlock)
    with pytest.raises(TimeoutError):
        with coord.background_engine_lock() as acquired:
            assert acquired is True
            raise TimeoutError()
    unlock.assert_called_once_with(handle)
