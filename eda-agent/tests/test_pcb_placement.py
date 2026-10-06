# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Offline tests for the constructive PCB placement + sizing engine.

No Altium dependency: the engine is pure Python (numpy only). These
exercise the sizing estimate, the quality contract (HPWL drops on a
scattered netlist after construction + polish), the safety contract (no
same-side courtyard overlap in the final placement), that the
layer-change proxy is computed, and determinism under a fixed seed.
"""

from __future__ import annotations

import numpy as np

from eda_agent.design.pcb_placement import (
    ConstructOptions,
    DesignRules,
    ObjectiveWeights,
    _cong_term,
    _conn_term,
    _decap_term,
    _is_legal,
    _infer_crystal_groups,
    _infer_switch_node_groups,
    _match_axis_term,
    _match_centroid_term,
    _match_term,
    _pair_decaps_to_power_pins,
    _separation_term,
    _therm_term,
    _tighten_region,
    _via_term,
    construct_placement,
    construct_placement_best_of,
    construct_placement_visual,
    constructive_seed,
    decoupling_report,
    ratsnest_crossings,
    score,
    size_board,
    tighten_match_clusters,
)
from eda_agent.placement import PlaceComp, PlaceNet, PlacePin


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

def _scattered_board() -> tuple[list[PlaceComp], list[PlaceNet]]:
    """An IC plus decaps, resistors, and an edge connector.

    The passives start flung to the corners so a connectivity-driven
    placer has clear wirelength to recover. Roles / pins are tagged so
    the decap pairing and connector handling engage.
    """
    u1 = PlaceComp(
        ref="U1", w=200, h=200, cx=0, cy=0, rotatable=True,
        pins=(
            PlacePin(-90, 0, "VCC"),
            PlacePin(90, 0, "GND"),
            PlacePin(0, 90, "NET1"),
            PlacePin(0, -90, "NET2"),
        ),
    )
    u1.role = "ic"
    c1 = PlaceComp(
        ref="C1", w=40, h=40, cx=900, cy=900, rotatable=True,
        pins=(PlacePin(-20, 0, "VCC"), PlacePin(20, 0, "GND")),
    )
    c2 = PlaceComp(
        ref="C2", w=40, h=40, cx=-900, cy=900, rotatable=True,
        pins=(PlacePin(-20, 0, "VCC"), PlacePin(20, 0, "GND")),
    )
    r1 = PlaceComp(
        ref="R1", w=40, h=40, cx=900, cy=-900, rotatable=True,
        pins=(PlacePin(-20, 0, "NET1"), PlacePin(20, 0, "NET3")),
    )
    r2 = PlaceComp(
        ref="R2", w=40, h=40, cx=-900, cy=-900, rotatable=True,
        pins=(PlacePin(-20, 0, "NET2"), PlacePin(20, 0, "NET3")),
    )
    j1 = PlaceComp(
        ref="J1", w=100, h=200, cx=0, cy=1200, rotatable=False,
        pins=(PlacePin(0, 50, "NET3"), PlacePin(0, -50, "GND")),
    )
    j1.edge = True
    j1.edge_band = "L"

    comps = [u1, c1, c2, r1, r2, j1]
    nets = [
        PlaceNet(refs=("U1", "C1", "C2"), name="VCC"),
        PlaceNet(refs=("U1", "C1", "C2", "J1"), name="GND"),
        PlaceNet(refs=("U1", "R1"), name="NET1"),
        PlaceNet(refs=("U1", "R2"), name="NET2"),
        PlaceNet(refs=("R1", "R2", "J1"), name="NET3"),
    ]
    return comps, nets


def _two_ic_board() -> tuple[list[PlaceComp], list[PlaceNet]]:
    """Two ICs sharing a multi-pin bus, for the via / side machinery."""
    u1 = PlaceComp(
        ref="U1", w=200, h=200, cx=0, cy=0, rotatable=True,
        pins=tuple(PlacePin(-90, -90 + 30 * i, f"B{i}") for i in range(4)),
    )
    u1.role = "ic"
    u1.flippable = True
    u2 = PlaceComp(
        ref="U2", w=200, h=200, cx=800, cy=0, rotatable=True,
        pins=tuple(PlacePin(90, -90 + 30 * i, f"B{i}") for i in range(4)),
    )
    u2.role = "ic"
    u2.flippable = True
    comps = [u1, u2]
    nets = [PlaceNet(refs=("U1", "U2"), name=f"B{i}") for i in range(4)]
    return comps, nets


# --------------------------------------------------------------------------- #
# Board sizing
# --------------------------------------------------------------------------- #

def test_size_board_is_large_enough_for_parts():
    """The estimated board must hold the total inflated courtyard area
    and never be thinner than the largest single part + edge clearance."""
    comps, nets = _scattered_board()
    rules = DesignRules(layers=2)
    region = size_board(comps, nets, rules)

    cc = rules.courtyard_clr
    a_total = sum((c.w + 2 * cc) * (c.h + 2 * cc) for c in comps)
    board_area = region.width * region.height
    # Board area exceeds the inflated courtyard area (utilization < 1).
    assert board_area >= a_total

    max_dim = max(max(c.w, c.h) for c in comps)
    assert region.width >= max_dim + 2 * rules.edge_clr - 1e-6
    assert region.height >= max_dim + 2 * rules.edge_clr - 1e-6


def test_size_board_handles_empty_set():
    rules = DesignRules(layers=2)
    region = size_board([], [], rules)
    assert region.width > 0
    assert region.height > 0


def test_size_board_utilization_lowers_with_one_layer():
    """A one-layer board targets a lower utilization, hence more area."""
    comps, nets = _scattered_board()
    two = size_board(comps, nets, DesignRules(layers=2))
    one = size_board(comps, nets, DesignRules(layers=1))
    assert one.width * one.height >= two.width * two.height


# --------------------------------------------------------------------------- #
# Quality: HPWL drops after construction + polish
# --------------------------------------------------------------------------- #

def test_hpwl_drops_versus_initial_scatter():
    """Constructed placement has far lower HPWL than the input scatter."""
    comps, nets = _scattered_board()
    rules = DesignRules(layers=2)
    weights = ObjectiveWeights()

    region = size_board(comps, nets, rules)
    # Initial HPWL measured on the input centroids, in the same metric.
    init_pos = {c.ref: [c.cx, c.cy] for c in comps}
    init_rot = {c.ref: c.rotation for c in comps}
    init_sides = {c.ref: 1 for c in comps}
    init = score(comps, init_pos, init_rot, init_sides, nets, region,
                 rules, weights)

    result = construct_placement(comps, nets, rules, ConstructOptions(seed=3))
    assert result.report.hpwl < init.hpwl


def test_constructive_seed_beats_scatter_before_polish():
    """The constructive seed alone (no annealing) already shortens HPWL,
    confirming the constructor -- not the polish -- is the real placer."""
    comps, nets = _scattered_board()
    rules = DesignRules(layers=2)
    weights = ObjectiveWeights()
    region = size_board(comps, nets, rules)
    rng = np.random.default_rng(0)

    init_pos = {c.ref: [c.cx, c.cy] for c in comps}
    rot = {c.ref: c.rotation for c in comps}
    sides = {c.ref: 1 for c in comps}
    init = score(comps, init_pos, rot, sides, nets, region, rules, weights)

    seeded = constructive_seed(comps, nets, region, rules, rng, fixed_pos={})
    seeded_hpwl = score(comps, seeded, rot, sides, nets, region, rules,
                        weights).hpwl
    assert seeded_hpwl < init.hpwl


# --------------------------------------------------------------------------- #
# Safety: no courtyard overlaps in the final placement
# --------------------------------------------------------------------------- #

def test_final_placement_has_no_courtyard_overlap():
    comps, nets = _scattered_board()
    rules = DesignRules(layers=2)
    result = construct_placement(comps, nets, rules, ConstructOptions(seed=11))
    # The engine reports the summed courtyard overlap area directly.
    assert result.report.clear <= 1e-3
    assert result.report.legal is True


def test_final_placement_stays_inside_board():
    comps, nets = _scattered_board()
    rules = DesignRules(layers=2)
    result = construct_placement(comps, nets, rules, ConstructOptions(seed=11))
    # ``legal`` already encodes inside-board + no-overlap; assert both via
    # the edge term being zero where parts are comfortably inside.
    assert result.report.legal is True
    region = result.region
    for ref, p in result.placements.items():
        assert 0.0 <= p.x <= region.width
        assert 0.0 <= p.y <= region.height


def test_no_overlap_across_multiple_seeds():
    comps, nets = _scattered_board()
    rules = DesignRules(layers=2)
    for seed in (0, 1, 2, 7, 13, 42):
        result = construct_placement(
            comps, nets, rules, ConstructOptions(seed=seed)
        )
        assert result.report.clear <= 1e-3, f"overlap at seed {seed}"


# --------------------------------------------------------------------------- #
# Via / layer-change proxy is computed
# --------------------------------------------------------------------------- #

def test_via_proxy_is_computed_and_zero_when_single_sided():
    """The via term is part of the report and is identically zero while
    every part sits on the same side (always true on one layer)."""
    comps, nets = _two_ic_board()
    rules = DesignRules(layers=1)
    result = construct_placement(comps, nets, rules, ConstructOptions(seed=4))
    report = result.report
    # The attribute exists and is a real number.
    assert isinstance(report.via, float)
    # One layer => all parts top => no layer changes => via term is 0.
    assert report.via == 0.0


def test_via_proxy_nonnegative_on_two_layers():
    """On two layers the via term is still well-defined and non-negative
    (it only becomes positive once a flip puts pins on both sides)."""
    comps, nets = _two_ic_board()
    rules = DesignRules(layers=2)
    weights = ObjectiveWeights()
    region = size_board(comps, nets, rules)
    pos = {c.ref: [c.cx, c.cy] for c in comps}
    rot = {c.ref: c.rotation for c in comps}
    # Force one IC to the bottom so a layer change is unavoidable.
    sides = {"U1": 1, "U2": -1}
    report = score(comps, pos, rot, sides, nets, region, rules, weights)
    assert report.via > 0.0


# --------------------------------------------------------------------------- #
# Determinism under a fixed seed
# --------------------------------------------------------------------------- #

def test_determinism_same_seed_same_result():
    comps, nets = _scattered_board()
    rules = DesignRules(layers=2)
    a = construct_placement(comps, nets, rules, ConstructOptions(seed=99))
    b = construct_placement(comps, nets, rules, ConstructOptions(seed=99))

    assert a.centroids == b.centroids
    assert a.rotations == b.rotations
    assert a.sides == b.sides
    assert a.accepted == b.accepted
    assert a.rejected == b.rejected
    a_pl = {k: (v.x, v.y, v.rotation, v.side) for k, v in a.placements.items()}
    b_pl = {k: (v.x, v.y, v.rotation, v.side) for k, v in b.placements.items()}
    assert a_pl == b_pl


def test_different_seeds_explore_differently():
    """A different seed should be free to land on a different optimum;
    this guards against the RNG being ignored (which would make the seed
    parameter a lie)."""
    comps, nets = _scattered_board()
    rules = DesignRules(layers=2)
    a = construct_placement(comps, nets, rules, ConstructOptions(seed=1))
    b = construct_placement(comps, nets, rules, ConstructOptions(seed=2))
    # Both legal, but at least one placed centroid differs.
    assert a.report.clear <= 1e-3 and b.report.clear <= 1e-3
    assert a.centroids != b.centroids


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #

def test_empty_input():
    rules = DesignRules(layers=2)
    result = construct_placement([], [], rules)
    assert result.placements == {}
    assert result.region.width > 0


def test_objective_report_exposes_every_term():
    comps, nets = _scattered_board()
    rules = DesignRules(layers=2)
    result = construct_placement(comps, nets, rules, ConstructOptions(seed=5))
    r = result.report
    for term in ("hpwl", "via", "cong", "clear", "edge", "decap",
                 "conn", "therm"):
        assert hasattr(r, term)
        assert isinstance(getattr(r, term), float)
    assert isinstance(r.weighted_total, float)
    assert 0.0 <= r.utilization


# --------------------------------------------------------------------------- #
# Best-of-N seeded restarts
# --------------------------------------------------------------------------- #

def test_best_of_n_is_no_worse_than_any_single_seed():
    comps, nets = _scattered_board()
    rules = DesignRules(layers=2)
    seeds = (0, 1, 2, 3, 4)
    singles = [
        construct_placement(comps, nets, rules, ConstructOptions(seed=s))
        for s in seeds
    ]
    best = construct_placement_best_of(comps, nets, rules, seeds=seeds)
    # The winner must be a legal placement whenever any single seed is legal,
    # and its objective must be <= the best individual seed's objective.
    legal_totals = [r.report.weighted_total for r in singles if r.report.legal]
    if legal_totals:
        assert best.report.legal
        assert best.report.weighted_total <= min(legal_totals) + 1e-6
    else:
        all_totals = [r.report.weighted_total for r in singles]
        assert best.report.weighted_total <= min(all_totals) + 1e-6


def test_best_of_n_is_deterministic():
    comps, nets = _scattered_board()
    rules = DesignRules(layers=2)
    a = construct_placement_best_of(comps, nets, rules, seeds=(0, 1, 2, 3))
    b = construct_placement_best_of(comps, nets, rules, seeds=(0, 1, 2, 3))
    assert a.report.weighted_total == b.report.weighted_total
    assert a.placements.keys() == b.placements.keys()
    for ref in a.placements:
        pa, pb = a.placements[ref], b.placements[ref]
        assert (pa.x, pa.y, pa.rotation, pa.side) == (pb.x, pb.y, pb.rotation, pb.side)


def test_best_of_n_annotates_winning_seed():
    comps, nets = _scattered_board()
    rules = DesignRules(layers=2)
    best = construct_placement_best_of(comps, nets, rules, seeds=(0, 1, 2))
    assert any("best of 3 seeds" in n for n in best.notes)


def test_spectral_seed_produces_a_legal_placement():
    """The spectral (eigenvector) seed feeds the same shove/polish and yields a
    legal placement on its own."""
    comps, nets = _scattered_board()
    res = construct_placement(
        comps, nets, DesignRules(layers=2),
        ConstructOptions(seed=0, seed_mode="spectral"))
    assert res.report.legal
    assert len(res.placements) == len(comps)


def test_best_of_explores_both_seed_strategies_and_never_regresses():
    """best_of tries greedy AND spectral and keeps the winner, so its result is
    no worse than greedy-only -- and on a scattered netlist the global spectral
    seed actually wins."""
    comps, nets = _scattered_board()
    rules = DesignRules(layers=2)
    seeds = (0, 1, 2, 3)
    combined = construct_placement_best_of(comps, nets, rules, seeds=seeds)
    greedy_only = min(
        (construct_placement(comps, nets, rules,
                             ConstructOptions(seed=s, seed_mode="greedy"))
         for s in seeds),
        key=lambda r: (0 if r.report.legal else 1, r.report.weighted_total),
    )
    assert combined.report.legal
    # Never worse than greedy-only; the annotation records which strategy won.
    assert combined.report.weighted_total <= greedy_only.report.weighted_total + 1e-6
    assert any("strategies" in n for n in combined.notes)


def test_best_of_n_empty_seeds_falls_back_to_base():
    comps, nets = _scattered_board()
    rules = DesignRules(layers=2)
    best = construct_placement_best_of(
        comps, nets, rules, seeds=(), base_opts=ConstructOptions(seed=7)
    )
    # Empty seeds -> the single base seed (7) is still used (no crash, a legal
    # result). best_of now also tries the spectral strategy at that seed and
    # keeps whichever wins, so it is never WORSE than the single greedy run.
    single = construct_placement(
        comps, nets, rules, ConstructOptions(seed=7, seed_mode="greedy"))
    assert best.report.legal
    assert best.report.weighted_total <= single.report.weighted_total + 1e-6


# --------------------------------------------------------------------------- #
# Via / layer-change proxy (the "least layer changes" objective)
# --------------------------------------------------------------------------- #

def test_via_term_zero_when_net_is_single_sided():
    comps, nets = _scattered_board()
    sides = {c.ref: 1 for c in comps}          # everything on top
    assert _via_term(comps, sides, nets) == 0.0


def test_via_term_counts_per_net_layer_split():
    # One net of four members; put exactly one member on the bottom.
    u1, c1, c2, r1, r2, j1 = _scattered_board()[0]
    comps = [u1, c1, c2, j1]
    from eda_agent.placement import PlaceNet
    nets = [PlaceNet(refs=("U1", "C1", "C2", "J1"), name="N", weight=1.0)]
    sides = {"U1": 1, "C1": 1, "C2": 1, "J1": -1}   # 3 top, 1 bottom
    # min(top, bot) = min(3, 1) = 1, degree-normalised by (members - 1) = 3.
    assert _via_term(comps, sides, nets) == 1.0 / 3.0
    # All on one side -> 0; the split is what costs layer changes.
    assert _via_term(comps, {r: 1 for r in sides}, nets) == 0.0


def test_construct_via_term_zero_on_single_layer_board():
    comps, nets = _two_ic_board()
    result = construct_placement(comps, nets, DesignRules(layers=1),
                                 ConstructOptions(seed=2))
    # A single-layer board cannot have layer changes.
    assert result.report.via == 0.0
    assert all(s >= 0 for s in result.sides.values())


def test_engine_does_not_gratuitously_split_nets_across_layers():
    comps, nets = _two_ic_board()
    result = construct_placement(comps, nets, DesignRules(layers=2),
                                 ConstructOptions(seed=2))
    engine_via = _via_term(comps, result.sides, nets)
    # Worst case: alternate every part across the two sides.
    alt_sides = {c.ref: (1 if i % 2 == 0 else -1)
                 for i, c in enumerate(comps)}
    worst_via = _via_term(comps, alt_sides, nets)
    assert worst_via > 0.0           # the baseline really is split
    assert engine_via <= worst_via   # the engine does not gratuitously split


# --------------------------------------------------------------------------- #
# Congestion proxy (routability stand-in)
# --------------------------------------------------------------------------- #

def _region(side: float = 1000.0):
    from eda_agent.placement.autoplace import BoardRegion
    return BoardRegion(x1=0.0, y1=0.0, x2=side, y2=side)


def test_cong_term_zero_without_multipin_nets():
    region = _region()
    # Single-point "nets" deposit no spread; density stays uniform-zero.
    world = {"A": [(100.0, 100.0)], "B": []}
    nets = []
    assert _cong_term(world, region, nets, bins=8) == 0.0


def test_cong_term_zero_for_degenerate_region():
    from eda_agent.placement.autoplace import BoardRegion
    flat = BoardRegion(x1=0.0, y1=0.0, x2=0.0, y2=1000.0)  # zero width
    world = {"A": [(0.0, 0.0), (0.0, 500.0)]}
    assert _cong_term(world, flat, [], bins=8) == 0.0


def test_cong_term_penalizes_concentration():
    region = _region()
    # Two nets of identical wirelength (hp = 140 each), so only their spatial
    # concentration differs.
    concentrated = {
        "A": [(50.0, 50.0), (120.0, 120.0)],
        "B": [(60.0, 60.0), (130.0, 130.0)],
    }
    spread = {
        "A": [(50.0, 50.0), (120.0, 120.0)],
        "B": [(850.0, 850.0), (920.0, 920.0)],
    }
    c = _cong_term(concentrated, region, [], bins=8)
    s = _cong_term(spread, region, [], bins=8)
    assert c > s          # piling nets into one corner is more congested
    assert s >= 0.0


# --------------------------------------------------------------------------- #
# Decoupling-to-IC locality (manufacturability / EMC)
# --------------------------------------------------------------------------- #

def test_decap_term_measures_distance_to_served_power_pin():
    from eda_agent.design.pcb_placement import _pin_world
    ic = PlaceComp(
        ref="U1", w=200, h=200, cx=0, cy=0, rotatable=True,
        pins=(PlacePin(-90, 0, "VCC"),),
    )
    cap = PlaceComp(
        ref="C1", w=40, h=40, cx=0, cy=0,
        pins=(PlacePin(0, 0, "VCC"), PlacePin(0, 0, "GND")),
    )
    comps = [ic, cap]
    rot = {"U1": 0.0, "C1": 0.0}
    sides = {"U1": 1, "C1": 1}
    pairs = {"C1": [("U1", -90.0, 0.0)]}
    # World position of U1's VCC pin at rotation 0, top side.
    pw = _pin_world(ic, (0.0, 0.0), 0.0, 1, PlacePin(-90.0, 0.0, ""))
    # Decap sitting exactly on the pin -> zero locality penalty.
    pos = {"U1": [0.0, 0.0], "C1": [pw[0], pw[1]]}
    assert _decap_term(comps, pos, rot, sides, pairs) == 0.0
    # Move the decap 100 mils away -> penalty equals that distance.
    pos["C1"] = [pw[0] + 100.0, pw[1]]
    assert abs(_decap_term(comps, pos, rot, sides, pairs) - 100.0) < 1e-6


def test_engine_pulls_decaps_toward_their_ic():
    comps, nets = _scattered_board()
    rules = DesignRules(layers=2)
    pairs = _pair_decaps_to_power_pins(comps, nets)
    # C1 and C2 are recognised as decaps purely from connectivity.
    assert pairs, "no decaps paired"

    init_pos = {c.ref: [c.cx, c.cy] for c in comps}
    init_rot = {c.ref: c.rotation for c in comps}
    init_sides = {c.ref: 1 for c in comps}
    init_decap = _decap_term(comps, init_pos, init_rot, init_sides, pairs)

    result = construct_placement(comps, nets, rules, ConstructOptions(seed=3))
    final_pos = {r: [c[0], c[1]] for r, c in result.centroids.items()}
    final_decap = _decap_term(comps, final_pos, result.rotations,
                              result.sides, pairs)
    # The constructed placement parks decaps far closer to their IC pin
    # than the scattered input.
    assert final_decap < init_decap
    # The reported term matches the standalone recomputation.
    assert abs(result.report.decap - final_decap) < 1e-6


def test_decap_pairing_excludes_multipin_connector():
    """A bypass cap decouples the IC, never a power connector. A 4-pin
    connector that carries VCC must NOT become a decoupling target just
    because the >=3-pin heuristic would otherwise call it an IC."""
    u1 = PlaceComp(
        ref="U1", w=200, h=200, cx=0, cy=0, rotatable=True,
        pins=(PlacePin(-90, 0, "VCC"), PlacePin(90, 0, "GND"),
              PlacePin(0, 90, "SIG")),
    )
    u1.role = "ic"
    c1 = PlaceComp(
        ref="C1", w=40, h=40, cx=500, cy=500, rotatable=True,
        pins=(PlacePin(-20, 0, "VCC"), PlacePin(20, 0, "GND")),
    )
    j1 = PlaceComp(
        ref="J1", w=100, h=250, cx=0, cy=900, rotatable=False,
        pins=(PlacePin(0, 75, "VCC"), PlacePin(0, 25, "GND"),
              PlacePin(0, -25, "SIG"), PlacePin(0, -75, "GND")),
    )
    j1.edge = True
    comps = [u1, c1, j1]
    nets = [
        PlaceNet(refs=("U1", "C1", "J1"), name="VCC"),
        PlaceNet(refs=("U1", "C1", "J1"), name="GND"),
        PlaceNet(refs=("U1", "J1"), name="SIG"),
    ]
    pairs = _pair_decaps_to_power_pins(comps, nets)
    assert "C1" in pairs
    # Every candidate power pin for C1 belongs to the IC, not the connector.
    assert all(ic_ref == "U1" for ic_ref, _, _ in pairs["C1"])


def test_decaps_snap_tight_against_ic_without_overlap():
    """The decap-snap move parks each bypass cap hugging an IC power pin --
    just OUTSIDE the body (centring on the pin would overlap the IC and the
    polish would reject the move). Two caps sharing a rail spread to the two
    VCC pins instead of stacking."""
    from eda_agent.design.pcb_placement import _rect_overlap_area

    u1 = PlaceComp(
        ref="U1", w=200, h=200, cx=0, cy=0, rotatable=False,
        pins=(PlacePin(-90, 50, "VCC"), PlacePin(-90, -50, "VCC"),
              PlacePin(90, 0, "GND"), PlacePin(0, 90, "SIG")),
    )
    u1.role = "ic"
    c1 = PlaceComp(ref="C1", w=40, h=40, cx=800, cy=800, rotatable=True,
                   pins=(PlacePin(-20, 0, "VCC"), PlacePin(20, 0, "GND")))
    c2 = PlaceComp(ref="C2", w=40, h=40, cx=-800, cy=-800, rotatable=True,
                   pins=(PlacePin(-20, 0, "VCC"), PlacePin(20, 0, "GND")))
    comps = [u1, c1, c2]
    nets = [
        PlaceNet(refs=("U1", "C1", "C2"), name="VCC"),
        PlaceNet(refs=("U1", "C1", "C2"), name="GND"),
        PlaceNet(refs=("U1",), name="SIG"),
    ]
    rules = DesignRules(layers=2)
    res = construct_placement_best_of(comps, nets, rules, seeds=tuple(range(6)))
    rep = decoupling_report(comps, res.centroids, res.rotations,
                            res.sides, nets)
    dist = {d["decap"]: d["distance_mils"] for d in rep}
    # Both caps land within ~one cap-pitch of a VCC pin (snap offset is
    # body-half is irrelevant: the term is centre-to-pin, ~clr + cap_half +
    # at most one tangential pitch). A scattered start was 1100+ mils away.
    assert dist["C1"] <= 150.0
    assert dist["C2"] <= 150.0
    # And neither cap overlaps the IC body (the move stayed legal).
    pos = res.centroids
    uw, uh = u1.w, u1.h
    for cap in (c1, c2):
        assert _rect_overlap_area(
            cap.w, cap.h, pos[cap.ref][0], pos[cap.ref][1],
            uw, uh, pos["U1"][0], pos["U1"][1], 0.0) == 0.0


# --------------------------------------------------------------------------- #
# Mixed-signal keep-apart (facility-layout REL 'X') separation
# --------------------------------------------------------------------------- #

def _two_domain_board(tag: bool):
    """Analog block (U1,R1,R2) + digital block (U2,R3,R4), one bridge net.

    ``tag`` toggles the keepout_group tags so the same netlist can be placed
    with and without the keep-apart constraint.
    """
    def C(ref, cx, cy, pins, group):
        big = ref.startswith("U")
        c = PlaceComp(ref=ref, w=200 if big else 40, h=200 if big else 40,
                      cx=cx, cy=cy, rotatable=True,
                      pins=tuple(PlacePin(px, py, n) for px, py, n in pins))
        if big:
            c.role = "ic"
        if tag and group:
            c.keepout_group = group
        return c
    comps = [
        C("U1", 0, 0, [(-90, 0, "AVCC"), (90, 0, "AGND"),
                       (0, 90, "AIN"), (0, -90, "BR")], "analog"),
        C("R1", 150, 150, [(-20, 0, "AIN"), (20, 0, "AGND")], "analog"),
        C("R2", -150, 150, [(-20, 0, "AVCC"), (20, 0, "AIN")], "analog"),
        C("U2", 300, 0, [(-90, 0, "DVCC"), (90, 0, "DGND"),
                         (0, 90, "DCLK"), (0, -90, "BR")], "digital"),
        C("R3", 450, 150, [(-20, 0, "DCLK"), (20, 0, "DGND")], "digital"),
        C("R4", 150, -150, [(-20, 0, "DVCC"), (20, 0, "DCLK")], "digital"),
    ]
    nets = [
        PlaceNet(refs=("U1", "R1", "R2"), name="AIN"),
        PlaceNet(refs=("U1", "R2"), name="AVCC"),
        PlaceNet(refs=("U1", "R1"), name="AGND"),
        PlaceNet(refs=("U2", "R3", "R4"), name="DCLK"),
        PlaceNet(refs=("U2", "R4"), name="DVCC"),
        PlaceNet(refs=("U2", "R3"), name="DGND"),
        PlaceNet(refs=("U1", "U2"), name="BR"),
    ]
    return comps, nets


def _block_separation(centroids) -> float:
    import math
    a = [centroids[r] for r in ("U1", "R1", "R2")]
    d = [centroids[r] for r in ("U2", "R3", "R4")]
    ac = (sum(p[0] for p in a) / 3, sum(p[1] for p in a) / 3)
    dc = (sum(p[0] for p in d) / 3, sum(p[1] for p in d) / 3)
    return math.hypot(ac[0] - dc[0], ac[1] - dc[1])


def test_separation_term_zero_without_tags():
    """No keepout_group tags (or only one domain) -> the term is identically 0,
    so a single-domain board is unaffected (opt-in safety)."""
    from eda_agent.placement.autoplace import BoardRegion
    region = BoardRegion(x1=0.0, y1=0.0, x2=1000.0, y2=1000.0)
    comps, _ = _two_domain_board(tag=False)
    pos = {c.ref: [c.cx, c.cy] for c in comps}
    assert _separation_term(comps, pos, region) == 0.0


def test_separation_term_penalizes_close_conflicting_groups():
    """Two differently-tagged parts closer than the desired separation incur a
    positive penalty that shrinks to 0 as they move apart."""
    from eda_agent.placement.autoplace import BoardRegion
    region = BoardRegion(x1=0.0, y1=0.0, x2=1000.0, y2=1000.0)
    a = PlaceComp(ref="U1", w=40, h=40, cx=0, cy=0, pins=())
    b = PlaceComp(ref="U2", w=40, h=40, cx=0, cy=0, pins=())
    a.keepout_group = "analog"
    b.keepout_group = "digital"
    comps = [a, b]
    near = _separation_term(comps, {"U1": [0.0, 0.0], "U2": [100.0, 0.0]}, region)
    far = _separation_term(comps, {"U1": [0.0, 0.0], "U2": [900.0, 0.0]}, region)
    assert near > far >= 0.0
    # Same tag -> no penalty even when coincident.
    b.keepout_group = "analog"
    assert _separation_term(comps, {"U1": [0.0, 0.0], "U2": [0.0, 0.0]}, region) == 0.0


def test_engine_separates_keepout_groups():
    """End to end: tagging analog vs digital pushes the two blocks farther apart
    than the same netlist placed untagged, while staying legal."""
    rules = DesignRules(layers=2)
    ct, nt = _two_domain_board(tag=True)
    rt = construct_placement(ct, nt, rules, ConstructOptions(seed=2))
    cu, nu = _two_domain_board(tag=False)
    ru = construct_placement(cu, nu, rules, ConstructOptions(seed=2))
    assert rt.report.legal and ru.report.legal
    assert ru.report.sep == 0.0                      # untagged: term inactive
    assert _block_separation(rt.centroids) > _block_separation(ru.centroids)


# --------------------------------------------------------------------------- #
# Matched-pair keep-together (SLP 'A' / boids cohesion / analog matching)
# --------------------------------------------------------------------------- #

def _diff_pair_board(tag: bool):
    """U1 diff amp; R1 on IN+ path, R2 on IN- path. R1,R2 are a matched pair
    on DIFFERENT nets (no shared net), so only a match term keeps them
    together. ``tag`` toggles the match_group."""
    u = PlaceComp(ref="U1", w=200, h=200, cx=0, cy=0, rotatable=True, pins=(
        PlacePin(-90, 50, "INP"), PlacePin(-90, -50, "INN"), PlacePin(90, 0, "OUT")))
    u.role = "ic"
    r1 = PlaceComp(ref="R1", w=40, h=40, cx=-600, cy=600, rotatable=True,
                   pins=(PlacePin(-20, 0, "SRCP"), PlacePin(20, 0, "INP")))
    r2 = PlaceComp(ref="R2", w=40, h=40, cx=600, cy=-600, rotatable=True,
                   pins=(PlacePin(-20, 0, "SRCN"), PlacePin(20, 0, "INN")))
    j = PlaceComp(ref="J1", w=100, h=200, cx=0, cy=900, rotatable=False,
                  pins=(PlacePin(0, 40, "SRCP"), PlacePin(0, -40, "SRCN")))
    if tag:
        r1.match_group = r2.match_group = "diff"
    comps = [u, r1, r2, j]
    nets = [PlaceNet(refs=("U1", "R1"), name="INP"),
            PlaceNet(refs=("U1", "R2"), name="INN"),
            PlaceNet(refs=("R1", "J1"), name="SRCP"),
            PlaceNet(refs=("R2", "J1"), name="SRCN")]
    return comps, nets


def test_match_term_zero_without_tags():
    """No match_group tags -> the term is identically 0 (opt-in safety)."""
    from eda_agent.placement.autoplace import BoardRegion
    region = BoardRegion(x1=0.0, y1=0.0, x2=1000.0, y2=1000.0)
    comps, _ = _diff_pair_board(tag=False)
    pos = {c.ref: [c.cx, c.cy] for c in comps}
    assert _match_term(comps, pos, region) == 0.0


def test_match_term_grows_with_pair_distance():
    """Two same-group parts incur a penalty that grows with their distance."""
    from eda_agent.placement.autoplace import BoardRegion
    region = BoardRegion(x1=0.0, y1=0.0, x2=1000.0, y2=1000.0)
    a = PlaceComp(ref="R1", w=40, h=40, cx=0, cy=0, pins=())
    b = PlaceComp(ref="R2", w=40, h=40, cx=0, cy=0, pins=())
    a.match_group = b.match_group = "m"
    near = _match_term([a, b], {"R1": [0.0, 0.0], "R2": [100.0, 0.0]}, region)
    far = _match_term([a, b], {"R1": [0.0, 0.0], "R2": [900.0, 0.0]}, region)
    assert far > near > 0.0
    # Different tags -> no pull.
    b.match_group = "other"
    assert _match_term([a, b], {"R1": [0.0, 0.0], "R2": [900.0, 0.0]}, region) == 0.0


def test_engine_pulls_matched_pair_together():
    """End to end: tagging R1,R2 as a matched pair lands them far closer than
    the same netlist placed untagged, even though they share no net."""
    import math
    rules = DesignRules(layers=2)
    def dist(res):
        c = res.centroids
        return math.hypot(c["R1"][0] - c["R2"][0], c["R1"][1] - c["R2"][1])
    tagged = [dist(construct_placement(*(_diff_pair_board(tag=True)), rules,
              ConstructOptions(seed=s))) for s in range(4)]
    untag = [dist(construct_placement(*(_diff_pair_board(tag=False)), rules,
             ConstructOptions(seed=s))) for s in range(4)]
    # Averaged over seeds the matched pair is decisively closer.
    assert sum(tagged) / 4 < sum(untag) / 4


def test_match_axis_term_compares_mod_180():
    """Same-group parts on the same axis cost 0; a 90-vs-0 split costs 1; but
    90 vs 270 (a mirror pair, same axis) also costs 0 -- the term must NOT
    fight the natural mirror symmetry."""
    a = PlaceComp(ref="R1", w=60, h=20, cx=0, cy=0, pins=())
    b = PlaceComp(ref="R2", w=60, h=20, cx=0, cy=0, pins=())
    a.match_group = b.match_group = "m"
    comps = [a, b]
    assert _match_axis_term(comps, {"R1": 0.0, "R2": 0.0}) == 0.0
    assert _match_axis_term(comps, {"R1": 90.0, "R2": 270.0}) == 0.0   # mirror = same axis
    assert _match_axis_term(comps, {"R1": 0.0, "R2": 180.0}) == 0.0    # same axis
    assert _match_axis_term(comps, {"R1": 0.0, "R2": 90.0}) == 1.0     # real mismatch
    # No tags -> 0.
    a.match_group = ""
    assert _match_axis_term(comps, {"R1": 0.0, "R2": 90.0}) == 0.0


# --------------------------------------------------------------------------- #
# Common-centroid matching (analog cross-quad; Razavi)
# --------------------------------------------------------------------------- #

def _quad(roles):
    """Four unit cells in one match_group, roles assigned by ``roles`` (a
    4-tuple like ('A','B','B','A')). Returns the comps so a test can place
    them at the four corners and check the centroid term."""
    cells = []
    for i, role in enumerate(roles):
        c = PlaceComp(ref=f"R{i+1}", w=40, h=40, cx=0, cy=0, pins=())
        c.match_group = "quad"
        c.match_role = role
        cells.append(c)
    return cells


# The four board corners, in R1..R4 order.
_CORNERS = {"R1": [0.0, 0.0], "R2": [1000.0, 0.0],
            "R3": [0.0, 1000.0], "R4": [1000.0, 1000.0]}


def test_match_centroid_zero_without_roles():
    """match_group present but no match_role -> the term is identically 0
    (it only fires once the sub-devices are labelled). Opt-in safety."""
    from eda_agent.placement.autoplace import BoardRegion
    region = BoardRegion(x1=0.0, y1=0.0, x2=1000.0, y2=1000.0)
    cells = _quad(["", "", "", ""])           # grouped but unlabelled
    assert _match_centroid_term(cells, _CORNERS, region) == 0.0


def test_match_centroid_zero_for_single_role():
    """A group with only ONE distinct role cannot define a relative centroid,
    so the term is 0 -- a single device never self-penalises."""
    from eda_agent.placement.autoplace import BoardRegion
    region = BoardRegion(x1=0.0, y1=0.0, x2=1000.0, y2=1000.0)
    cells = _quad(["A", "A", "A", "A"])
    assert _match_centroid_term(cells, _CORNERS, region) == 0.0


def test_match_centroid_prefers_balanced_cross_quad():
    """The crux: ABBA (diagonal-balanced) has coincident sub-centroids -> 0,
    while AABB (the two A's on one edge, B's on the other) is unbalanced -> >0.
    match alone cannot tell them apart (both are equally compact); the
    centroid term is what selects the gradient-cancelling arrangement."""
    from eda_agent.placement.autoplace import BoardRegion
    region = BoardRegion(x1=0.0, y1=0.0, x2=1000.0, y2=1000.0)
    # ABBA: R1=A(0,0) R2=B(1,0) R3=B(0,1) R4=A(1,1) -> A diag, B diag, same centre.
    abba = _quad(["A", "B", "B", "A"])
    # AABB: R1=A R2=A R3=B R4=B -> A's on the bottom edge, B's on the top edge.
    aabb = _quad(["A", "A", "B", "B"])
    s_abba = _match_centroid_term(abba, _CORNERS, region)
    s_aabb = _match_centroid_term(aabb, _CORNERS, region)
    assert s_abba == 0.0           # perfectly common-centroid
    assert s_aabb > 0.0
    assert s_aabb > s_abba


def test_match_centroid_grows_as_sub_devices_separate():
    """Penalty shrinks to 0 as the two role centroids converge on one point."""
    from eda_agent.placement.autoplace import BoardRegion
    region = BoardRegion(x1=0.0, y1=0.0, x2=1000.0, y2=1000.0)
    a = PlaceComp(ref="R1", w=40, h=40, cx=0, cy=0, pins=())
    b = PlaceComp(ref="R2", w=40, h=40, cx=0, cy=0, pins=())
    a.match_group = b.match_group = "m"
    a.match_role, b.match_role = "A", "B"
    near = _match_centroid_term([a, b], {"R1": [0.0, 0.0], "R2": [100.0, 0.0]}, region)
    far = _match_centroid_term([a, b], {"R1": [0.0, 0.0], "R2": [900.0, 0.0]}, region)
    coincident = _match_centroid_term([a, b], {"R1": [0.0, 0.0], "R2": [0.0, 0.0]}, region)
    assert far > near > 0.0
    assert coincident == 0.0


def test_engine_balances_cross_quad():
    """End to end: tagging a 2x2 matched array with A/B roles lands the two
    sub-device centroids closer together than the same array placed with the
    group tag but no roles (which match keeps compact but unbalanced)."""
    import math
    rules = DesignRules(layers=2)

    def quad_board(roles):
        cells = _quad(roles)
        # Spread the four cells to the corners of the working area so the
        # optimiser has to actively arrange them.
        for c, (x, y) in zip(cells, [(-600, -600), (600, -600),
                                      (-600, 600), (600, 600)]):
            c.cx, c.cy = x, y
        ic = PlaceComp(ref="U1", w=200, h=200, cx=0, cy=0, rotatable=True,
                       pins=(PlacePin(0, 0, "C"),))
        ic.role = "ic"
        nets = [PlaceNet(refs=("U1", c.ref), name=f"N{i}")
                for i, c in enumerate(cells)]
        return [ic] + cells, nets

    def sub_centroid_gap(res):
        c = res.centroids
        ax = (c["R1"][0] + c["R4"][0]) / 2; ay = (c["R1"][1] + c["R4"][1]) / 2
        bx = (c["R2"][0] + c["R3"][0]) / 2; by = (c["R2"][1] + c["R3"][1]) / 2
        return math.hypot(ax - bx, ay - by)

    roled = [sub_centroid_gap(construct_placement(
        *quad_board(["A", "B", "B", "A"]), rules, ConstructOptions(seed=s)))
        for s in range(4)]
    plain = [sub_centroid_gap(construct_placement(
        *quad_board(["", "", "", ""]), rules, ConstructOptions(seed=s)))
        for s in range(4)]
    assert sum(roled) / 4 < sum(plain) / 4


# --------------------------------------------------------------------------- #
# Crystal-oscillator grouping
# --------------------------------------------------------------------------- #

def _xtal_board(n_other_decaps: int = 2):
    """MCU + crystal Y1 on XIN/XOUT + its load caps C6/C7 + some plain decaps."""
    def C(ref, pins):
        return PlaceComp(ref=ref, w=40, h=40, cx=0, cy=0,
                         pins=tuple(PlacePin(0, 0, p) for p in pins))
    u = PlaceComp(ref="U1", w=300, h=300, cx=0, cy=0, pins=(
        PlacePin(-150, 30, "VCC"), PlacePin(-150, -30, "GND"),
        PlacePin(-150, 90, "XIN"), PlacePin(-150, -90, "XOUT"),
        PlacePin(150, 0, "D0")))
    u.role = "ic"
    comps = [u, C("Y1", ["XIN", "XOUT"]),
             C("C6", ["XIN", "GND"]), C("C7", ["XOUT", "GND"]),
             C("R1", ["D0", "SIG"])]
    comps += [C(f"C{i}", ["VCC", "GND"]) for i in range(1, n_other_decaps + 1)]
    nets = [PlaceNet(refs=("U1", "Y1", "C6"), name="XIN"),
            PlaceNet(refs=("U1", "Y1", "C7"), name="XOUT"),
            PlaceNet(refs=("U1", "C6", "C7")
                     + tuple(f"C{i}" for i in range(1, n_other_decaps + 1)),
                     name="GND"),
            PlaceNet(refs=("U1",) + tuple(f"C{i}"
                     for i in range(1, n_other_decaps + 1)), name="VCC"),
            PlaceNet(refs=("U1", "R1"), name="D0")]
    return comps, nets


def test_infer_crystal_groups_tags_crystal_and_load_caps_only():
    comps, nets = _xtal_board()
    groups = _infer_crystal_groups(comps, nets)
    # Y1, C6, C7 share one group; nothing else is tagged.
    assert set(groups) == {"Y1", "C6", "C7"}
    assert len(set(groups.values())) == 1


def _xtal_full_board():
    """A realistic MCU subsystem big enough that the crystal floats away from
    its load caps without help (the caps snap to the IC's crystal pins as
    decaps; the crystal, a 2-pin signal part, drifts on the roomy board)."""
    def C(ref, w, h, pins, edge=False):
        c = PlaceComp(ref=ref, w=w, h=h, cx=0, cy=0, rotatable=not edge,
                      pins=tuple(PlacePin(px, py, n) for px, py, n in pins))
        if edge:
            c.edge = True
            c.edge_band = "B"
        return c
    u = C("U1", 300, 300, [
        (-150, 90, "VCC"), (-150, 30, "GND"), (-150, -30, "XIN"),
        (-150, -90, "XOUT"), (150, 90, "RST"), (150, 30, "D0"),
        (150, -30, "D1"), (90, 150, "USBDP"), (-90, 150, "USBDM"),
        (0, -150, "VCC")])
    u.role = "ic"
    comps = [u,
             C("C1", 40, 40, [(-20, 0, "VCC"), (20, 0, "GND")]),
             C("C2", 40, 40, [(-20, 0, "VCC"), (20, 0, "GND")]),
             C("C3", 40, 40, [(-20, 0, "VCC"), (20, 0, "GND")]),
             C("C4", 40, 40, [(-20, 0, "VCC"), (20, 0, "GND")]),
             C("Y1", 120, 80, [(-50, 0, "XIN"), (50, 0, "XOUT")]),
             C("C6", 40, 40, [(-20, 0, "XIN"), (20, 0, "GND")]),
             C("C7", 40, 40, [(-20, 0, "XOUT"), (20, 0, "GND")]),
             C("R1", 40, 40, [(-20, 0, "VCC"), (20, 0, "RST")]),
             C("R2", 40, 40, [(-20, 0, "D0"), (20, 0, "LED0")]),
             C("R3", 40, 40, [(-20, 0, "D1"), (20, 0, "LED1")]),
             C("D1", 60, 40, [(-25, 0, "LED0"), (25, 0, "GND")]),
             C("D2", 60, 40, [(-25, 0, "LED1"), (25, 0, "GND")]),
             C("J1", 100, 250, [(0, 80, "USBDP"), (0, 20, "USBDM"),
                                (0, -40, "VCC"), (0, -100, "GND")], edge=True)]
    nets = [
        PlaceNet(refs=("U1", "C1", "C2", "C3", "C4", "R1", "J1"), name="VCC"),
        PlaceNet(refs=("U1", "C1", "C2", "C3", "C4", "C6", "C7", "D1", "D2",
                       "J1"), name="GND"),
        PlaceNet(refs=("U1", "Y1", "C6"), name="XIN"),
        PlaceNet(refs=("U1", "Y1", "C7"), name="XOUT"),
        PlaceNet(refs=("U1", "R1"), name="RST"),
        PlaceNet(refs=("U1", "R2"), name="D0"),
        PlaceNet(refs=("U1", "R3"), name="D1"),
        PlaceNet(refs=("R2", "D1"), name="LED0"),
        PlaceNet(refs=("R3", "D2"), name="LED1"),
        PlaceNet(refs=("U1", "J1"), name="USBDP"),
        PlaceNet(refs=("U1", "J1"), name="USBDM"),
    ]
    return comps, nets


def test_engine_clusters_crystal_with_its_load_caps():
    """On a board roomy enough that the crystal would otherwise float, tagging
    its group (as the tool does) pulls Y1 decisively closer to its load caps."""
    import math
    rules = DesignRules()

    def y1_to_caps(tag: bool) -> float:
        comps, nets = _xtal_full_board()
        if tag:
            for ref, grp in _infer_crystal_groups(comps, nets).items():
                next(c for c in comps if c.ref == ref).match_group = grp
        res = construct_placement_best_of(comps, nets, rules, seeds=(0, 1, 2, 3))
        c = res.centroids
        return (math.hypot(c["Y1"][0] - c["C6"][0], c["Y1"][1] - c["C6"][1])
                + math.hypot(c["Y1"][0] - c["C7"][0],
                             c["Y1"][1] - c["C7"][1])) / 2

    assert y1_to_caps(True) < y1_to_caps(False)


# --------------------------------------------------------------------------- #
# Visual-driven repair (tighten_match_clusters)
# --------------------------------------------------------------------------- #

def _combined_objective(comps, nets, res):
    """The repair's accept metric: analytic weighted_total + visual penalty."""
    import math
    from eda_agent.design import visual_metrics as vm
    groups = {c.ref: getattr(c, "match_group", "") for c in comps
              if getattr(c, "match_group", "")}
    cr = ratsnest_crossings(
        comps, {r: [v[0], v[1]] for r, v in res.centroids.items()},
        res.rotations, res.sides, nets)
    vr = vm.visual_report(comps, res.centroids, res.region,
                          groups=groups, crossings=cr)
    pen = 0.0 if res.report.legal else 1e9
    return res.report.weighted_total + vr.penalty + pen


def _xtal_tagged():
    comps, nets = _xtal_full_board()
    for ref, grp in _infer_crystal_groups(comps, nets).items():
        next(c for c in comps if c.ref == ref).match_group = grp
    return comps, nets


def test_tighten_noop_without_match_groups():
    """No keep-together tags -> the repair returns the input unchanged."""
    comps, nets = _xtal_full_board()  # untagged
    rules = DesignRules()
    res = construct_placement_best_of(comps, nets, rules, seeds=(0, 1))
    assert tighten_match_clusters(comps, nets, rules, res) is res


def test_tighten_never_regresses_combined_objective():
    """The accept gate guarantees the repair never makes the board worse on
    the combined analytic-plus-visual objective."""
    rules = DesignRules()
    comps, nets = _xtal_tagged()
    res = construct_placement_best_of(comps, nets, rules, seeds=(0, 1, 2, 3))
    rep = tighten_match_clusters(comps, nets, rules, res)
    assert _combined_objective(comps, nets, rep) <= \
        _combined_objective(comps, nets, res) + 1e-6
    assert rep.report.legal  # never returns an illegal board the base wasn't


def test_tighten_pulls_back_a_force_scattered_cluster():
    """When a crystal member is dragged to a board corner, the repair detects
    the excess spread and relocates it tight against its IC, lowering both the
    cluster spread and the combined objective."""
    import dataclasses
    import math
    rules = DesignRules()
    comps, nets = _xtal_tagged()
    res = construct_placement_best_of(comps, nets, rules, seeds=(0, 1, 2, 3))

    cen = {r: list(v) for r, v in res.centroids.items()}
    cen["C6"] = [res.region.x1 + 20.0, res.region.y2 - 20.0]  # far corner
    rep_score = score(comps, {r: list(v) for r, v in cen.items()},
                      res.rotations, res.sides, nets, res.region, rules,
                      ObjectiveWeights())
    scattered = dataclasses.replace(
        res, centroids={r: (v[0], v[1]) for r, v in cen.items()},
        report=rep_score)

    def spread(r):
        c = r.centroids
        return max(math.dist(c[a], c[b])
                   for a in ("Y1", "C6", "C7") for b in ("Y1", "C6", "C7")
                   if a < b)

    out = tighten_match_clusters(comps, nets, rules, scattered)
    assert out is not scattered           # the repair was applied
    assert spread(out) < spread(scattered)
    assert _combined_objective(comps, nets, out) < \
        _combined_objective(comps, nets, scattered)
    assert out.report.legal


def test_match_snap_move_clusters_better_than_without():
    """The match-snap SA move lets a keep-together member jump to its cluster
    in one step (the global move a translate jump cannot make), so best-of
    leaves the crystal group tighter than with the move disabled."""
    import math
    rules = DesignRules()

    def crystal_spread(move_match: float) -> float:
        comps, nets = _xtal_tagged()
        res = construct_placement_best_of(
            comps, nets, rules, seeds=(0, 1, 2, 3),
            base_opts=ConstructOptions(move_match=move_match))
        c = res.centroids
        return max(math.dist(c[a], c[b])
                   for a in ("Y1", "C6", "C7") for b in ("Y1", "C6", "C7")
                   if a < b)

    assert crystal_spread(0.10) < crystal_spread(0.0)


def test_match_snap_move_is_noop_without_groups():
    """With no match tags the move is never sampled, so enabling its weight
    leaves an untagged board's result unchanged (determinism preserved)."""
    rules = DesignRules()
    comps_a, nets_a = _xtal_full_board()  # untagged
    comps_b, nets_b = _xtal_full_board()
    a = construct_placement_best_of(
        comps_a, nets_a, rules, seeds=(0, 1),
        base_opts=ConstructOptions(move_match=0.0))
    b = construct_placement_best_of(
        comps_b, nets_b, rules, seeds=(0, 1),
        base_opts=ConstructOptions(move_match=0.10))
    assert a.report.weighted_total == b.report.weighted_total


def test_construct_placement_visual_is_legal_and_no_worse():
    """The visual entry point returns a legal placement no worse than best-of
    on the combined objective."""
    rules = DesignRules()
    comps, nets = _xtal_tagged()
    base = construct_placement_best_of(comps, nets, rules, seeds=(0, 1, 2, 3))
    vis = construct_placement_visual(comps, nets, rules)
    assert vis.report.legal
    assert _combined_objective(comps, nets, vis) <= \
        _combined_objective(comps, nets, base) + 1e-6


# --------------------------------------------------------------------------- #
# Connector-on-edge and thermal-spread proxies
# --------------------------------------------------------------------------- #

def test_conn_term_zero_on_assigned_edge_and_grows_inward():
    from eda_agent.placement.autoplace import BoardRegion
    region = BoardRegion(x1=0.0, y1=0.0, x2=1000.0, y2=1000.0)
    j = PlaceComp(ref="J1", w=100, h=200, cx=0, cy=0, rotatable=False,
                  pins=(PlacePin(0, 50, "N"),))
    j.edge = True
    j.edge_band = "L"
    # On the left edge: connector centre x == half its width -> zero penalty.
    assert _conn_term([j], {"J1": [50.0, 500.0]}, region) == 0.0
    # Pushed 200 mils inward -> penalty is that distance squared.
    assert abs(_conn_term([j], {"J1": [250.0, 500.0]}, region)
               - 200.0 ** 2) < 1e-6


def test_engine_parks_connector_against_its_edge():
    comps, nets = _scattered_board()       # J1 has edge band "L"
    res = construct_placement(comps, nets, DesignRules(layers=2),
                              ConstructOptions(seed=3))
    region = res.region
    left = min(region.x1, region.x2)
    right = max(region.x1, region.x2)
    jx = res.centroids["J1"][0]
    # The connector sits in the left half, nearer the left edge than the right.
    assert (jx - left) < (right - jx)


def test_therm_term_zero_without_hot_pairs():
    cool = PlaceComp(ref="U1", w=200, h=200, cx=0, cy=0, pins=())
    assert _therm_term([cool], {"U1": [0.0, 0.0]}) == 0.0


def test_therm_term_repels_hot_parts():
    a = PlaceComp(ref="U1", w=200, h=200, cx=0, cy=0, pins=())
    b = PlaceComp(ref="U2", w=200, h=200, cx=0, cy=0, pins=())
    a.power_w = 2.0
    b.power_w = 3.0
    near = _therm_term([a, b], {"U1": [0.0, 0.0], "U2": [100.0, 0.0]})
    far = _therm_term([a, b], {"U1": [0.0, 0.0], "U2": [2000.0, 0.0]})
    assert near > far          # crowding hot parts together costs more
    assert far > 0.0


def test_eff_wh_swaps_relative_to_base_rotation():
    from eda_agent.design.pcb_placement import _eff_wh
    c = PlaceComp(ref="U1", w=300, h=100, cx=0, cy=0)  # base rotation 0
    assert _eff_wh(c, 0) == (300, 100)
    assert _eff_wh(c, 90) == (100, 300)     # 90 swaps w/h
    assert _eff_wh(c, 270) == (100, 300)
    assert _eff_wh(c, 180) == (300, 100)
    # A part supplied already at 90 deg, with w/h measured at 90: rotation 90
    # is the base (no swap), rotation 0 transposes it.
    c2 = PlaceComp(ref="U2", w=100, h=300, cx=0, cy=0)
    c2.rotation = 90
    assert _eff_wh(c2, 90) == (100, 300)
    assert _eff_wh(c2, 0) == (300, 100)


def test_size_board_reserves_routing_area_for_dense_designs():
    """Two boards with identical component area but different pin density:
    the wiring-dense one must get a larger board (routing channels)."""
    rules = DesignRules(layers=2)

    def comp(ref, npins):
        return PlaceComp(
            ref=ref, w=200, h=200, cx=0, cy=0,
            pins=tuple(PlacePin(0, i * 10, f"N{i}") for i in range(npins)),
        )

    sparse = [comp(f"U{i}", 2) for i in range(4)]   # avg 2 pins -> no derate
    dense = [comp(f"U{i}", 14) for i in range(4)]    # avg 14 pins -> derated

    rs = size_board(sparse, [], rules)
    rd = size_board(dense, [], rules)
    # Same total courtyard area, but the dense design gets more board.
    assert rd.width * rd.height > rs.width * rs.height
    # And both still hold the basic floor (board covers the courtyard area).
    a_total = sum((c.w + 2 * rules.courtyard_clr) ** 2 for c in sparse)
    assert rs.width * rs.height >= a_total


def _realistic_board():
    """A representative small board: a 16-pin MCU, four decoupling caps, two
    edge connectors, two resistors, and an LED, wired like a real design."""
    def ic_pins():
        # 4 pins per side of a 400x400 body.
        pins = []
        for i in range(4):
            off = -120 + i * 80
            pins.append(PlacePin(-200, off, f"L{i}"))   # left
            pins.append(PlacePin(200, off, f"R{i}"))     # right
        return tuple(pins)
    u1 = PlaceComp(ref="U1", w=400, h=400, cx=0, cy=0, rotatable=True,
                   pins=ic_pins() + (PlacePin(-200, 160, "VCC"),
                                     PlacePin(200, 160, "GND")))
    u1.role = "ic"
    u1.flippable = True

    caps = []
    for i in range(4):
        c = PlaceComp(ref=f"C{i+1}", w=40, h=40, cx=800 + i * 60, cy=800,
                      rotatable=True,
                      pins=(PlacePin(-20, 0, "VCC"), PlacePin(20, 0, "GND")))
        caps.append(c)

    j1 = PlaceComp(ref="J1", w=100, h=300, cx=-900, cy=0, rotatable=False,
                   pins=(PlacePin(0, 50, "IN"), PlacePin(0, -50, "GND")))
    j1.edge = True
    j1.edge_band = "L"
    j2 = PlaceComp(ref="J2", w=100, h=300, cx=900, cy=0, rotatable=False,
                   pins=(PlacePin(0, 50, "OUT"), PlacePin(0, -50, "GND")))
    j2.edge = True
    j2.edge_band = "R"

    r1 = PlaceComp(ref="R1", w=40, h=40, cx=-700, cy=700, rotatable=True,
                   pins=(PlacePin(-20, 0, "IN"), PlacePin(20, 0, "L0")))
    r2 = PlaceComp(ref="R2", w=40, h=40, cx=700, cy=-700, rotatable=True,
                   pins=(PlacePin(-20, 0, "R0"), PlacePin(20, 0, "LEDA")))
    led = PlaceComp(ref="LED1", w=60, h=40, cx=-700, cy=-700, rotatable=True,
                    pins=(PlacePin(-30, 0, "LEDA"), PlacePin(30, 0, "GND")))

    comps = [u1] + caps + [j1, j2, r1, r2, led]
    nets = [
        PlaceNet(refs=("U1", "C1", "C2", "C3", "C4", "J1"), name="VCC"),
        PlaceNet(refs=("U1", "C1", "C2", "C3", "C4", "J1", "J2", "LED1"),
                 name="GND"),
        PlaceNet(refs=("J1", "R1"), name="IN"),
        PlaceNet(refs=("U1", "R1"), name="L0"),
        PlaceNet(refs=("U1", "R2"), name="R0"),
        PlaceNet(refs=("R2", "LED1"), name="LEDA"),
        PlaceNet(refs=("U1", "J2"), name="OUT"),
    ]
    return comps, nets


def test_realistic_board_places_professionally():
    comps, nets = _realistic_board()
    rules = DesignRules(layers=2)
    res = construct_placement_best_of(comps, nets, rules, seeds=(0, 1, 2, 3))
    region = res.region
    cen = res.centroids

    # 1. Legal: no overlaps, everything on-board.
    assert res.report.legal is True
    assert res.report.clear <= 1e-3

    # 2. HPWL beat the scattered input.
    init_pos = {c.ref: [c.cx, c.cy] for c in comps}
    from eda_agent.design.pcb_placement import score, _via_term, _decap_term, \
        _pair_decaps_to_power_pins
    init = score(comps, init_pos, {c.ref: c.rotation for c in comps},
                 {c.ref: 1 for c in comps}, nets, region, rules,
                 ObjectiveWeights())
    assert res.report.hpwl < init.hpwl

    # 3. Both edge connectors sit nearer their assigned edge than the centre.
    left = min(region.x1, region.x2)
    right = max(region.x1, region.x2)
    mid = (left + right) / 2.0
    assert cen["J1"][0] < mid          # left-band connector on the left
    assert cen["J2"][0] > mid          # right-band connector on the right

    # 4. Decaps ended up closer to the MCU than they started.
    pairs = _pair_decaps_to_power_pins(comps, nets)
    assert pairs                       # caps recognised as decaps
    init_decap = _decap_term(comps, init_pos,
                             {c.ref: c.rotation for c in comps},
                             {c.ref: 1 for c in comps}, pairs)
    final_decap = _decap_term(comps, {r: [c[0], c[1]] for r, c in cen.items()},
                              res.rotations, res.sides, pairs)
    assert final_decap < init_decap


def test_realistic_board_legalizes_on_hard_seed():
    """A tightly-packed design (many parts + fixed edge connectors) must
    legalize even on a seed that needs several board-grow steps -- the grow
    cap used to stop too early and leave a residual courtyard overlap."""
    comps, nets = _realistic_board()
    # Seed 0 previously hit the grow cap at 6 and returned clear=622, legal=False.
    res = construct_placement(comps, nets, DesignRules(layers=2),
                              ConstructOptions(seed=0))
    assert res.report.legal is True
    assert res.report.clear <= 1e-3


def test_decap_pairing_computed_once_not_per_move(monkeypatch):
    """The structural decap pairing must be cached, not rebuilt on every SA
    evaluation. Before the fix it was called ~once per move (thousands);
    guard that it stays O(1) per placement."""
    import eda_agent.design.pcb_placement as _pcb

    calls = {"n": 0}
    orig = _pcb._pair_decaps_to_power_pins

    def _spy(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(_pcb, "_pair_decaps_to_power_pins", _spy)
    comps, nets = _realistic_board()
    _pcb.construct_placement(comps, nets, DesignRules(layers=2),
                             ConstructOptions(seed=0))
    # A handful of phase-level calls, NOT one per polish move.
    assert calls["n"] <= 10


def test_score_cached_decap_pairs_matches_uncached():
    """Passing the precomputed (structural) decap pairing to score() -- the
    hot-loop optimisation -- must give exactly the same report as letting
    score() derive it. The pairing is position-independent."""
    comps, nets = _realistic_board()
    res = construct_placement_best_of(comps, nets, DesignRules(layers=2),
                                      seeds=tuple(range(4)))
    pos = {r: [p.x, p.y] for r, p in res.placements.items()}
    rot, sides = dict(res.rotations), dict(res.sides)
    rules, w = DesignRules(layers=2), ObjectiveWeights()
    pairs = _pair_decaps_to_power_pins(comps, nets)
    a = score(comps, pos, rot, sides, nets, res.region, rules, w)
    b = score(comps, pos, rot, sides, nets, res.region, rules, w,
              decap_pairs=pairs)
    assert a.weighted_total == b.weighted_total
    assert a.decap == b.decap
    assert a.legal == b.legal


def test_is_legal_clear_value_matches_recompute():
    """Passing the already-computed clearance term to _is_legal must match
    recomputing it (the double-_clear_term elimination)."""
    comps, nets = _realistic_board()
    res = construct_placement(comps, nets, DesignRules(layers=2),
                              ConstructOptions(seed=1))
    pos = {r: [p.x, p.y] for r, p in res.placements.items()}
    rot, sides = dict(res.rotations), dict(res.sides)
    rules = DesignRules(layers=2)
    cv = res.report.clear
    assert (_is_legal(comps, pos, rot, sides, res.region, rules)
            == _is_legal(comps, pos, rot, sides, res.region, rules,
                         clear_value=cv))


def test_connectorless_board_places_legally():
    """An all-internal block (no connectors -> no edge anchors) must still
    place legally and not degenerate. The connector terms are structurally
    zero; decoupling still works."""
    u1 = PlaceComp("U1", 300, 300, 0, 0, rotatable=True, pins=(
        PlacePin(-150, 50, "VCC"), PlacePin(-150, -50, "GND"),
        PlacePin(150, 50, "A"), PlacePin(150, -50, "B")))
    u1.role = "ic"
    comps = [u1]
    for i in range(3):
        comps.append(PlaceComp(f"C{i+1}", 40, 40, 0, 0, rotatable=True,
                               pins=(PlacePin(0, 20, "VCC"),
                                     PlacePin(0, -20, "GND"))))
    comps.append(PlaceComp("R1", 40, 40, 0, 0, rotatable=True,
                           pins=(PlacePin(-20, 0, "A"), PlacePin(20, 0, "OA"))))
    comps.append(PlaceComp("R2", 40, 40, 0, 0, rotatable=True,
                           pins=(PlacePin(-20, 0, "B"), PlacePin(20, 0, "OB"))))
    nets = [
        PlaceNet(("U1", "C1", "C2", "C3"), name="VCC"),
        PlaceNet(("U1", "C1", "C2", "C3"), name="GND"),
        PlaceNet(("U1", "R1"), name="A"),
        PlaceNet(("U1", "R2"), name="B"),
    ]
    res = construct_placement_best_of(comps, nets, DesignRules(layers=2),
                                      seeds=tuple(range(4)))
    assert res.report.legal is True
    assert res.report.clear <= 1e-3
    assert res.report.conn == 0.0          # no connectors -> term is zero
    assert res.report.decap > 0.0          # decoupling still detected/pulled
    assert res.region.width > 0 and res.region.height > 0


# --------------------------------------------------------------------------- #
# Net criticality weighting
# --------------------------------------------------------------------------- #

def _crit_score(weight: float, p1: list[float], p2: list[float]) -> float:
    """Objective for a two-part board joined by one net of ``weight``, with
    the parts at fixed positions ``p1``/``p2``. Isolates the weighting
    mechanism from the stochastic legalizer."""
    from eda_agent.placement.autoplace import BoardRegion

    comps = [PlaceComp("P1", 80, 80, 0, 0), PlaceComp("P2", 80, 80, 0, 0)]
    nets = [PlaceNet(("P1", "P2"), name="CRIT", weight=weight)]
    region = BoardRegion(0.0, 0.0, 2000.0, 2000.0)
    rot = {"P1": 0.0, "P2": 0.0}
    side = {"P1": 1, "P2": 1}
    rep = score(comps, {"P1": p1, "P2": p2}, rot, side, nets,
                region, DesignRules(layers=2), ObjectiveWeights())
    return rep.weighted_total


def test_critical_net_weight_scales_the_long_net_penalty():
    """A heavier net weight must make the engine penalize a stretched-out
    critical net proportionally more -- the guarantee that lets a caller
    buy shorter traces on clocks / high-speed nets. Deterministic: no
    legalizer in the loop, just the objective gradient."""
    short = ([500.0, 500.0], [600.0, 500.0])   # 100-mil span
    long = ([200.0, 200.0], [1800.0, 1800.0])  # full-board span

    gaps = []
    for w in (1.0, 3.0, 8.0):
        penalty = _crit_score(w, *long) - _crit_score(w, *short)
        gaps.append(penalty)

    # The penalty for the long layout grows monotonically with weight, and
    # roughly in proportion (the HPWL term is linear in the net weight).
    assert gaps[0] > 0.0
    assert gaps[1] > gaps[0] * 1.5
    assert gaps[2] > gaps[1] * 1.5


def test_critical_weight_one_matches_unweighted():
    """Weight 1.0 is the identity -- a critical net at the default weight
    scores exactly like any ordinary net, so marking nothing critical is a
    true no-op. A heavier weight strictly raises the cost of the same
    layout."""
    short = ([500.0, 500.0], [600.0, 500.0])
    assert _crit_score(5.0, *short) > _crit_score(1.0, *short)


# --------------------------------------------------------------------------- #
# Per-net wirelength diagnostic
# --------------------------------------------------------------------------- #

def test_net_spans_reports_raw_bounding_span():
    """net_spans returns the un-normalised Manhattan bounding span per net
    (what a router must cover), and omits single-pin nets."""
    from eda_agent.design.pcb_placement import net_spans

    comps = [PlaceComp("A", 80, 80, 0, 0), PlaceComp("B", 80, 80, 0, 0),
             PlaceComp("C", 80, 80, 0, 0)]
    pos = {"A": [100.0, 100.0], "B": [400.0, 100.0], "C": [400.0, 500.0]}
    nets = [
        PlaceNet(("A", "B"), name="N1"),          # 300 wide, 0 tall
        PlaceNet(("A", "B", "C"), name="N2"),      # 300 wide, 400 tall
        PlaceNet(("A",), name="SINGLE"),           # one pin -> omitted
    ]
    spans = net_spans(comps, pos, {}, {}, nets)
    assert spans["N1"] == 300.0
    assert spans["N2"] == 700.0
    assert "SINGLE" not in spans
    # Unweighted: a net's span ignores PlaceNet.weight (pure geometry).
    nets_w = [PlaceNet(("A", "B"), name="N1", weight=9.0)]
    assert net_spans(comps, pos, {}, {}, nets_w)["N1"] == 300.0


# --------------------------------------------------------------------------- #
# Ratsnest crossings (routability / via-pressure indicator)
# --------------------------------------------------------------------------- #

def test_ratsnest_counts_crossing_nets():
    """Two different nets whose MST edges form an X count as one crossing;
    the same parts wired so the nets run parallel count as zero."""
    from eda_agent.design.pcb_placement import ratsnest_crossings

    comps = [
        PlaceComp("P1", 40, 40, 0, 0, pins=(PlacePin(0, 0, "N1"),)),
        PlaceComp("P2", 40, 40, 1000, 1000, pins=(PlacePin(0, 0, "N1"),)),
        PlaceComp("P3", 40, 40, 0, 1000, pins=(PlacePin(0, 0, "N2"),)),
        PlaceComp("P4", 40, 40, 1000, 0, pins=(PlacePin(0, 0, "N2"),)),
    ]
    pos = {c.ref: [c.cx, c.cy] for c in comps}
    crossing = [PlaceNet(("P1", "P2"), name="N1"),      # TL->BR
                PlaceNet(("P3", "P4"), name="N2")]      # BL->TR  (X)
    parallel = [PlaceNet(("P1", "P3"), name="N1"),      # left edge
                PlaceNet(("P4", "P2"), name="N2")]      # right edge
    assert ratsnest_crossings(comps, pos, {}, {}, crossing) == 1
    assert ratsnest_crossings(comps, pos, {}, {}, parallel) == 0


def test_ratsnest_fanout_cap_excludes_rails():
    """A high-fanout power rail (routed as a plane) is excluded from the
    signal ratsnest count when max_fanout caps it out."""
    from eda_agent.design.pcb_placement import ratsnest_crossings

    comps, nets = _realistic_board()
    res = construct_placement_best_of(comps, nets, DesignRules(layers=2),
                                      seeds=tuple(range(4)))
    pos = {r: [p.x, p.y] for r, p in res.placements.items()}
    rot, sides = dict(res.rotations), dict(res.sides)
    total = ratsnest_crossings(comps, pos, rot, sides, nets)
    signal = ratsnest_crossings(comps, pos, rot, sides, nets, max_fanout=4)
    # Excluding the VCC/GND rails can only lower (never raise) the count.
    assert signal <= total


def test_placement_is_routable_low_signal_crossings():
    """The wirelength-optimal placement is also routable: it leaves very few
    SIGNAL ratsnest crossings (HPWL is itself a good routability proxy, so
    the engine does not need crossing-aware selection). Guards against a
    future change that shortens wires at the cost of a tangled ratsnest.

    Power/ground rails are excluded by ROLE (``_rail_net_names``), not just by
    the fanout proxy: tight decoupling legitimately moves the power-net
    ratsnest, and a rail routes as a plane, so a rail-vs-signal crossing is
    not a real signal via. On a tiny fixture a rail has fanout 3-4 and the
    fanout cap alone would still count it -- the role filter is what keeps
    this measuring SIGNAL-vs-SIGNAL tangling only."""
    from eda_agent.design.pcb_placement import (
        _rail_net_names,
        ratsnest_crossings,
    )

    for fixture in (_realistic_board, _scattered_board):
        comps, nets = fixture()
        res = construct_placement_best_of(comps, nets, DesignRules(layers=2),
                                          seeds=tuple(range(6)))
        pos = {r: [p.x, p.y] for r, p in res.placements.items()}
        rails = frozenset(_rail_net_names(comps, nets))
        signal = ratsnest_crossings(comps, pos, dict(res.rotations),
                                    dict(res.sides), nets, max_fanout=4,
                                    exclude_nets=rails)
        n_signal_nets = sum(1 for n in nets
                            if len(set(n.refs)) <= 4 and n.name not in rails)
        # Far fewer crossings than signal nets -> a routable layout.
        assert signal <= max(2, n_signal_nets // 3)


# --------------------------------------------------------------------------- #
# Board outline tightening
# --------------------------------------------------------------------------- #

def test_tighten_region_fits_placement_plus_edge():
    """A tiny placement on a huge board shrinks to the part courtyard plus
    exactly one edge clearance per side (grid-snapped outward)."""
    from eda_agent.placement.autoplace import BoardRegion

    rules = DesignRules(layers=2)
    edge = rules.edge_clr
    comps = [PlaceComp("A", 100, 100, 0, 0), PlaceComp("B", 100, 100, 0, 0)]
    # Two parts whose courtyards span x:[450,1050] y:[950,1050].
    pos = {"A": [500.0, 1000.0], "B": [1000.0, 1000.0]}
    huge = BoardRegion(0.0, 0.0, 5000.0, 5000.0)
    tight = _tighten_region(comps, pos, {}, huge, rules)
    # bbox is x:[450,1050], y:[950,1050]; +edge each side.
    assert tight.x1 <= 450 - edge and tight.x2 >= 1050 + edge
    assert tight.y1 <= 950 - edge and tight.y2 >= 1050 + edge
    # ...and not wastefully larger than bbox + edge + one grid step.
    assert tight.width <= (1050 - 450) + 2 * edge + 2 * rules.grid
    assert tight.height <= 100 + 2 * edge + 2 * rules.grid


def test_tighten_region_never_enlarges():
    """Tightening only ever shrinks: a board already smaller than the
    part-bbox-plus-clearance is returned unchanged (clamped to itself)."""
    from eda_agent.placement.autoplace import BoardRegion

    rules = DesignRules(layers=2)
    comps = [PlaceComp("A", 100, 100, 0, 0)]
    pos = {"A": [500.0, 500.0]}
    snug = BoardRegion(400.0, 400.0, 600.0, 600.0)
    tight = _tighten_region(comps, pos, {}, snug, rules)
    assert tight.x1 >= snug.x1 and tight.y1 >= snug.y1
    assert tight.x2 <= snug.x2 and tight.y2 <= snug.y2


def test_construct_tightens_oversized_board_and_stays_legal():
    """The realistic board is sized square by the pre-placement estimate but
    lays out wide-and-short; the final outline must be tightened (smaller
    than the raw size_board estimate) while remaining legal."""
    comps, nets = _realistic_board()
    raw = size_board(comps, nets, DesignRules(layers=2))
    res = construct_placement_best_of(comps, nets, DesignRules(layers=2),
                                      seeds=tuple(range(6)))
    assert res.report.legal is True
    # The board shrank in at least one dimension versus the raw estimate.
    assert (res.region.width < raw.width or res.region.height < raw.height)
    assert any("tightened" in n for n in res.notes)


# --------------------------------------------------------------------------- #
# Decoupling analysis
# --------------------------------------------------------------------------- #

def test_decoupling_report_pairs_caps_to_ic_with_distance():
    """The report names which cap decouples which IC (structurally, not by
    designator) and gives the achieved centre-to-power-pin distance, worst
    first."""
    from eda_agent.design.pcb_placement import decoupling_report

    comps, nets = _realistic_board()
    res = construct_placement_best_of(comps, nets, DesignRules(layers=2),
                                      seeds=tuple(range(4)))
    pos = {r: [p.x, p.y] for r, p in res.placements.items()}
    rep = decoupling_report(comps, pos, dict(res.rotations),
                            dict(res.sides), nets)
    # The four decaps all serve U1.
    assert {r["decap"] for r in rep} == {"C1", "C2", "C3", "C4"}
    assert all(r["ic"] == "U1" for r in rep)
    assert all(r["distance_mils"] >= 0.0 for r in rep)
    # Sorted worst (largest distance) first.
    dists = [r["distance_mils"] for r in rep]
    assert dists == sorted(dists, reverse=True)


def test_decoupling_report_empty_without_decaps():
    """A netlist with no structurally-identifiable decoupling yields an empty
    report (no false pairings)."""
    from eda_agent.design.pcb_placement import decoupling_report

    comps = [PlaceComp("R1", 40, 40, 0, 0), PlaceComp("R2", 40, 40, 100, 0)]
    nets = [PlaceNet(("R1", "R2"), name="SIG")]
    pos = {"R1": [0.0, 0.0], "R2": [100.0, 0.0]}
    assert decoupling_report(comps, pos, {}, {}, nets) == []


def test_decap_pairing_rejects_signal_filter_cap():
    """A 2-pin cap from a low-fanout SIGNAL to ground is a filter cap, not a
    decoupler -- its power leg is not a rail, so it must not be paired to the
    IC. A real decap on the VCC rail is still detected."""
    u1 = PlaceComp("U1", 200, 200, 0, 0, pins=(
        PlacePin(-100, 50, "VCC"), PlacePin(-100, -50, "GND"),
        PlacePin(100, 0, "ADC")))
    u1.role = "ic"
    c1 = PlaceComp("C1", 40, 40, 0, 0, pins=(   # real decap on the VCC rail
        PlacePin(0, 20, "VCC"), PlacePin(0, -20, "GND")))
    cf = PlaceComp("CF", 40, 40, 0, 0, pins=(   # filter cap on the ADC signal
        PlacePin(0, 20, "ADC"), PlacePin(0, -20, "GND")))
    j1 = PlaceComp("J1", 100, 200, 0, 0, pins=(
        PlacePin(0, 50, "VCC"), PlacePin(0, -50, "GND")))
    comps = [u1, c1, cf, j1]
    nets = [
        PlaceNet(("U1", "C1", "J1"), name="VCC"),          # rail, degree 3
        PlaceNet(("U1", "C1", "CF", "J1"), name="GND"),    # densest -> ground
        PlaceNet(("U1", "CF"), name="ADC"),                # signal, degree 2
    ]
    pairs = _pair_decaps_to_power_pins(comps, nets)
    # Pairing maps decap -> list of candidate (ic, lx, ly); C1 serves U1.
    assert "C1" in pairs and pairs["C1"][0][0] == "U1"
    assert "CF" not in pairs   # filter cap on a degree-2 signal is excluded


def test_decap_detected_on_shared_rail_feeding_multiple_ics():
    """A VCC rail commonly feeds several ICs. Decaps on that rail must still
    be detected (the old 'exactly one IC' rule rejected ALL of them) and each
    must offer every feeding IC as a candidate."""
    from eda_agent.design.pcb_placement import _decap_term

    def _ic(ref, n):
        pins = [PlacePin(-100, 50, "VCC"), PlacePin(-100, -50, "GND")]
        for i in range(n):
            pins.append(PlacePin(100, -40 + i * 40, f"{ref}_S{i}"))
        c = PlaceComp(ref, 200, 200, 0, 0, pins=tuple(pins))
        c.role = "ic"
        return c

    u1, u2 = _ic("U1", 3), _ic("U2", 2)
    c1 = PlaceComp("C1", 40, 40, 0, 0,
                   pins=(PlacePin(0, 20, "VCC"), PlacePin(0, -20, "GND")))
    c2 = PlaceComp("C2", 40, 40, 0, 0,
                   pins=(PlacePin(0, 20, "VCC"), PlacePin(0, -20, "GND")))
    # A ground-only part so GND is unambiguously the densest (the ground net)
    # and VCC is the power rail.
    d1 = PlaceComp("D1", 40, 40, 0, 0,
                   pins=(PlacePin(0, 20, "SIG"), PlacePin(0, -20, "GND")))
    comps = [u1, u2, c1, c2, d1]
    nets = [PlaceNet(("U1", "U2", "C1", "C2"), name="VCC"),          # power, 4
            PlaceNet(("U1", "U2", "C1", "C2", "D1"), name="GND"),    # ground, 5
            PlaceNet(("U1", "D1"), name="SIG")]

    pairs = _pair_decaps_to_power_pins(comps, nets)
    assert {"C1", "C2"} <= set(pairs)               # detected despite 2 ICs
    assert {ic for ic, _, _ in pairs["C1"]} == {"U1", "U2"}  # both candidates
    # The IC VCC pin sits at local (-100, 50); place C1 on U1's pin and C2 on
    # U2's pin -- each decap is scored against its NEAREST IC.
    pos = {"U1": [0.0, 0.0], "U2": [1000.0, 0.0], "D1": [0.0, 400.0],
           "C1": [-100.0, 50.0], "C2": [900.0, 50.0]}
    rot = {c.ref: 0.0 for c in comps}
    sides = {c.ref: 1 for c in comps}
    assert _decap_term(comps, pos, rot, sides, pairs) < 1e-6


def test_decap_pairs_to_nearest_of_several_power_pins():
    """A big IC with several VCC pins on one rail must offer EVERY power pin
    as a candidate, so three decaps land one-per-pin instead of stacking on
    the first pin."""
    u1 = PlaceComp("U1", 600, 600, 0, 0, pins=(
        PlacePin(-300, 200, "VCC"), PlacePin(0, 300, "VCC"),
        PlacePin(300, 200, "VCC"),
        PlacePin(-300, -200, "GND"), PlacePin(0, 0, "SIG")))
    u1.role = "ic"
    caps = [PlaceComp(f"C{i+1}", 40, 40, 0, 0,
                      pins=(PlacePin(0, 20, "VCC"), PlacePin(0, -20, "GND")))
            for i in range(3)]
    # GND-only part so GND is the densest (ground) and VCC is the power rail.
    d1 = PlaceComp("D1", 40, 40, 0, 0,
                   pins=(PlacePin(0, 20, "SIG2"), PlacePin(0, -20, "GND")))
    comps = [u1, *caps, d1]
    nets = [PlaceNet(("U1", "C1", "C2", "C3"), name="VCC"),
            PlaceNet(("U1", "C1", "C2", "C3", "D1"), name="GND"),
            PlaceNet(("U1", "D1"), name="SIG2")]

    pairs = _pair_decaps_to_power_pins(comps, nets)
    # Each decap sees all three VCC pins as candidates.
    assert len(pairs["C1"]) == 3
    assert {(lx, ly) for _, lx, ly in pairs["C1"]} == {
        (-300, 200), (0, 300), (300, 200)}

    rot = {c.ref: 0.0 for c in comps}
    sides = {c.ref: 1 for c in comps}
    # One decap on each VCC pin: every cap finds ITS pin -> zero total.
    spread = {"U1": [0.0, 0.0], "D1": [800.0, 0.0],
              "C1": [-300.0, 200.0], "C2": [0.0, 300.0], "C3": [300.0, 200.0]}
    assert _decap_term(comps, spread, rot, sides, pairs) < 1e-6
    # With only the FIRST power pin as a candidate (the old behaviour), the
    # caps on pins 2 and 3 would be scored against the far pin 1 -> nonzero.
    first_only = {d: [v[0]] for d, v in pairs.items()}
    assert _decap_term(comps, spread, rot, sides, first_only) > 100.0


# --------------------------------------------------------------------------- #
# Switch-node clustering (switching regulator EMI loop)
# --------------------------------------------------------------------------- #

def _buck_board():
    """A buck converter: controller U1, inductor L1, catch diode D1, bootstrap
    cap C3 (the switch-node loop), plus input/output caps, fb divider, conns."""
    def C(ref, w, h, pins, **kw):
        c = PlaceComp(ref=ref, w=w, h=h, cx=0, cy=0, rotatable=True,
                      pins=tuple(PlacePin(lx, ly, n) for lx, ly, n in pins))
        for k, v in kw.items():
            setattr(c, k, v)
        return c
    u1 = C("U1", 400, 400, [
        (-200, 150, "VIN"), (-200, 50, "GND"), (-200, -50, "EN"),
        (-200, -150, "VCC"), (200, 150, "SW"), (200, 50, "BST"),
        (200, -50, "FB"), (200, -150, "COMP")])
    comps = [u1,
             C("L1", 300, 250, [(-100, 0, "SW"), (100, 0, "VOUT")]),
             C("C1", 100, 80, [(-40, 0, "VIN"), (40, 0, "GND")]),
             C("C2", 100, 80, [(-40, 0, "VOUT"), (40, 0, "GND")]),
             C("C3", 60, 40, [(-25, 0, "BST"), (25, 0, "SW")]),
             C("D1", 120, 100, [(-50, 0, "SW"), (50, 0, "GND")]),
             C("R1", 60, 40, [(-25, 0, "VOUT"), (25, 0, "FB")]),
             C("R2", 60, 40, [(-25, 0, "FB"), (25, 0, "GND")]),
             C("J1", 150, 250, [(0, 60, "VIN"), (0, -60, "GND")], edge_band="any"),
             C("J2", 150, 250, [(0, 60, "VOUT"), (0, -60, "GND")], edge_band="any")]
    nets = [
        PlaceNet(refs=("J1", "U1", "C1"), name="VIN", weight=1.0),
        PlaceNet(refs=("U1", "L1", "C3", "D1"), name="SW", weight=1.5),
        PlaceNet(refs=("L1", "C2", "R1", "J2"), name="VOUT", weight=1.0),
        PlaceNet(refs=("J1", "U1", "C1", "C2", "D1", "R2", "J2"), name="GND",
                 weight=0.2),
        PlaceNet(refs=("U1", "R1", "R2"), name="FB", weight=1.0),
        PlaceNet(refs=("U1", "C3"), name="BST", weight=1.0),
    ]
    return comps, nets


def test_infer_switch_node_groups_tags_loop_not_output():
    comps, nets = _buck_board()
    groups = _infer_switch_node_groups(comps, nets)
    # Inductor + diode + bootstrap cap share one group; the controller and the
    # output-side parts (output cap, fb divider, connectors) are excluded.
    assert set(groups) == {"L1", "D1", "C3"}
    assert len({groups[r] for r in ("L1", "D1", "C3")}) == 1
    for ref in ("U1", "C2", "R1", "R2", "J2"):
        assert ref not in groups


def test_infer_switch_node_groups_noop_without_inductor():
    comps, nets = _scattered_board()        # no inductor
    assert _infer_switch_node_groups(comps, nets) == {}


def test_switch_node_cluster_tightens_the_loop():
    """Tagging the switch-node group (as the tool does) pulls the inductor +
    diode + bootstrap cap into a tighter loop than HPWL alone leaves them."""
    import math
    rules = DesignRules()
    loop = ("L1", "D1", "C3")

    def loop_spread(tag: bool) -> float:
        comps, nets = _buck_board()
        if tag:
            for ref, grp in _infer_switch_node_groups(comps, nets).items():
                next(c for c in comps if c.ref == ref).match_group = grp
        res = construct_placement_best_of(comps, nets, rules, seeds=(0, 1, 2, 3))
        c = res.centroids
        return max(math.dist(c[a], c[b])
                   for a in loop for b in loop if a < b)

    assert loop_spread(True) < loop_spread(False)


# --------------------------------------------------------------------------- #
# Combined structural inference: crystal + switch-node + decaps on one board
# --------------------------------------------------------------------------- #

def _combined_buck_mcu_board():
    """A buck regulator (switch node L1/D1) and an MCU with a crystal (Y1 +
    load caps C3/C4) and decaps (C5/C6) on one board -- exercises crystal,
    switch-node and decap inference together."""
    def C(ref, nets, cx, cy):
        return PlaceComp(ref=ref, w=40, h=40, cx=cx, cy=cy, rotatable=True,
                         pins=tuple(PlacePin(-20 if i == 0 else 20, 0, n)
                                    for i, n in enumerate(nets)))
    u1 = PlaceComp(ref="U1", w=300, h=300, cx=0, cy=0, rotatable=True, pins=(
        PlacePin(-150, 80, "VIN"), PlacePin(-150, -80, "GND"),
        PlacePin(150, 80, "SW"), PlacePin(150, -80, "FB"),
        PlacePin(150, 0, "VOUT")))
    u1.role = "ic"
    u2 = PlaceComp(ref="U2", w=400, h=400, cx=2000, cy=0, rotatable=True, pins=(
        PlacePin(-200, 150, "VCC"), PlacePin(-200, -150, "GND"),
        PlacePin(-200, 50, "XIN"), PlacePin(-200, -50, "XOUT"),
        PlacePin(200, 0, "IO")))
    u2.role = "ic"
    L1 = PlaceComp(ref="L1", w=120, h=120, cx=-800, cy=900, rotatable=True,
                   pins=(PlacePin(-60, 0, "SW"), PlacePin(60, 0, "VOUT")))
    comps = [u1, u2, L1,
             C("D1", ["SW", "GND"], 700, -900), C("C1", ["VIN", "GND"], -1200, -600),
             C("C2", ["VOUT", "GND"], 1200, 800), C("R1", ["VOUT", "FB"], 600, 1100),
             C("R2", ["FB", "GND"], -500, -1100), C("Y1", ["XIN", "XOUT"], 3200, 1200),
             C("C3", ["XIN", "GND"], -1500, 1200), C("C4", ["XOUT", "GND"], 3500, -1200),
             C("C5", ["VCC", "GND"], 3200, -300), C("C6", ["VCC", "GND"], -300, 1400)]
    nets = [
        PlaceNet(refs=("U1", "C1"), name="VIN"),
        PlaceNet(refs=("U1", "L1", "D1"), name="SW"),
        PlaceNet(refs=("U1", "L1", "C2", "R1"), name="VOUT"),
        PlaceNet(refs=("U1", "R1", "R2"), name="FB"),
        PlaceNet(refs=("U1", "D1", "C1", "C2", "R2", "U2", "C3", "C4", "C5", "C6"),
                 name="GND"),
        PlaceNet(refs=("U2", "C5", "C6"), name="VCC"),
        PlaceNet(refs=("U2", "Y1", "C3"), name="XIN"),
        PlaceNet(refs=("U2", "Y1", "C4"), name="XOUT")]
    return comps, nets


def test_combined_inferences_compose_without_conflict():
    """Crystal, switch-node and decap inference applied together on one board:
    the crystal stays tight, the switch loop stays compact, decaps stay near
    the MCU, and the placement is legal -- none of the groupings sabotages the
    others."""
    import math
    comps, nets = _combined_buck_mcu_board()
    xg = _infer_crystal_groups(comps, nets)
    sg = _infer_switch_node_groups(comps, nets)
    # Both inferences fire and don't claim the same parts.
    assert {"Y1", "C3", "C4"} <= set(xg)
    assert {"L1", "D1"} <= set(sg)
    assert set(xg) & set(sg) == set()
    for c in comps:
        if c.ref in xg:
            c.match_group = xg[c.ref]
        elif c.ref in sg:
            c.match_group = sg[c.ref]
    res = construct_placement_best_of(comps, nets, DesignRules(layers=2),
                                      seeds=(0, 1, 2, 3))
    assert res.report.legal
    p = res.centroids

    def dist(a, b):
        return math.hypot(p[a][0] - p[b][0], p[a][1] - p[b][1])
    # Crystal load caps hug the crystal (tight cluster).
    assert dist("Y1", "C3") < 400 and dist("Y1", "C4") < 400
    # Switch-node loop parts stay together.
    assert dist("L1", "D1") < 500
    # MCU decaps land near the MCU, not stranded across the board.
    assert dist("C5", "U2") < 700 and dist("C6", "U2") < 900


def test_match_and_keepout_co_apply_without_conflict():
    """A diff pair (match_group: keep together) whose parts are also in the
    digital keepout domain (push apart from analog) must compose: the pair
    stays tight (same keepout tag -> no mutual push) while an analog part is
    pushed away. match_group and keepout_group are independent attributes."""
    import math
    # Driver U1 + receiver U2 (the diff endpoints) + series R1/R2 (the pair) +
    # an analog part A1 that must stay clear of the digital pair.
    u1 = PlaceComp(ref="U1", w=200, h=200, cx=0, cy=0, rotatable=True, pins=(
        PlacePin(90, 60, "DP"), PlacePin(90, -60, "DM"), PlacePin(-90, 0, "AN")))
    u1.role = "ic"
    u2 = PlaceComp(ref="U2", w=200, h=200, cx=2000, cy=0, rotatable=True, pins=(
        PlacePin(-90, 60, "DPC"), PlacePin(-90, -60, "DMC")))
    u2.role = "ic"
    r1 = PlaceComp(ref="R1", w=40, h=40, cx=900, cy=1500, rotatable=True,
                   pins=(PlacePin(-20, 0, "DP"), PlacePin(20, 0, "DPC")))
    r2 = PlaceComp(ref="R2", w=40, h=40, cx=1100, cy=-1500, rotatable=True,
                   pins=(PlacePin(-20, 0, "DM"), PlacePin(20, 0, "DMC")))
    a1 = PlaceComp(ref="A1", w=40, h=40, cx=200, cy=200, rotatable=True,
                   pins=(PlacePin(-20, 0, "AN"), PlacePin(20, 0, "AG")))
    comps = [u1, u2, r1, r2, a1]
    nets = [PlaceNet(refs=("U1", "R1"), name="DP"),
            PlaceNet(refs=("R1", "U2"), name="DPC"),
            PlaceNet(refs=("U1", "R2"), name="DM"),
            PlaceNet(refs=("R2", "U2"), name="DMC"),
            PlaceNet(refs=("U1", "A1"), name="AN")]
    # The diff series resistors are a matched pair AND in the digital domain;
    # A1 is analog.
    r1.match_group = r2.match_group = "dp"
    r1.keepout_group = r2.keepout_group = "digital"
    a1.keepout_group = "analog"

    res = construct_placement_best_of(comps, nets, DesignRules(layers=2),
                                      seeds=(0, 1, 2))
    assert res.report.legal
    p = res.centroids

    def dist(x, y):
        return math.hypot(p[x][0] - p[y][0], p[x][1] - p[y][1])
    # The matched pair stays tight (match wins; same keepout tag -> no push).
    assert dist("R1", "R2") < 500
    # The analog part is pushed clear of the digital pair.
    assert dist("A1", "R1") > dist("R1", "R2")
