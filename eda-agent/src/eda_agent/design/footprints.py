# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Datasheet-derived PCB land patterns (footprints).

A footprint's pad geometry comes from the manufacturer's datasheet -- either its
"Recommended Land Pattern" table or its mechanical dimensions. This module
builds the pad array from the numbers the datasheet gives, as pure geometry: the
pad CENTRES of a symmetric package are fixed once you know the pad size, the
pitch, and the cross-package span, none of which require a standards table. The
agent transcribes those few numbers from the (cited) datasheet and calls a
builder; the builder does the deterministic layout. No IPC-7351 constants are
used here -- that is a separate, optional compute path.

Everything is in millimetres (the datasheet's native unit for land patterns);
the Altium emit layer converts. Pad 1 is the package's polarity/orientation
reference; numbering follows the package convention (chips left-to-right, dual
and quad rows counter-clockwise from the top-left, as on the datasheet).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Pad:
    """One land, centre-referenced, millimetres."""

    designator: str
    x: float
    y: float
    width: float            # X extent
    height: float           # Y extent
    shape: str = "roundrect"  # roundrect | rect | round | oval
    layer: str = "top"        # "top" copper SMD, or "thru" for a plated hole
    hole_dia: float = 0.0     # > 0 for a through-hole pad


@dataclass(frozen=True)
class LandPattern:
    """A complete footprint: pads plus optional courtyard / body outline."""

    name: str
    pads: tuple[Pad, ...]
    courtyard: tuple[float, float, float, float] | None = None  # x1,y1,x2,y2
    body: tuple[float, float, float, float] | None = None       # silk bbox
    units: str = "mm"
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def pad_count(self) -> int:
        return len(self.pads)


def _one_of(span: float | None, inner_gap: float | None,
            center: float | None, pad_extent: float) -> float:
    """Resolve a pad-row HALF offset from whichever datasheet dimension is
    given: toe-to-toe ``span`` (outer-to-outer), ``inner_gap`` (heel-to-heel),
    or ``center`` (centre-to-centre). Exactly one must be supplied."""
    given = [v for v in (span, inner_gap, center) if v is not None]
    if len(given) != 1:
        raise ValueError(
            "give exactly one of span (toe-to-toe), inner_gap, or center")
    if span is not None:
        if span <= pad_extent:
            raise ValueError("span (toe-to-toe) must exceed the pad extent")
        return span / 2.0 - pad_extent / 2.0
    if inner_gap is not None:
        if inner_gap < 0:
            raise ValueError("inner_gap must be non-negative")
        return inner_gap / 2.0 + pad_extent / 2.0
    if center <= 0:                      # type: ignore[operator]
        raise ValueError("center span must be positive")
    return center / 2.0                  # type: ignore[operator]


def two_pin_chip(
    pad_w: float,
    pad_h: float,
    *,
    span: float | None = None,
    inner_gap: float | None = None,
    center: float | None = None,
    name: str = "CHIP",
    courtyard_margin: float = 0.25,
) -> LandPattern:
    """Two-terminal chip land (R / C / L / LED, 0402 etc.), pads on the X axis.

    Supply the cross-pad dimension the datasheet gives: ``span`` (toe-to-toe),
    ``inner_gap`` (between inner pad edges), or ``center`` (centre-to-centre).
    """
    if pad_w <= 0 or pad_h <= 0:
        raise ValueError("pad dimensions must be positive")
    cx = _one_of(span, inner_gap, center, pad_w)
    pads = (
        Pad("1", -cx, 0.0, pad_w, pad_h),
        Pad("2", +cx, 0.0, pad_w, pad_h),
    )
    return _finish(name, pads, courtyard_margin)


def dual_row(
    pins_per_side: int,
    pitch: float,
    pad_w: float,
    pad_h: float,
    *,
    span: float | None = None,
    inner_gap: float | None = None,
    center: float | None = None,
    name: str = "DUAL",
    courtyard_margin: float = 0.25,
) -> LandPattern:
    """Two-row gull-wing land (SOIC / SOP / SSOP / TSSOP), pad rows on the X
    axis, pads stacked on Y at ``pitch``. The cross-package dimension is the
    pad WIDTH direction (X), so ``span``/``inner_gap``/``center`` refer to that.

    Numbering is the package convention: pin 1 at top-left, down the left row,
    then up the right row (counter-clockwise viewed from the top).
    """
    if pins_per_side < 1:
        raise ValueError("pins_per_side must be >= 1")
    if pitch <= 0 or pad_w <= 0 or pad_h <= 0:
        raise ValueError("pitch and pad dimensions must be positive")
    cx = _one_of(span, inner_gap, center, pad_w)
    n = pins_per_side
    y_top = (n - 1) / 2.0 * pitch
    pads: list[Pad] = []
    # Left row: pin 1..n, top -> bottom.
    for i in range(n):
        pads.append(Pad(str(i + 1), -cx, y_top - i * pitch, pad_w, pad_h))
    # Right row: pin n+1..2n, bottom -> top.
    for i in range(n):
        pads.append(Pad(str(n + i + 1), +cx, -y_top + i * pitch, pad_w, pad_h))
    return _finish(name, tuple(pads), courtyard_margin)


def _finish(name: str, pads: tuple[Pad, ...], margin: float) -> LandPattern:
    """Attach a courtyard = pad bounding box grown by ``margin`` (mm)."""
    xs1 = min(p.x - p.width / 2 for p in pads)
    xs2 = max(p.x + p.width / 2 for p in pads)
    ys1 = min(p.y - p.height / 2 for p in pads)
    ys2 = max(p.y + p.height / 2 for p in pads)
    court = (round(xs1 - margin, 4), round(ys1 - margin, 4),
             round(xs2 + margin, 4), round(ys2 + margin, 4))
    return LandPattern(name=name, pads=pads, courtyard=court)


def pattern_bbox(lp: LandPattern) -> tuple[float, float, float, float]:
    """Bounding box (x1, y1, x2, y2) of all pads, millimetres."""
    return (
        min(p.x - p.width / 2 for p in lp.pads),
        min(p.y - p.height / 2 for p in lp.pads),
        max(p.x + p.width / 2 for p in lp.pads),
        max(p.y + p.height / 2 for p in lp.pads),
    )


def row_pitch(lp: LandPattern) -> float | None:
    """The dominant centre-to-centre pad pitch (smallest gap between adjacent
    same-row pads), or None for a 2-pad part. Used by the checker."""
    by_x: dict[float, list[float]] = {}
    for p in lp.pads:
        by_x.setdefault(round(p.x, 3), []).append(p.y)
    gaps: list[float] = []
    for ys in by_x.values():
        ys.sort()
        gaps += [round(b - a, 4) for a, b in zip(ys, ys[1:])]
    return min(gaps) if gaps else None


__all__ = [
    "Pad",
    "LandPattern",
    "two_pin_chip",
    "dual_row",
    "pattern_bbox",
    "row_pitch",
]
