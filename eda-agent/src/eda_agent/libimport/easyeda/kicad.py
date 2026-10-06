# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Emit KiCad 6+ library files from a normalized EasyEDA component.

Written against KiCad's own documented s-expression library formats.
Input is the neutral model from :mod:`document` (mils, Y-up, origin
relative), so this module only has to convert units and, for footprints,
flip Y back down (``.kicad_mod`` is Y-down while ``.kicad_sym`` is Y-up).

Both writers return text, nothing touches the filesystem here, so the
whole path is unit-testable offline.
"""

from __future__ import annotations

from typing import Optional

from eda_agent.libimport.easyeda.document import (
    EasyEdaComponent,
    EasyEdaFootprint,
    EasyEdaSymbol,
)
from eda_agent.libimport.easyeda.geometry import svg_arc_to_center
from eda_agent.libimport.easyeda.shapes import PIN_ELECTRIC

__all__ = ["footprint_to_kicad_mod", "symbol_to_kicad_sym"]

from eda_agent.units import MM_PER_MIL as _MIL_TO_MM

#: EasyEDA electrical code -> KiCad pin electrical type.
_KICAD_ELEC = {
    "undefined": "unspecified",
    "input": "input",
    "output": "output",
    "bidirectional": "bidirectional",
    "power": "power_in",
    # The reverse of the reader's mapping, so an ERC-relevant pin kind
    # survives a trip out to KiCad and back.
    "open_collector": "open_collector",
    "open_emitter": "open_emitter",
    "hiz": "tri_state",
}

#: EasyEDA layer id -> KiCad layer name.
_KICAD_LAYER = {
    1: "F.Cu", 2: "B.Cu", 3: "F.SilkS", 4: "B.SilkS",
    5: "F.Paste", 6: "B.Paste", 7: "F.Mask", 8: "B.Mask",
    # 11 is EasyEDA's MultiLayer (all copper). KiCad has no all-copper
    # GRAPHIC layer, so a graphic there falls back to F.Cu; multi-layer
    # PADS are handled properly by is_through_hole, which emits "*.Cu".
    10: "Edge.Cuts", 11: "F.Cu", 12: "Cmts.User",
    13: "F.Fab", 14: "B.Fab",
}

# Observed on real parts and NOT in the documented map: 99, 100, 101.
# Measured across an RS-485 transceiver, an MCU and an 0603 capacitor:
#   layer 99/100 -- every SOLIDREGION in all three parts (117 of them,
#                   both "solid" and "cutout" fills). None sits on a
#                   copper layer, so none is a pour; _is_real_pour skips
#                   them and the rendered footprints match the source.
#   layer 101    -- exactly one circle per part, which renders as the
#                   pin-1 marker, so the F.SilkS fallback is right here.
# Unmapped GRAPHICS therefore fall back to silkscreen deliberately;
# unmapped REGIONS are dropped, because painting them was measurably
# worse (see _is_real_pour).


def _is_real_pour(shape) -> bool:
    """True only for a region that genuinely adds copper.

    Real EasyEDA footprints are full of SOLIDREGION entries that are NOT
    pours: an LQFP-48 carries 97 of them, every one ``fill="cutout"`` on
    layers 99/100, which are not in the documented layer map. They mark
    material to REMOVE, per-pad.

    Emitting those as filled polygons put a solid block over the whole
    body and a fill over every pad, i.e. worse than the hairline outlines
    the fill support replaced. So require both an explicit solid fill and
    a layer we actually understand.
    """
    if str(getattr(shape, "fill", "") or "").lower() == "cutout":
        return False
    return getattr(shape, "layer", None) in _KICAD_LAYER


def _mm(mils: float) -> float:
    v = round(mils * _MIL_TO_MM, 6)
    # Normalize negative zero: "-0.0" is valid but reads as a defect in
    # a diffed library file.
    return 0.0 if v == 0 else v


def _esc(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def _shape_extent(s) -> Optional[tuple[float, float, float, float]]:
    """(x1, y1, x2, y2) of one shape in the neutral frame, or None."""
    k = s.kind
    if k == "pad":
        return (s.cx - s.width / 2, s.cy - s.height / 2,
                s.cx + s.width / 2, s.cy + s.height / 2)
    if k == "pin":
        # Span the whole pin, tip to body root, so a label never lands
        # on top of a pin line.
        import math
        a = math.radians(s.rotation)
        ex, ey = s.x + s.length * math.cos(a), s.y + s.length * math.sin(a)
        return (min(s.x, ex), min(s.y, ey), max(s.x, ex), max(s.y, ey))
    if k == "rect":
        return (s.x, s.y, s.x + s.width, s.y + s.height)
    if k in ("circle", "ellipse"):
        ry = s.ry if getattr(s, "ry", None) else s.radius
        return (s.cx - s.radius, s.cy - ry, s.cx + s.radius, s.cy + ry)
    if k == "hole":
        r = s.diameter / 2
        return (s.cx - r, s.cy - r, s.cx + r, s.cy + r)
    if k in ("polyline", "polygon", "track", "solid_region") and s.points:
        xs = [x for x, _ in s.points]
        ys = [y for _, y in s.points]
        return (min(xs), min(ys), max(xs), max(ys))
    if k == "arc" and getattr(s, "is_valid", False):
        # Endpoint hull understates a bulging arc, but it is a safe
        # lower bound for text placement and needs no trig.
        return (min(s.x1, s.x2), min(s.y1, s.y2),
                max(s.x1, s.x2), max(s.y1, s.y2))
    return None


def _bbox(shapes) -> tuple[float, float, float, float]:
    """Bounding box over every shape that has one, else a unit box."""
    boxes = [b for b in (_shape_extent(s) for s in shapes) if b]
    if not boxes:
        return (-100.0, -100.0, 100.0, 100.0)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


#: Clear of the body by one text height, so nothing ever overlaps.
_TEXT_GAP_MILS = 60.0


def _pin_lines(pins) -> list[str]:
    """The ``(pin ...)`` blocks for one sub-symbol.

    Shared by the unit-0 block and every numbered unit so the two cannot
    diverge: the graphic style, the visibility flag and the empty-name
    spelling were each a defect once, and having them written twice is
    how the next one gets fixed in only one place.
    """
    out: list[str] = []
    for s in pins:
        elec = _KICAD_ELEC.get(PIN_ELECTRIC.get(s.electric, "undefined"),
                               "unspecified")
        # Already in the KiCad/Altium convention: _to_mils_yup undid
        # EasyEDA's 180-degree offset and the Y-mirror negation.
        angle = int(round(s.rotation)) % 360
        # KiCad's grammar is `(pin <electrical> <graphic_style> ...)`
        # with exactly ONE style token. This emitted "line inverted" --
        # two tokens -- so an inverted pin produced a malformed file
        # that KiCad refused with "Unable to load library", while a
        # reader taking the second token merely saw a plain "line".
        if s.dot and s.clock:
            style = "inverted_clock"
        elif s.dot:
            style = "inverted"
        elif s.clock:
            style = "clock"
        else:
            style = "line"
        out.append(
            f'      (pin {elec} {style} '
            f'(at {_mm(s.x)} {_mm(s.y)} {angle}) (length {_mm(s.length)})')
        if not getattr(s, "display", True):
            out.append('        (hide yes)')
        # An empty name is written EMPTY, which is what KiCad 10 itself
        # writes. The "~" spelling is the legacy marker and no longer
        # appears in its libraries at all, so emitting it produced files
        # unlike anything KiCad generates.
        out.append(f'        (name "{_esc(s.name)}" '
                   f'(effects (font (size 1.27 1.27))))')
        out.append(f'        (number "{_esc(s.number)}" '
                   f'(effects (font (size 1.27 1.27))))')
        out.append('      )')
    return out


def symbol_to_kicad_sym(
    comp: EasyEdaComponent, lib_name: str = "easyeda",
) -> str:
    """A complete ``.kicad_sym`` document holding this one symbol."""
    sym: Optional[EasyEdaSymbol] = comp.symbol
    if sym is None:
        raise ValueError("component has no symbol geometry")

    name = _esc(sym.name or comp.mpn or "SYMBOL")
    # Reference above the body and Value below it, the KiCad library
    # convention. Leaving both at (0, 0) stacks them on each other and
    # on the pin names in the middle of the symbol.
    bx1, by1, bx2, by2 = _bbox(sym.shapes)
    mid_x = (bx1 + bx2) / 2.0
    ref_y = by2 + _TEXT_GAP_MILS
    val_y = by1 - _TEXT_GAP_MILS

    out: list[str] = []
    # Declare the format we actually WRITE. This emitted 20211014 (the
    # 2021 format) while using 2024 syntax such as "(hide yes)", so the
    # file contradicted its own header -- tolerated by KiCad, which
    # silently migrated it on open, and wrong in the same way the
    # two-token pin style was.
    #
    # Verified against KiCad 10.0.1: loads with no errors and `sym
    # upgrade` reports "not updated", i.e. already current. Older KiCad
    # may need `kicad-cli sym upgrade` on the result; that is the cost
    # of a self-consistent file and the project targets KiCad 9+.
    out.append('(kicad_symbol_lib (version 20251024) (generator eda_agent)')
    out.append(f'  (symbol "{name}" (in_bom yes) (on_board yes)')
    # KiCad declares label visibility once per symbol; the neutral model
    # and Altium both carry it per pin. Emit the hide only when EVERY pin
    # agrees, because a per-symbol flag cannot express a symbol that
    # hides some pin names and shows others. A partial disagreement is
    # reported rather than half-applied, since silently showing labels
    # the source hid is what makes an imported passive look wrong.
    _sym_pins = [s for s in sym.shapes if s.kind == "pin"]
    for _attr, _tag in (("name_visible", "pin_names"),
                        ("number_visible", "pin_numbers")):
        _flags = {bool(getattr(p, _attr, True)) for p in _sym_pins}
        if _flags == {False}:
            out.append(f'    ({_tag} (hide yes))')
        elif len(_flags) > 1:
            comp.warnings.append(
                f"symbol {name!r} mixes {_attr} across its pins; "
                f"KiCad declares it once per symbol, so the labels were "
                f"left visible")
    out.append(f'    (property "Reference" "{_esc(sym.prefix)}" (id 0) '
               f'(at {_mm(mid_x)} {_mm(ref_y)} 0) '
               f'(effects (font (size 1.27 1.27))))')
    out.append(f'    (property "Value" "{_esc(comp.mpn or name)}" (id 1) '
               f'(at {_mm(mid_x)} {_mm(val_y)} 0) '
               f'(effects (font (size 1.27 1.27))))')
    out.append(f'    (property "Footprint" "{_esc(comp.package)}" (id 2) '
               f'(at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))')
    out.append(f'    (property "Datasheet" "{_esc(comp.datasheet)}" (id 3) '
               f'(at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))')
    if comp.manufacturer:
        out.append(f'    (property "Manufacturer" '
                   f'"{_esc(comp.manufacturer)}" (id 4) (at 0 0 0) '
                   f'(effects (font (size 1.27 1.27)) (hide yes)))')
    if comp.lcsc_id:
        out.append(f'    (property "LCSC" "{_esc(comp.lcsc_id)}" (id 5) '
                   f'(at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))')

    out.append(f'    (symbol "{name}_0_1"')
    for s in sym.shapes:
        if s.kind == "rect":
            out.append(
                f'      (rectangle (start {_mm(s.x)} {_mm(s.y)}) '
                f'(end {_mm(s.x + s.width)} {_mm(s.y + s.height)}) '
                f'(stroke (width 0) (type default)) '
                f'(fill (type {"background" if s.fill else "none"})))')
        elif s.kind in ("circle", "ellipse"):
            out.append(
                f'      (circle (center {_mm(s.cx)} {_mm(s.cy)}) '
                f'(radius {_mm(s.radius)}) '
                f'(stroke (width 0) (type default)) (fill (type none)))')
        elif s.kind in ("polyline", "polygon") and len(s.points) >= 2:
            pts = list(s.points)
            if s.kind == "polygon" and pts[0] != pts[-1]:
                pts.append(pts[0])
            joined = " ".join(f"(xy {_mm(x)} {_mm(y)})" for x, y in pts)
            out.append(
                f'      (polyline (pts {joined}) '
                f'(stroke (width 0) (type default)) '
                f'(fill (type {"background" if s.fill else "none"})))')
        elif s.kind == "arc" and s.is_valid:
            arc = svg_arc_to_center(s.x1, s.y1, s.rx, s.ry, s.rotation,
                                    s.large_arc, s.sweep, s.x2, s.y2)
            if arc is not None:
                mx, my = arc.midpoint
                out.append(
                    f'      (arc (start {_mm(arc.x1)} {_mm(arc.y1)}) '
                    f'(mid {_mm(mx)} {_mm(my)}) '
                    f'(end {_mm(arc.x2)} {_mm(arc.y2)}) '
                    f'(stroke (width 0) (type default)) '
                    f'(fill (type none)))')
        elif s.kind == "text" and s.text:
            # Back to DECIDEGREES, which is what .kicad_sym text uses
            # (see the reader). This wrote a literal 0, so every rotated
            # string in a symbol body came out upright.
            ang10 = int(round(s.rotation * 10)) % 3600
            size_mm = _mm(s.font_size) or 1.27
            out.append(
                f'      (text "{_esc(s.text)}" '
                f'(at {_mm(s.x)} {_mm(s.y)} {ang10}) '
                f'(effects (font (size {size_mm} {size_mm}))))')

    # One sub-symbol per unit, matching KiCad's "NAME_<unit>_<style>"
    # convention. Writing every pin into _1_1 regardless would collapse
    # a multi-part component to a single unit -- and silently, since the
    # pin count and geometry would all still be right.
    by_unit: dict[int, list] = {}
    for s in sym.shapes:
        if s.kind == "pin":
            by_unit.setdefault(getattr(s, "unit", 1), []).append(s)

    # Unit 0 is KiCad's "shared by every unit", which is where a quad
    # gate keeps its supply rails. Its sub-symbol is _0_1 and the body
    # art above already opened that block, so those pins go INSIDE it
    # rather than opening a second block of the same name.
    #
    # kicad-cli tolerates the duplicate, so this is tidiness and not a
    # fix; no KiCad library writes a sub-symbol name twice, and relying
    # on a parser's tolerance for malformed structure is how the
    # two-token pin style survived unnoticed.
    out.extend(_pin_lines(by_unit.get(0, [])))
    out.append('    )')

    for unit in sorted(u for u in by_unit if u > 0):
        out.append(f'    (symbol "{name}_{unit}_1"')
        out.extend(_pin_lines(by_unit[unit]))
        out.append('    )')
    # Declare unit 1 explicitly when every pin lives in the shared unit
    # 0. This MATCHES KiCad's own libraries rather than fixing a
    # failure: AD7150BRMZ keeps all ten pins in a _0_0 block and carries
    # an empty _1_1 beside it, and five of the 219 sampled symbols are
    # built that way.
    #
    # Measured, because the first version of this comment claimed KiCad
    # plots nothing without it and that is FALSE -- kicad-cli renders
    # unit 1 either way. Kept because matching the structure KiCad emits
    # is the safer target for a format defined by its implementation,
    # not because anything currently breaks.
    if not any(u > 0 for u in by_unit):
        out.append(f'    (symbol "{name}_1_1"')
        out.append('    )')
    out.append('  )')
    out.append(')')
    return "\n".join(out) + "\n"


def footprint_to_kicad_mod(
    comp: EasyEdaComponent, model_path: Optional[str] = None,
) -> str:
    """A complete ``.kicad_mod`` document for this component.

    Args:
        model_path: explicit 3D model reference to embed. When
            omitted a ``.wrl`` beside the project is assumed,
            matching what model3d writes.
    """
    fp: Optional[EasyEdaFootprint] = comp.footprint
    if fp is None:
        raise ValueError("component has no footprint geometry")

    name = _esc(fp.name or comp.package or "FOOTPRINT")
    out: list[str] = []
    # Same reasoning as the symbol header above; 20260206 is what
    # KiCad 10.0.1 writes and `fp upgrade` leaves untouched.
    out.append(f'(footprint "{name}" (version 20260206) '
               f'(generator eda_agent)')
    out.append('  (layer "F.Cu")')
    if comp.description or comp.mpn:
        out.append(f'  (descr "{_esc(comp.description or comp.mpn)}")')
    out.append(f'  (attr {"through_hole" if _has_tht(fp) else "smd"})')
    # Same reasoning as the symbol: (0, 0) drops both strings across the
    # pads. Y is negated because a .kicad_mod is Y-down, so the neutral
    # TOP edge is the smaller emitted Y.
    fx1, fy1, fx2, fy2 = _bbox(fp.shapes)
    fmid_x = (fx1 + fx2) / 2.0
    out.append(f'  (fp_text reference "REF**" '
               f'(at {_mm(fmid_x)} {_mm(-(fy2 + _TEXT_GAP_MILS))}) '
               f'(layer "F.SilkS")'
               ' (effects (font (size 1 1) (thickness 0.15))))')
    out.append(f'  (fp_text value "{_esc(comp.mpn or name)}" '
               f'(at {_mm(fmid_x)} {_mm(-(fy1 - _TEXT_GAP_MILS))}) '
               f'(layer "F.Fab") (effects (font (size 1 1) '
               f'(thickness 0.15))))')

    for s in fp.shapes:
        layer = _KICAD_LAYER.get(getattr(s, "layer", 3), "F.SilkS")
        if s.kind == "pad":
            out.append(_kicad_pad(s))
        elif s.kind == "solid_region" and len(s.points) >= 3                 and _is_real_pour(s):
            # A SOLIDREGION is FILLED copper, not an outline. Emitting it
            # through the fp_line branch produced hairline traces and
            # silently lost the copper area, and dropped the closing edge
            # as well because the path's Z was never applied.
            pts = list(s.points)
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            joined = " ".join(f"(xy {_mm(x)} {_mm(-y)})" for x, y in pts)
            out.append(
                f'  (fp_poly (pts {joined}) (layer "{layer}") (width 0))')
        elif s.kind in ("track", "polyline") \
                and len(s.points) >= 2:
            w = _mm(s.stroke_width) or 0.12
            for (x1, y1), (x2, y2) in zip(s.points, s.points[1:]):
                out.append(
                    f'  (fp_line (start {_mm(x1)} {_mm(-y1)}) '
                    f'(end {_mm(x2)} {_mm(-y2)}) (layer "{layer}") '
                    f'(width {w}))')
        elif s.kind == "circle":
            w = _mm(s.stroke_width) or 0.12
            out.append(
                f'  (fp_circle (center {_mm(s.cx)} {_mm(-s.cy)}) '
                f'(end {_mm(s.cx + s.radius)} {_mm(-s.cy)}) '
                f'(layer "{layer}") (width {w}))')
        elif s.kind == "rect":
            w = _mm(s.stroke_width) or 0.12
            out.append(
                f'  (fp_rect (start {_mm(s.x)} {_mm(-s.y)}) '
                f'(end {_mm(s.x + s.width)} {_mm(-(s.y + s.height))}) '
                f'(layer "{layer}") (width {w}) (fill none))')
        elif s.kind == "arc" and s.is_valid:
            arc = svg_arc_to_center(s.x1, s.y1, s.rx, s.ry, s.rotation,
                                    s.large_arc, s.sweep, s.x2, s.y2)
            if arc is not None:
                mx, my = arc.midpoint
                w = _mm(s.stroke_width) or 0.12
                out.append(
                    f'  (fp_arc (start {_mm(arc.x1)} {_mm(-arc.y1)}) '
                    f'(mid {_mm(mx)} {_mm(-my)}) '
                    f'(end {_mm(arc.x2)} {_mm(-arc.y2)}) '
                    f'(layer "{layer}") (width {w}))')
        elif s.kind == "text" and s.text and s.visible:
            # Rotation negates with the Y mirror, same as pads.
            ang = int(round(-s.rotation)) % 360
            at = (f'{_mm(s.x)} {_mm(-s.y)}'
                  + (f' {ang}' if ang else ''))
            # Size and stroke came straight back as a hardcoded 1 mm /
            # 0.15 mm, so every exported string was the same height
            # whatever the source said. The corpus carries 91 distinct
            # heights, so that is a real loss, not a rounding one.
            size_mm = _mm(s.font_size) or 1.0
            thick_mm = _mm(getattr(s, "stroke_width", 0) or 0) or 0.15
            # KiCad spells mirroring as a justify token, and text on a
            # bottom layer needs it or it reads backwards.
            justify = (' (justify mirror)'
                       if getattr(s, "mirror", False) else '')
            out.append(
                f'  (fp_text user "{_esc(s.text)}" '
                f'(at {at}) (layer "{layer}") '
                f'(effects (font (size {size_mm} {size_mm}) '
                f'(thickness {thick_mm})){justify}))')
        elif s.kind == "hole":
            r = _mm(s.diameter) / 2.0
            out.append(
                f'  (pad "" np_thru_hole circle '
                f'(at {_mm(s.cx)} {_mm(-s.cy)}) (size {r * 2} {r * 2}) '
                f'(drill {r * 2}) (layers "*.Cu" "*.Mask"))')

    # Only reference a model file we actually produce. The previous
    # ".step" guess was a dangling reference: EasyEDA serves an OBJ-family
    # payload, which model3d converts to VRML, never STEP.
    ref = model_path
    if ref is None and fp.model_3d_name:
        ref = f"${{KIPRJMOD}}/{fp.model_3d_name}.wrl"
    if ref:
        # The EasyEDA model is centred on the footprint origin and Z-up,
        # and model3d emits KiCad's 0.1 inch VRML units, so the placement
        # is the identity.
        out.append(f'  (model "{_esc(ref)}"'
                   f' (offset (xyz 0 0 0)) (scale (xyz 1 1 1))'
                   f' (rotate (xyz 0 0 0)))')
    out.append(')')
    return "\n".join(out) + "\n"


def _has_tht(fp: EasyEdaFootprint) -> bool:
    return any(s.kind == "pad" and s.is_through_hole for s in fp.shapes)


def _kicad_pad(pad) -> str:
    """One ``pad`` s-expression. Y is flipped back down for .kicad_mod."""
    if pad.is_through_hole:
        ptype = "thru_hole" if pad.plated else "np_thru_hole"
        layers = '"*.Cu" "*.Mask"'
    else:
        ptype = "smd"
        # A pad on a PASTE or MASK layer is a stencil / mask aperture
        # with no copper of its own. Modern chip footprints use them to
        # subdivide paste on 01005 and 0201 parts, and 332 of the 6902
        # pads in KiCad 10.0.1's sampled libraries are one.
        #
        # Emitting the full copper stack for them, which this did, puts
        # copper where the source deliberately has none -- shorting
        # adjacent pads on exactly the fine-pitch parts that use the
        # technique. The layer has to drive the stack, not just the side.
        aperture = {
            5: '"F.Paste"', 6: '"B.Paste"',
            7: '"F.Mask"', 8: '"B.Mask"',
        }.get(pad.layer)
        if aperture:
            layers = aperture
        else:
            side = "B" if pad.layer == 2 else "F"
            layers = f'"{side}.Cu" "{side}.Paste" "{side}.Mask"'

    shape = {
        "ELLIPSE": "circle",
        "RECT": "rect",
        "ROUNDRECT": "roundrect",
        "OVAL": "oval",
        "POLYGON": "rect",   # no native equivalent; warned in document.py
    }.get(pad.shape, "circle")
    # A circle pad in KiCad must be square in size; use the larger extent
    # rather than silently shrinking the land.
    w, h = pad.width, pad.height
    if shape == "circle" and abs(w - h) > 1e-6:
        shape = "oval"
    # roundrect carries its corner radius as a ratio of the SHORTER
    # side, which is the neutral model's own convention (Altium's
    # percentage is against half that side, and the Altium emitter does
    # that conversion). Omitting the ratio would silently give KiCad's
    # 0.25 default in place of whatever the source had.
    extra_shape = ""
    if shape == "roundrect":
        # Used as-is, NOT `or 0.25`: a source may state a ratio of zero
        # to mean square corners on a pad it still calls a roundrect,
        # and treating that falsy value as "unset" rounds a pad the
        # designer deliberately left sharp. The reader already supplies
        # KiCad's 0.25 default when the field is genuinely absent.
        ratio = float(getattr(pad, "corner_ratio", 0.0))
        extra_shape = f" (roundrect_rratio {max(0.0, min(0.5, ratio)):g})"

    parts = [
        f'  (pad "{_esc(pad.number)}" {ptype} {shape}',
        # Y is negated just above because a .kicad_mod is Y-DOWN while
        # the neutral model is Y-UP. The same mirror negates rotation,
        # so undo the flip document._to_mils_yup applied. (Altium is
        # Y-up and therefore uses the neutral value as-is.)
        f'(at {_mm(pad.cx)} {_mm(-pad.cy)}'
        + (f' {int(round(-pad.rotation)) % 360})' if pad.rotation
           else ')'),
        f'(size {_mm(w)} {_mm(h)}){extra_shape}',
    ]
    if pad.hole_radius > 0:
        if pad.is_slot and pad.hole_length > 0:
            parts.append(
                f'(drill oval {_mm(pad.hole_length)} '
                f'{_mm(pad.hole_radius * 2)})')
        else:
            parts.append(f'(drill {_mm(pad.hole_radius * 2)})')
    parts.append(f'(layers {layers}))')
    return " ".join(parts)
