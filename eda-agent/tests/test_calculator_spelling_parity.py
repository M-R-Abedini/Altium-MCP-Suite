# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The same calculator call must work on every backend.

These seven calculators are registered everywhere and are pure maths,
so "available on this backend" was already true of all of them. It was
also useless: the Altium build spelled several arguments with their
unit and the shared build spelled them tersely, so a client written
against one backend got a missing-argument error on the other. The tool
was listed, documented, and identical in behaviour, and the call still
failed. That is the least useful kind of parity there is.

Both spellings are now accepted on both sides. The fix is additive on
purpose: these are published signatures, so renaming either spelling
would break whichever callers already use it, and there is no way to
find out which those are.

The pairs are declared once and driven through the registered tools on
every backend, so a new backend or a re-registered calculator is
covered without editing this file.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from eda_agent.tools import register_backend
from eda_agent.tools.registry import ToolRegistry

_BACKENDS = ("altium", "easyeda", "kicad")

#: tool -> (call that must work, the aliased keywords to swap in)
#: Both forms must produce the SAME answer, which is the property that
#: makes them aliases rather than two similar arguments.
_CASES = {
    "pcb_calc_trace_width_for_current": (
        {"current_amps": 2.0, "copper_oz": 1.0, "delta_t_c": 10.0},
        {"current_amps": "current_a"},
    ),
    "pcb_calc_thermal_vias": (
        {"drill_mm": 0.3, "plating_um": 25.0, "board_thickness_mm": 1.6,
         "power_w": 2.0, "delta_t_c": 20.0},
        {"board_thickness_mm": "length_mm"},
    ),
    "pcb_calc_termination": (
        {"length_mils": 4000.0, "rise_time_ns": 0.5, "z0_ohms": 50.0,
         "dielectric_constant": 4.2},
        {"z0_ohms": "z0", "dielectric_constant": "er"},
    ),
}


@pytest.fixture(scope="module")
def registries():
    out = {}
    for backend in _BACKENDS:
        reg = ToolRegistry()
        register_backend(reg, backend, "full")
        out[backend] = reg
    return out


def _call(registry, name, kwargs):
    return asyncio.run(registry.get(name).fn(**kwargs))


def _comparable(reply):
    """Drop the fields that legitimately differ between builds.

    The two implementations word their summary differently and echo
    their own spelling back, neither of which says the physics
    disagrees. The numbers are what must match.
    """
    return {k: v for k, v in reply.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)}


@pytest.mark.parametrize("backend", _BACKENDS)
@pytest.mark.parametrize("name", sorted(_CASES))
def test_both_spellings_are_accepted(backend, name, registries):
    registry = registries[backend]
    if name not in set(registry.names):
        pytest.skip(f"{name} is not registered on {backend}")

    canonical, aliases = _CASES[name]
    swapped = {aliases.get(k, k): v for k, v in canonical.items()}

    first = _call(registry, name, canonical)
    second = _call(registry, name, swapped)

    assert first.get("ok") is True, (
        f"{name} refused its own documented spelling on {backend}: "
        f"{first.get('reason')}")
    assert second.get("ok") is True, (
        f"{name} refused {sorted(aliases.values())} on {backend}, so a "
        f"client written for another backend cannot call it: "
        f"{second.get('reason')}")
    assert _comparable(first) == _comparable(second), (
        f"{name} on {backend} returns different numbers for the same "
        f"quantity depending on which spelling was used, so these are "
        f"not aliases")


@pytest.mark.parametrize("backend", _BACKENDS)
@pytest.mark.parametrize("name", sorted(_CASES))
def test_omitting_the_quantity_entirely_is_a_clean_refusal(
        backend, name, registries):
    """Making a required argument optional moves the error from schema
    validation to runtime, so the runtime message has to be as good.

    It must also NAME BOTH spellings: a caller who used the alias and
    misspelled it needs to know the alias exists, and a message naming
    only the canonical form sends them to the wrong fix.
    """
    registry = registries[backend]
    if name not in set(registry.names):
        pytest.skip(f"{name} is not registered on {backend}")

    canonical, aliases = _CASES[name]
    aliased_key = sorted(aliases)[0]
    without = {k: v for k, v in canonical.items() if k != aliased_key}

    reply = _call(registry, name, without)

    # Two behaviours are correct and one is not. A tool with no default
    # for the quantity must refuse, and its message must name BOTH
    # spellings: a caller who used the alias and mistyped it needs to
    # know the alias exists, and a message naming only the canonical
    # form sends them to the wrong fix. A tool that DOES define a
    # default may proceed, but then it has to actually use that
    # default, which is checked by supplying it explicitly and
    # demanding the same numbers.
    #
    # Altium's termination calculator falls in the second group on
    # purpose: 50 ohms and a dielectric of 4.2 are a deliberate
    # convenience. Its defaults live in _TERMINATION_DEFAULTS rather
    # than the signature, so an absent argument is distinguishable from
    # one that happens to equal the default, which is what makes the
    # conflict check below possible at all.
    if reply.get("ok") is False:
        reason = reply.get("reason") or ""
        assert aliased_key in reason and aliases[aliased_key] in reason, (
            f"the refusal names only one spelling, so a caller who used "
            f"the other is pointed at the wrong fix: {reason!r}")
        return

    explicit = _call(registry, name, canonical)
    assert _comparable(reply) == _comparable(explicit), (
        f"{name} on {backend} accepted a call with neither {aliased_key} "
        f"nor {aliases[aliased_key]} and did NOT fall back to the "
        f"documented default, so it computed with a missing input")


@pytest.mark.parametrize("backend", _BACKENDS)
@pytest.mark.parametrize("name", sorted(_CASES))
def test_two_spellings_that_disagree_are_refused(backend, name, registries):
    """Silently preferring one would compute with a number the caller
    did not intend, and report success."""
    registry = registries[backend]
    if name not in set(registry.names):
        pytest.skip(f"{name} is not registered on {backend}")

    canonical, aliases = _CASES[name]
    key = sorted(aliases)[0]
    conflicting = dict(canonical)
    conflicting[aliases[key]] = canonical[key] * 2 + 1

    reply = _call(registry, name, conflicting)
    assert reply.get("ok") is False, (
        f"{name} on {backend} was given {key} and {aliases[key]} with "
        f"different values and picked one silently")


def test_every_shared_calculator_is_covered_or_known_different(registries):
    """A calculator added later must not slip through unexamined.

    pcb_calc_track_current_capacity is listed as a known difference
    rather than an alias: Altium takes length_mils and computes voltage
    drop over a run, the shared build takes delta_t_c and computes
    capacity at a chosen temperature rise. Those are different
    features, not two names for one, so aliasing them would be a lie.
    """
    known_different = {"pcb_calc_track_current_capacity"}
    already_agreed = {"pcb_calc_impedance",
                      "pcb_calc_trace_width_for_impedance",
                      "pcb_calc_length_match"}

    shared = set.intersection(*(set(registries[b].names) for b in _BACKENDS))
    calculators = {n for n in shared if "_calc_" in n}
    unexamined = calculators - set(_CASES) - known_different - already_agreed
    assert not unexamined, (
        f"these shared calculators have never been checked for spelling "
        f"parity: {sorted(unexamined)}")


@pytest.mark.parametrize("name", sorted(_CASES))
def test_the_aliases_are_documented(name, registries):
    """An alias a caller cannot discover is not much of an alias."""
    for backend in _BACKENDS:
        registry = registries[backend]
        if name not in set(registry.names):
            continue
        doc = inspect.getdoc(registry.get(name).fn) or ""
        for alias in _CASES[name][1].values():
            assert alias in doc, (
                f"{name} on {backend} accepts {alias} but its docstring "
                f"never mentions it, so an MCP client reading the schema "
                f"cannot find it")
