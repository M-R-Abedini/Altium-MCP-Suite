# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Compute the electrical behaviour of recognised sub-circuits.

The matched-value check flags components that should be equal but aren't; this
module answers the complementary question -- "what does this block DO?" -- by
recognising each sub-circuit and computing its characteristic parameter from
the chosen component values: a divider's ratio, a filter's cut-off, a crystal's
load. It catches the wrong-but-consistent value error a divider built from two
perfectly valid resistors that simply produces the wrong ratio, which no
connectivity or equality check can see.

Most of these parameters are ratios or time-constants that need ONLY the
component values, not a supply voltage, so they are computable from the plan
alone. Composes motif recognition with the value-string parser. Pure offline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from eda_agent.design.plan import DesignPlan
from eda_agent.design.value_parser import format_value, try_parse_value

# Assumed per-leg stray capacitance for the crystal-load report (pF), the
# usual board+pin figure when a datasheet value is not supplied.
_CRYSTAL_STRAY_F = 5.0e-12


@dataclass(frozen=True)
class MotifDescription:
    motif_name: str
    parts: tuple[str, ...]
    summary: str
    params: dict[str, float] = field(default_factory=dict)


def _v(value_of: dict[str, str], match, pattern_name: str) -> Optional[float]:
    ref = match.host_refdes(pattern_name)
    if ref is None:
        return None
    return try_parse_value(value_of.get(ref, ""))


def _refs(match, *pattern_names: str) -> tuple[str, ...]:
    return tuple(r for r in (match.host_refdes(n) for n in pattern_names)
                if r is not None)


def _describe_voltage_divider(match, value_of):
    rt, rb = _v(value_of, match, "Rtop"), _v(value_of, match, "Rbot")
    if rt is None or rb is None or rt + rb == 0:
        return None
    ratio = rb / (rt + rb)
    return MotifDescription(
        "voltage_divider", _refs(match, "Rtop", "Rbot"),
        f"voltage divider: output = {ratio:.3f} x input", {"ratio": ratio})


def _describe_fb_divider(match, value_of):
    rt, rb = _v(value_of, match, "Rtop"), _v(value_of, match, "Rbot")
    if rt is None or rb is None or rb == 0:
        return None
    gain = 1.0 + rt / rb
    return MotifDescription(
        "fb_divider", _refs(match, "Rtop", "Rbot"),
        f"feedback divider: Vout = Vref x {gain:.3f}", {"gain": gain})


def _describe_rc_filter(name: str):
    def describe(match, value_of):
        r, c = _v(value_of, match, "R"), _v(value_of, match, "C")
        if r is None or c is None or r <= 0 or c <= 0:
            return None
        fc = 1.0 / (2.0 * math.pi * r * c)
        kind = "low-pass" if name == "rc_lowpass" else "high-pass"
        return MotifDescription(
            name, _refs(match, "R", "C"),
            f"RC {kind}: fc = {format_value(fc, 'Hz')}", {"f_cutoff_hz": fc})
    return describe


def _describe_crystal_load(match, value_of):
    cx, cy = _v(value_of, match, "Cx"), _v(value_of, match, "Cy")
    caps = [c for c in (cx, cy) if c is not None]
    if not caps:
        return None
    # Report the load each cap presents (they should be equal; the matched
    # check owns the mismatch case). CL = C/2 + Cstray.
    c = caps[0]
    c_load = c / 2.0 + _CRYSTAL_STRAY_F
    return MotifDescription(
        "crystal_load", _refs(match, "Y", "Cx", "Cy"),
        f"crystal load: CL = {format_value(c_load, 'F')} "
        f"(per-cap {format_value(c, 'F')}, stray "
        f"{format_value(_CRYSTAL_STRAY_F, 'F')})",
        {"c_load_f": c_load})


_DESCRIBERS = {
    "voltage_divider": _describe_voltage_divider,
    "fb_divider": _describe_fb_divider,
    "rc_lowpass": _describe_rc_filter("rc_lowpass"),
    "rc_highpass": _describe_rc_filter("rc_highpass"),
    "crystal_load": _describe_crystal_load,
}


def describe_motifs(plan: DesignPlan) -> list[MotifDescription]:
    """Recognise sub-circuits and compute each one's characteristic parameter.

    Returns a deterministically-ordered list of :class:`MotifDescription`.
    Blocks whose values are missing or unparseable are skipped (nothing to
    compute); a design with no recognised parametric block yields an empty
    list.
    """
    from eda_agent.design.motifs import recognize_motifs

    value_of = {p.refdes: (p.value or "").strip() for p in plan.parts}
    out: list[MotifDescription] = []
    for match in recognize_motifs(plan):
        describer = _DESCRIBERS.get(match.motif_name)
        if describer is None:
            continue
        desc = describer(match, value_of)
        if desc is not None:
            out.append(desc)
    out.sort(key=lambda d: (d.motif_name, d.parts))
    return out


__all__ = ["MotifDescription", "describe_motifs"]
