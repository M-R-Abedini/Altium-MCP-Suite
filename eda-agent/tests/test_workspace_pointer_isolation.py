# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The test suite must never repoint a live Altium bridge.

``C:\\ProgramData\\eda-agent\\workspace-path.txt`` is machine-global: it
tells the running DelphiScript loop which directory to poll, and the
script reads it once at startup. A test that writes its tmp workspace
there redirects a live session at a throwaway folder. The failure is
brutal to diagnose from the outside, because the script stays healthy
and simply reports zero requests while every real call times out.

These tests pin the two layers that prevent it: the writer refuses the
machine-global path under pytest, and the pointer location is
overridable so tests can still exercise the writer against scratch.
"""

from __future__ import annotations

from pathlib import Path

from eda_agent.config import (
    WORKSPACE_POINTER_ENV,
    WORKSPACE_POINTER_FILE,
    workspace_pointer_file,
    write_workspace_pointer,
)


def test_pointer_write_is_refused_for_the_global_path_under_pytest(
    tmp_path, monkeypatch,
):
    """With no override, a write under pytest must be a no-op.

    Asserted by content, not by mocking: record whatever the real file
    holds (it may not exist on CI), attempt the write a test would make,
    and require the observable state to be unchanged.
    """
    monkeypatch.delenv(WORKSPACE_POINTER_ENV, raising=False)
    existed = WORKSPACE_POINTER_FILE.exists()
    before = (
        WORKSPACE_POINTER_FILE.read_bytes() if existed else None
    )

    write_workspace_pointer(tmp_path / "some" / "tmp" / "workspace")

    assert WORKSPACE_POINTER_FILE.exists() is existed, (
        "a test run created or removed the machine-global pointer file"
    )
    if existed:
        assert WORKSPACE_POINTER_FILE.read_bytes() == before, (
            "a test run rewrote the machine-global pointer, which would "
            "redirect any live Altium polling loop"
        )


def test_pointer_override_redirects_the_write(tmp_path, monkeypatch):
    """With the override set, the writer targets the scratch path."""
    scratch = tmp_path / "pointer" / "workspace-path.txt"
    monkeypatch.setenv(WORKSPACE_POINTER_ENV, str(scratch))

    assert workspace_pointer_file() == scratch

    workspace = tmp_path / "ws"
    write_workspace_pointer(workspace)

    assert scratch.exists()
    written = scratch.read_text(encoding="mbcs").strip()
    assert written == str(workspace) + "\\", (
        "pointer must hold the workspace path with a trailing separator"
    )


def test_pointer_path_defaults_to_the_global_file(monkeypatch):
    """Without an override the resolver still returns the real path, so
    production installs keep working."""
    monkeypatch.delenv(WORKSPACE_POINTER_ENV, raising=False)
    assert workspace_pointer_file() == WORKSPACE_POINTER_FILE


def test_conftest_isolates_the_pointer_for_the_session(monkeypatch):
    """The session fixture points the whole run at scratch, so even a
    code path that bypasses the writer guard cannot reach the real file."""
    import os

    value = os.environ.get(WORKSPACE_POINTER_ENV)
    assert value, (
        "the session-scoped conftest fixture should set "
        f"{WORKSPACE_POINTER_ENV} for every test"
    )
    assert Path(value) != WORKSPACE_POINTER_FILE
