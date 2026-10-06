# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Enumerated string values must mean the same thing on both sides.

Two parameters cross the bridge as a word rather than a number: a pin's
electrical type and a power port's glyph. Each is turned into an Altium
enum by an If/Else chain that ends in a DEFAULT rather than an error, so
a value one side knows and the other does not is never rejected. An
unrecognised electrical type becomes Passive; an unrecognised power
style becomes a Circle. The call succeeds, the response says ok, and the
symbol is wrong in a way only someone looking at the sheet would catch.
ERC then reasons about a power pin recorded as passive.

Check the mapping that RUNS. Utils.pas
has tidy ``StrToPowerStyle`` and ``StrToPinOrientation`` converters and
nothing calls either of them: ``Gen_PlacePowerPort`` and
``Gen_PlacePowerPorts`` each inline their own If/Else chain, and
orientation is read as an integer so its compass words never run at all.
A guard pointed at the converters passes while the live chains drift,
and reports coverage that does not exist. Only ``StrToPinElectrical``
is actually called (Library.pas, Lib_AddPins).

The power-style vocabulary therefore exists in three copies. The two
that run are checked against each other here. The unused converter is
checked only for remaining unused, so adopting it later fails this
file first.

Only SPELLINGS are checked. Which Altium enum member a word becomes
cannot be verified without Altium, so step 7 of
docs/RELEASE_VERIFICATION.md covers that half.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PASCAL_DIR = REPO / "scripts" / "altium"
UTILS = PASCAL_DIR / "Utils.pas"
GENERIC = PASCAL_DIR / "Generic.pas"
TOOLS = REPO / "src" / "eda_agent" / "tools"

#: The advertised vocabularies, as the docstrings state them. Aliases a
#: mapping also tolerates are deliberately absent: they are accepted,
#: not promised, and pinning them would freeze convenience spellings
#: into the contract.
ELECTRICAL = {
    "input", "output", "bidirectional", "passive", "open_collector",
    "open_emitter", "power", "hiz", "io",
}
POWER_STYLE = {
    "circle", "arrow", "bar", "wave", "gnd_power", "gnd_signal",
    "gnd_earth",
}

#: Converters in Utils.pas that nothing calls. Listed so the dead code
#: is a known state rather than a discovery, and so adopting one is a
#: deliberate change that fails this test first.
UNUSED_CONVERTERS = ("StrToPowerStyle", "StrToPinOrientation")

#: Values that are correct only because they are what the final Else
#: produces. Listed because a typo yields the same value as the real
#: word, so the two are indistinguishable at runtime.
BY_FALLING_THROUGH = {"passive", "circle"}


def _function_body(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^Function\s+" + name + r"\b", text, re.M)
    assert match, f"{name} not found in {path.name}"
    nxt = re.search(r"^(?:Function|Procedure)\s+\w+", text[match.end():], re.M)
    return text[match.start():match.end() + (nxt.start() if nxt else 0)]


def _compared_literals(body: str, var: str) -> set[str]:
    """Every string the body compares ``var`` against."""
    return set(re.findall(var + r"\s*=\s*'([^']*)'", body))


def _strips_underscores(body: str, var: str) -> bool:
    return bool(re.search(r"StringReplace\s*\(\s*" + var + r"\s*,\s*'_'", body)
                or re.search(r"StripChar\s*\(\s*" + var + r"\s*,\s*'_'", body))


#: Only comparisons that ASSIGN the style count. Each handler tests
#: StyleStr twice: once to pick the glyph, and again a few lines later
#: to choose a default orientation (grounds and rails point down,
#: everything else up). Collecting every comparison merges the two, and
#: then deleting a glyph from the mapping changes nothing, because the
#: same word is still named in the orientation block. A mutation that
#: dropped 'wave' from the bulk handler passed this file unnoticed
#: before this was narrowed.
_STYLE_ASSIGN = re.compile(
    r"StyleStr\s*=\s*'([^']*)'\s*Then\s+PowerObj\.Style\s*:=")


def _power_style_sites() -> dict[str, set[str]]:
    """The style vocabulary each handler actually maps to a glyph."""
    return {
        name: set(_STYLE_ASSIGN.findall(_function_body(GENERIC, name)))
        for name in ("Gen_PlacePowerPort", "Gen_PlacePowerPorts")
    }


# ---------------------------------------------------------------- pins


def test_pin_electrical_accepts_every_advertised_value():
    body = _function_body(UTILS, "StrToPinElectrical")
    accepted = _compared_literals(body, "LS")
    assert accepted, "parsed no literals; the scan broke"
    strip = _strips_underscores(body, "LS")
    missing = sorted(
        v for v in ELECTRICAL
        if v not in BY_FALLING_THROUGH
        and (v.replace("_", "") if strip else v) not in accepted)
    assert not missing, (
        f"StrToPinElectrical does not recognise {missing}, so these "
        f"advertised types silently become Passive. Add a branch, or "
        f"stop advertising them.")


def test_pin_electrical_is_the_converter_that_actually_runs():
    """The guard above is only meaningful while something calls it."""
    callers = []
    for path in PASCAL_DIR.glob("*.pas"):
        if path.name in ("Altium_MCP.pas", "Utils.pas"):
            continue
        if "StrToPinElectrical(" in path.read_text(encoding="utf-8",
                                                   errors="replace"):
            callers.append(path.name)
    assert callers, (
        "nothing outside Utils.pas calls StrToPinElectrical any more, so "
        "this file is guarding dead code. Find the mapping that replaced "
        "it and point the check there.")


def test_underscore_stripping_is_seen_where_it_happens():
    """Without this normalisation the two open-* types read as missing."""
    body = _function_body(UTILS, "StrToPinElectrical")
    assert _strips_underscores(body, "LS")
    assert "opencollector" in _compared_literals(body, "LS")
    assert "open_collector" not in _compared_literals(body, "LS")


# -------------------------------------------------------- power ports


@pytest.mark.parametrize("handler", ["Gen_PlacePowerPort",
                                     "Gen_PlacePowerPorts"])
def test_power_style_handler_accepts_every_advertised_value(handler):
    accepted = _power_style_sites()[handler]
    assert accepted, f"parsed no style literals out of {handler}"
    missing = sorted(v for v in POWER_STYLE
                     if v not in BY_FALLING_THROUGH and v not in accepted)
    assert not missing, (
        f"{handler} does not recognise {missing}, so these advertised "
        f"styles silently draw a Circle instead of the glyph asked for.")


def test_the_two_power_style_handlers_agree():
    """The two copies of this vocabulary must not diverge.

    The single and bulk placement paths map style independently. A glyph
    added to one and not the other means the same request draws
    different symbols depending on which tool the caller reached for,
    and neither errors.
    """
    sites = _power_style_sites()
    single, bulk = sites["Gen_PlacePowerPort"], sites["Gen_PlacePowerPorts"]
    assert single == bulk, (
        f"the single and bulk power-port handlers map different style "
        f"vocabularies. Only in single: {sorted(single - bulk)}. Only in "
        f"bulk: {sorted(bulk - single)}.")


def test_both_handlers_default_rather_than_reject():
    """The premise of this file, asserted instead of assumed."""
    for name in ("Gen_PlacePowerPort", "Gen_PlacePowerPorts"):
        body = _function_body(GENERIC, name)
        assert re.search(r"Else\s+PowerObj\.Style\s*:=", body), (
            f"{name} no longer ends in a plain Else default; re-read "
            f"whether this guard is still the right shape")


# ------------------------------------------------------- dead converters


@pytest.mark.parametrize("converter", UNUSED_CONVERTERS)
def test_the_unused_converters_are_still_unused(converter):
    """If one gets adopted, its vocabulary must be checked, not assumed.

    StrToPowerStyle accepts spellings the live chains do not (``rail``,
    ``ground``, ``sgnd``, ``egnd`` among them). Wiring it in would widen
    the accepted set without anyone deciding to, and would make the two
    inline chains a third and fourth opinion. Adopting it is fine; doing
    so silently is not.
    """
    callers = []
    for path in PASCAL_DIR.glob("*.pas"):
        if path.name in ("Altium_MCP.pas", "Utils.pas"):
            continue
        if converter + "(" in path.read_text(encoding="utf-8",
                                             errors="replace"):
            callers.append(path.name)
    assert not callers, (
        f"{converter} is now called from {callers} but this file still "
        f"treats it as dead. Add its vocabulary to the checks above, and "
        f"reconcile it with the inline chains it now competes with.")


# --------------------------------------------------------- both sides


def test_the_docstrings_advertise_the_whole_contract():
    """A value the code takes but nobody documents is unusable.

    Checked against the tool modules rather than a parsed docstring so a
    value mentioned in prose still counts, since what matters is that a
    caller can find it, not where it is written.
    """
    library = (TOOLS / "library.py").read_text(encoding="utf-8",
                                               errors="replace")
    generic = (TOOLS / "generic.py").read_text(encoding="utf-8",
                                               errors="replace")
    missing_elec = sorted(v for v in ELECTRICAL if v not in library)
    missing_style = sorted(v for v in POWER_STYLE if v not in generic)
    assert not missing_elec, (
        f"library.py never mentions {missing_elec}, so a caller has no "
        f"way to learn those electrical types exist.")
    assert not missing_style, (
        f"generic.py never mentions {missing_style}, so a caller has no "
        f"way to learn those power styles exist.")


def test_the_scan_sees_what_it_claims_to():
    """A regex matching nothing would pass every assertion above."""
    electrical = _compared_literals(
        _function_body(UTILS, "StrToPinElectrical"), "LS")
    assert {"input", "output", "power", "hiz"} <= electrical
    assert {"in", "out", "oc", "oe"} <= electrical, (
        "alias spellings missing, so the parse stops early")

    sites = _power_style_sites()
    assert {"arrow", "bar", "wave", "gnd_earth"} <= sites["Gen_PlacePowerPort"]
    assert len(sites["Gen_PlacePowerPorts"]) >= 6
