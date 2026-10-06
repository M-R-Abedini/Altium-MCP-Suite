# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Value-consistency checks across recognised sub-circuits.

Some components are only correct when their values MATCH: a crystal's two load
capacitors must be equal (asymmetric loading pulls the oscillator), and the
matched series elements on the two legs of a differential pair (the series
resistors, the AC-coupling caps) must be equal (a mismatch unbalances the
pair). These are real design errors invisible to a connectivity check -- both
parts are present and wired correctly, only the chosen VALUES disagree.

This module composes the recognition layers (the crystal-load motif, the
differential-pair detector) with the value-string parser to flag those
mismatches from the plan alone. It is surfaced through the plan ERC-lite as a
``matched_value_mismatch`` warning.

NDA scope: only the current plan's topology and part values.
"""

from __future__ import annotations

from typing import Optional

from eda_agent.design.plan import DesignPlan
from eda_agent.design.plan_erc import ErcIssue
from eda_agent.design.value_parser import try_parse_value


def _values_equal(a: str, b: str, *, rel: float = 1e-3) -> Optional[bool]:
    """True/False if both strings parse (equal within ``rel``); None if either
    is missing or unparseable (then there is nothing to compare)."""
    va, vb = try_parse_value(a), try_parse_value(b)
    if va is None or vb is None:
        return None
    if va == 0.0 or vb == 0.0:
        return va == vb
    return abs(va - vb) / max(abs(va), abs(vb)) <= rel


def check_matched_values(plan: DesignPlan) -> list[ErcIssue]:
    """Flag matched components whose chosen values disagree (see module doc).

    Covers a crystal's two load caps and a differential pair's matched series
    elements (grouped by kind). Returns ``matched_value_mismatch`` warnings;
    empty when nothing matched is present or all matched values agree. Pairs
    where a value is missing/unparseable are skipped (the ERC malformed-value
    check handles those separately).
    """
    from eda_agent.design.diffpairs import detect_diff_pairs
    from eda_agent.design.motifs import _kind_from_refdes, recognize_motifs

    value_of = {p.refdes: (p.value or "").strip() for p in plan.parts}
    issues: list[ErcIssue] = []

    def _flag(a: str, b: str, context: str) -> None:
        eq = _values_equal(value_of.get(a, ""), value_of.get(b, ""))
        if eq is False:
            issues.append(ErcIssue(
                code="matched_value_mismatch", severity="warning",
                message=(f"{context} {a} ({value_of[a]}) and {b} "
                         f"({value_of[b]}) differ; they should be equal"),
                refs=(a, b)))

    # Crystal load caps: the two C-kind parts of each crystal-load motif.
    for match in recognize_motifs(plan):
        if match.motif_name != "crystal_load":
            continue
        caps = sorted(r for r in match.components
                      if _kind_from_refdes(r) == "C")
        if len(caps) == 2:
            _flag(caps[0], caps[1], "crystal load caps")

    # Differential-pair matched series elements, grouped by kind.
    for dp in detect_diff_pairs(plan):
        by_kind: dict[str, list[str]] = {}
        for ref in dp.series_parts:
            by_kind.setdefault(_kind_from_refdes(ref), []).append(ref)
        for refs in by_kind.values():
            if len(refs) < 2:
                continue
            ordered = sorted(refs)
            base = ordered[0]
            for other in ordered[1:]:
                _flag(base, other, "matched differential parts")

    return issues


__all__ = ["check_matched_values"]
