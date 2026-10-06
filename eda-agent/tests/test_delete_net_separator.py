# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""``pcb_delete_net`` must not let a net name become two net names.

The list of nets travels as ONE comma-separated field and
``PCB_DeleteNets`` splits it with ``Pos(',', NName)``. So a net whose own
name contains a comma arrives as two names.

That is worse than a call that does nothing, because the fragments can
match real nets. ``VCC,GND`` splits into ``VCC`` and ``GND``, both of
which exist on most boards. With ``force=True`` the handler deletes nets
that still have connected primitives, orphaning their pads and tracks,
so the failure is a board edit nobody asked for on nets nobody named.

Refused rather than sanitised. Stripping the comma would turn
``VCC,GND`` into ``VCCGND``, which is a name that matches nothing and
leaves the caller believing the net was considered. Refusing says what
happened and why.

The default path is unaffected: without ``force`` only empty nets are
removed, which is the ordinary cleanup this tool exists for.
"""

from __future__ import annotations

import pytest

from eda_agent.tools import pcb as pcb_module


def _capture_tool(monkeypatch, bridge):
    monkeypatch.setattr(pcb_module, "get_bridge", lambda: bridge)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    pcb_module.register_pcb_tools(DummyMcp())
    return captured["pcb_delete_net"]


class _Bridge:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, params or {}))
        return {"deleted": 2, "skipped_connected": 0, "skipped_nets": []}

    def commands(self):
        return [c for c, _ in self.calls]


@pytest.mark.asyncio
async def test_ordinary_names_are_forwarded(monkeypatch):
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)

    out = await tool(nets=["N1", "N2"])

    assert out["deleted"] == 2
    assert bridge.calls[0][1]["nets"] == "N1,N2"
    assert bridge.calls[0][1]["force"] == "false"


@pytest.mark.asyncio
async def test_a_comma_in_a_net_name_is_refused(monkeypatch):
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)

    out = await tool(nets=["VCC,GND", "N2"])

    assert out["ok"] is False
    assert "VCC,GND" in out["reason"]
    assert bridge.commands() == [], (
        "the refusal must precede the command; the split happens on the "
        "Pascal side, so sending it is already the mistake")


@pytest.mark.asyncio
async def test_it_is_refused_under_force_too(monkeypatch):
    """force is where the split stops being harmless."""
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)

    out = await tool(nets=["VCC,GND"], force=True)

    assert out["ok"] is False
    assert bridge.commands() == []


@pytest.mark.asyncio
async def test_the_empty_sweep_still_works(monkeypatch):
    """No names at all means sweep every empty net, and must not refuse."""
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)

    out = await tool()

    assert out.get("ok") is not False
    assert bridge.calls[0][1]["nets"] == ""


@pytest.mark.asyncio
async def test_blank_entries_are_dropped_not_sent(monkeypatch):
    """A stray blank would otherwise produce an empty comma segment."""
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)

    await tool(nets=["N1", "  ", "", "N2"])

    assert bridge.calls[0][1]["nets"] == "N1,N2"
