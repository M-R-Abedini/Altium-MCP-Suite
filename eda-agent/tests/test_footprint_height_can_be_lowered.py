# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Heights that are too TALL were unfixable, and that is the worse fault.

Footprint.Height drives Altium's placement-collision DRC. The sweep
could only ever raise a height, on the reasoning that a hand-set value
beats a model, and there was no setter at all, so a footprint carrying
an absurd height could be read and not corrected.

Raising-only leaves the damaging direction unreachable. A footprint
claiming 50mm when the part is 3mm fails against everything near it and
blocks placements that are fine, which floods the report and gets the
rule switched off. A too-low height merely fails to catch a real
collision, quietly.

THE ONE THING NEITHER MODE MAY DO IS WRITE ZERO. A footprint with no 3D
body yields no measurement, and writing the 0 that implies does not
relax the rule, it disables it for that part. Doing that across every
unmodelled part in a library would turn the check off wholesale while
reporting a successful sweep, which is the failure this file guards
hardest.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_PASCAL = (Path(__file__).resolve().parents[1]
           / "scripts" / "altium" / "Library.pas")


def _handler(name: str) -> str:
    source = _PASCAL.read_text(encoding="utf-8")
    start = source.index(f"Function {name}(")
    end = source.index("\nFunction ", start + 1)
    return source[start:end]


def _tools(monkeypatch, bridge):
    from eda_agent.tools import library as library_module

    monkeypatch.setattr(library_module, "get_bridge", lambda: bridge)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    library_module.register_library_tools(DummyMcp())
    return captured


class _Bridge:
    def __init__(self):
        self.sent = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.sent.append((command, params or {}))
        return {"ok": True}


# --------------------------------------------------------------------
# The sweep learned a direction.
# --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_match_mode_reaches_the_pascal(monkeypatch):
    bridge = _Bridge()
    tool = _tools(monkeypatch, bridge)["lib_update_footprint_heights_from_3d"]

    await tool(mode="match")

    command, params = bridge.sent[0]
    assert command == "library.update_footprint_heights_from_3d"
    assert params["mode"] == "match", (
        "the mode has to cross the bridge, or the tool grew an argument "
        "that changes nothing")


@pytest.mark.asyncio
async def test_the_default_is_still_raise_only(monkeypatch):
    """Changing the default would start lowering heights unasked."""
    bridge = _Bridge()
    tool = _tools(monkeypatch, bridge)["lib_update_footprint_heights_from_3d"]

    await tool()

    assert bridge.sent[0][1]["mode"] == "raise"


@pytest.mark.asyncio
async def test_an_unknown_mode_is_refused_before_the_bridge(monkeypatch):
    bridge = _Bridge()
    tool = _tools(monkeypatch, bridge)["lib_update_footprint_heights_from_3d"]

    out = await tool(mode="shrink")

    assert out["ok"] is False and "match" in out["reason"]
    assert bridge.sent == [], "a bad mode must not reach Altium"


# --------------------------------------------------------------------
# The setter that did not exist.
# --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_height_can_be_set_in_millimetres(monkeypatch):
    bridge = _Bridge()
    tool = _tools(monkeypatch, bridge)["lib_set_footprint_height"]

    await tool(height_mm=3.2, footprint_name="MODULE_A")

    command, params = bridge.sent[0]
    assert command == "library.set_footprint_height"
    assert params == {"height_mm": 3.2, "footprint_name": "MODULE_A"}


@pytest.mark.asyncio
async def test_a_negative_height_never_reaches_altium(monkeypatch):
    bridge = _Bridge()
    tool = _tools(monkeypatch, bridge)["lib_set_footprint_height"]

    out = await tool(height_mm=-1.0)

    assert out["ok"] is False
    assert bridge.sent == []


@pytest.mark.asyncio
async def test_a_nonsense_height_never_reaches_altium(monkeypatch):
    bridge = _Bridge()
    tool = _tools(monkeypatch, bridge)["lib_set_footprint_height"]

    for bad in ("tall", None, float("nan")):
        out = await tool(height_mm=bad)
        assert out["ok"] is False, f"{bad!r} was accepted"
    assert bridge.sent == []


# --------------------------------------------------------------------
# The Pascal, where the zero rule lives.
# --------------------------------------------------------------------

def test_neither_mode_can_write_a_zero_height():
    """The guard this file exists for.

    Both branches sit under a check that a height was actually
    measured, so an unmodelled footprint is never written at all.
    """
    body = _handler("Lib_UpdateFootprintHeightsFrom3D")

    assert "If NewH <= 0 Then" in body, (
        "an unmeasured footprint must be excluded before any write")
    # The only assignment to Height must be inside the measured branch.
    writes = [m.start() for m in re.finditer(r"Footprint\.Height := ", body)]
    assert len(writes) == 1, f"expected one write, found {len(writes)}"
    assert body.index("If NewH <= 0 Then") < writes[0]


def test_an_unmodelled_footprint_is_reported_not_silently_skipped():
    """Skipping quietly would read as "the library is now covered"."""
    body = _handler("Lib_UpdateFootprintHeightsFrom3D")

    assert "without_model" in body
    assert "without_model_names" in body, (
        "a count alone does not say WHICH parts still have no height")


def test_match_lowers_and_raise_does_not():
    body = _handler("Lib_UpdateFootprintHeightsFrom3D")

    assert "If Mode = 'match' Then ShouldWrite := (NewH <> OldH)" in body
    assert "Else ShouldWrite := (NewH > OldH);" in body


def test_an_unknown_mode_is_refused_in_the_pascal_too():
    """The Python check can be bypassed by tool_invoke or a raw call."""
    body = _handler("Lib_UpdateFootprintHeightsFrom3D")

    assert "BAD_MODE" in body


def test_the_setter_says_that_zero_disables_the_rule():
    """Clearing a height and disabling the check are the same edit.

    Only one of them sounds harmless, so the reply has to name the
    other one.
    """
    body = _handler("Lib_SetFootprintHeight")

    assert "BAD_HEIGHT" in body, "a negative height must be refused"
    assert "disables the placement-collision rule" in body


def test_the_setter_does_not_save_the_library():
    """Consistent with the sweep, which leaves saving to a human."""
    body = _handler("Lib_SetFootprintHeight")

    assert "JsonBool('saved', False)" in body
    assert "SaveDoc" not in body
