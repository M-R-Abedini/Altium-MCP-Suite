# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every mils-to-millimetre conversion in the tree must agree.

An inch is exactly 25.4 mm, so one mil is exactly 0.0254 mm. The factor
is written out in five places under three different names
(``MM_PER_MIL`` twice, ``_MIL_TO_MM``, a function-local ``MM``, and a
bare literal that ``route/repair.py`` divides by to go the other way).
They all agree today and nothing makes them keep agreeing.

A unit error is the failure this project already worries about most:
``docs/RELEASE_VERIFICATION.md`` tells the verifier that a 3D body
moving 25.4 times too far means the property wanted different units. The
same mistake in an exporter is quieter, because the file is still valid
and only the dimensions are wrong, and a footprint that is 25.4 times
too large is caught by a human or by the fab, not by a test.

This checks BEHAVIOUR rather than the constants. Comparing literals
would pass if someone kept the constant and multiplied where they meant
to divide, which is the more likely mistake: the two exporters multiply
and the DRC parser divides, using the same number.

The right fix is one shared definition instead of four. That is a
refactor across modules that do not otherwise import each other, so this
guard holds the invariant until then.
"""

from __future__ import annotations

import pytest

# One inch is exactly 25.4 mm by definition, so this is not a
# measurement and must never be "improved" to more decimal places.
MM_PER_MIL_EXACT = 0.0254


def test_the_exporters_convert_mils_to_mm():
    from eda_agent.export.kicad_footprint import _mm as footprint_mm
    from eda_agent.libimport.easyeda.kicad import _mm as easyeda_mm

    for mils, expected_mm in [(1000, 25.4), (100, 2.54), (0, 0.0),
                              (39.37007874, 1.0)]:
        assert footprint_mm(mils) == pytest.approx(expected_mm, abs=1e-6)
        assert easyeda_mm(mils) == pytest.approx(expected_mm, abs=1e-6)


def test_the_stackup_column_converts_the_same_way():
    from eda_agent.export.stackup_csv import _mm as stackup_mm

    # It returns a FORMATTED STRING for the CSV column, not a float.
    assert stackup_mm(1000) == "25.4000"
    assert stackup_mm(62) == "1.5748"
    assert stackup_mm(None) == ""


def test_the_drc_parser_converts_mm_back_to_mils():
    """The one that divides. A multiply here is off by 645x, not 25.4x."""
    from eda_agent.route.repair import _to_mils

    assert _to_mils(25.4, "mm") == pytest.approx(1000.0, abs=1e-6)
    assert _to_mils(1.0, "mm") == pytest.approx(39.3700787, abs=1e-5)
    # A value already in mils passes through untouched.
    assert _to_mils(10.0, "mil") == 10.0
    assert _to_mils(10.0, "MIL") == 10.0


def test_a_round_trip_returns_the_original():
    """Catches a factor that is self-consistently wrong in both halves."""
    from eda_agent.export.kicad_footprint import _mm
    from eda_agent.route.repair import _to_mils

    for mils in (1.0, 62.0, 1000.0, 3937.0):
        assert _to_mils(_mm(mils), "mm") == pytest.approx(mils, rel=1e-9)


def test_every_declared_factor_is_the_exact_one():
    """Named constants, checked as well, so a divergence names itself."""
    from eda_agent.export.kicad_footprint import MM_PER_MIL as a
    from eda_agent.export.stackup_csv import MM_PER_MIL as b
    from eda_agent.libimport.easyeda.kicad import _MIL_TO_MM as c

    for name, value in [("export.kicad_footprint.MM_PER_MIL", a),
                        ("export.stackup_csv.MM_PER_MIL", b),
                        ("libimport.easyeda.kicad._MIL_TO_MM", c)]:
        assert value == MM_PER_MIL_EXACT, (
            f"{name} is {value}, not {MM_PER_MIL_EXACT}. An inch is "
            "exactly 25.4 mm; this factor is a definition, not a "
            "measurement.")


def test_the_copper_thickness_factor_is_defined_only_in_units():
    """1 oz/ft^2 in mils, stated once.

    It was written three times in code (impedance_sizing, trace_sizing,
    and inline in the current-capacity tool) and all three agreed by
    luck, which is where the mils-to-mm factor was before it was
    consolidated. Prose mentions in docstrings are documentation, not a
    second source of truth, so only assignments and arithmetic count.
    """
    import ast
    import pathlib

    # AST, not a regex over lines. Docstrings say things like "1 oz =
    # 1.378 mils", and a text scan reads that as a definition: the
    # first version of this guard flagged four docstrings and no code.
    # A numeric literal in the tree is unambiguous, because a docstring
    # is a STRING constant and can never equal the float.
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "eda_agent"
    sites = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "units.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8",
                                            errors="replace"))
        except SyntaxError:                       # pragma: no cover
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant)
                    and isinstance(node.value, float)
                    and node.value == 1.378):
                sites.append(f"{path.relative_to(root)}:{node.lineno}")

    assert not sites, (
        f"the copper-thickness factor is restated outside units.py at "
        f"{sites}; import OZ_TO_MILS from eda_agent.units instead.")


def test_the_factor_is_defined_only_in_units():
    """No literal 0.0254 outside eda_agent/units.py.

    This file used to pin the count of scattered copies at five; the
    consolidation into ``eda_agent.units`` reduced the correct count to
    zero-outside-units, which is also the stronger guard: any new
    literal anywhere is a duplicate that can drift, and the fix is to
    import ``MM_PER_MIL`` (or ``MILS_PER_MM``) instead of restating it.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "eda_agent"
    sites = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "units.py":
            continue
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8",
                               errors="replace").splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if re.search(r"(?<![\d.])0\.0254(?![\d])", line):
                sites.append(f"{path.relative_to(root)}:{lineno}")

    assert not sites, (
        f"the mils-to-mm factor is restated outside units.py at {sites}; "
        "import MM_PER_MIL from eda_agent.units instead of duplicating "
        "the literal.")
