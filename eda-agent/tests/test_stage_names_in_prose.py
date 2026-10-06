# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Agent instructions must name pipeline stages that exist.

``src/eda_agent/design/autonomy.py`` and ``skills/autodesign/SKILL.md``
carry the same instruction, in different words: checkpoint before each
high-risk mutating stage, and then they LIST those stages by name. That
list is a copy of ``session.STAGES`` maintained by hand in two places,
which is the shape that drifts.

``tests/test_agent_facing_text_matches_code.py`` already checks the
stage COUNT across the files that state it. A count cannot see a wrong
NAME, the same gap that let the README advertise ``pcb_fillet_corners``:
rename a stage and the count stays 13 while both instructions point at
something that no longer runs.

The consequence is specific and bad. Those three stages are named
because they are the ones worth taking a checkpoint before. An agent
following an instruction that names a stage which never fires does not
checkpoint at all, so the undo is missing exactly where the pipeline is
riskiest.

SCOPED DELIBERATELY. Most stage names are ordinary words: ``plan``,
``placement``, ``routing``, ``outputs``, ``requirement``. They appear in
normal prose about PCB design across roughly a hundred files, so a check
covering all thirteen would be almost entirely noise. This reads only
the list attached to the checkpoint instruction, where a token is
unambiguously meant as a stage identifier.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Both copies of the instruction, whatever their formatting.
_ANCHOR = "high-risk mutating stage"

#: An identifier-shaped token: stage names in this list all carry an
#: underscore, which is what separates them from surrounding prose.
_IDENT = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+")

_FILES = (
    pathlib.Path("src") / "eda_agent" / "design" / "autonomy.py",
    pathlib.Path("skills") / "autodesign" / "SKILL.md",
)


def _stages() -> tuple[str, ...]:
    from eda_agent.design.session import STAGES
    return STAGES


def _windows(text: str) -> list[str]:
    """The sentence following each occurrence of the anchor.

    Cut at the end of the sentence so the next instruction's tokens are
    not swept in.
    """
    out = []
    for match in re.finditer(re.escape(_ANCHOR), text):
        window = text[match.end():match.end() + 160]
        out.append(re.split(r"\n\s*\n|\.\s", window, maxsplit=1)[0])
    return out


def _named_stages(text: str) -> list[str]:
    """Identifier-shaped tokens in the anchored sentence.

    Underscore-bearing only, because an unknown token is the thing being
    detected and there is no way to tell a typo'd single word from
    ordinary prose.
    """
    return [t for w in _windows(text) for t in _IDENT.findall(w)]


def _known_stages(text: str) -> set[str]:
    """Real stage names in the anchored sentence, single words included.

    Safe here where a blanket scan would not be: this sentence exists to
    list stages, so ``placement`` inside it means the stage. Used for
    the agreement check, which would otherwise be blind to any stage
    whose name carries no underscore. Mutation found exactly that: a
    ``placement`` added to one copy went unnoticed.
    """
    stages = set(_stages())
    found: set[str] = set()
    for window in _windows(text):
        for stage in stages:
            if re.search(r"\b" + re.escape(stage) + r"\b", window):
                found.add(stage)
    return found


def test_both_copies_of_the_instruction_are_present():
    """Guard the guard: a reworded anchor must not silently check zero."""
    missing = [str(f) for f in _FILES if not (_ROOT / f).is_file()]
    assert not missing, f"expected these to carry the instruction: {missing}"

    without = [str(f) for f in _FILES
               if _ANCHOR not in (_ROOT / f).read_text(encoding="utf-8")]
    assert not without, (
        f"the phrase {_ANCHOR!r} is gone from {without}, so this test now "
        "checks nothing. If the instruction was reworded, update _ANCHOR "
        "rather than deleting the check.")


def test_every_stage_the_instruction_names_exists():
    stages = set(_stages())
    problems: list[str] = []
    checked = 0
    for rel in _FILES:
        text = (_ROOT / rel).read_text(encoding="utf-8")
        named = _named_stages(text)
        assert named, (
            f"{rel} carries the instruction but names no stage; the list "
            "formatting changed and this test is reading nothing")
        for name in named:
            checked += 1
            if name not in stages:
                problems.append(f"{rel}: {name!r}")
    assert checked >= 4, (
        f"only {checked} stage names read across {len(_FILES)} files")
    assert not problems, (
        "agent instructions name pipeline stages that do not exist: "
        + ", ".join(problems)
        + f". Valid stages: {sorted(stages)}")


def test_the_two_copies_agree_with_each_other():
    """They are the same instruction, so they must name the same stages.

    Not implied by the check above: both could name real stages and
    still disagree about which ones deserve a checkpoint, which would
    make the skill and the built-in guidance quietly contradict.
    """
    named = {rel: sorted(_known_stages(
        (_ROOT / rel).read_text(encoding="utf-8"))) for rel in _FILES}
    values = list(named.values())
    assert values[0], "no stages read from the first copy"
    assert values[0] == values[1], (
        "the two copies of the checkpoint instruction name different "
        f"stages: {named}")


def test_a_renamed_stage_would_be_caught():
    """The comparison is against the live tuple, not a copy."""
    stages = _stages()
    assert "sch_to_pcb" in stages
    assert len(stages) == 13, (
        "the pipeline length changed; test_agent_facing_text_matches_code "
        "owns the count, but this assertion exists so a rename that also "
        "changes the length is not mistaken for a formatting problem")
