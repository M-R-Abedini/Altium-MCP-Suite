# SPDX-License-Identifier: Apache-2.0
"""Read-only ECO evidence: exact window, provider cells, and optional native export."""
from __future__ import annotations

import json
from pathlib import Path
from . import windows, ocr


def read_tables(hwnd: int, max_cells: int = 4000) -> dict:
    """Use UIA GridPattern rather than interpreting blank MSAA nodes as rows."""
    import comtypes
    import comtypes.client
    comtypes.CoInitialize()
    try:
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as uia
        automation = comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)
        element = automation.ElementFromHandle(hwnd)
        condition = automation.CreateOrCondition(
            automation.CreatePropertyCondition(30003, 50028),
            automation.CreatePropertyCondition(30003, 50036))
        grids = element.FindAll(4, condition)
        tables, budget = [], max_cells
        grid_count = int(grids.Length)
        for index in range(min(grid_count, 32)):
            grid = grids.GetElement(index)
            table = {"index": index, "cells": [], "complete": False}
            try:
                pattern = grid.GetCurrentPattern(10006).QueryInterface(uia.IUIAutomationGridPattern)
                rows, cols = int(pattern.CurrentRowCount), int(pattern.CurrentColumnCount)
                table.update(rows=rows, columns=cols)
                if rows < 0 or cols < 0 or cols > 64 or (rows > 0 and cols == 0):
                    raise ValueError("Invalid provider grid dimensions")
                for row in range(rows):
                    if budget < cols:
                        break
                    cells = []
                    for col in range(cols):
                        item = pattern.GetItem(row, col)
                        text = str(item.CurrentName or "")
                        if not text:
                            try:
                                value = item.GetCurrentPattern(10002).QueryInterface(uia.IUIAutomationValuePattern)
                                text = str(value.CurrentValue or "")
                            except Exception:
                                pass
                        cells.append(text)
                    table["cells"].append(cells)
                    budget -= cols
                table["complete"] = (len(table["cells"]) == rows and
                                     (rows == 0 or all(any(c.strip() for c in row) for row in table["cells"])))
            except Exception as exc:
                table["error"] = str(exc)
            tables.append(table)
        return {"tables": tables, "grid_read_complete": bool(tables) and grid_count <= 32 and all(t["complete"] for t in tables)}
    finally:
        comtypes.CoUninitialize()


def export(pid: int, output_path: str, phase: str = "unknown", native_report_path: str = "") -> dict:
    if phase not in {"unknown", "pre_execution", "post_execution"}:
        return {"ok": False, "reason": "phase must be unknown, pre_execution or post_execution"}
    matches = [d for d in windows.dialogs(pid)
               if (d.title or "").strip().casefold() == "engineering change order"]
    if len(matches) != 1:
        return {"ok": False, "reason": "Exactly one Engineering Change Order window is required; no main-window fallback"}
    target = matches[0]
    path = Path(output_path).resolve()
    if path.suffix.lower() != ".json" or path.exists() or path.with_suffix(".png").exists():
        return {"ok": False, "reason": "Choose a new .json output path; existing evidence is not overwritten"}
    result = {"dialog_title": target.title, "hwnd": target.hwnd,
              "phase": phase, "phase_source": "caller", "eco_verified": False,
              "note": "Pre-execution validation is not physical pad verification. Verify native comparison and pad assignments after execution."}
    if native_report_path:
        native = Path(native_report_path).resolve()
        if native.suffix.lower() not in {".txt", ".htm", ".html", ".csv", ".xml"} or not native.is_file() or native.stat().st_size > 10 * 1024 * 1024:
            return {"ok": False, "reason": "Native report must be an existing text export no larger than 10 MiB"}
        data = native.read_bytes()
        encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            text = data.decode("cp1252")
        result["native_report"] = {"path": str(native), "text": text, "interpretation": "raw native export; no inferred pass/fail"}
    try:
        result.update(read_tables(target.hwnd))
    except Exception as exc:
        result.update(tables=[], grid_read_complete=False, grid_error=str(exc))
    path.parent.mkdir(parents=True, exist_ok=True)
    image_path = path.with_suffix(".png")
    result["screenshot_path"] = str(image_path)
    if not result["grid_read_complete"]:
        result["grid_warning"] = "The provider does not expose complete readable cells. Use the exact dialog image or Altium's Report Changes export; blank nodes do not mean zero errors."
    owned_json, owned_image = False, False
    try:
        # Reserve both names exclusively before the capture can write a file.
        with path.open("x", encoding="utf-8") as stream:
            owned_json = True
            with image_path.open("xb"):
                owned_image = True
            if not ocr.capture_png(target.hwnd, str(image_path)):
                raise RuntimeError("Exact ECO window capture failed")
            json.dump(result, stream, ensure_ascii=False, indent=2)
    except Exception as exc:
        if owned_json:
            path.unlink(missing_ok=True)
        if owned_image:
            image_path.unlink(missing_ok=True)
        return {"ok": False, "reason": "Evidence was not exported: " + str(exc)}
    return {"ok": True, "path": str(path), "screenshot_path": str(image_path),
            "grid_read_complete": result["grid_read_complete"], "eco_verified": False}
