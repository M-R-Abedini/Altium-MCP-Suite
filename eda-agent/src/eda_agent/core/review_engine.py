# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The EDA-agnostic design review.

One function, :func:`review_snapshot`, runs every check on a
:class:`DesignSnapshot` and returns a single result. Because it only touches the
normalized snapshot, the exact same review runs against an Altium board and a
KiCad board -- the backend adapter is the only thing that differs.

Checks (annotation + connectivity + power) mirror the netlist-level core of the
Altium review: annotation hygiene, single-pin nets, shorts, unconnected parts
and pads, and decoupling presence. Geometric DRC (clearances, courtyards) is a
backend concern layered on top, not part of this netlist-level pass.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .snapshot import DesignSnapshot, SnapPart

# A conventional reference designator: letter(s) then a number, optional suffix
# (R4, U1, JP3, C12A). Used to tell real components from graphics / text
# footprints that carry a non-designator label.
_STD_DESIGNATOR = re.compile(r"^[A-Za-z]+\d+[A-Za-z]?$")

# Kinds that legitimately have no netlist connection, so "unconnected" or
# "no value" is not a finding for them.
_NON_ELECTRICAL = {"mechanical", "graphic"}
# Two-terminal passives that are shorted if both pins share one net.
_TWO_PIN_PASSIVE = {"resistor", "capacitor", "inductor", "ferrite", "fuse"}
# An IC is expected to be decoupled once it reaches this many connected pins.
_DECOUPLE_PIN_THRESHOLD = 8


def _is_placeholder_ref(ref: str) -> bool:
    r = (ref or "").strip()
    return (not r) or ("*" in r) or ("?" in r)


def _finding(code: str, severity: str, dimension: str, message: str,
             *, refs: list[str] | None = None, net: str | None = None) -> dict:
    return {
        "code": code,
        "severity": severity,
        "dimension": dimension,
        "message": message,
        "refs": refs or [],
        "net": net,
    }


def _annotation_findings(snap: DesignSnapshot) -> list[dict]:
    out: list[dict] = []
    placeholders: dict[str, int] = defaultdict(int)
    real: dict[str, list[str]] = defaultdict(list)
    for part in snap.parts:
        ref = part.refdes.strip()
        if _is_placeholder_ref(ref):
            placeholders[ref or "(blank)"] += 1
        else:
            real[ref].append(ref)
        if not part.value.strip() and part.kind not in _NON_ELECTRICAL:
            out.append(_finding(
                "missing_value", "info", "annotation",
                f"{ref or '(blank)'} has no value", refs=[ref] if ref else []))
    for ref, n in sorted(placeholders.items()):
        plural = "footprints" if n > 1 else "footprint"
        out.append(_finding(
            "unannotated_reference", "warning", "annotation",
            f"{n} unannotated {plural} (reference '{ref}')", refs=[ref]))
    for ref, uses in sorted(real.items()):
        if len(uses) > 1:
            out.append(_finding(
                "duplicate_reference", "error", "annotation",
                f"reference '{ref}' is used by {len(uses)} parts", refs=[ref]))
    return out


def _part_pin_index(snap: DesignSnapshot) -> dict[str, list[tuple[str, str]]]:
    """refdes -> [(pin, net), ...] over connected pins only."""
    idx: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for net in snap.nets:
        for p in net.pins:
            idx[p.refdes].append((p.pin, net.name))
    return idx


def _connectivity_findings(snap: DesignSnapshot,
                           part_pins: dict[str, list[tuple[str, str]]]) -> list[dict]:
    out: list[dict] = []

    # Single-pin nets -- a named net that reaches exactly one pin is almost
    # always a broken or unrouted connection.
    for net in snap.nets:
        if len(net.pins) == 1:
            p = net.pins[0]
            out.append(_finding(
                "single_pin_net", "warning", "connectivity",
                f"net '{net.name}' reaches only one pin ({p.refdes}.{p.pin})",
                refs=[p.refdes], net=net.name))

    # Same physical pin claimed by more than one net. Skip references that are
    # duplicated across parts -- their pin identity is ambiguous, so an
    # apparent multi-net pin is an artifact of the duplicate (already reported),
    # not a real short.
    ref_counts: dict[str, int] = defaultdict(int)
    for part in snap.parts:
        r = part.refdes.strip()
        if r and not _is_placeholder_ref(r):
            ref_counts[r] += 1
    ambiguous = {r for r, n in ref_counts.items() if n > 1}
    pin_nets: dict[tuple[str, str], set[str]] = defaultdict(set)
    for net in snap.nets:
        for p in net.pins:
            if p.refdes in ambiguous:
                continue
            pin_nets[(p.refdes, p.pin)].add(net.name)
    for (ref, pin), names in sorted(pin_nets.items()):
        if len(names) > 1:
            out.append(_finding(
                "shorted_pin", "error", "connectivity",
                f"pin {ref}.{pin} is on {len(names)} nets: "
                f"{', '.join(sorted(names))}", refs=[ref]))

    # Two-pin passive with both pins on one net (a dead short across it).
    by_kind = {p.refdes: p.kind for p in snap.parts}
    for ref, entries in sorted(part_pins.items()):
        if by_kind.get(ref) in _TWO_PIN_PASSIVE and len(entries) >= 2:
            nets_on = {n for _, n in entries}
            if len(nets_on) == 1:
                out.append(_finding(
                    "shorted_component", "error", "connectivity",
                    f"{ref} has both terminals on net "
                    f"'{next(iter(nets_on))}'", refs=[ref],
                    net=next(iter(nets_on))))

    # Parts with no connected pin at all. Only real components (standard
    # designator, electrical kind) count -- graphics and text footprints carry
    # non-designator labels and legitimately have no netlist connection.
    for part in snap.parts:
        if part.kind in _NON_ELECTRICAL or part.dnp:
            continue
        if not _STD_DESIGNATOR.match(part.refdes.strip()):
            continue
        if not part_pins.get(part.refdes):
            out.append(_finding(
                "unconnected_part", "warning", "connectivity",
                f"{part.refdes} has no connected pins", refs=[part.refdes]))

    # Pads with no net (board-level count from the adapter).
    if snap.unconnected_pad_count:
        out.append(_finding(
            "unconnected_pads", "warning", "connectivity",
            f"{snap.unconnected_pad_count} pad(s) belong to no net"))
    return out


def _power_findings(snap: DesignSnapshot,
                    part_pins: dict[str, list[tuple[str, str]]]) -> list[dict]:
    out: list[dict] = []
    by_kind = {p.refdes: p.kind for p in snap.parts}

    for net in snap.nets:
        if net.is_power and net.is_ground:
            out.append(_finding(
                "contradictory_net_flags", "error", "power",
                f"net '{net.name}' reads as both power and ground",
                net=net.name))

    # Missing decoupling: a power rail that a sizeable IC sits on but that no
    # capacitor also touches.
    caps_on_net: dict[str, bool] = defaultdict(bool)
    for net in snap.nets:
        for p in net.pins:
            if by_kind.get(p.refdes) == "capacitor":
                caps_on_net[net.name] = True
    ic_pin_count: dict[str, int] = {
        ref: len(entries) for ref, entries in part_pins.items()
        if by_kind.get(ref) == "ic"}
    for net in snap.nets:
        if not net.is_power:
            continue
        ics = sorted({p.refdes for p in net.pins
                      if by_kind.get(p.refdes) == "ic"
                      and ic_pin_count.get(p.refdes, 0) >= _DECOUPLE_PIN_THRESHOLD})
        if ics and not caps_on_net.get(net.name):
            out.append(_finding(
                "missing_decoupling", "warning", "power",
                f"power net '{net.name}' feeds {', '.join(ics)} but has no "
                f"decoupling capacitor", refs=ics, net=net.name))
    return out


def _net_classes(snap: DesignSnapshot) -> dict[str, Any]:
    by_net: dict[str, str] = {}
    groups: dict[str, list[str]] = defaultdict(list)
    for net in snap.nets:
        if net.is_ground:
            cls = "ground"
        elif net.is_power:
            cls = "power"
        else:
            cls = net.role or "signal"
        by_net[net.name] = cls
        groups[cls].append(net.name)
    return {"by_net": by_net,
            "groups": {k: sorted(v) for k, v in sorted(groups.items())}}


def _stats(snap: DesignSnapshot,
           part_pins: dict[str, list[tuple[str, str]]]) -> dict[str, Any]:
    by_kind: dict[str, int] = defaultdict(int)
    for p in snap.parts:
        by_kind[p.kind] += 1
    connected_pins = sum(len(v) for v in part_pins.values())
    stats = {
        "source": snap.source,
        "board": snap.board_name,
        "part_count": len(snap.parts),
        "net_count": len(snap.nets),
        "connected_pin_count": connected_pins,
        "unconnected_pad_count": snap.unconnected_pad_count,
        "parts_by_kind": dict(sorted(by_kind.items())),
        "power_rails": sorted(n.name for n in snap.nets if n.is_power),
        "ground_nets": sorted(n.name for n in snap.nets if n.is_ground),
    }
    # Fold in whatever geometric counts the backend supplied (tracks, vias,
    # zones, layers, ...) without letting them clobber the computed keys.
    for k, v in (snap.raw_stats or {}).items():
        stats.setdefault(f"board_{k}" if k in stats else k, v)
    return stats


def review_snapshot(snap: DesignSnapshot) -> dict[str, Any]:
    """Run the full agnostic review on a snapshot and return one result dict."""
    part_pins = _part_pin_index(snap)
    findings: list[dict] = []
    findings += _annotation_findings(snap)
    findings += _connectivity_findings(snap, part_pins)
    findings += _power_findings(snap, part_pins)

    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: (order.get(f["severity"], 3), f["dimension"],
                                 f["code"]))
    summary = {"error": 0, "warning": 0, "info": 0}
    for f in findings:
        summary[f["severity"]] = summary.get(f["severity"], 0) + 1

    return {
        "ok": True,
        "source": snap.source,
        "board": snap.board_name,
        "stats": _stats(snap, part_pins),
        "net_classes": _net_classes(snap),
        "summary": summary,
        "finding_count": len(findings),
        "findings": findings,
    }
