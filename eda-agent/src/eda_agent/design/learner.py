# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Capture user placement edits as training data.

After ``design_execute_plan`` emits a schematic, the user opens it in
Altium, drags components to taste, and saves. The diff between the
canvas's pre-edit positions and Altium's post-edit positions is the
training signal for the relative-anchor priors.

Pipeline:
1. ``orchestrator.execute_plan_via_canvas_from_json`` writes
   ``<project>.canvas.json`` = {plan, canvas, parameter_stamps}.
2. User edits the schematic in Altium and saves.
3. ``learn_from_layout(project_path)`` reads the snapshot, queries
   Altium for current positions, computes per-refdes deltas, builds
   (part_role, anchor_role, dx, dy, rot_delta) rows, appends to
   ``~/.eda-agent/placement_edits.jsonl``.
4. Offline aggregator builds ``placement_priors.json``.

Anchor heuristic for each moved part P:
- Highest-pin-count netlist neighbor on a NON-power, NON-ground net.
  Captures "I'm decoupling/biasing/sensing this IC" rather than the
  trivial "I share VCC with everything".
- Falls back to spatial nearest neighbor on the pre-edit canvas if no
  non-power neighbor exists.
- Skips the part entirely if no other component is on the canvas.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from eda_agent.design.plan import DesignPlan

logger = logging.getLogger("eda_agent.design.learner")


# Movement threshold below which we consider the part "unchanged".
# Altium's drag handles sometimes nudge by a grid unit even on a "no-op"
# click; require at least 25 mils of movement to count as a real edit.
_MOVE_THRESHOLD_MILS = 25


def _placement_edits_path() -> Path:
    """Default location for the JSONL log of placement edits.

    Lives under ``%USERPROFILE%\\.eda-agent\\placement_edits.jsonl`` so it
    persists across project / repo locations. Override via the
    ``EDA_AGENT_PLACEMENT_LOG`` env var for test isolation.
    """
    override = os.environ.get("EDA_AGENT_PLACEMENT_LOG")
    if override:
        return Path(override)
    userprofile = os.environ.get("USERPROFILE")
    base = Path(userprofile) if userprofile else Path.home()
    return base / ".eda-agent" / "placement_edits.jsonl"


def learn_from_layout(
    project_path: str,
    *,
    bridge: Any = None,
    log_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Diff current Altium positions vs the canvas snapshot; append deltas.

    Args:
        project_path: Path to the .PrjPcb whose schematic was edited.
            We use the same path the orchestrator persisted the
            ``<project>.canvas.json`` snapshot next to.
        bridge: AltiumBridge for the position queries. Defaults to the
            global one. Tests inject a fake.
        log_path: Where to append the JSONL rows. Defaults to the
            standard user-profile location.

    Returns:
        Dict with rows_appended, refdes_moved, refdes_unchanged,
        notes (list[str]).
    """
    out: dict[str, Any] = {
        "ok": True,
        "rows_appended": 0,
        "refdes_moved": [],
        "refdes_unchanged": [],
        "notes": [],
    }
    snapshot = _load_snapshot(project_path, out)
    if snapshot is None:
        return out

    plan_payload = snapshot.get("plan") or {}
    canvas_payload = snapshot.get("canvas") or {}
    try:
        plan = DesignPlan.model_validate(plan_payload)
    except Exception as exc:
        out["ok"] = False
        out["notes"].append(f"snapshot plan failed to validate: {exc}")
        return out

    pre_edit_by_refdes = {
        i["refdes"]: i for i in canvas_payload.get("instances", [])
    }
    if not pre_edit_by_refdes:
        out["notes"].append("snapshot has no placed instances; nothing to diff")
        return out

    bridge = bridge or _resolve_bridge()
    if bridge is None:
        out["ok"] = False
        out["notes"].append(
            "no Altium bridge available; cannot query post-edit positions"
        )
        return out

    post_edit_by_refdes = _query_post_edit_positions(project_path, bridge, out)
    if post_edit_by_refdes is None:
        return out

    role_by_refdes = {p.refdes: (p.role or "") for p in plan.parts}
    lib_ref_by_refdes = {p.refdes: p.lib_ref for p in plan.parts}
    design_id = _design_id_for(plan)

    rows: list[dict[str, Any]] = []
    for refdes, pre in pre_edit_by_refdes.items():
        post = post_edit_by_refdes.get(refdes)
        if post is None:
            out["notes"].append(
                f"{refdes}: present in snapshot but missing in current "
                f"layout (deleted?); skipping"
            )
            continue
        dx = post["x"] - pre["x"]
        dy = post["y"] - pre["y"]
        rot_delta = (post["rotation"] - pre["rotation"]) % 360
        if rot_delta > 180:
            rot_delta -= 360  # signed delta in (-180, 180]
        moved = (abs(dx) >= _MOVE_THRESHOLD_MILS
                 or abs(dy) >= _MOVE_THRESHOLD_MILS
                 or rot_delta != 0)
        if not moved:
            out["refdes_unchanged"].append(refdes)
            continue
        out["refdes_moved"].append(refdes)

        anchor_refdes = _pick_anchor(refdes, plan, pre_edit_by_refdes)
        if anchor_refdes is None:
            out["notes"].append(
                f"{refdes}: no anchor candidate found; skipping row"
            )
            continue

        rows.append({
            "ts": time.time(),
            "design_id": design_id,
            "refdes": refdes,
            "part_role": role_by_refdes.get(refdes, ""),
            "part_lib_ref": lib_ref_by_refdes.get(refdes, ""),
            "anchor_refdes": anchor_refdes,
            "anchor_role": role_by_refdes.get(anchor_refdes, ""),
            "anchor_lib_ref": lib_ref_by_refdes.get(anchor_refdes, ""),
            "dx_mils": dx,
            "dy_mils": dy,
            "rot_delta_deg": rot_delta,
            "design_size": len(plan.parts),
        })

    if rows:
        log_path = log_path or _placement_edits_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        out["rows_appended"] = len(rows)
        out["log_path"] = str(log_path)
    return out


def _load_snapshot(
    project_path: str, out: dict[str, Any]
) -> Optional[dict[str, Any]]:
    snapshot_path = Path(project_path).with_suffix(".canvas.json")
    if not snapshot_path.exists():
        out["ok"] = False
        out["notes"].append(
            f"no canvas snapshot at {snapshot_path}; run design_execute_plan "
            f"with use_canvas=True first so the pre-edit ground truth is "
            f"recorded"
        )
        return None
    try:
        return json.loads(snapshot_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        out["ok"] = False
        out["notes"].append(f"snapshot at {snapshot_path} is corrupt: {exc}")
        return None


def _query_post_edit_positions(
    project_path: str, bridge: Any, out: dict[str, Any]
) -> Optional[dict[str, dict[str, int]]]:
    """Read every placed eSchComponent's (x, y, rotation) from Altium."""
    try:
        response = bridge.send_command(
            "generic.query_objects",
            {
                "object_type": "eSchComponent",
                "properties": "Designator.Text,Location.X,Location.Y,Orientation",
                "scope": "project",
            },
        )
    except Exception as exc:
        out["ok"] = False
        out["notes"].append(f"query_objects failed: {exc}")
        return None
    by_refdes: dict[str, dict[str, int]] = {}
    for row in (response or {}).get("objects", []):
        refdes = str(row.get("Designator.Text", "")).strip()
        if not refdes:
            continue
        try:
            x = int(row.get("Location.X", 0))
            y = int(row.get("Location.Y", 0))
            # Altium's Orientation is TRotationBy90 (0..3); convert to degrees.
            orient = int(row.get("Orientation", 0)) % 4
        except (TypeError, ValueError):
            continue
        by_refdes[refdes] = {"x": x, "y": y, "rotation": orient * 90}
    return by_refdes


def _pick_anchor(
    moved_refdes: str,
    plan: DesignPlan,
    pre_edit_by_refdes: dict[str, dict[str, Any]],
) -> Optional[str]:
    """Pick the most relational anchor for a moved part.

    Preference order:
    1. Highest-pin-count netlist neighbor on a NON-power, NON-ground
       net. Captures intentional grouping (decoup_cap with its IC).
    2. Spatial nearest neighbor on the pre-edit canvas, Manhattan
       distance. Catches parts whose only nets are power/ground but
       which the user clearly placed near something specific.
    3. None if the design has no other placed component.
    """
    candidates = {p.refdes for p in plan.parts if p.refdes != moved_refdes}
    placed_candidates = candidates & pre_edit_by_refdes.keys()
    if not placed_candidates:
        return None

    # Build (other_refdes -> shared non-power-non-ground net count).
    shared_net_count: dict[str, int] = {}
    moved_pins = [
        (net.name, pin_ref.refdes)
        for net in plan.nets
        if not (net.is_power or net.is_ground)
        for pin_ref in net.pins
        if pin_ref.refdes == moved_refdes
    ]
    moved_nets = {net_name for (net_name, _) in moved_pins}
    if moved_nets:
        for net in plan.nets:
            if net.is_power or net.is_ground:
                continue
            if net.name not in moved_nets:
                continue
            for pin_ref in net.pins:
                if pin_ref.refdes == moved_refdes:
                    continue
                if pin_ref.refdes not in placed_candidates:
                    continue
                shared_net_count[pin_ref.refdes] = (
                    shared_net_count.get(pin_ref.refdes, 0) + 1
                )

    if shared_net_count:
        # Pick the one with the most shared signal nets. Tiebreak by
        # higher pin count (proxy for "the IC, not another passive").
        pin_count_by_part = {p.refdes: 0 for p in plan.parts}
        for net in plan.nets:
            for pin_ref in net.pins:
                pin_count_by_part[pin_ref.refdes] = (
                    pin_count_by_part.get(pin_ref.refdes, 0) + 1
                )
        best = max(
            shared_net_count.items(),
            key=lambda kv: (kv[1], pin_count_by_part.get(kv[0], 0)),
        )
        return best[0]

    # Spatial fallback: Manhattan-nearest placed neighbor in the pre-edit
    # canvas.
    moved_pre = pre_edit_by_refdes.get(moved_refdes)
    if moved_pre is None:
        return None
    mx, my = moved_pre["x"], moved_pre["y"]
    best_refdes: Optional[str] = None
    best_d = 10**9
    for other in placed_candidates:
        opre = pre_edit_by_refdes.get(other)
        if opre is None:
            continue
        d = abs(opre["x"] - mx) + abs(opre["y"] - my)
        if d < best_d:
            best_d = d
            best_refdes = other
    return best_refdes


def _design_id_for(plan: DesignPlan) -> str:
    """A stable short id for the design's topology shape.

    Used so the offline aggregator can group edits that came from the
    same design across multiple sessions (e.g., user iterated on the
    same buck three times). Doesn't try to detect "similar but
    different" designs -- that's a separate (harder) problem.
    """
    import hashlib
    sig_parts = []
    for p in sorted(plan.parts, key=lambda x: x.refdes):
        sig_parts.append(f"{p.refdes}:{p.lib_ref}")
    sig_nets = []
    for n in sorted(plan.nets, key=lambda x: x.name):
        pin_keys = sorted(f"{pr.refdes}.{pr.pin}" for pr in n.pins)
        sig_nets.append(f"{n.name}:{','.join(pin_keys)}")
    sig = "||".join(sig_parts + sig_nets)
    return hashlib.sha1(sig.encode("utf-8")).hexdigest()[:12]


def _resolve_bridge() -> Any:
    try:
        from eda_agent.bridge.altium_bridge import get_bridge
    except ImportError:
        return None
    try:
        return get_bridge()
    except Exception as exc:
        logger.warning("get_bridge failed: %s", exc)
        return None
