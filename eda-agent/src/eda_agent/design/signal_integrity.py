# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Transmission-line / termination sizing for high-speed nets.

The project already sizes a trace's characteristic impedance (``impedance_sizing``)
and its width for current/impedance, but nothing answered the two questions that
decide whether a fast net needs care at all:

1. **Is the net electrically long?** A net behaves as a lumped wire (no
   termination needed) only while its one-way flight time is a small fraction of
   the signal's rise time; past that the reflection arrives during the edge and
   the net must be treated as a transmission line. (Johnson & Graham, *High-Speed
   Digital Design: A Handbook of Black Magic*, the "electrically long" criterion;
   Bogatin, *Signal Integrity -- Simplified*.)

2. **What termination value?** Once a net is a transmission line, the reflection
   is killed by matching the source or the load to the line impedance Z0: series
   (source) ``Rs = Z0 - Rdriver``; parallel (end) ``Rp = Z0``; Thevenin split
   (two resistors whose parallel value is Z0, biased to a chosen rail fraction);
   AC (RC) termination (``Rp = Z0`` in series with a cap that blocks the DC path).

All formulas are closed-form and textbook; this module carries no lookup tables.
Propagation speed comes from the effective dielectric constant: ``v = c /
sqrt(Er_eff)`` so the one-way delay per inch is ``t_pd = sqrt(Er_eff) / c``.
Stripline is fully embedded (``Er_eff = Er``); microstrip has part of its field
in air, so ``Er_eff`` is reduced -- the Hammerstad form when the geometry is
known, else the common ``0.475*Er + 0.67`` approximation (Brooks, *PCB Currents*).

Inputs use the project's mils convention for length; rise time is in ns, Z0 and
resistances in ohms, the returned capacitance in farads.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Speed of light, inches per nanosecond (299.792458 mm/ns / 25.4).
_C_IN_PER_NS = 299.792458 / 25.4  # ~= 11.8028

# Johnson & Graham's conservative "electrically long" threshold: a net needs
# termination once its one-way flight time exceeds this fraction of the rise
# time. 1/6 is the point where the lumped approximation breaks down; looser
# practice uses 1/4 to 1/2. Exposed as a parameter, never hidden.
DEFAULT_LENGTH_FRACTION = 1.0 / 6.0


# --------------------------------------------------------------------------- #
# Propagation
# --------------------------------------------------------------------------- #
def effective_dielectric_constant(
    er: float,
    geometry: str = "microstrip",
    *,
    width_mils: float | None = None,
    height_mils: float | None = None,
) -> float:
    """Effective dielectric constant seen by a quasi-TEM wave.

    Stripline is fully embedded so ``Er_eff = Er``. A microstrip has part of its
    field in air: the Hammerstad relation ``Er_eff = (Er+1)/2 + (Er-1)/2 *
    (1 + 12 h/w)^-1/2`` when the width and dielectric height are known, else the
    ``0.475*Er + 0.67`` approximation.
    """
    if er <= 0:
        raise ValueError("dielectric constant must be positive")
    g = geometry.strip().lower()
    if g == "stripline":
        return er
    if g != "microstrip":
        raise ValueError("geometry must be 'microstrip' or 'stripline'")
    if width_mils and height_mils and width_mils > 0 and height_mils > 0:
        return (er + 1) / 2 + (er - 1) / 2 * (1 + 12 * height_mils / width_mils) ** -0.5
    return 0.475 * er + 0.67


def propagation_delay_ns_per_inch(er_eff: float) -> float:
    """One-way propagation delay per inch, ns/in (``sqrt(Er_eff) / c``)."""
    if er_eff <= 0:
        raise ValueError("effective dielectric constant must be positive")
    return math.sqrt(er_eff) / _C_IN_PER_NS


def flight_time_ns(length_mils: float, er_eff: float) -> float:
    """One-way flight time of a net, ns."""
    if length_mils < 0:
        raise ValueError("length must be non-negative")
    return (length_mils / 1000.0) * propagation_delay_ns_per_inch(er_eff)


# --------------------------------------------------------------------------- #
# Electrically-long test
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CriticalLength:
    er_eff: float
    t_pd_ns_per_inch: float
    fraction: float
    length_mils: float       # critical length
    length_inch: float
    length_mm: float


def critical_length(
    rise_time_ns: float,
    er_eff: float,
    *,
    fraction: float = DEFAULT_LENGTH_FRACTION,
) -> CriticalLength:
    """Longest net that still behaves as a lumped wire for this edge rate.

    A net needs termination once its one-way flight time exceeds
    ``fraction * rise_time``; the critical length is where they are equal,
    ``l = fraction * rise_time / t_pd``.
    """
    if rise_time_ns <= 0:
        raise ValueError("rise time must be positive")
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    t_pd = propagation_delay_ns_per_inch(er_eff)
    l_inch = fraction * rise_time_ns / t_pd
    return CriticalLength(
        er_eff=er_eff, t_pd_ns_per_inch=t_pd, fraction=fraction,
        length_mils=l_inch * 1000.0, length_inch=l_inch,
        length_mm=l_inch * 25.4)


@dataclass(frozen=True)
class ElectricalLength:
    length_mils: float
    rise_time_ns: float
    er_eff: float
    flight_time_ns: float
    critical_length_mils: float
    electrically_long: bool   # net is a transmission line for this edge
    delay_ratio: float        # flight_time / (fraction * rise_time); >1 == long


def is_electrically_long(
    length_mils: float,
    rise_time_ns: float,
    er_eff: float,
    *,
    fraction: float = DEFAULT_LENGTH_FRACTION,
) -> ElectricalLength:
    """Decide whether a net of this length needs termination for this edge."""
    cl = critical_length(rise_time_ns, er_eff, fraction=fraction)
    tof = flight_time_ns(length_mils, er_eff)
    threshold = fraction * rise_time_ns
    return ElectricalLength(
        length_mils=length_mils, rise_time_ns=rise_time_ns, er_eff=er_eff,
        flight_time_ns=tof, critical_length_mils=cl.length_mils,
        electrically_long=length_mils > cl.length_mils,
        delay_ratio=(tof / threshold) if threshold else math.inf)


# --------------------------------------------------------------------------- #
# Termination values
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SeriesTermination:
    r_series: float           # ideal, ohms
    r_series_e24: float       # nearest E24 preferred value
    driver_impedance: float
    z0: float


def series_termination(z0: float, driver_impedance: float = 0.0,
                       *, series: str = "E24") -> SeriesTermination:
    """Source (series) termination ``Rs = Z0 - Rdriver``, placed at the driver.

    The driver's own output impedance is part of the match, so subtract it. The
    canonical point-to-point CMOS termination -- no static power, but it halves
    the launched amplitude (correct, since the far end sees the full step after
    the round trip)."""
    from eda_agent.design.component_values import nearest_preferred
    if z0 <= 0:
        raise ValueError("Z0 must be positive")
    if driver_impedance < 0:
        raise ValueError("driver impedance must be non-negative")
    rs = max(0.0, z0 - driver_impedance)
    return SeriesTermination(
        r_series=rs, r_series_e24=nearest_preferred(rs, series) if rs > 0 else 0.0,
        driver_impedance=driver_impedance, z0=z0)


@dataclass(frozen=True)
class ParallelTermination:
    r_parallel: float
    r_parallel_e24: float
    z0: float


def parallel_termination(z0: float, *, series: str = "E24") -> ParallelTermination:
    """End (parallel) termination ``Rp = Z0`` to the reference rail."""
    from eda_agent.design.component_values import nearest_preferred
    if z0 <= 0:
        raise ValueError("Z0 must be positive")
    return ParallelTermination(
        r_parallel=z0, r_parallel_e24=nearest_preferred(z0, series), z0=z0)


@dataclass(frozen=True)
class TheveninTermination:
    r_pullup: float           # to Vcc
    r_pulldown: float         # to GND
    r_pullup_e24: float
    r_pulldown_e24: float
    r_thevenin: float         # parallel combination == Z0
    v_bias: float             # idle voltage the divider holds
    static_power_w: float     # always-on draw through the divider
    z0: float


def thevenin_termination(
    z0: float, vcc: float, *, v_bias: float | None = None,
    series: str = "E24",
) -> TheveninTermination:
    """Split (Thevenin) termination: two resistors whose parallel value is Z0.

    ``R_pullup = Z0 / a`` and ``R_pulldown = Z0 / (1-a)`` where ``a = Vbias/Vcc``;
    the parallel combination is Z0 and the divider idles the line at ``Vbias``
    (default mid-rail). Used on buses where a defined idle level helps; costs
    static power ``Vcc^2 / (R_pullup + R_pulldown)``."""
    from eda_agent.design.component_values import nearest_preferred
    if z0 <= 0 or vcc <= 0:
        raise ValueError("Z0 and Vcc must be positive")
    vb = vcc / 2.0 if v_bias is None else v_bias
    a = vb / vcc
    if not 0 < a < 1:
        raise ValueError("v_bias must be between 0 and Vcc (exclusive)")
    r_up = z0 / a
    r_dn = z0 / (1 - a)
    return TheveninTermination(
        r_pullup=r_up, r_pulldown=r_dn,
        r_pullup_e24=nearest_preferred(r_up, series),
        r_pulldown_e24=nearest_preferred(r_dn, series),
        r_thevenin=z0, v_bias=vb,
        static_power_w=vcc * vcc / (r_up + r_dn), z0=z0)


@dataclass(frozen=True)
class AcTermination:
    r_parallel: float
    r_parallel_e24: float
    capacitance_f: float
    time_constants: float     # RC expressed in units of one-way flight time
    z0: float


def ac_termination(
    z0: float, length_mils: float, er_eff: float,
    *, time_constants: float = 3.0, series: str = "E24",
) -> AcTermination:
    """AC (RC) termination: ``Rp = Z0`` in series with a blocking cap.

    The cap carries the reflected edge but blocks the DC path, so there is no
    static power. It must hold for the settling, so size the time constant to a
    few one-way flight times (Johnson & Graham use ``RC >= 3 * Td``):
    ``C = time_constants * Td / Z0``.

    ``time_constants`` is a DIMENSIONLESS multiplier of the one-way flight
    time Td (default 3.0 = "RC equals three flight times"), not a time
    value. The flight time itself comes from the length/Er arguments.
    """
    from eda_agent.design.component_values import nearest_preferred
    if z0 <= 0:
        raise ValueError("Z0 must be positive")
    if time_constants <= 0:
        raise ValueError("time_constants must be positive")
    td_ns = flight_time_ns(length_mils, er_eff)
    # C [F] = time_constants * Td [s] / Z0 [ohm]; Td_ns * 1e-9 -> seconds.
    c_f = time_constants * (td_ns * 1e-9) / z0
    return AcTermination(
        r_parallel=z0, r_parallel_e24=nearest_preferred(z0, series),
        capacitance_f=c_f, time_constants=time_constants, z0=z0)


# --------------------------------------------------------------------------- #
# Aggregator
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TerminationAdvice:
    electrical: ElectricalLength
    needs_termination: bool
    recommended: str                       # short name of the preferred scheme
    series: SeriesTermination | None
    parallel: ParallelTermination | None
    thevenin: TheveninTermination | None
    ac: AcTermination | None
    note: str


def recommend_termination(
    length_mils: float,
    rise_time_ns: float,
    z0: float,
    er: float,
    *,
    geometry: str = "microstrip",
    driver_impedance: float | None = None,
    vcc: float | None = None,
    width_mils: float | None = None,
    height_mils: float | None = None,
    fraction: float = DEFAULT_LENGTH_FRACTION,
    multi_load: bool = False,
) -> TerminationAdvice:
    """One-call termination assessment for a net.

    Computes the effective Er, decides whether the net is electrically long for
    the edge rate, and -- if so -- offers every applicable termination value
    with a recommendation: series for a point-to-point net (no static power),
    Thevenin/parallel for a multi-load bus. Returns the options so the planner
    can pick; the ``note`` explains the call.
    """
    er_eff = effective_dielectric_constant(
        er, geometry, width_mils=width_mils, height_mils=height_mils)
    el = is_electrically_long(length_mils, rise_time_ns, er_eff, fraction=fraction)
    if not el.electrically_long:
        return TerminationAdvice(
            electrical=el, needs_termination=False, recommended="none",
            series=None, parallel=None, thevenin=None, ac=None,
            note=(f"net {length_mils:.0f} mils < critical "
                  f"{el.critical_length_mils:.0f} mils for a {rise_time_ns:.2f} ns "
                  f"edge; treat as a lumped wire, no termination needed"))
    ser = series_termination(z0, driver_impedance or 0.0)
    par = parallel_termination(z0)
    thev = thevenin_termination(z0, vcc) if vcc else None
    act = ac_termination(z0, length_mils, er_eff)
    if multi_load:
        rec = "thevenin" if thev else "parallel"
        why = ("multi-load net -- terminate at the far end; Thevenin sets a "
               "defined idle level" if thev else
               "multi-load net -- parallel terminate at the far end")
    else:
        rec = "series"
        why = ("point-to-point net -- series (source) termination kills the "
               "reflection with no static power")
    return TerminationAdvice(
        electrical=el, needs_termination=True, recommended=rec,
        series=ser, parallel=par, thevenin=thev, ac=act,
        note=(f"net {length_mils:.0f} mils > critical "
              f"{el.critical_length_mils:.0f} mils ({el.delay_ratio:.1f}x); {why}"))


__all__ = [
    "DEFAULT_LENGTH_FRACTION",
    "effective_dielectric_constant",
    "propagation_delay_ns_per_inch",
    "flight_time_ns",
    "CriticalLength", "critical_length",
    "ElectricalLength", "is_electrically_long",
    "SeriesTermination", "series_termination",
    "ParallelTermination", "parallel_termination",
    "TheveninTermination", "thevenin_termination",
    "AcTermination", "ac_termination",
    "TerminationAdvice", "recommend_termination",
]
