# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The server preamble must be true, and must actually reach the client.

tool_catalog and tool_guide only help a caller who thinks to call them,
and the recorded failures are the ones where nobody did: a capability
reported ABSENT four times while the tool existed under another
namespace. Server instructions are the only text a client sees before
choosing anything, which is why the pointer lives there.

That makes it a claim about the code, and claims about code need a
guard. Everything the preamble names is checked against the live
surface: the tools, the namespaces, and the mils convention. A preamble
that confidently names something gone is worse than none, because it is
read first and trusted most.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from eda_agent.server import SERVER_INSTRUCTIONS, build_server_instructions

_BACKENDS = ("altium", "kicad", "easyeda")


def _surface(backend: str, toolset: str = "full") -> set[str]:
    """Register one backend into a throwaway registry and list it.

    RESTORES THE ACTIVE BACKEND. register_backend records which backend
    was registered in a process-global, so enumerating all three at
    import time leaves the LAST one active for every test collected
    afterwards. That is not hypothetical: it flipped the active backend
    to easyeda and made the autonomy guide's stage tools disagree with
    the state-machine playbooks, in a test file that neither imports nor
    mentions this one.
    """
    from eda_agent.core.backends import _REGISTERED, set_active_backend
    from eda_agent.server import register_backend
    from eda_agent.tools.registry import ToolRegistry

    previous = _REGISTERED
    try:
        registry = ToolRegistry()
        register_backend(registry, backend, toolset)
        return {t.name for t in asyncio.run(registry.list_tools())}
    finally:
        set_active_backend(previous or "")


_SURFACES = {b: _surface(b) for b in _BACKENDS}


def _tools_named(text: str) -> set[str]:
    return set(re.findall(r"\b(?:tool_[a-z_]+)\b", text))


def test_the_preamble_is_not_empty():
    assert SERVER_INSTRUCTIONS.strip()


def test_it_actually_reaches_the_client():
    """A preamble built and never handed over teaches nobody anything."""
    from eda_agent.server import mcp

    assert mcp.instructions, (
        "FastMCP was constructed without instructions, so nothing the "
        "preamble says is ever seen")
    assert "tool_guide" in mcp.instructions


@pytest.mark.parametrize("backend", _BACKENDS)
def test_every_tool_the_preamble_names_exists(backend):
    for name in _tools_named(SERVER_INSTRUCTIONS):
        assert name in _SURFACES[backend], (
            f"the preamble tells every client to use {name!r}, which does "
            f"not exist on the {backend} backend")


def test_it_names_at_least_the_two_it_is_for():
    named = _tools_named(SERVER_INSTRUCTIONS)
    assert {"tool_guide", "tool_catalog"} <= named


@pytest.mark.parametrize("namespace", ["lib_", "pcb_", "sch_", "obj_"])
def test_every_namespace_it_teaches_is_real(namespace):
    """The document-to-namespace split is the preamble's central claim."""
    assert namespace in SERVER_INSTRUCTIONS
    assert any(t.startswith(namespace) for t in _SURFACES["altium"]), (
        f"the preamble teaches the {namespace} namespace, which no tool "
        f"uses any more")


def test_the_minimal_wording_does_not_name_an_unadvertised_tool():
    """Under minimal a client sees two tools. Telling it to call a third
    is a dead end for exactly the clients that most need the pointer."""
    advertised = _surface("altium", "minimal")
    assert "tool_guide" not in advertised, (
        "this test encodes the minimal toolset as two tools; if tool_guide "
        "is now advertised there, the full wording applies and this guard "
        "should be retired")

    text = build_server_instructions("minimal")
    assert "through tool_invoke" in text
    assert "call tool_guide" not in text
    for name in _tools_named(text):
        assert name in advertised or name == "tool_guide", (
            f"the minimal preamble names {name!r}, which a minimal client "
            f"can neither see nor reach")


def test_the_full_wording_says_call_it_directly():
    text = build_server_instructions("full")
    assert "call tool_guide" in text
    assert "through tool_invoke" not in text


def test_an_unknown_toolset_falls_back_rather_than_crashing():
    """register_backend tolerates a stray value, so this must too."""
    assert build_server_instructions("nonsense").strip()
    assert build_server_instructions("").strip()


def test_the_mils_claim_matches_the_rest_of_the_surface():
    """Stated to every client, so it has to hold on every backend."""
    assert "mils" in SERVER_INSTRUCTIONS
    for backend in _BACKENDS:
        assert any("mils" in (t or "") for t in _tool_descriptions(backend)), (
            f"the preamble promises mils everywhere but no {backend} tool "
            f"documents them")


def _tool_descriptions(backend: str):
    """Same restore discipline as _surface: this registers too."""
    from eda_agent.core.backends import _REGISTERED, set_active_backend
    from eda_agent.server import register_backend
    from eda_agent.tools.registry import ToolRegistry

    previous = _REGISTERED
    try:
        registry = ToolRegistry()
        register_backend(registry, backend, "full")
        return [t.description for t in asyncio.run(registry.list_tools())]
    finally:
        set_active_backend(previous or "")
