# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Order parts so connected ones sit near each other on the grid.

The schematic and PCB emitters place parts on a grid; ordering that grid by
connectivity (a greedy breadth-first walk of the net graph, starting from the
highest-degree part) clusters parts that share nets, which shortens the ratsnest
and makes the generated layout far more usable than arbitrary order.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def order_by_connectivity(parts: list[dict[str, Any]],
                          nets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return ``parts`` reordered so net-connected parts are adjacent."""
    refset = {p.get("refdes", "") for p in parts if p.get("refdes")}
    adj: dict[str, set] = defaultdict(set)
    for net in nets:
        refs = [n.get("refdes") or n.get("reference", "")
                for n in net.get("nodes", [])]
        refs = [r for r in refs if r in refset]
        for a in refs:
            for b in refs:
                if a != b:
                    adj[a].add(b)

    def degree(r: str) -> int:
        return len(adj.get(r, ()))

    seen: set = set()
    order: list[str] = []
    remaining = sorted(refset, key=lambda r: (-degree(r), r))
    for start in remaining:
        if start in seen:
            continue
        queue = [start]
        while queue:
            cur = queue.pop(0)
            if cur in seen:
                continue
            seen.add(cur)
            order.append(cur)
            for nb in sorted(adj.get(cur, ()), key=lambda r: (-degree(r), r)):
                if nb not in seen:
                    queue.append(nb)

    by_ref = {p.get("refdes", ""): p for p in parts}
    ordered = [by_ref[r] for r in order if r in by_ref]
    ordered += [p for p in parts if p.get("refdes", "") not in seen]
    return ordered
