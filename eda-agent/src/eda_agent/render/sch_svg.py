# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Schematic SVG renderer.

Takes the geometry payload produced by the Pascal ``Gen_GetSchGeometry``
handler (every component, pin, wire, junction, net label, port and
power port from the active SchDoc, in mils) and produces an SVG
string. The SVG carries ``data-designator``, ``data-net``, ``data-pin``
attributes on every meaningful group so a dashboard or external client
can hook click / hover / cross-probe without having to re-parse the
schematic.

This is v1: components render as labelled bounding-box bodies with
pin stubs from the body to the electrical end. Symbol-internal
primitives (rectangles / lines / arcs inside each symbol) are
deferred to v2; the current output is recognisable and ready for
interactive overlay, but does not yet draw the actual symbol art.
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass
from typing import Any, Iterable


# Pin-orientation enum -> unit vector pointing from the body OUT to the
# electrical end. Altium stores 0/1/2/3 as 0/90/180/270 degrees.
_PIN_DIR = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}

# Altium SCH LineWidth enum -> approximate mils. Used to convert the
# integer line-width value the Pascal handler emits into an SVG
# stroke-width. The enum values come from TLineWidth in the SDK.
_LINE_WIDTH_MILS = {0: 1, 1: 4, 2: 10, 3: 20}


def _altium_color(c: Any, fallback: str = "#8b6914") -> str:
    """Convert an Altium BGR-packed color integer to an SVG rgb() string.

    Altium stores colors as ``0xBBGGRR`` -- the low byte is R, high byte
    is B. ``#000080`` (navy) round-trips as the integer ``8388608``
    (``0x800000``), confirming the byte order.
    """
    try:
        n = int(c)
    except (TypeError, ValueError):
        return fallback
    r = n & 0xFF
    g = (n >> 8) & 0xFF
    b = (n >> 16) & 0xFF
    return f"rgb({r},{g},{b})"


def _line_width(prim: dict[str, Any], default_mils: float = 4.0) -> float:
    """Resolve an Altium SCH line-width enum to an SVG stroke-width in mils."""
    lw = prim.get("line_width")
    if lw is None:
        return default_mils
    return float(_LINE_WIDTH_MILS.get(int(lw), default_mils))


@dataclass
class SchRenderOptions:
    """Tuning knobs for the SVG output. All units are mils unless noted."""
    margin: int = 200                # padding around the geometry bbox
    pin_stroke_mils: float = 4.0     # pin stub line width
    wire_stroke_mils: float = 6.0    # net wire line width
    component_stroke_mils: float = 6.0
    junction_radius_mils: float = 18.0
    pin_dot_radius_mils: float = 6.0
    label_font_mils: int = 60        # net labels
    designator_font_mils: int = 70   # component designator
    libref_font_mils: int = 50       # component library reference
    pin_font_mils: int = 35          # pin name/number
    title: str | None = None         # optional <title>
    # Y-axis: Altium origin is bottom-left, SVG is top-left -- flip on render.
    flip_y: bool = True


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def render_sch_svg(geometry: dict[str, Any],
                   options: SchRenderOptions | None = None) -> str:
    """Render the SchDoc geometry payload to a self-contained SVG string.

    Args:
        geometry: payload from ``Gen_GetSchGeometry`` -- a dict with
            ``components``, ``pins``, ``wires``, ``junctions``,
            ``net_labels``, ``ports``, ``power_ports``.
        options: render tunables; sensible defaults if omitted.

    Returns:
        A complete SVG document (root ``<svg>`` with embedded ``<style>``)
        as a string.
    """
    opt = options or SchRenderOptions()
    pieces: list[str] = []
    pieces.extend(_compute_viewbox_and_root(geometry, opt))
    pieces.append(_embedded_style())
    # Buses paint under wires so a wire crossing a bus reads clearly.
    pieces.append(_render_buses(geometry.get("buses") or [], opt))
    pieces.append(_render_wires(geometry.get("wires") or [], opt))
    # Sheet symbols sit under components so any nets drawn on top remain
    # visible. They're a top-level construct (hierarchical sub-sheets),
    # not components.
    pieces.append(_render_sheet_symbols(
        geometry.get("sheet_symbols") or [], opt))
    pieces.append(_render_components(
        geometry.get("components") or [],
        geometry.get("pins") or [], opt))
    pieces.append(_render_junctions(
        geometry.get("junctions") or [], opt,
        wires=geometry.get("wires") or []))
    pieces.append(_render_net_labels(geometry.get("net_labels") or [], opt))
    pieces.append(_render_ports(geometry.get("ports") or [], opt))
    pieces.append(_render_power_ports(geometry.get("power_ports") or [], opt))
    if opt.flip_y:
        pieces.append("</g>")  # close the Y-flip wrapper opened in the root
    pieces.append("</svg>")
    return "".join(p for p in pieces if p)


# ---------------------------------------------------------------------------
# viewBox + root
# ---------------------------------------------------------------------------


def _compute_viewbox_and_root(geometry: dict[str, Any],
                              opt: SchRenderOptions) -> list[str]:
    minx, miny, maxx, maxy = _bbox(geometry)
    if minx == maxx or miny == maxy:
        # Degenerate: pad a 1000-mil square at origin so something renders.
        minx, miny, maxx, maxy = -500, -500, 500, 500
    minx -= opt.margin
    miny -= opt.margin
    maxx += opt.margin
    maxy += opt.margin
    width = maxx - minx
    height = maxy - miny
    if opt.flip_y:
        # Map (mils) -> (viewBox pixels) with Y flipped: outer transform.
        view = f"{minx} {-maxy} {width} {height}"
        bg_x, bg_y = minx, -maxy
    else:
        view = f"{minx} {miny} {width} {height}"
        bg_x, bg_y = minx, miny
    title = (geometry.get("doc") or "schematic")
    pieces: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}" '
        f'preserveAspectRatio="xMidYMid meet" '
        f'data-doc="{_xml(title)}">'
    ]
    if opt.title:
        pieces.append(f"<title>{_xml(opt.title)}</title>")
    else:
        pieces.append(f"<title>{_xml(title)}</title>")
    # Paper-style background: white fill + faint blue 100-mil grid.
    # The grid sits OUTSIDE the Y-flip so it stays aligned with the
    # viewBox regardless of the per-element transforms applied later.
    pieces.append(
        f'<defs>'
        f'<pattern id="sch-grid" x="0" y="0" width="100" height="100" '
        f'patternUnits="userSpaceOnUse">'
        f'<circle cx="0" cy="0" r="2" fill="#c8d4e8"/>'
        f'</pattern>'
        f'</defs>'
        f'<rect x="{bg_x}" y="{bg_y}" width="{width}" height="{height}" '
        f'fill="white"/>'
        f'<rect x="{bg_x}" y="{bg_y}" width="{width}" height="{height}" '
        f'fill="url(#sch-grid)"/>'
    )
    if opt.flip_y:
        # Wrap all content in a Y-flip so downstream code can keep working
        # in Altium-native coordinates.
        pieces.append('<g transform="scale(1,-1)">')
    return pieces


def _bbox(geometry: dict[str, Any]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for c in geometry.get("components") or []:
        bb = c.get("bbox") or {}
        for k in ("x1", "x2"):
            v = bb.get(k)
            if v is not None:
                xs.append(float(v))
        for k in ("y1", "y2"):
            v = bb.get(k)
            if v is not None:
                ys.append(float(v))
    for w in (geometry.get("wires") or []) + (geometry.get("buses") or []):
        for v in w.get("verts") or []:
            xs.append(float(v[0]))
            ys.append(float(v[1]))
    for kind in ("net_labels", "ports", "power_ports", "junctions", "pins"):
        for o in geometry.get(kind) or []:
            xs.append(float(o.get("x", 0)))
            ys.append(float(o.get("y", 0)))
    for s in geometry.get("sheet_symbols") or []:
        sx = float(s.get("x", 0))
        sy = float(s.get("y", 0))
        sw = float(s.get("w", 0))
        sh = float(s.get("h", 0))
        # Sheet symbol Location is the bottom-left corner in Altium.
        xs.extend((sx, sx + sw))
        ys.extend((sy, sy + sh))
    if not xs:
        return 0.0, 0.0, 0.0, 0.0
    return min(xs), min(ys), max(xs), max(ys)


# ---------------------------------------------------------------------------
# Embedded stylesheet -- one place for the look
# ---------------------------------------------------------------------------


def _embedded_style() -> str:
    return (
        "<style>"
        "  .wire { stroke: #1f6feb; stroke-linecap: round; stroke-linejoin: round; fill: none; }"
        "  .bus { stroke: #8b5cf6; stroke-linecap: round; stroke-linejoin: round; fill: none; }"
        "  .sheet-sym-body { fill: #f3e8ff; stroke: #6b21a8; stroke-linejoin: round; }"
        "  .sheet-sym-head { fill: #6b21a8; }"
        "  .sheet-sym-title { fill: #ffffff; font-family: 'JetBrains Mono', monospace; font-weight: 700; }"
        "  .sheet-sym-file { fill: #6b21a8; font-family: 'JetBrains Mono', monospace; font-style: italic; }"
        "  .sheet-entry { fill: #6b21a8; }"
        "  .sheet-entry-label { fill: #6b21a8; font-family: 'JetBrains Mono', monospace; }"
        "  .junction { fill: #1f6feb; }"
        "  .comp-body { fill: #fff7d6; stroke: #8b6914; stroke-linejoin: round; }"
        "  .comp-pin { stroke: #8b6914; stroke-linecap: round; fill: none; }"
        "  .pin-dot { fill: #8b6914; }"
        "  .pin-label, .pin-num { fill: #8b6914; font-family: 'JetBrains Mono', monospace; }"
        "  .pin-num { font-weight: 600; }"
        "  .designator { fill: #b91c1c; font-family: 'JetBrains Mono', monospace; font-weight: 700; }"
        "  .libref { fill: #6b7280; font-family: 'JetBrains Mono', monospace; }"
        "  .net-label { fill: #15803d; font-family: 'JetBrains Mono', monospace; }"
        "  .port-body { fill: #fee2e2; stroke: #b91c1c; }"
        "  .port-text { fill: #b91c1c; font-family: 'JetBrains Mono', monospace; }"
        "  .power-line { stroke: #b91c1c; }"
        "  .power-text { fill: #b91c1c; font-family: 'JetBrains Mono', monospace; }"
        "  .comp:hover .comp-body { stroke-width: 8; }"
        "  .wire:hover { stroke: #f59e0b; }"
        "</style>"
    )


# ---------------------------------------------------------------------------
# Geometry renderers
# ---------------------------------------------------------------------------


def _render_wires(wires: Iterable[dict[str, Any]],
                  opt: SchRenderOptions) -> str:
    out: list[str] = ['<g class="wires">']
    for w in wires:
        verts = w.get("verts") or []
        if len(verts) < 2:
            continue
        pts = " ".join(f"{v[0]},{v[1]}" for v in verts)
        out.append(
            f'<polyline class="wire" stroke-width="{opt.wire_stroke_mils}" '
            f'points="{pts}"/>'
        )
    out.append("</g>")
    return "".join(out)


def _render_buses(buses: Iterable[dict[str, Any]],
                  opt: SchRenderOptions) -> str:
    """Bus lines -- drawn thicker than wires to read as a separate
    construct. Same vertex API as wires.
    """
    out: list[str] = ['<g class="buses">']
    sw = opt.wire_stroke_mils * 2.0
    for b in buses:
        verts = b.get("verts") or []
        if len(verts) < 2:
            continue
        pts = " ".join(f"{v[0]},{v[1]}" for v in verts)
        out.append(
            f'<polyline class="bus" stroke-width="{sw}" points="{pts}"/>'
        )
    out.append("</g>")
    return "".join(out)


def _render_sheet_symbols(symbols: Iterable[dict[str, Any]],
                          opt: SchRenderOptions) -> str:
    """Hierarchical sheet symbols -- the labelled boxes on a top sheet
    that represent sub-sheets. Renders the body, a coloured header bar
    carrying the sheet name, the referenced .SchDoc filename below the
    body, and per-edge sheet-entry labels.

    The interactive ``data-*`` set is the same one components carry, so
    a dashboard can hover/click a sheet symbol to drill into its sub-
    sheet.
    """
    out: list[str] = ['<g class="sheet-symbols">']
    head_h = max(80.0, opt.designator_font_mils + 30.0)
    for s in symbols:
        x = float(s.get("x", 0))
        y = float(s.get("y", 0))
        w = float(s.get("w", 0))
        h = float(s.get("h", 0))
        if w <= 0 or h <= 0:
            continue
        name = str(s.get("name", "") or "")
        fname = str(s.get("filename", "") or "")
        # ISch_SheetSymbol.Location returns the TOP-LEFT corner in
        # Altium-native coords (+Y up), unlike most SCH objects where
        # Location is bottom-left. So the body spans:
        #   x range: [x, x + w]
        #   y range: [y - h, y]    -- extends DOWN from the location
        # Header bar sits at the TOP of the body (highest Y).
        body_top = y           # top edge in Altium-native coords
        body_bot = y - h       # bottom edge
        head_bot = y - head_h  # bottom of the header bar
        out.append(
            f'<g class="sheet-sym" data-sheet="{_xml(name)}" '
            f'data-filename="{_xml(fname)}">'
            f'<rect class="sheet-sym-body" x="{x}" y="{body_bot}" '
            f'width="{w}" height="{h}" rx="18" '
            f'stroke-width="{opt.component_stroke_mils}"/>'
            # Header bar across the top of the box (path so just the
            # top corners round, not the bottom of the header).
            f'<path class="sheet-sym-head" d="'
            f'M {x + 18} {head_bot} '
            f'L {x + w - 18} {head_bot} '
            f'Q {x + w} {head_bot} {x + w} {head_bot + 18} '
            f'L {x + w} {body_top - 18} '
            f'Q {x + w} {body_top} {x + w - 18} {body_top} '
            f'L {x + 18} {body_top} '
            f'Q {x} {body_top} {x} {body_top - 18} '
            f'L {x} {head_bot + 18} '
            f'Q {x} {head_bot} {x + 18} {head_bot} '
            f'Z"/>'
        )
        # Title (sheet name) inside the header bar.
        out.append(
            _flippable_text(
                x + w / 2, body_top - head_h / 2,
                f'class="sheet-sym-title" font-size="{opt.designator_font_mils}" '
                f'text-anchor="middle" dominant-baseline="middle"',
                name, opt,
            )
        )
        # Referenced .SchDoc filename below the body (below body_bot).
        out.append(
            _flippable_text(
                x + w / 2, body_bot - 40,
                f'class="sheet-sym-file" font-size="{opt.libref_font_mils}" '
                f'text-anchor="middle"',
                fname, opt,
            )
        )
        # Sheet entries -- IO stubs on the edges. Their coordinates are
        # already in world space (post-placement of the parent symbol).
        for ent in s.get("entries") or []:
            ex = float(ent.get("x", 0))
            ey = float(ent.get("y", 0))
            ename = str(ent.get("name", "") or "")
            iotype = int(ent.get("iotype", 0) or 0)
            # Choose a text anchor by which edge the entry sits on. We
            # use the entry's position relative to the symbol bbox to
            # pick a side, which works regardless of how Side is encoded.
            on_left = abs(ex - x) < abs(ex - (x + w))
            anchor = "start" if on_left else "end"
            label_dx = 14 if on_left else -14
            out.append(
                f'<g class="sheet-entry" data-name="{_xml(ename)}" '
                f'data-iotype="{iotype}">'
                f'<circle class="sheet-entry" cx="{ex}" cy="{ey}" r="8"/>'
                + _flippable_text(
                    ex + label_dx, ey,
                    f'class="sheet-entry-label" font-size="{opt.pin_font_mils}" '
                    f'text-anchor="{anchor}" dominant-baseline="middle"',
                    ename, opt,
                )
                + "</g>"
            )
        out.append("</g>")
    out.append("</g>")
    return "".join(out)


def _render_components(components: Iterable[dict[str, Any]],
                       pins: Iterable[dict[str, Any]],
                       opt: SchRenderOptions) -> str:
    # Bucket pins by their owning component so we draw stubs inside the
    # component's <g>. data-* attributes are kept on every element so a
    # downstream consumer (dashboard, agent) can wire interaction.
    by_comp: dict[str, list[dict[str, Any]]] = {}
    for p in pins:
        by_comp.setdefault(p.get("comp", ""), []).append(p)

    out: list[str] = ['<g class="components">']
    for c in components:
        des = c.get("des", "")
        lib = c.get("lib_ref", "")
        bb = c.get("bbox") or {}
        x1 = float(bb.get("x1", c.get("x", 0)))
        y1 = float(bb.get("y1", c.get("y", 0)))
        x2 = float(bb.get("x2", c.get("x", 0)))
        y2 = float(bb.get("y2", c.get("y", 0)))
        body_w = max(1.0, x2 - x1)
        body_h = max(1.0, y2 - y1)

        out.append(
            f'<g class="comp" data-designator="{_xml(des)}" '
            f'data-lib-ref="{_xml(lib)}">'
        )
        # Invisible bounding-box hit target. Without it, mouse hover
        # only fires on the actual painted geometry (lines, fills) -- so
        # blank space inside a symbol body, gaps between pins, etc. all
        # stay inert and the highlight feels twitchy. A transparent
        # rect at the top of the group (drawn FIRST so primitives paint
        # on top) gives the entire bbox a single hit region. The
        # ``pointer-events`` value is critical: ``fill`` accepts events
        # on the filled area even when fill is "transparent"; without
        # it some browsers ignore the rect.
        # Padded slightly so pin labels at the edges still trigger
        # the comp hover (pins extend a bit past the body bbox).
        pad = max(40.0, opt.pin_stroke_mils * 4)
        out.append(
            f'<rect class="comp-hit" '
            f'x="{x1 - pad}" y="{y1 - pad}" '
            f'width="{body_w + 2 * pad}" height="{body_h + 2 * pad}" '
            f'fill="transparent" stroke="none" '
            f'pointer-events="fill"/>'
        )
        # v2: if the component carries symbol-internal primitives, draw
        # them as the body. The bounding-box rectangle is only a fallback
        # for symbols that didn't return any primitives (e.g. power
        # ports, sheet symbols).
        prims = c.get("primitives") or []
        if prims:
            for p in prims:
                out.append(_render_primitive(p, opt))
        else:
            out.append(
                f'<rect class="comp-body" x="{x1}" y="{y1}" '
                f'width="{body_w}" height="{body_h}" '
                f'stroke-width="{opt.component_stroke_mils}" rx="20"/>'
            )

        # Pin stubs: Pin.Location is the BODY-side root of the pin (where
        # it meets the symbol body); the electrical end is one pin_length
        # OUT along the orientation vector. The renderer earlier assumed
        # the opposite, which drew the whole pin (and its label) shifted
        # by one length toward the body. Empirically verified on a placed
        # SchDoc component -- the design-executor memory note about
        # "Pin.Location is the electrical end" appears to apply only to
        # one specific API path (`Gen_GetSchComponentPins`), not to
        # SchIterator+ePin on a SchDoc component, which is what this
        # renderer reads from.
        comp_pins = by_comp.get(des) or []
        for p in comp_pins:
            bx = float(p.get("x", 0))           # body-side root
            by = float(p.get("y", 0))
            rot = int(p.get("rot", 0)) % 360
            length = float(p.get("len", 0) or 0)
            dx, dy = _PIN_DIR.get(rot, (1, 0))
            px = bx + dx * length               # electrical end
            py = by + dy * length
            electrical = str(p.get("electrical", "") or "")
            out.append(
                f'<g class="pin" data-pin="{_xml(str(p.get("des", "")))}" '
                f'data-name="{_xml(str(p.get("name", "")))}" '
                f'data-electrical="{_xml(electrical)}">'
                f'<line class="comp-pin" '
                f'stroke-width="{opt.pin_stroke_mils}" '
                f'x1="{bx}" y1="{by}" x2="{px}" y2="{py}"/>'
                f'<circle class="pin-dot" cx="{px}" cy="{py}" '
                f'r="{opt.pin_dot_radius_mils}"/>'
            )
            # Electrical-type glyph (input arrow, OC bubble, etc.)
            if electrical:
                out.append(_pin_glyph(px, py, dx, dy, electrical, opt))
            # Pin label sits just inside the body, offset opposite to the
            # pin direction. bx/by is the body-side root of the pin stub,
            # so stepping by -(dx,dy)*10 walks 10 mils INTO the body --
            # the previous +dx*10 version walked OUT onto the stub, which
            # is where the pin number goes, not the pin name.
            label_x = bx - dx * 10
            label_y = by - dy * 10
            anchor = _pin_text_anchor(rot)
            out.append(
                _flippable_text(
                    label_x, label_y,
                    f'class="pin-label" font-size="{opt.pin_font_mils}" '
                    f'text-anchor="{anchor}"',
                    p.get("name", ""), opt,
                )
            )
            # Pin number, half-step further inside.
            num_x = bx + dx * (length * 0.45 + 10)
            num_y = by + dy * (length * 0.45 + 10)
            out.append(
                _flippable_text(
                    num_x, num_y,
                    f'class="pin-num" font-size="{opt.pin_font_mils}" '
                    f'text-anchor="middle"',
                    p.get("des", ""), opt,
                )
            )
            out.append("</g>")

        # Symbol-internal parameter text (custom annotations the symbol
        # author placed inside the body -- e.g. "Vout = 3.3 V", "MAX 24
        # mA"). Hidden ones and the special Designator/Comment are
        # filtered out in Pascal.
        for prm in c.get("params") or []:
            ptext = str(prm.get("text", "") or "")
            if not ptext:
                continue
            pcolor = _altium_color(prm.get("color"), "#6b7280")
            out.append(
                _flippable_text(
                    float(prm.get("x", 0)), float(prm.get("y", 0)),
                    f'class="param" font-size="{opt.libref_font_mils}" '
                    f'text-anchor="start" '
                    f'data-param="{_xml(prm.get("name", ""))}" '
                    f'fill="{pcolor}"',
                    ptext, opt,
                )
            )

        # Designator above, lib_ref below.
        cx = (x1 + x2) / 2.0
        out.append(
            _flippable_text(
                cx, y2 + 30,
                f'class="designator" font-size="{opt.designator_font_mils}" '
                f'text-anchor="middle"',
                des, opt,
            )
        )
        out.append(
            _flippable_text(
                cx, y1 - 30 - opt.libref_font_mils,
                f'class="libref" font-size="{opt.libref_font_mils}" '
                f'text-anchor="middle"',
                lib, opt,
            )
        )
        out.append("</g>")

    out.append("</g>")
    return "".join(out)


def _collect_auto_junctions(wires: Iterable[dict[str, Any]],
                             explicit: Iterable[dict[str, Any]]
                             ) -> list[dict[str, float]]:
    """Combine explicit eJunction objects with auto-detected T-junctions.

    Altium doesn't always materialise an eJunction object where wires
    meet: sometimes a T-junction is implicit (depends on the project's
    "place junctions automatically" setting and whether the doc was
    last hand-routed vs. auto-routed). To make the SVG match what the
    user sees in Altium, we recompute junctions from the wire graph:

      1) Wire endpoints with multiplicity >= 3 (multiway joins).
      2) Wire endpoints that land in the interior of another wire's
         segment (classic T-junction: A passes straight through, B
         ends in the middle of A).

    Axis-aligned only (schematics are orthogonal essentially always).
    """
    seen: set[tuple[float, float]] = set()
    out: list[dict[str, float]] = []
    TOL = 0.5

    def push(x: float, y: float) -> None:
        key = (round(float(x), 1), round(float(y), 1))
        if key in seen:
            return
        seen.add(key)
        out.append({"x": key[0], "y": key[1]})

    for j in explicit:
        push(j.get("x", 0), j.get("y", 0))

    wires_list = list(wires)

    endpoint_count: dict[tuple[float, float], int] = {}
    for w in wires_list:
        verts = w.get("verts") or []
        if len(verts) < 2:
            continue
        for v in (verts[0], verts[-1]):
            key = (round(float(v[0]), 1), round(float(v[1]), 1))
            endpoint_count[key] = endpoint_count.get(key, 0) + 1
    for (kx, ky), n in endpoint_count.items():
        if n >= 3:
            push(kx, ky)

    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for w in wires_list:
        verts = w.get("verts") or []
        for i in range(len(verts) - 1):
            segments.append((
                (float(verts[i][0]), float(verts[i][1])),
                (float(verts[i+1][0]), float(verts[i+1][1])),
            ))

    def on_interior(px: float, py: float,
                    ax: float, ay: float, bx: float, by: float) -> bool:
        if abs(ax - bx) < TOL:                 # vertical segment
            if abs(px - ax) > TOL:
                return False
            lo, hi = (ay, by) if ay < by else (by, ay)
            return lo + TOL < py < hi - TOL
        if abs(ay - by) < TOL:                 # horizontal segment
            if abs(py - ay) > TOL:
                return False
            lo, hi = (ax, bx) if ax < bx else (bx, ax)
            return lo + TOL < px < hi - TOL
        return False

    endpoints: list[tuple[float, float]] = []
    for w in wires_list:
        verts = w.get("verts") or []
        if len(verts) < 2:
            continue
        endpoints.append((float(verts[0][0]), float(verts[0][1])))
        endpoints.append((float(verts[-1][0]), float(verts[-1][1])))

    for px, py in endpoints:
        for (ax, ay), (bx, by) in segments:
            if (abs(px - ax) < TOL and abs(py - ay) < TOL) \
               or (abs(px - bx) < TOL and abs(py - by) < TOL):
                continue
            if on_interior(px, py, ax, ay, bx, by):
                push(px, py)
                break

    return out


def _render_junctions(junctions: Iterable[dict[str, Any]],
                      opt: SchRenderOptions,
                      wires: Iterable[dict[str, Any]] | None = None) -> str:
    out: list[str] = ['<g class="junctions">']
    if wires is None:
        # Backwards compat: render only the explicit ones.
        merged = list(junctions)
    else:
        merged = _collect_auto_junctions(wires, junctions)
    for j in merged:
        x = j.get("x", 0)
        y = j.get("y", 0)
        out.append(
            f'<circle class="junction" cx="{x}" cy="{y}" '
            f'r="{opt.junction_radius_mils}"/>'
        )
    out.append("</g>")
    return "".join(out)


def _render_net_labels(labels: Iterable[dict[str, Any]],
                       opt: SchRenderOptions) -> str:
    out: list[str] = ['<g class="net-labels">']
    for n in labels:
        text = n.get("text", "")
        x = float(n.get("x", 0))
        y = float(n.get("y", 0))
        out.append(
            f'<g class="net-label-group" data-net="{_xml(text)}">'
            + _flippable_text(
                x, y,
                f'class="net-label" font-size="{opt.label_font_mils}" '
                f'text-anchor="start"',
                text, opt,
            )
            + "</g>"
        )
    out.append("</g>")
    return "".join(out)


def _render_ports(ports: Iterable[dict[str, Any]],
                  opt: SchRenderOptions) -> str:
    out: list[str] = ['<g class="ports">']
    for p in ports:
        text = p.get("text", "")
        x = float(p.get("x", 0))
        y = float(p.get("y", 0))
        w = float(p.get("w", 600))
        h = float(opt.label_font_mils + 30)
        iotype = int(p.get("iotype", 0))
        # Simple arrow-ish port shape derived from IOType. v1 keeps all
        # ports as a coloured pill; arrow geometry per IOType is a polish
        # pass.
        out.append(
            f'<g class="port" data-net="{_xml(text)}" data-iotype="{iotype}">'
            f'<rect class="port-body" x="{x}" y="{y - h / 2}" '
            f'width="{w}" height="{h}" rx="20"/>'
            + _flippable_text(
                x + w / 2, y,
                f'class="port-text" font-size="{opt.label_font_mils}" '
                f'text-anchor="middle" dominant-baseline="middle"',
                text, opt,
            )
            + "</g>"
        )
    out.append("</g>")
    return "".join(out)


def _render_power_ports(power_ports: Iterable[dict[str, Any]],
                        opt: SchRenderOptions) -> str:
    out: list[str] = ['<g class="power-ports">']
    for p in power_ports:
        text = p.get("text", "")
        x = float(p.get("x", 0))
        y = float(p.get("y", 0))
        rot = int(p.get("rot", 90)) % 360       # 90 = VCC-up default
        style = int(p.get("style", 2))          # 2 = Bar default
        out.append(_power_glyph(x, y, rot, style, text, opt))
    out.append("</g>")
    return "".join(out)


# Altium ISch_PowerObject.Style enum -> glyph kind. Keys come straight
# from the Pascal handler (no remap). Ground variants extend AWAY from
# the connection point regardless of the Orientation field -- that's
# the convention even though Altium stores GND with Orientation=270.
_POWER_STYLE_BAR = 0       # Circle (rare, drawn as small circle)
# Common: 1=Arrow, 2=Bar, 3=Wave, 4=PowerGround, 5=SignalGround, 6=Earth,
# 7..11 are GOST variants we treat the same as their non-GOST cousins.
_GROUND_STYLES = {4, 5, 6, 9, 10, 11}


def _power_glyph(x: float, y: float, rot: int, style: int, text: str,
                 opt: SchRenderOptions) -> str:
    """Render one Altium power-port symbol respecting Orientation + Style.

    The connection point is at ``(x, y)``. The stub extends along the
    orientation vector, then the style-specific glyph caps the stub at
    its tip. For ground styles (PowerGround / SignalGround / Earth) the
    glyph is always a stack of horizontal-ish bars perpendicular to the
    stub -- the convention regardless of orientation -- so a GND symbol
    rotated 0 deg still reads as ground rather than arrow.
    """
    # Unit vector pointing FROM the connection point OUT to the glyph tip.
    # rot is in degrees in Altium-native (+Y = up).
    dirs = {0: (1.0, 0.0), 90: (0.0, 1.0),
            180: (-1.0, 0.0), 270: (0.0, -1.0)}
    dx, dy = dirs.get(rot % 360, (0.0, 1.0))
    # Perpendicular, used for drawing the bar(s).
    nx, ny = -dy, dx

    stub_len = 30
    tip_x = x + dx * stub_len
    tip_y = y + dy * stub_len

    sw = opt.wire_stroke_mils
    parts = [
        f'<g class="power-port" data-net="{_xml(text)}" '
        f'data-style="{style}" data-rot="{rot}">',
        # Stub from connection to glyph tip.
        f'<line class="power-line" stroke-width="{sw}" '
        f'x1="{x}" y1="{y}" x2="{tip_x}" y2="{tip_y}"/>',
    ]

    text_offset = 60   # default for non-ground glyphs

    if style in _GROUND_STYLES:
        # Stacked horizontal bars decreasing in width -- the universal
        # ground symbol. Earth (6) adds a third bar; SignalGround (5)
        # uses a solid downward triangle; PowerGround (4) and others
        # use the classic 3-bar form. We approximate Style 5 with a
        # triangle and the rest with 3 bars.
        if style == 5:
            # Triangle pointing along (dx, dy) from the tip.
            ax, ay = tip_x, tip_y
            bx, by = tip_x + nx * 50 + dx * 80, tip_y + ny * 50 + dy * 80
            cx, cy = tip_x - nx * 50 + dx * 80, tip_y - ny * 50 + dy * 80
            parts.append(
                f'<polygon class="power-line" '
                f'points="{ax},{ay} {bx},{by} {cx},{cy}" '
                f'stroke-width="{sw}" fill="currentColor" '
                f'fill-opacity="0.45"/>'
            )
            text_offset = 110
        else:
            bar_count = 3 if style == 6 else 3
            half_widths = [60, 40, 22] if bar_count == 3 else [60, 40]
            spacing = 22
            for i, hw in enumerate(half_widths):
                step = i * spacing
                cx = tip_x + dx * step
                cy = tip_y + dy * step
                x1 = cx + nx * hw
                y1 = cy + ny * hw
                x2 = cx - nx * hw
                y2 = cy - ny * hw
                parts.append(
                    f'<line class="power-line" stroke-width="{sw}" '
                    f'x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"/>'
                )
            text_offset = stub_len + spacing * len(half_widths) + 30
    elif style == 0:
        # Circle: small ring at the tip.
        parts.append(
            f'<circle class="power-line" cx="{tip_x}" cy="{tip_y}" '
            f'r="22" stroke-width="{sw}" fill="none"/>'
        )
        text_offset = stub_len + 80
    elif style == 1:
        # Arrow: triangle pointing OUT from the tip.
        ax = tip_x + dx * 50
        ay = tip_y + dy * 50
        bx_ = tip_x + nx * 25
        by_ = tip_y + ny * 25
        cx = tip_x - nx * 25
        cy = tip_y - ny * 25
        parts.append(
            f'<polygon class="power-line" '
            f'points="{ax},{ay} {bx_},{by_} {cx},{cy}" '
            f'stroke-width="{sw}" fill="currentColor" fill-opacity="0.45"/>'
        )
        text_offset = stub_len + 90
    elif style == 3:
        # Wave: a short sinusoid perpendicular to the stub at the tip.
        # Approximate with two arcs.
        a = 24
        parts.append(
            f'<path class="power-line" fill="none" stroke-width="{sw}" '
            f'd="M {tip_x + nx * a} {tip_y + ny * a} '
            f'q {dx * a} {dy * a} {nx * -a + dx * a} {ny * -a + dy * a} '
            f't {nx * -a + dx * a} {ny * -a + dy * a}"/>'
        )
        text_offset = stub_len + 80
    else:
        # Bar (default) + anything unknown: short perpendicular bar at the
        # tip, matching the prior behaviour for VCC-style symbols.
        parts.append(
            f'<line class="power-line" stroke-width="{sw}" '
            f'x1="{tip_x + nx * 40}" y1="{tip_y + ny * 40}" '
            f'x2="{tip_x - nx * 40}" y2="{tip_y - ny * 40}"/>'
        )
        text_offset = stub_len + 30

    # Label OUTSIDE the glyph along the orientation vector. For ground
    # styles, that puts "GND" below the stacked bars (since rot=270 has
    # dy = -1, y - text_offset is below in screen space).
    label_x = x + dx * text_offset
    label_y = y + dy * text_offset
    parts.append(
        _flippable_text(
            label_x, label_y,
            f'class="power-text" font-size="{opt.label_font_mils}" '
            f'text-anchor="middle"',
            text, opt,
        )
    )
    parts.append("</g>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _xml(s: Any) -> str:
    return html.escape(str(s or ""), quote=True)


def _pin_text_anchor(rot: int) -> str:
    # Pin rotation 0 = pin extends right of body -> label sits to the LEFT
    # of the body end -> text-anchor "end". Mirror for other rotations.
    if rot == 0:
        return "end"
    if rot == 180:
        return "start"
    return "middle"


def _render_primitive(p: dict[str, Any], opt: SchRenderOptions) -> str:
    """Render one symbol-internal primitive (rect, line, arc, polyline,
    polygon, ellipse, roundrect) to its SVG fragment.

    Coordinates are world-space mils (Altium iterates a placed
    component's child primitives in world coords). Colors come back as
    Altium BGR-packed integers; line widths as the SCH enum.
    """
    kind = p.get("kind")
    stroke = _altium_color(p.get("color"), "#8b6914")
    sw = _line_width(p, default_mils=opt.component_stroke_mils)

    def _fill(prim: dict[str, Any]) -> str:
        if prim.get("is_solid") and prim.get("area_color") is not None:
            return _altium_color(prim["area_color"], "#fff7d6")
        return "none"

    if kind in ("rect", "roundrect"):
        x1 = float(p.get("x1", 0))
        y1 = float(p.get("y1", 0))
        x2 = float(p.get("x2", 0))
        y2 = float(p.get("y2", 0))
        x = min(x1, x2)
        y = min(y1, y2)
        w = abs(x2 - x1)
        h = abs(y2 - y1)
        rx_attr = ""
        if kind == "roundrect":
            rx = float(p.get("rx", 0))
            ry = float(p.get("ry", 0))
            rx_attr = f' rx="{rx}" ry="{ry}"'
        return (
            f'<rect class="prim prim-rect" x="{x}" y="{y}" '
            f'width="{w}" height="{h}"{rx_attr} '
            f'stroke="{stroke}" fill="{_fill(p)}" stroke-width="{sw}"/>'
        )

    if kind == "line":
        return (
            f'<line class="prim prim-line" '
            f'x1="{p.get("x1", 0)}" y1="{p.get("y1", 0)}" '
            f'x2="{p.get("x2", 0)}" y2="{p.get("y2", 0)}" '
            f'stroke="{stroke}" stroke-width="{sw}" stroke-linecap="round"/>'
        )

    if kind == "arc":
        # Altium reports an arc as center + radius (or radius+secondary
        # for elliptical) + start/end angle in degrees. Map to an SVG
        # arc path. Y-axis: Altium is bottom-left-origin; the outer
        # wrapper already flips Y, so we draw in Altium-native space
        # (counter-clockwise positive angles).
        cx = float(p.get("cx", 0))
        cy = float(p.get("cy", 0))
        r = float(p.get("r", 0) or 0)
        r2 = float(p.get("r2", 0) or r)
        sa = float(p.get("start", 0) or 0)
        ea = float(p.get("end", 0) or 0)
        if r <= 0:
            return ""
        # Normalize end >= start.
        if ea < sa:
            ea += 360.0
        sweep = ea - sa
        full = abs(sweep - 360.0) < 0.01 or sweep <= 0.01
        if full:
            return (
                f'<ellipse class="prim prim-arc" cx="{cx}" cy="{cy}" '
                f'rx="{r}" ry="{r2}" stroke="{stroke}" fill="none" '
                f'stroke-width="{sw}"/>'
            )
        sa_r = math.radians(sa)
        ea_r = math.radians(ea)
        sx = cx + r * math.cos(sa_r)
        sy = cy + r2 * math.sin(sa_r)
        ex = cx + r * math.cos(ea_r)
        ey = cy + r2 * math.sin(ea_r)
        large = 1 if sweep > 180.0 else 0
        # Outer transform flips Y -> SVG sweep semantics reverse:
        # Altium CCW corresponds to SVG sweep-flag=1 in the flipped frame.
        sweep_flag = 1
        return (
            f'<path class="prim prim-arc" d="M {sx} {sy} '
            f'A {r} {r2} 0 {large} {sweep_flag} {ex} {ey}" '
            f'stroke="{stroke}" fill="none" stroke-width="{sw}"/>'
        )

    if kind in ("polyline", "polygon"):
        pts = " ".join(f"{v[0]},{v[1]}" for v in (p.get("pts") or []))
        if not pts:
            return ""
        if kind == "polygon":
            return (
                f'<polygon class="prim prim-polygon" points="{pts}" '
                f'stroke="{stroke}" fill="{_fill(p)}" '
                f'stroke-width="{sw}" stroke-linejoin="round"/>'
            )
        return (
            f'<polyline class="prim prim-polyline" points="{pts}" '
            f'stroke="{stroke}" fill="none" stroke-width="{sw}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>'
        )

    if kind == "ellipse":
        return (
            f'<ellipse class="prim prim-ellipse" '
            f'cx="{p.get("cx", 0)}" cy="{p.get("cy", 0)}" '
            f'rx="{p.get("rx", 0)}" ry="{p.get("ry", 0)}" '
            f'stroke="{stroke}" fill="{_fill(p)}" stroke-width="{sw}"/>'
        )

    if kind == "bezier":
        pts = p.get("pts") or []
        if len(pts) < 4:
            return ""
        d = (
            f"M {pts[0][0]} {pts[0][1]} "
            f"C {pts[1][0]} {pts[1][1]} "
            f"{pts[2][0]} {pts[2][1]} "
            f"{pts[3][0]} {pts[3][1]}"
        )
        return (
            f'<path class="prim prim-bezier" d="{d}" '
            f'stroke="{stroke}" fill="none" stroke-width="{sw}" '
            f'stroke-linecap="round"/>'
        )

    return ""


def _pin_glyph(px: float, py: float, dx: int, dy: int,
               electrical: str, opt: SchRenderOptions) -> str:
    """Emit a small SVG fragment for the pin's electrical-type indicator.

    Conventions follow IEC/IEEE schematic styling:
      - input  -> triangle pointing TOWARD the body (tip body-side of pin)
      - output -> triangle pointing AWAY from the body
      - io     -> diamond at the electrical end
      - open_collector / open_emitter -> small bubble (open circle)
      - hiz, passive, power -> no glyph
    """
    if electrical in ("passive", "power", ""):
        return ""
    # Perpendicular vector (rotated +90 CCW): perp = (-dy, dx).
    perp_x, perp_y = -dy, dx
    if electrical in ("open_collector", "open_emitter"):
        r = 15.0
        # Bubble sits just outside the body, between body and the
        # electrical end, with its centre offset away from the body.
        cx = px - dx * r
        cy = py - dy * r
        return (
            f'<circle class="pin-glyph pin-glyph-{electrical}" '
            f'cx="{cx}" cy="{cy}" r="{r}" '
            f'fill="white" stroke="#8b6914" stroke-width="2"/>'
        )
    size = 35.0
    hw = 18.0
    if electrical == "input":
        # Tip toward the body; base wide at the electrical end.
        tip_x = px - dx * size
        tip_y = py - dy * size
        b1_x, b1_y = px + perp_x * hw, py + perp_y * hw
        b2_x, b2_y = px - perp_x * hw, py - perp_y * hw
        points = f"{tip_x},{tip_y} {b1_x},{b1_y} {b2_x},{b2_y}"
        return (
            f'<polygon class="pin-glyph pin-glyph-input" '
            f'points="{points}" fill="#8b6914"/>'
        )
    if electrical == "output":
        tip_x = px + dx * size
        tip_y = py + dy * size
        b1_x, b1_y = px + perp_x * hw, py + perp_y * hw
        b2_x, b2_y = px - perp_x * hw, py - perp_y * hw
        points = f"{tip_x},{tip_y} {b1_x},{b1_y} {b2_x},{b2_y}"
        return (
            f'<polygon class="pin-glyph pin-glyph-output" '
            f'points="{points}" fill="#8b6914"/>'
        )
    if electrical == "io":
        # Diamond at the electrical end: 4 vertices at +/- dx and +/- perp.
        d = size * 0.7
        pts = [
            (px + dx * d, py + dy * d),
            (px + perp_x * d, py + perp_y * d),
            (px - dx * d, py - dy * d),
            (px - perp_x * d, py - perp_y * d),
        ]
        points = " ".join(f"{p[0]},{p[1]}" for p in pts)
        return (
            f'<polygon class="pin-glyph pin-glyph-io" '
            f'points="{points}" fill="none" stroke="#8b6914" '
            f'stroke-width="2"/>'
        )
    if electrical == "hiz":
        # Smaller outline triangle pointing away from body.
        tip_x = px + dx * size * 0.7
        tip_y = py + dy * size * 0.7
        b1_x, b1_y = px + perp_x * (hw * 0.7), py + perp_y * (hw * 0.7)
        b2_x, b2_y = px - perp_x * (hw * 0.7), py - perp_y * (hw * 0.7)
        points = f"{tip_x},{tip_y} {b1_x},{b1_y} {b2_x},{b2_y}"
        return (
            f'<polygon class="pin-glyph pin-glyph-hiz" '
            f'points="{points}" fill="none" stroke="#8b6914" '
            f'stroke-width="2"/>'
        )
    return ""


def _flippable_text(x: float, y: float, attrs: str, text: Any,
                    opt: SchRenderOptions) -> str:
    """Emit a <text> that reads upright even when we've flipped Y.

    With ``transform="scale(1,-1)"`` wrapping the whole canvas, text would
    appear upside-down. We re-flip the Y axis locally for each text node
    by translating to (x,y) then applying scale(1,-1).
    """
    safe = _xml(text)
    if opt.flip_y:
        return (
            f'<g transform="translate({x},{y}) scale(1,-1)">'
            f'<text {attrs} x="0" y="0">{safe}</text>'
            f'</g>'
        )
    return f'<text {attrs} x="{x}" y="{y}">{safe}</text>'
