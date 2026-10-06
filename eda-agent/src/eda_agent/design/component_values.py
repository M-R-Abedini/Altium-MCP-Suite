# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Component-value computation: E-series preferred values and the standard
sizing equations a planner needs to turn an intent ("3.3 V from a 0.8 V
feedback pin", "10 mA LED off 5 V", "1 kHz low-pass") into a manufacturable
part value.

Two reasons this is a deterministic module rather than left to the planner:

* **E-series snapping (IEC 60063).** A computed ideal value (e.g. 31.25 kOhm)
  does not exist as a real part; it must snap to the nearest preferred value
  of an E-series (E6/E12/E24/E48/E96/E192). Doing this by eye is error prone,
  especially across a decade boundary (9.8 -> 10, not 9.1).
* **Closed-form sizing.** Voltage dividers, LED series resistors and RC
  cut-offs are exact algebra; computing them in code and reporting the ACHIEVED
  value plus the error removes a class of silent arithmetic mistakes.

Every function returns the achieved (snapped) result and its error versus the
ideal, so a caller can see whether the nearest preferred value is good enough
or a tighter series is needed. Nothing here touches Altium; it is pure math.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# --------------------------------------------------------------------------- #
# E-series preferred-value mantissas (IEC 60063), one decade, 1.00 .. 9.xx.
# --------------------------------------------------------------------------- #

# E24 (5% / 1% hand-stock). E12 and E6 are its 2nd / 4th elements.
_E24 = [
    1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
    3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1,
]

# E96 (1% precision). E48 is its 2nd element; E192 is a superset not held here.
_E96 = [
    1.00, 1.02, 1.05, 1.07, 1.10, 1.13, 1.15, 1.18,
    1.21, 1.24, 1.27, 1.30, 1.33, 1.37, 1.40, 1.43,
    1.47, 1.50, 1.54, 1.58, 1.62, 1.65, 1.69, 1.74,
    1.78, 1.82, 1.87, 1.91, 1.96, 2.00, 2.05, 2.10,
    2.15, 2.21, 2.26, 2.32, 2.37, 2.43, 2.49, 2.55,
    2.61, 2.67, 2.74, 2.80, 2.87, 2.94, 3.01, 3.09,
    3.16, 3.24, 3.32, 3.40, 3.48, 3.57, 3.65, 3.74,
    3.83, 3.92, 4.02, 4.12, 4.22, 4.32, 4.42, 4.53,
    4.64, 4.75, 4.87, 4.99, 5.11, 5.23, 5.36, 5.49,
    5.62, 5.76, 5.90, 6.04, 6.19, 6.34, 6.49, 6.65,
    6.81, 6.98, 7.15, 7.32, 7.50, 7.68, 7.87, 8.06,
    8.25, 8.45, 8.66, 8.87, 9.09, 9.31, 9.53, 9.76,
]

_SERIES_MANTISSAS: dict[str, list[float]] = {
    "E6": _E24[::4],
    "E12": _E24[::2],
    "E24": _E24,
    "E48": _E96[::2],
    "E96": _E96,
}


def series_mantissas(series: str) -> list[float]:
    """Return the one-decade mantissa list for an E-series name (case
    insensitive). Raises ``ValueError`` on an unknown series."""
    key = series.strip().upper()
    if key not in _SERIES_MANTISSAS:
        raise ValueError(
            f"unknown E-series {series!r}; known: {sorted(_SERIES_MANTISSAS)}")
    return _SERIES_MANTISSAS[key]


def nearest_preferred(value: float, series: str = "E24") -> float:
    """Snap ``value`` to the nearest preferred value of ``series``.

    "Nearest" is measured in LOG space (equivalently the smallest value ratio),
    which is the correct metric for component tolerance and makes the choice
    decade-symmetric -- 9.8 snaps up to 10 (ratio 1.02), not down to 9.1 (ratio
    1.077). ``value`` must be positive.
    """
    if value <= 0:
        raise ValueError("value must be positive")
    mantissas = series_mantissas(series)
    decade = math.floor(math.log10(value))
    # Consider the decade below, at, and above so a value near a decade edge can
    # snap across it (e.g. 980 -> 1000, or 1.02 -> 1.00).
    best: Optional[float] = None
    best_err = math.inf
    for d in (decade - 1, decade, decade + 1):
        scale = 10.0 ** d
        for m in mantissas:
            cand = m * scale
            err = abs(math.log(cand / value))
            if err < best_err:
                best_err, best = err, cand
    assert best is not None
    return best


# --------------------------------------------------------------------------- #
# Result records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DividerResult:
    """A resistor pair plus the voltage it actually produces."""

    r_top: float           # ohms, high-side (between input and tap)
    r_bottom: float        # ohms, low-side (between tap and reference)
    v_out: float           # achieved tap voltage
    v_out_ideal: float     # requested tap voltage
    error_pct: float       # 100 * (v_out - ideal) / ideal


@dataclass(frozen=True)
class LedResult:
    resistor: float        # ohms, snapped
    current_a: float       # achieved LED current
    current_ideal_a: float
    power_w: float         # dissipation in the resistor
    error_pct: float


@dataclass(frozen=True)
class RcResult:
    r: float               # ohms
    c: float               # farads
    f_cutoff: float        # achieved -3 dB cut-off
    f_cutoff_ideal: float
    error_pct: float


@dataclass(frozen=True)
class CrystalCapResult:
    cap: float             # farads, each load cap (snapped, C1 = C2)
    cap_ideal: float       # farads, ideal each-cap value
    c_load_achieved: float # farads, crystal load the snapped pair presents
    c_load_target: float   # farads, datasheet CL
    error_pct: float


@dataclass(frozen=True)
class I2cPullupResult:
    r_min: float           # ohms, sink-current floor (VOL with IOL)
    r_max: float           # ohms, rise-time ceiling
    recommended: Optional[float]  # snapped value in [r_min, r_max], or None
    feasible: bool         # a preferred value exists in the window


@dataclass(frozen=True)
class DividerToleranceResult:
    v_nominal: float
    v_min: float
    v_max: float
    spread_pct: float      # 100 * (v_max - v_min) / v_nominal


@dataclass(frozen=True)
class OpampGainResult:
    config: str            # "inverting" | "non_inverting"
    r_feedback: float      # ohms, Rf
    r_input: float         # ohms, Rin (inverting) or Rg (non-inverting)
    gain: float            # achieved gain MAGNITUDE
    gain_ideal: float      # requested gain magnitude
    error_pct: float


@dataclass(frozen=True)
class BuckInductorResult:
    inductance: float          # henries, snapped
    inductance_ideal: float    # henries, exact
    ripple_current_a: float    # peak-to-peak inductor ripple at the snapped L
    peak_current_a: float      # Iout + ripple/2 (inductor saturation budget)
    error_pct: float


# --------------------------------------------------------------------------- #
# Sizing equations
# --------------------------------------------------------------------------- #


def resistor_divider(
    v_in: float,
    v_out: float,
    *,
    series: str = "E96",
    r_low: float = 1.0e3,
    r_high: float = 1.0e6,
) -> DividerResult:
    """Size an UNLOADED resistor divider ``v_out = v_in * Rb / (Rt + Rb)``.

    Enumerates the low-side resistor over ``series`` in ``[r_low, r_high]``,
    derives the ideal high-side, snaps it to ``series`` and keeps the pair with
    the smallest output error. ``v_in > v_out > 0`` required (a divider can only
    attenuate).
    """
    if not (v_in > v_out > 0):
        raise ValueError("require v_in > v_out > 0 for a divider")
    mantissas = series_mantissas(series)
    candidates = _values_in_range(mantissas, r_low, r_high)
    best: Optional[DividerResult] = None
    for rb in candidates:
        # v_out/v_in = Rb/(Rt+Rb)  ->  Rt = Rb * (v_in - v_out) / v_out
        rt_ideal = rb * (v_in - v_out) / v_out
        rt = nearest_preferred(rt_ideal, series)
        vo = v_in * rb / (rt + rb)
        err = 100.0 * (vo - v_out) / v_out
        if best is None or abs(err) < abs(best.error_pct):
            best = DividerResult(r_top=rt, r_bottom=rb, v_out=vo,
                                 v_out_ideal=v_out, error_pct=err)
    assert best is not None
    return best


def feedback_divider(
    v_out: float,
    v_ref: float,
    *,
    series: str = "E96",
    r_bottom: Optional[float] = None,
    r_low: float = 1.0e3,
    r_high: float = 1.0e5,
) -> DividerResult:
    """Size a regulator feedback divider ``v_out = v_ref * (1 + Rt / Rb)``.

    The tap sits on the FB pin at ``v_ref``. With ``r_bottom`` given, only the
    top resistor is chosen; otherwise the low-side is enumerated over ``series``
    in ``[r_low, r_high]`` and the best pair kept. ``v_out > v_ref > 0``.
    """
    if not (v_out > v_ref > 0):
        raise ValueError("require v_out > v_ref > 0 for a feedback divider")
    mantissas = series_mantissas(series)
    if r_bottom is not None:
        candidates = [r_bottom]
    else:
        candidates = _values_in_range(mantissas, r_low, r_high)
    best: Optional[DividerResult] = None
    for rb in candidates:
        # v_out/v_ref = 1 + Rt/Rb  ->  Rt = Rb * (v_out/v_ref - 1)
        rt_ideal = rb * (v_out / v_ref - 1.0)
        rt = nearest_preferred(rt_ideal, series)
        vo = v_ref * (1.0 + rt / rb)
        err = 100.0 * (vo - v_out) / v_out
        if best is None or abs(err) < abs(best.error_pct):
            best = DividerResult(r_top=rt, r_bottom=rb, v_out=vo,
                                 v_out_ideal=v_out, error_pct=err)
    assert best is not None
    return best


def led_series_resistor(
    v_supply: float,
    v_forward: float,
    i_led: float,
    *,
    series: str = "E24",
) -> LedResult:
    """Size an LED series resistor ``R = (Vsupply - Vf) / Iled`` and report the
    achieved current and the resistor dissipation. ``v_supply > v_forward`` and
    ``i_led > 0``."""
    if not (v_supply > v_forward >= 0):
        raise ValueError("require v_supply > v_forward >= 0")
    if i_led <= 0:
        raise ValueError("i_led must be positive")
    r_ideal = (v_supply - v_forward) / i_led
    r = nearest_preferred(r_ideal, series)
    current = (v_supply - v_forward) / r
    power = current * current * r
    err = 100.0 * (current - i_led) / i_led
    return LedResult(resistor=r, current_a=current, current_ideal_a=i_led,
                     power_w=power, error_pct=err)


def rc_lowpass(
    f_cutoff: float,
    *,
    r: Optional[float] = None,
    c: Optional[float] = None,
    series: str = "E24",
) -> RcResult:
    """Size a first-order RC low-pass for ``f = 1 / (2*pi*R*C)``.

    Provide exactly one of ``r`` / ``c``; the other is computed and snapped to
    ``series`` (a capacitor's nearest preferred value, since C stocks follow the
    same E-series). Returns the achieved cut-off. ``f_cutoff > 0``.
    """
    if f_cutoff <= 0:
        raise ValueError("f_cutoff must be positive")
    if (r is None) == (c is None):
        raise ValueError("provide exactly one of r or c")
    if r is not None:
        c_ideal = 1.0 / (2.0 * math.pi * r * f_cutoff)
        c = nearest_preferred(c_ideal, series)
    else:
        r_ideal = 1.0 / (2.0 * math.pi * c * f_cutoff)
        r = nearest_preferred(r_ideal, series)
    f = 1.0 / (2.0 * math.pi * r * c)
    err = 100.0 * (f - f_cutoff) / f_cutoff
    return RcResult(r=r, c=c, f_cutoff=f, f_cutoff_ideal=f_cutoff,
                    error_pct=err)


def crystal_load_caps(
    c_load: float,
    c_stray: float = 5.0e-12,
    *,
    series: str = "E24",
) -> CrystalCapResult:
    """Size the two symmetric load capacitors of a crystal oscillator.

    A crystal specifies a load capacitance ``CL`` it must see; the two
    external caps C1 = C2 plus the board/pin stray ``Cstray`` on each leg
    form that load: ``CL = C1*C2/(C1+C2) + Cstray = C/2 + Cstray`` for equal
    caps, so ``C = 2*(CL - Cstray)``. Returns the snapped each-cap value and
    the load it actually presents. ``c_load > c_stray`` required.
    """
    if not (c_load > c_stray >= 0):
        raise ValueError("require c_load > c_stray >= 0")
    cap_ideal = 2.0 * (c_load - c_stray)
    cap = nearest_preferred(cap_ideal, series)
    cl_achieved = cap / 2.0 + c_stray
    err = 100.0 * (cl_achieved - c_load) / c_load
    return CrystalCapResult(cap=cap, cap_ideal=cap_ideal,
                            c_load_achieved=cl_achieved, c_load_target=c_load,
                            error_pct=err)


def i2c_pullup(
    v_bus: float,
    c_bus: float,
    t_rise: float,
    *,
    i_ol: float = 3.0e-3,
    v_ol: float = 0.4,
    series: str = "E24",
) -> I2cPullupResult:
    """Bound the I2C bus pull-up resistor (NXP UM10204).

    Two constraints frame the value:

    * **Rise-time ceiling** -- the bus rises through one RC; the I2C spec
      uses ``t_rise = 0.8473 * R * Cbus`` (10..90 %), so
      ``Rmax = t_rise / (0.8473 * Cbus)``.
    * **Sink-current floor** -- the open-drain low must reach ``v_ol`` while
      sinking ``i_ol``: ``Rmin = (v_bus - v_ol) / i_ol``.

    ``t_rise`` is the spec maximum for the mode (1000 ns standard, 300 ns
    fast, 120 ns fast-plus). The recommendation is the LARGEST preferred value
    that still fits the window (lowest static power while meeting rise time);
    ``feasible`` is False when no preferred value lies in ``[Rmin, Rmax]`` (the
    bus capacitance is too high for the mode -- split the bus or add a buffer).
    """
    if v_bus <= v_ol:
        raise ValueError("require v_bus > v_ol")
    if c_bus <= 0 or t_rise <= 0 or i_ol <= 0:
        raise ValueError("c_bus, t_rise, i_ol must be positive")
    r_min = (v_bus - v_ol) / i_ol
    r_max = t_rise / (0.8473 * c_bus)
    if r_min > r_max:
        # The sink-current floor exceeds the rise-time ceiling: the bus
        # capacitance is too high for this mode at this voltage. No value fits.
        return I2cPullupResult(r_min=r_min, r_max=r_max, recommended=None,
                               feasible=False)
    candidates = [v for v in _values_in_range(
        series_mantissas(series), max(1.0, r_min), max(1.0, r_max))
        if r_min <= v <= r_max]
    recommended = max(candidates) if candidates else None
    return I2cPullupResult(r_min=r_min, r_max=r_max, recommended=recommended,
                           feasible=recommended is not None)


def divider_tolerance(
    v_in: float,
    r_top: float,
    r_bottom: float,
    *,
    tol_pct: float = 1.0,
) -> DividerToleranceResult:
    """Worst-case output window of an unloaded divider under resistor
    tolerance.

    The tap ``Vout = Vin*Rb/(Rt+Rb)`` is highest when Rb is at its high
    extreme and Rt at its low extreme, and lowest in the opposite corner --
    so the two resistors' tolerances stack rather than cancel. Returns the
    nominal, min and max output and the spread. ``tol_pct`` is the symmetric
    resistor tolerance in percent (1 for 1 %).
    """
    if v_in <= 0 or r_top <= 0 or r_bottom <= 0:
        raise ValueError("v_in, r_top, r_bottom must be positive")
    if tol_pct < 0:
        raise ValueError("tol_pct must be >= 0")
    t = tol_pct / 100.0
    nom = v_in * r_bottom / (r_top + r_bottom)
    v_max = v_in * (r_bottom * (1 + t)) / (r_top * (1 - t) + r_bottom * (1 + t))
    v_min = v_in * (r_bottom * (1 - t)) / (r_top * (1 + t) + r_bottom * (1 - t))
    spread = 100.0 * (v_max - v_min) / nom if nom else 0.0
    return DividerToleranceResult(v_nominal=nom, v_min=v_min, v_max=v_max,
                                  spread_pct=spread)


def opamp_gain_resistors(
    gain: float,
    *,
    config: str = "inverting",
    series: str = "E96",
    r_low: float = 1.0e3,
    r_high: float = 1.0e5,
) -> OpampGainResult:
    """Pick the Rf / Rin (or Rg) pair for an op-amp gain stage.

    ``inverting``: ``gain = -Rf/Rin`` -> ``Rf/Rin = |gain|``.
    ``non_inverting``: ``gain = 1 + Rf/Rg`` -> ``Rf/Rg = gain - 1``.

    Enumerates the input/ground resistor over ``series`` in
    ``[r_low, r_high]``, snaps the feedback resistor, and keeps the pair whose
    achieved gain magnitude is closest to the target. A non-inverting gain must
    be > 1 (a unity follower needs no gain resistors). Composes with the
    inverting op-amp motif: this sizes what that recognises.
    """
    cfg = config.strip().lower().replace("-", "_")
    g = abs(gain)
    if cfg == "inverting":
        if g <= 0:
            raise ValueError("inverting gain magnitude must be > 0")
        ratio = g                       # Rf/Rin
    elif cfg in ("non_inverting", "noninverting"):
        if g <= 1:
            raise ValueError(
                "non-inverting gain must be > 1; a unity follower needs no "
                "gain resistors")
        ratio = g - 1.0                 # Rf/Rg
    else:
        raise ValueError("config must be 'inverting' or 'non_inverting'")

    candidates = _values_in_range(series_mantissas(series), r_low, r_high)
    best: Optional[OpampGainResult] = None
    for r_in in candidates:
        rf = nearest_preferred(ratio * r_in, series)
        achieved = rf / r_in if cfg == "inverting" else 1.0 + rf / r_in
        err = 100.0 * (achieved - g) / g
        if best is None or abs(err) < abs(best.error_pct):
            best = OpampGainResult(
                config=("inverting" if cfg == "inverting" else "non_inverting"),
                r_feedback=rf, r_input=r_in, gain=achieved, gain_ideal=g,
                error_pct=err)
    assert best is not None
    return best


def buck_inductor(
    v_in: float,
    v_out: float,
    i_out: float,
    f_sw: float,
    *,
    ripple_fraction: float = 0.3,
    series: str = "E12",
) -> BuckInductorResult:
    """Size a buck-converter inductor (Erickson, *Fundamentals of Power
    Electronics*).

    ``L = (Vin - Vout) * Vout / (Vin * fsw * dIL)`` where the target ripple
    ``dIL = ripple_fraction * Iout`` (30 % is the usual rule). Snaps L to
    ``series`` and reports the ACTUAL peak-to-peak ripple and the peak inductor
    current ``Iout + dIL/2`` (the saturation budget). Requires
    ``v_in > v_out > 0``, positive ``i_out`` / ``f_sw``, and
    ``0 < ripple_fraction < 1``.
    """
    if not (v_in > v_out > 0):
        raise ValueError("require v_in > v_out > 0 for a buck")
    if i_out <= 0 or f_sw <= 0:
        raise ValueError("i_out and f_sw must be positive")
    if not (0.0 < ripple_fraction < 1.0):
        raise ValueError("ripple_fraction must be in (0, 1)")
    d_il_target = ripple_fraction * i_out
    l_ideal = (v_in - v_out) * v_out / (v_in * f_sw * d_il_target)
    inductance = nearest_preferred(l_ideal, series)
    d_il = (v_in - v_out) * v_out / (v_in * f_sw * inductance)
    peak = i_out + d_il / 2.0
    err = 100.0 * (inductance - l_ideal) / l_ideal
    return BuckInductorResult(
        inductance=inductance, inductance_ideal=l_ideal,
        ripple_current_a=d_il, peak_current_a=peak, error_pct=err)


def capacitor_energy(capacitance: float, voltage: float) -> float:
    """Energy stored in a capacitor, ``E = 0.5 * C * V^2`` (joules).

    Useful for surge / inrush budgeting and for checking a bulk cap's stored
    energy against a safety limit. ``capacitance`` and ``voltage`` must be
    non-negative."""
    if capacitance < 0 or voltage < 0:
        raise ValueError("capacitance and voltage must be non-negative")
    return 0.5 * capacitance * voltage * voltage


def holdup_capacitance(
    i_load: float,
    t_holdup: float,
    v_drop: float,
) -> float:
    """Bulk capacitance to hold a rail up for ``t_holdup`` while it sags by
    ``v_drop`` supplying ``i_load`` (constant-current approximation):
    ``C = I * t / dV`` (from ``C*dV = I*t``). Farads.

    The standard hold-up / ride-through sizing for a power input. All inputs
    must be positive."""
    if i_load <= 0 or t_holdup <= 0 or v_drop <= 0:
        raise ValueError("i_load, t_holdup, v_drop must be positive")
    return i_load * t_holdup / v_drop


def discharge_resistor(
    capacitance: float,
    v_initial: float,
    v_final: float,
    t_discharge: float,
) -> float:
    """Bleeder resistor that discharges ``capacitance`` from ``v_initial`` to
    ``v_final`` within ``t_discharge`` via ``V(t) = Vi * exp(-t/RC)``:
    ``R = t / (C * ln(Vi/Vf))`` (ohms).

    The classic X-cap / bulk-cap safety bleeder sizing (discharge to a safe
    voltage within the standard time). Requires ``v_initial > v_final > 0`` and
    positive capacitance / time."""
    if capacitance <= 0 or t_discharge <= 0:
        raise ValueError("capacitance and t_discharge must be positive")
    if not (v_initial > v_final > 0):
        raise ValueError("require v_initial > v_final > 0")
    return t_discharge / (capacitance * math.log(v_initial / v_final))


def junction_temperature(
    power_w: float,
    theta_ja: float,
    t_ambient: float = 25.0,
) -> float:
    """Junction temperature ``Tj = Ta + P * theta_JA`` (degC).

    The basic steady-state thermal check: a device dissipating ``power_w``
    through a junction-to-ambient thermal resistance ``theta_ja`` (degC/W) sits
    this far above ambient. Compare against the datasheet ``Tj(max)``. Power
    and theta must be non-negative."""
    if power_w < 0 or theta_ja < 0:
        raise ValueError("power_w and theta_ja must be non-negative")
    return t_ambient + power_w * theta_ja


def max_power_dissipation(
    tj_max: float,
    theta_ja: float,
    t_ambient: float = 25.0,
) -> float:
    """Maximum power a part can dissipate before reaching ``tj_max``:
    ``P = (Tj_max - Ta) / theta_JA`` (watts) -- the thermal derating. Requires
    ``tj_max > t_ambient`` and positive ``theta_ja``."""
    if theta_ja <= 0:
        raise ValueError("theta_ja must be positive")
    if tj_max <= t_ambient:
        raise ValueError("require tj_max > t_ambient (no thermal headroom)")
    return (tj_max - t_ambient) / theta_ja


def required_theta_ja(
    power_w: float,
    tj_max: float,
    t_ambient: float = 25.0,
) -> float:
    """The junction-to-ambient thermal resistance a part needs to keep ``Tj``
    at or below ``tj_max`` while dissipating ``power_w``:
    ``theta_JA = (Tj_max - Ta) / P`` (degC/W) -- sizes the package / heatsink /
    copper area. Requires positive power and ``tj_max > t_ambient``."""
    if power_w <= 0:
        raise ValueError("power_w must be positive")
    if tj_max <= t_ambient:
        raise ValueError("require tj_max > t_ambient (no thermal headroom)")
    return (tj_max - t_ambient) / power_w


def _values_in_range(mantissas: list[float], lo: float, hi: float) -> list[float]:
    """All preferred values m*10^d that fall within [lo, hi]."""
    if lo <= 0 or hi < lo:
        raise ValueError("require 0 < lo <= hi")
    out: list[float] = []
    d_lo = math.floor(math.log10(lo))
    d_hi = math.floor(math.log10(hi))
    for d in range(d_lo, d_hi + 1):
        scale = 10.0 ** d
        for m in mantissas:
            v = m * scale
            if lo <= v <= hi:
                out.append(v)
    return out


__all__ = [
    "DividerResult",
    "LedResult",
    "RcResult",
    "CrystalCapResult",
    "I2cPullupResult",
    "DividerToleranceResult",
    "series_mantissas",
    "nearest_preferred",
    "resistor_divider",
    "feedback_divider",
    "led_series_resistor",
    "rc_lowpass",
    "crystal_load_caps",
    "i2c_pullup",
    "divider_tolerance",
    "OpampGainResult",
    "BuckInductorResult",
    "opamp_gain_resistors",
    "buck_inductor",
    "capacitor_energy",
    "holdup_capacitance",
    "discharge_resistor",
    "junction_temperature",
    "max_power_dissipation",
    "required_theta_ja",
]
