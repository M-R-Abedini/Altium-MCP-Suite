# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""DRC-feedback repair planner (Phase 2d): violations in, actions out.

Pure functions, no IO. Input is the violation payload produced by the
``pcb_run_drc`` / ``pcb_get_clearance_violations`` tools::

    {"violation_count": N, "violations": [
        {"name": ..., "description": ..., "rule": ...,
         "x_mils": ..., "y_mils": ..., "layer": ...,
         "primitive1": {"detail", "type", "net", "layer",
                        "x_mils", "y_mils"},
         "primitive2": {...}},   # empty fields for 1-prim violations
        ...]}

``classify_violations`` sorts those into actionable buckets;
``plan_repairs`` turns buckets into an ordered action list using the
greedy worst-offender policy (rip the net touching the most clearance
violations first, mirroring the wire-short cull in design/pipeline.py).

Executor contract (the executor itself is NOT here -- it lives with the
MCP tool layer and applies actions via bridge calls):

- ``{"action": "rip_and_reroute", "net": N}``: delete N's routed copper
  (``pcb_delete_net`` keep_pads semantics) and route it again with the
  in-house grid router. For a
  net that was never routed this degenerates to a plain route attempt.
- ``{"action": "nudge", "net": N, "dx": dx_mils, "dy": dy_mils,
  "x_mils": x, "y_mils": y}``: translate N's offending primitive
  nearest (x, y) by (dx, dy) mils, e.g. select via location filter and
  ``obj_modify``; a local re-route of the segment is an acceptable
  stronger implementation.
- ``{"action": "widen"|"narrow", "net": N}``: set N's track width to
  the governing width rule's preferred value (``pcb_set_track_width``).
- ``{"action": "escalate", "reason": R}``: stop, surface R to the
  human. Anything after an escalate in the list is still safe to apply,
  but the board will not converge without intervention.

After applying one planner round the executor MUST re-run DRC and
re-plan from fresh violations; the planner is stateless and its
``max_rounds`` only bounds how many rip actions one plan may contain.
The executor enforces its own outer iteration bound.

All coordinates and deltas are integer mils.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

# Buckets, in planning order.
BUCKETS = (
    "net_clearance",   # clearance between two routed nets
    "pad_clearance",   # routed copper too close to a pad / component
    "unrouted",        # un-routed net constraint
    "antenna",         # net antennae (dangling stub / via)
    "width",           # width constraint
    "other",           # anything unrecognized
)

# How far a nudge moves the offending primitive, in mils. One default
# routing-grid step: big enough to clear a typical 6-10 mil gap deficit,
# small enough not to create new violations on the far side.
NUDGE_STEP_MILS = 10

# Primitive ``type`` strings that are routed copper (movable by a rip or
# nudge) vs. fixed footprint geometry. Matched case-insensitively.
_ROUTED_TYPES = {"track", "arc", "via"}
_FIXED_TYPES = {"pad", "component", "comp", "region", "fill", "polygon", "text"}

_NUMBER_UNIT = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*(mil|mm)", re.IGNORECASE)


def _to_mils(value: float, unit: str) -> float:
    """Convert a (value, unit) pair from DRC description text to mils."""
    from eda_agent.units import MM_PER_MIL

    if unit.lower() == "mm":
        return value / MM_PER_MIL
    return value


def _find_quantity(text: str, label: str) -> float | None:
    """Pull ``label = <number><unit>`` out of a violation description.

    Returns the value in mils, or None if the label is absent or carries
    no parsable number+unit.
    """
    pos = text.lower().find(label.lower())
    if pos < 0:
        return None
    match = _NUMBER_UNIT.search(text, pos)
    if match is None:
        return None
    return _to_mils(float(match.group(1)), match.group(2))


def _prim_net(prim: Any) -> str:
    if not isinstance(prim, dict):
        return ""
    return str(prim.get("net") or "").strip()


def _prim_type(prim: Any) -> str:
    if not isinstance(prim, dict):
        return ""
    return str(prim.get("type") or "").strip().lower()


def _prim_xy(prim: Any) -> tuple[int, int] | None:
    """Primitive location in mils, or None when either coordinate is absent."""
    if not isinstance(prim, dict):
        return None
    x, y = prim.get("x_mils"), prim.get("y_mils")
    if x is None or y is None:
        return None
    try:
        return int(round(float(x))), int(round(float(y)))
    except (TypeError, ValueError):
        return None


def _violation_text(violation: dict[str, Any]) -> str:
    """Concatenated searchable text of a violation, lowercased."""
    parts = (
        violation.get("rule") or "",
        violation.get("name") or "",
        violation.get("description") or "",
    )
    return " ".join(str(p) for p in parts).lower()


def _classify_one(violation: dict[str, Any]) -> str:
    """Bucket name for a single violation dict."""
    text = _violation_text(violation)
    if "un-routed" in text or "unrouted" in text or "broken net" in text:
        return "unrouted"
    if "antenna" in text:  # matches both "antenna" and "antennae"
        return "antenna"
    if "width" in text:
        return "width"
    if "clearance" in text:
        t1 = _prim_type(violation.get("primitive1"))
        t2 = _prim_type(violation.get("primitive2"))
        nets = _violation_nets(violation)
        if t1 in _FIXED_TYPES or t2 in _FIXED_TYPES:
            return "pad_clearance"
        if t1 in _ROUTED_TYPES and t2 in _ROUTED_TYPES and nets:
            return "net_clearance"
        # Types missing or unrecognized: two distinct nets still gives a
        # rippable net-vs-net conflict; one or zero nets does not.
        if len(nets) >= 2:
            return "net_clearance"
        return "other"
    return "other"


def _violation_nets(violation: dict[str, Any]) -> list[str]:
    """Distinct non-empty net names of the violation's primitives, ordered."""
    nets: list[str] = []
    for key in ("primitive1", "primitive2"):
        net = _prim_net(violation.get(key))
        if net and net not in nets:
            nets.append(net)
    return nets


def classify_violations(violations: Any) -> dict[str, Any]:
    """Sort DRC violations into repair buckets.

    Args:
        violations: Either the full ``pcb_run_drc`` payload (a dict with
            a ``violations`` list) or a bare list of violation dicts.

    Returns:
        ``{"ok": True, "buckets": {bucket: [violation, ...]},
        "counts": {bucket: n}, "total": n}`` -- every input violation
        lands in exactly one of ``BUCKETS``. Non-dict entries and
        malformed top-level input return ``{"ok": False, "reason": ...}``.
    """
    if isinstance(violations, dict):
        violations = violations.get("violations", [])
    if not isinstance(violations, list):
        return {"ok": False, "reason": "violations must be a list or a "
                                       "{violation_count, violations} payload"}

    buckets: dict[str, list[dict[str, Any]]] = {name: [] for name in BUCKETS}
    for index, violation in enumerate(violations):
        if not isinstance(violation, dict):
            return {"ok": False,
                    "reason": f"violations[{index}] is not a dict"}
        buckets[_classify_one(violation)].append(violation)

    return {
        "ok": True,
        "buckets": buckets,
        "counts": {name: len(items) for name, items in buckets.items()},
        "total": sum(len(items) for items in buckets.values()),
    }


def _nudge_vector(violation: dict[str, Any]) -> tuple[str, int, int, int, int] | None:
    """(net, dx, dy, x, y) for a pad-clearance violation, mils.

    The movable primitive is the routed one; the nudge pushes it away
    from the fixed primitive along the dominant separation axis. Returns
    None when no routed primitive with a net exists or geometry is
    missing/degenerate (caller falls back to rip or escalate).
    """
    p1, p2 = violation.get("primitive1"), violation.get("primitive2")
    if _prim_type(p1) in _ROUTED_TYPES and _prim_net(p1):
        movable, fixed = p1, p2
    elif _prim_type(p2) in _ROUTED_TYPES and _prim_net(p2):
        movable, fixed = p2, p1
    else:
        return None

    m_xy, f_xy = _prim_xy(movable), _prim_xy(fixed)
    if m_xy is None or f_xy is None:
        return None
    sep_x, sep_y = m_xy[0] - f_xy[0], m_xy[1] - f_xy[1]
    if sep_x == 0 and sep_y == 0:
        return None
    if abs(sep_x) >= abs(sep_y):
        dx, dy = (NUDGE_STEP_MILS if sep_x > 0 else -NUDGE_STEP_MILS), 0
    else:
        dx, dy = 0, (NUDGE_STEP_MILS if sep_y > 0 else -NUDGE_STEP_MILS)
    return _prim_net(movable), dx, dy, m_xy[0], m_xy[1]


def _width_direction(violation: dict[str, Any]) -> str:
    """``"widen"`` or ``"narrow"`` for a width violation.

    Parsed from the description's ``Actual Width`` vs the rule's
    ``Min`` / ``Max`` (mm and mil both handled). Unparsable text
    defaults to ``widen``: minimum-width violations are the common
    failure (a router squeezing a neck), maximum-width ones are rare.
    """
    text = " ".join(str(violation.get(k) or "")
                    for k in ("description", "name"))
    actual = _find_quantity(text, "actual width")
    if actual is None:
        actual = _find_quantity(text, "actual")
    minimum = _find_quantity(text, "min")
    maximum = _find_quantity(text, "max")
    if actual is not None:
        if minimum is not None and actual < minimum:
            return "widen"
        if maximum is not None and actual > maximum:
            return "narrow"
    return "widen"


def plan_repairs(buckets: Any, max_rounds: int = 5) -> dict[str, Any]:
    """Turn classified violations into an ordered repair-action list.

    Policy:

    1. Net-to-net clearance: greedy worst offender -- count how many
       clearance violations each net touches, rip the net with the most
       (ties broken by net name, deterministic), drop its violations
       from the working set, repeat. One rip consumes one round; when
       ``max_rounds`` rounds are spent and clearance violations remain,
       an ``escalate`` action is emitted instead of more rips.
    2. Unrouted nets: one ``rip_and_reroute`` per net (a re-route
       attempt; nothing to rip).
    3. Antennas: ``rip_and_reroute`` of the stub's net.
    4. Pad/component clearance: a single conflict on a net becomes a
       ``nudge`` away from the fixed primitive; a net with several pad
       conflicts (or missing geometry) is ripped instead -- one nudge
       cannot fix multiple spots. Pad-vs-pad conflicts (no routed
       primitive) are a placement problem and escalate.
    5. Width: ``widen`` / ``narrow`` per net, direction parsed from the
       violation text (default widen).
    6. ``other`` bucket: one ``escalate`` summarizing the unknown rules.

    A net already scheduled for ``rip_and_reroute`` is never given a
    second action -- the re-route supersedes nudges and width tweaks.

    Args:
        buckets: Output of :func:`classify_violations` (the full result
            dict or its ``buckets`` mapping).
        max_rounds: Rip budget for the worst-offender loop, >= 0.

    Returns:
        ``{"ok": True, "actions": [...], "rounds_used": n,
        "ripped_nets": [...]}``; empty buckets give an empty action list
        (idempotent). Malformed input returns ``{"ok": False, "reason"}``.
    """
    if isinstance(buckets, dict) and "buckets" in buckets:
        if buckets.get("ok") is False:
            return {"ok": False,
                    "reason": "buckets input carries ok=False; classify "
                              "violations successfully first"}
        buckets = buckets["buckets"]
    if not isinstance(buckets, dict):
        return {"ok": False, "reason": "buckets must be a dict "
                                       "(classify_violations output)"}
    if not isinstance(max_rounds, int) or max_rounds < 0:
        return {"ok": False, "reason": "max_rounds must be an integer >= 0"}

    def bucket(name: str) -> list[dict[str, Any]]:
        items = buckets.get(name, [])
        return items if isinstance(items, list) else []

    actions: list[dict[str, Any]] = []
    ripped: set[str] = set()

    def rip(net: str, reason: str) -> None:
        if net in ripped:
            return
        ripped.add(net)
        actions.append({"action": "rip_and_reroute", "net": net,
                        "reason": reason})

    # 1. Net-to-net clearance: greedy worst offender, bounded rounds.
    work = [v for v in bucket("net_clearance") if _violation_nets(v)]
    rounds_used = 0
    while work and rounds_used < max_rounds:
        counts: dict[str, int] = defaultdict(int)
        for violation in work:
            for net in _violation_nets(violation):
                counts[net] += 1
        worst = max(counts, key=lambda n: (counts[n], n))
        rip(worst, f"{counts[worst]} clearance violation(s), worst offender "
                   f"round {rounds_used + 1}")
        work = [v for v in work if worst not in _violation_nets(v)]
        rounds_used += 1
    if work:
        remaining_nets = sorted({n for v in work for n in _violation_nets(v)})
        actions.append({
            "action": "escalate",
            "reason": f"{len(work)} net clearance violation(s) remain after "
                      f"{max_rounds} rip round(s); nets: "
                      + ", ".join(remaining_nets),
        })

    # 2. Unrouted nets: route attempt per net.
    unrouted_nets = sorted({n for v in bucket("unrouted")
                            for n in _violation_nets(v)})
    for net in unrouted_nets:
        rip(net, "unrouted net")
    nameless_unrouted = sum(1 for v in bucket("unrouted")
                            if not _violation_nets(v))
    if nameless_unrouted:
        actions.append({
            "action": "escalate",
            "reason": f"{nameless_unrouted} unrouted violation(s) carry no "
                      "net name; cannot target a re-route",
        })

    # 3. Antennas: the stub belongs to the net, rip it clean.
    for net in sorted({n for v in bucket("antenna")
                       for n in _violation_nets(v)}):
        rip(net, "net antenna (dangling copper)")

    # 4. Pad/component clearance: nudge once, rip on repeats or missing
    #    geometry, escalate when nothing routed is involved.
    pad_by_net: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pad_unactionable = 0
    for violation in bucket("pad_clearance"):
        p1, p2 = violation.get("primitive1"), violation.get("primitive2")
        if _prim_type(p1) in _ROUTED_TYPES and _prim_net(p1):
            pad_by_net[_prim_net(p1)].append(violation)
        elif _prim_type(p2) in _ROUTED_TYPES and _prim_net(p2):
            pad_by_net[_prim_net(p2)].append(violation)
        else:
            pad_unactionable += 1
    for net in sorted(pad_by_net):
        if net in ripped:
            continue  # re-route supersedes the nudge
        conflicts = pad_by_net[net]
        vector = _nudge_vector(conflicts[0]) if len(conflicts) == 1 else None
        if vector is None:
            rip(net, f"{len(conflicts)} pad clearance conflict(s); nudge "
                     "not applicable")
            continue
        _net, dx, dy, x, y = vector
        actions.append({"action": "nudge", "net": net, "dx": dx, "dy": dy,
                        "x_mils": x, "y_mils": y,
                        "reason": "clearance to fixed pad/component"})
    if pad_unactionable:
        actions.append({
            "action": "escalate",
            "reason": f"{pad_unactionable} pad clearance violation(s) involve "
                      "no routed primitive (pad-vs-pad); placement-level fix "
                      "needed",
        })

    # 5. Width: widen/narrow per net.
    width_dir: dict[str, str] = {}
    for violation in bucket("width"):
        nets = _violation_nets(violation)
        if not nets:
            continue
        width_dir.setdefault(nets[0], _width_direction(violation))
    for net in sorted(width_dir):
        if net in ripped:
            continue
        actions.append({"action": width_dir[net], "net": net,
                        "reason": "width constraint violation"})

    # 6. Unknown rule kinds.
    other = bucket("other")
    if other:
        rules = sorted({str(v.get("rule") or v.get("name") or "unknown")
                        for v in other})
        actions.append({
            "action": "escalate",
            "reason": f"{len(other)} violation(s) of unhandled kind(s): "
                      + ", ".join(rules),
        })

    return {
        "ok": True,
        "actions": actions,
        "rounds_used": rounds_used,
        "ripped_nets": sorted(ripped),
    }


def plan_drc_repairs(violations: Any, max_rounds: int = 5) -> dict[str, Any]:
    """Classify a DRC payload and plan repairs in one call.

    Args:
        violations: ``pcb_run_drc`` payload or bare violation list.
        max_rounds: Rip budget, see :func:`plan_repairs`.

    Returns:
        :func:`plan_repairs` result extended with the classification
        ``counts``, or ``{"ok": False, "reason"}`` from either stage.
    """
    classified = classify_violations(violations)
    if not classified.get("ok"):
        return classified
    plan = plan_repairs(classified, max_rounds=max_rounds)
    if not plan.get("ok"):
        return plan
    plan["counts"] = classified["counts"]
    return plan
