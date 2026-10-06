# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the headless .SchLib reader + library review (roadmap V1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from eda_agent.fileio.altium_schlib import read_schlib_components
from eda_agent.fileio.review import (
    ERROR,
    WARNING,
    review_library_components,
    review_library_file,
)

LIB = Path(__file__).resolve().parent / "integration" / "fixtures" / "EDAAgentTest_ICs.SchLib"


def test_reads_library_component_headers():
    comps = read_schlib_components(LIB)
    by = {c["lib_reference"]: c for c in comps}
    assert {"SS14", "TPS54331D", "Component_1"} <= set(by)
    assert by["TPS54331D"]["description"].startswith("Buck DC-DC")
    assert by["SS14"]["description"]


def test_library_review_flags_placeholder_and_missing_desc():
    rep = review_library_file(LIB)
    checks = {(f["check"], f["designator"]) for f in rep["findings"]}
    # Component_1 is an unnamed default part with no description.
    assert ("placeholder_component_name", "Component_1") in checks
    assert ("library_missing_description", "Component_1") in checks
    # The properly-authored parts produce no findings.
    assert not any(f["designator"] in ("SS14", "TPS54331D")
                   for f in rep["findings"])


def test_library_review_severity():
    findings = review_library_components([
        {"name": "Component_3", "lib_reference": "Component_3", "description": ""},
        {"name": "R_0603", "lib_reference": "R_0603", "description": "resistor"},
    ])
    sev = {f["check"]: f["severity"] for f in findings}
    assert sev["placeholder_component_name"] == ERROR
    assert sev["library_missing_description"] == WARNING


def test_clean_library_part_no_findings():
    findings = review_library_components([
        {"name": "OPA1", "lib_reference": "OPA1", "description": "op-amp"},
    ])
    assert findings == []


def test_bad_library_file_raises(tmp_path):
    junk = tmp_path / "x.SchLib"
    junk.write_bytes(b"not ole")
    with pytest.raises(ValueError):
        read_schlib_components(junk)
