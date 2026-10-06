# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The batch payload grammar, over the real bridge.

Every bulk tool encodes its work into ONE string: operations split on
"~~", fields on ";", key and value on the first "=". That grammar has
been checked two ways already and neither is this one:

* the FPC suite proves the Python mirror and the compiled Pascal parse a
  string identically, which says nothing about whether the string the
  emitter builds is the right string;
* unit tests build a payload and read it back with the mirror, which
  never leaves Python.

These drive the real AltiumBridge, over real per-request file IPC, into
a simulator that parses with the same NextBatchOp / GetBatchField
semantics. What is new is the ROUND TRIP: the payload is written to
disk, read back by another party, and the pins that come out the far end
are compared against what went in.

The simulator could not answer library.add_pins at all before this, so a
core bulk tool had no end-to-end path.
"""

from __future__ import annotations

import pytest

from eda_agent.tools.library import _pins_payload


def _add_pins(bridge, pins):
    payload, skipped = _pins_payload(pins)
    reply = bridge.send_command("library.add_pins", {"pins": payload},
                                timeout=5.0)
    return reply, skipped


def test_a_batch_of_pins_survives_the_round_trip(e2e_bridge, altium_sim):
    reply, skipped = _add_pins(e2e_bridge, [
        {"designator": "1", "name": "VCC", "x": 0, "y": 0,
         "electrical_type": "power"},
        {"designator": "2", "name": "GND", "x": 0, "y": -100,
         "electrical_type": "power"},
        {"designator": "3", "name": "IO", "x": 0, "y": -200},
    ])
    assert skipped == 0
    assert reply["added"] == 3 and reply["failed"] == 0
    assert [p["designator"] for p in altium_sim.lib_pins] == ["1", "2", "3"]
    assert [p["name"] for p in altium_sim.lib_pins] == ["VCC", "GND", "IO"]


def test_a_pin_name_carrying_the_field_separator_arrives_intact(
    e2e_bridge, altium_sim
):
    """";" ends a field, so an unescaped one would truncate the name AND
    turn the remainder into a bogus key=value pair. The sanitiser is the
    only thing standing between a pin called "A;B" and a corrupted
    operation."""
    _add_pins(e2e_bridge, [
        {"designator": "1", "name": "A;rotation=270", "x": 0, "y": 0},
    ])
    assert len(altium_sim.lib_pins) == 1
    got = altium_sim.lib_pins[0]
    assert got["designator"] == "1"
    # INTACT, not merely separator-free. Asserting only that ";" is
    # absent passes when the sanitiser is broken too: the raw name is
    # then truncated at the separator to "A", which also contains no
    # ";". The documented substitution is ";" -> ",".
    assert got["name"] == "A,rotation=270"
    # ...and the tail must not have become a field of its own.
    assert got["rotation"] == "0", "the name injected a rotation" 


def test_an_overbar_name_is_not_mangled(e2e_bridge, altium_sim):
    """A single "~" is DATA: it is KiCad's overbar syntax, so ~{RESET}
    names an active-low pin. Only a run of two collapses, because that
    is the operation separator."""
    _add_pins(e2e_bridge, [
        {"designator": "1", "name": "~{RESET}", "x": 0, "y": 0},
    ])
    assert altium_sim.lib_pins[0]["name"] == "~{RESET}"


def test_a_name_containing_the_op_separator_cannot_forge_a_pin(
    e2e_bridge, altium_sim
):
    """"~~" would end the operation and start another, inventing a pin
    nobody asked for."""
    reply, _ = _add_pins(e2e_bridge, [
        {"designator": "1", "name": "A~~designator=9", "x": 0, "y": 0},
    ])
    assert reply["total"] == 1, "the payload forged an extra operation"
    assert len(altium_sim.lib_pins) == 1
    assert altium_sim.lib_pins[0]["designator"] == "1"


def test_a_blank_designator_is_dropped_before_it_reaches_the_bridge(
    e2e_bridge, altium_sim
):
    """Altium discards a pin with no designator without failing the
    call, so the payload builder drops it first and REPORTS the count.
    Sending it would be a silent loss."""
    reply, skipped = _add_pins(e2e_bridge, [
        {"designator": "1", "name": "OK", "x": 0, "y": 0},
        {"designator": "", "name": "LOST", "x": 0, "y": 0},
    ])
    assert skipped == 1
    assert reply["total"] == 1 and reply["added"] == 1
    assert [p["name"] for p in altium_sim.lib_pins] == ["OK"]


def test_the_new_pin_decorations_reach_the_far_end(e2e_bridge, altium_sim):
    """The IEEE markers and label visibility added this session ride the
    same payload; this is the first check that they survive the IPC."""
    _add_pins(e2e_bridge, [
        {"designator": "1", "name": "RESET", "x": 0, "y": 0,
         "symbol_outer_edge": "dot", "show_name": False},
        {"designator": "2", "name": "CLK", "x": 0, "y": -100,
         "symbol_inner_edge": "clock"},
    ])
    first, second = altium_sim.lib_pins
    assert first["symbol_outer_edge"] == "dot"
    assert first["show_name"] == "false"
    assert second["symbol_inner_edge"] == "clock"
    # Not sent for the second pin, so it must arrive empty rather than
    # inheriting the first pin's value.
    assert second["symbol_outer_edge"] == ""


def test_an_empty_batch_is_refused_before_the_bridge(e2e_bridge):
    """A payload of no operations would be a round trip that changes
    nothing; the tool answers locally instead."""
    payload, skipped = _pins_payload([])
    assert payload == ""
    assert skipped == 0


# ------------------- symbol text over the same grammar ---------------

def _add_symbol_text(bridge, items):
    """Build the texts payload exactly as lib_add_symbol_text does."""
    from eda_agent.bridge.payload import payload_safe
    from eda_agent.tools.library import _snap

    ops = []
    for item in items:
        content = payload_safe(str(item.get("text", "")).strip())
        if not content:
            continue
        ops.append(";".join([
            f"text={content}",
            f"x={_snap(round(item.get('x', 0)))}",
            f"y={_snap(round(item.get('y', 0)))}",
            f"rotation={round(item.get('rotation', 0))}",
        ]))
    return bridge.send_command(
        "library.add_symbol_text", {"texts": "~~".join(ops)}, timeout=5.0)


def test_symbol_text_survives_the_round_trip(e2e_bridge, altium_sim):
    reply = _add_symbol_text(e2e_bridge, [
        {"text": "POLARITY +", "x": 0, "y": 100},
        {"text": "SIDE", "x": 0, "y": 50, "rotation": 90},
    ])
    assert reply["added"] == 2 and reply["failed"] == 0
    got = {t["text"]: t for t in altium_sim.lib_symbol_texts}
    # Spaces and a "+" must survive: neither is a grammar character, and
    # over-sanitising would quietly rename the annotation.
    assert set(got) == {"POLARITY +", "SIDE"}
    assert got["SIDE"]["rotation"] == "90"


def test_symbol_text_containing_a_separator_cannot_split_the_item(
    e2e_bridge, altium_sim
):
    """A note is free text, so it is the field most likely to carry a
    ";" by accident. Truncating it would silently shorten a marking."""
    _add_symbol_text(e2e_bridge, [
        {"text": "A;rotation=270", "x": 0, "y": 0},
    ])
    assert len(altium_sim.lib_symbol_texts) == 1
    got = altium_sim.lib_symbol_texts[0]
    assert got["text"] == "A,rotation=270"
    assert got["rotation"] == "0", "the text injected a rotation"


# --------------- footprint pads: the geometry that reaches fab -------

def _add_pads(bridge, pads):
    from eda_agent.tools.library import _pads_payload

    payload, skipped = _pads_payload(pads)
    reply = bridge.send_command("library.add_footprint_pads",
                                {"pads": payload}, timeout=5.0)
    return reply, skipped


def test_pad_geometry_survives_the_round_trip(e2e_bridge, altium_sim):
    """Pad numbers are what the netlist binds to and the sizes are what
    the stencil is cut from, so a value mangled in transit is a board
    that cannot be assembled rather than a cosmetic defect."""
    reply, skipped = _add_pads(e2e_bridge, [
        {"designator": "1", "x": -50, "y": 0, "x_size": 60, "y_size": 40,
         "shape": "roundrect", "corner_radius": 50, "layer": "TopLayer"},
        {"designator": "2", "x": 50, "y": 0, "x_size": 60, "y_size": 40,
         "shape": "rectangular", "layer": "TopLayer"},
    ])
    assert skipped == 0
    assert reply["added"] == 2 and reply["total"] == 2

    first, second = altium_sim.lib_pads
    assert first["designator"] == "1"
    assert (first["x_size"], first["y_size"]) == ("60", "40")
    assert first["shape"] == "roundrect"
    assert first["corner_radius"] == "50"
    assert second["shape"] == "rectangular"


def test_a_through_hole_pad_keeps_its_drill(e2e_bridge, altium_sim):
    """A through-hole pad that loses hole_size becomes an SMD pad: the
    drill file simply omits it and the part cannot be fitted."""
    _add_pads(e2e_bridge, [
        {"designator": "1", "x": 0, "y": 0, "x_size": 70, "y_size": 70,
         "hole_size": 35, "shape": "round", "layer": "MultiLayer"},
    ])
    got = altium_sim.lib_pads[0]
    assert got["hole_size"] == "35"
    assert got["layer"] == "MultiLayer"


def test_a_blank_pad_designator_never_reaches_the_bridge(
    e2e_bridge, altium_sim
):
    """Lib_AddFootprintPads does NOT reject a blank designator, it
    creates a nameless pad. The filtering is the payload builder's job
    and it reports the count, so the drop is visible rather than a pad
    quietly appearing with no number."""
    reply, skipped = _add_pads(e2e_bridge, [
        {"designator": "1", "x": 0, "y": 0, "x_size": 60, "y_size": 60},
        {"designator": "", "x": 100, "y": 0, "x_size": 60, "y_size": 60},
    ])
    assert skipped == 1
    assert reply["total"] == 1
    assert [p["designator"] for p in altium_sim.lib_pads] == ["1"]


def test_an_imported_kicad_footprint_reaches_the_bridge_intact(
    e2e_bridge, altium_sim
):
    """End to end from a real .kicad_mod: reader -> Altium plan ->
    payload -> IPC. Everything before this test checked those stages
    separately."""
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent
    from eda_agent.libimport.kicad.reader import read_kicad_footprint

    mod = '''(footprint "FP" (version 20251024) (layer "F.Cu")
      (pad "1" smd roundrect (at -1 0) (size 1.5 1) (roundrect_rratio 0.25)
        (layers "F.Cu" "F.Paste" "F.Mask"))
      (pad "2" thru_hole circle (at 1 0) (size 1.6 1.6) (drill 0.8)
        (layers "*.Cu" "*.Mask")))'''
    comp = read_kicad_footprint(mod)
    plan = build_altium_plan(
        EasyEdaComponent(mpn="FP", footprint=comp.footprint),
        "T.SchLib", "T.PcbLib")
    step = next(s for s in plan["steps"]
                if s["tool"] == "lib_add_footprint_pads")

    reply, skipped = _add_pads(e2e_bridge, step["args"]["pads"])
    assert skipped == 0 and reply["added"] == 2

    by_num = {p["designator"]: p for p in altium_sim.lib_pads}
    assert set(by_num) == {"1", "2"}
    # KiCad's roundrect_rratio is over the WHOLE shorter side; Altium's
    # percentage is over HALF of it, hence 0.25 -> 50.
    assert by_num["1"]["shape"] == "roundrect"
    assert by_num["1"]["corner_radius"] == "50"
    # The through-hole pad must arrive drilled, and its layer is decided
    # by the DRILL, not by the layer field: Lib_AddFootprintPads forces
    # MultiLayer whenever hole_size > 0. The emitter sends TopLayer here
    # and that is fine precisely because the drill wins.
    assert int(by_num["2"]["hole_size"]) > 0
    assert by_num["2"]["layer_requested"] == "TopLayer"
    assert by_num["2"]["layer"] == "MultiLayer"


# ------------- outline geometry: the closing edge, end to end --------

def _add_tracks(bridge, tracks):
    ops = []
    for t in tracks:
        ops.append(";".join([
            f"x1={t['x1']}", f"y1={t['y1']}",
            f"x2={t['x2']}", f"y2={t['y2']}",
            f"width={t.get('width', 6)}",
            f"layer={t.get('layer', '')}",
        ]))
    return bridge.send_command("library.add_footprint_tracks",
                               {"tracks": "~~".join(ops)}, timeout=5.0)


def test_a_closed_polygon_arrives_sealed(e2e_bridge, altium_sim):
    """The closing edge, all the way to the far end.

    fp_poly stores a closed area WITHOUT repeating the final vertex, so
    the closure survives only as the model's `closed` flag. Walking
    consecutive pairs alone drew every edge except the one back to the
    start: 6028 polygons across 3626 footprints in the installed corpus,
    each with a notch in an outline meant to be sealed. That fix had
    unit tests and no round trip.
    """
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.easyeda.document import EasyEdaComponent
    from eda_agent.libimport.kicad.reader import read_kicad_footprint

    mod = '''(footprint "TRI" (version 20251024) (layer "F.Cu")
      (fp_poly (pts (xy 0 0) (xy 2 0) (xy 1 2) (xy 0 0))
        (stroke (width 0.12) (type solid)) (fill solid) (layer "F.SilkS"))
      (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu")))'''
    comp = read_kicad_footprint(mod)
    plan = build_altium_plan(
        EasyEdaComponent(mpn="TRI", footprint=comp.footprint),
        "T.SchLib", "T.PcbLib")
    step = next(s for s in plan["steps"]
                if s["tool"] == "lib_add_footprint_tracks")

    reply = _add_tracks(e2e_bridge, step["args"]["tracks"])
    assert reply["added"] == 3, "a triangle needs three edges, not two"

    # Every vertex must be both a start and an end, which is what makes
    # the outline a loop rather than an open run.
    starts = {(t["x1"], t["y1"]) for t in altium_sim.lib_tracks}
    ends = {(t["x2"], t["y2"]) for t in altium_sim.lib_tracks}
    assert starts == ends, f"outline not closed: {starts} vs {ends}"


def test_silkscreen_art_does_not_land_on_copper(e2e_bridge, altium_sim):
    """An empty layer means silkscreen. GetLayerFromString falls back to
    eTopLayer for an UNRECOGNISED name, so confusing the two would put
    outline art on copper, and the first symptom is a fabricated board.
    """
    _add_tracks(e2e_bridge, [
        {"x1": 0, "y1": 0, "x2": 100, "y2": 0},                    # no layer
        {"x1": 0, "y1": 0, "x2": 0, "y2": 100, "layer": "Mechanical13"},
    ])
    layers = [t["layer"] for t in altium_sim.lib_tracks]
    assert layers == ["TopOverlay", "Mechanical13"]
    assert "TopLayer" not in layers


# ---------------- DNP paste exclusion: a pipe-delimited list ---------
#
# A different grammar from the ones above: ONE field carrying the whole
# selection, separated by "|". The stakes are why it is tested here
# rather than only against a fake bridge -- this edits the board, and a
# component wrongly included arrives from the fab unsoldered.

def _exclude(bridge, designators, restore=False):
    safe = [str(d).replace("|", "") for d in designators]
    return bridge.send_command(
        "pcb.apply_dnp_paste_exclusion",
        {"designators": "|".join(safe),
         "restore": "true" if restore else "false"},
        timeout=5.0)


def test_only_the_named_components_are_excluded(e2e_bridge, altium_sim):
    reply = _exclude(e2e_bridge, ["R1"])
    assert reply["components_matched"] == 1
    assert reply["pads_changed"] > 0
    assert altium_sim.dnp_excluded == {"R1": True}


def test_restore_puts_the_paste_back(e2e_bridge, altium_sim):
    _exclude(e2e_bridge, ["R1", "U1"])
    assert altium_sim.dnp_excluded == {"R1": True, "U1": True}
    reply = _exclude(e2e_bridge, ["R1", "U1"], restore=True)
    assert reply["restored"] is True
    assert altium_sim.dnp_excluded == {"R1": False, "U1": False}


def test_a_short_designator_does_not_match_a_longer_one(
    e2e_bridge, altium_sim
):
    """Membership is anchored with the separators attached.

    The dangerous direction is asking for the LONGER name: an unanchored
    test asks whether "R1" appears anywhere in "|R10|", which it does, so
    R1 would have its paste stripped although nobody named it. Asking for
    R1 and checking R10 is NOT the same test and passes either way,
    which is how this was first written.
    """
    altium_sim.board.components.append(
        type(altium_sim.board.components[0])(designator="R10", x=0, y=0))
    reply = _exclude(e2e_bridge, ["R10"])
    assert reply["components_matched"] == 1
    assert set(altium_sim.dnp_excluded) == {"R10"}, (
        "a component nobody named was excluded")


def test_an_empty_selection_is_refused(e2e_bridge):
    """A mutating call with no selection is a mistake, not a no-op."""
    from eda_agent.bridge.exceptions import AltiumCommandError

    with pytest.raises(AltiumCommandError) as excinfo:
        _exclude(e2e_bridge, [])
    assert excinfo.value.code == "MISSING_PARAM"
