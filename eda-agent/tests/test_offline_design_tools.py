# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The design tools registered on EasyEDA must never need Altium.

The design family was Altium-only because ``design_execute_plan`` emits
Altium bridge commands. That was never true of the tools that BUILD and
check a plan, and on EasyEDA they now have somewhere to go.

Which ones are safe is MEASURED here, not judged by name, because the
names do not tell you: ``design_preview_plan`` reads like pure
computation over a plan and does reach the bridge, catching the failure
and returning a degraded answer. On a backend with no Altium it would
quietly report less than it appears to, and nothing would say so.

A static scan cannot answer this either. Every one of these tools looks
bridge-free in its own source; the call is one or two modules down,
behind a late import.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from eda_agent.tools import OFFLINE_DESIGN_TOOLS


class _Tripwire(Exception):
    pass


def _plan_json() -> str:
    import json

    return json.dumps({
        "spec": "a divider", "summary": "two resistors",
        "sheets": [{"name": "main"}],
        "parts": [
            {"refdes": "R1", "lib_ref": "RES", "value": "10k",
             "mpn": "M1", "footprint": "0603", "datasheet_url": "u"},
            {"refdes": "R2", "lib_ref": "RES", "value": "1k",
             "mpn": "M2", "footprint": "0603", "datasheet_url": "u"},
        ],
        "nets": [{"name": "MID", "pins": [{"refdes": "R1", "pin": "2"},
                                          {"refdes": "R2", "pin": "1"}]}],
    })


def _arguments_for(fn) -> dict:
    """Plausible values for every required parameter.

    Only required ones: the point is to reach the tool's body, not to
    exercise every branch.
    """
    out = {}
    for name, param in inspect.signature(fn).parameters.items():
        if param.default is not inspect.Parameter.empty:
            continue
        if "plan" in name:
            out[name] = _plan_json()
        elif "operations" in name:
            out[name] = []
        elif "path" in name:
            out[name] = "C:/nonexistent/x"
        else:
            out[name] = ""
    return out


@pytest.fixture
def design_tools(monkeypatch):
    """Every design tool, with the Altium bridge replaced by a tripwire."""
    import eda_agent.bridge as bridge_mod
    import eda_agent.bridge.altium_bridge as altium_bridge

    hits: list[str] = []

    def _no_bridge(*args, **kwargs):
        hits.append("bridge")
        raise _Tripwire("touched the Altium bridge")

    monkeypatch.setattr(bridge_mod, "get_bridge", _no_bridge)
    monkeypatch.setattr(altium_bridge, "get_bridge", _no_bridge)

    captured: dict = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    from eda_agent.tools.design import register_design_tools

    register_design_tools(_Mcp())
    return captured, hits


@pytest.mark.parametrize("name", ["design_datasheet_checklist"])
def test_an_offline_review_tool_never_reaches_the_altium_bridge(
        name, monkeypatch):
    """The review half of the same rule.

    design_datasheet_checklist returns a constant rule table. The
    datasheet discipline is about how a part is verified, not about
    which editor is open, so withholding it from a backend taught
    nothing except that the rules were Altium's. They are not.
    """
    import eda_agent.bridge as bridge_mod
    import eda_agent.bridge.altium_bridge as altium_bridge
    from eda_agent.tools import OFFLINE_REVIEW_TOOLS
    from eda_agent.tools.review import register_review_tools

    assert name in OFFLINE_REVIEW_TOOLS

    hits: list[str] = []

    def _no_bridge(*args, **kwargs):
        hits.append("bridge")
        raise _Tripwire("touched the Altium bridge")

    monkeypatch.setattr(bridge_mod, "get_bridge", _no_bridge)
    monkeypatch.setattr(altium_bridge, "get_bridge", _no_bridge)

    captured: dict = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    register_review_tools(_Mcp())
    result = asyncio.run(captured[name](**_arguments_for(captured[name])))

    assert not hits, (
        f"{name} is registered on the EasyEDA and KiCad backends and "
        f"reaches the Altium bridge")
    assert result.get("datasheet_rules"), (
        f"{name} answered without the rules it exists to return")


def test_the_offline_review_tool_is_actually_on_those_backends():
    """Registration, not just purity: the list is only worth having if
    the backends really get the tool."""
    from eda_agent.tools import register_backend

    for backend in ("easyeda", "kicad"):
        captured: dict = {}

        class _Mcp:
            def tool(self, *a, **k):
                def deco(fn):
                    captured[fn.__name__] = fn
                    return fn
                return deco

        register_backend(_Mcp(), backend)
        assert "design_datasheet_checklist" in captured, (
            f"the {backend} backend does not register the datasheet "
            f"checklist, so OFFLINE_REVIEW_TOOLS is inert")


@pytest.mark.parametrize("name", OFFLINE_DESIGN_TOOLS)
def test_the_tool_never_reaches_the_altium_bridge(name, design_tools):
    """Registered on EasyEDA, so reaching for Altium is a defect.

    Not "does not raise": a tool that reaches the bridge and CATCHES the
    failure returns a result and looks fine. What is checked is whether
    the bridge was touched at all.
    """
    captured, hits = design_tools

    fn = captured.get(name)
    assert fn is not None, (
        f"{name} is registered on the EasyEDA backend but no longer "
        f"exists; it would drop off that backend silently")

    before = len(hits)
    try:
        asyncio.run(fn(**_arguments_for(fn)))
    except _Tripwire:
        pass
    except Exception:
        # A tool refusing the stand-in arguments is fine. The question
        # is only whether it went looking for Altium.
        pass

    assert len(hits) == before, (
        f"{name} is registered on the EasyEDA backend and reaches the "
        f"Altium bridge. On a machine with no Altium it answers with a "
        f"degraded result rather than an error, so nothing reports it.")


def test_the_executor_is_not_offered_on_easyeda():
    """The one that genuinely cannot work here.

    ``design_execute_plan`` emits Altium bridge commands, so a plan run
    through it on EasyEDA fails at the first step. That is the whole
    reason the family was excluded, and it must stay excluded even as
    the rest is let in.
    """
    from eda_agent.tools import register_backend
    from eda_agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_backend(registry, "easyeda", "full")

    assert "design_execute_plan" not in registry.names
    assert "design_preview_plan" not in registry.names, (
        "design_preview_plan reaches the bridge and swallows the failure, "
        "so on EasyEDA it answers with less than it appears to")
    # And the plan-building half IS there, or this split achieved nothing.
    assert "design_layout_schematic" in registry.names
    assert "easyeda_emit_plan" in registry.names
