# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the headless schematic review (roadmap V1)."""

from __future__ import annotations

from pathlib import Path

from eda_agent.fileio.review import (
    ERROR,
    INFO,
    WARNING,
    review_components,
    review_cross_sheet,
    review_document_info,
    review_schematic_file,
    to_sarif,
)

FIXTURE = Path(__file__).resolve().parent / "integration" / "fixtures" / "main.SchDoc"


def test_review_real_fixture_has_no_errors():
    rep = review_schematic_file(FIXTURE)
    assert rep["component_count"] == 14
    # A real, buildable board: no hard errors (no collisions / missing desig).
    assert rep["summary"][ERROR] == 0
    checks = {f["check"] for f in rep["findings"]}
    assert "designator_collision" not in checks
    assert "missing_designator" not in checks


def _comp(**kw):
    base = {"designator": "R1", "lib_reference": "RES", "mpn": "M",
            "manufacturer": "Mfr", "value": "10k", "datasheet": "http://x",
            "parameters": {}}
    base.update(kw)
    return base


def test_designator_collision_is_error():
    findings = review_components([_comp(designator="R1"), _comp(designator="R1")])
    coll = [f for f in findings if f["check"] == "designator_collision"]
    assert coll and coll[0]["severity"] == ERROR
    assert "R1" in coll[0]["designator"]


def test_duplicate_unique_id_is_error():
    findings = review_components([
        _comp(designator="R1", unique_id="ABC123"),
        _comp(designator="R2", unique_id="ABC123"),  # copy-paste dup
    ])
    dup = [f for f in findings if f["check"] == "duplicate_unique_id"]
    assert dup and dup[0]["severity"] == ERROR
    assert "R1" in dup[0]["designator"] and "R2" in dup[0]["designator"]


def test_distinct_unique_ids_clean():
    findings = review_components([
        _comp(designator="R1", unique_id="AAA"),
        _comp(designator="R2", unique_id="BBB"),
    ])
    assert not [f for f in findings if f["check"] == "duplicate_unique_id"]


def test_real_fixture_has_no_duplicate_unique_ids():
    rep = review_schematic_file(FIXTURE)
    assert not [f for f in rep["findings"] if f["check"] == "duplicate_unique_id"]


def test_missing_designator_is_error():
    findings = review_components([_comp(designator="")])
    assert any(f["check"] == "missing_designator" and f["severity"] == ERROR
               for f in findings)


def test_missing_mpn_and_datasheet_flagged():
    findings = review_components([_comp(designator="U9", mpn="", datasheet="")])
    checks = {f["check"] for f in findings}
    assert "missing_mpn" in checks
    assert "missing_datasheet" in checks


def test_unannotated_designator_is_error():
    findings = review_components([_comp(designator="R?")])
    ua = [f for f in findings if f["check"] == "unannotated_designator"]
    assert ua and ua[0]["severity"] == ERROR
    assert ua[0]["designator"] == "R?"


def test_annotated_fixture_has_no_unannotated_findings():
    rep = review_schematic_file(FIXTURE)
    assert not [f for f in rep["findings"]
                if f["check"] == "unannotated_designator"]


def test_placeholder_value_flagged():
    findings = review_components([_comp(designator="R5", value="TBD")])
    ph = [f for f in findings if f["check"] == "placeholder_value"]
    assert ph and ph[0]["severity"] == WARNING


def test_passive_with_non_numeric_value_flagged():
    # A library reference leaked into the Value field of a resistor.
    findings = review_components([_comp(designator="R7", value="RES")])
    mv = [f for f in findings if f["check"] == "malformed_value"]
    assert mv and mv[0]["severity"] == WARNING and mv[0]["designator"] == "R7"


def test_annotated_passive_value_not_flagged():
    # Real schematics annotate values; a digit is present, so no false flag.
    for good in ("10k", "100n", "4u7", "0R", "10k 1%", "100n 0603", "2.2uF"):
        findings = review_components([_comp(designator="C3", value=good)])
        assert not [f for f in findings if f["check"] == "malformed_value"], good


def test_non_passive_non_numeric_value_not_flagged():
    # A connector/IC legitimately has a text value; the check is passives-only.
    findings = review_components([_comp(designator="J2", value="Phoenix_2P")])
    assert not [f for f in findings if f["check"] == "malformed_value"]


def test_real_fixture_has_no_malformed_values():
    rep = review_schematic_file(FIXTURE)
    assert not [f for f in rep["findings"] if f["check"] == "malformed_value"]


def test_fully_specified_part_is_clean():
    findings = review_components([_comp()])
    assert findings == []


def test_cross_sheet_collision_distinct_uids_flagged():
    # Same designator, two DIFFERENT physical parts (distinct UniqueIDs) on
    # two sheets -> a real ECO-breaking collision.
    findings = review_cross_sheet({
        "power.SchDoc": [_comp(designator="R1", unique_id="AAA")],
        "mcu.SchDoc": [_comp(designator="R1", unique_id="BBB")],
    })
    coll = [f for f in findings if f["check"] == "cross_sheet_designator_collision"]
    assert coll and coll[0]["severity"] == ERROR and coll[0]["designator"] == "R1"
    assert "power.SchDoc" in coll[0]["message"] and "mcu.SchDoc" in coll[0]["message"]


def test_multipart_component_across_sheets_not_flagged():
    # U1A / U1B: one physical multi-part component shares ONE UniqueID across
    # sheets -> legitimate, must NOT be flagged.
    findings = review_cross_sheet({
        "sheet1.SchDoc": [_comp(designator="U1", unique_id="SAME")],
        "sheet2.SchDoc": [_comp(designator="U1", unique_id="SAME")],
    })
    assert not [f for f in findings
                if f["check"] == "cross_sheet_designator_collision"]


def test_cross_sheet_without_uids_is_conservative():
    # No UniqueIDs -> a collision cannot be proven, so stay silent.
    findings = review_cross_sheet({
        "a.SchDoc": [_comp(designator="R1", unique_id="")],
        "b.SchDoc": [_comp(designator="R1", unique_id="")],
    })
    assert not [f for f in findings
                if f["check"] == "cross_sheet_designator_collision"]


def test_single_sheet_designator_not_cross_flagged():
    findings = review_cross_sheet({
        "only.SchDoc": [_comp(designator="R1", unique_id="AAA"),
                        _comp(designator="R2", unique_id="BBB")],
    })
    assert findings == []


def test_title_block_incomplete_flagged():
    findings = review_document_info(
        {"title": "", "revision": "", "sheet": {}})
    checks = [f for f in findings if f["check"] == "title_block_incomplete"]
    assert len(checks) == 2  # missing title + missing revision
    assert all(f["severity"] == INFO for f in checks)


def test_complete_title_block_is_clean():
    findings = review_document_info({"title": "Buck", "revision": "B"})
    assert findings == []


def test_review_report_includes_document(FIXTURE=FIXTURE):
    rep = review_schematic_file(FIXTURE)
    assert "document" in rep
    assert rep["document"]["sheet"]["custom_x"] == 1500
    # The unfilled fixture title block produces info findings.
    assert any(f["check"] == "title_block_incomplete" for f in rep["findings"])


def test_sarif_structure_valid():
    report = review_schematic_file(FIXTURE)
    sarif = to_sarif(report, tool_version="9.9.9")
    assert sarif["version"] == "2.1.0"
    assert sarif["$schema"].endswith("sarif-2.1.0.json")
    run = sarif["runs"][0]
    driver = run["tool"]["driver"]
    assert driver["name"] == "eda-agent" and driver["version"] == "9.9.9"
    assert driver["rules"], "no rule metadata"
    # One result per finding, each with a valid SARIF level + location URI.
    assert len(run["results"]) == len(report["findings"])
    for r in run["results"]:
        assert r["level"] in ("error", "warning", "note")
        assert r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]


def test_sarif_level_mapping():
    findings = review_components([
        {"designator": "R1", "mpn": "M", "manufacturer": "X", "value": "1k",
         "datasheet": "u", "lib_reference": "RES"},
        {"designator": "R1", "mpn": "M", "manufacturer": "X", "value": "1k",
         "datasheet": "u", "lib_reference": "RES"},  # collision -> error
    ])
    sarif = to_sarif({"file": "x.SchDoc", "findings": findings, "summary": {}})
    levels = {r["ruleId"]: r["level"] for r in sarif["runs"][0]["results"]}
    assert levels.get("designator_collision") == "error"


def test_sarif_uses_forward_slash_uri():
    sarif = to_sarif({"file": r"a\b\c.SchDoc", "findings": [], "summary": {}})
    # SARIF URIs are forward-slash even on Windows paths (still valid empty run).
    assert sarif["runs"][0]["results"] == []


def test_summary_counts_by_severity():
    rep_findings = review_components([
        _comp(designator="R1"), _comp(designator="R1"),  # collision (error)
        _comp(designator="U9", mpn="", datasheet=""),    # 2 warnings
    ])
    errors = [f for f in rep_findings if f["severity"] == ERROR]
    warnings = [f for f in rep_findings if f["severity"] == WARNING]
    assert len(errors) >= 1 and len(warnings) >= 2
