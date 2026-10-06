# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Benchmark suite: three golden plans through the full offline chain.

The golden plans (tests/benchmarks/plans/*.json) are fictional but
structurally realistic designs; ``run_benchmark`` drives validation +
ERC-lite, the schematic pipeline, the constructive PCB placer and the
Manhattan router on synthetic geometry. The asserted floors are loose
regression tripwires, not quality targets -- a failure means a code
change regressed a stage, not that a number drifted by a point.

Wall time: blinker ~1 s, buck ~3 s, mcu ~35 s (placement + routing at
45 parts). Each benchmark runs once per session via cached fixtures.
"""

from __future__ import annotations

import copy
import json
import math
from functools import lru_cache
from pathlib import Path

import pytest

from eda_agent.design.benchmark import (
    SyntheticSymbolExtractor,
    run_benchmark,
    synth_footprint,
)
from eda_agent.design.plan import DesignPlan
from eda_agent.design.plan_erc import check_plan_erc

PLANS_DIR = Path(__file__).parent / "plans"
PLAN_NAMES = ("blinker555", "buck", "mcu")


def _load(name: str) -> dict:
    return json.loads((PLANS_DIR / f"{name}.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _result(name: str) -> dict:
    return run_benchmark(_load(name))


# ---------------------------------------------------------------------------
# Fixture sanity: the golden plans themselves must stay valid
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", PLAN_NAMES)
def test_golden_plan_parses_and_cross_checks(name):
    plan = DesignPlan.model_validate(_load(name))
    assert plan.cross_check() == []
    assert check_plan_erc(plan).passed


def test_golden_plan_sizes():
    """Part counts anchor the three size classes the suite covers."""
    assert len(DesignPlan.model_validate(_load("blinker555")).parts) == 8
    assert len(DesignPlan.model_validate(_load("buck")).parts) == 15
    mcu = DesignPlan.model_validate(_load("mcu"))
    assert 40 <= len(mcu.parts) <= 50
    assert len(mcu.zones) == 4
    # The 8-bit data bus and the USB diff pair are present by name/role.
    net_names = {n.name for n in mcu.nets}
    assert {f"D{i}" for i in range(8)} <= net_names
    diff = {n.name for n in mcu.nets if n.role == "differential"}
    assert {"USB_DP", "USB_DM"} <= diff


# ---------------------------------------------------------------------------
# Plan validation + ERC-lite
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", PLAN_NAMES)
def test_plan_valid(name):
    res = _result(name)
    assert res["ok"], res.get("reason")
    assert res["plan_valid"], res["plan_problems"]


# ---------------------------------------------------------------------------
# Schematic stage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", PLAN_NAMES)
def test_schematic_ok_with_zero_strict_shorts(name):
    sch = _result(name)["schematic"]
    assert sch["ok"], sch["failures"]
    assert sch["shorts"] == 0
    assert sch["score"] >= 0.0 and math.isfinite(sch["score"])


@pytest.mark.parametrize("name", PLAN_NAMES)
def test_schematic_places_every_part(name):
    plan = DesignPlan.model_validate(_load(name))
    assert _result(name)["schematic"]["placed"] == len(plan.parts)


def test_schematic_power_nets_become_ports():
    """Power/ground rails render as port glyphs, not bare labels."""
    sch = _result("blinker555")["schematic"]
    assert sch["ports"] > 0
    assert sch["wires"] > 0


# ---------------------------------------------------------------------------
# Hierarchy stage
# ---------------------------------------------------------------------------


def test_hierarchy_small_plans_stay_single_sheet():
    assert _result("blinker555")["hierarchy"]["split"] is False
    assert _result("buck")["hierarchy"]["split"] is False


def test_hierarchy_mcu_splits_into_zone_sheets():
    hier = _result("mcu")["hierarchy"]
    assert hier["ok"] and hier["split"]
    # Zones are atomic, so 4 zones -> 4 child sheets; cut nets exist.
    assert hier["sheets"] == 4
    assert hier["cut_nets"] > 0


# ---------------------------------------------------------------------------
# PCB placement stage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", PLAN_NAMES)
def test_placement_returns_finite_score(name):
    pcb = _result(name)["pcb_placement"]
    assert pcb["ok"]
    assert math.isfinite(pcb["weighted_total"])
    assert math.isfinite(pcb["hpwl"]) and pcb["hpwl"] > 0
    assert pcb["legal"]
    assert pcb["board_mils"]["w"] > 0 and pcb["board_mils"]["h"] > 0


# ---------------------------------------------------------------------------
# Routing stage (loose floors -- regression tripwires, not targets)
# ---------------------------------------------------------------------------


def test_blinker_routes_completely():
    routing = _result("blinker555")["routing"]
    assert routing["ok"]
    assert routing["completion_pct"] == 100.0, routing["failed_nets"]
    assert routing["drc_self_check"]


def test_buck_routes_at_least_80pct():
    routing = _result("buck")["routing"]
    assert routing["ok"]
    assert routing["completion_pct"] >= 80.0, routing["failed_nets"]
    assert routing["drc_self_check"]


def test_mcu_routing_runs_clean():
    """No completion target at 45 parts on 2 layers, but the router must
    finish, beat a loose floor, and emit zero self-check violations."""
    routing = _result("mcu")["routing"]
    assert routing["ok"]
    assert routing["completion_pct"] >= 50.0, routing["failed_nets"]
    assert routing["drc_self_check"]
    assert routing["track_count"] > 0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_benchmark_is_deterministic():
    plan = _load("buck")
    assert run_benchmark(plan) == run_benchmark(copy.deepcopy(plan))


# ---------------------------------------------------------------------------
# Degenerate inputs
# ---------------------------------------------------------------------------


def test_run_benchmark_rejects_invalid_plan_dict():
    res = run_benchmark({"spec": "x"})  # misses required fields
    assert res["ok"] is False
    assert "validate" in res["reason"]


def test_run_benchmark_rejects_non_plan_input():
    res = run_benchmark("not a plan")  # type: ignore[arg-type]
    assert res["ok"] is False


def test_run_benchmark_surfaces_cross_check_problems():
    payload = _load("blinker555")
    bad = copy.deepcopy(payload)
    bad["parts"][0]["zone"] = "no_such_zone"
    res = run_benchmark(bad)
    assert res["ok"] is True  # plan parses; the problem is semantic
    assert res["plan_valid"] is False
    assert any("no_such_zone" in p for p in res["plan_problems"])


# ---------------------------------------------------------------------------
# Synthesis helpers
# ---------------------------------------------------------------------------


def _part(refdes: str, footprint: str):
    plan = DesignPlan.model_validate(_load("blinker555"))
    p = plan.parts[0].model_copy(deep=True)
    p.refdes = refdes
    p.footprint = footprint
    return p


def test_synth_footprint_chip_two_pads():
    fp = synth_footprint(_part("R1", "RES_0402"), ["1", "2"])
    assert not fp["through_hole"]
    assert [p["pin"] for p in fp["pads"]] == ["1", "2"]
    # Symmetric pads on the x axis.
    assert fp["pads"][0]["lx"] == -fp["pads"][1]["lx"]
    assert fp["pads"][0]["ly"] == fp["pads"][1]["ly"] == 0


def test_synth_footprint_connector_is_through_hole_row():
    fp = synth_footprint(_part("J1", "HDR_1X4"), ["1", "2", "3", "4"])
    assert fp["through_hole"]
    assert len(fp["pads"]) == 4
    assert len({p["lx"] for p in fp["pads"]}) == 4  # one row, distinct x
    assert all(p["ly"] == 0 for p in fp["pads"])


def test_synth_footprint_quad_has_unique_pad_positions():
    pins = [str(i) for i in range(1, 33)]
    fp = synth_footprint(_part("U1", "TQFP32"), pins)
    assert len(fp["pads"]) == 32
    pos = {(p["lx"], p["ly"]) for p in fp["pads"]}
    assert len(pos) == 32
    # Quad: square body, pads on all four sides.
    assert fp["w"] == fp["h"]
    assert {p["pin"] for p in fp["pads"]} == set(pins)


def test_synth_footprint_dual_row_unique_positions():
    pins = [str(i) for i in range(1, 21)]
    fp = synth_footprint(_part("U2", "SOIC20"), pins)
    assert len({(p["lx"], p["ly"]) for p in fp["pads"]}) == 20
    assert len({p["lx"] for p in fp["pads"]}) == 2  # two columns


def test_synthetic_symbols_cover_every_plan_part():
    plan = DesignPlan.model_validate(_load("mcu"))
    extractor = SyntheticSymbolExtractor(plan)
    for part in plan.parts:
        sym = extractor.extract_one(part.lib_path, part.lib_ref)
        assert sym is not None, part.refdes
        # Every net-referenced pin must resolve on the symbol.
        for net in plan.nets:
            for pr in net.pins:
                if pr.refdes == part.refdes:
                    assert sym.pin_by_id(pr.pin) is not None, (
                        part.refdes, pr.pin)


def test_synthetic_symbol_pins_have_distinct_endpoints():
    plan = DesignPlan.model_validate(_load("mcu"))
    extractor = SyntheticSymbolExtractor(plan)
    sym = extractor.extract_one(plan.parts[0].lib_path, "MCU32")
    assert sym is not None
    pts = {(p.x, p.y) for p in sym.pins}
    assert len(pts) == len(sym.pins)
