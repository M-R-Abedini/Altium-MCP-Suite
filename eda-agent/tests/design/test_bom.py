# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for BOM derivation from a DesignPlan."""

from __future__ import annotations

from eda_agent.design.bom import bom_summary, generate_bom
from eda_agent.design.plan import DesignPlan


def _plan(parts: list[dict]) -> DesignPlan:
    # one throwaway 2-pin net so the schema is satisfied; BOM ignores nets
    refs = [p["refdes"] for p in parts]
    return DesignPlan.model_validate({
        "spec": "t", "summary": "bom test", "sheets": [{"name": "main"}],
        "parts": parts,
        "nets": [{"name": "N1", "pins": [
            {"refdes": refs[0], "pin": "1"},
            {"refdes": refs[-1], "pin": "2"}]}],
    })


def test_identical_passives_consolidate():
    plan = _plan([
        {"refdes": "C1", "lib_ref": "CAP", "value": "100nF", "footprint": "C0402"},
        {"refdes": "C2", "lib_ref": "CAP", "value": "100nF", "footprint": "C0402"},
        {"refdes": "C3", "lib_ref": "CAP", "value": "100nF", "footprint": "C0402"},
    ])
    bom = generate_bom(plan)
    assert len(bom) == 1
    assert bom[0].qty == 3
    assert bom[0].refdes_list == ["C1", "C2", "C3"]
    assert bom[0].description == "100nF"


def test_different_values_are_separate_lines():
    plan = _plan([
        {"refdes": "R1", "lib_ref": "RES", "value": "10k", "footprint": "R0402"},
        {"refdes": "R2", "lib_ref": "RES", "value": "1k", "footprint": "R0402"},
    ])
    bom = generate_bom(plan)
    assert len(bom) == 2
    assert {bl.description for bl in bom} == {"10k", "1k"}
    assert all(bl.qty == 1 for bl in bom)


def test_mpn_consolidates_separately_from_generic():
    # same value, but one has an mpn -> two lines (one is a specific part)
    plan = _plan([
        {"refdes": "C1", "lib_ref": "CAP", "value": "100nF"},
        {"refdes": "C2", "lib_ref": "CAP", "value": "100nF",
         "mpn": "GRM155R71C104KA88D", "manufacturer": "Murata"},
        {"refdes": "C3", "lib_ref": "CAP", "value": "100nF",
         "mpn": "GRM155R71C104KA88D", "manufacturer": "Murata"},
    ])
    bom = generate_bom(plan)
    assert len(bom) == 2
    by_mpn = {bl.mpn: bl for bl in bom}
    assert by_mpn["GRM155R71C104KA88D"].qty == 2
    assert by_mpn["GRM155R71C104KA88D"].manufacturer == "Murata"
    assert by_mpn[None].qty == 1


def test_natural_sort_within_line_and_across():
    plan = _plan([
        {"refdes": "R10", "lib_ref": "RES", "value": "10k"},
        {"refdes": "R2", "lib_ref": "RES", "value": "10k"},
        {"refdes": "C1", "lib_ref": "CAP", "value": "1uF"},
    ])
    bom = generate_bom(plan)
    # C line sorts before R line; R2 before R10 within the resistor line
    assert bom[0].refdes_list == ["C1"]
    assert bom[1].refdes_list == ["R2", "R10"]


def test_distinct_mpns_same_manufacturer_split():
    plan = _plan([
        {"refdes": "U1", "lib_ref": "IC", "mpn": "PART-A", "manufacturer": "TI"},
        {"refdes": "U2", "lib_ref": "IC", "mpn": "PART-B", "manufacturer": "TI"},
    ])
    bom = generate_bom(plan)
    assert len(bom) == 2


def test_footprint_distinguishes_generic_lines():
    # same value, different footprint -> two lines (different physical part)
    plan = _plan([
        {"refdes": "R1", "lib_ref": "RES", "value": "10k", "footprint": "R0402"},
        {"refdes": "R2", "lib_ref": "RES", "value": "10k", "footprint": "R0603"},
    ])
    assert len(generate_bom(plan)) == 2


def test_summary_flags_lines_without_mpn():
    plan = _plan([
        {"refdes": "C1", "lib_ref": "CAP", "value": "100nF"},
        {"refdes": "U1", "lib_ref": "MCU", "mpn": "STM32", "manufacturer": "ST"},
    ])
    bom = generate_bom(plan)
    s = bom_summary(bom)
    assert s["line_count"] == 2
    assert s["total_parts"] == 2
    assert s["lines_without_mpn"] == ["C1"]    # the cap still needs an mpn


def test_does_not_mutate_plan():
    plan = _plan([{"refdes": "C1", "lib_ref": "CAP", "value": "100nF"}])
    before = plan.bom
    generate_bom(plan)
    assert plan.bom == before == []


def test_generated_bom_round_trips_onto_plan():
    plan = _plan([
        {"refdes": "C1", "lib_ref": "CAP", "value": "100nF"},
        {"refdes": "C2", "lib_ref": "CAP", "value": "100nF"},
    ])
    bom = generate_bom(plan)
    updated = plan.model_copy(update={"bom": bom})
    # the plan with its derived BOM is still schema-valid
    reparsed = DesignPlan.model_validate(updated.model_dump())
    assert reparsed.cross_check() == []
    assert reparsed.bom[0].qty == 2
