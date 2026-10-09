"""A health timeout or consumed stop request does not release the engine."""
import json
from unittest.mock import MagicMock

import pytest
import bridge_coordination as coord


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.setattr(coord, 'WORKSPACE', tmp_path)
    monkeypatch.setattr(coord, 'RUNTIME', tmp_path)
    monkeypatch.setattr(coord, '_editor_state', lambda: {'pid': 77, 'blocked': False})
    monkeypatch.setattr(coord, '_session_started_at', lambda pid: 100)
    monkeypatch.setattr(coord, '_file_ping', MagicMock(return_value={'pong': True}))
    clock = [0.0]
    monkeypatch.setattr(coord.time, 'monotonic', lambda: clock[0])
    def sleep(seconds):
        clock[0] += seconds
    monkeypatch.setattr(coord.time, 'sleep', sleep)
    identity = {'session_id': 'native-1', 'script_version': 'perf1'}
    (tmp_path / 'bridge-ready.json').write_text(json.dumps(identity))
    def released(**changes):
        (tmp_path / 'bridge-ready.json').unlink(missing_ok=True)
        (tmp_path / 'stop').unlink(missing_ok=True)
        (tmp_path / 'bridge-stopped.json').write_text(json.dumps({
            **identity, 'reason': 'coordinator_stop_file', **changes}))
    return tmp_path, clock, released, sleep


def test_unresponsive_loop_does_not_authorize_legacy_dispatch(native):
    work, _, _, _ = native
    coord._file_ping.side_effect = TimeoutError('slow editor')
    with pytest.raises(TimeoutError, match='not confirmed'):
        coord.stop_eda()
    assert not (work / 'stop').exists()


def test_consumed_stop_is_not_a_completion_acknowledgement(native, monkeypatch):
    work, clock, _, sleep = native
    def consume(seconds):
        sleep(seconds)
        (work / 'stop').unlink(missing_ok=True)
    monkeypatch.setattr(coord.time, 'sleep', consume)
    with pytest.raises(TimeoutError, match='not confirmed'):
        coord.stop_eda()
    assert clock[0] >= 5


def test_waits_for_cleanup_longer_than_old_half_second_delay(native, monkeypatch):
    work, clock, released, sleep = native
    def finish(seconds):
        sleep(seconds)
        (work / 'stop').unlink(missing_ok=True)
        if clock[0] >= 1.5:
            released()
    monkeypatch.setattr(coord.time, 'sleep', finish)
    coord.stop_eda()
    assert clock[0] >= 1.5
    assert not (work / 'bridge-ready.json').exists()


@pytest.mark.parametrize('changes', [{'session_id': 'old'}, {'script_version': 'old'},
                                   {'reason': 'dispatcher_exception'}])
def test_wrong_session_or_error_shutdown_cannot_authorize_handover(native, monkeypatch, changes):
    _, _, released, sleep = native
    def finish(seconds):
        sleep(seconds)
        released(**changes)
    monkeypatch.setattr(coord.time, 'sleep', finish)
    with pytest.raises(TimeoutError, match='not confirmed'):
        coord.stop_eda()


def test_already_confirmed_idle_release_needs_no_ping_or_stop(native):
    work, _, released, _ = native
    coord._remember_session({'pid': 77})
    released(reason='engine_idle_release')
    coord.stop_eda()
    coord._file_ping.assert_not_called()
    assert not (work / 'stop').exists()


def test_idle_release_racing_a_ping_timeout_is_accepted_only_with_proof(native):
    _, _, released, _ = native
    coord._remember_session({'pid': 77})
    def timeout(*a, **kw):
        released(reason='engine_idle_release')
        raise TimeoutError()
    coord._file_ping.side_effect = timeout
    coord.stop_eda()
