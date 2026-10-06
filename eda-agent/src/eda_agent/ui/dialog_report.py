# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Say what is on screen, what it says, and whether Altium is stuck.

Built because the alternative failed repeatedly in one live session. A
modal error was on screen while the tool that fired it reported
``dialog_may_have_opened: False``; a "no differences" dialog blocked the
editor while a plan waited for an Engineering Change Order that was
never coming; and a driver aborted reporting a window that "offers []"
because it could not see any of the four buttons in front of it.

Every one of those was a reporting failure rather than a logic failure.
The information was on screen the whole time.

WHAT A CALLER GETS. For each dialog: its caption, its class, the
message text, the buttons with their enabled state, a KIND, and whether
it holds content that cannot be read at all. Plus one process-level
answer, ``blocked``, which says whether a modal is holding the main
window, because that single fact explains a silent bridge.

WHAT IT STILL CANNOT DO. Grids and owner-drawn lists expose no window
text, so an ECO's pending-change list is unreadable from here. That is
reported as ``has_unreadable_content`` rather than being passed off as
an empty dialog.
"""

from __future__ import annotations

import re

from . import ocr
from . import windows as win

#: Dialog kinds, most specific first. Matched against the CAPTION and
#: the message text together, because Altium splits the meaning across
#: both: "Comparator Results (No Differences)" carries it in the title,
#: while a compare refusal puts "Cannot compare ..." only in the body.
#:
#: Every pattern here was measured on a real dialog during the session
#: that motivated this module, except where noted as conventional.
_KINDS = (
    ("nothing_to_do", (r"no differences",
                       r"nothing to (do|update)")),
    ("engineering_change_order", (r"engineering change order",)),
    # The board wizard's OPTIONS page, matched before the results below
    # because both carry "Update From PCB Libraries" in the caption and
    # only this one ends in "- Options".
    ("wizard", (r"update from pcb librar.*option",)),
    # Its RESULTS page. MEASURED: 'Update From PCB Libraries
    # [Board.PcbDoc]' offering ['Update All', 'Accept Changes (Create
    # ECO)', 'Close', 'Create &Report']. It is not a change order, it is
    # the list that CREATES one, and its forward action commits. Left
    # unclassified it read as unknown, so the driver stopped and the
    # board flow could never finish.
    ("change_preview", (r"update from pcb librar",)),
    ("error", (r"\berror\b", r"cannot\b", r"\bfailed\b", r"unable to\b")),
    ("warning", (r"\bwarning\b", r"\bcaution\b")),
    # A question mark ANYWHERE, not anchored to the end. Measured:
    # Altium's "Continue and create ECO?" arrives as one of several
    # recovered lines, so once they are joined the question sits in the
    # middle and an end-anchored pattern never fires, leaving a plain
    # Yes/No prompt classified as unknown. Errors and warnings are
    # matched before this, so a question mark inside an error message
    # still reads as an error.
    ("confirm", (r"\bconfirm\b", r"\?", r"are you sure")),
    ("wizard", (r"update from librar", r"\bwizard\b")),
    ("progress", (r"\bprogress\b", r"please wait")),
    # A plain report with nothing to decide. MEASURED: Force Annotate
    # ends with 'Information' / "No Designator changes are required in
    # Project", offering only OK, and the driver stopped dead on it
    # because an unknown dialog is never guessed at. Almost every
    # Altium operation finishes with one of these, so leaving them
    # unclassified meant a human had to finish every run by hand.
    #
    # Handled by _TITLE_ONLY_KINDS below rather than here, because the
    # patterns above are matched against the caption and the message
    # JOINED, where an anchored caption pattern can never fire.
)

#: Kinds decided by the CAPTION alone, tried only after every pattern
#: above has failed. Keeping them separate is what makes the precedence
#: safe: a box captioned "Information" whose message reports a failure
#: matches ``error`` first and never reaches here.
_TITLE_ONLY_KINDS = (
    ("information", (r"^information\b",)),
)

#: A dialog of one of these kinds must never be clicked past by an
#: automation: it is either reporting a problem or asking a human to
#: decide something the plan did not anticipate.
NEEDS_A_HUMAN = ("error", "warning", "confirm")


def classify(title: str, message: list) -> str:
    """The kind of dialog, from its caption and its message together."""
    haystack = " ".join([title or ""] + list(message or [])).lower()
    for kind, patterns in _KINDS:
        for pattern in patterns:
            if re.search(pattern, haystack, re.I):
                return kind
    caption = (title or "").strip().lower()
    for kind, patterns in _TITLE_ONLY_KINDS:
        for pattern in patterns:
            if re.search(pattern, caption, re.I):
                return kind
    return "unknown"


#: Recognised text, keyed by window handle and caption.
#:
#: Recognising costs a window capture and a call into the Windows OCR
#: engine, roughly two seconds. A driver POLLS, so without this every
#: pass over a dialog that has no readable text pays that again, the
#: loop crawls, and a run that is working looks indistinguishable from
#: one that has hung. Measured: it did exactly that.
#:
#: Keyed on the caption as well as the handle because Windows reuses
#: handles, and a reused handle showing a different dialog must not
#: inherit the previous one's text.
_OCR_CACHE: dict = {}

#: Bounded so a long session cannot grow it without limit.
_OCR_CACHE_MAX = 64


def _cached_ocr(window) -> list:
    key = (window.hwnd, window.title)
    if key in _OCR_CACHE:
        return _OCR_CACHE[key]
    text = ocr.read_window_text(window.hwnd)
    if len(_OCR_CACHE) >= _OCR_CACHE_MAX:
        _OCR_CACHE.clear()
    _OCR_CACHE[key] = text
    return text


def forget_ocr() -> None:
    """Drop the cache. For tests, and for a caller that knows a dialog
    has changed its text in place."""
    _OCR_CACHE.clear()


def describe_dialog(window, use_ocr: bool = True) -> dict:
    """One dialog, fully reported.

    ``use_ocr`` recovers message text that has no programmatic form at
    all. It costs a window capture and a call into the Windows
    recogniser, so it runs only when nothing readable was found, which
    is the case it exists for.
    """
    message = window.message_text()
    buttons = window.buttons()
    unreadable = window.has_unreadable_content()

    # OCR BEFORE classifying, not after. Measured: Altium's
    # "Comparator Results (2 Differences)" carries its actual question,
    # "Continue and create ECO?", only in painted text. Classifying on
    # the caption alone called it unknown, and an unknown dialog stops a
    # run, so the recovered text has to be available to the classifier
    # and not merely reported alongside it.
    recognised = []
    if not message and use_ocr:
        recognised = _cached_ocr(window)

    kind = classify(window.title, list(message) + list(recognised))
    out = {
        "title": window.title,
        "class": window.class_name,
        "kind": kind,
        "message": message,
        "buttons": [
            {"caption": b.text, "class": b.class_name, "enabled": b.enabled}
            for b in buttons
        ],
        "needs_a_human": kind in NEEDS_A_HUMAN,
        "has_unreadable_content": unreadable,
        "hwnd": window.hwnd,
    }
    if not message:
        # Never let an empty list read as "the dialog said nothing".
        # MEASURED: Altium paints message text with Delphi TLabel, which
        # owns no window handle, so the panel behind it answers
        # WM_GETTEXTLENGTH with zero and MSAA exposes nothing. The text
        # is on screen and simply has no programmatic representation.
        captions = {b["caption"] for b in out["buttons"]}
        # Drop what is already known exactly: the caption and the button
        # captions come from Win32 and do not need recognising, and
        # leaving them in invites a caller to trust an OCR copy of a
        # string it already had perfectly.
        extra = [line for line in recognised
                 if line not in captions and line != window.title]
        if extra:
            out["ocr_text"] = extra
            out["message_source"] = "ocr"
            out["message_note"] = (
                "message text was RECOGNISED FROM PIXELS, not read. "
                "Altium paints dialog text with controls that own no "
                "window handle, so this is the only way to see it. "
                "Treat it as a hint: OCR confuses 0 with O and 1 with l, "
                "which matters most in part numbers. The title and the "
                "button captions are exact and are not from OCR.")
        else:
            out["message_source"] = "none"
            out["message_note"] = (
                "no message text could be read, and OCR recovered "
                "nothing either. This is NOT evidence the dialog is "
                "empty. The CAPTION is reliable and usually carries the "
                "outcome; read the Messages panel, or look at the "
                "screen, for the detail.")
    else:
        out["message_source"] = "window_text"
    return out


def report(pid: int) -> dict:
    """Everything on screen for one process, and whether it is blocked.

    ``blocked`` comes from the main window being DISABLED, which is what
    Windows does while a modal is up. It is the reliable answer to "why
    has the bridge gone quiet", a question that otherwise looks the same
    whether Altium is showing a dialog, busy, or dead.
    """
    if not win.available():
        return {"ok": False,
                "reason": "pywin32 is not importable, so no window can be "
                          "inspected from this host"}

    found = [describe_dialog(w) for w in win.dialogs(pid)]
    blocked = win.is_blocked(pid)
    kinds = [d["kind"] for d in found]
    return {
        "ok": True,
        "pid": pid,
        "blocked": blocked,
        "dialog_count": len(found),
        "dialogs": found,
        "needs_a_human": any(d["needs_a_human"] for d in found),
        "summary": _summarise(found, blocked),
        "kinds": kinds,
    }


def _summarise(found: list, blocked: bool) -> str:
    """One sentence a caller can act on without parsing the rest."""
    if not found:
        return ("no dialog is open; a blocked main window here would mean "
                "something else is holding it"
                if blocked else "no dialog is open")
    parts = []
    for dialog in found:
        text = dialog["message"] or dialog.get("ocr_text") or []
        said = text[0] if text else "no readable text"
        parts.append(f"{dialog['kind']}: {dialog['title']!r} says "
                     f"{said!r}, offering "
                     f"{[b['caption'] for b in dialog['buttons']]}")
    head = "Altium is BLOCKED by a modal. " if blocked else ""
    return head + "; ".join(parts)
