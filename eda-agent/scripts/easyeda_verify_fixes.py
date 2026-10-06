# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Check this session's EasyEDA fixes against a live editor.

Twelve defects were corrected by reading the official API reference,
not by watching an editor. The reference settles what a method is
called and what shape its arguments take. It does not settle what the
editor does when asked, and three of the defects it exposed had a
CORRECT measurement attached to a WRONG cause, so the same mistake in
reverse is entirely possible here.

Each probe below states what a fix predicts and reports whether the
editor agrees. A probe that cannot distinguish the two outcomes is
worse than none, so where the old and new behaviour would look alike
the probe says so rather than claiming a pass.

READ ONLY, with one exception noted at its probe. Nothing changes a
design: selection and highlighting are visible, undoable and touch no
geometry.

Run with EasyEDA Pro open, a BOARD in front, and the extension at
0.9.18 or later:

    python scripts/easyeda_verify_fixes.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eda_agent.bridge.easyeda_bridge import (  # noqa: E402
    EasyEdaBridge,
    EasyEdaNotReachableError,
)


def _invoke(bridge, cls: str, method: str, args: list, timeout=60.0):
    """One raw API call through the reflective shim."""
    return bridge.send_editor_command(
        "system.invoke",
        {"class_name": cls, "method": method, "args": args},
        timeout=timeout)


# Each entry: (title, what the fix predicts, callable returning
# (verdict, detail)). Verdict is "CONFIRMS", "CONTRADICTS" or
# "INCONCLUSIVE", and inconclusive is a real answer.

def probe_drc_overload(bridge):
    """check(strict, ui, includeVerboseError=true) returns an ARRAY.

    The fix: calling with no arguments selected the boolean overload,
    which is why DRC could never report violations.
    """
    bare = _invoke(bridge, "pcb_Drc", "check", [], timeout=120.0)
    full = _invoke(bridge, "pcb_Drc", "check", [True, False, True],
                   timeout=120.0)
    bare_v, full_v = bare.get("result", {}).get("value"), \
        full.get("result", {}).get("value")
    if isinstance(full_v, list):
        # The element shape is undocumented: the overload is typed
        # Promise<Array<any>> and no DRC result interface exists in the
        # reference. Capturing the KEYS here is what settles whether a
        # violation names the two primitives involved, which is the
        # open question behind the routing-repair work. Reported, never
        # assumed: an empty list says nothing about the shape.
        keys = sorted({k for item in full_v[:20]
                       if isinstance(item, dict) for k in item})
        shape = (f"element keys: {', '.join(keys)}" if keys
                 else (f"elements are {type(full_v[0]).__name__}"
                       if full_v else
                       "the list is empty, so the element shape is still "
                       "unknown; run on a board with a real violation"))
        note = (f"three-argument form returned a list of {len(full_v)}; "
                f"zero-argument form returned {type(bare_v).__name__}. "
                f"{shape}")
        return ("CONFIRMS", note)
    if isinstance(full_v, bool) and isinstance(bare_v, bool):
        return ("CONTRADICTS",
                "both forms returned a boolean, so the overload is not "
                "what separates them and the DRC fix is wrong")
    return ("INCONCLUSIVE",
            f"three-argument form returned {type(full_v).__name__}")


def probe_region_argument_order(bridge):
    """getPrimitivesInRegion is (left, right, top, bottom).

    Distinguishing test: a TALL THIN rectangle. Under the old
    interleaving the same four numbers describe a different area, so
    the two orders return different sets unless the board is empty
    there. Equal results mean the probe could not tell, not that the
    fix is wrong.
    """
    x1, x2, y1, y2 = 0, 200, 0, 4000
    correct = _invoke(bridge, "pcb_Document", "getPrimitivesInRegion",
                      [x1, x2, y1, y2])
    swapped = _invoke(bridge, "pcb_Document", "getPrimitivesInRegion",
                      [x1, y1, x2, y2])
    a = correct.get("result", {}).get("value") or []
    b = swapped.get("result", {}).get("value") or []
    if not isinstance(a, list) or not isinstance(b, list):
        return ("INCONCLUSIVE", "the call did not return a list")
    if len(a) == len(b) == 0:
        return ("INCONCLUSIVE",
                "both orders found nothing; move the rectangle over "
                "some copper and run again")
    if len(a) != len(b):
        return ("CONFIRMS",
                f"the two orders disagree ({len(a)} vs {len(b)}), so the "
                f"argument order is load-bearing and was wrong before")
    return ("INCONCLUSIVE",
            f"both orders returned {len(a)} primitives; this rectangle "
            f"cannot tell them apart")


def probe_render_image_shape(bridge):
    """getRenderImage takes {symbolUuid, libraryUuid}, not a bare id.

    The old call was recorded as never returning. If the object form
    answers, the hang was the argument.
    """
    found = bridge.send_editor_command(
        "lib.search_symbols", {"query": "resistor"}, timeout=60.0)
    hits = (found.get("result") or {}).get("found") or []
    if not hits:
        return ("INCONCLUSIVE", "no symbol found to render")
    hit = hits[0]
    uuid = hit.get("uuid") or hit.get("symbolUuid")
    lib = hit.get("libraryUuid") or hit.get("library_uuid")
    if not uuid or not lib:
        return ("INCONCLUSIVE",
                f"the search hit carries no uuid/libraryUuid pair: "
                f"{sorted(hit)[:8]}")
    reply = bridge.send_editor_command(
        "lib.symbol_image", {"uuid": uuid, "library_uuid": lib},
        timeout=90.0)
    image = (reply.get("result") or {}).get("image")
    if image:
        return ("CONFIRMS",
                f"the object form answered; packed as "
                f"{image.get('kind') if isinstance(image, dict) else type(image).__name__}")
    if "error" in reply:
        return ("CONTRADICTS", f"still refused: {reply['error']}")
    return ("INCONCLUSIVE", "answered with no image payload")


def probe_search_paging(bridge):
    """itemsOfPage and page reach past the ten-result default."""
    first = bridge.send_editor_command(
        "lib.search_devices",
        {"query": "res", "items_of_page": 25, "page": 1}, timeout=60.0)
    got = (first.get("result") or {}).get("found") or []
    meta = (first.get("result") or {}).get("meta") or {}
    if len(got) > 10:
        return ("CONFIRMS",
                f"asked for 25 and received {len(got)}, so ten was the "
                f"default page and not a ceiling")
    if len(got) == 10 and meta.get("items_of_page") == 25:
        return ("CONTRADICTS",
                "asked for 25 and still received exactly 10, so ten may "
                "be a real cap after all")
    return ("INCONCLUSIVE",
            f"received {len(got)}; too few matches to tell")


def probe_tab_id_round_trip(bridge):
    """openDocument returns a tab id, which activateDocument accepts."""
    listing = bridge.send_editor_command("proj.list_schematics", {},
                                         timeout=60.0)
    sheets = (listing.get("result") or {}).get("schematics") or []
    if not sheets:
        return ("INCONCLUSIVE", "no schematic to open")
    uuid = sheets[0].get("uuid")
    if not uuid:
        return ("INCONCLUSIVE", "listing carries no uuid")
    opened = bridge.send_editor_command(
        "editor.open_document", {"uuid": uuid}, timeout=90.0)
    tab = (opened.get("result") or {}).get("tab_id")
    if not tab:
        return ("CONTRADICTS",
                "openDocument returned no tab id, so activateDocument "
                "has no source for one")
    back = bridge.send_editor_command(
        "editor.activate_document", {"tab_id": tab}, timeout=60.0)
    if (back.get("result") or {}).get("activated"):
        return ("CONFIRMS", f"tab id {tab!r} round-tripped")
    return ("CONTRADICTS",
            f"tab id {tab!r} was returned but not accepted: {back}")


def probe_cross_probe_by_designator(bridge):
    """doCrossProbeSelect takes designators, not primitive ids.

    THE ONE PROBE THAT CHANGES SOMETHING VISIBLE: it selects and
    highlights. No geometry is touched and the editor's own selection
    is trivially cleared.
    """
    comps = bridge.send_editor_command("pcb.components", {}, timeout=60.0)
    parts = (comps.get("result") or {}).get("components") or []
    named = [p.get("designator") for p in parts if p.get("designator")]
    if not named:
        return ("INCONCLUSIVE", "no designators on the open board")
    reply = bridge.send_editor_command(
        "pcb.cross_probe", {"designators": named[:1]}, timeout=60.0)
    result = reply.get("result") or {}
    if result.get("cross_probed") is True:
        return ("CONFIRMS", f"{named[0]} cross-probed by designator")
    if "error" in reply:
        return ("CONTRADICTS", f"refused: {reply['error']}")
    return ("CONTRADICTS",
            f"the editor answered {result.get('cross_probed')!r} for a "
            f"designator taken off the open board")


def probe_project_delete_absent(bridge):
    """dmt_Project has no deleteProject. Confirm by asking the runtime."""
    caps = bridge.send_editor_command("system.capabilities", {},
                                      timeout=60.0)
    classes = (caps.get("result") or {}).get("classes") or {}
    methods = classes.get("dmt_Project")
    if methods is None:
        return ("INCONCLUSIVE", "the runtime did not report dmt_Project")
    if "deleteProject" in methods:
        return ("CONTRADICTS",
                "the runtime DOES expose deleteProject, so the refusal "
                "we now return is wrong and it should be restored")
    return ("CONFIRMS",
            f"dmt_Project exposes {len(methods)} methods, none of them "
            f"deleteProject")


# ---------------------------------------------------------------------
# OPEN QUESTIONS, not fixes. These settle something no amount of reading
# can: a field whose VALUES are undocumented. The reference gives
# solderMaskExpansion a type and a one-line label and says nothing about
# units or sentinels, so whether -1000 means "fully covered" or "use the
# rule" cannot be decided offline. An audit built on a guessed threshold
# would report confident findings about nothing.
# ---------------------------------------------------------------------

def probe_via_tenting_values(bridge):
    """What values solderMaskExpansion actually takes on a real board.

    Blocks tented_via_ratio. Reports the distribution rather than a
    verdict, because the verdict is the thing being established.
    """
    reply = bridge.send_editor_command("pcb.vias", {}, timeout=90.0)
    vias = (reply.get("result") or {}).get("vias") or []
    if not vias:
        return ("INCONCLUSIVE", "no vias on the open board")
    seen = {}
    for v in vias:
        exp = v.get("solderMaskExpansion") or {}
        key = (exp.get("topSolderMask"), exp.get("bottomSolderMask"))
        seen[key] = seen.get(key, 0) + 1
    spread = sorted(seen.items(), key=lambda kv: -kv[1])
    detail = "; ".join(f"top={k[0]} bottom={k[1]} on {n} vias"
                       for k, n in spread[:5])
    if len(seen) == 1:
        return ("INCONCLUSIVE",
                f"every via carries the same value ({detail}), so this "
                f"board cannot show what distinguishes tented from not. "
                f"Untent one via in the editor and run again")
    return ("CONFIRMS",
            f"{len(seen)} distinct values, so the field does vary and "
            f"is readable per surface: {detail}")


def probe_search_by_properties_shape(bridge):
    """Learn the keys of ILIB_DevicePropertiesForSearch.

    Blocks a structured part lookup: every library search we expose
    takes a free-text key, and searchByProperties is the route to
    finding a part by manufacturer or MPN instead. The method is
    documented; its argument TYPE is not, three ways over (the
    interface page 404s, the reference index lists no such name, and it
    is absent from the offline clone).

    So this asks the editor. An empty object often produces an error
    naming the fields it wanted, which is the cheapest way to learn a
    shape. Failing that, a single plausible key at least separates
    "rejected the shape" from "accepted and matched nothing".

    The keys tried below come from ILIB_DeviceSearchItem, the
    documented RETURN element type. That is SUGGESTIVE ONLY: a
    search-by-properties argument often mirrors the searchable subset
    of its result, and often is not always. Nothing here should be
    written into a tool on the strength of this probe alone.
    """
    attempts = [
        ("empty object", {}),
        ("name", {"name": "resistor"}),
        ("otherProperty", {"otherProperty": {"Manufacturer": "Yageo"}}),
    ]
    notes = []
    for label, props in attempts:
        reply = _invoke(bridge, "lib_Device", "searchByProperties",
                        [props], timeout=60.0)
        if "error" in reply:
            notes.append(f"{label}: refused with {reply['error'][:90]}")
            continue
        value = (reply.get("result") or {}).get("value")
        if isinstance(value, list):
            notes.append(f"{label}: accepted, {len(value)} hits")
        else:
            notes.append(f"{label}: returned {type(value).__name__}")
    detail = "; ".join(notes)
    if any("accepted" in n and "0 hits" not in n for n in notes):
        return ("CONFIRMS", f"a key was accepted and matched: {detail}")
    if all("refused" in n for n in notes):
        return ("INCONCLUSIVE",
                f"every attempt was refused, which is still useful if the "
                f"message names the expected fields: {detail}")
    return ("INCONCLUSIVE", f"no attempt matched anything: {detail}")


def probe_library_file_by_uuid(bridge):
    """What an elibz actually is.

    Blocks both remaining library audits. They need per-item geometry
    across a whole library, and neither documented route gives it:
    lib_Footprint.get returns metadata only, and openInEditor costs a
    round trip per item.

    SYS_FileManager.getFootprintFileByFootprintUuid does, and its
    signature is fully specified:

      (footprintUuid: string | Array<string>, libraryUuid?: string,
       fileType?: 'elibz' | 'elibz2'): Promise<File | undefined>

    The uuid parameter takes an ARRAY, so a library reads in one call.
    The only unknown is what is INSIDE the returned file: we handle
    elibz nowhere. Two bytes settle whether it is a zip, and the size
    settles whether it carries geometry or a stub.
    """
    found = bridge.send_editor_command(
        "lib.search_footprints", {"query": "0402"}, timeout=60.0)
    hits = (found.get("result") or {}).get("found") or []
    if not hits:
        return ("INCONCLUSIVE", "no footprint found to fetch")
    hit = hits[0]
    uuid = hit.get("uuid") or hit.get("footprintUuid")
    lib = hit.get("libraryUuid") or hit.get("library_uuid")
    if not uuid:
        return ("INCONCLUSIVE", f"hit carries no uuid: {sorted(hit)[:8]}")

    reply = _invoke(bridge, "sys_FileManager",
                    "getFootprintFileByFootprintUuid",
                    [uuid, lib, "elibz"], timeout=90.0)
    if "error" in reply:
        return ("INCONCLUSIVE", f"refused: {reply['error'][:110]}")
    value = (reply.get("result") or {}).get("value")
    if value is None:
        return ("INCONCLUSIVE",
                "returned nothing for a uuid that a search had just "
                "produced, so either libraryUuid is required here or "
                "the fileType is wrong")
    # A File crossing this bridge arrives as whatever the shim made of
    # it. Reporting the raw shape is the point: if it is {} then the
    # File needs packedFile on the extension side, which is the same
    # fault the exports and the render images had.
    kind = type(value).__name__
    if isinstance(value, dict):
        keys = sorted(value)
        if not keys:
            return ("CONTRADICTS",
                    "the File arrived as an empty object, so it needs "
                    "packedFile() on the extension side before any of "
                    "this is usable")
        return ("CONFIRMS", f"returned an object with keys: {keys}")
    if isinstance(value, str):
        head = value[:16]
        zipish = value.startswith("UEsDB") or head.startswith("PK")
        return ("CONFIRMS",
                f"returned a {len(value)}-char string starting {head!r}; "
                f"{'looks like a zip' if zipish else 'not obviously a zip'}")
    return ("CONFIRMS", f"returned a {kind}")


PROBES = [
    ("OPEN QUESTION: what an elibz library file contains",
     probe_library_file_by_uuid),
    ("OPEN QUESTION: searchByProperties argument shape",
     probe_search_by_properties_shape),
    ("OPEN QUESTION: via tenting values", probe_via_tenting_values),
    ("DRC returns an array with the third argument", probe_drc_overload),
    ("Region search argument order", probe_region_argument_order),
    ("Render image takes an object", probe_render_image_shape),
    ("Library search pages past ten", probe_search_paging),
    ("Tab id round-trips open to activate", probe_tab_id_round_trip),
    ("Cross-probe by designator", probe_cross_probe_by_designator),
    ("Project delete is genuinely absent", probe_project_delete_absent),
]


def main() -> int:
    bridge = EasyEdaBridge()
    try:
        bridge.start()
    except Exception as exc:  # pragma: no cover - live path
        print(f"could not start the bridge: {exc}")
        return 2

    print("Waiting for the EasyEDA extension to connect...")
    try:
        bridge.send_editor_command("system.ping", {}, timeout=60.0)
    except EasyEdaNotReachableError as exc:
        print(f"no editor connected: {exc}")
        return 2
    print("connected.\n")

    tally = {"CONFIRMS": 0, "CONTRADICTS": 0, "INCONCLUSIVE": 0}
    for title, probe in PROBES:
        try:
            verdict, detail = probe(bridge)
        except EasyEdaNotReachableError as exc:
            verdict, detail = "INCONCLUSIVE", f"unreachable: {exc}"
        except Exception as exc:  # pragma: no cover - live path
            verdict, detail = "INCONCLUSIVE", f"{type(exc).__name__}: {exc}"
        tally[verdict] = tally.get(verdict, 0) + 1
        print(f"  {verdict:13} {title}")
        print(f"                {detail}")

    print()
    print(f"  confirmed {tally['CONFIRMS']}, contradicted "
          f"{tally['CONTRADICTS']}, inconclusive {tally['INCONCLUSIVE']}")
    if tally["CONTRADICTS"]:
        print("\n  A CONTRADICTION MEANS A FIX IS WRONG. The reference "
              "settles what a call is named and shaped, not what the "
              "editor does with it. Read the detail above before "
              "changing anything back.")
    if tally["INCONCLUSIVE"]:
        print("\n  Inconclusive is not a pass. It means the probe could "
              "not tell the two outcomes apart on this design.")
    return 1 if tally["CONTRADICTS"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
