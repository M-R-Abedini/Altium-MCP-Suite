# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The bulk delete honours the same guard as the single delete.

``obj_delete`` refuses an empty filter, because an empty filter deletes
every object of that type in scope, and it says so: "Safety guard: empty
filter would delete ALL objects of type X". Passing
``confirm_delete_all=True`` is the way through.

``obj_batch_delete`` had no such check. Each operation carries its own
filter, an empty one sweeps the same way, and nothing asked. The two
tools point at each other, ``obj_delete`` recommends the bulk one for
several sets at once and the bulk one says to prefer it over looping, so
a caller who hit the guard was told by the product to use the path
without it. A guard that the documentation routes around is not a guard.

The blast radius is identical: one object_type per operation, in one
scope. Nothing about the bulk form makes an unfiltered delete safer, so
nothing justified the asymmetry.

Sweeping is still possible, deliberately. Purging every junction on a
project is a real cleanup, and the confirmation only makes the caller
say so. ONE unfiltered operation is enough to require it: the other
operations in the batch would still have run, and a half-applied batch
is harder to reason about after the fact than one that did nothing.
"""

from __future__ import annotations

import pytest

from eda_agent.tools import generic as generic_module


def _capture_tool(monkeypatch, bridge):
    monkeypatch.setattr(generic_module, "get_bridge", lambda: bridge)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    generic_module.register_generic_tools(DummyMcp())
    return captured


class _Bridge:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, params or {}))
        return {"operations_processed": 2, "total": 7}

    def commands(self):
        return [c for c, _ in self.calls]


_FILTERED = {"scope": "active_doc", "object_type": "eWire",
             "filter": "Net=GND"}
_SWEEP = {"scope": "project", "object_type": "eJunction", "filter": ""}


@pytest.mark.asyncio
async def test_a_filtered_batch_needs_no_confirmation(monkeypatch):
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)["obj_batch_delete"]

    out = await tool(operations=[_FILTERED])

    assert out["operations_processed"] == 2
    assert "generic.batch_delete" in bridge.commands()


@pytest.mark.asyncio
async def test_an_unfiltered_operation_is_refused(monkeypatch):
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)["obj_batch_delete"]

    out = await tool(operations=[_SWEEP])

    assert "error" in out
    assert out["operations_processed"] == 0
    assert bridge.commands() == [], (
        "the refusal must precede the command; once it is sent the "
        "objects are gone")
    assert "eJunction in project" in out["unfiltered_operations"][0]


@pytest.mark.asyncio
async def test_one_unfiltered_operation_blocks_the_whole_batch(monkeypatch):
    """A partly-applied batch is worse than one that did nothing."""
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)["obj_batch_delete"]

    out = await tool(operations=[_FILTERED, _SWEEP])

    assert "error" in out
    assert bridge.commands() == []
    assert len(out["unfiltered_operations"]) == 1


@pytest.mark.asyncio
async def test_confirming_lets_the_sweep_through(monkeypatch):
    """The capability is preserved, only made explicit."""
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)["obj_batch_delete"]

    out = await tool(operations=[_SWEEP], confirm_delete_all=True)

    assert out["operations_processed"] == 2
    sent = bridge.calls[0][1]["operations"]
    assert "object_type=eJunction" in sent


@pytest.mark.asyncio
async def test_a_whitespace_filter_counts_as_empty(monkeypatch):
    """'  ' is not a filter, and reaches the handler as an empty one."""
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)["obj_batch_delete"]

    out = await tool(operations=[
        {"scope": "active_doc", "object_type": "eNoERC", "filter": "   "}])

    assert "error" in out
    assert bridge.commands() == []


@pytest.mark.asyncio
async def test_the_single_delete_guard_still_works(monkeypatch):
    """Pinned alongside, because the two must agree.

    If obj_delete ever loses its guard, the asymmetry returns pointing
    the other way and this file would still pass on its own.
    """
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)["obj_delete"]

    out = await tool(object_type="eJunction")

    assert "error" in out
    assert out["matched"] == 0
    assert bridge.commands() == []

    confirmed = await tool(object_type="eJunction", confirm_delete_all=True)
    assert "error" not in confirmed
