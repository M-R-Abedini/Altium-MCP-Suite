# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""``pcb_filter_variant_components`` must not guess at ``select``.

The handler classifies a variant's components and selects one
fitted-class on the board. Which class is decided by a WORD that crosses
the bridge as a string, and ``PCB_FilterVariantComponents`` tests it with
an If chain::

    If SelMode = 'all_fitted' Then ...
    Else If SelMode = 'fitted_original' Then ...
    Else If SelMode = 'alternate' Then ...
    (implicit else: not_fitted)

So an unrecognised word is not refused, it becomes not-fitted. Worse,
the reply is built with ``JsonStr('select', SelMode)``, echoing the
caller's own string rather than the class actually used. A typo of
``alternate`` therefore selects the OPPOSITE set and the response
confirms the word that was asked for.

Nothing downstream can detect that. The selection is what a delete, a
component class, or a variant review then acts on, so the wrong parts
get treated and every reported field agrees with the request.

The same shape as the pin-electrical and power-port vocabularies in
``test_enum_vocabularies.py``: a string with a defaulting else. This one
is validated in Python instead, because rejecting is the whole point and
the Pascal cannot reject without a redeploy.
"""

from __future__ import annotations

import pytest

from eda_agent.tools import pcb as pcb_module


def _capture_tool(monkeypatch, bridge):
    monkeypatch.setattr(pcb_module, "get_bridge", lambda: bridge)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    pcb_module.register_pcb_tools(DummyMcp())
    return captured["pcb_filter_variant_components"]


class _Bridge:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, params or {}))
        # Mirror the handler: echo the caller's own word back.
        return {"variant": (params or {}).get("variant_name"),
                "select": (params or {}).get("select"),
                "matched": 3, "designators": ["R1", "R2", "C9"]}

    def commands(self):
        return [c for c, _ in self.calls]


@pytest.mark.asyncio
@pytest.mark.parametrize("select", list(pcb_module.FILTER_VARIANT_SELECT))
async def test_every_advertised_class_is_accepted(monkeypatch, select):
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)

    out = await tool(variant_name="Assembly-A", select=select)

    assert out.get("ok") is not False
    assert bridge.calls[0][1]["select"] == select


@pytest.mark.asyncio
@pytest.mark.parametrize("typo", [
    "alternates",        # plural, would silently become not_fitted
    "fitted",            # plausible shorthand for all_fitted
    "not-fitted",        # hyphen instead of underscore
    "NOT_FITTED",        # the handler lowercases, but this tool should not
                         # rely on that to decide what it sends
    "",                  # empty: the handler defaults it, we should not
])
async def test_an_unknown_class_is_refused_before_anything_is_selected(
        monkeypatch, typo):
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)

    out = await tool(variant_name="Assembly-A", select=typo)

    assert out["ok"] is False, (
        f"select={typo!r} was accepted; the handler would fall through to "
        "not_fitted and echo the word back as if it had worked")
    assert bridge.commands() == [], (
        "the refusal must come before the command is sent, or the board "
        "selection has already changed")
    assert "not_fitted" in out["reason"] and "alternate" in out["reason"], (
        "the refusal must list the classes that ARE valid")


@pytest.mark.asyncio
async def test_the_default_is_one_of_the_advertised_classes(monkeypatch):
    """Calling with no select must not trip the tool's own validation."""
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)

    out = await tool(variant_name="Assembly-A")

    assert out.get("ok") is not False
    assert bridge.calls[0][1]["select"] in pcb_module.FILTER_VARIANT_SELECT


def test_the_vocabulary_matches_the_handler_branches():
    """The Python list and the Pascal If chain must not drift apart.

    Three classes are tested explicitly in the handler and the fourth is
    the implicit else. If someone adds a branch to the Pascal without
    adding the word here, this tool refuses a value the board supports;
    if they add it here first, it forwards a word that silently becomes
    not_fitted.
    """
    import pathlib
    import re

    pas = pathlib.Path(__file__).resolve().parents[1] / "scripts" / \
        "altium" / "PCB.pas"
    text = pas.read_text(encoding="utf-8", errors="replace")
    start = text.index("Function PCB_FilterVariantComponents")
    end = text.find("\nFunction ", start + 1)
    body = text[start:end if end != -1 else len(text)]

    branches = set(re.findall(r"SelMode\s*=\s*'(\w+)'", body))
    assert branches, (
        "no SelMode comparison found in PCB_FilterVariantComponents; the "
        "handler was rewritten and this check is reading nothing")

    # The default is assigned rather than compared, so pull it separately.
    default = re.search(r"If\s+SelMode\s*=\s*''\s*Then\s+SelMode\s*:=\s*'(\w+)'",
                        body)
    assert default, "the handler no longer defaults an empty select"

    known = branches | {default.group(1)}
    assert known == set(pcb_module.FILTER_VARIANT_SELECT), (
        f"the handler knows {sorted(known)} but this tool advertises "
        f"{sorted(pcb_module.FILTER_VARIANT_SELECT)}")
