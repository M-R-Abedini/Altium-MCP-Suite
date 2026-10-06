# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A lib_id names a library, not a path.

``extract_symbol`` and ``find_footprint_file`` both build a filename out
of a caller-supplied ``Lib:Name``, and both promise to search the
directories they were handed. Nothing upstream constrains the id:
``DesignPlan.Part.lib_ref`` is validated for length and nothing else.

So an id could name a file OUTSIDE those directories and be answered as
though it had been found in a library. Two spellings do it, and only one
of them is the obvious one:

* ``../../elsewhere/foo:Sym`` walks out with ``..``
* ``C:/anywhere/evil:Sym`` does not walk anywhere. On Windows a drive
  prefix makes the second argument to ``os.path.join`` absolute, so the
  search directory is discarded outright. A guard written against ``..``
  alone passes this one.

Each test plants a REAL readable file at the escape target, so a
refusal means the guard refused and not that the path happened to miss.
"""

from __future__ import annotations

import os

import pytest

from eda_agent.core.kicad_footprint import (find_footprint_file, is_inside,
                                            is_plain_name)
from eda_agent.core.kicad_symbol import extract_symbol

_SYMBOL_BODY = '(symbol "SECRET" (property "Reference" "U"))'


@pytest.fixture()
def planted(tmp_path):
    """A search dir, and a library sitting OUTSIDE it that must stay unread."""
    search = tmp_path / "symbols"
    search.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evil.kicad_sym").write_text(_SYMBOL_BODY, encoding="utf-8")

    pretty = outside / "evil.pretty"
    pretty.mkdir()
    (pretty / "SECRET.kicad_mod").write_text("(footprint)", encoding="utf-8")
    return search, outside


def _relative_escape(search, outside) -> str:
    """A .. path from the search dir to the planted library, as KiCad has none."""
    return os.path.relpath(str(outside / "evil"), str(search)).replace(os.sep, "/")


def test_the_planted_library_is_genuinely_readable(planted):
    """Guard the guard: the tests below must fail for the right reason.

    If the plant were unreadable, every refusal here would pass whether
    or not the code checks anything, which is the failure mode that
    makes a security test worthless.
    """
    search, outside = planted
    assert extract_symbol("evil:SECRET", [str(outside)]) is not None, (
        "the planted symbol cannot be read even from its own directory, "
        "so the escape tests below prove nothing")
    assert find_footprint_file("evil:SECRET", [str(outside)]) is not None


def test_symbol_lib_id_cannot_escape_with_dotdot(planted):
    search, outside = planted
    assert extract_symbol(f"{_relative_escape(search, outside)}:SECRET",
                          [str(search)]) is None


def test_symbol_lib_id_cannot_escape_with_a_rooted_path(planted):
    """Rooted, not drive-absolute, because only one of those is reachable.

    ``lib`` is everything before the FIRST colon, so it can never hold a
    drive letter: ``C:/x/evil:SECRET`` splits into ``lib="C"`` and the
    lookup misses because no ``C.kicad_sym`` exists, not because anything
    refused it. An earlier version of this test used that spelling and
    passed with the guard removed, which is worth more as a warning than
    the assertion was as a check.

    A leading separator is the spelling that reaches the join.
    """
    search, outside = planted
    # Drive stripped, single leading separator kept. An earlier attempt
    # built "//Users/..." by accident, which Windows reads as a UNC share
    # that does not exist, so it missed without the guard doing anything.
    rooted = os.path.splitdrive(str(outside))[1].replace("\\", "/")
    assert rooted.startswith("/") and not rooted.startswith("//"), rooted
    assert extract_symbol(f"{rooted}/evil:SECRET", [str(search)]) is None


@pytest.mark.skipif(os.name != "nt", reason="drive letters are Windows only")
def test_a_foreign_drive_discards_the_search_directory():
    """Why a colon is refused in the name, pinned as platform behaviour.

    The name half can hold one, since ``split(":", 1)`` leaves everything
    after the first colon there: ``x:D:evil`` yields ``name="D:evil"``.

    MEASURED, and not what it first looks like. On the SAME drive as the
    search directory, ``C:evil`` is appended relative to it and escapes
    nothing. It is a DIFFERENT drive that discards the base outright,
    which is why the rule is about the colon rather than about ``..``.
    """
    base = r"C:\base\search"
    assert os.path.join(base, "x.pretty", "C:evil.kicad_mod").startswith(base)
    assert os.path.join(base, "x.pretty", "D:evil.kicad_mod") == "D:evil.kicad_mod"


def test_an_escaped_candidate_is_refused_even_if_it_exists(planted, monkeypatch):
    """The containment check, exercised without needing a second drive.

    The escapes that matter most on Windows need a drive this machine may
    not have, so the interesting branch would otherwise go untested here:
    the lookup would miss because nothing is there, and pass whether or
    not anything was checked.

    Instead the filesystem is made to claim every candidate exists. A
    build that trusts ``os.path.isfile`` alone then returns a path
    outside the search directory, which is exactly the defect.
    """
    search, outside = planted
    monkeypatch.setattr(os.path, "isfile", lambda p: True)

    esc = os.path.relpath(str(outside), str(search)).replace(os.sep, "/")
    got = find_footprint_file(f"{esc}/evil:SECRET", [str(search)])
    assert got is None, f"returned {got}, which is outside {search}"


def test_footprint_lib_id_cannot_escape_with_dotdot(planted):
    search, outside = planted
    esc = os.path.relpath(str(outside), str(search)).replace(os.sep, "/")
    assert find_footprint_file(f"{esc}/evil:SECRET", [str(search)]) is None


def test_footprint_name_cannot_escape_either(planted):
    """The NAME half lands in the path too, unlike the symbol case."""
    search, outside = planted
    esc = os.path.relpath(str(outside / "evil.pretty" / "SECRET"),
                          str(search / "x.pretty")).replace(os.sep, "/")
    assert find_footprint_file(f"x:{esc}", [str(search)]) is None


def test_bare_footprint_name_cannot_escape(planted):
    """The no-colon branch joins the whole id, so it needs the same guard."""
    search, outside = planted
    (search / "lib.pretty").mkdir()
    esc = os.path.relpath(str(outside / "evil.pretty" / "SECRET"),
                          str(search / "lib.pretty")).replace(os.sep, "/")
    assert find_footprint_file(esc, [str(search)]) is None


def test_ordinary_kicad_names_are_still_accepted():
    """The rule refuses path syntax, not punctuation.

    KiCad ships symbols called ``+3V3``, ``C_Polarized`` and ``74HC00``,
    so a character allow-list would refuse parts that exist. Pinned
    because that is the tempting way to write this and it breaks real
    lookups rather than failing a test.
    """
    for good in ("Device", "Connector_Generic", "MCU_ST_STM32F4",
                 "+3V3", "-15V", "C_Polarized", "74HC00", "R_Small",
                 "~", "L293D", "Amplifier_Audio"):
        assert is_plain_name(good), good


def test_path_syntax_is_refused():
    for bad in ("", ".", "..", "a/b", "a\\b", "C:evil", "/abs", "x\x00y"):
        assert not is_plain_name(bad), bad


def test_is_inside_resolves_before_comparing(tmp_path):
    """A prefix match on the raw strings is not containment.

    ``/base-evil`` starts with ``/base`` as text and is a different
    directory, which is why the separator is part of the comparison.
    """
    base = tmp_path / "base"
    base.mkdir()
    sibling = tmp_path / "base-evil"
    sibling.mkdir()

    assert is_inside(str(base), str(base / "f.kicad_sym"))
    assert not is_inside(str(base), str(sibling / "f.kicad_sym"))
    assert not is_inside(str(base), str(base / ".." / "f.kicad_sym"))
