# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Differential-pair detection for the design engines.

A *differential pair* is the two complementary legs of a signal that a
datasheet says must be routed as a matched pair (USB D+/D-, a CAN bus, an
LVDS / SerDes lane, a differential clock). Beyond length-matched routing,
the small components that sit ON the pair -- a series resistor on each leg,
an AC-coupling capacitor on each leg, a series ESD part -- want SYMMETRIC,
matched placement so the two legs stay electrically identical. The PCB
placer already keeps tagged parts together and on a common axis
(``match_group`` / ``match_axis``); this module produces those tags
structurally, so a planner that marks the differential nets gets matched
placement of the pair's series elements for free.

Detection is naming-agnostic: it does NOT parse ``_P``/``_N`` / ``+``/``-``
suffixes. The signal is the planner-asserted net role ``differential`` (a
documented :class:`~eda_agent.design.plan.Net` role) plus topology. The
matched series elements split a leg into segments (J1 -Rseries- U1 is two
nets), so the two segments of one leg are first re-joined through the 2-pin
part that bridges them (a union-find over the differential nets), then the
two legs are paired by the multi-pin endpoint devices they share.

When a group is ambiguous -- more than two legs share the same endpoint
devices (a multi-lane connector where structure alone cannot say which leg
pairs with which) -- the group is SKIPPED, not guessed. Better no tag than a
wrong matched pair.

NDA scope: reads only the current plan's topology; no cross-project state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from eda_agent.design.plan import DesignPlan

# A part with at least this many distinct pins is a pair ENDPOINT device (a
# driver, receiver, or connector) rather than a 2-pin series element. The
# series elements that get matched are exactly the 2-pin parts.
_ENDPOINT_PIN_THRESHOLD = 3


@dataclass(frozen=True)
class DiffPair:
    """One detected differential pair.

    ``nets`` is every differential net in the pair (both legs, including the
    series-split segments), sorted. ``legs`` is the two legs as net tuples.
    ``endpoints`` is the set of multi-pin devices both legs share (the driver
    / receiver / connector). ``series_parts`` is the 2-pin elements sitting on
    the pair (series resistors, AC-coupling caps, series ESD), sorted.
    """

    nets: tuple[str, ...]
    legs: tuple[tuple[str, ...], tuple[str, ...]]
    endpoints: frozenset[str]
    series_parts: tuple[str, ...]


def _pin_counts(plan: DesignPlan) -> dict[str, int]:
    """refdes -> number of distinct pins it exposes across all nets."""
    pins: dict[str, set[str]] = {}
    for net in plan.nets:
        for pr in net.pins:
            pins.setdefault(pr.refdes, set()).add(str(pr.pin))
    return {r: len(s) for r, s in pins.items()}


def _differential_net_names(plan: DesignPlan) -> set[str]:
    return {
        n.name for n in plan.nets
        if (n.role or "").strip().lower() == "differential"
    }


class _UnionFind:
    def __init__(self, items):
        self._parent = {x: x for x in items}

    def find(self, x: str) -> str:
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        # path-compress
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


def detect_diff_pairs(plan: DesignPlan) -> list[DiffPair]:
    """Find differential pairs in ``plan`` (see the module docstring).

    Returns a deterministically ordered list of :class:`DiffPair`. Empty when
    fewer than two differential-role nets exist, or when no two legs share an
    endpoint device unambiguously -- so a design with no differential signals,
    or only ambiguous multi-lane groups, is unaffected.
    """
    diff_names = _differential_net_names(plan)
    if len(diff_names) < 2:
        return []

    pin_count = _pin_counts(plan)
    parts_of_net: dict[str, list[str]] = {
        n.name: list(dict.fromkeys(pr.refdes for pr in n.pins))
        for n in plan.nets
    }
    nets_of_part: dict[str, list[str]] = {}
    for name, refs in parts_of_net.items():
        for r in refs:
            nets_of_part.setdefault(r, []).append(name)

    # Re-join the series-split segments of one leg: a 2-pin part whose BOTH
    # nets are differential bridges them into a single logical leg. A 2-pin
    # part with one pin on a non-differential net (an ESD diode to ground, a
    # termination resistor to a rail) does NOT bridge -- it is a leaf on the
    # leg, not a series link.
    uf = _UnionFind(diff_names)
    for r, cnt in pin_count.items():
        if cnt != 2:
            continue
        on_diff = [nm for nm in nets_of_part.get(r, []) if nm in diff_names]
        if len(set(on_diff)) == 2:
            a, b = sorted(set(on_diff))
            uf.union(a, b)

    # Build the legs (connected components of differential nets).
    legs: dict[str, set[str]] = {}
    for nm in diff_names:
        legs.setdefault(uf.find(nm), set()).add(nm)

    # For each leg, the multi-pin endpoint devices and the 2-pin elements on it.
    leg_records = []
    for netset in legs.values():
        endpoints: set[str] = set()
        series: set[str] = set()
        for nm in netset:
            for r in parts_of_net.get(nm, []):
                pc = pin_count.get(r, 0)
                if pc >= _ENDPOINT_PIN_THRESHOLD:
                    endpoints.add(r)
                elif pc == 2:
                    series.add(r)
        leg_records.append((frozenset(netset), frozenset(endpoints),
                            frozenset(series)))

    # Pair legs that share the same endpoint-device set. Exactly two legs with
    # a non-empty shared endpoint set is an unambiguous pair; anything else is
    # skipped (no guess).
    by_endpoints: dict[frozenset[str], list] = {}
    for rec in leg_records:
        by_endpoints.setdefault(rec[1], []).append(rec)

    pairs: list[DiffPair] = []
    for endpoints, group in by_endpoints.items():
        if not endpoints or len(group) != 2:
            continue
        l1, l2 = group
        nets = tuple(sorted(l1[0] | l2[0]))
        series = tuple(sorted(l1[2] | l2[2]))
        pairs.append(DiffPair(
            nets=nets,
            legs=(tuple(sorted(l1[0])), tuple(sorted(l2[0]))),
            endpoints=endpoints,
            series_parts=series,
        ))

    pairs.sort(key=lambda p: p.nets)
    return pairs


def diff_pair_match_groups(plan: DesignPlan) -> dict[str, str]:
    """Map each differential pair's matched series elements to a shared
    ``match_group`` tag, ready to pass to ``pcb_plan_placement``.

    The 2-pin elements on a pair are grouped BY KIND (resistors with
    resistors, caps with caps): a kind that appears at least twice -- one on
    each leg -- is a matched set and gets a tag, so the placer keeps the two
    series resistors together and on a common axis. A lone element on one leg
    (no counterpart) is left untagged: there is nothing to match it to.
    Returns ``{refdes: group_name}``; empty when there is nothing to match, so
    a design with no differential pairs is unaffected.
    """
    from eda_agent.design.motifs import _kind_from_refdes

    out: dict[str, str] = {}
    for i, dp in enumerate(detect_diff_pairs(plan)):
        by_kind: dict[str, list[str]] = {}
        for r in dp.series_parts:
            by_kind.setdefault(_kind_from_refdes(r), []).append(r)
        for kind, refs in sorted(by_kind.items()):
            if len(refs) < 2:
                continue
            grp = f"_dp{i}_{kind}"
            for r in sorted(refs):
                out[r] = grp
    return out


__all__ = [
    "DiffPair",
    "detect_diff_pairs",
    "diff_pair_match_groups",
]
