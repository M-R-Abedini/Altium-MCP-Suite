# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""No Altium tool raises at its caller on typed-empty arguments.

The EasyEDA and KiCad surfaces carry a full envelope contract
(dict + ok + reason-on-refusal). Altium deliberately gets the WEAKER
guarantee here, and the reason is structural, not laziness: most Altium
tools pass the bridge reply through verbatim, so the reply SHAPE is
decided by the Pascal handlers. Asserting ok against a fake bridge
would test the fake. Shape convergence for Altium is task #39's
comparison against the real handlers, not this file.

What is honestly testable offline is the Python half: the validation
and argument handling ABOVE the bridge. A tool that raises on empty
arguments hands an MCP client a stack trace, which is the one failure
it cannot act on, and that property is the tool's own regardless of
what Pascal returns.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest


class _PermissiveBridge:
    """Answers any command with a minimal successful reply."""

    async def send_command_async(self, command, params=None, **kwargs):
        return {"ok": True, "success": True, "result": {}, "data": []}

    def send_command(self, command, params=None, **kwargs):
        return {"ok": True, "success": True, "result": {}, "data": []}

    def get_altium_status(self):
        # app_get_status passes this through verbatim, and the REAL
        # method is Python-side process inspection that always returns
        # a dict; the catch-all lambda's [] would misreport that tool
        # as a contract breach.
        return {"running": False, "attached": False}

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args, **kwargs: []


def _registered() -> dict:
    from mcp.server.fastmcp import FastMCP

    from eda_agent.tools import register_all_tools

    captured: dict = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    register_all_tools(_Mcp())
    return captured


def _empty_for(annotation) -> object:
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
#: Tools that shell out, spawn processes, or touch the filesystem by
#: design; driving them with garbage does real work rather than testing
#: validation. Each is named with the reason it is set aside.
_SET_ASIDE = {
    # Spawns kicad-cli / external processes with the given arguments.
    "lib_kicad_import": "invokes converters on the given path",
    "lib_easyeda_import": "reaches the network or reads the given file",
    "lib_extract_cse_zip": "reads and extracts the given archive",
    "lib_inspect_cse_zip": "reads the given archive",
    "lib_extract_intlib": "runs an external extractor",
    "part_search": "reaches distributor networks",
    "part_fetch": "reaches distributor networks",
}
_NAMES = sorted(n for n in _TOOLS if n not in _SET_ASIDE)


def test_the_surface_is_broad_enough_to_be_worth_guarding():
    assert len(_NAMES) > 300, (
        f"only {len(_NAMES)} altium tools registered; the registration "
        f"path changed and this file is guarding a remnant")


@pytest.mark.parametrize("name", _NAMES)
def test_an_altium_tool_does_not_raise_at_its_caller(name, monkeypatch,
                                                     tmp_path):
    # The first version of this file patched the get_bridge NAME on two
    # modules and called that isolation. Tool modules bind the name at
    # import time, so every test drove the real bridge and 33 request
    # files landed in the live workspace, among them
    # application.save_all. The shared helper injects at the singleton
    # global, tripwires real-bridge construction, and sandboxes the
    # workspace; its guarantees are proven by the tripwire tests in
    # test_tool_wrappers_smoke.py.
    from tests.conftest import install_bridge_fake

    fake = _PermissiveBridge()
    install_bridge_fake(monkeypatch, tmp_path, fake)

    fn = _TOOLS[name]
    kwargs = {pname: _empty_for(p.annotation)
              for pname, p in inspect.signature(fn).parameters.items()
              if p.default is inspect.Parameter.empty}
    try:
        result = asyncio.run(fn(**kwargs))
    except Exception as exc:                       # noqa: BLE001
        raise AssertionError(
            f"{name} raised {type(exc).__name__}: {exc}. A tool refuses "
            f"or reports; it does not throw at its caller") from exc

    assert isinstance(result, dict), (
        f"{name} returned {type(result).__name__}; a reply an MCP "
        f"client can read is an object")
