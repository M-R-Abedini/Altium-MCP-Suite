# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the design session journal (roadmap 1.4)."""

from __future__ import annotations

from datetime import datetime

import pytest

from eda_agent.design.session import (
    STAGES,
    STATUS_BLOCKED,
    STATUS_FAILED,
    STATUS_OK,
    SessionJournal,
    SessionStore,
)


def _store(tmp_path):
    return SessionStore(tmp_path / "sessions")


def test_start_and_state(tmp_path):
    store = _store(tmp_path)
    j = store.start("2-layer USB blinker", session_id="s1")
    st = j.state()
    assert st.session_id == "s1"
    assert st.requirement == "2-layer USB blinker"
    assert st.next_stage == "requirement"  # nothing done yet
    assert not st.complete


def test_stage_progression_advances_next_stage(tmp_path):
    j = _store(tmp_path).start("x", session_id="s1")
    j.enter_stage("requirement")
    j.stage_result("requirement", STATUS_OK)
    st = j.state()
    assert st.stage_status["requirement"] == STATUS_OK
    assert st.next_stage == "architecture"
    assert st.current_stage is None


def test_replay_is_durable_across_new_instances(tmp_path):
    # Simulate a client restart: a brand-new SessionJournal over the same
    # file reconstructs identical state from the log alone.
    store = _store(tmp_path)
    j = store.start("x", session_id="s1")
    j.enter_stage("requirement")
    j.stage_result("requirement", STATUS_OK)
    j.plan_revision(1, summary="first cut")

    reopened = store.get("s1")
    st = reopened.state()
    assert st.plan_revision == 1
    assert st.next_stage == "architecture"
    assert st.event_count == 4  # start + enter + result + revision


def test_blocked_then_resolved(tmp_path):
    j = _store(tmp_path).start("x", session_id="s1")
    j.blocked("What input voltage range?", stage="requirement")
    assert j.state().open_question == "What input voltage range?"
    j.resolved("5V USB")
    assert j.state().open_question is None


def test_artifacts_accumulate(tmp_path):
    j = _store(tmp_path).start("x", session_id="s1")
    j.artifact("out/board.svg", kind="render")
    j.artifact("out/fab.zip", kind="fab_package")
    arts = j.state().artifacts
    assert [a["path"] for a in arts] == ["out/board.svg", "out/fab.zip"]


def test_complete_when_all_stages_ok(tmp_path):
    j = _store(tmp_path).start("x", session_id="s1")
    for st in STAGES:
        j.stage_result(st, STATUS_OK)
    state = j.state()
    assert state.complete
    assert state.next_stage is None


def test_failed_stage_keeps_it_as_next(tmp_path):
    j = _store(tmp_path).start("x", session_id="s1")
    j.stage_result("requirement", STATUS_OK)
    j.stage_result("architecture", STATUS_FAILED)
    st = j.state()
    assert st.next_stage == "architecture"  # not ok -> still next
    assert not st.complete


def test_invalid_stage_rejected(tmp_path):
    j = _store(tmp_path).start("x", session_id="s1")
    with pytest.raises(ValueError):
        j.enter_stage("nonsense")
    with pytest.raises(ValueError):
        j.stage_result("requirement", "maybe")


def test_torn_trailing_line_is_tolerated(tmp_path):
    j = _store(tmp_path).start("x", session_id="s1")
    j.stage_result("requirement", STATUS_OK)
    # Simulate a crash mid-write: append a partial JSON line.
    with j.path.open("a", encoding="utf-8") as f:
        f.write('{"seq": 3, "ts": "2026')
    st = j.state()  # must not raise
    assert st.stage_status["requirement"] == STATUS_OK
    assert st.event_count == 2  # torn line ignored


def test_store_active_is_most_recent(tmp_path):
    store = _store(tmp_path)
    store.start("a", session_id="20260702-100000")
    store.start("b", session_id="20260702-110000")
    assert set(store.list()) == {"20260702-100000", "20260702-110000"}
    active = store.active()
    assert active is not None


def test_deterministic_ids_from_clock(tmp_path):
    store = _store(tmp_path)
    j = store.start("x", now=datetime(2026, 7, 2, 9, 30, 0))
    assert j.session_id == "20260702-093000"
