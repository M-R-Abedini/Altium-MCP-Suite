# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Geometric netlist solver for the headless .SchDoc reader (roadmap V1).

Altium does not store the compiled netlist in the ``.SchDoc``; it stores
geometry (pins, wires, power ports, junctions) and derives connectivity at
compile time. To review connectivity offline: floating pins, single-pin
nets, pin-to-pin shorts: we must reconstruct that netlist from the geometry.

The model, validated against a live-Altium compiled netlist:

    A net is a set of **points of interest** (POIs) joined transitively.
    POIs are: every wire endpoint, every pin's electrical end, every power
    port location, every net-label location, every junction location.

    Two POIs join when one lies on a wire segment that the other's wire
    shares: concretely, every POI that lies on a wire's span is unioned
    with that wire's two endpoints. Wire endpoints of the same segment are
    unioned directly.

The discipline that keeps this from over-merging (the failure mode of an
earlier attempt): connectivity is asserted **only at POIs**, never at a bare
geometric crossing of two wires. A four-way crossing with no junction dot has
no POI at the crossing, so the two wires stay separate: exactly Altium's
rule. Dropping a junction (RECORD=29) at the crossing adds a POI there, which
unions them. T-junctions (a wire endpoint on another wire's span) and pin
taps (a pin end on a span) connect automatically because the endpoint / pin
end is itself a POI.

Net names: a net carrying a power port or net label takes that name; an
otherwise-unnamed net is auto-named ``Net<D>_<p>`` after the pin ``p`` of its
alphabetically-first component ``D``: Altium's default auto-name form.

Coordinates are exact integers (raw SchDoc units), so coincidence and
on-segment tests are exact, no epsilon.

Validated envelope: wire + power port + junction + net-label (by-name)
connectivity, against a live-Altium compiled netlist (Blinker, 24/24) and
against the design plan through the offline pipeline (buck, label-heavy,
7/7 nets).

Buses need no separate handling here: an Altium bus is a VISUAL grouping:
its connectivity is carried by the per-pin net labels drawn at each bus
entry, which the by-name rule already resolves. A bus-borne net connects iff
its labels bind, exactly like any other label. What this solver cannot
invent is connectivity a given emit never drew: if a net is left floating
(e.g. a signal-net label placed off its pin's wire: a known pipeline
label-fallback case), the solver faithfully reports it disconnected. That is
the intended ERC behavior, not a solver gap. Cross-sheet connectors (ports
spanning sheets) are the one mechanism still outside scope.
"""

from __future__ import annotations

from typing import Any, Optional

# Orientation (degrees) -> unit direction a pin extends from its anchor.
_ORIENT_DIR = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}


def pin_electrical_end(x: int, y: int, length: int, orientation: int) -> tuple:
    """The wire-connection end of a pin: anchor + length along orientation.

    Verified against live Altium: the pin record's ``Location`` is the body
    anchor; the electrical end (where a wire lands) is this point plus the
    pin length in the orientation direction.
    """
    dx, dy = _ORIENT_DIR.get(orientation, (1, 0))
    return (x + dx * length, y + dy * length)


class _UnionFind:
    def __init__(self):
        self._parent: dict[Any, Any] = {}

    def add(self, k):
        self._parent.setdefault(k, k)

    def find(self, k):
        self.add(k)
        root = k
        while self._parent[root] != root:
            root = self._parent[root]
        # path-compress
        while self._parent[k] != root:
            self._parent[k], k = root, self._parent[k]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def _on_segment(px, py, x1, y1, x2, y2) -> bool:
    """True if (px,py) lies on the segment (x1,y1)-(x2,y2), endpoints incl."""
    # Collinear: cross product of (seg vector) x (point - p1) is zero.
    if (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1) != 0:
        return False
    return (min(x1, x2) <= px <= max(x1, x2)
            and min(y1, y2) <= py <= max(y1, y2))


def solve_nets(
    pins: list[dict[str, Any]],
    wires: list[dict[str, Any]],
    power_ports: Optional[list[dict[str, Any]]] = None,
    junctions: Optional[list[dict[str, Any]]] = None,
    net_labels: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Reconstruct the netlist from schematic geometry.

    Args:
        pins: ``[{component, pin, x, y}]`` where ``(x, y)`` is the pin's
            ELECTRICAL END (see :func:`pin_electrical_end`).
        wires: ``[{x1, y1, x2, y2}]`` straight segments.
        power_ports: ``[{x, y, name}]``, bind and name (GND, VCC, ...).
        junctions: ``[{x, y}]``, force a connection at a wire crossing.
        net_labels: ``[{x, y, name}]``, name a net.

    Returns ``{nets: {name: [{component, pin}, ...]}, pin_nets:
    {"comp.pin": name}}``.
    """
    power_ports = power_ports or []
    junctions = junctions or []
    net_labels = net_labels or []

    uf = _UnionFind()

    # Every POI is a coordinate node; identical coordinates share a node, so
    # coincident POIs are already unioned by construction.
    def poi(x, y):
        node = (x, y)
        uf.add(node)
        return node

    # 1. Wire endpoints of one segment are directly connected.
    for w in wires:
        a = poi(w["x1"], w["y1"])
        b = poi(w["x2"], w["y2"])
        uf.union(a, b)

    # 2. Gather all POIs that must be tested against wire spans: pin ends,
    #    port/junction/label locations, and the wire endpoints themselves
    #    (a wire endpoint lying on another wire's span is a T-junction).
    span_pois: list[tuple] = []
    for w in wires:
        span_pois.append(poi(w["x1"], w["y1"]))
        span_pois.append(poi(w["x2"], w["y2"]))
    for p in pins:
        span_pois.append(poi(p["x"], p["y"]))
    for pp in power_ports:
        span_pois.append(poi(pp["x"], pp["y"]))
    for j in junctions:
        span_pois.append(poi(j["x"], j["y"]))
    for nl in net_labels:
        span_pois.append(poi(nl["x"], nl["y"]))

    # 3. Any POI lying on a wire's span joins that wire (both endpoints).
    #    This is the ONLY connection rule beyond same-segment endpoints, and
    #    it fires only at POIs, so bare mid-span crossings never merge.
    for (px, py) in span_pois:
        for w in wires:
            if _on_segment(px, py, w["x1"], w["y1"], w["x2"], w["y2"]):
                uf.union((px, py), poi(w["x1"], w["y1"]))

    # 4a. Declared names (power ports + net labels), each at its own point.
    declared: list[tuple] = []  # (point, name)
    for src in (power_ports, net_labels):
        for item in src:
            name = (item.get("name") or "").strip()
            if name:
                declared.append(((item["x"], item["y"]), name))

    # 4b. Short detection BEFORE by-name merging: two DIFFERENT declared names
    #     landing on one GEOMETRIC net means those named nets are shorted.
    geom_names: dict[Any, set] = {}
    for (pt, name) in declared:
        geom_names.setdefault(uf.find(pt), set()).add(name)
    name_conflicts = [
        {"net": sorted(names)[0], "names": sorted(names)}
        for names in geom_names.values() if len(names) > 1
    ]

    # 4c. By-name connectivity (Altium's rule): a net label or power port ties
    #     together everything sharing its name, sheet-wide, with no wire
    #     between them. Merge every declared point of the same name.
    first_of_name: dict[str, tuple] = {}
    for (pt, name) in declared:
        if name in first_of_name:
            uf.union(pt, first_of_name[name])
        else:
            first_of_name[name] = pt

    # 4d. Final name per (post-merge) root.
    tags: dict[Any, str] = {}
    for (pt, name) in declared:
        tags.setdefault(uf.find(pt), name)

    # 5. Group pins by net root.
    root_pins: dict[Any, list[dict]] = {}
    for p in pins:
        root = uf.find((p["x"], p["y"]))
        root_pins.setdefault(root, []).append(
            {"component": p["component"], "pin": str(p["pin"])})

    # 6. Assign each net a name: a tag, else Altium's auto-name after the
    #    alphabetically-first component's pin on that net.
    nets: dict[str, list[dict]] = {}
    pin_nets: dict[str, str] = {}
    for root, members in root_pins.items():
        name = tags.get(root)
        if not name:
            first = min(members, key=lambda m: (m["component"], _pin_key(m["pin"])))
            name = f"Net{first['component']}_{first['pin']}"
        nets.setdefault(name, [])
        for m in members:
            nets[name].append(m)
            pin_nets[f"{m['component']}.{m['pin']}"] = name

    for members in nets.values():
        members.sort(key=lambda m: (m["component"], _pin_key(m["pin"])))

    return {"nets": nets, "pin_nets": pin_nets, "name_conflicts": name_conflicts}


def _pin_key(pin: str):
    """Sort pin designators numerically when numeric, else lexically."""
    s = str(pin)
    return (0, int(s)) if s.isdigit() else (1, s)


def solve_schematic_nets(path) -> dict[str, Any]:
    """Read a ``.SchDoc`` and reconstruct its netlist geometrically.

    Ties the file readers to :func:`solve_nets`: maps each pin to its owning
    component (OwnerIndex → designator), computes its electrical end, and
    feeds wires, power ports, junctions, and net labels to the solver.

    Returns the :func:`solve_nets` result: ``{nets, pin_nets}``. Fully
    offline; validated to reproduce a live-Altium compiled netlist exactly
    (grouping and names) on a 555 astable.
    """
    from .altium_sch import (
        RECORD_DESIGNATOR,
        _to_int,
        read_schdoc_records,
        read_schematic_junctions,
        read_schematic_net_label_locations,
        read_schematic_pins,
        read_schematic_power_ports,
        read_schematic_wires,
    )

    records = read_schdoc_records(path)
    designator_by_owner: dict[int, str] = {}
    for rec in records:
        if rec.get("RECORD") == RECORD_DESIGNATOR:
            oi = _to_int(rec.get("OwnerIndex"))
            text = rec.get("Text")
            if oi is not None and text:
                designator_by_owner[oi] = text

    pins: list[dict[str, Any]] = []
    for pin in read_schematic_pins(path):
        component = designator_by_owner.get(pin.get("owner_index"))
        if component is None or pin.get("x") is None or pin.get("y") is None:
            continue
        end = pin_electrical_end(pin["x"], pin["y"], pin["length"],
                                 pin["orientation"])
        pins.append({"component": component, "pin": pin["designator"],
                     "x": end[0], "y": end[1]})

    return solve_nets(
        pins,
        read_schematic_wires(path),
        read_schematic_power_ports(path),
        read_schematic_junctions(path),
        read_schematic_net_label_locations(path),
    )
