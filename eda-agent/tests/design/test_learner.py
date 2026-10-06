# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for design.learner: capture user placement edits as training data.

We mock the bridge so no Altium is involved. The snapshot file is the
contract the orchestrator persists; we hand-build one here that
mirrors what `execute_plan_via_canvas_from_json` would have written.

Coverage:
- Moves above the threshold produce one row per moved part.
- Moves below the threshold do not.
- Anchor picks the highest-pin-count non-power netlist neighbor when
  one exists.
- Anchor falls back to spatial nearest when only power/ground nets
  connect the part.
- Missing snapshot is a clean failure (no crash, ok=False).
- design_id is stable for the same plan, varies across plans.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from eda_agent.design.learner import _design_id_for, learn_from_layout
from eda_agent.design.plan import DesignPlan


_LIB = "/fake/lib.SchLib"


class FakeBridge:
    """Returns canned query_objects responses; ignores all other calls."""

    def __init__(self, post_edit_positions: dict[str, dict[str, int]]) -> None:
        self._positions = post_edit_positions

    def send_command(self, command: str, params: dict[str, Any]) -> Any:
        if command == "generic.query_objects":
            objects = []
            for refdes, pos in self._positions.items():
                # Convert degrees back to Altium's 0..3 enum.
                orient_enum = (pos["rotation"] // 90) % 4
                objects.append({
                    "Designator.Text": refdes,
                    "Location.X": pos["x"],
                    "Location.Y": pos["y"],
                    "Orientation": orient_enum,
                })
            return {"objects": objects, "count": len(objects)}
        raise NotImplementedError(command)


def _basic_snapshot() -> dict[str, Any]:
    """Plan + canvas snapshot mirroring what the orchestrator writes.

    R1 in series with C1 between a signal net and GND. Both placed.
    """
    plan = {
        "spec": "rc", "summary": "rc",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": [
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "timing_r"},
            {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "timing_cap"},
        ],
        "nets": [
            {"name": "SIG", "pins": [
                {"refdes": "R1", "pin": "2"},
                {"refdes": "C1", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "R1", "pin": "1"},
                {"refdes": "C1", "pin": "2"}]},
        ],
    }
    canvas = {
        "sheets": [{"name": "main", "title": "", "size": "A4",
                    "width_mils": 11500, "height_mils": 7600}],
        "instances": [
            {"refdes": "R1", "lib_path": _LIB, "lib_ref": "RES",
             "x": 1000, "y": 1000, "rotation": 0, "sheet": "main"},
            {"refdes": "C1", "lib_path": _LIB, "lib_ref": "CAP",
             "x": 2000, "y": 1000, "rotation": 0, "sheet": "main"},
        ],
        "wires": [], "labels": [], "power_ports": [], "junctions": [],
    }
    return {"plan": plan, "canvas": canvas, "parameter_stamps": {}}


def _write_snapshot(tmp_path: Path, snapshot: dict[str, Any]) -> str:
    project_path = tmp_path / "test.PrjPcb"
    snapshot_path = project_path.with_suffix(".canvas.json")
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    return str(project_path)


def test_learn_records_moved_parts(tmp_path: Path):
    snapshot = _basic_snapshot()
    project_path = _write_snapshot(tmp_path, snapshot)
    # R1 moved 500 mils right; C1 unchanged.
    bridge = FakeBridge({
        "R1": {"x": 1500, "y": 1000, "rotation": 0},
        "C1": {"x": 2000, "y": 1000, "rotation": 0},
    })
    log_path = tmp_path / "edits.jsonl"
    result = learn_from_layout(
        project_path, bridge=bridge, log_path=log_path,
    )
    assert result["ok"]
    assert result["rows_appended"] == 1
    assert result["refdes_moved"] == ["R1"]
    assert result["refdes_unchanged"] == ["C1"]
    rows = [json.loads(l) for l in log_path.read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["refdes"] == "R1"
    assert row["part_role"] == "timing_r"
    assert row["dx_mils"] == 500
    assert row["dy_mils"] == 0


def test_learn_ignores_subthreshold_moves(tmp_path: Path):
    snapshot = _basic_snapshot()
    project_path = _write_snapshot(tmp_path, snapshot)
    # R1 nudged 10 mils (< 25 threshold).
    bridge = FakeBridge({
        "R1": {"x": 1010, "y": 1000, "rotation": 0},
        "C1": {"x": 2000, "y": 1000, "rotation": 0},
    })
    log_path = tmp_path / "edits.jsonl"
    result = learn_from_layout(
        project_path, bridge=bridge, log_path=log_path,
    )
    assert result["rows_appended"] == 0
    assert "R1" in result["refdes_unchanged"]
    assert not log_path.exists()


def test_learn_records_rotation_change(tmp_path: Path):
    snapshot = _basic_snapshot()
    project_path = _write_snapshot(tmp_path, snapshot)
    # R1 rotated 90 deg with no translation.
    bridge = FakeBridge({
        "R1": {"x": 1000, "y": 1000, "rotation": 90},
        "C1": {"x": 2000, "y": 1000, "rotation": 0},
    })
    log_path = tmp_path / "edits.jsonl"
    result = learn_from_layout(
        project_path, bridge=bridge, log_path=log_path,
    )
    assert result["rows_appended"] == 1
    row = json.loads(log_path.read_text().splitlines()[0])
    assert row["rot_delta_deg"] == 90
    assert row["dx_mils"] == 0


def test_learn_anchor_via_signal_net(tmp_path: Path):
    """When a moved passive shares a signal net with an IC, the IC is the
    anchor, not whatever GND-mate happens to be spatially close."""
    snapshot = {
        "plan": {
            "spec": "x", "summary": "x",
            "sheets": [{"name": "main", "size": "A4"}],
            "parts": [
                {"refdes": "U1", "lib_ref": "OPAMP", "lib_path": _LIB,
                 "status": "existing", "sheet": "main", "role": "ic"},
                {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
                 "status": "existing", "sheet": "main", "role": "feedback_r"},
                {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
                 "status": "existing", "sheet": "main", "role": "decoup_cap"},
            ],
            "nets": [
                # R1 shares OUT with U1; C1 shares GND only.
                {"name": "OUT", "pins": [
                    {"refdes": "U1", "pin": "1"},
                    {"refdes": "R1", "pin": "1"}]},
                {"name": "GND", "is_ground": True, "pins": [
                    {"refdes": "U1", "pin": "2"},
                    {"refdes": "R1", "pin": "2"},
                    {"refdes": "C1", "pin": "1"},
                    {"refdes": "C1", "pin": "2"}]},
            ],
        },
        "canvas": {
            "sheets": [{"name": "main", "title": "", "size": "A4",
                        "width_mils": 11500, "height_mils": 7600}],
            "instances": [
                {"refdes": "U1", "lib_path": _LIB, "lib_ref": "OPAMP",
                 "x": 5000, "y": 5000, "rotation": 0, "sheet": "main"},
                {"refdes": "R1", "lib_path": _LIB, "lib_ref": "RES",
                 "x": 3000, "y": 3000, "rotation": 0, "sheet": "main"},
                {"refdes": "C1", "lib_path": _LIB, "lib_ref": "CAP",
                 "x": 3100, "y": 3000, "rotation": 0, "sheet": "main"},
            ],
            "wires": [], "labels": [], "power_ports": [], "junctions": [],
        },
        "parameter_stamps": {},
    }
    project_path = _write_snapshot(tmp_path, snapshot)
    # R1 moves; expect anchor=U1 (signal-net share) even though C1 is
    # spatially closer.
    bridge = FakeBridge({
        "U1": {"x": 5000, "y": 5000, "rotation": 0},
        "R1": {"x": 3500, "y": 3000, "rotation": 0},
        "C1": {"x": 3100, "y": 3000, "rotation": 0},
    })
    log_path = tmp_path / "edits.jsonl"
    learn_from_layout(project_path, bridge=bridge, log_path=log_path)
    rows = [json.loads(l) for l in log_path.read_text().splitlines()]
    r1_row = next(r for r in rows if r["refdes"] == "R1")
    assert r1_row["anchor_refdes"] == "U1"
    assert r1_row["anchor_role"] == "ic"


def test_learn_anchor_falls_back_to_nearest_when_only_power_nets(tmp_path: Path):
    """A decoupling cap that shares only VCC + GND with everything else
    should anchor on the SPATIAL nearest neighbour, since no signal net
    distinguishes one IC over another."""
    snapshot = {
        "plan": {
            "spec": "x", "summary": "x",
            "sheets": [{"name": "main", "size": "A4"}],
            "parts": [
                {"refdes": "U1", "lib_ref": "IC1", "lib_path": _LIB,
                 "status": "existing", "sheet": "main", "role": "ic_a"},
                {"refdes": "U2", "lib_ref": "IC2", "lib_path": _LIB,
                 "status": "existing", "sheet": "main", "role": "ic_b"},
                {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
                 "status": "existing", "sheet": "main", "role": "decoup_cap"},
            ],
            "nets": [
                {"name": "VCC", "is_power": True, "pins": [
                    {"refdes": "U1", "pin": "1"},
                    {"refdes": "U2", "pin": "1"},
                    {"refdes": "C1", "pin": "1"}]},
                {"name": "GND", "is_ground": True, "pins": [
                    {"refdes": "U1", "pin": "2"},
                    {"refdes": "U2", "pin": "2"},
                    {"refdes": "C1", "pin": "2"}]},
            ],
        },
        "canvas": {
            "sheets": [{"name": "main", "title": "", "size": "A4",
                        "width_mils": 11500, "height_mils": 7600}],
            "instances": [
                {"refdes": "U1", "lib_path": _LIB, "lib_ref": "IC1",
                 "x": 1000, "y": 1000, "rotation": 0, "sheet": "main"},
                {"refdes": "U2", "lib_path": _LIB, "lib_ref": "IC2",
                 "x": 9000, "y": 1000, "rotation": 0, "sheet": "main"},
                {"refdes": "C1", "lib_path": _LIB, "lib_ref": "CAP",
                 "x": 1500, "y": 1500, "rotation": 0, "sheet": "main"},
            ],
            "wires": [], "labels": [], "power_ports": [], "junctions": [],
        },
        "parameter_stamps": {},
    }
    project_path = _write_snapshot(tmp_path, snapshot)
    bridge = FakeBridge({
        "U1": {"x": 1000, "y": 1000, "rotation": 0},
        "U2": {"x": 9000, "y": 1000, "rotation": 0},
        "C1": {"x": 2000, "y": 1500, "rotation": 0},  # moved 500 right
    })
    log_path = tmp_path / "edits.jsonl"
    learn_from_layout(project_path, bridge=bridge, log_path=log_path)
    rows = [json.loads(l) for l in log_path.read_text().splitlines()]
    c1_row = next(r for r in rows if r["refdes"] == "C1")
    # U1 is spatially closer in the pre-edit canvas (dist 1000 < 7500).
    assert c1_row["anchor_refdes"] == "U1"


def test_learn_missing_snapshot_returns_clean_error(tmp_path: Path):
    project_path = str(tmp_path / "does_not_exist.PrjPcb")
    result = learn_from_layout(project_path, bridge=FakeBridge({}))
    assert result["ok"] is False
    assert any("no canvas snapshot" in n for n in result["notes"])


def test_design_id_stable_for_same_plan():
    plan_dict = {
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": [
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
            {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
        ],
        "nets": [
            {"name": "N1", "pins": [
                {"refdes": "R1", "pin": "1"},
                {"refdes": "C1", "pin": "1"}]},
        ],
    }
    p1 = DesignPlan.model_validate(plan_dict)
    p2 = DesignPlan.model_validate(plan_dict)
    assert _design_id_for(p1) == _design_id_for(p2)


def test_design_id_differs_across_plans():
    base = {
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": [
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
        ],
        "nets": [
            {"name": "N1", "pins": [
                {"refdes": "R1", "pin": "1"},
                {"refdes": "R1", "pin": "2"}]},
        ],
    }
    other = json.loads(json.dumps(base))
    other["parts"].append({
        "refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
        "status": "existing", "sheet": "main",
    })
    p1 = DesignPlan.model_validate(base)
    p2 = DesignPlan.model_validate(other)
    assert _design_id_for(p1) != _design_id_for(p2)
