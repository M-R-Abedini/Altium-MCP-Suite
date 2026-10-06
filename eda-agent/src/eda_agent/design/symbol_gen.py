# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Parametric IC schematic-symbol geometry (pure Python, offline).

The schematic-side analog of :mod:`footprint_gen`. Given pins already grouped
onto the LEFT and RIGHT sides (the functional grouping a human/LLM decides per
the design discipline -- inputs left, outputs right), this lays them out into a
clean, grid-aligned symbol: a body rectangle plus every pin's exact
``Location`` / ``Orientation`` / wire-connection. A tool then emits it via
``lib_create_symbol`` + ``lib_add_pins`` (bulk) + ``lib_add_symbol_rectangle``.

It follows the discipline's symbol rules exactly:

* Pins go ONLY on the left and right (never top/bottom).
* Origin: the TOP-LEFTMOST pin's WIRE-CONNECTION point is at (0, 0). With the
  standard 200-mil pin length and a left pin pointing left (orientation 2), its
  Location is (200, 0) so the wire snaps at (0, 0); the top-right pin's Location
  is (body_width, 0), wire at (body_width + 200, 0).
* Everything on the 100-mil grid; pins step DOWN by 100 mils.

The caller supplies the side groupings; this is purely the geometry.
"""

from __future__ import annotations

from dataclasses import dataclass

PIN_LENGTH = 200
PIN_PITCH = 100
GRID = 100
_CHAR_W = 70          # ~mils per pin-name character, for inner-width sizing
_NAME_GAP = 300       # clear gap between the two name columns
_MIN_INNER = 600      # minimum inner body width (SOIC-8-ish)


@dataclass(frozen=True)
class SymbolGeometry:
    """Computed IC symbol: pins + body rectangle, ready to emit."""

    pins: tuple[dict, ...]        # {designator,name,x,y,length,rotation,
                                  #  electrical_type}
    body: dict                    # {x1,y1,x2,y2}
    width_mils: int
    height_mils: int


def _snap(v: float) -> int:
    return int(round(v / GRID) * GRID)


def generate_ic_symbol(
    left_pins: list[dict],
    right_pins: list[dict],
    *,
    min_inner_width: int = _MIN_INNER,
) -> SymbolGeometry:
    """Lay out an IC symbol from left/right pin groups.

    Each pin dict needs ``designator`` and ``name``; ``electrical_type``
    (default "passive") is passed through. Left pins are placed top->down
    starting at the origin row; right pins likewise on the far side. The
    body width is sized to clear the pin-name text on both sides.

    Returns a :class:`SymbolGeometry`. Raises if both sides are empty.
    """
    if not left_pins and not right_pins:
        raise ValueError("need at least one pin on a side")

    # Every pin needs a designator, and designators must be unique across
    # the whole symbol -- a missing or duplicated pin number makes an
    # invalid component that ERC and the netlist would mis-resolve.
    designators: list[str] = []
    for p in (*left_pins, *right_pins):
        d = str(p.get("designator", "")).strip()
        if not d:
            raise ValueError(
                f"every pin needs a non-empty 'designator' (offending pin: "
                f"{p!r})")
        designators.append(d)
    dupes = sorted({d for d in designators if designators.count(d) > 1})
    if dupes:
        raise ValueError(f"duplicate pin designator(s): {dupes}")

    max_l_name = max((len(str(p.get("name", ""))) for p in left_pins),
                     default=0)
    max_r_name = max((len(str(p.get("name", ""))) for p in right_pins),
                     default=0)
    inner = (max_l_name + max_r_name) * _CHAR_W + _NAME_GAP
    inner = max(min_inner_width, _snap(inner))
    body_x_left = PIN_LENGTH                 # left pin bodies sit here
    body_x_right = PIN_LENGTH + inner        # right pin bodies sit here

    pins: list[dict] = []
    # Left column: pin points LEFT (rotation 180 -> orientation 2), so the
    # wire-end = Location.X - length lands at x=0 for the top-left pin.
    for i, p in enumerate(left_pins):
        y = -i * PIN_PITCH
        pins.append({
            "designator": str(p.get("designator", "")),
            "name": str(p.get("name", "")),
            "x": body_x_left, "y": y, "length": PIN_LENGTH, "rotation": 180,
            "electrical_type": p.get("electrical_type", "passive"),
        })
    # Right column: pin points RIGHT (rotation 0 -> orientation 0), wire-end =
    # Location.X + length.
    for j, p in enumerate(right_pins):
        y = -j * PIN_PITCH
        pins.append({
            "designator": str(p.get("designator", "")),
            "name": str(p.get("name", "")),
            "x": body_x_right, "y": y, "length": PIN_LENGTH, "rotation": 0,
            "electrical_type": p.get("electrical_type", "passive"),
        })

    rows = max(len(left_pins), len(right_pins))
    body_bottom = -(rows - 1) * PIN_PITCH - PIN_PITCH   # one row of margin
    body = {
        "x1": body_x_left, "y1": PIN_PITCH,            # top edge above row 0
        "x2": body_x_right, "y2": body_bottom,
    }
    width = body_x_right + PIN_LENGTH                  # incl. right pin stub
    height = PIN_PITCH - body_bottom
    return SymbolGeometry(pins=tuple(pins), body=body,
                          width_mils=width, height_mils=height)


@dataclass(frozen=True)
class PassiveSymbol:
    """A 2-pin passive symbol: pins + glyph primitives, ready to emit.

    Horizontal layout, pin 1 wire-end at the origin (0,0) pointing left,
    pin 2 wire-end at (400,0) pointing right, body in x in [100, 300]."""

    pins: tuple[dict, ...]
    lines: tuple[dict, ...]        # {x1,y1,x2,y2}
    rectangles: tuple[dict, ...]   # {x1,y1,x2,y2}
    polygons: tuple[dict, ...]     # {points: [(x,y),...]}


# Standard 2-pin horizontal frame (rule-14 origin at pin-1 wire-end).
_BODY_L = 100
_BODY_R = 300


def _two_pins(et1="passive", et2="passive"):
    return (
        {"designator": "1", "name": "1", "x": _BODY_L, "y": 0,
         "length": _BODY_L, "rotation": 180, "electrical_type": et1},
        {"designator": "2", "name": "2", "x": _BODY_R, "y": 0,
         "length": _BODY_L, "rotation": 0, "electrical_type": et2},
    )


def generate_passive_symbol(kind: str) -> PassiveSymbol:
    """Geometry for a standard 2-pin passive glyph.

    ``kind``: "resistor"/"r" | "capacitor"/"c" |
    "polarized_capacitor"/"cap_pol" | "inductor"/"l" | "diode"/"d" |
    "led" | "crystal"/"xtal" | "fuse"/"f". Uses the IEC rectangle for R/L,
    two plates for C (plus a '+' marker for the polarized variant), a
    triangle+bar for D, the diode plus emission arrows for the LED, two
    plates around the resonator for a crystal, and a rectangle with a
    through-lead for a fuse. The LED, crystal, and polarized-cap glyphs
    carry sub-100-mil detail, so emit their lines/polygons with
    ``grid<=25``. Emit via ``lib_add_pins`` +
    ``lib_add_symbol_rectangle``/``lib_add_symbol_lines``/
    ``lib_add_symbol_polygon``.
    """
    k = kind.strip().lower()
    pins = _two_pins()
    lines: list[dict] = []
    rects: list[dict] = []
    polys: list[dict] = []

    # All glyph features sit on the 100-mil grid: the symbol primitive tools
    # snap to 100, so sub-100 detail (e.g. plates 40 mils apart) would
    # collapse. Body spans x in [100, 300] (the pin body-attach points),
    # y in [-100, 100].
    if k in ("resistor", "r", "res", "inductor", "l", "ind"):
        rects.append({"x1": _BODY_L, "y1": -100, "x2": _BODY_R, "y2": 100})
    elif k in ("capacitor", "c", "cap"):
        # Two parallel plates at the body edges; the pin stubs are the leads.
        lines.append({"x1": _BODY_L, "y1": -100, "x2": _BODY_L, "y2": 100})
        lines.append({"x1": _BODY_R, "y1": -100, "x2": _BODY_R, "y2": 100})
    elif k in ("polarized_capacitor", "cap_pol", "electrolytic", "cp"):
        # Two plates plus a '+' by the positive (pin-1) plate -- the
        # unambiguous polarity marker for an electrolytic / tantalum.
        lines.append({"x1": _BODY_L, "y1": -100, "x2": _BODY_L, "y2": 100})
        lines.append({"x1": _BODY_R, "y1": -100, "x2": _BODY_R, "y2": 100})
        lines.append({"x1": 25, "y1": 150, "x2": 75, "y2": 150})   # + horiz
        lines.append({"x1": 50, "y1": 125, "x2": 50, "y2": 175})   # + vert
    elif k in ("diode", "d"):
        # Triangle (anode at the base, apex pointing right) + cathode bar.
        polys.append({"points": [(_BODY_L, -100), (_BODY_L, 100), (_BODY_R, 0)]})
        lines.append({"x1": _BODY_R, "y1": -100, "x2": _BODY_R, "y2": 100})
    elif k in ("led", "diode_led"):
        # Diode + two emission arrows pointing away (up-right) -- the LED.
        polys.append({"points": [(_BODY_L, -100), (_BODY_L, 100), (_BODY_R, 0)]})
        lines.append({"x1": _BODY_R, "y1": -100, "x2": _BODY_R, "y2": 100})
        for x0 in (175, 225):                    # two parallel arrow shafts
            lines.append({"x1": x0, "y1": 125, "x2": x0 + 75, "y2": 200})
            # arrowhead barbs at the tip
            lines.append({"x1": x0 + 75, "y1": 200, "x2": x0 + 50, "y2": 200})
            lines.append({"x1": x0 + 75, "y1": 200, "x2": x0 + 75, "y2": 175})
    elif k in ("crystal", "xtal", "y"):
        # Two plates (the electrodes) with the resonator rectangle between.
        lines.append({"x1": _BODY_L, "y1": -100, "x2": _BODY_L, "y2": 100})
        lines.append({"x1": _BODY_R, "y1": -100, "x2": _BODY_R, "y2": 100})
        rects.append({"x1": 150, "y1": -75, "x2": 250, "y2": 75})
    elif k in ("fuse", "f"):
        # IEC fuse: a rectangle with the lead passing straight through.
        rects.append({"x1": _BODY_L, "y1": -100, "x2": _BODY_R, "y2": 100})
        lines.append({"x1": _BODY_L, "y1": 0, "x2": _BODY_R, "y2": 0})
    else:
        raise ValueError(
            f"unknown passive kind {kind!r}; use resistor/capacitor/"
            f"polarized_capacitor/inductor/diode/led/crystal/fuse")

    return PassiveSymbol(pins=pins, lines=tuple(lines),
                         rectangles=tuple(rects), polygons=tuple(polys))


__all__ = ["SymbolGeometry", "generate_ic_symbol",
           "PassiveSymbol", "generate_passive_symbol",
           "PIN_LENGTH", "PIN_PITCH"]
