# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every KiCad tool answers in the ok-plus-reason envelope.

The EasyEDA surface earned this test first: 102 of its tools were named
by no test, and driving all of them found the contract already sound
and locked it in. KiCad had the same gap, 36 of 85 tools unnamed, and
the same two structural guards that cannot answer whether a tool RUNS.

The bridge fake is permissive on attribute access so a tool fails only
on its own account. A tool that then refuses is fine, provided the
refusal carries a reason a caller can act on; a tool that RAISES hands
an MCP client a stack trace, which is the one failure it cannot act on.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

import eda_agent.bridge.kicad_bridge as bridge_mod
from eda_agent.tools.kicad import register_kicad_tools


class _PermissiveBridge:
    """Answers any attribute with a callable returning an empty list.

    An empty list satisfies the read-wrapper's happy path; a tool that
    needs a different shape trips its own error handling, which is the
    behaviour under test, not a fault of the fake.
    """

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args, **kwargs: []


def _registered() -> dict:
    captured: dict = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    register_kicad_tools(_Mcp())
    return captured


def _empty_for(annotation) -> object:
    """Typed-empty argument values; outermost type decides.

    The EasyEDA version of this helper learned the hard way that
    matching substrings in declaration order reads list[list[float]] as
    a float and manufactures six phantom crashes.
    """
    text = str(annotation)
    if text.startswith("list"):
        return []
    if text.startswith("dict"):
        return {}
    if "bool" in text:
        return False
    if "float" in text:
        return 0.0
    if "int" in text:
        return 0
    return ""


_TOOLS = _registered()
_NAMES = sorted(_TOOLS)


def test_the_surface_is_broad_enough_to_be_worth_guarding():
    assert len(_NAMES) > 60, (
        f"only {len(_NAMES)} kicad tools registered; the registration "
        f"path changed and this file is guarding a remnant")


@pytest.mark.parametrize("name", _NAMES)
def test_a_kicad_tool_answers_rather_than_raising(name, monkeypatch):
    """Empty arguments of the right type; an answer either way.

    NOT asserting success: refusing empty arguments is often correct.
    What a caller needs is an object with ok, and on refusal a reason
    of more than a couple of words.
    """
    monkeypatch.setattr(bridge_mod, "get_kicad_bridge",
                        lambda: _PermissiveBridge())

    fn = _TOOLS[name]
    kwargs = {pname: _empty_for(p.annotation)
              for pname, p in inspect.signature(fn).parameters.items()
              if p.default is inspect.Parameter.empty}
    try:
        result = asyncio.run(fn(**kwargs))
    except Exception as exc:                       # noqa: BLE001
        raise AssertionError(
            f"{name} raised {type(exc).__name__}: {exc}. A tool refuses "
            f"with a reason; it does not throw at its caller") from exc

    assert isinstance(result, dict), (
        f"{name} returned {type(result).__name__}")
    assert "ok" in result, (
        f"{name} answered with keys {sorted(result)[:6]} and no 'ok'")
    if result["ok"] is False:
        reason = result.get("reason")
        assert isinstance(reason, str) and len(reason.split()) >= 3, (
            f"{name} refused with {reason!r}, which names no remedy")
