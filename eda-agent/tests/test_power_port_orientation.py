# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""sch_place_power_port must be able to aim the glyph.

The bridge has always accepted an ``orientation`` param (Generic.pas
reads it and falls back to a style-based default when it is negative),
but the Python tool never passed one. That left a direct caller unable
to override the default, and the default is wrong for one common case:
it groups ``bar`` and ``wave`` with the ground styles and sends them
DOWN, so a VCC rail drawn as a bar comes out looking like a ground.

The design engine was never affected -- its emitter computes the
orientation itself and sends it explicitly -- so this is about the raw
tool being usable on its own.
"""

from __future__ import annotations

import asyncio

import pytest


class _RecordingBridge:
    """Captures commands instead of talking to Altium."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def send_command_async(self, command, params=None, **kwargs):
        self.calls.append((command, dict(params or {})))
        return {"success": True}


@pytest.fixture
def tools(monkeypatch):
    from eda_agent.tools import generic as generic_mod

    bridge = _RecordingBridge()
    monkeypatch.setattr(generic_mod, "get_bridge", lambda: bridge)

    captured: dict = {}

    class _Capture:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    generic_mod.register_generic_tools(_Capture())
    return captured, bridge


def _params(bridge, command="generic.place_power_port"):
    return next(p for c, p in bridge.calls if c == command)


def test_orientation_defaults_to_auto(tools):
    """Omitting it must keep the previous behaviour exactly.

    -1 tells the bridge to derive from style, which is what callers got
    before this parameter existed.
    """
    fns, bridge = tools
    asyncio.run(fns["sch_place_power_port"](text="GND", x=0, y=0,
                                            style="gnd_power"))
    assert _params(bridge)["orientation"] == "-1"


def test_explicit_orientation_reaches_the_bridge(tools):
    """A VCC bar must be aimable UP, which is the case the default gets
    wrong."""
    fns, bridge = tools
    asyncio.run(fns["sch_place_power_port"](text="VCC", x=100, y=200,
                                            style="bar", orientation=1))
    sent = _params(bridge)
    assert sent["orientation"] == "1"
    assert sent["style"] == "bar"
    assert sent["text"] == "VCC"


@pytest.mark.parametrize("orientation", [0, 1, 2, 3])
def test_all_four_orientations_pass_through(tools, orientation):
    fns, bridge = tools
    asyncio.run(fns["sch_place_power_port"](text="V", x=0, y=0,
                                            orientation=orientation))
    assert _params(bridge)["orientation"] == str(orientation)


@pytest.mark.parametrize("style,expected", [
    ("bar", 1), ("circle", 1), ("arrow", 1), ("wave", 1),
    ("gnd_power", 3), ("gnd_signal", 3), ("gnd_earth", 3),
])
def test_engine_emitter_aims_rails_up_and_grounds_down(style, expected):
    """Guard the path that was already correct, so it stays correct.

    Calls the REAL emitter and reads what it puts on the wire. Restating
    its rule inline here would assert nothing about the code.

    Note "bar" and "wave": the bridge's own style-based fallback sends
    both DOWN, so the emitter must keep sending an explicit orientation
    rather than ever relying on that default.
    """
    from eda_agent.design import emitter as emitter_mod

    sent: dict = {}

    class _Bridge:
        def send_command(self, command, params=None, **kwargs):
            sent["command"] = command
            sent["params"] = dict(params or {})
            return {"success": True}

    class _Port:
        def __init__(self, style):
            self.text, self.x, self.y, self.style = "NET", 10, 20, style

    result = emitter_mod.EmitResult()
    emitter_mod._emit_power_ports([_Port(style)], _Bridge(), result, "main")

    assert sent["command"] == "generic.place_power_ports"
    fields = dict(kv.split("=", 1)
                  for kv in sent["params"]["ports"].split(";"))
    assert fields["style"] == style
    assert int(fields["orientation"]) == expected
