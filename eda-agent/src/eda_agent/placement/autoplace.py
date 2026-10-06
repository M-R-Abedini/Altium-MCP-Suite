# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Force-directed PCB placement solver (pure Python, no Altium).

See the package docstring in :mod:`eda_agent.placement` for the overall
design. This module is the algorithm: dataclasses for the inputs, the
HPWL / overlap metrics, the spring-repulsion global-placement loop, and
the deterministic hard-shove legalization pass.

All coordinates are component **centroids** in mils. The MCP tool layer
is responsible for converting between a component's Altium origin
(``Comp.x``/``Comp.y``) and its bounding-box centroid before and after
calling :func:`plan_placement`.

Determinism: the solver never uses ``random``. It seeds from the
supplied centroids; coincident components are nudged apart by a small
deterministic offset derived from their sorted order, so repeated runs
on the same input give identical output.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlacePin:
    """One pin of a component, as a local offset + its net.

    ``lx``/``ly`` are the pin's position relative to the component
    centroid when the part is at rotation 0 (the footprint frame), in
    mils. ``net`` is the Altium net name. The solver rotates these
    offsets to score candidate orientations.
    """

    lx: float
    ly: float
    net: str


@dataclass
class PlaceComp:
    """One placeable component.

    ``cx``/``cy`` are the current bounding-box centroid in mils.
    ``w``/``h`` are the footprint bounding-box width/height in mils.
    ``layer`` partitions collision checking -- only components on the
    same layer can physically overlap. ``fixed`` components never move
    (connectors, mounting holes, anything the caller pins), but they
    still attract their nets and block others.
    """

    ref: str
    w: float
    h: float
    cx: float
    cy: float
    layer: str = "Top"
    fixed: bool = False
    # Current orientation in degrees (0/90/180/270). Used as the base
    # for rotation optimization and for back-computing pin offsets.
    rotation: float = 0.0
    # Per-pin local offsets at rotation 0 (footprint frame), each tagged
    # with its net. Empty => the solver treats this part as a point at
    # its centroid (pure XY placement; rotation never changes).
    pins: tuple[PlacePin, ...] = ()
    # Whether the solver may re-orient this part. Requires pins and an
    # orthogonal current rotation; the tool layer sets this.
    rotatable: bool = False


@dataclass
class PlaceNet:
    """One net as the set of components it touches.

    ``refs`` are component designators (deduplicated by the caller is
    fine; the solver dedups defensively). ``weight`` scales the net's
    pull -- a caller can down-weight power/ground rails so they do not
    dominate, though the solver already normalizes by net degree.
    """

    refs: tuple[str, ...]
    name: str = ""
    weight: float = 1.0


@dataclass
class BoardRegion:
    """Axis-aligned placement region in mils (board outline bounds)."""

    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return abs(self.x2 - self.x1)

    @property
    def height(self) -> float:
        return abs(self.y2 - self.y1)


@dataclass
class PlaceOptions:
    """Tunable solver parameters with sensible PCB defaults."""

    iterations: int = 400
    grid_mils: float = 5.0
    # Minimum copper-to-copper breathing room added on top of the two
    # half-extents when testing overlap / computing min separation.
    clearance_mils: float = 15.0
    spring_k: float = 0.08          # attraction toward net centroid
    repel_k: float = 0.30           # near-field push-apart strength
    repel_cutoff_mils: float = 0.0  # 0 => derived from region size
    boundary_k: float = 0.40        # pull back inside the board region
    cooling: float = 0.99
    damping: float = 0.85
    max_shove_rounds: int = 80
    # When True, ignore the seed centroids and lay components out on a
    # fresh grid first (a full re-place rather than a refinement).
    reseed_grid: bool = False
    # Keep fixed components pinned at their input centroid.
    respect_fixed: bool = True
    # Optimize component orientation (0/90/180/270) for parts marked
    # ``rotatable``. Requires per-pin geometry; pinless parts are never
    # rotated. ``rotation_sweeps`` bounds the Gauss-Seidel passes.
    optimize_rotation: bool = True
    rotation_sweeps: int = 4


@dataclass
class PlaceResult:
    """Solver output.

    ``positions`` maps ref -> final centroid ``(cx, cy)``. ``moved``
    maps ref -> centroid delta ``(dx, dy)`` from the input. Metrics are
    reported before and after so the caller can judge whether to apply.
    """

    positions: dict[str, tuple[float, float]]
    moved: dict[str, tuple[float, float]]
    hpwl_before: float
    hpwl_after: float
    overlap_pairs_before: int
    overlap_pairs_after: int
    iterations: int
    # Final orientation per component (degrees). Equals each part's
    # input rotation unless the optimizer re-oriented it.
    rotations: dict[str, float] = field(default_factory=dict)
    # Components whose orientation changed.
    rotated: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #

def hpwl(
    positions: dict[str, tuple[float, float]],
    nets: list[PlaceNet],
) -> float:
    """Half-perimeter wirelength over all nets.

    For each net, take the bounding box of its members' centroids and
    add (width + height) * weight. The standard analytic-placement
    proxy for routed length: cheap, differentiable-ish, and a good
    relative quality signal. Nets with fewer than two placed members
    contribute nothing.
    """
    total = 0.0
    for net in nets:
        xs: list[float] = []
        ys: list[float] = []
        seen: set[str] = set()
        for ref in net.refs:
            if ref in seen or ref not in positions:
                continue
            seen.add(ref)
            x, y = positions[ref]
            xs.append(x)
            ys.append(y)
        if len(xs) < 2:
            continue
        total += ((max(xs) - min(xs)) + (max(ys) - min(ys))) * net.weight
    return total


def _aabb_overlap(
    aw: float,
    ah: float,
    bw: float,
    bh: float,
    ax: float,
    ay: float,
    bx: float,
    by: float,
    clearance: float,
) -> tuple[float, float] | None:
    """Return per-axis overlap amounts (ox, oy) > 0, or None if clear.

    Takes each component's effective width/height (already rotated, if
    applicable) plus a shared clearance. Only the caller decides whether
    the two are on the same layer.
    """
    need_x = (aw + bw) / 2.0 + clearance
    need_y = (ah + bh) / 2.0 + clearance
    ox = need_x - abs(ax - bx)
    oy = need_y - abs(ay - by)
    if ox <= 0.0 or oy <= 0.0:
        return None
    return ox, oy


def _eff_dims(
    comp: PlaceComp,
    rotations: dict[str, float] | None,
) -> tuple[float, float]:
    """Effective (w, h) for collision given the part's chosen rotation.

    A 90/270 step relative to the part's input orientation swaps the
    footprint's bounding-box width and height. The input ``w``/``h`` are
    the AABB at ``comp.rotation``, so we swap only on an odd quarter-turn
    delta from that base.
    """
    if not rotations:
        return comp.w, comp.h
    delta = (rotations.get(comp.ref, comp.rotation) - comp.rotation) % 180
    if abs(delta - 90) < 1e-6:
        return comp.h, comp.w
    return comp.w, comp.h


def overlap_pair_count(
    comps: list[PlaceComp],
    positions: dict[str, tuple[float, float]],
    clearance: float = 0.0,
    rotations: dict[str, float] | None = None,
) -> int:
    """Count same-layer component pairs whose bounding boxes overlap.

    When ``rotations`` is given, each part's bounding box is taken at its
    chosen orientation (90/270 swaps width and height).
    """
    n = len(comps)
    count = 0
    for i in range(n):
        a = comps[i]
        ax, ay = positions[a.ref]
        aw, ah = _eff_dims(a, rotations)
        for j in range(i + 1, n):
            b = comps[j]
            if a.layer != b.layer:
                continue
            bx, by = positions[b.ref]
            bw, bh = _eff_dims(b, rotations)
            if _aabb_overlap(aw, ah, bw, bh, ax, ay, bx, by, clearance) is not None:
                count += 1
    return count


# --------------------------------------------------------------------------- #
# Rotation + pin-level wirelength
# --------------------------------------------------------------------------- #

# The only orientations the optimizer considers. PCB parts are placed on
# orthogonal angles in the overwhelming majority of designs; non-ortho
# placement is rare and left untouched (rotatable=False).
_ORIENTATIONS = (0.0, 90.0, 180.0, 270.0)


def _rotate_offset(lx: float, ly: float, deg: float) -> tuple[float, float]:
    """Rotate a local pin offset by an orthogonal angle (CCW, mils)."""
    d = int(round(deg)) % 360
    if d == 0:
        return lx, ly
    if d == 90:
        return -ly, lx
    if d == 180:
        return -lx, -ly
    if d == 270:
        return ly, -lx
    # Non-orthogonal fallback (not used by the optimizer): exact rotation.
    rad = math.radians(deg)
    c = math.cos(rad)
    s = math.sin(rad)
    return lx * c - ly * s, lx * s + ly * c


def _net_points(
    comps: list[PlaceComp],
    positions: dict[str, tuple[float, float]],
    rotations: dict[str, float],
    nets: list[PlaceNet],
) -> dict[str, list[tuple[float, float]]]:
    """Collect each net's connection points, scoped to ``nets``.

    For every member of a net, use its pin geometry on THAT net (rotated
    to the member's chosen orientation) when available; otherwise fall
    back to the member's centroid. Keyed by net name; only nets with two
    or more points are returned. Strictly net-scoped so callers can score
    a single component's nets without the rest of the board leaking in
    (and so a pinless design reduces to the plain centroid HPWL).
    """
    by_ref = {c.ref: c for c in comps}
    # (ref, net) -> list of local pin offsets at rotation 0.
    pin_index: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for c in comps:
        for p in c.pins:
            pin_index.setdefault((c.ref, p.net), []).append((p.lx, p.ly))

    pts: dict[str, list[tuple[float, float]]] = {}
    for net in nets:
        pl: list[tuple[float, float]] = []
        for ref in dict.fromkeys(net.refs):
            if ref not in positions:
                continue
            cx, cy = positions[ref]
            locs = pin_index.get((ref, net.name))
            if locs:
                base = by_ref[ref].rotation if ref in by_ref else 0.0
                rot = rotations.get(ref, base)
                for lx, ly in locs:
                    dx, dy = _rotate_offset(lx, ly, rot)
                    pl.append((cx + dx, cy + dy))
            else:
                pl.append((cx, cy))
        if len(pl) >= 2:
            pts[net.name] = pl
    return pts


def pin_hpwl(
    comps: list[PlaceComp],
    positions: dict[str, tuple[float, float]],
    rotations: dict[str, float],
    nets: list[PlaceNet],
) -> float:
    """Pin-aware HPWL: bounding box over true pin coordinates per net.

    Falls back to centroids for pinless parts (see :func:`_net_points`),
    so a design with no pin geometry yields the same value as the plain
    centroid :func:`hpwl`.
    """
    weight = {n.name: n.weight for n in nets}
    pts = _net_points(comps, positions, rotations, nets)
    total = 0.0
    for name, pl in pts.items():
        if len(pl) < 2:
            continue
        xs = [p[0] for p in pl]
        ys = [p[1] for p in pl]
        total += ((max(xs) - min(xs)) + (max(ys) - min(ys))) * weight.get(name, 1.0)
    return total


def _optimize_rotations(
    comps: list[PlaceComp],
    positions: dict[str, tuple[float, float]],
    nets: list[PlaceNet],
    options: PlaceOptions,
    rotations: dict[str, float],
) -> None:
    """Greedy Gauss-Seidel orientation assignment (in place).

    For each rotatable part, pick the orientation in ``_ORIENTATIONS``
    that minimizes the HPWL of the nets it touches, holding every other
    part fixed. Sweeps until stable or ``rotation_sweeps`` is hit.
    """
    by_ref = {c.ref: c for c in comps}
    # net name -> the rotatable parts that touch it (for local scoring).
    nets_of: dict[str, list[PlaceNet]] = {}
    for n in nets:
        nets_of.setdefault(n.name, []).append(n)
    rotatable = [
        c for c in comps
        if c.rotatable and c.pins and not (options.respect_fixed and c.fixed)
    ]
    if not rotatable:
        return

    def _cost_for(part: PlaceComp, trial_rot: float) -> float:
        # Sum HPWL of just the nets this part's pins touch, with the part
        # at trial_rot. Other parts keep their current rotation.
        touched = {p.net for p in part.pins}
        sub = [n for name in touched for n in nets_of.get(name, [])]
        saved = rotations[part.ref]
        rotations[part.ref] = trial_rot
        cost = pin_hpwl(comps, positions, rotations, sub)
        rotations[part.ref] = saved
        return cost

    for _ in range(max(1, options.rotation_sweeps)):
        changed = False
        for part in rotatable:
            best_rot = rotations[part.ref]
            best_cost = _cost_for(part, best_rot)
            for rot in _ORIENTATIONS:
                if rot == best_rot:
                    continue
                cost = _cost_for(part, rot)
                if cost < best_cost - 1e-6:
                    best_cost = cost
                    best_rot = rot
            if best_rot != rotations[part.ref]:
                rotations[part.ref] = best_rot
                changed = True
        if not changed:
            break


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #

def _deterministic_nudge(index: int) -> tuple[float, float]:
    """A small reproducible offset to break exact coincidence.

    Spirals outward by index so co-located seeds separate without any
    randomness. Magnitude is tiny (sub-mil to a few mils) -- just
    enough to give the spring/repulsion forces a direction.
    """
    angle = (index * 2.399963)  # golden-angle radians, deterministic
    radius = 0.5 + 0.25 * index
    return (radius * math.cos(angle), radius * math.sin(angle))


def _seed_positions(
    comps: list[PlaceComp],
    region: BoardRegion,
    options: PlaceOptions,
) -> dict[str, list[float]]:
    """Initial centroids: current positions (refined) or a fresh grid."""
    if options.reseed_grid:
        movable = [c for c in comps if not (options.respect_fixed and c.fixed)]
        fixed = [c for c in comps if options.respect_fixed and c.fixed]
        pos: dict[str, list[float]] = {c.ref: [c.cx, c.cy] for c in fixed}
        n = len(movable)
        if n:
            cols = max(1, int(math.ceil(math.sqrt(n))))
            rows = int(math.ceil(n / cols))
            margin = max(c.w for c in movable) / 2.0 + options.clearance_mils
            usable_w = max(1.0, region.width - 2 * margin)
            usable_h = max(1.0, region.height - 2 * margin)
            x0 = min(region.x1, region.x2) + margin
            y0 = min(region.y1, region.y2) + margin
            pitch_x = usable_w / max(1, cols - 1) if cols > 1 else 0.0
            pitch_y = usable_h / max(1, rows - 1) if rows > 1 else 0.0
            for i, c in enumerate(movable):
                col = i % cols
                row = i // cols
                pos[c.ref] = [x0 + col * pitch_x, y0 + row * pitch_y]
        return pos

    # Refinement: seed from current centroids, nudging exact duplicates.
    pos = {}
    seen: dict[tuple[float, float], int] = {}
    for idx, c in enumerate(sorted(comps, key=lambda c: c.ref)):
        key = (round(c.cx, 3), round(c.cy, 3))
        if key in seen:
            dx, dy = _deterministic_nudge(seen[key])
            seen[key] += 1
            pos[c.ref] = [c.cx + dx, c.cy + dy]
        else:
            seen[key] = 1
            pos[c.ref] = [c.cx, c.cy]
    return pos


# --------------------------------------------------------------------------- #
# Global placement (spring + repulsion)
# --------------------------------------------------------------------------- #

def _net_pairs_and_weights(
    nets: list[PlaceNet],
    present: set[str],
) -> list[tuple[str, str, float]]:
    """Star-model spring pairs.

    For each net, connect every member to the net's (running) centroid.
    We model that as member-to-member springs normalized by degree so a
    50-pin power net does not overwhelm a 2-pin signal net. To keep it
    O(degree) rather than O(degree^2) we connect each member to the
    *first* member of the net (a star hub), which has the same
    centroid-pulling effect at a fraction of the cost.
    """
    pairs: list[tuple[str, str, float]] = []
    for net in nets:
        members = [r for r in dict.fromkeys(net.refs) if r in present]
        deg = len(members)
        if deg < 2:
            continue
        # Normalize so total pull per net is comparable regardless of
        # fan-out; star hub is members[0].
        w = net.weight / (deg - 1)
        hub = members[0]
        for r in members[1:]:
            pairs.append((hub, r, w))
    return pairs


def _global_place(
    comps: list[PlaceComp],
    nets: list[PlaceNet],
    region: BoardRegion,
    options: PlaceOptions,
    pos: dict[str, list[float]],
) -> None:
    """In-place spring/repulsion relaxation with cooling."""
    by_ref = {c.ref: c for c in comps}
    refs = [c.ref for c in comps]
    present = set(refs)
    movable = {
        c.ref for c in comps if not (options.respect_fixed and c.fixed)
    }
    if not movable:
        return

    pairs = _net_pairs_and_weights(nets, present)

    cutoff = options.repel_cutoff_mils
    if cutoff <= 0.0:
        cutoff = max(region.width, region.height) * 0.5 + 1.0
    cutoff2 = cutoff * cutoff

    rx_lo = min(region.x1, region.x2)
    rx_hi = max(region.x1, region.x2)
    ry_lo = min(region.y1, region.y2)
    ry_hi = max(region.y1, region.y2)

    velocity = {r: [0.0, 0.0] for r in refs}
    temp = max(region.width, region.height) * 0.10 + 1.0

    n = len(refs)
    for _ in range(max(0, options.iterations)):
        force = {r: [0.0, 0.0] for r in refs}

        # Spring attraction along net star-pairs.
        for a, b, w in pairs:
            pa = pos[a]
            pb = pos[b]
            dx = pb[0] - pa[0]
            dy = pb[1] - pa[1]
            f = options.spring_k * w
            force[a][0] += f * dx
            force[a][1] += f * dy
            force[b][0] -= f * dx
            force[b][1] -= f * dy

        # Pairwise repulsion (same layer only). Near field: linear push
        # to clear bbox overlap; far field: mild 1/r^2 spread.
        for i in range(n):
            a = by_ref[refs[i]]
            pa = pos[a.ref]
            for j in range(i + 1, n):
                b = by_ref[refs[j]]
                if a.layer != b.layer:
                    continue
                pb = pos[b.ref]
                dx = pb[0] - pa[0]
                dy = pb[1] - pa[1]
                dist2 = dx * dx + dy * dy
                if dist2 > cutoff2:
                    continue
                dist = math.sqrt(dist2) + 1e-6
                min_sep = (
                    (a.w + b.w + a.h + b.h) / 4.0 + options.clearance_mils
                )
                if dist < min_sep:
                    f = (min_sep - dist) * options.repel_k
                else:
                    f = (min_sep * min_sep) / dist2 * options.repel_k * 0.05
                ux = dx / dist
                uy = dy / dist
                force[a.ref][0] -= f * ux
                force[a.ref][1] -= f * uy
                force[b.ref][0] += f * ux
                force[b.ref][1] += f * uy

        # Boundary: pull each centroid so its bbox stays inside region.
        for c in comps:
            p = pos[c.ref]
            hw = c.w / 2.0
            hh = c.h / 2.0
            if p[0] < rx_lo + hw:
                force[c.ref][0] += (rx_lo + hw - p[0]) * options.boundary_k
            elif p[0] > rx_hi - hw:
                force[c.ref][0] -= (p[0] - (rx_hi - hw)) * options.boundary_k
            if p[1] < ry_lo + hh:
                force[c.ref][1] += (ry_lo + hh - p[1]) * options.boundary_k
            elif p[1] > ry_hi - hh:
                force[c.ref][1] -= (p[1] - (ry_hi - hh)) * options.boundary_k

        # Integrate (fixed components stay put). Temperature caps the
        # per-step displacement so the system cannot explode.
        for r in refs:
            if r not in movable:
                velocity[r][0] = 0.0
                velocity[r][1] = 0.0
                continue
            vx = (velocity[r][0] + force[r][0]) * options.damping
            vy = (velocity[r][1] + force[r][1]) * options.damping
            velocity[r][0] = vx
            velocity[r][1] = vy
            step = math.hypot(vx, vy)
            if step > temp:
                scale = temp / step
                vx *= scale
                vy *= scale
            pos[r][0] += vx
            pos[r][1] += vy

        temp *= options.cooling


# --------------------------------------------------------------------------- #
# Legalization (deterministic hard shove)
# --------------------------------------------------------------------------- #

def _clamp(value: float, lo: float, hi: float) -> float:
    if lo > hi:
        return (lo + hi) / 2.0
    return max(lo, min(hi, value))


def _hard_shove(
    comps: list[PlaceComp],
    region: BoardRegion,
    options: PlaceOptions,
    pos: dict[str, list[float]],
    rotations: dict[str, float] | None = None,
) -> int:
    """Push overlapping same-layer pairs apart along the cheaper axis.

    Deterministic Jacobi shove: each round, every overlapping pair
    contributes a separation along its minimum-overlap axis, split
    evenly between the two components (fixed components absorb none of
    the push -- the movable side takes it all). Bounded by
    ``max_shove_rounds``; returns the residual overlap count. Bounding
    boxes honor each part's chosen rotation when ``rotations`` is given.
    """
    by_ref = {c.ref: c for c in comps}
    refs = [c.ref for c in comps]
    dims = {c.ref: _eff_dims(c, rotations) for c in comps}
    movable = {
        c.ref for c in comps if not (options.respect_fixed and c.fixed)
    }
    rx_lo = min(region.x1, region.x2)
    rx_hi = max(region.x1, region.x2)
    ry_lo = min(region.y1, region.y2)
    ry_hi = max(region.y1, region.y2)
    clr = options.clearance_mils

    n = len(refs)
    residual = 0
    for _ in range(max(1, options.max_shove_rounds)):
        delta = {r: [0.0, 0.0] for r in refs}
        overlaps = 0
        for i in range(n):
            a = by_ref[refs[i]]
            ax, ay = pos[a.ref]
            aw, ah = dims[a.ref]
            for j in range(i + 1, n):
                b = by_ref[refs[j]]
                if a.layer != b.layer:
                    continue
                bx, by = pos[b.ref]
                bw, bh = dims[b.ref]
                ovl = _aabb_overlap(aw, ah, bw, bh, ax, ay, bx, by, clr)
                if ovl is None:
                    continue
                overlaps += 1
                ox, oy = ovl
                a_move = a.ref in movable
                b_move = b.ref in movable
                if not a_move and not b_move:
                    continue
                # Split: if only one side can move, it takes the full push.
                if a_move and b_move:
                    fa = fb = 0.5
                elif a_move:
                    fa, fb = 1.0, 0.0
                else:
                    fa, fb = 0.0, 1.0
                # Separate along the cheaper (smaller-overlap) axis.
                if ox <= oy:
                    sign = 1.0 if (bx - ax) >= 0 else -1.0
                    if (bx - ax) == 0:
                        sign = 1.0 if a.ref < b.ref else -1.0
                    delta[a.ref][0] -= sign * ox * fa
                    delta[b.ref][0] += sign * ox * fb
                else:
                    sign = 1.0 if (by - ay) >= 0 else -1.0
                    if (by - ay) == 0:
                        sign = 1.0 if a.ref < b.ref else -1.0
                    delta[a.ref][1] -= sign * oy * fa
                    delta[b.ref][1] += sign * oy * fb

        for c in comps:
            if c.ref not in movable:
                continue
            ew, eh = dims[c.ref]
            hw = ew / 2.0
            hh = eh / 2.0
            nx = pos[c.ref][0] + delta[c.ref][0]
            ny = pos[c.ref][1] + delta[c.ref][1]
            pos[c.ref][0] = _clamp(nx, rx_lo + hw, rx_hi - hw)
            pos[c.ref][1] = _clamp(ny, ry_lo + hh, ry_hi - hh)

        residual = overlaps
        if overlaps == 0:
            break
    return residual


def _snap(value: float, grid: float) -> float:
    if grid <= 0:
        return value
    return round(value / grid) * grid


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def plan_placement(
    comps: list[PlaceComp],
    nets: list[PlaceNet],
    region: BoardRegion,
    options: PlaceOptions | None = None,
) -> PlaceResult:
    """Compute an improved placement for ``comps`` within ``region``.

    Returns a :class:`PlaceResult` with final centroids, per-component
    deltas, and HPWL / overlap metrics before and after. Pure function:
    does not mutate its inputs.

    Edge cases: an empty component set, or a set with a single
    component, returns the input unchanged with zero metrics.
    """
    options = options or PlaceOptions()
    notes: list[str] = []

    if not comps:
        return PlaceResult({}, {}, 0.0, 0.0, 0, 0, 0, notes=["no components"])

    # Defensive: dedup refs (last one wins) so positions is well-defined.
    seen_refs: dict[str, PlaceComp] = {}
    for c in comps:
        if c.ref in seen_refs:
            notes.append(f"duplicate ref {c.ref!r}; keeping last")
        seen_refs[c.ref] = c
    comps = list(seen_refs.values())

    start = {c.ref: (c.cx, c.cy) for c in comps}
    start_rot = {c.ref: c.rotation for c in comps}
    hpwl_before = pin_hpwl(comps, start, start_rot, nets)
    overlap_before = overlap_pair_count(comps, start, clearance=0.0)

    if len(comps) < 2:
        return PlaceResult(
            positions=dict(start),
            moved={c.ref: (0.0, 0.0) for c in comps},
            hpwl_before=hpwl_before,
            hpwl_after=hpwl_before,
            overlap_pairs_before=overlap_before,
            overlap_pairs_after=overlap_before,
            iterations=0,
            rotations=dict(start_rot),
            rotated={},
            notes=notes + ["fewer than 2 components; nothing to optimize"],
        )

    pos = _seed_positions(comps, region, options)
    _global_place(comps, nets, region, options, pos)

    # Orientation pass: choose each rotatable part's angle to shorten its
    # nets, then legalize with rotation-aware bounding boxes.
    rotations = dict(start_rot)
    if options.optimize_rotation:
        _optimize_rotations(comps, pos, nets, options, rotations)

    residual = _hard_shove(comps, region, options, pos, rotations)
    if residual:
        notes.append(
            f"{residual} overlapping pair(s) remain after legalization "
            f"(board may be too small for the component set)"
        )

    # Snap to grid, then a final clamp so snapping cannot push a part
    # off the board.
    rx_lo = min(region.x1, region.x2)
    rx_hi = max(region.x1, region.x2)
    ry_lo = min(region.y1, region.y2)
    ry_hi = max(region.y1, region.y2)
    by_ref = {c.ref: c for c in comps}
    final: dict[str, tuple[float, float]] = {}
    for ref, (x, y) in pos.items():
        c = by_ref[ref]
        if options.respect_fixed and c.fixed:
            final[ref] = start[ref]
            continue
        sx = _snap(x, options.grid_mils)
        sy = _snap(y, options.grid_mils)
        ew, eh = _eff_dims(c, rotations)
        hw = ew / 2.0
        hh = eh / 2.0
        sx = _clamp(sx, rx_lo + hw, rx_hi - hw)
        sy = _clamp(sy, ry_lo + hh, ry_hi - hh)
        final[ref] = (sx, sy)

    hpwl_after = pin_hpwl(comps, final, rotations, nets)
    overlap_after = overlap_pair_count(comps, final, clearance=0.0, rotations=rotations)
    moved = {
        ref: (final[ref][0] - start[ref][0], final[ref][1] - start[ref][1])
        for ref in final
    }
    rotated = {
        ref: rotations[ref]
        for ref in rotations
        if abs(rotations[ref] - start_rot[ref]) > 1e-6
    }

    return PlaceResult(
        positions=final,
        moved=moved,
        hpwl_before=hpwl_before,
        hpwl_after=hpwl_after,
        overlap_pairs_before=overlap_before,
        overlap_pairs_after=overlap_after,
        iterations=options.iterations,
        rotations=dict(rotations),
        rotated=rotated,
        notes=notes,
    )
