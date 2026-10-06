# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Offline tests for the variant fitted/not-fitted matrix CSV formatter."""

from __future__ import annotations

from eda_agent.export.variant_matrix_csv import format_variant_matrix_csv

_MATRIX = {
    "variants": ["Standard", "Lite"],
    "component_count": 3,
    "rows": [
        {"designator": "R1", "cells": ["Fitted", "Fitted"]},
        {"designator": "R2", "cells": ["Fitted", "Not Fitted"]},
        {"designator": "U1", "cells": ["Fitted", "Alternate"]},
    ],
}


def _rows(text: str) -> list[list[str]]:
    return [line.split(",") for line in text.strip().split("\n")]


def test_header_is_component_then_variants() -> None:
    rows = _rows(format_variant_matrix_csv(_MATRIX))
    assert rows[0] == ["Component", "Standard", "Lite"]


def test_one_row_per_component() -> None:
    rows = _rows(format_variant_matrix_csv(_MATRIX))
    assert len(rows) == 1 + 3
    assert [r[0] for r in rows[1:]] == ["R1", "R2", "U1"]


def test_cells_align_to_variants() -> None:
    rows = _rows(format_variant_matrix_csv(_MATRIX))
    assert rows[2] == ["R2", "Fitted", "Not Fitted"]
    assert rows[3] == ["U1", "Fitted", "Alternate"]


def test_missing_cell_is_blank_padded() -> None:
    matrix = {
        "variants": ["A", "B", "C"],
        "rows": [{"designator": "R9", "cells": ["Fitted"]}],
    }
    rows = _rows(format_variant_matrix_csv(matrix))
    assert rows[1] == ["R9", "Fitted", "", ""]


def test_extra_cell_is_truncated() -> None:
    matrix = {
        "variants": ["A"],
        "rows": [{"designator": "R9", "cells": ["Fitted", "Not Fitted"]}],
    }
    rows = _rows(format_variant_matrix_csv(matrix))
    assert rows[1] == ["R9", "Fitted"]


def test_no_variants_yields_component_only() -> None:
    out = format_variant_matrix_csv({"variants": [], "rows": [
        {"designator": "R1", "cells": []}]})
    assert _rows(out) == [["Component"], ["R1"]]


def test_designator_with_comma_quoted() -> None:
    out = format_variant_matrix_csv(
        {"variants": ["A"], "rows": [{"designator": "R1,x", "cells": ["Fitted"]}]})
    assert '"R1,x"' in out
