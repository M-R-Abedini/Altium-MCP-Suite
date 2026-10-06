# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Bus detection for the schematic pipeline.

A *bus* is a group of related signal nets that run together between the same
devices -- a data bus (D0..D7), an address bus, a parallel-peripheral or
memory interface. A professional schematic draws such a group as a single
thick BUS line with short 45-degree bus entries tapping each pin, rather than
as N separate wires or N label pairs: it groups the signals visually and cuts
label clutter on wide interfaces.

This module only DETECTS buses (the structural grouping). The geometry (bus
polyline + entries + per-net labels) and the Altium emit are layered on top.

Detection is naming-agnostic -- it does NOT rely on ``D0/D1/...`` or bracket
notation -- because the project plans nets by topology, not by name. The
signature: a set of >= ``min_width`` SIGNAL nets that each connect exactly the
same set of parts, where that set holds at least two multi-pin parts (ICs).
The canonical hit is a wide data/address bus between two chips (each member net
= ``{U1, U2}``). Power and ground nets are never buses.

NDA scope: reads only the current plan's topology; no cross-project state.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from eda_agent.design.canvas import (
    BusEntry,
    BusSegment,
    NetLabel,
    SymbolInstance,
    WireSegment,
)
from eda_agent.design.plan import DesignPlan

# A part is treated as a bus endpoint device (not a passive) when it touches at
# least this many nets -- the same >=4 "this is an IC" heuristic used elsewhere
# in the design engine. A 2-pin passive never anchors a bus.
_IC_NET_THRESHOLD = 4

# Default minimum bus width. Below this, a few nets shared between two chips are
# more readable as individual wires/labels than as a bus glyph; real buses are
# 4 bits and up (a byte lane, a nibble, an address/data bus).
DEFAULT_MIN_WIDTH = 4


@dataclass(frozen=True)
class BusGroup:
    """One detected bus.

    ``nets`` are the member signal-net names (sorted, so the result is
    deterministic). ``parts`` is the set of refdes the bus spans -- typically
    the two ICs whose pins the bus connects. ``endpoints`` is the subset of
    ``parts`` that are multi-pin devices (the IC anchors the geometry will tap).
    """

    nets: tuple[str, ...]
    parts: frozenset[str]
    endpoints: frozenset[str]

    @property
    def width(self) -> int:
        return len(self.nets)


def _net_count_by_refdes(plan: DesignPlan) -> dict[str, int]:
    counts: dict[str, int] = {}
    for net in plan.nets:
        for pin_ref in dict.fromkeys(pr.refdes for pr in net.pins):
            counts[pin_ref] = counts.get(pin_ref, 0) + 1
    return counts


def detect_buses(
    plan: DesignPlan,
    *,
    min_width: int = DEFAULT_MIN_WIDTH,
) -> list[BusGroup]:
    """Find buses in ``plan`` (see module docstring for the signature).

    Returns a list of :class:`BusGroup`, deterministically ordered (by the
    member nets). Empty when no group of >= ``min_width`` signal nets shares the
    same multi-IC part set -- so a design with no wide interface is unaffected.
    """
    net_counts = _net_count_by_refdes(plan)
    ground_power = {
        n.name for n in plan.nets
        if n.is_power or n.is_ground or (n.role or "") == "ground"
    }

    # Group signal nets by the exact set of parts they connect.
    by_parts: dict[frozenset[str], list[str]] = {}
    for net in plan.nets:
        if net.name in ground_power:
            continue
        parts = frozenset(pr.refdes for pr in net.pins)
        if len(parts) < 2:
            continue
        by_parts.setdefault(parts, []).append(net.name)

    buses: list[BusGroup] = []
    for parts, nets in by_parts.items():
        if len(nets) < min_width:
            continue
        endpoints = frozenset(
            p for p in parts if net_counts.get(p, 0) >= _IC_NET_THRESHOLD)
        if len(endpoints) < 2:
            continue
        buses.append(BusGroup(
            nets=tuple(sorted(nets)), parts=parts, endpoints=endpoints))

    buses.sort(key=lambda b: b.nets)
    return buses


# Default geometry (mils). DEPTH is how far the bus line sits past the pin
# column; ENTRY is the 45-degree bus-entry size (the wire stub fills the rest).
_BUS_DEPTH = 500
_BUS_ENTRY = 100

# Unit outward vector for each pin orientation (0=right,1=up,2=left,3=down) and
# its 90-degree perpendicular (the axis the bus line runs along).
_DIR = {0: (1, 0), 1: (0, 1), 2: (-1, 0), 3: (0, -1)}


_INDEXED_NET = re.compile(r"^(.*?)(\d+)$")


def bus_name(nets: tuple[str, ...] | list[str]) -> Optional[str]:
    """Altium bus name for a group, e.g. ``("D0".."D7") -> "D[0..7]"``.

    Only when every member net is ``<prefix><index>`` with ONE shared prefix
    and a CONTIGUOUS index run (so ``D[0..7]`` never implies a missing bit).
    Returns ``None`` otherwise -- arbitrarily-named bus nets keep just their
    per-signal labels, no bus name (the connection still works via those).
    """
    matched = [_INDEXED_NET.match(n) for n in nets]
    if not matched or not all(matched):
        return None
    prefixes = {m.group(1) for m in matched}
    if len(prefixes) != 1:
        return None
    idx = sorted(int(m.group(2)) for m in matched)
    if idx != list(range(idx[0], idx[-1] + 1)):
        return None
    return f"{prefixes.pop()}[{idx[0]}..{idx[-1]}]"


@dataclass(frozen=True)
class BusGeometry:
    """Drawable pieces of one IC's bus stub: the bus polyline segment, the
    per-pin 45-degree entries, the short wire stubs from pin to entry, the
    per-net labels (which carry the actual connectivity), and an optional bus
    NAME label (``D[0..7]``) on the bus line."""

    segments: tuple[BusSegment, ...]
    entries: tuple[BusEntry, ...]
    stubs: tuple[WireSegment, ...]
    labels: tuple[NetLabel, ...]
    bus_label: Optional[NetLabel] = None


def build_bus_geometry(
    bus: BusGroup,
    ic: SymbolInstance,
    plan: DesignPlan,
    sheet: str,
    *,
    depth: int = _BUS_DEPTH,
    entry: int = _BUS_ENTRY,
) -> Optional[BusGeometry]:
    """Bus stub for ONE endpoint IC of ``bus``.

    Lays a bus line parallel to the IC's bus-pin column (the side most of the
    bus's pins point), ``depth`` mils out. Each member pin gets: a wire stub
    from the pin to the bus-entry start, a 45-degree :class:`BusEntry` onto the
    bus line, and a :class:`NetLabel` (the net's identity -- this is what makes
    the connection; the bus line is the visual grouping). All entries slant the
    same way for a clean comb. Returns ``None`` when fewer than two of the bus's
    pins are found on this IC on a single side (no clean bus to draw -- the
    caller falls back to per-pin labels). Pure geometry; nothing is mutated.
    """
    net_pin = {}
    for net in plan.nets:
        for pr in net.pins:
            if pr.refdes == ic.refdes:
                net_pin.setdefault(net.name, pr.pin)

    placed = []
    for net in bus.nets:
        pin_id = net_pin.get(net)
        if pin_id is None:
            continue
        ep = ic.pin_world(pin_id)
        if ep is not None:
            placed.append((net, ep))
    if len(placed) < 2:
        return None

    # Keep only the pins on the dominant side (a clean bus is one column).
    dom = Counter(ep.orientation for _, ep in placed).most_common(1)[0][0]
    placed = [(n, ep) for n, ep in placed if ep.orientation == dom]
    if len(placed) < 2:
        return None
    dx, dy = _DIR[dom]
    perp = (-dy, dx)  # bus line runs along this axis; entries slant +perp
    pxv, pyv = perp

    segments: list[BusSegment] = []
    entries: list[BusEntry] = []
    stubs: list[WireSegment] = []
    labels: list[NetLabel] = []
    landings: list[tuple[int, int]] = []
    for net, ep in placed:
        x, y = ep.x, ep.y
        # stub: pin -> entry start (depth - entry out along the pin direction)
        sx = x + dx * (depth - entry)
        sy = y + dy * (depth - entry)
        stubs.append(WireSegment(x, y, sx, sy, sheet=sheet, net=net))
        # 45-degree entry: out by `entry` along the pin dir AND along +perp
        lx = sx + (dx + pxv) * entry
        ly = sy + (dy + pyv) * entry
        entries.append(BusEntry(sx, sy, lx, ly, sheet=sheet, net=net))
        landings.append((lx, ly))
        # label on the stub, near the pin
        labels.append(NetLabel(text=net, x=x + dx * 150, y=y + dy * 150,
                               orientation=dom, sheet=sheet))

    # The landings are collinear along `perp` (same depth); the bus line spans
    # them, extended half an entry past each end so the comb sits ON the bus.
    proj = lambda p: p[0] * pxv + p[1] * pyv  # noqa: E731 - coord along perp
    lo = min(landings, key=proj)
    hi = max(landings, key=proj)
    seg = BusSegment(
        x1=lo[0] - pxv * entry, y1=lo[1] - pyv * entry,
        x2=hi[0] + pxv * entry, y2=hi[1] + pyv * entry, sheet=sheet)
    segments.append(seg)

    # Optional Altium bus NAME label (D[0..7]) at the bus line's far end.
    name = bus_name(bus.nets)
    name_label = None
    if name is not None:
        name_label = NetLabel(text=name, x=seg.x2 + pxv * entry,
                              y=seg.y2 + pyv * entry, orientation=dom,
                              sheet=sheet)

    return BusGeometry(
        segments=tuple(segments), entries=tuple(entries),
        stubs=tuple(stubs), labels=tuple(labels), bus_label=name_label)


def _wire_crossings(canvas, plan) -> int:
    # Local import: quality imports canvas, and buses imports canvas, so going
    # buses -> quality at call time avoids an import cycle at module load.
    from eda_agent.design.quality import score_canvas
    return score_canvas(canvas, plan).wire_crossings


def _bus_segment_crossings(canvas) -> int:
    """Crossings that INVOLVE a bus line (bus-vs-wire or bus-vs-bus).

    score_canvas only sees WireSegments, so a bus line crossing a wire is
    invisible to the wire-crossing gate. This counts the axis-aligned crossings
    the added bus segments introduce: crossings over (wires + buses) minus the
    wires-only crossings, leaving exactly the bus-involved ones. A clean bus
    adds none; any > 0 means the bus line cuts across a wire and the caller
    should fall back to per-pin labels.
    """
    from eda_agent.design.quality import _count_wire_crossings
    bus_segs = [(b.x1, b.y1, b.x2, b.y2) for b in canvas.buses]
    if not bus_segs:
        return 0
    wire_segs = [(w.x1, w.y1, w.x2, w.y2) for w in canvas.wires]
    return (_count_wire_crossings(wire_segs + bus_segs)
            - _count_wire_crossings(wire_segs))


def apply_bus_drawing(
    canvas,
    plan: DesignPlan,
    *,
    min_width: int = DEFAULT_MIN_WIDTH,
    gate_crossings: bool = True,
) -> list[str]:
    """Redraw detected buses as bus glyphs on an already-wired canvas.

    Post-pass: for each detected bus whose geometry draws cleanly at BOTH
    endpoint ICs (:func:`build_bus_geometry` returns geometry, i.e. >=2 of its
    pins sit on one side), drop the bus nets' per-pin labels and any wires and
    add the bus line + 45-degree entries + stubs + labels instead. A bus that
    can't be drawn cleanly is left exactly as the wiring produced it (no
    regression).

    ``gate_crossings`` (default on) reverts the WHOLE change if it raised the
    wire-crossing count -- a bus is a readability win, so it must never add
    crossings; if it would, the per-pin form is kept. Returns the bus net names
    actually drawn as buses (empty when nothing was drawn). Mutates ``canvas``
    in place.
    """
    buses = detect_buses(plan, min_width=min_width)
    if not buses:
        return []

    drawable: list[tuple[BusGroup, list[BusGeometry]]] = []
    for bus in buses:
        geoms: list[BusGeometry] = []
        ok = True
        for ic_ref in sorted(bus.endpoints):
            inst = canvas.instance_by_refdes(ic_ref)
            geo = (build_bus_geometry(bus, inst, plan, inst.sheet)
                   if inst is not None else None)
            if geo is None:
                ok = False
                break
            geoms.append(geo)
        if ok:
            drawable.append((bus, geoms))
    if not drawable:
        return []

    bus_nets = {n for bus, _ in drawable for n in bus.nets}
    saved = (list(canvas.wires), list(canvas.labels),
             list(canvas.buses), list(canvas.bus_entries))
    before = _wire_crossings(canvas, plan) if gate_crossings else 0

    canvas.wires[:] = [w for w in canvas.wires if w.net not in bus_nets]
    canvas.labels[:] = [l for l in canvas.labels if l.text not in bus_nets]
    for _bus, geoms in drawable:
        for geo in geoms:
            canvas.add_wires(geo.stubs)
            canvas.add_bus_entries(geo.entries)
            canvas.add_buses(geo.segments)
            canvas.add_labels(geo.labels)
            if geo.bus_label is not None:
                canvas.add_labels([geo.bus_label])

    if gate_crossings and (_wire_crossings(canvas, plan) > before
                           or _bus_segment_crossings(canvas) > 0):
        (canvas.wires[:], canvas.labels[:],
         canvas.buses[:], canvas.bus_entries[:]) = saved
        return []
    return sorted(bus_nets)
