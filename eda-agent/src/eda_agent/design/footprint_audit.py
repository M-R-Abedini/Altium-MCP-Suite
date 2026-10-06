# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Audit a PcbLib footprint against a datasheet land-pattern spec.

Split of responsibilities, matching the rest of the design surface:

* The AGENT reads the manufacturer datasheet (fetched, cited) and
  transcribes the recommended land pattern into a
  :class:`LandPatternSpec` -- pad grid, dimensions, numbering, thermal
  pad, paste windowing. Nothing in this module knows any part by name;
  there are no built-in package tables to go stale or hallucinate.
* THIS module is the deterministic half: given the spec and the real
  pad geometry read from the library (``library.get_pad_geometry``), it
  aligns the two patterns and reports every discrepancy with expected
  vs actual values, in millimetres.

Alignment: a footprint's origin is wherever the librarian left it and
its rotation is a library convention, so raw coordinates never match a
datasheet drawing directly. The comparator centres both patterns on
their pad centroids and tries the four 90-degree rotations, keeping the
one with the smallest total matched distance. Mirroring is NOT tried:
a mirrored land pattern is a real defect the audit must report, not
silently compensate.

All tolerances are explicit in the spec with conservative defaults
(0.05 mm position / size), so a metric-to-imperial rounding survives
but a wrong pitch does not.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from pydantic import BaseModel, Field

__all__ = [
    "AuditFinding",
    "LandPatternSpec",
    "SpecPad",
    "SpecSource",
    "audit_footprint_against_spec",
    "expand_spec_pads",
]


class SpecSource(BaseModel):
    """Where the spec numbers came from. Required: an audit whose spec
    cannot be traced to a datasheet page is opinion, not verification."""

    datasheet_url: str = Field(min_length=1)
    reference: str = Field(
        min_length=1,
        description=(
            "Section / figure / page inside the datasheet the land "
            "pattern was read from, e.g. 'Figure 6-1, p. 23'."
        ),
    )
    part_number: str = Field(min_length=1)


class SpecPad(BaseModel):
    """One pad of the recommended land pattern, in mm, Y-up, any origin.

    ``name`` is the pad designator the datasheet assigns (usually the
    pin number). The comparator matches pads by POSITION, then checks
    the matched names, so a footprint with correct geometry but wrong
    numbering is reported as a sequence error, not a position error.
    """

    name: str
    x: float
    y: float
    w: float = Field(gt=0)
    h: float = Field(gt=0)
    shape: Optional[str] = Field(
        default=None,
        description=(
            "round | rectangular | roundrectangle | octagonal. None = "
            "don't check shape for this pad."
        ),
    )
    hole: Optional[float] = Field(
        default=None,
        description="Drill diameter, mm. None/0 = SMD pad.",
    )


class LandPatternSpec(BaseModel):
    """Datasheet land pattern, transcribed by the agent.

    Two ways to give the pads; they can be combined:

    * ``pads``: explicit list, for irregular patterns.
    * ``dual_row`` / ``quad``: parametric shorthand for the two most
      common regular patterns; expanded to explicit pads by
      :func:`expand_spec_pads` using the standard CCW-from-pin-1
      numbering (dual row: pin 1 top of left column, down the left,
      up the right -- the SOIC/SOP convention; quad: pin 1 top of left
      side, CCW around -- the QFP/QFN convention).
    """

    source: SpecSource
    pads: list[SpecPad] = Field(default_factory=list)
    dual_row: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "{count, pitch, span, pad_w, pad_h, shape?} -- count is "
            "TOTAL pads, span is centre-to-centre between columns."
        ),
    )
    quad: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "{count, pitch, span_x, span_y, pad_w, pad_h, shape?} -- "
            "count is TOTAL pads divisible by 4; pad_w is the dimension "
            "along the row."
        ),
    )
    thermal_pad: Optional[SpecPad] = Field(
        default=None,
        description="Exposed pad, usually named after the last pin + 1.",
    )
    position_tol: float = Field(default=0.05, gt=0)
    size_tol: float = Field(default=0.05, gt=0)
    # Paste policy the datasheet/assembly note prescribes for the
    # thermal pad: full aperture ('full') or segmented windows
    # ('windowed'). Signal pads are expected rule-driven full paste.
    thermal_paste: Optional[str] = None


def expand_spec_pads(spec: LandPatternSpec) -> list[SpecPad]:
    """Explicit pad list from the spec, expanding parametric shorthands."""
    pads: list[SpecPad] = list(spec.pads)

    if spec.dual_row:
        d = spec.dual_row
        count = int(d["count"])
        if count % 2:
            raise ValueError("dual_row count must be even")
        pitch = float(d["pitch"])
        span = float(d["span"])
        pad_w = float(d["pad_w"])
        pad_h = float(d["pad_h"])
        shape = d.get("shape")
        per_side = count // 2
        top = (per_side - 1) * pitch / 2.0
        # Left column: pin 1 at the top, descending. Right column
        # ascends so the numbering runs counter-clockwise.
        for i in range(per_side):
            pads.append(SpecPad(
                name=str(i + 1), x=-span / 2.0, y=top - i * pitch,
                w=pad_w, h=pad_h, shape=shape))
        for i in range(per_side):
            pads.append(SpecPad(
                name=str(per_side + i + 1), x=span / 2.0,
                y=-top + i * pitch, w=pad_w, h=pad_h, shape=shape))

    if spec.quad:
        q = spec.quad
        count = int(q["count"])
        if count % 4:
            raise ValueError("quad count must be divisible by 4")
        pitch = float(q["pitch"])
        span_x = float(q["span_x"])
        span_y = float(q["span_y"])
        pad_w = float(q["pad_w"])
        pad_h = float(q["pad_h"])
        shape = q.get("shape")
        per_side = count // 4
        top = (per_side - 1) * pitch / 2.0
        n = 1
        # Left side, top to bottom. Pads on left/right sides are wide
        # along X (pad_h x pad_w swapped relative to top/bottom rows).
        for i in range(per_side):
            pads.append(SpecPad(
                name=str(n), x=-span_x / 2.0, y=top - i * pitch,
                w=pad_h, h=pad_w, shape=shape))
            n += 1
        # Bottom side, left to right.
        for i in range(per_side):
            pads.append(SpecPad(
                name=str(n), x=-top + i * pitch, y=-span_y / 2.0,
                w=pad_w, h=pad_h, shape=shape))
            n += 1
        # Right side, bottom to top.
        for i in range(per_side):
            pads.append(SpecPad(
                name=str(n), x=span_x / 2.0, y=-top + i * pitch,
                w=pad_h, h=pad_w, shape=shape))
            n += 1
        # Top side, right to left.
        for i in range(per_side):
            pads.append(SpecPad(
                name=str(n), x=top - i * pitch, y=span_y / 2.0,
                w=pad_w, h=pad_h, shape=shape))
            n += 1

    if spec.thermal_pad is not None:
        pads.append(spec.thermal_pad)

    if not pads:
        raise ValueError(
            "spec has no pads: give `pads`, `dual_row`, or `quad`")
    return pads


class AuditFinding(BaseModel):
    """One discrepancy between the library footprint and the spec."""

    check: str
    severity: str  # "error" | "warning"
    message: str
    pad: Optional[str] = None
    expected: Optional[str] = None
    actual: Optional[str] = None


def _centroid(pts: list[tuple[float, float]]) -> tuple[float, float]:
    n = max(1, len(pts))
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)


def _rot(pt: tuple[float, float], quarter: int) -> tuple[float, float]:
    x, y = pt
    q = quarter % 4
    if q == 1:
        return (-y, x)
    if q == 2:
        return (-x, -y)
    if q == 3:
        return (y, -x)
    return (x, y)


def _greedy_match(
    spec_pts: list[tuple[float, float]],
    fp_pts: list[tuple[float, float]],
) -> list[tuple[int, int, float]]:
    """Greedy nearest-neighbour matching: (spec_idx, fp_idx, distance).

    Pairs closest-first so an off-position pad steals nobody's partner.
    Greedy is adequate here because land-pattern pads are far apart
    relative to any credible placement error; a full assignment solver
    would only matter for errors larger than the pattern itself.
    """
    cand = []
    for i, sp in enumerate(spec_pts):
        for j, fp in enumerate(fp_pts):
            cand.append((math.hypot(sp[0] - fp[0], sp[1] - fp[1]), i, j))
    cand.sort()
    used_s: set[int] = set()
    used_f: set[int] = set()
    out = []
    for d, i, j in cand:
        if i in used_s or j in used_f:
            continue
        used_s.add(i)
        used_f.add(j)
        out.append((i, j, d))
    return out


def audit_footprint_against_spec(
    spec: LandPatternSpec,
    footprint: dict[str, Any],
) -> dict[str, Any]:
    """Compare real pad geometry against the datasheet land pattern.

    ``footprint`` is the ``library.get_pad_geometry`` response dict.
    Returns ``{ok, rotation_applied_deg, findings: [AuditFinding...]}``
    with ``ok`` true only when no error-severity finding exists.
    """
    findings: list[AuditFinding] = []
    spec_pads = expand_spec_pads(spec)
    fp_pads = list(footprint.get("pads") or [])

    if len(fp_pads) != len(spec_pads):
        findings.append(AuditFinding(
            check="pad_count", severity="error",
            message="pad count differs from the datasheet land pattern",
            expected=str(len(spec_pads)), actual=str(len(fp_pads)),
        ))

    if not fp_pads:
        return {
            "ok": False,
            "rotation_applied_deg": 0,
            "findings": [f.model_dump(exclude_none=True) for f in findings],
        }

    # --- alignment: centre both point sets, try the 4 rotations -------
    spec_pts_raw = [(p.x, p.y) for p in spec_pads]
    fp_pts_raw = [(float(p["x_mm"]), float(p["y_mm"])) for p in fp_pads]
    scx, scy = _centroid(spec_pts_raw)
    fcx, fcy = _centroid(fp_pts_raw)
    spec_pts = [(x - scx, y - scy) for (x, y) in spec_pts_raw]
    fp_centred = [(x - fcx, y - fcy) for (x, y) in fp_pts_raw]

    best_quarter = 0
    best_key = None
    best_pairs: list[tuple[int, int, float]] = []
    for quarter in range(4):
        fp_pts = [_rot(p, quarter) for p in fp_centred]
        pairs = _greedy_match(spec_pts, fp_pts)
        cost = sum(d for (_, _, d) in pairs)
        # Many land patterns are position-symmetric under 180 degrees
        # (any dual row) or 90 (any square quad), so geometry alone can
        # tie. Break ties by designator agreement: when two rotations
        # fit equally, the librarian's intent is the one where the
        # numbering lines up. Names are ONLY a tie-break, so a genuinely
        # mis-numbered footprint still reports sequence errors.
        name_hits = sum(
            1 for (si, fi, _) in pairs
            if spec_pads[si].name == str(fp_pads[fi].get("name"))
        )
        key = (round(cost, 9), -name_hits)
        if best_key is None or key < best_key:
            best_key = key
            best_quarter = quarter
            best_pairs = pairs
    fp_pts = [_rot(p, best_quarter) for p in fp_centred]

    # Rotating the pad CENTRES by 90/270 also swaps each pad's w/h in
    # the aligned frame; account for it when comparing sizes.
    swap_wh = best_quarter % 2 == 1

    # --- per-pad checks ------------------------------------------------
    matched_fp: set[int] = set()
    for (si, fi, dist) in best_pairs:
        sp = spec_pads[si]
        fp = fp_pads[fi]
        matched_fp.add(fi)

        if dist > spec.position_tol:
            findings.append(AuditFinding(
                check="pad_position", severity="error", pad=sp.name,
                message="pad centre is off the datasheet position",
                expected=(
                    f"({spec_pts[si][0] + scx:.3f}, "
                    f"{spec_pts[si][1] + scy:.3f}) mm"),
                actual=(
                    f"off by {dist:.3f} mm "
                    f"(tolerance {spec.position_tol} mm)"),
            ))

        fw = float(fp["w_mm"])
        fh = float(fp["h_mm"])
        # The footprint pad's own rotation swaps its drawn extents.
        rot = float(fp.get("rotation") or 0.0)
        if abs(((rot % 180) + 180) % 180 - 90) < 1e-6:
            fw, fh = fh, fw
        if swap_wh:
            fw, fh = fh, fw
        if abs(fw - sp.w) > spec.size_tol or abs(fh - sp.h) > spec.size_tol:
            findings.append(AuditFinding(
                check="pad_size", severity="error", pad=sp.name,
                message="pad size differs from the datasheet",
                expected=f"{sp.w:.3f} x {sp.h:.3f} mm",
                actual=f"{fw:.3f} x {fh:.3f} mm",
            ))

        if sp.shape and str(fp.get("shape")) != sp.shape:
            findings.append(AuditFinding(
                check="pad_shape", severity="warning", pad=sp.name,
                message="pad shape differs from the datasheet",
                expected=sp.shape, actual=str(fp.get("shape")),
            ))

        if sp.hole is not None and sp.hole > 0:
            fhole = float(fp.get("hole_mm") or 0.0)
            if abs(fhole - sp.hole) > spec.size_tol:
                findings.append(AuditFinding(
                    check="pad_hole", severity="error", pad=sp.name,
                    message="drill differs from the datasheet",
                    expected=f"{sp.hole:.3f} mm",
                    actual=f"{fhole:.3f} mm",
                ))
        elif float(fp.get("hole_mm") or 0.0) > 0:
            findings.append(AuditFinding(
                check="pad_hole", severity="error", pad=sp.name,
                message="footprint pad has a drill where the datasheet "
                        "pad is SMD",
                expected="no hole",
                actual=f"{float(fp['hole_mm']):.3f} mm",
            ))

        # Sequence: geometry matched this footprint pad to this spec
        # position; the designator on it must agree.
        if str(fp.get("name")) != sp.name:
            findings.append(AuditFinding(
                check="pad_sequence", severity="error", pad=sp.name,
                message=(
                    "pad at this position carries the wrong designator "
                    "(numbering sequence differs from the datasheet)"),
                expected=sp.name, actual=str(fp.get("name")),
            ))

    for fi, fp in enumerate(fp_pads):
        if fi not in matched_fp:
            findings.append(AuditFinding(
                check="pad_extra", severity="error",
                pad=str(fp.get("name")),
                message="footprint pad has no counterpart in the "
                        "datasheet land pattern",
                actual=(
                    f"({float(fp['x_mm']):.3f}, {float(fp['y_mm']):.3f}) mm"),
            ))

    # --- thermal-pad paste policy ---------------------------------------
    if spec.thermal_pad is not None and spec.thermal_paste:
        tp = None
        for (si, fi, _) in best_pairs:
            if spec_pads[si] is spec.thermal_pad or (
                    spec_pads[si].name == spec.thermal_pad.name
                    and si == len(spec_pads) - 1):
                tp = fp_pads[fi]
                break
        if tp is not None:
            src = str(tp.get("paste_expansion_source") or "rule")
            exp = float(tp.get("paste_expansion_mm") or 0.0)
            if spec.thermal_paste == "windowed":
                # A windowed EP needs the pad's own full-face paste
                # suppressed (manual negative expansion) with paste
                # delivered by separate stencil apertures. Full
                # rule-driven paste over a large EP voids the joint.
                if not (src == "manual" and exp < 0):
                    findings.append(AuditFinding(
                        check="thermal_paste", severity="warning",
                        pad=spec.thermal_pad.name,
                        message=(
                            "datasheet asks for windowed paste on the "
                            "exposed pad, but the pad carries full-face "
                            "paste (no manual negative expansion found); "
                            "verify the stencil design"),
                        expected="windowed apertures",
                        actual=f"paste source={src}, expansion={exp} mm",
                    ))

    ok = not any(f.severity == "error" for f in findings)
    return {
        "ok": ok,
        "rotation_applied_deg": best_quarter * 90,
        "findings": [f.model_dump(exclude_none=True) for f in findings],
    }
