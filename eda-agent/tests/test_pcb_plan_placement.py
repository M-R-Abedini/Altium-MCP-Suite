# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Integration tests for the pcb_plan_placement MCP tool.

No Altium: a routing fake bridge supplies pcb.get_components,
pcb.get_board_outline, project.get_connectivity_batch, and the board-wide
ePadObject query. These lock down the tool wiring and -- most
importantly -- the rotation geometry (pad -> component spatial join,
back-rotation to rotation-0 pin offsets, and the centroid->origin move
math), which cannot be checked live here.
"""

from __future__ import annotations

import pytest

from eda_agent.tools import pcb as pcb_module


def _capture_tool(monkeypatch, fake_bridge):
    monkeypatch.setattr(pcb_module, "get_bridge", lambda: fake_bridge)
    captured = {}

    class DummyMcp:
        def tool(self):
            def decorator(fn):
                captured[fn.__name__] = fn
                return fn
            return decorator

    pcb_module.register_pcb_tools(DummyMcp())
    return captured["pcb_plan_placement"]


class _RoutingBridge:
    """Fake bridge that answers each command from a scripted scenario."""

    def __init__(self, components, outline, connectivity, pads):
        self._components = components
        self._outline = outline
        self._connectivity = connectivity
        self._pads = pads
        self.calls: list[tuple[str, dict]] = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, params or {}))
        if command == "pcb.get_components":
            return {"components": self._components}
        if command == "pcb.get_board_outline":
            return {"bounding_rect": self._outline}
        if command == "project.get_connectivity_batch":
            return {"components": self._connectivity}
        if command == "generic.query_objects":
            return {"objects": self._pads}
        if command == "pcb.batch_move_components":
            return {"moves_applied": params.get("moves", "").count("|") + 1}
        return {"ok": True}


def _bbox(cx, cy, w, h):
    return {
        "x1": cx - w / 2, "y1": cy - h / 2,
        "x2": cx + w / 2, "y2": cy + h / 2,
        "width": w, "height": h,
    }


def _scenario():
    """A horizontal 2-pin R1 between an anchor above (net A) and below
    (net B). Rotating R1 90 deg aligns its pins with the anchors."""
    components = [
        {"designator": "R1", "x": 1000, "y": 1000, "rotation": 0,
         "layer": "TopLayer", "bbox": _bbox(1000, 1000, 700, 200)},
        {"designator": "A1", "x": 1000, "y": 2000, "rotation": 0,
         "layer": "TopLayer", "bbox": _bbox(1000, 2000, 100, 100)},
        {"designator": "A2", "x": 1000, "y": 0, "rotation": 0,
         "layer": "TopLayer", "bbox": _bbox(1000, 0, 100, 100)},
    ]
    outline = {"left": 0, "bottom": -500, "right": 2000, "top": 2500}
    connectivity = [
        {"designator": "R1", "pins": [{"net": "A"}, {"net": "B"}]},
        {"designator": "A1", "pins": [{"net": "A"}]},
        {"designator": "A2", "pins": [{"net": "B"}]},
    ]
    pads = [
        {"X": "1300", "Y": "1000", "Net": "A"},   # R1 pin A (right end)
        {"X": "700", "Y": "1000", "Net": "B"},    # R1 pin B (left end)
        {"X": "1000", "Y": "2000", "Net": "A"},   # A1
        {"X": "1000", "Y": "0", "Net": "B"},      # A2
    ]
    return _RoutingBridge(components, outline, connectivity, pads)


@pytest.mark.asyncio
async def test_dry_run_rotates_two_pin_part(monkeypatch):
    bridge = _scenario()
    tool = _capture_tool(monkeypatch, bridge)
    out = await tool(designators=["R1"])  # only R1 movable; anchors fixed

    assert out["dry_run"] is True
    assert out["pin_count"] == 4          # 2 (R1) + 1 (A1) + 1 (A2)
    assert out["movable_count"] == 1
    assert out["fixed_count"] == 2
    # R1 should be re-oriented to point its pins at the anchors.
    assert out["rotated_count"] == 1
    r1_moves = [m for m in out["moves"] if m["designator"] == "R1"]
    assert len(r1_moves) == 1
    assert r1_moves[0]["to"]["rotation"] in (90.0, 270.0)
    # Pin-aware HPWL must not get worse.
    assert out["hpwl_after"] <= out["hpwl_before"]
    assert out["overlap_pairs_after"] == 0


@pytest.mark.asyncio
async def test_optimize_rotation_false_skips_pad_query(monkeypatch):
    bridge = _scenario()
    tool = _capture_tool(monkeypatch, bridge)
    out = await tool(designators=["R1"], optimize_rotation=False)

    assert out["pin_count"] == 0
    assert out["rotated_count"] == 0
    # No pad query issued when rotation optimization is off.
    assert not any(c == "generic.query_objects" for c, _ in bridge.calls)


@pytest.mark.asyncio
async def test_apply_emits_rotation_in_batch_op(monkeypatch):
    bridge = _scenario()
    tool = _capture_tool(monkeypatch, bridge)
    out = await tool(designators=["R1"], apply=True)

    assert out["dry_run"] is False
    move_calls = [p for c, p in bridge.calls if c == "pcb.batch_move_components"]
    assert len(move_calls) == 1
    ops = move_calls[0]["moves"]
    assert "R1," in ops
    # Rotation field present (90.0 or 270.0) in the packed op.
    assert "90.0" in ops or "270.0" in ops


@pytest.mark.asyncio
async def test_no_board_outline_falls_back_to_error(monkeypatch):
    bridge = _scenario()
    bridge._outline = None
    tool = _capture_tool(monkeypatch, bridge)
    out = await tool(designators=["R1"], region=None)
    assert out.get("error") == "NO_BOARD_OUTLINE"


@pytest.mark.asyncio
async def test_explicit_region_skips_outline_query(monkeypatch):
    bridge = _scenario()
    tool = _capture_tool(monkeypatch, bridge)
    out = await tool(
        designators=["R1"],
        region={"x1": 0, "y1": -500, "x2": 2000, "y2": 2500},
    )
    assert out["region"] == {"x1": 0.0, "y1": -500.0, "x2": 2000.0, "y2": 2500.0}
    assert not any(c == "pcb.get_board_outline" for c, _ in bridge.calls)


@pytest.mark.asyncio
async def test_refine_engine_reports_objective(monkeypatch):
    bridge = _scenario()
    tool = _capture_tool(monkeypatch, bridge)
    out = await tool(designators=["R1"])
    assert out["engine"] == "refine"
    report = out["objective_report"]
    # Every un-weighted term plus the aggregate is present.
    for key in ("hpwl", "via", "cong", "clear", "edge", "decap",
                "conn", "therm", "weighted_total", "legal", "utilization"):
        assert key in report
    # Single-layer top-side placement: the via term is structurally zero.
    assert report["via"] == 0


@pytest.mark.asyncio
async def test_construct_engine_places_and_reports(monkeypatch):
    bridge = _scenario()
    tool = _capture_tool(monkeypatch, bridge)
    out = await tool(engine="construct")
    assert out["engine"] == "construct"
    assert out["dry_run"] is True
    # Same back-compatible contract shape as the refine path.
    for key in ("hpwl_before", "hpwl_after", "overlap_pairs_after",
                "moves", "objective_report"):
        assert key in out
    assert "weighted_total" in out["objective_report"]


@pytest.mark.asyncio
async def test_construct_engine_restarts_keeps_contract(monkeypatch):
    bridge = _scenario()
    tool = _capture_tool(monkeypatch, bridge)
    # Several seeded restarts route through the best-of-N wrapper; the result
    # must keep the same contract and be no worse than a single run.
    single = await tool(engine="construct", restarts=1)
    multi = await tool(engine="construct", restarts=3)
    assert multi["engine"] == "construct"
    for key in ("hpwl_before", "hpwl_after", "overlap_pairs_after",
                "moves", "objective_report"):
        assert key in multi
    assert (multi["objective_report"]["weighted_total"]
            <= single["objective_report"]["weighted_total"] + 1e-6)


@pytest.mark.asyncio
async def test_critical_nets_weight_reaches_the_objective(monkeypatch):
    """Marking a net critical must scale its pull inside the solver -- the
    reported weighted objective for the same board rises when one of its two
    nets is weighted up, proving the param flows through to PlaceNet.weight."""
    base = await _capture_tool(monkeypatch, _scenario())(engine="construct")
    weighted = await _capture_tool(monkeypatch, _scenario())(
        engine="construct", critical_nets=["A"], critical_weight=8.0
    )
    assert weighted["objective_report"]["legal"] is True
    # Net A's wirelength now counts 8x, so the weighted HPWL is strictly higher.
    assert (weighted["objective_report"]["hpwl"]
            > base["objective_report"]["hpwl"] + 1e-6)


@pytest.mark.asyncio
async def test_net_length_report_lists_longest_and_critical(monkeypatch):
    """The placement result carries a per-net wirelength diagnostic: the
    longest nets (sorted, descending span) and the achieved span of any net
    the caller flagged critical."""
    out = await _capture_tool(monkeypatch, _scenario())(
        engine="construct", critical_nets=["A"]
    )
    rep = out["net_length_report"]
    spans = [n["span_mils"] for n in rep["longest_nets"]]
    assert spans == sorted(spans, reverse=True)          # descending
    assert all("net" in n and "span_mils" in n for n in rep["longest_nets"])
    # The flagged critical net's achieved span is echoed back.
    assert "A" in rep["critical_net_spans"]
    assert rep["critical_net_spans"]["A"] >= 0.0


@pytest.mark.asyncio
async def test_summary_synthesises_the_reports(monkeypatch):
    """A one-line summary string distils legality, utilization, routability
    and (for construct) the suggested board so the planner can judge at a
    glance without parsing every report."""
    out = await _capture_tool(monkeypatch, _scenario())(engine="construct")
    s = out["summary"]
    assert isinstance(s, str) and s
    low = s.lower()
    assert "legal" in low
    assert "utilization" in low
    assert "ratsnest" in low or "crossing" in low
    # construct sizes a board -> the summary names it; refine does not.
    assert "suggested board" in low
    ref = await _capture_tool(monkeypatch, _scenario())(
        designators=["R1"], engine="refine")
    assert "suggested board" not in ref["summary"].lower()


@pytest.mark.asyncio
async def test_edge_parts_seats_part_on_specified_edge(monkeypatch):
    """Tagging a part via edge_parts seats it against that board edge (a
    connector needing cable access) and activates the connector term. Without
    it the part is interior and the connector term is zero."""
    base = await _capture_tool(monkeypatch, _scenario())(engine="construct")
    edged = await _capture_tool(monkeypatch, _scenario())(
        engine="construct", edge_parts={"A1": "L"})

    assert edged["objective_report"]["legal"] is True
    # A1 was not a connector before -> conn term zero; tagging activates it.
    assert base["objective_report"]["conn"] == 0.0
    assert edged["objective_report"]["conn"] > 0.0

    def _x(out, ref):
        m = {mv["designator"]: mv["to"] for mv in out["moves"]}
        return m[ref]["x"] if ref in m else None

    ax_edge = _x(edged, "A1")
    reg = edged["region"]
    # A1 sits in the LEFT half of the board (seated against the left edge).
    assert ax_edge is not None
    assert ax_edge < (reg["x1"] + reg["x2"]) / 2.0


@pytest.mark.asyncio
async def test_edge_parts_invalid_band_ignored(monkeypatch):
    """An unrecognised band string is ignored (no crash, part stays free)."""
    out = await _capture_tool(monkeypatch, _scenario())(
        engine="construct", edge_parts={"A1": "diagonal"})
    assert out["objective_report"]["legal"] is True
    assert out["objective_report"]["conn"] == 0.0   # not tagged -> no edge part


@pytest.mark.asyncio
async def test_ratsnest_report_present_in_output(monkeypatch):
    """The placement result carries a ratsnest routability indicator with a
    signal count (rails excluded) <= the conservative total."""
    out = await _capture_tool(monkeypatch, _scenario())(engine="construct")
    rn = out["ratsnest"]
    assert {"signal_crossings", "total_crossings", "signal_fanout_cap"} <= set(rn)
    assert rn["signal_crossings"] >= 0
    assert rn["signal_crossings"] <= rn["total_crossings"]


@pytest.mark.asyncio
async def test_decoupling_report_present_in_output(monkeypatch):
    """The placement result carries a structural decoupling report (possibly
    empty) as a list of pairings with distances."""
    out = await _capture_tool(monkeypatch, _scenario())(engine="construct")
    dr = out["decoupling_report"]
    assert isinstance(dr, list)
    for entry in dr:
        assert set(entry) >= {"decap", "ic", "distance_mils"}
        assert entry["distance_mils"] >= 0.0


@pytest.mark.asyncio
async def test_construct_engine_suggests_a_board_size(monkeypatch):
    """The construct engine sizes its own board; the tool surfaces it as
    ``suggested_board`` (the 'best PCB size' answer) with sane dimensions."""
    out = await _capture_tool(monkeypatch, _scenario())(engine="construct")
    sb = out["suggested_board"]
    assert sb is not None
    assert sb["width"] > 0 and sb["height"] > 0
    assert sb["x2"] - sb["x1"] == sb["width"]
    assert sb["y2"] - sb["y1"] == sb["height"]


@pytest.mark.asyncio
async def test_refine_engine_has_no_suggested_board(monkeypatch):
    """The refine engine works inside the existing outline and proposes no
    new board size."""
    out = await _capture_tool(monkeypatch, _scenario())(
        designators=["R1"], engine="refine"
    )
    assert out["suggested_board"] is None


@pytest.mark.asyncio
async def test_net_length_report_omits_critical_block_when_none(monkeypatch):
    """With no critical nets the report still lists longest_nets but carries
    no critical_net_spans block."""
    out = await _capture_tool(monkeypatch, _scenario())(engine="construct")
    rep = out["net_length_report"]
    assert "longest_nets" in rep
    assert "critical_net_spans" not in rep


@pytest.mark.asyncio
async def test_critical_weight_is_clamped(monkeypatch):
    """An out-of-range critical_weight must not crash or destabilize the
    solver; it is clamped into [1, 20]."""
    out = await _capture_tool(monkeypatch, _scenario())(
        engine="construct", critical_nets=["A"], critical_weight=1000.0
    )
    assert out["objective_report"]["legal"] is True
    assert "moves" in out


@pytest.mark.asyncio
async def test_no_critical_nets_is_a_noop(monkeypatch):
    """Omitting critical_nets leaves every net at weight 1.0 -- the result
    matches the plain run exactly (determinism preserved)."""
    plain = await _capture_tool(monkeypatch, _scenario())(engine="construct")
    none = await _capture_tool(monkeypatch, _scenario())(
        engine="construct", critical_nets=None
    )
    assert (plain["objective_report"]["weighted_total"]
            == none["objective_report"]["weighted_total"])


@pytest.mark.asyncio
async def test_bad_engine_rejected(monkeypatch):
    bridge = _scenario()
    tool = _capture_tool(monkeypatch, bridge)
    out = await tool(engine="nonsense")
    assert out.get("error") == "BAD_ENGINE"


@pytest.mark.asyncio
async def test_construct_engine_render_png_writes_preview(tmp_path, monkeypatch):
    bridge = _scenario()
    tool = _capture_tool(monkeypatch, bridge)
    out_png = tmp_path / "pcb_preview.png"
    out = await tool(engine="construct", render_png=str(out_png))
    assert out["engine"] == "construct"
    assert out.get("preview_png") == str(out_png)
    assert "preview_error" not in out
    from PIL import Image
    assert out_png.exists() and out_png.stat().st_size > 1000
    with Image.open(out_png) as im:
        im.verify()


@pytest.mark.asyncio
async def test_render_png_failure_keeps_move_data(tmp_path, monkeypatch):
    bridge = _scenario()
    tool = _capture_tool(monkeypatch, bridge)
    bad = tmp_path / "no\x00dir" / "x.png"   # NUL -> OSError on write
    out = await tool(engine="construct", render_png=str(bad))
    assert "objective_report" in out and "moves" in out
    assert "preview_error" in out and "preview_png" not in out


def _scenario_plan() -> dict:
    """A DesignPlan matching _scenario()'s refdes: A1 on a sensitive analog
    net, A2 on a digital clock net, R1 bridging both (the boundary)."""
    return {
        "spec": "mixed", "summary": "analog A1, digital A2, R1 bridges",
        "sheets": [{"name": "main"}],
        "parts": [
            {"refdes": "R1", "lib_ref": "RES"},
            {"refdes": "A1", "lib_ref": "AMP"},
            {"refdes": "A2", "lib_ref": "LOG"},
        ],
        "nets": [
            {"name": "A", "role": "analog_sensitive",
             "pins": [{"refdes": "R1", "pin": "1"}, {"refdes": "A1", "pin": "1"}]},
            {"name": "B", "role": "clock",
             "pins": [{"refdes": "R1", "pin": "2"}, {"refdes": "A2", "pin": "1"}]},
        ],
    }


@pytest.mark.asyncio
async def test_plan_json_infers_mixed_signal_keepout(monkeypatch):
    bridge = _scenario()
    tool = _capture_tool(monkeypatch, bridge)
    out = await tool(designators=["R1"], engine="construct",
                     plan_json=_scenario_plan())
    assert "auto_constraints" in out
    inferred = out["auto_constraints"]["keepout_groups_inferred"]
    assert inferred == {"A1": "analog", "A2": "digital"}   # R1 boundary, untagged


@pytest.mark.asyncio
async def test_no_plan_json_means_no_auto_constraints(monkeypatch):
    bridge = _scenario()
    tool = _capture_tool(monkeypatch, bridge)
    out = await tool(designators=["R1"])
    assert "auto_constraints" not in out


@pytest.mark.asyncio
async def test_bad_plan_json_is_reported_not_fatal(monkeypatch):
    bridge = _scenario()
    tool = _capture_tool(monkeypatch, bridge)
    out = await tool(designators=["R1"], plan_json="{not valid json")
    # The placement still runs; the plan problem is surfaced, not raised.
    assert out["dry_run"] is True
    assert "plan_json_error" in out["auto_constraints"]
