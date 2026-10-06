# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Pure-Python BOM decoupling audit: every IC power pin wants a cap.

Focus: the supply-rail recognition that decides which IC pins are 'power'
and therefore must be checked. Analog/battery/USB/reference rails are easy
to miss; missing one silently skips its decoupling check.
"""

from __future__ import annotations

import pytest

from eda_agent.tools.audit import _net_is_power, find_missing_decoupling_from_bom


@pytest.mark.parametrize(
    "rail", ["VCC", "VDD", "VCCA", "VDDIO", "V3V3", "V5",
             "AVDD", "AVCC", "VBAT", "VBUS", "VEE", "VPP", "VREF",
             "AVDD3V3", "VREF_ADC"])
def test_supply_rails_recognised(rail):
    assert _net_is_power(rail) is True


@pytest.mark.parametrize("sig", ["VOUT", "VIN", "CLK", "DATA", "MISO", "SDA"])
def test_signals_not_power(sig):
    assert _net_is_power(sig) is False


def _bom(pins_u1, caps_on_nets):
    """Build a minimal BOM: U1 with the given pins + one cap per listed net."""
    comps = [{"designator": "U1",
              "pins": [{"pin": str(i + 1), "name": nm, "net": net}
                       for i, (nm, net) in enumerate(pins_u1)]}]
    for i, net in enumerate(caps_on_nets):
        comps.append({"designator": f"C{i+1}",
                      "pins": [{"pin": "1", "name": "1", "net": net},
                               {"pin": "2", "name": "2", "net": "GND"}]})
    return {"components": comps}


def test_analog_supply_pin_without_cap_is_flagged():
    """U1 has VCC (decoupled) and AVDD (NOT decoupled). The analog rail must
    be recognised as a power pin so the missing cap is reported -- previously
    AVDD was not seen as power and the violation was silently skipped."""
    bom = _bom(pins_u1=[("VCC", "VCC"), ("AVDD", "AVDD"), ("OUT", "SIG")],
               caps_on_nets=["VCC"])   # cap on VCC only
    rep = find_missing_decoupling_from_bom(bom)
    assert rep["checked"] == 1
    items = {it["designator"]: it for it in rep["items"]}
    assert "U1" in items
    assert items["U1"]["status"] == "partial"
    uncovered = {p["net"] for p in items["U1"]["uncovered_pins"]}
    assert "AVDD" in uncovered          # the gap this fix closes
    assert "VCC" not in uncovered       # VCC is decoupled


def test_fully_decoupled_ic_is_clean():
    """Every power pin has a cap -> no violation."""
    bom = _bom(pins_u1=[("VCC", "VCC"), ("AVDD", "AVDD")],
               caps_on_nets=["VCC", "AVDD"])
    rep = find_missing_decoupling_from_bom(bom)
    assert rep["checked"] == 1
    assert rep["violations"] == 0
