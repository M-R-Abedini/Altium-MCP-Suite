# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""EasyEDA shape-string parser.

Independent implementation written from the vendor's own published
specification (docs.easyeda.com "EasyEDA Document Format" and the
easyeda-documents "Open File Format" notes). No third-party converter
source was used; the AGPL-licensed easyeda2kicad in particular is not a
reference here, which keeps this module cleanly Apache-2.0.

Format, per that spec:

* A document is JSON. Geometry is compressed into strings to keep files
  small, so the interesting content is string parsing, not JSON walking.
* ``~``    separates the attributes of one shape.
* ``^^``   separates the segments of a compound shape (pins, pads).
* ``#@$``  separates shapes inside a library element.
* `` ` ``  separates custom key/value attribute pairs.
* Coordinates are multiples of **10 mil** ("EasyEDA takes 10 mil as a
  basic factor"), on a screen-style canvas whose Y axis grows DOWNWARD.

Everything here is pure and offline: strings in, dataclasses out. Unit
and axis conversion is deliberately NOT done at this layer, the emitters
decide, because KiCad symbols are Y-up, KiCad footprints are Y-down and
Altium is Y-up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

__all__ = [
    "ATTR_SEP",
    "EASYEDA_UNIT_MIL",
    "LAYER_NAMES",
    "SEGMENT_SEP",
    "SHAPE_SEP",
    "EeArc",
    "EeCircle",
    "EePad",
    "EePin",
    "EePolyline",
    "EeRect",
    "EeShape",
    "EeText",
    "EeTrack",
    "EeHole",
    "parse_footprint_shapes",
    "parse_symbol_shapes",
    "split_shapes",
]

ATTR_SEP = "~"
SEGMENT_SEP = "^^"
SHAPE_SEP = "#@$"

#: One EasyEDA coordinate unit, in mils. From the PCB format spec:
#: "EasyEDA takes 10 mil as a basic factor". The schematic grid uses the
#: same factor (a 100 mil pin pitch is 10 units).
EASYEDA_UNIT_MIL = 10.0

#: Layer ids from the PCB format spec.
LAYER_NAMES: dict[int, str] = {
    1: "top_copper",
    2: "bottom_copper",
    3: "top_silk",
    4: "bottom_silk",
    5: "top_paste",
    6: "bottom_paste",
    7: "top_mask",
    8: "bottom_mask",
    9: "ratlines",
    10: "board_outline",
    11: "multi_layer",
    12: "document",
    13: "top_assembly",
    14: "bottom_assembly",
    21: "inner1",
    22: "inner2",
    23: "inner3",
    24: "inner4",
}


def _f(fields: list[str], idx: int, default: float = 0.0) -> float:
    """Field ``idx`` as float, tolerating blanks and junk.

    EasyEDA omits trailing attributes freely and writes empty strings for
    unset numbers, so a strict parse would reject perfectly good real
    documents.
    """
    try:
        raw = fields[idx].strip()
    except IndexError:
        return default
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _s(fields: list[str], idx: int, default: str = "") -> str:
    try:
        return fields[idx].strip()
    except IndexError:
        return default


def _i(fields: list[str], idx: int, default: int = 0) -> int:
    return int(_f(fields, idx, float(default)))


def split_shapes(blob: str) -> list[str]:
    """Split a library element's shape blob on the ``#@$`` marker."""
    if not blob:
        return []
    return [s for s in blob.split(SHAPE_SEP) if s.strip()]


def parse_points(raw: str) -> list[tuple[float, float]]:
    """Parse a coordinate list.

    EasyEDA writes point runs either space separated ("x y x y") or comma
    separated ("x,y x,y"); both appear in real documents.
    """
    if not raw:
        return []
    nums: list[float] = []
    for tok in raw.replace(",", " ").split():
        try:
            nums.append(float(tok))
        except ValueError:
            continue
    return [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]


@dataclass
class EeShape:
    """Base: every parsed shape keeps its raw string for diagnostics."""

    kind: str
    raw: str = ""


@dataclass
class EeRect(EeShape):
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    layer: int = 0
    stroke_width: float = 0.0
    fill: str = ""


@dataclass
class EeCircle(EeShape):
    cx: float = 0.0
    cy: float = 0.0
    radius: float = 0.0
    stroke_width: float = 0.0
    layer: int = 0
    # Ellipses carry a second radius; circles repeat rx.
    ry: Optional[float] = None


@dataclass
class EePolyline(EeShape):
    points: list[tuple[float, float]] = field(default_factory=list)
    stroke_width: float = 0.0
    layer: int = 0
    closed: bool = False
    fill: str = ""


@dataclass
class EeTrack(EeShape):
    points: list[tuple[float, float]] = field(default_factory=list)
    stroke_width: float = 0.0
    layer: int = 0


@dataclass
class EeArc(EeShape):
    """An SVG elliptical arc, decomposed into numeric fields.

    The raw ``path`` is kept for diagnostics, but every consumer uses
    the structured fields. Rewriting coordinates inside the path STRING
    was the original design and it was quietly wrong: SVG packs a
    command against its number ("M10,0"), so a whitespace tokenizer
    silently passed the path through untransformed and arcs came out
    ten times too small. Numbers in fields cannot have that failure.
    """

    path: str = ""
    stroke_width: float = 0.0
    layer: int = 0
    x1: float = 0.0
    y1: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    rotation: float = 0.0
    large_arc: int = 0
    sweep: int = 0
    x2: float = 0.0
    y2: float = 0.0

    @property
    def is_valid(self) -> bool:
        return self.rx > 0 and self.ry > 0


_ARC_PATH_RE = re.compile(
    r"M\s*(-?[\d.]+)[,\s]+(-?[\d.]+)\s*"
    r"A\s*(-?[\d.]+)[,\s]+(-?[\d.]+)[,\s]+(-?[\d.]+)[,\s]+"
    r"([01])[,\s]*([01])[,\s]+(-?[\d.]+)[,\s]+(-?[\d.]+)",
    re.IGNORECASE,
)


def _fill_arc(arc: EeArc) -> EeArc:
    """Decompose ``arc.path`` into numeric fields, if it is an arc."""
    m = _ARC_PATH_RE.search(arc.path or "")
    if not m:
        return arc
    arc.x1 = float(m.group(1))
    arc.y1 = float(m.group(2))
    arc.rx = abs(float(m.group(3)))
    arc.ry = abs(float(m.group(4)))
    arc.rotation = float(m.group(5))
    arc.large_arc = int(m.group(6))
    arc.sweep = int(m.group(7))
    arc.x2 = float(m.group(8))
    arc.y2 = float(m.group(9))
    return arc


@dataclass
class EeText(EeShape):
    x: float = 0.0
    y: float = 0.0
    text: str = ""
    font_size: float = 0.0
    stroke_width: float = 0.0
    rotation: float = 0.0
    layer: int = 0
    text_type: str = ""
    mirror: bool = False
    visible: bool = True


@dataclass
class EeHole(EeShape):
    cx: float = 0.0
    cy: float = 0.0
    diameter: float = 0.0


@dataclass
class EePad(EeShape):
    number: str = ""
    # ELLIPSE | RECT | ROUNDRECT | OVAL | POLYGON
    shape: str = "ELLIPSE"
    #: Corner rounding of a ROUNDRECT, as a fraction of the SHORTER pad
    #: side (KiCad's roundrect_rratio convention, 0.25 being its
    #: default). Altium expresses the same thing as a percentage of HALF
    #: the shorter side, so converting between them is not a straight
    #: multiply by 100; the emitter does that conversion.
    corner_ratio: float = 0.0
    cx: float = 0.0
    cy: float = 0.0
    width: float = 0.0
    height: float = 0.0
    layer: int = 1
    hole_radius: float = 0.0    # EasyEDA stores a RADIUS here
    hole_length: float = 0.0    # slot length, 0 = round hole
    rotation: float = 0.0
    plated: bool = True
    points: list[tuple[float, float]] = field(default_factory=list)

    @property
    def is_through_hole(self) -> bool:
        return self.hole_radius > 0 or self.layer == 11

    @property
    def is_slot(self) -> bool:
        return self.hole_length > 0 and self.hole_length != self.hole_radius * 2


@dataclass
class EePin(EeShape):
    number: str = ""
    name: str = ""
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    length: float = 0.0
    electric: int = 0           # EasyEDA electrical type code
    #: Which sub-part of a multi-part component owns this pin. 1 is the
    #: first (and only) part of an ordinary symbol; 0 means the pin is
    #: SHARED by every sub-part, which is how a quad op-amp carries its
    #: supply rails. Matches lib_add_pins' owner_part_id exactly. The
    #: EasyEDA parser models no sub-parts, so every pin it produces
    #: stays at 1 (that is a fact about this reader, not a claim about
    #: what the EasyEDA format can express).
    unit: int = 1
    display: bool = True
    name_visible: bool = True
    number_visible: bool = True
    dot: bool = False           # inverted / active-low bubble
    clock: bool = False


#: EasyEDA pin electrical codes -> neutral names. From the schematic
#: format spec's "electric type" field.
PIN_ELECTRIC: dict[int, str] = {
    0: "undefined",
    1: "input",
    2: "output",
    3: "bidirectional",
    4: "power",
    # 0-4 are EasyEDA's own vocabulary and must keep those numbers.
    # Everything below is the neutral model's, for kinds EasyEDA has no
    # code for but both KiCad and Altium do. Numbered from 10 so a
    # future EasyEDA code can never collide with one of them.
    #
    # These are not cosmetic. They are what ERC reasons about: an
    # open-collector output recorded as passive defeats the check for a
    # missing pull-up, and two of them driving one net stops being a
    # reported conflict. 1827 open-collector, 1858 tri-state and 119
    # open-emitter pins appear in KiCad 10.0.1's libraries.
    10: "open_collector",
    11: "open_emitter",
    12: "hiz",
}


def _parse_pin(raw: str) -> EePin:
    """Parse a ``P`` shape.

    Segment layout per the schematic spec:
      0 config: command, display, electric, spice num, x, y, rotation, id, locked
      1 pin dot: x, y
      2 pin path: path, colour        (the path carries the pin LENGTH)
      3 name label: visible, x, y, rot, text, anchor, font, size
      4 number label: visible, x, y, rot, text, anchor, font, size
      5 dot (inverted) indicator: visible, x, y
      6 clock indicator: visible, path
    """
    segs = raw.split(SEGMENT_SEP)
    cfg = segs[0].split(ATTR_SEP) if segs else []

    pin = EePin(kind="pin", raw=raw)
    pin.display = _s(cfg, 1, "show").lower() not in ("none", "0", "false")
    pin.electric = _i(cfg, 2, 0)
    pin.x = _f(cfg, 4)
    pin.y = _f(cfg, 5)
    pin.rotation = _f(cfg, 6)

    # Segment 2 holds the pin body path, "M <x> <y> h <len>" (or v).
    # The pin's drawn LENGTH is the only place its extent is recorded.
    if len(segs) > 2:
        path_fields = segs[2].split(ATTR_SEP)
        pin.length = _pin_length_from_path(_s(path_fields, 0))

    if len(segs) > 3:
        nm = segs[3].split(ATTR_SEP)
        pin.name_visible = _s(nm, 0, "1") not in ("0", "none", "")
        pin.name = _s(nm, 4)
    if len(segs) > 4:
        nu = segs[4].split(ATTR_SEP)
        pin.number_visible = _s(nu, 0, "1") not in ("0", "none", "")
        pin.number = _s(nu, 4)
    if len(segs) > 5:
        dot = segs[5].split(ATTR_SEP)
        pin.dot = _s(dot, 0, "0") in ("1", "show", "true")
    if len(segs) > 6:
        clk = segs[6].split(ATTR_SEP)
        pin.clock = _s(clk, 0, "0") in ("1", "show", "true")
    return pin


def _pin_length_from_path(path: str) -> float:
    """Pin length from its body path.

    EasyEDA writes the pin body as a two-point path, typically
    ``M <x> <y> h <dx>`` or ``M <x> <y> v <dy>``. The magnitude of the
    trailing move is the pin length in EasyEDA units.
    """
    if not path:
        return 0.0
    # SVG packs commands against their numbers ("M370,290h-10"), so
    # whitespace splitting alone finds nothing. Match the command letter
    # and its first number directly.
    m = re.search(r"[hHvV]\s*(-?\d+(?:\.\d+)?)", path)
    if m:
        try:
            return abs(float(m.group(1)))
        except ValueError:
            pass
    # Fall back to the span between the first two absolute points, which
    # covers the "M x y L x y" spelling.
    nums = re.findall(r"-?\d+(?:\.\d+)?", path)
    if len(nums) >= 4:
        try:
            x1, y1, x2, y2 = (float(n) for n in nums[:4])
            return max(abs(x2 - x1), abs(y2 - y1))
        except ValueError:
            return 0.0
    return 0.0


def _parse_pad(raw: str) -> EePad:
    """Parse a ``PAD`` shape.

    Field order per the PCB spec:
      PAD~shape~cx~cy~w~h~layer~net~number~holeRadius~points~rotation~id
         ~holeLength~holePoints~plated
    """
    f = raw.split(ATTR_SEP)
    pad = EePad(kind="pad", raw=raw)
    pad.shape = _s(f, 1, "ELLIPSE").upper()
    pad.cx = _f(f, 2)
    pad.cy = _f(f, 3)
    pad.width = _f(f, 4)
    pad.height = _f(f, 5)
    pad.layer = _i(f, 6, 1)
    pad.number = _s(f, 8)
    pad.hole_radius = _f(f, 9)
    pad.points = parse_points(_s(f, 10))
    pad.rotation = _f(f, 11)
    pad.hole_length = _f(f, 13)
    pad.plated = _s(f, 15, "Y").upper() != "N"
    return pad


def parse_footprint_shapes(blob: str) -> list[EeShape]:
    """Parse a PCB-library shape blob into typed shapes."""
    out: list[EeShape] = []
    for raw in split_shapes(blob):
        f = raw.split(ATTR_SEP)
        cmd = _s(f, 0).upper()
        if cmd == "PAD":
            out.append(_parse_pad(raw))
        elif cmd == "TRACK":
            out.append(EeTrack(
                kind="track", raw=raw,
                stroke_width=_f(f, 1), layer=_i(f, 2, 3),
                points=parse_points(_s(f, 4))))
        elif cmd == "ARC":
            out.append(_fill_arc(EeArc(
                kind="arc", raw=raw,
                stroke_width=_f(f, 1), layer=_i(f, 2, 3),
                path=_s(f, 4))))
        elif cmd == "CIRCLE":
            out.append(EeCircle(
                kind="circle", raw=raw,
                cx=_f(f, 1), cy=_f(f, 2), radius=_f(f, 3),
                stroke_width=_f(f, 4), layer=_i(f, 5, 3)))
        elif cmd == "RECT":
            out.append(EeRect(
                kind="rect", raw=raw,
                x=_f(f, 1), y=_f(f, 2), width=_f(f, 3), height=_f(f, 4),
                layer=_i(f, 5, 3)))
        elif cmd == "HOLE":
            out.append(EeHole(
                kind="hole", raw=raw,
                cx=_f(f, 1), cy=_f(f, 2), diameter=_f(f, 3)))
        elif cmd == "TEXT":
            out.append(EeText(
                kind="text", raw=raw,
                text_type=_s(f, 1), x=_f(f, 2), y=_f(f, 3),
                stroke_width=_f(f, 4), rotation=_f(f, 5),
                mirror=_s(f, 6) in ("1", "true"),
                layer=_i(f, 7, 3), font_size=_f(f, 9),
                text=_s(f, 10),
                visible=_s(f, 12, "1") not in ("0", "none")))
        elif cmd == "SOLIDREGION":
            out.append(EePolyline(
                kind="solid_region", raw=raw,
                layer=_i(f, 1, 3), points=parse_points(_s(f, 3)),
                closed=True, fill=_s(f, 4)))
        elif cmd == "VIA":
            # Vias inside a footprint are through features; model as a
            # plated pad so the emitters place real copper.
            pad = EePad(kind="pad", raw=raw, shape="ELLIPSE",
                        cx=_f(f, 1), cy=_f(f, 2),
                        width=_f(f, 3), height=_f(f, 3),
                        layer=11, hole_radius=_f(f, 5), plated=True)
            out.append(pad)
    return out


def parse_symbol_shapes(blob: str) -> list[EeShape]:
    """Parse a schematic-library shape blob into typed shapes."""
    out: list[EeShape] = []
    for raw in split_shapes(blob):
        head = raw.split(SEGMENT_SEP, 1)[0]
        f = head.split(ATTR_SEP)
        cmd = _s(f, 0).upper()
        if cmd == "P":
            out.append(_parse_pin(raw))
        elif cmd == "R":
            out.append(EeRect(
                kind="rect", raw=raw,
                x=_f(f, 1), y=_f(f, 2), width=_f(f, 5), height=_f(f, 6),
                stroke_width=_f(f, 8), fill=_s(f, 10)))
        elif cmd == "E":
            out.append(EeCircle(
                kind="ellipse", raw=raw,
                cx=_f(f, 1), cy=_f(f, 2), radius=_f(f, 3), ry=_f(f, 4),
                stroke_width=_f(f, 6)))
        elif cmd in ("PL", "PG", "W"):
            out.append(EePolyline(
                kind="polygon" if cmd == "PG" else "polyline", raw=raw,
                points=parse_points(_s(f, 1)),
                stroke_width=_f(f, 3), closed=cmd == "PG",
                fill=_s(f, 5)))
        elif cmd == "A":
            out.append(_fill_arc(EeArc(
                kind="arc", raw=raw, path=_s(f, 1), stroke_width=_f(f, 3))))
        elif cmd == "T":
            out.append(EeText(
                kind="text", raw=raw,
                x=_f(f, 2), y=_f(f, 3), rotation=_f(f, 4),
                font_size=_f(f, 7), text=_s(f, 12),
                visible=_s(f, 13, "1") not in ("0", "none")))
    return out
