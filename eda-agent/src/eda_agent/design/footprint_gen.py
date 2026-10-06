# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Parametric standard-footprint geometry (pure Python, offline).

Authoring a footprint pad-by-pad is slow and error-prone. This computes the
full geometry of a standard SMD footprint family -- every pad plus the
silkscreen outline and the courtyard rectangle -- from the REAL package
dimensions the user supplies (pitch, pad size, row span, pin count). The
result is plain data: a tool then emits it via the bulk authoring tools
(`lib_add_footprint_pads`, `lib_add_footprint_tracks`) in two IPC round-trips.

It carries NO hardcoded part library -- the caller passes the dimensions from
the datasheet's recommended land pattern, so the output is the real footprint,
not a placeholder. Seven families cover the bulk of parts:

* ``chip`` -- 2-terminal passives (0402/0603/0805/1206/...): two pads on the X
  axis, ``pitch`` apart centre-to-centre.
* ``sip`` -- a single in-line row (single-row headers, SIP resistor networks):
  ``pin_count`` pads stepped by ``pitch`` along one axis.
* ``dual`` -- two opposing rows (SOIC/SOP/SON/SOT23/DIP): ``pin_count`` pins
  split evenly, rows ``row_span`` apart on X, pins stepped by ``pitch`` on Y,
  counterclockwise numbering (left column top->bottom, right column
  bottom->top).
* ``header`` -- a 2-row pin/box header (IDC, SWD/JTAG): two parallel rows
  ``row_span`` apart, ``pitch`` along each row.
* ``tab`` -- a power package with a thermal/mounting tab (SOT-223/DPAK/TO-220):
  the lead pads plus a large tab pad sized by ``tab_w`` / ``tab_h``.
* ``quad`` -- four sides (QFP/QFN): ``pin_count`` split across four sides,
  counterclockwise from the top-left, pin 1 at the top of the left side.
* ``bga`` -- a ball grid array: a row/column matrix of round pads on ``pitch``.

All lengths are mils. Pad centres are offsets from the footprint origin (0,0)
at rotation 0. Pin 1 is bottom-left-ish per Altium/IPC convention; the silk
pin-1 marker is a short tick by pin 1.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FootprintGeometry:
    """Computed footprint: pads + silk + courtyard, ready to emit in bulk."""

    pads: tuple[dict, ...]            # {designator,x,y,x_size,y_size,shape}
    silk_tracks: tuple[dict, ...]     # {x1,y1,x2,y2,width,layer}
    courtyard_tracks: tuple[dict, ...]  # {x1,y1,x2,y2,width,layer}
    width_mils: float                 # overall extent (pad-to-pad)
    height_mils: float

    def all_tracks(self) -> tuple[dict, ...]:
        return self.silk_tracks + self.courtyard_tracks


_SILK_W = 6
_CY_W = 4
_CY_LAYER = "Mechanical1"


def _rect_tracks(x1, y1, x2, y2, width, layer):
    """Four tracks forming the rectangle (x1,y1)-(x2,y2)."""
    return [
        {"x1": x1, "y1": y1, "x2": x2, "y2": y1, "width": width, "layer": layer},
        {"x1": x2, "y1": y1, "x2": x2, "y2": y2, "width": width, "layer": layer},
        {"x1": x2, "y1": y2, "x2": x1, "y2": y2, "width": width, "layer": layer},
        {"x1": x1, "y1": y2, "x2": x1, "y2": y1, "width": width, "layer": layer},
    ]


def generate_footprint(
    family: str,
    pin_count: int,
    *,
    pitch: float = 50.0,
    pad_w: float = 24.0,
    pad_h: float = 30.0,
    row_span: float = 0.0,
    shape: str = "roundrect",
    silk: bool = True,
    courtyard: float = 10.0,
    body_w: float = 0.0,
    body_h: float = 0.0,
    hole: float = 0.0,
    rows: int = 0,
    cols: int = 0,
    exposed_pad: float = 0.0,
    skip: list = None,
    tab_w: float = 0.0,
    tab_h: float = 0.0,
) -> FootprintGeometry:
    """Compute a standard SMD footprint (see the module docstring).

    Args:
        family: "chip" | "sip" | "dual" | "header" | "tab" | "quad" | "bga".
        pin_count: total pads (2 for chip).
        pitch: centre-to-centre within a row (mils).
        pad_w, pad_h: pad size (mils).
        row_span: centre-to-centre between the two opposing rows / sides
            (mils). Required for dual / quad. For chip it is derived from
            ``pitch`` (the two pads sit at +-pitch/2).
        shape: pad shape passed straight to the pad tool (default
            "roundrect").
        silk: emit a silkscreen body outline.
        courtyard: extra clearance (mils) for the courtyard rectangle past
            the pad/body extent; 0 disables the courtyard.
        body_w, body_h: package body for the silk outline; defaults to the
            pad extent when 0.

    Returns:
        A :class:`FootprintGeometry`.
    """
    fam = family.strip().lower()
    if fam != "bga" and pin_count < 1:
        raise ValueError("pin_count must be >= 1 (bga uses rows*cols)")
    # Geometry sanity: a zero / negative pad or pitch silently produces a
    # broken footprint (a 0-width pad, mirrored spacing) that would reach
    # Altium. Reject it here with a clear message.
    if pitch <= 0:
        raise ValueError(f"pitch must be > 0 (got {pitch})")
    if pad_w <= 0:
        raise ValueError(f"pad_w must be > 0 (got {pad_w})")
    if fam != "bga" and pad_h <= 0:
        raise ValueError(f"pad_h must be > 0 (got {pad_h})")
    if hole < 0:
        raise ValueError(f"hole must be >= 0 (0 = SMD pad; got {hole})")
    if hole > 0:
        pad_min = pad_w if fam == "bga" else min(pad_w, pad_h)
        if hole >= pad_min:
            raise ValueError(
                f"hole ({hole}) must be smaller than the pad ({pad_min}) to "
                f"leave a copper annular ring")
    if exposed_pad < 0:
        raise ValueError(f"exposed_pad must be >= 0 (got {exposed_pad})")
    if fam == "chip":
        return _chip(pad_w, pad_h, pitch, shape, silk, courtyard,
                     body_w, body_h, hole)
    if fam == "sip":
        return _sip(pin_count, pitch, pad_w, pad_h, shape, silk, courtyard,
                    body_w, body_h, hole)
    if fam == "dual":
        return _dual(pin_count, pitch, pad_w, pad_h, row_span, shape,
                     silk, courtyard, body_w, body_h, hole)
    if fam == "header":
        return _header(pin_count, pitch, pad_w, pad_h, row_span, shape,
                       silk, courtyard, body_w, body_h, hole)
    if fam == "tab":
        return _tab(pin_count, pitch, pad_w, pad_h, row_span, shape,
                    silk, courtyard, body_w, body_h, hole, tab_w, tab_h)
    if fam == "quad":
        return _quad(pin_count, pitch, pad_w, pad_h, row_span, shape,
                     silk, courtyard, body_w, body_h, hole, exposed_pad)
    if fam == "bga":
        return _bga(rows, cols, pitch, pad_w, silk, courtyard, body_w, body_h,
                    skip)
    raise ValueError(
        f"unknown family {family!r}; use chip / sip / dual / header / "
        f"tab / quad / bga")


# JEDEC ball-row letters: A..Y skipping I, O, Q, S, X, Z; then AA, AB, ...
_BGA_SKIP = set("IOQSXZ")
_BGA_LETTERS = [c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c not in _BGA_SKIP]


def _bga_row_label(i: int) -> str:
    """Row label for 0-based row index (A, B, ..., Y, AA, AB, ...)."""
    n = len(_BGA_LETTERS)
    if i < n:
        return _BGA_LETTERS[i]
    return _BGA_LETTERS[i // n - 1] + _BGA_LETTERS[i % n]


def _bga(rows, cols, pitch, ball, silk, courtyard, body_w, body_h, skip=None):
    if rows < 1 or cols < 1:
        raise ValueError("bga family needs rows >= 1 and cols >= 1")
    omit = {str(s).strip().upper() for s in (skip or [])}
    x0 = (cols - 1) * pitch / 2.0
    y0 = (rows - 1) * pitch / 2.0
    pads: list[dict] = []
    # A1 is top-left (row A at the top); columns number left->right.
    # `skip` depopulates balls (real BGAs omit corners / centre / JEDEC).
    for r in range(rows):
        for c in range(cols):
            ball_id = _bga_row_label(r) + str(c + 1)
            if ball_id.upper() in omit:
                continue
            pads.append({
                "designator": ball_id,
                "x": -x0 + c * pitch, "y": y0 - r * pitch,
                "x_size": ball, "y_size": ball, "shape": "round",
                "hole_size": 0,
            })
    ext_x = (cols - 1) * pitch + ball
    ext_y = (rows - 1) * pitch + ball
    return _finish(pads, ext_x, ext_y, silk, courtyard, body_w, body_h)


def _pad(designator, x, y, x_size, y_size, shape, hole):
    """One pad dict. Through-hole (hole>0): pin 1 rectangular (orientation
    marker), the rest round, per the universal DIP/header convention."""
    s = shape
    if hole > 0:
        s = "rectangular" if str(designator) == "1" else "round"
    return {"designator": str(designator), "x": x, "y": y,
            "x_size": x_size, "y_size": y_size, "shape": s,
            "hole_size": hole}


def _finish(pads, ext_x, ext_y, silk, courtyard, body_w, body_h):
    bw = body_w or ext_x
    bh = body_h or ext_y
    silk_tracks: list[dict] = []
    if silk:
        silk_tracks = _rect_tracks(-bw / 2, -bh / 2, bw / 2, bh / 2,
                                   _SILK_W, "TopOverlay")
    cy_tracks: list[dict] = []
    if courtyard > 0:
        cx = ext_x / 2 + courtyard
        cy = ext_y / 2 + courtyard
        cy_tracks = _rect_tracks(-cx, -cy, cx, cy, _CY_W, _CY_LAYER)
    return FootprintGeometry(
        pads=tuple(pads), silk_tracks=tuple(silk_tracks),
        courtyard_tracks=tuple(cy_tracks),
        width_mils=ext_x, height_mils=ext_y)


def _chip(pad_w, pad_h, pitch, shape, silk, courtyard, body_w, body_h, hole):
    half = pitch / 2.0
    pads = [
        _pad("1", -half, 0, pad_w, pad_h, shape, hole),
        _pad("2", half, 0, pad_w, pad_h, shape, hole),
    ]
    return _finish(pads, pitch + pad_w, pad_h, silk, courtyard, body_w, body_h)


def _sip(n, pitch, pad_w, pad_h, shape, silk, courtyard, body_w, body_h, hole):
    """Single in-line row (header / SIP), pin 1 at the left."""
    span = (n - 1) * pitch
    pads = [
        _pad(str(i + 1), i * pitch - span / 2.0, 0, pad_w, pad_h, shape, hole)
        for i in range(n)
    ]
    return _finish(pads, span + pad_w, pad_h, silk, courtyard, body_w, body_h)


def _dual(n, pitch, pad_w, pad_h, row_span, shape, silk, courtyard,
          body_w, body_h, hole):
    if row_span <= 0:
        raise ValueError("dual family needs row_span > 0")
    n_left = (n + 1) // 2
    n_right = n - n_left
    rh = row_span / 2.0
    pads: list[dict] = []
    # Left column: pin 1 at top, going down.
    top_l = (n_left - 1) * pitch / 2.0
    for i in range(n_left):
        pads.append(_pad(str(i + 1), -rh, top_l - i * pitch,
                         pad_w, pad_h, shape, hole))
    # Right column: continue numbering bottom -> top.
    top_r = (n_right - 1) * pitch / 2.0
    for j in range(n_right):
        pads.append(_pad(str(n_left + j + 1), rh, -top_r + j * pitch,
                         pad_w, pad_h, shape, hole))
    ext_x = row_span + pad_w
    ext_y = max(top_l, top_r) * 2 + pad_h
    return _finish(pads, ext_x, ext_y, silk, courtyard, body_w, body_h)


def _header(n, pitch, pad_w, pad_h, row_span, shape, silk, courtyard,
            body_w, body_h, hole):
    """Two-row pin / box header (IDC, dual pin header, SWD/JTAG).

    Geometrically distinct from ``dual`` (SOIC): a header is ``n/2``
    COLUMNS of two pads (a top and a bottom row ``row_span`` apart),
    numbered column-major odd/even -- pin 1 top of the first column, pin 2
    directly below it, pin 3 top of the next column, ... -- which is the
    universal 0.1" dual-header convention. ``dual``'s CCW numbering would
    mislabel the connector.
    """
    if row_span <= 0:
        raise ValueError(
            "header family needs row_span > 0 (spacing between the rows)")
    if n % 2 != 0:
        raise ValueError("header family needs an even pin_count (two rows)")
    cols = n // 2
    rh = row_span / 2.0
    along0 = (cols - 1) * pitch / 2.0
    pads: list[dict] = []
    for k in range(cols):
        x = k * pitch - along0
        pads.append(_pad(str(2 * k + 1), x, rh, pad_w, pad_h, shape, hole))
        pads.append(_pad(str(2 * k + 2), x, -rh, pad_w, pad_h, shape, hole))
    ext_x = (cols - 1) * pitch + pad_w
    ext_y = row_span + pad_h
    return _finish(pads, ext_x, ext_y, silk, courtyard, body_w, body_h)


def _tab(n, pitch, pad_w, pad_h, row_span, shape, silk, courtyard,
         body_w, body_h, hole, tab_w, tab_h):
    """Power / tab package (SOT-223, DPAK / D2PAK, TO-220).

    ``n`` signal pads on one row (numbered 1..n left->right) plus one large
    tab / thermal pad on the opposite row, ``row_span`` apart centre to
    centre. The tab takes designator ``n+1`` (like the quad exposed pad);
    in the schematic it is usually tied to a signal pin (e.g. the regulator
    ground / MOSFET drain). Through-hole leads (TO-220) by setting hole>0.
    """
    if row_span <= 0:
        raise ValueError(
            "tab family needs row_span > 0 (signal row to tab spacing)")
    if tab_w <= 0 or tab_h <= 0:
        raise ValueError(
            "tab family needs tab_w > 0 and tab_h > 0 (the tab pad size)")
    rh = row_span / 2.0
    span = (n - 1) * pitch
    pads: list[dict] = []
    for i in range(n):
        pads.append(_pad(str(i + 1), i * pitch - span / 2.0, -rh,
                         pad_w, pad_h, shape, hole))
    # The tab pad: large, centred on the opposite row, designator n+1.
    pads.append({
        "designator": str(n + 1), "x": 0.0, "y": rh,
        "x_size": tab_w, "y_size": tab_h, "shape": "rectangular",
        "hole_size": 0,
    })
    ext_x = max(span + pad_w, tab_w)
    ext_y = row_span + max(pad_h, tab_h)
    return _finish(pads, ext_x, ext_y, silk, courtyard, body_w, body_h)


def _quad(n, pitch, pad_w, pad_h, row_span, shape, silk, courtyard,
          body_w, body_h, hole, exposed_pad=0.0):
    if row_span <= 0:
        raise ValueError("quad family needs row_span > 0")
    if n % 4 != 0:
        raise ValueError("quad family needs pin_count divisible by 4")
    per_side = n // 4
    rh = row_span / 2.0
    along0 = (per_side - 1) * pitch / 2.0
    pads: list[dict] = []
    pin = 1
    for side in range(4):
        for k in range(per_side):
            if side == 0:      # left, top -> bottom; pads horizontal
                x, y, sx, sy = -rh, along0 - k * pitch, pad_h, pad_w
            elif side == 1:    # bottom, left -> right; pads vertical
                x, y, sx, sy = k * pitch - along0, -rh, pad_w, pad_h
            elif side == 2:    # right, bottom -> top
                x, y, sx, sy = rh, -along0 + k * pitch, pad_h, pad_w
            else:              # top, right -> left
                x, y, sx, sy = along0 - k * pitch, rh, pad_w, pad_h
            pads.append(_pad(str(pin), x, y, sx, sy, shape, hole))
            pin += 1
    # QFN/QFP centre exposed (thermal) pad; gets the next designator.
    if exposed_pad > 0:
        pads.append({"designator": str(n + 1), "x": 0, "y": 0,
                     "x_size": exposed_pad, "y_size": exposed_pad,
                     "shape": "rectangular", "hole_size": 0})
    ext = row_span + pad_h
    return _finish(pads, ext, ext, silk, courtyard, body_w, body_h)


__all__ = ["FootprintGeometry", "generate_footprint"]
