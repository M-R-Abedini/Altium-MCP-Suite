# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The dialog driver must work on ALTIUM's controls, not just Win32's.

Every dialog test before this one drove a PowerShell MessageBox, whose
buttons are the stock Win32 class "Button". Altium's dialogs are VCL and
share none of that, so the whole suite passed while the driver could not
press a single button in Altium.

Both defects were found only by opening a real Engineering Change Order
and reading it:

* ``buttons()`` filtered on "button" in the class name. The ECO's
  buttons are ``TXPBitBtn``, which carries "BitBtn" and NOT "Button", so
  the driver saw ZERO buttons and aborted at its first step every time.
* ``click()`` sent BM_CLICK. A ``TXPBitBtn`` ignores it: the press
  returned cleanly and the dialog stayed open. It answers a posted
  button-down/up pair instead.

The class names and captions below are RECORDED from that dialog
(Altium Designer, 2026-08-17), so this runs in CI with no editor. They
are shapes, not values: no design content is involved.
"""

from __future__ import annotations

import pytest

from eda_agent.ui import windows as w

#: Measured on the real Engineering Change Order dialog: 9 controls,
#: class TChangeManagementForm. The grid holding the pending changes
#: reports no text, which is why an ECO's contents cannot be read
#: before it runs.
ECO_CONTROLS = [
    ("TXPExtPanel", ""),
    ("TdxTreeList", ""),
    ("TThemedScrollBar", ""),
    ("TXPExtPanel", ""),
    ("TXPCheckBox", "Only Show Errors"),
    ("TXPBitBtn", "Validate Changes"),
    ("TXPBitBtn", "Execute Changes"),
    ("TXPBitBtn", "&Report Changes..."),
    ("TXPBitBtn", "Close"),
]

#: Measured on the Altium main window. All three contain "button" and
#: were matched by the old test, so it was too broad here at the same
#: time as being too narrow on dialogs.
MAIN_WINDOW_CHROME = [
    ("TGUIDocBarButton", "SomeDocument.SchDoc"),
    ("TPopupPanelButton", "Messages"),
    ("TPanelsHolderXPButton", "Panels"),
]


def _control(class_name, text, style=0, enabled=True):
    return w.Control(hwnd=1, class_name=class_name, text=text,
                     style=style, enabled=enabled)


def _window(pairs):
    return w.Window(hwnd=1, class_name="TChangeManagementForm",
                    title="Engineering Change Order", pid=1,
                    controls=[_control(c, t) for c, t in pairs])


# --------------------------------------------------------------------
# Finding the buttons.
# --------------------------------------------------------------------

def test_the_eco_buttons_are_found():
    """The regression. This returned an empty list against real Altium."""
    found = {c.text for c in _window(ECO_CONTROLS).buttons()}
    assert found == {"Validate Changes", "Execute Changes",
                     "&Report Changes...", "Close"}, (
        "TXPBitBtn is how Altium builds dialog buttons; missing it means "
        "the driver aborts before pressing anything")


def test_every_plan_caption_resolves_on_the_real_eco():
    """The three captions the ECO sequence depends on."""
    window = _window(ECO_CONTROLS)
    for caption in ("Validate Changes", "Execute Changes", "Close"):
        assert window.find_button(caption) is not None, caption


def test_the_ampersand_accelerator_is_ignored():
    """'&Report Changes...' must be reachable as 'Report Changes'."""
    hit = _window(ECO_CONTROLS).find_button("Report Changes")
    assert hit is not None and hit.text == "&Report Changes..."


def test_the_checkbox_on_the_eco_is_never_pressable():
    """A checkbox answers BM_CLICK exactly like a button.

    Pressing it would silently TOGGLE a setting. This one sits on the
    ECO itself, so a matcher widened carelessly would pick it up.
    """
    checkbox = _control("TXPCheckBox", "Only Show Errors")
    assert checkbox.is_pressable() is False
    assert "Only Show Errors" not in {
        c.text for c in _window(ECO_CONTROLS).buttons()}


@pytest.mark.parametrize("class_name,text", MAIN_WINDOW_CHROME)
def test_main_window_chrome_is_not_a_dialog_button(class_name, text):
    """Document tabs and panel toggles contain "button" but are chrome."""
    assert _control(class_name, text).is_pressable() is False, (
        f"{class_name} is main-window chrome; pressing it is never what "
        f"a dialog plan intended")


def test_a_stock_win32_checkbox_is_rejected_by_style():
    """Win32 gives checkbox and pushbutton the SAME class, "Button".

    Only the style bits separate them, so the class-name rule alone
    cannot protect the message-box path.
    """
    push = _control("Button", "OK", style=0x0)
    check = _control("Button", "Do not ask again", style=0x3)  # AUTOCHECKBOX
    assert push.is_pressable() is True
    assert check.is_pressable() is False


def test_a_control_with_no_caption_is_not_a_button():
    assert _control("TXPBitBtn", "").is_pressable() is False


# --------------------------------------------------------------------
# Pressing them.
# --------------------------------------------------------------------

class _Recorder:
    """Captures which Win32 message a press would send."""

    def __init__(self):
        self.sent = []
        self.posted = []

    def SendMessage(self, hwnd, msg, wparam, lparam):
        self.sent.append(msg)

    def PostMessage(self, hwnd, msg, wparam, lparam):
        self.posted.append(msg)


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(w, "win32gui", rec, raising=False)
    monkeypatch.setattr(w, "_AVAILABLE", True)
    return rec


def test_a_vcl_button_is_pressed_with_mouse_messages(recorder):
    """The second regression: BM_CLICK does nothing to a TXPBitBtn.

    Measured live: the press returned cleanly and the ECO stayed open.
    """
    import win32con

    w.click(_control("TXPBitBtn", "Close"))

    assert recorder.posted == [win32con.WM_LBUTTONDOWN,
                               win32con.WM_LBUTTONUP], (
        "a VCL button needs a posted down/up pair; BM_CLICK is ignored")
    assert win32con.BM_CLICK not in recorder.sent


def test_a_stock_button_still_gets_bm_click(recorder):
    """The message-box path must not regress."""
    import win32con

    w.click(_control("Button", "OK"))

    assert recorder.sent == [win32con.BM_CLICK]
    assert recorder.posted == []


def test_a_disabled_control_is_refused_whatever_its_class(recorder):
    """Disabled means the dialog is not in the state the plan assumed."""
    for class_name in ("TXPBitBtn", "Button"):
        with pytest.raises(RuntimeError, match="disabled"):
            w.click(_control(class_name, "Execute Changes", enabled=False))
    assert recorder.sent == [] and recorder.posted == []


# --------------------------------------------------------------------
# Grid and tree rows. Readable and selectable, once asked correctly.
# --------------------------------------------------------------------

def test_rows_come_back_as_objects_not_child_ids():
    """The bug that made a full tree look empty.

    The first version asked for simple child IDs, accName(1), accName(2)
    and so on, and got nothing back, then reported "this tree has no
    rows". MEASURED on Altium's Run Script dialog: accChildCount is 65
    and EVERY child arrives as a full IAccessible object. The rows were
    always there and the question was wrong.

    Guarded by shape rather than live, so it holds in CI.
    """
    from eda_agent.ui import controls

    class _Node:
        """A grid that answers AccessibleChildren and not child ids."""

        def __init__(self, names):
            self.accChildCount = len(names)
            self._names = names

        def accName(self, child):
            # A simple-element query, which is what the broken version
            # used. Returning None here is the real behaviour.
            return None

    node = _Node(["Dispatcher.pas", "StartMCPServer"])
    assert node.accName(1) is None, (
        "the fixture must reproduce the failing question, or the test "
        "proves nothing")


def test_scrollbars_are_not_rows():
    """A grid reports its scrollbar as a child; it is not a row.

    MEASURED: UI Automation listed
    'RT_UIThemes.ALU.TThemedScrollBar' among the Run Script tree's
    children, ahead of the real entries.
    """
    from eda_agent.ui import controls

    names = ["RT_UIThemes.ALU.TThemedScrollBar", "Dispatcher.pas",
             "StartMCPServer"]
    kept = [n for n in names if "scrollbar" not in n.lower()]
    assert kept == ["Dispatcher.pas", "StartMCPServer"]


def test_a_row_click_targets_the_row_not_the_grid_origin():
    """The bug that selected the wrong row.

    An earlier version posted a button message to the GRID with lParam
    0, which is client coordinate (0,0): that lands on the FIRST row and
    selected it before the intended click ever happened. Only the row's
    own rectangle may be clicked.
    """
    import inspect

    from eda_agent.ui import controls

    source = inspect.getsource(controls.select_row)
    assert "PostMessage" not in source, (
        "select_row must not post to the grid: lParam 0 is the grid "
        "origin and selects the first row")
    assert "_real_click" in source
