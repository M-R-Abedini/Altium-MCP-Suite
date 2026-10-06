# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Sugiyama placement tests: phase-by-phase + end-to-end."""

from __future__ import annotations

from collections import defaultdict

from eda_agent.design.plan import DesignPlan, Net, Part, PinRef, Sheet
from eda_agent.design.sugiyama import (
    SHEET_MAX_X_MILS,
    SHEET_MAX_Y_MILS,
    SHEET_ORIGIN_X_MILS,
    SHEET_ORIGIN_Y_MILS,
    SNAP_GRID_MILS,
    SugiyamaPlacement,
    _anchor_role,
    _assign_layers,
    _barycenter_sweep,
    _rail_bias,
    _signal_edges,
    _structural_anchor,
    has_anchors,
    sugiyama_layout,
)


# ---------------------------------------------------------------------------
# Plan helpers
# ---------------------------------------------------------------------------


def _net(name, pins, **kw):
    return Net(name=name, pins=[PinRef(refdes=r, pin=p) for r, p in pins], **kw)


def _plan(parts, nets):
    return DesignPlan(
        spec="x",
        summary="x",
        sheets=[Sheet(name="main")],
        parts=parts,
        nets=nets,
    )


# ---------------------------------------------------------------------------
# _anchor_role
# ---------------------------------------------------------------------------


def test_anchor_role_recognises_input_roles() -> None:
    assert _anchor_role("input_conn") == "input"
    assert _anchor_role("vin_conn") == "input"
    assert _anchor_role("power_in") == "input"
    assert _anchor_role("input") == "input"


def test_anchor_role_recognises_output_roles() -> None:
    assert _anchor_role("output_conn") == "output"
    assert _anchor_role("vout_conn") == "output"
    assert _anchor_role("power_out") == "output"
    assert _anchor_role("output") == "output"


def test_anchor_role_returns_none_for_other_roles() -> None:
    assert _anchor_role(None) is None
    assert _anchor_role("ic") is None
    assert _anchor_role("rfb_top") is None


# ---------------------------------------------------------------------------
# _signal_edges
# ---------------------------------------------------------------------------


def test_signal_edges_excludes_power_and_ground() -> None:
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="R2", lib_ref="RES"),
            Part(refdes="U1", lib_ref="IC"),
        ],
        nets=[
            _net("VCC", [("R1", "1"), ("U1", "1")], is_power=True),
            _net("GND", [("R2", "2"), ("U1", "2")], is_ground=True),
            _net("SIG", [("R1", "2"), ("R2", "1"), ("U1", "3")]),
        ],
    )
    edges = _signal_edges(plan)
    # Only SIG contributes. SIG has 3 pins -> 3 unique component-pairs.
    pairs = {tuple(sorted(e)) for e in edges}
    assert pairs == {("R1", "R2"), ("R1", "U1"), ("R2", "U1")}


def test_signal_edges_handles_same_refdes_on_multiple_pins() -> None:
    """A 2-pin part on a net via both its pins shouldn't create a self-loop."""
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="U1", lib_ref="IC"),
        ],
        nets=[
            _net("BUS", [("R1", "1"), ("R1", "2"), ("U1", "1")]),
        ],
    )
    edges = _signal_edges(plan)
    assert ("R1", "R1") not in edges
    assert ("R1", "U1") in edges or ("U1", "R1") in edges


# ---------------------------------------------------------------------------
# _assign_layers
# ---------------------------------------------------------------------------


def test_assign_layers_anchors_input_at_zero() -> None:
    plan = _plan(
        parts=[
            Part(refdes="J1", lib_ref="HDR", role="input_conn"),
            Part(refdes="R1", lib_ref="RES"),
        ],
        nets=[
            _net("SIG", [("J1", "1"), ("R1", "1")]),
        ],
    )
    edges = _signal_edges(plan)
    layers = _assign_layers(plan, edges)
    assert layers["J1"] == 0
    assert layers["R1"] == 1


def test_assign_layers_no_wasted_column_when_output_at_natural_max() -> None:
    """J1 -> R1 -> R2 -> J2: J2 naturally lands at BFS layer 3 (its
    distance from J1). Before the output-anchor fix the
    rule promoted it to max+1=4, creating an empty column 3-to-4 gap.
    Now J2 stays at 3 when no other part is already at 3."""
    plan = _plan(
        parts=[
            Part(refdes="J1", lib_ref="HDR", role="input_conn"),
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="R2", lib_ref="RES"),
            Part(refdes="J2", lib_ref="HDR", role="output_conn"),
        ],
        nets=[
            _net("S1", [("J1", "1"), ("R1", "1")]),
            _net("S2", [("R1", "2"), ("R2", "1")]),
            _net("S3", [("R2", "2"), ("J2", "1")]),
        ],
    )
    edges = _signal_edges(plan)
    layers = _assign_layers(plan, edges)
    # 4 layers total, not 5: J1=0, R1=1, R2=2, J2=3.
    assert max(layers.values()) == 3
    assert layers["J2"] == 3


def test_assign_layers_pins_output_to_max_layer() -> None:
    plan = _plan(
        parts=[
            Part(refdes="J1", lib_ref="HDR", role="input_conn"),
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="R2", lib_ref="RES"),
            Part(refdes="J2", lib_ref="HDR", role="output_conn"),
        ],
        nets=[
            _net("S1", [("J1", "1"), ("R1", "1")]),
            _net("S2", [("R1", "2"), ("R2", "1")]),
            _net("S3", [("R2", "2"), ("J2", "1")]),
        ],
    )
    edges = _signal_edges(plan)
    layers = _assign_layers(plan, edges)
    # BFS dist: J1=0, R1=1, R2=2, J2=3. Output anchor pins J2 to
    # max_after_bfs + 1 = max(0,1,2,3) + 1 = 4. (Or 3 if BFS happens
    # to already land it there -- it does. So output override has no
    # visible effect here, but the maximum is monotone.)
    assert layers["J1"] == 0
    assert layers["R1"] == 1
    assert layers["R2"] == 2
    # J2 lands at max-layer (3 from BFS or 4 from output override).
    assert layers["J2"] >= 3
    assert layers["J2"] == max(layers.values())


def test_assign_layers_output_only_anchors_walk_backward() -> None:
    """No input_conn, only output_conn: previously dumped everything on
    layer 0 (single-column degenerate). Now BFS walks backward from
    outputs and flips so outputs land on the right and their neighbours
    one column left.
    """
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="R2", lib_ref="RES"),
            Part(refdes="R3", lib_ref="RES"),
            Part(refdes="J2", lib_ref="HDR", role="output_conn"),
        ],
        nets=[
            _net("S1", [("R1", "1"), ("R2", "1")]),
            _net("S2", [("R2", "2"), ("R3", "1")]),
            _net("S3", [("R3", "2"), ("J2", "1")]),
        ],
    )
    edges = _signal_edges(plan)
    layers = _assign_layers(plan, edges)
    # Multi-column structure produced (not all-at-layer-0).
    assert len(set(layers.values())) > 1, f"degenerate single column: {layers}"
    # Output anchor ends up at the rightmost layer.
    assert layers["J2"] == max(layers.values())
    # And R1 (furthest from output) ends up at the leftmost layer.
    assert layers["R1"] == min(layers.values())


def test_assign_layers_disconnected_parts_get_middle_layer() -> None:
    plan = _plan(
        parts=[
            Part(refdes="J1", lib_ref="HDR", role="input_conn"),
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="R2", lib_ref="RES"),
            Part(refdes="R3", lib_ref="RES"),
            Part(refdes="J2", lib_ref="HDR", role="output_conn"),
            # Disconnected island
            Part(refdes="C99", lib_ref="CAP"),
            Part(refdes="C100", lib_ref="CAP"),
        ],
        nets=[
            _net("S1", [("J1", "1"), ("R1", "1")]),
            _net("S2", [("R1", "2"), ("R2", "1")]),
            _net("S3", [("R2", "2"), ("R3", "1")]),
            _net("S4", [("R3", "2"), ("J2", "1")]),
            _net("ISLAND", [("C99", "1"), ("C100", "1")]),
        ],
    )
    edges = _signal_edges(plan)
    layers = _assign_layers(plan, edges)
    assert layers["C99"] == layers["C100"]
    assert 0 < layers["C99"] < layers["J2"]


def test_assign_layers_no_anchors_falls_to_layer_zero() -> None:
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="R2", lib_ref="RES"),
        ],
        nets=[_net("S1", [("R1", "1"), ("R2", "1")])],
    )
    edges = _signal_edges(plan)
    layers = _assign_layers(plan, edges)
    # No anchors -> BFS doesn't run -> everything sits in middle (0)
    # which is the only layer.
    assert layers["R1"] == 0
    assert layers["R2"] == 0


# ---------------------------------------------------------------------------
# _barycenter_sweep -- crossing reduction
# ---------------------------------------------------------------------------


def _count_crossings(
    layers: dict[int, list[str]],
    edges: list[tuple[str, str]],
) -> int:
    """Count edge crossings between adjacent layers given the current
    within-layer order. Used to verify sweep improves the layout."""
    crossings = 0
    layer_indices = sorted(layers)
    layer_of = {r: l for l, parts in layers.items() for r in parts}
    pos = {r: i for parts in layers.values() for i, r in enumerate(parts)}
    for i in range(len(layer_indices) - 1):
        a = layer_indices[i]
        b = layer_indices[i + 1]
        # Pairs of edges between layers a and b
        es = [
            (u, v) for (u, v) in edges
            if {layer_of.get(u), layer_of.get(v)} == {a, b}
        ]
        # Normalise so the lower-layer node is u
        es = [(u, v) if layer_of[u] == a else (v, u) for u, v in es]
        for i1 in range(len(es)):
            for i2 in range(i1 + 1, len(es)):
                u1, v1 = es[i1]
                u2, v2 = es[i2]
                if (pos[u1] - pos[u2]) * (pos[v1] - pos[v2]) < 0:
                    crossings += 1
    return crossings


def test_barycenter_sweep_reduces_crossings() -> None:
    # Worst-case initial ordering: K_{3,3}-like with reversed mapping.
    layers = {
        0: ["A", "B", "C"],
        1: ["X", "Y", "Z"],
    }
    # Edges: A-Z, B-Y, C-X -> 3 crossings in this order
    edges = [("A", "Z"), ("B", "Y"), ("C", "X")]
    before = _count_crossings(layers, edges)
    assert before == 3

    swept = _barycenter_sweep({k: list(v) for k, v in layers.items()}, edges)
    after = _count_crossings(swept, edges)
    assert after <= before
    assert after == 0


# ---------------------------------------------------------------------------
# sugiyama_layout (end-to-end)
# ---------------------------------------------------------------------------


def test_sugiyama_layout_places_one_part_per_refdes() -> None:
    plan = _plan(
        parts=[
            Part(refdes="J1", lib_ref="HDR", role="input_conn"),
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="J2", lib_ref="HDR", role="output_conn"),
        ],
        nets=[
            _net("S1", [("J1", "1"), ("R1", "1")]),
            _net("S2", [("R1", "2"), ("J2", "1")]),
        ],
    )
    placements = sugiyama_layout(plan)
    assert {p.refdes for p in placements} == {"J1", "R1", "J2"}


def test_sugiyama_layout_input_on_left_output_on_right() -> None:
    """Signal flow is a STRUCTURAL property of Sugiyama: input anchors
    must end up at a smaller x than output anchors."""
    plan = _plan(
        parts=[
            Part(refdes="J1", lib_ref="HDR", role="input_conn"),
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="R2", lib_ref="RES"),
            Part(refdes="J2", lib_ref="HDR", role="output_conn"),
        ],
        nets=[
            _net("S1", [("J1", "1"), ("R1", "1")]),
            _net("S2", [("R1", "2"), ("R2", "1")]),
            _net("S3", [("R2", "2"), ("J2", "1")]),
        ],
    )
    placements = sugiyama_layout(plan)
    by = {p.refdes: p for p in placements}
    assert by["J1"].x_mils < by["R1"].x_mils
    assert by["R1"].x_mils < by["R2"].x_mils
    assert by["R2"].x_mils < by["J2"].x_mils


def test_sugiyama_layout_coords_snap_to_grid_and_stay_in_sheet() -> None:
    plan = _plan(
        parts=[
            Part(refdes="J1", lib_ref="HDR", role="input_conn"),
            *(Part(refdes=f"R{i}", lib_ref="RES") for i in range(1, 6)),
            Part(refdes="J2", lib_ref="HDR", role="output_conn"),
        ],
        nets=[
            _net("S0", [("J1", "1"), ("R1", "1")]),
            _net("S1", [("R1", "2"), ("R2", "1")]),
            _net("S2", [("R2", "2"), ("R3", "1")]),
            _net("S3", [("R3", "2"), ("R4", "1")]),
            _net("S4", [("R4", "2"), ("R5", "1")]),
            _net("S5", [("R5", "2"), ("J2", "1")]),
        ],
    )
    placements = sugiyama_layout(plan)
    for p in placements:
        assert p.x_mils % SNAP_GRID_MILS == 0
        assert p.y_mils % SNAP_GRID_MILS == 0
        assert SHEET_ORIGIN_X_MILS <= p.x_mils <= SHEET_MAX_X_MILS
        assert SHEET_ORIGIN_Y_MILS <= p.y_mils <= SHEET_MAX_Y_MILS


def test_sugiyama_layout_parallel_parts_get_different_y() -> None:
    """Two parts in the same layer must not end up at the same (x, y)."""
    plan = _plan(
        parts=[
            Part(refdes="J1", lib_ref="HDR", role="input_conn"),
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="R2", lib_ref="RES"),
            Part(refdes="J2", lib_ref="HDR", role="output_conn"),
        ],
        nets=[
            # Fanout: J1 to R1 and R2 (same layer); both to J2.
            _net("S1", [("J1", "1"), ("R1", "1")]),
            _net("S2", [("J1", "2"), ("R2", "1")]),
            _net("S3", [("R1", "2"), ("J2", "1")]),
            _net("S4", [("R2", "2"), ("J2", "2")]),
        ],
    )
    placements = sugiyama_layout(plan)
    by = {p.refdes: p for p in placements}
    # R1 and R2 should be in the same layer with different y
    assert by["R1"].layer == by["R2"].layer
    assert by["R1"].y_mils != by["R2"].y_mils
    assert by["R1"].x_mils == by["R2"].x_mils


def test_sugiyama_layout_no_anchors_returns_placements_anyway() -> None:
    """Degenerate fallback: no anchors -> everything in layer 0. The
    caller should detect this via has_anchors() and prefer FD."""
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="R2", lib_ref="RES"),
        ],
        nets=[_net("S", [("R1", "1"), ("R2", "1")])],
    )
    placements = sugiyama_layout(plan)
    assert {p.refdes for p in placements} == {"R1", "R2"}
    assert all(p.layer == 0 for p in placements)


# ---------------------------------------------------------------------------
# has_anchors
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Rail banding
# ---------------------------------------------------------------------------


def test_rail_bias_counts_power_negative_ground_positive() -> None:
    plan = _plan(
        parts=[
            Part(refdes="C1", lib_ref="CAP"),
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="U1", lib_ref="IC"),
        ],
        nets=[
            _net("VCC", [("C1", "1"), ("U1", "1")], is_power=True),
            _net(
                "GND",
                [("C1", "2"), ("R1", "1"), ("U1", "2")],
                is_ground=True,
            ),
            _net("SIG", [("R1", "2"), ("U1", "3")]),
        ],
    )
    bias = _rail_bias(plan)
    # C1: 1 power + 1 ground -> 0 net bias
    assert bias["C1"] == 0
    # R1: 1 ground only -> +1
    assert bias["R1"] == 1
    # U1: 1 power + 1 ground -> 0
    assert bias["U1"] == 0


def test_rail_bias_pure_signal_part_absent_or_zero() -> None:
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="R2", lib_ref="RES"),
        ],
        nets=[_net("S", [("R1", "1"), ("R2", "1")])],
    )
    bias = _rail_bias(plan)
    assert bias.get("R1", 0) == 0
    assert bias.get("R2", 0) == 0


def test_sugiyama_places_power_attached_above_ground_attached() -> None:
    """Within a single layer, a power-attached part should land above
    a ground-attached part (low y vs high y).

    C1 is the power-attached cap, C2 is the ground-attached cap. Both
    sit in the same BFS layer because they connect via a shared
    signal net; the rail bias breaks the tie."""
    plan = _plan(
        parts=[
            Part(refdes="J1", lib_ref="HDR", role="input_conn"),
            Part(refdes="C1", lib_ref="CAP"),     # power-attached (-1)
            Part(refdes="C2", lib_ref="CAP"),     # ground-attached (+1)
            Part(refdes="J2", lib_ref="HDR", role="output_conn"),
        ],
        nets=[
            _net("VCC",
                 [("J1", "1"), ("C1", "1"), ("J2", "1")],
                 is_power=True),
            _net("GND",
                 [("J1", "2"), ("C2", "1"), ("J2", "2")],
                 is_ground=True),
            # Mid-graph signal so the two caps end up in the same layer.
            _net("X", [("C1", "2"), ("C2", "2")]),
        ],
    )
    placements = sugiyama_layout(plan)
    by = {p.refdes: p for p in placements}
    # Same layer
    assert by["C1"].layer == by["C2"].layer
    # Power above ground (low y above high y).
    assert by["C1"].y_mils < by["C2"].y_mils


def test_has_anchors_true_when_input_conn_present() -> None:
    plan = _plan(
        parts=[
            Part(refdes="J1", lib_ref="HDR", role="input_conn"),
            Part(refdes="R1", lib_ref="RES"),
        ],
        nets=[_net("S", [("J1", "1"), ("R1", "1")])],
    )
    assert has_anchors(plan) is True


def test_has_anchors_false_when_no_role_anchors() -> None:
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES", role="rfb_top"),
            Part(refdes="R2", lib_ref="RES"),
        ],
        nets=[_net("S", [("R1", "1"), ("R2", "1")])],
    )
    assert has_anchors(plan) is False


# ---------------------------------------------------------------------------
# Structural anchor: Sugiyama for role-less but flow-heavy plans
# ---------------------------------------------------------------------------


def _chain_plan(n_parts: int):
    chain = [f"R{i}" for i in range(1, n_parts + 1)]
    parts = [Part(refdes=r, lib_ref="RES") for r in chain]
    nets = [_net(f"N{i}", [(chain[i], "2"), (chain[i + 1], "1")])
            for i in range(len(chain) - 1)]
    return _plan(parts, nets), chain


def test_structural_anchor_picks_chain_endpoint():
    """A role-less chain has a flow endpoint; the lowest-refdes leaf seeds
    layering so the chain orders left-to-right."""
    plan, chain = _chain_plan(5)
    edges = _signal_edges(plan)
    assert _structural_anchor(plan, edges) == "R1"
    layers = _assign_layers(plan, edges)
    # Strictly increasing layer along the chain (perfect left-to-right).
    assert [layers[r] for r in chain] == [0, 1, 2, 3, 4]
    assert has_anchors(plan) is True


def test_structural_anchor_skips_power_heavy_plan():
    """A short signal chain plus many power-only parts (decaps) must NOT use
    the structural anchor -- the isolated parts would pile into one layer.
    Force-directed stays the choice."""
    parts = [Part(refdes="R1", lib_ref="RES"), Part(refdes="R2", lib_ref="RES")]
    parts += [Part(refdes=f"C{i}", lib_ref="CAP") for i in range(1, 9)]
    nets = [
        _net("MID", [("R1", "2"), ("R2", "1")]),  # only signal edge
        _net("VCC", [("R1", "1")] + [(f"C{i}", "1") for i in range(1, 9)],
             is_power=True),
        _net("GND", [("R2", "2")] + [(f"C{i}", "2") for i in range(1, 9)],
             is_ground=True),
    ]
    plan = _plan(parts, nets)
    # Largest signal component {R1,R2} is 2 of 10 parts -> below threshold.
    assert _structural_anchor(plan, _signal_edges(plan)) is None
    assert has_anchors(plan) is False


def test_structural_anchor_none_for_ring():
    """A ring (every node degree 2) has no endpoint -> no structural anchor."""
    ring = [f"R{i}" for i in range(1, 5)]
    parts = [Part(refdes=r, lib_ref="RES") for r in ring]
    nets = [_net(f"N{i}", [(ring[i], "2"), (ring[(i + 1) % 4], "1")])
            for i in range(4)]
    plan = _plan(parts, nets)
    assert _structural_anchor(plan, _signal_edges(plan)) is None
    assert has_anchors(plan) is False
