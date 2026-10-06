# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A tool name means the same thing on every backend that registers it.

``pcb_calc_*`` is defined TWICE: in tools/pcb.py for the Altium backend
and in tools/calc.py for KiCad and EasyEDA. Both delegate to the same
design/* functions, so the physics agrees and cannot drift. The
wrappers do not: five of the six take different ARGUMENTS depending on
which backend registered them, and a caller that learned the tool on
one backend gets a TypeError on the other.

The divergences are pinned by name below rather than merely counted.
That way a SEVENTH cannot appear unnoticed, and fixing a listed one
fails this file until the entry is removed, so the list cannot quietly
outlive the problem. Converging them is task #55, which needs a
decision about which spelling wins because both are published.
"""
from __future__ import annotations

import inspect

import pytest

from eda_agent.tools.calc import register_calc_tools
from eda_agent.tools.pcb import register_pcb_tools


def _registered(register) -> dict:
    captured: dict = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    register(_Mcp())
    return captured


_CALC = _registered(register_calc_tools)
_PCB = _registered(register_pcb_tools)
_SHARED = sorted(set(_CALC) & set(_PCB))

#: Known signature divergences, each with what actually differs. An
#: entry is a recorded defect awaiting task #55, NOT an exemption.
KNOWN_DIVERGENT: dict[str, str] = {
    "pcb_calc_trace_width_for_current":
        "current_a (calc) vs current_amps (pcb)",
    "pcb_calc_track_current_capacity":
        "delta_t_c (calc) vs length_mils (pcb)",
    "pcb_calc_termination":
        "z0/er vs z0_ohms/dielectric_constant; pcb adds length_fraction",
    "pcb_calc_thermal_vias":
        "length_mm (calc) vs board_thickness_mm (pcb); pcb adds k_cu",
    "pcb_calc_length_match":
        "same parameter names in a DIFFERENT ORDER, so a positional "
        "caller silently transposes them",
}


def _params(fn) -> list[str]:
    return list(inspect.signature(fn).parameters)


def test_the_two_definitions_cover_the_same_tools():
    assert len(_SHARED) >= 6, (
        f"only {len(_SHARED)} calculators are defined on both sides; the "
        "duplication this file guards has changed shape")


@pytest.mark.parametrize("name", _SHARED)
def test_a_calculator_takes_the_same_arguments_on_every_backend(name):
    """Same name, same arguments, or a caller cannot move between
    backends without rewriting the call."""
    calc_params, pcb_params = _params(_CALC[name]), _params(_PCB[name])
    differs = calc_params != pcb_params
    if name in KNOWN_DIVERGENT:
        assert differs, (
            f"{name} is listed in KNOWN_DIVERGENT as {KNOWN_DIVERGENT[name]!r} "
            f"but its signatures now agree. Delete the entry: a stale list "
            f"hides the next real divergence.")
        return
    assert not differs, (
        f"{name} takes {calc_params} on kicad/easyeda and {pcb_params} on "
        f"altium. A caller that learned it on one backend fails on the "
        f"other. Either converge them (task #55) or add an entry to "
        f"KNOWN_DIVERGENT saying what differs and why it is tolerated.")


def test_the_two_current_capacity_implementations_agree_numerically():
    """The arithmetic is duplicated, so pin the NUMBERS, not the imports.

    calc.py delegates to design.trace_sizing.current_capacity_amps.
    pcb.py does NOT: it computes IPC-2221 inline with its own copies of
    k, the 0.44 and 0.725 exponents, and the 1.378 mils-per-ounce
    thickness. Measured, they agree to 0.000% across a sweep,
    which is the same "all agreed, by luck" state the mils-to-mm factor
    was in before task #43 consolidated it: nothing enforces it, and
    the divergence when it comes lands as two backends quoting
    different current ratings for the same track.

    A structural check (does pcb.py import the engine?) would pass the
    moment someone re-inlined the formula differently. This runs both
    and compares.
    """
    from eda_agent.design.trace_sizing import current_capacity_amps

    def inline(width_mils, copper_oz, delta_t, layer):
        # Verbatim from tools/pcb.py's pcb_calc_track_current_capacity.
        thickness_mils = copper_oz * 1.378
        k = 0.024 if layer == "internal" else 0.048
        return k * (delta_t ** 0.44) * (
            (thickness_mils * width_mils) ** 0.725)

    worst = 0.0
    for width in (5, 10, 20, 50, 100):
        for copper_oz in (0.5, 1.0, 2.0):
            for delta_t in (5, 10, 20, 30):
                for layer in ("external", "internal"):
                    shared = current_capacity_amps(
                        width, copper_oz=copper_oz, delta_t_c=delta_t,
                        layer=layer)
                    mine = inline(width, copper_oz, delta_t, layer)
                    if shared:
                        worst = max(worst, abs(shared - mine) / shared)
    assert worst < 1e-9, (
        f"the two IPC-2221 implementations now disagree by up to "
        f"{worst * 100:.3f}%. Two backends would quote different current "
        f"ratings for the same track. Consolidate onto "
        f"design.trace_sizing (task #55).")


def test_a_pure_calculator_is_not_denied_to_a_backend():
    """No pure calculator may exist on Altium alone.

    pcb_calc_impedance was exactly that: pure math
    that never touched the bridge, defined only in pcb.py, so KiCad and
    EasyEDA users could size a width for a target impedance but could
    not check an existing one. It is the visible symptom of the
    duplication, a calculator added to one copy only, and this is the
    guard that stops the next one being added the same way.

    A calculator that DOES read the board (pcb_calc_polygon_area asks
    the bridge for the polygon) is correctly Altium-only and does not
    count.
    """
    pure_but_altium_only = []
    for name in sorted(set(_PCB) - set(_CALC)):
        if not name.startswith("pcb_calc_"):
            continue
        if "get_bridge" not in inspect.getsource(_PCB[name]):
            pure_but_altium_only.append(name)
    assert not pure_but_altium_only, (
        f"{pure_but_altium_only} compute without touching the bridge but "
        f"exist only on the Altium backend, so KiCad and EasyEDA users "
        f"cannot reach them. Define them in calc.py, which every backend "
        f"registers, rather than in pcb.py alone.")
