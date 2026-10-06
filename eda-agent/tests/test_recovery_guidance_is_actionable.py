# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Recovery instructions must name things that still exist.

``eda_agent.bridge.recovery`` is what a user reads when the bridge has
stopped answering. The steps are written into ``last_fault.json`` and
shown by the dashboard, so they are the instructions someone follows at
the exact moment nothing else is working.

That makes them the worst place in the codebase for a stale reference. A
step naming a procedure that was renamed, or a README section that
moved, strands the reader with no way to tell whether they typed it
wrong or the instructions are simply out of date.

Two claims are checkable and both are checked here:

* the relaunch step names ``Dispatcher.pas > StartMCPServer``, so that
  file must exist and must declare that procedure
* the docs hint names ``README 'Known limitations' >
  'Altium DelphiScript engine can crash'``, so both must be headings

This is the mirror of ``test_release_verification_claims.py``. That one
checks a document's claims about the code; this one checks the code's
claims about a document. Same defect either way: a fact stated twice
with nothing forcing the two to agree.
"""

from __future__ import annotations

import pathlib
import re

from eda_agent.bridge import recovery

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PAS_DIR = _ROOT / "scripts" / "altium"
_README = _ROOT / "README.md"

# "Dispatcher.pas > StartMCPServer"
_PAS_REF = re.compile(r"(\w+\.pas)\s*>\s*(\w+)")
# "README 'Known limitations' > 'Altium DelphiScript engine can crash'"
_QUOTED = re.compile(r"'([^']+)'")

_FAULTS = (recovery.STUCK_HANDLER, recovery.DEAD_LOOP,
           recovery.CORRUPT_RESPONSE)


def _all_steps() -> list[str]:
    steps = []
    for fault in _FAULTS:
        steps.extend(recovery.recovery_guidance(fault)["steps"])
    return steps


def _all_docs_hints() -> list[str]:
    return [recovery.recovery_guidance(f)["docs"] for f in _FAULTS]


def _headings() -> set[str]:
    return {
        line.lstrip("#").strip()
        for line in _README.read_text(encoding="utf-8").splitlines()
        if line.startswith("#")
    }


def test_the_relaunch_step_names_a_procedure_that_exists():
    refs = [m for step in _all_steps() for m in _PAS_REF.findall(step)]
    assert refs, (
        "no '<file>.pas > <Procedure>' reference found in any recovery "
        "step. Either the relaunch instruction was reworded and this test "
        "now checks nothing, or the instruction lost its entry point.")
    for filename, proc in refs:
        path = _PAS_DIR / filename
        assert path.exists(), (
            f"recovery tells the user to run {proc} from {filename}, which "
            f"is not in {_PAS_DIR}")
        text = path.read_text(encoding="utf-8", errors="replace")
        declared = re.search(
            r"^\s*(?:Procedure|Function)\s+" + re.escape(proc) + r"\b",
            text, re.M | re.I)
        assert declared, (
            f"recovery tells the user to run {proc} from {filename}, but "
            f"{filename} declares no such routine. Someone renamed the "
            "entry point and left the instructions behind.")


def test_the_docs_hint_points_at_real_readme_sections():
    hints = [h for h in _all_docs_hints() if h]
    assert hints, "no docs hint is attached to any fault kind"
    headings = _headings()
    checked = 0
    for hint in set(hints):
        sections = _QUOTED.findall(hint)
        assert sections, (
            f"docs hint {hint!r} names no quoted section, so nothing here "
            "can verify it still resolves")
        for section in sections:
            checked += 1
            assert section in headings, (
                f"recovery points the user at README section {section!r}, "
                "which is not a heading in README.md. Closest headings: "
                + ", ".join(sorted(
                    h for h in headings
                    if h and section.split()[0].lower() in h.lower())[:3]))
    assert checked >= 2, (
        f"only {checked} README section(s) checked; the hint format "
        "changed and this test is reading less than it thinks")


def test_every_fault_kind_gets_steps_and_a_diagnosis():
    """An empty recovery block is worse than none: it reads as 'no fix'."""
    for fault in _FAULTS:
        guidance = recovery.recovery_guidance(fault)
        assert guidance["steps"], f"{fault} has no recovery steps"
        assert guidance["diagnosis"].strip(), f"{fault} has no diagnosis"
        assert guidance["fault"] == fault


def test_an_unknown_fault_still_gets_a_usable_fallback():
    """Documented behaviour: callers never receive an empty dict."""
    guidance = recovery.recovery_guidance("something_new_and_unhandled")
    assert guidance["steps"]
    assert guidance["diagnosis"].strip()
