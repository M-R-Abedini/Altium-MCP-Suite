# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Check a PCB footprint against the datasheet-derived land pattern.

A wrong or mismatched footprint is one of the most expensive board errors -- it
survives schematic ERC and only shows at assembly. This module compares an
actual footprint's pads (read from the library / board) against the land pattern
the datasheet specifies (built by :mod:`footprints`), and reports exactly where
they diverge: pad count, missing / extra pads, pad positions, and pad sizes.

The comparison is centroid-aligned: both pad sets are shifted so their centroid
is at the origin before comparing, so an arbitrary footprint origin (corner,
pin 1, body centre) never causes a false mismatch -- it checks the relative pad
GEOMETRY, which is what has to match the datasheet. Sizes are compared in
absolute millimetres. Mirroring (bottom-side placement) is not auto-detected;
pass top-side geometry.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from eda_agent.design.footprints import LandPattern, Pad


@dataclass(frozen=True)
class FootprintIssue:
    code: str           # pad_count | missing_pad | extra_pad | pad_position | pad_size
    severity: str       # "error" | "warning"
    message: str
    designator: str = ""


@dataclass(frozen=True)
class FootprintCheckReport:
    issues: tuple[FootprintIssue, ...]
    pos_tol_mm: float
    size_tol_mm: float

    @property
    def errors(self) -> tuple[FootprintIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "error")

    @property
    def passed(self) -> bool:
        return not self.errors


def _centroid(pads: list[Pad]) -> tuple[float, float]:
    n = len(pads)
    return (sum(p.x for p in pads) / n, sum(p.y for p in pads) / n)


def check_footprint(
    measured: list[Pad],
    expected: LandPattern,
    *,
    pos_tol_mm: float = 0.05,
    size_tol_mm: float = 0.05,
) -> FootprintCheckReport:
    """Compare ``measured`` pads against the datasheet ``expected`` land pattern.

    Matches pads by designator, after centroid-aligning both sets. Flags pad
    count, missing / extra designators, position deltas beyond ``pos_tol_mm``,
    and width/height deltas beyond ``size_tol_mm``.
    """
    issues: list[FootprintIssue] = []
    exp_pads = list(expected.pads)
    if not measured or not exp_pads:
        raise ValueError("both measured and expected must have pads")

    if len(measured) != len(exp_pads):
        issues.append(FootprintIssue(
            code="pad_count", severity="error",
            message=(f"footprint has {len(measured)} pads; datasheet land "
                     f"pattern has {len(exp_pads)}")))

    mcx, mcy = _centroid(measured)
    ecx, ecy = _centroid(exp_pads)
    meas_by = {p.designator: p for p in measured}
    exp_by = {p.designator: p for p in exp_pads}

    for d in sorted(set(exp_by) - set(meas_by)):
        issues.append(FootprintIssue(
            code="missing_pad", severity="error",
            message=f"pad {d} is in the datasheet land pattern but not the "
                    f"footprint", designator=d))
    for d in sorted(set(meas_by) - set(exp_by)):
        issues.append(FootprintIssue(
            code="extra_pad", severity="error",
            message=f"pad {d} is in the footprint but not the datasheet land "
                    f"pattern", designator=d))

    for d in sorted(set(meas_by) & set(exp_by)):
        m, e = meas_by[d], exp_by[d]
        mx, my = m.x - mcx, m.y - mcy           # centroid-relative
        ex, ey = e.x - ecx, e.y - ecy
        dist = math.hypot(mx - ex, my - ey)
        if dist > pos_tol_mm:
            issues.append(FootprintIssue(
                code="pad_position", severity="error",
                message=(f"pad {d} is {dist:.3f} mm from its datasheet "
                         f"position (>{pos_tol_mm} mm)"), designator=d))
        dw = abs(m.width - e.width)
        dh = abs(m.height - e.height)
        if dw > size_tol_mm or dh > size_tol_mm:
            issues.append(FootprintIssue(
                code="pad_size", severity="error",
                message=(f"pad {d} is {m.width:.3f}x{m.height:.3f} mm; "
                         f"datasheet land pattern is {e.width:.3f}x"
                         f"{e.height:.3f} mm"), designator=d))

    return FootprintCheckReport(
        issues=tuple(issues), pos_tol_mm=pos_tol_mm, size_tol_mm=size_tol_mm)


__all__ = ["FootprintIssue", "FootprintCheckReport", "check_footprint"]
