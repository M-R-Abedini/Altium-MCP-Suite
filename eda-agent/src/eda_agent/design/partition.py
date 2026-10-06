# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Min-cut netlist partitioning (Kernighan-Lin style swap refinement).

Splits a set of components into balanced groups that minimise the number of
nets crossing between groups. The classic use is deciding how to break a
dense design across schematic sheets, or grouping a PCB into functional
"rooms" -- in both cases you want each group internally well-connected and
few signals crossing the boundary.

Pure Python, deterministic, no numpy. Power/ground rails (high fan-out, they
connect nearly everything and would resist any split) are excluded from the
connectivity graph via a fan-out cap, so the partition follows the SIGNAL
structure -- exactly what a functional split should track.

The refinement is the full Kernighan-Lin pass: each pass tentatively makes
the best swap of every unlocked pair -- including cut-increasing ones --
tracking the running gain, then commits only the prefix of swaps with the
maximum cumulative gain. Allowing the cut to dip before it climbs is what
escapes the local optima a plain improving-swap hill-climber gets stuck in.
Deterministic. Groups beyond two come from recursive bisection of the
largest group.
"""

from __future__ import annotations

from typing import Iterable


def _build_adjacency(
    refdes: list[str],
    nets: Iterable[Iterable[str]],
    max_fanout: int,
) -> dict[str, dict[str, int]]:
    """Component-component graph: edge weight = count of shared (signal) nets.

    Nets touching more than ``max_fanout`` parts are skipped as rails/buses.
    """
    present = set(refdes)
    adj: dict[str, dict[str, int]] = {r: {} for r in refdes}
    for net in nets:
        members = [r for r in dict.fromkeys(net) if r in present]
        if len(members) < 2:
            continue
        if max_fanout and len(members) > max_fanout:
            continue
        for i in range(len(members)):
            a = members[i]
            for j in range(i + 1, len(members)):
                b = members[j]
                adj[a][b] = adj[a].get(b, 0) + 1
                adj[b][a] = adj[b].get(a, 0) + 1
    return adj


def _gains(side_a: set, side_b: set, adj: dict[str, dict[str, int]]) -> dict[str, int]:
    """D-values: external minus internal connectivity for every component.

    Moving a component to the other side reduces the cut by its D-value;
    swapping a in A with b in B changes the cut by ``D[a] + D[b] - 2*w(a,b)``.
    """
    d: dict[str, int] = {}
    for side, other in ((side_a, side_b), (side_b, side_a)):
        for x in side:
            ext = 0
            intl = 0
            for o, w in adj[x].items():
                if o in other:
                    ext += w
                elif o in side:
                    intl += w
            d[x] = ext - intl
    return d


def _bisect(group: list[str], adj: dict[str, dict[str, int]]) -> tuple[list[str], list[str]]:
    """Balanced min-cut bipartition by full Kernighan-Lin passes.

    Each pass tentatively makes the best swap of every still-unlocked pair --
    even cut-INCREASING ones -- recording the running gain, then commits only
    the prefix of swaps that gave the maximum cumulative gain. Letting the
    search dip before climbing is what escapes the local optima a plain
    improving-swap hill-climber gets stuck in. Repeats until no pass yields a
    positive cumulative gain. Deterministic (sorted iteration, fixed ties).
    """
    order = sorted(group)
    n = len(order)
    if n < 2:
        return order, []
    mid = (n + 1) // 2
    side_a = set(order[:mid])
    side_b = set(order[mid:])

    while True:
        cur_a = set(side_a)
        cur_b = set(side_b)
        locked: set[str] = set()
        seq: list[tuple[int, str, str]] = []
        for _ in range(min(len(cur_a), len(cur_b))):
            d = _gains(cur_a, cur_b, adj)
            best_gain: int | None = None
            best_pair: tuple[str, str] | None = None
            for a in sorted(x for x in cur_a if x not in locked):
                for b in sorted(x for x in cur_b if x not in locked):
                    g = d[a] + d[b] - 2 * adj[a].get(b, 0)
                    if best_gain is None or g > best_gain:
                        best_gain = g
                        best_pair = (a, b)
            if best_pair is None:
                break
            a, b = best_pair
            seq.append((best_gain, a, b))
            locked.add(a)
            locked.add(b)
            cur_a.discard(a)
            cur_a.add(b)
            cur_b.discard(b)
            cur_b.add(a)

        # Commit the prefix with the maximum cumulative gain.
        cum = 0
        best_cum = 0
        best_k = 0
        for k, (g, _, _) in enumerate(seq):
            cum += g
            if cum > best_cum:
                best_cum = cum
                best_k = k + 1
        if best_cum <= 0:
            break
        for _, a, b in seq[:best_k]:
            side_a.discard(a)
            side_a.add(b)
            side_b.discard(b)
            side_b.add(a)
    return sorted(side_a), sorted(side_b)


def partition_netlist(
    refdes: list[str],
    nets: Iterable[Iterable[str]],
    n_groups: int = 2,
    max_fanout: int = 8,
) -> dict:
    """Partition ``refdes`` into ``n_groups`` balanced min-cut groups.

    ``nets`` is an iterable of refdes collections (one per net). Returns
    ``{group_of: {refdes: idx}, n_groups, cut_nets, group_sizes}`` where
    ``cut_nets`` is how many nets (including rails) span more than one group
    -- the boundary signal count a split would have to carry.
    """
    refdes = list(dict.fromkeys(refdes))
    nets = [list(n) for n in nets]
    n_groups = max(1, int(n_groups))
    if n_groups == 1 or len(refdes) <= 1:
        group_of = {r: 0 for r in refdes}
        return _result(group_of, nets, 1)

    adj = _build_adjacency(refdes, nets, max_fanout)

    # Recursive bisection: keep splitting the largest group until n_groups.
    groups: list[list[str]] = [sorted(refdes)]
    while len(groups) < n_groups:
        # Pick the largest splittable group.
        idx = max(range(len(groups)),
                  key=lambda i: (len(groups[i]), -i))
        target = groups[idx]
        if len(target) < 2:
            break
        a, b = _bisect(target, adj)
        groups[idx:idx + 1] = [a, b]

    group_of = {r: g for g, grp in enumerate(groups) for r in grp}
    return _result(group_of, nets, len(groups))


def _result(group_of: dict[str, int], nets: list[list[str]], n_groups: int) -> dict:
    cut = 0
    for net in nets:
        seen = {group_of.get(r) for r in net if r in group_of}
        seen.discard(None)
        if len(seen) > 1:
            cut += 1
    sizes = [0] * n_groups
    for g in group_of.values():
        sizes[g] += 1
    return {
        "group_of": dict(group_of),
        "n_groups": n_groups,
        "cut_nets": cut,
        "group_sizes": sizes,
    }
