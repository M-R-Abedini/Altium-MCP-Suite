# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the `eda-agent review` CLI (roadmap V1 step 4)."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from eda_agent.server import _run_review

FIXTURE = Path(__file__).resolve().parent / "integration" / "fixtures" / "main.SchDoc"


def _ns(**kw):
    # Every CLI review invocation is opt-in; default the flag on for tests
    # that are exercising the review itself (the gate has its own tests).
    kw.setdefault("offline", True)
    return Namespace(**kw)


def test_review_disabled_by_default_exit_two(capsys, monkeypatch):
    # No --offline and no env var -> refuse with a pointer to the live tools.
    monkeypatch.delenv("EDA_AGENT_HEADLESS_REVIEW", raising=False)
    rc = _run_review(Namespace(file=FIXTURE, json=False, offline=False))
    assert rc == 2
    err = capsys.readouterr().err
    assert "disabled by default" in err
    assert "proj_run_erc" in err  # points at the preferred live path


def test_review_env_var_enables(capsys, monkeypatch):
    # The env var is the MCP-side opt-in; it also enables the CLI.
    monkeypatch.setenv("EDA_AGENT_HEADLESS_REVIEW", "1")
    rc = _run_review(Namespace(file=FIXTURE, json=False, offline=False))
    assert rc == 0
    assert "14 components" in capsys.readouterr().out


def test_review_fixture_exit_zero(capsys):
    rc = _run_review(_ns(file=FIXTURE, json=False))
    assert rc == 0  # no error-severity findings
    out = capsys.readouterr().out
    assert "14 components" in out
    assert "missing_datasheet" in out


def test_review_json_mode(capsys):
    rc = _run_review(_ns(file=FIXTURE, json=True))
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["component_count"] == 14
    assert "summary" in report and "findings" in report


def test_fail_on_threshold(capsys):
    # Fixture has 0 errors, 4 warnings, 2 info.
    assert _run_review(_ns(file=FIXTURE, json=False, fail_on="error")) == 0
    assert _run_review(_ns(file=FIXTURE, json=False, fail_on="warning")) == 1
    assert _run_review(_ns(file=FIXTURE, json=False, fail_on="info")) == 1
    assert _run_review(_ns(file=FIXTURE, json=False, fail_on="never")) == 0


def test_review_unreadable_file_exit_two(tmp_path, capsys):
    junk = tmp_path / "bad.SchDoc"
    junk.write_bytes(b"not ole")
    rc = _run_review(_ns(file=junk, json=False))
    assert rc == 2
    assert "cannot read" in capsys.readouterr().err


def test_review_errors_exit_one(tmp_path, capsys, monkeypatch):
    # A design with a designator collision must exit 1 (fail CI).
    import eda_agent.server as srv
    monkeypatch.setattr(
        "eda_agent.fileio.review.read_schematic_components",
        lambda p: [
            {"designator": "R1", "mpn": "M", "manufacturer": "X",
             "value": "1k", "datasheet": "u", "lib_reference": "RES"},
            {"designator": "R1", "mpn": "M", "manufacturer": "X",
             "value": "1k", "datasheet": "u", "lib_reference": "RES"},
        ],
    )
    monkeypatch.setattr(
        "eda_agent.fileio.review.read_schematic_nets", lambda p: [])
    monkeypatch.setattr(
        "eda_agent.fileio.review.read_schematic_document_info",
        lambda p: {"title": "T", "revision": "1", "sheet": {}})
    rc = _run_review(_ns(file=Path("whatever.SchDoc"), json=False))
    assert rc == 1
    assert "designator_collision" in capsys.readouterr().out


def test_review_subcommand_registered():
    # The argument parser exposes `review` with a file arg and the opt-in
    # --offline flag (required, since the review is off by default).
    import eda_agent.server as srv
    import sys
    argv = sys.argv
    try:
        sys.argv = ["eda-agent", "review", "--offline", str(FIXTURE)]
        rc = srv.main()
    finally:
        sys.argv = argv
    assert rc == 0
