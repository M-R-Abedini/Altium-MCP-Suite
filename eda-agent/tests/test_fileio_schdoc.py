# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the headless .SchDoc reader (roadmap V1).

Runs against the real buck-converter fixture (tests/integration/fixtures/
main.SchDoc), no Altium, no license, so this parser is a CI-gated,
license-free way to read an Altium schematic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eda_agent.fileio.altium_sch import (
    RECORD_COMPONENT,
    read_schdoc_records,
    read_schematic_components,
    read_schematic_nets,
    read_schematic_pins,
    read_schematic_wires,
)

FIXTURE = Path(__file__).resolve().parent / "integration" / "fixtures" / "main.SchDoc"


@pytest.fixture(scope="module")
def components():
    if not FIXTURE.exists():
        pytest.skip("needs local-only binary fixture main.SchDoc")
    return read_schematic_components(FIXTURE)


def test_records_parse(fixture=FIXTURE):
    recs = read_schdoc_records(fixture)
    assert recs, "no records parsed"
    assert recs[0].get("HEADER", "").startswith("Protel for Windows")
    # A mix of record types is present (components, designators, params).
    kinds = {r.get("RECORD") for r in recs}
    assert {"1", "34"} <= kinds


def test_extracts_all_components(components):
    assert len(components) == 14
    assert all(c["designator"] for c in components), "a component lost its designator"


def test_designator_to_part_mapping_is_correct(components):
    by_desig = {c["designator"]: c for c in components}
    # Verified against the actual board: U1 is the buck controller, D1 the
    # catch diode, R1 a resistor, J1 a connector, NOT swapped.
    assert by_desig["U1"]["lib_reference"] == "TPS54331D"
    assert by_desig["D1"]["lib_reference"] == "SS14"
    assert by_desig["R1"]["lib_reference"].startswith("RES ")
    assert by_desig["J1"]["lib_reference"].startswith("Phoenix Connector")


def test_component_fields_present(components):
    u1 = next(c for c in components if c["designator"] == "U1")
    assert u1["unique_id"]
    assert u1["x"] is not None and u1["y"] is not None


def test_component_parameters_extracted(components):
    by = {c["designator"]: c for c in components}
    # MPN + manufacturer pulled from RECORD=41 parameters, joined by owner.
    assert by["U1"]["mpn"] == "TPS54331DR"
    assert by["U1"]["manufacturer"] == "Texas Instruments"
    assert by["R1"]["mpn"] == "RC0603FR-0710KL"
    assert by["R1"]["value"] == "10K"
    assert by["D1"]["manufacturer"] == "Onsemi"
    # Every component carries its raw parameter dict too.
    assert isinstance(by["U1"]["parameters"], dict)


def test_altium_value_reference_resolved(components):
    # J1's Comment is the Altium "=Partnumber" reference; it must resolve to
    # the actual part number, not the literal "=Partnumber".
    j1 = next(c for c in components if c["designator"] == "J1")
    assert j1["value"] == "1985195"
    assert not j1["value"].startswith("=")


def test_every_component_has_an_mpn(components):
    # This fixture is a fully-specified board: a headless BOM check would
    # expect every part to carry a manufacturer part number.
    missing = [c["designator"] for c in components if not c["mpn"]]
    assert not missing, f"components missing MPN: {missing}"


def test_extracts_declared_nets():
    nets = read_schematic_nets(FIXTURE)
    by = {n["name"]: n for n in nets}
    # Power ports declare the rails; net labels declare signal nets.
    assert by["GND"]["power_count"] == 3 and by["GND"]["label_count"] == 0
    assert by["VIN"]["power_count"] == 2
    assert by["SW"]["label_count"] == 1 and by["SW"]["power_count"] == 0
    assert by["SW"]["total"] == 1
    # A known buck rail set is present.
    assert {"GND", "VIN", "VOUT", "SW", "FB"} <= set(by)


def test_extracts_document_info():
    from eda_agent.fileio.altium_sch import read_schematic_document_info
    info = read_schematic_document_info(FIXTURE)
    # Title block is unfilled in this fixture (Altium "*" -> empty).
    assert info["title"] == ""
    assert info["revision"] == ""
    # Sheet geometry is real data.
    assert info["sheet"]["custom_x"] == 1500
    assert info["sheet"]["custom_y"] == 950
    assert info["sheet"]["title_block_on"] is True


def test_extracts_wire_segments():
    wires = read_schematic_wires(FIXTURE)
    assert len(wires) == 71  # matches the RECORD=27 count in this sheet
    for w in wires:
        assert all(isinstance(w[k], int) for k in ("x1", "y1", "x2", "y2"))


def test_extracts_pins_with_consistent_owners():
    pins = read_schematic_pins(FIXTURE)
    assert len(pins) == 34
    # Every pin's owner_index resolves to a component (owner scheme matches).
    recs = read_schdoc_records(FIXTURE)
    comp_owners = {pos - 1 for pos, r in enumerate(recs)
                   if pos > 0 and r.get("RECORD") == RECORD_COMPONENT}
    assert all(p["owner_index"] in comp_owners for p in pins)
    # Orientations are quantized to the four cardinal directions.
    assert all(p["orientation"] in (0, 90, 180, 270) for p in pins)
    # U1's VIN pin is present and owned by a component.
    vin = [p for p in pins if p["name"] == "VIN"]
    assert vin and vin[0]["length"] > 0


def test_bad_file_raises(tmp_path):
    junk = tmp_path / "not_a_schdoc.SchDoc"
    junk.write_bytes(b"this is not an OLE file")
    with pytest.raises(ValueError):
        read_schdoc_records(junk)
