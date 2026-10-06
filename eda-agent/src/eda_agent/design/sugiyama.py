# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""From-scratch Sugiyama-style layered graph drawing for schematics.

A pure-Python implementation of the Sugiyama framework (Sugiyama,
Tagawa & Toda 1981) adapted for circuit schematics. No third-party
layout dependencies; everything is written under Apache-2.0.

Pipeline:

1. **Build the layered graph.** Each Part is a node. Each non-power /
   non-ground Net contributes adjacency edges between its pins'
   components (clique expansion -- ``C(k, 2)`` edges per ``k``-pin
   net). Power and ground nets are excluded: they're connectivity only
   and would otherwise dominate the spring/distance structure. The
   port glyphs from Stage 6 of the synthesis carry their topology.

2. **Identify anchors.** Parts whose ``role`` is in ``_INPUT_ROLES``
   (input connector, VIN, power-in) pin to layer 0. Parts in
   ``_OUTPUT_ROLES`` (output connector, VOUT, power-out) pin to the
   maximum layer. This is what gives the schematic left-to-right
   signal flow as a STRUCTURAL property, not an emergent one.

3. **Layer assignment via BFS from input anchors.** Each part's layer
   is its BFS hop count from the nearest input anchor. Unanchored
   parts disconnected from any input land in a middle layer. Output
   anchors override to ``max_layer + 1``.

4. **Crossing reduction (barycenter sweep).** Each layer's within-
   layer order is iteratively refined: each part's barycenter is the
   mean position of its connected neighbours in the adjacent layer;
   sort by barycenter. Sweep down then up, repeat until stable or for
   a fixed number of iterations.

5. **Coordinate assignment (even spacing).** Layers are spaced evenly
   across the sheet width; within each layer, parts are spaced evenly
   across the sheet height. This produces the canonical "columns
   from left to right" look. Brandes-Köpf coordinate refinement
   (aligning dummy nodes for smooth bends) is a future polish: even
   spacing is enough for a v1 that's structurally correct.

Skipped relative to the synthesis recommendation (Phase C polish):

- Cycle removal via explicit FAS. We use undirected BFS for layering,
  so cycles don't break the algorithm -- direction is implicit in BFS
  distance. Asymmetric edge weighting for feedback paths is a future
  refinement.
- Brandes-Köpf coordinate assignment. Even spacing is the v1 stand-in.
- Port-side selection from neighbour direction. Rotation stays 0
  (library-native); the port-aware variant is Phase C.2.
- Rail banding (VCC top, GND bottom) via in-layer constraints.

The output is a list of placements with the same shape as
``layout.PlacedPart`` so the rest of the pipeline (motif splat, executor)
consumes it identically.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Optional

import networkx as nx

from eda_agent.design.canvas import sheet_dimensions
from eda_agent.design.force_directed import _bbox_half, _pin_count_per_part
from eda_agent.design.plan import DesignPlan


# Sheet geometry (mirrors layout.py constants -- A4 landscape with a
# 1000-mil margin all round).
SHEET_ORIGIN_X_MILS = 1000
SHEET_ORIGIN_Y_MILS = 1000
SHEET_MAX_X_MILS = 10500
SHEET_MAX_Y_MILS = 7500
SNAP_GRID_MILS = 100

# Layer/row spacing within the sheet. These are CEILINGS: the actual
# spacing is size-aware (derived from the bbox of the parts actually in
# each column, plus a wiring channel) and never exceeds these values,
# so a column of 0402 passives packs ~1300 mils from its neighbour
# while a column holding a 100-pin MCU keeps the full berth. Fixed
# pitches here were the root cause of the "8 parts scattered over
# 3000x7000 mils" spread. Both axes still shrink further to fit the
# sheet ("available / count") when a design is large.
LAYER_SPACING_MILS = 2000   # max x-step between layers
ROW_SPACING_MILS = 1400     # max y-step between rows within one layer
COLUMN_CHANNEL_MILS = 400   # wiring channel kept clear between columns
ROW_SLACK_MILS = 100        # extra grid step between stacked bboxes

# Column folding: a layer with more than this many rows of SMALL parts
# (passives / 3-pin, never ICs) folds into two sub-columns, halving the
# tower height. A 5-part timing chain as one 5000-mil column was the
# single worst shape in the benchmark renders -- professional sheets
# fold such chains into a 2-D block.
FOLD_MAX_ROWS = 4


_INPUT_ROLES = frozenset({"input_conn", "vin_conn", "power_in", "input"})
_OUTPUT_ROLES = frozenset({"output_conn", "vout_conn", "power_out", "output"})

# Rail-banding bias factor for the barycenter sweep. A part with one
# more ground pin than power pins gets its within-layer position
# nudged down by this many slots. Small enough to let neighbour
# connectivity drive the primary ordering; large enough to break
# ties and steer ambiguous cases.
_RAIL_BIAS_FACTOR = 0.5


@dataclass(frozen=True)
class SugiyamaPlacement:
    """One part's computed placement from Sugiyama.

    Same geometry contract as ``layout.PlacedPart`` (mils, snapped to
    a 100-mil grid, clamped to the A4 sheet) with two extra fields
    -- ``layer`` (x-axis bucket) and ``row`` (y-axis position within
    layer) -- so callers can reason about the structural placement
    independent of the absolute (x, y).
    """

    refdes: str
    sheet: str
    x_mils: int
    y_mils: int
    rotation: int = 0
    layer: int = 0
    row: int = 0


def _signal_edges(plan: DesignPlan) -> list[tuple[str, str]]:
    """Component-component adjacency from non-power, non-ground nets.

    Power and ground are connectivity-only in schematics (port glyphs
    carry them) and would dominate layering if included -- every
    bypass cap would be adjacent to every other part touching VCC.

    Edges are deduplicated and canonical-ordered (lo, hi) per pair so
    two nets sharing the same component pair don't produce both
    ``(R1, R2)`` and ``(R2, R1)`` for downstream consumers. NetworkX
    deduplicates internally so the prior list-of-duplicates was
    functionally fine, but the smaller deterministic list is cheaper
    for the barycenter sweep's adjacency build.
    """
    edges: set[tuple[str, str]] = set()
    for net in plan.nets:
        if net.is_power or net.is_ground:
            continue
        # Unique refdes (a 2-pin part appearing twice on a net counts
        # as one node, not a self-loop).
        refdes_list = list(dict.fromkeys(p.refdes for p in net.pins))
        for i in range(len(refdes_list)):
            for j in range(i + 1, len(refdes_list)):
                a, b = refdes_list[i], refdes_list[j]
                edges.add((a, b) if a < b else (b, a))
    return list(edges)


def _anchor_role(part_role: Optional[str]) -> Optional[str]:
    """Return ``"input"``, ``"output"``, or ``None`` per part role."""
    if part_role is None:
        return None
    if part_role in _INPUT_ROLES:
        return "input"
    if part_role in _OUTPUT_ROLES:
        return "output"
    return None


def _structural_anchor(
    plan: DesignPlan,
    edges: list[tuple[str, str]],
) -> Optional[str]:
    """A synthetic input anchor for plans with no role-tagged anchor.

    Many plans (zone-based, no explicit input/output roles) would fall
    back to force-directed, which wiggles a clean signal chain out of
    order. When the signal graph has a flow endpoint -- a degree-1 node --
    BFS layering from it lays the graph out left-to-right (a chain becomes
    a straight ordered ladder). Picks the lowest-refdes endpoint for
    determinism.

    Returns ``None`` (keep force-directed) when:
      - there are no signal edges, or
      - the flow doesn't span MOST parts. A board with a short signal
        chain plus many power-only parts (decaps on VCC/GND carry no
        signal edge) would dump those isolated parts into one middle
        layer where they overlap; force-directed spreads them cleanly.
        The largest signal-connected component must cover >=60% of parts
        (and at least 3) before Sugiyama is worthwhile, or
      - that component is a ring/mesh with no degree-1 endpoint.
    """
    if not edges:
        return None
    G = nx.Graph()
    G.add_nodes_from(p.refdes for p in plan.parts)
    G.add_edges_from(edges)
    largest = max(nx.connected_components(G), key=len, default=set())
    if len(largest) < 3 or len(largest) < 0.6 * len(plan.parts):
        return None
    leaves = sorted(n for n in largest if G.degree(n) == 1)
    return leaves[0] if leaves else None


def _assign_layers(
    plan: DesignPlan,
    edges: list[tuple[str, str]],
) -> dict[str, int]:
    """BFS layering from input anchors; output anchors pinned to max+1.

    Parts not reachable from any input anchor land in a middle layer
    (so the result is still a coherent column structure). Plans with
    no anchors at all degenerate to layer 0 for every part -- caller
    should treat that as a signal to fall back to force-directed.
    """
    G = nx.Graph()
    G.add_nodes_from(p.refdes for p in plan.parts)
    G.add_edges_from(edges)

    inputs: list[str] = []
    outputs: list[str] = []
    for p in plan.parts:
        role = _anchor_role(p.role)
        if role == "input":
            inputs.append(p.refdes)
        elif role == "output":
            outputs.append(p.refdes)

    # A power-only anchor (a 2-pin VIN connector whose nets are all
    # power/ground, say) has no signal edges: BFS from it discovers
    # nothing, the disconnected-middle rule then computes middle=0, and
    # EVERY part collapses into a single layer-0 column. Only anchors
    # that actually touch the signal graph may seed the BFS; isolated
    # anchors keep their column by decree afterwards (inputs leftmost,
    # outputs rightmost).
    iso_inputs = [r for r in inputs if r not in G or G.degree(r) == 0]
    iso_outputs = [r for r in outputs if r not in G or G.degree(r) == 0]
    inputs = [r for r in inputs if r in G and G.degree(r) > 0]
    outputs = [r for r in outputs if r in G and G.degree(r) > 0]

    # No connected role anchor: seed the BFS from a structural flow
    # endpoint so a chain/tree still lays out left-to-right instead of
    # degenerating.
    if not inputs and not outputs:
        anchor = _structural_anchor(plan, edges)
        if anchor is not None:
            inputs = [anchor]

    layer: dict[str, int] = {}

    if inputs:
        # BFS forward from every input anchor; layer = hop distance.
        for r in inputs:
            layer[r] = 0
        queue: deque[tuple[str, int]] = deque((r, 0) for r in inputs)
        while queue:
            node, dist = queue.popleft()
            for neighbour in G.neighbors(node):
                if neighbour in layer:
                    continue
                layer[neighbour] = dist + 1
                queue.append((neighbour, dist + 1))
    elif outputs:
        # Output-anchored only: BFS BACKWARD from outputs, then flip so
        # outputs land at the rightmost layer (max) and their neighbours
        # one column left. Without this, _assign_layers used to dump
        # every part on layer 0 (single-column degenerate result) when
        # only output_conn anchors were present.
        for r in outputs:
            layer[r] = 0
        queue = deque((r, 0) for r in outputs)
        while queue:
            node, dist = queue.popleft()
            for neighbour in G.neighbors(node):
                if neighbour in layer:
                    continue
                layer[neighbour] = dist + 1
                queue.append((neighbour, dist + 1))
        if layer:
            max_l = max(layer.values())
            layer = {r: max_l - l for r, l in layer.items()}

    # Parts disconnected from any input anchor get assigned to the
    # middle layer of whatever's been discovered. Avoids unanchored
    # parts piling up at one extreme.
    max_layer = max(layer.values(), default=0)
    middle = max_layer // 2 if max_layer > 0 else 0
    for p in plan.parts:
        if p.refdes not in layer:
            layer[p.refdes] = middle

    # Output anchors land in the rightmost layer. Pin them to the
    # larger of (their natural BFS distance, the max layer reached by
    # any non-output part). The old rule always added a fresh layer
    # past max_layer, which wasted a column when the output anchor
    # was already at the BFS frontier (typical chain layouts like
    # J1 -> R1 -> R2 -> J2: J2 naturally lands at layer 3, no need
    # to promote it to 4 with no other parts in column 3).
    if outputs:
        output_set = set(outputs)
        other_max = max(
            (l for r, l in layer.items() if r not in output_set),
            default=-1,
        )
        output_bfs_max = max(
            (l for r, l in layer.items() if r in output_set),
            default=0,
        )
        target_layer = max(other_max, output_bfs_max)
        for r in outputs:
            layer[r] = target_layer

    # Signal-isolated role anchors (filtered from the BFS seeds above)
    # land on their decreed edge: inputs leftmost, outputs rightmost.
    for r in iso_inputs:
        layer[r] = 0
    if iso_outputs:
        rightmost = max(layer.values(), default=0)
        for r in iso_outputs:
            layer[r] = rightmost

    return layer


def _rail_bias(plan: DesignPlan) -> dict[str, int]:
    """Per-part rail-banding bias: positive = ground-leaning, negative = power-leaning.

    Counts power-net pins and ground-net pins per refdes. Returns
    ``ground_pins - power_pins`` per part. Used as a small additive
    bias in the barycenter sweep so power-attached parts gravitate
    toward the top of their layer (low y) and ground-attached parts
    toward the bottom (high y) -- the canonical "power up, ground
    down" schematic convention.

    A part with no rail pins gets bias 0 and is ordered purely by
    its signal-neighbour barycenter.
    """
    bias: dict[str, int] = defaultdict(int)
    for net in plan.nets:
        if net.is_power:
            for p in net.pins:
                bias[p.refdes] -= 1
        elif net.is_ground:
            for p in net.pins:
                bias[p.refdes] += 1
    return dict(bias)


def _barycenter_of(
    refdes: str,
    adj: dict[str, set[str]],
    ref_pos: dict[str, int],
    default: float,
    rail_bias: dict[str, int],
) -> float:
    """Mean index in the reference layer + rail-banding nudge.

    The barycenter is the average position of ``refdes``'s neighbours
    in the adjacent reference layer (the value used for crossing
    reduction). The rail-banding nudge shifts the result up (power)
    or down (ground) so that within an ambiguous tie -- e.g. two
    bypass caps on the same rail with equivalent neighbour structure
    -- the power-attached one comes out on top.
    """
    ns = [ref_pos[n] for n in adj[refdes] if n in ref_pos]
    base = sum(ns) / len(ns) if ns else default
    return base + _RAIL_BIAS_FACTOR * rail_bias.get(refdes, 0)


def _barycenter_sweep(
    layers: dict[int, list[str]],
    edges: list[tuple[str, str]],
    rail_bias: Optional[dict[str, int]] = None,
    iterations: int = 24,
) -> dict[int, list[str]]:
    """Iterative barycenter sweep to reduce edge crossings between layers.

    Sweep down (fix layer L, reorder L+1 by barycenter in L), then up
    (fix L+1, reorder L). Repeat ``iterations`` times. The algorithm
    converges quickly in practice; 24 iterations is well past the
    point of diminishing returns for schematic-sized graphs.

    ``rail_bias`` (optional): per-part bias from ``_rail_bias``. When
    supplied, the barycenter is shifted slightly so power-attached
    parts gravitate toward low position indices (top of layer) and
    ground-attached parts toward high indices (bottom). Pass ``None``
    or empty dict to disable rail banding.
    """
    adj: dict[str, set[str]] = defaultdict(set)
    for u, v in edges:
        adj[u].add(v)
        adj[v].add(u)

    bias = rail_bias or {}
    layer_indices = sorted(layers)

    for _ in range(iterations):
        # Snapshot before this round so we can early-exit on convergence.
        # Small graphs typically converge in <5 rounds; without the
        # check the loop pays for all 24.
        snapshot = {l: tuple(layers[l]) for l in layers}

        # Down sweep.
        for i in range(len(layer_indices) - 1):
            ref = layers[layer_indices[i]]
            ref_pos = {r: idx for idx, r in enumerate(ref)}
            default = len(ref) / 2
            tgt = layers[layer_indices[i + 1]]
            tgt.sort(
                key=lambda r: _barycenter_of(r, adj, ref_pos, default, bias)
            )

        # Up sweep.
        for i in range(len(layer_indices) - 1, 0, -1):
            ref = layers[layer_indices[i]]
            ref_pos = {r: idx for idx, r in enumerate(ref)}
            default = len(ref) / 2
            tgt = layers[layer_indices[i - 1]]
            tgt.sort(
                key=lambda r: _barycenter_of(r, adj, ref_pos, default, bias)
            )

        if all(tuple(layers[l]) == snapshot[l] for l in layers):
            break  # converged; no order changed this round

    return layers


def _layout_max(plan: DesignPlan) -> tuple[int, int]:
    """Usable layout bounds (max_x, max_y) from the (first) sheet's size.

    A bigger sheet => more room, so a large design SPREADS instead of cramming
    its rows below comfortable spacing (the cause of routing shorts on dense
    sheets). A4 reproduces the historical 10500x7500 exactly; the margins are
    the same absolute frame->bounds offsets as A4. Multi-sheet plans use the
    first sheet's size (the layout is one coordinate space).
    """
    size = plan.sheets[0].size if plan.sheets else "A4"
    frame_w, frame_h = sheet_dimensions(size)
    max_x = frame_w - (11500 - SHEET_MAX_X_MILS)
    max_y = frame_h - (7600 - SHEET_MAX_Y_MILS)
    return max_x, max_y


def _assign_coordinates(
    plan: DesignPlan,
    by_layer: dict[int, list[str]],
) -> list[SugiyamaPlacement]:
    """Even-spaced (x, y) for every part.

    Layers are spaced across the sheet width (capped at
    ``LAYER_SPACING_MILS``); within each layer parts are spaced
    across the sheet height. Both axes snap to ``SNAP_GRID_MILS``.

    Within-layer spacing uses ``min(ROW_SPACING_MILS, sheet_height //
    n)`` -- the simple fit-the-sheet formula. Wider per-layer spacing
    based on the biggest part's bbox would be more canonical, but the
    interaction with shove's wall-clamping produces residual overlaps
    on the buck plan that shove can't fully repair. Until shove gains
    layer-aware wall handling, even spacing remains the v1 stand-in.

    NOTE: a priority-method y-alignment refinement (slide each node to its
    neighbours' median to straighten wires; Sugiyama-Tagawa-Toda 1981) was
    trialled here and REJECTED -- see ``_align`` notes in the module memory.
    Forced, it is a proxy trap: it trades routed wirelength for crossings
    inconsistently (helped an asymmetric fan and a 33-part hub on the real
    ``total``, but tilted already-straight lone-node chains and raised both
    crossings and wirelength on those). No sugiyama-local gate distinguishes
    the cases faithfully because the node-centre edge-span proxy disagrees
    with the routed score. The faithful version is Brandes-Koepf's balanced
    median-of-four-alignments (keeps a node centred when its up/down pulls
    conflict, which is exactly the straight-chain case the crude method
    breaks); that is a larger, separately-validated change.
    """
    layer_indices = sorted(by_layer)
    if not layer_indices:
        return []

    max_x, max_y = _layout_max(plan)
    sheet_width = max_x - SHEET_ORIGIN_X_MILS
    sheet_height = max_y - SHEET_ORIGIN_Y_MILS
    n_layers = len(layer_indices)

    # Size-aware column pitch: each gap is the two adjacent columns'
    # widest half-bboxes plus a wiring channel, capped at the legacy
    # LAYER_SPACING_MILS (never wider than before, tighter when the
    # columns hold small parts). Uniform per column pair -- per-PART
    # spacing was trialled and rejected (shove wall-clamp interaction,
    # see docstring). If the cumulative width overflows the sheet, all
    # gaps scale down proportionally (the legacy fit behaviour).
    pin_count = _pin_count_per_part(plan)
    half_w: dict[int, int] = {}
    for l in layer_indices:
        half_w[l] = max(
            (_bbox_half(pin_count.get(r, 2)) for r in by_layer[l]),
            default=_bbox_half(2),
        )

    # Fold decision per layer: many rows, all small parts. fold_dx is the
    # second sub-column's x offset; the layer's effective width for the
    # column-gap computation grows by it.
    folded: dict[int, bool] = {}
    fold_dx: dict[int, int] = {}
    for l in layer_indices:
        small = half_w[l] <= _bbox_half(3)
        folded[l] = small and len(by_layer[l]) > FOLD_MAX_ROWS
        fold_dx[l] = (2 * half_w[l] + COLUMN_CHANNEL_MILS // 2) if folded[l] else 0

    gaps: list[int] = []
    for prev, cur in zip(layer_indices, layer_indices[1:]):
        ideal = (half_w[prev] + fold_dx[prev]
                 + half_w[cur] + COLUMN_CHANNEL_MILS)
        gaps.append(min(LAYER_SPACING_MILS + fold_dx[prev], ideal))
    total_width = sum(gaps)
    if total_width > sheet_width and total_width > 0:
        scale = sheet_width / total_width
        gaps = [max(SNAP_GRID_MILS, int(g * scale)) for g in gaps]

    layer_x: dict[int, int] = {}
    x = SHEET_ORIGIN_X_MILS
    for i, l in enumerate(layer_indices):
        if i > 0:
            x += gaps[i - 1]
        layer_x[l] = (x // SNAP_GRID_MILS) * SNAP_GRID_MILS

    refdes_to_sheet = {p.refdes: p.sheet for p in plan.parts}

    placements: list[SugiyamaPlacement] = []
    for l in layer_indices:
        nodes = by_layer[l]
        n = len(nodes)
        if n == 0:
            continue
        # Size-aware row pitch: enough for the column's tallest bbox
        # plus one grid of slack, capped at the legacy ROW_SPACING_MILS
        # and still shrinking to fit the sheet when the column is long.
        # A folded column advances y every TWO entries (consecutive
        # barycenter rows sit side by side), halving the tower height.
        ideal_step = 2 * half_w[l] + ROW_SLACK_MILS
        n_rows = (n + 1) // 2 if folded[l] else n
        y_step = min(ROW_SPACING_MILS, ideal_step,
                     max(SNAP_GRID_MILS, sheet_height // max(1, n_rows)))
        total_height = (n_rows - 1) * y_step
        y_start = SHEET_ORIGIN_Y_MILS + (sheet_height - total_height) // 2
        for row, refdes in enumerate(nodes):
            if folded[l]:
                y = y_start + (row // 2) * y_step
                x_part = layer_x[l] + (row % 2) * fold_dx[l]
            else:
                y = y_start + row * y_step
                x_part = layer_x[l]
            y_snapped = (y // SNAP_GRID_MILS) * SNAP_GRID_MILS
            placements.append(
                SugiyamaPlacement(
                    refdes=refdes,
                    sheet=refdes_to_sheet.get(refdes, "main"),
                    x_mils=(x_part // SNAP_GRID_MILS) * SNAP_GRID_MILS,
                    y_mils=y_snapped,
                    layer=l,
                    row=row,
                )
            )

    return placements


def _pin_side_adjust(
    plan: DesignPlan,
    layer: dict[str, int],
    ic_pin_offsets: dict[str, dict[str, tuple[int, int]]],
) -> dict[str, int]:
    """Move small parts to the side of the IC their pins actually sit on.

    BFS layering knows hop distance but not symbol geometry, so the 555's
    timing network can land in the column RIGHT of the IC while wiring to
    its LEFT-side pins -- every wire then loops around the IC body. For
    each 2-3 pin part whose signal nets connect to exactly ONE IC with
    known pin offsets, compute the mean x-offset of the IC pins it wires
    to: negative -> the part belongs one column left of the IC, positive
    -> one column right. Layers can go negative here; the caller
    re-normalises to 0-based.
    """
    pin_count: dict[str, int] = {}
    for net in plan.nets:
        for pr in net.pins:
            pin_count[pr.refdes] = pin_count.get(pr.refdes, 0) + 1

    # part -> {ic -> [dx of the IC pins it shares a signal net with]}
    touch: dict[str, dict[str, list[int]]] = {}
    for net in plan.nets:
        if net.is_power or net.is_ground:
            continue
        ic_pins = [(pr.refdes, str(pr.pin)) for pr in net.pins
                   if pr.refdes in ic_pin_offsets]
        others = {pr.refdes for pr in net.pins
                  if pr.refdes not in ic_pin_offsets}
        for ic, pin in ic_pins:
            dx = ic_pin_offsets[ic].get(pin, (0, 0))[0]
            for r in others:
                touch.setdefault(r, {}).setdefault(ic, []).append(dx)

    adjusted = dict(layer)
    for r, ics in touch.items():
        if pin_count.get(r, 0) > 3 or len(ics) != 1:
            continue
        ic, dxs = next(iter(ics.items()))
        if ic not in adjusted or not dxs:
            continue
        mean_dx = sum(dxs) / len(dxs)
        if mean_dx < 0:
            adjusted[r] = adjusted[ic] - 1
        elif mean_dx > 0:
            adjusted[r] = adjusted[ic] + 1

    # Re-normalise to 0-based contiguous-ish layers (negatives allowed
    # above; gaps are harmless, _assign_coordinates iterates sorted keys).
    lo = min(adjusted.values(), default=0)
    if lo < 0:
        adjusted = {r: l - lo for r, l in adjusted.items()}
    return adjusted


def sugiyama_layout(
    plan: DesignPlan,
    ic_pin_offsets: dict[str, dict[str, tuple[int, int]]] | None = None,
) -> list[SugiyamaPlacement]:
    """Compute Sugiyama-style placement for every part in ``plan``.

    Returns one ``SugiyamaPlacement`` per ``plan.parts`` entry.
    Coordinates are snapped to a 100-mil grid and clamped to the A4
    sheet area. ``rotation`` is left at 0 (port-side selection is a
    future stage).

    ``ic_pin_offsets`` (the pipeline's ``_ic_pin_offsets`` shape) enables
    the pin-side adjustment: small parts move to the side of their IC
    that their pins actually sit on. Optional because symbol geometry
    only exists after extraction; the pipeline offers the adjusted layout
    as a scored best-of variant.
    """
    edges = _signal_edges(plan)
    layer = _assign_layers(plan, edges)
    if ic_pin_offsets:
        layer = _pin_side_adjust(plan, layer, ic_pin_offsets)

    by_layer: dict[int, list[str]] = defaultdict(list)
    for refdes, l in layer.items():
        by_layer[l].append(refdes)
    # Deterministic seed for the sweep so the same plan always yields
    # the same layout.
    for l in by_layer:
        by_layer[l].sort()

    bias = _rail_bias(plan)
    by_layer = _barycenter_sweep(by_layer, edges, rail_bias=bias)
    return _assign_coordinates(plan, by_layer)


def has_anchors(plan: DesignPlan) -> bool:
    """True iff Sugiyama layering can establish a left-to-right order.

    That holds when a part carries an input/output role OR -- absent any
    role -- the signal graph has a flow endpoint (a degree-1 node) to seed
    BFS from. A ring/mesh with no endpoint and no role has no clean order,
    so this returns False and the caller falls back to force-directed.
    """
    for p in plan.parts:
        if _anchor_role(p.role) is not None:
            return True
    return _structural_anchor(plan, _signal_edges(plan)) is not None


__all__ = [
    "LAYER_SPACING_MILS",
    "ROW_SPACING_MILS",
    "SHEET_MAX_X_MILS",
    "SHEET_MAX_Y_MILS",
    "SHEET_ORIGIN_X_MILS",
    "SHEET_ORIGIN_Y_MILS",
    "SNAP_GRID_MILS",
    "SugiyamaPlacement",
    "has_anchors",
    "sugiyama_layout",
]
