# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A skill is static markdown, so nothing adapts it for the backend.

The discipline document and the autonomy guide are built at call time
and are rewritten for whichever editor is attached. A SKILL is not: the
client loads the file as written. So every tool it names has to be
either available everywhere, or named alongside the backend it belongs
to.

This started as two real defects in the autodesign skill. It told the
agent to confirm the editor was alive with `app_get_status`, which does
not exist on EasyEDA or KiCad and is the wrong check even on Altium:
`running` means the process exists and `attached` is a local boolean
set by whoever last called attach, so neither proves the bridge
answers. And it prescribed `app_checkpoint` unconditionally, on a
backend that has no such tool and on KiCad, which has no checkpoint at
all, meaning the run was not revertible in the one place the skill
claimed it was.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from eda_agent.design.autonomy import _registered_tools

_SKILLS = sorted((pathlib.Path(__file__).resolve().parents[1] / "skills")
                 .rglob("*.md"))

_BACKENDS = ("altium", "easyeda", "kicad")

#: Prefixes that denote a tool rather than an ordinary underscored word.
_PREFIXES = ("design_", "app_", "proj_", "sch_", "pcb_", "lib_", "obj_",
             "audit_", "route_", "sim_", "easyeda_", "kicad_", "part_")


def _named(text: str) -> set:
    """Tool-shaped names in backticks, minus the pipeline stage names.

    Stages are written in backticks too and look exactly like tools:
    ``sch_to_pcb`` carries a real tool prefix and is not a tool. This
    guard reported it as a phantom on its first run, which is the third
    time that trap has been sprung in this codebase, so the exclusion
    is taken from the canonical STAGES list rather than a local list
    that could drift away from it.
    """
    from eda_agent.design.session import STAGES

    return {n for n in re.findall(r"`([a-z][a-z0-9_]+)`", text)
            if n.startswith(_PREFIXES) and n not in set(STAGES)}


def _applicable_backends(text: str) -> tuple:
    """Which backends a skill actually claims.

    A skill that drives the autonomous loop cannot apply to a backend
    with no autonomous loop, and KiCad registers three design tools
    against EasyEDA's thirty-one: no session, no next_action, no guide.
    Judging that skill against KiCad would demand a qualifier on every
    sentence for a backend the skill explicitly excludes.

    So applicability is derived from the surface rather than declared:
    if the skill is built on design_next_action, it applies exactly to
    the backends that register design_next_action.
    """
    if "design_next_action" not in text:
        return _BACKENDS
    return tuple(b for b in _BACKENDS
                 if "design_next_action" in _registered_tools(b))


def test_there_are_skills_to_check():
    assert _SKILLS, "no skill files found; these guards check nothing"


@pytest.mark.parametrize("path", _SKILLS, ids=lambda p: p.name)
def test_every_tool_a_skill_names_exists_somewhere(path):
    """A name that no backend registers is simply wrong."""
    everywhere = set().union(*(_registered_tools(b) for b in _BACKENDS))
    assert len(everywhere) > 400, (
        "the registration walk is broken and this guard would pass by "
        "checking nothing")

    unknown = sorted(_named(path.read_text(encoding="utf-8")) - everywhere)
    assert not unknown, (
        f"{path.name} names {unknown}, which no backend registers")


@pytest.mark.parametrize("path", _SKILLS, ids=lambda p: p.name)
def test_a_backend_specific_tool_is_named_with_its_backend(path):
    """A tool missing on some backend must say which one it belongs to.

    The test is deliberately loose about HOW that is said: naming the
    editor anywhere in the same paragraph counts. Pinning the exact
    wording would make this a formatting guard, and the property that
    matters is only that a reader on the wrong backend is not told to
    call something that is not there.
    """
    text = path.read_text(encoding="utf-8")
    backends = _applicable_backends(text)
    available = {b: _registered_tools(b) for b in backends}
    paragraphs = re.split(r"\n\s*\n", text)

    unqualified = []
    for para in paragraphs:
        for name in _named(para):
            missing = [b for b in backends if name not in available[b]]
            if not missing:
                continue
            # Which editors is this paragraph already talking about?
            said = {b for b in backends
                    if re.search(b, para, re.IGNORECASE)}
            # Naming any backend at all is taken as the author scoping
            # the sentence; naming none is the failure.
            if not said:
                unqualified.append(
                    f"{name} (absent on {'/'.join(missing)})")

    assert not unqualified, (
        f"{path.name} tells the agent to call these without saying which "
        f"editor they need, so a run on another backend follows the "
        f"instruction and fails: {sorted(set(unqualified))}")


def test_the_liveness_check_is_a_ping_not_a_status_read():
    """The specific defect, stated as the property.

    `app_get_status` returns `running` from the process table and
    `attached` from a local boolean. A skill that treats either as
    "the bridge works" sends the agent into a run against an editor
    that may never answer.
    """
    autodesign = [p for p in _SKILLS if p.name == "SKILL.md"
                  and p.parent.name == "autodesign"]
    assert autodesign, "the autodesign skill moved; update this guard"
    text = autodesign[0].read_text(encoding="utf-8")

    assert "app_ping" in text, (
        "the skill no longer tells the agent to ping; a status read is "
        "not a liveness check")
    before = text.split("app_ping")[0]
    assert "app_get_status" not in before, (
        "app_get_status is presented as the liveness check again")
