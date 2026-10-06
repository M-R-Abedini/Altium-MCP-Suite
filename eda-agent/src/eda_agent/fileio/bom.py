# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Headless BOM consolidation (roadmap V1: hardware CI companion).

The ``.SchDoc`` reader already extracts every placed component with its
normalized ``mpn`` / ``manufacturer`` / ``value`` / ``datasheet``. This turns
that flat list into a purchasable Bill of Materials, one line per distinct
part, designators grouped and naturally sorted, quantity summed, with no
running Altium and no license. It is the read-only complement to the live
``proj_get_bom`` / ``proj_export_bom_html`` tools, for a file on disk or a CI
artifact.

Grouping key is ``(mpn, value, lib_reference)`` case-folded: two parts merge
into one line only when they are the same orderable item. Parts with no
designator are skipped (they are not placed instances). Pure Python.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Split a reference designator into (letter-prefix, number, suffix) so R2
# sorts before R10 (natural order), and un-numbered names still sort stably.
_DESIG_RE = re.compile(r"^([A-Za-z]*)(\d*)(.*)$")


def _desig_sort_key(designator: str):
    m = _DESIG_RE.match(designator.strip())
    if not m:
        return (designator, -1, "")
    prefix, number, rest = m.groups()
    return (prefix.upper(), int(number) if number else -1, rest)


def consolidate_bom(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group a parsed component list into consolidated BOM line items.

    Returns a list of ``{quantity, designators, mpn, manufacturer, value,
    lib_reference, datasheet}``, one entry per distinct orderable part,
    ordered by the first (naturally-sorted) designator on each line. A
    component with no designator is skipped.
    """
    groups: dict[tuple, dict[str, Any]] = {}
    for c in components:
        designator = (c.get("designator") or "").strip()
        if not designator:
            continue
        mpn = (c.get("mpn") or "").strip()
        value = (c.get("value") or "").strip()
        lib_reference = (c.get("lib_reference") or "").strip()
        key = (mpn.lower(), value.lower(), lib_reference.lower())
        line = groups.setdefault(key, {
            "quantity": 0,
            "designators": [],
            "mpn": mpn,
            "manufacturer": (c.get("manufacturer") or "").strip(),
            "value": value,
            "lib_reference": lib_reference,
            "datasheet": (c.get("datasheet") or "").strip(),
        })
        line["designators"].append(designator)
        line["quantity"] += 1

    lines = list(groups.values())
    for line in lines:
        line["designators"].sort(key=_desig_sort_key)
    lines.sort(key=lambda g: _desig_sort_key(g["designators"][0]))
    return lines


def bom_from_file(path: str | Path) -> list[dict[str, Any]]:
    """Read a ``.SchDoc`` or ``.PrjPcb`` and return its consolidated BOM.

    A ``.SchDoc`` is read directly; a ``.PrjPcb`` aggregates every sheet
    (a designator that repeats across sheets, e.g. a multi-part component,
    is de-duplicated so it counts once). Pure Python; no Altium.
    """
    path = Path(path)
    if path.suffix.lower() == ".schdoc":
        from .altium_sch import read_schematic_components
        return consolidate_bom(read_schematic_components(path))

    from .altium_project import read_project_sheets
    from .altium_sch import read_schematic_components

    by_designator: dict[str, dict] = {}
    for sheet in read_project_sheets(path):
        for comp in read_schematic_components(sheet):
            d = (comp.get("designator") or "").strip()
            if d:
                by_designator.setdefault(d, comp)  # first sheet wins per refdes
    return consolidate_bom(list(by_designator.values()))


def bom_to_csv(lines: list[dict[str, Any]]) -> str:
    """Render consolidated BOM lines as CSV (header + one row per line).

    Designators are joined with a comma-space in a single quoted cell. Uses
    ``csv`` so quoting/escaping is correct on every field.
    """
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        ["Quantity", "Designators", "MPN", "Manufacturer", "Value",
         "LibReference", "Datasheet"])
    for line in lines:
        writer.writerow([
            line["quantity"],
            ", ".join(line["designators"]),
            line["mpn"],
            line["manufacturer"],
            line["value"],
            line["lib_reference"],
            line["datasheet"],
        ])
    return buf.getvalue()
