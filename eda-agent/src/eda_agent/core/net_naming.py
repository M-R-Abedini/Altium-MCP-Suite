# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Power / ground net recognition by conventional naming.

A live board snapshot (unlike an authored plan) carries no is_power/is_ground
flags, so the shared review engine infers them from the net name. These are
industry-standard rail conventions (GND, VCC, +3V3, ...), not anything specific
to one library, so name-based inference is appropriate here. It only feeds
net-class labelling and the decoupling heuristic; a miss degrades gracefully.
"""

from __future__ import annotations

import re

# Ground rails: GND and its domain variants, VSS family, and the plain-language
# forms. Kept deliberately conservative so a signal net is not mistaken for one.
_GROUND = re.compile(
    r"^(?:[ad-gp-su]?gnd|gnd[ad]?|vss[ad]?|dgnd|agnd|pgnd|sgnd|"
    r"ground|earth|chassis|0v)(?:[_\-/]?[a-z0-9]+)?$",
    re.IGNORECASE,
)

# Power rails: the V-prefixed families (VCC/VDD/VBAT/...), explicit voltage
# tokens like 3V3 / +5V / -12V / 1V8, and the negative-supply names.
_POWER_VFAMILY = re.compile(
    r"^[+\-]?v(?:cc|dd|bat|in|out|bus|sys|ref|ee|pp|aa|"
    r"dda|cca|ddio|ccio|core|io|ana|dig|logic|mem|ddr|"
    r"cc\d|dd\d)?(?:[_\-/]?[a-z0-9]+)?$",
    re.IGNORECASE,
)
# 3V3, +5V, -12V, 1V8, 24V, +3.3V ...
_POWER_VOLTAGE = re.compile(
    r"^[+\-]?\d+(?:[v.]\d+)?v?(?:[_\-/]?[a-z0-9]+)?$",
    re.IGNORECASE,
)
_POWER_WORD = re.compile(
    r"^(?:pwr|power|vsupply|supply|vrail|vbat|vbatt|bat|batt|b\+)"
    r"(?:[_\-/]?[a-z0-9]+)?$",
    re.IGNORECASE,
)


def _local(name: str) -> str:
    """The local net name: KiCad prefixes hierarchical nets with a sheet path
    ("/7.4V_Batt"); classification keys on the trailing segment."""
    n = (name or "").strip()
    if "/" in n:
        n = n.rstrip("/").split("/")[-1]
    return n.strip()


def is_ground_net(name: str) -> bool:
    n = _local(name)
    return bool(n) and bool(_GROUND.match(n))


def is_power_net(name: str) -> bool:
    n = _local(name)
    if not n or is_ground_net(name):
        return False
    if _POWER_VFAMILY.match(n) or _POWER_WORD.match(n):
        return True
    # A bare voltage token must contain a 'V' to count (so "3V3"/"+5V" match but
    # "12" or "SPI0" do not).
    return bool(_POWER_VOLTAGE.match(n)) and "v" in n.lower()
