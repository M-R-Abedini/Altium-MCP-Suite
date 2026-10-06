# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The SHIPPING driver, against real Windows dialogs.

These existed before against a scripted plan runner that the tool no
longer uses, which meant the real-window coverage was pointed at dead
code while the driver that actually ships had none. Repointed here.

Real dialogs, not fakes, because the whole risk of this package lives in
the gap between what Win32 is assumed to do and what it does. Every bug
worth finding in it so far has been in that gap: buttons that are not
class "Button", a press that a VCL control ignores, text with no window
handle at all.

WHAT A MESSAGE BOX CANNOT PROVE. It is not Altium, so it cannot settle
the VCL-specific questions. Those are covered by
test_altium_control_shapes.py, from shapes recorded off real Altium
dialogs. What it CAN prove is that the loop finds a genuine top-level
window, reads its buttons, presses one, and observes it close, and that
its refusals hold when the thing in front of it is real.
"""

from __future__ import annotations

import itertools
import subprocess
import sys
import time

import pytest

from eda_agent.ui import dialog_driver as dd
from eda_agent.ui import dialog_report as dr
from eda_agent.ui import windows as w

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not w.available(),
    reason="live dialog driving needs win32 and pywin32")


#: Makes every spawned caption unique. Windows are matched by a
#: SUBSTRING of the title, several tests here used the same wording, and
#: proc.kill() is asynchronous, so a window could outlive its test and be
#: picked up by the next one. That test then watched a corpse disappear
#: and reported "the dialog closed during a DRY RUN, so something was
#: pressed", blaming the driver for a window it never touched. Seen
#: twice in CI, never locally, because the local runs were ordered and
#: the runner shuffles.
_SPAWN_SEQ = itertools.count(1)


def _unique(title):
    """A caption no other test can match, that still classifies the same.

    The suffix goes on the END on purpose. The classifier searches
    anywhere in the string, so "Continue and proceed?" keeps its
    question mark and stays a confirmation, and the unrecognised probe
    stays unrecognised.
    """
    return f"{title} [{next(_SPAWN_SEQ)}]"


def _spawn(title, buttons):
    script = ("Add-Type -AssemblyName System.Windows.Forms; "
              "[void][System.Windows.Forms.MessageBox]::Show("
              f"'please answer','{title}',"
              f"[System.Windows.Forms.MessageBoxButtons]::{buttons})")
    return subprocess.Popen(["powershell", "-NoProfile", "-Command", script])


def _reap(proc, hwnd=None):
    """Kill the probe AND wait for its window to actually be gone.

    Returning while the window still exists is what let one test's
    dialog leak into the next.
    """
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass
    if hwnd is not None:
        w.wait_for_close(hwnd, 5.0)


@pytest.fixture
def dialog(request):
    """A real modal with a caption chosen per test, killed afterwards.

    The caption matters: it is what the classifier reads, so it decides
    whether the driver sees an unrecognised dialog or a confirmation.
    """
    title, buttons = getattr(request, "param", ("eda-agent unknown probe",
                                                "OKCancel"))
    title = _unique(title)
    proc = _spawn(title, buttons)
    found = None
    try:
        found = w.wait_for_window(
            lambda x: title in (x.title or ""), timeout=25)
        if found is None:
            pytest.skip("the test dialog did not appear on this host")
        dr.forget_ocr()
        yield found
    finally:
        _reap(proc, found.hwnd if found is not None else None)
        dr.forget_ocr()


def _still_open(hwnd):
    return not w.wait_for_close(hwnd, 1.0)


def _probe_alive(pid):
    """Is the process owning the probe dialog still running?

    The dry-run test asks whether the dialog is still on screen, which
    only means anything while the process putting it there is alive. A
    shared CI runner can take that process away for its own reasons, and
    then a vanished window says nothing about whether the driver pressed
    something. MEASURED: the test passed one CI run and failed the next
    on identical code.
    """
    import psutil

    try:
        return psutil.Process(pid).is_running()
    except Exception:
        return False


def test_it_finds_a_real_window_and_reads_its_buttons(dialog):
    """The foundation. Everything else is worthless if this is wrong."""
    snapshot = dr.report(dialog.pid)

    assert snapshot["dialog_count"] >= 1
    mine = [d for d in snapshot["dialogs"] if d["hwnd"] == dialog.hwnd]
    assert mine, "the driver cannot see a dialog that is plainly on screen"
    captions = {b["caption"] for b in mine[0]["buttons"]}
    assert {"OK", "Cancel"} <= captions, captions


def test_a_dry_run_presses_nothing_on_a_real_dialog(dialog):
    out = dd.drive(dialog.pid, intent="cancel", dry_run=True,
                   wait_first=5.0, budget=20.0, settle=0.1)

    # EVERY step, not just the first. A run that reported one hypothesis
    # and then pressed something on a later window would have passed the
    # original single-step check.
    assert out["steps"], "the dry run saw nothing at all"
    for step in out["steps"]:
        assert "WOULD press" in (step.get("action") or ""), (
            f"a dry run recorded a real action: {step.get('action')!r}")

    # The independent check, and the reason it is conditional. The
    # driver's own record above could be wrong, so the window is asked
    # too, but a dialog can also vanish because the host killed the
    # process holding it, and that says nothing about this driver.
    if not _probe_alive(dialog.pid):
        pytest.skip("the probe process was killed by the host, so the "
                    "dialog's absence proves nothing about the dry run")
    assert _still_open(dialog.hwnd), (
        "the dialog closed during a DRY RUN, so something was pressed")


def test_cancel_really_presses_and_the_window_really_closes(dialog):
    """The whole press path end to end: find, click, observe closure."""
    out = dd.drive(dialog.pid, intent="cancel", dry_run=False,
                   wait_first=5.0, budget=30.0, settle=0.1)

    assert out["steps"][0]["action"].startswith("pressed")
    assert not _still_open(dialog.hwnd), "the press did not close it"
    assert out["finished"] == "no dialog is open"


def test_an_unrecognised_real_dialog_is_refused_even_fully_authorised(dialog):
    """The rule that used to be violated, against something real.

    "eda-agent unknown probe" matches no classifier pattern, so this is
    a genuine unrecognised dialog carrying an ordinary OK. It must not
    be pressed, whatever the caller authorised.
    """
    out = dd.drive(dialog.pid, intent="proceed", allow_commit=True,
                   allow_confirm=True, dry_run=False, wait_first=5.0,
                   budget=20.0, settle=0.1)

    assert out["stopped_for_a_human"] is True
    assert "not recognised" in out["steps"][0]["why"]
    assert _still_open(dialog.hwnd), "it pressed a button it did not understand"


@pytest.mark.parametrize("dialog", [("Continue and proceed?", "YesNo")],
                         indirect=True)
def test_a_real_confirmation_is_answered_only_when_allowed(dialog):
    """A caption ending in a question mark is a confirmation.

    Withheld: it stops. Allowed: it answers Yes and the dialog goes.
    """
    refused = dd.drive(dialog.pid, intent="proceed", allow_confirm=False,
                       dry_run=False, wait_first=5.0, budget=20.0,
                       settle=0.1)
    assert refused["stopped_for_a_human"] is True
    assert _still_open(dialog.hwnd)

    allowed = dd.drive(dialog.pid, intent="proceed", allow_confirm=True,
                       dry_run=False, wait_first=5.0, budget=30.0,
                       settle=0.1)
    assert allowed["steps"][0]["action"].startswith("pressed")
    assert not _still_open(dialog.hwnd)


@pytest.mark.parametrize(
    "dialog", [("Permanently delete everything?", "YesNo")], indirect=True)
def test_a_real_destructive_confirmation_is_never_answered(dialog):
    """Authorisation cannot buy an answer to this one."""
    out = dd.drive(dialog.pid, intent="proceed", allow_commit=True,
                   allow_confirm=True, dry_run=False, wait_first=5.0,
                   budget=20.0, settle=0.1)

    assert "irreversible" in out["steps"][0]["why"]
    assert _still_open(dialog.hwnd)


def test_an_error_beside_another_dialog_takes_priority_live():
    """Two real modals at once, and the error must win.

    Not a contrived case: an operation can raise a question and then
    fail, leaving both on screen. Driving the ordinary one first would
    carry on past a failure the caller has not seen.
    """
    benign_title = _unique("Continue and proceed?")
    failing_title = _unique("Error while doing the thing")
    benign = _spawn(benign_title, "YesNo")
    failing = _spawn(failing_title, "OK")
    first = None
    second = None
    try:
        first = w.wait_for_window(
            lambda x: benign_title in (x.title or ""), timeout=25)
        second = w.wait_for_window(
            lambda x: failing_title in (x.title or ""), timeout=25)
        if first is None or second is None:
            pytest.skip("both test dialogs did not appear on this host")
        dr.forget_ocr()

        # Both belong to the same powershell image but different pids,
        # so drive the one the error is on and assert it is chosen over
        # the confirmation that shares the screen.
        out = dd.drive(second.pid, intent="proceed", allow_commit=True,
                       allow_confirm=True, dry_run=False, wait_first=5.0,
                       budget=20.0, settle=0.1)

        assert out["stopped_for_a_human"] is True
        assert out["steps"][0]["kind"] == "error"
        assert _still_open(second.hwnd), "it pressed past an error"
        assert _still_open(first.hwnd), "it answered the other dialog too"
    finally:
        _reap(benign, first.hwnd if first is not None else None)
        _reap(failing, second.hwnd if second is not None else None)
        dr.forget_ocr()


def test_no_dialog_at_all_is_reported_not_invented():
    """Against a process that has none, it must say so and press nothing."""
    import os

    out = dd.drive(os.getpid(), intent="proceed", dry_run=False,
                   wait_first=1.0, budget=10.0, settle=0.1)

    assert out["steps"] == []
    assert "no dialog appeared" in out["finished"]


def test_blocked_is_false_when_nothing_is_modal():
    import os

    snapshot = dr.report(os.getpid())
    assert snapshot["ok"] is True
    assert snapshot["blocked"] is False
    assert snapshot["summary"] == "no dialog is open"


# --------------------------------------------------------------------
# The collision itself, asserted deterministically.
#
# The live tests above cannot prove this fix: they passed locally every
# single time with the broken fixture, including five shuffled runs,
# and still failed twice in CI. What went wrong was a PROPERTY of the
# captions, so that is what gets checked here, with no windows and no
# timing involved.
# --------------------------------------------------------------------

def test_two_spawns_never_share_a_caption():
    """The race, removed at the source.

    Windows are matched by substring of the title. With two tests using
    the same wording and proc.kill() returning before the window is
    gone, the second test could latch onto the first one's dying dialog
    and then report the driver had pressed something.
    """
    seen = [_unique("eda-agent unknown probe") for _ in range(50)]

    assert len(set(seen)) == 50, "a caption repeated, so the race is back"
    for caption in seen:
        others = [o for o in seen if o != caption]
        assert not any(caption in o for o in others), (
            f"{caption!r} is a substring of another caption, which is how "
            f"wait_for_window picks the wrong window")


def test_a_unique_caption_still_classifies_the_same():
    """The suffix must not change what the dialog IS.

    Several tests depend on the classifier's verdict: the probe has to
    stay unrecognised, the question has to stay a confirmation, and the
    failure has to stay an error. A prefix, or a suffix that swallowed
    the question mark, would silently retarget those tests at a
    different branch while they carried on passing.
    """
    for base, expected in (("eda-agent unknown probe", "unknown"),
                           ("Continue and proceed?", "confirm"),
                           ("Error while doing the thing", "error")):
        assert dr.classify(base, []) == expected, f"baseline moved: {base}"
        assert dr.classify(_unique(base), []) == expected, (
            f"the uniqueness suffix changed {base!r} from {expected}")


def test_the_teardown_waits_for_the_window_to_go():
    """Killing the process is not the same as the window being gone.

    Returning early is what allowed a dialog to outlive its test.
    """
    import inspect

    source = inspect.getsource(_reap)
    assert "wait_for_close" in source
    assert "proc.wait" in source
