# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The EDA-agnostic offline plan-analysis tools shared to the KiCad backend."""

from __future__ import annotations

import asyncio
import json

import pytest


def _payload(result):
    if isinstance(result, tuple):
        result = result[0]
    return json.loads(result[0].text)


@pytest.fixture(scope="module")
def plan_mcp():
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_plan_tools
    m = FastMCP("test")
    register_plan_tools(m)
    return m


def _call(mcp, name, **kw):
    return _payload(asyncio.run(mcp.call_tool(name, kw)))


_PLAN = {
    "spec": "t", "summary": "t", "sheets": [{"name": "main"}], "zones": [],
    "parts": [{"refdes": "R1", "lib_ref": "R", "value": "10k",
               "status": "existing"},
              {"refdes": "R2", "lib_ref": "R", "value": "10k",
               "status": "existing"}],
    "nets": [{"name": "A", "pins": [{"refdes": "R1", "pin": "1"},
                                    {"refdes": "R2", "pin": "1"}]},
             {"name": "B", "pins": [{"refdes": "R1", "pin": "2"},
                                    {"refdes": "R2", "pin": "2"}]}],
    "bom": [], "design_rules": [], "open_questions": [],
}


def test_kicad_backend_registers_plan_tools():
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_backend
    m = FastMCP("test")
    register_backend(m, "kicad")
    names = {t.name for t in asyncio.run(m.list_tools())}
    assert {"design_validate_plan", "design_review_plan"} <= names


def test_both_backend_does_not_double_register_plan():
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_backend
    m = FastMCP("test")
    register_backend(m, "both")  # would raise on duplicate tool name
    names = [t.name for t in asyncio.run(m.list_tools())]
    assert names.count("design_review_plan") == 1


def test_validate_plan_ok(plan_mcp):
    r = _call(plan_mcp, "design_validate_plan", plan_json=_PLAN)
    assert r["ok"] is True and "2 parts" in r["summary"]


def test_validate_plan_bad_json(plan_mcp):
    r = _call(plan_mcp, "design_validate_plan", plan_json="{not json")
    assert r["ok"] is False and any("invalid JSON" in e for e in r["errors"])


def test_review_plan_bundles_sections(plan_mcp):
    r = _call(plan_mcp, "design_review_plan", plan_json=_PLAN)
    assert r["ok"] is True
    assert r["stats"]["part_count"] == 2 and r["stats"]["net_count"] == 2
    assert set(r) >= {"stats", "erc", "circuits", "placement_constraints",
                      "net_classes"}
