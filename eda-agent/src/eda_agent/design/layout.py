# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Placement orchestrator + audits + motif splat + rotation pass.

``compute_layout`` is the public entry point. It branches:

- Plans with input/output anchor roles -> Sugiyama layered placement
  (signal flow left-to-right by construction; see ``sugiyama.py``).
- Plans without anchors -> force-directed placement (constellation
  graphs without clear flow; see ``force_directed.py``).

Both paths run through a unified rotation pass, hard-shove overlap
repair, and motif splat (canonical sub-circuit layouts).

The audit helpers (``audit_overlaps``, ``audit_wire_crossings``) are
Python-side equivalents of the live Altium audit; they let tests
measure layout quality without a bridge round-trip.

The spring solver, shove, and shared placement primitives
(``PlacedPart``, sheet constants, ``_bbox_half``, ``_mass``,
``_pin_count_per_part``, ``_rotation_for_part``) live in
``force_directed.py`` and are re-exported here for compatibility
with existing callers.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from eda_agent.design.force_directed import (
    PlacedPart,
    SHEET_MAX_X_MILS,
    SHEET_MAX_Y_MILS,
    SHEET_ORIGIN_X_MILS,
    SHEET_ORIGIN_Y_MILS,
    SNAP_GRID_MILS,
    _BBOX_HALF_2PIN_MILS,
    _SHOVE_CLEARANCE_MILS,
    _bbox_half,
    _force_directed_layout,
    _hard_shove_pass,
    _is_series_flow_part,
    _mass,
    _pin_count_per_part,
    _rotation_for_part,
)
from eda_agent.design.motifs import (
    Match,
    MOTIF_CATALOGUE,
    Motif,
    get_motif_by_name,
    recognize_motifs,
)
from eda_agent.design.plan import DesignPlan, Net, Part, Zone
from eda_agent.design.router import _segment_crosses_rect
from eda_agent.design.sugiyama import has_anchors, sugiyama_layout


def audit_overlaps(
    plan: DesignPlan,
    placed: list[PlacedPart],
) -> list[tuple[str, str]]:
    """Return overlapping (refdes_a, refdes_b) pairs using solver bboxes.

    Public helper used by tests and (optionally) the executor to verify
    the post-shove layout is clean. Uses the SAME ``_bbox_half`` sizing
    the force-directed solver and shove pass use, so a non-empty result
    here is the definitive failure indicator.
    """
    pin_count = _pin_count_per_part(plan)
    bbox_half = {r: _bbox_half(pin_count.get(r, 2)) for r in pin_count}
    out: list[tuple[str, str]] = []
    for i in range(len(placed)):
        for j in range(i + 1, len(placed)):
            a = placed[i]
            b = placed[j]
            if a.sheet != b.sheet:
                continue
            ha = bbox_half[a.refdes]
            hb = bbox_half[b.refdes]
            # Strict inequality: a kissing pair (delta == sum) is NOT
            # an overlap. Mirrors the audit-side semantics where touch
            # alone is acceptable post-grid-snap.
            if abs(a.x_mils - b.x_mils) < (ha + hb) and abs(a.y_mils - b.y_mils) < (ha + hb):
                out.append((a.refdes, b.refdes))
    return out


def audit_wire_crossings(
    plan: DesignPlan,
    placed: list[PlacedPart],
    wires: list[tuple[int, int, int, int]],
) -> list[tuple[str, tuple[int, int, int, int]]]:
    """Python-side equivalent of the Altium ``audit_schematic`` wire-
    crossings check, runnable in tests without a live bridge.

    Returns ``(refdes, segment)`` pairs for every (component_bbox,
    wire_segment) intersection where the wire crosses the BODY of a
    component that doesn't own either endpoint. An empty result means
    the router produced a clean layout for the given placement.

    The bbox sizing matches ``_bbox_half`` (the same envelope the
    placement solver, shove, and motif-splat collision check use), so
    a violation here is the same thing the live audit would flag.
    """
    pin_count = _pin_count_per_part(plan)
    bbox_half = {r: _bbox_half(pin_count.get(r, 2)) for r in pin_count}
    sheet_by_refdes = {p.refdes: p.sheet for p in placed}
    bboxes_by_sheet: dict[str, list[tuple[str, int, int, int, int]]] = defaultdict(list)
    for p in placed:
        half = bbox_half.get(p.refdes, _BBOX_HALF_2PIN_MILS)
        bboxes_by_sheet[p.sheet].append((
            p.refdes,
            p.x_mils - half, p.y_mils - half,
            p.x_mils + half, p.y_mils + half,
        ))

    violations: list[tuple[str, tuple[int, int, int, int]]] = []
    for seg in wires:
        sx1, sy1, sx2, sy2 = seg
        # No sheet hint on wire segments; assume the default sheet for
        # the offline audit. Future: tag wires by sheet.
        for sheet, bboxes in bboxes_by_sheet.items():
            for refdes, rx1, ry1, rx2, ry2 in bboxes:
                # Skip the owner: any obstacle that contains either
                # endpoint can be the pin's home part. A wire MUST
                # exit / enter the component it connects to.
                if rx1 <= sx1 <= rx2 and ry1 <= sy1 <= ry2:
                    continue
                if rx1 <= sx2 <= rx2 and ry1 <= sy2 <= ry2:
                    continue
                if _segment_crosses_rect(
                    sx1, sy1, sx2, sy2, rx1, ry1, rx2, ry2
                ):
                    violations.append((refdes, seg))
    return violations


def _splat_motifs(
    plan: DesignPlan,
    placed: list[PlacedPart],
    matches: list[Match],
) -> list[PlacedPart]:
    """Override non-anchor motif component positions to canonical offsets.

    For each motif match, pick the first component in
    ``motif.canonical`` iteration order as the anchor (it keeps whatever
    position force-directed + shove gave it). The remaining motif
    components are moved to ``anchor_pos + canonical_offset_diff`` so
    the motif reads as the canonical drawing (vertical divider, RC
    chain with cap below R, ...) instead of an emergent FD cluster.

    Collision-aware: if applying a motif's canonical offsets would put
    any of its components inside the bbox of a non-motif part, that
    motif's splat is skipped and its components keep their FD
    positions. Dense plans where canonical positions can't fit fall
    back gracefully rather than producing overlaps.

    Future B.3 will make shove motif-group-aware so the cluster moves
    together when crowded instead of skipping splat.
    """
    by_refdes = {p.refdes: p for p in placed}
    pin_count = _pin_count_per_part(plan)
    for match in matches:
        motif = get_motif_by_name(match.motif_name)
        if motif is None or not motif.canonical:
            continue

        # Find motif origin in absolute coords.
        #
        # IC-anchored motif: origin is the IC's placed position; the IC
        # itself is NOT moved (other motifs may reference it too).
        # Self-contained motif: origin = first canonical-mapped part's
        # placed position minus its canonical offset; that part stays
        # where FD put it and the others splat relative to it.
        anchor_skip: Optional[str] = None
        if motif.ic_anchor is not None:
            ic_host = match.host_refdes(motif.ic_anchor)
            if ic_host is None or ic_host not in by_refdes:
                continue
            ic_part = by_refdes[ic_host]
            meta_x = ic_part.x_mils
            meta_y = ic_part.y_mils
            anchor_skip = None  # the IC isn't in canonical, nothing to skip
            # Single-component IC-anchored motifs are still meaningful
            # (boot_cap has just one canonical entry).
            if not motif.canonical:
                continue
        else:
            if len(motif.canonical) < 2:
                continue  # one-component self-contained motif: no-op
            anchor_pat: Optional[str] = None
            anchor_host: Optional[str] = None
            for pat_refdes in motif.canonical:
                host = match.host_refdes(pat_refdes)
                if host is not None and host in by_refdes:
                    anchor_pat = pat_refdes
                    anchor_host = host
                    break
            if anchor_pat is None or anchor_host is None:
                continue
            anchor_part = by_refdes[anchor_host]
            ax_off, ay_off = motif.canonical[anchor_pat]
            meta_x = anchor_part.x_mils - ax_off
            meta_y = anchor_part.y_mils - ay_off
            anchor_skip = anchor_pat

        # Compute target positions at the current meta-anchor; if
        # they collide, try small shifts of the anchor before giving
        # up. The shifts let a motif still land in canonical form
        # when an FD-placed singleton happens to occupy the
        # canonical slot -- common in dense plans.
        motif_members = match.components
        # The anchor IC is a motif member, but its bbox is REAL estate the
        # canonical offsets must respect: a sheet-edge clamp inside
        # _targets_at can drag a satellite part back INTO the IC, and
        # skipping members in the collision check made that invisible
        # (measured: catch diode clamped from y=700 to y=1000 landed
        # inside the regulator's courtyard, cascading shove distortions
        # across the whole sheet). Check the anchor with the same
        # courtyard metric audit_overlaps uses; other members stay
        # exempt (intra-motif hugging is intentional).
        ic_anchor_host: Optional[str] = None
        if motif.ic_anchor is not None:
            ic_anchor_host = match.host_refdes(motif.ic_anchor)

        def _targets_at(mx: int, my: int) -> dict[str, tuple[int, int]]:
            t: dict[str, tuple[int, int]] = {}
            for pat_refdes, (dx, dy) in motif.canonical.items():
                if pat_refdes == anchor_skip:
                    continue
                host = match.host_refdes(pat_refdes)
                if host is None or host not in by_refdes:
                    continue
                raw_x = mx + dx
                raw_y = my + dy
                sx = int((raw_x // SNAP_GRID_MILS) * SNAP_GRID_MILS)
                sy = int((raw_y // SNAP_GRID_MILS) * SNAP_GRID_MILS)
                sx = max(SHEET_ORIGIN_X_MILS, min(SHEET_MAX_X_MILS, sx))
                sy = max(SHEET_ORIGIN_Y_MILS, min(SHEET_MAX_Y_MILS, sy))
                t[host] = (sx, sy)
            return t

        def _has_collisions(t: dict[str, tuple[int, int]]) -> bool:
            for host, (tx, ty) in t.items():
                host_half = _bbox_half(pin_count.get(host, 2))
                for other_refdes, other in by_refdes.items():
                    if other_refdes in motif_members:
                        # The anchor IC still counts as an obstacle (see
                        # comment above); other members are exempt.
                        if other_refdes != ic_anchor_host or \
                                other_refdes == host:
                            continue
                    other_half = _bbox_half(pin_count.get(other_refdes, 2))
                    gap = host_half + other_half + _SHOVE_CLEARANCE_MILS
                    if (abs(tx - other.x_mils) < gap
                            and abs(ty - other.y_mils) < gap):
                        return True
            return False

        # Shift candidates: (0, 0) first; then 4 axis shifts, then
        # diagonal shifts, smallest displacement first so the motif stays
        # visually associated with its anchor / IC. The 300-600 tail
        # exists for the anchor-IC escape case: a sheet-edge clamp can
        # park a satellite inside the IC courtyard, and the escape is a
        # few hundred mils along the open axis.
        shifts = [
            (0, 0),
            (100, 0), (-100, 0), (0, 100), (0, -100),
            (200, 0), (-200, 0), (0, 200), (0, -200),
            (100, 100), (-100, 100), (100, -100), (-100, -100),
            (200, 200), (-200, 200), (200, -200), (-200, -200),
            (300, 0), (-300, 0), (0, 300), (0, -300),
            (400, 0), (-400, 0), (0, 400), (0, -400),
            (300, 300), (-300, 300), (300, -300), (-300, -300),
            (600, 0), (-600, 0), (0, 600), (0, -600),
        ]
        targets: Optional[dict[str, tuple[int, int]]] = None
        for sx_shift, sy_shift in shifts:
            cand = _targets_at(meta_x + sx_shift, meta_y + sy_shift)
            if not _has_collisions(cand):
                targets = cand
                break

        if targets is None:
            continue  # no clean shift; leave motif at FD positions

        for host, (sx, sy) in targets.items():
            old = by_refdes[host]
            by_refdes[host] = PlacedPart(
                refdes=old.refdes,
                sheet=old.sheet,
                x_mils=sx,
                y_mils=sy,
                rotation=old.rotation,
            )

    return list(by_refdes.values())


def _signal_neighbours(plan: DesignPlan) -> dict[str, set[str]]:
    """For each part, the set of other parts sharing a signal net.

    Power and ground nets are excluded because they connect everything
    indirectly and would dominate the neighbour-direction calculation.
    """
    nbrs: dict[str, set[str]] = defaultdict(set)
    for net in plan.nets:
        if net.is_power or net.is_ground:
            continue
        refs = list(dict.fromkeys(p.refdes for p in net.pins))
        for r1 in refs:
            for r2 in refs:
                if r1 != r2:
                    nbrs[r1].add(r2)
    return nbrs


def _neighbour_aware_rotation(
    refdes: str,
    placed_pos: dict[str, tuple[int, int]],
    neighbours: set[str],
) -> int:
    """Pick horizontal (0) or vertical (270) for a 2-pin signal part.

    Sums |dx| vs |dy| from this part to every placed neighbour.
    If neighbours are mostly above/below the part, rotation 270
    (vertical) keeps the part's pins facing them. Otherwise rotation
    0 (horizontal).

    Uses 270 rather than 90 to match the existing library convention
    (some 2-pin passive libraries have pin 1 on the right in
    native orientation; 270 swings pin 1 to the top).
    """
    dx_sum = 0
    dy_sum = 0
    my_x, my_y = placed_pos[refdes]
    for nb in neighbours:
        if nb not in placed_pos:
            continue
        nx, ny = placed_pos[nb]
        dx_sum += abs(nx - my_x)
        dy_sum += abs(ny - my_y)
    return 270 if dy_sum > dx_sum else 0


_INPUT_CONN_ROLES = frozenset({"input_conn", "vin_conn", "power_in", "input"})
_OUTPUT_CONN_ROLES = frozenset({"output_conn", "vout_conn", "power_out", "output"})


def _apply_rotations(
    plan: DesignPlan,
    placed: list[PlacedPart],
) -> list[PlacedPart]:
    """Decide rotation for every placed part using net topology and
    placed-neighbour positions. Used by both Sugiyama and FD paths.

    Rules (in priority order):
      - **Connector role**: parts with ``role`` in the input-conn set
        (input_conn, vin_conn, power_in, input) get rotation 0 so
        their pins face RIGHT into the schematic from the left edge.
        Parts in the output-conn set get rotation 180 so their pins
        face LEFT into the schematic from the right edge. Connectors
        belong horizontal -- they're the sheet's I/O edge, not
        vertical rail-attached passives.
      - Parts with >=3 pins (no connector role): rotation=0
        (functional pin layout means pins live on left/right of body
        per discipline rule 13).
      - 2-pin part on a power or ground rail: rotation=270 (vertical,
        rail-up convention).
      - 2-pin signal-only part: H (0) or V (270) based on whether
        connected signal neighbours are mostly horizontal or vertical
        relative to the part's placed position.
      - Anything else (single-pin parts, parts with no net coverage):
        rotation=0.

    Library-aware refinement (read the placed pin coordinates and
    flip when pin polarity ends up inverted) is a future improvement;
    this is the geometry-only stage.
    """
    placed_pos: dict[str, tuple[int, int]] = {
        p.refdes: (p.x_mils, p.y_mils) for p in placed
    }
    pin_count = _pin_count_per_part(plan)
    parts_by_refdes = {p.refdes: p for p in plan.parts}
    neighbours = _signal_neighbours(plan)

    out: list[PlacedPart] = []
    for p in placed:
        part = parts_by_refdes.get(p.refdes)
        if part is None:
            out.append(p)
            continue

        role = (part.role or "").strip().lower()
        if role in _INPUT_CONN_ROLES:
            # Input connector at left edge of sheet -> pins face right
            # into the schematic interior. Some terminal-block library
            # parts have pins on the LEFT in their native orientation,
            # so rotation 180 swings the pins around to face right.
            rotation = 180
        elif role in _OUTPUT_CONN_ROLES:
            # Output connector at right edge -> pins face left into
            # the schematic interior. Rotation 0 keeps the library
            # native pin direction (pins on the left of the body).
            rotation = 0
        else:
            # _rotation_for_part handles the rail-attached 2-pin case
            # (returns 270) and falls back to 0 otherwise.
            rotation = _rotation_for_part(part, plan.nets)
            # For 2-pin signal-only parts (rotation came back as 0),
            # refine using neighbour positions. Series power-flow parts
            # (inductor/ferrite/fuse in the rail path) are EXEMPT: their
            # 0 is the deliberate horizontal convention, and the
            # neighbour heuristic was re-verticalizing the buck's
            # inductor whenever its partners sat above/below.
            if (rotation == 0 and pin_count.get(p.refdes, 0) == 2
                    and not _is_series_flow_part(part, plan.nets)):
                rotation = _neighbour_aware_rotation(
                    p.refdes, placed_pos, neighbours[p.refdes]
                )

        out.append(
            PlacedPart(
                refdes=p.refdes,
                sheet=p.sheet,
                x_mils=p.x_mils,
                y_mils=p.y_mils,
                rotation=rotation,
            )
        )
    return out


def order_rail_columns(
    plan: DesignPlan,
    placed: list[PlacedPart],
) -> list[PlacedPart]:
    """Reorder stacked 2-pin rail parts so column potential is monotonic.

    The convention every professional schematic follows: within a vertical
    stack, potential DESCENDS top to bottom -- the part touching the power
    rail sits at the top of the column, mid-potential (signal-to-signal)
    parts in the middle, ground-connected parts at the bottom. Neither
    Sugiyama nor the motif splat enforces this, so a divider's filter cap
    (MID-GND) can land ABOVE the top resistor (VCC-MID). With the standard
    pin polarity (power pin up, ground pin down) that inversion makes the
    connected pins face AWAY from each other, and the router is forced
    into a wrap-around loop -- the single most visible amateur artefact
    on a small sheet.

    Mechanics: group vertical (rotation 90/270) 2-pin parts by exact
    (sheet, x) column; rank each part by the strongest rail it touches
    (power=2, neither=1, ground-only=0); reassign the SAME y-slots in
    rank order (stable: equal ranks keep their current top-to-bottom
    order). Positions are permuted, never invented, so column occupancy
    and sheet extent are unchanged. Parts with other rotations, more
    pins, or connector roles are left alone.
    """
    pin_count = _pin_count_per_part(plan)
    part_by_refdes = {p.refdes: p for p in plan.parts}

    touches_power: set[str] = set()
    touches_ground: set[str] = set()
    for net in plan.nets:
        for pr in net.pins:
            if net.is_power:
                touches_power.add(pr.refdes)
            if net.is_ground:
                touches_ground.add(pr.refdes)

    def rank(refdes: str) -> int:
        if refdes in touches_power:
            return 2
        if refdes in touches_ground:
            return 0
        return 1

    def eligible(p: PlacedPart) -> bool:
        if pin_count.get(p.refdes, 0) != 2:
            return False
        if p.rotation % 180 != 90:  # vertical parts only (90 / 270)
            return False
        part = part_by_refdes.get(p.refdes)
        role = ((part.role or "") if part else "").strip().lower()
        return role not in _INPUT_CONN_ROLES and role not in _OUTPUT_CONN_ROLES

    columns: dict[tuple[str, int], list[PlacedPart]] = defaultdict(list)
    for p in placed:
        if eligible(p):
            columns[(p.sheet, p.x_mils)].append(p)

    new_y: dict[str, int] = {}
    for col_parts in columns.values():
        if len(col_parts) < 2:
            continue
        top_down = sorted(col_parts, key=lambda p: -p.y_mils)
        slots = [p.y_mils for p in top_down]
        want = sorted(top_down, key=lambda p: -rank(p.refdes))  # stable
        if [p.refdes for p in want] == [p.refdes for p in top_down]:
            continue
        for slot_y, p in zip(slots, want):
            new_y[p.refdes] = slot_y

    if not new_y:
        return placed
    return [
        PlacedPart(
            refdes=p.refdes, sheet=p.sheet, x_mils=p.x_mils,
            y_mils=new_y.get(p.refdes, p.y_mils), rotation=p.rotation,
        )
        for p in placed
    ]


def _sugiyama_to_placed(
    plan: DesignPlan,
    ic_pin_offsets: dict[str, dict[str, tuple[int, int]]] | None = None,
) -> list[PlacedPart]:
    """Sugiyama placement adapted to the ``PlacedPart`` type.

    The Sugiyama module returns its own ``SugiyamaPlacement`` records
    so it can stay free of layout.py's dependency on motifs and
    force-directed helpers. We convert to ``PlacedPart`` here so the
    rest of the pipeline (motif splat, executor) sees one type.
    """
    return [
        PlacedPart(
            refdes=s.refdes,
            sheet=s.sheet,
            x_mils=s.x_mils,
            y_mils=s.y_mils,
            rotation=s.rotation,
        )
        for s in sugiyama_layout(plan, ic_pin_offsets)
    ]


def compute_layout(
    plan: DesignPlan, *, engine: str = "auto",
    ic_pin_offsets: dict[str, dict[str, tuple[int, int]]] | None = None,
    pin_attract_k: float | None = None,
) -> list[PlacedPart]:
    """Compute (x, y) for every part in the plan.

    Pipeline:

    1. **Placement.** If the plan has at least one part with an
       input/output anchor role (``input_conn`` / ``vin_conn`` /
       ``output_conn`` / ...), use **Sugiyama layered placement** for
       structural left-to-right signal flow. Otherwise fall back to
       **force-directed** placement (a "constellation" graph with no
       clear DAG structure -- e.g. a 16-channel mux array -- still
       looks reasonable under FD even if Sugiyama would degenerate
       into a single column).

    2. **Overlap repair.** ``_hard_shove_pass`` audits every part
       pair against ``_bbox_half(pin_count)`` and pushes overlapping
       pairs apart along their minimum-overlap axis. Runs for both
       Sugiyama and FD output -- Sugiyama's even-spacing within a
       layer doesn't guarantee bbox separation for high-pin-count
       ICs.

    3. **Motif splat.** ``_splat_motifs`` overrides non-anchor motif
       component positions to the motif's canonical offsets. This is
       what makes a buck's FB divider render as a vertical R-R
       column even if Sugiyama placed the resistors at a slightly
       different y. Collision-aware: skips splat per motif when the
       canonical positions would collide with non-motif parts.

    Output is snapped to a 100-mil grid and clamped to the A4 sheet.
    Rotation stays at 0 (library-native).

    ``engine`` forces the placement core: ``"auto"`` (default) keeps the
    has-anchors heuristic above; ``"sugiyama"`` / ``"force_directed"`` pin it.
    The pipeline's best-of runs both and scores them, because neither wins
    everywhere -- sugiyama is cleaner on a connected signal chain, while
    force-directed (whose spring graph uses ALL nets, power included) handles a
    board whose signal graph is split by a power-only bridge (a regulator).
    """
    _fd_kw = {} if pin_attract_k is None else {"pin_attract_k": pin_attract_k}
    if engine == "force_directed":
        placed = _force_directed_layout(plan, ic_pin_offsets, **_fd_kw)
    elif engine == "sugiyama":
        placed = _sugiyama_to_placed(plan, ic_pin_offsets)
    elif has_anchors(plan):
        placed = _sugiyama_to_placed(plan, ic_pin_offsets)
    else:
        placed = _force_directed_layout(plan, ic_pin_offsets, **_fd_kw)

    # Unified rotation pass: pick H/V per part from net topology +
    # neighbour positions. Both Sugiyama and FD return rotation=0,
    # this is where the schematic-direction convention is applied.
    placed = _apply_rotations(plan, placed)

    cleaned, _residual = _hard_shove_pass(plan, placed)
    matches = recognize_motifs(plan)
    if matches:
        cleaned = _splat_motifs(plan, cleaned, matches)
    return cleaned
