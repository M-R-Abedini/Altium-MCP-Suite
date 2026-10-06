# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The tool wrapper: what it refuses, and what it authorises.

Only the wrapper is covered here. The decision logic lives in
test_dialog_driver.py, the real-window behaviour in
test_dialog_driver_live_windows.py, and Altium's own control shapes in
test_altium_control_shapes.py.

This file used to also exercise a scripted plan runner. That runner has
been deleted: the tool reacts to whatever dialog is on screen now, and
keeping a second, obsolete mechanism around meant the real-window tests
were pointed at code that no longer shipped.
"""

from __future__ import annotations

import asyncio

import pytest


class _FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


@pytest.fixture(autouse=True)
def _altium_is_present(monkeypatch):
    """Answer the process lookup without a real Altium.

    Every tool here resolves a process id first and returns early if
    there is none. Nothing stubbed that, so the whole file passed on a
    machine with Altium open and failed everywhere else: green locally,
    eight failures in CI. The tools are about DECIDING what to press,
    which has nothing to do with whether an editor happens to be
    running, so the lookup is answered here and the decisions are what
    gets tested.
    """
    from eda_agent.tools import uiauto

    monkeypatch.setattr(uiauto, "_altium_pid", lambda: (4242, None))


@pytest.fixture
def update_tool():
    from eda_agent.tools.uiauto import register_uiauto_tools

    mcp = _FakeMcp()
    register_uiauto_tools(mcp)
    return mcp.tools["app_update_from_libraries"]


@pytest.fixture
def stub_drive(monkeypatch):
    """Capture what the tool asks the driver to do, without driving."""
    from eda_agent.ui import dialog_driver

    seen = {}

    def fake(pid, **kwargs):
        seen.update(kwargs)
        seen["pid"] = pid
        return {"ok": True, "committed": False, "steps": [],
                "finished": "stub"}

    monkeypatch.setattr(dialog_driver, "drive", fake)
    return seen


def test_a_real_run_without_the_eco_confirmation_is_allowed_but_cannot_commit(
        update_tool, stub_drive):
    """The gate sits where the commit happens, and that is better.

    A blanket refusal of every real run made the tool useless in the
    commonest case: with the design already in sync there is no change
    order at all, only a "no differences" dialog needing dismissal, and
    refusing to run left that modal blocking the editor.

    VERIFIED LIVE: without authorisation it pressed Yes, pressed
    Validate Changes, then stopped at Execute with committed False.
    """
    out = asyncio.run(update_tool(dry_run=False))

    assert out["ok"] is True, "a real run must not be refused outright"
    assert stub_drive["allow_commit"] is False, (
        "without confirm_execute_eco the driver must be told it may not "
        "commit, which is what actually protects the design")


def test_confirm_execute_eco_is_what_authorises_the_commit(update_tool,
                                                           stub_drive):
    asyncio.run(update_tool(dry_run=False, confirm_execute_eco=True))
    assert stub_drive["allow_commit"] is True


def test_a_dry_run_never_authorises_a_commit(update_tool, stub_drive):
    asyncio.run(update_tool(dry_run=True, confirm_execute_eco=True))
    assert stub_drive["dry_run"] is True, (
        "a dry run must stay dry even when the commit was authorised")


def test_confirmations_are_answered_by_default(update_tool, stub_drive):
    """Altium asks "Continue and create ECO?" before every change order,
    so a default of not answering would make the tool unable to finish."""
    asyncio.run(update_tool(dry_run=False))
    assert stub_drive["allow_confirm"] is True


def test_confirmations_can_be_withheld(update_tool, stub_drive):
    asyncio.run(update_tool(dry_run=False, answer_confirmations=False))
    assert stub_drive["allow_confirm"] is False


def test_an_unknown_target_is_refused(update_tool):
    out = asyncio.run(update_tool(target="library"))
    assert out["ok"] is False
    assert "schematic or pcb" in out["reason"]


def test_an_unknown_launch_mode_is_refused(update_tool):
    out = asyncio.run(update_tool(launch="double click it"))
    assert out["ok"] is False
    assert "already_open" in out["reason"]


@pytest.mark.parametrize("target", ["schematic", "pcb"])
def test_launching_by_menu_needs_explicit_consent_to_steal_focus(update_tool,
                                                                 target):
    """Opening the wizard is possible, but only with real input.

    MEASURED, each fallback because the safer one did nothing: the
    bridge's menu call reports success and invokes nothing even for
    mapped paths (#83); Altium has no Win32 menu, so there are no menu
    ids; and its DevExpress bars expose MSAA READ-ONLY, so
    accDoDefaultAction returns cleanly and raises no popup. What works
    is Alt+T plus a real click on the item.

    That brings Altium to the front and moves the pointer, which every
    other press in this package avoids, so it is never done unasked.
    """
    out = asyncio.run(update_tool(launch="menu", target=target))

    assert out["ok"] is False
    assert "menu_may_steal_focus" in out["reason"], (
        "must name the flag that enables it")
    assert "already_open" in out["reason"], "must offer the quiet route too"
    assert "Update From" in out["reason"], "must name the wizard to open"


@pytest.fixture
def stub_menu(monkeypatch):
    """Capture the menu path the tool asks for, without driving Altium.

    Patching the WRONG name here is not a harmless test bug: when these
    stubbed the superseded open_item, the real click_path ran and drove
    the live editor from a unit test. Patch what the tool calls.
    """
    from eda_agent.ui import menu

    seen = {}

    def fake(pid, path, *a, **k):
        seen.update(pid=pid, path=path)
        return {"ok": True, "path": path}

    monkeypatch.setattr(menu, "click_path", fake)
    return seen


def test_menu_launch_is_attempted_once_consent_is_given(update_tool,
                                                        stub_drive,
                                                        stub_menu):
    """With consent, it drives the menu bar rather than the bridge.

    The bridge is deliberately not used: its menu call reports success
    and opens nothing (#83).
    """
    out = asyncio.run(update_tool(launch="menu", menu_may_steal_focus=True,
                                  dry_run=True))

    assert out["ok"] is True
    assert stub_menu["path"] == "Tools|Update From Libraries...", (
        "the caption must match Altium's menu exactly, ellipsis included: "
        "the Tools menu carries several entries starting 'Update'")


def test_the_pcb_target_uses_its_own_menu_path(update_tool, stub_drive,
                                               stub_menu):
    asyncio.run(update_tool(target="pcb", launch="menu",
                            menu_may_steal_focus=True, dry_run=True))
    assert stub_menu["path"] == "Tools|Update From PCB Libraries..."


def test_a_menu_that_does_not_open_reports_it_and_drives_nothing(
        update_tool, monkeypatch):
    """No wizard means no driving, and the reply must say so.

    Otherwise the run waits out its whole timeout for a dialog that was
    never opened.
    """
    from eda_agent.ui import menu

    monkeypatch.setattr(menu, "click_path", lambda *a, **k: {
        "ok": False, "reason": "'Tools' is not on the menu bar",
        "offered": ["File", "Help"]})

    out = asyncio.run(update_tool(launch="menu", menu_may_steal_focus=True,
                                  dry_run=True))

    assert out["ok"] is False
    assert "not on the menu bar" in out["reason"]
    assert out["offered"] == ["File", "Help"], (
        "what the menu actually held is what makes this fixable")
    assert "nothing was driven" in out["note"]
    assert "steps" not in out
