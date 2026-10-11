"""Completion, UI return and one-shot close are independent ownership gates."""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import bridge_coordination as coord
from test_review_fixes import ownership
from test_handover_confirmation import native


def test_completion_keeps_owner_until_ui_thread_returns(ownership, monkeypatch):
    done = ownership / 'done.json'
    coord.begin_legacy('id', ownership / 'request.json', done)
    done.write_text('{"request_id":"id"}')
    barrier = MagicMock(side_effect=TimeoutError('still returning'))
    monkeypatch.setattr(coord, '_confirm_editor_return', barrier)
    with pytest.raises(TimeoutError):
        coord._guard_editor()
    assert coord._legacy_marker().exists() and done.exists()
    barrier.side_effect = None
    coord._guard_editor()
    assert not coord._legacy_marker().exists()


@pytest.mark.parametrize('after', [dict(pid=78, hwnd=10, blocked=False),
                                  dict(pid=77, hwnd=11, blocked=False),
                                  dict(pid=77, hwnd=10, blocked=True)])
def test_window_identity_or_modal_change_invalidates_return(monkeypatch, after):
    states = iter([dict(pid=77, hwnd=10, blocked=False), after])
    monkeypatch.setattr(coord, '_editor_state', lambda: next(states))
    monkeypatch.setattr(coord, '_session_started_at', lambda pid: 100)
    barrier = MagicMock()
    monkeypatch.setattr(coord, '_ui_thread_barrier', barrier)
    with pytest.raises(RuntimeError, match='during engine handover'):
        coord._confirm_editor_return(dict(pid=77))
    barrier.assert_called_once_with(10, 3000)


def test_clean_stop_marker_does_not_bypass_return_barrier(native, monkeypatch):
    _, _, released, _ = native
    coord._remember_session(dict(pid=77))
    released()
    monkeypatch.setattr(coord, '_confirm_editor_return', MagicMock(side_effect=TimeoutError('tail busy')))
    with pytest.raises(TimeoutError, match='tail busy'):
        coord.stop_eda()


@pytest.fixture
def close(ownership, monkeypatch):
    monkeypatch.setattr(coord, 'stop_eda', lambda: None)
    monkeypatch.setattr(coord, 'SCRIPT', Path('bridge.PrjScr'))
    monkeypatch.setattr(coord, 'EXE', Path('X2.EXE'))
    monkeypatch.setattr(coord.subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    return ownership


def test_close_never_queues_before_shutdown_confirmation(close, monkeypatch):
    monkeypatch.setattr(coord, 'stop_eda', MagicMock(side_effect=TimeoutError('not stopped')))
    launch = MagicMock()
    monkeypatch.setattr(coord.subprocess, 'Popen', launch)
    with pytest.raises(TimeoutError):
        coord.close_project_once({'project_path': 'scratch.PrjPcb'}, 1)
    launch.assert_not_called()
    assert not (close / 'project-close-request.json').exists()


def test_close_rejects_lossy_path_before_stopping_or_publishing(close, monkeypatch):
    from eda_agent.bridge.exceptions import AltiumCommandError
    stop = MagicMock()
    launch = MagicMock()
    monkeypatch.setattr(coord, 'stop_eda', stop)
    monkeypatch.setattr(coord.subprocess, 'Popen', launch)
    with pytest.raises(AltiumCommandError) as failure:
        coord.close_project_once({'project_path': '\u062a\u0633\u062a.PrjPcb'}, 1)
    assert failure.value.details['request_sent'] is False
    stop.assert_not_called()
    launch.assert_not_called()
    assert not (close / 'project-close-request.json').exists()
    assert not coord._legacy_marker().exists()


def test_uncompleted_close_is_withdrawn_without_replay(close, monkeypatch):
    launch = MagicMock()
    monkeypatch.setattr(coord.subprocess, 'Popen', launch)
    with pytest.raises(TimeoutError, match='ownership retained'):
        coord.close_project_once({'project_path': 'scratch.PrjPcb'}, 0)
    launch.assert_called_once()
    assert coord._legacy_marker().exists()
    assert not (close / 'project-close-request.json').exists()
    with pytest.raises(RuntimeError, match='not confirmed completion'):
        coord._guard_editor()


def test_completed_close_requires_targeted_native_readback(close, monkeypatch):
    def launch(*args, **kwargs):
        request = json.loads((close / 'project-close-request.json').read_text())
        rid = request['id']
        assert request['params'] == {'project_path': 'scratch.PrjPcb', 'save': False}
        (close / ('close-response_' + rid + '.json')).write_text(json.dumps({
            'id': rid, 'success': True, 'data': {'success': True, 'closed': True, 'project_path': 'scratch.PrjPcb'}}))
        (close / ('close-completed_' + rid + '.json')).write_text(json.dumps({'request_id': rid}))
    monkeypatch.setattr(coord.subprocess, 'Popen', launch)
    assert coord.close_project_once({'project_path': 'scratch.PrjPcb', 'save': False}, 1)['closed']
    assert not coord._legacy_marker().exists()
    assert not list(close.glob('close-*'))


def test_completion_tails_never_pump_messages():
    root = Path(__file__).resolve().parents[1]
    files = [
        ('eda-agent/scripts/altium/Dispatcher.pas', "WriteFileContent(WorkspaceDir + 'bridge-stopped.json'"),
        ('eda-agent/scripts/altium/Dispatcher.pas', "WriteFileContent(WorkspaceDir + 'close-completed_'"),
        ('coffeenmusic/server/AltiumScript/Altium_API.pas', "RequestData.SaveToFile(ROOT_DIR + 'completed_'"),
        ('coffeenmusic/server/SandboxScript/Sandbox.pas', "OutLines.SaveToFile('__ALTIUM_MCP_SANDBOX_COMPLETION__')"),
    ]
    for name, publication in files:
        tail = (root / name).read_text().split(publication, 1)[1]
        if name.endswith('Dispatcher.pas'):
            tail = tail.split('End;', 1)[0]
        assert 'Application.ProcessMessages' not in tail
        assert 'RunProcess(' not in tail
