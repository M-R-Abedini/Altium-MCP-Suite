# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Multi-sheet hierarchy planning tests, pure Python, no Altium round-trips."""

from __future__ import annotations

import pytest

from eda_agent.design.hierarchy import apply_hierarchy, plan_hierarchy
from eda_agent.design.plan import DesignPlan, Net, Part, PinRef, Sheet, Zone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _net(name, pins, **kw):
    return Net(name=name, pins=[PinRef(refdes=r, pin=str(p)) for r, p in pins], **kw)


def _plan(parts, nets, zones=()):
    return DesignPlan(
        spec="hierarchy test",
        summary="hierarchy test plan",
        sheets=[Sheet(name="main")],
        zones=list(zones),
        parts=parts,
        nets=nets,
    )


def _two_block_plan():
    """30 parts in two star-connected blocks joined by one signal net.

    Block A: hub U1 + R1..R14, each Rn tied to U1 by its own net.
    Block B: hub U2 + C1..C14, likewise. LINK joins the hubs; GND and VCC
    rails span everything. The min-cut split is exactly the two blocks.
    """
    parts = [Part(refdes="U1", lib_ref="MCU"), Part(refdes="U2", lib_ref="PHY")]
    nets = []
    for i in range(1, 15):
        parts.append(Part(refdes=f"R{i}", lib_ref="RES"))
        nets.append(_net(f"NA{i}", [("U1", i), (f"R{i}", 1)]))
    for i in range(1, 15):
        parts.append(Part(refdes=f"C{i}", lib_ref="CAP"))
        nets.append(_net(f"NB{i}", [("U2", i), (f"C{i}", 1)]))
    nets.append(_net("LINK", [("U1", 20), ("U2", 20)]))
    nets.append(_net("GND", [(p.refdes, 99) for p in parts], is_ground=True))
    nets.append(_net("VCC", [("U1", 98), ("U2", 98)], is_power=True))
    return _plan(parts, nets)


def _zoned_plan():
    """22 parts in two zones with roles; one directed signal between them.

    Zone 'supply' (role power_in): J1 + R1..R10. Zone 'mcu' (role mcu):
    U1 + C1..C10. SIG runs from many-pinned U1 to the single-signal R10,
    so direction inference must call the mcu side the driver. VCC spans
    both zones by NAME only (no flag) to exercise the rail-name exclusion.
    """
    zones = [
        Zone(name="supply", sheet="main", role="power_in"),
        Zone(name="mcu", sheet="main", role="mcu"),
    ]
    parts = [Part(refdes="J1", lib_ref="CONN", zone="supply"),
             Part(refdes="U1", lib_ref="MCU", zone="mcu")]
    nets = []
    for i in range(1, 11):
        parts.append(Part(refdes=f"R{i}", lib_ref="RES", zone="supply"))
        nets.append(_net(f"NJ{i}", [("J1", i), (f"R{i}", 1)]))
    for i in range(1, 11):
        parts.append(Part(refdes=f"C{i}", lib_ref="CAP", zone="mcu"))
        nets.append(_net(f"NU{i}", [("U1", i), (f"C{i}", 1)]))
    nets.append(_net("SIG", [("U1", 30), ("R10", 2)]))
    nets.append(_net("GND", [(p.refdes, 99) for p in parts], is_ground=True))
    nets.append(_net("VCC", [("J1", 98), ("U1", 98)]))  # name-only rail
    return _plan(parts, nets, zones)


# ---------------------------------------------------------------------------
# No-op path
# ---------------------------------------------------------------------------


def test_small_plan_stays_single_sheet():
    plan = _plan(
        parts=[Part(refdes="R1", lib_ref="RES"), Part(refdes="C1", lib_ref="CAP")],
        nets=[_net("N1", [("R1", 1), ("C1", 1)]),
              _net("GND", [("R1", 2), ("C1", 2)], is_ground=True)],
    )
    out = plan_hierarchy(plan, max_parts_per_sheet=20)
    assert out["ok"] is True
    assert out["split"] is False
    assert out["ports"] == []
    assert out["top_sheet_ops"] == []
    assert out["sheets"] == [{
        "name": "main", "refdes": ["C1", "R1"], "zones": [], "part_count": 2,
    }]


def test_threshold_is_inclusive():
    """Exactly max_parts_per_sheet parts does not split."""
    parts = [Part(refdes=f"R{i}", lib_ref="RES") for i in range(1, 6)]
    nets = [_net("GND", [(p.refdes, 2) for p in parts], is_ground=True)]
    out = plan_hierarchy(_plan(parts, nets), max_parts_per_sheet=5)
    assert out["ok"] is True and out["split"] is False
    out = plan_hierarchy(_plan(parts, nets), max_parts_per_sheet=4)
    assert out["ok"] is True and out["split"] is True


def test_apply_noop_hierarchy_returns_unchanged_copy():
    plan = _plan(
        parts=[Part(refdes="R1", lib_ref="RES"), Part(refdes="C1", lib_ref="CAP")],
        nets=[_net("N1", [("R1", 1), ("C1", 1)])],
    )
    hierarchy = plan_hierarchy(plan, max_parts_per_sheet=20)
    new_plan = apply_hierarchy(plan, hierarchy)
    assert new_plan is not plan
    assert new_plan.model_dump() == plan.model_dump()


# ---------------------------------------------------------------------------
# Splitting
# ---------------------------------------------------------------------------


def test_two_block_plan_splits_along_blocks():
    plan = _two_block_plan()
    out = plan_hierarchy(plan, max_parts_per_sheet=20)
    assert out["ok"] is True and out["split"] is True
    assert len(out["sheets"]) == 2

    block_a = {"U1"} | {f"R{i}" for i in range(1, 15)}
    block_b = {"U2"} | {f"C{i}" for i in range(1, 15)}
    groups = [set(s["refdes"]) for s in out["sheets"]]
    assert block_a in groups
    assert block_b in groups
    assert all(s["part_count"] == 15 for s in out["sheets"])


def test_all_parts_assigned_exactly_once():
    plan = _two_block_plan()
    out = plan_hierarchy(plan, max_parts_per_sheet=20)
    seen = [r for s in out["sheets"] for r in s["refdes"]]
    assert sorted(seen) == sorted(p.refdes for p in plan.parts)
    assert len(seen) == len(set(seen))


def test_zones_never_split():
    plan = _zoned_plan()
    out = plan_hierarchy(plan, max_parts_per_sheet=15)
    assert out["split"] is True
    sheet_of = {r: s["name"] for s in out["sheets"] for r in s["refdes"]}
    for zone_name in ("supply", "mcu"):
        members = [p.refdes for p in plan.parts if p.zone == zone_name]
        assert len({sheet_of[r] for r in members}) == 1
    # Zone names ride along on the owning sheet.
    zone_sheets = {z: s["name"] for s in out["sheets"] for z in s["zones"]}
    assert set(zone_sheets) == {"supply", "mcu"}


def test_oversize_zone_accepted_not_split():
    """A zone bigger than the cap still lands whole on one sheet."""
    zones = [Zone(name="big", sheet="main", role="analog")]
    parts = [Part(refdes=f"R{i}", lib_ref="RES", zone="big") for i in range(1, 9)]
    parts.append(Part(refdes="C1", lib_ref="CAP"))
    nets = [_net("GND", [(p.refdes, 2) for p in parts], is_ground=True),
            _net("S1", [("R1", 1), ("C1", 1)])]
    out = plan_hierarchy(_plan(parts, nets, zones), max_parts_per_sheet=4)
    assert out["ok"] is True and out["split"] is True
    sheet_of = {r: s["name"] for s in out["sheets"] for r in s["refdes"]}
    assert len({sheet_of[f"R{i}"] for i in range(1, 9)}) == 1


def test_sheet_names_from_dominant_zone_roles():
    out = plan_hierarchy(_zoned_plan(), max_parts_per_sheet=15)
    names = {s["name"] for s in out["sheets"]}
    assert names == {"power_in", "mcu"}


def test_unzoned_groups_get_block_names():
    out = plan_hierarchy(_two_block_plan(), max_parts_per_sheet=20)
    for s in out["sheets"]:
        assert s["name"].startswith("block_")
    assert len({s["name"] for s in out["sheets"]}) == 2


def test_deterministic():
    a = plan_hierarchy(_two_block_plan(), max_parts_per_sheet=20)
    b = plan_hierarchy(_two_block_plan(), max_parts_per_sheet=20)
    assert a == b


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------


def test_port_direction_inference():
    """The many-pinned MCU side drives SIG -> output from mcu, into power_in."""
    out = plan_hierarchy(_zoned_plan(), max_parts_per_sheet=15)
    sig = [p for p in out["ports"] if p["net"] == "SIG"]
    assert sig == [{
        "net": "SIG", "from_sheet": "mcu", "to_sheet": "power_in",
        "io_type": "output",
    }]


def test_power_and_ground_nets_excluded_from_ports():
    out = plan_hierarchy(_zoned_plan(), max_parts_per_sheet=15)
    port_nets = {p["net"] for p in out["ports"]}
    assert "GND" not in port_nets          # is_ground flag
    assert "VCC" not in port_nets          # conventional rail name, no flag
    assert port_nets == {"SIG"}


def test_symmetric_link_is_bidirectional():
    """Two equally-connected hubs on either side of the cut tie -> bidi."""
    out = plan_hierarchy(_two_block_plan(), max_parts_per_sheet=20)
    link = [p for p in out["ports"] if p["net"] == "LINK"]
    assert len(link) == 1
    assert link[0]["io_type"] == "bidirectional"
    assert {link[0]["from_sheet"], link[0]["to_sheet"]} == \
        {s["name"] for s in out["sheets"]}


def test_cut_nets_counts_rails_too():
    out = plan_hierarchy(_two_block_plan(), max_parts_per_sheet=20)
    # LINK + GND + VCC span the cut.
    assert out["cut_nets"] == 3


# ---------------------------------------------------------------------------
# Top-sheet ops
# ---------------------------------------------------------------------------


def test_top_sheet_ops_shapes():
    out = plan_hierarchy(_zoned_plan(), max_parts_per_sheet=15)
    ops = out["top_sheet_ops"]

    toc = [o for o in ops if o["tool"] == "sch_generate_toc"]
    assert len(toc) == 1
    assert set(toc[0]["params"]) == {"x1", "y1", "x2", "y2", "title"}

    symbols = [o for o in ops if o["tool"] == "sch_place_sheet_symbol"]
    assert len(symbols) == len(out["sheets"])
    for sym in symbols:
        p = sym["params"]
        assert set(p) == {"x1", "y1", "x2", "y2", "sheet_file_name", "sheet_name"}
        assert p["sheet_file_name"] == f"{p['sheet_name']}.SchDoc"
        assert p["x2"] > p["x1"] and p["y2"] > p["y1"]
        assert all(isinstance(p[k], int) for k in ("x1", "y1", "x2", "y2"))

    entries = [o for o in ops if o["tool"] == "sch_place_sheet_entry"]
    assert {e["params"]["entry_name"] for e in entries} == {"SIG"}
    for e in entries:
        assert set(e["params"]) == {
            "sheet_name", "entry_name", "io_type", "side", "distance_from_top"}
        assert e["params"]["io_type"] in (
            "input", "output", "bidirectional", "unspecified")
        assert e["params"]["side"] in ("left", "right", "top", "bottom")


def test_sheet_entries_match_ports_on_both_ends():
    out = plan_hierarchy(_zoned_plan(), max_parts_per_sheet=15)
    entries = [o["params"] for o in out["top_sheet_ops"]
               if o["tool"] == "sch_place_sheet_entry"]
    by_sheet = {(e["sheet_name"], e["entry_name"]): e for e in entries}
    driver = by_sheet[("mcu", "SIG")]
    receiver = by_sheet[("power_in", "SIG")]
    assert driver["io_type"] == "output" and driver["side"] == "right"
    assert receiver["io_type"] == "input" and receiver["side"] == "left"


def test_symbol_boxes_do_not_overlap():
    out = plan_hierarchy(_two_block_plan(), max_parts_per_sheet=20)
    boxes = [o["params"] for o in out["top_sheet_ops"]
             if o["tool"] == "sch_place_sheet_symbol"]
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            disjoint = (a["x2"] <= b["x1"] or b["x2"] <= a["x1"]
                        or a["y2"] <= b["y1"] or b["y2"] <= a["y1"])
            assert disjoint


# ---------------------------------------------------------------------------
# apply_hierarchy
# ---------------------------------------------------------------------------


def test_apply_hierarchy_reassigns_parts_and_zones():
    plan = _zoned_plan()
    hierarchy = plan_hierarchy(plan, max_parts_per_sheet=15)
    new_plan = apply_hierarchy(plan, hierarchy)

    sheet_names = [s.name for s in new_plan.sheets]
    assert sheet_names[0] == hierarchy["top_sheet"]
    assert set(sheet_names[1:]) == {s["name"] for s in hierarchy["sheets"]}

    expect = {r: s["name"] for s in hierarchy["sheets"] for r in s["refdes"]}
    for part in new_plan.parts:
        assert part.sheet == expect[part.refdes]
    for zone in new_plan.zones:
        # Zone follows its parts' sheet.
        member = next(p for p in new_plan.parts if p.zone == zone.name)
        assert zone.sheet == member.sheet


def test_apply_hierarchy_is_pure():
    plan = _zoned_plan()
    before = plan.model_dump()
    hierarchy = plan_hierarchy(plan, max_parts_per_sheet=15)
    new_plan = apply_hierarchy(plan, hierarchy)
    assert plan.model_dump() == before
    assert new_plan is not plan
    assert new_plan.model_dump() != before


def test_apply_hierarchy_result_is_schema_valid():
    plan = _zoned_plan()
    hierarchy = plan_hierarchy(plan, max_parts_per_sheet=15)
    new_plan = apply_hierarchy(plan, hierarchy)
    assert new_plan.cross_check() == []
    # Round-trips through the strict schema.
    DesignPlan.model_validate(new_plan.model_dump())


def test_apply_hierarchy_accepts_dict_plan():
    plan = _zoned_plan()
    hierarchy = plan_hierarchy(plan, max_parts_per_sheet=15)
    new_plan = apply_hierarchy(plan.model_dump(), hierarchy)
    assert new_plan.cross_check() == []


def test_apply_hierarchy_rejects_bad_hierarchy():
    plan = _zoned_plan()
    with pytest.raises(ValueError):
        apply_hierarchy(plan, {"ok": False, "reason": "nope"})
    with pytest.raises(ValueError):
        apply_hierarchy(plan, {"ok": True, "split": True, "sheets": []})


# ---------------------------------------------------------------------------
# Degenerate input
# ---------------------------------------------------------------------------


def test_invalid_threshold():
    out = plan_hierarchy(_two_block_plan(), max_parts_per_sheet=0)
    assert out["ok"] is False
    assert "max_parts_per_sheet" in out["reason"]


def test_invalid_plan_dict():
    out = plan_hierarchy({"spec": "x"}, max_parts_per_sheet=20)
    assert out["ok"] is False
    assert "invalid plan" in out["reason"]


def test_plan_accepts_dict_input():
    out = plan_hierarchy(_two_block_plan().model_dump(), max_parts_per_sheet=20)
    assert out["ok"] is True and out["split"] is True
