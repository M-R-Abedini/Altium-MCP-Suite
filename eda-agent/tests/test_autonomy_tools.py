# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Wiring tests for the autonomy-stage MCP tools, pure Python, no Altium:

  - design_validate_requirement  (stage 1, design/requirement.py)
  - design_load_fab_profile      (stage 8, design/fab_profile.py)
  - design_synthesize_rules      (stage 8, design/rule_synthesis.py)
  - design_plan_hierarchy        (stage 6, design/hierarchy.py)
  - design_apply_hierarchy       (stage 6, design/hierarchy.py)
  - lib_inspect_cse_zip          (stage 5, libimport/cse.py)
  - lib_extract_cse_zip          (stage 5, libimport/cse.py)

The underlying modules carry their own logic suites (tests/design/ and
tests/test_cse_import.py); these tests cover the tool layer: argument
shapes, JSON-string vs dict inputs, error shapes, and defaults.

All fab capability numbers below are SYNTHETIC fixture values, not any
real fab's capabilities.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from eda_agent.tools import design as design_module
from eda_agent.tools import library as library_module


# ---------------------------------------------------------------------------
# Harness: capture the @mcp.tool-decorated closures without a real server.
# ---------------------------------------------------------------------------


class _CaptureMCP:
    def __init__(self):
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def _design_tool(name: str):
    mcp = _CaptureMCP()
    design_module.register_design_tools(mcp)
    return mcp.tools[name]


def _library_tool(name: str):
    mcp = _CaptureMCP()
    library_module.register_library_tools(mcp)
    return mcp.tools[name]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _requirement_dict(**overrides) -> dict:
    d = {
        "function": "USB-powered LED blinker",
        "inputs": [
            {"name": "VBUS", "kind": "power", "voltage_v": 5.0,
             "current_a": 0.5},
        ],
        "outputs": [
            {"name": "LED1", "kind": "digital"},
        ],
        "supply": [
            {"name": "3V3", "voltage_v": 3.3, "current_a": 0.1},
        ],
        "quantities": [5, 100],
        "open_questions": [],
    }
    d.update(overrides)
    return d


def _profile_dict(**overrides) -> dict:
    d = {
        "name": "TestFab synthetic profile",
        "source": "synthetic test fixture, not a real fab",
        "copper_layer_counts": [2],
        "min_track_mils": 6.0,
        "min_gap_mils": 7.0,
        "min_drill_mils": 12.0,
        "min_annular_ring_mils": 5.0,
        "min_hole_to_hole_mils": 10.0,
        "min_mask_sliver_mils": 3.0,
        "min_silk_width_mils": 5.0,
        "stackups": [
            {
                "name": "test-2L",
                "layers": [
                    {"name": "Top", "kind": "copper",
                     "thickness_mils": 1.4, "copper_oz": 1.0},
                    {"name": "Core", "kind": "core",
                     "thickness_mils": 6.0, "er": 4.0},
                    {"name": "Bottom", "kind": "copper",
                     "thickness_mils": 1.4, "copper_oz": 1.0},
                ],
            },
        ],
    }
    d.update(overrides)
    return d


def _small_plan_dict() -> dict:
    """4 parts, well under any split threshold."""
    return {
        "spec": "autonomy tool test",
        "summary": "small plan",
        "sheets": [{"name": "main"}],
        "parts": [
            {"refdes": "U1", "lib_ref": "MCU"},
            {"refdes": "R1", "lib_ref": "RES"},
            {"refdes": "C1", "lib_ref": "CAP"},
            {"refdes": "J1", "lib_ref": "CONN"},
        ],
        "nets": [
            {"name": "VCC", "is_power": True, "pins": [
                {"refdes": "J1", "pin": "1"}, {"refdes": "U1", "pin": "8"},
                {"refdes": "C1", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "J1", "pin": "2"}, {"refdes": "U1", "pin": "4"},
                {"refdes": "C1", "pin": "2"}]},
            {"name": "SIG", "pins": [
                {"refdes": "U1", "pin": "1"}, {"refdes": "R1", "pin": "1"}]},
        ],
    }


def _dense_plan_dict() -> dict:
    """30 parts in two star-connected blocks; the min-cut split is clean."""
    parts = [{"refdes": "U1", "lib_ref": "MCU"},
             {"refdes": "U2", "lib_ref": "PHY"}]
    nets = []
    for i in range(1, 15):
        parts.append({"refdes": f"R{i}", "lib_ref": "RES"})
        nets.append({"name": f"NA{i}", "pins": [
            {"refdes": "U1", "pin": str(i)},
            {"refdes": f"R{i}", "pin": "1"}]})
    for i in range(1, 15):
        parts.append({"refdes": f"C{i}", "lib_ref": "CAP"})
        nets.append({"name": f"NB{i}", "pins": [
            {"refdes": "U2", "pin": str(i)},
            {"refdes": f"C{i}", "pin": "1"}]})
    nets.append({"name": "LINK", "pins": [
        {"refdes": "U1", "pin": "20"}, {"refdes": "U2", "pin": "20"}]})
    nets.append({"name": "GND", "is_ground": True, "pins": [
        {"refdes": p["refdes"], "pin": "99"} for p in parts]})
    nets.append({"name": "VCC", "is_power": True, "pins": [
        {"refdes": "U1", "pin": "98"}, {"refdes": "U2", "pin": "98"}]})
    return {
        "spec": "autonomy tool test",
        "summary": "dense plan",
        "sheets": [{"name": "main"}],
        "parts": parts,
        "nets": nets,
    }


def _make_cse_zip(path: Path, mpn: str = "STM32F103C8T6",
                  members: dict | None = None) -> Path:
    if members is None:
        members = {
            f"{mpn}.SchLib": b"schlib-bytes",
            f"{mpn}.PcbLib": b"pcblib-bytes",
            f"{mpn}.stp": b"step-bytes",
        }
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


# ---------------------------------------------------------------------------
# design_validate_requirement
# ---------------------------------------------------------------------------


async def test_validate_requirement_ok():
    tool = _design_tool("design_validate_requirement")
    out = await tool(_requirement_dict())
    assert out["ok"] is True
    assert out["issues"] == []
    assert out["summary"].startswith("Function: USB-powered LED blinker")
    assert "Quantities: 5, 100" in out["summary"]


async def test_validate_requirement_open_questions_block():
    tool = _design_tool("design_validate_requirement")
    out = await tool(_requirement_dict(
        open_questions=["operating temperature range?"]))
    assert out["ok"] is False
    assert any("unresolved open question" in i for i in out["issues"])
    # The summary is still produced so the planner can show the user context.
    assert "Open questions: 1 unresolved" in out["summary"]


async def test_validate_requirement_cross_checks_fire():
    tool = _design_tool("design_validate_requirement")
    out = await tool(_requirement_dict(inputs=[], outputs=[], supply=[]))
    assert out["ok"] is False
    assert any("no outputs" in i for i in out["issues"])
    assert any("no power source" in i for i in out["issues"])


async def test_validate_requirement_schema_error_shape():
    tool = _design_tool("design_validate_requirement")
    # Duplicate IO names violate the model validator; extra keys are
    # forbidden by the schema. Both land in "errors" (not "issues").
    out = await tool(_requirement_dict(outputs=[
        {"name": "VBUS", "kind": "digital"}]))
    assert out["ok"] is False
    assert "errors" in out
    assert any("duplicate IO name" in e for e in out["errors"])

    out2 = await tool({**_requirement_dict(), "bogus_field": 1})
    assert out2["ok"] is False
    assert "errors" in out2


# ---------------------------------------------------------------------------
# design_load_fab_profile
# ---------------------------------------------------------------------------


async def test_load_fab_profile_ok():
    tool = _design_tool("design_load_fab_profile")
    out = await tool(_profile_dict())
    assert out["ok"] is True
    assert out["profile"]["name"] == "TestFab synthetic profile"
    assert out["stackups"] == ["test-2L"]
    assert "min track 6" in out["summary"]


async def test_load_fab_profile_invalid_returns_reason():
    tool = _design_tool("design_load_fab_profile")
    bad = _profile_dict()
    del bad["min_track_mils"]
    out = await tool(bad)
    assert out["ok"] is False
    assert "reason" in out


async def test_load_fab_profile_rejects_bad_stackup():
    tool = _design_tool("design_load_fab_profile")
    bad = _profile_dict()
    # Dielectric outer layer violates the Stackup validator.
    bad["stackups"][0]["layers"] = list(
        reversed(bad["stackups"][0]["layers"][:2]))
    out = await tool(bad)
    assert out["ok"] is False


# ---------------------------------------------------------------------------
# design_synthesize_rules
# ---------------------------------------------------------------------------


async def test_synthesize_rules_explicit_map():
    tool = _design_tool("design_synthesize_rules")
    out = await tool(_profile_dict(),
                     net_class_map={"VCC": "power", "GND": "ground"})
    assert out["ok"] is True
    names = {r["name"] for r in out["rules"]}
    assert {"Clearance_Fab_Min", "Width_Fab_Min",
            "Via_Fab_Min_Drill"} <= names
    for r in out["rules"]:
        assert isinstance(r["value"], int)
    assert out["net_classes"] == {"VCC": "power", "GND": "ground"}


async def test_synthesize_rules_derives_classes_from_plan():
    tool = _design_tool("design_synthesize_rules")
    out = await tool(_profile_dict(),
                     plan_json=json.dumps(_small_plan_dict()))
    assert out["ok"] is True
    assert out["net_classes"]["VCC"] == "power"
    assert out["net_classes"]["GND"] == "ground"
    assert "SIG" in out["net_classes"]


async def test_synthesize_rules_explicit_map_wins_over_plan():
    tool = _design_tool("design_synthesize_rules")
    out = await tool(_profile_dict(),
                     plan_json=json.dumps(_small_plan_dict()),
                     net_class_map={"ONLY": "signal"})
    assert out["ok"] is True
    assert out["net_classes"] == {"ONLY": "signal"}


async def test_synthesize_rules_class_current_option():
    tool = _design_tool("design_synthesize_rules")
    out = await tool(_profile_dict(),
                     net_class_map={"VCC": "power", "GND": "ground"},
                     options={"class_current_a": {"power": 2.0}})
    assert out["ok"] is True
    width_rules = [r for r in out["rules"]
                   if r["rule_type"] == "width"
                   and r.get("scope") == "InNetClass('power')"]
    assert len(width_rules) == 1
    assert width_rules[0]["name"] == "Width_power"
    assert width_rules[0]["value"] >= 6  # never below the fab floor


async def test_synthesize_rules_no_classes_still_emits_floors():
    tool = _design_tool("design_synthesize_rules")
    out = await tool(_profile_dict())
    assert out["ok"] is True
    assert out["net_classes"] == {}
    assert {r["name"] for r in out["rules"]} >= {"Clearance_Fab_Min"}


async def test_synthesize_rules_bad_inputs():
    tool = _design_tool("design_synthesize_rules")
    out = await tool(_profile_dict(), plan_json="{not json")
    assert out["ok"] is False
    assert "invalid plan JSON" in out["reason"]

    out2 = await tool(_profile_dict(),
                      plan_json=json.dumps({"spec": "x"}))
    assert out2["ok"] is False
    assert "invalid plan" in out2["reason"]

    out3 = await tool(_profile_dict(), net_class_map={},
                      options={"bogus_option": 1})
    assert out3["ok"] is False
    assert "bogus_option" in out3["reason"]


# ---------------------------------------------------------------------------
# design_plan_hierarchy
# ---------------------------------------------------------------------------


async def test_plan_hierarchy_small_plan_no_split():
    tool = _design_tool("design_plan_hierarchy")
    out = await tool(_small_plan_dict())
    assert out["ok"] is True
    assert out["split"] is False
    assert out["ports"] == []
    assert out["top_sheet_ops"] == []


async def test_plan_hierarchy_dense_plan_splits():
    tool = _design_tool("design_plan_hierarchy")
    out = await tool(_dense_plan_dict(), max_parts_per_sheet=20)
    assert out["ok"] is True
    assert out["split"] is True
    assert len(out["sheets"]) >= 2
    # Every part lands on exactly one sheet.
    all_refdes = [r for s in out["sheets"] for r in s["refdes"]]
    assert sorted(all_refdes) == sorted(
        p["refdes"] for p in _dense_plan_dict()["parts"])
    # Top-sheet ops use only the three known tools, coords are ints (mils).
    op_tools = {op["tool"] for op in out["top_sheet_ops"]}
    assert op_tools <= {"sch_generate_toc", "sch_place_sheet_symbol",
                        "sch_place_sheet_entry"}
    assert "sch_place_sheet_symbol" in op_tools
    # Rails never become ports.
    port_nets = {p["net"] for p in out["ports"]}
    assert "GND" not in port_nets and "VCC" not in port_nets


async def test_plan_hierarchy_accepts_json_string_and_is_deterministic():
    tool = _design_tool("design_plan_hierarchy")
    payload = json.dumps(_dense_plan_dict())
    out1 = await tool(payload)
    out2 = await tool(payload)
    assert out1 == out2
    assert out1["ok"] is True


async def test_plan_hierarchy_invalid_json():
    tool = _design_tool("design_plan_hierarchy")
    out = await tool("{not json")
    assert out["ok"] is False
    assert "invalid plan JSON" in out["reason"]


# ---------------------------------------------------------------------------
# design_apply_hierarchy
# ---------------------------------------------------------------------------


async def test_apply_hierarchy_round_trip():
    plan_tool = _design_tool("design_plan_hierarchy")
    apply_tool = _design_tool("design_apply_hierarchy")
    plan = _dense_plan_dict()
    hierarchy = await plan_tool(plan)
    assert hierarchy["split"] is True
    out = await apply_tool(json.dumps(plan), hierarchy)
    assert out["ok"] is True
    # Top sheet first, then the children, matching the hierarchy.
    child_names = [s["name"] for s in hierarchy["sheets"]]
    assert out["sheets"] == [hierarchy["top_sheet"], *child_names]
    # Every part re-homed onto its hierarchy sheet.
    sheet_of = {r: s["name"] for s in hierarchy["sheets"]
                for r in s["refdes"]}
    for part in out["plan"]["parts"]:
        assert part["sheet"] == sheet_of[part["refdes"]]
    assert "part(s)" in out["summary"]


async def test_apply_hierarchy_non_split_unchanged():
    plan_tool = _design_tool("design_plan_hierarchy")
    apply_tool = _design_tool("design_apply_hierarchy")
    plan = _small_plan_dict()
    hierarchy = await plan_tool(plan)
    assert hierarchy["split"] is False
    out = await apply_tool(plan, hierarchy)
    assert out["ok"] is True
    assert [p["refdes"] for p in out["plan"]["parts"]] == [
        p["refdes"] for p in plan["parts"]]


async def test_apply_hierarchy_malformed_hierarchy():
    apply_tool = _design_tool("design_apply_hierarchy")
    out = await apply_tool(_small_plan_dict(), {"ok": False, "reason": "x"})
    assert out["ok"] is False
    assert "reason" in out


async def test_apply_hierarchy_invalid_json():
    apply_tool = _design_tool("design_apply_hierarchy")
    out = await apply_tool("{not json", {"ok": True, "split": False})
    assert out["ok"] is False
    assert "invalid plan JSON" in out["reason"]


# ---------------------------------------------------------------------------
# lib_inspect_cse_zip
# ---------------------------------------------------------------------------


async def test_inspect_cse_zip_ok(tmp_path):
    tool = _library_tool("lib_inspect_cse_zip")
    zp = _make_cse_zip(tmp_path / "LIB_STM32F103C8T6.zip")
    out = await tool(str(zp))
    assert out["ok"] is True
    assert out["mpn"] == "STM32F103C8T6"
    assert out["schlib"] == "STM32F103C8T6.SchLib"
    assert out["pcblib"] == "STM32F103C8T6.PcbLib"
    assert out["step"] == "STM32F103C8T6.stp"
    assert out["suspicious"] == []


async def test_inspect_cse_zip_missing_file(tmp_path):
    tool = _library_tool("lib_inspect_cse_zip")
    out = await tool(str(tmp_path / "nope.zip"))
    assert out["ok"] is False
    assert "not found" in out["reason"]


async def test_inspect_cse_zip_no_libs(tmp_path):
    tool = _library_tool("lib_inspect_cse_zip")
    zp = _make_cse_zip(tmp_path / "junk.zip",
                       members={"readme.txt": b"hello"})
    out = await tool(str(zp))
    assert out["ok"] is False


# ---------------------------------------------------------------------------
# lib_extract_cse_zip
# ---------------------------------------------------------------------------


async def test_extract_cse_zip_explicit_dest(tmp_path):
    tool = _library_tool("lib_extract_cse_zip")
    zp = _make_cse_zip(tmp_path / "LIB_STM32F103C8T6.zip")
    dest = tmp_path / "staged"
    out = await tool(str(zp), dest_dir=str(dest))
    assert out["ok"] is True
    assert out["mpn"] == "STM32F103C8T6"
    for f in out["files"]:
        p = Path(f)
        assert p.is_file()
        assert p.parent == dest
    # Install plan: both libraries first, then the links.
    tools_in_order = [step["tool"] for step in out["install_plan"]]
    assert tools_in_order[:2] == ["lib_install_library",
                                  "lib_install_library"]
    assert "lib_link_footprint" in tools_in_order
    assert "lib_link_3d_model" in tools_in_order


async def test_extract_cse_zip_default_dest_under_workspace(
        tmp_path, monkeypatch):
    class _Cfg:
        workspace_dir = tmp_path / "workspace"

    monkeypatch.setattr(library_module, "get_config", lambda: _Cfg())
    tool = _library_tool("lib_extract_cse_zip")
    zp = _make_cse_zip(tmp_path / "LIB_STM32F103C8T6.zip")
    out = await tool(str(zp))
    assert out["ok"] is True
    expected = tmp_path / "workspace" / "cse_imports" / "LIB_STM32F103C8T6"
    for f in out["files"]:
        assert Path(f).parent == expected


async def test_extract_cse_zip_rejects_zip_slip(tmp_path):
    tool = _library_tool("lib_extract_cse_zip")
    zp = _make_cse_zip(tmp_path / "evil.zip", members={
        "OK.SchLib": b"x",
        "../escape.PcbLib": b"y",
    })
    dest = tmp_path / "staged"
    out = await tool(str(zp), dest_dir=str(dest))
    assert out["ok"] is False
    assert "zip-slip" in out["reason"]
    assert not dest.exists() or not any(dest.iterdir())
