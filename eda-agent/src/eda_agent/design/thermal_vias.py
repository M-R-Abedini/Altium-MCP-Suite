# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Thermal-via array sizing for power components.

The thermal calcs in :mod:`component_values` work in terms of a junction-to-
ambient resistance (``junction_temperature``, ``required_theta_ja``,
``max_power_dissipation``) but say nothing about how the heat actually leaves the
package into the board. For a power pad (a QFN/DFN exposed pad, a regulator tab,
a power inductor) the dominant path is a field of plated vias carrying heat from
the top copper down to an inner or bottom plane. This module sizes that field.

A single plated via is a copper tube; its axial thermal resistance is the
one-dimensional Fourier conduction result ``R = L / (k * A)`` where ``L`` is the
conduction length (top layer to the heat-spreading plane), ``k`` the copper
thermal conductivity (~385 W/(m.K)), and ``A`` the copper cross-section -- the
barrel annulus for an unfilled/resin-filled via, or the full circle for a
copper-filled via. Vias in a field conduct in parallel, so ``R_array = R_single
/ N``. Inverting gives the count needed to hit a target resistance or to hold a
power dissipation within a temperature rise.

This is the conduction bottleneck of the via field itself; it assumes the top
pad and the receiving plane spread heat well (large copper, the usual case under
a power pad). Spreading resistance in thin copper is a separate, geometry-heavy
term left to a field solver. All formulas are first-principles conduction, no
lookup tables.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Thermal conductivity of copper, W/(m.K). Bulk is ~401; electrodeposited via
# plating runs a little lower, so 385 is the common design value. A parameter.
K_COPPER = 385.0


def via_barrel_area_mm2(
    drill_mm: float, plating_um: float, *, filled_copper: bool = False,
) -> float:
    """Conducting copper cross-section of one via, mm^2.

    Units are mixed by industry convention and the parameter names say so:
    ``drill_mm`` is MILLIMETRES, ``plating_um`` is MICROMETRES (25 um = 1 oz
    barrel plating). Passing a plating value in mm here is 1000x off.

    Unfilled (or resin-filled) via: the plated annulus between the drill wall
    and the finished hole. Copper-filled via: the full finished circle.
    """
    if drill_mm <= 0:
        raise ValueError("drill diameter must be positive")
    if plating_um < 0:
        raise ValueError("plating thickness must be non-negative")
    # PLATING GROWS INWARD FROM THE DRILL WALL.
    #
    # The drill removes material, and copper is deposited onto the wall
    # of the hole it leaves, so the finished hole is SMALLER than the
    # drill and the copper lies between (r_drill - t) and r_drill.
    #
    # This previously computed the annulus OUTSIDE the drill radius,
    # r_drill to r_drill + t, which is copper in the space the drill
    # had already cleared. It overstated the barrel by 18 percent for a
    # 0.3mm drill with 25um plating, and thermal resistance came out
    # low by the same factor: a via array that looked adequate and was
    # not. The docstring above already described the correct annulus.
    r_drill = drill_mm / 2.0
    t = plating_um / 1000.0  # um -> mm
    r_inner = r_drill - t

    # Plating thicker than the radius closes the hole; it cannot eat
    # past the centre, and the barrel is then a solid cylinder.
    if r_inner <= 0.0:
        return math.pi * r_drill * r_drill

    if filled_copper:
        # A filled via is copper across the drilled hole, not beyond it.
        return math.pi * r_drill * r_drill
    return math.pi * (r_drill * r_drill - r_inner * r_inner)


def single_via_thermal_resistance(
    drill_mm: float,
    plating_um: float,
    length_mm: float,
    *,
    filled_copper: bool = False,
    k_cu: float = K_COPPER,
) -> float:
    """Axial thermal resistance of one via, K/W (``R = L / (k * A)``).

    ``length_mm`` is the conduction length -- the board thickness when the heat
    sinks to a bottom plane, or the depth to the receiving inner plane.
    """
    if length_mm <= 0:
        raise ValueError("conduction length must be positive")
    if k_cu <= 0:
        raise ValueError("thermal conductivity must be positive")
    area_m2 = via_barrel_area_mm2(
        drill_mm, plating_um, filled_copper=filled_copper) * 1e-6
    length_m = length_mm * 1e-3
    return length_m / (k_cu * area_m2)


def via_array_thermal_resistance(
    n: int,
    drill_mm: float,
    plating_um: float,
    length_mm: float,
    *,
    filled_copper: bool = False,
    k_cu: float = K_COPPER,
) -> float:
    """Thermal resistance of ``n`` identical vias in parallel, K/W."""
    if n <= 0:
        raise ValueError("via count must be positive")
    return single_via_thermal_resistance(
        drill_mm, plating_um, length_mm,
        filled_copper=filled_copper, k_cu=k_cu) / n


def vias_for_thermal_resistance(
    target_k_per_w: float,
    drill_mm: float,
    plating_um: float,
    length_mm: float,
    *,
    filled_copper: bool = False,
    k_cu: float = K_COPPER,
) -> int:
    """Smallest via count whose array resistance is <= ``target_k_per_w``."""
    if target_k_per_w <= 0:
        raise ValueError("target resistance must be positive")
    r1 = single_via_thermal_resistance(
        drill_mm, plating_um, length_mm,
        filled_copper=filled_copper, k_cu=k_cu)
    return max(1, math.ceil(r1 / target_k_per_w))


@dataclass(frozen=True)
class ThermalViaReport:
    single_via_k_per_w: float
    via_count: int
    array_k_per_w: float
    target_k_per_w: float | None
    temp_rise_c: float | None       # across the via field, if a power was given
    barrel_area_mm2: float


def assess_thermal_vias(
    drill_mm: float,
    plating_um: float,
    length_mm: float,
    *,
    filled_copper: bool = False,
    k_cu: float = K_COPPER,
    power_w: float | None = None,
    delta_t_c: float | None = None,
    target_k_per_w: float | None = None,
    via_count: int | None = None,
) -> ThermalViaReport:
    """Size a thermal-via field, or score a proposed one.

    Provide a target -- a ``target_k_per_w`` directly, or a ``power_w`` with a
    ``delta_t_c`` budget (target = dT / P) -- and the count is solved. Or pass
    an explicit ``via_count`` to evaluate a layout you already have. The
    temperature rise across the field is reported when a power is given.
    """
    r1 = single_via_thermal_resistance(
        drill_mm, plating_um, length_mm,
        filled_copper=filled_copper, k_cu=k_cu)
    area = via_barrel_area_mm2(drill_mm, plating_um, filled_copper=filled_copper)

    target = target_k_per_w
    if target is None and power_w and delta_t_c:
        if power_w <= 0 or delta_t_c <= 0:
            raise ValueError("power_w and delta_t_c must be positive")
        target = delta_t_c / power_w

    if via_count is not None:
        if via_count <= 0:
            raise ValueError("via_count must be positive")
        n = via_count
    elif target is not None:
        n = max(1, math.ceil(r1 / target))
    else:
        raise ValueError(
            "give a target (target_k_per_w, or power_w + delta_t_c) or a via_count")

    r_array = r1 / n
    rise = (power_w * r_array) if power_w else None
    return ThermalViaReport(
        single_via_k_per_w=r1, via_count=n, array_k_per_w=r_array,
        target_k_per_w=target, temp_rise_c=rise, barrel_area_mm2=area)


__all__ = [
    "K_COPPER",
    "via_barrel_area_mm2",
    "single_via_thermal_resistance",
    "via_array_thermal_resistance",
    "vias_for_thermal_resistance",
    "ThermalViaReport",
    "assess_thermal_vias",
]
