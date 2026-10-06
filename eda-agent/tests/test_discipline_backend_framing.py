# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The discipline doc names the editor a plan is actually going into.

design_get_discipline is registered on every backend, and its text was
written for Altium: the opening paragraph tells the planner its plan
will be instantiated in Altium Designer. On EasyEDA that is the first
sentence a planner reads and it is wrong about both the editor and the
tool that runs the plan.

Only the OPENING is adapted. The rest names Altium tools inside
sentences that sometimes explain WHY a tool is Altium-only, and
substituting there yields prose that contradicts itself; that split is
task #58.

The no-op case is guarded deliberately. The first version of this
replacement did not match, because the line break fell somewhere other
than assumed, and the doc was served unchanged while reporting
success. A silent no-op is the worst outcome here: the planner reads
the Altium framing believing it was corrected.
"""
from __future__ import annotations

import pytest

from eda_agent.design.discipline import _DISCIPLINE, get_discipline


def _for(backend, monkeypatch):
    monkeypatch.setenv("EDA_AGENT_BACKEND", backend)
    return get_discipline()


def test_the_easyeda_planner_is_told_the_right_editor(monkeypatch):
    text = _for("easyeda", monkeypatch)
    assert "instantiate in EasyEDA Pro" in text
    assert "easyeda_run_plan" in text, (
        "the planner is not told which tool runs its plan here")


def test_the_easyeda_planner_is_not_told_it_is_using_altium(monkeypatch):
    text = _for("easyeda", monkeypatch)
    opening = text[:800]
    assert "instantiate in Altium Designer" not in opening, (
        "the opening still says the plan goes into Altium")


def test_the_altium_text_is_untouched(monkeypatch):
    """It was written for Altium; changing it there would be a
    regression, not a fix."""
    text = _for("altium", monkeypatch)
    assert "instantiate in Altium Designer." in text
    assert "could not be adapted" not in text


def test_a_failed_substitution_says_so_instead_of_pretending(monkeypatch):
    """The failure this file exists for.

    If the sentence ever changes so the replacement stops matching, the
    doc must SAY the framing is stale rather than serve the Altium
    version silently.
    """
    import eda_agent.design.discipline as mod

    monkeypatch.setattr(mod, "_DISCIPLINE",
                        "# Design Discipline\n\nNo matching sentence here.\n")
    monkeypatch.setenv("EDA_AGENT_BACKEND", "easyeda")
    text = mod.get_discipline()

    assert "could not be adapted" in text, (
        "the substitution silently did nothing and the doc claimed "
        "nothing was wrong")
    assert "EasyEDA Pro" in text
    assert "easyeda_run_plan" in text


def test_the_schema_still_follows_the_text(monkeypatch):
    """The doc is text plus the DesignPlan schema; adapting the framing
    must not drop the half the planner validates against."""
    for backend in ("altium", "easyeda", "kicad"):
        text = _for(backend, monkeypatch)
        assert "## DesignPlan JSON schema" in text
        assert "```json" in text


@pytest.mark.parametrize("backend", ["altium", "easyeda", "kicad"])
def test_every_backend_gets_the_whole_document(backend, monkeypatch):
    """Adaptation must not truncate: the rules are the point."""
    text = _for(backend, monkeypatch)
    assert len(text) > len(_DISCIPLINE) * 0.9
    assert "## Hard rules" in text


def test_a_name_written_with_its_arguments_is_substituted_too(monkeypatch):
    """The worked examples were the half that never got adapted.

    Substitution matched `name` and nothing else, so a name written
    with its call signature, `lib_create_ic_symbol(name, left_pins,
    right_pins)`, sat inside a backtick span that did not end after the
    name and was skipped. Rule 9's three one-call generators are all
    written that way, which is the worst possible place for the gap:
    they are the instructions a planner copies verbatim when authoring
    a part, so an EasyEDA planner was handed three Altium calls to make.

    The bare-name form is asserted alongside so a fix that swaps one
    blind spot for the other fails here.
    """
    text = _for("easyeda", monkeypatch)

    for altium_call in ("lib_create_standard_footprint(",
                        "lib_create_ic_symbol(",
                        "lib_create_passive_symbol(",
                        "pcb_check_placement_collision("):
        assert altium_call not in text, (
            f"{altium_call}...) survived into the EasyEDA document; a "
            f"planner following rule 9 would call a tool that is not "
            f"registered here")

    for easyeda_call in ("easyeda_create_standard_footprint(",
                         "easyeda_create_ic_symbol(",
                         "easyeda_create_passive_symbol("):
        assert easyeda_call in text, (
            f"{easyeda_call} is missing, so the generator instruction "
            f"was removed rather than translated")

    # The bare form must still work; it is the majority of the document.
    assert "`easyeda_create_symbol`" in text
    assert "`lib_create_symbol`" not in text


def test_a_shorter_tool_name_does_not_eat_a_longer_one(monkeypatch):
    """`lib_add_pins` and `lib_add_pin`-style prefixes share a front.

    Matching on the delimiter after the name is what keeps them apart.
    A fix that dropped it would rewrite the front of the longer name
    and leave a trailing fragment, so the document would name a tool
    nobody registers.
    """
    text = _for("easyeda", monkeypatch)
    import re

    from eda_agent.design.autonomy import _registered_tools

    available = _registered_tools("easyeda")
    named = set(re.findall(r"`(easyeda_[a-z0-9_]+)[`(]", text))
    unknown = sorted(n for n in named if n not in available)
    assert not unknown, (
        f"the adapted document names {unknown}, which the easyeda "
        f"backend does not register; a substitution produced a "
        f"malformed name rather than a real one")


@pytest.mark.parametrize("backend", ["easyeda", "kicad"])
def test_the_eco_rules_are_replaced_not_translated(backend, monkeypatch):
    """Rules 6 and 7 describe a mechanism only Altium has.

    They explain that the Engineering Change Order raises a dialog a
    human must click, and give the trick for populating a board without
    it. Swapping the tool names inside them is the worst available
    outcome: the heading becomes an EasyEDA tool while the body still
    instructs the reader to fetch a UniqueId and avoid a dialog that
    does not exist here. It reads as authoritative and describes
    nothing. So the block is replaced wholesale.

    What replaces it must not invent the opposite claim either. Whether
    these editors prompt has not been measured on a live session, so
    the text says to treat the transfer as attended until someone has
    watched it, and this guard holds it to that.
    """
    text = _for(backend, monkeypatch)

    assert "Engineering Change Order" not in text, (
        "the Altium ECO rules reached a backend that has no ECO")
    assert "Execute Changes" not in text
    assert "post-ECO" not in text, (
        "a section heading still dates the layout phase from an Altium "
        "step this backend does not perform")
    assert "could not be replaced" not in text, (
        "the anchors moved and the block fell through to the fallback "
        "note; the replacement is no longer happening")

    assert "has not been verified" in text or "has not been checked" in text, (
        "the replacement states the transfer's modality as fact; it has "
        "not been measured on a live session, so it must not be claimed "
        "in either direction")


def test_altium_keeps_its_own_eco_rules(monkeypatch):
    """The block swap must not fire on the backend it was written for.

    A replacement that ran unconditionally would delete real, correct
    and hard-won guidance: that the change-review dialog cannot be
    suppressed is the reason an unattended Altium run must not call it.
    """
    text = _for("altium", monkeypatch)
    assert "Engineering Change Order" in text
    assert "Execute Changes" in text
    assert text == _DISCIPLINE + text[len(_DISCIPLINE):], (
        "the Altium document is no longer the source text plus the "
        "schema; something adapted a backend that needs no adapting")
