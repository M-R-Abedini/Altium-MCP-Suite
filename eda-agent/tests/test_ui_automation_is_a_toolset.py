# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The GUI driver has to be TOOLS, not a recipe rediscovered each time.

Every capability here already existed as working code, reachable only
by writing a throwaway script: pressing a button lived inside one
wizard-specific driver, the reactive loop was never exposed at all, and
finding a submenu meant clicking a deliberately wrong entry and reading
the failure's ``offered`` list. A session that wanted to drive Altium
had to reinvent the same three things.

Two properties matter more than the individual tools and are asserted
directly, because losing either quietly turns the toolset back into a
recipe:

* NOTHING HERE MAY DEPEND ON THE BRIDGE beyond the process-id lookup.
  These are the tools for when a modal has the scripting engine
  blocked, so an IPC call inside one would fail exactly when it is
  needed.
* A COMMAND AND ITS DIALOGS ARE ONE CALL. Split across two bridge
  round-trips the second never arrives, because the first is blocked
  behind the modal it raised.
"""

from __future__ import annotations

import inspect

import pytest

from eda_agent.tools import uiauto
from eda_agent.ui import menu


def _tools():
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    uiauto.register_uiauto_tools(DummyMcp())
    return captured


# --------------------------------------------------------------------
# The surface exists at all.
# --------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "app_press_dialog_button",
    "app_drive_dialogs",
    "app_run_ui_command",
])
def test_the_missing_primitives_are_registered(name):
    """Each of these was working code with no way to call it."""
    assert name in _tools(), (
        f"{name} is the tool that stops a session hand-rolling a script")


def test_pressing_a_button_is_reachable_without_the_wizard():
    """The press used to be private to app_update_from_libraries."""
    tools = _tools()
    source = inspect.getsource(tools["app_press_dialog_button"])
    assert "windows.click" in source, (
        "the generic press must use the same click path proven against "
        "TXPBitBtn, not a second implementation")


# --------------------------------------------------------------------
# Bridge independence. The property that makes these usable at all.
# --------------------------------------------------------------------

def test_no_ui_tool_sends_a_bridge_command():
    """A modal blocks the scripting engine; these must not need it.

    The pid comes from a process SCAN via get_altium_status, which is
    not IPC. Anything that actually sends a command would hang behind
    the very dialog the tool exists to answer.
    """
    source = inspect.getsource(uiauto)
    assert "send_command" not in source, (
        "a UI tool that sends a bridge command cannot answer the modal "
        "that is blocking the bridge")


def test_the_pid_lookup_is_shared_not_reinvented():
    """Four copies of the same six lines is how a fifth gets it wrong."""
    source = inspect.getsource(uiauto)
    assert source.count("get_altium_status()") <= 2, (
        "resolve the pid through _altium_pid; each inlined copy is a "
        "place the not-running check can be forgotten")


# --------------------------------------------------------------------
# The composite. Its whole reason for existing is the modal.
# --------------------------------------------------------------------

def test_the_composite_drives_dialogs_in_the_same_call():
    """Menu click and dialog drive must not be two round-trips."""
    source = inspect.getsource(_tools()["app_run_ui_command"])
    assert "menu.click_path" in source and "dialog_driver.drive" in source, (
        "a command that raises a modal blocks the bridge, so the drive "
        "has to happen inside the same call as the click")


def test_the_composite_defaults_to_presenting_not_committing():
    """A named command may proceed; changing the design may not."""
    signature = inspect.signature(_tools()["app_run_ui_command"])
    assert signature.parameters["allow_commit"].default is False, (
        "a change order must be presented, not executed, unless asked")
    assert signature.parameters["allow_confirm"].default is True, (
        "a command invoked on purpose needs its 'yes, go ahead' "
        "answered or it does nothing at all")
    assert signature.parameters["dry_run"].default is False, (
        "unlike the bare driver, naming a command to RUN and having "
        "nothing happen is not what the caller asked for")


def test_the_bare_driver_defaults_to_dry_run():
    """The opposite default, on purpose: this one is for looking."""
    signature = inspect.signature(_tools()["app_drive_dialogs"])
    assert signature.parameters["dry_run"].default is True
    assert signature.parameters["allow_commit"].default is False
    assert signature.parameters["allow_confirm"].default is False


def test_a_failed_menu_click_does_not_report_success():
    """Reaching the drive stage at all depends on the click landing."""
    source = inspect.getsource(_tools()["app_run_ui_command"])
    assert 'return {"ok": False, "stage": "menu"' in source, (
        "a menu path that did not resolve must fail here, not fall "
        "through to a driver with nothing to drive")


# --------------------------------------------------------------------
# Menu discovery, which had no tool at all.
# --------------------------------------------------------------------

def test_listing_a_menu_activates_altium_first():
    """The bug that made discovery useless.

    Listing a menu's contents means OPENING it, which is a real click,
    and Windows sends a synthesised click to the foreground window
    rather than to the one it was aimed at. So discovery needs the same
    activation as invoking. list_only skipped it and could therefore
    only succeed in the one case where you do not need to ask what the
    menus are. MEASURED: it returned "the menu bar is not laid out"
    even with may_steal_focus set.

    Reading the top-level BAR is the weaker case and needs only a
    restore, measured in ui/menu.bring_to_front. This test is about the
    popup, which needs the focus.
    """
    assert hasattr(menu, "bring_to_front"), (
        "activation must be a shared step, or the next caller that "
        "needs it will forget it again")
    source = inspect.getsource(menu._list_path)
    assert "bring_to_front" in source


def test_listing_returns_one_level_not_every_open_popup():
    """Opening a submenu leaves the parent popup on screen too.

    The accessible layer reports every open popup together, so the
    first version answered 'Tools|Annotation' with Annotation's nine
    entries AND Tools' sixteen, which reads as one flat menu that does
    not exist.
    """
    source = inspect.getsource(menu._list_path)
    assert "_newest_popup" in source, (
        "read back only the popup the last click opened")
    assert hasattr(menu, "_newest_popup")


def test_listing_always_closes_the_menu_it_opened():
    """A dropped menu swallows the next click."""
    source = inspect.getsource(menu._list_path)
    assert "finally:" in source and "close_open_menu" in source, (
        "a discovery call must not change what the editor does next")


def test_an_empty_path_means_the_menu_bar():
    """Discovery has to have a starting point that needs no guess."""
    source = inspect.getsource(menu._list_path)
    assert "if not levels:" in source


# --------------------------------------------------------------------
# The gate on the generic press.
# --------------------------------------------------------------------

def test_a_committing_press_is_refused_by_default():
    """A driver walking an unfamiliar dialog must not execute one."""
    source = inspect.getsource(_tools()["app_press_dialog_button"])
    assert "dialog_driver.COMMITTING" in source, (
        "reuse the driver's own notion of a committing caption rather "
        "than a second word list that can drift from it")
    assert "allow_irreversible" in source


def test_a_disabled_button_is_refused():
    source = inspect.getsource(_tools()["app_press_dialog_button"])
    assert "button.enabled" in source


def test_a_missing_button_reports_what_was_offered():
    """A wrong caption should cost one call, not an investigation."""
    source = inspect.getsource(_tools()["app_press_dialog_button"])
    assert '"offered"' in source


# --------------------------------------------------------------------
# Driving the handlers for real. The tests above read source, which a
# mutation can walk straight past, so the decisions that matter are
# also exercised against the shipping code.
# --------------------------------------------------------------------

from eda_agent.ui import windows as real_windows

#: The Engineering Change Order, as MEASURED off live Altium: VCL
#: TXPBitBtn buttons, one of them the committing press.
ECO_BUTTONS = [
    ("TXPCheckBox", "Only Show Errors", True),
    ("TXPBitBtn", "Validate Changes", True),
    ("TXPBitBtn", "Execute Changes", True),
    ("TXPBitBtn", "&Report Changes...", False),
    ("TXPBitBtn", "Close", True),
]


def _eco_window():
    return real_windows.Window(
        hwnd=4242, class_name="TChangeManagementForm",
        title="Engineering Change Order", pid=1,
        controls=[real_windows.Control(hwnd=i, class_name=c, text=t,
                                       style=0, enabled=e)
                  for i, (c, t, e) in enumerate(ECO_BUTTONS, start=1)])


class _FakeWindows:
    """Stands in for the Win32 layer, recording what got clicked."""

    def __init__(self, dialogs):
        self._dialogs = dialogs
        self.clicked = []

    def dialogs(self, pid):
        return list(self._dialogs)

    def click(self, control):
        self.clicked.append(control.text)

    def wait_for_close(self, hwnd, timeout=5.0):
        return True


@pytest.fixture
def press(monkeypatch):
    """app_press_dialog_button bound to a fake screen."""
    fake = _FakeWindows([_eco_window()])
    monkeypatch.setattr(uiauto, "windows", fake)
    monkeypatch.setattr(uiauto, "_altium_pid", lambda: (1234, None))
    return _tools()["app_press_dialog_button"], fake


@pytest.mark.asyncio
async def test_execute_changes_is_refused_without_the_flag(press):
    """The press that applies a change order to a real board."""
    tool, fake = press

    out = await tool("Execute Changes")

    assert out["ok"] is False
    assert fake.clicked == [], "nothing may be clicked when refusing"
    assert "allow_irreversible" in out["reason"]


@pytest.mark.asyncio
async def test_execute_changes_goes_through_when_asked(press):
    tool, fake = press

    out = await tool("Execute Changes", allow_irreversible=True)

    assert out["ok"] is True
    assert fake.clicked == ["Execute Changes"]


@pytest.mark.asyncio
async def test_a_harmless_button_needs_no_flag(press):
    """Gating everything would make the tool useless."""
    tool, fake = press

    out = await tool("Close")

    assert out["ok"] is True
    assert fake.clicked == ["Close"]


@pytest.mark.asyncio
async def test_the_ampersand_is_ignored_and_disabled_is_refused(press):
    """Report Changes carries an accelerator AND is disabled here."""
    tool, fake = press

    out = await tool("Report Changes")

    assert out["ok"] is False
    assert "disabled" in out["reason"], (
        "the caption must match through the ampersand, or this would "
        "fail as 'not a button' and hide the real state")
    assert fake.clicked == []


@pytest.mark.asyncio
async def test_a_wrong_caption_lists_the_real_ones(press):
    tool, _ = press

    out = await tool("Apply Now")

    assert out["ok"] is False
    assert "Execute Changes" in out["offered"]


@pytest.mark.asyncio
async def test_no_dialog_open_is_a_reason_not_a_crash(monkeypatch):
    monkeypatch.setattr(uiauto, "windows", _FakeWindows([]))
    monkeypatch.setattr(uiauto, "_altium_pid", lambda: (1234, None))

    out = await _tools()["app_press_dialog_button"]("Close")

    assert out["ok"] is False and "no dialog" in out["reason"]


@pytest.mark.asyncio
async def test_a_failed_menu_click_never_reaches_the_driver(monkeypatch):
    """The composite must not report a drive it could not have run.

    Driven rather than read: an earlier shape returned the driver's own
    ok, so a click that never landed still came back looking answered.
    """
    calls = []

    class _Menu:
        MenuBarUnavailable = menu.MenuBarUnavailable

        @staticmethod
        def click_path(pid, path):
            calls.append(("click", path))
            return {"ok": False, "reason": "'Nope' is not on the menu bar",
                    "offered": ["Tools", "Design"]}

    class _Driver:
        COMMITTING = ("commit",)

        @staticmethod
        def role_of(caption):
            return None

        @staticmethod
        def drive(*a, **k):
            calls.append(("drive", None))
            return {"ok": True}

    monkeypatch.setattr(uiauto, "menu", _Menu)
    monkeypatch.setattr(uiauto, "dialog_driver", _Driver)
    monkeypatch.setattr(uiauto, "_altium_pid", lambda: (1234, None))

    out = await _tools()["app_run_ui_command"]("Tools|Nope")

    assert out["ok"] is False
    assert out["stage"] == "menu"
    assert ("drive", None) not in calls, (
        "the driver must not run when the command never fired")


@pytest.mark.asyncio
async def test_a_drive_that_stopped_for_a_human_is_not_a_success(
        monkeypatch):
    """An unanswered dialog left on screen is not a completed command."""
    class _Menu:
        @staticmethod
        def click_path(pid, path):
            return {"ok": True, "path": path}

    class _Driver:
        COMMITTING = ("commit",)

        @staticmethod
        def drive(*a, **k):
            return {"ok": True, "stopped_for_a_human": True,
                    "steps": [{"kind": "error"}]}

    monkeypatch.setattr(uiauto, "menu", _Menu)
    monkeypatch.setattr(uiauto, "dialog_driver", _Driver)
    monkeypatch.setattr(uiauto, "_altium_pid", lambda: (1234, None))

    out = await _tools()["app_run_ui_command"]("Tools|Something")

    assert out["ok"] is False, (
        "a command that raised something nobody could answer has not "
        "succeeded, whatever the driver's own ok says")
    assert out["dialogs"]["stopped_for_a_human"] is True


# --------------------------------------------------------------------
# A press that changed nothing is not a success.
#
# MEASURED 2026-08-18 against a wedged Altium. The dialog was
# "Comparator Results (No Differences)" with a single OK button, the
# window pumped WM_NULL and answered IsHungAppWindow with False, and it
# acted on nothing: a posted click, BM_CLICK, WM_COMMAND to the parent
# panel, VK_RETURN and even a real synthesized mouse click at the
# button's screen position all left it open.
#
# app_press_dialog_button returned ok true for every one of those. That
# is the same defect this session spent the day removing from
# app_run_menu and proj_sync_pcb, sitting in the tool written to
# replace them.
#
# Staying open is NORMAL for some presses, which is why closure alone
# cannot be the test: Validate Changes deliberately leaves the change
# order up, and a wizard page advances without closing its window. The
# verdict turns on the button's ROLE and on whether it was the only way
# out of the dialog.
# --------------------------------------------------------------------

def _dialog(buttons, title="Comparator Results (No Differences)"):
    return real_windows.Window(
        hwnd=99, class_name="TMessageForm", title=title, pid=1,
        controls=[real_windows.Control(hwnd=i, class_name="TXPBitBtn",
                                       text=t, style=0, enabled=True)
                  for i, t in enumerate(buttons, start=1)])


class _StuckWindows(_FakeWindows):
    """A screen where the press lands but nothing happens."""

    def wait_for_close(self, hwnd, timeout=5.0):
        return False


@pytest.mark.asyncio
async def test_a_sole_dismiss_that_leaves_the_dialog_open_is_a_failure(
        monkeypatch):
    """The exact live case, and the regression this file is named for."""
    fake = _StuckWindows([_dialog(["OK"])])
    monkeypatch.setattr(uiauto, "windows", fake)
    monkeypatch.setattr(uiauto, "_altium_pid", lambda: (1234, None))

    out = await _tools()["app_press_dialog_button"]("OK")

    assert fake.clicked == ["OK"], "the press must still be attempted"
    assert out["ok"] is False, (
        "the only button on the dialog was pressed and the dialog is "
        "still there; reporting success hides a wedged editor")
    assert out["dialog_closed"] is False
    assert "did not take" in out["reason"]


@pytest.mark.asyncio
async def test_a_dismiss_that_closes_the_dialog_is_a_success(monkeypatch):
    """The other direction, so the check cannot just always fail."""
    fake = _FakeWindows([_dialog(["OK"])])
    monkeypatch.setattr(uiauto, "windows", fake)
    monkeypatch.setattr(uiauto, "_altium_pid", lambda: (1234, None))

    out = await _tools()["app_press_dialog_button"]("OK")

    assert out["ok"] is True and out["dialog_closed"] is True
    assert out["outcome_verified"] is True


@pytest.mark.asyncio
async def test_validate_may_leave_the_change_order_open(monkeypatch):
    """Not every press is meant to close its dialog.

    Validate Changes is the case that makes closure the wrong test on
    its own: it deliberately leaves the order up so the result can be
    read, and judging it by the window going away would report a
    correct press as broken.
    """
    fake = _StuckWindows([_dialog(
        ["Validate Changes", "Execute Changes", "Close"],
        title="Engineering Change Order")])
    monkeypatch.setattr(uiauto, "windows", fake)
    monkeypatch.setattr(uiauto, "_altium_pid", lambda: (1234, None))

    out = await _tools()["app_press_dialog_button"]("Validate Changes")

    assert out["ok"] is True, "a validate press is not judged by closure"
    assert out["dialog_closed"] is False


@pytest.mark.asyncio
async def test_a_dismiss_with_another_way_out_is_not_condemned(monkeypatch):
    """Only the SOLE way out can be judged by the window closing.

    With more than one button the dialog may legitimately stay up while
    something else is chosen, so this must not be treated as a failure
    on the strength of closure alone.
    """
    fake = _StuckWindows([_dialog(["OK", "Cancel"])])
    monkeypatch.setattr(uiauto, "windows", fake)
    monkeypatch.setattr(uiauto, "_altium_pid", lambda: (1234, None))

    out = await _tools()["app_press_dialog_button"]("OK")

    assert out["ok"] is True


def test_a_posted_click_aims_at_the_middle_of_the_control():
    """lParam 0 is the top-left CORNER, which a VCL button may miss.

    The same mistake put a click at a grid's origin and selected its
    first row, which is recorded in select_row's own guard.
    """
    import inspect

    source = inspect.getsource(real_windows.click)
    assert "GetClientRect" in source, (
        "the press must compute a point inside the control")
    assert "lparam" in source
