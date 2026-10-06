# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Unit tests for the EDA-agnostic core: snapshot model, net-name
classification, and the shared review engine. No live EDA required."""

from __future__ import annotations

from eda_agent.core.net_naming import is_ground_net, is_power_net
from eda_agent.core.snapshot import DesignSnapshot
from eda_agent.core.review_engine import review_snapshot


def _snap(parts, pins, **kw):
    return DesignSnapshot.build("test", parts, pins, **kw)


def _codes(result):
    return {f["code"] for f in result["findings"]}


# -- net naming -------------------------------------------------------------

def test_ground_names():
    for n in ("GND", "gnd", "AGND", "DGND", "PGND", "VSS", "GND_RET",
              "/GND", "0V", "EARTH"):
        assert is_ground_net(n), n
    for n in ("SPI_CLK", "VCC", "NET1", "3V3"):
        assert not is_ground_net(n), n


def test_power_names_including_hierarchical_and_voltage():
    for n in ("VCC", "VDD", "3V3", "+5V", "-12V", "1V8", "/7.4V_Batt",
              "/B+", "/BAT_D", "VBAT", "/3V3", "VREF"):
        assert is_power_net(n), n
    for n in ("GND", "SPI_MOSI", "RESET", "Net-(U1-FB)", "CLK"):
        assert not is_power_net(n), n


# -- snapshot assembly ------------------------------------------------------

def test_build_groups_nets_and_infers_flags():
    parts = [{"refdes": "U1"}, {"refdes": "C1"}]
    pins = [
        {"refdes": "U1", "pin": "1", "net": "3V3"},
        {"refdes": "C1", "pin": "1", "net": "3V3"},
        {"refdes": "U1", "pin": "2", "net": "GND"},
        {"refdes": "C1", "pin": "2", "net": "GND"},
        {"refdes": "U1", "pin": "3", "net": ""},   # unconnected, dropped
    ]
    snap = _snap(parts, pins, unconnected_pad_count=1)
    names = {n.name for n in snap.nets}
    assert names == {"3V3", "GND"}
    p = {n.name: n for n in snap.nets}
    assert p["3V3"].is_power and not p["3V3"].is_ground
    assert p["GND"].is_ground and not p["GND"].is_power


def test_kind_from_refdes():
    snap = _snap([{"refdes": "R1"}, {"refdes": "C2"}, {"refdes": "U3"},
                  {"refdes": "L4"}, {"refdes": "FB1"}, {"refdes": "J5"}], [])
    kinds = {p.refdes: p.kind for p in snap.parts}
    assert kinds == {"R1": "resistor", "C2": "capacitor", "U3": "ic",
                     "L4": "inductor", "FB1": "ferrite", "J5": "connector"}


# -- review engine ----------------------------------------------------------

def test_clean_board_no_findings():
    parts = [{"refdes": "R1", "value": "10k"}, {"refdes": "R2", "value": "4k7"}]
    pins = [{"refdes": "R1", "pin": "1", "net": "A"},
            {"refdes": "R2", "pin": "1", "net": "A"},
            {"refdes": "R1", "pin": "2", "net": "B"},
            {"refdes": "R2", "pin": "2", "net": "B"}]
    res = review_snapshot(_snap(parts, pins))
    assert res["finding_count"] == 0
    assert res["summary"] == {"error": 0, "warning": 0, "info": 0}


def test_duplicate_reference_is_error():
    parts = [{"refdes": "R1", "value": "1k"}, {"refdes": "R1", "value": "1k"}]
    pins = [{"refdes": "R1", "pin": "1", "net": "A"},
            {"refdes": "R1", "pin": "2", "net": "B"}]
    res = review_snapshot(_snap(parts, pins))
    assert "duplicate_reference" in _codes(res)
    assert res["summary"]["error"] >= 1


def test_duplicate_reference_suppresses_derivative_shorted_pin():
    # Two parts sharing a reference must not be reported as a shorted pin just
    # because their like-numbered pins land on different nets.
    parts = [{"refdes": "JP1"}, {"refdes": "JP1"}]
    pins = [{"refdes": "JP1", "pin": "1", "net": "A"},
            {"refdes": "JP1", "pin": "1", "net": "B"}]
    res = review_snapshot(_snap(parts, pins))
    assert "shorted_pin" not in _codes(res)
    assert "duplicate_reference" in _codes(res)


def test_single_pin_net_is_warning():
    parts = [{"refdes": "R1"}]
    pins = [{"refdes": "R1", "pin": "1", "net": "LONELY"},
            {"refdes": "R1", "pin": "2", "net": "GND"}]
    # GND has one pin too; both are single-pin nets here.
    res = review_snapshot(_snap(parts, pins))
    assert "single_pin_net" in _codes(res)


def test_shorted_two_pin_passive():
    parts = [{"refdes": "R1", "value": "0R"}]
    pins = [{"refdes": "R1", "pin": "1", "net": "SAME"},
            {"refdes": "R1", "pin": "2", "net": "SAME"}]
    res = review_snapshot(_snap(parts, pins))
    assert "shorted_component" in _codes(res)


def test_unannotated_grouped_not_duplicate():
    parts = [{"refdes": "G***"}, {"refdes": "G***"}, {"refdes": "G***"}]
    res = review_snapshot(_snap(parts, []))
    assert "unannotated_reference" in _codes(res)
    assert "duplicate_reference" not in _codes(res)
    ann = [f for f in res["findings"] if f["code"] == "unannotated_reference"]
    assert len(ann) == 1 and "3 unannotated" in ann[0]["message"]


def test_unconnected_part_only_for_standard_designators():
    parts = [{"refdes": "R9"},                    # real, no pins -> flagged
             {"refdes": "kibuzzard-AB12"}]         # graphic label -> ignored
    res = review_snapshot(_snap(parts, []))
    unconn = [f["refs"] for f in res["findings"]
              if f["code"] == "unconnected_part"]
    flat = [r for refs in unconn for r in refs]
    assert "R9" in flat
    assert "kibuzzard-AB12" not in flat


def test_missing_decoupling_flagged_and_cleared_by_a_cap():
    ic_pins = [{"refdes": "U1", "pin": str(i), "net": "3V3"} for i in range(1, 9)]
    # No cap on 3V3 -> missing_decoupling.
    res = review_snapshot(_snap([{"refdes": "U1"}], ic_pins))
    assert "missing_decoupling" in _codes(res)
    # Add a cap on the same rail -> cleared.
    with_cap = ic_pins + [{"refdes": "C1", "pin": "1", "net": "3V3"}]
    res2 = review_snapshot(_snap([{"refdes": "U1"}, {"refdes": "C1"}], with_cap))
    assert "missing_decoupling" not in _codes(res2)


def test_net_classes_and_stats():
    parts = [{"refdes": "U1"}, {"refdes": "C1"}]
    pins = [{"refdes": "U1", "pin": "1", "net": "3V3"},
            {"refdes": "C1", "pin": "1", "net": "3V3"},
            {"refdes": "U1", "pin": "2", "net": "SIG"},
            {"refdes": "C1", "pin": "2", "net": "GND"}]
    res = review_snapshot(_snap(parts, pins, board_name="b", raw_stats={"vias": 5}))
    assert res["net_classes"]["by_net"]["3V3"] == "power"
    assert res["net_classes"]["by_net"]["GND"] == "ground"
    assert res["net_classes"]["by_net"]["SIG"] == "signal"
    assert res["stats"]["part_count"] == 2
    assert res["stats"]["vias"] == 5
    assert "3V3" in res["stats"]["power_rails"]
