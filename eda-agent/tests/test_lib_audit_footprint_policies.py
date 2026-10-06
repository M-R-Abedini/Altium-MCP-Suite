# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the lib_audit_footprint_policies tool (bridge orchestration)."""

from __future__ import annotations

import asyncio
import json

from mcp.server.fastmcp import FastMCP

import eda_agent.tools.library as lib
from eda_agent.tools import register_all_tools


def _geometry(name):
    """Two clean SMD parts and one THT part whose drilled pad is stuck on a
    single copper layer (a real defect). C0402 silks on the wrong overlay."""
    silk_layer = "BottomOverlay" if name == "C0402" else "TopOverlay"
    if name == "THT_CONN":
        pads = [{"name": "1", "shape": "rectangular", "layer": "top", "hole": 20},
                {"name": "2", "shape": "round", "layer": "multi", "hole": 20}]
    else:
        pads = [{"name": "1", "shape": "rectangular", "layer": "top", "hole": 0},
                {"name": "2", "shape": "round", "layer": "top", "hole": 0}]
    return {"name": name, "pads": pads, "bodies": 1,
            "primitives": [{"kind": "track", "layer": silk_layer}]}


_NAMES = ["R0402", "C0402", "THT_CONN"]


class _FakeBridge:
    """Serves the library through the bulk paging command, recording each
    page request so the tool's paging behaviour can be asserted."""

    def __init__(self, page_size=None):
        self.calls = []
        self.page_size = page_size

    async def send_command_async(self, command, params=None, timeout=None):
        params = params or {}
        self.calls.append((command, dict(params), timeout))
        if command == "library.get_library_geometry":
            offset = params.get("offset", 0)
            limit = self.page_size or params.get("limit", 250)
            window = _NAMES[offset:offset + limit]
            return {"library_path": "Demo.PcbLib", "total": len(_NAMES),
                    "offset": offset, "count": len(window),
                    "footprints": [_geometry(n) for n in window]}
        return {}


def _call(args, monkeypatch, bridge=None):
    bridge = bridge or _FakeBridge()
    monkeypatch.setattr(lib, "get_bridge", lambda: bridge)
    m = FastMCP("t")
    register_all_tools(m)
    r = asyncio.run(m.call_tool("lib_audit_footprint_policies", args))
    c = r[0] if isinstance(r, tuple) else r
    return json.loads(c[0].text), bridge


def test_audit_runs_over_whole_library(monkeypatch):
    rep, _ = _call({}, monkeypatch)
    assert rep["footprint_count"] == 3
    assert rep["library_path"] == "Demo.PcbLib"
    assert "findings" in rep and "conventions" in rep and "summary" in rep


def test_audit_uses_the_bulk_command_not_per_footprint(monkeypatch):
    # The whole point of the bulk path: no per-footprint round trips.
    _, bridge = _call({}, monkeypatch)
    commands = [c for c, _, _ in bridge.calls]
    assert "library.get_footprint_pads" not in commands
    assert commands.count("library.get_library_geometry") == 1


def test_audit_pages_until_the_library_is_exhausted(monkeypatch):
    # A page size below the library size forces paging; every footprint must
    # still reach the engine exactly once.
    bridge = _FakeBridge(page_size=2)
    rep, bridge = _call({}, monkeypatch, bridge=bridge)
    assert rep["footprint_count"] == 3
    offsets = [p["offset"] for c, p, _ in bridge.calls
               if c == "library.get_library_geometry"]
    assert offsets == [0, 2]


def test_audit_passes_a_generous_timeout(monkeypatch):
    # The first page opens the PcbLib; the default timeout is too short.
    _, bridge = _call({}, monkeypatch)
    timeouts = [t for c, _, t in bridge.calls
                if c == "library.get_library_geometry"]
    assert all(t and t >= 60 for t in timeouts)


def test_audit_flags_the_bad_drill(monkeypatch):
    rep, _ = _call({}, monkeypatch)
    drill = [f for f in rep["findings"]
             if f["dimension"] == "pad_drill" and f["footprint"] == "THT_CONN"]
    assert drill and drill[0]["actual"] == "top"


def test_layer_role_checks_fire_from_live_geometry(monkeypatch):
    # With primitives flowing through, the silk-layer outlier (C0402 on
    # BottomOverlay) is caught live, and its fix is an auto layer-move.
    rep, _ = _call({}, monkeypatch)
    silk = [f for f in rep["findings"]
            if f["dimension"] == "silk_layer" and f["footprint"] == "C0402"]
    assert silk and silk[0]["expected"] == "TopOverlay"
    fix = [a for a in rep["fixes"] if a["dimension"] == "silk_layer"]
    assert fix and fix[0]["auto"] is True


def test_audit_surfaces_fix_plan(monkeypatch):
    rep, _ = _call({}, monkeypatch)
    assert "fixes" in rep
    drill_fix = [a for a in rep["fixes"] if a["dimension"] == "pad_drill"]
    assert drill_fix and drill_fix[0]["auto"] is True
    assert drill_fix[0]["params"] == {"pad": "1", "layer": "multi"}


def test_library_path_flows_to_the_bridge(monkeypatch):
    _, bridge = _call({"library_path": "X.PcbLib"}, monkeypatch)
    _, params, _ = bridge.calls[0]
    assert params["library_path"] == "X.PcbLib"


def test_explicit_policy_passthrough(monkeypatch):
    # An explicit policy dict flows to the engine (courtyard required -> every
    # footprint lacks courtyard geometry here, so all get flagged).
    rep, _ = _call({"policy": {"courtyard": True}}, monkeypatch)
    court = [f for f in rep["findings"] if f["dimension"] == "courtyard"]
    assert len(court) == 3  # none have courtyard geometry
