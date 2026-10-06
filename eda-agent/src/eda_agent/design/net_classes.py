# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Net-class assignment from net roles.

Before routing a board you set up net classes (Power, Ground, Differential,
Clock, Analog, ...) and hang the width / clearance / impedance rules off them.
The plan's ``Net.role`` (and the power/ground flags) already carry that intent,
so this maps each net to a class deterministically -- the membership the
planner would otherwise assign by hand. Naming-agnostic (roles + flags, not net
names). Composes with the differential-pair detector and the mixed-signal
domain inference, which read the same roles. Pure offline.
"""

from __future__ import annotations

from dataclasses import dataclass

from eda_agent.design.plan import DesignPlan

# role -> class for the well-known roles (power/ground come from flags first).
_ROLE_CLASS = {
    "differential": "differential",
    "clock": "clock",
    "analog_sensitive": "analog",
    "feedback": "analog",
    "switch": "switch",
    "high_current": "high_current",
    "control": "control",
    "power": "power",
    "ground": "ground",
}


@dataclass(frozen=True)
class NetClassReport:
    """Per-net class plus the inverse grouping."""

    by_net: dict[str, str]                 # net name -> class
    groups: dict[str, tuple[str, ...]]     # class -> sorted net names


def net_class_of(net) -> str:
    """Classify one net from its flags / role. Power and ground flags win over
    a role; otherwise a known role maps to its class; default ``signal``."""
    role = (net.role or "").strip().lower()
    if net.is_ground or role == "ground":
        return "ground"
    if net.is_power or role == "power":
        return "power"
    return _ROLE_CLASS.get(role, "signal")


def classify_nets(plan: DesignPlan) -> NetClassReport:
    """Assign every net in ``plan`` to a class (see module docstring).

    Returns a :class:`NetClassReport` with the per-net mapping and the inverse
    grouping (class -> sorted nets), both deterministic.
    """
    by_net: dict[str, str] = {}
    groups: dict[str, list[str]] = {}
    for net in plan.nets:
        cls = net_class_of(net)
        by_net[net.name] = cls
        groups.setdefault(cls, []).append(net.name)
    return NetClassReport(
        by_net=dict(sorted(by_net.items())),
        groups={c: tuple(sorted(n)) for c, n in sorted(groups.items())})


__all__ = ["NetClassReport", "net_class_of", "classify_nets"]
