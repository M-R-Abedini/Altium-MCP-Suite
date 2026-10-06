# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Orthogonal wire routing primitives for schematic generation.

Pure-functional helpers used by the executor to draw Manhattan wires
between pin endpoints. Split out of ``executor.py`` to keep that file
focused on orchestration + Altium IPC.

Layers in this module:

- **Pin stubs** -- ``pin_direction_vector``, ``adaptive_stub_length``,
  ``stub_endpoints``. Compute where the first wire segment leaves a
  pin and how long it extends. Adaptive clipping prevents the stub
  from drawing through a neighbouring component's body when parts
  sit at the placement engine's minimum 950-mil center spacing.

- **Segment-vs-rectangle geometry** -- ``segment_crosses_rect``,
  ``_l_path_collisions``, ``_path_collisions``, ``_path_length``.
  Used by both the router and the offline audit functions.

- **Two-point routing** -- ``route_l_path``, ``route_s_bend``.
  ``route_l_path`` tries both L-orderings and falls back to a
  3-segment S-bend (mid-coordinate from obstacle edges) when both
  L-paths collide.

- **Multi-pin routing** -- ``route_signal_pins``. Picks the cheaper
  of chain (consecutive L-paths in x- or y-sort order) and star
  (every pin to a shared hub) topology, with the hub candidates
  including the centroid, each pin, obstacle-pushed centroids, and
  the four corners of the stub-ends bounding box.

All segments are axis-aligned; the router never emits diagonals.
Coordinates are mils, snapped to a 100-mil grid by the caller.
"""

from __future__ import annotations

from typing import Optional


# ---------------------------------------------------------------------------
# Pin stubs
# ---------------------------------------------------------------------------

# 100-mil stub wire length between a pin's electrical hot end and the net
# label / power port that attaches to it. ERC reports "Floating net labels"
# when a label sits exactly on a pin endpoint without an intervening wire,
# so every label/port gets pulled out along the pin's vector by this much.
_STUB_LEN_MILS = 300
# Minimum stub length when an obstacle clips the default 300-mil
# extension. ERC flags net labels and power ports that sit exactly on
# a pin endpoint, so even a clipped stub must leave SOME room.
_STUB_MIN_LEN_MILS = 100
# Margin between the clipped stub's far end and the closest obstacle
# so the wire doesn't visually kiss the next component's body.
_STUB_CLEARANCE_MILS = 50


def _pin_direction_vector(orientation: int) -> tuple[int, int]:
    """Map Altium's TRotationBy90 pin orientation to a unit vector.

    0=right (+x), 1=up (+y), 2=left (-x), 3=down (-y). Matches what
    Pascal's ``Gen_GetSchComponentPins`` returns from ``Pin.Orientation``.
    Unknown values fall through as (1, 0) so the stub still draws.
    """
    if orientation == 1:
        return (0, 1)
    if orientation == 2:
        return (-1, 0)
    if orientation == 3:
        return (0, -1)
    return (1, 0)


def _adaptive_stub_length(
    pin_x: int,
    pin_y: int,
    dx: int,
    dy: int,
    obstacles: list[tuple[int, int, int, int]],
    base_length: int = _STUB_LEN_MILS,
) -> int:
    """Maximum stub length that doesn't enter another component's bbox.

    Walks from ``(pin_x, pin_y)`` along ``(dx, dy)`` and finds the
    first obstacle (other than the pin's owner bbox) the ray would
    enter. Returns the distance to that obstacle minus a small
    clearance, clipped to ``[_STUB_MIN_LEN_MILS, base_length]``.
    The owner bbox (which contains the pin) is excluded by checking
    "pin inside obstacle" -- the stub may legitimately exit the
    owner's body.

    ``base_length`` defaults to ``_STUB_LEN_MILS`` (300). Callers can
    request a longer base when staggering multiple same-direction
    stubs from the same component so their bends don't share a
    column.
    """
    if (dx, dy) == (0, 0):
        return base_length
    max_len = base_length
    for rx1, ry1, rx2, ry2 in obstacles:
        # Skip the obstacle that contains the pin (owner).
        if rx1 <= pin_x <= rx2 and ry1 <= pin_y <= ry2:
            continue
        # Ray-vs-rectangle entry distance for axis-aligned rays.
        if dx > 0:  # +x
            if pin_y < ry1 or pin_y > ry2 or rx1 <= pin_x:
                continue
            entry = rx1 - pin_x
        elif dx < 0:  # -x
            if pin_y < ry1 or pin_y > ry2 or rx2 >= pin_x:
                continue
            entry = pin_x - rx2
        elif dy > 0:  # +y
            if pin_x < rx1 or pin_x > rx2 or ry1 <= pin_y:
                continue
            entry = ry1 - pin_y
        else:  # dy < 0
            if pin_x < rx1 or pin_x > rx2 or ry2 >= pin_y:
                continue
            entry = pin_y - ry2
        clipped = max(_STUB_MIN_LEN_MILS, entry - _STUB_CLEARANCE_MILS)
        if clipped < max_len:
            max_len = clipped
    return max_len


def _stub_endpoints(
    pin_x: int,
    pin_y: int,
    orientation: int,
    pin_length_mils: int,  # retained for ABI compat; ignored
    obstacles: Optional[list[tuple[int, int, int, int]]] = None,
    extra_length_mils: int = 0,
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Compute (stub_start, stub_end) for a pin.

    ``ISch_Pin.Location`` returns the ELECTRICAL endpoint of the pin
    (the point where a wire attaches), NOT the body-side end. Verified
    empirically: U1 pin 1 BOOT of a placed TPS54331D symbol returned
    location (3500, 4400) when the IC body's left edge was at x=3700;
    the leftmost (electrical) end of the pin graphic at x=3500 is what
    Pin.Location reports.

    The stub starts AT Pin.Location and extends outward along the
    pin's orientation vector. The extension length is
    ``_STUB_LEN_MILS`` (300 mil) by default, plus
    ``extra_length_mils`` for staggering when several same-direction
    stubs leave the same component (so their bends don't share a
    column). The total is then clipped by ``_adaptive_stub_length``
    when an ``obstacles`` list is supplied so the stub doesn't draw
    through a neighbouring component's body at the placement engine's
    minimum 950-mil center spacing.
    """
    dx, dy = _pin_direction_vector(orientation)
    hot_x = pin_x
    hot_y = pin_y
    base = _STUB_LEN_MILS + max(0, extra_length_mils)
    length = (
        _adaptive_stub_length(
            pin_x, pin_y, dx, dy, obstacles, base_length=base
        )
        if obstacles
        else base
    )
    end_x = hot_x + dx * length
    end_y = hot_y + dy * length
    return ((hot_x, hot_y), (end_x, end_y))


# ---------------------------------------------------------------------------
# Segment-vs-rectangle geometry
# ---------------------------------------------------------------------------


def _segment_crosses_rect(
    x1: int, y1: int, x2: int, y2: int,
    rx1: int, ry1: int, rx2: int, ry2: int,
) -> bool:
    """True iff axis-aligned segment (x1,y1)->(x2,y2) crosses the interior
    of the axis-aligned rectangle [rx1,rx2] x [ry1,ry2]. Endpoints sitting
    on the boundary count as crossings only when the segment continues
    into the interior.
    """
    rx1, rx2 = (rx1, rx2) if rx1 <= rx2 else (rx2, rx1)
    ry1, ry2 = (ry1, ry2) if ry1 <= ry2 else (ry2, ry1)
    if y1 == y2:  # horizontal segment
        if y1 <= ry1 or y1 >= ry2:
            return False
        seg_x_lo, seg_x_hi = min(x1, x2), max(x1, x2)
        return seg_x_lo < rx2 and seg_x_hi > rx1
    if x1 == x2:  # vertical segment
        if x1 <= rx1 or x1 >= rx2:
            return False
        seg_y_lo, seg_y_hi = min(y1, y2), max(y1, y2)
        return seg_y_lo < ry2 and seg_y_hi > ry1
    return False  # only orthogonal segments here


def _l_path_collisions(
    x1: int, y1: int, x2: int, y2: int,
    horiz_first: bool,
    obstacles: list[tuple[int, int, int, int]],
    skip_at: tuple[tuple[int, int], ...] = (),
) -> int:
    """Count how many obstacle rects an L-path (x1,y1)->(x2,y2) crosses.

    Obstacles are (rx1, ry1, rx2, ry2) bboxes. Skip rects that contain
    either endpoint (those are the pin's own component or the centroid's
    host part -- wires must enter / leave SOME bbox to connect).
    """
    if horiz_first:
        segs = [(x1, y1, x2, y1), (x2, y1, x2, y2)]
    else:
        segs = [(x1, y1, x1, y2), (x1, y2, x2, y2)]
    n = 0
    for (sx1, sy1, sx2, sy2) in segs:
        for rx1, ry1, rx2, ry2 in obstacles:
            # Skip obstacles owning either endpoint.
            owns_start = rx1 <= x1 <= rx2 and ry1 <= y1 <= ry2
            owns_end = rx1 <= x2 <= rx2 and ry1 <= y2 <= ry2
            if owns_start or owns_end:
                continue
            if (x1, y1) in skip_at or (x2, y2) in skip_at:
                continue
            if _segment_crosses_rect(sx1, sy1, sx2, sy2, rx1, ry1, rx2, ry2):
                n += 1
                break
    return n


def _path_collisions(
    segs: list[tuple[int, int, int, int]],
    obstacles: list[tuple[int, int, int, int]],
    skip_endpoints: tuple[tuple[int, int], ...],
) -> int:
    """Count obstacles crossed by an arbitrary axis-aligned path.

    ``skip_endpoints`` lists points (the pin's home, the centroid) that
    sit inside an obstacle by construction; obstacles containing any
    of those points don't count as a crossing.
    """
    n = 0
    for sx1, sy1, sx2, sy2 in segs:
        for rx1, ry1, rx2, ry2 in obstacles:
            if any(rx1 <= ex <= rx2 and ry1 <= ey <= ry2 for ex, ey in skip_endpoints):
                continue
            if _segment_crosses_rect(sx1, sy1, sx2, sy2, rx1, ry1, rx2, ry2):
                n += 1
                break
    return n


def _path_length(segs: list[tuple[int, int, int, int]]) -> int:
    """Manhattan length of a path."""
    return sum(abs(sx2 - sx1) + abs(sy2 - sy1) for sx1, sy1, sx2, sy2 in segs)


# ---------------------------------------------------------------------------
# Two-point routing
# ---------------------------------------------------------------------------


_S_BEND_MARGIN_MILS = 100  # one grid cell of clearance past an obstacle edge


def _route_s_bend(
    x1: int, y1: int, x2: int, y2: int,
    obstacles: list[tuple[int, int, int, int]],
) -> Optional[list[tuple[int, int, int, int]]]:
    """3-segment S-bend that tries to route AROUND obstacles.

    Two variants:
      - HVH: horizontal at y1, vertical at ``x_mid``, horizontal at y2.
      - VHV: vertical at x1, horizontal at ``y_mid``, vertical at x2.

    For each variant we try a set of candidate mid-coordinates: the
    geometric midpoint plus the edges of every obstacle (with a
    margin), since detours typically need to pass just before or just
    after an obstacle. Returns the shortest fully-clean route, or
    ``None`` if no clean S-bend exists -- caller falls back to the
    less-bad L-path.
    """
    if x1 == x2 or y1 == y2:
        return None  # endpoints already share an axis -> single segment
    skip = ((x1, y1), (x2, y2))
    candidates: list[tuple[int, list[tuple[int, int, int, int]]]] = []

    # HVH: vertical run at x_mid
    x_mids: set[int] = {(x1 + x2) // 2}
    for rx1, _ry1, rx2, _ry2 in obstacles:
        x_mids.add(rx1 - _S_BEND_MARGIN_MILS)
        x_mids.add(rx2 + _S_BEND_MARGIN_MILS)
    for x_mid in x_mids:
        if x_mid == x1 or x_mid == x2:
            continue
        segs = [
            (x1, y1, x_mid, y1),
            (x_mid, y1, x_mid, y2),
            (x_mid, y2, x2, y2),
        ]
        if _path_collisions(segs, obstacles, skip) == 0:
            candidates.append((_path_length(segs), segs))

    # VHV: horizontal run at y_mid
    y_mids: set[int] = {(y1 + y2) // 2}
    for _rx1, ry1_o, _rx2, ry2_o in obstacles:
        y_mids.add(ry1_o - _S_BEND_MARGIN_MILS)
        y_mids.add(ry2_o + _S_BEND_MARGIN_MILS)
    for y_mid in y_mids:
        if y_mid == y1 or y_mid == y2:
            continue
        segs = [
            (x1, y1, x1, y_mid),
            (x1, y_mid, x2, y_mid),
            (x2, y_mid, x2, y2),
        ]
        if _path_collisions(segs, obstacles, skip) == 0:
            candidates.append((_path_length(segs), segs))

    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


def _route_l_path(
    x1: int, y1: int, x2: int, y2: int,
    obstacles: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int, int]]:
    """Manhattan route from (x1, y1) to (x2, y2) avoiding obstacles.

    Strategy:
      1. Try both L-orderings (H-then-V, V-then-H); if either is
         collision-free, return it.
      2. If both L-paths collide, try a 3-segment S-bend that routes
         around the obstacles via a mid-coordinate chosen from
         obstacle edges and the geometric midpoint.
      3. Fall back to the less-bad L-path if no clean S-bend exists.

    Step 2 catches the common "two adjacent components both block
    H-first AND V-first" case without escalating to a full A* router.
    """
    if x1 == x2 and y1 == y2:
        return []
    if x1 == x2 or y1 == y2:
        return [(x1, y1, x2, y2)]
    h_first = _l_path_collisions(x1, y1, x2, y2, True, obstacles)
    v_first = _l_path_collisions(x1, y1, x2, y2, False, obstacles)
    if h_first == 0 or v_first == 0:
        if v_first < h_first:
            return [(x1, y1, x1, y2), (x1, y2, x2, y2)]
        return [(x1, y1, x2, y1), (x2, y1, x2, y2)]
    # Both L-paths collide -- try the S-bend rescue.
    s_segs = _route_s_bend(x1, y1, x2, y2, obstacles)
    if s_segs is not None:
        return s_segs
    # Final fallback: the less-bad L.
    if v_first < h_first:
        return [(x1, y1, x1, y2), (x1, y2, x2, y2)]
    return [(x1, y1, x2, y1), (x2, y1, x2, y2)]


# ---------------------------------------------------------------------------
# Multi-pin routing
# ---------------------------------------------------------------------------


def _count_bends(segs: list[tuple[int, int, int, int]]) -> int:
    """Number of CORNER points in a segment set (the readability metric).

    A corner is a point where a horizontal and a vertical segment meet at a
    shared ENDPOINT -- the wire visibly changes direction. A pin tapping into
    a trunk mid-span is a T-junction, not a corner, and is correctly NOT
    counted (the trunk is one long segment, so the tap point is interior to
    it). Fewer corners reads cleaner.
    """
    incident: dict[tuple[int, int], list[bool]] = {}
    for x1, y1, x2, y2 in segs:
        horiz = y1 == y2
        for p in ((x1, y1), (x2, y2)):
            incident.setdefault(p, []).append(horiz)
    bends = 0
    for flags in incident.values():
        if any(flags) and not all(flags):
            bends += 1
    return bends


def _net_obstacle_crossings(
    segs: list[tuple[int, int, int, int]],
    stub_ends: list[tuple[int, int]],
    obstacles: list[tuple[int, int, int, int]],
) -> int:
    """Segments crossing a component body, exempting a segment whose own
    endpoint is a net pin sitting inside that body (a legitimate stub start)."""
    pin_set = set(stub_ends)
    count = 0
    for sx1, sy1, sx2, sy2 in segs:
        for rx1, ry1, rx2, ry2 in obstacles:
            exempt = any(
                (px, py) in pin_set and rx1 <= px <= rx2 and ry1 <= py <= ry2
                for (px, py) in ((sx1, sy1), (sx2, sy2))
            )
            if exempt:
                continue
            if _segment_crosses_rect(sx1, sy1, sx2, sy2, rx1, ry1, rx2, ry2):
                count += 1
                break
    return count


def _trunk_candidates(
    stub_ends: list[tuple[int, int]],
) -> list[list[tuple[int, int, int, int]]]:
    """Trunk-and-stub (single-spine rectilinear Steiner) routings.

    A straight TRUNK at the MEDIAN coordinate (the 1-D Steiner-optimal spine
    position) with each pin tapping in via one perpendicular stub. Returns the
    horizontal-trunk and vertical-trunk variants; the caller scores both
    against obstacles and the other topologies. This is the canonical clean
    schematic routing for a shared net -- minimal corners, short total wire.
    """
    xs = [p[0] for p in stub_ends]
    ys = [p[1] for p in stub_ends]

    def _median(vals: list[int]) -> int:
        s = sorted(vals)
        return (s[len(s) // 2] // 100) * 100

    out: list[list[tuple[int, int, int, int]]] = []
    # Horizontal trunk at median y; vertical stubs.
    ty = _median(ys)
    h: list[tuple[int, int, int, int]] = [(min(xs), ty, max(xs), ty)]
    h += [(x, y, x, ty) for (x, y) in stub_ends if y != ty]
    out.append(h)
    # Vertical trunk at median x; horizontal stubs.
    tx = _median(xs)
    v: list[tuple[int, int, int, int]] = [(tx, min(ys), tx, max(ys))]
    v += [(x, y, tx, y) for (x, y) in stub_ends if x != tx]
    out.append(v)
    return out


def _route_signal_pins(
    stub_ends: list[tuple[int, int]],
    obstacles: list[tuple[int, int, int, int]] | None = None,
) -> list[tuple[int, int, int, int]]:
    """Manhattan-route wires connecting the stub ends of pins on a signal net.

    Within-block schematic convention: connect same-net pins with real
    wires rather than relying on per-pin net labels. Power and ground
    nets are exempt (the rail symbology is the connection).

    For 2 pins: a 2-segment L-path between the two stub ends.
    For 3+ pins: try CHAIN (consecutive in x-sort and y-sort) and STAR
    (every pin to a shared hub) topologies; pick whichever has fewer
    bbox crossings. Star hub candidates: each pin, the centroid, the
    centroid pushed out of any obstacle it lands inside, and the four
    corners of the stub-ends bounding box.

    All segments are axis-aligned (horizontal OR vertical), grid-snapped.
    When ``obstacles`` (component-body bboxes) are supplied each L-path
    picks the ordering that crosses fewer of them.
    """
    obstacles = obstacles or []
    if len(stub_ends) < 2:
        return []
    segs: list[tuple[int, int, int, int]] = []
    if len(stub_ends) == 2:
        (x1, y1), (x2, y2) = stub_ends
        segs.extend(_route_l_path(x1, y1, x2, y2, obstacles))
        return segs

    # 3+ pins: enumerate CHAIN (consecutive pins), STAR (shared hub) and
    # TRUNK (median spine, pins tap in) topologies, then pick the one with the
    # fewest body crossings, then the fewest CORNERS (the readability metric),
    # then the shortest wire. Trunk-and-stub is the canonical clean schematic
    # form and usually wins; chain/star are kept because one of them can route
    # cleanly around obstacles that a straight trunk would cut through.
    candidate_sets: list[list[tuple[int, int, int, int]]] = []

    # Chain in x-then-y and y-then-x pin orders.
    by_x = sorted(stub_ends, key=lambda p: (p[0], p[1]))
    by_y = sorted(stub_ends, key=lambda p: (p[1], p[0]))
    for chain in (by_x, by_y):
        cs: list[tuple[int, int, int, int]] = []
        for i in range(len(chain) - 1):
            cs.extend(_route_l_path(
                chain[i][0], chain[i][1], chain[i + 1][0], chain[i + 1][1],
                obstacles))
        candidate_sets.append(cs)

    # Star hubs: centroid, each pin, centroid pushed out of any obstacle it
    # sits in, and the stub-ends bounding-box corners (a wrap-around fallback).
    raw_cx = (sum(p[0] for p in stub_ends) // len(stub_ends) // 100) * 100
    raw_cy = (sum(p[1] for p in stub_ends) // len(stub_ends) // 100) * 100
    hubs: list[tuple[int, int]] = [(raw_cx, raw_cy), *stub_ends]
    for rx1, ry1, rx2, ry2 in obstacles:
        if rx1 < raw_cx < rx2 and ry1 < raw_cy < ry2:
            hubs += [(rx1 - 100, raw_cy), (rx2 + 100, raw_cy),
                     (raw_cx, ry1 - 100), (raw_cx, ry2 + 100)]
    bb_xmin = (min(p[0] for p in stub_ends) // 100) * 100
    bb_xmax = (max(p[0] for p in stub_ends) // 100) * 100
    bb_ymin = (min(p[1] for p in stub_ends) // 100) * 100
    bb_ymax = (max(p[1] for p in stub_ends) // 100) * 100
    hubs += [(bb_xmin, bb_ymin), (bb_xmax, bb_ymin),
             (bb_xmin, bb_ymax), (bb_xmax, bb_ymax)]
    for (hx, hy) in hubs:
        spokes: list[tuple[int, int, int, int]] = []
        for (x, y) in stub_ends:
            if (x, y) != (hx, hy):
                spokes.extend(_route_l_path(x, y, hx, hy, obstacles))
        if spokes:
            candidate_sets.append(spokes)

    # Trunk-and-stub (median spine), horizontal and vertical.
    candidate_sets.extend(_trunk_candidates(stub_ends))

    best_key: tuple[int, int, int] | None = None
    best_segs: list[tuple[int, int, int, int]] = []
    for cand in candidate_sets:
        if not cand:
            continue
        key = (
            _net_obstacle_crossings(cand, stub_ends, obstacles),
            _count_bends(cand),
            _path_length(cand),
        )
        if best_key is None or key < best_key:
            best_key = key
            best_segs = cand
    return best_segs


__all__ = [
    "_STUB_LEN_MILS",
    "_STUB_MIN_LEN_MILS",
    "_STUB_CLEARANCE_MILS",
    "_S_BEND_MARGIN_MILS",
    "_adaptive_stub_length",
    "_l_path_collisions",
    "_path_collisions",
    "_path_length",
    "_pin_direction_vector",
    "_route_l_path",
    "_route_s_bend",
    "_route_signal_pins",
    "_segment_crosses_rect",
    "_stub_endpoints",
]
