# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Power/ground net classification used to pick the port representation.

A net is drawn as a power-port glyph (not wires/labels) when the planner
flags it OR names it conventionally. These tests pin the name heuristic and,
critically, its conservatism: signal nets that merely contain a voltage token
must NOT be pulled onto a port.
"""

from __future__ import annotations

import pytest

from eda_agent.design._wiring import (
    _is_ground_net,
    _is_power_net,
    _net_representation,
)
from eda_agent.design.plan import Net, PinRef


def _net(name: str, **flags) -> Net:
    return Net(
        name=name,
        pins=[PinRef(refdes="R1", pin="1"), PinRef(refdes="R2", pin="2")],
        **flags,
    )


@pytest.mark.parametrize("name", ["GND", "AGND", "DGND", "PGND", "VSS", "EARTH"])
def test_ground_names_detected(name):
    assert _is_ground_net(_net(name)) is True


@pytest.mark.parametrize("name", ["VSS_MON", "SIGNAL", "CLK", "DATA"])
def test_non_ground_names_rejected(name):
    assert _is_ground_net(_net(name)) is False


@pytest.mark.parametrize(
    "name", ["VCC", "VDD", "VBAT", "VBUS", "V5", "V12", "V3V3", "P3V3"])
def test_power_rail_names_detected(name):
    assert _is_power_net(_net(name)) is True


@pytest.mark.parametrize(
    "name", ["VOUT", "VIN", "V5_SENSE", "VCCO", "ADC", "CLK", "MISO"])
def test_signal_names_not_power(name):
    """Conservative: a signal that merely contains a voltage-like token is
    never mistaken for a supply rail."""
    assert _is_power_net(_net(name)) is False


def test_flag_overrides_name():
    assert _is_power_net(_net("SOME_RAIL", is_power=True)) is True
    assert _is_ground_net(_net("RETURN", is_ground=True)) is True


def test_representation_ports_named_rails_without_flags():
    """A net named GND/VCC but missing the flag still routes to a port glyph
    (previously it fell through to wires/labels and tangled the sheet)."""
    one_zone = {"R1": "z", "R2": "z"}
    assert _net_representation(_net("GND"), one_zone) == "port"
    assert _net_representation(_net("VCC"), one_zone) == "port"
    assert _net_representation(_net("V3V3"), one_zone) == "port"
    # A genuine signal still routes as a wire within one zone.
    assert _net_representation(_net("VOUT"), one_zone) == "wire"
    assert _net_representation(_net("DATA"), one_zone) == "wire"
