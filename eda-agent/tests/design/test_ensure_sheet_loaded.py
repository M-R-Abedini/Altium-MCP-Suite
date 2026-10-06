# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Getting a sheet open before anything is drawn on it.

_ensure_sheet_loaded is the emitter's first step for every sheet, and
everything after it targets whatever document is active. It had no test.

Its load path cannot observe its own outcome: App_RunProcess fires
Altium's RunProcess and answers success unconditionally, because
RunProcess reports no status. That is fine, and deliberately so, because
the caller's next call is set_active_document, which REFUSES a document
that is not loaded (NOT_LOADED) and aborts the sheet. The check lives
one step later rather than here.

What these pin is that the function reports what it actually knows, and
that both branches, open-existing and create-new, issue the right calls
and fail closed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eda_agent.design.emitter import EmitResult, _ensure_sheet_loaded


class _Bridge:
    """Records commands; can be told to raise for one of them."""

    def __init__(self, raise_on: str | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.raise_on = raise_on

    def send_command(self, command, params=None, timeout=None):
        self.calls.append((command, params or {}))
        if command == self.raise_on:
            raise RuntimeError(f"{command} refused")
        return {"success": True}

    def commands(self):
        return [c for c, _ in self.calls]


def _project(tmp_path: Path) -> Path:
    return tmp_path / "proj.PrjPcb"


def test_an_existing_sheet_is_opened_and_its_path_returned(tmp_path):
    from eda_agent.design._wiring import _sheet_path

    project = _project(tmp_path)
    sheet = _sheet_path(project, "main")
    sheet.parent.mkdir(parents=True, exist_ok=True)
    sheet.write_text("", encoding="utf-8")

    bridge, result = _Bridge(), EmitResult()
    got = _ensure_sheet_loaded(project, "main", bridge, result)

    assert got == sheet
    assert "application.run_process" in bridge.commands()
    # It must NOT create a document that already exists: that would
    # overwrite a sheet the user has work on.
    assert "application.create_document" not in bridge.commands()
    assert result.ok is True


def test_the_note_claims_only_what_was_requested(tmp_path):
    """RunProcess reports no status, so asserting "loaded" here would be
    evidence-free, and on a failed load the notes would contradict each
    other: "loaded" followed by set_active_document's NOT_LOADED."""
    from eda_agent.design._wiring import _sheet_path

    project = _project(tmp_path)
    sheet = _sheet_path(project, "main")
    sheet.parent.mkdir(parents=True, exist_ok=True)
    sheet.write_text("", encoding="utf-8")

    result = EmitResult()
    _ensure_sheet_loaded(project, "main", _Bridge(), result)
    note = " ".join(result.notes).lower()
    assert "requested" in note, result.notes


def test_a_missing_sheet_is_created_and_attached_to_the_project(tmp_path):
    """create_document's add_to_project attaches to the FOCUSED project,
    which right after project.create can still be Free Documents, so the
    explicit attach is what guarantees membership."""
    project = _project(tmp_path)
    project.parent.mkdir(parents=True, exist_ok=True)

    bridge, result = _Bridge(), EmitResult()
    got = _ensure_sheet_loaded(project, "power", bridge, result)

    assert got is not None
    commands = bridge.commands()
    assert "application.create_document" in commands
    assert "project.add_document" in commands
    assert commands.index("application.create_document") < commands.index(
        "project.add_document"), "attach must follow creation"
    assert result.ok is True


def test_the_created_sheet_is_named_and_pathed_from_the_project(tmp_path):
    project = _project(tmp_path)
    project.parent.mkdir(parents=True, exist_ok=True)
    bridge = _Bridge()
    _ensure_sheet_loaded(project, "power", bridge, EmitResult())

    create = next(p for c, p in bridge.calls
                  if c == "application.create_document")
    assert create["kind"] == "SCH"
    assert create["name"] == "power"
    assert create["file_path"].endswith("power.SchDoc")


@pytest.mark.parametrize("failing,exists", [
    ("application.run_process", True),
    ("application.create_document", False),
])
def test_a_failure_marks_the_result_not_ok_and_returns_none(
    failing, exists, tmp_path
):
    """Fail closed. Returning a path after a failed open would let the
    caller draw onto whatever document happened to be active."""
    from eda_agent.design._wiring import _sheet_path

    project = _project(tmp_path)
    project.parent.mkdir(parents=True, exist_ok=True)
    if exists:
        sheet = _sheet_path(project, "main")
        sheet.write_text("", encoding="utf-8")

    result = EmitResult()
    got = _ensure_sheet_loaded(project, "main", _Bridge(raise_on=failing),
                               result)

    assert got is None
    assert result.ok is False
    assert any(failing.split(".")[-1] in n or "failed" in n
               for n in result.notes), result.notes
