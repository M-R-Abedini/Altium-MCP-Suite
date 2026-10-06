# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""End-to-end autonomy harness test through the real MCP tool layer (1.4).

The unit tests cover session.py / state_machine.py directly. This drives the
harness the way a client does: through design_session_start /
design_next_action / design_session_log via call_tool, so the tool wrappers
(session resolution, asdict serialization, workspace-path handling) are
exercised too, not just the underlying modules.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest


@pytest.fixture
def mcp(tmp_path, monkeypatch):
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_all_tools

    # Point the session store at a temp workspace (tools import get_config
    # lazily, so patching the module function takes effect at call time).
    monkeypatch.setattr(
        "eda_agent.config.get_config",
        lambda: SimpleNamespace(workspace_dir=tmp_path),
    )
    m = FastMCP("test")
    register_all_tools(m)
    return m


def _call(mcp, name, args):
    result = asyncio.run(mcp.call_tool(name, args))
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


def test_full_autonomous_walk_completes(mcp):
    started = _call(mcp, "design_session_start", {"requirement": "2-layer blinker"})
    sid = started["session_id"]
    assert started["state"]["next_stage"] == "requirement"

    seen_stages = []
    for _ in range(40):  # generous bound; pipeline is 13 stages
        action = _call(mcp, "design_next_action", {"session_id": sid})
        if action["status"] == "complete":
            break
        assert action["status"] in ("proceed", "retry")
        stage = action["stage"]
        seen_stages.append(stage)
        # The action carries actionable guidance for the client.
        assert action["suggested_tools"] and action["exit_gate"]
        _call(mcp, "design_session_log", {
            "event": "stage_result", "session_id": sid,
            "stage": stage, "status": "ok",
        })
    else:
        pytest.fail("pipeline did not complete within the bound")

    # Walked all 13 stages in order and finished.
    from eda_agent.design.session import STAGES
    assert seen_stages == list(STAGES)
    final = _call(mcp, "design_next_action", {"session_id": sid})
    assert final["status"] == "complete"


def test_blocked_then_resume_through_tools(mcp):
    started = _call(mcp, "design_session_start", {"requirement": "buck"})
    sid = started["session_id"]

    # Client hits a question at the requirement stage.
    _call(mcp, "design_session_log", {
        "event": "blocked", "session_id": sid,
        "stage": "requirement", "question": "What input voltage?",
    })
    action = _call(mcp, "design_next_action", {"session_id": sid})
    assert action["status"] == "blocked"
    assert action["open_question"] == "What input voltage?"

    # User answers; the run proceeds.
    _call(mcp, "design_session_log", {
        "event": "resolved", "session_id": sid, "text": "5V USB",
    })
    action = _call(mcp, "design_next_action", {"session_id": sid})
    assert action["status"] in ("proceed", "retry")
    assert action["stage"] == "requirement"


def test_resume_reports_next_action(mcp):
    started = _call(mcp, "design_session_start", {"requirement": "x"})
    sid = started["session_id"]
    _call(mcp, "design_session_log", {
        "event": "stage_result", "session_id": sid,
        "stage": "requirement", "status": "ok",
    })
    resume = _call(mcp, "design_session_resume", {"session_id": sid})
    assert "architecture" in resume["guidance"]
    assert resume["state"]["next_stage"] == "architecture"
