# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""React to whatever dialog is actually on screen. No scripted sequence.

WHY THE SCRIPTED VERSION FAILED. A fixed plan encodes a belief about
which dialogs appear and in what order, and that belief is wrong the
first time the editor takes a different path. Measured, in one session:

* with the design already in sync, Update PCB shows "Comparator Results
  (No Differences)" and NO change order. The plan waited out its
  timeout for an ECO that was never coming, while that modal blocked
  the editor.
* with real differences, Altium first asks "Continue and create ECO?"
  on a Yes/No box. The plan knew nothing about it, so it waited for the
  ECO while the question sat unanswered in front of it.

Neither is an exotic case. Both are the normal behaviour of the feature
the plan was written for, and a longer plan would only postpone the
problem to the next unlisted dialog.

WHAT THIS DOES INSTEAD. Loop: look at what is open, classify it, choose
a button by the ROLE its caption plays, press, look again. The order
comes from the editor, not from a list here.

WHAT IT REFUSES TO DO. It stops rather than improvising when a dialog
reports an error, asks something it was not sent to answer, or offers
no button whose role it recognises. Pressing an unrecognised button in
a PCB tool is the accident this whole package exists to avoid.
"""

from __future__ import annotations

import time

from . import dialog_report as report
from . import windows as win

#: Button roles, recognised by caption. A role is what pressing the
#: button MEANS, which is what a decision can be made from; the exact
#: wording differs between dialogs and Altium versions.
#:
#: Captions are matched loosely (case and punctuation are ignored, and
#: a caption need only CONTAIN the phrase) because Altium writes
#: "Accept Changes (Create ECO)" and "&Report Changes...".
_ROLES = {
    "validate": ("validate changes", "validate"),
    "commit": ("execute changes", "accept changes", "apply"),
    "advance": ("next", "continue", "yes", "finish", "ok"),
    "dismiss": ("close", "cancel", "no"),
    "report": ("report changes", "report"),
}

#: Roles that change the design. Never pressed without explicit consent.
COMMITTING = ("commit",)

#: Advance actions from LEAST final to MOST final.
#:
#: Several buttons can carry the advance role at once, and picking
#: whichever appears first in the dialog's own button order is arbitrary.
#: MEASURED on the real Update From Library wizard, whose buttons arrive
#: as ['&Advanced...', '&Choose Component...', 'Return Selected to
#: &Default', '&Parameters Changes...', '&Finish', 'Cancel', '&Next >',
#: '< &Back']: Finish comes before Next, so the arbitrary choice was
#: Finish, and pressing that on page one skips every remaining
#: configuration page and accepts whatever defaults were showing.
#:
#: Step through when stepping through is offered.
_ADVANCE_PREFERENCE = ("next", "continue", "ok", "yes", "finish")

#: Words that make a confirmation UNANSWERABLE by automation, whatever
#: the caller authorised. A routine "Continue and create ECO?" is part
#: of the operation the caller asked for; "Delete 12 objects?" is a
#: different question that happens to wear the same Yes/No buttons, and
#: no intent flag should be able to answer it on a human's behalf.
_IRREVERSIBLE_WORDS = (
    "delete", "remove", "discard", "overwrite", "erase", "permanently",
    "cannot be undone", "lose", "replace all", "reset",
)


def is_answerable_confirmation(dialog: dict) -> bool:
    """Whether a confirm dialog is safe for automation to answer.

    Safe means: it is asking to carry on with the operation, not asking
    permission to destroy something. Judged on every scrap of text
    available, INCLUDING text recovered by OCR, because Altium paints
    the question and the caption alone rarely contains it.
    """
    haystack = " ".join(
        [dialog.get("title") or ""]
        + list(dialog.get("message") or [])
        + list(dialog.get("ocr_text") or [])
    ).lower()
    return not any(word in haystack for word in _IRREVERSIBLE_WORDS)


def _article(word: str) -> str:
    """"a" or "an". These strings are read by people when a run stops."""
    return "an" if str(word or "")[:1].lower() in "aeiou" else "a"


def _normalise(text: str) -> str:
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def role_of(caption: str) -> str | None:
    """What pressing this button means, or None if unrecognised.

    Longest phrase wins, so "validate changes" is not mistaken for the
    "advance" role that a bare "next" would take.
    """
    flat = _normalise(caption)
    best, best_len = None, 0
    for role, phrases in _ROLES.items():
        for phrase in phrases:
            key = _normalise(phrase)
            if key and key in flat and len(key) > best_len:
                best, best_len = role, len(key)
    return best


def buttons_by_role(dialog: dict) -> dict:
    """Every enabled button on a dialog, grouped by role."""
    out: dict = {}
    for button in dialog.get("buttons", []):
        if not button.get("enabled", True):
            continue
        role = role_of(button["caption"])
        if role:
            out.setdefault(role, []).append(button["caption"])
    return out


def decide(dialog: dict, intent: str, allow_commit: bool,
           already_pressed: set, allow_confirm: bool = False) -> tuple:
    """Choose what to press. Returns (caption, why) or (None, why).

    Decisions come from the dialog's KIND and the roles it offers, never
    from a position in a sequence, so an unexpected dialog is handled on
    the same terms as an expected one.
    """
    kind = dialog.get("kind")
    roles = buttons_by_role(dialog)

    if kind == "confirm" and intent == "proceed" and allow_confirm:
        # A confirmation that merely asks whether to carry on IS the
        # operation the caller requested. Measured: Altium asks
        # "Continue and create ECO?" before every change order, so a
        # driver that always stops here can never complete a sync.
        # Anything that reads as destructive still stops, whatever was
        # authorised.
        if not is_answerable_confirmation(dialog):
            return None, (
                "this confirmation mentions an irreversible action, so "
                "it is left for a human no matter what was authorised")
        caption = _first(roles, "advance")
        if caption:
            return caption, "confirming: this is the operation requested"
        return None, "a confirmation with nothing that means yes"

    if kind in report.NEEDS_A_HUMAN:
        return None, (
            f"{_article(kind)} {kind} dialog is asking for a decision this "
            f"was not sent to make. Stopping rather than answering for you")

    if kind == "progress":
        return None, "waiting: the editor is working"

    if kind == "nothing_to_do":
        caption = _first(roles, "dismiss") or _first(roles, "advance")
        if caption:
            return caption, "there was no work to do, so dismissing"
        return None, "nothing to do, and no button to dismiss it with"

    if kind == "information":
        # A report with nothing to decide, acknowledged rather than
        # escalated. MEASURED: Force Annotate finishes with
        # 'Information' / "No Designator changes are required", and
        # the run stopped dead on it because the caption alone was
        # unrecognised. Almost every Altium command ends this way.
        #
        # This is NOT the unknown-dialog branch relaxing: the kind is
        # only assigned when nothing matched error, warning or confirm
        # first, so a failure announced in an Information box still
        # stops the run.
        caption = _first(roles, "advance") or _first(roles, "dismiss")
        if caption:
            return caption, "acknowledging a report with nothing to decide"
        return None, "an information dialog with no button to close it"

    if intent == "cancel":
        caption = _first(roles, "dismiss")
        if caption:
            return caption, "backing out as asked"
        return None, "asked to back out, but nothing dismisses this"

    if kind in ("engineering_change_order", "change_preview"):
        # change_preview is the board wizard's results list, measured as
        # 'Update From PCB Libraries [Board.PcbDoc]' offering ['Update
        # All', 'Accept Changes (Create ECO)', 'Close', 'Create
        # &Report']. It is not a change order, but its forward action
        # commits, so it needs the same gate: without that branch it
        # fell through to the generic advance path, found no advance
        # button, and reported "offers nothing this knows how to press"
        # while a perfectly good commit button sat there ungated.
        #
        # Order derived from what is offered and what has already been
        # done on THIS window, not from a script: validate before
        # committing, because executing a change order that failed
        # validation is the worst outcome available here.
        if "validate" in roles and "validate" not in already_pressed:
            return _first(roles, "validate"), "validating before committing"
        if "commit" in roles:
            if not allow_commit:
                return None, (
                    "this would EXECUTE the change order and alter the "
                    "design. Re-run with commit authorised if that is "
                    "what you want")
            if "commit" in already_pressed:
                caption = _first(roles, "dismiss")
                return caption, "changes executed, closing the order"
            return _first(roles, "commit"), "executing the change order"
        caption = _first(roles, "dismiss")
        return caption, "nothing left to do on this order"

    if kind == "unknown":
        # An UNRECOGNISED dialog is never advanced, even when it offers
        # a button whose caption is familiar. "OK" is only safe when the
        # question is known, and here it is not: an unrecognised Altium
        # dialog may be a settings sheet, a licence prompt, or a
        # destructive action wearing an ordinary button. Recognising the
        # BUTTON is not recognising the DIALOG, and this branch used to
        # confuse the two and press OK on anything.
        return None, (
            f"this dialog is not recognised (offers "
            f"{[b['caption'] for b in dialog.get('buttons', [])]}). "
            f"Refusing to guess what its buttons would do. Read it with "
            f"app_list_open_dialogs, then drive it deliberately")

    # Wizards and anything else RECOGNISED: move forward, and only
    # through a button whose role is known.
    caption = _first(roles, "advance")
    if caption:
        return caption, f"advancing {_article(kind)} {kind} dialog"

    return None, (f"{_article(kind)} {kind} dialog offers nothing "
                  f"this knows how to press")


def _first(roles: dict, role: str):
    values = roles.get(role) or []
    if not values:
        return None
    if role == "advance":
        return _least_final(values)
    return values[0]


def _least_final(captions: list):
    """The advance button that commits to the least.

    Ranked by _ADVANCE_PREFERENCE rather than by the order the dialog
    happens to list its buttons in, so a wizard offering both Next and
    Finish is stepped through instead of being short-circuited.
    """
    def rank(caption):
        flat = _normalise(caption)
        for i, phrase in enumerate(_ADVANCE_PREFERENCE):
            if _normalise(phrase) in flat:
                return i
        return len(_ADVANCE_PREFERENCE)

    return sorted(captions, key=rank)[0]


def drive(pid: int, intent: str = "proceed", allow_commit: bool = False,
          allow_confirm: bool = False, wait_first: float = 30.0,
          dry_run: bool = True, budget: float = 300.0,
          max_presses: int = 40, settle: float = 0.3) -> dict:
    """Answer dialogs until none is left, the goal is met, or it stops.

    Args:
        pid: the editor process whose dialogs are driven.
        intent: ``proceed`` to carry an operation forward, or ``cancel``
            to back out of whatever is open.
        allow_commit: permit the one class of press that changes the
            design. Off by default, and named for the consequence.
        dry_run: decide and report, press nothing. The default.
        budget: seconds before giving up, so a wedged editor cannot
            hold this forever.
        max_presses: a backstop against a dialog that reappears
            unchanged after every press.

    Returns:
        A record of every observation and decision, plus ``committed``,
        ``stopped_for_a_human`` and the reason it finished.
    """
    if not win.available():
        return {"ok": False, "reason": "pywin32 is not importable",
                "steps": []}

    steps: list = []
    pressed_per_window: dict = {}
    committed = False
    presses = 0
    deadline = time.monotonic() + budget
    # How long to allow for the FIRST dialog to appear. A caller
    # fires an operation and then drives, so the screen is legitimately
    # empty for a moment at the start.
    first_deadline = time.monotonic() + wait_first
    finished = None
    needs_human = False

    while True:
        if time.monotonic() > deadline:
            finished = (f"gave up after {budget:g}s with a dialog still "
                        f"open")
            break
        # Counts PRESSES, not steps. Waiting on a progress dialog and
        # retrying a window that vanished are both recorded as steps but
        # neither touches anything, so counting them here would abandon
        # a legitimately slow operation and blame it on "a dialog that
        # keeps reappearing". Altium's compile has been measured at up
        # to 169s, which is exactly the case that would have been lost.
        if presses >= max_presses:
            finished = (f"stopped after {max_presses} presses: a dialog "
                        f"keeps reappearing unchanged")
            break

        snapshot = report.report(pid)
        if not snapshot.get("ok"):
            return {"ok": False, "reason": snapshot.get("reason"),
                    "steps": steps}

        open_dialogs = snapshot["dialogs"]
        if not open_dialogs:
            # An empty screen means two different things and they must
            # not be conflated. BEFORE anything has been pressed it
            # usually means the operation has not put its dialog up yet:
            # measured, Altium's compare takes a second or two, and
            # exiting on the first empty look is a race that reports
            # "nothing to do" while the work is still starting. AFTER a
            # press it genuinely means the sequence finished.
            if steps or time.monotonic() >= first_deadline:
                finished = ("no dialog is open" if steps else
                            f"no dialog appeared within {wait_first:g}s")
                break
            time.sleep(settle)
            continue

        # An error anywhere takes priority over whatever else is up.
        dialog = next((d for d in open_dialogs if d["needs_a_human"]),
                      open_dialogs[0])
        seen = pressed_per_window.setdefault(dialog["hwnd"], set())
        caption, why = decide(dialog, intent, allow_commit, seen,
                              allow_confirm=allow_confirm)

        record = {
            "title": dialog["title"],
            "kind": dialog["kind"],
            "says": dialog.get("message") or dialog.get("ocr_text") or [],
            "offers": [b["caption"] for b in dialog["buttons"]],
            "decision": caption,
            "why": why,
        }

        if caption is None:
            if dialog["kind"] == "progress":
                steps.append({**record, "action": "waited"})
                time.sleep(min(1.0, settle * 3))
                continue
            record["action"] = "stopped"
            steps.append(record)
            needs_human = dialog["needs_a_human"] or dialog["kind"] == "unknown"
            finished = why
            break

        role = role_of(caption)
        if dry_run:
            record["action"] = f"WOULD press {caption!r}"
            steps.append(record)
            finished = ("dry run: stopping before the first press so "
                        "nothing changes")
            break

        target = win.capture(dialog["hwnd"])
        button = target.find_button(caption) if target else None
        if button is None:
            record["action"] = "vanished"
            record["why"] = ("the dialog changed between reading it and "
                             "pressing, so nothing was pressed")
            steps.append(record)
            continue

        win.click(button)
        presses += 1
        seen.add(role)
        if role in COMMITTING:
            committed = True
        record["action"] = f"pressed {caption!r}"
        steps.append(record)
        # wait_for_close has already waited for this one to go. The loop
        # re-scans for the next dialog immediately, so a further pause
        # here just delayed finding it.
        win.wait_for_close(dialog["hwnd"], timeout=max(2.0, settle * 4))

    return {
        "ok": not needs_human,
        "dry_run": dry_run,
        "intent": intent,
        "committed": committed,
        "stopped_for_a_human": needs_human,
        "finished": finished,
        "steps": steps,
        "note": ("Decisions come from the dialog on screen, not from a "
                 "fixed sequence. A dialog whose buttons have no known "
                 "meaning stops the run instead of being guessed at."),
    }
