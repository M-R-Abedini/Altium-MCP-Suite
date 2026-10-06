# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the autonomy state machine (design_next_action core)."""

from __future__ import annotations

import asyncio

import pytest

from eda_agent.design.session import STAGES, STATUS_FAILED, STATUS_OK, SessionStore
from eda_agent.design.state_machine import (
    BLOCKED,
    COMPLETE,
    MAX_STAGE_ATTEMPTS,
    PROCEED,
    RETRY,
    STAGE_PLAYBOOKS,
    next_action,
)


def _journal(tmp_path):
    return SessionStore(tmp_path / "s").start("blinker", session_id="s1")


def test_fresh_session_proceeds_to_first_stage(tmp_path):
    j = _journal(tmp_path)
    act = next_action(j.state())
    assert act.status == PROCEED
    assert act.stage == STAGES[0] == "requirement"
    assert act.suggested_tools  # non-empty playbook
    assert act.exit_gate


def test_advances_through_stages(tmp_path):
    j = _journal(tmp_path)
    j.stage_result("requirement", STATUS_OK)
    act = next_action(j.state())
    assert act.status == PROCEED
    assert act.stage == "architecture"


def test_open_question_blocks_everything(tmp_path):
    j = _journal(tmp_path)
    j.blocked("What supply voltage?", stage="requirement")
    act = next_action(j.state())
    assert act.status == BLOCKED
    assert act.open_question == "What supply voltage?"


def test_failed_stage_becomes_retry(tmp_path):
    j = _journal(tmp_path)
    j.stage_result("requirement", STATUS_FAILED)
    act = next_action(j.state())
    assert act.status == RETRY
    assert act.stage == "requirement"
    assert act.attempt == 1


def test_repeated_failure_escalates_to_blocked(tmp_path):
    j = _journal(tmp_path)
    for _ in range(MAX_STAGE_ATTEMPTS):
        j.stage_result("requirement", STATUS_FAILED)
    act = next_action(j.state())
    assert act.status == BLOCKED
    assert act.open_question is not None
    assert "requirement" in act.guidance


def test_all_stages_ok_is_complete(tmp_path):
    j = _journal(tmp_path)
    for st in STAGES:
        j.stage_result(st, STATUS_OK)
    act = next_action(j.state())
    assert act.status == COMPLETE
    assert act.stage is None


def test_every_stage_has_a_playbook():
    for st in STAGES:
        assert st in STAGE_PLAYBOOKS, f"stage {st} missing a playbook"
        play = STAGE_PLAYBOOKS[st]
        assert play["goal"] and play["tools"] and play["exit_gate"]


def test_no_extra_playbook_stages():
    assert set(STAGE_PLAYBOOKS) == set(STAGES)


def test_suggested_tools_are_real_tools():
    # Guard against playbook tool-name drift as the surface evolves.
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_all_tools

    mcp = FastMCP("t")
    register_all_tools(mcp)
    registered = {t.name for t in asyncio.run(mcp.list_tools())}

    referenced = {tool for play in STAGE_PLAYBOOKS.values() for tool in play["tools"]}
    missing = sorted(referenced - registered)
    assert not missing, f"playbooks reference non-existent tools: {missing}"
