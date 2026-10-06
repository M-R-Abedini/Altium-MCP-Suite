# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Offline benchmark harness: run one golden DesignPlan end to end.

``run_benchmark`` exercises the whole offline design chain on a plan --
validation + ERC-lite, hierarchy split, the schematic pipeline, the
constructive PCB placer, and the Manhattan router -- and returns one
scores dict. It never touches Altium: symbols and footprints are
synthesized deterministically from the plan itself (pin ids referenced
by the nets plus the package token in the footprint name), so a golden
plan JSON is the only input a benchmark needs.

The numbers are regression tripwires, not absolute quality claims: the
synthetic footprints are generic (a "TQFP32" here is a 50 mil pitch
quad, not the real 0.8 mm part), so a score is comparable only against
the same harness version on the same plan.

Units: schematic canvas and all PCB values (placement, routing,
footprints) are MILS; the plan's Zone boxes (mm) are advisory only and
never read here.

Determinism: the placer is seeded (``seed`` argument), the router and
the schematic pipeline are seed-free deterministic, and every synthesis
step iterates sorted containers -- same plan in, same dict out.
"""

from __future__ import annotations

import math
from typing import Any, Optional, Union

from pydantic import ValidationError

from eda_agent.design.hierarchy import apply_hierarchy, plan_hierarchy
from eda_agent.design.net_classes import classify_nets
from eda_agent.design.pcb_placement import (
    ConstructOptions,
    ConstructResult,
    DesignRules,
    construct_placement,
)
from eda_agent.design.pipeline import build_canvas_from_plan
from eda_agent.design.plan import DesignPlan, Part
from eda_agent.design.plan_erc import check_plan_erc
from eda_agent.design.quality import score_canvas
from eda_agent.design.symbols import (
    SymbolBBox,
    SymbolExtractor,
    SymbolModel,
    SymbolPin,
)
from eda_agent.placement.autoplace import PlaceComp, PlaceNet, PlacePin
from eda_agent.route.router import route_geometry

__all__ = [
    "run_benchmark",
    "SyntheticSymbolExtractor",
    "synth_footprint",
    "DEFAULT_HIERARCHY_THRESHOLD",
]

# Parts-per-sheet threshold above which the hierarchy splitter runs
# before the schematic pipeline (matches plan_hierarchy's default).
DEFAULT_HIERARCHY_THRESHOLD = 20

# Placement target utilization. Lower than the engine default (0.55) on
# purpose: the benchmark routes every net as discrete tracks (no pours),
# which needs more channel room than a real board where power is poured.
_PLACE_UTILIZATION = 0.35

# Routing grid pitch (mils). Finer than the router default (25) because
# the auto-sized benchmark boards are small; 25 mil cells leave too few
# channels between 50 mil pitch IC pads.
_ROUTE_GRID_PITCH_MILS = 10

# Routing rules for the synthetic 2-layer board (mils).
_ROUTE_RULES: dict[str, Any] = {
    "clearance_mils": 10,
    "track_width_mils": {"default": 10, "power": 15, "ground": 15,
                         "high_current": 15, "switch": 15},
    "via_size_mils": 40,
    "via_drill_mils": 20,
    "layers": ["TopLayer", "BottomLayer"],
}


# ---------------------------------------------------------------------------
# Pin inventory
# ---------------------------------------------------------------------------


def _pin_sort_key(pin_id: str) -> tuple[int, int, str]:
    """Numeric pin ids sort numerically, names after, both stable."""
    try:
        return (0, int(pin_id), "")
    except ValueError:
        return (1, 0, pin_id)


def _pins_by_refdes(plan: DesignPlan) -> dict[str, list[str]]:
    """Sorted distinct pin ids each refdes is referenced with in nets."""
    by_ref: dict[str, set[str]] = {}
    for net in plan.nets:
        for pr in net.pins:
            by_ref.setdefault(pr.refdes, set()).add(str(pr.pin))
    return {r: sorted(p, key=_pin_sort_key) for r, p in by_ref.items()}


def _full_pin_ids(referenced: list[str]) -> list[str]:
    """Expand all-numeric pin references to the full 1..max range (the
    physical package has every pin even if the plan nets only some);
    non-numeric ids pass through. Always at least pins 1 and 2."""
    if referenced and all(p.isdigit() for p in referenced):
        top = max(2, max(int(p) for p in referenced))
        return [str(i) for i in range(1, top + 1)]
    return referenced if len(referenced) >= 2 else ["1", "2"]


# ---------------------------------------------------------------------------
# Synthetic schematic symbols
# ---------------------------------------------------------------------------


def _synth_symbol(lib_path: str, lib_ref: str, pin_ids: list[str],
                  prefix: str) -> SymbolModel:
    """Generic symbol (mils): 2-pin parts get the canonical horizontal
    passive shape; everything else a dual-column box, 100 mil pitch.

    LIMITATION, and it matters when choosing what to measure here: pins
    are split into columns by INDEX (DIP order), and every pin is typed
    ``passive``. Nothing consults electrical direction, because a
    DesignPlan does not carry any: ``PinRef`` is only refdes + pin, and
    ``Net.role`` is functional ("decoup_cap", "feedback", "clock"), not
    directional.

    So on a synthetic benchmark an output pin lands wherever its index
    falls. On the 555 plan, pin 3 (OUT) sits in the LEFT column, and a
    placer that honours pin sides then correctly puts the LED branch to
    the left of the IC. Left-to-right signal flow is therefore NOT
    expressible on these benchmarks, and any flow-direction metric
    evaluated against them scores this function rather than the layout
    engine.

    Real designs are unaffected: ``SymbolExtractor`` reads
    ``electrical_type`` off the actual Altium symbol, so pin sides and
    directions there are genuine. Validate flow conventions on real
    symbols via the bridge, not here.
    """
    if len(pin_ids) <= 2:
        ids = (pin_ids + ["1", "2"])[:2]
        pins = (
            SymbolPin(designator=ids[0], name=ids[0], x=-100, y=0,
                      orientation=2, length=100, electrical_type="passive"),
            SymbolPin(designator=ids[1], name=ids[1], x=100, y=0,
                      orientation=0, length=100, electrical_type="passive"),
        )
        bbox = SymbolBBox(x_min=-50, y_min=-30, x_max=50, y_max=30)
        return SymbolModel(lib_path=lib_path, lib_ref=lib_ref, pins=pins,
                           body_bbox=bbox, designator_prefix=prefix)

    n = len(pin_ids)
    n_left = (n + 1) // 2
    n_right = n - n_left
    hw = 300 if n > 16 else 200
    pitch = 100
    pins: list[SymbolPin] = []
    top_l = (n_left - 1) * pitch // 2
    for i, pid in enumerate(pin_ids[:n_left]):
        pins.append(SymbolPin(
            designator=pid, name=pid, x=-(hw + 100), y=top_l - i * pitch,
            orientation=2, length=100, electrical_type="passive"))
    top_r = (n_right - 1) * pitch // 2
    for j, pid in enumerate(pin_ids[n_left:]):
        # DIP-style counterclockwise: right column ascends bottom to top.
        pins.append(SymbolPin(
            designator=pid, name=pid, x=hw + 100, y=-top_r + j * pitch,
            orientation=0, length=100, electrical_type="passive"))
    half_h = max(top_l, top_r) + 50
    bbox = SymbolBBox(x_min=-hw, y_min=-half_h, x_max=hw, y_max=half_h)
    return SymbolModel(lib_path=lib_path, lib_ref=lib_ref, pins=tuple(pins),
                       body_bbox=bbox, designator_prefix=prefix)


class SyntheticSymbolExtractor(SymbolExtractor):
    """SymbolExtractor that fabricates symbols from the plan's own pin
    references instead of asking Altium. Keyed by (lib_path, lib_ref);
    parts sharing a lib_ref share the union of their referenced pins."""

    def __init__(self, plan: DesignPlan) -> None:
        # Parent __init__ skipped on purpose: no bridge, no cache.
        by_ref = _pins_by_refdes(plan)
        merged: dict[tuple[str, str], set[str]] = {}
        prefix: dict[tuple[str, str], str] = {}
        for part in plan.parts:
            key = (part.lib_path or "", part.lib_ref)
            merged.setdefault(key, set()).update(by_ref.get(part.refdes, ()))
            prefix.setdefault(key, _refdes_prefix(part.refdes))
        self._symbols: dict[tuple[str, str], SymbolModel] = {}
        for key in sorted(merged):
            pin_ids = _full_pin_ids(
                sorted(merged[key], key=_pin_sort_key))
            self._symbols[key] = _synth_symbol(
                key[0], key[1], pin_ids, prefix[key])

    def extract_one(self, lib_path: str, lib_ref: str
                    ) -> Optional[SymbolModel]:
        return self._symbols.get((lib_path, lib_ref))

    def extract_many(self, refs):
        return {key: self._symbols[key]
                for key in refs if tuple(key) in self._symbols}


# ---------------------------------------------------------------------------
# Synthetic footprints (mils)
# ---------------------------------------------------------------------------

# Chip-package body sizes (w, h) in mils, matched as substrings of the
# footprint name. Order matters: longer / more specific tokens first.
_CHIP_BODIES: tuple[tuple[str, tuple[int, int]], ...] = (
    ("IND", (160, 160)),
    ("SMA", (180, 110)),
    ("SOD", (110, 60)),
    ("XTAL", (130, 100)),
    ("1812", (180, 125)),
    ("1210", (125, 100)),
    ("1206", (125, 60)),
    ("0805", (80, 50)),
    ("0603", (60, 30)),
    ("0402", (40, 20)),
)

_QUAD_TOKENS = ("QFP", "QFN")


def _refdes_prefix(refdes: str) -> str:
    letters = "".join(ch for ch in refdes if ch.isalpha())
    return letters or "U"


def synth_footprint(part: Part, pin_ids: list[str]) -> dict[str, Any]:
    """Deterministic generic footprint for a plan part (all mils).

    Returns ``{"w", "h", "through_hole", "pads": [{"pin", "lx", "ly",
    "sx", "sy"}]}`` where lx/ly are pad-center offsets from the part
    centroid at rotation 0. Geometry is chosen from the footprint-name
    token (chip size codes, QFP/QFN -> quad) and the pin count
    (single-row for connectors, dual-row otherwise).
    """
    fp = (part.footprint or "").upper()
    prefix = _refdes_prefix(part.refdes)
    n = len(pin_ids)

    if prefix == "J" or "HDR" in fp or "CONN" in fp:
        # Single-row through-hole header, 100 mil pitch.
        pads = []
        for i, pid in enumerate(pin_ids):
            lx = i * 100 - (n - 1) * 50
            pads.append({"pin": pid, "lx": lx, "ly": 0, "sx": 55, "sy": 55})
        return {"w": n * 100, "h": 100, "through_hole": True, "pads": pads}

    if n <= 2:
        body_w, body_h = 80, 40
        for token, dims in _CHIP_BODIES:
            if token in fp:
                body_w, body_h = dims
                break
        ids = (pin_ids + ["1", "2"])[:2]
        half = body_w // 2
        pad_w = max(20, body_w // 2)
        pad_h = max(20, body_h)
        pads = [
            {"pin": ids[0], "lx": -half, "ly": 0, "sx": pad_w, "sy": pad_h},
            {"pin": ids[1], "lx": half, "ly": 0, "sx": pad_w, "sy": pad_h},
        ]
        return {"w": body_w + pad_w, "h": pad_h,
                "through_hole": False, "pads": pads}

    pitch = 50
    pad = 30
    if any(t in fp for t in _QUAD_TOKENS) and n >= 8:
        per_side = (n + 3) // 4
        half = (per_side - 1) * pitch // 2 + 100
        pads = []
        for i, pid in enumerate(pin_ids):
            side, k = divmod(i, per_side)
            along = k * pitch - (per_side - 1) * pitch // 2
            if side == 0:    # left column, top to bottom
                lx, ly = -half, -along
            elif side == 1:  # bottom row, left to right
                lx, ly = along, -half
            elif side == 2:  # right column, bottom to top
                lx, ly = half, along
            else:            # top row, right to left
                lx, ly = -along, half
            pads.append({"pin": pid, "lx": lx, "ly": ly, "sx": pad, "sy": pad})
        ext = 2 * half + pad + 20
        return {"w": ext, "h": ext, "through_hole": False, "pads": pads}

    # Dual-row (SOIC / SOT style), counterclockwise numbering.
    n_left = (n + 1) // 2
    n_right = n - n_left
    row_half = 175 if n > 16 else 125
    pads = []
    top_l = (n_left - 1) * pitch // 2
    for i, pid in enumerate(pin_ids[:n_left]):
        pads.append({"pin": pid, "lx": -row_half, "ly": top_l - i * pitch,
                     "sx": pad, "sy": pad})
    top_r = (n_right - 1) * pitch // 2
    for j, pid in enumerate(pin_ids[n_left:]):
        pads.append({"pin": pid, "lx": row_half, "ly": -top_r + j * pitch,
                     "sx": pad, "sy": pad})
    w = 2 * row_half + pad + 20
    h = max(top_l, top_r) * 2 + pad + 20
    return {"w": w, "h": h, "through_hole": False, "pads": pads}


# ---------------------------------------------------------------------------
# Stage runners
# ---------------------------------------------------------------------------


def _single_sheet_subplan(plan: DesignPlan,
                          sheet_name: str) -> Optional[DesignPlan]:
    """Restrict ``plan`` to one sheet: its parts, its zones, and every
    net cut down to its on-sheet pins (dropped below 2 pins -- in the
    real flow that pin connects through a port, by name). Returns None
    when the sheet holds no parts or no wireable net."""
    parts = [p for p in plan.parts if p.sheet == sheet_name]
    if not parts:
        return None
    refs = {p.refdes for p in parts}
    nets = []
    for net in plan.nets:
        pins = [pr for pr in net.pins if pr.refdes in refs]
        if len(pins) >= 2:
            nets.append(net.model_copy(update={"pins": pins}))
    if not nets:
        return None
    sub = plan.model_copy(deep=True)
    sub.sheets = [s for s in plan.sheets if s.name == sheet_name]
    sub.zones = [z for z in plan.zones if z.sheet == sheet_name]
    sub.parts = parts
    sub.nets = nets
    sub.bom = []
    return sub


def _schematic_scores(plan: DesignPlan) -> dict[str, Any]:
    """Run the schematic pipeline sheet by sheet and aggregate.

    Each sheet is wired as its own single-sheet plan. Running the whole
    multi-sheet plan through one pipeline call is wrong: sheets share
    one coordinate space, so the strict short detector cross-matches a
    sheet's wires against another sheet's pins and reports phantom
    shorts.
    """
    agg = {"ok": True, "score": 0.0, "shorts": 0, "labels": 0, "ports": 0,
           "placed": 0, "wires": 0, "failures": [], "sheets_run": 0}
    for sheet in plan.sheets:
        sub = _single_sheet_subplan(plan, sheet.name)
        if sub is None:
            continue
        extractor = SyntheticSymbolExtractor(sub)
        result = build_canvas_from_plan(sub, extractor, strict_shorts=True)
        agg["sheets_run"] += 1
        agg["ok"] = agg["ok"] and result.ok
        agg["shorts"] += sum(
            1 for f in result.failures if "routing short" in f.text)
        agg["labels"] += result.label_count
        agg["ports"] += result.power_port_count
        agg["placed"] += result.placement_count
        agg["wires"] += result.wire_count
        agg["failures"].extend(
            f"{sheet.name}: {f.text}" for f in result.failures
            if "routing short" not in f.text)
        if result.canvas.instances_on(sheet.name):
            agg["score"] += score_canvas(
                result.canvas, sub, sheet=sheet.name).total
    agg["score"] = round(agg["score"], 1)
    return agg


def _build_place_problem(
    plan: DesignPlan,
    footprints: dict[str, dict[str, Any]],
) -> tuple[list[PlaceComp], list[PlaceNet]]:
    """PlaceComp / PlaceNet lists from the plan + synthetic footprints."""
    pin_net: dict[tuple[str, str], str] = {}
    for net in plan.nets:
        for pr in net.pins:
            pin_net[(pr.refdes, str(pr.pin))] = net.name

    comps: list[PlaceComp] = []
    for part in plan.parts:
        fp = footprints[part.refdes]
        pins = tuple(
            PlacePin(p["lx"], p["ly"], pin_net[(part.refdes, p["pin"])])
            for p in fp["pads"]
            if (part.refdes, p["pin"]) in pin_net
        )
        pc = PlaceComp(ref=part.refdes, w=float(fp["w"]), h=float(fp["h"]),
                       cx=0.0, cy=0.0, layer="Top", fixed=False,
                       rotation=0.0, pins=pins, rotatable=bool(pins))
        # The engine reads these via getattr; role drives the connector
        # edge-pinning and the decap term.
        prefix = _refdes_prefix(part.refdes)
        if part.role:
            pc.role = part.role  # type: ignore[attr-defined]
        elif prefix == "J":
            pc.role = "connector"  # type: ignore[attr-defined]
        elif prefix == "U":
            pc.role = "ic"  # type: ignore[attr-defined]
        comps.append(pc)

    nets: list[PlaceNet] = []
    for net in plan.nets:
        refs = sorted({pr.refdes for pr in net.pins})
        if len(refs) >= 2:
            nets.append(PlaceNet(tuple(refs), name=net.name))
    return comps, nets


def _placement_scores(
    plan: DesignPlan,
    footprints: dict[str, dict[str, Any]],
    seed: int,
) -> tuple[dict[str, Any], Optional[ConstructResult],
           list[PlaceComp]]:
    """Run the constructive placer on the synthetic footprint set.

    The live tool layer additionally applies the structural crystal /
    switch-node match-group inference; the benchmark skips it because
    those detectors are role-blind and false-match the rail-bridging RC
    pairs the mcu fixture carries (pull-up + filter cap reads as a
    crystal), which wrecks legalization. The placement number here
    benchmarks the core engine only.
    """
    comps, nets = _build_place_problem(plan, footprints)
    rules = DesignRules(utilization=_PLACE_UTILIZATION)
    result = construct_placement(comps, nets, rules,
                                 ConstructOptions(seed=seed))
    report = result.report
    scores = {
        "ok": bool(report.legal) and math.isfinite(report.weighted_total),
        "weighted_total": round(report.weighted_total, 1),
        "hpwl": round(report.hpwl, 1),
        "legal": bool(report.legal),
        "utilization": round(report.utilization, 3),
        "board_mils": {"w": round(result.region.width, 1),
                       "h": round(result.region.height, 1)},
    }
    return scores, result, comps


def _rotated(lx: float, ly: float, deg: float) -> tuple[float, float]:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return lx * c - ly * s, lx * s + ly * c


def _routing_geometry(
    plan: DesignPlan,
    footprints: dict[str, dict[str, Any]],
    placement: ConstructResult,
) -> dict[str, Any]:
    """Board geometry dict (mils) for the router: every synthetic pad at
    its placed world position, bbox = the placed board with an apron."""
    pin_net: dict[tuple[str, str], str] = {}
    for net in plan.nets:
        for pr in net.pins:
            pin_net[(pr.refdes, str(pr.pin))] = net.name

    pads: list[dict[str, Any]] = []
    for part in plan.parts:
        ref = part.refdes
        fp = footprints[ref]
        cx, cy = placement.centroids.get(ref, (0.0, 0.0))
        rot = placement.rotations.get(ref, 0.0)
        side = placement.sides.get(ref, 1)
        if fp["through_hole"]:
            layer = "Multilayer"
        else:
            layer = "TopLayer" if side >= 0 else "BottomLayer"
        for p in fp["pads"]:
            lx = -p["lx"] if (side < 0 and not fp["through_hole"]) else p["lx"]
            wx, wy = _rotated(lx, p["ly"], rot)
            pads.append({
                "x": int(round(cx + wx)), "y": int(round(cy + wy)),
                "x_size": p["sx"], "y_size": p["sy"],
                "shape": "Rectangular", "layer": layer,
                "net": pin_net.get((ref, p["pin"]), ""),
                "rotation": rot,
            })

    apron = 150
    region = placement.region
    return {
        "bbox": {"x1": region.x1 - apron, "y1": region.y1 - apron,
                 "x2": region.x2 + apron, "y2": region.y2 + apron},
        "pads": pads,
        "tracks": [],
        "vias": [],
    }


def _routing_scores(
    plan: DesignPlan,
    footprints: dict[str, dict[str, Any]],
    placement: ConstructResult,
) -> dict[str, Any]:
    geometry = _routing_geometry(plan, footprints, placement)
    classes = classify_nets(plan).by_net
    solution = route_geometry(geometry, rules=_ROUTE_RULES,
                              net_classes=classes,
                              grid_pitch_mils=_ROUTE_GRID_PITCH_MILS)
    if not solution.get("ok"):
        return {"ok": False, "reason": solution.get("reason", "router error"),
                "completion_pct": 0.0, "drc_self_check": False}
    summary = solution["summary"]
    validation = solution.get("validation") or {}
    failed = sorted(
        n for n, r in solution["nets"].items() if r["status"] == "failed")
    return {
        "ok": True,
        "completion_pct": round(summary["completion"] * 100.0, 1),
        "drc_self_check": bool(validation.get("ok")),
        "drc_violations": len(validation.get("violations") or []),
        "routed": summary["routed"],
        "failed": summary["failed"],
        "failed_nets": failed,
        "track_count": summary["track_count"],
        "via_count": summary["via_count"],
        "total_length_mils": summary["total_length_mils"],
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_benchmark(
    plan: Union[DesignPlan, dict],
    *,
    hierarchy_threshold: int = DEFAULT_HIERARCHY_THRESHOLD,
    seed: int = 0,
) -> dict[str, Any]:
    """Run the full offline design chain on one golden plan.

    Returns ``{"ok": False, "reason": ...}`` when the plan does not parse,
    otherwise::

        {"ok": True,
         "plan_valid": bool,        # cross_check clean AND ERC-lite passed
         "plan_problems": [...],    # cross_check strings + ERC error messages
         "hierarchy": {"split", "sheets"},
         "schematic": {"ok", "score", "shorts", "labels", "ports", ...},
         "pcb_placement": {"ok", "weighted_total", "hpwl", "legal", ...},
         "routing": {"ok", "completion_pct", "drc_self_check", ...}}

    Plans above ``hierarchy_threshold`` parts are split with
    ``plan_hierarchy`` / ``apply_hierarchy`` before the schematic stage
    (the real multi-sheet workflow); placement and routing always run on
    the flat part list. ``seed`` feeds the placer; every other stage is
    deterministic by construction.
    """
    if isinstance(plan, dict):
        try:
            plan = DesignPlan.model_validate(plan)
        except ValidationError as exc:
            return {"ok": False, "reason": f"plan does not validate: {exc}"}
    elif not isinstance(plan, DesignPlan):
        return {"ok": False, "reason": "plan must be a DesignPlan or dict"}

    problems = list(plan.cross_check())
    erc = check_plan_erc(plan)
    problems.extend(i.message for i in erc.errors)
    plan_valid = not problems

    hier = plan_hierarchy(plan, max_parts_per_sheet=hierarchy_threshold)
    hierarchy = {
        "ok": bool(hier.get("ok")),
        "split": bool(hier.get("split")),
        "sheets": len(hier.get("sheets") or []),
        "cut_nets": int(hier.get("cut_nets") or 0),
    }
    sch_plan = plan
    if hier.get("ok") and hier.get("split"):
        sch_plan = apply_hierarchy(plan, hier)

    schematic = _schematic_scores(sch_plan)

    pins_by_ref = _pins_by_refdes(plan)
    footprints = {
        part.refdes: synth_footprint(
            part, _full_pin_ids(pins_by_ref.get(part.refdes, [])))
        for part in plan.parts
    }
    pcb_placement, placement_result, _ = _placement_scores(
        plan, footprints, seed)

    if placement_result is not None and placement_result.placements:
        routing = _routing_scores(plan, footprints, placement_result)
    else:
        routing = {"ok": False, "reason": "no placement to route",
                   "completion_pct": 0.0, "drc_self_check": False}

    return {
        "ok": True,
        "plan_valid": plan_valid,
        "plan_problems": problems,
        "erc_warnings": len(erc.warnings),
        "hierarchy": hierarchy,
        "schematic": schematic,
        "pcb_placement": pcb_placement,
        "routing": routing,
    }
