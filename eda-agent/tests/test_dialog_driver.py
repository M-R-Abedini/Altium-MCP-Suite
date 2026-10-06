# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Deciding what to press from what is on screen, and refusing to guess.

The scripted predecessor failed on live Altium twice in one session, and
both failures were the same shape: a dialog appeared that the script did
not list, so it waited for one that was never coming while the unlisted
modal blocked the editor. A longer script would only move the problem to
the next unlisted dialog, so the order now comes from the editor.

What has to be guarded is therefore not "does it press the right thing
in the right order" but the judgement:

* it never presses a button whose meaning it does not know
* it never commits a change to a design without explicit authorisation
* it answers a routine "carry on?" but never an irreversible one
* it distinguishes "nothing has appeared yet" from "nothing is left"

Every dialog shape below is recorded from real Altium.
"""

from __future__ import annotations

import pytest

from eda_agent.ui import dialog_driver as dd

#: Recorded live. Altium asks this BEFORE every change order, and the
#: scripted driver did not know it existed.
CONFIRM_ECO = {
    "kind": "confirm", "hwnd": 1,
    "title": "Comparator Results (2 Differences)",
    "message": [],
    "ocr_text": ["1 of the 2 differences detected can be resolved by an "
                 "automatically generated ECO.", "Continue and create ECO?"],
    "buttons": [{"caption": "&Yes", "enabled": 1},
                {"caption": "&No", "enabled": 1},
                {"caption": "Cancel", "enabled": 1}],
    "needs_a_human": True,
}

ECO = {
    "kind": "engineering_change_order", "hwnd": 2,
    "title": "Engineering Change Order", "message": [],
    "buttons": [{"caption": "Validate Changes", "enabled": 1},
                {"caption": "Execute Changes", "enabled": 1},
                {"caption": "&Report Changes...", "enabled": 1},
                {"caption": "Close", "enabled": 1}],
    "needs_a_human": False,
}

NOTHING_TO_DO = {
    "kind": "nothing_to_do", "hwnd": 3,
    "title": "Comparator Results (No Differences)", "message": [],
    "ocr_text": ["No Differences Detected"],
    "buttons": [{"caption": "OK", "enabled": 1}],
    "needs_a_human": False,
}

ERROR = {
    "kind": "error", "hwnd": 4, "title": "Error",
    "message": ["Cannot compare a source document against its owner "
                "project SCH"],
    "buttons": [{"caption": "OK", "enabled": 1}],
    "needs_a_human": True,
}

UNKNOWN = {
    "kind": "unknown", "hwnd": 5, "title": "Something New", "message": [],
    "buttons": [{"caption": "Frobnicate", "enabled": 1}],
    "needs_a_human": False,
}


# --------------------------------------------------------------------
# Reading a button's meaning.
# --------------------------------------------------------------------

@pytest.mark.parametrize("caption,role", [
    ("Validate Changes", "validate"),
    ("Execute Changes", "commit"),
    ("Accept Changes (Create ECO)", "commit"),
    ("Close", "dismiss"), ("Cancel", "dismiss"), ("&No", "dismiss"),
    ("&Yes", "advance"), ("Next", "advance"), ("Finish", "advance"),
    ("OK", "advance"),
    ("&Report Changes...", "report"),
    ("Frobnicate", None),
])
def test_button_meaning_is_read_from_the_caption(caption, role):
    assert dd.role_of(caption) == role


def test_validate_is_not_mistaken_for_a_plain_advance():
    """Longest match wins, or 'Validate Changes' could read as advance
    and the driver would commit without validating."""
    assert dd.role_of("Validate Changes") == "validate"


def test_disabled_buttons_are_not_offered():
    dialog = {"buttons": [{"caption": "Execute Changes", "enabled": 0},
                          {"caption": "Close", "enabled": 1}]}
    assert dd.buttons_by_role(dialog) == {"dismiss": ["Close"]}


# --------------------------------------------------------------------
# The judgement.
# --------------------------------------------------------------------

def test_an_error_always_stops(monkeypatch):
    caption, why = dd.decide(ERROR, "proceed", True, set(), allow_confirm=True)
    assert caption is None
    assert "stopping" in why.lower()


def test_an_unrecognised_dialog_stops_rather_than_guessing():
    caption, why = dd.decide(UNKNOWN, "proceed", True, set(),
                             allow_confirm=True)
    assert caption is None
    assert "refusing to guess" in why.lower()


@pytest.mark.parametrize("caption", ["OK", "Next", "Yes", "Finish"])
def test_an_unrecognised_dialog_is_not_advanced_by_a_familiar_button(caption):
    """Recognising the BUTTON is not recognising the DIALOG.

    This is the hole the first version had: an unknown dialog offering
    an ordinary "OK" was advanced, because the code fell through to the
    generic advance branch and only refused when no caption was
    familiar. But "OK" is safe only when the question is known, and on
    an unrecognised dialog it is not: it could be a settings sheet, a
    licence prompt, or a destructive action wearing an ordinary button.
    """
    dialog = dict(UNKNOWN,
                  buttons=[{"caption": caption, "enabled": 1}])

    chosen, why = dd.decide(dialog, "proceed", True, set(),
                            allow_confirm=True)

    assert chosen is None, (
        f"pressed {caption!r} on a dialog it does not recognise")
    assert "not recognised" in why


def test_an_unrecognised_dialog_still_reports_what_it_offered():
    """So a human can decide, without going to look at the screen."""
    dialog = dict(UNKNOWN, buttons=[{"caption": "Rebuild", "enabled": 1},
                                    {"caption": "Skip", "enabled": 1}])
    _caption, why = dd.decide(dialog, "proceed", True, set())
    assert "Rebuild" in why and "Skip" in why


def test_cancel_can_still_escape_an_unrecognised_dialog():
    """Refusing to advance must not mean being unable to back out.

    Otherwise an unrecognised dialog becomes a trap: nothing may press
    it forward and nothing may dismiss it, leaving the editor blocked.
    """
    dialog = dict(UNKNOWN, buttons=[{"caption": "Cancel", "enabled": 1}])
    caption, why = dd.decide(dialog, "cancel", False, set())
    assert caption == "Cancel"
    assert "backing out" in why


#: Recorded from the real Tools > Update From Libraries wizard.
#: Note Finish is listed BEFORE Next, which is what made the arbitrary
#: choice the wrong one.
UPDATE_WIZARD = {
    "kind": "wizard", "hwnd": 11, "title": "Update From Library",
    "message": [], "needs_a_human": False,
    "buttons": [{"caption": c, "enabled": 1} for c in
                ["&Advanced...", "&Choose Component...",
                 "Return Selected to &Default", "&Parameters Changes...",
                 "&Finish", "Cancel", "&Next >", "< &Back"]],
}


def test_a_wizard_is_stepped_through_not_short_circuited():
    """Next beats Finish, whatever order the dialog lists them in.

    MEASURED on the real wizard: its buttons arrive with Finish before
    Next, so taking the first advance-role button pressed Finish on page
    one. That skips every remaining configuration page and accepts
    whatever defaults happened to be showing.
    """
    caption, _why = dd.decide(UPDATE_WIZARD, "proceed", False, set())
    assert caption == "&Next >", (
        "pressing Finish while Next is offered abandons the pages in "
        "between")


def test_finish_is_used_when_it_is_the_only_way_forward():
    """The preference must not deadlock the last page."""
    last = dict(UPDATE_WIZARD,
                buttons=[{"caption": "&Finish", "enabled": 1},
                         {"caption": "Cancel", "enabled": 1}])
    caption, _why = dd.decide(last, "proceed", False, set())
    assert caption == "&Finish"


def test_only_the_wizard_buttons_carry_a_role():
    """Advanced, Choose Component and Back must mean nothing here.

    'Advanced...' in particular is close enough to 'advance' to be worth
    asserting: it opens a settings sheet, and pressing it mid-run would
    wander off the path.
    """
    roles = dd.buttons_by_role(UPDATE_WIZARD)
    assert set(roles) == {"advance", "dismiss"}
    assert sorted(roles["advance"]) == ["&Finish", "&Next >"]
    assert roles["dismiss"] == ["Cancel"]


def test_a_recognised_wizard_is_still_advanced():
    """The refusal must be scoped to UNKNOWN, not to everything."""
    wizard = {"kind": "wizard", "hwnd": 9, "title": "Update From Libraries",
              "message": [], "needs_a_human": False,
              "buttons": [{"caption": "Next", "enabled": 1}]}
    caption, _why = dd.decide(wizard, "proceed", False, set())
    assert caption == "Next"


def test_committing_needs_explicit_authorisation():
    caption, why = dd.decide(ECO, "proceed", False, {"validate"})
    assert caption is None
    assert "EXECUTE" in why


def test_the_eco_order_is_derived_not_scripted():
    """Validate, then commit, then close: chosen from what is offered
    and what has already been pressed on this window."""
    seen, chosen = set(), []
    for _ in range(3):
        caption, _why = dd.decide(ECO, "proceed", True, seen)
        chosen.append(caption)
        seen.add(dd.role_of(caption))
    assert chosen == ["Validate Changes", "Execute Changes", "Close"]


def test_an_eco_without_a_validate_button_still_commits():
    """The order is not a fixed list: a dialog that offers no Validate
    must not deadlock waiting to press one."""
    eco = dict(ECO, buttons=[{"caption": "Execute Changes", "enabled": 1},
                             {"caption": "Close", "enabled": 1}])
    caption, _ = dd.decide(eco, "proceed", True, set())
    assert caption == "Execute Changes"


def test_nothing_to_do_is_dismissed_and_is_not_a_failure():
    caption, why = dd.decide(NOTHING_TO_DO, "proceed", False, set())
    assert caption == "OK"
    assert "no work" in why


def test_a_routine_confirmation_is_answered_only_when_allowed():
    assert dd.decide(CONFIRM_ECO, "proceed", True, set(),
                     allow_confirm=False)[0] is None
    assert dd.decide(CONFIRM_ECO, "proceed", True, set(),
                     allow_confirm=True)[0] == "&Yes"


def test_an_irreversible_confirmation_is_never_answered():
    """No authorisation flag may answer this on a human's behalf."""
    destructive = dict(CONFIRM_ECO, ocr_text=["Delete 12 objects?"],
                       message=["This cannot be undone"])
    caption, why = dd.decide(destructive, "proceed", True, set(),
                             allow_confirm=True)
    assert caption is None
    assert "irreversible" in why


def test_the_destructive_check_reads_ocr_text_too():
    """The question is painted, so ignoring OCR would miss the warning."""
    painted = dict(CONFIRM_ECO, message=[],
                   ocr_text=["Permanently delete the selected nets?"])
    assert dd.is_answerable_confirmation(painted) is False


def test_cancel_intent_backs_out_of_anything():
    caption, why = dd.decide(ECO, "cancel", True, set())
    assert caption == "Close"
    assert "backing out" in why


# --------------------------------------------------------------------
# The loop.
# --------------------------------------------------------------------

class _Screen:
    """A scripted sequence of screens, standing in for the editor."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.clicked = []

    def report(self, pid):
        frame = self.frames[0] if len(self.frames) == 1 else self.frames.pop(0)
        return {"ok": True, "pid": pid, "blocked": bool(frame),
                "dialog_count": len(frame), "dialogs": frame,
                "needs_a_human": any(d["needs_a_human"] for d in frame),
                "summary": "", "kinds": [d["kind"] for d in frame]}


@pytest.fixture
def screen(monkeypatch):
    def install(frames):
        scr = _Screen(frames)
        monkeypatch.setattr(dd.win, "available", lambda: True)
        monkeypatch.setattr(dd.report, "report", scr.report)

        class _Btn:
            def __init__(self, caption):
                self.text = caption

            def describe(self):
                return self.text

        class _Win:
            def find_button(self, caption):
                return _Btn(caption)

        monkeypatch.setattr(dd.win, "capture", lambda hwnd: _Win())
        monkeypatch.setattr(dd.win, "click",
                            lambda b: scr.clicked.append(b.text))
        monkeypatch.setattr(dd.win, "wait_for_close",
                            lambda hwnd, timeout: True)
        return scr
    return install


def test_a_dry_run_presses_nothing(screen):
    scr = screen([[ECO], []])
    out = dd.drive(1, allow_commit=True, dry_run=True)
    assert scr.clicked == []
    assert "WOULD press" in out["steps"][0]["action"]
    assert out["committed"] is False


def test_the_full_live_sequence_is_driven_without_a_plan(screen):
    """The exact sequence measured on live Altium, decided step by step."""
    scr = screen([[CONFIRM_ECO], [ECO], [ECO], [ECO], []])

    out = dd.drive(1, allow_commit=True, allow_confirm=True, dry_run=False,
                   settle=0)

    assert scr.clicked == ["&Yes", "Validate Changes", "Execute Changes",
                           "Close"]
    assert out["committed"] is True
    assert out["ok"] is True
    assert out["finished"] == "no dialog is open"


def test_it_waits_for_the_first_dialog_instead_of_declaring_victory(screen):
    """A caller fires an operation and THEN drives.

    Measured: Altium's compare needs a moment, and exiting on the first
    empty look reported "nothing to do" while the work was still
    starting.
    """
    scr = screen([[], [], [NOTHING_TO_DO], []])

    out = dd.drive(1, dry_run=False, wait_first=5.0, settle=0)

    assert scr.clicked == ["OK"], "it must not give up before the dialog"
    assert out["finished"] == "no dialog is open"


def test_an_empty_screen_after_a_press_ends_the_run(screen):
    scr = screen([[NOTHING_TO_DO], []])
    out = dd.drive(1, dry_run=False, wait_first=60.0, settle=0)
    assert out["finished"] == "no dialog is open"
    assert scr.clicked == ["OK"]


def test_nothing_appearing_at_all_is_reported_as_such(screen):
    screen([[]])
    out = dd.drive(1, dry_run=False, wait_first=0.2, settle=0)
    assert "no dialog appeared" in out["finished"]


def test_an_error_stops_the_run_and_flags_a_human(screen):
    scr = screen([[ERROR]])
    out = dd.drive(1, allow_commit=True, allow_confirm=True, dry_run=False,
                   settle=0)
    assert scr.clicked == []
    assert out["stopped_for_a_human"] is True
    assert out["ok"] is False


def test_an_error_beside_another_dialog_takes_priority(screen):
    scr = screen([[ECO, ERROR]])
    out = dd.drive(1, allow_commit=True, dry_run=False, settle=0)
    assert scr.clicked == [], "the ECO must not be driven past an error"
    assert out["stopped_for_a_human"] is True


def test_a_dialog_that_never_changes_cannot_loop_forever(screen):
    scr = screen([[NOTHING_TO_DO]])
    out = dd.drive(1, dry_run=False, max_presses=4, settle=0)
    assert len(scr.clicked) == 4
    assert "keeps reappearing" in out["finished"]


PROGRESS = {
    "kind": "progress", "hwnd": 7, "title": "Compiling", "message": [],
    "buttons": [], "needs_a_human": False,
}


def test_a_progress_dialog_is_waited_on_not_pressed(screen):
    """The editor is working. Pressing anything would interrupt it.

    A progress dialog often has no button at all, or only Abort, and
    neither is something to press on the caller's behalf.
    """
    scr = screen([[PROGRESS], [PROGRESS], [NOTHING_TO_DO], []])

    out = dd.drive(1, dry_run=False, wait_first=30.0, settle=0)

    assert scr.clicked == ["OK"], "it must wait, then handle what follows"
    waited = [s for s in out["steps"] if s["action"] == "waited"]
    assert len(waited) == 2
    assert "editor is working" in waited[0]["why"]


def test_waiting_cannot_outlast_the_budget(screen):
    """A progress dialog that never finishes must not hold forever."""
    screen([[PROGRESS]])

    out = dd.drive(1, dry_run=False, wait_first=30.0, budget=0.6, settle=0)

    assert "gave up after" in out["finished"]
    assert "still" in out["finished"]


def test_a_dialog_that_vanishes_mid_press_is_not_an_error(screen,
                                                          monkeypatch):
    """A race, and an ordinary one: dialogs close while being read.

    Between deciding and pressing, the window can go, because the
    editor moved on or a human clicked it. That must be recorded and
    retried against whatever is on screen now, never reported as a
    failed press.
    """
    scr = screen([[NOTHING_TO_DO], [NOTHING_TO_DO], []])
    real_capture = dd.win.capture
    calls = []

    def vanishing(hwnd):
        calls.append(hwnd)
        return None if len(calls) == 1 else real_capture(hwnd)

    monkeypatch.setattr(dd.win, "capture", vanishing)

    out = dd.drive(1, dry_run=False, wait_first=30.0, settle=0)

    actions = [s["action"] for s in out["steps"]]
    assert actions[0] == "vanished"
    assert "changed between reading it and pressing" in out["steps"][0]["why"]
    assert scr.clicked == ["OK"], "it must go on to press the next one"
    assert out["ok"] is True


def test_every_step_records_what_was_seen_and_why(screen):
    screen([[CONFIRM_ECO], []])
    out = dd.drive(1, allow_confirm=True, dry_run=False, settle=0)
    step = out["steps"][0]
    for field in ("title", "kind", "says", "offers", "decision", "why",
                  "action"):
        assert field in step, field
    assert step["says"], "the recovered text must be recorded"


#: The board wizard's RESULTS list, recorded live. Not a change order,
#: but its forward action creates one, so it needs the same gate.
CHANGE_PREVIEW = {
    "kind": "change_preview", "hwnd": 21,
    "title": "Update From PCB Libraries [Board.PcbDoc]",
    "message": [], "needs_a_human": False,
    "buttons": [{"caption": c, "enabled": 1} for c in
                ["Update All", "Accept Changes (Create ECO)", "Close",
                 "Create &Report"]],
}


def test_the_board_results_list_is_gated_like_a_change_order():
    """Without this it fell through and reported "offers nothing".

    The generic advance path found no Next/OK/Finish, so a perfectly
    good commit button sat there ungated and unreachable, and the run
    stopped with a message that read like the dialog was unusable.
    """
    refused, why = dd.decide(CHANGE_PREVIEW, "proceed", False, set())
    assert refused is None
    assert "EXECUTE" in why, why

    allowed, _ = dd.decide(CHANGE_PREVIEW, "proceed", True, set())
    assert allowed == "Accept Changes (Create ECO)"


def test_update_all_is_not_mistaken_for_an_action():
    """'Update All' selects rows; it is not the forward action."""
    roles = dd.buttons_by_role(CHANGE_PREVIEW)
    assert roles.get("commit") == ["Accept Changes (Create ECO)"]
    assert "Update All" not in sum(roles.values(), [])


def test_the_board_results_list_can_be_backed_out_of():
    caption, why = dd.decide(CHANGE_PREVIEW, "cancel", False, set())
    assert caption == "Close"
    assert "backing out" in why
