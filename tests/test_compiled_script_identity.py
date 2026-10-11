"""Preflight must not dispatch edits through a different source generation."""
from unittest.mock import Mock

import pytest
import bridge_coordination as coord


def preflight(monkeypatch, tmp_path, projects):
    expected = str(tmp_path / 'current' / 'AltiumMCP-EDA.PrjScr')
    monkeypatch.setenv('ALTIUM_MCP_EDA_SCRIPT', expected)
    ensure = Mock(side_effect=[{'pong': 'old'}, {'pong': 'new'}])
    stop = Mock()
    monkeypatch.setattr(coord, '_ensure', ensure)
    monkeypatch.setattr(coord, 'stop_eda', stop)
    monkeypatch.setattr(coord, '_read_marker', Mock(side_effect=[
        {'project_path': expected if p == 'current' else p} for p in projects]))
    return ensure, stop


def test_same_version_different_sources_require_confirmed_stop(monkeypatch, tmp_path):
    ensure, stop = preflight(monkeypatch, tmp_path, ['old.PrjScr', 'current'])
    assert coord.ensure_eda(None, None) == {'pong': 'new'}
    stop.assert_called_once()
    assert ensure.call_count == 2


def test_matching_sources_are_not_restarted(monkeypatch, tmp_path):
    ensure, stop = preflight(monkeypatch, tmp_path, ['current'])
    assert coord.ensure_eda(None, None) == {'pong': 'old'}
    stop.assert_not_called()
    ensure.assert_called_once()


@pytest.mark.parametrize('project', ['still-old.PrjScr', None, {'bad': 'type'}])
def test_restarted_generation_must_match_before_dispatch(monkeypatch, tmp_path, project):
    preflight(monkeypatch, tmp_path, ['old.PrjScr', project])
    with pytest.raises(RuntimeError, match='no design command was queued'):
        coord.ensure_eda(None, None)


def test_stop_failure_prevents_a_second_launch(monkeypatch, tmp_path):
    ensure, stop = preflight(monkeypatch, tmp_path, ['old.PrjScr'])
    stop.side_effect = TimeoutError('Native shutdown unconfirmed')
    with pytest.raises(TimeoutError, match='unconfirmed'):
        coord.ensure_eda(None, None)
    ensure.assert_called_once()
