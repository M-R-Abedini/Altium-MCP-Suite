# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Mixed-signal domain inference for PCB placement.

Keeping noisy digital / switching circuitry physically away from sensitive
analog is one of the oldest layout rules (Ott, *Electromagnetic Compatibility
Engineering*; the analog/digital partition behind every data-converter app
note). The PCB placer already separates parts that carry DIFFERENT
``keepout_group`` tags (the facility-layout 'X' relationship / boids
separation), but those tags were user-supplied. This module derives them
structurally from the planner-asserted :class:`~eda_agent.design.plan.Net`
roles, so a design that marks its sensitive and its noisy nets gets the
domains separated for free -- the mixed-signal counterpart of
``diff_pair_match_groups``.

A part is assigned to a domain by the roles of the nets it touches:

* touches a SENSITIVE (analog) net and no noisy net  -> ``analog``
* touches a NOISY (digital / switching) net and no analog net -> ``digital``
* touches BOTH -> it is the boundary bridge (the data converter, the
  mixed-signal IC) and is left UNTAGGED -- it must not be pushed away from
  either side; it sits on the split and lets HPWL seat it between them.
* touches NEITHER -> untagged (power / passive infrastructure with no domain).

Naming-agnostic: it reads roles, not refdes or net names. NDA scope: only the
current plan's topology.
"""

from __future__ import annotations

from dataclasses import dataclass

from eda_agent.design.plan import DesignPlan

# Quiet, high-impedance, easily-disturbed nets.
ANALOG_ROLES = frozenset({"analog_sensitive", "feedback"})
# Fast-edged / switching / digital aggressors.
DIGITAL_ROLES = frozenset({"control", "clock", "differential", "switch"})


@dataclass(frozen=True)
class DomainAssignment:
    """Parts grouped by mixed-signal domain (all sorted, deterministic).

    ``boundary`` parts touch both domains -- the data converter / mixed IC --
    and are intentionally left out of the keepout tags.
    """

    analog: tuple[str, ...]
    digital: tuple[str, ...]
    boundary: tuple[str, ...]


def _roles_per_part(plan: DesignPlan) -> dict[str, set[str]]:
    roles: dict[str, set[str]] = {}
    for net in plan.nets:
        role = (net.role or "").strip().lower()
        for pr in net.pins:
            roles.setdefault(pr.refdes, set()).add(role)
    return roles


def classify_domains(plan: DesignPlan) -> DomainAssignment:
    """Partition the plan's parts into analog / digital / boundary by net role
    (see the module docstring)."""
    roles_of = _roles_per_part(plan)
    analog: list[str] = []
    digital: list[str] = []
    boundary: list[str] = []
    for ref in sorted(roles_of):
        roles = roles_of[ref]
        is_analog = bool(roles & ANALOG_ROLES)
        is_digital = bool(roles & DIGITAL_ROLES)
        if is_analog and is_digital:
            boundary.append(ref)
        elif is_analog:
            analog.append(ref)
        elif is_digital:
            digital.append(ref)
    return DomainAssignment(tuple(analog), tuple(digital), tuple(boundary))


def infer_keepout_groups(plan: DesignPlan) -> dict[str, str]:
    """Map clearly-analog and clearly-digital parts to ``"analog"`` /
    ``"digital"`` keepout tags, ready for ``pcb_plan_placement(keepout_groups=)``.

    Returns ``{}`` unless BOTH domains are present -- a single-domain board has
    nothing to separate (the separation term is identically zero when every tag
    is the same), so emitting tags would be noise. Boundary (mixed) parts are
    left untagged so the placer seats them on the split rather than exiling
    them.
    """
    d = classify_domains(plan)
    if not (d.analog and d.digital):
        return {}
    out: dict[str, str] = {}
    for ref in d.analog:
        out[ref] = "analog"
    for ref in d.digital:
        out[ref] = "digital"
    return out


__all__ = [
    "ANALOG_ROLES",
    "DIGITAL_ROLES",
    "DomainAssignment",
    "classify_domains",
    "infer_keepout_groups",
]
