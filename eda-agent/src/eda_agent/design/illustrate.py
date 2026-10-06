# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Render the abstract outputs of the design engines to PNG images.

These are developer/validation illustrations: they turn a
:class:`~eda_agent.design.schematic_layout.SchematicLayout` or a constructed
PCB placement into a picture so a human can see what the placer produced,
without going through Altium. Matplotlib (Agg backend) is imported lazily so
importing this module is cheap and headless-safe.
"""

from __future__ import annotations

from typing import Mapping, Optional, Sequence


_SCH_COLORS = {
    "wire": "#1565c0",
    "net_label": "#ef6c00",
    "power_port": "#c62828",
}


def schematic_png(layout, path: str, *, title: str = "") -> str:
    """Draw a schematic layout (symbol boxes, pins, wires, labels, ports).

    ``layout`` is a SchematicLayout. Returns the written path.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    fig, ax = plt.subplots(figsize=(9, 7))

    # Symbol bodies + designators. The stored bbox is the courtyard (body plus
    # pin stubs); draw the real body as an inset rectangle and the pins as
    # short stub lines to it, so the drawing reads like a schematic rather than
    # a wall of solid blocks.
    for refdes in sorted(layout.placed):
        sym = layout.placed[refdes]
        x1, y1, x2, y2 = sym.bbox
        bw, bh = abs(x2 - x1), abs(y2 - y1)
        # Body = bbox inset by ~28% per side (clamped to a sane minimum).
        ix = min(0.28 * bw, max(0.0, (bw - 150) / 2.0))
        iy = min(0.28 * bh, max(0.0, (bh - 150) / 2.0))
        bx0, by0 = min(x1, x2) + ix, min(y1, y2) + iy
        body_w, body_h = max(60.0, bw - 2 * ix), max(60.0, bh - 2 * iy)
        ax.add_patch(Rectangle(
            (bx0, by0), body_w, body_h,
            fill=True, facecolor="#eceff1", edgecolor="#37474f", linewidth=1.2,
            zorder=2,
        ))
        # Pin stubs from each pin to the nearest body edge.
        bx1, by1 = bx0 + body_w, by0 + body_h
        for (px, py) in sym.pins.values():
            tx = min(max(px, bx0), bx1)
            ty = min(max(py, by0), by1)
            ax.plot([px, tx], [py, ty], color="#607d8b", linewidth=0.8,
                    zorder=1)
            ax.plot([px], [py], marker="o", markersize=2.5, color="#455a64",
                    zorder=3)
        ax.text(sym.x_mils, sym.y_mils, refdes, ha="center", va="center",
                fontsize=8, color="#263238", weight="bold", zorder=4)

    # Wire routes.
    for net_name in sorted(layout.routes):
        for route in layout.routes[net_name]:
            for s in route.segments:
                ax.plot([s.x1, s.x2], [s.y1, s.y2],
                        color=_SCH_COLORS["wire"], linewidth=1.4)

    # Net labels / power ports at each pin endpoint of the net.
    for net_name in sorted(layout.decisions):
        dec = layout.decisions[net_name]
        if dec.kind not in ("net_label", "power_port"):
            continue
        color = _SCH_COLORS[dec.kind]
        for (refdes, pin) in layout.route_membership.get(net_name, []):
            sym = layout.placed.get(refdes)
            if sym is None or pin not in sym.pins:
                continue
            px, py = sym.pins[pin]
            marker = "v" if dec.kind == "power_port" else "s"
            ax.plot([px], [py], marker=marker, markersize=5, color=color)
            ax.text(px, py + 30, net_name, ha="center", va="bottom",
                    fontsize=6, color=color)

    sc = layout.score
    sub = (f"crossings={sc.wire_crossings}  "
           f"score={sc.total:.0f}  "
           f"nets: wire/label/port = "
           f"{sum(1 for d in layout.decisions.values() if d.kind == 'wire')}/"
           f"{sum(1 for d in layout.decisions.values() if d.kind == 'net_label')}/"
           f"{sum(1 for d in layout.decisions.values() if d.kind == 'power_port')}")
    ax.set_title((title + "\n" if title else "") + sub, fontsize=9)
    ax.set_aspect("equal")
    ax.margins(0.08)
    ax.grid(True, linewidth=0.3, color="#cfd8dc")
    ax.set_xlabel("mils")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def placement_png(
    comps: Sequence,
    positions: Mapping[str, Sequence[float]],
    region,
    nets: Sequence,
    path: str,
    *,
    title: str = "",
    rotations: Optional[Mapping[str, float]] = None,
    sides: Optional[Mapping[str, int]] = None,
    report=None,
) -> str:
    """Draw a PCB placement (board outline, courtyards, net stars).

    ``comps`` are PlaceComp; ``positions`` maps ref -> (x, y) centroid;
    ``region`` is a BoardRegion; ``nets`` are PlaceNet. Returns the path.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    rotations = rotations or {}
    sides = sides or {}
    by_ref = {c.ref: c for c in comps}

    fig, ax = plt.subplots(figsize=(8, 8))

    # Board outline.
    bx = min(region.x1, region.x2)
    by = min(region.y1, region.y2)
    ax.add_patch(Rectangle((bx, by), region.width, region.height,
                           fill=False, edgecolor="#1b5e20", linewidth=1.6))

    # Net stars: connect each net's member centroids to the net centroid.
    for net in nets:
        pts = [positions[r] for r in dict.fromkeys(net.refs) if r in positions]
        if len(pts) < 2:
            continue
        ncx = sum(p[0] for p in pts) / len(pts)
        ncy = sum(p[1] for p in pts) / len(pts)
        for (x, y) in pts:
            ax.plot([ncx, x], [ncy, y], color="#90a4ae", linewidth=0.5,
                    zorder=1)

    # Component courtyards (effective w/h at the chosen rotation).
    for ref in sorted(positions):
        c = by_ref.get(ref)
        if c is None:
            continue
        x, y = positions[ref][0], positions[ref][1]
        rot = float(rotations.get(ref, getattr(c, "rotation", 0.0)))
        w, h = (c.h, c.w) if int(round(rot)) % 180 == 90 else (c.w, c.h)
        side = int(sides.get(ref, 1))
        face = "#e3f2fd" if side >= 0 else "#fff3e0"
        ax.add_patch(Rectangle((x - w / 2.0, y - h / 2.0), w, h, fill=True,
                               facecolor=face, edgecolor="#0d47a1",
                               linewidth=1.1, zorder=2))
        ax.text(x, y, ref, ha="center", va="center", fontsize=8,
                color="#0d47a1", weight="bold", zorder=3)

    sub = ""
    if report is not None:
        sub = (f"HPWL={report.hpwl:.0f}  via={report.via:.2f}  "
               f"cong={report.cong:.1f}  legal={report.legal}  "
               f"util={report.utilization:.2f}")
    ax.set_title((title + "\n" if title else "") + sub, fontsize=9)
    ax.set_aspect("equal")
    ax.margins(0.08)
    ax.grid(True, linewidth=0.3, color="#cfd8dc")
    ax.set_xlabel("mils")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def canvas_png(canvas, path: str, *, sheet: Optional[str] = None,
               title: str = "") -> str:
    """Draw a wired :class:`~eda_agent.design.canvas.SchematicCanvas` to PNG.

    This renders the REAL schematic-pipeline output (symbol bodies + pins,
    wires, buses + bus entries, net labels, power-port glyphs, junction dots)
    so a developer can run the render-and-look loop on
    ``build_canvas_from_plan`` / ``build_best_canvas_from_plan`` directly,
    instead of the abstract SchematicLayout that :func:`schematic_png` draws.
    Pass ``sheet`` to pick a sheet (defaults to the first instance's sheet).
    Returns the written path.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    if sheet is None:
        sheet = canvas.instances[0].sheet if canvas.instances else "main"
    insts = canvas.instances_on(sheet)
    wires = canvas.wires_on(sheet)
    labels = canvas.labels_on(sheet)
    ports = canvas.power_ports_on(sheet)
    junctions = canvas.junctions_on(sheet)
    buses = canvas.buses_on(sheet)
    bus_entries = canvas.bus_entries_on(sheet)

    fig, ax = plt.subplots(figsize=(14, 10))

    # Wires (and buses) under the bodies.
    for w in wires:
        ax.plot([w.x1, w.x2], [w.y1, w.y2], color="#1565c0", linewidth=1.0,
                zorder=1)
    for b in buses:
        ax.plot([b.x1, b.x2], [b.y1, b.y2], color="#0033aa", linewidth=3.0,
                zorder=1)
    for e in bus_entries:
        ax.plot([e.x1, e.x2], [e.y1, e.y2], color="#6a1b9a", linewidth=1.5,
                zorder=2)

    # Symbol bodies + pin dots + designator.
    for inst in insts:
        bb = inst.world_bbox()
        ax.add_patch(Rectangle(
            (bb.x_min, bb.y_min), bb.x_max - bb.x_min, bb.y_max - bb.y_min,
            fill=True, facecolor="#eceff1", edgecolor="#37474f",
            linewidth=1.0, zorder=3))
        for pin in inst.symbol.pins:
            pw = inst.pin_world(pin.designator)
            if pw is not None:
                ax.plot([pw.x], [pw.y], marker="o", markersize=2.0,
                        color="#455a64", zorder=4)
        # Designator ABOVE the body (schematic convention). Centering it on
        # the box smears the bold text across tiny 2-pin passive outlines and
        # reads as doubled; a small gap above keeps it legible at any box size.
        ax.text((bb.x_min + bb.x_max) / 2.0, bb.y_max + 25,
                inst.refdes, ha="center", va="bottom", fontsize=8,
                color="#263238", weight="bold", zorder=5)

    for lab in labels:
        ax.text(lab.x, lab.y, lab.text, fontsize=6, color="#ef6c00",
                ha="center", va="bottom", zorder=6)
    for p in ports:
        marker = "^" if "gnd" in p.style else "v"
        ax.plot([p.x], [p.y], marker=marker, markersize=7, color="#c62828",
                zorder=6)
        ax.text(p.x, p.y, p.text, fontsize=5, color="#c62828", ha="center",
                va="top", zorder=6)
    for j in junctions:
        ax.plot([j.x], [j.y], marker="o", markersize=5, color="#000000",
                zorder=7)

    sub = (f"{len(insts)} parts  {len(wires)} wires  {len(labels)} labels  "
           f"{len(ports)} ports  {len(junctions)} junctions")
    ax.set_title((title + "\n" if title else "") + sub, fontsize=9)
    ax.set_aspect("equal")
    ax.margins(0.05)
    ax.grid(True, linewidth=0.2, color="#eceff1")
    ax.set_xlabel("mils")
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)
    return path
