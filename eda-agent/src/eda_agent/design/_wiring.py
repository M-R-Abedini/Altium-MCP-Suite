# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Pure-Python helpers shared by executor.py and pipeline.py.

The legacy ``executor.py`` and the new canvas-based ``pipeline.py``
both need: BOM/parameter resolution, net-representation rules (port vs
wire vs label), ground-style heuristics, junction detection, and the
project-stem-namespaced sheet path. Keeping two copies risks drift.

This module is the single source of truth; both callers import from
here. The helpers are name-mangled with a leading underscore for the
legacy executor's expectations; the pipeline imports them under the
same names so a future un-mangling pass can flip both at once.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from eda_agent.design.plan import DesignPlan, Net, Part


def _sheet_path(project_path: Path, sheet_name: str) -> Path:
    """Default SchDoc path for a plan-declared sheet.

    Namespaced by the project stem so two projects in the same workspace can
    both declare a sheet called 'main' without colliding on disk. Without the
    namespace, calling design.execute_plan twice with two different .PrjPcb
    paths but the same plan.sheets[].name silently writes the second design
    onto the first one's SchDoc, mixing parts from two unrelated projects.
    """
    return project_path.parent / f"{project_path.stem}__{sheet_name}.SchDoc"


def _bom_lookup(plan: DesignPlan) -> dict[str, tuple[Optional[str], Optional[str]]]:
    """Map refdes -> (manufacturer, mpn) drawn from plan.bom for fallback.

    Built once per execution so the per-part lookup is O(1). Multiple BomLines
    can reference the same refdes only if there's a planner bug; first match
    wins.
    """
    lookup: dict[str, tuple[Optional[str], Optional[str]]] = {}
    for line in plan.bom:
        for refdes in line.refdes_list:
            lookup.setdefault(refdes, (line.manufacturer, line.mpn))
    return lookup


def _part_parameters(
    part: Part,
    bom_lookup: dict[str, tuple[Optional[str], Optional[str]]],
) -> dict[str, str]:
    """Build the parameter sub-object the Pascal handler will stamp.

    Resolution order for Manufacturer / MPN: Part fields win over BomLine
    fallback. Empty values are stripped so the Pascal side has nothing to
    skip and the IPC payload stays compact.
    """
    bom_mfr, bom_mpn = bom_lookup.get(part.refdes, (None, None))
    mfr = part.manufacturer or bom_mfr
    mpn = part.mpn or bom_mpn

    candidate: dict[str, Optional[str]] = {
        "Value": part.value,
        "Manufacturer": mfr,
        "Manufacturer Part Number": mpn,
        "Footprint": part.footprint,
    }
    return {k: v for k, v in candidate.items() if v}


def _detect_junctions(
    wires: list[tuple[int, int, int, int]]
    | list[tuple[int, int, int, int, str]],
) -> list[tuple[int, int]]:
    """Return the set of points that need a junction dot.

    NET-AWARE: a junction dot is an electrical CONNECTION, so it must only be
    placed where wires of the SAME net meet. Two different nets whose wires
    merely cross or touch must NOT get a dot -- that would short them. Wires
    may therefore carry a 5th element, the net name; same-coordinate meetings
    of DIFFERENT nets are ignored. Four-tuple wires (no net) are all treated as
    one net -- correct when the caller already passes a single net's wires.

    Two same-net cases need a dot:

    1. **Three or more same-net wire endpoints coincide** (a T- or 4-way join).
    2. **A same-net wire endpoint sits strictly between the endpoints of
       another same-net wire's axis-aligned segment** ("wire stops on wire").

    Only axis-aligned segments (the only kind we emit) are checked.
    """
    from collections import Counter

    # Normalise to (x1, y1, x2, y2, net); net=None groups all four-tuples.
    norm = [
        (w[0], w[1], w[2], w[3], w[4] if len(w) > 4 else None)
        for w in wires
    ]

    # 1. Three+ endpoints of the SAME net coinciding.
    endpoint_counts: Counter = Counter()
    for (x1, y1, x2, y2, net) in norm:
        endpoint_counts[((x1, y1), net)] += 1
        endpoint_counts[((x2, y2), net)] += 1
    junctions: set[tuple[int, int]] = {
        pt for (pt, _net), c in endpoint_counts.items() if c >= 3
    }

    # 2. A same-net endpoint terminating on a same-net segment's interior.
    endpoints_by_net: dict[object, set[tuple[int, int]]] = {}
    for (x1, y1, x2, y2, net) in norm:
        s = endpoints_by_net.setdefault(net, set())
        s.add((x1, y1))
        s.add((x2, y2))
    for (sx1, sy1, sx2, sy2, net) in norm:
        for (x, y) in endpoints_by_net.get(net, ()):
            if (x, y) == (sx1, sy1) or (x, y) == (sx2, sy2):
                continue
            if sx1 == sx2 and x == sx1:
                if min(sy1, sy2) < y < max(sy1, sy2):
                    junctions.add((x, y))
            elif sy1 == sy2 and y == sy1:
                if min(sx1, sx2) < x < max(sx1, sx2):
                    junctions.add((x, y))

    return sorted(junctions)


def _cross_net_meeting_counts(
    wires: list[tuple[int, int, int, int, str]],
) -> dict[str, int]:
    """Per-net count of CROSS-NET wire meetings (the geometries Altium
    auto-junctions on compile, silently shorting the two nets).

    A meeting is (a) two different nets' wire ENDPOINTS coinciding, or (b) one
    net's endpoint terminating on a DIFFERENT net's segment interior (a T).
    Pure crossings -- two segments crossing with no shared point -- are NOT
    counted: Altium does not auto-junction those, so they are safe. The result
    maps each offending net to how many meetings it touches, so a caller can
    greedily fall the worst offender back to labels until none remain.

    The count is an offender SCORE, not a count of distinct short locations:
    a net that chains several wire endpoints through one meeting point scores
    once per endpoint, deliberately ranking heavily-entangled nets first for
    the greedy label-fallback.
    """
    from collections import defaultdict

    counts: dict[str, int] = defaultdict(int)

    # (a) endpoint coincidences across nets.
    nets_at: dict[tuple[int, int], set[str]] = defaultdict(set)
    for (x1, y1, x2, y2, net) in wires:
        nets_at[(x1, y1)].add(net)
        nets_at[(x2, y2)].add(net)
    for nets in nets_at.values():
        if len(nets) > 1:
            for n in nets:
                counts[n] += 1

    # (b) an endpoint of one net terminating on another net's segment interior.
    for (x1, y1, x2, y2, net) in wires:
        for (ex, ey) in ((x1, y1), (x2, y2)):
            for (sx1, sy1, sx2, sy2, snet) in wires:
                if snet == net:
                    continue
                if (ex, ey) == (sx1, sy1) or (ex, ey) == (sx2, sy2):
                    continue
                if sx1 == sx2 and ex == sx1 and min(sy1, sy2) < ey < max(sy1, sy2):
                    counts[net] += 1
                    counts[snet] += 1
                elif sy1 == sy2 and ey == sy1 and min(sx1, sx2) < ex < max(sx1, sx2):
                    counts[net] += 1
                    counts[snet] += 1

    return dict(counts)


def _ground_style(net_name: str) -> str:
    """Pick a power-port style for an is_ground net based on its name.

    Altium has separate gnd_power / gnd_signal / gnd_earth glyphs. When the
    net name carries a hint we honour it; otherwise default to gnd_power.
    """
    upper = net_name.upper()
    if "EARTH" in upper or upper == "PE":
        return "gnd_earth"
    if "AGND" in upper or "ANALOG" in upper or upper == "AGND":
        return "gnd_signal"
    return "gnd_power"


# Explicit power-rail net names (uppercased) recognised when the planner names
# a rail conventionally but omits is_power. Kept to unambiguous supply rails so
# a signal that merely contains a voltage (e.g. "ADC_2V5_REF") is NOT pulled
# onto a port glyph.
_POWER_RAIL_NAMES = frozenset({
    "VCC", "VDD", "VEE", "VBAT", "VDDA", "VCCA", "AVDD", "AVCC", "AVEE",
    "VBUS", "VPP", "VDDIO", "VCCIO", "VREF", "VSYS", "VIO",
})
# Bare voltage-rail tokens: V5, V12, V3V3, P3V3, V1V8 (a letter-led name the
# plan schema allows, all-digits-after). Anchored so suffixes like "_SENSE"
# break the match.
_POWER_RAIL_RE = re.compile(r"^[VP][0-9]+([VP][0-9]+)?$")


def _is_ground_net(net: Net) -> bool:
    """True if the net is ground -- by the is_ground flag OR an unambiguous
    ground name (GND family, VSS, EARTH/PE). Mirrors the name heuristic the
    port emitter already uses so representation and styling agree."""
    if net.is_ground:
        return True
    upper = net.name.upper()
    return ("GND" in upper or upper in ("VSS", "VSSA", "EARTH", "PE"))


def _is_power_net(net: Net) -> bool:
    """True if the net is a power rail -- by the is_power flag OR a recognised
    rail name. Conservative on names (explicit set + strict voltage token) so
    a signal net is never mistaken for a supply."""
    if net.is_power:
        return True
    upper = net.name.upper()
    return upper in _POWER_RAIL_NAMES or bool(_POWER_RAIL_RE.match(upper))


def _power_port_orientation(pin_orientation: int, is_ground: bool) -> int:
    """Canonical schematic convention:

    - VCC / power rails ALWAYS point up   (orientation 1) -- bar / circle
      glyph sits above the connection point.
    - GND ALWAYS points down              (orientation 3) -- triangle /
      bar glyph hangs below the connection point.

    Independent of the pin's outward direction. The stub wire absorbs
    the horizontal-vs-vertical mismatch when the pin faces sideways:
    the port's electrical connection is always at the stub end, and
    the glyph extends UP for power or DOWN for ground from there.

    ``pin_orientation`` is unused (retained for ABI compat).
    """
    del pin_orientation  # noqa: F841 - retained for ABI compat
    return 3 if is_ground else 1


def _label_justification(pin_orientation: int) -> int:
    """Justification for a net label at a pin's stub end.

    HARD RULE, set by the user: a net label on a LEFT-facing pin must
    read to the LEFT of the pin, never overlap it. The anchor stays on
    the stub (it is the electrical hotspot); justification decides which
    way the text grows. Left-facing pin (orientation 2) -> bottom-right
    justified (2): text ends at the anchor and grows away from the part.
    Every other orientation keeps bottom-left (0): text grows right,
    which is away from (or clear of) the part for right / up / down
    pins.
    """
    return 2 if pin_orientation % 4 == 2 else 0


def _apply_no_stranded_parts_rule(
    nets: "list[Net]",
    reps: dict[str, str],
    sheet_refdes: "set[str]",
) -> list[str]:
    """HARD RULE: no part may have every pin on net-labels only.

    A component whose every terminal is drawn as a floating net label --
    no wire, no power-port glyph -- reads as an unconnected orphan; no
    professional schematic does this, and it is the core of the
    "label soup" artefact. For any part whose incident on-sheet nets are
    ALL ``label_per_pin``, promote its cheapest incident net (fewest
    pins) to ``wire`` so at least one terminal gets drawn copper.
    ``port`` representations already count as a drawn connection.

    Mutates ``reps`` in place; returns the promoted net names (for
    caller notes). Deterministic: parts and candidate nets are visited
    in sorted order, so repeated runs promote the same nets.
    """
    nets_of_part: dict[str, list[Net]] = {}
    for net in nets:
        for pr in net.pins:
            if pr.refdes in sheet_refdes:
                nets_of_part.setdefault(pr.refdes, []).append(net)

    promoted: list[str] = []
    for refdes in sorted(nets_of_part):
        incident = nets_of_part[refdes]
        if not incident:
            continue
        if any(reps.get(n.name) != "label_per_pin" for n in incident):
            continue
        best = min(incident, key=lambda n: (len(n.pins), n.name))
        if reps.get(best.name) == "label_per_pin":
            reps[best.name] = "wire"
            promoted.append(best.name)
    return promoted


def _net_representation(
    net: Net,
    refdes_to_zone: dict[str, Optional[str]],
) -> str:
    """Pick the visual representation for a net per discipline rule 3.

    Three-tier priority:
      1. power or ground (the ``is_power``/``is_ground`` flag OR a
         recognised rail name -- see ``_is_power_net`` / ``_is_ground_net``)
         -> ``'port'``: power-port glyph at every pin (cluster-consolidated
         by the caller). Name detection keeps a conventionally-named rail
         (``GND``, ``VCC``, ``V3V3``) off the wire/label path even when the
         planner forgets the flag.
      2. ``force_label=True`` -> ``'label_per_pin'``: planner-driven
         override for an intra-block net that would tangle as a wire.
      3. All pins share one zone (functional block) -> ``'wire'``: route
         pins together with wires; no label.
      4. Otherwise (pins span multiple zones, or some pins are unzoned
         while others are in a zone) -> ``'label_per_pin'``: one net
         label at each pin, no inter-pin wires.

    A net whose pins are ALL unzoned (every component has ``zone=None``)
    falls through to ``'wire'`` because they share the implicit "no zone"
    group. This keeps current behaviour for plans that don't define
    zones yet: the executor still wires them together. Once the planner
    assigns zones, the rule kicks in.

    ``force_wires=True`` beats everything, including the rail-name
    heuristic, and is the planner's explicit way to demand a drawn wire.
    """
    if getattr(net, "force_wires", False):
        return "wire"
    if _is_power_net(net) or _is_ground_net(net):
        return "port"
    if net.force_label:
        return "label_per_pin"
    zones = {refdes_to_zone.get(p.refdes) for p in net.pins}
    if len(zones) == 1:
        return "wire"
    return "label_per_pin"
