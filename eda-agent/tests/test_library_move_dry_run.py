# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The bulk library moves can be previewed before they delete anything.

``lib_move_components`` and ``lib_move_footprints`` select by regex and
``delete_from_source`` defaults to True, so one loose pattern moves more
than intended and empties it out of the source in the same call. They had
no way to see the selection first.

The handler itself is careful, which is worth stating so nobody
over-corrects: a failed replicate is counted in ``failed`` and skipped,
so the source keeps it, and a source path equal to the destination is
refused outright. The unrecoverable case is ``overwrite=True`` replacing
a DIFFERENT component that happened to share a name in the destination.

The regex is applied with ``re.search``, not ``fullmatch``. ``R`` selects
every name containing an R. That is a reasonable regex convention and a
poor thing to discover after the move, which is what dry_run is for.

Neither tool had any test before this file, so the round trip is pinned
too, not just the new flag.
"""

from __future__ import annotations

import pytest

from eda_agent.tools import library as library_module


def _capture(monkeypatch, bridge, name):
    monkeypatch.setattr(library_module, "get_bridge", lambda: bridge)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    library_module.register_library_tools(DummyMcp())
    return captured[name]


_COMPONENTS = {"components": [{"name": "MODULE_A"}, {"name": "MODULE_B"},
                              {"name": "REG_1"}]}
_FOOTPRINTS = {"footprints": [{"name": "BGA49"}, {"name": "QFN32"},
                              {"name": "R0402"}]}


class _Bridge:
    def __init__(self, listing):
        self.listing = listing
        self.calls: list[tuple[str, dict]] = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, params or {}))
        if command in ("library.get_components", "library.get_footprints"):
            return self.listing
        return {"success": True, "moved": 2, "skipped": 0, "failed": 0}

    def commands(self):
        return [c for c, _ in self.calls]


@pytest.mark.asyncio
async def test_component_dry_run_reports_the_regex_selection(monkeypatch):
    bridge = _Bridge(_COMPONENTS)
    tool = _capture(monkeypatch, bridge, "lib_move_components")

    out = await tool(source_schlib="a.SchLib", dest_schlib="b.SchLib",
                     name_regex="MODULE", dry_run=True)

    assert out["dry_run"] is True
    assert out["resolved"] == ["MODULE_A", "MODULE_B"]
    assert out["count"] == 2
    assert "library.move_components" not in bridge.commands(), (
        "a dry run must not reach the move command")


@pytest.mark.asyncio
async def test_dry_run_surfaces_both_destructive_flags(monkeypatch):
    """The two things that decide whether the move can be undone."""
    bridge = _Bridge(_COMPONENTS)
    tool = _capture(monkeypatch, bridge, "lib_move_components")

    out = await tool(source_schlib="a.SchLib", dest_schlib="b.SchLib",
                     names=["MODULE_A"], overwrite=True, dry_run=True)

    assert out["delete_from_source"] is True, (
        "the default is destructive, so the preview must show it")
    assert out["overwrite"] is True


@pytest.mark.asyncio
async def test_the_regex_is_a_search_not_a_full_match(monkeypatch):
    """Pinned because it decides how wide a short pattern reaches."""
    bridge = _Bridge(_COMPONENTS)
    tool = _capture(monkeypatch, bridge, "lib_move_components")

    out = await tool(source_schlib="a.SchLib", dest_schlib="b.SchLib",
                     name_regex="_", dry_run=True)

    assert out["resolved"] == ["MODULE_A", "MODULE_B", "REG_1"], (
        "a single underscore selecting everything is the behaviour "
        "dry_run exists to expose, not a bug to fix here")


@pytest.mark.asyncio
async def test_a_real_move_still_sends_the_command(monkeypatch):
    bridge = _Bridge(_COMPONENTS)
    tool = _capture(monkeypatch, bridge, "lib_move_components")

    out = await tool(source_schlib="a.SchLib", dest_schlib="b.SchLib",
                     names=["MODULE_A", "MODULE_B"])

    assert out["moved"] == 2
    sent = dict(bridge.calls)["library.move_components"]
    assert sent["names"] == "MODULE_A~~MODULE_B"
    assert sent["delete_from_source"] == "true"


@pytest.mark.asyncio
async def test_footprint_dry_run_reports_the_selection(monkeypatch):
    bridge = _Bridge(_FOOTPRINTS)
    tool = _capture(monkeypatch, bridge, "lib_move_footprints")

    out = await tool(source_pcblib="a.PcbLib", dest_pcblib="b.PcbLib",
                     name_regex="^[BQ]", dry_run=True)

    assert out["resolved"] == ["BGA49", "QFN32"]
    assert "library.move_footprints" not in bridge.commands()


@pytest.mark.asyncio
async def test_a_regex_matching_nothing_is_an_error_not_an_empty_move(
        monkeypatch):
    """Sending an empty name list would move nothing and report success."""
    bridge = _Bridge(_COMPONENTS)
    tool = _capture(monkeypatch, bridge, "lib_move_components")

    out = await tool(source_schlib="a.SchLib", dest_schlib="b.SchLib",
                     name_regex="NOTHING_MATCHES_THIS", dry_run=True)

    assert "error" in out
    assert "library.move_components" not in bridge.commands()
