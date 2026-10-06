# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for persisted fault state (roadmap 1.2 dashboard recovery banner)."""

from __future__ import annotations

from eda_agent.bridge.fault_state import (
    FAULT_FILE,
    clear_fault,
    read_fault,
    record_fault,
)
from eda_agent.bridge.recovery import DEAD_LOOP, recovery_guidance


def test_record_read_clear_roundtrip(tmp_path):
    assert read_fault(tmp_path) is None  # healthy: nothing recorded

    record_fault(tmp_path, recovery_guidance(DEAD_LOOP), when="2026-07-02T12:00:00")
    payload = read_fault(tmp_path)
    assert payload is not None
    assert payload["recovery"]["fault"] == DEAD_LOOP
    assert payload["recovery"]["steps"]
    assert payload["when"] == "2026-07-02T12:00:00"
    assert (tmp_path / FAULT_FILE).exists()

    clear_fault(tmp_path)
    assert read_fault(tmp_path) is None
    assert not (tmp_path / FAULT_FILE).exists()


def test_read_tolerates_corrupt_file(tmp_path):
    (tmp_path / FAULT_FILE).write_text("{not json", encoding="utf-8")
    assert read_fault(tmp_path) is None  # no raise


def test_clear_is_idempotent(tmp_path):
    clear_fault(tmp_path)  # nothing to clear -> no raise
    clear_fault(tmp_path)


def test_bridge_first_success_sweeps_stale_fault(tmp_path):
    # A fault persisted by a PREVIOUS process must be cleared on the first
    # successful command of a fresh bridge, even though its in-memory flag
    # starts False -- otherwise a server restart strands a stale banner.
    record_fault(tmp_path, recovery_guidance(DEAD_LOOP))
    assert read_fault(tmp_path) is not None

    from eda_agent.bridge.altium_bridge import AltiumBridge
    bridge = AltiumBridge()
    assert bridge._fault_recorded is False
    bridge._clear_fault_if_any(tmp_path)          # first success sweeps it
    assert read_fault(tmp_path) is None


def test_bridge_clear_is_noop_after_first_success(tmp_path, monkeypatch):
    # After the one-time stale sweep, healthy steady-state calls with no
    # recorded fault must not touch disk.
    from eda_agent.bridge import fault_state
    from eda_agent.bridge.altium_bridge import AltiumBridge

    bridge = AltiumBridge()
    bridge._clear_fault_if_any(tmp_path)          # first success: the one sweep

    called = {"n": 0}
    real_clear = fault_state.clear_fault

    def _spy(ws):
        called["n"] += 1
        return real_clear(ws)

    monkeypatch.setattr(fault_state, "clear_fault", _spy)
    bridge._clear_fault_if_any(tmp_path)          # steady state
    bridge._clear_fault_if_any(tmp_path)
    assert called["n"] == 0                        # no disk I/O


def test_bridge_note_then_clear_flag(tmp_path, monkeypatch):
    from eda_agent.bridge.altium_bridge import AltiumBridge
    bridge = AltiumBridge()
    bridge._note_fault(tmp_path, recovery_guidance(DEAD_LOOP))
    assert bridge._fault_recorded is True
    assert read_fault(tmp_path) is not None

    bridge._clear_fault_if_any(tmp_path)
    assert bridge._fault_recorded is False
    assert read_fault(tmp_path) is None
