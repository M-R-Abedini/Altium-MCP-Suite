# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""MCP Tools for Altium Designer.

Error-shape convention (two families, consistent within each):

- Bridge-backed tools (anything that round-trips Altium) report failures as
  ``{"error": "<message>", <count_field>: 0, ...}`` -- or raise, in which
  case the MCP layer surfaces the exception text. Successful payloads come
  from the Pascal handler verbatim.
- Offline calculators (``pcb_calc_*``, ``design_compute_*``, exporters)
  report ``{"ok": False, "reason": "<message>"}`` and ``{"ok": True, ...}``
  on success.

New tools should match the family they belong to rather than invent a third
shape.
"""

from .application import (
    register_application_tools,
    register_meta_tools,
)
from .guidance import register_guidance_tools
from .uiauto import register_uiauto_tools
from .project import register_project_tools
from .library import register_library_tools
from .generic import register_generic_tools
from .pcb import register_pcb_tools
from .review import register_review_tools
from .sim import register_sim_tools
from .design import register_design_tools
from .render import register_render_tools
from .audit import register_audit_tools
from .route import register_route_tools
from .easyeda import register_easyeda_tools
from .kicad import register_kicad_tools
from .eda import register_eda_tools
from .calc import register_calc_tools
from .plan import register_plan_tools
from .parts import register_parts_tools

# Recognised backends. "altium" is the default and stays the full historical
# suite; "kicad" exposes only the KiCad-native tools; "easyeda" drives
# EasyEDA Pro through its extension API; "both" unions Altium and KiCad for
# the rare user who drives both EDA tools from one server.
#
# "both" deliberately does NOT include easyeda. It exists for the two
# desktop tools a user is likely to run side by side, and quietly widening
# it would change what an existing setting means.
BACKENDS = ("altium", "kicad", "easyeda", "both")
DEFAULT_BACKEND = "altium"

# "full" advertises every tool. "minimal" registers them all internally
# but advertises only tool_catalog + tool_invoke, for MCP clients that
# cap tool count or stall serializing hundreds of schemas at startup.
# See eda_agent.tools.registry for why this beats merging tools into
# generic dispatchers.
TOOLSETS = ("full", "minimal")
DEFAULT_TOOLSET = "full"

#: Design tools that compute over a plan and never touch a bridge, so
#: they work on any backend.
#:
#: The design family was Altium-only because ``design_execute_plan``
#: emits Altium commands. That is still true of the executor, but it was
#: never true of the tools that BUILD and check a plan, and on EasyEDA
#: they now have somewhere to go: ``easyeda_emit_plan`` turns a plan into
#: EasyEDA calls and ``easyeda_run_plan`` runs them.
#:
#: MEASURED, not judged by name. Each was called with the Altium bridge
#: replaced by a tripwire, and only the ones that never reached it are
#: here. That mattered: ``design_preview_plan`` reads like pure
#: computation and does use the bridge, catching the failure and
#: returning a degraded result, so on a backend with no Altium it would
#: quietly answer with less than it appears to.
#:
#: A test re-runs that measurement, so this list cannot go stale by
#: someone adding a bridge call to a tool named here.
#:
#: The test only ever asks whether a NAMED tool stays offline, since it
#: iterates this list, so in principle a tool that stopped touching the
#: bridge would stay withheld. Re-measuring one is worth doing, but do
#: it against the RIGHT symbol: ``design_preview_plan`` looks offline to
#: a tripwire on ``eda_agent.bridge.get_bridge`` and is not, because
#: orchestrator._resolve_bridge imports
#: ``eda_agent.bridge.altium_bridge.get_bridge`` instead. It reaches the
#: bridge for symbol extraction, and on a backend with no Altium it
#: returns ok False with every count at zero, which is the degraded
#: answer this list exists to prevent shipping.
OFFLINE_DESIGN_TOOLS = (
    "design_add_circuit_block",
    "design_add_part",
    "design_apply_hierarchy",
    "design_autonomy_guide",
    "design_bom_file",
    "design_compose_netlist",
    "design_compute_component_value",
    "design_connect_bus",
    "design_describe_circuits",
    "design_edit_plan",
    "design_generate_bom",
    "design_get_discipline",
    "design_layout_schematic",
    "design_learn_from_layout",
    "design_list_circuit_blocks",
    "design_load_fab_profile",
    "design_next_action",
    "design_plan_hierarchy",
    "design_review_file",
    "design_session_log",
    "design_session_resume",
    "design_session_start",
    "design_session_status",
    "design_solve_netlist_file",
    "design_suggest_diff_pair_traces",
    "design_suggest_partition",
    "design_synthesize_rules",
    "design_validate_requirement",
)


def register_offline_design_tools(mcp) -> None:
    """Register the plan-building design tools that need no bridge.

    The design tools are defined together in one function, so they are
    captured into a throwaway registry and only the named ones are
    re-registered. That keeps the split in one list rather than
    scattering conditionals through a 2000-line module.
    """
    from .design import register_design_tools
    from .registry import ToolRegistry

    capture = ToolRegistry()
    register_design_tools(capture)

    missing = [n for n in OFFLINE_DESIGN_TOOLS if capture.get(n) is None]
    if missing:
        # A renamed tool would otherwise drop silently off this backend.
        raise RuntimeError(
            f"OFFLINE_DESIGN_TOOLS names tools that no longer exist: "
            f"{missing}")

    for name in OFFLINE_DESIGN_TOOLS:
        mcp.tool()(capture.get(name).fn)


#: Review tools that need no bridge. Measured the same way as
#: OFFLINE_DESIGN_TOOLS: called with the Altium bridge replaced by a
#: tripwire, and the tripwire did not fire. The datasheet discipline is
#: about how a part is verified, not about which editor is open, so
#: withholding it from a backend taught nothing except that the rules
#: are Altium's. They are not.
OFFLINE_REVIEW_TOOLS = (
    "design_datasheet_checklist",
)


def register_offline_review_tools(mcp) -> None:
    """Register the review tools that need no bridge.

    Same capture-and-reselect shape as the design half, and the same
    reason: the review tools are defined together, and a conditional
    per tool inside that module would scatter the split.
    """
    from .registry import ToolRegistry
    from .review import register_review_tools

    capture = ToolRegistry()
    register_review_tools(capture)

    missing = [n for n in OFFLINE_REVIEW_TOOLS if capture.get(n) is None]
    if missing:
        # A renamed tool would otherwise drop silently off this backend.
        raise RuntimeError(
            f"OFFLINE_REVIEW_TOOLS names tools that no longer exist: "
            f"{missing}")

    for name in OFFLINE_REVIEW_TOOLS:
        mcp.tool()(capture.get(name).fn)


def register_all_tools(mcp):
    """Register all Altium tools with the MCP server."""
    # Dialog-driving tools. Altium-only and Windows-only:
    # they press buttons in the GUI for the one operation that
    # has no API at all, so they do not belong on any other
    # backend and are registered with the Altium suite alone.
    register_uiauto_tools(mcp)
    register_application_tools(mcp)
    register_project_tools(mcp)
    register_library_tools(mcp)
    register_generic_tools(mcp)
    register_pcb_tools(mcp)
    register_review_tools(mcp)
    register_sim_tools(mcp)
    register_design_tools(mcp)
    register_render_tools(mcp)
    register_audit_tools(mcp)
    register_route_tools(mcp)


def register_backend(
    mcp, backend: str = DEFAULT_BACKEND, toolset: str = DEFAULT_TOOLSET,
) -> str:
    """Register the tool surface for one backend and return the name used.

    An unknown backend falls back to the default rather than raising, so a
    stray value in the environment can never leave the server toolless.

    Args:
        toolset: "full" advertises every tool. "minimal" advertises only
            tool_catalog and tool_invoke while keeping all the others
            reachable through them, for clients that cannot cope with a
            few hundred tools.
    """
    toolset = (toolset or DEFAULT_TOOLSET).strip().lower()
    if toolset not in TOOLSETS:
        toolset = DEFAULT_TOOLSET
    if toolset == "minimal":
        return _register_minimal(mcp, backend)
    return _register_full(mcp, backend)


def _register_minimal(mcp, backend: str) -> str:
    """Capture the whole surface, then advertise only the meta-tools."""
    from .registry import MINIMAL_TOOLS, ToolRegistry

    registry = ToolRegistry()
    name = _register_full(registry, backend)
    missing = [t for t in MINIMAL_TOOLS if t not in registry]
    if missing:
        # Advertising nothing usable would be worse than a full surface,
        # so fall back loudly rather than serve an unusable server.
        raise RuntimeError(
            f"minimal toolset needs {missing}, which the backend did not "
            f"register")
    for tool_name in MINIMAL_TOOLS:
        # These close over `registry`, so they discover and dispatch
        # across everything captured above even though the client sees
        # only these two.
        mcp.tool()(registry.get(tool_name).fn)
    return name


def _register_full(mcp, backend: str = DEFAULT_BACKEND) -> str:
    backend = (backend or DEFAULT_BACKEND).strip().lower()
    if backend not in BACKENDS:
        backend = DEFAULT_BACKEND
    # ONE SOURCE OF TRUTH FOR WHICH EDA IS ACTIVE. The neutral tools
    # (review_design and the rest) used to resolve their backend from
    # EDA_AGENT_BACKEND independently of this argument, so the two agreed
    # only while a single caller set both. Registering the EasyEDA
    # surface without also exporting the variable produced EasyEDA tools
    # over an ALTIUM resolver, and review_design reported a clean,
    # plausible review of an entirely different design.
    from ..core.backends import set_active_backend
    set_active_backend(backend)
    if backend in ("altium", "both"):
        register_all_tools(mcp)
    if backend in ("kicad", "both"):
        register_kicad_tools(mcp)
    # The engineering calculators (pcb_calc_*) are pure physics and
    # EDA-independent. Altium's register_all_tools already defines them, so add
    # them only for a KiCad-only server to reach calculator parity without
    # double-registering under "both".
    if backend == "easyeda":
        register_easyeda_tools(mcp)
        # The plan-building half of the design family. Not the executor:
        # design_execute_plan emits Altium commands and stays Altium-only,
        # which is why the whole family was excluded before EasyEDA had a
        # path of its own to run a plan.
        register_offline_design_tools(mcp)
    if backend in ("kicad", "easyeda"):
        register_calc_tools(mcp)
        register_plan_tools(mcp)
        register_offline_review_tools(mcp)
    # The EDA-agnostic main-flow tools (review_design, get_board_info,
    # list_components, list_nets) work on whichever backend is active, so they
    # register for every backend.
    register_eda_tools(mcp)
    # tool_catalog / tool_invoke describe the tool surface itself rather than
    # any one EDA tool, so they belong on every backend too. They also have to
    # exist for the minimal toolset to be possible at all.
    register_meta_tools(mcp)
    # tool_guide answers which tool for which DOCUMENT, and what is proven
    # impossible. That is a different question from tool_catalog's "what is
    # this called", and it is the one that has been getting wrong answers,
    # so it registers everywhere the catalogue does.
    register_guidance_tools(mcp)
    # Part sourcing is EDA-agnostic: the providers answer about parts, not
    # about Altium or KiCad, so both backends get them.
    register_parts_tools(mcp)
    return backend


__all__ = [
    "register_all_tools",
    "register_backend",
    "register_easyeda_tools",
    "register_kicad_tools",
    "register_eda_tools",
    "register_calc_tools",
    "register_plan_tools",
    "register_offline_design_tools",
    "OFFLINE_DESIGN_TOOLS",
    "register_offline_review_tools",
    "OFFLINE_REVIEW_TOOLS",
    "BACKENDS",
    "DEFAULT_BACKEND",
    "TOOLSETS",
    "DEFAULT_TOOLSET",
    "register_application_tools",
    "register_guidance_tools",
    "register_uiauto_tools",
    "register_meta_tools",
    "register_parts_tools",
    "register_project_tools",
    "register_library_tools",
    "register_generic_tools",
    "register_pcb_tools",
    "register_review_tools",
    "register_sim_tools",
    "register_design_tools",
    "register_render_tools",
    "register_audit_tools",
    "register_route_tools",
    "register_kicad_tools",
]
