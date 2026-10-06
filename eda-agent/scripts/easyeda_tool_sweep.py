# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Drive every read-only EasyEDA TOOL against a live editor.

`easyeda_smoke.py` probes bridge COMMANDS. This probes the MCP tools,
and the difference is not cosmetic: a command can round-trip perfectly
while the tool wrapping it reads a field the editor never sends. Three
tools shipped that way and were caught only when a live board
contradicted them, because every test in the suite fed them the shape
they expected.

Two phases, one connection, because a live session is scarce.

Phase 1 runs the tools classified ``readonly`` whose arguments all have
defaults. Nothing it calls mutates the design.

Phase 2 dumps the KEY SET of a representative object from the reads
that block the remaining audits. Those audits are not blocked on
effort; they are blocked on nobody knowing whether the editor reports
the field they would need. Measuring beats another guess.

Refusals come before results, and in a deliberate order. A build
mismatch is checked FIRST: EasyEDA installs by version, so re-importing
a package whose version matches the installed one is a silent no-op,
and every other diagnosis is meaningless against unknown code. An
earlier revision of this script printed the mismatch and carried on to
blame the document type, naming the wrong one of two candidate faults.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import pathlib
import sys
import time
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from eda_agent.tools import metadata as M
from eda_agent.tools import register_backend
from eda_agent.tools.registry import ToolRegistry

#: Commands measured to hang. A tool fanning out to one hangs with it,
#: so they are named rather than discovered the expensive way.
HANGING_COMMANDS = frozenset({
    "pcb.attributes", "sch.attributes", "sys.paths", "pcb.strings",
    "pcb.poured",
    # Measured on a live schematic: no reply in 30 seconds. Why they
    # hang is not established, and it may be the editor's behaviour
    # rather than this extension's.
    "sch.selection", "sch.assembly_variants",
})

#: How long to wait on a command that has hung before. The extension
#: gives up at 15s and says so, so this only has to outlast that: the
#: aim is to collect ITS message, which names the command and says the
#: call was accepted rather than refused, instead of timing out here
#: and reporting the less specific "no reply".
#:
#: Four of these now try a SECOND read path before giving up
#: (getAllPrimitiveId then get per item, where getAll stalls), and the
#: reply carries a `via` field saying which one answered. That field is
#: the point of probing them: "ids" means three board audits and one
#: library check stop being blocked.
HANG_PROBE_TIMEOUT = 25.0

#: The four hanging reads that now have a fallback. Worth calling out
#: separately in the output, because their `via` is a finding rather
#: than a detail.
FALLBACK_READS = frozenset({
    "sch.attributes", "pcb.attributes", "pcb.strings", "pcb.poured",
})

#: Reads whose object shape decides whether a blocked audit is
#: implementable. The second element is the field the payload sits under.
SHAPE_TARGETS = (
    ("pcb.images", "images"),                     # non-embedded images
    ("pcb.pads", "pads"),                         # removed pad shapes
    ("pcb.vias", "vias"),                         # tented via ratio
    ("pcb.components", "components"),             # lock state, rotation
    ("pcb.regions", "regions"),
    ("pcb.fills", "fills"),
    ("pcb.embedded_objects", "embedded_objects"),
    ("pcb.net_classes", "net_classes"),           # routing DRC data
    ("pcb.rule_configurations", "rule_configurations"),
    ("sch.components", "components"),             # ports / labels / power
    ("sch.pins", "pins"),
    ("sch.wires", "wires"),
    ("sch.buses", "buses"),
    ("sch.netlist", "netlist"),
    # PCB reads carry a numeric layer, and which integer is top copper
    # decides whether a board ships with reversed silkscreen and which
    # side a net is routed on. The rows are a list, each carrying id,
    # name and type; type is the field the classifiers read.
    ("pcb.layers", "layers"),
)

#: Reads that need an argument, so they cannot go in SHAPE_TARGETS.
#: The value says where to get it.
#:
#: pcb.bboxes gives easyeda_plan_placement its component sizes, and
#: without it placement falls back to pad extents, which understate a
#: footprint and pack parts too tightly.
#:
#: The payload sits under `boxes`, not `bboxes`, and each entry is
#: {primitive_id, bbox: {minX, minY, maxX, maxY}}. Both the key and the
#: corner names differ from the x1/y1 form used elsewhere, which is why
#: the field is named here rather than inferred from the command.
ARGUMENT_SHAPE_TARGETS = (
    ("pcb.bboxes", "boxes", "pcb.components", "components", "primitiveId"),
)

#: A design document only. EasyEDA injects the pcb_*/sch_* API per
#: document type, so on the start page every probe fails with
#: "undefined" and not one of those failures is real.
DESIGN_DOCUMENTS = frozenset({"pcb", "schematic"})


def selectable_tools(registry) -> list:
    """The tools this sweep is allowed to call, and no others.

    Two filters, each load-bearing. ``readonly`` excludes the 109 tools
    classified ``silent``, which mutate without a dialog: pointing
    those at a real board is not a smoke test, it is an edit. And a
    tool with a required argument cannot be called blind, so passing
    nothing would only measure how it reports a missing argument.
    """
    out = []
    for name in sorted(registry.names):
        if not name.startswith("easyeda_"):
            continue
        if M.interaction_of(name) != M.READONLY:
            continue
        fn = registry.get(name).fn
        params = inspect.signature(fn).parameters.values()
        if any(p.default is inspect.Parameter.empty for p in params):
            continue
        out.append((name, fn))
    return out


def refusal(ping: dict, expected_build: str):
    """Why this session must not be recorded, or None to proceed.

    Ordered by how badly a wrong answer misleads. An unexpected build
    invalidates everything downstream, so it is judged before the
    document type rather than after.
    """
    build = ping.get("build")
    if build != expected_build:
        return (4, f"the editor is running build {build!r} but this tree "
                   f"builds {expected_build!r}. EasyEDA installs BY "
                   f"VERSION, so re-importing at the same version is a "
                   f"silent no-op: bump extension.json, rebuild, and "
                   f"import that.")

    # A Node harness runs this same extension against a fake editor whose
    # board is HARNESS-BOARD. If it scans ports while this listener is
    # up it connects HERE, and fake data gets recorded as a live
    # measurement. That happened, and the record had to be repaired.
    if str(ping.get("board") or "").upper().startswith("HARNESS"):
        return (2, "the test harness connected, not a real editor.")

    kind = str(ping.get("document") or "unknown")
    if kind not in DESIGN_DOCUMENTS:
        return (3, f"the active document is {kind!r}. EasyEDA only "
                   f"injects the pcb_*/sch_* API into a design document, "
                   f"so every probe would fail with 'undefined' and none "
                   f"of those failures would be real.")
    return None


#: Tools whose ANSWER is the point, not just its shape.
#:
#: The sweep records key names for everything, which is right for a
#: board read where the rows are the user's design and not this
#: project's business. It is exactly wrong for these: system.capabilities
#: exists to say which API classes are present in this runtime, and a
#: live run recorded that its reply had a `probed_present` key while
#: throwing away the list. Every later question about why a command
#: hung then had to be answered from a guessed list of what was
#: available, which is how a measurement session ends up producing
#: inferences.
_ANSWER_MATTERS = frozenset({
    "easyeda_get_capabilities",
    "easyeda_get_environment",
    "easyeda_get_project_info",
    "easyeda_get_measured_shapes",
})


def harvest_outcomes(shapes: dict) -> tuple:
    """Turn harvest results into (command -> usable, command -> fields).

    Separated out so the rule can be tested rather than merely written
    down. The rule is that ONLY harvested commands reach the shared
    verification record: they were issued raw and their replies read
    raw, which is a measurement. A tool verdict is not, because a tool
    can fan out to several commands or refuse on its own arguments
    before sending anything, so filing one as a command fact is an
    inference dressed as evidence.

    An EMPTY container is False, matching the record's own rule: on a
    loaded board an empty result usually means the reply shape was
    misread, and that must never be filed as a success.
    """
    outcomes: dict = {}
    field_names: dict = {}
    for command, got in (shapes or {}).items():
        if not isinstance(got, dict) or "skipped" in got:
            continue
        if "." not in command:
            # A tool name, not a command. Refused rather than skipped:
            # something is feeding the wrong collection in.
            raise ValueError(
                f"{command!r} is not a command name; only harvested "
                f"commands may reach the verification record")
        usable = bool(got.get("count"))
        outcomes[command] = usable
        keys = got.get("sample_keys")
        if usable and keys:
            field_names[command] = ", ".join(keys)
    return outcomes, field_names


def _try_open_a_design_document(bridge) -> str:
    """List what exists and open one, reporting what happened.

    Returns a sentence rather than raising: this runs while the sweep is
    deciding whether it can proceed at all, and a failure here is a
    measurement (editor.open_document does not work) rather than a
    reason to abandon the session.
    """
    for command, field, kind in (("sch.list_schematics", "schematics",
                                  "schematic"),
                                 ("pcb.list_boards", "boards", "PCB")):
        try:
            reply = bridge.send_editor_command(command, timeout=15.0)
        except Exception as exc:                       # noqa: BLE001
            return f"{command} failed: {exc}"
        if "error" in reply:
            return f"{command} refused: {reply['error']}"

        items = (reply.get("result") or {}).get(field) or []
        if not items:
            continue

        first = items[0]
        uuid = ""
        if isinstance(first, dict):
            uuid = str(first.get("uuid") or first.get("id") or "")
        elif isinstance(first, str):
            uuid = first
        if not uuid:
            return (f"{command} listed {len(items)} {kind}(s) but none "
                    f"carried a uuid: {str(first)[:90]}")

        try:
            opened = bridge.send_editor_command(
                "editor.open_document", {"uuid": uuid}, timeout=20.0)
        except Exception as exc:                       # noqa: BLE001
            return f"editor.open_document({uuid[:8]}...) failed: {exc}"
        if "error" in opened:
            return f"editor.open_document refused: {opened['error']}"
        return f"opened a {kind} ({uuid[:8]}...) via editor.open_document"

    return "nothing to open: no schematics and no boards were listed"


def classify(reply, elapsed: float, name: str = "") -> dict:
    """One tool's outcome, in the shape the report is built from."""
    if isinstance(reply, dict):
        failed = reply.get("ok") is False or "error" in reply
        out = {"verdict": "refused" if failed else "ok",
               "seconds": elapsed,
               "keys": sorted(reply)[:14],
               "reason": reply.get("reason") or reply.get("error")}
        if name in _ANSWER_MATTERS:
            out["reply"] = reply
        return out
    return {"verdict": "ok", "seconds": elapsed,
            "type": type(reply).__name__}


def _shape(payload) -> dict:
    if isinstance(payload, dict):
        first = next(iter(payload.values()), None)
        container, count = "dict", len(payload)
    elif isinstance(payload, list):
        first = payload[0] if payload else None
        container, count = "list", len(payload)
    else:
        return {"container": type(payload).__name__, "value": payload}
    return {"container": container, "count": count,
            "sample_keys": sorted(first) if isinstance(first, dict) else None,
            "sample": first}


def main() -> int:
    from eda_agent.bridge import easyeda_bridge as EB
    from eda_agent.bridge.easyeda_bridge import EasyEdaBridge
    from easyeda_smoke import local_build_id

    out_path = pathlib.Path(
        os.environ.get("EDA_SWEEP_OUT", "easyeda_tool_sweep.json"))

    bridge = EasyEdaBridge()
    status = bridge.start()
    EB._BRIDGE = bridge          # the tools resolve through the singleton
    print(f"Listening on {status['host']}:{status['port']}", flush=True)
    print("Connect EasyEDA Pro with a PCB or schematic tab open.",
          flush=True)

    wait = int(os.environ.get("EDA_SWEEP_WAIT", "900"))
    deadline = time.time() + wait
    while time.time() < deadline and not bridge.connected:
        time.sleep(0.5)
    if not bridge.connected:
        print(f"\nNo editor connected within {wait}s.", flush=True)
        bridge.stop()
        return 1

    # Give a second tab a moment to dial in. The bridge keeps one
    # connection per editor runtime now, and a session with both a PCB
    # and a schematic connected is the one that proves the routing:
    # pcb_* does not exist in the schematic runtime, so a misrouted
    # command fails in a way no single-tab run can show.
    grace = float(os.environ.get("EDA_SWEEP_SECOND_TAB_GRACE", "20"))
    settle = time.time() + grace
    while time.time() < settle and len(getattr(bridge, "_conns", {})) < 2:
        time.sleep(0.5)

    ping = (bridge.send_editor_command("system.ping", timeout=15.0)
            .get("result") or {})
    print(f"\nCONNECTED document={ping.get('document')!r} "
          f"build={ping.get('build')!r} api={ping.get('api')!r}", flush=True)

    editors = getattr(bridge, "_conns", {})
    print(f"editor runtimes connected: {len(editors)}", flush=True)
    if len(editors) > 1 and hasattr(bridge, "_learn_contexts"):
        bridge._learn_contexts()
        contexts = sorted(str(i.get("context"))
                          for i in bridge._conns.values())
        print(f"  contexts: {contexts}", flush=True)
    elif len(editors) == 1:
        print("  (only one tab; open a PCB AND a schematic to exercise "
              "namespace routing)", flush=True)

    verdict = refusal(ping, local_build_id())

    # A wrong tab is worth WAITING through rather than exiting on.
    #
    # Re-importing an extension leaves EasyEDA on its settings page, so
    # the first connect after an import reports the document as
    # "unknown" almost every time. Exiting there costs a full restart
    # of the listener and another connect, for something the person can
    # fix in one click. The build mismatch is different and still exits
    # at once: that needs a rebuild and a re-import, so waiting would
    # only stall.
    if verdict is not None and verdict[0] == 3:
        print(f"\n{verdict[1]}", flush=True)

        # Optionally open one instead of waiting for a human.
        #
        # The extension has the whole discovery-and-open loop:
        # sch.list_schematics / pcb.list_boards name what exists and
        # editor.open_document opens one by uuid. The listing halves are
        # confirmed working live; editor.open_document has NEVER been
        # measured, so trying it here both removes the manual click and
        # settles whether it works.
        #
        # OPT-IN, because opening a document changes what is in front of
        # the person watching. That is not a design edit, but it is
        # still their screen, and a harness should not rearrange it
        # uninvited.
        if os.environ.get("EDA_SWEEP_TRY_OPEN") == "1":
            print("EDA_SWEEP_TRY_OPEN=1: asking the editor to open a "
                  "design document itself.", flush=True)
            opened = _try_open_a_design_document(bridge)
            print(f"  {opened}", flush=True)
            try:
                ping = (bridge.send_editor_command("system.ping",
                                                   timeout=10.0)
                        .get("result") or {})
                verdict = refusal(ping, local_build_id())
                if verdict is None:
                    print(f"  it worked: document is now "
                          f"{ping.get('document')!r}", flush=True)
            except Exception as exc:                   # noqa: BLE001
                print(f"  ping after open failed: {exc}", flush=True)

    if verdict is not None and verdict[0] == 3:
        print("Waiting for a design tab: click onto a PCB or schematic "
              "and this will carry on by itself.", flush=True)
        patience = float(os.environ.get("EDA_SWEEP_TAB_WAIT", "600"))
        until = time.time() + patience
        while time.time() < until:
            time.sleep(3.0)
            try:
                ping = (bridge.send_editor_command("system.ping",
                                                   timeout=10.0)
                        .get("result") or {})
            except Exception:                          # noqa: BLE001
                continue
            verdict = refusal(ping, local_build_id())
            if verdict is None:
                print(f"\ndocument is now {ping.get('document')!r}, "
                      f"continuing", flush=True)
                break

    if verdict is not None:
        code, why = verdict
        print(f"\nREFUSING: {why}", flush=True)
        bridge.stop()
        return code

    registry = ToolRegistry()
    register_backend(registry, "easyeda", "full")
    targets = selectable_tools(registry)
    print(f"\nsweeping {len(targets)} read-only tools\n", flush=True)

    results = {}
    for i, (name, fn) in enumerate(targets, 1):
        started = time.time()
        try:
            reply = asyncio.run(asyncio.wait_for(fn(), timeout=25))
            results[name] = classify(
                reply, round(time.time() - started, 2), name)
        except asyncio.TimeoutError:
            results[name] = {"verdict": "timeout",
                             "seconds": round(time.time() - started, 2)}
        except Exception as exc:                       # noqa: BLE001
            results[name] = {"verdict": "raised",
                             "seconds": round(time.time() - started, 2),
                             "reason": f"{type(exc).__name__}: {exc}",
                             "trace": traceback.format_exc()[-600:]}
        print(f"[{i:3}/{len(targets)}] {results[name]['verdict']:8} {name}",
              flush=True)

    # The review is the point, not a line in a table of 90 verdicts.
    #
    # easyeda_review_board is selected like any other read-only tool, so
    # a real design review of the open board happens on every run and
    # was being recorded as "ok" next to eighty-nine others. What it
    # FOUND is the thing worth reading, and it is also the first live
    # evidence that the audits work against a real board rather than
    # against the measured shapes they were written from.
    review = results.get("easyeda_review_board")
    if review and review.get("verdict") == "ok":
        try:
            reply = asyncio.run(asyncio.wait_for(
                registry.get("easyeda_review_board").fn(), timeout=90))
        except Exception as exc:                       # noqa: BLE001
            reply = {"ok": False, "reason": str(exc)}
        print("\n--- design review of the open board ---", flush=True)
        if reply.get("ok"):
            print(f"  {reply.get('audits_run')} of "
                  f"{reply.get('audits_total')} audits produced a count; "
                  f"{reply.get('total_violations')} violations",
                  flush=True)
            for finding in reply.get("findings") or []:
                print(f"    {finding['violation_count']:5}  "
                      f"{finding['audit']}", flush=True)
            for entry in (reply.get("refused") or [])[:8]:
                print(f"    refused: {entry.get('audit')} "
                      f"({str(entry.get('reason'))[:60]})", flush=True)
        else:
            print(f"  refused: {reply.get('reason')}", flush=True)
        # NOT into `results`. That map is one entry per TOOL CALL, each
        # carrying a verdict, and the tally at the end reads that key
        # off every entry. Filing a raw reply here crashed the run
        # AFTER the harvest had been printed, which is the worst place
        # for it: the data was on screen and the summary never came.
        review_detail = reply

    # What the API can actually DO here, for the work that is blocked on
    # exactly that question.
    #
    # system.capabilities enumerates the METHODS on every class the
    # runtime exposes, which is the answer to "can EasyEDA create an
    # assembly variant / annotate a schematic / remove a document" -
    # questions currently parked because they were assumed to need the
    # installed api-types.d.ts. They do not. The reply already carries
    # it and the harness was throwing it away.
    _BLOCKED_ON = {
        "sch_ManufactureData": "assembly variants: read works, is there a "
                               "create/set?",
        "dmt_EditorControl": "opening and switching documents",
        "sch_Document": "document-level operations (remove, save)",
        "pcb_Document": "document-level operations",
        "pcb_Layer": "the layer vocabulary two library audits need",
        "sch_PrimitiveComponent": "annotation, replace-component",
    }
    try:
        caps = (registry.get("easyeda_get_capabilities").fn)
        cap_reply = asyncio.run(asyncio.wait_for(caps(), timeout=30))
        classes = (cap_reply or {}).get("classes") or {}
        print("\n--- API surface for the blocked questions ---", flush=True)
        for name, why in sorted(_BLOCKED_ON.items()):
            methods = classes.get(name)
            if methods is None:
                print(f"  {name:26} ABSENT in this runtime  ({why})",
                      flush=True)
            else:
                print(f"  {name:26} {len(methods)} methods  ({why})",
                      flush=True)
                print(f"      {', '.join(methods)}", flush=True)

        # Then EVERY class, because the six above are a guess about
        # where a capability lives. `annotate` might sit on a document
        # class, or on one nothing here has ever called. Pre-filtering
        # what to look at is how a measurement session ends up needing
        # a second measurement session.
        others = sorted(set(classes) - set(_BLOCKED_ON))
        if others:
            print(f"\n  every other class ({len(others)}):", flush=True)
            for name in others:
                print(f"    {name:28} {len(classes[name])}", flush=True)

        # And name the methods that would answer the open questions,
        # wherever they turn out to live.
        wanted = ("annotat", "variant", "replace", "remove", "delete",
                  "rename", "parameter", "layer", "open", "close")
        hits = []
        for name, methods in sorted(classes.items()):
            for method in methods:
                low = method.lower()
                if any(w in low for w in wanted):
                    hits.append(f"{name}.{method}")
        if hits:
            print(f"\n  methods matching the open questions "
                  f"({len(hits)}):", flush=True)
            for hit in hits:
                print(f"    {hit}", flush=True)
    except Exception as exc:                           # noqa: BLE001
        print(f"\ncould not read the API surface: {exc}", flush=True)

    print("\n--- shape harvest ---", flush=True)
    shapes = {}
    for command, field in SHAPE_TARGETS:
        # The known hangs are PROBED, not skipped.
        #
        # Skipping them was right while a hang was unbounded: one such
        # read cost the rest of the run. Since the extension started
        # answering its own timeout the cost is a bounded failure, and
        # skipping now only guarantees the shape stays unknown. Two of
        # these decide whether a blocked audit is implementable at all,
        # so the answer is worth the wait.
        #
        # A little past the extension's own ceiling, so its timeout
        # message arrives rather than this side giving up first and
        # reporting a less specific failure.
        timeout = 20.0
        if command in HANGING_COMMANDS:
            timeout = HANG_PROBE_TIMEOUT
        try:
            reply = bridge.send_editor_command(command, timeout=timeout)
            if command in FALLBACK_READS:
                route = (reply.get("result") or {}).get("via")
                if route:
                    print(f"  {command}: answered via {route}"
                          + ("  <- the fallback works, four items "
                             "unblock" if route == "ids" else ""),
                          flush=True)
            # The editor's own error, kept rather than flattened.
            #
            # This used to read (result or {}).get(field) and report the
            # shape of whatever came back. When the command ERRORED the
            # reply carries `error` and no `result`, so that produced
            # the single word "NoneType" for every failure and threw
            # away the message. A live run then reported seven reads as
            # NoneType, which says the field is missing; the editor had
            # actually said "Cannot read properties of null", which
            # says something entirely different and is the only clue to
            # what went wrong.
            if "error" in reply:
                shapes[command] = {"editor_error": str(reply["error"])}
            else:
                shapes[command] = _shape(
                    (reply.get("result") or {}).get(field))
        except Exception as exc:                       # noqa: BLE001
            shapes[command] = {"error": f"{type(exc).__name__}: {exc}"}
        got = shapes[command]
        print(f"  {command:26} "
              f"{got.get('count', got.get('editor_error', got.get('error', got.get('container'))))}",
              flush=True)

    # Reads that need an argument, fed from a read that does not.
    #
    # These cannot sit in SHAPE_TARGETS because probing them with
    # nothing only measures how they report a missing argument, which
    # is not the question. The question is what a REAL answer looks
    # like, and three shipped features guess at it.
    for command, field, source, source_field, id_field in \
            ARGUMENT_SHAPE_TARGETS:
        try:
            first = bridge.send_editor_command(source, timeout=20.0)
            rows = ((first.get("result") or {}).get(source_field)) or []
            ids = [str(r.get(id_field)) for r in rows
                   if isinstance(r, dict) and r.get(id_field)]
            if not ids:
                shapes[command] = {
                    "skipped": f"{source} reported no {id_field} to ask about"}
                print(f"  {command:26} no ids from {source}", flush=True)
                continue
            # A handful is enough to see the shape, and asking about
            # every component on a large board is a slow way to learn
            # the same thing.
            reply = bridge.send_editor_command(
                command, {"primitive_ids": ids[:5]}, timeout=30.0)
            if "error" in reply:
                shapes[command] = {"editor_error": str(reply["error"])}
            else:
                payload = (reply.get("result") or {}).get(field)
                shapes[command] = _shape(payload)
                # The shape summary says list-or-dict; for this one the
                # FIELD NAMES inside decide whether placement can read
                # it, so a sample is kept.
                sample = None
                if isinstance(payload, dict):
                    for value in payload.values():
                        sample = value
                        break
                elif isinstance(payload, list) and payload:
                    sample = payload[0]
                if isinstance(sample, dict):
                    shapes[command]["sample_keys"] = sorted(sample)
        except Exception as exc:                       # noqa: BLE001
            shapes[command] = {"error": f"{type(exc).__name__}: {exc}"}
        got = shapes[command]
        print(f"  {command:26} "
              f"{got.get('sample_keys', got.get('editor_error', got.get('error', got.get('skipped'))))}",
              flush=True)

    # Fold the harvest into the shared verification record.
    #
    # Only the HARVEST, never the tool results. The record maps a
    # COMMAND to whether it returned usable data; the sweep's first
    # phase drives tools, and a tool can fan out to several commands or
    # refuse on its own arguments before sending anything. Filing a tool
    # verdict as a command fact would be an inference wearing the
    # clothes of a measurement, which is the thing this record exists to
    # keep out. The harvest issues raw commands and reads raw replies,
    # so those are measurements and belong here.
    #
    # An EMPTY container counts as False, matching the record's own
    # rule: on a loaded board an empty result usually means the reply
    # shape was misread, and that must never be filed as a success.
    try:
        from eda_agent.bridge.easyeda_verified import record_verified

        outcomes, field_names = harvest_outcomes(shapes)
        if outcomes:
            record_verified(
                outcomes, str(ping.get("api") or "") or None,
                time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
                shapes=field_names)
            print(f"\nrecorded {sum(outcomes.values())} of "
                  f"{len(outcomes)} harvested commands to the "
                  f"verification record", flush=True)
    except Exception as exc:                           # noqa: BLE001
        # The record is a by-product. Losing it must not lose the run.
        print(f"\ncould not update the verification record: {exc}",
              flush=True)

    out_path.write_text(
        json.dumps({"document": ping.get("document"),
                    "build": ping.get("build"),
                    "results": results, "shapes": shapes,
                    "review_detail": review_detail},
                   indent=2, default=str),
        encoding="utf-8")

    from collections import Counter
    # .get, not [], so an entry that somehow lacks a verdict is
    # reported as such rather than ending the run. A summary is the
    # last thing printed and the first thing read.
    tally = Counter(
        (r.get("verdict", "no-verdict") if isinstance(r, dict)
         else "no-verdict")
        for r in results.values())
    print(f"\n=== {dict(tally)} ===\nwritten to {out_path}", flush=True)
    bridge.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
