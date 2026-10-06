# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Power/ground port placement: prefer a straight per-pin stub, fall back to an L.

A power/ground pin is connected to a rail glyph (a local VCC/GND symbol) by a
short stub. The canonical schematic look is a STRAIGHT stub -- the glyph sits
directly above (power) or below (ground) the pin, so the connection is one
vertical line with zero bends. The earlier cluster-port approach placed one
glyph at a cluster centroid and L-routed every pin to it, which (measured)
dominated both the wire length AND the bend count of a sheet -- the single
largest source of schematic clutter, while the signal nets routed near-cleanly.

This module decides, per pin, whether a straight stub is geometrically safe
(its vertical column is clear of foreign-net pins, foreign wires, component
bodies, AND stubs already planned in this pass) and, if not, falls back to a
short L to a shifted column -- the same correctness the cluster logic had, but
straight wherever possible. It is a PURE geometry function with no dependency
on the canvas or the Altium executor, so BOTH the preview pipeline and the
executor apply-path can call it and cannot diverge.

Coordinates are mils on the schematic grid; +y is up, so a power glyph is at
``pin_y + stub`` and a ground glyph at ``pin_y - stub``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StubPlan:
    """One pin's planned rail connection."""

    pin: tuple[int, int]                       # the pin endpoint
    port: tuple[int, int]                      # the rail glyph position
    segments: tuple[tuple[int, int, int, int], ...]  # wire segments pin->port
    straight: bool                             # True = single straight stub


def _vseg_clear(
    px: int, ylo: int, yhi: int,
    pins: list[tuple[int, int]],
    segs: list[tuple[int, int, int, int]],
    bodies: list[tuple[int, int, int, int]],
) -> bool:
    """Is the vertical segment x=px, (ylo..yhi) clear of every obstacle?

    A foreign pin ON the column between the ends shorts; a foreign horizontal
    wire spanning the column crosses; a foreign vertical wire overlapping the
    span crosses; a body the column passes through is illegible. Endpoints are
    exclusive (touching at a shared endpoint is a legal junction, not a cross).
    """
    for (ox, oy) in pins:
        if ox == px and ylo < oy < yhi:
            return False
    for (x1, y1, x2, y2) in segs:
        if y1 == y2 and ylo < y1 < yhi and min(x1, x2) < px < max(x1, x2):
            return False
        if x1 == x2 == px and not (yhi <= min(y1, y2) or ylo >= max(y1, y2)):
            return False
    for (bx1, by1, bx2, by2) in bodies:
        if bx1 < px < bx2 and not (yhi <= by1 or ylo >= by2):
            return False
    return True


def plan_rail_stubs(
    pins: list[tuple[int, int]],
    *,
    is_ground: bool,
    foreign_pins: list[tuple[int, int]],
    foreign_segments: list[tuple[int, int, int, int]],
    bodies: list[tuple[int, int, int, int]],
    stub_mils: int = 300,
    grid_mils: int = 100,
    max_shift_mils: int = 1500,
) -> list[StubPlan]:
    """Plan a rail connection for each pin of ONE power/ground net.

    For each pin, try a straight vertical stub to a glyph ``stub_mils`` away
    (below for ground, above for power); accept it when its column is clear of
    ``foreign_pins`` / ``foreign_segments`` / ``bodies`` AND of every stub
    already planned in this call (the accumulation prevents two pins' straight
    stubs from crossing). Otherwise fall back to a short L: shift the glyph to
    the nearest clear grid column and route pin -> (shifted_x, pin_y) ->
    glyph. Returns one :class:`StubPlan` per pin, in input order.

    Pure geometry: the caller supplies pin endpoints and obstacle geometry and
    converts the returned segments/ports into canvas or Altium objects. Sharing
    this function keeps the preview and the executor identical by construction.
    """
    dy = -stub_mils if is_ground else stub_mils
    own = [p for p in pins]
    own_set = set(own)
    # Foreign pins for THIS net exclude its own pins (a pin can't short itself).
    base_pins = [p for p in foreign_pins if p not in own_set]
    placed: list[tuple[int, int, int, int]] = list(foreign_segments)
    plans: list[StubPlan] = []
    for (px, py) in pins:
        ylo, yhi = (py + dy, py) if dy < 0 else (py, py + dy)
        # other own-net pins are not obstacles (same net), but other nets' are
        if _vseg_clear(px, ylo, yhi, base_pins, placed, bodies):
            seg = (px, py, px, py + dy)
            plans.append(StubPlan(pin=(px, py), port=(px, py + dy),
                                  segments=(seg,), straight=True))
            placed.append(seg)
            continue
        # L-fallback: find the nearest clear column for the vertical leg.
        forbidden = {ox for (ox, oy) in base_pins}
        cx = px
        for d in range(grid_mils, max_shift_mils + grid_mils, grid_mils):
            for cand in (px + d, px - d):
                if cand in forbidden:
                    continue
                clo, chi = (py + dy, py) if dy < 0 else (py, py + dy)
                if _vseg_clear(cand, clo, chi, base_pins, placed, bodies):
                    cx = cand
                    break
            if cx != px:
                break
        h = (px, py, cx, py)
        v = (cx, py, cx, py + dy)
        plans.append(StubPlan(pin=(px, py), port=(cx, py + dy),
                              segments=(h, v), straight=False))
        placed.extend([h, v])
    return plans


__all__ = ["StubPlan", "plan_rail_stubs"]
