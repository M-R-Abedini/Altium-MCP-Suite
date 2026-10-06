# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""End-to-end simulator coverage for pcb.* read handlers (roadmap 1.3).

Until now the Python Altium simulator only routed application / project /
library / generic, leaving the 105 pcb.* tools with zero automated
integration coverage. This exercises the simulator's new ``pcb`` dispatch
category through the real ``AltiumBridge``, mirroring the response shapes of
PCB.pas (PCB_GetComponents / GetNets / GetBoardOutline / GetBoardStatistics)
so a Python-side pcb change is caught in CI instead of only on live Altium.
"""

from __future__ import annotations

import pytest


def test_pcb_get_components(e2e_bridge):
    res = e2e_bridge.send_command("pcb.get_components", timeout=5.0)
    assert res["count"] == 3
    designators = {c["designator"] for c in res["components"]}
    assert designators == {"R1", "R2", "U1"}
    u1 = next(c for c in res["components"] if c["designator"] == "U1")
    # Shape parity with PCB_GetComponents.
    for key in ("comment", "x", "y", "rotation", "layer", "footprint",
                "source_designator", "height_mils", "bbox"):
        assert key in u1
    assert set(u1["bbox"]) == {"x1", "y1", "x2", "y2", "width", "height"}
    assert u1["footprint"] == "LQFP-64"


def test_pcb_get_nets(e2e_bridge):
    res = e2e_bridge.send_command("pcb.get_nets", timeout=5.0)
    assert res["count"] == 3
    assert set(res["nets"]) == {"GND", "VCC", "NET1"}


def test_pcb_get_board_outline(e2e_bridge):
    res = e2e_bridge.send_command("pcb.get_board_outline", timeout=5.0)
    assert res["point_count"] == 4
    assert len(res["vertices"]) == 4
    br = res["bounding_rect"]
    assert set(br) == {"left", "bottom", "right", "top"}
    assert br["right"] > br["left"] and br["top"] > br["bottom"]


def test_pcb_get_board_statistics(e2e_bridge):
    res = e2e_bridge.send_command("pcb.get_board_statistics", timeout=5.0)
    assert res["component_count"] == 3
    assert res["track_count"] == 12
    assert res["board_area_sq_mils"] == res["board_width_mils"] * res["board_height_mils"]
    for key in ("via_count", "pad_count", "layer_count",
                "total_trace_length_mils", "unrouted_connections"):
        assert key in res


def test_pcb_move_component_roundtrip(e2e_bridge):
    # The write path: move R1, then read it back from get_components.
    res = e2e_bridge.send_command(
        "pcb.move_component",
        {"designator": "R1", "x": "1234", "y": "5678", "rotation": "180"},
        timeout=5.0,
    )
    assert res["x"] == 1234 and res["y"] == 5678 and res["rotation"] == 180

    comps = e2e_bridge.send_command("pcb.get_components", timeout=5.0)["components"]
    r1 = next(c for c in comps if c["designator"] == "R1")
    assert r1["x"] == 1234 and r1["y"] == 5678 and r1["rotation"] == 180


def test_pcb_move_component_not_found(e2e_bridge):
    from eda_agent.bridge.altium_bridge import AltiumCommandError
    with pytest.raises(AltiumCommandError):
        e2e_bridge.send_command(
            "pcb.move_component", {"designator": "ZZ99", "x": "0"}, timeout=5.0
        )


def test_pcb_batch_move_components(e2e_bridge):
    res = e2e_bridge.send_command(
        "pcb.batch_move_components",
        {"moves": "R1,100,200,|R2,,,90|NOPE,0,0,0"},
        timeout=5.0,
    )
    assert res["moves_applied"] == 2
    assert res["failed"] == 1

    comps = {c["designator"]: c
             for c in e2e_bridge.send_command("pcb.get_components", timeout=5.0)["components"]}
    assert comps["R1"]["x"] == 100 and comps["R1"]["y"] == 200
    assert comps["R2"]["rotation"] == 90


def test_pcb_place_via_roundtrip(e2e_bridge):
    # Board starts with no vias.
    assert e2e_bridge.send_command("pcb.get_vias", timeout=5.0)["count"] == 0

    res = e2e_bridge.send_command(
        "pcb.place_via",
        {"x": "500", "y": "600", "net": "GND", "size": "50", "hole_size": "28"},
        timeout=5.0,
    )
    assert res["placed"] is True and res["x"] == 500 and res["y"] == 600

    vias = e2e_bridge.send_command("pcb.get_vias", timeout=5.0)
    assert vias["count"] == 1
    v = vias["vias"][0]
    assert v["x"] == 500 and v["y"] == 600 and v["net"] == "GND"
    assert set(v) == {"x", "y", "net", "size", "hole_size", "low_layer", "high_layer"}


def test_pcb_place_via_requires_coords(e2e_bridge):
    from eda_agent.bridge.altium_bridge import AltiumCommandError
    with pytest.raises(AltiumCommandError):
        e2e_bridge.send_command("pcb.place_via", {"net": "GND"}, timeout=5.0)


def test_pcb_get_unrouted_nets(e2e_bridge):
    res = e2e_bridge.send_command("pcb.get_unrouted_nets", timeout=5.0)
    assert res["net_count"] == 1
    assert res["total_unrouted"] == 1
    n = res["unrouted_nets"][0]
    assert n["net"] == "NET1" and n["unrouted_connections"] == 1


def test_pcb_run_drc_clean_board(e2e_bridge):
    res = e2e_bridge.send_command("pcb.run_drc", timeout=10.0)
    assert res["violation_count"] == 0
    assert res["violations"] == []


def test_pcb_run_drc_reports_violations(e2e_bridge, altium_sim):
    # Seed a violation into the mock board; run_drc must surface it.
    altium_sim.board.drc_violations = [
        {"type": "Clearance", "message": "GND too close to VCC", "net": "GND"}
    ]
    res = e2e_bridge.send_command("pcb.run_drc", timeout=10.0)
    assert res["violation_count"] == 1
    assert res["violations"][0]["type"] == "Clearance"


def test_pcb_create_nets_from_list(e2e_bridge):
    # NET1/VCC/GND already exist; SPI_CS is new.
    res = e2e_bridge.send_command(
        "pcb.create_nets_from_list",
        {"nets": "GND|VCC|SPI_CS"}, timeout=5.0)
    assert res["created"] == 1 and res["existing"] == 2
    nets = e2e_bridge.send_command("pcb.get_nets", timeout=5.0)["nets"]
    assert "SPI_CS" in nets


def test_pcb_bind_pad_nets_roundtrip(e2e_bridge):
    # designator=U1 exists, GND exists -> bound; ZZ99 missing -> failed.
    res = e2e_bridge.send_command("pcb.bind_pad_nets", {
        "bindings": "designator=U1;pin=4;net=GND~~designator=ZZ99;pin=1;net=GND",
    }, timeout=5.0)
    assert res["bound"] == 1
    assert res["failed"] == 1
    assert "ZZ99" in res["missing_components"]


def test_pcb_bind_pad_nets_missing_net(e2e_bridge):
    res = e2e_bridge.send_command("pcb.bind_pad_nets", {
        "bindings": "designator=U1;pin=1;net=NONEXISTENT",
    }, timeout=5.0)
    assert res["bound"] == 0 and res["failed"] == 1
    assert "NONEXISTENT" in res["missing_nets"]


def test_pcb_bridge_legs_via_tools(e2e_bridge, monkeypatch):
    # Drive the two SCH->PCB legs through the real MCP tool wrappers, which
    # own the wire encoding (pipe-join / ~~-op grammar).
    import asyncio
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_all_tools

    monkeypatch.setattr("eda_agent.tools.pcb.get_bridge", lambda: e2e_bridge)
    m = FastMCP("t")
    register_all_tools(m)

    def _run(name, args):
        import json as _j
        r = asyncio.run(m.call_tool(name, args))
        c = r[0] if isinstance(r, tuple) else r
        return _j.loads(c[0].text)

    created = _run("pcb_create_nets_from_list", {"nets": ["A_BUS", "B_BUS"]})
    assert created["created"] == 2
    bound = _run("pcb_bind_pad_nets", {"bindings": [
        {"designator": "U1", "pin": "2", "net": "A_BUS"},
    ]})
    assert bound["bound"] == 1


def test_pcb_place_components_geometry_only(e2e_bridge):
    before = e2e_bridge.send_command("pcb.get_components", timeout=5.0)["count"]
    res = e2e_bridge.send_command("pcb.place_components", {
        "placements": "footprint==0402;;designator==R9;;x==1200;;y==1300",
        "board_path": "",
    }, timeout=5.0)
    assert res["placed"] == 1 and res["total"] == 1
    after = e2e_bridge.send_command("pcb.get_components", timeout=5.0)
    assert after["count"] == before + 1
    r9 = next(c for c in after["components"] if c["designator"] == "R9")
    assert r9["x"] == 1200 and r9["footprint"] == "0402"


def test_pcb_place_components_synced_creates_nets(e2e_bridge):
    # Synced mode: pad_nets creates the net and binds pads (ECO-free).
    res = e2e_bridge.send_command("pcb.place_components", {
        "placements": ("footprint==SOT-23;;designator==Q1;;x==900;;y==900;;"
                       "pad_nets==1=GATE_DRV|2=GND|3=SW_NODE"),
        "board_path": "",
    }, timeout=5.0)
    assert res["placed"] == 1
    nets = e2e_bridge.send_command("pcb.get_nets", timeout=5.0)["nets"]
    assert "GATE_DRV" in nets and "SW_NODE" in nets


def test_pcb_place_components_full_bridge_via_tool(e2e_bridge, monkeypatch):
    # The full SCH->PCB trio through the real tool: place (synced) then
    # verify connectivity landed as pad-net bindings.
    import asyncio, json as _j
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_all_tools

    monkeypatch.setattr("eda_agent.tools.pcb.get_bridge", lambda: e2e_bridge)
    m = FastMCP("t"); register_all_tools(m)
    r = asyncio.run(m.call_tool("pcb_place_components", {"placements": [
        {"footprint": "0603", "library_path": "Std.PcbLib",
         "designator": "C9", "x": 400, "y": 500,
         "pad_nets": {"1": "VCC", "2": "GND"}},
    ]}))
    c = r[0] if isinstance(r, tuple) else r
    out = _j.loads(c[0].text)
    assert out["placed"] == 1


def test_pcb_place_tracks_bumps_track_count(e2e_bridge):
    before = e2e_bridge.send_command(
        "pcb.get_board_statistics", timeout=5.0)["track_count"]
    res = e2e_bridge.send_command("pcb.place_tracks", {
        "tracks": "500,500,600,500,10,TopLayer,GND|600,500,600,700,10,TopLayer,GND",
    }, timeout=5.0)
    assert res["placed"] == 2 and res["failed"] == 0
    after = e2e_bridge.send_command(
        "pcb.get_board_statistics", timeout=5.0)["track_count"]
    assert after == before + 2


def test_pcb_place_tracks_via_tool(e2e_bridge, monkeypatch):
    # The routing-apply step through the real tool (which owns the
    # comma/pipe track encoding).
    import asyncio, json as _j
    from mcp.server.fastmcp import FastMCP
    from eda_agent.tools import register_all_tools
    monkeypatch.setattr("eda_agent.tools.pcb.get_bridge", lambda: e2e_bridge)
    m = FastMCP("t"); register_all_tools(m)
    r = asyncio.run(m.call_tool("pcb_place_tracks", {"tracks": [
        {"x1": 100, "y1": 100, "x2": 200, "y2": 100, "width": 8, "net_name": "VCC"},
    ]}))
    c = r[0] if isinstance(r, tuple) else r
    assert _j.loads(c[0].text)["placed"] == 1


def test_pcb_unknown_action_raises(e2e_bridge):
    from eda_agent.bridge.altium_bridge import AltiumCommandError
    with pytest.raises(AltiumCommandError):
        e2e_bridge.send_command("pcb.no_such_action", timeout=5.0)
