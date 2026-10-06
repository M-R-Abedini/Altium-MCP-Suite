# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Top-level orchestrator: plan JSON -> canvas -> Altium emit.

Wraps the three pure-Python stages (symbol extraction, pipeline, emit)
into one entry point the MCP tool layer can call. Returns a result dict
shaped compatibly with the legacy ``ExecutorResult.to_dict()`` so the
``design_execute_plan`` tool can flip between the two paths without
changing its return shape.

What this module is NOT: a placement algorithm. Layout decisions live
in ``pipeline.py``; this is just glue that wires bridge + extractor +
pipeline + emitter together.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional, Union

from pydantic import ValidationError

from eda_agent.design.emitter import EmitFailure, EmitResult, emit_canvas
from eda_agent.design.pipeline import (
    PipelineResult,
    build_best_canvas_from_plan,
    build_canvas_from_plan,
)
from eda_agent.design.plan import DesignPlan, PartStatus
from eda_agent.design.plan_erc import check_plan_erc
from eda_agent.design.render_svg import render_canvas_svg
from eda_agent.design.symbols import SymbolCache, SymbolExtractor

logger = logging.getLogger("eda_agent.design.orchestrator")


def _default_cache_dir() -> Path:
    """Where to keep extracted SymbolModel JSON between runs.

    Lives under the repo (or installed package) so a fresh checkout
    re-extracts; this is the natural place for derived caches that don't
    belong in user data.
    """
    # Repo root is two levels up from this file: src/eda_agent/design/.
    return Path(__file__).resolve().parents[3] / ".symbol_cache"


def execute_plan_via_canvas_from_json(
    plan_json: Union[str, dict],
    project_path: str,
    *,
    bridge: Any = None,
    cache_dir: Optional[Path] = None,
    write_preview_svg: bool = False,
    placement_hints: Optional[dict[str, dict[str, int]]] = None,
) -> dict[str, Any]:
    """Parse + validate + run the canvas-based execute pipeline.

    Args:
        plan_json: A DesignPlan as JSON string or dict.
        project_path: Where the resulting .PrjPcb lands.
        bridge: AltiumBridge for symbol extraction + emission. Defaults
            to the global one.
        cache_dir: Where to store the SymbolModel cache. Defaults to
            ``<repo>/.symbol_cache/``.
        write_preview_svg: If True, also dumps the canvas SVG to a
            sibling ``.preview.svg`` next to the project file. Cheap, and
            extremely useful for debugging layout issues without
            squinting at Altium.

    Returns:
        Dict with the legacy ExecutorResult shape plus a ``canvas`` key
        carrying the canvas's to_dict() snapshot. Includes:

        - ``ok``: True iff pipeline + emit both reported success.
        - ``project_path``, ``sheets_touched``.
        - ``placed``: list of {refdes, sheet, x_mils, y_mils, rotation}.
        - ``failures``: list of {refdes, code, reason}.
        - ``needs_creation``: refdes list (from pipeline notes).
        - ``notes``: free-text notes from pipeline + emitter.
        - ``nets_labelled``, ``power_ports_placed``: counts.
        - ``net_mismatches``: empty for now; canvas pipeline doesn't
          do post-emit netlist verification yet (the legacy executor
          did, via project.get_nets).
        - ``canvas``: ``canvas.to_dict()`` for caller-side inspection.
        - ``preview_svg_path``: where the SVG was written, when enabled.
    """
    out = _empty_result_dict(project_path)
    payload = _parse_plan(plan_json, out)
    if payload is None:
        return out
    plan = _validate_plan(payload, out)
    if plan is None:
        return out
    # Pydantic validation alone is not enough: it does not catch
    # cross-references (a net naming an unknown refdes, a part on a zone
    # that lives on another sheet) or connectivity faults (a pin on two
    # nets, contradictory power/ground flags, a floating net). The
    # standalone validation tool runs these; the execute path MUST enforce
    # them itself, or an electrically-broken plan reaches emit.
    if _enforce_plan_gates(plan, out):
        return out

    bridge = bridge or _resolve_bridge()
    if bridge is None:
        out["ok"] = False
        reason = f" ({_last_bridge_failure})" if _last_bridge_failure else ""
        out["notes"].append(
            "no Altium bridge available; symbol extraction needs Altium "
            f"to load each referenced SchLib.{reason}"
        )
        return out

    cache_dir = cache_dir or _default_cache_dir()
    cache = SymbolCache(cache_dir)
    extractor = SymbolExtractor(bridge, cache)

    # build_best_canvas_from_plan tries N placement variants (aspect-
    # rescaled from the base) and returns the lowest-scoring one. This
    # closes the iteration loop the SVG renderer was always meant to
    # serve: the pipeline no longer ships the first canvas it produces
    # if a cheaper compress-the-bbox variant scores better.
    pipeline_result = build_best_canvas_from_plan(
        plan, extractor, placement_hints=placement_hints,
    )
    _merge_pipeline_result(out, pipeline_result)
    if not pipeline_result.ok:
        return out

    if write_preview_svg:
        try:
            preview_path = Path(project_path).with_suffix(".preview.svg")
            preview_path.write_text(
                render_canvas_svg(pipeline_result.canvas), encoding="utf-8"
            )
            out["preview_svg_path"] = str(preview_path)
            out["notes"].append(f"preview SVG: {preview_path}")
        except Exception as exc:
            out["notes"].append(f"preview SVG write failed: {exc}")

    # Save plan + canvas snapshot as the pre-edit ground truth that
    # `design_learn_from_layout` later diffs against. The learner needs
    # the plan for role lookups; bundling both in one file avoids
    # drift. Lives next to the project file as `<project>.canvas.json`.
    snapshot_path = Path(project_path).with_suffix(".canvas.json")
    try:
        snapshot_payload = {
            "plan": payload,
            "canvas": pipeline_result.canvas.to_dict(),
            "parameter_stamps": pipeline_result.parameter_stamps,
        }
        snapshot_path.write_text(
            json.dumps(snapshot_payload, indent=2), encoding="utf-8"
        )
        out["canvas_snapshot_path"] = str(snapshot_path)
    except Exception as exc:
        out["notes"].append(f"canvas snapshot write failed: {exc}")

    emit_result = emit_canvas(
        pipeline_result.canvas,
        project_path,
        bridge,
        parameter_stamps=pipeline_result.parameter_stamps,
    )
    _merge_emit_result(out, emit_result)
    return out


def preview_plan_from_json(
    plan_json: Union[str, dict],
    output_svg_path: Optional[str] = None,
    *,
    bridge: Any = None,
    cache_dir: Optional[Path] = None,
    placement_hints: Optional[dict[str, dict[str, int]]] = None,
) -> dict[str, Any]:
    """Run plan -> canvas + render SVG, **without** emitting to Altium.

    Same as ``execute_plan_via_canvas_from_json`` minus the emit step.
    Useful for seeing what the layout will look like before paying the
    IPC cost of placing parts in Altium. Symbol extraction still talks
    to Altium (cache miss only), but the round-trips stop there; there
    is no project create, no place, no save.

    Args:
        plan_json: A DesignPlan as JSON string or dict.
        output_svg_path: Where to write the rendered SVG. If omitted,
            defaults to a temp file alongside the symbol cache so
            repeated previews don't accumulate.
        bridge: AltiumBridge; defaults to the global one.
        cache_dir: SymbolModel cache directory.

    Returns:
        Dict with:
            - ``ok``: True iff the pipeline ran without failures.
            - ``preview_svg_path``: where the SVG was written.
            - ``canvas``: snapshot of the produced canvas.
            - ``counts``: {placements, wires, labels, power_ports, junctions}.
            - ``notes`` / ``failures``: surfaced from the pipeline.
    """
    out: dict[str, Any] = {
        "ok": True,
        "preview_svg_path": None,
        "canvas": None,
        "counts": {},
        "notes": [],
        "failures": [],
    }
    payload = _parse_plan(plan_json, out)
    if payload is None:
        return out
    plan = _validate_plan(payload, out)
    if plan is None:
        return out

    bridge = bridge or _resolve_bridge()
    if bridge is None:
        out["ok"] = False
        reason = f" ({_last_bridge_failure})" if _last_bridge_failure else ""
        out["notes"].append(
            f"no Altium bridge available; symbol extraction needs Altium.{reason}"
        )
        return out

    cache_dir = cache_dir or _default_cache_dir()
    cache = SymbolCache(cache_dir)
    extractor = SymbolExtractor(bridge, cache)

    # Preview always uses the multi-try iteration so the agent sees the
    # SAME score it would emit; otherwise a hint-driven preview could
    # land at a different layout than the eventual execute step.
    pipeline_result = build_best_canvas_from_plan(
        plan, extractor, placement_hints=placement_hints,
    )
    out["canvas"] = pipeline_result.canvas.to_dict()
    out["counts"] = {
        "placements": pipeline_result.placement_count,
        "wires": pipeline_result.wire_count,
        "labels": pipeline_result.label_count,
        "power_ports": pipeline_result.power_port_count,
        "junctions": pipeline_result.junction_count,
    }
    for note in pipeline_result.notes:
        text = note.text if note.severity == "info" else f"[{note.severity}] {note.text}"
        out["notes"].append(text)
    for failure in pipeline_result.failures:
        out["failures"].append(failure.text)
    if not pipeline_result.ok:
        out["ok"] = False
        return out

    target = Path(output_svg_path) if output_svg_path else (
        _default_cache_dir() / "preview.svg"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(
            render_canvas_svg(pipeline_result.canvas), encoding="utf-8"
        )
        out["preview_svg_path"] = str(target)
    except Exception as exc:
        out["ok"] = False
        out["notes"].append(f"SVG write failed: {exc}")
    return out


def _empty_result_dict(project_path: str) -> dict[str, Any]:
    return {
        "ok": True,
        "project_path": project_path,
        "sheets_touched": [],
        "placed": [],
        "failures": [],
        "needs_creation": [],
        "notes": [],
        "nets_labelled": 0,
        "power_ports_placed": 0,
        "net_mismatches": [],
        "canvas": None,
        "preview_svg_path": None,
        "canvas_snapshot_path": None,
    }


def _parse_plan(
    plan_json: Union[str, dict], out: dict[str, Any]
) -> Optional[dict[str, Any]]:
    if isinstance(plan_json, dict):
        return plan_json
    try:
        return json.loads(plan_json)
    except json.JSONDecodeError as exc:
        out["ok"] = False
        # Match the legacy executor's wording so tools/tests that grep
        # for "invalid JSON" keep working across both execution paths.
        out["notes"].append(f"invalid JSON: {exc}")
        return None


def _validate_plan(
    payload: dict[str, Any], out: dict[str, Any]
) -> Optional[DesignPlan]:
    try:
        return DesignPlan.model_validate(payload)
    except ValidationError as exc:
        out["ok"] = False
        out["notes"].extend(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        )
        return None


def _enforce_plan_gates(plan: DesignPlan, out: dict[str, Any]) -> bool:
    """Run the mandatory pre-emit gates the canvas execute path owns.

    Mirrors the legacy executor: structural cross-check, then ERC-lite,
    then the needs_creation halt. Returns True if any gate tripped (the
    caller should return ``out`` immediately) and leaves ``out['ok']``
    False with the reasons in ``out['notes']`` / ``out['needs_creation']``.

    These run regardless of which placement engine follows, because they
    are about the plan being electrically sound and complete, not about
    layout.
    """
    # 1. Structural cross-check (unknown refdes in a net, a part pointing
    #    at a zone on another sheet, a zone on an unknown sheet).
    cross = plan.cross_check()
    if cross:
        out["ok"] = False
        out["notes"].extend(cross)
        return True

    # 2. ERC-lite: shorted pins, contradictory power/ground flags, floating
    #    nets (errors halt); decoupling / value warnings are surfaced but
    #    do not block.
    report = check_plan_erc(plan)
    for issue in report.warnings:
        out["notes"].append(f"[erc warning] {issue.code}: {issue.message}")
    if report.errors:
        out["ok"] = False
        for issue in report.errors:
            out["notes"].append(f"[erc error] {issue.code}: {issue.message}")
        return True

    # 3. needs_creation halt -- read from the structured plan, never from
    #    formatted note text. Emitting a partial design (skipping the
    #    unresolved parts) would mislead a reviewer about completeness.
    needs_creation = [
        p.refdes for p in plan.parts if p.status == PartStatus.NEEDS_CREATION
    ]
    if needs_creation:
        out["ok"] = False
        out["needs_creation"] = needs_creation
        out["notes"].append(
            "Plan contains needs_creation parts; refusing to instantiate a "
            "partial design. Resolve those parts (pick an existing-lib "
            "equivalent or author a new symbol) and re-run."
        )
        return True

    return False


_last_bridge_failure: str = ""


def _resolve_bridge() -> Any:
    """Lazy-import the global bridge so this module doesn't pull bridge
    code at import time (keeps it cheap for unit tests that don't need
    Altium).

    On failure the reason is kept in ``_last_bridge_failure`` so callers
    can put the REAL cause (e.g. "Altium is not running") in the result
    notes instead of a generic "no bridge".
    """
    global _last_bridge_failure
    try:
        from eda_agent.bridge.altium_bridge import get_bridge
    except ImportError as exc:
        _last_bridge_failure = f"bridge module unavailable: {exc}"
        return None
    try:
        bridge = get_bridge()
        _last_bridge_failure = ""
        return bridge
    except Exception as exc:
        _last_bridge_failure = f"{type(exc).__name__}: {exc}"
        logger.warning("get_bridge failed: %s", exc)
        return None


def _merge_pipeline_result(
    out: dict[str, Any], pr: PipelineResult
) -> None:
    out["canvas"] = pr.canvas.to_dict()
    out["nets_labelled"] = pr.label_count
    out["power_ports_placed"] = pr.power_port_count
    for note in pr.notes:
        text = note.text
        if note.severity != "info":
            text = f"[{note.severity}] {text}"
        out["notes"].append(text)
        # Surface needs_creation skips so the MCP caller can act on them.
        # Read the structured refdes off the note, never the formatted text
        # (the old token split returned the literal word "skipping").
        if note.refdes is not None and note.refdes not in out["needs_creation"]:
            out["needs_creation"].append(note.refdes)
    for failure in pr.failures:
        out["failures"].append({
            "refdes": "",
            "code": "PIPELINE_ERROR",
            "reason": failure.text,
        })
    if pr.failures:
        out["ok"] = False


def _merge_emit_result(out: dict[str, Any], er: EmitResult) -> None:
    out["sheets_touched"] = list(er.sheets_emitted)
    # Recover (refdes, sheet, x, y, rotation) placement records from the
    # canvas + the emit's placed_refdes set so the legacy result shape
    # ("placed": [...]) stays intact.
    canvas_dict = out.get("canvas") or {}
    canvas_instances = {
        i["refdes"]: i for i in canvas_dict.get("instances", [])
    }
    for refdes in er.placed_refdes:
        inst = canvas_instances.get(refdes)
        if inst is None:
            continue
        out["placed"].append({
            "refdes": refdes,
            "sheet": inst.get("sheet", "main"),
            "x_mils": inst.get("x", 0),
            "y_mils": inst.get("y", 0),
            "rotation": inst.get("rotation", 0),
        })
    for failure in er.failures:
        out["failures"].append({
            "refdes": failure.refdes,
            "code": failure.code,
            "reason": failure.reason,
        })
    out["notes"].extend(er.notes)
    if er.failures or not er.ok:
        out["ok"] = False
