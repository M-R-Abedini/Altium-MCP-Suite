# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Drift guard for the out-of-band tool-metadata registry.

``eda_agent.tools.metadata`` classifies every MCP tool by category /
maturity / interaction for the discovery layer, the docs generator, and
the bridge-audit interaction classes. These tests keep that registry
honest as tools come and go:

  - every registered tool resolves to a valid record (recognized
    category, allowed maturity, allowed interaction);
  - no override names a tool that no longer exists (stale-override drift);
  - the audit-identified modal/partial tools keep their classification.
"""

from __future__ import annotations

import asyncio

import pytest

from eda_agent.tools import metadata as M


@pytest.fixture(scope="module")
def tool_names() -> list[str]:
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_backend

    # register_backend, NOT register_all_tools: the latter is only the
    # Altium-specific suite. The backend-agnostic registrars
    # (register_eda_tools, register_meta_tools, register_parts_tools)
    # are layered on top of it, so the inner call builds a surface no
    # real server serves and makes their overrides look stale.
    mcp = FastMCP("test")
    register_backend(mcp, "altium")
    tools = asyncio.run(mcp.list_tools())
    return sorted(t.name for t in tools)


def test_every_tool_has_a_recognized_category(tool_names):
    uncategorized = [n for n in tool_names if M.category_of(n) == "other"]
    assert not uncategorized, (
        "tools with an unrecognized name prefix (add the prefix to "
        f"_CATEGORY_BY_PREFIX in metadata.py): {uncategorized}"
    )


def test_every_tool_resolves_to_valid_metadata(tool_names):
    for name in tool_names:
        rec = M.tool_metadata(name)
        assert rec["maturity"] in M.MATURITIES, (name, rec)
        assert rec["interaction"] in M.INTERACTIONS, (name, rec)


def test_no_stale_overrides(tool_names):
    registered = set(tool_names)
    stale_interaction = [n for n in M.INTERACTION_OVERRIDES if n not in registered]
    stale_maturity = [n for n in M.MATURITY_OVERRIDES if n not in registered]
    assert not stale_interaction, f"stale interaction overrides: {stale_interaction}"
    assert not stale_maturity, f"stale maturity overrides: {stale_maturity}"


def test_modal_and_partial_tools_are_classified():
    # The bridge audit's human-in-the-loop tools must not silently regress
    # to ``silent`` -- an LLM planning around them needs the warning.
    assert M.interaction_of("proj_sync_pcb") == M.MODAL
    assert M.interaction_of("proj_sync_schematic") == M.MODAL
    assert M.interaction_of("pcb_add_teardrops") == M.MODAL
    assert M.interaction_of("pcb_place_components") == M.PARTIAL


def test_readonly_queries_are_not_marked_silent():
    for name in ("pcb_get_nets", "proj_get_bom", "audit_find_via_antennas",
                 "pcb_calc_impedance"):
        assert M.interaction_of(name) == M.READONLY, name


def test_no_export_tool_claims_to_be_read_only(tool_names):
    """An export writes a file, and readonly says it does not.

    This is not a labelling nicety. The live sweep calls every tool
    classified readonly with its default arguments, so a misfiled
    export runs against a real installation and writes, or overwrites,
    a file nobody asked it to touch.

    easyeda_export_bom_html landed exactly there: the deriver filed it
    under "design" because it reads design.snapshot, design is an
    offline category, and offline falls back to readonly. It defaults
    to writing bom.html into the workspace. part_fetch fell into the
    same hole earlier for the same reason, which is why the rule is
    checked here rather than left to whoever adds the next one.
    """
    offenders = sorted(
        n for n in tool_names
        if "_export_" in n and M.interaction_of(n) == M.READONLY)
    assert not offenders, (
        f"these write a file but are classified readonly, so the sweep "
        f"would call them on a live install: {offenders}")


def test_catalog_is_sorted_by_category_then_name(tool_names):
    recs = M.catalog(tool_names)
    keys = [(r["category"], r["name"]) for r in recs]
    assert keys == sorted(keys)


def test_no_easyeda_tool_falls_back_to_the_flat_category():
    """`tool_catalog` filters BY category, so one bucket is no index.

    Every EasyEDA tool shares a prefix, so the prefix table would file
    all of them under "easyeda". That is not a cosmetic difference: a
    client with a tool-count limit browses by category to find anything
    at all, and Altium's surface is browsable in thirteen headings while
    this would be one of 128.

    The category is derived from the command each tool sends, so a new
    tool files itself. This catches the two ways that fails: a tool that
    sends nothing and has no override, and a command namespace nobody
    has mapped yet. Both land back in the flat bucket, and nothing else
    would notice.
    """
    from eda_agent.tools import register_backend
    from eda_agent.tools.metadata import category_of
    from eda_agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_backend(registry, "easyeda", "full")
    names = [n for n in registry.names if n.startswith("easyeda_")]

    assert len(names) > 100, (
        f"only {len(names)} easyeda tools registered; this guard is "
        f"checking almost nothing")

    unfiled = sorted(n for n in names if category_of(n) == "easyeda")
    assert not unfiled, (
        "these EasyEDA tools have no subject category, so they are "
        "reachable through tool_catalog only by listing everything. Map "
        "their command namespace in _EASYEDA_NAMESPACE_CATEGORY, or add "
        "an entry to _EASYEDA_CATEGORY_OVERRIDE:\n  " + "\n  ".join(unfiled))


def test_the_easyeda_categories_are_the_same_headings_altium_uses():
    """A backend with headings of its own is a second thing to learn.

    "pcb" has to mean pcb on either backend, or a client written against
    one cannot browse the other.
    """
    from eda_agent.tools import register_backend
    from eda_agent.tools.metadata import category_of
    from eda_agent.tools.registry import ToolRegistry

    altium = ToolRegistry()
    register_backend(altium, "altium", "full")
    known = {category_of(n) for n in altium.names}

    easyeda = ToolRegistry()
    register_backend(easyeda, "easyeda", "full")
    used = {category_of(n) for n in easyeda.names
            if n.startswith("easyeda_")}

    invented = sorted(used - known)
    assert not invented, (
        f"the EasyEDA tools use categories Altium does not: {invented}. "
        f"Either map them onto an existing heading or add the heading to "
        f"both backends deliberately.")
