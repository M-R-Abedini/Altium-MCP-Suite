# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Collision-free placement of designator/value text for placed symbols.

Professional sheets never let a component's annotation collide with a
wire, another body, a power-port glyph, or another annotation ("no
component label or value obscuring any wire" is a standing rule in every
published style guide). Libraries store text at a fixed offset from the
symbol origin, so on a dense sheet the defaults collide constantly --
this pass picks, per instance, the least-loaded side of the body for the
designator+value block and records the chosen anchor on the instance.

The pass is deterministic (sorted refdes order; earlier blocks become
obstacles for later ones), pure geometry, and runs AFTER wiring so the
full obstacle picture exists. It never moves symbols or wires, so it
cannot affect connectivity or the layout score -- safe to run on every
build. The SVG renderer honours the stored anchors; the emitter mirrors
them onto ``ISch_Component.Designator/Comment.Location`` so the live
sheet matches the offline render.

Coordinates are canvas mils, y-up. A text block anchor is the LOWER-LEFT
corner of the designator line; the value line sits one line height below
the designator.
"""

from __future__ import annotations

from typing import Iterable, Optional

from eda_agent.design.canvas import SchematicCanvas

# Rough Altium default-font metrics, mils. Wide enough to be safe for
# collision purposes without measuring real glyphs.
CHAR_W = 55
LINE_H = 110
GAP = 60  # clearance between body edge and text block

# Obstacle weights: colliding with text is worst (unreadable), then
# bodies/glyphs, then wires. Each class also carries a FLAT trip cost on
# ANY overlap -- side preference is worth at most ~24 points, and a wire
# slicing through a text line is far worse than a non-preferred side, so
# the flat cost must dominate the bonus spread (measured: a thin
# 20-mil-wide wire rect through a 500-mil block scored only ~10 area
# points and the preferred side won anyway, drawing the wire through
# the connector's value text).
_W_TEXT, _F_TEXT = 4.0, 40.0
_W_BODY, _F_BODY = 3.0, 30.0
_W_PORT, _F_PORT = 2.0, 25.0
_W_WIRE, _F_WIRE = 3.0, 25.0
# Side preference when scores tie: right of body reads best for 2-pin
# parts, then above, then below, then left. ICs prefer ABOVE the body
# (their left/right edges are pin+wire territory by construction).
_SIDE_BONUS = {"E": 0.0, "N": 8.0, "S": 16.0, "W": 24.0}
_SIDE_BONUS_IC = {"N": 0.0, "S": 8.0, "E": 16.0, "W": 24.0}


def _overlap(a: tuple, b: tuple) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    w = min(ax2, bx2) - max(ax1, bx1)
    h = min(ay2, by2) - max(ay1, by1)
    if w <= 0 or h <= 0:
        return 0.0
    return float(w) * float(h)


def _block_dims(refdes: str, value: str) -> tuple[int, int]:
    lines = [refdes] + ([value] if value else [])
    w = max(CHAR_W * len(t) for t in lines)
    h = LINE_H * len(lines)
    return (w, h)


def place_instance_text(canvas: SchematicCanvas) -> int:
    """Assign ``designator_pos`` / ``value_pos`` on every instance.

    Returns the number of instances whose text was moved off the default
    side (informational).
    """
    moved = 0
    for sheet in canvas.sheets:
        moved += _place_sheet(canvas, sheet.name)
    return moved


def _place_sheet(canvas: SchematicCanvas, sheet_name: str) -> int:
    instances = canvas.instances_on(sheet_name)
    if not instances:
        return 0

    bodies = []
    for inst in instances:
        bb = inst.world_bbox()
        bodies.append((bb.x_min, bb.y_min, bb.x_max, bb.y_max))

    wires = [
        (min(w.x1, w.x2) - 10, min(w.y1, w.y2) - 10,
         max(w.x1, w.x2) + 10, max(w.y1, w.y2) + 10)
        for w in canvas.wires if w.sheet == sheet_name
    ]
    ports = []
    for p in canvas.power_ports:
        if p.sheet != sheet_name:
            continue
        # Glyph plus its text line. The NAME is drawn beyond the glyph
        # (below a ground symbol, above a rail bar) and is often wider
        # than the glyph itself -- measured on the buck output corner,
        # where VOUT/GND port names overlapped J2's and R2's values
        # because only the glyph rectangle was an obstacle.
        half_w = max(150, (CHAR_W * len(p.text)) // 2 + 25)
        ports.append((p.x - half_w, p.y - 350, p.x + half_w, p.y + 350))
    texts = []
    for l in canvas.labels:
        if l.sheet != sheet_name:
            continue
        w = CHAR_W * len(l.text)
        if getattr(l, "justification", 0) == 2:
            texts.append((l.x - w, l.y, l.x, l.y + LINE_H))
        else:
            texts.append((l.x, l.y, l.x + w, l.y + LINE_H))

    moved = 0
    for inst in sorted(instances, key=lambda i: i.refdes):
        bb = inst.world_bbox()
        w, h = _block_dims(inst.refdes, inst.value or "")
        cy = (bb.y_min + bb.y_max) // 2
        candidates = {
            "E": (bb.x_max + GAP, cy),
            "W": (bb.x_min - GAP - w, cy),
            "N": (bb.x_min, bb.y_max + GAP),
            "S": (bb.x_min, bb.y_min - GAP - h),
        }
        n_pins = len(list(inst.all_pin_endpoints()))
        bonus = _SIDE_BONUS_IC if n_pins >= 3 else _SIDE_BONUS
        best_side = "E"
        best_cost: Optional[float] = None
        for side, (bx, by) in candidates.items():
            rect = (bx, by, bx + w, by + h)
            cost = bonus[side]
            for ob in texts:
                a = _overlap(rect, ob)
                if a > 0:
                    cost += _F_TEXT + _W_TEXT * a / 1000.0
            for ob in bodies:
                if ob == (bb.x_min, bb.y_min, bb.x_max, bb.y_max):
                    continue  # own body: candidates are outside it anyway
                a = _overlap(rect, ob)
                if a > 0:
                    cost += _F_BODY + _W_BODY * a / 1000.0
            for ob in ports:
                a = _overlap(rect, ob)
                if a > 0:
                    cost += _F_PORT + _W_PORT * a / 1000.0
            for ob in wires:
                a = _overlap(rect, ob)
                if a > 0:
                    cost += _F_WIRE + _W_WIRE * a / 1000.0
            if best_cost is None or cost < best_cost - 1e-9:
                best_cost = cost
                best_side = side

        bx, by = candidates[best_side]
        # Designator on the TOP line of the block, value below it.
        top_line_y = by + h - LINE_H
        inst.designator_pos = (int(bx), int(top_line_y))
        inst.value_pos = (
            (int(bx), int(top_line_y - LINE_H)) if inst.value else None
        )
        if best_side != "E":
            moved += 1
        # The placed block is an obstacle for every later instance.
        texts.append((bx, by, bx + w, by + h))
    return moved


__all__ = ["place_instance_text", "CHAR_W", "LINE_H"]
