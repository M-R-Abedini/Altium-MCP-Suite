# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Headless Altium project reader (roadmap V1: hardware CI).

Real designs are multi-sheet: a ``.PrjPcb`` project owns several ``.SchDoc``
sheets. The authoritative sheet list lives in the sibling
``.PrjPcbStructure`` file as pipe-delimited records:

    Record=TopLevelDocument|FileName=main.SchDoc|SheetNumber=
    Record=SubDocument|FileName=power.SchDoc|...

(The ``.PrjPcb`` INI itself only reliably carries settings; its print-view
paths can point at stale/renamed files, so we trust the structure file.)
This lets ``eda-agent review`` cover a whole project, not one sheet, and
enables cross-sheet checks (e.g. a net label that appears on only one sheet
but nowhere else is a likely typo). Fully offline.
"""

from __future__ import annotations

from pathlib import Path


def read_project_sheets(prjpcb_path: str | Path) -> list[Path]:
    """Return the .SchDoc sheets of a project, resolved to absolute paths.

    Reads the sibling ``<project>.PrjPcbStructure`` for ``FileName=*.SchDoc``
    records and resolves each relative to the project directory. Missing
    files are skipped (the caller can report them). Returns [] if the
    structure file is absent.
    """
    prjpcb_path = Path(prjpcb_path)
    project_dir = prjpcb_path.parent
    structure = prjpcb_path.with_suffix(".PrjPcbStructure")
    if not structure.exists():
        return []

    text = structure.read_text(encoding="utf-8-sig", errors="replace")
    sheets: list[Path] = []
    seen: set[str] = set()
    for line in text.splitlines():
        for field in line.split("|"):
            if not field.startswith("FileName="):
                continue
            name = field[len("FileName="):].strip()
            if not name.lower().endswith(".schdoc"):
                continue
            # Structure filenames are project-relative (usually a bare name).
            resolved = (project_dir / name)
            key = resolved.name.lower()
            if key in seen:
                continue
            seen.add(key)
            if resolved.exists():
                sheets.append(resolved)
    return sheets
