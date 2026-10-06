# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Synthesised input must never reach another application.

Every other call in ui/ is addressed to a window handle and physically
cannot go astray. keybd_event and mouse_event are different: they are
delivered to whatever is ACTIVE at the instant they fire, so an event
meant for Altium lands in whatever the user switched to.

The rule these tests hold to is that no such event is emitted without a
foreground check IMMEDIATELY BEFORE IT, including between a key press
and its release, because focus moves between one event and the next.
"""
from __future__ import annotations

import inspect
import re

import pytest

from eda_agent.ui import controls, menu
from eda_agent.ui import windows as win

#: The raw Win32 calls that put input into the system.
_SYNTHESIS = re.compile(r"user32\.(keybd_event|mouse_event)\(")
#: What counts as a check. require_foreground refocuses and confirms.
_GUARD = re.compile(r"require_foreground\(")


def _emitting_functions(module):
    """Every function in a module that synthesises input."""
    out = []
    for name, obj in vars(module).items():
        if not inspect.isfunction(obj):
            continue
        try:
            source = inspect.getsource(obj)
        except OSError:                          # pragma: no cover - guard
            continue
        if _SYNTHESIS.search(source):
            out.append((name, source))
    return out


@pytest.mark.parametrize("module", [win, controls, menu],
                         ids=["windows", "controls", "menu"])
def test_every_synthesised_event_is_preceded_by_a_check(module):
    """No keybd_event or mouse_event without a guard above it.

    Checked per LINE rather than per function: a function that guards
    once at the top and then emits five events in a loop satisfies a
    naive "is the guard mentioned" test while still firing four events
    it never checked.
    """
    offenders = []
    for name, source in _emitting_functions(module):
        lines = source.splitlines()
        for i, line in enumerate(lines):
            if not _SYNTHESIS.search(line):
                continue
            # A guard anywhere in the preceding few lines, which is what
            # "immediately before" means once the try/except and the
            # cursor restore are counted.
            window = "\n".join(lines[max(0, i - 8):i])
            if not _GUARD.search(window):
                offenders.append(f"{module.__name__}.{name}:{i + 1}  {line.strip()}")
    assert not offenders, (
        "these events are emitted with no foreground check before them, "
        "so they can be delivered to another application:\n  "
        + "\n  ".join(offenders))


def test_the_guard_refocuses_rather_than_only_refusing():
    """A bare refusal would make this unusable.

    Focus moves for ordinary reasons between events. If the guard only
    raised, any operation the user glanced away from would fail, and the
    pressure would be to remove the guard rather than to fix the focus.
    """
    source = inspect.getsource(win.require_foreground)
    assert "_force_foreground" in source
    assert "ShowWindow" in source, (
        "a minimized target has to be restored before it can be raised")

    raising = inspect.getsource(win._force_foreground)
    assert "SetForegroundWindow" in raising
    assert "AttachThreadInput" in raising, (
        "a bare SetForegroundWindow from a background process is denied "
        "SILENTLY, which is how bring_to_front used to report a success "
        "it had never confirmed")


def test_the_guard_compares_by_process_not_by_handle():
    """A dialog and the main frame are different windows, one app.

    Comparing handles would refuse every click aimed at a dialog, popup
    menu or panel, which is most of what this package does.
    """
    source = inspect.getsource(win.require_foreground)
    assert "window_pid" in source and "foreground_pid" in source


def test_a_missing_target_is_reported_not_ignored():
    """require_foreground must not pass silently on a dead window."""
    with pytest.raises(win.ForegroundLost):
        win.require_foreground(0, "a test event")


# --------------------------------------------------------------------
# Containment: this package drives ONE application and no other.
# --------------------------------------------------------------------

def test_coordinate_entry_points_check_the_point_is_over_the_app():
    """A foreground check alone does not confine a click.

    It proves the right application is ACTIVE. It says nothing about
    where the pointer is, and a coordinate outside that application, or
    over something floating above it, is delivered to whatever is at
    that point.
    """
    for func in (win.click_at, win.drag, menu._right_click):
        source = inspect.getsource(func)
        assert "require_point_in_app" in source, (
            f"{func.__name__} takes raw coordinates and does not confine "
            f"them to the target application")


def test_no_tool_accepts_a_window_handle_from_the_caller():
    """Handles must be resolved inside, never passed in.

    Every target in this package comes from dialogs(pid) or frame(pid),
    which are scoped to the one process. A tool taking an hwnd argument
    would let a caller address any window on the desktop and would
    bypass the containment above, which only covers coordinates.
    """
    from eda_agent.tools import uiauto

    source = inspect.getsource(uiauto)
    offenders = []
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped.startswith("async def app_"):
            continue
        if "hwnd" in stripped or "window_handle" in stripped:
            offenders.append(stripped)
    assert not offenders, (
        "these tools take a window handle from the caller: "
        + "; ".join(offenders))


def test_a_point_over_another_process_is_refused():
    """The refusal is real, not just a code path that exists."""
    with pytest.raises(win.ForeignTarget):
        win.require_point_in_app(0, 0, 0, "a test click")


# --------------------------------------------------------------------
# The kill switch.
# --------------------------------------------------------------------

def test_disabling_stops_input_but_not_reading(monkeypatch):
    """Off means no synthesised events. It does NOT mean blind.

    Reads stay available on purpose: app_list_open_dialogs and the
    modal detection built on it are how the rest of the system notices
    that Altium is blocked. Turning off automation to be safer must not
    remove the checks that keep it safe.
    """
    monkeypatch.setenv(win.UI_AUTOMATION_ENV, "0")
    assert win.automation_enabled() is False
    assert win.enumerate_windows() is not None

    with pytest.raises(win.AutomationDisabled):
        win.send_keys("escape")
    with pytest.raises(win.AutomationDisabled):
        win.click_at(5, 5)


def test_it_is_on_by_default(monkeypatch):
    monkeypatch.delenv(win.UI_AUTOMATION_ENV, raising=False)
    assert win.automation_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "disabled",
                                   "FALSE", " Off "])
def test_the_off_spellings_all_work(monkeypatch, value):
    monkeypatch.setenv(win.UI_AUTOMATION_ENV, value)
    assert win.automation_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", ""])
def test_anything_else_leaves_it_on(monkeypatch, value):
    """Ambiguity resolves to ON, because the flag is opt-OUT.

    A typo must not silently disable the automation and leave a caller
    wondering why every tool refuses.
    """
    monkeypatch.setenv(win.UI_AUTOMATION_ENV, value)
    assert win.automation_enabled() is True


def test_the_switch_is_read_at_call_time(monkeypatch):
    """Import-time reads cannot be turned off in a running server."""
    monkeypatch.delenv(win.UI_AUTOMATION_ENV, raising=False)
    assert win.automation_enabled() is True
    monkeypatch.setenv(win.UI_AUTOMATION_ENV, "0")
    assert win.automation_enabled() is False
    monkeypatch.setenv(win.UI_AUTOMATION_ENV, "1")
    assert win.automation_enabled() is True
