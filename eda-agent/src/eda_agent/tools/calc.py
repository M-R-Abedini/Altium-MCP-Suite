# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""EDA-agnostic engineering calculators.

Trace-width, impedance, termination, length-match and thermal-via sizing are
pure physics (IPC-2221/2141, transmission-line theory) with no dependency on any
EDA tool. The Altium backend already exposes these as ``pcb_calc_*``; this module
gives the KiCad backend the same tools, with the same names and the same
underlying functions, so the two backends are calculator-equivalent.

Offline family: ``{"ok": True, ...}`` / ``{"ok": False, "reason": ...}``.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Optional


def _dictify(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return {"result": obj}


def _ok(obj: Any) -> dict[str, Any]:
    return {"ok": True, **_dictify(obj)}


def _fail(e: Exception) -> dict[str, Any]:
    return {"ok": False, "reason": str(e)}


def _either(canonical_name: str, canonical, alias_name: str, alias):
    """One quantity, spelled two ways, so the same call works anywhere.

    These calculators are registered on every backend, but the Altium
    build spells several arguments with their unit and the shared build
    spells them tersely: ``z0_ohms`` against ``z0``,
    ``dielectric_constant`` against ``er``, ``current_amps`` against
    ``current_a``. Same physics, same result, different keyword, so a
    client written against one backend raised a missing-argument error
    on the other. That is not feature parity in the way that matters:
    the tool is there, listed, documented, and the call still fails.

    Accepting both is purely additive, which is the point. These are
    published signatures, so renaming either spelling would break
    whichever callers already use it, and there is no way to know which
    those are.

    Returns the value, or raises so the caller's existing except-clause
    turns it into the ordinary refusal shape rather than a traceback.
    """
    if canonical is not None and alias is not None:
        if canonical != alias:
            raise ValueError(
                f"{canonical_name}={canonical} and {alias_name}={alias} were "
                f"both given and disagree; they are the same quantity, so "
                f"pass one")
        return canonical
    value = canonical if canonical is not None else alias
    if value is None:
        raise ValueError(
            f"{canonical_name} is required (it may also be spelled "
            f"{alias_name})")
    return value


def register_calc_tools(mcp) -> None:

    @mcp.tool()
    async def pcb_calc_impedance(
        geometry: str,
        width_mils: float,
        dielectric_height_mils: float,
        dielectric_constant: float = 4.2,
        copper_oz: float = 1.0,
        spacing_mils: float = 0.0,
    ) -> dict[str, Any]:
        """Characteristic impedance of a PCB trace (IPC-2141 / Wadell).

        The forward direction of ``pcb_calc_trace_width_for_impedance``:
        instead of "how wide for 50 ohms?", it answers "what impedance
        does this width give?". Pure physics, no EDA tool involved.

        Measured against a live editor: this existed only on the Altium
        backend,
        so KiCad and EasyEDA users could size a width for a target but
        could not check an existing one. The signature and reply match
        the Altium tool exactly, and both now compute through
        design/impedance_sizing, so the two backends cannot answer
        differently.

        Accuracy is the usual closed-form +/-10 percent; a fab's field
        solver refines it against the real stackup.

        Args:
            geometry: microstrip / microstrip_diff / stripline /
                stripline_diff.
            width_mils: trace width.
            dielectric_height_mils: for microstrip the height above the
                reference plane; for stripline the FULL thickness
                between the two planes.
            dielectric_constant: er, 4.2 for common FR-4.
            copper_oz: copper weight; 1 oz is 1.378 mils finished.
            spacing_mils: edge-to-edge gap, required for the _diff
                geometries.

        Returns:
            {ok, geometry, width_mils, dielectric_height_mils,
             dielectric_constant, thickness_mils, z0_ohms,
             propagation_delay_ps_per_inch}, plus spacing_mils and
            zdiff_ohms for a differential geometry.
        """
        import math

        from ..design.impedance_sizing import (
            diff_coupling_factor, z0_microstrip, z0_stripline,
        )
        from ..units import OZ_TO_MILS

        geometry = geometry.strip().lower()
        valid = ("microstrip", "microstrip_diff",
                 "stripline", "stripline_diff")
        if geometry not in valid:
            return {"ok": False,
                    "reason": "geometry must be one of " + ", ".join(valid)}
        if width_mils <= 0 or dielectric_height_mils <= 0:
            return {"ok": False, "reason":
                    "width_mils and dielectric_height_mils must be > 0"}
        if dielectric_constant <= 0:
            return {"ok": False, "reason": "dielectric_constant must be > 0"}
        is_diff = geometry.endswith("_diff")
        if is_diff and spacing_mils <= 0:
            return {"ok": False, "reason":
                    "spacing_mils must be > 0 for differential geometries"}

        w = float(width_mils)
        h = float(dielectric_height_mils)
        er = float(dielectric_constant)
        t = copper_oz * OZ_TO_MILS

        if geometry.startswith("microstrip"):
            z0 = z0_microstrip(w, h, er, copper_oz=copper_oz)
            er_eff = (er + 1.0) / 2.0
        else:
            z0 = z0_stripline(w, h, er, copper_oz=copper_oz)
            er_eff = er

        # A NON-POSITIVE IMPEDANCE IS NOT AN ANSWER.
        #
        # Both closed forms are a logarithm of (reference spacing over
        # conductor width), so once the trace is wide enough relative to
        # the dielectric the argument passes 1 and the result goes
        # through zero and negative. Measured: microstrip turns negative
        # around w/h 7.4, and stripline reads 0.4 ohms at w/h 2.0 and
        # negative at 4.0.
        #
        # The small positive value is the more dangerous of the two,
        # because a negative impedance is visibly wrong and 0.4 ohms
        # merely looks like a badly matched trace. Both are the formula
        # being used outside its range, so both are refused rather than
        # returned with a caveat nobody reads.
        from ..design.impedance_sizing import impedance_validity
        checked = impedance_validity(z0, w, h, er)
        if not checked["usable"]:
            return {
                "ok": False,
                "reason": checked["reason"],
                "geometry": geometry,
                "width_mils": w,
                "dielectric_height_mils": h,
                "width_to_height_ratio": checked["width_to_height_ratio"],
            }

        # c0 = 11.8 in/ns in vacuum, so tpd = sqrt(er_eff)*1000/11.8 ps/in
        out: dict[str, Any] = {
            "ok": True,
            "geometry": geometry,
            "width_mils": w,
            "dielectric_height_mils": h,
            "dielectric_constant": er,
            "thickness_mils": round(t, 3),
            "z0_ohms": round(z0, 1),
            "propagation_delay_ps_per_inch": round(
                math.sqrt(er_eff) * 1000.0 / 11.8, 2),
        }
        # IPC-2141 states these expressions over roughly 0.1 <= w/h <= 2
        # and 1 <= er <= 15. Inside that band the usual +/-10 percent
        # applies; outside it the error grows without warning, and the
        # answer still arrives as a tidy number. Saying which side of the
        # band a result came from is the difference between a figure to
        # design against and one to check with a field solver.
        out["width_to_height_ratio"] = checked["width_to_height_ratio"]
        if checked["outside_validity_range"]:
            out["outside_validity_range"] = True
            out["accuracy_warning"] = checked["warning"]

        if is_diff:
            out["spacing_mils"] = float(spacing_mils)
            out["zdiff_ohms"] = round(
                2.0 * z0 * diff_coupling_factor(
                    geometry, float(spacing_mils), h), 1)
        return out

    @mcp.tool()
    async def pcb_calc_trace_width_for_impedance(
            target_ohms: float, geometry: str, dielectric_height_mils: float,
            dielectric_constant: float = 4.2, copper_oz: float = 1.0,
            spacing_mils: float = 0.0) -> dict[str, Any]:
        """Trace width (mils) to hit a target impedance for a geometry
        (microstrip / stripline / their *_diff variants). Differential targets
        need spacing_mils. Inverse of the IPC-2141 impedance formula."""
        from ..design.impedance_sizing import trace_width_for_impedance
        try:
            out = _ok(trace_width_for_impedance(
                target_ohms, geometry, dielectric_height_mils,
                dielectric_constant=dielectric_constant, copper_oz=copper_oz,
                spacing_mils=spacing_mils))
        except Exception as e:
            return _fail(e)

        # The SAME validity band the forward direction reports.
        #
        # Solving for width cannot produce a negative number, so this
        # direction never looks wrong: asking for 1 ohm on a
        # 5 mil dielectric answers 34.6 mils and calls it feasible. That
        # is w/h near 7, far outside the range the closed form is stated
        # over, and the forward tool flags exactly that geometry. Two
        # halves of one formula must not disagree about whether an
        # answer can be trusted.
        width = out.get("width_mils")
        height = float(dielectric_height_mils or 0)
        if isinstance(width, (int, float)) and height > 0:
            ratio = width / height
            out["width_to_height_ratio"] = round(ratio, 3)
            er = float(dielectric_constant)
            if ratio > 2.0 or ratio < 0.1 or er < 1.0 or er > 15.0:
                out["outside_validity_range"] = True
                out["accuracy_warning"] = (
                    f"the width solves to w/h {ratio:.2f} with er {er}. The "
                    f"IPC-2141 closed form is stated for 0.1 to 2.0 and er "
                    f"1 to 15, so this is an extrapolation rather than the "
                    f"usual +/-10 percent. A target this far outside the "
                    f"band usually means the stackup needs changing rather "
                    f"than the trace.")
        return out

    @mcp.tool()
    async def pcb_calc_termination(
            length_mils: float, rise_time_ns: float,
            z0: Optional[float] = None, er: Optional[float] = None,
            geometry: str = "microstrip",
            driver_impedance: Optional[float] = None,
            vcc: Optional[float] = None, width_mils: Optional[float] = None,
            height_mils: Optional[float] = None,
            multi_load: bool = False,
            z0_ohms: Optional[float] = None,
            dielectric_constant: Optional[float] = None,
            driver_impedance_ohms: Optional[float] = None,
            dielectric_height_mils: Optional[float] = None) -> dict[str, Any]:
        """Assess whether a net is electrically long for its edge rate and, if
        so, recommend a termination (series for point-to-point, Thevenin /
        parallel for a multi-load bus) with E24 values.

        Args:
            length_mils: routed length of the net.
            rise_time_ns: driver edge rate.
            z0: characteristic impedance in ohms. The Altium build
                spells this ``z0_ohms``; either is accepted.
            er: dielectric constant, spelled ``dielectric_constant`` on
                the Altium build; either is accepted.
            geometry: ``microstrip`` or ``stripline``.
            driver_impedance: driver output impedance in ohms, also
                spelled ``driver_impedance_ohms``.
            vcc: supply rail, for Thevenin values.
            width_mils: trace width, if the propagation delay is to be
                derived rather than assumed.
            height_mils: dielectric height, also spelled
                ``dielectric_height_mils``.
            multi_load: true for a bus with several receivers.
            z0_ohms: alias for ``z0``.
            dielectric_constant: alias for ``er``.
            driver_impedance_ohms: alias for ``driver_impedance``.
            dielectric_height_mils: alias for ``height_mils``.
        """
        from ..design.signal_integrity import recommend_termination
        kw: dict[str, Any] = {"geometry": geometry, "multi_load": multi_load}
        try:
            impedance = _either("z0", z0, "z0_ohms", z0_ohms)
            epsilon = _either("er", er, "dielectric_constant",
                              dielectric_constant)
            driver = (driver_impedance if driver_impedance is not None
                      else driver_impedance_ohms)
            height = (height_mils if height_mils is not None
                      else dielectric_height_mils)
            for k, v in (("driver_impedance", driver), ("vcc", vcc),
                         ("width_mils", width_mils), ("height_mils", height)):
                if v is not None:
                    kw[k] = v
            return _ok(recommend_termination(length_mils, rise_time_ns,
                                             impedance, epsilon, **kw))
        except Exception as e:
            return _fail(e)

    @mcp.tool()
    async def pcb_calc_length_match(
            dielectric_constant: float = 4.2, geometry: str = "stripline",
            width_mils: Optional[float] = None,
            dielectric_height_mils: Optional[float] = None,
            skew_budget_ps: Optional[float] = None,
            rise_time_ns: Optional[float] = None,
            match_fraction: float = 0.1,
            lengths: Optional[dict[str, float]] = None) -> dict[str, Any]:
        """Resolve the effective Er and a match tolerance from a skew budget (or
        match_fraction * rise_time_ns), then optionally report a group of net
        lengths against it."""
        from ..design.length_matching import assess_length_match
        try:
            out = _ok(assess_length_match(
                dielectric_constant=dielectric_constant, geometry=geometry,
                width_mils=width_mils,
                dielectric_height_mils=dielectric_height_mils,
                skew_budget_ps=skew_budget_ps, rise_time_ns=rise_time_ns,
                match_fraction=match_fraction, lengths=lengths))
        except Exception as e:
            return _fail(e)

        # SAY WHICH GEOMETRY PRODUCED THE DELAY.
        #
        # A stripline is fully embedded so er_eff is er, while a
        # microstrip has part of its field in air and er_eff is far
        # lower. For er 4.2 that is 173.6 against 138.3 ps per inch, a
        # 25 percent difference in every length this tool converts.
        #
        # This defaults to stripline and pcb_calc_termination defaults
        # to microstrip. Each is defensible alone; together they answer
        # differently for one board, and the reply named neither. The
        # default is not changed here, because moving it would silently
        # alter numbers callers already rely on. What changes is that
        # the assumption is now visible.
        out["geometry"] = geometry
        out["dielectric_constant"] = dielectric_constant
        out["geometry_note"] = (
            f"delay computed for {geometry}. A stripline uses er "
            f"directly and a microstrip uses an effective er near half "
            f"of it, so the two differ by roughly a quarter. Pass "
            f"geometry explicitly if this board is not {geometry}.")
        return out

    @mcp.tool()
    async def pcb_calc_thermal_vias(
            drill_mm: float, plating_um: float,
            length_mm: Optional[float] = None,
            filled_copper: bool = False, power_w: Optional[float] = None,
            delta_t_c: Optional[float] = None,
            target_k_per_w: Optional[float] = None,
            via_count: Optional[int] = None,
            board_thickness_mm: Optional[float] = None) -> dict[str, Any]:
        """Size a thermal-via field for a target thermal resistance (given
        directly, or as power_w with a delta_t_c budget), or score an existing
        via_count. Fourier conduction, R = L/(k*A).

        Args:
            drill_mm: via drill diameter, in mm.
            plating_um: barrel plating thickness, in microns.
            length_mm: conduction length, which for a via through the
                board is the board thickness. The Altium build spells
                this ``board_thickness_mm``; either is accepted.
            filled_copper: whether the barrels are copper filled.
            power_w: power to remove, in watts.
            delta_t_c: permitted temperature rise, in Celsius.
            target_k_per_w: thermal resistance target, given directly.
            via_count: score this many vias instead of sizing a field.
            board_thickness_mm: alias for ``length_mm``.
        """
        from ..design.thermal_vias import assess_thermal_vias
        kw: dict[str, Any] = {"filled_copper": filled_copper}
        for k, v in (("power_w", power_w), ("delta_t_c", delta_t_c),
                     ("target_k_per_w", target_k_per_w),
                     ("via_count", via_count)):
            if v is not None:
                kw[k] = v
        try:
            length = _either("length_mm", length_mm,
                             "board_thickness_mm", board_thickness_mm)
            return _ok(assess_thermal_vias(drill_mm, plating_um, length, **kw))
        except Exception as e:
            return _fail(e)

    @mcp.tool()
    async def pcb_calc_trace_width_for_current(
            current_a: Optional[float] = None, copper_oz: float = 1.0,
            delta_t_c: float = 10.0,
            layer: str = "external", margin: float = 0.2,
            length_mils: float = 0.0,
            current_amps: Optional[float] = None) -> dict[str, Any]:
        """Minimum track width (mils) to carry a current at a temperature rise
        (IPC-2221), widened by ``margin`` and snapped to a 0.1 mil grid.

        Args:
            current_a: the current to carry, in amps. The Altium build
                spells this ``current_amps``; either is accepted.
            copper_oz: copper weight in ounces.
            delta_t_c: permitted temperature rise, in Celsius.
            layer: ``external`` or ``internal``.
            margin: fractional widening applied to the IPC result.
            length_mils: run length, for the voltage-drop figure.
            current_amps: alias for ``current_a``.
        """
        from ..design.trace_sizing import trace_width_for_current
        try:
            amps = _either("current_a", current_a, "current_amps",
                           current_amps)
            return _ok(trace_width_for_current(
                amps, copper_oz=copper_oz, delta_t_c=delta_t_c,
                layer=layer, margin=margin, length_mils=length_mils))
        except Exception as e:
            return _fail(e)

    @mcp.tool()
    async def pcb_calc_track_current_capacity(
            width_mils: float, copper_oz: float = 1.0,
            delta_t_c: float = 10.0, layer: str = "external") -> dict[str, Any]:
        """Forward IPC-2221: the current a track of this width sustains at a
        given temperature rise."""
        from ..design.trace_sizing import current_capacity_amps
        try:
            amps = current_capacity_amps(width_mils, copper_oz=copper_oz,
                                         delta_t_c=delta_t_c, layer=layer)
            return {"ok": True, "current_a": round(amps, 4),
                    "width_mils": width_mils, "copper_oz": copper_oz,
                    "delta_t_c": delta_t_c, "layer": layer}
        except Exception as e:
            return _fail(e)
