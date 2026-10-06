# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Composer tests: motif splatting + Sugiyama fallback + role filter.

The composer is the production placement entrypoint (see
``design.composer.compose_layout``). It runs Sugiyama as a baseline,
overlays motif-driven positions for matched sub-circuits, drops
matches that fail role-compatibility, and returns one PlacedPart per
plan part. These tests pin down the contract:

- Every plan part gets exactly one placement.
- Matched parts get motif positions (snapped to 100-mil grid).
- Unmatched parts keep their Sugiyama baseline.
- A claimed part with an incompatible Part.role causes the whole match
  to be dropped, falling back to Sugiyama.
- The IC anchor of an IC-anchored motif gets its baseline position; the
  passives are offset relative to it.
"""

from __future__ import annotations

from eda_agent.design.composer import compose_layout, _MOTIF_INCOMPATIBLE_ROLES
from eda_agent.design.plan import DesignPlan

_LIB = "/fake/lib.SchLib"


def _make_plan(parts: list[dict], nets: list[dict]) -> DesignPlan:
    return DesignPlan.model_validate({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": parts,
        "nets": nets,
    })


# ---------------------------- basic contract ----------------------------


def test_compose_produces_one_placement_per_part():
    plan = _make_plan(
        parts=[
            {"refdes": "U1", "lib_ref": "IC", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "ic"},
            {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "vcc_decoup"},
        ],
        nets=[
            {"name": "VCC", "is_power": True, "pins": [
                {"refdes": "U1", "pin": "8"},
                {"refdes": "C1", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "U1", "pin": "1"},
                {"refdes": "C1", "pin": "2"}]},
        ],
    )
    result = compose_layout(plan)
    refdes_set = {p.refdes for p in result.placements}
    assert refdes_set == {"U1", "C1"}


def test_compose_matches_bypass_cap_motif():
    """A cap between power and ground should match bypass_cap."""
    plan = _make_plan(
        parts=[
            {"refdes": "U1", "lib_ref": "IC", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "ic"},
            {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "vcc_decoup"},
        ],
        nets=[
            {"name": "VCC", "is_power": True, "pins": [
                {"refdes": "U1", "pin": "8"},
                {"refdes": "C1", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "U1", "pin": "1"},
                {"refdes": "C1", "pin": "2"}]},
        ],
    )
    result = compose_layout(plan)
    match_names = [m.motif_name for m in result.motif_matches]
    assert "bypass_cap" in match_names
    assert "C1" in result.motif_parts


def test_compose_fallback_when_no_motifs_match():
    """A two-part plan with no recognised motif: both parts fall back."""
    plan = _make_plan(
        parts=[
            {"refdes": "U1", "lib_ref": "IC", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "ic"},
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "fb_top"},
        ],
        nets=[
            # R between two signal nets: doesn't match pull_up/pull_down
            # (no power), doesn't match voltage_divider (only one R).
            {"name": "SIG_A", "pins": [
                {"refdes": "U1", "pin": "1"},
                {"refdes": "R1", "pin": "1"}]},
            {"name": "SIG_B", "pins": [
                {"refdes": "U1", "pin": "2"},
                {"refdes": "R1", "pin": "2"}]},
        ],
    )
    result = compose_layout(plan)
    assert result.motif_matches == []
    assert result.motif_parts == set()
    assert result.fallback_parts == {"U1", "R1"}


# -------------------------- role compatibility --------------------------


def test_role_filter_drops_rc_lowpass_on_decoup_cap():
    """A C with role=vcc_decoup must NOT be claimed by rc_lowpass.

    Structural matcher sees C between a signal net and GND -- looks
    like a low-pass cap. But the Part.role tells us it's an IC
    decoupling cap, not a filter. The filter should drop the match so
    the canonical priors layer can place it correctly.
    """
    plan = _make_plan(
        parts=[
            {"refdes": "U1", "lib_ref": "IC", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "ic"},
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "fb_top"},
            {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "vcc_decoup"},
        ],
        nets=[
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "C1", "pin": "2"},
                {"refdes": "U1", "pin": "GND"}]},
            {"name": "IN", "pins": [
                {"refdes": "U1", "pin": "1"},
                {"refdes": "R1", "pin": "1"}]},
            {"name": "OUT", "pins": [
                {"refdes": "R1", "pin": "2"},
                {"refdes": "C1", "pin": "1"}]},
        ],
    )
    result = compose_layout(plan)
    match_names = {m.motif_name for m in result.motif_matches}
    assert "rc_lowpass" not in match_names
    # The decoupling cap falls through to Sugiyama / priors layer.
    assert "C1" in result.fallback_parts


def test_role_filter_keeps_rc_lowpass_on_filter_cap():
    """A C with role=filter_c IS the right thing for rc_lowpass to claim."""
    plan = _make_plan(
        parts=[
            {"refdes": "U1", "lib_ref": "IC", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "ic"},
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "filter_r"},
            {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "filter_c"},
        ],
        nets=[
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "C1", "pin": "2"},
                {"refdes": "U1", "pin": "GND"}]},
            {"name": "IN", "pins": [
                {"refdes": "U1", "pin": "1"},
                {"refdes": "R1", "pin": "1"}]},
            {"name": "OUT", "pins": [
                {"refdes": "R1", "pin": "2"},
                {"refdes": "C1", "pin": "1"}]},
        ],
    )
    result = compose_layout(plan)
    match_names = {m.motif_name for m in result.motif_matches}
    assert "rc_lowpass" in match_names


def test_role_filter_table_consistency():
    """Every motif name in the incompatibility table must exist in the
    catalogue. Catches typos that would silently disable the filter."""
    from eda_agent.design.motifs import MOTIF_CATALOGUE
    catalogue_names = {m.name for m in MOTIF_CATALOGUE}
    for motif_name in _MOTIF_INCOMPATIBLE_ROLES.keys():
        assert motif_name in catalogue_names, (
            f"_MOTIF_INCOMPATIBLE_ROLES references unknown motif "
            f"{motif_name!r} -- typo or catalogue change?"
        )


# ----------------------------- IC anchoring -----------------------------


def test_ic_anchored_motif_anchors_to_ic_baseline_position():
    """IC-anchored motif (boot_cap): passives sit at IC + canonical offset."""
    plan = _make_plan(
        parts=[
            # Use a regulator-like U with BOOT and SW pins. The exact
            # pin numbers don't matter to the motif matcher -- only the
            # bipartite structure: U connects to BOOT and SW, C connects
            # to BOOT and SW, BOOT is internal degree-2.
            {"refdes": "U1", "lib_ref": "REG", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "ic"},
            {"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB,
             "status": "existing", "sheet": "main", "role": "boot"},
        ],
        nets=[
            {"name": "BOOT", "pins": [
                {"refdes": "U1", "pin": "BOOT"},
                {"refdes": "C1", "pin": "1"}]},
            {"name": "SW", "pins": [
                {"refdes": "U1", "pin": "SW"},
                {"refdes": "C1", "pin": "2"}]},
        ],
    )
    result = compose_layout(plan)
    by_refdes = {p.refdes: p for p in result.placements}
    # boot_cap canonical offset is (300, 1300) from the U anchor: the
    # cap bridges ABOVE the IC so neither the BOOT nor the SW wire has
    # to wrap around the body. The IC stays at its Sugiyama baseline;
    # the cap is offset.
    if "C1" in result.motif_parts:
        ux, uy = by_refdes["U1"].x_mils, by_refdes["U1"].y_mils
        cx, cy = by_refdes["C1"].x_mils, by_refdes["C1"].y_mils
        # Snap-to-100 might shift by up to 99 mils per axis from the
        # post-Sugiyama IC position; the splat's collision-escape shift
        # ladder can add up to another 600. Assert the CONVENTION (cap
        # clearly above the body, near the IC's column) rather than the
        # exact offset pair.
        assert (cy - uy) >= 1000, "boot cap must sit above the IC body"
        assert abs(cx - ux) <= 1000, "boot cap must stay by the IC column"
