# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Post-emission net verification.

_verify_nets compares the plan's intended topology against Altium's
compiled netlist, and it exists to catch the fault ERC cannot: two nets
bridged by a stray wire look to Altium like ONE valid net, so every pin
is connected and ERC passes. If this check is wrong or quietly skipped,
that short ships.

It had no test at all. That matters more here than for most helpers,
because its failure mode is a clean report: no mismatches is exactly
what a correct design produces, so a broken verifier is indistinguishable
from a good result.

Topology is what is compared, not names. Altium auto-generates names like
NetR1_1 for unlabelled nets, so the fixtures below deliberately use
names that match nothing in the plan.
"""

from __future__ import annotations

import pytest

from eda_agent.design.executor import ExecutorResult, _verify_nets
from eda_agent.design.plan import DesignPlan, Net, Part, PinRef, Sheet


def _plan() -> DesignPlan:
    """Two nets, two pins each, sharing no pin."""
    return DesignPlan(
        spec="divider",
        summary="Two resistors forming a divider.",
        sheets=[Sheet(name="main")],
        parts=[
            Part(refdes="R1", lib_ref="RES_0805", value="1k", sheet="main"),
            Part(refdes="R2", lib_ref="RES_0805", value="1k", sheet="main"),
        ],
        nets=[
            Net(name="VIN", pins=[PinRef(refdes="R1", pin="1"),
                                  PinRef(refdes="R2", pin="1")]),
            Net(name="VOUT", pins=[PinRef(refdes="R1", pin="2"),
                                   PinRef(refdes="R2", pin="2")]),
        ],
    )


class _Bridge:
    def __init__(self, netlist):
        self.netlist = netlist

    def send_command(self, command, params=None, timeout=None):
        return self.netlist


def _pins(*triples):
    return {"pins": [{"component": c, "pin": p, "net": n}
                     for c, p, n in triples]}


def _run(netlist):
    result = ExecutorResult()
    _verify_nets(_plan(), r"C:\p\proj.PrjPcb", _Bridge(netlist), result)
    return result


def test_a_correct_design_reports_nothing_despite_generated_names():
    """The names differ from the plan's on purpose: matching topology
    with auto-generated names is the NORMAL case, and flagging it would
    make the check useless noise."""
    result = _run(_pins(
        ("R1", "1", "NetR1_1"), ("R2", "1", "NetR1_1"),
        ("R1", "2", "NetR1_2"), ("R2", "2", "NetR1_2"),
    ))
    assert result.net_mismatches == []


def test_a_short_is_detected():
    """Both plan nets landed on ONE Altium net. ERC sees a single valid
    net and passes; this is the only thing that catches it."""
    result = _run(_pins(
        ("R1", "1", "NetR1_1"), ("R2", "1", "NetR1_1"),
        ("R1", "2", "NetR1_1"), ("R2", "2", "NetR1_1"),
    ))
    codes = [m.code for m in result.net_mismatches]
    assert "NET_SHORT" in codes, codes
    short = next(m for m in result.net_mismatches if m.code == "NET_SHORT")
    assert "VIN" in short.plan_net and "VOUT" in short.plan_net
    assert short.actual_nets == ["NetR1_1"]


def test_an_open_is_detected():
    """One plan net's pins split across two Altium nets: a wire missing."""
    result = _run(_pins(
        ("R1", "1", "NetR1_1"), ("R2", "1", "NetR2_1"),   # VIN split
        ("R1", "2", "NetR1_2"), ("R2", "2", "NetR1_2"),
    ))
    opens = [m for m in result.net_mismatches if m.code == "NET_OPEN"]
    assert opens, [m.code for m in result.net_mismatches]
    assert opens[0].plan_net == "VIN"
    assert opens[0].actual_nets == ["NetR1_1", "NetR2_1"]


def test_a_pin_absent_from_the_netlist_is_reported():
    result = _run(_pins(
        ("R1", "1", "NetR1_1"),                            # R2.1 missing
        ("R1", "2", "NetR1_2"), ("R2", "2", "NetR1_2"),
    ))
    missing = [m for m in result.net_mismatches
               if m.code == "NET_MISSING_PIN"]
    assert missing, [m.code for m in result.net_mismatches]
    assert missing[0].pins == ["R2.1"]


def test_a_short_and_an_open_are_both_reported():
    """One report per fault: stopping at the first would hide the rest of
    a badly wired sheet behind one message."""
    result = _run(_pins(
        ("R1", "1", "NetX"), ("R2", "1", "NetY"),          # VIN open
        ("R1", "2", "NetX"), ("R2", "2", "NetX"),          # VOUT shares NetX
    ))
    codes = {m.code for m in result.net_mismatches}
    assert {"NET_OPEN", "NET_SHORT"} <= codes, codes


def test_a_non_dict_answer_is_reported_not_swallowed():
    result = _run(["unexpected", "list"])
    assert result.net_mismatches == []
    assert any("non-dict" in n for n in result.notes), result.notes


def test_a_netlist_without_pins_says_verification_was_skipped():
    """The dangerous path. No mismatches here does NOT mean the design is
    clean, so it has to say so rather than return silently."""
    result = _run({"count": 0})
    assert result.net_mismatches == []
    assert any("skipped" in n.lower() for n in result.notes), result.notes


def test_an_empty_pin_list_is_not_treated_as_verification_passing():
    """An empty netlist means nothing was matched, so every planned pin
    is missing; reporting a clean design here would be a lie."""
    result = _run({"pins": []})
    codes = {m.code for m in result.net_mismatches}
    assert codes == {"NET_MISSING_PIN"}, codes
    assert len(result.net_mismatches) == 4
