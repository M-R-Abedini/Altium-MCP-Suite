# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Apply learned placement priors as a post-Sugiyama bias.

The v1 "model" is just a dict: ``(part_role, anchor_role)`` ->
``(dx, dy, rotation)``. This module loads it from
``placement_priors.json`` (shipped in the package; see
``scripts/train/build_placement_priors.py``) and biases the post-layout
placements toward the user's recorded preferences.

Algorithm per part:
1. Find the part's anchor using the same heuristic as the learner --
   highest-pin-count netlist neighbor on a non-power, non-ground net,
   spatial-nearest fallback. Consistency between learner and applier
   matters: the prior says "this is where I want it relative to anchor
   X", so we need to look up the same X.
2. Look up the prior keyed by (part_role, anchor_role).
3. If present, snap the part's position to
   ``anchor_position + (dx, dy)`` (grid-aligned) and apply the
   rotation delta.
4. If not present, leave the placement untouched.

This is a one-pass bias, not iterative. Subsequent overlap-repair in
``compute_layout`` may push parts apart again; that's fine -- priors
are a hint, not a constraint.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from eda_agent.design.layout import PlacedPart
from eda_agent.design.plan import DesignPlan

logger = logging.getLogger("eda_agent.design.priors")


_DEFAULT_PRIORS_PATH = Path(__file__).resolve().parent / "placement_priors.json"


def _placement_priors_path() -> Path:
    """Resolve where to read priors from.

    Override path via the ``EDA_AGENT_PRIORS`` env var so callers can
    swap in a test fixture or a custom-trained model without touching
    the bundled file.
    """
    import os
    override = os.environ.get("EDA_AGENT_PRIORS")
    if override:
        return Path(override)
    return _DEFAULT_PRIORS_PATH


def load_priors(
    path: Optional[Path] = None,
) -> Optional[dict[str, dict[str, Any]]]:
    """Read the priors JSON, or fall back to canonical hand-authored ones.

    Lookup order:
      1. ``placement_priors.json`` learned from user edits (overrides built-ins).
      2. ``CANONICAL_PRIORS`` -- hand-authored, datasheet-anchored,
         shipped with the package. Always present.

    Returns the inner ``priors`` mapping keyed by ``"part_role|anchor_role"``.
    Always returns a usable dict; the canonical fallback ensures fresh
    installs get professional-looking output without needing votes.
    """
    p = path or _placement_priors_path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            priors = data.get("priors")
            if isinstance(priors, dict) and priors:
                return priors
        except json.JSONDecodeError as exc:
            logger.warning("placement_priors.json at %s is corrupt: %s", p, exc)
    # Fall back to hand-authored canonical priors. These encode
    # "where this part type sits relative to its anchor" for common
    # role pairings the planner uses; lifted from datasheet typical-
    # application figures and EDA conventions.
    return dict(CANONICAL_PRIORS)


# Canonical role-pair offsets. Each entry is keyed
# "<part_role>|<anchor_role>" with (dx, dy, rotation_delta) in mils.
# Anchor convention: the highest-pin-count non-power netlist neighbor
# (same heuristic as learner._pick_anchor).
#
# These were authored from canonical EDA practice + datasheet typical-
# application circuits, NOT learned from votes. They're the "good
# default" for fresh installs.
CANONICAL_PRIORS: dict[str, dict[str, Any]] = {
    # --- IC decoupling caps (HF and bulk) ---
    "vcc_decoup|ic":   {"dx":    0, "dy":  400, "rotation": 0, "n_samples": 0,
                        "part_role": "vcc_decoup", "anchor_role": "ic"},
    "decoup_hf|ic":    {"dx":    0, "dy":  300, "rotation": 0, "n_samples": 0,
                        "part_role": "decoup_hf",  "anchor_role": "ic"},
    "decoup_bulk|ic":  {"dx":  200, "dy":  700, "rotation": 0, "n_samples": 0,
                        "part_role": "decoup_bulk","anchor_role": "ic"},
    "decoup_cap|ic":   {"dx":    0, "dy":  400, "rotation": 0, "n_samples": 0,
                        "part_role": "decoup_cap", "anchor_role": "ic"},
    # --- LED indicator chain (current-limit R + LED) ---
    "led_limit|ic":    {"dx": 1500, "dy": 1000, "rotation": 0, "n_samples": 0,
                        "part_role": "led_limit",  "anchor_role": "ic"},
    "indicator|ic":    {"dx": 2500, "dy": 1000, "rotation": 0, "n_samples": 0,
                        "part_role": "indicator",  "anchor_role": "ic"},
    "indicator|led_limit": {"dx": 1000, "dy": 0, "rotation": 0, "n_samples": 0,
                            "part_role": "indicator", "anchor_role": "led_limit"},
    # --- Feedback divider (regulator / op-amp) ---
    "fb_top|ic":       {"dx":  500, "dy": -500, "rotation": 270, "n_samples": 0,
                        "part_role": "fb_top",     "anchor_role": "ic"},
    "fb_bot|ic":       {"dx":  500, "dy":-1000, "rotation": 270, "n_samples": 0,
                        "part_role": "fb_bot",     "anchor_role": "ic"},
    # --- Power-in / power-out connectors ---
    "power_in|ic":     {"dx":-2000, "dy":    0, "rotation": 180, "n_samples": 0,
                        "part_role": "power_in",   "anchor_role": "ic"},
    "power_out|ic":    {"dx": 2000, "dy":    0, "rotation": 0, "n_samples": 0,
                        "part_role": "power_out",  "anchor_role": "ic"},
    "input_conn|ic":   {"dx":-2000, "dy":    0, "rotation": 180, "n_samples": 0,
                        "part_role": "input_conn", "anchor_role": "ic"},
    "output_conn|ic":  {"dx": 2000, "dy":    0, "rotation": 0, "n_samples": 0,
                        "part_role": "output_conn","anchor_role": "ic"},
    # --- Pullup / debounce ---
    "pullup|ic":       {"dx": -500, "dy":  500, "rotation": 270, "n_samples": 0,
                        "part_role": "pullup",     "anchor_role": "ic"},
    "debounce|ic":     {"dx": -500, "dy": -500, "rotation": 270, "n_samples": 0,
                        "part_role": "debounce",   "anchor_role": "ic"},
    # --- Filter network (R+C low-pass when anchored to upstream IC) ---
    "filter_r|ic":     {"dx":  800, "dy":    0, "rotation": 0, "n_samples": 0,
                        "part_role": "filter_r",   "anchor_role": "ic"},
    "filter_c|ic":     {"dx": 1600, "dy": -400, "rotation": 270, "n_samples": 0,
                        "part_role": "filter_c",   "anchor_role": "ic"},
    # --- Crystal load caps (symmetric) ---
    "crystal_cap_l|crystal": {"dx": -400, "dy": 0, "rotation": 270, "n_samples": 0,
                              "part_role": "crystal_cap_l","anchor_role": "crystal"},
    "crystal_cap_r|crystal": {"dx":  400, "dy": 0, "rotation": 270, "n_samples": 0,
                              "part_role": "crystal_cap_r","anchor_role": "crystal"},
}


def apply_placement_priors(
    placements: list[PlacedPart],
    plan: DesignPlan,
    priors: dict[str, dict[str, Any]],
    *,
    grid_mils: int = 100,
) -> list[PlacedPart]:
    """Shift placements toward the recorded preference for each role pair.

    Args:
        placements: Output of ``compute_layout(plan)`` -- the Sugiyama /
            force-directed placement decisions.
        plan: The plan that produced ``placements``; used to look up
            roles and pick anchors.
        priors: The ``priors`` sub-dict from ``placement_priors.json``.
        grid_mils: Snap-to-grid step for the biased positions. Altium's
            schematic grid is typically 100 mils; matching it keeps
            wires landing on grid intersections.

    Returns:
        A new list of PlacedPart objects with biased positions. The
        input is not mutated.
    """
    if not priors:
        return list(placements)

    inferred_roles = _infer_decoup_roles(plan)
    # Crystal load caps + crystal: recover the roles the symmetric crystal
    # priors key on (decoup caps sit on power rails, crystal caps on XIN/XOUT
    # signal nets, so the two inferences never collide).
    inferred_roles.update(_infer_crystal_roles(plan))
    role_by_refdes = {
        p.refdes: (p.role or inferred_roles.get(p.refdes, ""))
        for p in plan.parts
    }
    placement_by_refdes = {p.refdes: p for p in placements}

    # Place ICs / high-pin-count anchors first so their positions are
    # stable before passives get pinned relative to them. Anchor parts
    # are typically what other parts cluster around; biasing them risks
    # cascade movement, so we leave anchors untouched in this pass.
    pin_count_by_refdes = _pin_count_by_refdes(plan)
    anchor_set = {
        refdes
        for refdes, count in pin_count_by_refdes.items()
        if count >= 4
    }

    # Build a reverse-index: which priors apply when the part has role X?
    # Each entry is (anchor_role, prior_dict). Lets us check if ANY of the
    # roles present in the plan can serve as anchor for this part's role.
    priors_by_part_role: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for key, prior in priors.items():
        if "|" not in key:
            continue
        part_role, anchor_role = key.split("|", 1)
        priors_by_part_role.setdefault(part_role, []).append((anchor_role, prior))

    # Build a map of role -> highest-pin-count refdes with that role.
    # When applying a canonical prior, we anchor against the part with
    # the matching role (not the heuristic learner-anchor).
    refdes_by_role: dict[str, list[str]] = {}
    for refdes, role in role_by_refdes.items():
        if role:
            refdes_by_role.setdefault(role, []).append(refdes)

    # Per-(role, anchor) instance index so multiple same-role parts
    # (C1, C2, C3 all "decoup_cap") don't land on top of each other.
    # Each subsequent instance gets a small additional offset.
    instance_idx: dict[tuple[str, str], int] = {}

    out: list[PlacedPart] = []
    for placement in placements:
        if placement.refdes in anchor_set:
            out.append(placement)
            continue
        role = role_by_refdes.get(placement.refdes, "")
        if not role:
            out.append(placement)
            continue
        candidates = priors_by_part_role.get(role, [])
        # Find the first canonical prior whose anchor_role exists in the plan.
        chosen_anchor_refdes: Optional[str] = None
        chosen_prior: Optional[dict[str, Any]] = None
        # A decoupling cap must anchor to the IC on ITS OWN power rail, not the
        # board's biggest IC: otherwise every decap on a multi-IC board piles
        # onto one chip, and a decap whose IC the planner forgot to tag role=ic
        # gets no prior at all. The rail-mate is found structurally (highest-
        # pin-count part sharing the cap's non-ground net), so it fires even
        # when no part is explicitly tagged "ic".
        if role in _DECOUP_ROLES:
            rail_anchor = _decoupling_rail_anchor(
                placement.refdes, plan, placement_by_refdes)
            if rail_anchor is not None:
                for anchor_role, prior in candidates:
                    if anchor_role == "ic":
                        chosen_anchor_refdes = rail_anchor
                        chosen_prior = prior
                        break
        if chosen_anchor_refdes is None:
            for anchor_role, prior in candidates:
                anchor_refdes_list = refdes_by_role.get(anchor_role, [])
                if not anchor_refdes_list:
                    continue
                # Pick the highest-pin-count match (so "ic" anchors to the
                # actual IC, not some other thing tagged with role=ic).
                chosen_anchor_refdes = max(
                    anchor_refdes_list,
                    key=lambda r: pin_count_by_refdes.get(r, 0),
                )
                chosen_prior = prior
                break
        if chosen_anchor_refdes is None or chosen_prior is None:
            out.append(placement)
            continue
        anchor_placement = placement_by_refdes.get(chosen_anchor_refdes)
        if anchor_placement is None:
            out.append(placement)
            continue
        # Per-instance offset: if there are 3 decoup caps anchored to U1,
        # space them out so they don't overlap. Step the second and
        # subsequent instances along the prior's primary axis.
        key = (role, chosen_anchor_refdes)
        idx = instance_idx.get(key, 0)
        instance_idx[key] = idx + 1
        dx = int(chosen_prior.get("dx", 0))
        dy = int(chosen_prior.get("dy", 0))
        # Step perpendicular to the primary axis to avoid stacking.
        if idx > 0:
            if abs(dx) >= abs(dy):
                # Primary axis is X; step in Y.
                dy += idx * 400 * (1 if idx % 2 else -1)
            else:
                dx += idx * 400 * (1 if idx % 2 else -1)
        new_x = anchor_placement.x_mils + dx
        new_y = anchor_placement.y_mils + dy
        new_x = (new_x // grid_mils) * grid_mils
        new_y = (new_y // grid_mils) * grid_mils
        new_rot = (placement.rotation + int(chosen_prior.get("rotation", 0))) % 360
        out.append(PlacedPart(
            refdes=placement.refdes,
            sheet=placement.sheet,
            x_mils=new_x,
            y_mils=new_y,
            rotation=new_rot,
        ))
    return out


def _pin_count_by_refdes(plan: DesignPlan) -> dict[str, int]:
    counts: dict[str, int] = {}
    for net in plan.nets:
        for pin_ref in net.pins:
            counts[pin_ref.refdes] = counts.get(pin_ref.refdes, 0) + 1
    return counts


# Part roles whose anchor is the IC on the part's own power rail, not the
# board's biggest IC (every decoupling-cap variant in CANONICAL_PRIORS).
_DECOUP_ROLES = frozenset({
    "vcc_decoup", "decoup_hf", "decoup_bulk", "decoup_cap",
})



def _infer_decoup_roles(plan: DesignPlan) -> dict[str, str]:
    """Structurally tag untagged decoupling caps so they get the decoup prior.

    A two-pin capacitor whose legs are one ground net and one POWER RAIL is a
    decoupling cap by construction. Gating on the rail (a flagged is_power net,
    OR one the connector-guarded ``motifs._infer_power_nets`` recognises) is
    what keeps a FILTER cap out: a filter's node is a signal net, never a
    power rail, so it is not tagged here and its rc_lowpass / rc_highpass motif
    still governs its placement. Only caps the planner left un-roled are
    considered. Returns ``{refdes: "decoup_cap"}``.

    Closes the last annotation gap: with the rail-aware anchor, a decap now
    lands by its IC even when the planner tagged neither the cap's role, nor
    the IC's role, nor the rail's is_power flag.
    """
    # Local import keeps the priors module's top-level dependencies light.
    from eda_agent.design.motifs import _infer_power_nets, _kind_from_refdes

    rails = {n.name for n in plan.nets if n.is_power} | _infer_power_nets(plan)
    if not rails:
        return {}
    ground = {
        n.name for n in plan.nets
        if n.is_ground or (n.role or "") == "ground"
    }
    parts_nets: dict[str, set[str]] = {}
    for n in plan.nets:
        for pr in n.pins:
            parts_nets.setdefault(pr.refdes, set()).add(n.name)

    out: dict[str, str] = {}
    for p in plan.parts:
        if (p.role or "") or _kind_from_refdes(p.refdes) != "C":
            continue
        legs = parts_nets.get(p.refdes, set())
        if len(legs) == 2 and (legs & ground) and (legs & rails):
            out[p.refdes] = "decoup_cap"
    return out


def _crystal_clusters(plan: DesignPlan) -> list[tuple[str, str, str]]:
    """Find each crystal oscillator group as ``(crystal, cap_l, cap_r)``.

    Structural signature, mirroring the PCB ``_infer_crystal_groups``: a
    two-pin part on exactly two NON-ground nets, each of which also carries a
    two-pin cap to ground (a load cap) AND both of which reach a common
    multi-pin IC (the MCU -- this rejects an arbitrary 2-pin bridge). The cap
    on the lexicographically-smaller crystal net is returned as ``cap_l``, the
    other as ``cap_r`` (the choice is arbitrary; the layout is symmetric). Only
    crystals/caps the planner left un-roled are considered, so an explicit
    planner role is never overridden.
    """
    ground_nets = {
        n.name for n in plan.nets
        if n.is_ground or (n.role or "") == "ground"
    }
    if not ground_nets:
        return {}
    members = {n.name: {pr.refdes for pr in n.pins} for n in plan.nets}
    nets_of: dict[str, set[str]] = {}
    pins_of: dict[str, int] = {}
    for n in plan.nets:
        for pr in n.pins:
            nets_of.setdefault(pr.refdes, set()).add(n.name)
            pins_of[pr.refdes] = pins_of.get(pr.refdes, 0) + 1
    from eda_agent.design.motifs import _kind_from_refdes

    role_of = {p.refdes: (p.role or "") for p in plan.parts}

    def _load_cap(net_name: str) -> Optional[str]:
        for r in members.get(net_name, set()):
            # Must be an actual capacitor -- otherwise a feedback divider (R on
            # VOUT/FB with a resistor R-to-ground on FB and a cap on VOUT) reads
            # as a crystal, stealing the FB resistors' roles.
            if (pins_of.get(r) != 2 or role_of.get(r)
                    or _kind_from_refdes(r) != "C"):
                continue
            legs = nets_of.get(r, set())
            if len(legs) == 2 and net_name in legs and (legs & ground_nets):
                return r
        return None

    clusters: list[tuple[str, str, str, str]] = []
    for p in plan.parts:
        if role_of.get(p.refdes) or pins_of.get(p.refdes) != 2:
            continue
        legs = nets_of.get(p.refdes, set())
        non_gnd = sorted(legs - ground_nets)
        if len(legs) != 2 or len(non_gnd) != 2:
            continue
        a, b = non_gnd
        ca, cb = _load_cap(a), _load_cap(b)
        if not ca or not cb or ca == cb:
            continue
        ic_a = {r for r in members[a] if pins_of.get(r, 0) >= 3}
        ic_b = {r for r in members[b] if pins_of.get(r, 0) >= 3}
        common_ic = ic_a & ic_b
        if not common_ic:
            continue
        # The MCU the oscillator hangs off: the highest-pin-count shared IC
        # (refdes as a deterministic tie-break).
        ic = max(common_ic, key=lambda r: (pins_of.get(r, 0), r))
        clusters.append((p.refdes, ca, cb, ic))
    return clusters


def _infer_crystal_roles(plan: DesignPlan) -> dict[str, str]:
    """Structurally tag a crystal and its load caps so the symmetric crystal
    priors fire. See :func:`_crystal_clusters` for the detection. Returns
    ``{refdes: role}`` with the crystal tagged ``crystal`` and the cap on the
    lexicographically-smaller net ``crystal_cap_l``, the other
    ``crystal_cap_r`` (the choice is arbitrary; the layout is symmetric).
    """
    out: dict[str, str] = {}
    for y, ca, cb, _ic in _crystal_clusters(plan):
        out[y] = "crystal"
        out[ca] = "crystal_cap_l"
        out[cb] = "crystal_cap_r"
    return out




# Spacing of each load cap from the crystal, mirroring the
# ``crystal_cap_l|crystal`` / ``crystal_cap_r|crystal`` priors.
_CRYSTAL_CAP_DX = 400


def resnap_crystal_clusters(
    plan: DesignPlan,
    placements: list[PlacedPart],
    *,
    grid_mils: int = 100,
) -> list[PlacedPart]:
    """Re-seat each crystal oscillator as a compact cluster beside its MCU.

    Two things scatter the oscillator and neither survives to here on its own:
    the symmetric load-cap prior is undone by the post-priors overlap shove
    (which sizes parts by pin count, so reads the tight 2-pin pair as
    overlapping and moves the crystal too), and nothing pulls the crystal in to
    the IC, so Sugiyama/FD can leave it thousands of mils away -- the dominant
    XIN/XOUT wirelength. This deterministic pass, run AFTER the shove, fixes
    both: it pins the crystal just clear of its anchor IC's body (on whichever
    side Sugiyama already favoured, snapped to the nearer axis so the drawing
    stays orthogonal) and flanks it with the two load caps +/-400 mils on the
    PERPENDICULAR axis, so neither cap lands between the crystal and the IC.
    Caps are tiny, so the minor courtyard overlap this reintroduces is cosmetic
    on a schematic, and shortening XIN/XOUT is what the scorer rewards. A no-op
    when no crystal cluster is present.
    """
    clusters = _crystal_clusters(plan)
    if not clusters:
        return placements
    from eda_agent.design.force_directed import _bbox_half

    pin_count: dict[str, int] = {}
    for n in plan.nets:
        for pr in n.pins:
            pin_count[pr.refdes] = pin_count.get(pr.refdes, 0) + 1

    def _snap(v: float) -> int:
        return int((v // grid_mils) * grid_mils)

    by_refdes = {p.refdes: p for p in placements}
    out_by_refdes = dict(by_refdes)
    for y, ca, cb, ic in clusters:
        yp = by_refdes.get(y)
        if yp is None:
            continue
        icp = by_refdes.get(ic)
        cap_horizontal = True  # caps flank the crystal left/right by default
        if icp is not None:
            # Place the crystal just clear of the IC body, on the axis it is
            # already nearer to (keep its Sugiyama side -> shorter XIN/XOUT).
            clear = (_bbox_half(pin_count.get(ic, 4))
                     + _bbox_half(pin_count.get(y, 2)) + 200)
            ddx = yp.x_mils - icp.x_mils
            ddy = yp.y_mils - icp.y_mils
            if abs(ddx) >= abs(ddy):
                ux, uy = (1 if ddx >= 0 else -1), 0
                cap_horizontal = False  # crystal beside IC -> caps stack up/down
            else:
                ux, uy = 0, (1 if ddy >= 0 else -1)
                cap_horizontal = True
            yx = _snap(icp.x_mils + ux * clear)
            yy = _snap(icp.y_mils + uy * clear)
            yp = PlacedPart(refdes=y, sheet=yp.sheet, x_mils=yx, y_mils=yy,
                            rotation=yp.rotation)
            out_by_refdes[y] = yp
        for cap, sign in ((ca, -1), (cb, +1)):
            cp = by_refdes.get(cap)
            if cp is None:
                continue
            if cap_horizontal:
                nx = _snap(yp.x_mils + sign * _CRYSTAL_CAP_DX)
                ny = _snap(yp.y_mils)
            else:
                nx = _snap(yp.x_mils)
                ny = _snap(yp.y_mils + sign * _CRYSTAL_CAP_DX)
            out_by_refdes[cap] = PlacedPart(
                refdes=cp.refdes, sheet=cp.sheet,
                x_mils=nx, y_mils=ny, rotation=cp.rotation)
    # Preserve input order.
    return [out_by_refdes[p.refdes] for p in placements]


def resnap_motif_clusters(
    plan: DesignPlan,
    placements: list[PlacedPart],
    *,
    grid_mils: int = 100,
) -> list[PlacedPart]:
    """Re-tighten motif clusters that the post-priors overlap shove spread.

    The composer splats each recognised motif into its canonical relative shape
    (a clean diode-bridge diamond, a C-L-C pi filter, a regulator's fb_divider /
    boot_cap / lc_output around the IC), but the overlap shove sizes parts by
    pin count and so reads a tight 2-pin cluster as overlapping -- it scatters
    the parts, undoing the geometry (a bootstrap cap can end up thousands of
    mils from its IC). This restores each motif's canonical arrangement:
    IC-anchored motifs re-splat around the IC's CURRENT (post-shove) position;
    self-contained motifs re-splat around the matched parts' centroid -- so each
    group stays where signal flow + the shove left it, but its internal shape
    comes back. ``crystal_load`` is skipped (it has its own, better
    ``resnap_crystal_clusters``).

    When two motifs on one IC have COINCIDING canonical slots (fb_divider's Rtop
    and lc_output's L share an offset), re-snapping both would re-overlap them,
    so the higher-specificity motif wins the slot and the colliding part keeps
    its shove position (``min_sep`` guard). No-op when no motif fired.
    """
    from eda_agent.design.motifs import get_motif_by_name, recognize_motifs

    matches = recognize_motifs(plan)
    if not matches:
        return placements

    by_refdes = {p.refdes: p for p in placements}
    out_by_refdes = dict(by_refdes)

    def _snap(v: float) -> int:
        return int((v // grid_mils) * grid_mils)

    # When two motifs share an IC their canonical slots can coincide (e.g.
    # fb_divider's Rtop and lc_output's L both sit at the same offset). The
    # shove rightly separates those; re-snapping both back would re-overlap
    # them. So per IC, track the slots already claimed and SKIP a part whose
    # slot is within MIN_SEP of one a higher-specificity motif on the SAME IC
    # took -- it keeps its shove position. The collision set is PER-IC: two
    # unrelated motifs at different board positions (a diode bridge and a pi
    # filter) must not block each other.
    min_sep = 500
    claimed_by_ic: dict[str, list[tuple[int, int]]] = {}

    def _spec(m) -> int:
        mo = get_motif_by_name(m.motif_name)
        return mo.specificity if mo else 0

    for match in sorted(matches, key=_spec, reverse=True):
        motif = get_motif_by_name(match.motif_name)
        if motif is None:
            continue
        # crystal_load is IC-anchored but has its own (better) resnap.
        if match.motif_name == "crystal_load":
            continue
        if motif.ic_anchor is not None:
            # Anchor to the IC's CURRENT (post-shove) position; the passives
            # snap back to their canonical offsets from it. Collisions are
            # checked only against other motifs on this same IC.
            ic = match.host_refdes(motif.ic_anchor)
            icp = by_refdes.get(ic) if ic else None
            if icp is None:
                continue
            ax, ay = icp.x_mils, icp.y_mils
            claimed = claimed_by_ic.setdefault(ic, [])
        else:
            # Self-contained: anchor to the matched parts' current centroid; the
            # canonical offsets are internally non-colliding, so no cross-motif
            # collision check (a separate motif elsewhere must not block it).
            members = [match.host_refdes(p) for p in motif.canonical]
            members = [r for r in members if r is not None and r in by_refdes]
            if len(members) < 2:
                continue
            ax = sum(by_refdes[r].x_mils for r in members) // len(members)
            ay = sum(by_refdes[r].y_mils for r in members) // len(members)
            claimed = None
        for pat, (dx, dy) in motif.canonical.items():
            r = match.host_refdes(pat)
            if r is None or r not in by_refdes:
                continue
            tx, ty = _snap(ax + dx), _snap(ay + dy)
            if claimed is not None and any(
                    abs(tx - cx) < min_sep and abs(ty - cy) < min_sep
                    for cx, cy in claimed):
                continue                       # would re-overlap; leave to shove
            p = by_refdes[r]
            out_by_refdes[r] = PlacedPart(
                refdes=r, sheet=p.sheet, x_mils=tx, y_mils=ty,
                rotation=p.rotation)
            if claimed is not None:
                claimed.append((tx, ty))

    return [out_by_refdes[p.refdes] for p in placements]


def _decoupling_rail_anchor(
    cap_refdes: str,
    plan: DesignPlan,
    placement_by_refdes: dict[str, PlacedPart],
) -> Optional[str]:
    """The IC a decoupling cap serves: the highest-pin-count placed part that
    shares the cap's NON-GROUND net (its power rail).

    A decoupling cap connects exactly ground + one rail, so "the cap's net
    that isn't ground" is its rail regardless of whether the planner flagged
    it is_power -- which makes this robust to unflagged rails too. Requires
    the rail-mate to have >= 3 pins so a cap never anchors to another cap
    (returns None then, so the caller falls back to the generic role anchor).
    """
    ground = {
        n.name for n in plan.nets
        if n.is_ground or (n.role or "") == "ground"
    }
    cap_nets = {
        n.name for n in plan.nets
        if any(pr.refdes == cap_refdes for pr in n.pins)
    }
    rails = cap_nets - ground
    if not rails:
        return None
    pin_counts = _pin_count_by_refdes(plan)
    best: Optional[str] = None
    best_pc = 2  # require >= 3 pins (an IC); a cap is 2
    for net in plan.nets:
        if net.name not in rails:
            continue
        for pin_ref in net.pins:
            r = pin_ref.refdes
            if r == cap_refdes or r not in placement_by_refdes:
                continue
            pc = pin_counts.get(r, 0)
            if pc > best_pc:
                best_pc = pc
                best = r
    return best


def _pick_anchor(
    moved_refdes: str,
    plan: DesignPlan,
    placement_by_refdes: dict[str, PlacedPart],
) -> Optional[str]:
    """Same anchor heuristic as ``learner._pick_anchor``.

    Duplicated rather than imported to keep ``priors`` free of any
    learner-side imports (which would pull in os-environ, file I/O,
    etc.). Kept in sync by docstring + unit test parity.
    """
    candidates = {p.refdes for p in plan.parts if p.refdes != moved_refdes}
    placed_candidates = candidates & placement_by_refdes.keys()
    if not placed_candidates:
        return None

    moved_nets = {
        net.name for net in plan.nets
        if not (net.is_power or net.is_ground)
        for pin_ref in net.pins
        if pin_ref.refdes == moved_refdes
    }
    shared_count: dict[str, int] = {}
    if moved_nets:
        for net in plan.nets:
            if net.is_power or net.is_ground:
                continue
            if net.name not in moved_nets:
                continue
            for pin_ref in net.pins:
                if pin_ref.refdes == moved_refdes:
                    continue
                if pin_ref.refdes not in placed_candidates:
                    continue
                shared_count[pin_ref.refdes] = (
                    shared_count.get(pin_ref.refdes, 0) + 1
                )

    if shared_count:
        pin_counts = _pin_count_by_refdes(plan)
        best = max(
            shared_count.items(),
            key=lambda kv: (kv[1], pin_counts.get(kv[0], 0)),
        )
        return best[0]

    moved_p = placement_by_refdes.get(moved_refdes)
    if moved_p is None:
        return None
    mx, my = moved_p.x_mils, moved_p.y_mils
    best_refdes: Optional[str] = None
    best_d = 10**9
    for other in placed_candidates:
        op = placement_by_refdes.get(other)
        if op is None:
            continue
        d = abs(op.x_mils - mx) + abs(op.y_mils - my)
        if d < best_d:
            best_d = d
            best_refdes = other
    return best_refdes
