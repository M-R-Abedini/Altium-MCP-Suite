# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Schema tests for DesignPlan, round-trip, validation, cross-check."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from eda_agent.design.plan import (
    BomLine,
    DesignPlan,
    DesignRuleDelta,
    Net,
    Part,
    PartStatus,
    PinRef,
    Sheet,
    Zone,
)


def _minimal_plan() -> DesignPlan:
    """Smallest plan that should pass validation: two parts, one net."""
    return DesignPlan(
        spec="LED with 1k current-limiting resistor on 5V rail",
        summary="A single LED tied to GND through a 1k resistor on the 5V0 rail.",
        sheets=[Sheet(name="main", title="LED test", size="A4")],
        zones=[
            Zone(name="led_zone", sheet="main", origin_mm=(0.0, 0.0), size_mm=(40.0, 40.0))
        ],
        parts=[
            Part(refdes="R1", lib_ref="RES_0805", value="1k", sheet="main", zone="led_zone"),
            Part(refdes="D1", lib_ref="LED_RED", sheet="main", zone="led_zone"),
        ],
        nets=[
            Net(
                name="V5",
                pins=[PinRef(refdes="R1", pin="1")],
                is_power=True,
            ),
            Net(
                name="LED_A",
                pins=[PinRef(refdes="R1", pin="2"), PinRef(refdes="D1", pin="A")],
            ),
            Net(
                name="GND",
                pins=[PinRef(refdes="D1", pin="K")],
                is_ground=True,
            ),
        ],
    )


# A power/ground net of size 1 violates Net.pins min_length=2; we relax that
# only in `_minimal_plan` if needed. Build one that's actually valid:
def _valid_minimal_plan() -> DesignPlan:
    return DesignPlan(
        spec="LED with 1k current-limiting resistor on 5V rail",
        summary="LED tied to GND through 1k on the 5V0 rail.",
        sheets=[Sheet(name="main")],
        parts=[
            Part(refdes="R1", lib_ref="RES_0805", value="1k", sheet="main"),
            Part(refdes="D1", lib_ref="LED_RED", sheet="main"),
        ],
        nets=[
            Net(
                name="V5",
                is_power=True,
                pins=[
                    PinRef(refdes="R1", pin="1"),
                    PinRef(refdes="D1", pin="A"),  # placeholder so pins>=2
                ],
            ),
            Net(
                name="LED_A",
                pins=[
                    PinRef(refdes="R1", pin="2"),
                    PinRef(refdes="D1", pin="A"),
                ],
            ),
        ],
    )


def test_minimal_plan_validates() -> None:
    plan = _valid_minimal_plan()
    assert plan.spec.startswith("LED")
    assert len(plan.parts) == 2
    assert plan.cross_check() == []


def test_round_trip_through_json() -> None:
    plan = _valid_minimal_plan()
    blob = plan.model_dump_json()
    parsed = json.loads(blob)
    assert parsed["spec"] == plan.spec
    rehydrated = DesignPlan.model_validate(parsed)
    assert rehydrated == plan


def test_extra_fields_rejected() -> None:
    payload = json.loads(_valid_minimal_plan().model_dump_json())
    payload["mystery"] = "should_fail"
    with pytest.raises(ValidationError):
        DesignPlan.model_validate(payload)


def test_invalid_refdes_rejected() -> None:
    with pytest.raises(ValidationError):
        Part(refdes="r1", lib_ref="RES_0805", sheet="main")  # lowercase


def test_invalid_net_name_rejected() -> None:
    with pytest.raises(ValidationError):
        Net(
            name="3V3 (rail)",  # spaces & parens not allowed
            pins=[PinRef(refdes="R1", pin="1"), PinRef(refdes="R2", pin="1")],
        )


def test_duplicate_refdes_rejected() -> None:
    with pytest.raises(ValidationError):
        DesignPlan(
            spec="x",
            summary="x",
            sheets=[Sheet(name="main")],
            parts=[
                Part(refdes="R1", lib_ref="RES", sheet="main"),
                Part(refdes="R1", lib_ref="RES", sheet="main"),
            ],
            nets=[
                Net(
                    name="N1",
                    pins=[PinRef(refdes="R1", pin="1"), PinRef(refdes="R1", pin="2")],
                )
            ],
        )


def test_duplicate_net_rejected() -> None:
    with pytest.raises(ValidationError):
        DesignPlan(
            spec="x",
            summary="x",
            sheets=[Sheet(name="main")],
            parts=[Part(refdes="R1", lib_ref="RES", sheet="main")],
            nets=[
                Net(
                    name="N1",
                    pins=[PinRef(refdes="R1", pin="1"), PinRef(refdes="R1", pin="2")],
                ),
                Net(
                    name="N1",
                    pins=[PinRef(refdes="R1", pin="1"), PinRef(refdes="R1", pin="2")],
                ),
            ],
        )


def test_duplicate_pin_in_net_rejected() -> None:
    with pytest.raises(ValidationError):
        Net(
            name="N1",
            pins=[
                PinRef(refdes="R1", pin="1"),
                PinRef(refdes="R1", pin="1"),
            ],
        )


def test_cross_check_catches_unknown_sheet() -> None:
    plan = _valid_minimal_plan()
    plan.parts[0].sheet = "nonexistent"
    problems = plan.cross_check()
    assert any("nonexistent" in p for p in problems)


def test_cross_check_catches_unknown_refdes_in_net() -> None:
    plan = _valid_minimal_plan()
    plan.nets[0].pins[0] = PinRef(refdes="R99", pin="1")
    problems = plan.cross_check()
    assert any("R99" in p for p in problems)


def test_cross_check_catches_dangling_bom_refdes() -> None:
    # a BOM line naming a refdes the plan lacks is a dangling reference the
    # field validators can't see (the BOM is just strings)
    plan = _valid_minimal_plan()
    plan.bom.append(BomLine(refdes_list=["ZZ99"], qty=1))
    problems = plan.cross_check()
    assert any("ZZ99" in p and "BOM" in p for p in problems)


def test_cross_check_passes_valid_bom() -> None:
    plan = _valid_minimal_plan()
    plan.bom.append(BomLine(refdes_list=[plan.parts[0].refdes], qty=1))
    assert plan.cross_check() == []


def test_cross_check_catches_unknown_zone() -> None:
    plan = _valid_minimal_plan()
    plan.parts[0].zone = "ghost"
    problems = plan.cross_check()
    assert any("ghost" in p for p in problems)


def test_cross_check_catches_zone_on_other_sheet() -> None:
    # A part on sheet B may not sit in a zone that lives on sheet A. Name-only
    # membership (the old check) let this slip through.
    plan = DesignPlan(
        spec="two-sheet design", summary="part references a zone on another sheet",
        sheets=[Sheet(name="a"), Sheet(name="b")],
        zones=[Zone(name="za", sheet="a")],
        parts=[
            Part(refdes="R1", lib_ref="RES", sheet="b", zone="za"),
            Part(refdes="R2", lib_ref="RES", sheet="b"),
        ],
        nets=[Net(name="N", pins=[
            PinRef(refdes="R1", pin="1"), PinRef(refdes="R2", pin="1")])],
    )
    problems = plan.cross_check()
    assert any("R1" in p and "za" in p and "b" in p and "a" in p
               for p in problems), problems


def test_duplicate_sheet_name_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate sheet name"):
        DesignPlan(
            spec="x", summary="x",
            sheets=[Sheet(name="main"), Sheet(name="main")],
            parts=[
                Part(refdes="R1", lib_ref="RES", sheet="main"),
                Part(refdes="R2", lib_ref="RES", sheet="main"),
            ],
            nets=[Net(name="N", pins=[
                PinRef(refdes="R1", pin="1"), PinRef(refdes="R2", pin="1")])],
        )


def test_duplicate_zone_name_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate zone name"):
        DesignPlan(
            spec="x", summary="x",
            sheets=[Sheet(name="main")],
            zones=[Zone(name="z", sheet="main"), Zone(name="z", sheet="main")],
            parts=[
                Part(refdes="R1", lib_ref="RES", sheet="main", zone="z"),
                Part(refdes="R2", lib_ref="RES", sheet="main", zone="z"),
            ],
            nets=[Net(name="N", pins=[
                PinRef(refdes="R1", pin="1"), PinRef(refdes="R2", pin="1")])],
        )


def test_part_status_default_is_existing() -> None:
    p = Part(refdes="U1", lib_ref="STM32F103", sheet="main")
    assert p.status == PartStatus.EXISTING


def test_part_status_needs_creation_round_trips() -> None:
    p = Part(
        refdes="U1",
        lib_ref="MAX9611_NEW",
        sheet="main",
        status=PartStatus.NEEDS_CREATION,
    )
    blob = p.model_dump_json()
    rehydrated = Part.model_validate_json(blob)
    assert rehydrated.status == PartStatus.NEEDS_CREATION


def test_design_rule_delta_round_trip() -> None:
    rule = DesignRuleDelta(
        rule_kind="Width",
        scope="POWER",
        parameters={"min_mil": "10", "preferred_mil": "20"},
    )
    blob = rule.model_dump_json()
    assert DesignRuleDelta.model_validate_json(blob) == rule


def test_bom_line_round_trip() -> None:
    bom = BomLine(refdes_list=["R1", "R2"], mpn="ERJ-6ENF1001V", qty=2)
    blob = bom.model_dump_json()
    assert BomLine.model_validate_json(blob) == bom


def test_open_questions_default_empty() -> None:
    plan = _valid_minimal_plan()
    assert plan.open_questions == []


def test_part_manufacturer_and_mpn_default_to_none() -> None:
    """Slice 4 schema extension: manufacturer + mpn are optional, None by default.

    The executor stamps these onto the placed symbol so downstream BOM
    extraction sees a populated column. Both must round-trip cleanly through
    JSON and accept None without ValidationError.
    """
    p = Part(refdes="U1", lib_ref="LM7805", sheet="main")
    assert p.manufacturer is None
    assert p.mpn is None

    explicit = Part(
        refdes="U2",
        lib_ref="LM7805",
        sheet="main",
        manufacturer="Texas Instruments",
        mpn="LM7805CT",
    )
    assert explicit.manufacturer == "Texas Instruments"
    assert explicit.mpn == "LM7805CT"

    rehydrated = Part.model_validate_json(explicit.model_dump_json())
    assert rehydrated.manufacturer == "Texas Instruments"
    assert rehydrated.mpn == "LM7805CT"

    # Round-trip with None values still produces None on the other side.
    bare = Part.model_validate_json(p.model_dump_json())
    assert bare.manufacturer is None
    assert bare.mpn is None
