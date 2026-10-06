# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Constructive PCB placement + board sizing engine (pure Python).

This module is a *constructor*: given a component set with real
footprint extents and the compiled netlist, it sizes the board, assigns
sides and orientations, and produces a legal first-pass placement. It is
designed for small hand-routed boards (roughly 10-80 parts), where a
connectivity-seeded greedy construction followed by a short, cooled,
fixed-seed local polish beats a long stochastic search.

Pipeline (see :func:`construct_placement`):

1. **size_board** -- estimate the board rectangle from inflated
   courtyard area and a target utilization, with an aspect ratio biased
   by connectivity / the presence of connectors.
2. **seed_constraints** -- pin connectors to an edge band, hold fixed
   parts, initialise the side map.
3. **constructive_seed** -- place movable parts in descending net-degree
   order at the net-weighted centroid of their already-placed
   neighbours; nudge off coincidence deterministically.
4. **optimize_rotations** -- a dedicated greedy 0/90/180/270 pass that
   minimises pin-level wirelength, run *before* the polish.
5. **legalize** -- a deterministic hard-shove pass removes residual
   same-side bounding-box overlaps; if the region is too small it grows
   and the construction re-runs (the sizing<->placement closure loop).
6. **sa_polish** -- a short, low-temperature Metropolis local search on
   the full weighted objective, kept deliberately brief so the result is
   reproducible and the constructive seed remains the real placer.

The full objective is

    C = w_hpwl*HPWL + w_via*VIA + w_cong*CONG + w_clear*CLR
        + w_edge*EDGE + w_decap*DECAP + w_conn*CONN + w_therm*THERM

where the clearance and edge weights are large enough to behave as
near-hard constraints. Every term is also reported un-weighted in the
:class:`ObjectiveReport` so a caller can inspect the trade-off rather
than trust a single scalar.

Determinism: a single ``opts.seed`` threads one :class:`numpy.random.Generator`
through every stochastic step (the constructive nudge, the polish move
selection, and the acceptance draw). Same seed + same inputs yields an
identical :class:`ConstructResult`. No bare ``random`` module is used.

This is a from-scratch constructor that owns board sizing and
side/rotation assignment. It shares the ``PlaceComp`` / ``PlaceNet`` /
``PlacePin`` / ``BoardRegion`` vocabulary and the
``new_origin = centroid - R(rot)*C0`` back-rotation contract with the
relaxation refiner in :mod:`eda_agent.placement`, so a construct pass can
hand off to a refine pass. Pure Python (numpy only); no GPU, no learned
models. The congestion term is a deliberate drop-in slot for a future
routability predictor and is documented as nearly content-free at this
part count.

NDA scope: this engine reads only the current project's board geometry
and netlist (see :mod:`eda_agent.design`). It carries no cross-project
state.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Optional

import numpy as np

from eda_agent.placement.autoplace import (
    BoardRegion,
    PlaceComp,
    PlaceNet,
    PlacePin,
    PlaceOptions,
    _hard_shove,
    _net_pairs_and_weights,
    _optimize_rotations,
    _rotate_offset,
    pin_hpwl,
)

__all__ = [
    "DesignRules",
    "ObjectiveWeights",
    "ConstructOptions",
    "ObjectiveReport",
    "Placement",
    "ConstructResult",
    "construct_placement",
    "construct_placement_best_of",
    "construct_placement_visual",
    "tighten_match_clusters",
    "size_board",
    "constructive_seed",
    "score",
    "net_spans",
    "decoupling_report",
    "ratsnest_crossings",
    "sa_polish",
]


# --------------------------------------------------------------------------- #
# Configuration dataclasses
# --------------------------------------------------------------------------- #

@dataclass
class DesignRules:
    """Physical constraints the sizer and objective need.

    A lightweight, engine-local view of the board's rules -- distinct
    from the richer rule models elsewhere in the project. Only the
    fields the placement constructor reads live here.

    ``utilization`` is the target fraction of board area occupied by
    inflated courtyards; lower means more breathing room. ``layers`` is
    1 or 2 (the via / side-flip machinery only does anything on 2).
    ``courtyard_clr`` is the per-side keep-out added around each part's
    bounding box. ``edge_clr`` is the minimum body-to-board-edge gap.
    ``grid`` is the placement snap grid in mils. ``component_clr`` is the
    extra copper-to-copper breathing room used during legalization and
    the overlap term.
    """

    layers: int = 2
    utilization: float = 0.0  # 0 => pick a default from ``layers``
    courtyard_clr: float = 10.0
    edge_clr: float = 40.0
    grid: float = 5.0
    component_clr: float = 15.0

    def effective_utilization(self) -> float:
        """Resolve the target utilization, defaulting by layer count."""
        if self.utilization > 0.0:
            return self.utilization
        return 0.55 if self.layers >= 2 else 0.45


@dataclass
class ObjectiveWeights:
    """Per-term weights for the full placement objective.

    The clearance and edge weights are intentionally large so those
    terms act as soft constraints (an illegal move is almost always
    rejected). The via and congestion weights are small because those
    terms carry little signal at this part count; they are retained for
    the higher-count / two-layer path.
    """

    hpwl: float = 1.0
    via: float = 0.5
    cong: float = 0.3
    clear: float = 50.0
    edge: float = 20.0
    decap: float = 2.0
    conn: float = 10.0
    therm: float = 0.2
    # Keep-apart (mixed-signal separation). The per-pair penalty is the
    # normalised shortfall below the desired separation (0..1), so this weight
    # is roughly "mils of wirelength a full overlap of two conflicting groups
    # is worth". Active only when parts carry differing keepout_group tags, so
    # it is identically zero on the common single-domain design.
    sep: float = 1500.0
    # Keep-together (matched-pair adjacency: SLP 'A' relationship / boids
    # cohesion / analog common-centroid matching). Pulls parts sharing a
    # match_group adjacent even when they share NO net (HPWL cannot). Squared
    # normalised distance, so it gathers from afar yet is gentle near
    # adjacency. Zero unless >=2 parts share a tag.
    match: float = 3000.0
    # Matched-pair orientation consensus (boids ALIGNMENT rule). Penalises
    # same-match_group parts sitting on DIFFERENT axes, compared MOD 180 -- a
    # 2-pin part at 90 vs 270 is the same axis (the natural mirror pair a
    # differential layout wants), only 0/180-vs-90/270 is a real mismatch. So
    # this nudges matched parts onto a common axis WITHOUT fighting the mirror
    # symmetry the pin-pointing rotation already produces. Per-mismatched-pair.
    match_axis: float = 600.0
    # Common-centroid matching (analog precision; Razavi cross-quad). Drives the
    # centroids of a match_group's match_role sub-devices to coincide, so a
    # linear process/thermal gradient cancels. Squared normalised distance like
    # match, sharing its scale; it breaks the AABB-vs-ABBA tie match cannot see.
    # Zero unless a group has >= 2 distinct match_role tags.
    match_centroid: float = 3000.0


@dataclass
class ConstructOptions:
    """Tunable parameters for the constructor.

    ``seed`` makes the whole run deterministic. The polish is bounded by
    ``max_moves`` (kept small on purpose). ``max_grow_steps`` caps the
    sizing<->placement closure loop. ``cong_bins`` is the side length of
    the congestion density grid.
    """

    seed: int = 0
    # Initial-placement strategy: "greedy" (connectivity-weighted centroid,
    # signal-flow aware) or "spectral" (graph-Laplacian eigenvector / vibration
    # normal modes, Hall 1970). Greedy is signal-flow native; spectral spreads
    # by GLOBAL connectivity structure and tends to win on scattered / mesh
    # netlists where greedy's local centroid order collapses parts together.
    # construct_placement_best_of explores both so each design keeps its winner.
    seed_mode: str = "greedy"
    # Polish schedule.
    max_moves: int = 0          # 0 => derive a short budget from N
    cooling: float = 0.95       # geometric temperature decay per epoch
    start_temp_fraction: float = 0.3   # begin already-cooled, for polish
    min_accept_rate: float = 0.01
    stale_epochs: int = 5
    probe_moves: int = 200
    # Construction.
    signal_flow_alpha: float = 0.5     # blend of flow-rank vs centroid x
    rotation_sweeps: int = 4
    max_shove_rounds: int = 80
    # Board-grow cap for the size<->placement closure loop. The loop stops as
    # soon as the placement is overlap-free, so an easy design exits early;
    # this cap only bounds the hard, tightly-packed cases (many parts plus
    # fixed edge connectors) that need several grows to find room. Set high
    # enough that those still legalize rather than returning a residual
    # overlap.
    max_grow_steps: int = 12
    # Objective grid.
    cong_bins: int = 8
    # Move-type sampling weights (translate, rotate, swap, flip, decap, match).
    move_translate: float = 0.45
    move_rotate: float = 0.20
    move_swap: float = 0.15
    move_flip: float = 0.10
    move_decap: float = 0.10
    # Match-snap: jump a keep-together member adjacent to its nearest group-mate.
    # A global move the small translate jump cannot make -- a member stranded
    # across the board (every intermediate position overlaps something) can
    # coalesce into its cluster in one step. Only active when match groups exist.
    move_match: float = 0.10


# --------------------------------------------------------------------------- #
# Result dataclasses
# --------------------------------------------------------------------------- #

@dataclass
class ObjectiveReport:
    """Every objective term, un-weighted, plus the weighted total.

    Exposing the terms separately lets a caller see which constraint is
    binding rather than trusting the scalar ``weighted_total``.
    ``legal`` is True when there is no same-side overlap and no part
    sits outside the board. ``utilization`` is the achieved courtyard
    area fraction.
    """

    hpwl: float = 0.0
    via: float = 0.0
    cong: float = 0.0
    clear: float = 0.0
    edge: float = 0.0
    decap: float = 0.0
    conn: float = 0.0
    therm: float = 0.0
    sep: float = 0.0
    match: float = 0.0
    match_axis: float = 0.0
    match_centroid: float = 0.0
    weighted_total: float = 0.0
    legal: bool = True
    utilization: float = 0.0

    def as_dict(self) -> dict[str, float]:
        """Flat dict of the un-weighted terms (for logging / display)."""
        return {
            "hpwl": self.hpwl,
            "via": self.via,
            "cong": self.cong,
            "clear": self.clear,
            "edge": self.edge,
            "decap": self.decap,
            "conn": self.conn,
            "therm": self.therm,
            "sep": self.sep,
            "match": self.match,
            "match_axis": self.match_axis,
            "match_centroid": self.match_centroid,
        }


@dataclass
class Placement:
    """Final pose of one component in the Altium frame.

    ``x``/``y`` are the back-rotated *origin* (not the centroid):
    ``origin = centroid - R(rotation)*C0`` where ``C0`` is the
    part's centroid-from-origin offset at rotation 0. ``side`` is +1
    (top) or -1 (bottom).
    """

    x: float
    y: float
    rotation: float
    side: int


@dataclass
class ConstructResult:
    """Top-level output of :func:`construct_placement`."""

    region: BoardRegion
    placements: dict[str, Placement]
    report: ObjectiveReport
    # Centroid-space positions (useful for handing off to the refiner).
    centroids: dict[str, tuple[float, float]] = field(default_factory=dict)
    rotations: dict[str, float] = field(default_factory=dict)
    sides: dict[str, int] = field(default_factory=dict)
    # Polish bookkeeping.
    accepted: int = 0
    rejected: int = 0
    iterations: int = 0
    grow_steps: int = 0
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Power-aware component view
# --------------------------------------------------------------------------- #

# These attributes are read off ``PlaceComp`` when present (the adapter
# in the tool layer can set them), but the engine degrades gracefully
# when they are absent so it stays usable with the plain dataclass.

def _role(comp: PlaceComp) -> str:
    """Best-effort role tag: 'ic' | 'decap' | 'connector' | 'mount' | ''."""
    return str(getattr(comp, "role", "") or "")


def _power_w(comp: PlaceComp) -> float:
    """Dissipation in watts (0 when unknown)."""
    try:
        return float(getattr(comp, "power_w", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _is_connector(comp: PlaceComp) -> bool:
    if bool(getattr(comp, "edge", False)):
        return True
    return _role(comp) == "connector"


def _keepout_group(comp: PlaceComp) -> str:
    """Mixed-signal keep-apart tag ('' when untagged).

    Parts carrying DIFFERENT non-empty tags are pushed apart by the
    separation term -- the planner sets this to segregate, e.g., a noisy
    switching / digital section from a sensitive analog / RF one. Read by
    ``getattr`` so a plain ``PlaceComp`` with no tag is simply single-domain.
    """
    return str(getattr(comp, "keepout_group", "") or "")


def _match_group(comp: PlaceComp) -> str:
    """Matched-pair tag ('' when untagged).

    Parts sharing the SAME non-empty tag are pulled adjacent by the match
    term -- the planner sets this for components that must sit together for
    matching even though they share no net (a differential pair's two input
    resistors, a current mirror, a common-centroid array).
    """
    return str(getattr(comp, "match_group", "") or "")


def _flippable(comp: PlaceComp) -> bool:
    return bool(getattr(comp, "flippable", False))


def _assigned_edge(comp: PlaceComp) -> str:
    """Edge band a connector is assigned to: 'L'|'R'|'T'|'B' or ''."""
    return str(getattr(comp, "edge_band", "") or "")


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #

def _eff_wh(comp: PlaceComp, rotation: float) -> tuple[float, float]:
    """Bounding-box (w, h) of ``comp`` at ``rotation`` (90/270 swaps w/h).

    Contract: ``comp.w`` / ``comp.h`` are the footprint bounding box AT
    ``comp.rotation`` (its incoming/base orientation), not at rotation 0.
    The swap is therefore relative to the base: a part already supplied at
    90 deg with its w/h measured at 90 deg has delta 0 here and is not
    swapped again. Callers that re-measure w/h to rotation 0 must also set
    ``comp.rotation = 0`` so the base matches, otherwise every clearance /
    edge / legality computation that uses these dims will be transposed.
    """
    delta = (rotation - comp.rotation) % 180
    if abs(delta - 90) < 1e-6:
        return comp.h, comp.w
    return comp.w, comp.h


def _snap(value: float, grid: float) -> float:
    if grid <= 0:
        return value
    return round(value / grid) * grid


def _clamp(value: float, lo: float, hi: float) -> float:
    if lo > hi:
        return (lo + hi) / 2.0
    return max(lo, min(hi, value))


def _rect_overlap_area(
    aw: float, ah: float, ax: float, ay: float,
    bw: float, bh: float, bx: float, by: float,
    clearance: float,
) -> float:
    """Overlap area of two clearance-inflated AABBs (0 if disjoint)."""
    ox = (aw + bw) / 2.0 + clearance - abs(ax - bx)
    oy = (ah + bh) / 2.0 + clearance - abs(ay - by)
    if ox <= 0.0 or oy <= 0.0:
        return 0.0
    return ox * oy


def _net_degree(nets: list[PlaceNet]) -> dict[str, int]:
    """ref -> number of distinct nets it touches (its connectivity)."""
    deg: dict[str, int] = {}
    for net in nets:
        for ref in dict.fromkeys(net.refs):
            deg[ref] = deg.get(ref, 0) + 1
    return deg


def _net_count_between(nets: list[PlaceNet]) -> dict[tuple[str, str], int]:
    """(a, b) -> count of nets connecting a and b (a < b), undirected."""
    counts: dict[tuple[str, str], int] = {}
    for net in nets:
        members = list(dict.fromkeys(net.refs))
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                key = (a, b) if a < b else (b, a)
                counts[key] = counts.get(key, 0) + 1
    return counts


def _pin_world(
    comp: PlaceComp,
    pos: tuple[float, float],
    rotation: float,
    side: int,
    pin: PlacePin,
) -> tuple[float, float]:
    """World coordinate of one pin.

    p = (x, y) + R(rotation) * (lx, ly); a bottom side (-1) mirrors the
    local x before rotation (the part is flipped about its y axis).
    """
    lx = pin.lx if side >= 0 else -pin.lx
    dx, dy = _rotate_offset(lx, pin.ly, rotation)
    return pos[0] + dx, pos[1] + dy


# --------------------------------------------------------------------------- #
# Decap <-> power-pin pairing
# --------------------------------------------------------------------------- #

def _rail_net_names(
    comps: list[PlaceComp],
    nets: list[PlaceNet],
) -> set[str]:
    """Power + ground rail net names, identified structurally (no name match).

    Ground is the single densest net (every part returns to it). A power rail
    is the non-ground leg of any two-pin cap whose other leg is ground and
    whose power leg feeds three or more parts -- the same structural decoupling
    signature :func:`_pair_decaps_to_power_pins` uses. These nets route as
    copper planes / pours, not point-to-point traces, so a SIGNAL-routability
    metric treats them as planes rather than signals regardless of their
    fanout. The fanout proxy alone misses a rail in a tiny netlist (a 3- or
    4-part rail looks like a signal); this catches it by role.
    """
    if not comps or not nets:
        return set()
    members = {n.name: list(dict.fromkeys(n.refs)) for n in nets}
    ground = max(members, key=lambda nm: len(members[nm]), default="")
    if not ground:
        return set()
    rails = {ground}
    pin_count = {c.ref: len(c.pins) for c in comps}
    for c in comps:
        if pin_count.get(c.ref, 0) != 2:
            continue
        legs = list(dict.fromkeys(p.net for p in c.pins if p.net))
        if len(legs) != 2 or ground not in legs:
            continue
        power = legs[0] if legs[1] == ground else legs[1]
        if len(members.get(power, [])) >= 3:
            rails.add(power)
    return rails


def _infer_switch_node_groups(
    comps: list[PlaceComp],
    nets: list[PlaceNet],
) -> dict[str, str]:
    """Cluster a switching regulator's switch-node parts for keep-together.

    The switch node of a buck/boost (the controller SW pin -> inductor ->
    catch diode / sync FET, plus the bootstrap cap) carries the high di/dt
    current and is THE dominant EMI / efficiency layout rule: that loop must be
    physically small. HPWL alone does not compactify it -- the inductor is also
    pulled toward its output net -- so the loop stays spread.

    Structural signature (naming-agnostic, no power/ground flags needed on the
    PCB net model): an inductor (two-pin part, refdes kind ``L``) has two nets;
    the switch node is the one that reaches the CONTROLLER -- a part with >= 4
    pins -- and stays LOCAL (fewest members, distinguishing it from the output
    rail, which may also power a chip). The non-controller parts on that net
    (the inductor and its loop-mates: diode, bootstrap cap) get a shared
    ``match_group`` so keep-together pulls them tight while HPWL on the switch
    net seats the cluster at the controller's SW pin. Needs >= 2 such parts
    (an inductor plus at least one loop-mate), else there is nothing to
    cluster. Returns ``{refdes: group_name}``.
    """
    if not comps or not nets:
        return {}
    from eda_agent.design.motifs import _kind_from_refdes

    members = {n.name: set(dict.fromkeys(n.refs)) for n in nets}
    pins_of = {c.ref: len(c.pins) for c in comps}
    nets_of: dict[str, list[str]] = {}
    for n in nets:
        for r in set(n.refs):
            nets_of.setdefault(r, []).append(n.name)

    out: dict[str, str] = {}
    idx = 0
    for c in comps:
        if _kind_from_refdes(c.ref) != "L" or pins_of.get(c.ref, 0) != 2:
            continue
        # Candidate switch nets: this inductor's nets that reach a controller
        # (a >= 4-pin IC). Prefer the most LOCAL one (fewest members) so the
        # output rail (which may also reach a load IC) is not chosen.
        candidates = []
        for net_name in nets_of.get(c.ref, []):
            net_parts = members.get(net_name, set())
            if any(pins_of.get(r, 0) >= 4 for r in net_parts):
                candidates.append((len(net_parts), net_name))
        if not candidates:
            continue
        candidates.sort()
        sw_net = candidates[0][1]
        loop_parts = [r for r in members[sw_net] if pins_of.get(r, 0) < 4]
        if len(loop_parts) < 2:
            continue
        grp = f"_sw{idx}"
        idx += 1
        for r in loop_parts:
            out[r] = grp
    return out


def _infer_crystal_groups(
    comps: list[PlaceComp],
    nets: list[PlaceNet],
) -> dict[str, str]:
    """Group each crystal/resonator with its two load caps for keep-together.

    A crystal oscillator must be laid out as a TIGHT cluster -- the crystal and
    its two load capacitors right at the MCU's XIN/XOUT pins with short
    symmetric traces -- because long crystal traces pick up noise and
    destabilise the oscillator. Without help the load caps snap to the IC's
    crystal pins (they read as decaps on the XIN/XOUT rails) but the crystal
    itself, a 2-pin part on two signal nets, floats away.

    Structural signature (naming-agnostic): a TWO-pin part on exactly two
    NON-ground nets, where each of those nets also carries a two-pin cap to
    ground (the load cap) AND both nets reach a common multi-pin IC. The
    crystal and its two load caps get a shared ``match_group`` so the
    keep-together term pulls the crystal in to its caps at the IC. The
    common-IC test distinguishes a real resonator from an arbitrary 2-pin part
    bridging two filtered nodes. Returns ``{refdes: group_name}``.
    """
    if not comps or not nets:
        return {}
    members = {n.name: set(dict.fromkeys(n.refs)) for n in nets}
    ground = max(members, key=lambda nm: len(members[nm]), default="")
    if not ground:
        return {}
    pins_of = {c.ref: len(c.pins) for c in comps}
    nets_of: dict[str, list[str]] = {}
    for name, refs in members.items():
        for r in refs:
            nets_of.setdefault(r, []).append(name)

    from eda_agent.design.motifs import _kind_from_refdes

    def _load_cap(net_name: str) -> str | None:
        for c in comps:
            # Must be a capacitor: otherwise a feedback divider (a resistor from
            # FB to ground) reads as a load cap and the divider's top resistor
            # is mis-clustered as a crystal.
            if pins_of.get(c.ref, 0) != 2 or _kind_from_refdes(c.ref) != "C":
                continue
            if set(nets_of.get(c.ref, [])) == {net_name, ground}:
                return c.ref
        return None

    out: dict[str, str] = {}
    idx = 0
    for c in comps:
        if pins_of.get(c.ref, 0) != 2:
            continue
        cn = nets_of.get(c.ref, [])
        non_gnd = [n for n in cn if n != ground]
        if len(cn) != 2 or len(non_gnd) != 2:
            continue
        a, b = non_gnd
        ca, cb = _load_cap(a), _load_cap(b)
        if not ca or not cb or ca == cb:
            continue
        ic_a = {r for r in members[a] if pins_of.get(r, 0) >= 3}
        ic_b = {r for r in members[b] if pins_of.get(r, 0) >= 3}
        if not (ic_a & ic_b):
            continue
        grp = f"_xtal{idx}"
        idx += 1
        out[c.ref] = out[ca] = out[cb] = grp
    return out


def _pair_decaps_to_power_pins(
    comps: list[PlaceComp],
    nets: list[PlaceNet],
) -> dict[str, tuple[str, float, float]]:
    """Pair each decoupling capacitor to the IC power pin it serves.

    Naming-agnostic: a decap is found on the connectivity graph, never
    by matching a library reference or designator string. A decap is any
    two-pin passive whose two nets are one power net and one ground net,
    where that power net is a RAIL (feeds three or more parts -- the IC,
    this cap, and at least a power source / sibling). The rail test rejects
    a 2-pin bypass cap from a low-fanout signal to ground (a filter, not a
    decoupler). A rail commonly feeds SEVERAL ICs, and a single IC several
    power pins, so the result maps
    ``decap_ref -> [(ic_ref, power_pin_lx, power_pin_ly), ...]`` -- one
    entry per IC power pin the rail reaches. The DECAP objective term and
    the decap-snap polish move pair each cap to whichever candidate power
    pin it lands NEAREST (a shared-rail decap serves the closest power pin).

    Power and ground nets are inferred structurally: a ground net is the
    single highest-degree net (the densest rail), and any net touching a
    decap that is not that ground net is treated as a power net. ICs are
    parts with three or more pins (or tagged ``role == 'ic'``). The
    densest-net-is-ground assumption holds for realistic netlists (every
    part returns to ground); it can mislabel a contrived design where a
    power net touches strictly more parts than ground.
    """
    if not comps or not nets:
        return {}

    by_ref = {c.ref: c for c in comps}

    # Net membership and degree.
    members: dict[str, list[str]] = {}
    for net in nets:
        members[net.name] = list(dict.fromkeys(net.refs))

    # Ground net heuristic: the densest net. Fall back to none.
    ground_net = ""
    best_deg = -1
    for name, mem in members.items():
        if len(mem) > best_deg:
            best_deg = len(mem)
            ground_net = name

    # net -> the ICs on it. A decoupling cap bypasses an active device's
    # supply pin, never a connector: a multi-pin header/USB/edge connector
    # carries power THROUGH the board, it does not need local decoupling. So
    # a part tagged role=='ic' always counts, a connector never does (even
    # with many pins), and otherwise the >=3-pin heuristic applies. Without
    # the connector exclusion a 4-pin power connector looks like an IC, and
    # every VCC cap could "satisfy" its decap term by parking near the
    # connector instead of the chip -- scattering the decoupling.
    def _is_ic(ref: str) -> bool:
        c = by_ref.get(ref)
        if c is None:
            return False
        if _role(c) == "ic":
            return True
        if _is_connector(c):
            return False
        return len(c.pins) >= 3

    nets_by_name = {n.name: n for n in nets}

    # nets a ref participates in.
    ref_nets: dict[str, list[str]] = {}
    for net in nets:
        for ref in dict.fromkeys(net.refs):
            ref_nets.setdefault(ref, []).append(net.name)

    result: dict[str, list[tuple[str, float, float]]] = {}
    for comp in comps:
        if len(comp.pins) != 2:
            continue
        if _is_ic(comp.ref):
            continue
        net_names = [p.net for p in comp.pins if p.net]
        distinct = list(dict.fromkeys(net_names))
        if len(distinct) != 2:
            continue
        # One leg must be ground, the other a power net.
        if ground_net not in distinct:
            continue
        power_candidates = [nm for nm in distinct if nm != ground_net]
        if len(power_candidates) != 1:
            continue
        power_net = power_candidates[0]

        # The power leg must be a real rail, not a low-fanout signal a 2-pin
        # filter cap happens to tie to ground. A rail feeds at least the IC,
        # this cap, and one more part (a power source / sibling decap), so its
        # degree is >= 3. This rejects signal bypass caps (e.g. an ADC input
        # to ground) that are NOT decoupling the IC's supply.
        if len(members.get(power_net, [])) < 3:
            continue

        # The power rail may feed SEVERAL ICs (the common case: one VCC for
        # the whole board). Record the rail's power pin on EVERY IC it
        # reaches; the consumer pairs the decap to whichever it lands nearest.
        candidates: list[tuple[str, float, float]] = []
        for ic_ref in members.get(power_net, []):
            if not _is_ic(ic_ref):
                continue
            ic = by_ref.get(ic_ref)
            if ic is None:
                continue
            # Every power pin of the IC on this rail is a candidate, not just
            # the first: a big MCU/FPGA has several VCC pins and each wants its
            # own decap, so a cap pairs to (and is scored against) the nearest
            # power PIN, not merely the nearest IC.
            for p in ic.pins:
                if p.net == power_net:
                    candidates.append((ic_ref, p.lx, p.ly))
        if candidates:
            result[comp.ref] = candidates

    return result


# --------------------------------------------------------------------------- #
# Side assignment
# --------------------------------------------------------------------------- #

def _assign_sides(
    comps: list[PlaceComp],
    rules: DesignRules,
) -> dict[str, int]:
    """Initial side map: every part starts on top (+1).

    Bottom (-1) placement is only ever enabled later (by the flip-side
    polish move) when ``rules.layers == 2`` and the part is flippable.
    On a single-layer board every side is +1 and the via term is
    identically zero -- it is still computed, it just never contributes.
    """
    return {c.ref: 1 for c in comps}


# --------------------------------------------------------------------------- #
# Board sizing
# --------------------------------------------------------------------------- #

def _quick_hull_aspect(
    comps: list[PlaceComp],
    nets: list[PlaceNet],
) -> float:
    """Aspect ratio (w/h) of a fast connectivity seed's bounding box.

    Runs a couple of cheap relaxation sweeps from a circular seed so the
    sizer can bias the board rectangle toward the natural shape of the
    netlist. Returns 1.0 when there is nothing to spread.
    """
    movable = [c for c in comps if not c.fixed]
    if len(movable) < 2:
        return 1.0

    present = {c.ref for c in comps}
    pairs = _net_pairs_and_weights(nets, present)
    if not pairs:
        return 1.0

    # Seed on a ring so the relaxation has somewhere to pull from.
    pos: dict[str, list[float]] = {}
    n = len(comps)
    for i, c in enumerate(sorted(comps, key=lambda c: c.ref)):
        angle = 2.0 * math.pi * i / max(1, n)
        pos[c.ref] = [100.0 * math.cos(angle), 100.0 * math.sin(angle)]

    for _ in range(40):
        force = {r: [0.0, 0.0] for r in pos}
        for a, b, w in pairs:
            dx = pos[b][0] - pos[a][0]
            dy = pos[b][1] - pos[a][1]
            force[a][0] += 0.1 * w * dx
            force[a][1] += 0.1 * w * dy
            force[b][0] -= 0.1 * w * dx
            force[b][1] -= 0.1 * w * dy
        for c in movable:
            pos[c.ref][0] += force[c.ref][0]
            pos[c.ref][1] += force[c.ref][1]

    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    if h < 1e-6 or w < 1e-6:
        return 1.0
    return w / h


def size_board(
    comps: list[PlaceComp],
    nets: list[PlaceNet],
    rules: DesignRules,
) -> BoardRegion:
    """Estimate the board rectangle from area + utilization + aspect.

    Total inflated courtyard area is the sum over parts of
    ``(w + 2*courtyard_clr) * (h + 2*courtyard_clr)``. The board area is
    that divided by the target utilization. The aspect ratio comes from
    a quick connectivity seed, or 1.5 when any connector is present
    (boards with edge I/O tend to be wider than tall), else 1.0. Width
    and height are grid-snapped and each is floored to at least the
    largest single-part dimension plus two edge clearances, so even a
    one-big-part board has room.
    """
    grid = max(1.0, rules.grid)
    cc = rules.courtyard_clr
    edge = rules.edge_clr

    if not comps:
        # Smallest sensible empty board.
        w = math.ceil((2 * edge) / grid) * grid
        return BoardRegion(0.0, 0.0, w, w)

    a_total = 0.0
    max_dim = 0.0
    total_pins = 0
    for c in comps:
        a_total += (c.w + 2 * cc) * (c.h + 2 * cc)
        max_dim = max(max_dim, c.w, c.h)
        total_pins += len(getattr(c, "pins", ()) or ())

    # Wiring-density derate: a board with many pins per part needs routing
    # channels that pure courtyard area ignores, so the effective target
    # utilization drops (more board) as the average pin count rises above a
    # sparse baseline. Bounded so it never balloons the board.
    avg_pins = total_pins / len(comps)
    density_derate = _clamp(1.0 - 0.04 * max(0.0, avg_pins - 3.0), 0.6, 1.0)
    u = rules.effective_utilization() * density_derate
    a_board = a_total / max(1e-6, u)

    has_connector = any(_is_connector(c) for c in comps)
    rho = _quick_hull_aspect(comps, nets)
    if rho <= 0.0 or not math.isfinite(rho):
        rho = 1.5 if has_connector else 1.0
    # Keep the rectangle from going pathologically thin: a quick
    # connectivity seed can splay a sparse netlist into a sliver, which
    # would not actually fit the parts side by side.
    rho = _clamp(rho, 0.4, 2.5)

    w = math.ceil(math.sqrt(a_board * rho) / grid) * grid
    w = max(w, math.ceil((max_dim + 2 * edge) / grid) * grid)
    h = math.ceil((a_board / max(1.0, w)) / grid) * grid
    h = max(h, math.ceil((max_dim + 2 * edge) / grid) * grid)

    return BoardRegion(0.0, 0.0, float(w), float(h))


def _tighten_region(
    comps: list[PlaceComp],
    pos: dict[str, list[float]],
    rotations: dict[str, float],
    region: BoardRegion,
    rules: DesignRules,
) -> BoardRegion:
    """Fit the board outline to the achieved placement plus edge clearance.

    ``size_board`` estimates the rectangle BEFORE placement from area and a
    rough aspect, and the legalizer only ever grows it -- so a design whose
    parts end up in a tighter footprint than estimated is left on an
    oversized board (wasted copper, low utilization). This shrinks the
    rectangle to the part courtyard bounding box expanded by one edge
    clearance on each side. It NEVER enlarges (clamped to the incoming
    region), so a board the legalizer had to grow is left untouched. The
    parts do not move; only the rectangle changes, so legality is preserved
    (every courtyard stays one edge clearance inside the new boundary).
    """
    if not pos:
        return region
    grid = max(1.0, rules.grid)
    edge = rules.edge_clr
    xs1: list[float] = []
    ys1: list[float] = []
    xs2: list[float] = []
    ys2: list[float] = []
    for c in comps:
        if c.ref not in pos:
            continue
        w, h = _eff_wh(c, rotations.get(c.ref, c.rotation))
        cx, cy = pos[c.ref]
        xs1.append(cx - w / 2.0)
        ys1.append(cy - h / 2.0)
        xs2.append(cx + w / 2.0)
        ys2.append(cy + h / 2.0)
    if not xs1:
        return region

    nx1 = math.floor((min(xs1) - edge) / grid) * grid
    ny1 = math.floor((min(ys1) - edge) / grid) * grid
    nx2 = math.ceil((max(xs2) + edge) / grid) * grid
    ny2 = math.ceil((max(ys2) + edge) / grid) * grid

    # Only ever shrink: never push a boundary outside the incoming region.
    rx_lo, rx_hi = min(region.x1, region.x2), max(region.x1, region.x2)
    ry_lo, ry_hi = min(region.y1, region.y2), max(region.y1, region.y2)
    nx1 = max(nx1, rx_lo)
    ny1 = max(ny1, ry_lo)
    nx2 = min(nx2, rx_hi)
    ny2 = min(ny2, ry_hi)
    return BoardRegion(float(nx1), float(ny1), float(nx2), float(ny2))


# --------------------------------------------------------------------------- #
# Constructive seed
# --------------------------------------------------------------------------- #

def _golden_nudge(index: int) -> tuple[float, float]:
    """Deterministic spiral offset to break coincidence (sub-mil up)."""
    angle = index * 2.399963  # golden angle in radians
    radius = 0.5 + 0.25 * index
    return radius * math.cos(angle), radius * math.sin(angle)


def _flow_rank(
    comps: list[PlaceComp],
    nets: list[PlaceNet],
    fixed_pos: dict[str, tuple[float, float]],
) -> dict[str, float]:
    """Normalised longest-path rank from connector/fixed parts (0..1).

    A breadth-relaxed longest-path over the connectivity graph, rooted
    at connectors and fixed parts. Used to bias the seed x so inputs
    settle on one side and outputs on the other. Returns 0.5 for every
    part when there are no roots.
    """
    refs = [c.ref for c in comps]
    by_ref = {c.ref: c for c in comps}
    adj: dict[str, set[str]] = {r: set() for r in refs}
    for net in nets:
        members = [r for r in dict.fromkeys(net.refs) if r in by_ref]
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                adj[members[i]].add(members[j])
                adj[members[j]].add(members[i])

    roots = [
        c.ref for c in comps
        if _is_connector(c) or c.ref in fixed_pos or c.fixed
    ]
    if not roots:
        return {r: 0.5 for r in refs}

    dist = {r: math.inf for r in refs}
    for r in roots:
        dist[r] = 0.0
    # Bellman-Ford-style relaxation (graph is small).
    for _ in range(len(refs)):
        changed = False
        for u in refs:
            if dist[u] is math.inf:
                continue
            for v in adj[u]:
                if dist[u] + 1 < dist[v]:
                    dist[v] = dist[u] + 1
                    changed = True
        if not changed:
            break

    finite = [d for d in dist.values() if d is not math.inf]
    max_d = max(finite) if finite else 1.0
    if max_d <= 0:
        max_d = 1.0
    rank: dict[str, float] = {}
    for r in refs:
        d = dist[r]
        rank[r] = (d / max_d) if d is not math.inf else 1.0
    return rank


def constructive_seed(
    comps: list[PlaceComp],
    nets: list[PlaceNet],
    region: BoardRegion,
    rules: DesignRules,
    rng: np.random.Generator,
    fixed_pos: dict[str, tuple[float, float]],
) -> dict[str, list[float]]:
    """Greedy connectivity-seeded placement (stage one).

    Connectors and fixed parts are placed first at their assigned
    location. The remaining movable parts are placed in descending
    net-degree order (ICs and other hubs first) -- each at the
    net-weighted centroid of its already-placed neighbours,

        pos_i = sum_j (net_count_ij * pos_j) / sum_j net_count_ij,

    grid-snapped, then nudged off any coincidence with a deterministic
    golden-angle spiral. A part with no placed neighbour yet is dropped
    near the board centre with the same nudge. An optional signal-flow
    bias blends the seed x with a longest-path rank so I/O lands on the
    correct side.

    This is connectivity-native: it gives the polish a good basin
    without the connectivity-blind whitespace bias of an area-packing
    seed. The supplied ``rng`` is not consumed here (the construction is
    fully deterministic); it is accepted so the signature matches the
    rest of the stochastic pipeline.
    """
    rx_lo = min(region.x1, region.x2)
    rx_hi = max(region.x1, region.x2)
    ry_lo = min(region.y1, region.y2)
    ry_hi = max(region.y1, region.y2)
    cx_mid = (rx_lo + rx_hi) / 2.0
    cy_mid = (ry_lo + ry_hi) / 2.0
    grid = rules.grid

    counts = _net_count_between(nets)
    # Per-ref adjacency with weights for quick centroid evaluation.
    adj: dict[str, list[tuple[str, int]]] = {c.ref: [] for c in comps}
    for (a, b), n in counts.items():
        if a in adj:
            adj[a].append((b, n))
        if b in adj:
            adj[b].append((a, n))

    pos: dict[str, list[float]] = {}
    placed: set[str] = set()

    # 1. Fixed / connector parts first.
    for c in comps:
        if c.ref in fixed_pos:
            fx, fy = fixed_pos[c.ref]
            pos[c.ref] = [fx, fy]
            placed.add(c.ref)
        elif c.fixed:
            pos[c.ref] = [c.cx, c.cy]
            placed.add(c.ref)

    # 2. Movable parts by descending net degree, then by ref for ties.
    deg = _net_degree(nets)
    movable = [c for c in comps if c.ref not in placed]
    movable.sort(key=lambda c: (-deg.get(c.ref, 0), c.ref))

    rank = _flow_rank(comps, nets, fixed_pos)
    has_roots = any(
        _is_connector(c) or c.ref in fixed_pos or c.fixed for c in comps
    )
    alpha = _SEED_FLOW_ALPHA if has_roots else 0.0

    nudge_index = 0
    for c in movable:
        num_x = 0.0
        num_y = 0.0
        den = 0.0
        for nb, w in adj[c.ref]:
            if nb in placed:
                num_x += w * pos[nb][0]
                num_y += w * pos[nb][1]
                den += w
        if den > 0.0:
            sx = num_x / den
            sy = num_y / den
        else:
            sx = cx_mid
            sy = cy_mid

        # Signal-flow bias on x: blend the centroid with the flow rank.
        rank_x = rx_lo + rank.get(c.ref, 0.5) * (rx_hi - rx_lo)
        a = alpha
        sx = a * rank_x + (1.0 - a) * sx

        sx = _snap(sx, grid)
        sy = _snap(sy, grid)
        hw, hh = c.w / 2.0, c.h / 2.0
        sx = _clamp(sx, rx_lo + hw, rx_hi - hw)
        sy = _clamp(sy, ry_lo + hh, ry_hi - hh)

        # Nudge off coincidence with anything already placed.
        if any(abs(pos[p][0] - sx) < 1e-6 and abs(pos[p][1] - sy) < 1e-6
               for p in placed):
            dx, dy = _golden_nudge(nudge_index)
            nudge_index += 1
            sx = _clamp(sx + dx, rx_lo + hw, rx_hi - hw)
            sy = _clamp(sy + dy, ry_lo + hh, ry_hi - hh)

        pos[c.ref] = [sx, sy]
        placed.add(c.ref)

    return pos


# Seed-time signal-flow blend: how much the seed x leans on the
# flow-rank versus the connectivity centroid. Kept small so the
# centroid leads and I/O is only nudged toward its side -- a larger
# value collapses parts of the same rank onto one column, which the
# bounded legalizer then struggles to spread.
_SEED_FLOW_ALPHA = 0.15


def spectral_seed(
    comps: list[PlaceComp],
    nets: list[PlaceNet],
    region: BoardRegion,
    rules: DesignRules,
    rng: np.random.Generator,
    fixed_pos: dict[str, tuple[float, float]],
) -> dict[str, list[float]]:
    """Spectral (graph-Laplacian eigenvector) placement seed.

    Cross-disciplinary borrow: a netlist is a network of springs, and the
    minimum-energy way to spread it on the plane is given by the low
    eigenvectors of the graph Laplacian -- the SAME mathematics as the
    resonant vibration modes of a membrane (mechanical engineering), the
    slow diffusion modes of a network, and spectral clustering (ML). Hall's
    1970 quadratic-placement theorem: the coordinate vector minimising the
    weighted sum of squared edge lengths subject to a spread constraint is
    the Fiedler-style generalised eigenvector ``L v = lambda D v`` with the
    smallest non-trivial eigenvalue; the next one gives the orthogonal axis.

    Unlike the greedy centroid seed this captures GLOBAL structure in closed
    form (no ordering, no local collapse), so it tends to win on scattered or
    mesh-like netlists. ``construct_placement_best_of`` runs both seeds and
    keeps the lower-objective result, so this never regresses a design where
    the greedy / signal-flow seed is better. Fixed parts (edge connectors)
    overwrite their spectral coordinate, exactly as the greedy seed does; the
    downstream shove then spreads everything around them.

    Falls back to the greedy seed for trivially small graphs (< 3 parts),
    where the eigenproblem is degenerate.
    """
    refs = [c.ref for c in comps]
    if len(refs) < 3:
        return constructive_seed(comps, nets, region, rules, rng, fixed_pos)

    idx = {r: i for i, r in enumerate(refs)}
    n = len(refs)
    adj = np.zeros((n, n))
    for r1, r2, w in _net_pairs_and_weights(nets, set(refs)):
        if r1 in idx and r2 in idx:
            adj[idx[r1], idx[r2]] += w
            adj[idx[r2], idx[r1]] += w
    deg = adj.sum(axis=1)
    # A part with no spring (isolated) gets a tiny self-weight so the
    # normalised Laplacian stays finite; it lands near the centre and the
    # shove disperses it.
    deg = np.where(deg > 0.0, deg, 1e-6)
    inv_sqrt = np.diag(1.0 / np.sqrt(deg))
    norm_lap = inv_sqrt @ (np.diag(deg) - adj) @ inv_sqrt
    # Symmetric -> real eigenvalues; eigh returns them ascending.
    eigvals, eigvecs = np.linalg.eigh(norm_lap)
    gen_vecs = inv_sqrt @ eigvecs           # back to L v = lambda D v vectors
    order = np.argsort(eigvals)
    # order[0] is the trivial constant mode (lambda ~ 0); take the next two.
    vx = gen_vecs[:, order[1]]
    vy = gen_vecs[:, order[2]]

    rx_lo, rx_hi = min(region.x1, region.x2), max(region.x1, region.x2)
    ry_lo, ry_hi = min(region.y1, region.y2), max(region.y1, region.y2)

    def _fit(v: np.ndarray, lo: float, hi: float) -> np.ndarray:
        v = v - v.min()
        span = v.max() - v.min()
        if span < 1e-9:
            return np.full_like(v, (lo + hi) / 2.0)
        margin = 0.12 * (hi - lo)
        return lo + margin + v / span * ((hi - lo) - 2.0 * margin)

    xs = _fit(vx, rx_lo, rx_hi)
    ys = _fit(vy, ry_lo, ry_hi)
    pos = {r: [float(xs[i]), float(ys[i])] for i, r in enumerate(refs)}
    for r, (fx, fy) in fixed_pos.items():
        if r in pos:
            pos[r] = [float(fx), float(fy)]
    return pos


# --------------------------------------------------------------------------- #
# Objective
# --------------------------------------------------------------------------- #

def _net_world_points(
    comps: list[PlaceComp],
    pos: dict[str, list[float]],
    rotations: dict[str, float],
    sides: dict[str, int],
    nets: list[PlaceNet],
) -> dict[str, list[tuple[float, float]]]:
    """World pin coordinates per net (centroid fallback for pinless)."""
    by_ref = {c.ref: c for c in comps}
    pin_index: dict[tuple[str, str], list[PlacePin]] = {}
    for c in comps:
        for p in c.pins:
            pin_index.setdefault((c.ref, p.net), []).append(p)

    out: dict[str, list[tuple[float, float]]] = {}
    for net in nets:
        pts: list[tuple[float, float]] = []
        for ref in dict.fromkeys(net.refs):
            if ref not in pos:
                continue
            c = by_ref.get(ref)
            p = (pos[ref][0], pos[ref][1])
            pins = pin_index.get((ref, net.name)) if c else None
            if pins and c:
                rot = rotations.get(ref, c.rotation)
                side = sides.get(ref, 1)
                for pin in pins:
                    pts.append(_pin_world(c, p, rot, side, pin))
            else:
                pts.append(p)
        if len(pts) >= 2:
            out[net.name] = pts
    return out


def _hpwl_term(
    world: dict[str, list[tuple[float, float]]],
    nets: list[PlaceNet],
) -> float:
    """Degree-normalised half-perimeter wirelength.

    Each net contributes ``q_e * ((max_x - min_x) + (max_y - min_y))``
    with ``q_e = weight / max(1, |P_e| - 1)`` so a many-pin power rail
    does not swamp the signal nets.
    """
    weight = {n.name: n.weight for n in nets}
    total = 0.0
    for name, pts in world.items():
        if len(pts) < 2:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        q = weight.get(name, 1.0) / max(1, len(pts) - 1)
        total += q * ((max(xs) - min(xs)) + (max(ys) - min(ys)))
    return total


def net_spans(
    comps: list[PlaceComp],
    pos: dict[str, list[float]],
    rotations: dict[str, float],
    sides: dict[str, int],
    nets: list[PlaceNet],
) -> dict[str, float]:
    """Raw half-perimeter bounding span (mils) of each net in this layout.

    Unlike the objective's ``_hpwl_term`` this is NOT degree-normalised
    and NOT weighted: it is the physical bounding-box span a router must
    cover for the net. A caller uses it as a placement diagnostic -- the
    longest nets are the routing risk, and the obvious candidates to mark
    critical and re-place. Single-pin (and unplaced) nets are omitted.
    """
    world = _net_world_points(comps, pos, rotations, sides, nets)
    out: dict[str, float] = {}
    for name, pts in world.items():
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        out[name] = (max(xs) - min(xs)) + (max(ys) - min(ys))
    return out


def _mst_edges(
    pts: list[tuple[float, float]],
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Euclidean minimum-spanning-tree edges over the points (Prim's).

    The MST is the canonical ratsnest topology -- the shortest set of
    connections that ties every pin of a net together, which is what a
    router approximates. O(n^2), fine for a net's pin count.
    """
    n = len(pts)
    if n < 2:
        return []
    in_tree = [False] * n
    best_d = [math.inf] * n
    best_src = [0] * n
    in_tree[0] = True   # seed the tree with point 0
    for j in range(1, n):
        dx = pts[j][0] - pts[0][0]
        dy = pts[j][1] - pts[0][1]
        best_d[j] = dx * dx + dy * dy
    edges: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for _ in range(n - 1):
        u = -1
        ud = math.inf
        for j in range(n):
            if not in_tree[j] and best_d[j] < ud:
                ud = best_d[j]
                u = j
        if u < 0:
            break
        in_tree[u] = True
        edges.append((pts[best_src[u]], pts[u]))
        for j in range(n):
            if not in_tree[j]:
                dx = pts[j][0] - pts[u][0]
                dy = pts[j][1] - pts[u][1]
                d = dx * dx + dy * dy
                if d < best_d[j]:
                    best_d[j] = d
                    best_src[j] = u
    return edges


def _segments_cross(
    p1: tuple[float, float], q1: tuple[float, float],
    p2: tuple[float, float], q2: tuple[float, float],
) -> bool:
    """True iff segment p1q1 properly crosses p2q2 at an interior point.

    Shared endpoints and collinear touching do NOT count -- only a real
    interior crossing (the case a router must resolve with a layer change).
    """
    def _orient(a, b, c) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    # Any shared endpoint => not a proper crossing.
    if p1 == p2 or p1 == q2 or q1 == p2 or q1 == q2:
        return False
    d1 = _orient(p2, q2, p1)
    d2 = _orient(p2, q2, q1)
    d3 = _orient(p1, q1, p2)
    d4 = _orient(p1, q1, q2)
    # Strict straddle on both sides => proper crossing.
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) \
        and d1 != 0 and d2 != 0 and d3 != 0 and d4 != 0


def ratsnest_crossings(
    comps: list[PlaceComp],
    pos: dict[str, list[float]],
    rotations: dict[str, float],
    sides: dict[str, int],
    nets: list[PlaceNet],
    max_fanout: int = 0,
    exclude_nets: frozenset = frozenset(),
) -> int:
    """Count interior crossings between DIFFERENT nets' ratsnest (MST) edges.

    A placement diagnostic, not an objective: the straight-line MST of each
    net is the router's first approximation, and two different-net edges
    that cross must eventually be separated by a layer change (a via). The
    count is therefore a routability / via-pressure indicator -- lower is
    more routable. Same-net edges never count (they connect). O(E^2) over
    the MST edges, fine for board-scale nets.

    ``max_fanout`` (when > 0) skips nets touching more than that many parts.
    High-fanout power/ground rails route as copper planes/pours, not
    point-to-point traces, so their MST crossings do NOT become signal vias;
    excluding them gives the SIGNAL via-pressure (the number that matters).
    ``exclude_nets`` skips named nets outright -- pass :func:`_rail_net_names`
    to drop rails by ROLE, which catches a low-fanout rail in a tiny netlist
    that the ``max_fanout`` proxy alone would still count as a signal.
    """
    world = _net_world_points(comps, pos, rotations, sides, nets)
    fanout = {n.name: len(set(n.refs)) for n in nets}
    segs: list[tuple[str, tuple[float, float], tuple[float, float]]] = []
    for name, pts in world.items():
        if name in exclude_nets:
            continue
        if max_fanout and fanout.get(name, 0) > max_fanout:
            continue
        uniq = list(dict.fromkeys(pts))
        for a, b in _mst_edges(uniq):
            segs.append((name, a, b))
    count = 0
    for i in range(len(segs)):
        n1, a1, b1 = segs[i]
        for j in range(i + 1, len(segs)):
            n2, a2, b2 = segs[j]
            if n1 == n2:
                continue
            if _segments_cross(a1, b1, a2, b2):
                count += 1
    return count


def _via_term(
    comps: list[PlaceComp],
    sides: dict[str, int],
    nets: list[PlaceNet],
) -> float:
    """Layer-change lower bound: per net min(top_pins, bottom_pins).

    Degree-normalised like HPWL. Identically zero whenever every part is
    on the same side (always true for a single-layer board).
    """
    by_ref = {c.ref: c for c in comps}
    weight = {n.name: n.weight for n in nets}
    total = 0.0
    for net in nets:
        members = [r for r in dict.fromkeys(net.refs) if r in by_ref]
        if len(members) < 2:
            continue
        top = sum(1 for r in members if sides.get(r, 1) >= 0)
        bot = len(members) - top
        q = weight.get(net.name, 1.0) / max(1, len(members) - 1)
        total += q * min(top, bot)
    return total


def _clear_term(
    comps: list[PlaceComp],
    pos: dict[str, list[float]],
    rotations: dict[str, float],
    sides: dict[str, int],
    rules: DesignRules,
) -> float:
    """Sum of same-side courtyard overlap areas (noise floored to 0)."""
    cc = rules.courtyard_clr
    n = len(comps)
    # Precompute each part's effective half-extents, position and side ONCE
    # (O(n)) instead of recomputing _eff_wh in the O(n^2) inner loop. The
    # pair test below is inlined from _rect_overlap_area so a disjoint pair
    # (the common case on a spread board) is rejected without a call.
    xs: list[float] = []
    ys: list[float] = []
    hw: list[float] = []
    hh: list[float] = []
    sd: list[int] = []
    for c in comps:
        x, y = pos[c.ref]
        w, h = _eff_wh(c, rotations.get(c.ref, c.rotation))
        xs.append(x)
        ys.append(y)
        hw.append(w / 2.0)
        hh.append(h / 2.0)
        sd.append(sides.get(c.ref, 1))
    total = 0.0
    for i in range(n):
        ax = xs[i]
        ay = ys[i]
        ahw = hw[i]
        ahh = hh[i]
        asd = sd[i]
        for j in range(i + 1, n):
            if sd[j] != asd:
                continue
            ox = ahw + hw[j] + cc - abs(ax - xs[j])
            if ox <= 0.0:
                continue
            oy = ahh + hh[j] + cc - abs(ay - ys[j])
            if oy <= 0.0:
                continue
            area = ox * oy
            if area > 1e-3:
                total += area
    return total


def _edge_term(
    comps: list[PlaceComp],
    pos: dict[str, list[float]],
    rotations: dict[str, float],
    region: BoardRegion,
    rules: DesignRules,
) -> float:
    """Quadratic board-edge clearance penalty (bites only near edge).

    For each part, ``dx`` and ``dy`` are the courtyard gaps to the
    nearest left/right and top/bottom edges; the term adds
    ``max(0, edge_clr - dx)^2 + max(0, edge_clr - dy)^2``. A part whose
    body lies fully outside the board yields a large quadratic penalty.
    """
    rx_lo = min(region.x1, region.x2)
    rx_hi = max(region.x1, region.x2)
    ry_lo = min(region.y1, region.y2)
    ry_hi = max(region.y1, region.y2)
    edge = rules.edge_clr
    total = 0.0
    for c in comps:
        x, y = pos[c.ref]
        w, h = _eff_wh(c, rotations.get(c.ref, c.rotation))
        dx = min(x - w / 2.0 - rx_lo, rx_hi - (x + w / 2.0))
        dy = min(y - h / 2.0 - ry_lo, ry_hi - (y + h / 2.0))
        gx = max(0.0, edge - dx)
        gy = max(0.0, edge - dy)
        total += gx * gx + gy * gy
    return total


def _nearest_ic_power_pin(
    decap_ref: str,
    candidates: list[tuple[str, float, float]],
    by_ref: dict[str, PlaceComp],
    pos: dict[str, list[float]],
    rotations: dict[str, float],
    sides: dict[str, int],
) -> tuple[str, float, float] | None:
    """World position of the closest candidate IC power pin to the decap.

    A decap on a shared rail can serve any of several ICs; it is pulled to
    (and scored against) the nearest one in the current placement. Returns
    ``(ic_ref, px_world, py_world)`` or ``None`` if no candidate is placed.
    """
    if decap_ref not in pos:
        return None
    dx0, dy0 = pos[decap_ref]
    best: tuple[str, float, float] | None = None
    best_d = None
    for ic_ref, plx, ply in candidates:
        if ic_ref not in pos:
            continue
        ic = by_ref.get(ic_ref)
        if ic is None:
            continue
        rot = rotations.get(ic_ref, ic.rotation)
        side = sides.get(ic_ref, 1)
        px, py = _pin_world(ic, (pos[ic_ref][0], pos[ic_ref][1]),
                            rot, side, PlacePin(plx, ply, ""))
        d = math.hypot(dx0 - px, dy0 - py)
        if best_d is None or d < best_d:
            best_d = d
            best = (ic_ref, px, py)
    return best


def _decap_term(
    comps: list[PlaceComp],
    pos: dict[str, list[float]],
    rotations: dict[str, float],
    sides: dict[str, int],
    pairs: dict[str, list[tuple[str, float, float]]],
) -> float:
    """Sum of decap-centroid-to-nearest-served-IC-power-pin distances."""
    by_ref = {c.ref: c for c in comps}
    total = 0.0
    for decap_ref, candidates in pairs.items():
        nearest = _nearest_ic_power_pin(
            decap_ref, candidates, by_ref, pos, rotations, sides)
        if nearest is None:
            continue
        _, px, py = nearest
        total += math.hypot(pos[decap_ref][0] - px, pos[decap_ref][1] - py)
    return total


def decoupling_report(
    comps: list[PlaceComp],
    pos: dict[str, list[float]],
    rotations: dict[str, float],
    sides: dict[str, int],
    nets: list[PlaceNet],
) -> list[dict]:
    """Per-decap proximity to the IC power pin it serves, in this placement.

    Surfaces the engine's structural decoupling analysis (which two-pin
    cap decouples which IC, found on the connectivity graph -- never by
    name) together with the ACHIEVED centre-to-power-pin distance in mils.
    A caller uses it to confirm decaps landed tight against their ICs (the
    manufacturability goal) without trusting the placement blind. Sorted by
    descending distance so the worst-placed decap is first. Empty when the
    netlist has no structurally-identifiable decoupling.
    """
    pairs = _pair_decaps_to_power_pins(comps, nets)
    if not pairs:
        return []
    by_ref = {c.ref: c for c in comps}
    out: list[dict] = []
    for decap_ref, candidates in pairs.items():
        nearest = _nearest_ic_power_pin(
            decap_ref, candidates, by_ref, pos, rotations, sides)
        if nearest is None:
            continue
        ic_ref, px, py = nearest
        dist = math.hypot(pos[decap_ref][0] - px, pos[decap_ref][1] - py)
        out.append({"decap": decap_ref, "ic": ic_ref,
                    "distance_mils": round(dist, 1)})
    out.sort(key=lambda d: (-d["distance_mils"], d["decap"]))
    return out


def _conn_term(
    comps: list[PlaceComp],
    pos: dict[str, list[float]],
    region: BoardRegion,
) -> float:
    """Squared distance of each connector to its assigned edge band."""
    rx_lo = min(region.x1, region.x2)
    rx_hi = max(region.x1, region.x2)
    ry_lo = min(region.y1, region.y2)
    ry_hi = max(region.y1, region.y2)
    total = 0.0
    for c in comps:
        if not _is_connector(c):
            continue
        x, y = pos[c.ref]
        band = _assigned_edge(c)
        if band == "L":
            d = x - (rx_lo + c.w / 2.0)
        elif band == "R":
            d = (rx_hi - c.w / 2.0) - x
        elif band == "B":
            d = y - (ry_lo + c.h / 2.0)
        elif band == "T":
            d = (ry_hi - c.h / 2.0) - y
        else:
            # No assigned band: distance to the nearest edge.
            d = min(
                x - rx_lo, rx_hi - x, y - ry_lo, ry_hi - y,
            )
        total += d * d
    return total


def _cong_term(
    world: dict[str, list[tuple[float, float]]],
    region: BoardRegion,
    nets: list[PlaceNet],
    bins: int,
) -> float:
    """Grid-bin congestion proxy.

    Each net spreads its HPWL over the bins its bounding box covers
    (density += HPWL / bbox_area into every covered bin); the term is the
    sum over bins of ``max(0, density - capacity)^2``. At this part count
    the grid is mostly empty so the term carries almost no gradient; it
    is retained as the slot a future routability model would fill.
    """
    bins = max(1, bins)
    rx_lo = min(region.x1, region.x2)
    ry_lo = min(region.y1, region.y2)
    bw = region.width / bins
    bh = region.height / bins
    if bw <= 0 or bh <= 0:
        return 0.0
    density = np.zeros((bins, bins), dtype=float)

    for name, pts in world.items():
        if len(pts) < 2:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        hp = (max(xs) - min(xs)) + (max(ys) - min(ys))
        i0 = int(_clamp((min(xs) - rx_lo) / bw, 0, bins - 1))
        i1 = int(_clamp((max(xs) - rx_lo) / bw, 0, bins - 1))
        j0 = int(_clamp((min(ys) - ry_lo) / bh, 0, bins - 1))
        j1 = int(_clamp((max(ys) - ry_lo) / bh, 0, bins - 1))
        covered = (i1 - i0 + 1) * (j1 - j0 + 1)
        # Spread the net's wirelength evenly over the bins its bounding
        # box covers; this keeps the per-bin load bounded (a near-zero-
        # area net deposits its whole length into one bin, not infinity).
        load = hp / max(1, covered)
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                density[i, j] += load

    # Capacity: the mean per-bin load. A perfectly even spread sits at
    # the mean and contributes nothing; only hotspots above it are
    # penalised.
    capacity = float(density.mean()) if density.size else 0.0
    over = np.maximum(0.0, density - capacity)
    # Normalise by bin area so the term is in comparable units to HPWL
    # rather than scaling with the (mils) length-squared.
    scale = 1.0 / max(1e-6, bw * bh)
    return float(np.sum(over * over)) * scale


def _therm_term(
    comps: list[PlaceComp],
    pos: dict[str, list[float]],
) -> float:
    """Hot-part repulsion: sum P_i*P_j / max(dist, eps) over pairs."""
    hot = [(c.ref, _power_w(c)) for c in comps if _power_w(c) > 0.0]
    if len(hot) < 2:
        return 0.0
    eps = 1.0
    total = 0.0
    for i in range(len(hot)):
        ri, pi = hot[i]
        for j in range(i + 1, len(hot)):
            rj, pj = hot[j]
            dx = pos[ri][0] - pos[rj][0]
            dy = pos[ri][1] - pos[rj][1]
            total += pi * pj / max(math.hypot(dx, dy), eps)
    return total


def _is_legal(
    comps: list[PlaceComp],
    pos: dict[str, list[float]],
    rotations: dict[str, float],
    sides: dict[str, int],
    region: BoardRegion,
    rules: DesignRules,
    clear_value: float | None = None,
) -> bool:
    """No same-side courtyard overlap and every body inside the board.

    ``clear_value`` lets a caller that already computed the (O(n^2))
    courtyard-overlap term pass it in to skip recomputing it here.
    """
    cv = clear_value if clear_value is not None \
        else _clear_term(comps, pos, rotations, sides, rules)
    if cv > 1e-3:
        return False
    rx_lo = min(region.x1, region.x2)
    rx_hi = max(region.x1, region.x2)
    ry_lo = min(region.y1, region.y2)
    ry_hi = max(region.y1, region.y2)
    for c in comps:
        x, y = pos[c.ref]
        w, h = _eff_wh(c, rotations.get(c.ref, c.rotation))
        if x - w / 2.0 < rx_lo - 1e-6 or x + w / 2.0 > rx_hi + 1e-6:
            return False
        if y - h / 2.0 < ry_lo - 1e-6 or y + h / 2.0 > ry_hi + 1e-6:
            return False
    return True


def _utilization(comps: list[PlaceComp], region: BoardRegion,
                 rules: DesignRules) -> float:
    board_area = region.width * region.height
    if board_area <= 0:
        return 0.0
    cc = rules.courtyard_clr
    used = sum((c.w + 2 * cc) * (c.h + 2 * cc) for c in comps)
    return used / board_area


_SEPARATION_FRACTION = 0.5


def _separation_term(
    comps: list[PlaceComp],
    pos: dict[str, list[float]],
    region: BoardRegion,
) -> float:
    """Mixed-signal keep-apart penalty (facility-layout REL 'X' relationship).

    For every pair of parts carrying DIFFERENT non-empty ``keepout_group``
    tags, penalise centre-to-centre distances below a desired separation
    ``D = 0.5 * board diagonal`` with the squared normalised shortfall
    ``((D - dist) / D)^2`` (0 when already far enough, 1 when coincident).
    Summed over conflicting pairs. Identically zero unless two distinct tags
    are present, so a single-domain board is unaffected.

    This is the industrial-engineering Systematic-Layout-Planning 'X'
    (undesirable adjacency) rating made continuous: it segregates a noisy
    switching / digital section from a sensitive analog / RF one. It trades
    against HPWL in the weighted objective, but because separating the groups
    also de-interleaves their intra-group nets it usually LOWERS HPWL rather
    than fighting it.
    """
    tagged = [(c.ref, _keepout_group(c)) for c in comps]
    tagged = [(r, g) for r, g in tagged if g]
    if len({g for _, g in tagged}) < 2:
        return 0.0
    d_desired = _SEPARATION_FRACTION * math.hypot(region.width, region.height)
    if d_desired <= 0.0:
        return 0.0
    total = 0.0
    for i in range(len(tagged)):
        ri, gi = tagged[i]
        for j in range(i + 1, len(tagged)):
            rj, gj = tagged[j]
            if gi == gj:
                continue
            dist = math.hypot(pos[ri][0] - pos[rj][0], pos[ri][1] - pos[rj][1])
            if dist < d_desired:
                shortfall = (d_desired - dist) / d_desired
                total += shortfall * shortfall
    return total


def _match_term(
    comps: list[PlaceComp],
    pos: dict[str, list[float]],
    region: BoardRegion,
) -> float:
    """Matched-pair keep-together penalty (SLP 'A' / boids cohesion / analog
    common-centroid matching).

    For every pair of parts sharing the SAME non-empty ``match_group`` tag,
    add the squared normalised centre distance ``(dist / board_diagonal)^2``.
    Squared so it pulls hard when the pair is far apart and eases off as they
    approach -- it gathers matched parts into one neighbourhood without
    fighting the clearance term down to a hard contact. Identically zero
    unless two parts share a tag.

    This is the complement of :func:`_separation_term`: separation is the
    facility-layout 'X' (undesirable adjacency) relationship and the boids
    SEPARATION rule; matching is the 'A' (absolutely-necessary adjacency)
    relationship and the boids COHESION rule. HPWL already co-locates parts
    that share a net; this co-locates matched parts that share NONE (a diff
    pair's two input resistors land on different input nets, so only a match
    term keeps them together for process / thermal matching).
    """
    groups: dict[str, list[str]] = {}
    for c in comps:
        g = _match_group(c)
        if g:
            groups.setdefault(g, []).append(c.ref)
    if not any(len(refs) >= 2 for refs in groups.values()):
        return 0.0
    diag = math.hypot(region.width, region.height)
    if diag <= 0.0:
        return 0.0
    total = 0.0
    for refs in groups.values():
        for i in range(len(refs)):
            for j in range(i + 1, len(refs)):
                dist = math.hypot(pos[refs[i]][0] - pos[refs[j]][0],
                                  pos[refs[i]][1] - pos[refs[j]][1])
                total += (dist / diag) ** 2
    return total


def _match_axis_term(
    comps: list[PlaceComp],
    rotations: dict[str, float],
) -> float:
    """Matched-pair orientation-consensus penalty (boids ALIGNMENT rule).

    Counts pairs of same-``match_group`` parts that sit on DIFFERENT axes,
    comparing rotation MOD 180. For a two-pin part 90 and 270 are the same
    axis (the mirror pair a differential layout naturally wants), so only a
    0/180-vs-90/270 split is penalised. This pushes matched parts onto a
    common axis for process / thermal matching WITHOUT fighting the mirror
    symmetry the rotation optimiser already produces by pointing each part's
    pins at its (mirror-image) net. Zero unless two parts share a tag.
    """
    groups: dict[str, list[str]] = {}
    for c in comps:
        g = _match_group(c)
        if g:
            groups.setdefault(g, []).append(c.ref)
    total = 0.0
    for refs in groups.values():
        if len(refs) < 2:
            continue
        axes = [int(round(rotations.get(r, 0.0))) % 180 for r in refs]
        for i in range(len(axes)):
            for j in range(i + 1, len(axes)):
                if axes[i] != axes[j]:
                    total += 1.0
    return total


def _match_role(comp: PlaceComp) -> str:
    """Sub-device label within a ``match_group`` -- which matched half a part
    belongs to ('A'/'B' for a differential pair's two sides or a current
    mirror's two transistors; the unit-cell label of a matched array). Empty
    when the part carries no role tag. Duck-typed like ``match_group``."""
    return str(getattr(comp, "match_role", "") or "")


def _match_centroid_term(
    comps: list[PlaceComp],
    pos: dict[str, list[float]],
    region: BoardRegion,
) -> float:
    """Common-centroid matching penalty (analog precision layout; Razavi,
    *Design of Analog CMOS Integrated Circuits*, common-centroid / cross-quad).

    For a ``match_group`` whose members carry a ``match_role`` sub-label (the
    two -- or more -- matched sub-devices), the centroids of the sub-devices
    should COINCIDE so that a linear process / thermal gradient across the
    group averages out to first order. The penalty is the summed squared
    normalised distance of each role centroid from the mean of the role
    centroids (the common centre)::

        target = mean_r centroid_r
        sum_r ( |centroid_r - target| / diag )^2

    Using the mean of the role centroids (not the part centroid) makes it
    symmetric in the roles and exactly zero when every sub-device shares one
    centre, regardless of member-count imbalance.

    This is distinct from :func:`_match_term`, which only gathers the members
    into one neighbourhood: a compact AABB split and a gradient-cancelling
    ABBA cross-quad are equally tight, so match cannot tell them apart -- the
    centroid term is what selects the balanced arrangement. A group needs
    >= 2 distinct roles to contribute (a lone role is trivially centred on
    itself), so a single-device tag stays at zero, as does any untagged board.
    """
    groups: dict[str, dict[str, list[str]]] = {}
    for c in comps:
        g = _match_group(c)
        r = _match_role(c)
        if g and r:
            groups.setdefault(g, {}).setdefault(r, []).append(c.ref)
    diag = math.hypot(region.width, region.height)
    if diag <= 0.0:
        return 0.0
    total = 0.0
    for roles in groups.values():
        if len(roles) < 2:
            continue
        centroids = []
        for members in roles.values():
            cx = sum(pos[r][0] for r in members) / len(members)
            cy = sum(pos[r][1] for r in members) / len(members)
            centroids.append((cx, cy))
        tx = sum(c[0] for c in centroids) / len(centroids)
        ty = sum(c[1] for c in centroids) / len(centroids)
        for (cx, cy) in centroids:
            total += (math.hypot(cx - tx, cy - ty) / diag) ** 2
    return total


def score(
    comps: list[PlaceComp],
    pos: dict[str, list[float]],
    rotations: dict[str, float],
    sides: dict[str, int],
    nets: list[PlaceNet],
    region: BoardRegion,
    rules: DesignRules,
    weights: ObjectiveWeights,
    decap_pairs: dict[str, list[tuple[str, float, float]]] | None = None,
) -> ObjectiveReport:
    """Evaluate the full weighted objective and return every term.

    The report exposes each term un-weighted plus the weighted total, so
    a caller can see which constraint dominates. Pin world coordinates
    use ``p = (x, y) + R(theta) * (lx, ly)`` with a bottom-side flip
    mirroring the local x.

    ``decap_pairs`` is the structural (position-independent) decap->IC-pin
    map. A hot caller (the SA polish) computes it ONCE and passes it in so
    it is not rebuilt on every evaluation; when omitted it is derived here.
    """
    world = _net_world_points(comps, pos, rotations, sides, nets)
    if decap_pairs is None:
        decap_pairs = _pair_decaps_to_power_pins(comps, nets)

    hpwl_v = _hpwl_term(world, nets)
    via_v = _via_term(comps, sides, nets)
    cong_v = _cong_term(world, region, nets, _default_cong_bins(comps))
    clear_v = _clear_term(comps, pos, rotations, sides, rules)
    edge_v = _edge_term(comps, pos, rotations, region, rules)
    decap_v = _decap_term(comps, pos, rotations, sides, decap_pairs)
    conn_v = _conn_term(comps, pos, region)
    therm_v = _therm_term(comps, pos)
    sep_v = _separation_term(comps, pos, region)
    match_v = _match_term(comps, pos, region)
    match_axis_v = _match_axis_term(comps, rotations)
    match_centroid_v = _match_centroid_term(comps, pos, region)

    total = (
        weights.hpwl * hpwl_v
        + weights.via * via_v
        + weights.cong * cong_v
        + weights.clear * clear_v
        + weights.edge * edge_v
        + weights.decap * decap_v
        + weights.conn * conn_v
        + weights.therm * therm_v
        + weights.sep * sep_v
        + weights.match * match_v
        + weights.match_axis * match_axis_v
        + weights.match_centroid * match_centroid_v
    )
    return ObjectiveReport(
        hpwl=hpwl_v,
        via=via_v,
        cong=cong_v,
        clear=clear_v,
        edge=edge_v,
        decap=decap_v,
        conn=conn_v,
        therm=therm_v,
        sep=sep_v,
        match=match_v,
        match_axis=match_axis_v,
        match_centroid=match_centroid_v,
        weighted_total=total,
        legal=_is_legal(comps, pos, rotations, sides, region, rules,
                        clear_value=clear_v),
        utilization=_utilization(comps, region, rules),
    )


def _default_cong_bins(comps: list[PlaceComp]) -> int:
    """A small congestion grid sized to the part count."""
    return max(4, min(12, int(math.ceil(math.sqrt(max(1, len(comps)))))))


# --------------------------------------------------------------------------- #
# Simulated-annealing polish
# --------------------------------------------------------------------------- #

def _metropolis_accept(
    delta_c: float,
    temperature: float,
    rng: np.random.Generator,
) -> bool:
    """Accept a move iff it improves the objective, else probabilistically.

    Returns True when ``delta_c <= 0`` and otherwise when
    ``rng.random() < exp(-delta_c / temperature)``. This is the single
    place an acceptance random draw happens, so the seed fully
    determines the run.
    """
    if delta_c <= 0.0:
        return True
    if temperature <= 0.0:
        return False
    # Guard the exponent so a huge uphill move never overflows.
    x = -delta_c / temperature
    if x < -700.0:
        return False
    return bool(rng.random() < math.exp(x))


def sa_polish(
    comps: list[PlaceComp],
    pos: dict[str, list[float]],
    rotations: dict[str, float],
    sides: dict[str, int],
    nets: list[PlaceNet],
    region: BoardRegion,
    rules: DesignRules,
    weights: ObjectiveWeights,
    opts: ConstructOptions,
    rng: np.random.Generator,
) -> tuple[dict, dict, dict, int, int]:
    """Short, cooled Metropolis local search (the polish, not the placer).

    Move types are sampled by weight: translate (a grid jump), rotate
    (a quarter turn), swap (exchange two same-side parts), flip-side
    (only on a two-layer board, only for flippable parts), and decap-snap
    (jump a decap onto its served IC power pin). Each candidate is
    grid-snapped and the objective is re-evaluated; the clearance and
    edge terms have large weights so an illegal candidate is almost
    always rejected. The best-seen state is kept separately and restored
    at the end. ``pos`` / ``rotations`` / ``sides`` are mutated in place
    and also returned, along with the accept / reject counts.

    The budget is deliberately small (a few hundred moves per part), the
    schedule begins already cooled, and the search stops early on a low
    accept rate or a stale best. SA is a polish here, not the optimiser:
    the constructive seed and the greedy rotation pass do the real work.
    """
    movable = [c for c in comps if not c.fixed]
    by_ref = {c.ref: c for c in comps}
    n = len(movable)
    if n == 0:
        return pos, rotations, sides, 0, 0

    rx_lo = min(region.x1, region.x2)
    rx_hi = max(region.x1, region.x2)
    ry_lo = min(region.y1, region.y2)
    ry_hi = max(region.y1, region.y2)
    grid = max(1.0, rules.grid)
    decap_pairs = _pair_decaps_to_power_pins(comps, nets)
    two_layer = rules.layers >= 2
    # Keep-together groups for the match-snap move (only groups with >= 2
    # members on the movable set matter; a lone-tagged part has nowhere to go).
    match_groups: dict[str, list[str]] = {}
    movable_refs = {c.ref for c in movable}
    for c in comps:
        g = _match_group(c)
        if g:
            match_groups.setdefault(g, []).append(c.ref)
    match_groups = {g: refs for g, refs in match_groups.items()
                    if len(refs) >= 2 and any(r in movable_refs for r in refs)}
    member_to_group = {r: g for g, refs in match_groups.items() for r in refs
                       if r in movable_refs}

    def full_cost() -> float:
        # Pass the once-computed structural decap pairing so score() does not
        # rebuild it on every probe/move (it is position-independent).
        return score(comps, pos, rotations, sides, nets, region,
                     rules, weights, decap_pairs=decap_pairs).weighted_total

    cur_cost = full_cost()

    # Probe a handful of moves to set the initial temperature, then begin
    # the schedule already partly cooled (this is a polish).
    deltas: list[float] = []
    for _ in range(max(1, opts.probe_moves)):
        snap = _snapshot(pos, rotations, sides)
        _apply_random_move(
            movable, by_ref, pos, rotations, sides, region, rules,
            grid, decap_pairs, two_layer, opts, rng,
            match_groups, member_to_group,
        )
        d = abs(full_cost() - cur_cost)
        deltas.append(d)
        _restore(pos, rotations, sides, snap)
    mean_delta = sum(deltas) / max(1, len(deltas))
    if mean_delta <= 1e-9:
        t0 = grid  # nothing to cool against; a small floor
    else:
        t0 = -mean_delta / math.log(0.8)
    temp = max(1e-6, opts.start_temp_fraction * t0)
    t_min = max(1e-9, t0 * 1e-3)

    budget = opts.max_moves if opts.max_moves > 0 else max(200, 300 * n)
    epoch_len = max(1, 100 * n)

    best_pos = _snapshot(pos, rotations, sides)
    best_cost = cur_cost

    accepted = 0
    rejected = 0
    moves_done = 0
    stale = 0
    epoch_accept = 0
    epoch_moves = 0

    while moves_done < budget and temp > t_min:
        snap = _snapshot(pos, rotations, sides)
        _apply_random_move(
            movable, by_ref, pos, rotations, sides, region, rules,
            grid, decap_pairs, two_layer, opts, rng,
            match_groups, member_to_group,
        )
        new_cost = full_cost()
        delta = new_cost - cur_cost
        if _metropolis_accept(delta, temp, rng):
            cur_cost = new_cost
            accepted += 1
            epoch_accept += 1
            if cur_cost < best_cost - 1e-9:
                best_cost = cur_cost
                best_pos = _snapshot(pos, rotations, sides)
        else:
            _restore(pos, rotations, sides, snap)
            rejected += 1

        moves_done += 1
        epoch_moves += 1

        if epoch_moves >= epoch_len:
            rate = epoch_accept / max(1, epoch_moves)
            temp *= opts.cooling
            if best_cost < cur_cost + 1e-9 and epoch_accept == 0:
                stale += 1
            else:
                stale = 0
            epoch_accept = 0
            epoch_moves = 0
            if rate < opts.min_accept_rate or stale >= opts.stale_epochs:
                break

    # Restore the best-seen state.
    _restore(pos, rotations, sides, best_pos)
    return pos, rotations, sides, accepted, rejected


def _snapshot(
    pos: dict[str, list[float]],
    rotations: dict[str, float],
    sides: dict[str, int],
) -> tuple[dict, dict, dict]:
    return (
        {r: [v[0], v[1]] for r, v in pos.items()},
        dict(rotations),
        dict(sides),
    )


def _restore(
    pos: dict[str, list[float]],
    rotations: dict[str, float],
    sides: dict[str, int],
    snap: tuple[dict, dict, dict],
) -> None:
    sp, sr, ss = snap
    for r, v in sp.items():
        pos[r][0] = v[0]
        pos[r][1] = v[1]
    rotations.clear()
    rotations.update(sr)
    sides.clear()
    sides.update(ss)


_ORIENTATIONS = (0.0, 90.0, 180.0, 270.0)


_SLOT_DIRS = tuple(
    (math.cos(k * math.pi / 4.0), math.sin(k * math.pi / 4.0))
    for k in range(8)
)


def _adjacent_slot(
    m: PlaceComp,
    anchor_ref: str,
    by_ref: dict[str, PlaceComp],
    pos: dict[str, list[float]],
    rotations: dict[str, float],
    region: BoardRegion,
    rules: DesignRules,
    grid: float,
) -> tuple[float, float]:
    """A grid-snapped, collision-free slot for ``m`` beside group-mate
    ``anchor_ref``.

    Used by the match-snap move to drop a keep-together member against its
    nearest group-mate. Tries the eight directions around the mate, preferring
    the side ``m`` already approaches from (so a near member barely moves), and
    returns the first slot clear of every other placed part -- a member
    stranded behind the IC its mate hugs can thus find an open side instead of
    landing on the IC and being rejected. ``max(w, h)`` spacing keeps the pair
    clear at any rotation. Falls back to the approach side when every slot
    collides (the SA then rejects the move).
    """
    a = by_ref[anchor_ref]
    ax, ay = pos[anchor_ref][0], pos[anchor_ref][1]
    aw, ah = _eff_wh(a, rotations.get(anchor_ref, a.rotation))
    mw, mh = _eff_wh(m, rotations.get(m.ref, m.rotation))
    dist = (max(aw, ah) + max(mw, mh)) / 2.0 + rules.component_clr
    rx_lo = min(region.x1, region.x2) + mw / 2.0
    rx_hi = max(region.x1, region.x2) - mw / 2.0
    ry_lo = min(region.y1, region.y2) + mh / 2.0
    ry_hi = max(region.y1, region.y2) - mh / 2.0
    apx = pos[m.ref][0] - ax
    apy = pos[m.ref][1] - ay
    al = math.hypot(apx, apy) or 1.0
    apx, apy = apx / al, apy / al
    dirs = sorted(_SLOT_DIRS, key=lambda d: -(d[0] * apx + d[1] * apy))
    fallback = None
    for dx, dy in dirs:
        tx = _clamp(_snap(ax + dx * dist, grid), rx_lo, rx_hi)
        ty = _clamp(_snap(ay + dy * dist, grid), ry_lo, ry_hi)
        if fallback is None:
            fallback = (tx, ty)
        clash = False
        for c in by_ref.values():
            if c.ref == m.ref or c.ref not in pos:
                continue
            cw, ch = _eff_wh(c, rotations.get(c.ref, c.rotation))
            if _rect_overlap_area(mw, mh, tx, ty, cw, ch,
                                   pos[c.ref][0], pos[c.ref][1],
                                   rules.component_clr) > 0.0:
                clash = True
                break
        if not clash:
            return tx, ty
    return fallback


def _decap_slot(
    dref: str,
    ic_ref: str,
    px: float,
    py: float,
    by_ref: dict[str, PlaceComp],
    pos: dict[str, list[float]],
    rotations: dict[str, float],
    sides: dict[str, int],
    rules: DesignRules,
    bounds: tuple[float, float, float, float],
    grid: float,
) -> tuple[float, float]:
    """Target (x, y) for a decap hugging its IC's power-pin face.

    Aligned to the supply pin, offset just OUTSIDE the IC body by the copper
    clearance (centring on the pin would overlap the IC), then slid
    tangentially along that face to the nearest slot that doesn't collide
    with another same-side part. The tangential search is what lets several
    caps that share ONE supply pin (the common small-MCU case: one VCC pad,
    two or three bypass caps) spread along the face instead of stacking on a
    single illegal point. Falls back to the pin-aligned anchor if every
    searched slot is blocked.
    """
    rx_lo, rx_hi, ry_lo, ry_hi = bounds
    c = by_ref[dref]
    ww, hh = _eff_wh(c, rotations.get(dref, c.rotation))
    ic = by_ref.get(ic_ref)
    if ic is None or ic_ref not in pos:
        return _snap(px, grid), _snap(py, grid)

    icx, icy = pos[ic_ref][0], pos[ic_ref][1]
    icw, ich = _eff_wh(ic, rotations.get(ic_ref, ic.rotation))
    clr = rules.component_clr
    dx, dy = px - icx, py - icy
    horizontal = abs(dx) >= abs(dy)
    if horizontal:
        sign = 1.0 if dx >= 0 else -1.0
        base_x = icx + sign * (icw / 2.0 + clr + ww / 2.0)
        base_y = py
        pitch = hh + clr        # tangential step runs along y
    else:
        sign = 1.0 if dy >= 0 else -1.0
        base_y = icy + sign * (ich / 2.0 + clr + hh / 2.0)
        base_x = px
        pitch = ww + clr        # tangential step runs along x

    my_side = sides.get(dref, 1)
    others = [
        o for o in by_ref.values()
        if o.ref != dref and o.ref in pos and sides.get(o.ref, 1) == my_side
    ]

    def _blocked(tx: float, ty: float) -> bool:
        for o in others:
            ow, oh = _eff_wh(o, rotations.get(o.ref, o.rotation))
            if _rect_overlap_area(
                ww, hh, tx, ty, ow, oh, pos[o.ref][0], pos[o.ref][1], clr,
            ) > 0.0:
                return True
        return False

    slots = [0]
    for k in range(1, 9):
        slots.extend((k, -k))
    for k in slots:
        if horizontal:
            tx, ty = base_x, base_y + k * pitch
        else:
            tx, ty = base_x + k * pitch, base_y
        tx = _clamp(_snap(tx, grid), rx_lo + ww / 2.0, rx_hi - ww / 2.0)
        ty = _clamp(_snap(ty, grid), ry_lo + hh / 2.0, ry_hi - hh / 2.0)
        if not _blocked(tx, ty):
            return tx, ty
    return (
        _clamp(_snap(base_x, grid), rx_lo + ww / 2.0, rx_hi - ww / 2.0),
        _clamp(_snap(base_y, grid), ry_lo + hh / 2.0, ry_hi - hh / 2.0),
    )


def _apply_random_move(
    movable: list[PlaceComp],
    by_ref: dict[str, PlaceComp],
    pos: dict[str, list[float]],
    rotations: dict[str, float],
    sides: dict[str, int],
    region: BoardRegion,
    rules: DesignRules,
    grid: float,
    decap_pairs: dict[str, list[tuple[str, float, float]]],
    two_layer: bool,
    opts: ConstructOptions,
    rng: np.random.Generator,
    match_groups: dict[str, list[str]] = None,
    member_to_group: dict[str, str] = None,
) -> None:
    """Apply one in-place random move, sampled by type weight."""
    rx_lo = min(region.x1, region.x2)
    rx_hi = max(region.x1, region.x2)
    ry_lo = min(region.y1, region.y2)
    ry_hi = max(region.y1, region.y2)
    match_groups = match_groups or {}
    member_to_group = member_to_group or {}

    kinds = ["translate", "rotate", "swap", "flip", "decap", "match"]
    w = np.array([
        opts.move_translate, opts.move_rotate, opts.move_swap,
        opts.move_flip if two_layer else 0.0,
        opts.move_decap if decap_pairs else 0.0,
        opts.move_match if match_groups else 0.0,
    ], dtype=float)
    if w.sum() <= 0:
        w = np.array([1.0, 0.0, 0.0, 0.0, 0.0])
    w = w / w.sum()
    kind = kinds[int(rng.choice(len(kinds), p=w))]

    if kind == "translate":
        c = movable[int(rng.integers(len(movable)))]
        step = grid * (1 + int(rng.integers(0, 4)))
        ang = rng.random() * 2.0 * math.pi
        nx = pos[c.ref][0] + step * math.cos(ang)
        ny = pos[c.ref][1] + step * math.sin(ang)
        ww, hh = _eff_wh(c, rotations.get(c.ref, c.rotation))
        pos[c.ref][0] = _clamp(_snap(nx, grid), rx_lo + ww / 2.0, rx_hi - ww / 2.0)
        pos[c.ref][1] = _clamp(_snap(ny, grid), ry_lo + hh / 2.0, ry_hi - hh / 2.0)

    elif kind == "rotate":
        rotatable = [c for c in movable if c.rotatable and c.pins]
        if not rotatable:
            return
        c = rotatable[int(rng.integers(len(rotatable)))]
        rotations[c.ref] = _ORIENTATIONS[int(rng.integers(4))]
        # Re-clamp inside the board with the new effective dims.
        ww, hh = _eff_wh(c, rotations[c.ref])
        pos[c.ref][0] = _clamp(pos[c.ref][0], rx_lo + ww / 2.0, rx_hi - ww / 2.0)
        pos[c.ref][1] = _clamp(pos[c.ref][1], ry_lo + hh / 2.0, ry_hi - hh / 2.0)

    elif kind == "swap":
        if len(movable) < 2:
            return
        i = int(rng.integers(len(movable)))
        j = int(rng.integers(len(movable)))
        if i == j:
            return
        a, b = movable[i], movable[j]
        if sides.get(a.ref, 1) != sides.get(b.ref, 1):
            return
        pos[a.ref][0], pos[b.ref][0] = pos[b.ref][0], pos[a.ref][0]
        pos[a.ref][1], pos[b.ref][1] = pos[b.ref][1], pos[a.ref][1]

    elif kind == "flip":
        flippable = [c for c in movable if _flippable(c)]
        if not flippable:
            return
        c = flippable[int(rng.integers(len(flippable)))]
        sides[c.ref] = -sides.get(c.ref, 1)

    elif kind == "decap":
        if not decap_pairs:
            return
        decap_refs = [r for r in decap_pairs if r in pos]
        if not decap_refs:
            return
        dref = decap_refs[int(rng.integers(len(decap_refs)))]
        nearest = _nearest_ic_power_pin(
            dref, decap_pairs[dref], by_ref, pos, rotations, sides)
        if nearest is None:
            return
        ic_ref, px, py = nearest
        tx, ty = _decap_slot(
            dref, ic_ref, px, py, by_ref, pos, rotations, sides, rules,
            (rx_lo, rx_hi, ry_lo, ry_hi), grid,
        )
        pos[dref][0] = tx
        pos[dref][1] = ty

    elif kind == "match":
        # Jump a keep-together member adjacent to its nearest placed group-mate.
        members = [r for r in member_to_group if r in pos]
        if not members:
            return
        mref = members[int(rng.integers(len(members)))]
        grp = member_to_group[mref]
        mates = [r for r in match_groups.get(grp, ())
                 if r != mref and r in pos]
        if not mates:
            return
        n = min(mates, key=lambda r: (pos[r][0] - pos[mref][0]) ** 2
                + (pos[r][1] - pos[mref][1]) ** 2)
        m = by_ref[mref]
        tx, ty = _adjacent_slot(
            m, n, by_ref, pos, rotations, region, rules, grid)
        pos[mref][0] = tx
        pos[mref][1] = ty


# --------------------------------------------------------------------------- #
# Top-level constructor
# --------------------------------------------------------------------------- #

def _place_options(rules: DesignRules, opts: ConstructOptions) -> PlaceOptions:
    """Bridge ``DesignRules`` / ``ConstructOptions`` into the refiner's
    option object, for the reused rotation and shove passes."""
    return PlaceOptions(
        grid_mils=rules.grid,
        clearance_mils=rules.component_clr,
        respect_fixed=True,
        optimize_rotation=True,
        rotation_sweeps=opts.rotation_sweeps,
        max_shove_rounds=opts.max_shove_rounds,
    )


def construct_placement(
    comps: list[PlaceComp],
    nets: list[PlaceNet],
    rules: DesignRules,
    opts: ConstructOptions = ConstructOptions(),
) -> ConstructResult:
    """Build a legal first-pass placement and the board it sits on.

    Pipeline: size the board, pin connectors / hold fixed parts, place
    the rest at connectivity-weighted centroids, choose orientations
    with a dedicated greedy pass, legalize with a hard-shove (growing the
    board and re-running if it cannot fit), then run a short cooled local
    polish. Returns the board rectangle, a per-component
    :class:`Placement` (back-rotated origin, rotation, side), an
    :class:`ObjectiveReport`, and accept / reject / iteration counters.

    Pure function: it does not mutate its inputs and makes no bridge
    calls. Deterministic given ``opts.seed`` -- one
    :class:`numpy.random.Generator` is threaded through every stochastic
    step.
    """
    weights = ObjectiveWeights()
    notes: list[str] = []

    if not comps:
        region = size_board(comps, nets, rules)
        return ConstructResult(
            region=region,
            placements={},
            report=ObjectiveReport(),
            notes=["no components"],
        )

    # Dedup refs defensively (last wins).
    dedup: dict[str, PlaceComp] = {}
    for c in comps:
        dedup[c.ref] = c
    comps = list(dedup.values())

    rng = np.random.default_rng(opts.seed)
    region = size_board(comps, nets, rules)
    place_opts = _place_options(rules, opts)

    # Fixed positions: connectors get pinned to an edge band centre; any
    # explicitly fixed part holds its input centroid.
    fixed_pos = _seed_constraints(comps, region, rules)

    sides = _assign_sides(comps, rules)
    rotations = {c.ref: c.rotation for c in comps}

    seed_fn = spectral_seed if opts.seed_mode == "spectral" else constructive_seed
    grow_steps = 0
    pos: dict[str, list[float]] = {}
    while True:
        pos = seed_fn(comps, nets, region, rules, rng, fixed_pos)
        # Dedicated greedy rotation pass before any annealing.
        _optimize_rotations(comps, _as_tuple_pos(pos), nets, place_opts,
                            rotations)
        # Hold pinned parts (connectors at their edge band, fixed parts)
        # while the shove spreads everything else around them.
        shove_comps = _pin_for_legalize(comps, fixed_pos)
        _hard_shove(shove_comps, region, place_opts, pos, rotations)
        # Base the grow decision on the courtyard-overlap objective, not
        # the shove's stricter copper clearance (a near-miss within the
        # copper gap is still a legal placement and must not grow the
        # board forever).
        residual_area = _clear_term(comps, pos, rotations, sides, rules)
        if residual_area <= 1e-3 or grow_steps >= opts.max_grow_steps:
            if residual_area > 1e-3:
                notes.append(
                    f"courtyard overlap {residual_area:.0f} remains after "
                    f"legalization; board grown {grow_steps} step(s)"
                )
            break
        # Grow the board one grid step on its shorter dimension and retry.
        grow_steps += 1
        region = _grow_region(region, rules)
        # Re-pin connectors against the new edges.
        fixed_pos = _seed_constraints(comps, region, rules)

    # Short cooled polish on the full objective.
    pos, rotations, sides, accepted, rejected = sa_polish(
        comps, pos, rotations, sides, nets, region, rules, weights, opts, rng,
    )

    # The polish optimizes the weighted objective and can accept a
    # wirelength-improving move that introduces a small courtyard overlap
    # (the clearance penalty is weighted, not a hard gate). Re-run the
    # deterministic legalizer on the polished positions so the returned
    # placement is guaranteed non-overlapping; surface any residual.
    shove_comps = _pin_for_legalize(comps, fixed_pos)
    _hard_shove(shove_comps, region, place_opts, pos, rotations)
    post_overlap = _clear_term(comps, pos, rotations, sides, rules)
    if post_overlap > 1e-3:
        notes.append(
            f"courtyard overlap {post_overlap:.0f} remains after post-polish "
            f"legalization"
        )

    # Fit the outline to the final placement: size_board estimates before
    # placement and the legalizer only grows, so an over-estimated board is
    # left oversized. Tightening lifts utilization without moving any part.
    tight = _tighten_region(comps, pos, rotations, region, rules)
    if tight.width < region.width or tight.height < region.height:
        notes.append(
            f"board tightened {region.width:.0f}x{region.height:.0f} -> "
            f"{tight.width:.0f}x{tight.height:.0f}"
        )
        region = tight

    report = score(comps, pos, rotations, sides, nets, region, rules, weights)

    placements = _build_placements(comps, pos, rotations, sides, rules, region)
    centroids = {r: (pos[r][0], pos[r][1]) for r in pos}

    return ConstructResult(
        region=region,
        placements=placements,
        report=report,
        centroids=centroids,
        rotations=dict(rotations),
        sides=dict(sides),
        accepted=accepted,
        rejected=rejected,
        iterations=accepted + rejected,
        grow_steps=grow_steps,
        notes=notes,
    )


def construct_placement_best_of(
    comps: list[PlaceComp],
    nets: list[PlaceNet],
    rules: DesignRules,
    *,
    seeds: tuple = (0, 1, 2, 3),
    base_opts: ConstructOptions = None,
) -> ConstructResult:
    """Run :func:`construct_placement` once per seed and keep the best result.

    The constructor is mostly deterministic, but the polish and a few
    construction tie-breaks are seed-driven, so different seeds explore
    different local optima. Restarting from several seeds and keeping the
    lowest-objective legal placement reduces the variance of a single run at
    a cost linear in the number of seeds. Selection prefers a legal placement
    first, then the lower weighted objective, with the seed value as a final
    deterministic tie-break. Returns the winning :class:`ConstructResult`,
    annotated with which seed won.

    The greedy connectivity / signal-flow seed is run across the FULL seed
    budget (so the result is never worse than it was before the spectral seed
    existed). The spectral (eigenvector) seed is added as a bounded probe of a
    couple of seeds: it wins on different netlist shapes (scattered / mesh
    graphs where greedy's local centroid order collapses parts together), and
    because its initial coordinates are deterministic, one or two polish seeds
    already capture its benefit. Keeping the lower-objective result per design
    makes spectral a strict improvement at a small added cost -- it is chosen
    only where it actually beats greedy on the real weighted objective.
    """
    base = base_opts if base_opts is not None else ConstructOptions()
    seed_list = list(seeds) if seeds else [base.seed]
    # Full greedy budget + a bounded spectral probe (deterministic seed coords,
    # so a couple of polish seeds suffice).
    jobs = [("greedy", s) for s in seed_list]
    jobs += [("spectral", s) for s in seed_list[:2]]

    best: ConstructResult = None
    best_key = None
    for mode, s in jobs:
        res = construct_placement(
            comps, nets, rules, replace(base, seed=s, seed_mode=mode))
        # Lower is better: legal beats illegal, then weighted_total. The
        # (mode, seed) tie-break keeps selection fully deterministic.
        key = (0 if res.report.legal else 1, res.report.weighted_total,
               0 if mode == "greedy" else 1, s)
        if best is None or key < best_key:
            best, best_key = res, key

    if best is not None:
        won_mode = "greedy" if best_key[2] == 0 else "spectral"
        best.notes = list(best.notes) + [
            f"best of {len(seed_list)} seeds x 2 strategies "
            f"({won_mode} seed={best_key[3]}, "
            f"total={best.report.weighted_total:.1f})"
        ]
    return best


# --------------------------------------------------------------------------- #
# Visual-driven repair + selection
# --------------------------------------------------------------------------- #

def _pin_world_points_for_ref(
    comp: PlaceComp,
    centroid: tuple[float, float],
    rotation: float,
    side: int,
) -> list[tuple[str, float, float]]:
    """World ``(net, x, y)`` for each pin of one placed component.

    Mirrors the pin transform used in the objective: a bottom-side part
    (``side < 0``) flips local x. Kept local so the visual repair does not
    depend on the net-indexed :func:`_net_world_points`.
    """
    th = math.radians(rotation)
    ct, st = math.cos(th), math.sin(th)
    sf = -1.0 if side < 0 else 1.0
    out: list[tuple[str, float, float]] = []
    for p in comp.pins:
        lx = sf * p.lx
        ly = p.ly
        out.append((p.net, centroid[0] + lx * ct - ly * st,
                    centroid[1] + lx * st + ly * ct))
    return out


def _match_groups_map(comps: list[PlaceComp]) -> dict[str, str]:
    """Refdes -> non-empty ``match_group`` tag (keep-together clusters)."""
    return {c.ref: _match_group(c) for c in comps if _match_group(c)}


def _cluster_anchor(
    comps: list[PlaceComp],
    nets: list[PlaceNet],
    members: set[str],
    centroids: dict[str, list[float]],
    rotations: dict[str, float],
    sides: dict[str, int],
) -> tuple[Optional[tuple[float, float]], Optional[str]]:
    """Where a keep-together cluster should sit: the centroid of the cluster's
    external pin connections, and the multi-pin IC it serves.

    The cluster's own nets (minus the dominant ground rail, which connects
    almost everything and would drag the anchor to the board centre) are the
    signal nets it must stay close to -- a crystal's XIN/XOUT, a matched
    pair's shared node. The anchor is the average world position of the pins
    on those nets that belong to NON-cluster parts; the served IC is a
    >=3-pin such part (the MCU for a crystal). Returns ``(None, None)`` when
    the cluster has no external signal connection to anchor against.
    """
    by_ref = {c.ref: c for c in comps}
    group_nets = {
        p.net for r in members if r in by_ref for p in by_ref[r].pins
    }
    if not group_nets:
        return None, None
    counts = {n.name: len(set(n.refs)) for n in nets}
    dominant = max(counts, key=counts.get) if counts else ""
    signal_nets = {n for n in group_nets if n != dominant}
    if not signal_nets:
        signal_nets = group_nets
    pts: list[tuple[float, float]] = []
    anchor_ic: Optional[str] = None
    for c in comps:
        if c.ref in members or c.ref not in centroids:
            continue
        hit = [
            (wx, wy)
            for net, wx, wy in _pin_world_points_for_ref(
                c, (centroids[c.ref][0], centroids[c.ref][1]),
                rotations.get(c.ref, 0.0), sides.get(c.ref, 1))
            if net in signal_nets
        ]
        if hit:
            pts.extend(hit)
            if len(c.pins) >= 3 and anchor_ic is None:
                anchor_ic = c.ref
    if not pts:
        return None, None
    ax = sum(p[0] for p in pts) / len(pts)
    ay = sum(p[1] for p in pts) / len(pts)
    return (ax, ay), anchor_ic


def tighten_match_clusters(
    comps: list[PlaceComp],
    nets: list[PlaceNet],
    rules: DesignRules,
    result: ConstructResult,
    *,
    weights: ObjectiveWeights = None,
    seed: int = 0,
) -> ConstructResult:
    """Relocate any scattered keep-together cluster tight against the IC it
    serves, then re-legalize -- a visual-driven repair.

    A cluster tagged with a shared ``match_group`` (a crystal + its load caps,
    a matched pair) must sit together; the analytic objective's match term is
    a soft pull that a poor seed can defeat, and local polish cannot teleport a
    member across the board past other parts (a global move). This pass reads
    the visual compactness metric, and for each cluster whose spread exceeds
    its tight-pack target it: picks up the members, drops them in a tight line
    just outside the served IC at the cluster's signal-pin anchor, re-runs the
    grow-and-shove legalizer, and settles with a short match-boosted polish.

    The repaired placement is accepted only when it is legal AND lowers the
    combined analytic-plus-visual objective ``weighted_total + visual_penalty``
    -- so tightening a cluster is kept when its (small) added wirelength buys a
    larger drop in the compactness penalty, and rejected when it would wreck
    the board. Returns the repaired :class:`ConstructResult`, or the input
    unchanged when nothing is scattered or no repair improves the combination.
    """
    from eda_agent.design import visual_metrics as _vm

    w = weights if weights is not None else ObjectiveWeights()
    by_ref = {c.ref: c for c in comps}
    groups = _match_groups_map(comps)
    if not groups:
        return result

    members_by_group: dict[str, list[str]] = {}
    for ref, grp in groups.items():
        members_by_group.setdefault(grp, []).append(ref)
    scattered = []
    _, excess0 = _vm.group_compactness(comps, result.centroids, groups)
    for grp, refs in members_by_group.items():
        if len(refs) >= 2 and excess0.get(grp, 0.0) > 1e-6:
            scattered.append(grp)
    if not scattered:
        return result

    def _combined(res: ConstructResult) -> float:
        cr = ratsnest_crossings(
            comps, {r: [v[0], v[1]] for r, v in res.centroids.items()},
            res.rotations, res.sides, nets)
        vr = _vm.visual_report(comps, res.centroids, res.region,
                               groups=groups, crossings=cr)
        legal_pen = 0.0 if res.report.legal else 1e9
        return res.report.weighted_total + vr.penalty + legal_pen

    base_combined = _combined(result)

    cen = {r: [v[0], v[1]] for r, v in result.centroids.items()}
    rot = dict(result.rotations)
    sides = dict(result.sides)
    moved = False
    for grp in scattered:
        members = set(members_by_group[grp])
        anchor, anchor_ic = _cluster_anchor(
            comps, nets, members, cen, rot, sides)
        if anchor is None or anchor_ic is None:
            continue
        ax, ay = anchor
        icc = cen[anchor_ic]
        dx, dy = ax - icc[0], ay - icc[1]
        dl = math.hypot(dx, dy) or 1.0
        dx, dy = dx / dl, dy / dl
        px, py = -dy, dx  # perpendicular: pack members in a line along it
        # Drop the cluster just outside the IC body along the outward
        # direction, members spaced on the perpendicular.
        # Drop the cluster clear of the IC body: gap past the pin anchor,
        # members spaced wide enough on the perpendicular to start nearly
        # overlap-free (a too-tight drop lands the polish in a bad basin).
        biggest = max(max(by_ref[r].w, by_ref[r].h) for r in members
                      if r in by_ref)
        gap = max(120.0, biggest * 0.75 + rules.component_clr * 2.0)
        cx0, cy0 = ax + dx * gap, ay + dy * gap
        ordered = sorted(members)
        step = max(110.0, biggest + rules.component_clr * 2.0)
        mid = (len(ordered) - 1) / 2.0
        for i, r in enumerate(ordered):
            cen[r] = [cx0 + px * (i - mid) * step,
                      cy0 + py * (i - mid) * step]
        moved = True
    if not moved:
        return result

    # Re-legalize with the grow-closure loop only. The relocation already made
    # the global move (the cluster is tight against its IC); a full re-polish
    # here -- especially a match-boosted one -- re-optimizes the whole board
    # under a distorted objective and inflates wirelength unpredictably. The
    # deterministic shove just resolves the overlaps the drop created, keeping
    # the cluster tight and every other part where best-of placed it.
    region = result.region
    place_opts = PlaceOptions()
    place_opts.clearance_mils = rules.component_clr
    place_opts.respect_fixed = True
    place_opts.max_shove_rounds = 120
    fixed_pos = {c.ref: (c.cx, c.cy) for c in comps if c.fixed}
    for _ in range(4):
        shove_comps = _pin_for_legalize(comps, fixed_pos)
        _hard_shove(shove_comps, region, place_opts, cen, rot)
        if _clear_term(comps, cen, rot, sides, rules) <= 1e-3:
            break
        region = _grow_region(region, rules)

    # Settle with a match-boosted all-movable polish: the relocation + shove
    # disturbed wirelength board-wide, and an all-movable polish lets the whole
    # board recover while the boosted match term holds the freshly tightened
    # cluster together as it shifts.
    settle_w = replace(w, match=w.match * 4.0)
    rng = np.random.default_rng(seed)
    cen, rot, sides, _, _ = sa_polish(
        comps, cen, rot, sides, nets, region, rules, settle_w,
        ConstructOptions(max_moves=2000, seed=seed), rng)

    region = _tighten_region(comps, cen, rot, region, rules)
    report = score(comps, cen, rot, sides, nets, region, rules, w)
    placements = _build_placements(comps, cen, rot, sides, rules, region)
    repaired = ConstructResult(
        region=region,
        placements=placements,
        report=report,
        centroids={r: (cen[r][0], cen[r][1]) for r in cen},
        rotations=dict(rot),
        sides=dict(sides),
        notes=list(result.notes) + [
            f"visual repair: tightened {len(scattered)} cluster(s) "
            f"{','.join(scattered)}"],
    )
    if _combined(repaired) < base_combined:
        return repaired
    return result


def construct_placement_visual(
    comps: list[PlaceComp],
    nets: list[PlaceNet],
    rules: DesignRules,
    *,
    seeds: tuple = (0, 1, 2, 3),
    base_opts: ConstructOptions = None,
) -> ConstructResult:
    """:func:`construct_placement_best_of` followed by the visual repair pass.

    Best-of picks the lowest-objective legal seed; the repair then tightens any
    keep-together cluster the analytic objective left scattered. This is the
    entry point the tool layer uses so a crystal / matched group comes out
    clustered even when no single seed placed it well.
    """
    best = construct_placement_best_of(
        comps, nets, rules, seeds=seeds, base_opts=base_opts)
    if best is None:
        return best
    return tighten_match_clusters(comps, nets, rules, best)


def _as_tuple_pos(pos: dict[str, list[float]]) -> dict[str, tuple[float, float]]:
    """The reused rotation pass reads tuple positions; it never writes,
    so a live view of the same coordinates is fine."""
    return {r: (v[0], v[1]) for r, v in pos.items()}


def _seed_constraints(
    comps: list[PlaceComp],
    region: BoardRegion,
    rules: DesignRules,
) -> dict[str, tuple[float, float]]:
    """Compute pinned positions for connectors and fixed parts.

    A connector with an assigned edge band is seated against that edge
    (x fixed for L/R, y fixed for T/B), centred on the free axis. A
    connector with no band, and any explicitly fixed part, holds its
    input centroid. The rotation lock that points a connector's mating
    face outward is the caller's responsibility (the connector's input
    rotation is preserved).
    """
    rx_lo = min(region.x1, region.x2)
    rx_hi = max(region.x1, region.x2)
    ry_lo = min(region.y1, region.y2)
    ry_hi = max(region.y1, region.y2)
    cx_mid = (rx_lo + rx_hi) / 2.0
    cy_mid = (ry_lo + ry_hi) / 2.0
    edge = rules.edge_clr

    out: dict[str, tuple[float, float]] = {}
    for c in comps:
        if _is_connector(c):
            band = _assigned_edge(c)
            if band == "L":
                out[c.ref] = (rx_lo + c.w / 2.0 + edge, cy_mid)
            elif band == "R":
                out[c.ref] = (rx_hi - c.w / 2.0 - edge, cy_mid)
            elif band == "B":
                out[c.ref] = (cx_mid, ry_lo + c.h / 2.0 + edge)
            elif band == "T":
                out[c.ref] = (cx_mid, ry_hi - c.h / 2.0 - edge)
            else:
                out[c.ref] = (c.cx, c.cy)
        elif c.fixed:
            out[c.ref] = (c.cx, c.cy)
    return out


def _pin_for_legalize(
    comps: list[PlaceComp],
    fixed_pos: dict[str, tuple[float, float]],
) -> list[PlaceComp]:
    """Shallow copies of ``comps`` with pinned parts marked ``fixed``.

    The legalizer absorbs no shove into fixed parts, so flagging the
    pinned connectors / fixed parts keeps them seated at their edge band
    while the movable parts spread around them. The originals are left
    untouched (the polish still treats only truly-fixed parts as fixed).
    """
    out: list[PlaceComp] = []
    for c in comps:
        if c.fixed or c.ref in fixed_pos:
            out.append(
                PlaceComp(
                    ref=c.ref, w=c.w, h=c.h, cx=c.cx, cy=c.cy,
                    layer=c.layer, fixed=True, rotation=c.rotation,
                    pins=c.pins, rotatable=c.rotatable,
                )
            )
        else:
            out.append(c)
    return out


def _grow_region(region: BoardRegion, rules: DesignRules) -> BoardRegion:
    """Enlarge the board ~10% on its shorter dimension, grid-snapped.

    Growing by a single grid step recovers from a too-small board far
    too slowly, so each closure iteration adds a meaningful fraction of
    the current span (with a one-grid-step floor).
    """
    grid = max(1.0, rules.grid)
    if region.width <= region.height:
        inc = max(grid, math.ceil(0.1 * region.width / grid) * grid)
        return BoardRegion(region.x1, region.y1, region.x2 + inc, region.y2)
    inc = max(grid, math.ceil(0.1 * region.height / grid) * grid)
    return BoardRegion(region.x1, region.y1, region.x2, region.y2 + inc)


def _build_placements(
    comps: list[PlaceComp],
    pos: dict[str, list[float]],
    rotations: dict[str, float],
    sides: dict[str, int],
    rules: DesignRules,
    region: BoardRegion,
) -> dict[str, Placement]:
    """Snap, clamp, and back-rotate centroids into Altium origins.

    ``new_origin = centroid - R(rotation) * C0`` where ``C0`` is the
    part's centroid-from-origin offset at rotation 0. ``C0`` is read off
    the component when the adapter provides it (``c0x`` / ``c0y``);
    absent that, the origin coincides with the centroid.
    """
    rx_lo = min(region.x1, region.x2)
    rx_hi = max(region.x1, region.x2)
    ry_lo = min(region.y1, region.y2)
    ry_hi = max(region.y1, region.y2)
    grid = rules.grid

    out: dict[str, Placement] = {}
    for c in comps:
        x, y = pos[c.ref]
        rot = rotations.get(c.ref, c.rotation)
        side = sides.get(c.ref, 1)
        if not c.fixed:
            ww, hh = _eff_wh(c, rot)
            x = _clamp(_snap(x, grid), rx_lo + ww / 2.0, rx_hi - ww / 2.0)
            y = _clamp(_snap(y, grid), ry_lo + hh / 2.0, ry_hi - hh / 2.0)
        c0x = float(getattr(c, "c0x", 0.0) or 0.0)
        c0y = float(getattr(c, "c0y", 0.0) or 0.0)
        rdx, rdy = _rotate_offset(c0x, c0y, rot)
        out[c.ref] = Placement(
            x=x - rdx,
            y=y - rdy,
            rotation=rot,
            side=side,
        )
    return out
