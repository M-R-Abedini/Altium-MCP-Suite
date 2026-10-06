# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The autonomous-design loop protocol (roadmap 2.1 client packaging).

The harness has the pieces: a durable session journal, a 13-stage state
machine (``design_next_action``), background jobs, project checkpoints, but
a client needs to know the *protocol* that ties them together. This module is
the single source of that protocol, surfaced as the ``design_autonomy_guide``
tool and the ``autonomous_design`` MCP prompt so any client (Claude Code,
Codex, ...) can drive a full spec-to-board run the same way.

Pure data; no Altium. Reuses the canonical stage list + playbooks so the
guide never drifts from what the state machine actually enforces.
"""

from __future__ import annotations

import re

from .session import STAGES
from .state_machine import MAX_STAGE_ATTEMPTS, STAGE_PLAYBOOKS

# The loop a client runs. Kept short and imperative: it is meant to be read
# once and followed.
LOOP_PROTOCOL = [
    "1. Call design_get_discipline once: hard rules + the DesignPlan schema.",
    "2. design_session_start(requirement): opens the durable journal. Keep "
    "the returned session_id.",
    "3. If a project is open or will be modified, app_checkpoint('before "
    "autonomous run') so the whole run is revertible.",
    "4. Loop: call design_next_action(session_id) and act on its status:",
    "   - proceed / retry: do the stage using its suggested_tools until the "
    "exit_gate is met, then design_session_log(event='stage_result', "
    "stage=<stage>, status='ok'). If you cannot finish without the user, log "
    "status='blocked' with a question and stop.",
    "   - blocked: put the open_question to the user; when answered, "
    "design_session_log(event='resolved', text=<answer>) and continue.",
    "   - complete: the pipeline is done; proceed to outputs review.",
    "5. Checkpoint again before each high-risk mutating stage (sch_to_pcb, "
    "routing, pours_tuning).",
    "6. Long engine runs (routing a dense board) can exceed the tool "
    "timeout: start them with design_job_start and poll design_job_status.",
    f"Bounded retries: a stage that fails {MAX_STAGE_ATTEMPTS} times escalates "
    "to a human question automatically: do not loop past it.",
]

# The non-negotiables, condensed. The full text is in design_get_discipline.
HARD_CONSTRAINTS = [
    "Datasheet-first: every device fact fetched + cited from the manufacturer "
    "datasheet; never fabricated.",
    "NDA isolation: never mine or reference other client designs.",
    "No third-party routing engines or account-gated APIs in the design loop.",
    "Verify quality render-and-look, not by score alone; the visual rubric is "
    "the shipping bar.",
    "No unverifiable safety tables: ship only sourced/verified values.",
]


_BACKEND_TOOLS: dict = {}


def _registered_tools(backend: str) -> set:
    """Tool names the given backend registers, computed once per backend.

    Late import on purpose: eda_agent.tools imports this module, so a
    module-level import is circular. Cached because registering the
    whole surface is not free and the answer cannot change within a
    process.
    """
    if backend in _BACKEND_TOOLS:
        return _BACKEND_TOOLS[backend]

    captured: dict = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    try:
        from ..tools import register_backend

        register_backend(_Mcp(), backend)
    except Exception:                                # noqa: BLE001
        # A guide naming every tool beats one naming none, so an
        # unexpected registration failure falls back to no filtering.
        _BACKEND_TOOLS[backend] = set()
        return set()
    _BACKEND_TOOLS[backend] = set(captured)
    return _BACKEND_TOOLS[backend]


#: Altium tool -> the tool that does the same job on another backend.
#: Only entries VERIFIED to exist are useful, so a test asserts every
#: value is registered somewhere; a mapping to a tool nobody has is
#: worse than no mapping, because it reads as available.
_EQUIVALENTS = {
    "pcb_place_components": "easyeda_place_pcb_components",
    "pcb_move_components": "easyeda_snap_components_to_grid",
    "proj_compare_sch_pcb": "easyeda_compare_schematic_pcb",
    "design_execute_plan": "easyeda_run_plan",
    "design_lint_report": "easyeda_review_board",
    "design_audit_schematic": "easyeda_review_board",
    "pcb_run_drc": "run_drc",
    "proj_run_erc": "run_erc",
    "lib_create_symbol": "easyeda_create_symbol",
    "lib_search": "easyeda_search_devices",
    "pcb_create_design_rule": "easyeda_create_net_class",
    "pcb_modify_layer": "easyeda_modify_layer",
    "pcb_place_tracks": "easyeda_add_polyline",
    "pcb_place_via": "easyeda_add_via",
    "pcb_start_polygon_placement": "easyeda_add_zone",
    "proj_generate_fab_package": "easyeda_export_gerber",
    "proj_export_step": "easyeda_export_3d",
    # Named by the DISCIPLINE rules rather than the stage playbooks.
    # Every one was checked against the live registry before being
    # added; a proposed easyeda_add_polygon was rejected because no
    # such tool exists, which is the check earning its place.
    "lib_add_pins": "easyeda_add_pins",
    "lib_add_symbol_rectangle": "easyeda_add_schematic_rectangle",
    "lib_add_symbol_arc": "easyeda_add_arc",
    "lib_add_symbol_lines": "easyeda_add_polyline",
    "lib_add_footprint_pads": "easyeda_add_pads",
    "lib_add_footprint_pad": "easyeda_add_pad",
    "lib_add_footprint_tracks": "easyeda_add_polyline",
    "lib_add_footprint_track": "easyeda_add_line",
    "lib_create_footprint": "easyeda_create_footprint",
    "design_validate": "design_validate_plan",
    "app_set_active_document": "easyeda_open_document",
    "app_checkpoint": "easyeda_checkpoint",
    "obj_batch_modify": "easyeda_modify_pcb_components",
    "sch_place_components": "easyeda_place_schematic_components",
    "sch_place_wires": "easyeda_add_wires",
    "sch_set_components_parameters":
        "easyeda_set_schematic_component_properties",
    # The one-call generators of rule 9. These were named in the text
    # all along and went unmapped because they are written WITH their
    # call signature, so the substitution never matched them and the
    # gap was invisible: the rule read as adapted because the prose
    # around it was.
    "lib_create_standard_footprint": "easyeda_create_standard_footprint",
    "lib_create_ic_symbol": "easyeda_create_ic_symbol",
    "lib_create_passive_symbol": "easyeda_create_passive_symbol",
    # Rule 15 lists five symbol-primitive helpers and four were mapped.
    # The fifth was rejected earlier on the grounds that no equivalent
    # existed, which was true of the name that was checked
    # (easyeda_add_polygon) and false of the tool that exists. Checking
    # a guessed name proves nothing when it comes back absent; this one
    # was found by searching the registry instead.
    "lib_add_symbol_polygon": "easyeda_add_schematic_polygon",
    # Read-side tools. A planner that cannot read the board state it is
    # about to change is the failure these prevent.
    "pcb_get_components": "easyeda_get_components",
    "pcb_check_placement_collision": "easyeda_audit_placement_collisions",
    "proj_get_nets": "easyeda_get_nets",
    "proj_get_unconnected_pins": "easyeda_get_unconnected_pins",
    "obj_crossref_net": "easyeda_cross_probe",
    # Both renders map to the same tool: EasyEDA draws whichever
    # document is open rather than offering one per editor.
    "sch_render_svg": "easyeda_render_image",
    "pcb_render_svg": "easyeda_render_image",
    # design_visual_review renders through the Altium bridge, so on
    # this backend the equivalent is the editor's own render. Two
    # stages, placement and verification, were telling the caller to
    # run a tool that does not exist here.
    "design_visual_review": "easyeda_render_image",
    # The placement solver is EDA-agnostic; only the reading and
    # writing differed, and the EasyEDA plumbing now exists. Without
    # this mapping the placement stage names a tool the backend lacks,
    # which is the whole stage.
    "pcb_plan_placement": "easyeda_plan_placement",
    # The grid router is EDA-agnostic too; only the geometry fetch
    # differed. route_plan_repairs is NOT mapped and must not be: it
    # reads a paired-primitive DRC shape EasyEDA does not report, so a
    # repair plan there escalates everything and looks like a working
    # tool. See task #48.
    "route_plan": "easyeda_route_plan",
    # The schematic-to-board update, which is the whole of the
    # sch_to_pcb stage. EasyEDA calls it importChanges and the tool has
    # existed all along; nothing connected the two, so the stage
    # reported its only tool as absent on this backend and read as
    # impossible when it was merely unmapped.
    "pcb_build_from_project": "easyeda_import_schematic_changes",
    # Stitching is geometry plus one via call, and both existed. The
    # pours_tuning stage named the Altium tool and reported it absent.
    "pcb_place_stitching_vias": "easyeda_place_stitching_vias",
    # A power port is a net flag here, not a sheet port; net ports are
    # EasyEDA's cross-sheet connector and would be the wrong glyph.
    "sch_place_power_port": "easyeda_create_net_flag",
    "sch_place_net_label": "easyeda_create_net_label",
}


def _stage_tools(stage: str, available: set) -> tuple:
    """(tools you can call here, tools this stage wants but lacks).

    The playbooks were written against Altium and name Altium tools.
    On EasyEDA 33 of the 50 named across the 13 stages are not
    registered, and six stages name nothing that exists there, so an
    agent following the guide was told to call tools it does not have.
    Naming what is absent, rather than quietly dropping it, keeps a
    thin stage legible: "there is no tool for this here" is guidance,
    a silently empty list is a puzzle.
    """
    wanted = STAGE_PLAYBOOKS[stage]["tools"]
    if not available:                    # filtering unavailable
        return list(wanted), []
    usable, absent = [], []
    for tool in wanted:
        if tool in available:
            usable.append(tool)
            continue
        # The same job under another name is still the job. Only
        # substitute a tool this backend really registers.
        swap = _EQUIVALENTS.get(tool)
        if swap and swap in available and swap not in usable:
            usable.append(swap)
        elif not swap or swap not in available:
            absent.append(tool)
    return usable, absent


def _adapt_lines(lines, backend: str, available: set) -> list:
    """Swap Altium tool names in guidance TEXT for the local ones.

    Safe here for the same measured reason it is safe in the discipline
    document: neither the loop protocol nor the hard constraints
    contains a sentence explaining that a tool is unavailable, so every
    mention is a plain instruction where the equivalent reads
    correctly. Checked with seven phrasings, all absent.

    A name with no equivalent is LEFT ALONE rather than deleted: a
    sentence with a hole in it is worse than one naming a tool the
    reader will discover they lack, and the stage entries already
    report absences explicitly.

    The ``backend == "altium"`` test below is belt-and-braces, and
    honestly so: mutation-testing it produced an EQUIVALENT mutant,
    because no mapping whose replacement exists on Altium has a key
    appearing in this prose, so removing the test changes nothing
    today. It stays because that is a property of the current table,
    not of the code, and the next entry could break it silently.
    """
    if backend == "altium" or not available:
        return list(lines)
    out = []
    for line in lines:
        for altium_tool, swap in _EQUIVALENTS.items():
            if swap in available and altium_tool in line:
                # Word-bounded, because a plain replace rewrites the
                # FRONT of a longer name. "design_validate" is a key
                # and "design_validate_plan" is a real tool, so a
                # substring swap turned an exit gate into
                # "design_validate_plan_plan": a name nothing
                # registers, which then collected a "(not available on
                # this backend)" annotation and told the client its own
                # working tool was missing. Two more pairs in the table
                # (footprint pad/pads, track/tracks) are only safe
                # today by dict ordering, which is not a property worth
                # relying on.
                line = re.sub(rf"\b{re.escape(altium_tool)}\b", swap, line)
        # A step whose tools are ALL missing is not merely naming
        # something unavailable, it is advising a capability that does
        # not exist here: the long-run step tells a client to start a
        # background job and poll it, and EasyEDA has no job system.
        # Saying so beats leaving advice that cannot be taken.
        # STAGE names look like tool names and are not tools. Step 5
        # lists sch_to_pcb, routing and pours_tuning as stages to
        # checkpoint before; reading sch_to_pcb as an absent tool
        # annotated a step whose actual tool, easyeda_checkpoint, is
        # right there in the sentence. The same trap caught the docs
        # guard earlier.
        named = {n for n in _TOOL_NAME.findall(line)
                 if n.startswith(_TOOL_PREFIXES) and n not in STAGES}
        if named and not (named & available):
            line += " (not available on this backend)"
        out.append(line)
    return out


_TOOL_NAME = re.compile(r"\b([a-z]+_[a-z0-9_]+)\b")
_TOOL_PREFIXES = ("lib_", "pcb_", "sch_", "proj_", "obj_", "app_",
                  "design_", "audit_", "easyeda_", "kicad_")


def _stage_entry(stage: str, available: set, backend: str = "") -> dict:
    """One stage of the playbook, adapted to the backend asking.

    The goal and the exit gate go through the same adaptation as the
    loop protocol. They were skipped before, and the omission was easy
    to miss because the tools list beside them WAS adapted: a stage
    read as fully translated while its exit gate still told an EasyEDA
    client to wait on an Altium tool. An exit gate is the sentence that
    decides when a stage is finished, so naming a tool the client
    cannot call is the one place a wrong name stalls the run.
    """
    usable, absent = _stage_tools(stage, available)
    play = STAGE_PLAYBOOKS[stage]
    goal, gate = _adapt_lines(
        [play["goal"], play["exit_gate"]], backend or "altium", available)
    entry = {
        "stage": stage,
        "goal": goal,
        "tools": usable,
        "exit_gate": gate,
    }
    if absent:
        entry["tools_not_on_this_backend"] = absent
    if not usable:
        entry["note"] = (
            "no tool for this stage is registered on this backend; do "
            "the equivalent by hand in the editor, or skip the stage")
    return entry


def autonomy_guide() -> dict:
    """The full autonomous-design protocol as structured data."""
    from ..core.backends import active_backend_name

    backend = active_backend_name()
    available = _registered_tools(backend)
    return {
        "overview": (
            "Drive a full spec-to-board design by looping the state machine: "
            "call design_next_action, do what it says, log the result, repeat "
            "until complete or blocked. The server owns sequencing and gates, "
            "so you never memorize the workflow."
        ),
        "loop": _adapt_lines(LOOP_PROTOCOL, backend, available),
        "backend": backend,
        "stages": [
            _stage_entry(st, available, backend) for st in STAGES
        ],
        "constraints": HARD_CONSTRAINTS,
        "resume": (
            "A run survives context loss: a fresh client calls "
            "design_session_resume (or design_next_action) with the session_id "
            "and picks up from recorded state."
        ),
    }


def autonomy_prompt_text(requirement: str = "") -> str:
    """Render the guide as a single instruction block for an MCP prompt."""
    g = autonomy_guide()
    lines = [
        "# Autonomous PCB design",
        "",
        g["overview"],
        "",
        "## Loop",
        *g["loop"],
        "",
        "## Hard constraints",
        *[f"- {c}" for c in g["constraints"]],
        "",
        "## Pipeline stages",
        *[f"- {s['stage']}: {s['goal']}" for s in g["stages"]],
        "",
        g["resume"],
    ]
    if requirement.strip():
        lines += [
            "",
            "## This run's requirement",
            requirement.strip(),
            "",
            "Begin now: call design_get_discipline, then "
            "design_session_start with the requirement above.",
        ]
    return "\n".join(lines)
