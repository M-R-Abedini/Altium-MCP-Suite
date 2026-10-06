# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The confirm gate must cover calls that damage a board without saying so.

``_DESTRUCTIVE_PREFIX`` catches delete, remove, clear, destroy, reset and
overwrite. That misses five DRC methods whose names sound like edits and
whose effect is not: moving a differential pair onto a different net
silently re-scopes whatever is already routed under it, and renaming a
net class orphans every rule that referenced the old name. Before this,
``easyeda_invoke("pcb_Drc", "modifyDifferentialPairPositiveNet", ...)``
went through with no confirmation at all.

The fix is a name list, not a wider prefix. Widening to ``modify`` would
fire on ``pcb_PrimitiveVia.modify`` and every other ordinary edit, and a
guard that fires constantly teaches a caller to pass confirm reflexively,
which is worse protection than none. Both the extension and this side
carry the list, deliberately: the extension's copy only protects editors
running a build that has it, and an old build is exactly when someone
reaches for the generic shim.

Every gated name is checked against the official reference, because a
guard on a method that does not exist protects nothing while looking
thorough.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from eda_agent.tools.easyeda import (
    _DESTRUCTIVE_EXACT,
    _DESTRUCTIVE_PREFIX,
    _destructive_refusal,
)

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_CLASSES = _ROOT / "reference" / "easyeda-api-skill" / "references" / "classes"
_MAIN_JS = _ROOT / "extensions" / "easyeda" / "main.js"

#: The five that prompted this file, with the damage each does.
_NEWLY_GATED = {
    "modifyDifferentialPairPositiveNet": "repoints a routed pair onto another net",
    "modifyDifferentialPairNegativeNet": "repoints a routed pair onto another net",
    "modifyDifferentialPairName": "orphans rules referencing the old name",
    "modifyNetClassName": "orphans rules referencing the old name",
    "modifyEqualLengthNetGroupName": "orphans rules referencing the old name",
    # Replaces the PLACEMENT of the whole board. It sat unguarded beside
    # importAutoRouteJsonFile, which was gated, and the only difference
    # between them is which half of the layout they discard.
    "importAutoLayoutJsonFile": "replaces the placement of the whole board",
    # The automatic engines. autoRouting was guarded only by the
    # easyeda_auto_route TOOL, so the generic shim reached the same
    # method unchecked and the documented guarantee held for one route
    # in and not the other.
    "autoRouting": "discards existing routing and re-routes the board",
    "autoLayout": "rearranges a whole schematic or board",
    "restoreDefault": "resets the editor's own settings",
}

#: Where each gated name lives, since they are spread across classes and
#: a guard looking in the wrong file proves nothing.
_DECLARED_IN = {
    "importAutoLayoutJsonFile": "PCB_Document.md",
    "autoRouting": "PCB_Document.md",
    "autoLayout": "SCH_Document.md",
    "restoreDefault": "SYS_Setting.md",
}


def _refused(method: str, confirm: bool) -> bool:
    out = _destructive_refusal("pcb_Drc", method, confirm)
    return out is not None


@pytest.mark.parametrize("method,damage", sorted(_NEWLY_GATED.items()))
def test_it_refuses_without_confirmation(method, damage):
    assert _refused(method, confirm=False), (
        f"{method} {damage}, and goes through unconfirmed")


@pytest.mark.parametrize("method", sorted(_NEWLY_GATED))
def test_it_proceeds_once_confirmed(method):
    """A gate that cannot be passed is a removal, not a guard."""
    assert not _refused(method, confirm=True)


@pytest.mark.parametrize("method", ["modify", "modifyLayer", "getAllNetClasses",
                                    "createNetClass", "setLayerVisible"])
def test_ordinary_edits_stay_ungated(method):
    """The reason this is a list and not a wider prefix.

    Gating every `modify` would fire on ordinary primitive edits, and a
    guard that fires constantly trains the caller to pass confirm
    without reading it.
    """
    assert not _refused(method, confirm=False), (
        f"{method} is an ordinary edit and must not demand confirmation")


@pytest.mark.skipif(not _CLASSES.is_dir(),
                    reason="official reference not cloned")
@pytest.mark.parametrize("method", sorted(_NEWLY_GATED))
def test_every_gated_name_is_a_real_method(method):
    """A guard on a name nothing exposes is decoration."""
    where = _DECLARED_IN.get(method, "PCB_Drc.md")
    drc = (_CLASSES / where).read_text(encoding="utf-8")
    assert re.search(rf"\[{re.escape(method)}\(", drc), (
        f"{method} is gated but does not appear in {where}; either the "
        f"name is misspelt, in which case the gate never fires, or the "
        f"method was withdrawn and the entry is dead weight")


def test_both_copies_of_the_list_agree():
    """The extension carries the same list, and drift disarms one half."""
    js = _MAIN_JS.read_text(encoding="utf-8")
    missing = [m for m in _NEWLY_GATED if f"'{m}'" not in js and f'"{m}"' not in js]
    assert not missing, (
        f"the extension's copy of the destructive list is missing {missing}. "
        f"An editor running that build accepts these unconfirmed even though "
        f"the Python side refuses them")


def test_the_prefix_list_still_does_its_own_job():
    """Guards the parametrized cases above against a vacuous gate."""
    assert _refused("deleteNetClass", confirm=False)
    assert _refused("clearRouting", confirm=False)
    for prefix in _DESTRUCTIVE_PREFIX:
        assert _refused(f"{prefix}Something", confirm=False), prefix


def test_the_exact_list_is_read_rather_than_reimplemented():
    """If this file drifted from the real frozenset it would prove nothing."""
    assert set(_NEWLY_GATED) <= _DESTRUCTIVE_EXACT, (
        "this file names methods the code no longer gates")
