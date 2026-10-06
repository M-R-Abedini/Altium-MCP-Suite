# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Batch files reach Altium in the codepage its reader actually decodes.

The Pascal batch handlers read with AssignFile/ReadLn, which hands the
script engine bytes in the SYSTEM ANSI codepage. The tools previously
wrote latin-1, which was wrong twice on a CP1252 machine: 0x80-0x9F
characters (trademark sign, curly quotes, dashes) crashed the tool with
UnicodeEncodeError before any file was written, and a latin-1/ANSI
divergence would have reached Altium as the wrong character. The tools
now write the ANSI codepage and REFUSE values it cannot represent,
naming the offending character, instead of raising or silently
substituting.

Characters are built with chr() so no problematic literal appears in
this file's own text.
"""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.tools.library import _batch_encoding, register_library_tools

# In CP1252 (and most single-byte ANSI codepages) but NOT latin-1.
_DASH = chr(0x2014)
# In no ANSI codepage anywhere: outside the BMP entirely.
_EMOJI = chr(0x1F600)


class _CapturingBridge:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict]] = []

    async def send_command_async(self, command, params=None, **kwargs):
        self.commands.append((command, params or {}))
        return {"ok": True, "success": True}


def _tools() -> dict:
    captured: dict = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    register_library_tools(_Mcp())
    return captured


@pytest.fixture
def batch_env(monkeypatch, tmp_path):
    from tests.conftest import install_bridge_fake

    fake = _CapturingBridge()
    workspace = install_bridge_fake(monkeypatch, tmp_path, fake)
    return fake, workspace, _tools()


def test_a_dash_value_survives_the_round_trip(batch_env):
    """The exact character class that crashed under latin-1 now works."""
    fake, workspace, tools = batch_env
    value = f"10k {_DASH} do not fit"
    result = asyncio.run(tools["lib_batch_set_params"](
        assignments=[{"component_name": "RES_A", "param_name": "Note",
                      "param_value": value}]))
    assert result.get("ok", True) is not False, result
    assert fake.commands, "the bridge was never called"
    written = (workspace / "batch_params.txt").read_text(
        encoding=_batch_encoding())
    assert value in written, (
        "the batch file does not round-trip through the encoding the "
        "Pascal reader decodes")


def test_an_unrepresentable_value_is_refused_before_writing(batch_env):
    """No ANSI codepage carries an emoji; the tool must refuse with the
    character named, and must refuse BEFORE touching the workspace."""
    fake, workspace, tools = batch_env
    result = asyncio.run(tools["lib_batch_set_params"](
        assignments=[{"component_name": "RES_A", "param_name": "Note",
                      "param_value": f"bad {_EMOJI}"}]))
    assert result.get("ok") is False
    assert "cannot represent" in result.get("reason", "")
    assert _EMOJI in result.get("reason", ""), (
        "the refusal does not name the offending character")
    assert not fake.commands, "the bridge was called despite the refusal"
    assert not (workspace / "batch_params.txt").exists(), (
        "a batch file was written despite the refusal")


def test_batch_rename_applies_the_same_contract(batch_env):
    fake, workspace, tools = batch_env
    ok_result = asyncio.run(tools["lib_batch_rename"](
        assignments=[{"old_name": "OLD", "new_name": f"NEW{_DASH}1"}]))
    assert ok_result.get("ok", True) is not False, ok_result
    written = (workspace / "batch_rename.txt").read_text(
        encoding=_batch_encoding())
    assert f"NEW{_DASH}1" in written

    bad = asyncio.run(tools["lib_batch_rename"](
        assignments=[{"old_name": "OLD", "new_name": f"NEW{_EMOJI}"}]))
    assert bad.get("ok") is False
    assert "cannot represent" in bad.get("reason", "")
