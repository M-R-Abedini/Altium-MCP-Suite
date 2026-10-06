# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A pin's root is not where it connects, and the reply must say so.

ISch_Pin.Location is the BODY-SIDE ROOT. The point a wire attaches to is
PinLength away along Orientation. Measured on a live sheet with wires as
the ground truth, because a wire endpoint is where the connection
physically is: four pins at Location.X 3700, Orientation 2, PinLength
300, every attached wire vertex at x 3400, none at 3700. A right-facing
pin read Location.X 4900 and connected at 5200.

The fact was already documented in three places and still got
rediscovered by getting it wrong, so these tests are about it being
IMPOSSIBLE TO MISS at the point of use rather than about it being
written down somewhere.
"""
from __future__ import annotations

import inspect
import re
import subprocess
from pathlib import Path

import pytest

from eda_agent.tools.pin_hints import HINT, pin_location_hint
from tests.test_cross_validate import (          # noqa: F401  (fixture)
    fpc_executable,
    read_outputs,
    write_inputs,
)

GENERIC_PAS = Path(__file__).parent.parent / "scripts" / "altium" / "Generic.pas"
CROSS_VALIDATOR = Path(__file__).parent / "cross_validate_pascal.pas"


# ---------------------------------------------------------------------------
# The arithmetic, mirrored from PinEndX / PinEndY in Generic.pas.
# ---------------------------------------------------------------------------

def pin_end_x(root_x: int, orient: int, pin_len: int) -> int:
    if orient == 0:
        return root_x + pin_len
    if orient == 2:
        return root_x - pin_len
    return root_x


def pin_end_y(root_y: int, orient: int, pin_len: int) -> int:
    if orient == 1:
        return root_y + pin_len
    if orient == 3:
        return root_y - pin_len
    return root_y


@pytest.mark.parametrize("root_x,orient,length,expected", [
    # The four measured pins: left facing, root 3700, wires at 3400.
    (3700, 2, 300, 3400),
    # The right-facing pin on the same component: root 4900, wire at 5200.
    (4900, 0, 300, 5200),
    # Vertical pins do not move in x.
    (3700, 1, 300, 3700),
    (3700, 3, 300, 3700),
])
def test_x_matches_the_measured_wires(root_x, orient, length, expected):
    assert pin_end_x(root_x, orient, length) == expected


@pytest.mark.parametrize("root_y,orient,length,expected", [
    (8200, 1, 300, 8500),
    (8200, 3, 300, 7900),
    (8200, 0, 300, 8200),
    (8200, 2, 300, 8200),
])
def test_y_follows_orientation(root_y, orient, length, expected):
    assert pin_end_y(root_y, orient, length) == expected


def test_the_offset_is_signed_not_constant():
    """Getting the DIRECTION wrong is the failure mode, not the magnitude.

    Adding PinLength to a left-facing pin moves it further from the wire
    rather than onto it, and the geometry still looks plausible. A stored
    note in this project once concluded from that symptom that the offset
    should be dropped entirely, which is wrong in the other direction.
    """
    assert pin_end_x(3700, 2, 300) == 3400
    assert pin_end_x(3700, 0, 300) == 4000
    assert pin_end_x(3700, 2, 300) != pin_end_x(3700, 0, 300)


# ---------------------------------------------------------------------------
# One implementation, not two.
# ---------------------------------------------------------------------------

def test_the_getter_and_the_pin_dump_share_the_helper():
    """Two copies of this arithmetic would drift and disagree per pin.

    obj_query's ConnectionX and get_sch_component_pins' connection point
    describe the same physical location, so they must be the same code.
    """
    text = GENERIC_PAS.read_text(encoding="utf-8", errors="replace")
    assert text.count("Function PinEndX") == 1
    assert text.count("Function PinEndY") == 1
    # Used by the property getter AND by the pin dump.
    assert text.count("PinEndX(") >= 3, (
        "PinEndX should be defined once and called from both the "
        "ConnectionX getter and the pin dump")


def test_the_pin_dump_hands_over_the_connection_point():
    """It used to return only the root, under a name that reads like
    'the pin's position', leaving every caller to derive the rest."""
    text = GENERIC_PAS.read_text(encoding="utf-8", errors="replace")
    assert "connection_x_mils" in text
    assert "connection_y_mils" in text


def test_no_open_coded_pin_offset_remains():
    """A hand-rolled `+ PinLength` is the thing being replaced."""
    text = GENERIC_PAS.read_text(encoding="utf-8", errors="replace")
    stray = re.findall(r"PCoord \+ PLen|PCoord - PLen", text)
    assert not stray, (
        f"open-coded pin offsets left in Generic.pas: {stray}. They should "
        f"go through PinEndX / PinEndY so there is one definition")


# ---------------------------------------------------------------------------
# The hint. Narrow enough not to be noise, present when it matters.
# ---------------------------------------------------------------------------

def test_asking_a_pin_for_its_location_gets_warned():
    assert pin_location_hint("ePin", "Location.X,Location.Y") == HINT
    assert pin_location_hint("epin", "Name,Location.X") == HINT


def test_a_caller_who_already_asked_for_the_connection_is_not_nagged():
    """Repeating it to someone who knows trains them to ignore the field."""
    assert pin_location_hint(
        "ePin", "Location.X,Location.Y,ConnectionX,ConnectionY") is None
    assert pin_location_hint("ePin", "ConnectionX") is None


def test_it_does_not_fire_on_other_objects():
    """A PCB pad has no root-versus-end split, so warning there is noise."""
    for obj in ("eSchComponent", "ePadObject", "eWire", "eNetLabel",
                "ePowerObject", ""):
        assert pin_location_hint(obj, "Location.X,Location.Y") is None


def test_it_does_not_fire_when_location_was_not_asked_for():
    assert pin_location_hint("ePin", "Name,Designator,PinLength") is None


def test_the_hint_names_the_property_that_fixes_it():
    """A warning that does not say what to do instead is just noise."""
    assert "ConnectionX" in HINT
    assert "ConnectionY" in HINT


def test_obj_query_attaches_the_hint():
    """The wiring, not just the helper.

    The helper being correct is worth nothing if the tool never calls it,
    which is exactly how InvalidateCompileCache stayed dead in this same
    codebase.
    """
    from eda_agent.tools import generic

    source = inspect.getsource(generic)
    assert "pin_location_hint(object_type, properties)" in source
    assert '_hint_pin_connection' in source


# ---------------------------------------------------------------------------
# The REAL Pascal, compiled and run. The mirror above only proves itself.
# ---------------------------------------------------------------------------

def _function_source(text: str, name: str) -> str:
    start = text.index("Function " + name)
    terminator = "\nEnd;"
    end = text.index(terminator, start) + len(terminator)
    return text[start:end]


@pytest.mark.parametrize("name", ["PinEndX", "PinEndY"])
def test_the_cross_validated_copy_matches_the_real_source(name):
    """A stale copy would go on proving the OLD arithmetic correct."""
    original = _function_source(
        GENERIC_PAS.read_text(encoding="utf-8", errors="replace"), name)
    copy = _function_source(
        CROSS_VALIDATOR.read_text(encoding="utf-8", errors="replace"), name)

    def flat(text):
        return re.sub(r"\s+", " ", text).strip()

    assert flat(original) == flat(copy), (
        f"{name} in cross_validate_pascal.pas has drifted from Generic.pas")


def test_pascal_agrees_with_the_mirror(fpc_executable, tmp_path):
    """Without this, reversing the sign in the Pascal passed every test.

    Verified by mutation: flipping the two branches of PinEndX left the
    whole file green, because everything else exercises the Python
    mirror. This is the only test that runs what Altium runs.
    """
    cases, expected = [], []
    for root in (0, 3700, 4900, -1200):
        for orient in (0, 1, 2, 3):
            for length in (0, 300, 1000):
                cases.append(("PinEndX", [str(root), str(orient), str(length)]))
                expected.append(str(pin_end_x(root, orient, length)))
                cases.append(("PinEndY", [str(root), str(orient), str(length)]))
                expected.append(str(pin_end_y(root, orient, length)))

    input_file = tmp_path / "pin_in.txt"
    output_file = tmp_path / "pin_out.txt"
    write_inputs(cases, str(input_file))
    result = subprocess.run(
        [fpc_executable, str(input_file), str(output_file)],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"{result.stdout} {result.stderr}"

    pascal = read_outputs(str(output_file))
    assert len(pascal) == len(cases)
    mismatches = [
        f"  {fn}{args}: Pascal {got!r}, Python {want!r}"
        for (fn, args), got, want in zip(cases, pascal, expected)
        if got != want
    ]
    assert not mismatches, "pin arithmetic disagrees: " + "; ".join(mismatches)
