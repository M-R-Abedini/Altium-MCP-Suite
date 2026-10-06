# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Inventory schema + JSON round-trip + raw-row mapping."""

from __future__ import annotations

import json
from pathlib import Path

from eda_agent.design.inventory import (
    ComponentSummary,
    LibraryInventory,
    LibrarySummary,
    _component_summary_from_raw,
)


def test_round_trip_through_json(tmp_path: Path) -> None:
    inv = LibraryInventory(
        libraries=[
            LibrarySummary(
                path=r"C:\Libs\PassivesLib.SchLib",
                components=[
                    ComponentSummary(
                        lib_ref="RES_0805",
                        designator_prefix="R",
                        pin_count=2,
                        description="Generic 0805 resistor",
                    ),
                    ComponentSummary(
                        lib_ref="RES_0603",
                        designator_prefix="R",
                        pin_count=2,
                    ),
                ],
            ),
        ]
    )
    target = tmp_path / "inv.json"
    inv.to_json_file(target)

    rehydrated = LibraryInventory.from_json_file(target)
    assert rehydrated == inv
    assert rehydrated.total_components() == 2


def test_find_by_lib_ref() -> None:
    inv = LibraryInventory(
        libraries=[
            LibrarySummary(
                path="x",
                components=[ComponentSummary(lib_ref="LM7805")],
            )
        ]
    )
    found = inv.find("LM7805")
    assert found is not None
    path, comp = found
    assert path == "x"
    assert comp.lib_ref == "LM7805"
    assert inv.find("MISSING") is None


def test_raw_row_mapping_handles_aliases() -> None:
    raw = {
        "Name": "STM32F103C8T6",
        "Designator": "U",
        "Description": "ARM Cortex-M3 MCU",
        "PinCount": "48",
        "DefaultFootprint": "LQFP-48",
    }
    summary = _component_summary_from_raw(raw)
    assert summary.lib_ref == "STM32F103C8T6"
    assert summary.designator_prefix == "U"
    assert summary.pin_count == 48
    assert summary.footprint == "LQFP-48"


def test_raw_row_mapping_pin_count_from_list() -> None:
    raw = {"name": "LED", "pins": [{"n": "A"}, {"n": "K"}]}
    summary = _component_summary_from_raw(raw)
    assert summary.pin_count == 2


def test_extra_top_level_field_rejected(tmp_path: Path) -> None:
    payload = {"libraries": [], "rogue_field": True}
    target = tmp_path / "bad.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    try:
        LibraryInventory.from_json_file(target)
    except Exception:
        return
    raise AssertionError("LibraryInventory should reject extra top-level fields")
