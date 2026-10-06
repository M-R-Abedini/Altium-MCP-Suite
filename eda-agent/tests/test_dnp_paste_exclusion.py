# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Suppressing stencil paste on Not-Fitted components.

This tool EDITS THE BOARD, and the edit is one a fab house acts on: a
component whose paste is wrongly stripped arrives unsoldered. So the
selection matters as much as the mechanism, and most of what is pinned
here is about which components get touched.

The detection stays in audit_variant_not_fitted; this reads that list
rather than re-deriving it, so there is one definition of Not Fitted.
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
    return captured["pcb_apply_dnp_paste_exclusion"]


class _Bridge:
    def __init__(self, not_fitted=None, reply=None):
        self.not_fitted = not_fitted
        self.reply = reply or {"pads_changed": 4, "components_matched": 2}
        self.calls = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, params or {}))
        if command == "audit.variant_not_fitted":
            return self.not_fitted
        return self.reply

    def sent(self, command):
        return next(p for c, p in self.calls if c == command)

    def commands(self):
        return [c for c, _ in self.calls]


_TWO_DNP = {"variant": "Assembly-A",
            "items": [{"designator": "R5"}, {"designator": "C12"}]}


@pytest.mark.asyncio
async def test_the_dnp_list_comes_from_the_current_variant(monkeypatch):
    """Detection is not duplicated here. Re-deriving it would give two
    definitions of Not Fitted that could disagree."""
    bridge = _Bridge(not_fitted=_TWO_DNP)
    tool = _capture_tool(monkeypatch, bridge)
    await tool()

    assert "audit.variant_not_fitted" in bridge.commands()
    sent = bridge.sent("pcb.apply_dnp_paste_exclusion")
    assert sent["designators"] == "R5|C12"
    assert sent["restore"] == "false"


@pytest.mark.asyncio
async def test_an_explicit_list_overrides_the_variant(monkeypatch):
    bridge = _Bridge(not_fitted=_TWO_DNP)
    tool = _capture_tool(monkeypatch, bridge)
    await tool(designators=["U7"])

    # No point asking the variant when the caller already said who.
    assert "audit.variant_not_fitted" not in bridge.commands()
    assert bridge.sent("pcb.apply_dnp_paste_exclusion")["designators"] == "U7"


@pytest.mark.asyncio
async def test_nothing_not_fitted_means_nothing_is_edited(monkeypatch):
    """An empty variant list must not reach the board at all. Sending an
    empty designator field would be a mutating call with no selection."""
    bridge = _Bridge(not_fitted={"variant": "Base", "items": []})
    tool = _capture_tool(monkeypatch, bridge)
    out = await tool()

    assert "pcb.apply_dnp_paste_exclusion" not in bridge.commands()
    assert out["components_matched"] == 0
    assert "no Not-Fitted" in out["note"]


@pytest.mark.asyncio
async def test_dry_run_changes_nothing_and_names_the_targets(monkeypatch):
    """The board edit is the kind worth previewing: a wrong variant
    selection strips paste off parts that are meant to be fitted."""
    bridge = _Bridge(not_fitted=_TWO_DNP)
    tool = _capture_tool(monkeypatch, bridge)
    out = await tool(dry_run=True)

    assert "pcb.apply_dnp_paste_exclusion" not in bridge.commands()
    assert out["dry_run"] is True
    assert out["designators"] == ["R5", "C12"]
    assert out["source"] == "variant_not_fitted"


@pytest.mark.asyncio
async def test_restore_is_forwarded(monkeypatch):
    bridge = _Bridge(not_fitted=_TWO_DNP)
    tool = _capture_tool(monkeypatch, bridge)
    await tool(designators=["R5", "C12"], restore=True)
    assert bridge.sent("pcb.apply_dnp_paste_exclusion")["restore"] == "true"


@pytest.mark.asyncio
async def test_restore_refuses_to_guess_the_list(monkeypatch):
    """The fab-visible failure, made impossible rather than documented.

    Restoring by re-resolving the current variant is only correct while
    the variant has not changed since the apply. When it has, the
    components excluded under the old variant stay suppressed and the
    ones Not-Fitted under the new one get restored they never lost.
    Both halves are silent: the call succeeds and the pads look normal.

    So restore does not resolve anything. Refusing costs one argument;
    guessing wrong costs a board of unsoldered parts.
    """
    bridge = _Bridge(not_fitted=_TWO_DNP)
    tool = _capture_tool(monkeypatch, bridge)

    out = await tool(restore=True)

    assert out["ok"] is False
    assert "designators" in out["reason"]
    assert "use_current_variant" in out["reason"], (
        "a refusal must name the way forward, or it is just a wall")
    assert bridge.commands() == [], (
        "the refusal must come before any bridge traffic; asking the "
        "variant and then refusing still reads the live project")


@pytest.mark.asyncio
async def test_restore_can_still_use_the_variant_when_asked(monkeypatch):
    """The old behaviour stays reachable, but only on purpose."""
    bridge = _Bridge(not_fitted=_TWO_DNP)
    tool = _capture_tool(monkeypatch, bridge)

    out = await tool(restore=True, use_current_variant=True)

    assert out.get("ok") is not False
    assert bridge.sent("pcb.apply_dnp_paste_exclusion")["restore"] == "true"
    assert bridge.sent("pcb.apply_dnp_paste_exclusion")["designators"] == \
        "R5|C12"
    assert out["source"] == "variant_not_fitted"


@pytest.mark.asyncio
async def test_applying_is_unaffected_by_the_restore_rule(monkeypatch):
    """Apply SHOULD resolve from the current variant. That is its job.

    Pinned because the obvious over-correction is to require an explicit
    list everywhere, which would make the tool useless for the case it
    exists to serve.
    """
    bridge = _Bridge(not_fitted=_TWO_DNP)
    tool = _capture_tool(monkeypatch, bridge)

    out = await tool()

    assert out.get("ok") is not False
    assert bridge.sent("pcb.apply_dnp_paste_exclusion")["designators"] == \
        "R5|C12"


@pytest.mark.asyncio
async def test_a_restore_dry_run_is_refused_the_same_way(monkeypatch):
    """A preview of a call that would be refused is a false green."""
    bridge = _Bridge(not_fitted=_TWO_DNP)
    tool = _capture_tool(monkeypatch, bridge)

    out = await tool(restore=True, dry_run=True)

    assert out["ok"] is False
    assert bridge.commands() == []


@pytest.mark.asyncio
async def test_a_separator_in_a_designator_is_refused_not_stripped(
    monkeypatch
):
    """The list rides ONE field, pipe-delimited. A designator carrying a
    pipe would split into two names and strip paste off a component
    nobody selected. The first fix STRIPPED the pipe, which prevents
    the split but can produce a DIFFERENT valid name: 'R|1' became
    'R1', a real component. Refusing names what happened; nothing is
    sent."""
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)
    result = await tool(designators=["R5|U1", "C12"])

    assert result["ok"] is False
    assert "R5|U1" in result["reason"]
    assert bridge.commands() == [], (
        "the bridge was called despite the refusal")


@pytest.mark.asyncio
async def test_blank_designators_are_dropped_not_sent(monkeypatch):
    """An empty name would match nothing, but an empty SEGMENT in the
    pipe list is noise the Pascal has to reason about."""
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)
    await tool(designators=["R5", "  ", "", "C12"])
    assert bridge.sent(
        "pcb.apply_dnp_paste_exclusion")["designators"] == "R5|C12"


@pytest.mark.asyncio
async def test_the_reply_says_which_variant_the_list_came_from(monkeypatch):
    """An apply and a later restore must be comparable.

    Both resolve their targets the same way, so with no explicit list
    each follows whatever variant is open at the time. Exclude under
    one, switch, restore, and a component that is fitted in the new
    variant keeps its aperture suppressed: the call succeeds, the pad
    looks ordinary, and the part comes back unsoldered.

    Nothing can detect that from the two replies unless they say which
    variant they resolved against, which is what these keys are for.
    """
    bridge = _Bridge(not_fitted=_TWO_DNP)
    tool = _capture_tool(monkeypatch, bridge)
    result = await tool()

    assert result["source"] == "variant_not_fitted"
    assert result["variant"] == "Assembly-A", result


@pytest.mark.asyncio
async def test_an_explicit_list_reports_no_variant(monkeypatch):
    """It did not resolve against one, so claiming a variant would lie."""
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)
    result = await tool(designators=["R5"])

    assert result["source"] == "explicit"
    assert not result.get("variant"), result


@pytest.mark.asyncio
async def test_the_bridge_reply_is_not_overwritten(monkeypatch):
    """The handler's own fields win; these keys only fill gaps.

    setdefault, not assignment: if the Pascal ever reports a variant of
    its own, that one is authoritative and this must not clobber it.
    """
    bridge = _Bridge(not_fitted=_TWO_DNP,
                     reply={"pads_changed": 2, "variant": "FromPascal"})
    tool = _capture_tool(monkeypatch, bridge)
    result = await tool()

    assert result["variant"] == "FromPascal", result


@pytest.mark.asyncio
async def test_a_name_that_is_only_separators_is_refused(monkeypatch):
    """Under the old strip, a name of only pipes reduced to "" and left
    a stray delimiter in the payload, which skewed the handler's
    pipe-count of components_requested. Under refusal that hazard is
    unreachable: any pipe-carrying name refuses the whole call."""
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge)
    result = await tool(designators=["R5", "||", "C12"])

    assert result["ok"] is False
    assert bridge.commands() == [], (
        "the bridge was called despite the refusal")
