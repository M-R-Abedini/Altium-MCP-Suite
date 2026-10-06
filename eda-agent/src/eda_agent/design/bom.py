# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Derive a bill of materials from a DesignPlan's parts (offline).

A plan carries a ``bom`` field of :class:`~eda_agent.design.plan.BomLine`,
but nothing builds it -- the planner has to hand-group refdes, which is
redundant with the parts it already authored and easy to get wrong (a
forgotten part, a miscounted quantity). This rolls the parts up into BOM
lines by grouping identical orderable items, so the BOM falls out of the
authoring instead of being a separate manual pass.

Grouping rule, matching how a real BOM consolidates:

* parts that carry an ``mpn`` group by ``(manufacturer, mpn)`` -- one
  orderable line per distinct manufacturer part number;
* parts without an ``mpn`` group by ``(lib_ref, value, footprint)`` -- so
  every 100 nF 0402 cap is one line, but a 100 nF cap with a specific mpn
  stays its own line.

Naming-agnostic and deterministic: refdes within a line and the lines
themselves are natural-sorted (R2 before R10), so the same plan always
yields the same BOM.
"""

from __future__ import annotations

import re

from eda_agent.design.plan import BomLine, DesignPlan

_REFDES_SPLIT = re.compile(r"^([A-Za-z]+)(\d+)([A-Za-z]?)$")


def _natural_key(refdes: str) -> tuple:
    """Sort key so R2 precedes R10 and prefixes group (C* before R*)."""
    m = _REFDES_SPLIT.match(refdes)
    if m:
        return (m.group(1), int(m.group(2)), m.group(3))
    return (refdes, 0, "")


def generate_bom(plan: DesignPlan) -> list[BomLine]:
    """Roll ``plan.parts`` up into consolidated BOM lines.

    Returns a deterministically ordered list of :class:`BomLine`. Does not
    mutate ``plan``; the caller decides whether to set ``plan.bom``.
    """
    groups: dict[tuple, list] = {}
    order: list[tuple] = []
    for part in plan.parts:
        if part.mpn and part.mpn.strip():
            key = ("mpn", (part.manufacturer or "").strip(),
                   part.mpn.strip())
        else:
            key = ("generic", part.lib_ref, (part.value or "").strip(),
                   (part.footprint or "").strip())
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(part)

    lines: list[BomLine] = []
    for key in order:
        members = groups[key]
        refdes_list = sorted((p.refdes for p in members), key=_natural_key)
        first = members[0]
        description = first.value or first.lib_ref
        lines.append(BomLine(
            refdes_list=refdes_list,
            manufacturer=first.manufacturer,
            mpn=first.mpn,
            description=description,
            qty=len(members),
        ))
    lines.sort(key=lambda bl: _natural_key(bl.refdes_list[0]))
    return lines


def bom_summary(lines: list[BomLine]) -> dict:
    """Headline numbers for a generated BOM."""
    total_qty = sum(bl.qty for bl in lines)
    without_mpn = [bl.refdes_list[0] for bl in lines if not bl.mpn]
    return {
        "line_count": len(lines),
        "total_parts": total_qty,
        "lines_without_mpn": without_mpn,
    }


__all__ = ["generate_bom", "bom_summary"]
