# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Text the bridge cannot carry is named before it is silently flattened.

Altium's DelphiScript strings are single byte. ``UnescapeJsonString`` in
Main.pas emits ``Chr(Code)`` for a codepoint up to 255 and a literal
``?`` above it, so text crossing the wire is flattened rather than
refused. The call succeeds, the field is wrong, and nothing says so.

The boundary is exactly U+00FF, not "non-ASCII", and getting that
distinction right is the point: the micro sign, the degree sign and
accented Latin all survive, so warning about them would be noise that
teaches callers to ignore the warning. The ohm sign and CJK text do not
survive.

It matters most on imported parts. LCSC descriptions are frequently
Chinese and the importers pass ``comp.description`` into the plan
unchanged, so a part imports with question marks where its description
should be. The plan is reported BEFORE it is executed, which is the
moment a caller can still do something about it.

The scan lives in ``build_altium_plan`` rather than in either import
tool. ``lib_easyeda_import`` and ``lib_kicad_import`` both call it, and
putting the check in one of them is exactly how the two drift apart:
the first version of this did, and only the EasyEDA path warned.

Not fixable in Python: the flattening happens inside the Pascal. The
honest thing available is to say which fields it will hit.

WHY ONLY THE IMPORT PATH, and not a check inside the bridge covering
every tool. The import plan is the one place where text the USER NEVER
TYPED reaches Altium: ``libimport/easyeda/fetch.py`` is the only module
that pulls remote content into something the bridge then writes, so a
Chinese description can arrive without anyone choosing it. Text passed
directly to a tool was typed by the caller, who can see what they typed,
and warning about it would be noise on every call. A bridge-level check
would cover no additional SOURCE of surprise, only additional volume.
"""

from __future__ import annotations

import pytest

from eda_agent.bridge.payload import unsendable_chars
from eda_agent.libimport.easyeda.altium import unsendable_in_plan

OHM = chr(0x03A9)
MICRO = chr(0x00B5)
DEGREE = chr(0x00B0)
E_ACUTE = chr(0x00E9)
CJK = chr(0x7535) + chr(0x963B)


@pytest.mark.parametrize("text", [
    "10" + MICRO + "F",
    "85" + DEGREE + "C",
    "R" + E_ACUTE + "f",
    "plain ascii",
    "",
])
def test_latin1_text_survives_and_is_not_flagged(text):
    """Warning about text that arrives intact would train callers to
    ignore the warning."""
    assert unsendable_chars(text) == ""


@pytest.mark.parametrize("text,expected", [
    ("10" + OHM, OHM),
    (CJK, CJK),
    ("mix " + OHM + " and " + MICRO, OHM),
])
def test_text_above_latin1_is_reported(text, expected):
    assert unsendable_chars(text) == expected


def test_each_offending_character_is_listed_once_in_order():
    text = OHM + "a" + CJK + "b" + OHM
    assert unsendable_chars(text) == OHM + CJK


def test_the_boundary_is_255_not_127():
    """U+00FF survives, U+0100 does not. Pinned because 'non-ASCII'
    is the intuitive but wrong rule."""
    assert unsendable_chars(chr(0x00FF)) == ""
    assert unsendable_chars(chr(0x0100)) == chr(0x0100)


def test_a_plan_reports_the_tool_and_field(monkeypatch):
    steps = [
        {"tool": "lib_create_symbol",
         "args": {"name": "MODULE_A", "description": "chip " + CJK}},
        {"tool": "lib_add_pins", "args": {"pins": "1,VIN"}},
    ]
    found = unsendable_in_plan(steps)

    assert found == [("lib_create_symbol.description", CJK)], (
        "the caller needs to know WHICH field, not just that something "
        "is wrong")


def test_a_clean_plan_reports_nothing(monkeypatch):
    steps = [{"tool": "lib_create_symbol",
              "args": {"name": "MODULE_A", "description": "10" + MICRO + "F"}}]
    assert unsendable_in_plan(steps) == []


def test_non_string_arguments_are_skipped():
    """Coordinates cannot be flattened, and str() on them would be noise."""
    steps = [{"tool": "lib_add_pins",
              "args": {"x": -300, "y": 0, "count": 4, "visible": True}}]
    assert unsendable_in_plan(steps) == []


def test_a_malformed_plan_does_not_raise():
    """The warning path must never be the thing that breaks an import."""
    assert unsendable_in_plan(None) == []
    assert unsendable_in_plan([None]) == []
    assert unsendable_in_plan([{}]) == []
    assert unsendable_in_plan([{"tool": "t"}]) == []
    assert unsendable_in_plan([{"args": None}]) == []


def test_the_same_field_is_not_reported_twice():
    steps = [
        {"tool": "lib_create_symbol", "args": {"description": CJK}},
        {"tool": "lib_create_symbol", "args": {"description": CJK}},
    ]
    assert len(unsendable_in_plan(steps)) == 1


def _plan_with_description(description: str):
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent
    from eda_agent.libimport.kicad.reader import read_kicad_footprint

    mod = ('(footprint "FP" (version 20251024) (layer "F.Cu")\n'
           '  (pad "1" smd roundrect (at -1 0) (size 1.5 1)\n'
           '    (roundrect_rratio 0.25)\n'
           '    (layers "F.Cu" "F.Paste" "F.Mask")))')
    comp = read_kicad_footprint(mod)
    return build_altium_plan(
        EasyEdaComponent(mpn="FP", description=description,
                         footprint=comp.footprint),
        "T.SchLib", "T.PcbLib")


def test_the_shared_plan_builder_emits_the_warning():
    """The invariant that keeps the two importers together.

    lib_easyeda_import and lib_kicad_import both return
    build_altium_plan's warnings verbatim, so putting the scan here is
    what makes both of them report it. The first version of this change
    put the scan in lib_easyeda_import instead, and lib_kicad_import
    silently kept importing question marks.
    """
    plan = _plan_with_description("chip " + CJK)

    hits = [w for w in plan["warnings"] if "cannot carry" in w]
    assert len(hits) == 1, plan["warnings"]
    assert "lib_create_footprint.description" in hits[0]
    assert CJK in hits[0]


def test_the_shared_plan_builder_stays_quiet_on_latin1():
    plan = _plan_with_description("10" + MICRO + "F " + DEGREE + "C")
    assert [w for w in plan["warnings"] if "cannot carry" in w] == []
