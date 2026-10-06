# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the tool-reference generator (roadmap 2.4)."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "gen_tool_reference", _REPO / "scripts" / "gen_tool_reference.py")
gen = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gen)


@pytest.fixture(scope="module")
def reference():
    return gen.build_reference()


@pytest.fixture(scope="module")
def tool_names():
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_all_tools
    m = FastMCP("t")
    register_all_tools(m)
    return [t.name for t in asyncio.run(m.list_tools())]


def _names_for(backend: str) -> list[str]:
    from eda_agent.tools import register_backend
    from eda_agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_backend(registry, backend, "full")
    return list(registry.names)


@pytest.fixture(scope="module")
def names_by_backend():
    return {backend: _names_for(backend)
            for backend in ("altium", "kicad", "easyeda")}


def test_reference_lists_every_tool(reference, tool_names):
    for name in tool_names:
        assert f"`{name}`" in reference, f"{name} missing from the reference"


def test_reference_lists_every_tool_on_every_backend(
        reference, names_by_backend):
    """The check above reads the ALTIUM surface only.

    Which made "lists every tool" true of a third of them. The hole is
    reachable rather than theoretical: if the generator stopped
    including a backend, the staleness check below would fail and tell
    the reader to regenerate, regenerating would make the committed file
    match again, and every test would pass with a whole backend missing
    from the documentation.
    """
    for backend, names in sorted(names_by_backend.items()):
        assert len(names) > 40, (
            f"only {len(names)} tools registered for {backend}; this "
            f"guard is checking almost nothing")
        missing = sorted(n for n in names if f"`{n}`" not in reference)
        assert not missing, (
            f"the {backend} backend has {len(missing)} tools absent from "
            f"the reference: {missing[:10]}")


def test_reference_has_legend_and_header(reference):
    assert "# Tool reference" in reference
    assert "**Maturity**" in reference and "**Interaction**" in reference
    assert "Auto-generated" in reference


def test_reference_groups_key_categories(reference):
    for cat in ("## application", "## pcb", "## design", "## audit", "## meta"):
        assert cat in reference


def test_reference_flags_modal_tools(reference):
    # A modal tool must be shown as modal so a reader plans around it.
    lines = [ln for ln in reference.splitlines() if "proj_sync_pcb`" in ln]
    assert lines and "modal" in lines[0]


def test_pipe_in_summary_is_escaped(reference):
    # Every table row has exactly 4 content cells (5 pipes): an unescaped
    # pipe in a summary would break the markdown table.
    for ln in reference.splitlines():
        if ln.startswith("| `"):
            assert ln.count("|") == 5, ln


def test_the_committed_reference_is_current(reference):
    """The file in docs/ must match what the generator produces now.

    Every check above reads freshly generated output, so all of them
    stay green while the COMMITTED file rots. That file is the one
    people actually read, and it carries per-tool maturity and
    interaction badges: a stale copy tells a reader an operation is
    safe or offline when the code says otherwise. Nothing regenerates
    it automatically, so nothing but this notices.

    Generation is deterministic (same input, byte-identical output), so
    this compares whole files rather than sampling.
    """
    committed = _REPO / "docs" / "TOOL_REFERENCE.md"
    assert committed.is_file(), f"{committed} is missing"
    on_disk = committed.read_text(encoding="utf-8")
    assert on_disk == reference, (
        "docs/TOOL_REFERENCE.md is out of date with the tool surface. "
        "Regenerate it with `python scripts/gen_tool_reference.py` and "
        "include the result in the same commit as the change that moved "
        "it.")


def test_the_maturity_legend_claims_no_verification_it_cannot_show():
    """A label that measures a REQUIREMENT must not read as a RESULT.

    `live_only` is computed from what a tool needs, not from anything
    anyone ran. The legend said "verified only on live Altium", which
    claimed verification for 182 EasyEDA tools nobody had ever run, and
    named the wrong EDA while doing it. A live EasyEDA session had 64
    of 65 reads fail, so the sentence was false in both halves.

    The same overstatement was already corrected once for the simulator
    label, which is why this is a guard and not just a fix.

    What IS measured is recorded per command in
    extensions/easyeda/verified.json by a real session, and reported
    through verified_live rather than through a badge.
    """
    import re

    legend = re.search(r"\*\*Maturity\*\*: (.+?)\n", gen.build_reference())
    assert legend, "the maturity legend is gone; this guard checks nothing"
    text = legend.group(1)

    live = re.search(r"`live_only` = ([^;.]+)", text)
    assert live, f"no live_only entry in the legend: {text}"
    wording = live.group(1).lower()

    assert "verified" not in wording, (
        f"the legend says live_only means {wording!r}. Nothing measures "
        f"that: the label is derived from what the tool needs. Say what "
        f"it requires, and leave verification to verified_live.")

    # Naming one EDA is wrong the moment the reference covers more than
    # one, which it does.
    assert "altium" not in wording, (
        f"the legend says live_only means {wording!r}, but most of the "
        f"tools carrying that label are not Altium tools")
