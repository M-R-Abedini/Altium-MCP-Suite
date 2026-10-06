# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Exercise the EasyEDA command vocabulary against a live editor.

Everything about this backend is verified except the one thing that
matters most: whether the editor actually answers these commands, and
in the shape the Python side reads. That cannot be established without
EasyEDA running, so it is established here rather than assumed.

READ ONLY. Nothing in this script changes a design. The destructive
commands exist and are guarded, and a smoke test is the wrong place to
find out whether a guard works on someone's open board.

The output is the point. A command that returns an empty list is NOT
reported as passing: on a board with parts, an empty component list
means the response shape was misread, which is the failure this whole
exercise is looking for. Empty results are called out separately so a
wrong shape cannot hide as a quiet success.

Run with EasyEDA Pro open, a board loaded, and the extension installed:

    python scripts/easyeda_smoke.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eda_agent.bridge.easyeda_bridge import (  # noqa: E402
    EasyEdaBridge,
    EasyEdaNotReachableError,
)

#: (command, params, what a healthy answer looks like). The third item
#: names the key whose emptiness would mean a misread shape rather than
#: an empty design.
PROBES: list[tuple[str, dict, str]] = [
    ("system.ping", {}, "pong"),
    ("proj.info", {}, "project"),
    ("pcb.list_boards", {}, "boards"),
    ("pcb.components", {}, "components"),
    ("pcb.nets", {}, "nets"),
    ("pcb.layers", {}, "layers"),
    ("pcb.pads", {}, "pads"),
    ("pcb.vias", {}, "vias"),
    ("pcb.lines", {}, "lines"),
    ("pcb.arcs", {}, "arcs"),
    ("pcb.regions", {}, "regions"),
    ("pcb.attributes", {}, "attributes"),
    ("pcb.dimensions", {}, "dimensions"),
    ("pcb.net_classes", {}, "net_classes"),
    ("pcb.differential_pairs", {}, "differential_pairs"),
    ("pcb.selection", {}, "selected"),
    ("sch.list_schematics", {}, "schematics"),
    ("sch.list_pages", {}, "pages"),
    ("sch.components", {}, "components"),
    ("sch.pins", {}, "pins"),
    ("sch.wires", {}, "wires"),
    ("sch.attributes", {}, "attributes"),
    ("sch.netlist", {}, "netlist"),
    ("sch.assembly_variants", {}, "variants"),
    ("sys.paths", {}, "projects"),
    ("lib.list_libraries", {}, "libraries"),
    ("pcb.strings", {}, "strings"),
    ("pcb.fills", {}, "fills"),
    ("pcb.images", {}, "images"),
    ("pcb.embedded_objects", {}, "objects"),
    ("pcb.pours", {}, "pours"),
    ("pcb.poured", {}, "poured"),
    ("pcb.net_rules", {}, "rules"),
    ("pcb.net_lengths", {}, "lengths"),
    ("pcb.rule_configurations", {}, "configurations"),
    ("pcb.length_match_groups", {}, "groups"),
    ("sch.buses", {}, "buses"),
    ("sch.selection", {}, "primitives"),
    ("dmt.team", {}, "team"),
    ("dmt.boards", {}, "boards"),
    ("dmt.panels", {}, "panels"),
    ("dmt.current_panel", {}, "open"),
    ("proj.list", {}, "project_uuids"),
    ("sys.environment", {}, "version"),
    ("sys.workspaces", {}, "workspaces"),
    # The snapshot is the one that feeds every EDA-agnostic check, so a
    # wrong shape here is the most expensive of all.
    ("design.snapshot", {}, "parts"),
]

#: Run last: they are slow, and a failure here is less informative than
#: a failure in the reads above.
SLOW_PROBES: list[tuple[str, dict, str]] = [
    ("design.run_drc", {}, "violations"),
    ("design.run_erc", {}, "violations"),
    # Every fabrication export. Still read-only, and each generates a
    # whole file, so they run last and separately: a slow export failing
    # says nothing about whether the design reads correctly.
    ("export.gerber", {}, "file"),
    ("export.bom", {}, "file"),
    ("export.sch_bom", {}, "file"),
    ("export.netlist", {}, "file"),
    ("export.schematic_netlist", {}, "file"),
    ("export.simulation_netlist", {}, "file"),
    ("export.dxf", {}, "file"),
    ("export.pdf", {}, "file"),
    ("export.pick_and_place", {}, "file"),
    ("export.test_points", {}, "file"),
    ("export.flying_probe", {}, "file"),
    ("export.dsn", {}, "file"),
    ("export.pads", {}, "file"),
    ("export.pcb_info", {}, "file"),
    ("export.ipc2581", {}, "file"),
    ("export.ipcd356", {}, "file"),
    ("export.altium", {}, "file"),
    ("export.model_3d", {}, "file"),
    ("export.schematic_document", {}, "file"),
]

#: Commands for which an EMPTY answer is an ordinary state of a real
#: board rather than a misread shape. Nothing is selected most of the
#: time; most boards carry no dimensions, images or panels; a board
#: with no equal-length groups is a board without length matching. An
#: empty answer from anything ELSE stays suspicious, because that is
#: exactly how the net_lengths field-name bug presented: a loaded board
#: reporting a clean empty list.
#:
#: Still NOT verified: an empty list proves the command answered, not
#: that its items have the shape the tools read.
MAY_BE_EMPTY: frozenset = frozenset({
    "pcb.selection", "sch.selection", "pcb.dimensions", "pcb.regions",
    "pcb.images", "pcb.embedded_objects", "pcb.strings",
    "pcb.length_match_groups", "sch.buses", "dmt.panels",
    "pcb.differential_pairs", "pcb.poured",
})

#: Read-only commands this script deliberately does NOT probe, and why.
#:
#: Kept explicit so the coverage guard has something to check against.
#: Without it, a command silently dropping out of the probe list is
#: indistinguishable from one that was never meant to be in it.
NOT_PROBED: dict[str, str] = {
    "editor.close_document": "needs a document uuid, and probing it would close whatever the person is looking at: a read-only classification that is still rude to exercise unasked",
    "pcb.net_length": "needs a net name, and there is no net every board has",
    "pcb.primitives_in_region": "needs a rectangle, and any guess is arbitrary",
    "system.capabilities": "called directly before the probe loop, because its answer decides whether the rest mean anything",
    "pcb.bbox": "needs primitive ids; probed with none it can only be refused",
    "pcb.bboxes": "needs primitive ids, the same as pcb.bbox",
    "dmt.folders": "needs the team uuid, read first from dmt.team",
    "proj.get": "needs a project uuid",
    "dmt.panel_info": "needs a panel uuid, read first from dmt.panels, and most installs have no panel at all",
    "lib.classifications": "needs a library uuid, and there is no library every install has",
    "lib.get_device": "needs a device uuid and its library",
    "lib.devices_by_lcsc": "needs LCSC part numbers",
    "lib.search_devices": "needs a query, and reaches the library service",
    "lib.search_symbols": "needs a query, and reaches the library service",
    "lib.search_footprints": "needs a query, and reaches the library service",
    "lib.search_3d_models": "needs a query, and reaches the library service",
    "lib.symbol_image": "needs a symbol uuid, and returns an image",
    "lib.footprint_image": "needs a footprint uuid, and returns an image",
    "editor.render_image": "returns an image rather than a shape to check",
    "sys.document_source": "returns the whole open document; large, and read by the checkpoint tool rather than probed",
}


#: How many items of a list to look at when reporting its keys. The
#: whole list would be no more accurate: a list long enough to disagree
#: with itself does so within the first few dozen.
_SHAPE_SAMPLE = 24


def _shape_of(items) -> str:
    """The keys on a list of objects, split by whether ALL of them have it.

    This is the reason the smoke run exists. Nothing offline can
    establish what EasyEDA calls the fields on a component or a pad, and
    a tool written against a guessed name does not fail loudly: it reads
    nothing and reports a clean empty result.

    A key on every item can be read directly. A key on only some has to
    be read defensively, and reporting the union flat hides which is
    which.
    """
    sample = [item for item in items[:_SHAPE_SAMPLE]
              if isinstance(item, dict)]
    if not sample:
        return ""

    everywhere = set(sample[0])
    anywhere = set()
    for item in sample:
        everywhere &= set(item)
        anywhere |= set(item)

    parts = [f"always: {', '.join(sorted(everywhere))}"] if everywhere else []
    sometimes = anywhere - everywhere
    if sometimes:
        parts.append(f"sometimes: {', '.join(sorted(sometimes))}")
    return "; ".join(parts)


def _summarise(value) -> str:
    if isinstance(value, list):
        shape = _shape_of(value)
        return f"list[{len(value)}] {shape}" if shape else f"list[{len(value)}]"
    if isinstance(value, dict):
        return f"dict({', '.join(list(value)[:4])})"
    text = str(value)
    return text if len(text) < 48 else text[:45] + "..."


#: Commands whose editor-side promise NEVER SETTLES. Measured
#: reproducibly: the extension awaits an EasyEDA API call
#: that neither resolves nor rejects, so the dispatcher's own error
#: handling cannot help and the caller waits out its whole timeout.
#:
#: They are still probed, because "does it still hang?" is the question
#: a later editor version can answer differently. They just get a short
#: clock: five of them at 90 seconds is seven and a half minutes of
#: someone holding a tab open to learn nothing new.
KNOWN_HANGING = frozenset({
    "pcb.attributes",
    "sch.attributes",
    "sys.paths",
    "pcb.strings",
    "pcb.poured",
    # Measured on a live schematic: no reply in 30 seconds.
    #
    # WHY they hang is not established. They were first written up here
    # as hanging on the right document with the API present, which was
    # an inference from a hand-written list of what a schematic runtime
    # offers, not a measurement. The run that produced these timeouts
    # DID call system.capabilities successfully and the harness recorded
    # only its key names, discarding the answer, so the one artefact
    # that could settle it was thrown away.
    #
    # These reach for sch_SelectControl and sch_ManufactureData. If
    # those are absent the 0.5.9 dispatcher guard now refuses instantly
    # and these entries become unnecessary; if present, the hang is
    # real. Listed until a run with the capabilities reply settles it.
    "sch.selection",
    "sch.assembly_variants",
})

#: Long enough that a slow-but-working command still answers, short
#: enough that a confirmed hang costs seconds rather than minutes.
HANG_RECHECK_TIMEOUT = 10.0


def local_build_id() -> "str | None":
    """The build id this tree's main.js would stamp, or None."""
    import pathlib
    import sys

    root = pathlib.Path(__file__).resolve().parent.parent
    source = root / "extensions" / "easyeda" / "main.js"
    if not source.exists():
        return None
    sys.path.insert(0, str(root / "extensions" / "easyeda"))
    try:
        from build import build_id            # type: ignore[import]

        return build_id(source.read_text(encoding="utf-8"))
    except Exception:                          # noqa: BLE001
        return None


def report_stale_build(reported: "str | None") -> bool:
    """Say loudly when the editor is running a DIFFERENT build.

    build_id() has always existed and main.js has always reported it,
    with a comment promising the Python side compares them. It did
    not. The cost of that gap was concrete: a whole session read as
    "the export fix is broken" when the editor was simply running a
    build from before the fix, and the only clue was one REFUSED line
    for a command the older build had never heard of.

    An extension that is installed, enabled and months old looks
    identical to a current one in EasyEDA's Extensions Manager, so
    this is the only cheap way to know.
    """
    local = local_build_id()
    if not reported or not local:
        return False
    if reported == local:
        print(f"  extension build {reported} matches this tree.\n")
        return False
    print()
    print("  " + "!" * 66)
    print(f"  STALE EXTENSION: the editor is running build {reported!r},")
    print(f"  this tree builds {local!r}. Everything below tests the OLD")
    print("  code, so a fix made since that build will read as broken.")
    print("  Rebuild with `python extensions/easyeda/build.py`, then")
    print("  re-import the .eext in Settings > Extensions. A re-import")
    print("  of the SAME version number is a silent no-op, so bump the")
    print("  version in extension.json first.")
    print("  " + "!" * 66)
    print()
    return True


def filter_unmeasured(probes, known_shapes) -> list:
    """The probes whose command has no recorded shape yet.

    A full run took thirteen minutes, most of it the export family
    burning a 90-second timeout each. A person's connection window is
    the scarce resource, not the machine's, so a run can be narrowed to
    what is still unknown.
    """
    known = set(known_shapes or {})
    return [p for p in probes if p[0] not in known]


def _populated_field_count(item) -> int:
    """How many of an item's fields carry something.

    Used to pick a REPRESENTATIVE sample rather than the first one. An
    optional field is null on the item that does not use it, so the
    first pad being SMD is why a through-hole pad's `hole` shape went
    unmeasured through two harvests.
    """
    if not isinstance(item, dict):
        return 0
    return sum(1 for value in item.values()
               if value not in (None, "", [], {}, False))


def run(bridge: EasyEdaBridge, probes, outcomes: dict,
        shapes: "dict | None" = None,
        samples: "dict | None" = None,
        timeout: float = 90.0) -> tuple[int, int, int]:
    worked = empty = failed = 0
    for command, params, key in probes:
        try:
            reply = bridge.send_editor_command(
                command, params, timeout=timeout)
        except EasyEdaNotReachableError as exc:
            print(f"  UNREACHABLE {command}: {exc}")
            outcomes[command] = False
            failed += 1
            continue

        if "error" in reply:
            print(f"  REFUSED     {command}: {reply['error']}")
            outcomes[command] = False
            failed += 1
            continue

        result = reply.get("result")
        if not isinstance(result, dict):
            print(f"  ODD SHAPE   {command}: result is "
                  f"{type(result).__name__}, expected an object")
            outcomes[command] = False
            failed += 1
            continue

        if key not in result:
            print(f"  WRONG KEY   {command}: no {key!r}; got "
                  f"{sorted(result)[:5]}")
            outcomes[command] = False
            failed += 1
            continue

        value = result[key]
        # Empty is reported separately, never as a pass. On a real board
        # an empty component list means the shape was misread. But an
        # empty SELECTION is Tuesday, and twenty-two suspicious empties
        # in one run buried the single real one among them.
        if value in ([], {}, None, ""):
            if command in MAY_BE_EMPTY:
                print(f"  empty (ok)  {command}: {key} is empty, which "
                      f"is an ordinary state for this command. Not "
                      f"verified: nothing about the item shape was "
                      f"measurable.")
            else:
                print(f"  EMPTY       {command}: {key} is empty. On a "
                      f"loaded board this usually means the response "
                      f"shape differs from what the extension expects.")
            outcomes[command] = False
            empty += 1
            continue

        summary = _summarise(value)
        print(f"  ok          {command}: {key} = {summary}")
        outcomes[command] = True
        if shapes is not None:
            # Kept even when it is only a count. A bare "list[12]" is
            # itself a finding: it means the items are plain values
            # rather than objects, and no audit can be written against
            # fields that are not there.
            shapes[command] = summary
        if samples is not None:
            # One truncated example item. The shapes above answer WHICH
            # keys exist; the audits blocked after the first harvest
            # were blocked on what the VALUES look like (is a rule a
            # number or an object, is tenting a sign or a flag), and
            # only an example answers that. Machine-local, like the
            # rest of the record.
            example = value[0] if isinstance(value, list) else value
            # A KEYED collection is a list wearing a different hat.
            # sch.netlist answers {uid: {props: {...}, ...}}, and
            # storing the whole dict truncated meant one component's
            # parameters filled the entire budget, so whatever else an
            # entry carries (pins, above all) was never seen. Sample
            # ONE entry, exactly as a list is sampled.
            if (isinstance(example, dict) and example
                    and all(isinstance(v, dict) for v in example.values())):
                example = next(iter(example.values()))
            try:
                text = json.dumps(example, ensure_ascii=False)
            except (TypeError, ValueError):
                text = str(example)
            # 400 characters cut the FIRST harvest's component sample
            # off before otherProperty and pads, which are exactly the
            # nested fields the remaining audits are blocked on, and a
            # sample that stops before the interesting field measures
            # nothing. Long enough to reach them, still bounded.
            samples[command] = text[:2000]
            # Nested objects and lists are where the shape actually
            # lives: a component's `footprint` is an object and its
            # `pads` a list, and knowing only that they exist is what
            # let design.snapshot read a `footprintName` that was never
            # there. One level down, keys only.
            if isinstance(example, dict):
                nested = {}
                for field, inner in example.items():
                    if isinstance(inner, dict):
                        nested[field] = sorted(inner)
                    elif (isinstance(inner, list) and inner
                            and isinstance(inner[0], dict)):
                        nested[field] = [f"list[{len(inner)}] of",
                                         *sorted(inner[0])]
                if nested:
                    samples[command + " (nested)"] = json.dumps(
                        nested, ensure_ascii=False)[:2000]

            # The first item is not a representative one. The first pad
            # on the live board was SMD, so its `hole` was null and the
            # shape a THROUGH-HOLE pad puts there stayed unmeasured;
            # same for a component carrying no otherProperty and a via
            # with no distinctive mask expansion. Recording the item
            # with the most populated fields answers those in the SAME
            # run rather than in a later one.
            if isinstance(value, list) and len(value) > 1:
                richest = max(value, key=_populated_field_count)
                if (_populated_field_count(richest)
                        > _populated_field_count(example)):
                    try:
                        rich_text = json.dumps(richest, ensure_ascii=False)
                    except (TypeError, ValueError):
                        rich_text = str(richest)
                    samples[command + " (fullest)"] = rich_text[:2000]
        worked += 1
    return worked, empty, failed


def main() -> int:
    bridge = EasyEdaBridge()
    status = bridge.start()
    print(f"Listening on {status['host']}:{status['port']}")
    print("Open EasyEDA Pro with the eda-agent extension installed.")

    # Sixty seconds is enough for a scripted probe and far too short for
    # a person: connecting means switching to another application,
    # opening a document and picking a menu item. EDA_SMOKE_WAIT
    # overrides it.
    try:
        wait_seconds = max(5, int(os.environ.get("EDA_SMOKE_WAIT", "60")))
    except ValueError:
        wait_seconds = 60
    deadline = time.time() + wait_seconds
    while time.time() < deadline and not bridge.connected:
        time.sleep(0.5)

    if not bridge.connected:
        print(f"\nNo editor connected within {wait_seconds}s.")
        print("Install the extension: build it with "
              "`python extensions/easyeda/build.py`, then in EasyEDA Pro "
              "use Settings > Extensions and point it at "
              "extensions/easyeda/.")
        bridge.stop()
        return 1

    # EasyEDA loads its API PER DOCUMENT TYPE. Its own pro-api manifest
    # declares services for default / sch / symbol / pcb / panel, and on
    # the start page only the reduced "default" surface exists: the
    # socket works, dmt_Pcb is half there, and every pcb_* and sch_*
    # class is undefined.
    #
    # Run the probes anyway and 64 of 65 come back "Cannot read
    # properties of undefined", which reads as sixty-four bugs in this
    # project. It happened, and it cost hours. So ask first.
    try:
        ping = (bridge.send_editor_command("system.ping", timeout=10.0)
                .get("result") or {})
        kind = str(ping.get("document") or "unknown")
    except Exception:                            # noqa: BLE001
        ping, kind = {}, "unknown"

    report_stale_build(ping.get("build"))

    if kind not in ("pcb", "schematic"):
        print(f"\nConnected, but the active document is {kind!r}.")
        print("EasyEDA only injects the pcb_* and sch_* API into a "
              "design document, so every probe would fail with "
              "'undefined' and none of those failures would be real.")
        # Wait rather than exit. Re-importing an extension leaves the
        # editor on its settings page, so the first connect after an
        # import lands here nearly every time, and quitting costs a
        # full restart plus another connect for something that is one
        # click to fix. EDA_SMOKE_TAB_WAIT=0 restores the old
        # exit-immediately behaviour for a scripted run.
        try:
            patience = float(os.environ.get("EDA_SMOKE_TAB_WAIT", "600"))
        except ValueError:
            patience = 600.0
        if patience > 0:
            print(f"Click onto a PCB or schematic tab; waiting up to "
                  f"{int(patience)}s for one.", flush=True)
            until = time.time() + patience
            while time.time() < until and kind not in ("pcb", "schematic"):
                time.sleep(3.0)
                try:
                    ping = (bridge.send_editor_command("system.ping",
                                                       timeout=10.0)
                            .get("result") or {})
                    kind = str(ping.get("document") or "unknown")
                except Exception:                    # noqa: BLE001
                    continue
        if kind not in ("pcb", "schematic"):
            print("Open a PCB or a schematic in EasyEDA Pro, then run "
                  "this again.")
            bridge.stop()
            return 1
        print(f"document is now {kind!r}, continuing", flush=True)

    # A Node harness runs this same extension against a FAKE eda whose
    # board is named HARNESS-BOARD. If that harness scans ports while
    # this listener is up, it connects HERE, and everything below then
    # records fake data as a live measurement. That happened on
    # once, and the record had to be restored by hand.
    try:
        boards = (bridge.send_editor_command(
            "pcb.list_boards", timeout=15.0).get("result") or {})
        board_names = [str((b or {}).get("name", ""))
                       for b in (boards.get("boards") or [])
                       if isinstance(b, dict)]
    except Exception:                            # noqa: BLE001
        board_names = []
    if any(name.startswith("HARNESS") for name in board_names):
        print(f"\nThe connected client is the TEST HARNESS, not an "
              f"editor: its board is named {board_names!r}. Nothing "
              f"will be recorded. Stop the harness and connect the "
              f"real EasyEDA.")
        bridge.stop()
        return 1

    print(f"\nEditor connected, {kind} document open.\n")

    # What the editor actually injected here, before probing anything.
    # A live session is rare and this is the single most informative
    # call in it: one answer covers the whole surface, where the probes
    # below only report the commands this project happens to have.
    try:
        caps = (bridge.send_editor_command(
            "system.capabilities", timeout=30.0).get("result") or {})
    except Exception as exc:                     # noqa: BLE001
        caps = {}
        print(f"  capability probe failed: {exc}")

    classes = caps.get("classes")
    if isinstance(classes, dict) and classes:
        from eda_agent.bridge.easyeda_verified import verified_path

        target = verified_path().with_name("capabilities.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        # The WHOLE payload, not just the class map. The extra fields
        # are the diagnosis: whether a name absent from a key listing
        # answers when asked for directly, and whether EasyEDA's own
        # full API root is reachable from here.
        target.write_text(
            json.dumps({**caps, "document": kind}, indent=2, sort_keys=True),
            encoding="utf-8")
        methods = sum(len(v) for v in classes.values() if isinstance(v, list))
        print(f"  {len(classes)} API classes present, {methods} methods. "
              f"Written to {target}")
        missing = [name for name in ("pcb_PrimitiveComponent",
                                     "sch_PrimitiveComponent",
                                     "lib_LibrariesList", "dmt_Project")
                   if name not in classes]
        if missing:
            print(f"  absent in this context: {', '.join(missing)}")

        # The two questions that decide what to do about it.
        enumerated = caps.get("enumerated") or []
        present = caps.get("probed_present") or []
        hidden = [n for n in present if n not in enumerated]
        if hidden:
            print(f"  {len(hidden)} class(es) answered when asked for but "
                  f"did not appear in a key listing, so `eda` is lazy: "
                  f"{', '.join(hidden[:6])}")
        else:
            print("  nothing was hidden from the key listing, so the "
                  "surface really is reduced rather than lazy")

        root = caps.get("extapi_root") or {}
        if root.get("reachable"):
            print(f"  EasyEDA's own API root IS reachable via "
                  f"{root.get('where')} with {root.get('count')} of the "
                  f"known classes on it")
        else:
            print("  EasyEDA's own API root is not reachable from the "
                  "extension context")

    print("Running READ-ONLY probes.\n")
    outcomes: dict[str, bool] = {}
    shapes: dict[str, str] = {}
    samples: dict[str, str] = {}
    # Schematic reads FAIL inside the editor while the PCB canvas is
    # active, and the other way round: measured live, sch.components
    # answers "failed to get all components" and three sch probes each
    # burn the full 90s timeout. Probing them from the wrong tab is
    # four and a half minutes of noise that reads as breakage, so the
    # wrong-tab family is set aside by NAME instead.
    other = "sch." if kind == "pcb" else "pcb."
    runnable = [p for p in PROBES if not p[0].startswith(other)]
    deferred = [p[0] for p in PROBES if p[0].startswith(other)]
    if deferred:
        print(f"  {len(deferred)} {other}* probes need the "
              f"{'schematic' if other == 'sch.' else 'pcb'} tab active "
              f"and are set aside; run again with that tab focused to "
              f"cover them.\n")

    # EDA_SMOKE_NEW narrows the run to what nothing has measured yet.
    only_new = os.environ.get("EDA_SMOKE_NEW", "").strip().lower() in (
        "1", "true", "yes")
    slow = SLOW_PROBES
    if only_new:
        from eda_agent.bridge.easyeda_verified import load_verified

        known = (load_verified() or {}).get("shapes") or {}
        before = len(runnable) + len(slow)
        runnable = filter_unmeasured(runnable, known)
        slow = filter_unmeasured(slow, known)
        print(f"  EDA_SMOKE_NEW: {before - len(runnable) - len(slow)} "
              f"already-measured commands skipped; probing "
              f"{len(runnable) + len(slow)}.\n")

    hangs = [p for p in runnable if p[0] in KNOWN_HANGING]
    runnable = [p for p in runnable if p[0] not in KNOWN_HANGING]

    worked, empty, failed = run(bridge, runnable, outcomes, shapes,
                                samples)

    if hangs:
        print(f"\n{len(hangs)} commands measured to hang, re-checked on a "
              f"{HANG_RECHECK_TIMEOUT:.0f}s clock:\n")
        wh, eh, fh = run(bridge, hangs, outcomes, shapes, samples,
                         timeout=HANG_RECHECK_TIMEOUT)
        worked, empty, failed = worked + wh, empty + eh, failed + fh
        if wh:
            print(f"  {wh} of them ANSWERED this time: the editor "
                  f"changed, so update KNOWN_HANGING.")

    if slow:
        print("\nSlower checks (the editor's own DRC and ERC):\n")
    # MEASURED, not guessed at: an export that works answers in
    # seconds (the whole family came back EMPTY promptly on
    # measured), while the ones that fail never settle at all, so
    # the editor's promise hangs and the full timeout is dead time.
    # Eight of them at 90s is twelve minutes of a person holding a tab
    # open. 30s is far above any real export here and cuts that to
    # four. A genuine export that needs longer shows up as a
    # timeout, which is a finding rather than a silent loss.
    w2, e2, f2 = run(bridge, slow, outcomes, shapes, samples,
                     timeout=30.0)
    worked, empty, failed = worked + w2, empty + e2, failed + f2

    total = worked + empty + failed
    print(f"\n{worked}/{total} answered with data, {empty} empty, "
          f"{failed} failed.")

    # Record what was measured, per command. This is the only writer:
    # verified_live reads it rather than carrying an opinion, so a
    # command is verified exactly when a real editor answered it with
    # usable data, and never because someone edited a constant.
    from eda_agent.bridge.easyeda_verified import record_verified

    editor = None
    try:
        editor = str(bridge.send_editor_command(
            "system.ping", timeout=10.0).get("result", {}).get("api"))
    except Exception:            # noqa: BLE001 - the record is optional
        editor = None

    path = record_verified(
        outcomes, editor,
        time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        shapes=shapes, samples=samples)
    print(f"\nRecorded {sum(outcomes.values())} verified command(s) and "
          f"{len(shapes)} response shape(s) to {path}")
    print("The shapes are the field names a tool has to be written "
          "against. Nothing offline can establish them, so a live run "
          "is the only place they exist.")

    if empty or failed:
        print("\nEmpty and failed results are the interesting ones: they "
              "are where the assumed response shape and the editor's "
              "actual one disagree. Report them rather than retrying.")
    else:
        print("\nEvery probe returned data.")

    bridge.stop()
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
