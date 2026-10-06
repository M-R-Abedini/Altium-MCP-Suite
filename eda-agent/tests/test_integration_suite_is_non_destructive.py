# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The live-Altium tests must not modify the design they connect to.

``tests/integration/`` binds to a real Altium. It is
gated at collection on ``EDA_AGENT_INTEGRATION=1``, so a plain
``pytest`` no longer reaches a live session at all; see
``tests/test_integration_tests_are_opt_in.py``. This file covers the
other half, what those tests are allowed to do once someone opts in.

Every command they send reads the design: ping, get_open_documents,
get_active_document, get_focused, get_components, open, compile. Note
that ``open`` and ``compile`` do not modify the design but are not
invisible either, since they change which document has focus. That is
acceptable for a run the developer asked for, and was the reason the
collection gate was added for runs they did not.

Nothing enforced that. A single ``lib_add_pins`` or ``pcb_place_via``
added to those tests would silently start editing the board of anyone
who runs the suite with Altium up, and it would look like an ordinary
test until someone noticed their library had grown a component.

The property is worth keeping rather than just documenting: it is what
makes the live suite runnable without ceremony. Verification that has
to mutate belongs in ``docs/RELEASE_VERIFICATION.md``, where it is a
deliberate step a human takes against a session they chose, not a side
effect of running the tests.

Static check only. It reads the command strings the tests pass to
``send_command``, so a command assembled at runtime is invisible. That
direction is safe: it cannot produce a false accusation, only miss an
exotic one, and nothing in that directory builds a command dynamically.
"""

from __future__ import annotations

import ast
from pathlib import Path

INTEGRATION = Path(__file__).resolve().parent / "integration"

#: Verb prefixes that change the design. Shared with
#: test_maturity_matches_reality, which uses the same list to check that
#: no readonly-labelled tool issues one. A second copy here would drift
#: from that one, and then the two claims would quietly stop agreeing.
from tests.conftest import MUTATING_COMMAND_VERBS as _MUTATING_VERBS

#: Reads that happen to start with a mutating verb. ``project.open``
#: loads a document and changes no design data; ``project.compile``
#: refreshes the compiled netlist, which is derived state. Both are
#: needed to reach a project at all.
_ALLOWED = {"project.open", "project.compile"}


def _commands_sent() -> dict[str, str]:
    """Every literal command the integration tests send, to its site."""
    found: dict[str, str] = {}
    for path in sorted(INTEGRATION.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8",
                                            errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (func.attr if isinstance(func, ast.Attribute)
                    else getattr(func, "id", ""))
            if name not in ("send_command", "send_command_async"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            command = node.args[0].value
            if isinstance(command, str) and "." in command:
                found.setdefault(
                    command,
                    f"{path.relative_to(INTEGRATION).as_posix()}:{node.lineno}")
    return found


def _is_mutating(command: str) -> bool:
    action = command.split(".", 1)[-1]
    return any(action.startswith(verb) for verb in _MUTATING_VERBS)


def test_no_live_test_sends_a_design_mutating_command():
    sent = _commands_sent()
    assert sent, "no commands parsed out of tests/integration; scan broke"

    offenders = sorted(
        f"{cmd}  ({where})" for cmd, where in sent.items()
        if _is_mutating(cmd) and cmd not in _ALLOWED)
    assert not offenders, (
        "these live-Altium tests send commands that modify the design, "
        "so running the suite would edit whatever session the developer "
        "has open:\n  " + "\n  ".join(offenders)
        + "\nPut verification that has to mutate in "
          "docs/RELEASE_VERIFICATION.md instead.")


def test_the_scan_sees_the_commands_it_should():
    """A parse that found nothing would pass the check above."""
    sent = _commands_sent()
    assert "application.ping" in sent or "project.get_focused" in sent, (
        f"the integration suite's known commands are missing from the "
        f"scan; it found {sorted(sent)}")
    assert len(sent) >= 5, f"only {len(sent)} commands found: {sorted(sent)}"


def test_the_detector_recognises_a_mutating_command():
    """Pin the classifier, so the check cannot pass by never matching."""
    assert _is_mutating("library.add_pins")
    assert _is_mutating("pcb.place_via")
    assert _is_mutating("generic.batch_modify")
    assert not _is_mutating("application.ping")
    assert not _is_mutating("pcb.get_components")


def test_the_allowlist_entries_are_still_used():
    """A stale exemption would quietly widen what counts as a read."""
    sent = _commands_sent()
    unused = sorted(cmd for cmd in _ALLOWED if cmd not in sent)
    assert not unused, (
        f"these are exempted but no longer sent, so the exemption is "
        f"dead and should go: {unused}")
