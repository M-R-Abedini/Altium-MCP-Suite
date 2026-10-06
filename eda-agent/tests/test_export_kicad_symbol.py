# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""``lib_export_kicad_symbol`` writes the file and reports what it moved.

Exporting reads a symbol, but naming one SELECTS it, and the selection
outlives the call. That matters because the library tools that take no
component name act on whatever is current: export symbol A while editing
symbol B, and the next ``lib_add_pins`` adds pins to A.

It is not restored. The caller asked about that component, so leaving it
selected is defensible; leaving it silent is not, so the reply carries
``current_component`` whenever a name was given. Same principle as
``proj_print_all_variants``, which does restore and reports whether the
restore worked: say what state you left behind.

The tool had no tests at all before this file.
"""

from __future__ import annotations

import pytest

from eda_agent.tools import library as library_module


def _capture_tool(monkeypatch, bridge):
    monkeypatch.setattr(library_module, "get_bridge", lambda: bridge)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    library_module.register_library_tools(DummyMcp())
    return captured["lib_export_kicad_symbol"]


_PINS = {
    "component": "MODULE_A",
    "pins": [
        {"designator": "1", "name": "VIN", "x": -300, "y": 0,
         "orientation": 0, "electrical_type": "power"},
        {"designator": "2", "name": "OUT", "x": 300, "y": 0,
         "orientation": 2, "electrical_type": "output"},
    ],
}


class _Bridge:
    def __init__(self, pins=None):
        self.pins = pins if pins is not None else _PINS
        self.calls: list[tuple[str, dict]] = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, params or {}))
        if command == "library.get_pin_list":
            return self.pins
        return {"success": True}

    def commands(self):
        return [c for c, _ in self.calls]


@pytest.mark.asyncio
async def test_naming_a_component_reports_the_selection_it_leaves(
        monkeypatch, tmp_path):
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)

    out = await tool(component_name="MODULE_A",
                     output_path=str(tmp_path / "a.kicad_sym"))

    assert out["success"] is True
    assert out["current_component"] == "MODULE_A"
    assert "act on it" in out["note"], (
        "a side effect the caller cannot see is the same as a bug the "
        "next call inherits")


@pytest.mark.asyncio
async def test_exporting_the_current_component_changes_nothing(
        monkeypatch, tmp_path):
    """With no name, nothing is selected, so nothing is reported."""
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)

    out = await tool(output_path=str(tmp_path / "b.kicad_sym"))

    assert "library.set_current_component" not in bridge.commands(), (
        "an export with no component name must not change the selection")
    assert "current_component" not in out
    assert out["symbol"] == "MODULE_A"


@pytest.mark.asyncio
async def test_the_component_is_selected_before_the_pins_are_read(
        monkeypatch, tmp_path):
    """Order is the whole correctness argument.

    get_pin_list has no component parameter: it reads whatever
    GetTargetLibComponent resolves. Reading before selecting would
    export the previous component's pins into a file named after the
    requested one, which no assertion on the reply would catch.
    """
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)

    await tool(component_name="MODULE_A",
               output_path=str(tmp_path / "c.kicad_sym"))

    cmds = bridge.commands()
    assert cmds.index("library.set_current_component") < \
        cmds.index("library.get_pin_list")


@pytest.mark.asyncio
async def test_the_written_file_carries_every_pin(monkeypatch, tmp_path):
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)
    target = tmp_path / "d.kicad_sym"

    out = await tool(component_name="MODULE_A", output_path=str(target))

    text = target.read_text(encoding="utf-8")
    assert out["pin_count"] == 2
    assert text.count("(pin ") == 2
    assert '(number "1"' in text and '(number "2"' in text
    assert "power_in" in text, "electrical types must survive the mapping"
    assert text.startswith("(kicad_symbol_lib")


@pytest.mark.asyncio
async def test_a_symbol_with_no_pins_still_produces_a_valid_file(
        monkeypatch, tmp_path):
    """The body falls back to a fixed rectangle rather than an empty one.

    A zero-size rectangle is what a naive min/max over no pins gives,
    and KiCad renders it as nothing at all.
    """
    bridge = _Bridge(pins={"component": "MODULE_A", "pins": []})
    tool = _capture_tool(monkeypatch, bridge)
    target = tmp_path / "e.kicad_sym"

    out = await tool(component_name="MODULE_A", output_path=str(target))

    text = target.read_text(encoding="utf-8")
    assert out["pin_count"] == 0
    assert "(rectangle (start -2.5400 2.5400) (end 2.5400 -2.5400)" in text, (
        "with no pins the body must fall back to a fixed 5.08mm square; "
        "a min/max over an empty list would give a zero-size rectangle "
        "that KiCad draws as nothing")
    assert "(pin " not in text
