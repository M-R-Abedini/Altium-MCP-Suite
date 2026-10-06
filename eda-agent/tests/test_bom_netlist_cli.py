# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the `eda-agent bom` and `eda-agent netlist` CLIs (roadmap V1)."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from eda_agent.server import _run_bom, _run_netlist

_FIX = Path(__file__).resolve().parent / "integration" / "fixtures"
SCH = _FIX / "main.SchDoc"
PRJ = _FIX / "EDAAgentTest.PrjPcb"


# --- bom ---------------------------------------------------------------------
def test_bom_disabled_without_optin(capsys, monkeypatch):
    monkeypatch.delenv("EDA_AGENT_HEADLESS_REVIEW", raising=False)
    rc = _run_bom(Namespace(file=SCH, offline=False, csv=False, json=False))
    assert rc == 2
    assert "disabled by default" in capsys.readouterr().err


def test_bom_human_output(capsys):
    rc = _run_bom(Namespace(file=SCH, offline=True, csv=False, json=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "14 parts" in out


def test_bom_csv_output(capsys):
    rc = _run_bom(Namespace(file=SCH, offline=True, csv=True, json=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("Quantity,Designators,MPN")


def test_bom_project_via_env_var(capsys, monkeypatch):
    monkeypatch.setenv("EDA_AGENT_HEADLESS_REVIEW", "1")
    rc = _run_bom(Namespace(file=PRJ, offline=False, csv=False, json=True))
    assert rc == 0
    lines = json.loads(capsys.readouterr().out)
    assert sum(ln["quantity"] for ln in lines) == 14


def test_bom_unreadable_exit_two(tmp_path, capsys):
    junk = tmp_path / "bad.SchDoc"
    junk.write_bytes(b"not ole")
    rc = _run_bom(Namespace(file=junk, offline=True, csv=False, json=False))
    assert rc == 2
    assert "cannot read" in capsys.readouterr().err


# --- netlist -----------------------------------------------------------------
def test_netlist_disabled_without_optin(capsys, monkeypatch):
    monkeypatch.delenv("EDA_AGENT_HEADLESS_REVIEW", raising=False)
    rc = _run_netlist(Namespace(file=SCH, offline=False, json=False,
                                fail_on="error"))
    assert rc == 2


def test_netlist_reports_shorts_and_fails(capsys):
    # main.SchDoc is a known-shorted emit -> net_short -> exit 1 on error.
    rc = _run_netlist(Namespace(file=SCH, offline=True, json=False,
                                fail_on="error"))
    assert rc == 1
    assert "net_short" in capsys.readouterr().out


def test_netlist_fail_on_never_is_zero(capsys):
    rc = _run_netlist(Namespace(file=SCH, offline=True, json=False,
                                fail_on="never"))
    assert rc == 0


def test_netlist_json_has_nets(capsys):
    rc = _run_netlist(Namespace(file=SCH, offline=True, json=True,
                                sarif=False, fail_on="never"))
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["nets"] and "findings" in doc


def test_netlist_sarif_output(capsys):
    rc = _run_netlist(Namespace(file=SCH, offline=True, json=False,
                                sarif=True, fail_on="never"))
    assert rc == 0
    sarif = json.loads(capsys.readouterr().out)
    assert sarif["version"] == "2.1.0"
    # the shorted fixture produces net_short results
    rule_ids = {r["ruleId"] for r in sarif["runs"][0]["results"]}
    assert "net_short" in rule_ids
