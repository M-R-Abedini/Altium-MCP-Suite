# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Min-cut netlist partitioning (Kernighan-Lin swap refinement)."""

from __future__ import annotations

import pytest

from eda_agent.design.partition import partition_netlist


def _two_block_netlist():
    """Two tight 4-part chains joined by a single bridge net, plus a GND-like
    rail touching everything (high fan-out)."""
    refdes = [f"A{i}" for i in range(1, 5)] + [f"B{i}" for i in range(1, 5)]
    nets = [["A1", "A2"], ["A2", "A3"], ["A3", "A4"],
            ["B1", "B2"], ["B2", "B3"], ["B3", "B4"],
            ["A4", "B1"],          # the single inter-block bridge
            list(refdes)]          # rail (fan-out 8)
    return refdes, nets


def test_two_clear_blocks_separate_cleanly():
    refdes, nets = _two_block_netlist()
    r = partition_netlist(refdes, nets, n_groups=2, max_fanout=6)
    g = r["group_of"]
    # Each block lands wholly in one group, and the two blocks differ.
    a_groups = {g[f"A{i}"] for i in range(1, 5)}
    b_groups = {g[f"B{i}"] for i in range(1, 5)}
    assert len(a_groups) == 1 and len(b_groups) == 1
    assert a_groups != b_groups
    assert r["group_sizes"] == [4, 4]           # balanced


def test_partition_is_deterministic():
    refdes, nets = _two_block_netlist()
    assert (partition_netlist(refdes, nets, 2, 6)
            == partition_netlist(refdes, nets, 2, 6))


def test_recursive_bisection_to_four_groups():
    refdes, nets = _two_block_netlist()
    r = partition_netlist(refdes, nets, n_groups=4, max_fanout=6)
    assert r["n_groups"] == 4
    assert sum(r["group_sizes"]) == 8
    assert all(s >= 1 for s in r["group_sizes"])


def test_rail_excluded_lowers_cut_vs_included():
    """Counting the high-fan-out rail in the GRAPH would resist splitting;
    excluding it (max_fanout) lets the signal structure drive a low-cut
    split. With the rail included in the graph the swap gains vanish."""
    refdes, nets = _two_block_netlist()
    excl = partition_netlist(refdes, nets, 2, max_fanout=6)
    incl = partition_netlist(refdes, nets, 2, max_fanout=0)  # 0 => keep all
    # Excluding the rail yields the clean block separation (2 cut nets: the
    # bridge + the rail itself); including it can only be >= that.
    assert excl["cut_nets"] <= incl["cut_nets"]


def test_kl_matches_brute_force_optimal_on_structured_nets():
    """On structured netlists the KL bisection should reach the brute-force
    optimal balanced cut -- the prefix-commit escapes the local optima a
    plain improving-swap hill-climber stalls in."""
    import itertools

    def _optimal_cut(refdes, nets):
        n = len(refdes)
        present = set(refdes)
        best = 10 ** 9
        for combo in itertools.combinations(refdes, n // 2):
            a = set(combo)
            cut = sum(1 for net in nets
                      if len({(r in a) for r in net if r in present}) > 1)
            best = min(best, cut)
        return best

    # Two 4-node rings joined by two bridges -- a classic KL escape case.
    refdes = ["L1", "L2", "L3", "L4", "R1", "R2", "R3", "R4"]
    nets = [["L1", "L2"], ["L2", "L3"], ["L3", "L4"], ["L4", "L1"],
            ["R1", "R2"], ["R2", "R3"], ["R3", "R4"], ["R4", "R1"],
            ["L1", "R1"], ["L2", "R2"]]
    r = partition_netlist(refdes, nets, 2, max_fanout=0)
    assert r["cut_nets"] == _optimal_cut(refdes, nets)


def test_single_group_is_trivial():
    refdes, nets = _two_block_netlist()
    r = partition_netlist(refdes, nets, n_groups=1)
    assert r["n_groups"] == 1
    assert set(r["group_of"].values()) == {0}
    assert r["cut_nets"] == 0


def test_tool_suggests_partition(monkeypatch):
    from eda_agent.tools import design as design_module

    captured = {}

    class DummyMcp:
        def tool(self):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    design_module.register_design_tools(DummyMcp())
    tool = captured["design_suggest_partition"]

    refdes, nets = _two_block_netlist()
    plan = {
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "zones": [{"name": "z", "sheet": "main"}],
        "parts": [{"refdes": r, "lib_ref": "RES", "status": "existing",
                   "sheet": "main", "zone": "z"} for r in refdes],
        "nets": [{"name": f"N{i}",
                  "pins": [{"refdes": m, "pin": "1"} for m in net]}
                 for i, net in enumerate(nets) if len(net) >= 2],
    }
    import asyncio
    out = asyncio.run(tool(plan, n_groups=2, max_fanout=6))
    assert out["ok"] is True
    assert out["n_groups"] == 2
    assert out["group_sizes"] == [4, 4]
    assert "cross the boundary" in out["summary"]
    # The two blocks are in different groups.
    flat = {r: g for g, members in out["groups"].items() for r in members}
    assert flat["A1"] != flat["B1"]
    # boundary_nets names the cut nets: the A4-B1 bridge (and the rail). It is
    # a subset of the plan's net names, and its count matches cut_nets.
    assert isinstance(out["boundary_nets"], list)
    assert len(out["boundary_nets"]) == out["cut_nets"]
    # The bridge net (A4<->B1) crosses; an internal net (A1<->A2) does not.
    bridge_name = next(
        net_name for net_name, members in
        ((f"N{i}", net) for i, net in enumerate(nets) if len(net) >= 2)
        if set(members) == {"A4", "B1"})
    assert bridge_name in out["boundary_nets"]
