# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""design_lint_report(checks=[...]) filter: folds the audit singletons (2.2)."""

from __future__ import annotations

import asyncio
import json

import pytest


class _FakeBridge:
    """Records dispatched commands; returns a benign audit-shaped payload."""

    def __init__(self):
        self.commands: list[str] = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.commands.append(command)
        if command == "project.get_bom":
            return {"components": [], "bom": []}
        return {"checked": 1, "violations": 0, "items": []}


@pytest.fixture
def mcp_with_fake(monkeypatch):
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_all_tools

    fake = _FakeBridge()
    # The tool resolves the bridge via get_bridge() at call time.
    monkeypatch.setattr("eda_agent.tools.review.get_bridge", lambda: fake)
    m = FastMCP("test")
    register_all_tools(m)
    return m, fake


def _run(mcp, **kwargs):
    result = asyncio.run(mcp.call_tool("design_lint_report", kwargs))
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


def test_full_run_dispatches_many_audits(mcp_with_fake):
    mcp, fake = mcp_with_fake
    res = _run(mcp)
    # Full sweep hits many distinct audit.* commands + the BOM fetch.
    audit_cmds = [c for c in fake.commands if c.startswith("audit.")]
    assert len(audit_cmds) > 10
    assert "project.get_bom" in fake.commands
    assert res["totals"]["checks_run"] > 10


def test_checks_filter_runs_only_named(mcp_with_fake):
    mcp, fake = mcp_with_fake
    res = _run(mcp, checks=["find_via_antennas"])
    audit_cmds = [c for c in fake.commands if c.startswith("audit.")]
    assert audit_cmds == ["audit.find_via_antennas"]
    # No BOM-side check requested -> no BOM fetch.
    assert "project.get_bom" not in fake.commands
    assert set(res["summary"]) == {"find_via_antennas"}


def test_checks_filter_bom_side_only(mcp_with_fake):
    mcp, fake = mcp_with_fake
    res = _run(mcp, checks=["find_missing_decoupling"])
    # A BOM-side check DOES trigger the single BOM fetch, but no audit.*.
    assert "project.get_bom" in fake.commands
    assert not [c for c in fake.commands if c.startswith("audit.")]
    assert set(res["summary"]) == {"find_missing_decoupling"}


def test_unknown_check_reported(mcp_with_fake):
    mcp, fake = mcp_with_fake
    res = _run(mcp, checks=["not_a_real_audit"])
    assert res["_unknown_checks"] == ["not_a_real_audit"]
