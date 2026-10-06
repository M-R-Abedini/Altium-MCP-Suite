# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Layer-stack CSV report.

``pcb_get_layer_stackup`` returns the structured stackup (copper layers with
the dielectric beneath each). This turns that into the conventional fab
stackup report a board house expects: one row per physical layer, copper and
dielectric interleaved top-to-bottom, with thickness in both mil and mm and
the dielectric constant where it applies.

Pure: no Altium, no bridge. The tool layer fetches the stackup and calls
``format_stackup_csv``.
"""

from __future__ import annotations

from typing import Any

from eda_agent.units import MM_PER_MIL

# Conventional fab-report columns, in order.
_HEADER = [
    "index",
    "layer",
    "type",
    "material",
    "thickness_mil",
    "thickness_mm",
    "dielectric_constant",
]


def _csv_field(value: Any) -> str:
    """Render one CSV field, quoting only when needed (comma/quote/newline)."""
    text = "" if value is None else str(value)
    if any(ch in text for ch in (",", '"', "\n", "\r")):
        return '"' + text.replace('"', '""') + '"'
    return text


def _csv_row(fields: list[Any]) -> str:
    return ",".join(_csv_field(f) for f in fields)


def _mm(mils: float) -> str:
    """Format a mil value as mm with 4 decimals, blank for a missing value."""
    if mils is None:
        return ""
    return f"{float(mils) * MM_PER_MIL:.4f}"


def _mil(mils: float) -> str:
    """Format a mil value with 4 decimals (same precision as the mm column,
    so a thin film doesn't round to zero in one unit but not the other)."""
    if mils is None:
        return ""
    return f"{float(mils):.4f}"


def _is_no_dielectric(dtype: Any) -> bool:
    return str(dtype or "").strip().lower() in ("", "none", "no", "nodielectric")


def format_stackup_csv(stackup: dict[str, Any]) -> str:
    """Format a ``pcb.get_layer_stackup`` result as a fab stackup CSV.

    Each input layer entry carries a copper layer and the dielectric below
    it; this emits a Copper row then (when present) a Dielectric row, so the
    output reads top-to-bottom like a real stackup table. Rows are indexed
    from 1. Returns the CSV text (with a trailing newline). Tolerates missing
    keys -- a field that is absent is left blank rather than raising.
    """
    layers = stackup.get("layers") or []
    lines = [_csv_row(_HEADER)]
    index = 0

    for entry in layers:
        # Copper row.
        index += 1
        cu_thick = entry.get("copper_thickness_mils")
        lines.append(_csv_row([
            index,
            entry.get("name", ""),
            "copper",
            "copper",
            _mil(cu_thick),
            _mm(cu_thick),
            "",
        ]))

        # Dielectric row (only when the entry has a real dielectric below).
        dtype = entry.get("dielectric_type")
        d_height = entry.get("dielectric_height_mils")
        if not _is_no_dielectric(dtype) and d_height not in (None, 0, 0.0):
            index += 1
            er = entry.get("dielectric_constant")
            lines.append(_csv_row([
                index,
                "",
                "dielectric",
                dtype or "",
                _mil(d_height),
                _mm(d_height),
                "" if er in (None, "") else f"{float(er):.3f}",
            ]))

    return "\n".join(lines) + "\n"


def stackup_total_thickness_mils(stackup: dict[str, Any]) -> float:
    """Sum of every copper + dielectric thickness, in mils (board thickness)."""
    total = 0.0
    for entry in stackup.get("layers") or []:
        cu = entry.get("copper_thickness_mils") or 0
        total += float(cu)
        if not _is_no_dielectric(entry.get("dielectric_type")):
            total += float(entry.get("dielectric_height_mils") or 0)
    return total
