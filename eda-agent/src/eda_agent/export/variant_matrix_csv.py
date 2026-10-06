# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Variant fitted/not-fitted matrix CSV.

``project.get_variant_matrix`` returns every flattened component (rows) and its
status under each variant (cells: Fitted / Not Fitted / Alternate). This formats
that as the conventional matrix CSV -- one component per row, one column per
variant -- which merges cleanly with a BOM in a spreadsheet.

Pure: no Altium, no bridge.
"""

from __future__ import annotations

from typing import Any


def _csv_field(value: Any) -> str:
    text = "" if value is None else str(value)
    if any(ch in text for ch in (",", '"', "\n", "\r")):
        return '"' + text.replace('"', '""') + '"'
    return text


def _csv_row(fields: list[Any]) -> str:
    return ",".join(_csv_field(f) for f in fields)


def format_variant_matrix_csv(matrix: dict[str, Any]) -> str:
    """Format a ``project.get_variant_matrix`` result as a CSV table.

    Header is ``Component`` followed by one column per variant name. Each row
    is a component designator and its per-variant status, taken positionally
    from the row's ``cells`` (a missing cell is left blank, an extra cell is
    dropped, so the table stays rectangular). Returns the CSV text with a
    trailing newline.
    """
    variants = list(matrix.get("variants") or [])
    rows = matrix.get("rows") or []

    lines = [_csv_row(["Component", *variants])]
    n = len(variants)
    for row in rows:
        cells = row.get("cells") or []
        padded = [cells[i] if i < len(cells) else "" for i in range(n)]
        lines.append(_csv_row([row.get("designator", ""), *padded]))

    return "\n".join(lines) + "\n"
