# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for headless project-level review (roadmap V1, multi-sheet)."""

from __future__ import annotations

from pathlib import Path

from eda_agent.fileio.altium_project import read_project_sheets
from eda_agent.fileio.review import review_project_file

FIXDIR = Path(__file__).resolve().parent / "integration" / "fixtures"
PRJ = FIXDIR / "EDAAgentTest.PrjPcb"
SCH = FIXDIR / "main.SchDoc"


def test_resolves_project_sheets_from_structure():
    sheets = read_project_sheets(PRJ)
    # The .PrjPcbStructure lists main.SchDoc as the top-level document.
    assert [s.name for s in sheets] == ["main.SchDoc"]
    assert all(s.exists() for s in sheets)


def test_project_review_aggregates_sheets():
    rep = review_project_file(PRJ)
    assert rep["sheet_count"] == 1
    assert rep["component_count"] == 14
    # Every finding is tagged with its sheet for a multi-sheet project.
    assert rep["findings"], "expected findings on the unfilled fixture"
    assert all(f.get("sheet") == "main.SchDoc" for f in rep["findings"])


def test_project_review_accepts_bare_schdoc():
    # Passing a .SchDoc directly falls back to single-sheet review.
    rep = review_project_file(SCH)
    assert rep["component_count"] == 14
    assert "sheet_count" not in rep  # single-sheet shape


def test_missing_structure_returns_no_sheets(tmp_path):
    fake = tmp_path / "Empty.PrjPcb"
    fake.write_text("[Design]\nVersion=1.0\n", encoding="utf-8")
    assert read_project_sheets(fake) == []


def test_design_review_file_disabled_by_default(monkeypatch):
    # The offline reader is opt-in: with no env var the tool refuses and
    # points at the preferred live-Altium tools.
    monkeypatch.delenv("EDA_AGENT_HEADLESS_REVIEW", raising=False)
    import asyncio
    import json
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_all_tools

    m = FastMCP("t")
    register_all_tools(m)
    r = asyncio.run(m.call_tool("design_review_file", {"path": str(PRJ)}))
    c = r[0] if isinstance(r, tuple) else r
    rep = json.loads(c[0].text)
    assert rep.get("disabled") is True
    assert "disabled by default" in rep["error"]
    assert "sheet_count" not in rep  # it never parsed the file


def test_design_solve_netlist_file_disabled_by_default(monkeypatch):
    monkeypatch.delenv("EDA_AGENT_HEADLESS_REVIEW", raising=False)
    import asyncio
    import json
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_all_tools

    m = FastMCP("t")
    register_all_tools(m)
    r = asyncio.run(m.call_tool("design_solve_netlist_file", {"path": str(SCH)}))
    c = r[0] if isinstance(r, tuple) else r
    rep = json.loads(c[0].text)
    assert rep.get("disabled") is True
    assert "net_count" not in rep


def test_design_solve_netlist_file_enabled(monkeypatch):
    monkeypatch.setenv("EDA_AGENT_HEADLESS_REVIEW", "1")
    import asyncio
    import json
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_all_tools

    m = FastMCP("t")
    register_all_tools(m)
    r = asyncio.run(m.call_tool("design_solve_netlist_file", {"path": str(SCH)}))
    c = r[0] if isinstance(r, tuple) else r
    rep = json.loads(c[0].text)
    assert rep["net_count"] > 0 and rep["pin_count"] > 0
    assert isinstance(rep["nets"], dict)
    # main.SchDoc is a known-shorted emit -> the ERC surfaces net_short.
    assert any(f["check"] == "net_short" for f in rep["findings"])


def test_design_bom_file_disabled_by_default(monkeypatch):
    monkeypatch.delenv("EDA_AGENT_HEADLESS_REVIEW", raising=False)
    import asyncio
    import json
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_all_tools

    m = FastMCP("t")
    register_all_tools(m)
    r = asyncio.run(m.call_tool("design_bom_file", {"path": str(SCH)}))
    c = r[0] if isinstance(r, tuple) else r
    rep = json.loads(c[0].text)
    assert rep.get("disabled") is True
    assert "line_count" not in rep


def test_design_bom_file_enabled(monkeypatch):
    monkeypatch.setenv("EDA_AGENT_HEADLESS_REVIEW", "1")
    import asyncio
    import json
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_all_tools

    m = FastMCP("t")
    register_all_tools(m)
    r = asyncio.run(m.call_tool("design_bom_file", {"path": str(PRJ)}))
    c = r[0] if isinstance(r, tuple) else r
    rep = json.loads(c[0].text)
    assert rep["part_count"] == 14 and rep["line_count"] > 0
    assert isinstance(rep["lines"], list)


def test_design_review_file_mcp_tool(monkeypatch):
    monkeypatch.setenv("EDA_AGENT_HEADLESS_REVIEW", "1")
    import asyncio
    import json
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_all_tools

    m = FastMCP("t")
    register_all_tools(m)
    r = asyncio.run(m.call_tool("design_review_file", {"path": str(PRJ)}))
    c = r[0] if isinstance(r, tuple) else r
    rep = json.loads(c[0].text)
    assert rep["sheet_count"] == 1 and rep["component_count"] == 14
    assert "summary" in rep


def test_design_review_file_bad_path_returns_error(monkeypatch):
    monkeypatch.setenv("EDA_AGENT_HEADLESS_REVIEW", "1")
    import asyncio
    import json
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_all_tools

    m = FastMCP("t")
    register_all_tools(m)
    r = asyncio.run(m.call_tool("design_review_file", {"path": "nope.SchDoc"}))
    c = r[0] if isinstance(r, tuple) else r
    rep = json.loads(c[0].text)
    assert "error" in rep and not rep.get("disabled")
