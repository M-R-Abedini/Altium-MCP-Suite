# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The closed string vocabularies agree across the Python/Pascal bridge.

The Altium half of the vocabulary contract (the EasyEDA half lives in
test_easyeda_command_contract.py). Same two directions, same failure
modes:

1. DOCSTRING vs PASCAL. A caller reads the docstring; the DelphiScript
   parse chain decides. A documented word the chain does not accept
   falls into the chain's silent fallback (passive pin, rounded pad),
   which is worse than a refusal: the part is subtly wrong and nothing
   says so.
2. GENERATOR vs PASCAL. symbol_gen / footprint_gen emit these words
   with nobody reading them in between; an unaccepted word turns every
   generated pin or pad into the fallback.

Plus one lesson this file exists to keep fixed: the single-pin handler
carried its own INLINE copy of the electrical chain, case- and
underscore-sensitive with no aliases, while the batch path used the
shared StrToPinElectrical. 'Input' made a passive pin on one path and
an input pin on the other. The chain now lives once, in Utils.pas, and
a test below fails if an inline copy ever comes back.

Layer names are deliberately NOT covered: GetLayerFromString resolves
against Altium's own String2Layer, so there is no list of ours to
disagree with.
"""
from __future__ import annotations

import pathlib
import re

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "altium"


def _pascal(name: str) -> str:
    return (_SCRIPTS / name).read_text(encoding="latin-1")


def _pascal_all() -> str:
    """Every unit, concatenated, for functions whose file is not the point.

    A helper can legitimately move between units: DelphiScript has no
    forward declarations, so a function called from an earlier unit has
    to be defined in one earlier still. Naming the file here couples
    this guard to a layout decision it does not care about, and the
    failure it produces then reads as a missing vocabulary rather than
    a relocated one. Searching every unit keeps the real protection,
    which is that the function must exist SOMEWHERE and its accepted
    words must match what the docstrings promise.
    """
    return "\n".join(_pascal(p.name) for p in sorted(_SCRIPTS.glob("*.pas")))


def _function_body(source: str, name: str) -> str:
    """The text of one Pascal function, from its header to the next
    top-level Function/Procedure header."""
    match = re.search(
        rf"^Function {name}\b.*?(?=^(?:Function|Procedure)\s)",
        source, re.MULTILINE | re.DOTALL)
    assert match, f"{name} not found; the vocabulary moved and this " \
                  f"file guards a remnant"
    return match.group(0)


def _normalize(word: str) -> str:
    return word.lower().replace("_", "")


# ---------------------------------------------------------------- electrical

#: What lib_add_pins / lib_add_pin document, verbatim.
DOCUMENTED_ELECTRICAL = (
    "input", "output", "bidirectional", "passive", "open_collector",
    "open_emitter", "power", "hiz", "io",
)


def _accepted_electrical() -> set[str]:
    body = _function_body(_pascal("Utils.pas"), "StrToPinElectrical")
    accepted = set(re.findall(r"LS = '([a-z]+)'", body))
    assert accepted, "no accepted words parsed out of StrToPinElectrical"
    # The fallback IS the acceptance of 'passive' (and of any unknown
    # word, which is the documented semantics).
    assert "Result := eElectricPassive" in body.split("Else")[-1], (
        "StrToPinElectrical lost its passive fallback")
    return accepted


def test_every_documented_electrical_word_is_accepted():
    accepted = _accepted_electrical()
    missing = [w for w in DOCUMENTED_ELECTRICAL
               if w != "passive" and _normalize(w) not in accepted]
    assert not missing, (
        f"documented electrical types {missing} are not accepted by "
        "StrToPinElectrical; they would silently become passive pins")


def test_the_docstring_actually_documents_that_vocabulary():
    """The DOCUMENTED_ELECTRICAL table above must mirror the real
    docstring, or this file drifts from what callers read."""
    from tests.test_altium_no_raise import _TOOLS
    doc = _TOOLS["lib_add_pins"].__doc__ or ""
    for word in DOCUMENTED_ELECTRICAL:
        assert word in doc, (
            f"lib_add_pins no longer documents electrical type {word!r}; "
            "update DOCUMENTED_ELECTRICAL to match the docstring")


def test_read_back_electrical_words_can_be_resent():
    """PinElectricalToStr's output feeds callers who copy values into
    their next write; every word it emits must round-trip."""
    accepted = _accepted_electrical()
    body = _function_body(_pascal("Utils.pas"), "PinElectricalToStr")
    emitted = set(re.findall(r"Result := '([a-z_]+)'", body))
    assert emitted, "no emitted words parsed out of PinElectricalToStr"
    stuck = [w for w in emitted
             if w != "passive" and _normalize(w) not in accepted]
    assert not stuck, (
        f"PinElectricalToStr emits {stuck}, which StrToPinElectrical "
        "does not accept; a read-modify-write flips those pins to "
        "passive")


def test_the_electrical_chain_is_stated_once():
    """No handler carries an inline copy of the electrical chain.

    The inline copy in Lib_AddPin was case-sensitive and alias-free
    while the batch path was not, so the same word produced different
    pins depending on the tool. Library.pas must PARSE via the shared
    helper and never compare ElecType against a literal itself.
    """
    library = _pascal("Library.pas")
    assert "StrToPinElectrical(ElecType)" in library, (
        "Lib_AddPin no longer routes through StrToPinElectrical")
    inline = re.findall(r"ElecType = '[a-z_]+'", library)
    assert not inline, (
        f"an inline electrical comparison chain is back: {inline[:3]}. "
        "Route through StrToPinElectrical in Utils.pas instead")


# ---------------------------------------------------------------- pad shape

#: What lib_add_footprint_pad / lib_add_footprint_pads document.
DOCUMENTED_SHAPES = ("round", "rectangular", "octagonal", "roundrect")


def _accepted_shapes() -> set[str]:
    library = _pascal("Library.pas")
    accepted = set(re.findall(r"Shape = '([a-z_]+)'", library))
    assert accepted, "no accepted words parsed out of the Shape chains"
    assert "Else Pad.TopShape := eRounded" in library, (
        "the pad-shape chains lost their rounded fallback")
    return accepted


def test_every_documented_shape_is_accepted_or_the_fallback():
    accepted = _accepted_shapes()
    missing = [s for s in DOCUMENTED_SHAPES
               if s != "round" and s not in accepted]
    assert not missing, (
        f"documented pad shapes {missing} are not accepted; they would "
        "silently become rounded pads. ('round' itself IS the fallback.)")


def test_generated_footprints_emit_only_documented_shapes():
    """footprint_gen's output feeds the pad tools with nobody reading
    the words in between."""
    from eda_agent.design.footprint_gen import generate_footprint

    emitted: set[str] = set()
    for geom in (
        generate_footprint("chip", 2, pitch=40, pad_w=24, pad_h=28),
        generate_footprint("quad", 32, pitch=20, pad_w=12, pad_h=60,
                           row_span=350),
        generate_footprint("dual", 8, pitch=100, pad_w=60, pad_h=60,
                           row_span=300, hole=32),
    ):
        emitted.update(p.get("shape", "") for p in geom.pads
                       if p.get("shape"))
    unknown = [s for s in emitted if s not in DOCUMENTED_SHAPES]
    assert not unknown, (
        f"footprint_gen emits pad shapes {unknown} that are not in the "
        "documented vocabulary; on the Pascal side they fall back to "
        "rounded")


# ---------------------------------------------------------------- obj types

def test_every_object_type_the_docstrings_name_is_accepted():
    """The obj tool docstrings teach by example ('eJunction', 'eWire').
    An example that the Pascal chain does not accept teaches a call
    that matches nothing."""
    from tests.test_altium_no_raise import _TOOLS

    everything = _pascal_all()
    # The chains compare against NORMALISED words now (lowercase, no
    # separators, no leading "e"), so a caller writing "Sheet Symbol" or
    # "sheetsymbol" resolves the same as "eSheetSymbol". Parsing the
    # normalised form and normalising the docstring word is what keeps
    # this checking the real relationship rather than the spelling.
    sch = set(re.findall(r"N = '(\w+)'",
                         _function_body(everything, "ObjectTypeFromString")))
    pcb = set(re.findall(r"N = '(\w+)'",
                         _function_body(everything,
                                        "ObjectTypeFromStringPCB")))
    accepted = sch | pcb
    assert accepted, "no object types parsed out of the Pascal chains"

    def _normalise(word: str) -> str:
        """Mirror of NormalizeTypeName in Utils.pas."""
        w = word.lower().replace(" ", "").replace("_", "").replace("-", "")
        return w[1:] if len(w) > 1 and w.startswith("e") else w

    unknown: list[str] = []
    for tool in ("obj_query", "obj_delete", "obj_batch_delete",
                 "obj_modify", "obj_count", "obj_select"):
        doc = (_TOOLS.get(tool) or (lambda: None)).__doc__ or ""
        for word in set(re.findall(r'"(e[A-Z]\w+)"', doc)):
            if _normalise(word) not in accepted:
                unknown.append(f"{tool}: {word}")
    assert not unknown, (
        f"docstring examples name object types the Pascal never "
        f"accepts: {unknown}")


def test_the_spellings_a_caller_actually_used_are_accepted():
    """Measured on a live board, not imagined.

    One session asked for "Component", then "Sheet", then "Sheet Symbol",
    then "SheetSymbol", and every one came back INVALID_TYPE with no
    indication of what would have worked. Three of those four resolve
    now. "Sheet" deliberately does not, because a sheet is a document
    rather than an object on one, and the refusal says so.
    """
    everything = _pascal_all()
    sch = set(re.findall(r"N = '(\w+)'",
                         _function_body(everything, "ObjectTypeFromString")))
    pcb = set(re.findall(r"N = '(\w+)'",
                         _function_body(everything,
                                        "ObjectTypeFromStringPCB")))
    accepted = sch | pcb

    def _normalise(word: str) -> str:
        w = word.lower().replace(" ", "").replace("_", "").replace("-", "")
        return w[1:] if len(w) > 1 and w.startswith("e") else w

    for spelling in ("Component", "component", "Sheet Symbol", "SheetSymbol",
                     "sheet_symbol", "eSheetSymbol", "pin", "Net Label",
                     "track", "Polygon", "component_body"):
        assert _normalise(spelling) in accepted, (
            f"{spelling!r} still does not resolve. A caller who spells a "
            f"type the obvious way gets INVALID_TYPE and no list of what "
            f"would have worked.")

    # And the one that must NOT silently resolve, because accepting it
    # would return nothing and look like an empty board.
    assert _normalise("Sheet") not in accepted, (
        "'Sheet' resolves to an object type. A sheet is a document; "
        "accepting it returns an empty result that reads as a clean board")
