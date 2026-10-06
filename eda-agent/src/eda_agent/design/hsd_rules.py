# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""High-speed design helpers: size every differential pair's traces.

A controlled-impedance board sizes each differential pair's traces to a target
impedance (90 ohm USB, 100 ohm HDMI/LVDS) for the chosen stackup. This finds
the pairs (the same role-driven detector the placement constraints use) and
sizes each one with the verified IPC-2141 impedance inverse, so a planner gets
the trace width for every pair in one call instead of identifying pairs and
running the calculator by hand.

The stackup (dielectric height / er / copper) and the target impedance are
BOARD-LEVEL decisions the planner supplies; everything else is derived. Pure
offline, formula-based (round-trips with the forward impedance calc).
"""

from __future__ import annotations

from dataclasses import dataclass

from eda_agent.design.plan import DesignPlan


@dataclass(frozen=True)
class DiffPairTrace:
    """A differential pair and the trace geometry recommended for it."""

    nets: tuple[str, ...]
    endpoints: tuple[str, ...]
    width_mils: float
    spacing_mils: float
    target_ohms: float
    single_ended_z0_ohms: float
    feasible: bool


def suggest_diff_pair_traces(
    plan: DesignPlan,
    *,
    target_ohms: float = 90.0,
    geometry: str = "microstrip_diff",
    dielectric_height_mils: float = 7.0,
    dielectric_constant: float = 4.2,
    copper_oz: float = 1.0,
    spacing_mils: float = 6.0,
) -> list[DiffPairTrace]:
    """Recommend a trace width for every differential pair in ``plan``.

    Detects the pairs (role ``differential``, see
    :func:`~eda_agent.design.diffpairs.detect_diff_pairs`) and sizes each to
    ``target_ohms`` for the given stackup and ``spacing_mils`` via the IPC-2141
    impedance inverse. Returns one :class:`DiffPairTrace` per pair (sorted);
    empty when the plan has no differential pairs. ``geometry`` must be a
    differential one (``microstrip_diff`` / ``stripline_diff``).
    """
    from eda_agent.design.diffpairs import detect_diff_pairs
    from eda_agent.design.impedance_sizing import trace_width_for_impedance

    if not geometry.strip().lower().endswith("_diff"):
        raise ValueError("geometry must be a differential geometry "
                         "(microstrip_diff / stripline_diff)")
    out: list[DiffPairTrace] = []
    for dp in detect_diff_pairs(plan):
        r = trace_width_for_impedance(
            target_ohms, geometry, dielectric_height_mils,
            dielectric_constant=dielectric_constant, copper_oz=copper_oz,
            spacing_mils=spacing_mils)
        out.append(DiffPairTrace(
            nets=dp.nets, endpoints=tuple(sorted(dp.endpoints)),
            width_mils=round(r.width_mils, 2), spacing_mils=spacing_mils,
            target_ohms=target_ohms,
            single_ended_z0_ohms=round(r.single_ended_z0_ohms, 1),
            feasible=r.feasible))
    out.sort(key=lambda t: t.nets)
    return out


@dataclass(frozen=True)
class DiffPairSkew:
    """A differential pair and its within-pair (P-vs-N) skew assessment."""

    nets: tuple[str, ...]
    endpoints: tuple[str, ...]
    leg_lengths: tuple[float, float]
    mismatch_mils: float
    skew_ps: float
    compensation_mils: float
    tolerance_mils: float | None
    within_tolerance: bool


def assess_diff_pair_skew(
    plan: DesignPlan,
    segment_lengths: dict[str, float],
    *,
    geometry: str = "stripline",
    dielectric_constant: float = 4.2,
    width_mils: float | None = None,
    dielectric_height_mils: float | None = None,
    tolerance_mils: float | None = None,
    skew_budget_ps: float | None = None,
) -> list[DiffPairSkew]:
    """Assess the intra-pair skew of every differential pair in ``plan``.

    Detects the pairs (role ``differential``) and, for each, sums the routed
    lengths of each leg's segments and reports the P-vs-N mismatch, the delay
    skew it represents, and the serpentine compensation to add to the shorter
    leg -- see :func:`~eda_agent.design.length_matching.intra_pair_skew` for the
    physics (intra-pair skew converts differential energy to common mode). The
    skew budget comes from ``skew_budget_ps`` (or an explicit ``tolerance_mils``)
    and is far tighter than a bus's inter-pair budget. ``segment_lengths`` maps
    each differential net name to its routed length in mils. Returns one
    :class:`DiffPairSkew` per pair (sorted); empty when the plan has no pairs.
    """
    from eda_agent.design.diffpairs import detect_diff_pairs
    from eda_agent.design.length_matching import intra_pair_skew
    from eda_agent.design.signal_integrity import effective_dielectric_constant

    er_eff = effective_dielectric_constant(
        dielectric_constant, geometry,
        width_mils=width_mils, height_mils=dielectric_height_mils)

    out: list[DiffPairSkew] = []
    for dp in detect_diff_pairs(plan):
        ips = intra_pair_skew(
            dp.legs, segment_lengths, er_eff,
            tolerance_mils=tolerance_mils, skew_budget_ps=skew_budget_ps)
        out.append(DiffPairSkew(
            nets=dp.nets, endpoints=tuple(sorted(dp.endpoints)),
            leg_lengths=(round(ips.leg_lengths[0], 2),
                         round(ips.leg_lengths[1], 2)),
            mismatch_mils=round(ips.mismatch_mils, 2),
            skew_ps=round(ips.skew_ps, 3),
            compensation_mils=round(ips.compensation_mils, 2),
            tolerance_mils=(round(ips.tolerance_mils, 2)
                            if ips.tolerance_mils is not None else None),
            within_tolerance=ips.within_tolerance))
    out.sort(key=lambda s: s.nets)
    return out


__all__ = [
    "DiffPairTrace",
    "suggest_diff_pair_traces",
    "DiffPairSkew",
    "assess_diff_pair_skew",
]
