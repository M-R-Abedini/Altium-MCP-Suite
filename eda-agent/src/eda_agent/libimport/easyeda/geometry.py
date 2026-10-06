# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""SVG arc geometry for the EasyEDA converter.

EasyEDA stores arcs as SVG path data ("M x1,y1 A rx,ry rot large sweep
x2,y2"), an ENDPOINT parameterization. Both target CADs want a CENTRE
parameterization instead: KiCad takes three points on the curve, Altium
takes centre plus start and end angles. Converting between the two is
the standard endpoint-to-centre algorithm from the SVG 1.1
specification, appendix F.6.5, implemented here rather than approximated.

Approximating an arc by its chord (the cheap alternative) visibly
deforms pin-1 markers and package outlines, and silently dropping arcs
loses silkscreen entirely, so the real conversion is worth the ~60 lines.
"""

from __future__ import annotations

import math
import re
from typing import NamedTuple, Optional

__all__ = ["ArcGeometry", "parse_svg_arc", "svg_arc_to_center"]


class ArcGeometry(NamedTuple):
    """An arc in centre form.

    Angles are in DEGREES, measured counter-clockwise from +X, in the
    coordinate frame the input points were given in.
    """

    cx: float
    cy: float
    rx: float
    ry: float
    start_angle: float
    end_angle: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def sweep_deg(self) -> float:
        return self.end_angle - self.start_angle

    def point_at(self, t: float) -> tuple[float, float]:
        """A point on the arc, ``t`` in 0..1 from start to end."""
        a = math.radians(self.start_angle + self.sweep_deg * t)
        return (self.cx + self.rx * math.cos(a),
                self.cy + self.ry * math.sin(a))

    @property
    def midpoint(self) -> tuple[float, float]:
        return self.point_at(0.5)

    @property
    def is_circular(self) -> bool:
        """True when rx == ry within tolerance.

        Neither KiCad's fp_arc nor Altium's arc primitive represents a
        true ellipse, so a caller must know when it is about to lose
        fidelity.
        """
        return abs(self.rx - self.ry) <= max(1e-6, 1e-3 * max(self.rx, self.ry))


_NUM = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"
_ARC_RE = re.compile(
    rf"M\s*({_NUM})[,\s]+({_NUM})\s*"
    rf"A\s*({_NUM})[,\s]+({_NUM})[,\s]+({_NUM})[,\s]+"
    rf"([01])[,\s]*([01])[,\s]+({_NUM})[,\s]+({_NUM})",
    re.IGNORECASE,
)


def parse_svg_arc(path: str) -> Optional[ArcGeometry]:
    """Parse an EasyEDA arc path into centre form, or None if it is not
    a single ``M ... A ...`` arc."""
    if not path:
        return None
    m = _ARC_RE.search(path)
    if not m:
        return None
    x1, y1, rx, ry, rot, large, sweep, x2, y2 = (
        float(m.group(1)), float(m.group(2)),
        float(m.group(3)), float(m.group(4)), float(m.group(5)),
        int(m.group(6)), int(m.group(7)),
        float(m.group(8)), float(m.group(9)),
    )
    return svg_arc_to_center(x1, y1, rx, ry, rot, large, sweep, x2, y2)


def svg_arc_to_center(
    x1: float, y1: float,
    rx: float, ry: float, phi_deg: float,
    large_arc: int, sweep: int,
    x2: float, y2: float,
) -> Optional[ArcGeometry]:
    """Endpoint to centre parameterization, per SVG 1.1 F.6.5.

    Returns None for a degenerate arc (zero radius, or coincident
    endpoints), which the caller should treat as "not an arc" rather
    than emitting something malformed.
    """
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return None
    if math.isclose(x1, x2, abs_tol=1e-9) and math.isclose(y1, y2, abs_tol=1e-9):
        return None

    phi = math.radians(phi_deg)
    cos_p, sin_p = math.cos(phi), math.sin(phi)

    # F.6.5.1 compute (x1', y1')
    dx2, dy2 = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p = cos_p * dx2 + sin_p * dy2
    y1p = -sin_p * dx2 + cos_p * dy2

    # F.6.6.2 scale the radii up if they cannot span the endpoints.
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:
        s = math.sqrt(lam)
        rx *= s
        ry *= s

    # F.6.5.2 compute (cx', cy')
    num = (rx * rx) * (ry * ry) - (rx * rx) * (y1p * y1p) \
        - (ry * ry) * (x1p * x1p)
    den = (rx * rx) * (y1p * y1p) + (ry * ry) * (x1p * x1p)
    if den == 0:
        return None
    factor = math.sqrt(max(0.0, num / den))
    if large_arc == sweep:
        factor = -factor
    cxp = factor * (rx * y1p / ry)
    cyp = factor * (-ry * x1p / rx)

    # F.6.5.3 compute (cx, cy)
    cx = cos_p * cxp - sin_p * cyp + (x1 + x2) / 2.0
    cy = sin_p * cxp + cos_p * cyp + (y1 + y2) / 2.0

    # F.6.5.5 / F.6.5.6 start angle and sweep
    def angle_of(px: float, py: float) -> float:
        return math.degrees(math.atan2((py - cyp) / ry, (px - cxp) / rx))

    theta1 = angle_of(x1p, y1p)
    theta2 = angle_of(-x1p, -y1p)
    delta = theta2 - theta1

    if sweep == 0 and delta > 0:
        delta -= 360.0
    elif sweep == 1 and delta < 0:
        delta += 360.0

    return ArcGeometry(
        cx=cx, cy=cy, rx=rx, ry=ry,
        start_angle=theta1, end_angle=theta1 + delta,
        x1=x1, y1=y1, x2=x2, y2=y2,
    )
