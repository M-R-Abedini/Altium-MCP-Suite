# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Plan statistics: a quick structural overview of a DesignPlan.

A design-comprehension aid -- part counts by type, the power rails, and the
net-degree distribution (which net fans out the widest is a routing hotspot)
-- so a planner or reviewer can size up a plan at a glance before emit.
Naming-agnostic (refdes-kind + the power/ground flags). Pure offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from eda_agent.design.plan import DesignPlan


@dataclass(frozen=True)
class PlanStats:
    part_count: int
    net_count: int
    parts_by_kind: dict[str, int]      # kind letter -> count (R, C, U, ...)
    ic_count: int                      # parts with >= 4 distinct pins
    passive_count: int                 # 2-pin R / C / L
    power_rails: tuple[str, ...]       # is_power net names, sorted
    ground_nets: tuple[str, ...]       # is_ground net names, sorted
    avg_net_degree: float              # mean distinct-parts-per-net
    # Widest non-power/ground net: (name, parts) -- a routing hotspot.
    highest_fanout_signal: tuple[str, int] | None = None


def _pin_counts(plan: DesignPlan) -> dict[str, int]:
    pins: dict[str, set[str]] = {}
    for net in plan.nets:
        for pr in net.pins:
            pins.setdefault(pr.refdes, set()).add(str(pr.pin))
    return {r: len(s) for r, s in pins.items()}


def summarize_plan(plan: DesignPlan) -> PlanStats:
    """Compute a structural summary of ``plan`` (see module docstring)."""
    from eda_agent.design.motifs import _kind_from_refdes

    pin_count = _pin_counts(plan)

    by_kind: dict[str, int] = {}
    for part in plan.parts:
        kind = _kind_from_refdes(part.refdes)
        by_kind[kind] = by_kind.get(kind, 0) + 1

    ic_count = sum(1 for p in plan.parts
                   if pin_count.get(p.refdes, 0) >= 4)
    passive_count = sum(
        1 for p in plan.parts
        if _kind_from_refdes(p.refdes) in ("R", "C", "L")
        and pin_count.get(p.refdes, 0) == 2)

    power = sorted(n.name for n in plan.nets if n.is_power
                   or (n.role or "").strip().lower() == "power")
    ground = sorted(n.name for n in plan.nets if n.is_ground
                    or (n.role or "").strip().lower() == "ground")
    power_ground = set(power) | set(ground)

    degrees = []
    hotspot: tuple[str, int] | None = None
    for net in plan.nets:
        parts = len({pr.refdes for pr in net.pins})
        degrees.append(parts)
        if net.name not in power_ground:
            if hotspot is None or parts > hotspot[1]:
                hotspot = (net.name, parts)
    avg_degree = sum(degrees) / len(degrees) if degrees else 0.0

    return PlanStats(
        part_count=len(plan.parts), net_count=len(plan.nets),
        parts_by_kind=dict(sorted(by_kind.items())),
        ic_count=ic_count, passive_count=passive_count,
        power_rails=tuple(power), ground_nets=tuple(ground),
        avg_net_degree=avg_degree, highest_fanout_signal=hotspot)


__all__ = ["PlanStats", "summarize_plan"]
