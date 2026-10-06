# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Run the KiCad->Altium plan through the REAL tool functions.

The sibling of test_easyeda_altium_plan_executes, on the shared harness
from conftest so the two cannot drift into checking different things.

Worth having separately even though both importers share one emitter:
they do NOT share a reader, and the plan is only as good as the neutral
model handed to it. A reader that produced blank pad designators or
zero-size pads would still build a plan that looks structurally fine and
then quietly lose geometry at the tool layer, which is exactly the
failure the EasyEDA side hit with drill holes.

Fixtures are inline, not read from an installed KiCad, so this runs
anywhere.
"""

from __future__ import annotations

import asyncio
import json

import pytest

_SYM = '''(kicad_symbol_lib (version 20251024) (generator test)
  (symbol "PLANPART"
    (property "Reference" "U" (at 0 0 0))
    (property "Footprint" "PKG:PLANFP" (at 0 0 0))
    (symbol "PLANPART_0_1"
      (rectangle (start -7.62 5.08) (end 7.62 -5.08)
        (stroke (width 0) (type default)) (fill (type background))))
    (symbol "PLANPART_1_1"
      (pin input line (at -10.16 2.54 0) (length 2.54)
        (name "IN") (number "1"))
      (pin power_in line (at -10.16 0 0) (length 2.54)
        (name "VCC") (number "2"))
      (pin output line (at 10.16 2.54 180) (length 2.54)
        (name "OUT") (number "3"))
      (pin power_in line (at 10.16 0 180) (length 2.54)
        (name "GND") (number "4")))))'''

_MOD = '''(footprint "PLANFP" (version 20251024) (layer "F.Cu")
  (pad "1" smd rect (at -1.905 0.95) (size 1.2 0.6)
    (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "2" smd rect (at -1.905 -0.95) (size 1.2 0.6)
    (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "3" thru_hole circle (at 1.905 0.95) (size 1.5 1.5)
    (drill 0.8) (layers "*.Cu" "*.Mask"))
  (pad "4" thru_hole circle (at 1.905 -0.95) (size 1.5 1.5)
    (drill 0.8) (layers "*.Cu" "*.Mask"))
  (fp_line (start -2.5 1.5) (end 2.5 1.5)
    (stroke (width 0.12) (type solid)) (layer "F.SilkS"))
  (fp_circle (center -2.5 1.5) (end -2.4 1.5)
    (stroke (width 0.12) (type solid)) (layer "F.SilkS"))
  (fp_text user "M" (at 0 2.5) (layer "F.SilkS")
    (effects (font (size 1 1) (thickness 0.15)))))'''


@pytest.fixture
def plan(tmp_path):
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.kicad.reader import read_kicad_files

    sym = tmp_path / "L.kicad_sym"
    mod = tmp_path / "F.kicad_mod"
    sym.write_text(_SYM, encoding="utf-8")
    mod.write_text(_MOD, encoding="utf-8")

    comp = read_kicad_files(symbol_path=str(sym), footprint_path=str(mod),
                            symbol_name="PLANPART")
    return build_altium_plan(comp, "T.SchLib", "T.PcbLib")


def test_every_plan_step_runs_without_raising(altium_tool_harness, plan):
    """The whole plan, executed in order, through the real tools."""
    fns, bridge = altium_tool_harness
    assert len(plan["steps"]) >= 8, "plan too small to be a real check"

    failures: list[str] = []
    for i, step in enumerate(plan["steps"], 1):
        fn = fns.get(step["tool"])
        if fn is None:
            failures.append(f"step {i}: {step['tool']} not registered")
            continue
        try:
            asyncio.run(fn(**step["args"]))
        except Exception as exc:  # noqa: BLE001 - collect, do not abort
            failures.append(
                f"step {i}: {step['tool']} raised "
                f"{type(exc).__name__}: {exc}")
    assert not failures, "\n".join(failures)

    assert len(bridge.calls) >= len(plan["steps"]), (
        f"{len(plan['steps'])} steps produced only {len(bridge.calls)} "
        f"bridge commands; a step is silently doing nothing")


def test_no_step_silently_discards_part_of_its_payload(
        altium_tool_harness, plan):
    """"It did not raise" is not the same as "it did the work".

    The bulk tools filter their input and report a count rather than
    failing, so a plan can execute cleanly while losing pads. This is
    the check that caught the EasyEDA drill-hole bug, where holes were
    emitted with blank designators and dropped without any error.
    """
    fns, _ = altium_tool_harness

    losses = []
    for i, step in enumerate(plan["steps"], 1):
        fn = fns.get(step["tool"])
        if fn is None:
            continue
        result = asyncio.run(fn(**step["args"]))
        if not isinstance(result, dict):
            continue
        for key in ("skipped_invalid", "skipped", "failed", "dropped"):
            count = result.get(key)
            if isinstance(count, int) and count:
                losses.append(f"step {i} {step['tool']}: {key}={count}")
    assert not losses, (
        "steps executed without error but discarded part of their "
        "payload:\n  " + "\n  ".join(losses))


def test_every_pin_and_pad_reaches_the_bridge(altium_tool_harness, plan):
    """Geometry has to survive the flatten into a payload string.

    Both bulk tools take a list of dicts and flatten it. That transform
    is where values get dropped without an error, and the result is a
    part with pins in the wrong place that looks like it converted.
    """
    fns, bridge = altium_tool_harness

    for tool, key, field in (("lib_add_pins", "pins", "designator"),
                             ("lib_add_footprint_pads", "pads",
                              "designator")):
        step = next(s for s in plan["steps"] if s["tool"] == tool)
        asyncio.run(fns[tool](**step["args"]))
        _, params = next(c for c in bridge.calls if tool[4:] in c[0])
        blob = json.dumps(params)
        for item in step["args"][key]:
            assert item[field] in blob, (
                f"{tool}: {key[:-1]} {item[field]} did not reach the bridge")


def test_through_hole_pads_keep_their_drill(altium_tool_harness, plan):
    """A drill lost in the payload gives an unsolderable board.

    Asserted on the plan rather than the reader because this is the leg
    where the EasyEDA importer previously lost holes: the reader had
    them and the plan did not.
    """
    step = next(s for s in plan["steps"]
                if s["tool"] == "lib_add_footprint_pads")
    drilled = [p for p in step["args"]["pads"]
               if float(p.get("hole_size", 0) or 0) > 0]
    assert len(drilled) == 2, (
        f"fixture has 2 through-hole pads, plan carries {len(drilled)}")
    # And every one of them must still name a pad, since a blank
    # designator is silently discarded by the bulk tool.
    assert all(p["designator"] for p in drilled)


_MULTI_SYM = '''(kicad_symbol_lib (version 20251024) (generator test)
  (symbol "DUALPART"
    (property "Reference" "U" (at 0 0 0))
    (symbol "DUALPART_1_1"
      (pin output line (at 5.08 0 180) (length 2.54)
        (name "OUT1") (number "1")))
    (symbol "DUALPART_2_1"
      (pin output line (at 5.08 0 180) (length 2.54)
        (name "OUT2") (number "7")))
    (symbol "DUALPART_3_1"
      (pin power_in line (at 0 5.08 270) (length 2.54)
        (hide yes) (name "V+") (number "8")))))'''


@pytest.fixture
def multi_part_plan(tmp_path):
    from eda_agent.libimport.easyeda.altium import build_altium_plan
    from eda_agent.libimport.kicad.reader import read_kicad_files

    sym = tmp_path / "M.kicad_sym"
    sym.write_text(_MULTI_SYM, encoding="utf-8")
    comp = read_kicad_files(symbol_path=str(sym), symbol_name="DUALPART")
    return build_altium_plan(comp, "T.SchLib", "T.PcbLib")


def test_multi_part_arguments_survive_the_tool_layer(
        altium_tool_harness, multi_part_plan):
    """part_count and owner_part_id must reach the BRIDGE, not just the plan.

    Both are newer arguments on the library tools, and a plan can name
    them perfectly while the tool body drops them on the way into the
    payload string -- which would silently collapse a multi-part
    component into a flat one with every pin on sub-part 1.
    """
    fns, bridge = altium_tool_harness

    for step in multi_part_plan["steps"]:
        fn = fns.get(step["tool"])
        if fn is not None:
            asyncio.run(fn(**step["args"]))

    _, create = next(c for c in bridge.calls if "create_symbol" in c[0])
    assert str(create.get("part_count")) == "3"

    _, pins = next(c for c in bridge.calls if "add_pins" in c[0])
    blob = json.dumps(pins)
    for designator, owner in (("1", 1), ("7", 2), ("8", 3)):
        assert f"designator={designator};" in blob
        assert f"owner_part_id={owner}" in blob
    # The hidden supply pin must arrive hidden.
    assert "hidden=true" in blob.lower()
