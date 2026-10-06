# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Task-oriented guidance: which tool, on which document, and what bites.

``tool_catalog`` answers "what is this tool called". It cannot answer
"what is the right approach for this document kind", and that is the
question that has actually been getting wrong answers.

The failures this exists for were all the same shape: a capability was
reported ABSENT when it was present, or a board tool was aimed at a
library. Both produce a confident wrong answer rather than an error, so
neither is self-correcting.

The three answers a caller needs are kept distinct, because collapsing
them is what makes the wrong ones convincing:

* here is the tool, and here is what it needs first
* the tool you were reaching for acts on a DIFFERENT document
* this is genuinely not possible, and here is why

The third matters as much as the first. Without it there is no way to
distinguish "you missed it" from "it does not exist", so the same dead
end gets investigated again every time it comes up.

Tool names here are checked against the live surface by
``tests/test_tool_guide_names_real_tools.py``. A recipe that names a
tool which has been renamed or dropped fails there rather than sending
a caller somewhere empty.
"""

from __future__ import annotations

import re
from typing import Any, Optional

#: Document kinds a recipe can apply to. ``any`` means it does not turn
#: on which document is in front.
DOCUMENT_KINDS = ("library", "board", "schematic", "project", "any")


#: Each recipe: what you are trying to do, the tools that do it, and the
#: tools that LOOK like they do it but act on another document.
#:
#: ``avoid`` is the load-bearing field. Naming the right tool only helps
#: a caller who is already looking here; naming the wrong one by the
#: reason it is wrong is what makes a recipe findable from the mistake.
_RECIPES: tuple[dict[str, Any], ...] = (
    {
        "task": "Delete primitives inside a footprint",
        "keywords": ("delete", "primitive", "footprint", "silkscreen",
                     "track", "clear", "library"),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["lib_delete_footprint_primitives"],
        "avoid": [
            ("obj_delete", "resolves a BOARD, not the library in front"),
            ("pcb_delete_object", "resolves a BOARD, not the library"),
        ],
        "note": (
            "Both board tools open the first PcbDoc an open project holds "
            "when none is focused, so aimed at a library they do not fail. "
            "They remove primitives from a board you never named. Pads are "
            "excluded unless include_pads is set, since removing one "
            "changes the pinout rather than the drawing."),
    },
    {
        "task": "Set mechanical layer names and kinds on a BOARD",
        "keywords": ("mechanical", "layer", "mechkind", "kind", "rename",
                     "pair", "paired", "board"),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["pcb_get_mech_layer_names", "pcb_set_mech_layers",
                "pcb_set_mech_layer_kind"],
        "avoid": [
            ("lib_set_mech_layers", "refuses a PcbDoc: it is the LIBRARY "
                                    "one. Use pcb_set_mech_layers here"),
        ],
        "note": (
            "Read the board first with pcb_get_mech_layer_names: it reports "
            "what each layer actually HOLDS, which a PcbDoc header does not "
            "carry, so kinds can be moved with the geometry in view. Paired "
            "kinds come from a SECOND enumeration with no side suffix and "
            "are written on the pair, not on either layer. Writing a paired "
            "kind as though it were a layer kind silently does nothing."),
    },
    {
        "task": "Set mechanical layer names and kinds in a LIBRARY",
        "keywords": ("mechanical", "layer", "mechkind", "kind", "rename",
                     "pair", "paired", "library", "pcblib"),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["lib_set_mech_layers", "lib_run_across"],
        "avoid": [
            ("pcb_set_mech_layers", "acts on a BOARD. In a library use "
                                    "lib_set_mech_layers"),
        ],
        "note": (
            "lib_run_across applies the same layer setup to every library "
            "in a folder in one call. Paired kinds are written on the pair "
            "rather than on either layer here too."),
    },
    {
        "task": "Delete a parameter from a library symbol",
        "keywords": ("parameter", "delete", "remove", "symbol", "schlib",
                     "library"),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["obj_delete"],
        "avoid": [],
        "note": (
            "Scope it with lib_component:NAME. A parameter is owned by its "
            "component, so a document-level delete is a silent no-op, and a "
            "SchLib holds no PLACED components for a component walk to "
            "find. Scoping resolves the symbol first, which is what makes "
            "the delete reach it."),
    },
    {
        "task": "Reach one part of a multi-part symbol",
        "keywords": ("multi-part", "multipart", "part", "gate", "section",
                     "symbol", "pins"),
        "document_kind": "library",
        "backends": ("altium",),
        "use": ["lib_get_pin_list", "obj_query", "sch_set_component_part_id"],
        "avoid": [],
        "note": (
            "Suffix the scope with @N, as lib_component:NAME@2. A SchLib "
            "iterator only ever yields the CURRENT part's primitives, so "
            "without the suffix every read returns part 1 no matter which "
            "part you meant."),
    },
    {
        "task": "Run DRC or ERC",
        "keywords": ("drc", "erc", "rules", "check", "violations"),
        "document_kind": "any",
        "backends": ("altium", "kicad", "easyeda"),
        "use": ["run_drc", "run_erc"],
        # Scoped: app_run_menu is an Altium tool, and offering it as a
        # trap on a backend that has no such tool teaches a distinction
        # that does not exist there.
        "avoid": [
            ("app_run_menu", "the menu path reports success for a run that "
                             "did nothing, and returns no violations",
             ("altium",)),
        ],
        "note": (
            "The dedicated tools validate the document context and return "
            "the violation list. run_drc and run_erc are the EDA-agnostic "
            "pair and work on every backend."),
    },
    {
        "task": "Suppress or restore stencil paste on Not-Fitted parts",
        "keywords": ("paste", "stencil", "dnp", "not-fitted", "notfitted",
                     "aperture", "variant", "restore"),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["audit_variant_not_fitted", "pcb_apply_dnp_paste_exclusion"],
        "avoid": [],
        "note": (
            "Restore refuses to guess: call it with the SAME designators you "
            "applied. Resolving the list from the current variant means you "
            "can exclude under one variant, switch, and leave a component "
            "that is fitted in the new one with its aperture still "
            "suppressed. Nothing reports that. The call succeeds, the pad "
            "looks ordinary in the editor, and the part comes back from "
            "assembly unsoldered."),
    },
    {
        "task": "Place a power port for a rail",
        "keywords": ("power", "port", "rail", "vcc", "gnd", "ground",
                     "orientation", "symbol"),
        "document_kind": "schematic",
        "backends": ("altium",),
        "use": ["sch_place_power_port", "audit_power_port_orientation"],
        "avoid": [],
        "note": (
            "Pass orientation explicitly for a rail drawn with style=bar. "
            "The style-based default groups bar and wave with the grounds, "
            "so a VCC bar comes out pointing DOWN and reads as a ground "
            "symbol. orientation=1 is what a rail wants."),
    },
    {
        "task": "Rename a PCB component designator",
        "keywords": ("designator", "rename", "annotate", "renumber"),
        "document_kind": "board",
        "backends": ("altium",),
        "use": ["proj_annotate"],
        "avoid": [
            ("obj_modify", "writing a designator on a PCB component from a "
                           "script CRASHES Altium"),
        ],
        "note": (
            "Annotation is the supported route and keeps the schematic and "
            "board in step, which a direct rename would not."),
    },
)


#: Proven not possible, with the reason. Kept separate from the recipes
#: because "no tool exists" and "no route exists" call for different
#: responses: the first is a gap worth filling, the second is a dead end
#: worth remembering.
_NOT_POSSIBLE: tuple[dict[str, Any], ...] = (
    {
        "capability": "Rename a PCB designator directly from a script",
        "backends": ("altium",),
        "why": "Writing the designator on a PCB component crashes Altium.",
        "do_instead": ["proj_annotate"],
    },
    {
        "capability": "Apply an ECO without a human click",
        "backends": ("altium",),
        "why": ("The Engineering Change Order dialog is non-suppressible by "
                "design. It can be launched, not completed."),
        "do_instead": [],
    },
    {
        "capability": "Use hierarchical sheets in a schematic",
        "backends": ("easyeda",),
        "why": ("There is no hierarchy. Schematic PAGES are siblings with "
                "an order, and no class exposes a sheet symbol, a sheet "
                "entry or a parent link. A netlist here is flat because "
                "nothing is nested, not because anything was flattened, so "
                "a report describing an EasyEDA design as hierarchical is "
                "wrong. Reuse happens through circuit blocks instead."),
        "do_instead": ["easyeda_list_schematic_pages",
                       "easyeda_search_circuit_blocks"],
    },
    {
        "capability": "Place a reusable circuit block onto a schematic",
        "backends": ("easyeda",),
        "why": ("Documented and not present. lib_Cbb is fully available at "
                "runtime, so blocks can be found and managed, but "
                "sch_PrimitiveComponent.placeCbbSchematicPage is absent "
                "from the captured surface of editor 2.2.47.7 even though "
                "the reference specifies it. A newer build may carry it: "
                "this is a build gap, not an API gap."),
        "do_instead": ["easyeda_search_circuit_blocks"],
    },
    {
        "capability": "Add or remove teardrops",
        "backends": ("easyeda",),
        "why": ("No teardrop method exists. Checked in BOTH sources, "
                "because neither alone is conclusive: absent from the "
                "official class reference, and absent from a 675-method "
                "runtime capture. The only mention anywhere is a gerber "
                "export option, which is a rendering choice rather than "
                "an edit to the board."),
        "do_instead": [],
    },
    {
        "capability": "Place thieving copper",
        "backends": ("easyeda",),
        "why": ("No thieving method in the class reference or in the "
                "675-method runtime capture."),
        "do_instead": [],
    },
    {
        "capability": "Tune track length with a serpentine",
        "backends": ("easyeda",),
        "why": ("No length-tuning method in either source. Length "
                "MATCHING exists as a CONSTRAINT through pcb_Drc's "
                "equal-length net groups, which is the rule rather than "
                "the detour: the editor can be told which nets must "
                "match, and cannot be asked to draw the serpentine that "
                "makes them."),
        "do_instead": ["easyeda_create_length_match_group",
                       "easyeda_add_nets_to_length_match_group"],
    },
    {
        "capability": "Create test points on the board",
        "backends": ("easyeda",),
        "why": ("Only the EXPORT exists: getTestPointFile writes the "
                "flying-probe file for whatever the board already has. "
                "Nothing creates a test point, in either source."),
        "do_instead": ["easyeda_export_test_points"],
    },
    {
        "capability": "Delete a whole project",
        "backends": ("easyeda",),
        "why": ("EasyEDA's API has no project delete. dmt_Project offers "
                "create, open, move and the info reads, and no other class "
                "deletes one. The document tree can remove a board, folder, "
                "panel, PCB, schematic or schematic page, never the project."),
        "do_instead": ["easyeda_delete_schematic", "easyeda_delete_pcb",
                       "easyeda_delete_panel"],
    },
    {
        "capability": "Run DRC or ERC over the IPC API",
        "backends": ("kicad",),
        "why": ("KiCad's IPC API does not expose them. They run through "
                "kicad-cli instead, which this server drives for you."),
        "do_instead": ["run_drc", "run_erc"],
    },
)


#: Words that carry no subject. Without this a question matches any
#: entry whose prose happens to contain "the", which turned "calibrate
#: the coffee machine" into a hit on an unrelated dead end. A guide that
#: answers questions it was not asked is worse than one that misses,
#: because the answer looks authoritative.
_STOPWORDS = frozenset((
    "the", "and", "for", "with", "from", "into", "onto", "that", "this",
    "how", "can", "you", "was", "are", "does", "did", "any", "all",
    "get", "set", "use", "using", "make", "want", "need", "should",
))


def _score(recipe: dict, task: str, document_kind: str) -> int:
    """How much of the question this recipe accounts for. 0 means no.

    Ranked rather than first-match: "delete a component parameter" in a
    library hits both the parameter recipe and the footprint-primitive
    one on the word "delete", and returning whichever comes first in the
    table produced a confident WRONG answer, which is the exact failure
    this module exists to prevent. The subject words have to outweigh
    the verb, so a keyword hit counts for more than a tool-name hit.
    """
    if document_kind and recipe["document_kind"] not in (document_kind, "any"):
        return 0
    if not task:
        return 1
    words = {w for w in task.lower().replace("-", " ").split()
             if len(w) > 2 and w not in _STOPWORDS}
    if not words:
        return 1
    keywords = " ".join(recipe["keywords"]).lower()
    title = recipe["task"].lower()
    tools = " ".join(recipe["use"]).lower()
    score = 0
    for w in words:
        if w in title:
            score += 3
        if w in keywords:
            score += 3
        elif w in tools:
            score += 1
    return score


def _matches(recipe: dict, task: str, document_kind: str) -> bool:
    return _score(recipe, task, document_kind) > 0


def avoid_entries(recipe: dict, backend: str = ""):
    """The ``avoid`` list for one recipe, narrowed to a backend.

    An entry may carry its own backend tuple as a third element. Without
    that narrowing a trap belonging to one editor is presented on every
    editor, which is the same wrong-tool-for-this-context error the
    guide exists to prevent.
    """
    out = []
    for entry in recipe["avoid"]:
        name, why = entry[0], entry[1]
        scope = entry[2] if len(entry) > 2 else recipe["backends"]
        if backend and backend not in scope:
            continue
        out.append({"tool": name, "why": why})
    return out


#: Phrases a docstring uses to send the reader somewhere else. Each is
#: a recipe that somebody already wrote next to the code, which is the
#: only place a recipe cannot go stale: the redirect and the tool it
#: redirects to are edited together or not at all.
_REDIRECT_MARKERS = (
    "use this rather than",
    "use this instead of",
    "prefer ",
    " instead of ",
    " rather than ",
    "this is the library tool",
    "acts on an open",
)

#: Written like a tool name. Used to pull the target out of a redirect
#: sentence rather than guessing which words are tools.
_TOOLNAME = re.compile(r"(?<![a-z0-9_])((?:app|lib|pcb|sch|obj|proj|design|audit|run|part"
                       r"|easyeda|kicad|sim|route|tool)_[a-z0-9_]+)")


def derived_recipes(task: str, docs: dict[str, str],
                    limit: int = 6) -> list[dict[str, Any]]:
    """Recipes read off the tool docstrings, for what the table misses.

    THE HAND-WRITTEN TABLE IS TEN ENTRIES AGAINST SEVEN HUNDRED TOOLS,
    and an empty answer from it reads as "no such capability" however
    carefully the note says otherwise. That reading has been made and
    reported more than once.

    Docstrings already carry the redirects: "use this rather than
    lib_link_3d_model", "this is the LIBRARY tool", "acts on an open
    .PcbDoc". They are maintained because they sit next to the code
    they describe, which is exactly what a separate list of ten is not.

    So a miss falls back to reading them. This finds fewer, vaguer
    answers than a curated recipe, and it finds them for every tool
    rather than for ten.
    """
    words = [w for w in re.split(r"[^a-z0-9]+", task.lower()) if len(w) > 2]
    if not words or not docs:
        return []

    out: list[tuple[int, str, dict[str, Any]]] = []
    for name, doc in docs.items():
        if not doc:
            continue
        low = doc.lower()
        # Score on the caller's words, in the name and in the text. The
        # name is weighted higher: a tool called pcb_place_3d_body is a
        # better answer to "place a 3d body" than one that mentions it.
        score = sum(3 for w in words if w in name.lower())
        score += sum(1 for w in words if w in low)
        if score <= 0:
            continue

        redirects: list[str] = []
        for line in doc.splitlines():
            ll = line.lower()
            if any(m in ll for m in _REDIRECT_MARKERS):
                for other in _TOOLNAME.findall(line):
                    if other != name and other not in redirects:
                        redirects.append(other)
        out.append((score, name, {
            "use": [name],
            "summary": (doc.strip().splitlines() or [""])[0].strip(),
            "see_also": redirects,
        }))

    out.sort(key=lambda t: (-t[0], t[1]))
    return [r for _s, _n, r in out[:limit]]


def guidance_for(task: str = "", document_kind: str = "",
                 backend: str = "",
                 docs: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """The recipes and dead ends matching one question. Pure, testable."""
    task = (task or "").strip()
    document_kind = (document_kind or "").strip().lower()
    backend = (backend or "").strip().lower()

    if document_kind and document_kind not in DOCUMENT_KINDS:
        return {"ok": False, "reason": (
            f"document_kind must be one of {', '.join(DOCUMENT_KINDS)}")}

    def on_backend(entry) -> bool:
        return not backend or backend in entry["backends"]

    ranked = sorted(
        ((_score(r, task, document_kind), i, r)
         for i, r in enumerate(_RECIPES) if on_backend(r)),
        key=lambda t: (-t[0], t[1]))
    recipes = []
    for score, _i, r in ranked:
        if score <= 0:
            continue
        # Copied, not mutated: the module-level table is shared by every
        # caller and narrowing it in place would leak one request's
        # backend filter into the next.
        recipes.append(dict(r, avoid=avoid_entries(r, backend)))
    scored_dead = []
    for i, d in enumerate(_NOT_POSSIBLE):
        if not on_backend(d):
            continue
        shim = {"task": d["capability"], "keywords": (),
                "use": d["do_instead"], "document_kind": "any"}
        score = _score(shim, task, "")
        if score > 0:
            scored_dead.append((score, i, d))
    scored_dead.sort(key=lambda t: (-t[0], t[1]))
    dead_ends = [d for _s, _i, d in scored_dead]

    # Only when the curated table missed. A hand-written recipe is a
    # better answer when there is one, and mixing the two would bury it.
    derived: list[dict[str, Any]] = []
    if not recipes and not dead_ends and task and docs:
        derived = derived_recipes(task, docs)

    return {
        "ok": True,
        "matched": len(recipes),
        "recipes": recipes,
        "not_possible": dead_ends,
        "derived": derived,
        # An empty result is a real answer and must not read as an error.
        # It means this file has nothing on the subject, NOT that the
        # server cannot do it: fall back to tool_catalog.
        "note": ("No curated recipe covers this. `derived` is read off "
                 "the tool docstrings and is the next best thing; "
                 "tool_catalog searches the whole surface."
                 if derived else
                 ("No recipe covers this; search the surface with "
                  "tool_catalog before concluding a tool does not exist."
                  if not recipes and not dead_ends else "")),
    }


def register_guidance_tools(mcp):
    """Register the guidance tool. Backend-agnostic, like the meta pair."""

    @mcp.tool()
    async def tool_guide(task: str = "", document_kind: str = "",
                         backend: str = "") -> dict[str, Any]:
        """How to do something correctly, and what is genuinely impossible.

        USE THIS BEFORE CONCLUDING A CAPABILITY IS MISSING. The recorded
        failures are not "could not find the tool name", which
        ``tool_catalog`` already solves. They are aiming a board tool at
        a library, and reporting a capability absent when it was
        present. Both return a confident wrong answer rather than an
        error.

        Answers three distinct things, kept apart on purpose: the tool
        and its prerequisites, the tool you were probably reaching for
        and why it acts on another document, and the short list of
        things that are proven impossible with the reason.

        An empty result means this guide has nothing on the subject. It
        is not evidence that the server cannot do it: fall back to
        ``tool_catalog``.

        Search here for help when a tool seems missing, when you are not
        sure which tool to use, when an operation seems not supported,
        or when a call failed because the wrong document was open.
        (``tool_catalog`` matches on this description, and searching the
        obvious word, help, used to return four other tools and not this
        one. The vocabulary of being stuck has to appear here, not only
        the vocabulary of the answer.)

        Args:
            task: What you are trying to do, in your own words, e.g.
                "delete silkscreen from a footprint". Matched on
                keywords, so a rough phrase is fine.
            document_kind: Narrow to one of library, board, schematic,
                project, any. Optional.
            backend: altium, kicad or easyeda. Optional; omit to see
                every backend's answer.

        Returns:
            Dict with ``recipes`` (each with ``use``, ``avoid`` and a
            ``note``), ``not_possible``, and ``matched``.
        """
        # The docstrings are read from the live registry rather than a
        # copy, so a tool renamed or re-described is reflected the moment
        # it is, with nothing to update here.
        docs: dict[str, str] = {}
        try:
            for spec in await mcp.list_tools():
                docs[spec.name] = getattr(spec, "description", "") or ""
        except Exception:            # noqa: BLE001
            docs = {}
        return guidance_for(task, document_kind, backend, docs)
