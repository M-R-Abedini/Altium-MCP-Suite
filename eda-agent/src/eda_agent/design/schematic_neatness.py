# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Interpretable schematic-neatness report for a wired SchematicCanvas.

The layout scorer in :mod:`quality` collapses a sheet to one ``weighted_total``
badness number that is good for *ranking* candidate layouts but opaque for
*understanding* why a sheet looks unprofessional -- and (measured repeatedly)
it under-values structure, rewarding a compact-but-structureless force-directed
layout over a canonical one. This module reports the badness DECOMPOSED into
named, individually-meaningful metrics, so a human (or a future structural
engine) can see which dimension is bad and by how much. It is a pure read-only
diagnostic -- it never changes a layout, so it cannot regress one.

The metrics, each with its drawing-quality meaning:

* ``spread`` -- placement bbox (w, h) in mils and its aspect. Sprawl is the
  root cause of long wires, label fallback, and long power spokes.
* ``signal_wire_mils`` / ``power_wire_mils`` -- routed length split. On spread
  boards power/ground spokes dominate (a spread symptom, not a routing fault).
* ``detour_ratio`` -- signal routed length / MST-of-pins lower bound. ~1 means
  the router is near-optimal; >>1 means genuine detours. (Diagnoses routing
  vs placement: a large MST with ratio ~1 is a placement-spread problem.)
* ``label_fallback_frac`` -- fraction of multi-pin SIGNAL nets drawn entirely
  as net-labels instead of wires. This is the visible "label soup"; it rises
  when wiring a net at the given placement would short or cross.
* ``bends_per_signal_net`` / ``bends_per_power_net`` -- mean orthogonal bends,
  split by net kind. SIGNAL bends are the meaningful routing-neatness signal
  (pro: ~1-2 per net, an L); POWER bends just track spoke count (each of N
  rail pins L-routes to its port), so a high power figure is a spread/spoke
  symptom, not a routing fault. Measured: signal nets route near-minimally;
  the high overall bend count is power/ground spokes. The global scorer does
  NOT penalise bends at all.
* ``diagonal_wires`` -- non-orthogonal segments (should be 0).
* ``port_fragmentation`` -- power/ground glyphs per rail net. Many per-pin
  ground symbols are conventional; a high count still reads busy.
* ``straddle_nets`` -- nets whose pins on one IC land on OPPOSITE body sides,
  forcing a wrap-around wire no matter the placement (a symbol pin-side issue,
  upstream of layout).
* ``four_way_junctions`` -- junction dots where >=4 same-net wire-ends meet
  (ambiguous "+" junctions; pros stagger into two T's).
* ``duplicate_wires`` -- exact-overlapping same-net segments (pure redundancy).

Naming-agnostic; reads only the canvas + plan. Pure offline.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class NeatnessReport:
    """Decomposed, interpretable schematic-neatness metrics for one sheet."""

    spread_w_mils: int
    spread_h_mils: int
    spread_aspect: float
    signal_wire_mils: int
    power_wire_mils: int
    detour_ratio: float
    label_fallback_frac: float
    labeled_nets: tuple[str, ...]
    bends_per_signal_net: float
    bends_per_power_net: float
    diagonal_wires: int
    port_fragmentation: dict[str, int]
    straddle_nets: int
    four_way_junctions: int
    duplicate_wires: int
    breakdown: dict[str, float] = field(default_factory=dict)

    def summary(self) -> str:
        """One-line-per-metric human-readable digest."""
        lines = [
            f"spread       {self.spread_w_mils}x{self.spread_h_mils} mils "
            f"(aspect {self.spread_aspect:.2f})",
            f"wire length  signal {self.signal_wire_mils} / power "
            f"{self.power_wire_mils} mils  (detour {self.detour_ratio:.2f}x)",
            f"label soup   {self.label_fallback_frac*100:.0f}% of signal nets "
            f"labelled {list(self.labeled_nets) or ''}",
            f"bends/net    signal {self.bends_per_signal_net:.1f} / power "
            f"{self.bends_per_power_net:.1f}   diagonal wires "
            f"{self.diagonal_wires}",
            f"ports/rail   {self.port_fragmentation}",
            f"straddles    {self.straddle_nets}   4-way junctions "
            f"{self.four_way_junctions}   dup wires {self.duplicate_wires}",
        ]
        return "\n".join(lines)

    def flags(self) -> list[str]:
        """Dimensions that are out of a rough professional bound, worst-first.

        Thresholds come from the neat-schematic exploration and are the
        dimensions with a CLEAR target (not the placement-spread ones, which
        are board-dependent and have no single threshold). Each returned string
        names the dimension and its value so a caller can prioritise. An empty
        list means nothing wrong on the checked dimensions.
        """
        out: list[tuple[int, str]] = []
        # (severity, message); higher severity first.
        if self.diagonal_wires > 0:
            out.append((9, f"non-orthogonal wires: {self.diagonal_wires}"))
        if self.four_way_junctions > 0:
            out.append((7, f"ambiguous 4-way junctions: "
                            f"{self.four_way_junctions} (stagger into T's)"))
        if self.detour_ratio > 1.5:
            out.append((6, f"signal routing detours: detour ratio "
                            f"{self.detour_ratio:.2f}x"))
        # power bends: with straight stubs ~1; >4 means L-spoke topology
        if self.bends_per_power_net > 4.0:
            out.append((5, f"power L-spoke clutter: {self.bends_per_power_net:.1f} "
                            f"bends/rail-net (straight stubs would cut this)"))
        if self.bends_per_signal_net > 3.0:
            out.append((5, f"high signal bends: {self.bends_per_signal_net:.1f}/net"))
        if self.straddle_nets > 0:
            out.append((4, f"pin-straddle nets: {self.straddle_nets} "
                            f"(symbol pin-side forces wrap-arounds)"))
        if self.label_fallback_frac > 0.2:
            out.append((3, f"label fallback: {self.label_fallback_frac*100:.0f}% "
                            f"of signal nets labelled {list(self.labeled_nets)}"))
        if self.duplicate_wires > 0:
            out.append((1, f"duplicate wire segments: {self.duplicate_wires}"))
        out.sort(key=lambda t: -t[0])
        return [m for _, m in out]


def _mst_len(pts: list[tuple[int, int]]) -> int:
    """Rectilinear MST length of a point set (Prim, |pts| small)."""
    if len(pts) < 2:
        return 0
    in_tree = {0}
    total = 0
    while len(in_tree) < len(pts):
        best = None
        for i in in_tree:
            for j in range(len(pts)):
                if j in in_tree:
                    continue
                d = abs(pts[i][0] - pts[j][0]) + abs(pts[i][1] - pts[j][1])
                if best is None or d < best[0]:
                    best = (d, j)
        total += best[0]
        in_tree.add(best[1])
    return total


def _bends(segs: list[tuple[int, int, int, int]]) -> int:
    """Count vertices where a horizontal and a vertical segment of one net
    meet -- an orthogonal bend (or pin-entry corner)."""
    inc: dict[tuple[int, int], list[int]] = defaultdict(lambda: [0, 0])
    for (x1, y1, x2, y2) in segs:
        if y1 == y2 and x1 != x2:
            inc[(x1, y1)][0] += 1
            inc[(x2, y2)][0] += 1
        elif x1 == x2 and y1 != y2:
            inc[(x1, y1)][1] += 1
            inc[(x2, y2)][1] += 1
    return sum(1 for h, v in inc.values() if h >= 1 and v >= 1)


def _straddle_count(canvas, plan) -> int:
    """Nets whose pins on a single >=3-pin part land on opposite body sides."""
    inst_by_ref = {i.refdes: i for i in canvas.instances}
    count = 0
    for net in plan.nets:
        by_part: dict[str, set[str]] = defaultdict(set)
        for pr in net.pins:
            by_part[pr.refdes].add(str(pr.pin))
        for ref, pins in by_part.items():
            inst = inst_by_ref.get(ref)
            if inst is None or len(inst.symbol.pins) < 3:
                continue
            bb = inst.world_bbox()
            cx = (bb.x_min + bb.x_max) / 2.0
            sides = set()
            for p in pins:
                pw = inst.pin_world(p)
                if pw is not None:
                    sides.add("L" if pw.x < cx else "R")
            if len(sides) > 1:
                count += 1
    return count


def neatness_report(canvas, plan, *, sheet: Optional[str] = None) -> NeatnessReport:
    """Compute the decomposed neatness metrics for one sheet (see module doc)."""
    if sheet is None:
        sheet = canvas.instances[0].sheet if canvas.instances else "main"
    insts = canvas.instances_on(sheet)
    wires = canvas.wires_on(sheet)
    ports = canvas.power_ports_on(sheet)
    junctions = canvas.junctions_on(sheet)

    pg = {n.name: bool(n.is_power or n.is_ground) for n in plan.nets}

    # spread
    if insts:
        bbs = [i.world_bbox() for i in insts]
        w = max(b.x_max for b in bbs) - min(b.x_min for b in bbs)
        h = max(b.y_max for b in bbs) - min(b.y_min for b in bbs)
    else:
        w = h = 0
    aspect = (max(w, h) / max(1, min(w, h))) if (w and h) else 1.0

    # wire length split
    sig_len = sum(abs(x.x1 - x.x2) + abs(x.y1 - x.y2)
                  for x in wires if not pg.get(x.net))
    pwr_len = sum(abs(x.x1 - x.x2) + abs(x.y1 - x.y2)
                  for x in wires if pg.get(x.net))
    diag = sum(1 for x in wires if x.x1 != x.x2 and x.y1 != x.y2)

    # detour ratio (signal): routed vs MST of pins
    inst_by_ref = {i.refdes: i for i in insts}
    sig_mst = 0
    rl: dict[str, int] = defaultdict(int)
    for x in wires:
        rl[x.net] += abs(x.x1 - x.x2) + abs(x.y1 - x.y2)
    for net in plan.nets:
        if pg.get(net.name):
            continue
        pts = []
        for pr in net.pins:
            it = inst_by_ref.get(pr.refdes)
            if it is None:
                continue
            pw = it.pin_world(str(pr.pin))
            if pw is not None:
                pts.append((pw.x, pw.y))
        sig_mst += _mst_len(pts)
    detour = (sig_len / sig_mst) if sig_mst else 0.0

    # label fallback fraction (multi-pin signal nets with no wire)
    wired = {x.net for x in wires}
    multi = [n for n in plan.nets
             if len(n.pins) >= 2 and not pg.get(n.name)]
    labeled = tuple(sorted(n.name for n in multi if n.name not in wired))
    label_frac = (len(labeled) / len(multi)) if multi else 0.0

    # bends per wired net, split signal vs power (power = spoke-count symptom)
    by_net: dict[str, list[tuple[int, int, int, int]]] = defaultdict(list)
    for x in wires:
        by_net[x.net].append((x.x1, x.y1, x.x2, x.y2))
    sig_nets = [n for n in by_net if not pg.get(n)]
    pwr_nets = [n for n in by_net if pg.get(n)]
    bends_sig = (sum(_bends(by_net[n]) for n in sig_nets) / len(sig_nets)
                 if sig_nets else 0.0)
    bends_pwr = (sum(_bends(by_net[n]) for n in pwr_nets) / len(pwr_nets)
                 if pwr_nets else 0.0)

    # port fragmentation (glyphs per rail net)
    frag: dict[str, int] = Counter(p.text for p in ports)
    port_frag = {n.name: frag.get(n.name, 0) for n in plan.nets if pg.get(n.name)}

    # 4-way junctions and duplicate wires
    deg: dict[tuple[int, int], int] = defaultdict(int)
    for x in wires:
        deg[(x.x1, x.y1)] += 1
        deg[(x.x2, x.y2)] += 1
    junc_pts = {(j.x, j.y) for j in junctions}
    four_way = sum(1 for p, d in deg.items() if d >= 4 and p in junc_pts)

    seen: Counter = Counter()
    for x in wires:
        a, b = (x.x1, x.y1), (x.x2, x.y2)
        seen[(x.net,) + (a + b if a <= b else b + a)] += 1
    dup = sum(c - 1 for c in seen.values() if c > 1)

    straddles = _straddle_count(canvas, plan)

    return NeatnessReport(
        spread_w_mils=int(w), spread_h_mils=int(h), spread_aspect=round(aspect, 3),
        signal_wire_mils=int(sig_len), power_wire_mils=int(pwr_len),
        detour_ratio=round(detour, 3),
        label_fallback_frac=round(label_frac, 3), labeled_nets=labeled,
        bends_per_signal_net=round(bends_sig, 2),
        bends_per_power_net=round(bends_pwr, 2), diagonal_wires=diag,
        port_fragmentation=port_frag, straddle_nets=straddles,
        four_way_junctions=four_way, duplicate_wires=dup,
    )


__all__ = ["NeatnessReport", "neatness_report"]
