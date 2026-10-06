# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The normalized design snapshot shared by every EDA backend.

A snapshot is a flat, backend-neutral view of a board: its parts, the pins on
those parts, and the nets that join them. Both the Altium and KiCad adapters
produce exactly this shape, so the review engine and neutral tools never learn
which EDA they are talking to.

The attribute names (``net.pins[].refdes/.pin``, ``net.is_power/.is_ground``,
``part.refdes/.value``) mirror the authored-plan model on purpose, so the
offline analysis idioms carry over -- but unlike the plan model this one imposes
no validation (single-pin nets, odd reference strings and unconnected pins are
all representable, because a real board contains them and the review needs to
see them).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .net_naming import is_ground_net, is_power_net

# Reference-designator prefix -> coarse kind. Naming-agnostic beyond the
# universal single-letter EDA convention; anything unmapped is "other".
_KIND_BY_PREFIX = {
    "R": "resistor", "C": "capacitor", "L": "inductor", "FB": "ferrite",
    "U": "ic", "IC": "ic", "D": "diode", "LED": "led", "Q": "transistor",
    "Y": "crystal", "X": "crystal", "XTAL": "crystal",
    "J": "connector", "P": "connector", "CN": "connector", "CON": "connector",
    "SW": "switch", "S": "switch", "K": "relay", "T": "transformer",
    "F": "fuse", "TP": "testpoint", "MH": "mechanical", "MP": "mechanical",
    "H": "mechanical", "G": "graphic", "BT": "battery", "B": "battery",
    "ANT": "antenna", "M": "module", "RN": "resistor", "CN": "connector",
}


def _kind_from_refdes(refdes: str) -> str:
    """Coarse component kind from the leading letters of a reference."""
    r = (refdes or "").strip().upper()
    if not r:
        return "other"
    prefix = ""
    for ch in r:
        if ch.isalpha():
            prefix += ch
        else:
            break
    if not prefix:
        return "other"
    # Longest known prefix wins (FB before F, LED before L).
    for n in (len(prefix), 3, 2, 1):
        cand = prefix[:n]
        if cand in _KIND_BY_PREFIX:
            return _KIND_BY_PREFIX[cand]
    return "other"


@dataclass
class SnapPin:
    """One pin/pad of a part, and the net it sits on (``""`` = unconnected)."""
    refdes: str
    pin: str
    net: str = ""


@dataclass
class SnapPart:
    """A placed component."""
    refdes: str
    value: str = ""
    footprint: str = ""
    lib_ref: str = ""
    layer: Optional[Any] = None
    locked: Optional[bool] = None
    dnp: bool = False

    @property
    def kind(self) -> str:
        return _kind_from_refdes(self.refdes)


@dataclass
class SnapNet:
    """A net and the pins on it."""
    name: str
    pins: list[SnapPin] = field(default_factory=list)
    is_power: bool = False
    is_ground: bool = False
    role: str = ""

    @property
    def part_refs(self) -> set[str]:
        return {p.refdes for p in self.pins}


@dataclass
class DesignSnapshot:
    """Everything the shared review needs, filled by a backend adapter."""
    source: str                       # "altium" | "kicad"
    board_name: str = ""
    parts: list[SnapPart] = field(default_factory=list)
    nets: list[SnapNet] = field(default_factory=list)
    unconnected_pad_count: int = 0
    raw_stats: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        source: str,
        parts: list[dict[str, Any]],
        pins: list[dict[str, Any]],
        *,
        board_name: str = "",
        unconnected_pad_count: int = 0,
        raw_stats: Optional[dict[str, Any]] = None,
    ) -> "DesignSnapshot":
        """Assemble a snapshot from flat part and pin dicts.

        ``parts`` items: ``{refdes, value?, footprint?, lib_ref?, layer?,
        locked?, dnp?}``. ``pins`` items: ``{refdes, pin, net}`` (net ``""`` /
        missing means unconnected). Nets are grouped from the connected pins and
        power/ground flags inferred from their names.
        """
        snap_parts = [
            SnapPart(
                refdes=str(p.get("refdes", "")),
                value=str(p.get("value", "") or ""),
                footprint=str(p.get("footprint", "") or ""),
                lib_ref=str(p.get("lib_ref", "") or ""),
                layer=p.get("layer"),
                locked=p.get("locked"),
                dnp=bool(p.get("dnp", False)),
            )
            for p in parts
        ]

        by_net: dict[str, SnapNet] = {}
        for pin in pins:
            net_name = str(pin.get("net", "") or "").strip()
            if not net_name:
                continue
            sp = SnapPin(
                refdes=str(pin.get("refdes", "")),
                pin=str(pin.get("pin", "")),
                net=net_name,
            )
            net = by_net.get(net_name)
            if net is None:
                net = SnapNet(
                    name=net_name,
                    is_power=is_power_net(net_name),
                    is_ground=is_ground_net(net_name),
                )
                by_net[net_name] = net
            net.pins.append(sp)

        nets = [by_net[name] for name in sorted(by_net)]
        return cls(
            source=source,
            board_name=board_name,
            parts=snap_parts,
            nets=nets,
            unconnected_pad_count=unconnected_pad_count,
            raw_stats=dict(raw_stats or {}),
        )
