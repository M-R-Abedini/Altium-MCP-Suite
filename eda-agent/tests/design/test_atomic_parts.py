# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Atomic-parts contract tests.

Covers the new Part fields (manufacturer, mpn, datasheet_url),
Part.validate_atomic, the validator's _check_atomic_parts pass, the
end-to-end wiring of `plan=...` through validate(...), and the
inventory snapshot's ability to surface mpn / manufacturer / datasheet
/ footprint either from top-level row fields or from a nested
parameters dict.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from eda_agent.design.inventory import _component_summary_from_raw
from eda_agent.design.plan import (
    DesignPlan,
    Net,
    Part,
    PartStatus,
    PinRef,
    Sheet,
)
from eda_agent.design.validator import _check_atomic_parts, validate


# ---------------------------------------------------------------------------
# Part fields + validate_atomic
# ---------------------------------------------------------------------------


def test_part_accepts_new_atomic_fields() -> None:
    p = Part(
        refdes="R1",
        lib_ref="RES_0805",
        value="1k",
        footprint="0805",
        manufacturer="Yageo",
        mpn="RC0805FR-071KL",
        datasheet_url="https://example.com/RC0805.pdf",
    )
    assert p.manufacturer == "Yageo"
    assert p.mpn == "RC0805FR-071KL"
    assert p.datasheet_url == "https://example.com/RC0805.pdf"


def test_part_atomic_fields_optional_for_backwards_compat() -> None:
    # An older plan that doesn't carry the new fields must still parse.
    p = Part(refdes="R1", lib_ref="RES_0805")
    assert p.manufacturer is None
    assert p.mpn is None
    assert p.datasheet_url is None


def test_part_atomic_fields_round_trip_through_json() -> None:
    p = Part(
        refdes="U1",
        lib_ref="LM358",
        footprint="SOIC-8",
        manufacturer="Texas Instruments",
        mpn="LM358DR",
        datasheet_url="https://www.ti.com/lit/ds/symlink/lm358.pdf",
    )
    blob = p.model_dump_json()
    parsed = Part.model_validate_json(blob)
    assert parsed == p


def test_part_validate_atomic_clean_when_all_fields_set() -> None:
    p = Part(
        refdes="U1",
        lib_ref="LM358",
        footprint="SOIC-8",
        mpn="LM358DR",
        datasheet_url="https://example.com/lm358.pdf",
    )
    assert p.validate_atomic() == []


def test_part_validate_atomic_flags_missing_mpn() -> None:
    p = Part(
        refdes="R1",
        lib_ref="RES_0805",
        footprint="0805",
        datasheet_url="https://example.com/r.pdf",
    )
    issues = p.validate_atomic()
    assert len(issues) == 1
    assert "R1" in issues[0] and "mpn" in issues[0]


def test_part_validate_atomic_flags_missing_footprint_and_datasheet() -> None:
    p = Part(refdes="R1", lib_ref="RES_0805", mpn="X")
    issues = p.validate_atomic()
    assert len(issues) == 2
    joined = " | ".join(issues)
    assert "footprint" in joined
    assert "datasheet_url" in joined


def test_part_validate_atomic_returns_three_when_all_missing() -> None:
    # The user-facing assertion from the task spec.
    p = Part(refdes="R1", lib_ref="RES")
    issues = p.validate_atomic()
    assert issues  # non-empty
    assert len(issues) == 3


def test_part_validate_atomic_ignores_needs_creation() -> None:
    # needs_creation parts are escalated before they reach a BOM, so the
    # atomic-parts contract is intentionally relaxed for them.
    p = Part(
        refdes="U99",
        lib_ref="NEW_PART",
        status=PartStatus.NEEDS_CREATION,
    )
    assert p.validate_atomic() == []


def test_part_validate_atomic_treats_blank_strings_as_missing() -> None:
    p = Part(
        refdes="R1",
        lib_ref="RES_0805",
        footprint="  ",
        mpn="",
        datasheet_url="\t",
    )
    issues = p.validate_atomic()
    assert len(issues) == 3


# ---------------------------------------------------------------------------
# Validator wiring
# ---------------------------------------------------------------------------


def _plan_with_parts(parts: list[Part]) -> DesignPlan:
    """Build the smallest DesignPlan that contains the given parts."""
    # The schema requires at least one Net with 2 pins; we synthesize a
    # trivial one between the first two parts. Callers must supply >=2
    # parts.
    assert len(parts) >= 2, "test helper needs at least two parts"
    return DesignPlan(
        spec="atomic-parts test",
        summary="atomic-parts test plan",
        sheets=[Sheet(name="main")],
        parts=parts,
        nets=[
            Net(
                name="N1",
                pins=[
                    PinRef(refdes=parts[0].refdes, pin="1"),
                    PinRef(refdes=parts[1].refdes, pin="1"),
                ],
            )
        ],
    )


def test_check_atomic_parts_emits_one_warning_per_bad_part() -> None:
    plan = _plan_with_parts(
        [
            Part(refdes="R1", lib_ref="RES_0805"),  # missing all three
            Part(
                refdes="R2",
                lib_ref="RES_0805",
                footprint="0805",
                mpn="RC0805",
                datasheet_url="https://example.com/r.pdf",
            ),  # clean
        ]
    )
    issues = _check_atomic_parts(plan)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.category == "atomic_parts"
    assert issue.severity == "warning"
    assert issue.refdes == "R1"
    assert "mpn" in issue.text
    assert "footprint" in issue.text
    assert "datasheet_url" in issue.text
    assert "incomplete BOM" in issue.text


def test_check_atomic_parts_returns_empty_for_none_plan() -> None:
    assert _check_atomic_parts(None) == []


class _FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def send_command(self, command: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((command, params or {}))
        if command == "project.get_messages":
            return {"messages": [], "count": 0}
        if command == "generic.get_unconnected_pins":
            return {"unconnected_pins": [], "count": 0}
        return {"ok": True}


def test_validate_runs_atomic_parts_check_when_plan_supplied() -> None:
    bridge = _FakeBridge()
    plan = _plan_with_parts(
        [
            Part(refdes="R1", lib_ref="RES_0805"),  # missing all three
            Part(
                refdes="R2",
                lib_ref="RES_0805",
                footprint="0805",
                mpn="RC0805",
                datasheet_url="https://example.com/r.pdf",
            ),
        ]
    )
    report = validate(bridge=bridge, plan=plan)
    atomic_warnings = [w for w in report.warnings if w.category == "atomic_parts"]
    assert len(atomic_warnings) == 1
    assert atomic_warnings[0].refdes == "R1"
    # Warnings don't fail the overall report.
    assert report.passed is True


def test_validate_without_plan_skips_atomic_parts_check() -> None:
    bridge = _FakeBridge()
    report = validate(bridge=bridge)
    assert not any(w.category == "atomic_parts" for w in report.warnings)


def test_validate_serializes_atomic_warning_to_dict() -> None:
    bridge = _FakeBridge()
    plan = _plan_with_parts(
        [
            Part(refdes="C1", lib_ref="CAP"),
            Part(
                refdes="C2",
                lib_ref="CAP",
                footprint="0402",
                mpn="GRM",
                datasheet_url="https://example.com/cap.pdf",
            ),
        ]
    )
    report = validate(bridge=bridge, plan=plan)
    blob = report.to_dict()
    atomic = [w for w in blob["warnings"] if w["category"] == "atomic_parts"]
    assert atomic and atomic[0]["refdes"] == "C1"


# ---------------------------------------------------------------------------
# Inventory mapping
# ---------------------------------------------------------------------------


def test_component_summary_reads_top_level_atomic_fields() -> None:
    raw = {
        "name": "LM358",
        "description": "Dual op-amp",
        "mpn": "LM358DR",
        "manufacturer": "Texas Instruments",
        "datasheet": "https://www.ti.com/lit/ds/symlink/lm358.pdf",
        "footprint": "SOIC-8",
    }
    summary = _component_summary_from_raw(raw)
    assert summary.lib_ref == "LM358"
    assert summary.mpn == "LM358DR"
    assert summary.manufacturer == "Texas Instruments"
    assert summary.datasheet == "https://www.ti.com/lit/ds/symlink/lm358.pdf"
    assert summary.footprint == "SOIC-8"


def test_component_summary_reads_atomic_fields_from_parameters_dict() -> None:
    # This is the live-Altium shape: Lib_GetComponents emits a parameters
    # dict; we should still recover MPN/Manufacturer/Datasheet/Footprint
    # via the canonical Altium parameter names.
    raw = {
        "name": "LM358",
        "description": "Dual op-amp",
        "parameters": {
            "Manufacturer Part Number": "LM358DR",
            "Manufacturer": "Texas Instruments",
            "Datasheet": "https://www.ti.com/lit/ds/symlink/lm358.pdf",
            "Footprint": "SOIC-8",
            "Value": "LM358",
        },
    }
    summary = _component_summary_from_raw(raw)
    assert summary.mpn == "LM358DR"
    assert summary.manufacturer == "Texas Instruments"
    assert summary.datasheet == "https://www.ti.com/lit/ds/symlink/lm358.pdf"
    assert summary.footprint == "SOIC-8"
    # The parameters dict should round-trip too.
    assert summary.parameters["Value"] == "LM358"


def test_component_summary_parameter_lookup_is_case_insensitive() -> None:
    raw = {
        "name": "X",
        "parameters": {
            "MPN": "ABC",
            "MFR": "ACME",
            "DATASHEETURL": "https://acme.example/x.pdf",
        },
    }
    summary = _component_summary_from_raw(raw)
    assert summary.mpn == "ABC"
    assert summary.manufacturer == "ACME"
    assert summary.datasheet == "https://acme.example/x.pdf"


def test_component_summary_atomic_fields_default_to_none() -> None:
    raw = {"name": "PASSIVE_NO_METADATA"}
    summary = _component_summary_from_raw(raw)
    assert summary.mpn is None
    assert summary.manufacturer is None
    assert summary.datasheet is None
    assert summary.footprint is None


def test_component_summary_top_level_wins_over_parameters() -> None:
    raw = {
        "name": "X",
        "mpn": "EXPLICIT",
        "parameters": {"MPN": "FROM_PARAMS"},
    }
    summary = _component_summary_from_raw(raw)
    assert summary.mpn == "EXPLICIT"


# ---------------------------------------------------------------------------
# Discipline doc surfaces the contract
# ---------------------------------------------------------------------------


def test_discipline_doc_mentions_atomic_parts() -> None:
    from eda_agent.design.discipline import get_discipline

    doc = get_discipline()
    lowered = doc.lower()
    # The discipline doc should call out the contract explicitly so the
    # planner can't claim it didn't know.
    assert "atomic-parts" in lowered or "atomic parts" in lowered
    assert "mpn" in lowered
    assert "datasheet_url" in lowered
