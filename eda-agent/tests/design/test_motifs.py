# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Motif recognition tests, pure Python, no Altium round-trips."""

from __future__ import annotations

import pytest

from eda_agent.design.motifs import (
    BOOT_CAP,
    BYPASS_CAP,
    CRYSTAL_LOAD,
    DIODE_BRIDGE,
    FB_DIVIDER,
    LC_OUTPUT,
    MOTIF_CATALOGUE,
    OPAMP_INVERTING,
    PI_FILTER,
    PULL_DOWN_R,
    PULL_UP_R,
    RC_COMPENSATION,
    RC_HIGHPASS,
    RC_LOWPASS,
    RC_SNUBBER,
    VOLTAGE_DIVIDER,
    Match,
    Motif,
    build_circuit_graph,
    find_all_matches,
    find_motif_matches,
    recognize_motifs,
    resolve_matches,
    splat_motif,
)
from eda_agent.design.motifs import (
    _infer_power_nets,
    _kind_from_refdes,
    _net_role_tag,
)
from eda_agent.design.plan import (
    DesignPlan,
    Net,
    Part,
    PinRef,
    Sheet,
    Zone,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _net(name: str, pins: list[tuple[str, str]], **kw) -> Net:
    return Net(name=name, pins=[PinRef(refdes=r, pin=p) for r, p in pins], **kw)


def _plan(parts: list[Part], nets: list[Net], zones: list[Zone] | None = None) -> DesignPlan:
    return DesignPlan(
        spec="motif test",
        summary="motif test plan",
        sheets=[Sheet(name="main")],
        zones=zones or [],
        parts=parts,
        nets=nets,
    )


# ---------------------------------------------------------------------------
# Helpers tested directly
# ---------------------------------------------------------------------------


def _ldo_plan(flag_power: bool):
    """LDO: J1(in)->C1->U1->C2->R1->D1->J2(out). VIN/VOUT power flag toggled."""
    extra_vin = {"is_power": True} if flag_power else {}
    extra_vout = {"is_power": True} if flag_power else {}
    parts = [
        Part(refdes="J1", lib_ref="HDR", role="input_conn", status="existing"),
        Part(refdes="C1", lib_ref="CAP", status="existing"),
        Part(refdes="U1", lib_ref="LDO", status="existing"),
        Part(refdes="C2", lib_ref="CAP", status="existing"),
        Part(refdes="R1", lib_ref="RES", status="existing"),
        Part(refdes="D1", lib_ref="LED", status="existing"),
        Part(refdes="J2", lib_ref="HDR", role="output_conn", status="existing"),
    ]
    nets = [
        _net("VIN", [("J1", "1"), ("C1", "1"), ("U1", "1")], **extra_vin),
        _net("GND", [("J1", "2"), ("C1", "2"), ("U1", "2"), ("C2", "2"),
                     ("D1", "2")], is_ground=True),
        _net("VOUT", [("U1", "3"), ("C2", "1"), ("R1", "1"), ("J2", "1")],
             **extra_vout),
        _net("LEDK", [("R1", "2"), ("D1", "1")]),
    ]
    return _plan(parts, nets)


def test_infer_power_catches_connector_fed_rail():
    """A raw input / regulator-output rail the planner forgot to flag is found
    structurally: it carries a decoupling cap to ground AND reaches a power
    connector."""
    plan = _ldo_plan(flag_power=False)
    assert _infer_power_nets(plan) == {"VIN", "VOUT"}


def test_infer_power_ignores_filter_node():
    """An RC filter's output node has the same cap-to-ground signature but
    touches no power connector, so it is NOT promoted -- the rc_lowpass motif
    must keep matching it."""
    parts = [
        Part(refdes="J1", lib_ref="HDR", role="input_conn", status="existing"),
        Part(refdes="R1", lib_ref="RES", status="existing"),
        Part(refdes="C1", lib_ref="CAP", status="existing"),
        Part(refdes="U1", lib_ref="OPAMP", status="existing"),
    ]
    nets = [
        _net("SIGIN", [("J1", "1"), ("R1", "1")]),
        _net("FILT", [("R1", "2"), ("C1", "1"), ("U1", "3")]),
        _net("GND", [("J1", "2"), ("C1", "2"), ("U1", "4")], is_ground=True),
        _net("VCC", [("U1", "7"), ("R1", "1")], is_power=True),
    ]
    plan = _plan(parts, nets)
    assert "FILT" not in _infer_power_nets(plan)
    names = {m.motif_name for m in recognize_motifs(plan)}
    assert "rc_lowpass" in names      # real filter still recognised


def test_unflagged_ldo_recognises_bypass_not_false_filter():
    """End to end: with VIN/VOUT unflagged, the structural inference still makes
    both decoupling caps read as bypass_cap and suppresses the rc_lowpass false
    positive that C2 + the LED resistor would otherwise trigger."""
    got = {(m.motif_name, frozenset(m.components))
           for m in recognize_motifs(_ldo_plan(flag_power=False))}
    assert ("bypass_cap", frozenset({"C1"})) in got
    assert ("bypass_cap", frozenset({"C2"})) in got
    assert not any(name == "rc_lowpass" for name, _ in got)
    # And it matches what explicitly flagging the rails would have produced.
    flagged = {(m.motif_name, frozenset(m.components))
               for m in recognize_motifs(_ldo_plan(flag_power=True))}
    assert got == flagged


def test_kind_from_refdes_known_prefixes() -> None:
    assert _kind_from_refdes("R1") == "R"
    assert _kind_from_refdes("C12") == "C"
    assert _kind_from_refdes("L3") == "L"
    assert _kind_from_refdes("D2") == "D"
    assert _kind_from_refdes("U3") == "U"
    assert _kind_from_refdes("Y1") == "Y"
    assert _kind_from_refdes("FB1") == "FB"
    assert _kind_from_refdes("Q4") == "Q"
    assert _kind_from_refdes("TVS1") == "D"


def test_kind_from_refdes_unknown_prefix_passes_through() -> None:
    # Unknown but valid alphabetic prefix is returned as-is (uppercased)
    # so it remains visible to the matcher rather than silently becoming
    # a wildcard.
    assert _kind_from_refdes("ZZ7") == "ZZ"


def test_net_role_tag_power_wins_over_role() -> None:
    n = _net("VCC", [("R1", "1"), ("R2", "1")], is_power=True, role="custom")
    assert _net_role_tag(n) == "power"


def test_net_role_tag_ground_wins_over_role() -> None:
    n = _net("GND", [("R1", "1"), ("R2", "1")], is_ground=True, role="custom")
    assert _net_role_tag(n) == "ground"


def test_net_role_tag_explicit_role() -> None:
    n = _net("FB", [("R1", "1"), ("R2", "1")], role="feedback")
    assert _net_role_tag(n) == "feedback"


def test_net_role_tag_defaults_to_signal() -> None:
    n = _net("X", [("R1", "1"), ("R2", "1")])
    assert _net_role_tag(n) == "signal"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def test_build_circuit_graph_emits_bipartite_nodes_and_edges() -> None:
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="C1", lib_ref="CAP"),
        ],
        nets=[
            _net("VCC", [("R1", "1"), ("C1", "1")], is_power=True),
            _net("GND", [("R1", "2"), ("C1", "2")], is_ground=True),
        ],
    )
    G = build_circuit_graph(plan)
    assert G.has_node(("C", "R1"))
    assert G.has_node(("C", "C1"))
    assert G.has_node(("N", "VCC"))
    assert G.has_node(("N", "GND"))
    assert G.nodes[("C", "R1")]["kind"] == "R"
    assert G.nodes[("C", "C1")]["kind"] == "C"
    assert G.nodes[("N", "VCC")]["role"] == "power"
    assert G.nodes[("N", "GND")]["role"] == "ground"
    assert G.has_edge(("C", "R1"), ("N", "VCC"))
    assert G.has_edge(("C", "C1"), ("N", "GND"))


def test_build_circuit_graph_carries_zone_attr() -> None:
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES", zone="buck"),
            Part(refdes="C1", lib_ref="CAP"),
        ],
        nets=[
            _net("X", [("R1", "1"), ("C1", "1")]),
        ],
        zones=[Zone(name="buck", sheet="main")],
    )
    G = build_circuit_graph(plan)
    assert G.nodes[("C", "R1")]["zone"] == "buck"
    assert G.nodes[("C", "C1")]["zone"] is None


# ---------------------------------------------------------------------------
# Motif matches: positive cases
# ---------------------------------------------------------------------------


def test_bypass_cap_matches_cap_between_rail_and_ground() -> None:
    plan = _plan(
        parts=[
            Part(refdes="C1", lib_ref="CAP"),
            Part(refdes="U1", lib_ref="IC"),  # so VCC/GND aren't degenerate
        ],
        nets=[
            _net("VCC", [("C1", "1"), ("U1", "1")], is_power=True),
            _net("GND", [("C1", "2"), ("U1", "2")], is_ground=True),
        ],
    )
    matches = list(
        find_motif_matches(build_circuit_graph(plan), BYPASS_CAP)
    )
    assert len(matches) == 1
    assert matches[0].components == {"C1"}


def test_bypass_cap_does_not_match_cap_between_signals() -> None:
    plan = _plan(
        parts=[
            Part(refdes="C1", lib_ref="CAP"),
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="R2", lib_ref="RES"),
        ],
        nets=[
            _net("SIG_A", [("C1", "1"), ("R1", "1")]),
            _net("SIG_B", [("C1", "2"), ("R2", "1")]),
        ],
    )
    assert list(
        find_motif_matches(build_circuit_graph(plan), BYPASS_CAP)
    ) == []


def test_voltage_divider_matches_pure_r_r_mid_tap() -> None:
    # U1 is here only to give VCC and GND >=2 pins each (schema requires
    # Net.pins min_length=2). The motif claims R1+R2.
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="R2", lib_ref="RES"),
            Part(refdes="U1", lib_ref="IC"),
        ],
        nets=[
            _net("VCC", [("R1", "1"), ("U1", "1")], is_power=True),
            _net("MID", [("R1", "2"), ("R2", "1")]),
            _net("GND", [("R2", "2"), ("U1", "2")], is_ground=True),
        ],
    )
    matches = list(
        find_motif_matches(build_circuit_graph(plan), VOLTAGE_DIVIDER)
    )
    assert len(matches) == 1
    assert matches[0].components == {"R1", "R2"}


def test_voltage_divider_rejects_external_fanout_on_mid() -> None:
    """If MID picks up a third pin (e.g. a U.FB), MID is no longer
    internal and voltage_divider must not match. This is the case we'll
    handle in a future fb_divider motif."""
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="R2", lib_ref="RES"),
            Part(refdes="U1", lib_ref="IC"),
        ],
        nets=[
            _net("VCC", [("R1", "1"), ("U1", "1")], is_power=True),
            _net("MID", [("R1", "2"), ("R2", "1"), ("U1", "FB")]),
            _net("GND", [("R2", "2"), ("U1", "2")], is_ground=True),
        ],
    )
    matches = list(
        find_motif_matches(build_circuit_graph(plan), VOLTAGE_DIVIDER)
    )
    assert matches == []


def test_pull_up_r_matches_r_between_power_and_signal() -> None:
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="U1", lib_ref="IC"),
        ],
        nets=[
            _net("VCC", [("R1", "1"), ("U1", "1")], is_power=True),
            _net("RESET_N", [("R1", "2"), ("U1", "10")]),
        ],
    )
    matches = list(
        find_motif_matches(build_circuit_graph(plan), PULL_UP_R)
    )
    assert len(matches) == 1
    assert matches[0].components == {"R1"}


def test_pull_down_r_matches_r_between_signal_and_ground() -> None:
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="U1", lib_ref="IC"),
        ],
        nets=[
            _net("BOOT", [("R1", "1"), ("U1", "10")]),
            _net("GND", [("R1", "2"), ("U1", "2")], is_ground=True),
        ],
    )
    matches = list(
        find_motif_matches(build_circuit_graph(plan), PULL_DOWN_R)
    )
    assert len(matches) == 1
    assert matches[0].components == {"R1"}


def test_rc_snubber_matches_rc_series_between_two_signals() -> None:
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="C1", lib_ref="CAP"),
            Part(refdes="U1", lib_ref="IC"),
        ],
        nets=[
            _net("SW", [("R1", "1"), ("U1", "1")]),
            _net("SNUB_MID", [("R1", "2"), ("C1", "1")]),
            _net("RTN", [("C1", "2"), ("U1", "2")]),
        ],
    )
    matches = list(
        find_motif_matches(build_circuit_graph(plan), RC_SNUBBER)
    )
    assert len(matches) == 1
    assert matches[0].components == {"R1", "C1"}


# ---------------------------------------------------------------------------
# Zone coherence
# ---------------------------------------------------------------------------


def test_zone_coherence_rejects_cross_zone_match() -> None:
    """R1 in 'buck', R2 in 'amp' don't form a divider even if the
    topology looks right."""
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES", zone="buck"),
            Part(refdes="R2", lib_ref="RES", zone="amp"),
            Part(refdes="U1", lib_ref="IC"),
        ],
        nets=[
            _net("VCC", [("R1", "1"), ("U1", "1")], is_power=True),
            _net("MID", [("R1", "2"), ("R2", "1")]),
            _net("GND", [("R2", "2"), ("U1", "2")], is_ground=True),
        ],
        zones=[Zone(name="buck", sheet="main"), Zone(name="amp", sheet="main")],
    )
    matches = list(
        find_motif_matches(build_circuit_graph(plan), VOLTAGE_DIVIDER)
    )
    assert matches == []


def test_zone_coherence_allows_same_zone_match() -> None:
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES", zone="buck"),
            Part(refdes="R2", lib_ref="RES", zone="buck"),
            Part(refdes="U1", lib_ref="IC", zone="buck"),
        ],
        nets=[
            _net("VCC", [("R1", "1"), ("U1", "1")], is_power=True),
            _net("MID", [("R1", "2"), ("R2", "1")]),
            _net("GND", [("R2", "2"), ("U1", "2")], is_ground=True),
        ],
        zones=[Zone(name="buck", sheet="main")],
    )
    matches = list(
        find_motif_matches(build_circuit_graph(plan), VOLTAGE_DIVIDER)
    )
    assert len(matches) == 1
    assert matches[0].components == {"R1", "R2"}


def test_zone_coherence_can_be_disabled() -> None:
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES", zone="buck"),
            Part(refdes="R2", lib_ref="RES", zone="amp"),
            Part(refdes="U1", lib_ref="IC"),
        ],
        nets=[
            _net("VCC", [("R1", "1"), ("U1", "1")], is_power=True),
            _net("MID", [("R1", "2"), ("R2", "1")]),
            _net("GND", [("R2", "2"), ("U1", "2")], is_ground=True),
        ],
        zones=[Zone(name="buck", sheet="main"), Zone(name="amp", sheet="main")],
    )
    matches = list(
        find_motif_matches(
            build_circuit_graph(plan), VOLTAGE_DIVIDER, require_same_zone=False,
        )
    )
    assert len(matches) == 1


# ---------------------------------------------------------------------------
# Overlap resolution
# ---------------------------------------------------------------------------


def test_resolve_voltage_divider_wins_over_pull_up_pull_down() -> None:
    """Rtop of a divider also matches pull_up; Rbot also matches pull_down.
    The divider has higher specificity AND more components, so it claims
    both Rs and the pull_up/pull_down matches lose."""
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="R2", lib_ref="RES"),
            Part(refdes="U1", lib_ref="IC"),
        ],
        nets=[
            _net("VCC", [("R1", "1"), ("U1", "1")], is_power=True),
            _net("MID", [("R1", "2"), ("R2", "1")]),
            _net("GND", [("R2", "2"), ("U1", "2")], is_ground=True),
        ],
    )
    all_matches = find_all_matches(plan)
    # Sanity: both kinds of match are found before arbitration.
    motif_names = {m.motif_name for m in all_matches}
    assert "voltage_divider" in motif_names
    assert "pull_up_r" in motif_names
    assert "pull_down_r" in motif_names

    kept = resolve_matches(all_matches)
    kept_names = {m.motif_name for m in kept}
    assert kept_names == {"voltage_divider"}


def test_resolve_independent_motifs_both_kept() -> None:
    """Two separate bypass caps (different rails, different parts) both
    survive arbitration."""
    plan = _plan(
        parts=[
            Part(refdes="C1", lib_ref="CAP"),
            Part(refdes="C2", lib_ref="CAP"),
            Part(refdes="U1", lib_ref="IC"),
        ],
        nets=[
            _net("V3V3", [("C1", "1"), ("U1", "1")], is_power=True),
            _net("V5", [("C2", "1"), ("U1", "3")], is_power=True),
            _net(
                "GND",
                [("C1", "2"), ("C2", "2"), ("U1", "2")],
                is_ground=True,
            ),
        ],
    )
    kept = resolve_matches(find_all_matches(plan))
    bypass_matches = [m for m in kept if m.motif_name == "bypass_cap"]
    assert len(bypass_matches) == 2
    claimed = set().union(*(m.components for m in bypass_matches))
    assert claimed == {"C1", "C2"}


# ---------------------------------------------------------------------------
# Splat
# ---------------------------------------------------------------------------


def test_splat_motif_resolves_canonical_offsets_to_absolute_positions() -> None:
    plan = _plan(
        parts=[
            Part(refdes="R5", lib_ref="RES"),
            Part(refdes="R7", lib_ref="RES"),
            Part(refdes="U1", lib_ref="IC"),
        ],
        nets=[
            _net("VCC", [("R5", "1"), ("U1", "1")], is_power=True),
            _net("MID", [("R5", "2"), ("R7", "1")]),
            _net("GND", [("R7", "2"), ("U1", "2")], is_ground=True),
        ],
    )
    matches = list(
        find_motif_matches(build_circuit_graph(plan), VOLTAGE_DIVIDER)
    )
    assert len(matches) == 1
    placement = splat_motif(matches[0], VOLTAGE_DIVIDER, anchor=(5000, 4000))

    # Canonical for voltage_divider: Rtop at (0, 0), Rbot at (0, -1000).
    # Either ordering of R5/R7 to Rtop/Rbot is acceptable -- the pattern
    # is symmetric in pin numbering. We just check the geometry.
    assert placement.anchor_x_mils == 5000
    assert placement.anchor_y_mils == 4000
    assert set(placement.parts) == {"R5", "R7"}
    positions = sorted(placement.parts.values(), key=lambda p: p[1])
    # The lower-y resistor is Rbot at (5000, 3000), the upper is Rtop
    # at (5000, 4000).
    assert positions[0] == (5000, 3000)
    assert positions[1] == (5000, 4000)


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def test_recognize_motifs_end_to_end() -> None:
    """A small mixed plan: one divider, one bypass cap on the same rail.
    Both should survive arbitration (no shared parts)."""
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="R2", lib_ref="RES"),
            Part(refdes="C1", lib_ref="CAP"),
            Part(refdes="U1", lib_ref="IC"),
        ],
        nets=[
            _net("VCC", [("R1", "1"), ("C1", "1"), ("U1", "1")], is_power=True),
            _net("MID", [("R1", "2"), ("R2", "1")]),
            _net(
                "GND",
                [("R2", "2"), ("C1", "2"), ("U1", "2")],
                is_ground=True,
            ),
        ],
    )
    kept = recognize_motifs(plan)
    names = sorted(m.motif_name for m in kept)
    assert names == ["bypass_cap", "voltage_divider"]
    claimed: set[str] = set()
    for m in kept:
        assert not (m.components & claimed)
        claimed.update(m.components)
    assert claimed == {"R1", "R2", "C1"}
    # U1 isn't claimed by any motif -- it's a singleton meta.


# ---------------------------------------------------------------------------
# Catalogue sanity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("motif", MOTIF_CATALOGUE)
def test_each_catalogue_motif_has_canonical_for_every_pattern_component(motif: Motif) -> None:
    """canonical positions every pattern component EXCEPT the
    ``ic_anchor`` U (which keeps its placed position from FD)."""
    pattern_components = {
        n[1] for n in motif.pattern.nodes if n[0] == "C"
    }
    expected = pattern_components - (
        {motif.ic_anchor} if motif.ic_anchor is not None else set()
    )
    assert set(motif.canonical) == expected, (
        f"{motif.name}: canonical={set(motif.canonical)} "
        f"vs expected={expected}"
    )


# ---------------------------------------------------------------------------
# IC-anchored motifs (Phase B.3)
# ---------------------------------------------------------------------------


def test_fb_divider_matches_regulator_feedback_divider() -> None:
    """R-R-mid-tap where the mid node also touches a U pin (FB_NODE
    is degree-3 internal)."""
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="R2", lib_ref="RES"),
            Part(refdes="U1", lib_ref="REG"),
        ],
        nets=[
            _net("VOUT", [("R1", "1"), ("U1", "3")], is_power=True),
            _net("FB", [("R1", "2"), ("R2", "1"), ("U1", "5")]),
            _net("GND", [("R2", "2"), ("U1", "2")], is_ground=True),
        ],
    )
    matches = list(
        find_motif_matches(build_circuit_graph(plan), FB_DIVIDER)
    )
    assert len(matches) == 1
    assert matches[0].components == {"R1", "R2", "U1"}


def test_fb_divider_does_not_match_pure_divider_without_u() -> None:
    """voltage_divider's R-R-mid is degree-2 internal; fb_divider
    requires the mid to also have a U pin (degree-3)."""
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="R2", lib_ref="RES"),
            Part(refdes="U1", lib_ref="LOAD"),
        ],
        nets=[
            _net("VCC", [("R1", "1"), ("U1", "1")], is_power=True),
            _net("MID", [("R1", "2"), ("R2", "1")]),
            _net("GND", [("R2", "2"), ("U1", "2")], is_ground=True),
        ],
    )
    matches = list(
        find_motif_matches(build_circuit_graph(plan), FB_DIVIDER)
    )
    assert matches == []


def test_fb_divider_wins_over_voltage_divider_when_u_pin_present() -> None:
    """A regulator's FB divider could in principle match voltage_divider
    (no it can't: voltage_divider requires MID degree=2, fb_divider's
    MID is degree-3). So only fb_divider wins by construction."""
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="R2", lib_ref="RES"),
            Part(refdes="U1", lib_ref="REG"),
        ],
        nets=[
            _net("VOUT", [("R1", "1"), ("U1", "3")], is_power=True),
            _net("FB", [("R1", "2"), ("R2", "1"), ("U1", "5")]),
            _net("GND", [("R2", "2"), ("U1", "2")], is_ground=True),
        ],
    )
    kept = recognize_motifs(plan)
    names = sorted(m.motif_name for m in kept)
    assert "fb_divider" in names
    assert "voltage_divider" not in names


def _diode_bridge_plan():
    """J1 (AC in) -> 4-diode bridge -> C1 smoothing -> U1. The bridge is a
    bipartite 4-cycle: AC1 -D1- VPLUS -D2- AC2 -D4- VMINUS -D3- AC1."""
    parts = [
        Part(refdes="J1", lib_ref="HDR"),
        Part(refdes="D1", lib_ref="DIODE"),
        Part(refdes="D2", lib_ref="DIODE"),
        Part(refdes="D3", lib_ref="DIODE"),
        Part(refdes="D4", lib_ref="DIODE"),
        Part(refdes="C1", lib_ref="CAP"),
        Part(refdes="U1", lib_ref="REG"),
    ]
    nets = [
        _net("AC1", [("J1", "1"), ("D1", "1"), ("D3", "2")]),
        _net("AC2", [("J1", "2"), ("D2", "1"), ("D4", "2")]),
        _net("VPLUS", [("D1", "2"), ("D2", "2"), ("C1", "1"), ("U1", "1")],
             is_power=True),
        _net("VMINUS", [("D3", "1"), ("D4", "1"), ("C1", "2"), ("U1", "2")],
             is_ground=True),
    ]
    return _plan(parts, nets)


def test_diode_bridge_matches_four_diode_bridge() -> None:
    """Four diodes in the rectifier 4-cycle resolve to a diode_bridge over
    {D1..D4}. The bridge is symmetric (AC1<->AC2), so VF2 reports several
    automorphic orderings covering the same component set."""
    plan = _diode_bridge_plan()
    matches = list(find_motif_matches(build_circuit_graph(plan), DIODE_BRIDGE))
    assert matches, "expected a diode_bridge match"
    comp_sets = {frozenset(m.components) for m in matches}
    assert comp_sets == {frozenset({"D1", "D2", "D3", "D4"})}


def test_diode_bridge_rejects_esd_diode_array() -> None:
    """Four diodes in a STAR to one ground (an ESD clamp array) is not a
    bridge -- there is no second DC rail and no 4-cycle."""
    plan = _plan(
        parts=[
            Part(refdes="U1", lib_ref="MCU"),
            Part(refdes="D1", lib_ref="DIODE"),
            Part(refdes="D2", lib_ref="DIODE"),
            Part(refdes="D3", lib_ref="DIODE"),
            Part(refdes="D4", lib_ref="DIODE"),
        ],
        nets=[
            _net("SIG1", [("U1", "1"), ("D1", "1")]),
            _net("SIG2", [("U1", "2"), ("D2", "1")]),
            _net("SIG3", [("U1", "3"), ("D3", "1")]),
            _net("SIG4", [("U1", "4"), ("D4", "1")]),
            _net("GND", [("D1", "2"), ("D2", "2"), ("D3", "2"),
                         ("D4", "2"), ("U1", "5")], is_ground=True),
        ],
    )
    matches = list(find_motif_matches(build_circuit_graph(plan), DIODE_BRIDGE))
    assert matches == []


def test_recognize_diode_bridge_end_to_end() -> None:
    """The full pipeline recognises the bridge as one unit."""
    kept = recognize_motifs(_diode_bridge_plan())
    names = sorted(m.motif_name for m in kept)
    assert "diode_bridge" in names


def _pi_filter_plan(with_output_cap: bool = True, stray_decap: bool = False):
    """J1 -> Cin -> L -> Cout -> U1 power-rail pi filter. ``with_output_cap``
    drops C2; ``stray_decap`` puts C1 on an UNRELATED rail (not the inductor's
    input net) to prove it can't stand in as the input cap."""
    parts = [
        Part(refdes="J1", lib_ref="HDR"),
        Part(refdes="C1", lib_ref="CAP"),
        Part(refdes="L1", lib_ref="IND"),
        Part(refdes="U1", lib_ref="LOAD"),
    ]
    nets = [
        _net("VIN", [("J1", "1"), ("L1", "1")]
             + ([] if stray_decap else [("C1", "1")]), is_power=True),
        _net("VFILT", [("L1", "2"), ("U1", "1")], is_power=True),
        _net("GND", [("J1", "2"), ("C1", "2"), ("U1", "2")], is_ground=True),
    ]
    if stray_decap:
        # C1 lives on a separate rail (with its own load), NOT VIN.
        parts.append(Part(refdes="U2", lib_ref="LOAD"))
        nets.append(_net("VAUX", [("C1", "1"), ("U2", "1")], is_power=True))
        nets[2].pins.append(PinRef(refdes="U2", pin="2"))
    if with_output_cap:
        parts.append(Part(refdes="C2", lib_ref="CAP"))
        # C2 from VFILT to GND -- the output cap.
        nets[1].pins.append(PinRef(refdes="C2", pin="1"))
        nets[2].pins.append(PinRef(refdes="C2", pin="2"))
    return _plan(parts, nets)


def test_pi_filter_matches_c_l_c() -> None:
    """Two caps to ground bridged by a series inductor -> a pi_filter over
    {C1, L1, C2}. The motif is symmetric (Cin<->Cout), so VF2 reports both
    automorphic orderings; they cover the same component set, and resolution
    keeps one."""
    plan = _pi_filter_plan()
    matches = list(find_motif_matches(build_circuit_graph(plan), PI_FILTER))
    assert matches, "expected a pi_filter match"
    comp_sets = {frozenset(m.components) for m in matches}
    assert comp_sets == {frozenset({"C1", "L1", "C2"})}


def test_pi_filter_rejects_single_cap_lc() -> None:
    """One cap + inductor (an LC, not a pi) does not match -- pi needs the
    second cap on the inductor's OUTPUT net."""
    plan = _pi_filter_plan(with_output_cap=False)
    matches = list(find_motif_matches(build_circuit_graph(plan), PI_FILTER))
    assert matches == []


def test_pi_filter_input_cap_must_share_inductor_net() -> None:
    """A decoupling cap on an unrelated rail cannot serve as the input cap --
    the input cap must sit on the SAME net as one inductor terminal."""
    plan = _pi_filter_plan(stray_decap=True)
    matches = list(find_motif_matches(build_circuit_graph(plan), PI_FILTER))
    assert matches == []


def test_pi_filter_resolves_over_two_bypass_caps() -> None:
    """Both pi caps would each match bypass_cap; pi_filter (higher specificity,
    3 components) claims all three, so the filter is recognised as ONE unit
    and its caps are not scattered as independent bypass caps."""
    kept = recognize_motifs(_pi_filter_plan())
    names = sorted(m.motif_name for m in kept)
    assert "pi_filter" in names
    assert "bypass_cap" not in names


def _inverting_amp_plan():
    """Inverting op-amp: Rin from VIN to the summing node (U1.IN-), Rf from
    the summing node back to VOUT (U1.OUT). IN+ tied to GND. SUMMING is the
    virtual-ground inverting input -- degree-3 (Rin + Rf + U1)."""
    parts = [
        Part(refdes="J1", lib_ref="HDR"),
        Part(refdes="R1", lib_ref="RES"),   # input resistor (Rin)
        Part(refdes="R2", lib_ref="RES"),   # feedback resistor (Rf)
        Part(refdes="U1", lib_ref="OPAMP"),
        Part(refdes="J2", lib_ref="HDR"),
    ]
    nets = [
        _net("VIN", [("J1", "1"), ("R1", "1")]),
        _net("SUMMING", [("R1", "2"), ("R2", "1"), ("U1", "2")]),
        _net("VOUT", [("R2", "2"), ("U1", "1"), ("J2", "1")]),
        _net("GND", [("U1", "3"), ("J1", "2"), ("J2", "2")], is_ground=True),
    ]
    return _plan(parts, nets)


def test_opamp_inverting_matches_inverting_amp() -> None:
    """Rin-Rf around an op-amp with the summing node degree-3 internal."""
    plan = _inverting_amp_plan()
    matches = list(
        find_motif_matches(build_circuit_graph(plan), OPAMP_INVERTING)
    )
    assert len(matches) == 1
    assert matches[0].components == {"R1", "R2", "U1"}


def test_opamp_inverting_matches_when_output_has_a_signal_subtype_role() -> None:
    """A motif's generic "signal" net must still match a net the planner tagged
    with a specific signal SUBTYPE (analog_sensitive, clock, ...). Tagging the
    op-amp output analog_sensitive (a documented role hint) must NOT break
    opamp_inverting recognition."""
    parts = [
        Part(refdes="J1", lib_ref="HDR"), Part(refdes="R1", lib_ref="RES"),
        Part(refdes="R2", lib_ref="RES"), Part(refdes="U1", lib_ref="OPAMP"),
        Part(refdes="J2", lib_ref="HDR"),
    ]
    nets = [
        _net("VIN", [("J1", "1"), ("R1", "1")], role="analog_sensitive"),
        _net("SUMMING", [("R1", "2"), ("R2", "1"), ("U1", "2")]),
        # Output carries a specific signal subtype, not the bare "signal" role.
        _net("VOUT", [("R2", "2"), ("U1", "1"), ("J2", "1")],
             role="analog_sensitive"),
        _net("GND", [("U1", "3"), ("J1", "2"), ("J2", "2")], is_ground=True),
    ]
    matches = list(find_motif_matches(
        build_circuit_graph(_plan(parts, nets)), OPAMP_INVERTING))
    assert len(matches) == 1
    assert matches[0].components == {"R1", "R2", "U1"}


def test_signal_motif_still_rejects_power_and_ground() -> None:
    """The signal-family widening must NOT let a "signal" net match power or
    ground -- those are not signals."""
    from eda_agent.design.motifs import _node_match
    sig = {"bipartite": "net", "role": "signal"}
    assert _node_match({"bipartite": "net", "role": "clock"}, sig) is True
    assert _node_match({"bipartite": "net", "role": "analog_sensitive"}, sig)
    assert _node_match({"bipartite": "net", "role": "power"}, sig) is False
    assert _node_match({"bipartite": "net", "role": "ground"}, sig) is False


def test_opamp_inverting_does_not_match_feedback_divider() -> None:
    """A regulator feedback divider has its lower resistor on GROUND, so the
    opamp's signal-role VIN cannot map onto that ground net -- no false match
    (this is what keeps fb_divider and opamp_inverting disjoint)."""
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="R2", lib_ref="RES"),
            Part(refdes="U1", lib_ref="REG"),
        ],
        nets=[
            _net("VOUT", [("R1", "1"), ("U1", "3")], is_power=True),
            _net("FB", [("R1", "2"), ("R2", "1"), ("U1", "5")]),
            _net("GND", [("R2", "2"), ("U1", "2")], is_ground=True),
        ],
    )
    matches = list(
        find_motif_matches(build_circuit_graph(plan), OPAMP_INVERTING)
    )
    assert matches == []


def test_fb_divider_does_not_match_inverting_amp() -> None:
    """Symmetric guard: the inverting amp has no resistor to ground and a
    signal (not power) output, so fb_divider cannot claim it."""
    plan = _inverting_amp_plan()
    matches = list(
        find_motif_matches(build_circuit_graph(plan), FB_DIVIDER)
    )
    assert matches == []


def test_recognize_opamp_inverting_end_to_end() -> None:
    """Through the full pipeline the inverting amp resolves to opamp_inverting
    and not voltage_divider (the two R's share a degree-3 summing node, which
    voltage_divider's degree-2 MID rejects)."""
    kept = recognize_motifs(_inverting_amp_plan())
    names = sorted(m.motif_name for m in kept)
    assert "opamp_inverting" in names
    assert "voltage_divider" not in names


def test_boot_cap_matches_cap_between_two_u_pins() -> None:
    """Bootstrap cap: C with both terminals on the SAME U (BOOT and SW).
    BOOT is degree-2 internal (just C + U)."""
    plan = _plan(
        parts=[
            Part(refdes="C1", lib_ref="CAP"),
            Part(refdes="U1", lib_ref="REG"),
            # Inductor on SW so SW has degree 3 -- C, U, L -- not 2.
            Part(refdes="L1", lib_ref="IND"),
        ],
        nets=[
            _net("BOOT", [("C1", "1"), ("U1", "1")]),
            _net("SW", [("C1", "2"), ("U1", "8"), ("L1", "1")]),
            # Schema needs every part on something -- give L a power net.
            _net(
                "VOUT",
                [("L1", "2"), ("U1", "3")],
                is_power=True,
            ),
        ],
    )
    matches = list(
        find_motif_matches(build_circuit_graph(plan), BOOT_CAP)
    )
    assert len(matches) == 1
    assert matches[0].components == {"C1", "U1"}


def test_lc_output_matches_inductor_and_cap_at_switch_node() -> None:
    plan = _plan(
        parts=[
            Part(refdes="L1", lib_ref="IND"),
            Part(refdes="C1", lib_ref="CAP"),
            Part(refdes="U1", lib_ref="REG"),
        ],
        nets=[
            _net("SW", [("L1", "1"), ("U1", "8")]),
            _net("VOUT", [("L1", "2"), ("C1", "1"), ("U1", "3")], is_power=True),
            _net("GND", [("C1", "2"), ("U1", "2")], is_ground=True),
        ],
    )
    matches = list(
        find_motif_matches(build_circuit_graph(plan), LC_OUTPUT)
    )
    assert len(matches) == 1
    assert matches[0].components == {"L1", "C1", "U1"}


def test_crystal_load_matches_xtal_with_two_caps() -> None:
    plan = _plan(
        parts=[
            Part(refdes="Y1", lib_ref="XTAL"),
            Part(refdes="C1", lib_ref="CAP"),
            Part(refdes="C2", lib_ref="CAP"),
            Part(refdes="U1", lib_ref="MCU"),
        ],
        nets=[
            _net("XIN", [("Y1", "1"), ("C1", "1"), ("U1", "10")]),
            _net("XOUT", [("Y1", "2"), ("C2", "1"), ("U1", "11")]),
            _net(
                "GND",
                [("C1", "2"), ("C2", "2"), ("U1", "2")],
                is_ground=True,
            ),
        ],
    )
    matches = list(
        find_motif_matches(build_circuit_graph(plan), CRYSTAL_LOAD)
    )
    # Y has 2 pins (symmetric) -- pattern might find both (Cx,Cy) and
    # (Cy,Cx) labelings. Both label the same set of 4 components.
    assert len(matches) >= 1
    for m in matches:
        assert m.components == {"Y1", "C1", "C2", "U1"}


def test_rc_compensation_matches_r_c_chain_from_u_pin_to_gnd() -> None:
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="C1", lib_ref="CAP"),
            Part(refdes="U1", lib_ref="REG"),
        ],
        nets=[
            _net("COMP", [("R1", "1"), ("U1", "6")]),
            _net("COMP_MID", [("R1", "2"), ("C1", "1")]),
            _net("GND", [("C1", "2"), ("U1", "2")], is_ground=True),
        ],
    )
    matches = list(
        find_motif_matches(build_circuit_graph(plan), RC_COMPENSATION)
    )
    assert len(matches) == 1
    assert matches[0].components == {"R1", "C1", "U1"}


def test_multiple_ic_anchored_motifs_share_same_u() -> None:
    """A regulator with fb_divider, boot_cap and rc_compensation all
    on the same U1 should keep ALL three motifs after arbitration."""
    plan = _plan(
        parts=[
            # FB divider
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="R2", lib_ref="RES"),
            # Boot cap
            Part(refdes="C1", lib_ref="CAP"),
            # RC compensation
            Part(refdes="R3", lib_ref="RES"),
            Part(refdes="C2", lib_ref="CAP"),
            # Inductor for SW so BOOT_CAP is consistent (SW has degree>=3)
            Part(refdes="L1", lib_ref="IND"),
            # The shared regulator
            Part(refdes="U1", lib_ref="REG"),
        ],
        nets=[
            _net("VOUT", [("R1", "1"), ("L1", "2"), ("U1", "3")], is_power=True),
            _net("FB", [("R1", "2"), ("R2", "1"), ("U1", "5")]),
            _net("BOOT", [("C1", "1"), ("U1", "1")]),
            _net("SW", [("C1", "2"), ("L1", "1"), ("U1", "8")]),
            _net("COMP", [("R3", "1"), ("U1", "6")]),
            _net("COMP_MID", [("R3", "2"), ("C2", "1")]),
            _net(
                "GND",
                [("R2", "2"), ("C2", "2"), ("U1", "2")],
                is_ground=True,
            ),
        ],
    )
    kept = recognize_motifs(plan)
    names = sorted(m.motif_name for m in kept)
    assert "fb_divider" in names
    assert "boot_cap" in names
    assert "rc_compensation" in names
    # Each motif claims disjoint passives.
    claimed: set[str] = set()
    for m in kept:
        motif = next(mm for mm in MOTIF_CATALOGUE if mm.name == m.motif_name)
        # IC isn't claimed exclusively; passives are.
        passives = m.components - (
            {m.host_refdes(motif.ic_anchor)} if motif.ic_anchor else set()
        )
        assert not (passives & claimed), (
            f"motif {m.motif_name} claims {passives}, overlapping {claimed}"
        )
        claimed.update(passives)


def test_ic_anchored_splat_uses_u_position_as_origin() -> None:
    """splat_motif on an IC-anchored motif takes U's absolute position as
    the meta-origin; canonical entries land at U.pos + canonical_offset."""
    plan = _plan(
        parts=[
            Part(refdes="R1", lib_ref="RES"),
            Part(refdes="R2", lib_ref="RES"),
            Part(refdes="U1", lib_ref="REG"),
        ],
        nets=[
            _net("VOUT", [("R1", "1"), ("U1", "3")], is_power=True),
            _net("FB", [("R1", "2"), ("R2", "1"), ("U1", "5")]),
            _net("GND", [("R2", "2"), ("U1", "2")], is_ground=True),
        ],
    )
    matches = list(
        find_motif_matches(build_circuit_graph(plan), FB_DIVIDER)
    )
    assert len(matches) == 1
    # U1's "placed" position (say, (4000, 5000)): splat puts Rtop at
    # (4000 + 1500, 5000) = (5500, 5000) and Rbot at (5500, 4000).
    placement = splat_motif(matches[0], FB_DIVIDER, anchor=(4000, 5000))
    # The pattern is symmetric in R1/R2 -> Rtop/Rbot labeling; just
    # check the geometry of the placed pair.
    positions = sorted(placement.parts.values(), key=lambda p: -p[1])
    assert positions[0] == (5500, 5000)
    assert positions[1] == (5500, 4000)


def test_match_components_and_nets_helpers() -> None:
    plan = _plan(
        parts=[
            Part(refdes="C1", lib_ref="CAP"),
            Part(refdes="U1", lib_ref="IC"),
        ],
        nets=[
            _net("VCC", [("C1", "1"), ("U1", "1")], is_power=True),
            _net("GND", [("C1", "2"), ("U1", "2")], is_ground=True),
        ],
    )
    matches = list(
        find_motif_matches(build_circuit_graph(plan), BYPASS_CAP)
    )
    assert len(matches) == 1
    m = matches[0]
    assert m.components == {"C1"}
    assert m.nets == {"VCC", "GND"}
    assert m.host_refdes("C") == "C1"
    assert m.host_refdes("nonexistent") is None
