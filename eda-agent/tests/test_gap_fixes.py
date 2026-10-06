# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the gap-finding-run fixes (Python side).

Covers the two Python-side fixes from the autonomous-project gap run:
  - design_snapshot_inventory: name_filter / limit_per_library /
    include_descriptions trimming (Gap #7: unbounded output).
  - place_sch_components_from_library: document_path focuses the target
    sheet before placing, and aborts on focus failure (Gap #5: parts
    silently landing on the wrong document).

The Pascal-side fixes (pcb_place_component, get_object_count doc: scope,
ePin pin number, get_active_document fallbacks) need a live Altium
redeploy to verify and are not covered here.
"""

from __future__ import annotations

import pytest


def _capture(module, register_fn_name: str):
    captured = {}

    class DummyMcp:
        def tool(self):
            def decorator(fn):
                captured[fn.__name__] = fn
                return fn
            return decorator

    getattr(module, register_fn_name)(DummyMcp())
    return captured


# --------------------------------------------------------------------
# Gap #7: design_snapshot_inventory trimming
# --------------------------------------------------------------------

class _FakeInventory:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self):
        return self._payload


def _big_inventory():
    res = [{"lib_ref": f"RES {i}K 1% 0603", "description": "x" * 80,
            "pin_count": 2} for i in range(200)]
    res.append({"lib_ref": "NE555 SOIC-8", "description": "timer",
                "pin_count": 8})
    return {"libraries": [{"path": "C:\\L\\R.SchLib", "components": res}]}


@pytest.fixture
def _patched_design(monkeypatch, tmp_path):
    from eda_agent.tools import design as d
    monkeypatch.setattr(d, "snapshot_live",
                        lambda paths: _FakeInventory(_big_inventory()))

    class _Cfg:
        workspace_dir = tmp_path

    monkeypatch.setattr("eda_agent.config.get_config", lambda: _Cfg())
    return _capture(d, "register_design_tools")["design_snapshot_inventory"]


@pytest.mark.asyncio
async def test_inventory_default_caps_per_library(_patched_design):
    out = await _patched_design(library_paths=["C:\\L\\R.SchLib"])
    lib = out["libraries"][0]
    assert lib["total"] == 201
    assert lib["returned"] == 60           # default limit_per_library
    assert len(lib["components"]) == 60


@pytest.mark.asyncio
async def test_inventory_name_filter(_patched_design):
    out = await _patched_design(library_paths=["C:\\L\\R.SchLib"],
                                name_filter="ne555")
    lib = out["libraries"][0]
    assert lib["total"] == 1
    assert lib["components"][0]["lib_ref"] == "NE555 SOIC-8"


@pytest.mark.asyncio
async def test_inventory_can_drop_descriptions(_patched_design):
    out = await _patched_design(library_paths=["C:\\L\\R.SchLib"],
                                include_descriptions=False, limit_per_library=3)
    for c in out["libraries"][0]["components"]:
        assert "description" not in c


@pytest.mark.asyncio
async def test_inventory_caches_full_to_disk(_patched_design, tmp_path):
    await _patched_design(library_paths=["C:\\L\\R.SchLib"], limit_per_library=5)
    cached = tmp_path / "inventory.json"
    assert cached.exists()
    import json
    full = json.loads(cached.read_text(encoding="utf-8"))
    # The cache is the FULL unfiltered inventory, not the trimmed view.
    assert len(full["libraries"][0]["components"]) == 201


# --------------------------------------------------------------------
# Gap #5: placement focuses the target document first
# --------------------------------------------------------------------

class _RecordingBridge:
    def __init__(self, focus_ok=True):
        self.calls = []
        self._focus_ok = focus_ok

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, params or {}))
        if command == "application.set_active_document":
            return {"success": self._focus_ok}
        return {"placed": 1, "failed": 0, "total": 1}


def _place_tool(monkeypatch, bridge):
    from eda_agent.tools import generic as g
    monkeypatch.setattr(g, "get_bridge", lambda: bridge)
    return _capture(g, "register_generic_tools")["sch_place_components"]


@pytest.mark.asyncio
async def test_placement_focuses_target_doc_first(monkeypatch):
    bridge = _RecordingBridge(focus_ok=True)
    tool = _place_tool(monkeypatch, bridge)
    await tool(
        placements=[{"lib_reference": "NE555 SOIC-8", "x": 1000, "y": 1000,
                     "designator": "U1"}],
        document_path="C:\\proj\\Blinker.SchDoc",
    )
    cmds = [c for c, _ in bridge.calls]
    assert cmds[0] == "application.set_active_document"
    assert cmds[1] == "generic.place_sch_components_from_library"


@pytest.mark.asyncio
async def test_placement_aborts_if_focus_fails(monkeypatch):
    bridge = _RecordingBridge(focus_ok=False)
    tool = _place_tool(monkeypatch, bridge)
    out = await tool(
        placements=[{"lib_reference": "NE555 SOIC-8", "x": 1000, "y": 1000}],
        document_path="C:\\proj\\Blinker.SchDoc",
    )
    assert out["error"] == "FOCUS_FAILED"
    assert out["placed"] == 0
    # It must NOT have attempted the placement after a failed focus.
    cmds = [c for c, _ in bridge.calls]
    assert "generic.place_sch_components_from_library" not in cmds


@pytest.mark.asyncio
async def test_placement_without_doc_path_skips_focus(monkeypatch):
    bridge = _RecordingBridge()
    tool = _place_tool(monkeypatch, bridge)
    await tool(placements=[{"lib_reference": "X", "x": 0, "y": 0}])
    cmds = [c for c, _ in bridge.calls]
    assert "application.set_active_document" not in cmds
    assert cmds == ["generic.place_sch_components_from_library"]
