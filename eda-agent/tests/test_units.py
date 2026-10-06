# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The unit factors are defined once, and stay that way.

Before consolidation, 0.0254 was defined independently in six places
across two backends under four names, plus one inline division. All six
agreed by luck; nothing enforced it. The divergence, when it comes,
lands as geometry 25.4x off in exactly one code path, which renders
plausibly and fails at fabrication.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from eda_agent import units

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


def test_the_factors_are_the_definitions():
    assert units.MM_PER_MIL == 0.0254
    assert units.MM_PER_INCH == 25.4
    assert units.MILS_PER_MM == pytest.approx(39.37007874, abs=1e-8)
    # The inch relation holds exactly, not approximately: both numbers
    # are definitions, so drift between them is an edit, not rounding.
    assert units.MM_PER_INCH == units.MM_PER_MIL * 1000


def test_no_second_definition_of_the_factor_exists():
    """A NEW assignment of the literal is a fork of the fact.

    Positional literals (a part placed AT 25.4 mm) are arithmetic, not
    conversions, and do not match the assignment pattern here.
    """
    pattern = re.compile(
        r"^\s*_?[A-Za-z_]*\s*=\s*(?:0\.0254|25\.4|1000\.0?\s*/\s*25\.4)\s*(?:#.*)?$",
        re.MULTILINE)

    offenders = []
    for path in _SRC.rglob("*.py"):
        if path.name == "units.py":
            continue
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(_SRC)}: "
                             f"{match.group(0).strip()}")

    assert not offenders, (
        "the conversion factor is defined outside eda_agent.units, "
        "which is the fork this module exists to prevent:\n  "
        + "\n  ".join(offenders))


def test_the_former_definition_sites_import_the_shared_one():
    """The six consolidated sites must actually use the module.

    Deleting an import and re-inlining the number would satisfy the
    assignment guard above only if the literal were also changed; this
    closes the half where someone re-inlines it exactly.
    """
    expected = {
        "export/kicad_footprint.py": "from eda_agent.units import",
        "export/stackup_csv.py": "from eda_agent.units import",
        "libimport/easyeda/kicad.py": "from eda_agent.units import",
        "libimport/kicad/reader.py": "from eda_agent.units import",
        "route/repair.py": "from eda_agent.units import",
        "tools/library.py": "from eda_agent.units import",
    }
    missing = []
    for rel, needle in expected.items():
        text = (_SRC / "eda_agent" / rel).read_text(encoding="utf-8")
        if needle not in text:
            missing.append(rel)
    assert not missing, (
        f"these consolidated sites no longer import eda_agent.units: "
        f"{missing}")
