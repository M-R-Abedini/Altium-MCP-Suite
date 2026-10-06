# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""E2e coverage for the audit dispatch category (roadmap 1.3).

The audit handlers in the simulator are SHAPE mirrors only (canned
{checked, violations, items}); the real detection logic lives in Audit.pas
and is not reimplemented (simulator caveat). The value here is exercising
design_lint_report's ORCHESTRATION and a standalone audit_* tool through the
real bridge, not the detection logic itself.
"""

from __future__ import annotations

import asyncio
import json

import pytest


def _reg(e2e_bridge, monkeypatch):
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_all_tools
    # design_lint_report + audit tools resolve the bridge via their module's
    # get_bridge at call time.
    monkeypatch.setattr("eda_agent.tools.review.get_bridge", lambda: e2e_bridge)
    monkeypatch.setattr("eda_agent.tools.audit.get_bridge", lambda: e2e_bridge)
    m = FastMCP("t")
    register_all_tools(m)
    return m


def _call(mcp, name, args=None):
    r = asyncio.run(mcp.call_tool(name, args or {}))
    c = r[0] if isinstance(r, tuple) else r
    return json.loads(c[0].text)


def test_standalone_audit_tool_clean(e2e_bridge, monkeypatch):
    mcp = _reg(e2e_bridge, monkeypatch)
    res = _call(mcp, "audit_find_via_antennas")
    assert res["checked"] == 1
    assert res["violations"] == 0


def test_lint_report_orchestration_runs_all_sections(e2e_bridge, monkeypatch):
    mcp = _reg(e2e_bridge, monkeypatch)
    res = _call(mcp, "design_lint_report")
    # Many audit sections ran through the real bridge + audit dispatch.
    assert res["totals"]["checks_run"] > 5
    # The Pascal-backed audit shape-mirrors are all clean (0 violations);
    # BOM-side Python checks may legitimately flag the mock IC's missing
    # decoupling, so only assert the audit-dispatched sections here.
    assert res["summary"]["find_via_antennas"]["violations"] == 0
    assert res["summary"]["find_designator_collisions"]["violations"] == 0


def test_lint_report_surfaces_seeded_violation(e2e_bridge, altium_sim, monkeypatch):
    altium_sim.audit_results["find_via_antennas"] = {
        "checked": 10, "violations": 2,
        "items": [{"via": "V1"}, {"via": "V2"}],
    }
    mcp = _reg(e2e_bridge, monkeypatch)
    res = _call(mcp, "design_lint_report")
    assert res["summary"]["find_via_antennas"]["violations"] == 2
    assert res["totals"]["violations"] >= 2


def test_lint_report_checks_filter_through_bridge(e2e_bridge, monkeypatch):
    mcp = _reg(e2e_bridge, monkeypatch)
    res = _call(mcp, "design_lint_report", {"checks": ["find_via_antennas"]})
    assert set(res["summary"]) == {"find_via_antennas"}
