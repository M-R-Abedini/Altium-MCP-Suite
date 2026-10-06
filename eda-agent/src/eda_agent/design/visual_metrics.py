# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Perceptual placement metrics: what the rendered board SHOWS, as distinct
from the analytic objective the solver minimizes.

The analytic score (HPWL + clearance + via + ...) can rate a board fine while
a glance at the render shows a broken oscillator cluster or a tangle of
ratsnest crossings -- a proxy gap. The motivating case: a 15-part MCU board
scored ``weighted_total=17779`` with ``legal=True`` while its crystal and the
crystal's two load caps were flung to opposite edges of the board (spread 860
mils). The number called it fine; a human reading the picture saw a broken
oscillator instantly.

These metrics re-read the placement the way a person -- or a vision model --
reads the picture: how tight each keep-together cluster is, how many net lines
cross, and how the parts balance across the board. They are pure geometry on
the placement centroids (no image parsing), so they are deterministic and
cheap, and they slot into two places:

  * best-of *selection* -- co-rank candidates by ``penalty`` so the engine can
    prefer the variant that also looks right, not only the one with the lowest
    abstract score; and
  * a repair *trigger* -- ``group_excess`` flags which keep-together clusters
    are scattered so a targeted relocation pass can tighten them.

NDA scope: reads only the current placement geometry; carries no cross-project
state.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence


@dataclass
class VisualReport:
    """Perceptual quality of one placement.

    ``group_spread`` maps each keep-together group to the maximum pairwise
    centroid distance among its members (the visual "how scattered" measure).
    ``group_excess`` is that spread minus the group's tight-pack target,
    floored at zero -- a positive value means the cluster reads as broken.
    ``crossings`` is the inter-net ratsnest crossing count (the caller passes
    the engine's count in; the picture's tangle). ``whitespace_cv`` is the
    coefficient of variation of part-area occupancy across the four board
    quadrants -- high means the board looks lopsided. ``penalty`` is the
    combined scalar (higher is worse-looking) used for selection.
    """

    group_spread: dict[str, float] = field(default_factory=dict)
    group_excess: dict[str, float] = field(default_factory=dict)
    crossings: int = 0
    whitespace_cv: float = 0.0
    penalty: float = 0.0


def cluster_spread(
    centroids: Mapping[str, Sequence[float]], refs: Sequence[str]
) -> float:
    """Maximum pairwise centroid distance among ``refs`` (0 for < 2 present)."""
    pts = [centroids[r] for r in refs if r in centroids]
    if len(pts) < 2:
        return 0.0
    worst = 0.0
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = math.dist(pts[i], pts[j])
            if d > worst:
                worst = d
    return worst


def tight_pack_diag(comp_dims: Sequence[tuple[float, float]]) -> float:
    """Diagonal of the smallest square that holds the members packed side by
    side, plus a little slack.

    A keep-together group of small parts wants to sit within roughly this
    distance; anything much larger reads as scattered. Uses total member area
    as the pack-square area (a lower bound on the real packing, which is fine
    as a *target* threshold), then returns the square's diagonal. A small
    additive floor keeps a two-tiny-cap group from demanding sub-part-size
    tightness.
    """
    if not comp_dims:
        return 0.0
    area = sum(w * h for (w, h) in comp_dims)
    side = math.sqrt(max(area, 1.0))
    biggest = max(max(w, h) for (w, h) in comp_dims)
    # Target: a pack square (diagonal), but never tighter than the biggest
    # single member plus a clearance-ish margin.
    return max(side * math.sqrt(2.0), biggest * 1.4)


def group_compactness(
    comps: Sequence,
    centroids: Mapping[str, Sequence[float]],
    groups: Mapping[str, str],
) -> tuple[dict[str, float], dict[str, float]]:
    """Per-group spread and excess-over-target.

    ``groups`` maps refdes -> group name (e.g. a crystal's shared
    ``match_group``). Returns ``(spread, excess)`` dicts keyed by group name.
    ``excess`` is ``max(0, spread - tight_pack_target)`` -- zero when the
    cluster is already tight, positive (in mils) when it reads as broken.
    """
    by_ref = {c.ref: c for c in comps}
    members: dict[str, list[str]] = {}
    for ref, grp in groups.items():
        if grp:
            members.setdefault(grp, []).append(ref)

    spread: dict[str, float] = {}
    excess: dict[str, float] = {}
    for grp, refs in members.items():
        if len(refs) < 2:
            continue
        sp = cluster_spread(centroids, refs)
        dims = [(by_ref[r].w, by_ref[r].h) for r in refs if r in by_ref]
        target = tight_pack_diag(dims)
        spread[grp] = sp
        excess[grp] = max(0.0, sp - target)
    return spread, excess


def whitespace_cv(
    comps: Sequence,
    centroids: Mapping[str, Sequence[float]],
    region,
) -> float:
    """Coefficient of variation of part-area occupancy across board quadrants.

    Splits the board into four quadrants and sums each part's bounding-box
    area into the quadrant holding its centroid, then returns std/mean of the
    four totals. 0.0 means perfectly balanced; large values mean the layout
    crowds one corner and leaves dead copper elsewhere -- something the
    analytic objective, which only sums net length and clearance, does not
    see directly.
    """
    by_ref = {c.ref: c for c in comps}
    cx_mid = (region.x1 + region.x2) / 2.0
    cy_mid = (region.y1 + region.y2) / 2.0
    quads = [0.0, 0.0, 0.0, 0.0]
    for ref, (x, y) in centroids.items():
        c = by_ref.get(ref)
        if c is None:
            continue
        q = (0 if x < cx_mid else 1) + (0 if y < cy_mid else 2)
        quads[q] += c.w * c.h
    mean = sum(quads) / 4.0
    if mean <= 0.0:
        return 0.0
    var = sum((q - mean) ** 2 for q in quads) / 4.0
    return math.sqrt(var) / mean


# Selection weights. ``excess`` is in mils (tens..hundreds), ``crossings`` is a
# small integer, ``whitespace_cv`` is ~0..2. These bring each onto a scale
# comparable to the analytic ``weighted_total`` so a caller can add
# ``penalty`` to the objective for co-ranking. Kept deliberately modest:
# selection should break near-ties toward the better-looking layout, not
# overrule a large analytic gap (the repair pass, not selection, is what
# fixes a badly scattered cluster outright).
W_GROUP_EXCESS = 8.0
W_CROSSING = 120.0
W_WHITESPACE = 600.0


def visual_report(
    comps: Sequence,
    centroids: Mapping[str, Sequence[float]],
    region,
    *,
    groups: Optional[Mapping[str, str]] = None,
    crossings: int = 0,
) -> VisualReport:
    """Assemble a :class:`VisualReport` from a placement's centroids.

    ``groups`` defaults to the components' own ``match_group`` attribute (the
    keep-together tag). ``crossings`` is the engine's inter-net ratsnest count,
    passed in so this module stays free of the heavier net-geometry code.
    """
    if groups is None:
        groups = {
            c.ref: str(getattr(c, "match_group", "") or "")
            for c in comps
            if str(getattr(c, "match_group", "") or "")
        }
    spread, excess = group_compactness(comps, centroids, groups)
    ws_cv = whitespace_cv(comps, centroids, region)
    penalty = (
        W_GROUP_EXCESS * sum(excess.values())
        + W_CROSSING * float(crossings)
        + W_WHITESPACE * ws_cv
    )
    return VisualReport(
        group_spread=spread,
        group_excess=excess,
        crossings=int(crossings),
        whitespace_cv=ws_cv,
        penalty=penalty,
    )
