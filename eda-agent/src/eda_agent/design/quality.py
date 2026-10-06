# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Score a SchematicCanvas's layout quality.

The pipeline used to produce one canvas and emit it. With this module
it can produce N candidate canvases, score each, and pick the lowest-
score one. That closes the iteration loop the SVG renderer was always
meant to serve.

A LayoutScore aggregates six metrics into one badness number. Lower is
better. Components, weights, and rationale:

| metric                 | weight | rationale                                    |
|------------------------|--------|----------------------------------------------|
| wire_crossings         |   100  | each crossing is a visual eyesore + extra junction risk |
| wires_through_bodies   |   400  | wires running through component bodies are illegible AND often short |
| body_overlaps          |  1000  | overlapping components are illegal; high penalty so the optimiser flees |
| aspect_ratio_penalty   |    50  | tall-skinny or wide-flat boards waste sheet; mild penalty |
| total_wire_length      |   0.01 | per mil; encourages compactness without dominating other terms |
| port_count             |    25  | excess power ports = layout spread too far for one cluster |

Weights are heuristic; tune them as failure modes shift. The breakdown
is preserved in the score so failures explain themselves.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from eda_agent.design.canvas import SchematicCanvas, WireSegment
from eda_agent.design.plan import DesignPlan

logger = logging.getLogger("eda_agent.design.quality")


@dataclass
class LayoutScore:
    """One layout's quality breakdown. ``total`` is the badness number."""

    total: float = 0.0
    wire_crossings: int = 0
    wires_through_bodies: int = 0
    body_overlaps: int = 0
    aspect_ratio_penalty: float = 0.0
    total_wire_length: int = 0
    port_count: int = 0
    alignment_penalty: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)

    def __lt__(self, other: "LayoutScore") -> bool:
        return self.total < other.total


# Tuned weights -- see module docstring.
_W_CROSSINGS = 100.0
_W_THROUGH_BODY = 400.0
_W_OVERLAP = 1000.0
_W_ASPECT = 50.0
_W_LENGTH = 0.01
_W_PORTS = 25.0
# Node alignment is a core drawing aesthetic; mild so it nudges toward tidy
# rows/columns without overriding crossings / overlaps.
_W_ALIGNMENT = 40.0


def score_canvas(
    canvas: SchematicCanvas,
    plan: Optional[DesignPlan] = None,
    *,
    sheet: Optional[str] = None,
) -> LayoutScore:
    """Return a badness score for one sheet of the canvas.

    Lower is better. The score is interpretable -- a value of 0 is a
    "perfect" layout (no crossings, no body intersections, square
    bbox, minimal wires). Real designs land in the hundreds-to-low-
    thousands.

    ``plan`` is optional; when supplied the wire-net membership is
    available for richer diagnostics (currently unused but plumbed in
    for future expansion).
    """
    sheet_name = sheet or (canvas.sheets[0].name if canvas.sheets else "main")
    instances = canvas.instances_on(sheet_name)
    wires = canvas.wires_on(sheet_name)
    ports = canvas.power_ports_on(sheet_name)
    if not instances:
        return LayoutScore()

    body_rects = [inst.world_bbox() for inst in instances]
    wire_segs = [(w.x1, w.y1, w.x2, w.y2) for w in wires]
    wire_nets = [w.net for w in wires]

    crossings = _count_wire_crossings(wire_segs, wire_nets)
    through_body = _count_wires_through_bodies(
        wire_segs, body_rects, instances
    )
    overlaps = _count_body_overlaps(body_rects)
    aspect_pen = _aspect_ratio_penalty(body_rects)
    length = _total_wire_length(wire_segs)
    port_count = len(ports)
    align_pen = _alignment_penalty(body_rects)

    # Try the learned model first; fall back to the heuristic if no
    # quality_model.json is bundled (fresh install, no votes yet).
    learned = _load_quality_model()
    if learned is not None:
        breakdown, total = _apply_learned_model(
            learned, crossings, through_body, overlaps,
            aspect_pen, length, port_count,
        )
    else:
        breakdown = {
            "crossings": crossings * _W_CROSSINGS,
            "through_body": through_body * _W_THROUGH_BODY,
            "overlaps": overlaps * _W_OVERLAP,
            "aspect": aspect_pen * _W_ASPECT,
            "length": length * _W_LENGTH,
            "ports": port_count * _W_PORTS,
            "alignment": align_pen * _W_ALIGNMENT,
        }
        total = sum(breakdown.values())

    return LayoutScore(
        total=total,
        wire_crossings=crossings,
        wires_through_bodies=through_body,
        body_overlaps=overlaps,
        aspect_ratio_penalty=aspect_pen,
        total_wire_length=length,
        port_count=port_count,
        alignment_penalty=align_pen,
        breakdown=breakdown,
    )


def _alignment_penalty(body_rects, tol: int = 100) -> float:
    """Fraction of bodies NOT sharing a row or column with another body.

    Body centres are snapped to ``tol`` mils; a body is aligned if its
    snapped centre-x or centre-y is shared by at least one other body.
    Returns 0.0 when every body lines up (best) and approaches 1.0 when
    none do. Single-body sheets are trivially aligned (0.0). ``body_rects``
    are ``SymbolBBox`` objects (x_min/y_min/x_max/y_max).
    """
    if len(body_rects) < 2:
        return 0.0

    def snap(v: float) -> int:
        return int(round(v / tol) * tol)

    cxs = [snap((r.x_min + r.x_max) / 2.0) for r in body_rects]
    cys = [snap((r.y_min + r.y_max) / 2.0) for r in body_rects]
    xcount: dict[int, int] = {}
    ycount: dict[int, int] = {}
    for x in cxs:
        xcount[x] = xcount.get(x, 0) + 1
    for y in cys:
        ycount[y] = ycount.get(y, 0) + 1
    aligned = sum(
        1 for i in range(len(body_rects))
        if xcount[cxs[i]] >= 2 or ycount[cys[i]] >= 2
    )
    return 1.0 - aligned / len(body_rects)


def _count_wire_crossings(
    segs: list[tuple[int, int, int, int]],
    nets: Optional[list[str]] = None,
) -> int:
    """Pairs of axis-aligned wires that cross.

    Only counts true crossings (horizontal segment intersecting
    vertical segment at an interior point of both). Coincident
    parallel overlaps are NOT counted here -- those would inflate the
    score for parallel buses that are legitimately stacked.

    When ``nets`` (a per-segment net name, parallel to ``segs``) is
    supplied, a crossing between two segments of the SAME net is NOT
    counted: same-net wires meeting mid-span is an electrical junction
    (drawn with a dot), not a readability fault. Only crossings between
    DIFFERENT nets -- where two unrelated signals visually overlap -- are
    counted. Without ``nets`` every crossing counts (back-compatible).
    """
    horiz = [(min(x1, x2), max(x1, x2), y1, (nets[i] if nets else None))
             for i, (x1, y1, x2, y2) in enumerate(segs) if y1 == y2]
    vert = [(x1, min(y1, y2), max(y1, y2), (nets[i] if nets else None))
            for i, (x1, y1, x2, y2) in enumerate(segs) if x1 == x2]
    count = 0
    for hx_lo, hx_hi, hy, hnet in horiz:
        for vx, vy_lo, vy_hi, vnet in vert:
            if hx_lo < vx < hx_hi and vy_lo < hy < vy_hi:
                # Same non-empty net => junction, not a crossing fault.
                if hnet is not None and hnet == vnet and hnet != "":
                    continue
                count += 1
    return count


def _count_wires_through_bodies(
    segs: list[tuple[int, int, int, int]],
    body_rects: list,
    instances: list,
) -> int:
    """Wires whose path crosses a component body interior.

    Skips wires that merely TOUCH the body's edge (those are legitimate
    pin connections). Counts only crossings of the body's INTERIOR --
    i.e., the wire passes from one side of the bbox to the other
    through the inside.
    """
    count = 0
    for (x1, y1, x2, y2) in segs:
        for bb in body_rects:
            if _segment_crosses_bbox_interior(x1, y1, x2, y2,
                                              bb.x_min, bb.y_min,
                                              bb.x_max, bb.y_max):
                count += 1
                break  # one crossing per wire is enough; avoid double-count
    return count


def _segment_crosses_bbox_interior(
    x1: int, y1: int, x2: int, y2: int,
    bx1: int, by1: int, bx2: int, by2: int,
) -> bool:
    """True iff an axis-aligned wire segment passes through the bbox interior.

    Edge-touching segments (the wire terminates on a pin at the body
    edge) do not count. Interior means the segment has a non-zero-length
    overlap strictly inside the bbox.
    """
    if x1 == x2:
        # Vertical segment. Crosses interior iff x is inside (bx1, bx2)
        # AND segment y-range intersects (by1, by2) on the interior.
        if not (bx1 < x1 < bx2):
            return False
        seg_lo, seg_hi = (y1, y2) if y1 <= y2 else (y2, y1)
        return seg_lo < by2 and seg_hi > by1 and seg_lo < by2 and seg_hi > by1
    if y1 == y2:
        if not (by1 < y1 < by2):
            return False
        seg_lo, seg_hi = (x1, x2) if x1 <= x2 else (x2, x1)
        return seg_lo < bx2 and seg_hi > bx1
    return False


def _count_body_overlaps(body_rects: list) -> int:
    """Pairs of component bboxes that intersect.

    Legitimate adjacency (sharing an edge but not overlapping) is OK;
    only strict bbox intersection counts.
    """
    count = 0
    for i in range(len(body_rects)):
        for j in range(i + 1, len(body_rects)):
            a, b = body_rects[i], body_rects[j]
            if (a.x_min < b.x_max and a.x_max > b.x_min
                    and a.y_min < b.y_max and a.y_max > b.y_min):
                count += 1
    return count


def _aspect_ratio_penalty(body_rects: list) -> float:
    """Penalty for non-square overall bbox.

    Schematics should fill the available sheet roughly evenly. Tall-
    skinny or wide-flat layouts waste space and force long wires.
    Returns a value in [0, 1] where 0 = perfect square, 1 = degenerate.
    """
    if not body_rects:
        return 0.0
    x_min = min(b.x_min for b in body_rects)
    y_min = min(b.y_min for b in body_rects)
    x_max = max(b.x_max for b in body_rects)
    y_max = max(b.y_max for b in body_rects)
    w = max(1, x_max - x_min)
    h = max(1, y_max - y_min)
    ratio = max(w, h) / min(w, h)
    # Map ratio [1.0, +inf) to penalty [0.0, 1.0+).
    # ratio=1.0 -> 0; ratio=2.0 -> 0.5; ratio=4.0 -> 0.75.
    return 1.0 - 1.0 / ratio


def _total_wire_length(segs: list[tuple[int, int, int, int]]) -> int:
    return sum(abs(x1 - x2) + abs(y1 - y2) for (x1, y1, x2, y2) in segs)


# ---------------------- learned model (Bradley-Terry) ----------------------


_DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "quality_model.json"
_MODEL_CACHE: Optional[dict[str, Any]] = None
_MODEL_CACHE_PATH: Optional[Path] = None


def _quality_model_path() -> Path:
    """Resolve the model file location. Env override for tests / custom builds."""
    override = os.environ.get("EDA_AGENT_QUALITY_MODEL")
    if override:
        return Path(override)
    return _DEFAULT_MODEL_PATH


def _load_quality_model() -> Optional[dict[str, Any]]:
    """Read the BT-trained weights from disk; cache in-memory.

    Returns None when no model file is present (fresh install with no
    votes yet). The cache is invalidated when the file path changes
    (via env var override) so tests stay deterministic.
    """
    global _MODEL_CACHE, _MODEL_CACHE_PATH
    path = _quality_model_path()
    if _MODEL_CACHE is not None and _MODEL_CACHE_PATH == path:
        return _MODEL_CACHE
    if not path.exists():
        _MODEL_CACHE = None
        _MODEL_CACHE_PATH = path
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("quality_model at %s is unreadable: %s", path, exc)
        _MODEL_CACHE = None
        _MODEL_CACHE_PATH = path
        return None
    _MODEL_CACHE = data
    _MODEL_CACHE_PATH = path
    return data


def reset_model_cache() -> None:
    """Force the next score_canvas call to re-read the model from disk.

    Useful right after running the trainer -- otherwise the running
    Python process keeps using the stale weights it loaded at startup.
    """
    global _MODEL_CACHE, _MODEL_CACHE_PATH
    _MODEL_CACHE = None
    _MODEL_CACHE_PATH = None


def _apply_learned_model(
    model: dict[str, Any],
    crossings: int,
    through_body: int,
    overlaps: int,
    aspect: float,
    length: int,
    port_count: int,
) -> tuple[dict[str, float], float]:
    """Convert raw features to a score using the learned weights.

    Bradley-Terry trains so that HIGHER s(canvas) = BETTER layout. To
    keep the same "lower is better" contract the heuristic uses (and
    that ``build_best_canvas_from_plan`` consumes via `min`), we
    negate the BT score before returning. The breakdown is per-feature
    so callers see which feature drove the score.
    """
    raw = model.get("weights_raw", {})
    intercept = float(model.get("intercept_raw", 0.0))
    contributions = {
        "crossings": float(raw.get("wire_crossings", 0.0)) * crossings,
        "through_body": float(raw.get("wires_through_bodies", 0.0)) * through_body,
        "overlaps": float(raw.get("body_overlaps", 0.0)) * overlaps,
        "aspect": float(raw.get("aspect_ratio_penalty", 0.0)) * aspect,
        "length": float(raw.get("total_wire_length", 0.0)) * length,
        "ports": float(raw.get("port_count", 0.0)) * port_count,
        "intercept": intercept,
    }
    bt_score = sum(contributions.values())
    # BT_score = goodness; we want badness, so negate.
    breakdown = {k: -v for k, v in contributions.items()}
    return breakdown, -bt_score
