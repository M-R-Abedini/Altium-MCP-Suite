# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Length-matching / skew budgets for buses and differential pairs.

Altium can physically meander a net to a target length (``pcb_tune_length``) and
report routed lengths (``pcb_get_trace_lengths``), but the design-time decision
those tools execute -- *how tightly must these nets match, and how much copper
must each one gain* -- had no offline calc. This module supplies it, reusing the
propagation physics from :mod:`signal_integrity` (no duplicated formulas).

The whole module rests on one exact unit identity: propagation delay in ns/inch
is numerically equal to ps/mil (1 ns / 1 in = 1000 ps / 1000 mil), so a skew in
picoseconds maps to a length in mils by ``length_mils = skew_ps / t_pd_ns_per_in``.

A skew budget comes from one of two places:

* a direct picosecond budget (e.g. DDR byte-lane skew, a few ps), or
* a fraction of the signal's rise time (the common "match to within 10-20 % of
  the edge" rule -- Johnson & Graham, *High-Speed Digital Design*).

Matching only ever lengthens the shorter nets up to the longest member (you can
add serpentine copper, not remove it), so the group report targets the longest
net and reports each shorter net's required compensation.
"""

from __future__ import annotations

from dataclasses import dataclass

from eda_agent.design.signal_integrity import (
    effective_dielectric_constant,
    propagation_delay_ns_per_inch,
)


def length_for_skew(skew_ps: float, er_eff: float) -> float:
    """Trace-length difference (mils) that produces ``skew_ps`` of delay skew.

    Uses the ns/in == ps/mil identity: ``length_mils = skew_ps / t_pd``.
    """
    if skew_ps < 0:
        raise ValueError("skew must be non-negative")
    t_pd = propagation_delay_ns_per_inch(er_eff)  # ns/in == ps/mil
    return skew_ps / t_pd


def skew_for_length(length_mils: float, er_eff: float) -> float:
    """Delay skew (ps) from a trace-length difference of ``length_mils``."""
    if length_mils < 0:
        raise ValueError("length must be non-negative")
    return length_mils * propagation_delay_ns_per_inch(er_eff)  # ps/mil * mil


def match_tolerance_for_rise_time(
    rise_time_ns: float, er_eff: float, *, fraction: float = 0.1,
) -> float:
    """Length-match window (mils) that keeps skew under ``fraction`` of the edge.

    The common "match to within 10-20 % of the rise time" rule: the allowed
    skew is ``fraction * rise_time``, converted to a length.
    """
    if rise_time_ns <= 0:
        raise ValueError("rise time must be positive")
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    skew_budget_ps = fraction * rise_time_ns * 1000.0  # ns -> ps
    return length_for_skew(skew_budget_ps, er_eff)


@dataclass(frozen=True)
class MatchMember:
    name: str
    length_mils: float
    mismatch_mils: float        # how far short of the longest member
    skew_ps: float              # delay error that mismatch represents
    compensation_mils: float    # serpentine copper to add to reach the target
    within_tolerance: bool      # True when no tolerance set, or mismatch <= it


@dataclass(frozen=True)
class MatchReport:
    er_eff: float
    t_pd_ps_per_mil: float
    target_length_mils: float       # the longest member (everyone matches up)
    tolerance_mils: float | None    # match window, if a budget was given
    worst_skew_ps: float            # longest-vs-shortest delay skew
    all_matched: bool               # all within tolerance (False if no budget)
    members: tuple[MatchMember, ...]


def match_group_report(
    lengths: dict[str, float],
    er_eff: float,
    *,
    tolerance_mils: float | None = None,
    skew_budget_ps: float | None = None,
) -> MatchReport:
    """Assess a length-matched group (a bus or a P/N diff pair).

    Targets the longest net (matching adds copper, never removes it) and, for
    each member, reports the mismatch, the delay skew it represents, and the
    serpentine compensation needed. If a ``skew_budget_ps`` (or an explicit
    ``tolerance_mils``) is given, flags each net and the group as matched.
    """
    if not lengths:
        raise ValueError("lengths must contain at least one net")
    for name, L in lengths.items():
        if L < 0:
            raise ValueError(f"net {name!r} has negative length")

    tol = tolerance_mils
    if tol is None and skew_budget_ps is not None:
        tol = length_for_skew(skew_budget_ps, er_eff)

    target = max(lengths.values())
    t_pd = propagation_delay_ns_per_inch(er_eff)  # ps/mil

    members: list[MatchMember] = []
    for name in sorted(lengths):
        L = lengths[name]
        mismatch = target - L
        members.append(MatchMember(
            name=name, length_mils=L, mismatch_mils=mismatch,
            skew_ps=mismatch * t_pd, compensation_mils=mismatch,
            within_tolerance=(tol is None) or (mismatch <= tol + 1e-9)))

    worst_skew = (target - min(lengths.values())) * t_pd
    all_matched = tol is not None and all(m.within_tolerance for m in members)
    return MatchReport(
        er_eff=er_eff, t_pd_ps_per_mil=t_pd, target_length_mils=target,
        tolerance_mils=tol, worst_skew_ps=worst_skew,
        all_matched=all_matched, members=tuple(members))


@dataclass(frozen=True)
class IntraPairSkew:
    """Within-pair (P-vs-N) skew of one differential pair.

    Distinct from the bus / inter-pair matching :class:`MatchReport` does: that
    matches several independent nets up to the longest. Here the two legs of one
    pair are matched to *each other*, because any P-vs-N length difference
    converts differential energy into common mode -- it degrades the eye and
    radiates as EMI (Bogatin, *Signal Integrity -- Simplified*; Johnson &
    Graham). The budget is correspondingly far tighter than the bus skew budget.
    """

    leg_lengths: tuple[float, float]   # (leg-0 total, leg-1 total) mils
    mismatch_mils: float               # |leg0 - leg1|
    skew_ps: float                     # delay error the mismatch represents
    longer_leg: int                    # 0 or 1 -- the leg to leave alone
    compensation_mils: float           # serpentine copper to add to the shorter leg
    tolerance_mils: float | None       # intra-pair window, if a budget was given
    within_tolerance: bool             # True when no budget, or mismatch <= it
    t_pd_ps_per_mil: float


def intra_pair_skew(
    legs: tuple,
    segment_lengths: dict[str, float],
    er_eff: float,
    *,
    tolerance_mils: float | None = None,
    skew_budget_ps: float | None = None,
) -> IntraPairSkew:
    """Assess the within-pair skew of a differential pair's two legs.

    ``legs`` is the pair's two legs, each an iterable of the net-segment names
    that make up that leg -- exactly the shape of
    :attr:`~eda_agent.design.diffpairs.DiffPair.legs` (a series resistor or
    AC-coupling cap splits one leg into several segments, so each leg's routed
    length is the sum of its segments). ``segment_lengths`` maps every net name
    in both legs to its routed length in mils.

    The shorter leg is the one to lengthen (you add serpentine copper, never
    remove it), so ``compensation_mils`` is the mismatch and ``longer_leg`` is
    the leg to leave alone. A ``skew_budget_ps`` (or explicit ``tolerance_mils``)
    flags whether the pair is matched. Naming-agnostic: works on the topology
    legs, not on ``_P``/``_N`` suffixes.
    """
    if len(legs) != 2:
        raise ValueError("a differential pair has exactly two legs")

    leg_totals: list[float] = []
    for i, leg in enumerate(legs):
        nets = list(leg)
        if not nets:
            raise ValueError(f"leg {i} has no net segments")
        total = 0.0
        for nm in nets:
            if nm not in segment_lengths:
                raise ValueError(f"no routed length for net {nm!r}")
            L = segment_lengths[nm]
            if L < 0:
                raise ValueError(f"net {nm!r} has negative length")
            total += L
        leg_totals.append(total)

    tol = tolerance_mils
    if tol is None and skew_budget_ps is not None:
        tol = length_for_skew(skew_budget_ps, er_eff)

    t_pd = propagation_delay_ns_per_inch(er_eff)  # ps/mil
    mismatch = abs(leg_totals[0] - leg_totals[1])
    longer = 0 if leg_totals[0] >= leg_totals[1] else 1
    return IntraPairSkew(
        leg_lengths=(leg_totals[0], leg_totals[1]),
        mismatch_mils=mismatch,
        skew_ps=mismatch * t_pd,
        longer_leg=longer,
        compensation_mils=mismatch,
        tolerance_mils=tol,
        within_tolerance=(tol is None) or (mismatch <= tol + 1e-9),
        t_pd_ps_per_mil=t_pd)


def assess_length_match(
    *,
    dielectric_constant: float = 4.2,
    geometry: str = "stripline",
    width_mils: float | None = None,
    dielectric_height_mils: float | None = None,
    skew_budget_ps: float | None = None,
    rise_time_ns: float | None = None,
    match_fraction: float = 0.1,
    lengths: dict[str, float] | None = None,
) -> dict:
    """Convenience front end: resolve Er_eff and a tolerance, then (optionally)
    report a group. Buses are usually routed on inner stripline layers, so that
    is the default geometry. The skew budget is taken from ``skew_budget_ps`` if
    given, else from ``match_fraction * rise_time_ns``.
    """
    er_eff = effective_dielectric_constant(
        dielectric_constant, geometry,
        width_mils=width_mils, height_mils=dielectric_height_mils)
    t_pd = propagation_delay_ns_per_inch(er_eff)

    budget_ps = skew_budget_ps
    if budget_ps is None and rise_time_ns:
        budget_ps = match_fraction * rise_time_ns * 1000.0
    tol = length_for_skew(budget_ps, er_eff) if budget_ps else None

    out: dict = {
        "er_eff": er_eff,
        "t_pd_ps_per_mil": t_pd,
        "skew_budget_ps": budget_ps,
        "tolerance_mils": tol,
    }
    if lengths:
        out["report"] = match_group_report(
            lengths, er_eff, skew_budget_ps=budget_ps)
    return out


__all__ = [
    "length_for_skew",
    "skew_for_length",
    "match_tolerance_for_rise_time",
    "MatchMember",
    "MatchReport",
    "match_group_report",
    "IntraPairSkew",
    "intra_pair_skew",
    "assess_length_match",
]
