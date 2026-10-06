# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Design-rule + stackup synthesis from a fab profile and net classes.

Setting up a board means turning three inputs -- the fab's published limits
(:class:`~eda_agent.design.fab_profile.FabProfile`), the net-class map
(``classify_nets``), and a few board-level targets (per-class current,
differential impedance) -- into the concrete ``pcb_create_design_rule`` and
``pcb_modify_layer`` calls. This module does that mapping offline so the
caller dispatches the returned dicts verbatim.

Every synthesized number traces to either a profile field or one of the
verified calculators (``trace_sizing`` IPC-2221 inverse, ``impedance_sizing``
IPC-2141 inverse); an internal provenance assert enforces that no value can
reach the output any other way. Nothing here carries capability numbers --
when an input needed for a rule is missing (no impedance target, no er on
the stackup), the rule is skipped with a note rather than guessed.

Units: all rule values and stackup heights are INTEGER MILS on the wire
(the wrapper shape); profile floats are rounded up for widths/clearances so
rounding never lands below a computed or fab minimum.
"""

from __future__ import annotations

import math
import re
from typing import Optional, Union

from eda_agent.design.fab_profile import (
    FabProfile,
    Stackup,
    copper_layers,
    dielectric_spans,
    load_fab_profile,
)

_OPTION_KEYS = {
    "stackup",                 # stackup name in the profile (default: first)
    "geometry",                # diff geometry, default "microstrip_diff"
    "class_current_a",         # {class: amps} -> per-class width rules
    "delta_t_c",               # passthrough to trace_width_for_current
    "track_margin",            # passthrough margin to trace_width_for_current
    "layer",                   # "external"/"internal" for trace sizing
    "copper_oz",               # override the stackup's outer copper weight
    "diff_pair_target_ohms",   # Zdiff target; required for differential rules
    "diff_pair_spacing_mils",  # diff gap (default: profile min gap)
}

# Numeric fields the wrappers transmit; the provenance assert walks these.
_NUMERIC_FIELDS = (
    "value", "max_value", "favored_value", "max_uncoupled_length",
    "copper_thickness_mils", "dielectric_height_mils", "dielectric_constant",
)


def _sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def _altium_layer_name(index: int, copper_count: int) -> str:
    if index == 0:
        return "TopLayer"
    if index == copper_count - 1:
        return "BottomLayer"
    return f"MidLayer{index}"


def synthesize_rules(
    profile: Union[FabProfile, dict, str],
    net_class_map,
    options: Optional[dict] = None,
) -> dict:
    """Synthesize PCB design rules + stackup ops from ``profile`` and the
    net-class assignment.

    ``profile`` is a :class:`FabProfile`, a profile dict, or a JSON path
    (see :func:`load_fab_profile`). ``net_class_map`` is the ``by_net``
    dict from :func:`~eda_agent.design.net_classes.classify_nets` (the
    report itself is also accepted). ``options`` keys are listed in
    ``_OPTION_KEYS``; class-scoped rules assume the caller creates PCB net
    classes named after the synthesized classes (``pcb_create_net_class``).

    Returns ``{"ok": True, "rules": [...], "stackup_ops": [...],
    "notes": [...], "stackup": name|None}`` where each ``rules`` entry is a
    ``pcb_create_design_rule`` parameter dict (mils, ints) and each
    ``stackup_ops`` entry is a ``pcb_modify_layer`` parameter dict.
    On bad input: ``{"ok": False, "reason": str}``.
    """
    from eda_agent.design.impedance_sizing import trace_width_for_impedance
    from eda_agent.design.trace_sizing import trace_width_for_current

    loaded = load_fab_profile(profile)
    if not loaded["ok"]:
        return loaded
    prof: FabProfile = loaded["profile"]

    if hasattr(net_class_map, "by_net"):
        net_class_map = net_class_map.by_net
    if not isinstance(net_class_map, dict):
        return {"ok": False,
                "reason": "net_class_map must be a net->class dict "
                          "(or a NetClassReport)"}
    opts = dict(options or {})
    unknown = sorted(set(opts) - _OPTION_KEYS)
    if unknown:
        return {"ok": False,
                "reason": f"unknown option(s): {', '.join(unknown)}; "
                          f"valid: {', '.join(sorted(_OPTION_KEYS))}"}

    stk = _pick_stackup(prof, opts.get("stackup"))
    if isinstance(stk, dict):              # error shape from the picker
        return stk

    allowed: set = set()                   # provenance: every emitted number

    def rec(v):
        allowed.add(v)
        return v

    rules: list[dict] = []
    notes: list[str] = []
    if prof.source:
        notes.append(f"profile values transcribed from: {prof.source}")

    # --- fab-floor rules, straight from the profile -----------------------
    min_track = rec(math.ceil(prof.min_track_mils))
    min_gap = rec(math.ceil(prof.min_gap_mils))
    min_drill = rec(math.ceil(prof.min_drill_mils))
    rules.append({
        "name": "Clearance_Fab_Min", "rule_type": "clearance",
        "value": min_gap, "scope": "All", "net_scope": "different_nets"})
    rules.append({
        "name": "Width_Fab_Min", "rule_type": "width",
        "value": min_track, "favored_value": min_track, "scope": "All"})
    rules.append({
        "name": "Via_Fab_Min_Drill", "rule_type": "via_size",
        "value": min_drill, "scope": "All"})
    via_pad = math.ceil(prof.min_drill_mils + 2 * prof.min_annular_ring_mils)
    notes.append(
        f"minimum via pad diameter {via_pad} mil (min drill + 2x min annular "
        f"ring); no annular rule type is exposed, enforce when picking via "
        f"styles")
    notes.append(
        f"profile floors with no wrapper rule type, enforce manually: "
        f"hole-to-hole {prof.min_hole_to_hole_mils} mil, mask sliver "
        f"{prof.min_mask_sliver_mils} mil, silk width "
        f"{prof.min_silk_width_mils} mil")

    classes_present = set(net_class_map.values())
    copper_oz = _resolve_copper_oz(opts, stk)

    # --- per-class width rules from the IPC-2221 current inverse ----------
    class_current = opts.get("class_current_a") or {}
    if not isinstance(class_current, dict):
        return {"ok": False, "reason": "class_current_a must be a dict "
                                       "{class: amps}"}
    for cls in sorted(class_current):
        amps = class_current[cls]
        if cls not in classes_present:
            notes.append(f"class_current_a[{cls!r}] skipped: no net in that "
                         f"class")
            continue
        kwargs: dict = {}
        if copper_oz is not None:
            kwargs["copper_oz"] = copper_oz
        if "delta_t_c" in opts:
            kwargs["delta_t_c"] = opts["delta_t_c"]
        if "track_margin" in opts:
            kwargs["margin"] = opts["track_margin"]
        if "layer" in opts:
            kwargs["layer"] = opts["layer"]
        try:
            sized = trace_width_for_current(amps, **kwargs)
        except ValueError as exc:
            return {"ok": False, "reason": f"class {cls!r}: {exc}"}
        width = math.ceil(sized.recommended_width_mils)
        if width < min_track:
            notes.append(
                f"Width_{cls}: computed {sized.recommended_width_mils} mil "
                f"for {amps} A is below the fab minimum track; clamped to "
                f"{min_track} mil")
            width = min_track
        rec(width)
        rules.append({
            "name": f"Width_{_sanitize(cls)}", "rule_type": "width",
            "value": width, "favored_value": width,
            "scope": f"InNetClass('{cls}')"})
        notes.append(
            f"Width_{cls}: {amps} A on {sized.layer} layer at "
            f"+{sized.delta_t_c} degC via IPC-2221 inverse -> "
            f"{sized.recommended_width_mils} mil (rule {width} mil)")

    # --- differential pair rules from the IPC-2141 impedance inverse ------
    if "differential" in classes_present:
        diff = _diff_rules(
            prof, stk, opts, copper_oz, min_track, min_gap, rec,
            trace_width_for_impedance)
        if isinstance(diff, dict) and not diff.get("ok", True):
            return diff
        diff_rules_out, diff_notes = diff
        rules.extend(diff_rules_out)
        notes.extend(diff_notes)

    # --- stackup ops -------------------------------------------------------
    stackup_ops: list[dict] = []
    if stk is None:
        notes.append("no stackup in profile; stackup_ops empty")
    else:
        stackup_ops = _stackup_ops(stk, rec, notes)

    _assert_traced(rules, stackup_ops, allowed)
    return {"ok": True, "rules": rules, "stackup_ops": stackup_ops,
            "notes": notes, "stackup": stk.name if stk else None}


def _pick_stackup(prof: FabProfile, name: Optional[str]):
    """The requested (or first) stackup, None when the profile has none, or
    an ``{"ok": False, ...}`` dict for an unknown name."""
    if name:
        for stk in prof.stackups:
            if stk.name == name:
                return stk
        return {"ok": False,
                "reason": f"stackup {name!r} not in profile "
                          f"{[s.name for s in prof.stackups]}"}
    return prof.stackups[0] if prof.stackups else None


def _resolve_copper_oz(opts: dict, stk: Optional[Stackup]) -> Optional[float]:
    """Copper weight for the calculators: explicit option, else the outer
    copper ply's weight, else None (the calculator's own default applies)."""
    if "copper_oz" in opts:
        return opts["copper_oz"]
    if stk is not None:
        return copper_layers(stk)[0].copper_oz
    return None


def _diff_rules(prof, stk, opts, copper_oz, min_track, min_gap, rec,
                trace_width_for_impedance):
    """Width + gap rules for the differential class, or (rules, notes) with
    a skip note when an input is missing. May return an error dict."""
    notes: list[str] = []
    target = opts.get("diff_pair_target_ohms")
    if target is None:
        notes.append("differential class present but no diff_pair_target_ohms "
                     "supplied; differential rules skipped")
        return [], notes
    if stk is None:
        notes.append("differential rules skipped: profile has no stackup to "
                     "size impedance against")
        return [], notes
    span = dielectric_spans(stk)[0]        # under the top copper
    if span.er is None:
        notes.append(f"differential rules skipped: stackup {stk.name!r} has "
                     f"no er on the dielectric under the top copper")
        return [], notes

    spacing = math.ceil(opts.get("diff_pair_spacing_mils",
                                 prof.min_gap_mils))
    if spacing < min_gap:
        notes.append(f"diff pair spacing {spacing} mil is below the fab "
                     f"minimum gap; clamped to {min_gap} mil")
        spacing = min_gap
    rec(spacing)

    geometry = str(opts.get("geometry", "microstrip_diff")).strip().lower()
    if not geometry.endswith("_diff"):
        return {"ok": False,
                "reason": f"geometry {geometry!r} is not differential "
                          f"(microstrip_diff / stripline_diff)"}
    kwargs = {"dielectric_constant": span.er,
              "spacing_mils": float(spacing)}
    if copper_oz is not None:
        kwargs["copper_oz"] = copper_oz
    try:
        sized = trace_width_for_impedance(target, geometry,
                                          span.height_mils, **kwargs)
    except ValueError as exc:
        return {"ok": False, "reason": f"differential sizing: {exc}"}
    if not sized.feasible:
        notes.append(
            f"differential rules skipped: {target} ohm is infeasible for "
            f"this stackup (h={span.height_mils} mil, er={span.er}); raise "
            f"the spacing or use a thinner dielectric")
        return [], notes

    width = math.ceil(sized.width_mils)
    if width < min_track:
        notes.append(
            f"Width_differential: computed {round(sized.width_mils, 2)} mil "
            f"for {target} ohm is below the fab minimum track; clamped to "
            f"{min_track} mil -- actual impedance will run below target")
        width = min_track
    rec(width)
    rules = [
        {"name": "Width_differential", "rule_type": "width",
         "value": width, "favored_value": width,
         "scope": "InNetClass('differential')"},
        # Gap pinned to the sized spacing: the impedance only holds at the
        # spacing the width was solved for.
        {"name": "DiffPair_Gap", "rule_type": "differential_pairs",
         "value": spacing, "favored_value": spacing, "max_value": spacing,
         "scope": "IsDifferentialPair"},
    ]
    notes.append(
        f"differential: {target} ohm {geometry} on h={span.height_mils} mil "
        f"er={span.er} at gap {spacing} mil via IPC-2141 inverse -> "
        f"{round(sized.width_mils, 2)} mil (rule {width} mil, "
        f"SE Z0 {round(sized.single_ended_z0_ohms, 1)} ohm)")
    return rules, notes


def _stackup_ops(stk: Stackup, rec, notes: list[str]) -> list[dict]:
    """One ``pcb_modify_layer`` dict per copper layer; the dielectric fields
    describe the span directly below that layer."""
    coppers = copper_layers(stk)
    spans = dielectric_spans(stk)
    ops: list[dict] = []
    for i, cu in enumerate(coppers):
        op: dict = {"layer": _altium_layer_name(i, len(coppers)),
                    "name": cu.name}
        thick = int(round(cu.thickness_mils))
        if thick >= 1:
            op["copper_thickness_mils"] = rec(thick)
        else:
            notes.append(
                f"{cu.name}: copper thickness {cu.thickness_mils} mil rounds "
                f"below 1 mil; the wrapper takes integer mils, thickness not "
                f"set")
        if i < len(coppers) - 1:
            span = spans[i]
            op["dielectric_type"] = span.kind
            height = int(round(span.height_mils))
            if height >= 1:
                op["dielectric_height_mils"] = rec(height)
                if abs(height - span.height_mils) > 1e-9:
                    notes.append(
                        f"{cu.name}: dielectric height "
                        f"{span.height_mils} mil rounded to {height} mil "
                        f"(wrapper takes integer mils)")
            else:
                notes.append(
                    f"{cu.name}: dielectric height {span.height_mils} mil "
                    f"rounds below 1 mil; not set")
            if span.er is not None:
                op["dielectric_constant"] = rec(round(span.er, 3))
            if span.ply_count > 1:
                notes.append(
                    f"{cu.name}: {span.ply_count} dielectric plies combined "
                    f"into one span (summed height, thickness-weighted er) "
                    f"-- Altium's layer model takes one dielectric per layer")
        ops.append(op)
    return ops


def _assert_traced(rules: list[dict], stackup_ops: list[dict],
                   allowed: set) -> None:
    """Every numeric leaving this module must have been recorded when it was
    derived from the profile or a calculator. A failure here is a bug, not
    a user error, hence assert rather than the ok/reason shape."""
    for d in rules + stackup_ops:
        for field in _NUMERIC_FIELDS:
            if field in d:
                assert d[field] in allowed, \
                    f"untraced value {field}={d[field]} in {d.get('name', d)}"


__all__ = ["synthesize_rules"]
