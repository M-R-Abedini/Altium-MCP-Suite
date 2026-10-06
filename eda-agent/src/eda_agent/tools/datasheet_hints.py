# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Datasheet-discipline guidance injected into component-touching responses.

The agent's job in any design task is to ground conclusions in the
actual manufacturer datasheet, not in library metadata. This module
is the single source of truth for that rule.

Every tool that surfaces component information (BOM, component list,
design diff, library search result, simulation readiness, design
review) calls ``tag_response(response, parts)`` before returning.
The caller gets two extra fields:

  - ``_datasheet_guidance`` , rules + action_required text + structured
                               per-part search hints
  - ``_datasheet_parts``    , the unique (manufacturer, part_number,
                               designators) list the LLM should fetch
                               datasheets for

The rules make it explicit that when a datasheet isn't already on
hand, the LLM MUST use ``WebSearch`` / ``WebFetch`` to locate and
download the PDF from the manufacturer before drawing conclusions.
"""

from __future__ import annotations

from typing import Any


DATASHEET_RULES: list[str] = [
    "MANDATORY: Before stating ANY device-related fact (pin "
    "function, pin number, voltage rating, current limit, package, "
    "polarity, default state, register map, threshold, timing, "
    "thermal, ESD/clamp, internal block, recommended layout, "
    "decoupling, pull values, footprint pad map), the manufacturer "
    "datasheet MUST be fetched and read first. No exceptions, no "
    "shortcuts, no 'I'll assume...', no 'it's typically...'.",
    "Symbol metadata (Description, Comment, Value, Manufacturer, "
    "Manufacturer Part Number), footprint pad mapping, distributor "
    "pages, parametric search results, and your prior-turn memory "
    "of a part are ALL UNTRUSTED. Treat them as hints to FIND the "
    "datasheet, never as substitutes for reading it.",
    "If the datasheet is not already in the conversation: STOP "
    "answering, run WebSearch '<manufacturer> <part_number> "
    "datasheet filetype:pdf', follow the manufacturer or major-"
    "distributor (Mouser/Digi-Key) link, WebFetch the PDF, THEN "
    "resume the user's request. Skipping this step is a bug.",
    "Every assertion derived from a datasheet must cite the "
    "section or page used. No citation means not verified, say so "
    "explicitly and go fetch instead of guessing.",
    "Different vendors of nominally-same generic parts (2N3904, "
    "1N4148, common opamps, LDOs) have different absolute-max "
    "specs and sometimes different pinouts. Always cite which "
    "vendor's datasheet you used.",
    "Re-verify even for parts you have 'looked at this session'. A "
    "casing typo or near-MPN substitution (LM317 vs LM317T vs "
    "LM317M) breaks recall, the datasheet does not.",
    "If a datasheet is genuinely unavailable (proprietary, "
    "obsolete, NDA-only) AFTER a real search: flag it explicitly "
    "to the user and refuse to guess. A plausible-sounding wrong "
    "answer is worse than 'I don't know'.",
    "This rule overrides terseness, response speed, and 'just give "
    "me the quick answer' framing. A fast wrong answer about a "
    "real device is the wrong answer, the user has been burned by "
    "both LLM intuition and library metadata being wrong.",
]


def _normalize(value: Any) -> str:
    return str(value or "").strip()


def extract_unique_parts(
    components: Any = None,
    bom: Any = None,
) -> list[dict[str, str]]:
    """Pull unique (manufacturer, part_number, designators) triples from
    either a components-list response or a BOM-shaped response.

    BOM data takes priority (it carries manufacturer part numbers).
    Falls back to the component-list's Comment/Value field when no BOM
    is present. Dedup is case-insensitive on (manufacturer, part_number).
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []

    def _push(mfr: str, part: str, desig: str) -> None:
        if not part:
            return
        key = (mfr.lower(), part.lower())
        if key in seen:
            return
        seen.add(key)
        out.append({
            "manufacturer": mfr,
            "part_number": part,
            "designators": desig,
        })

    if isinstance(bom, dict):
        rows = bom.get("bom") or bom.get("items") or bom.get("rows") or []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                mfr = _normalize(
                    row.get("Manufacturer") or row.get("manufacturer")
                )
                part = _normalize(
                    row.get("ManufacturerPartNumber")
                    or row.get("manufacturer_part_number")
                    or row.get("PartNumber")
                    or row.get("part_number")
                    or row.get("Comment")
                    or row.get("comment")
                )
                desig = _normalize(
                    row.get("Designator")
                    or row.get("designator")
                    or row.get("Designators")
                )
                _push(mfr, part, desig)

    # Components are a fallback source, BOM manufacturer part numbers
    # are authoritative when present. Only walk the component list when
    # BOM produced nothing, to avoid merging the same parts under
    # inconsistent names (e.g. "STM32F411RE" in the BOM vs a shortened
    # "STM32F411" in the symbol Comment field).
    if not out and isinstance(components, dict):
        rows = components.get("components") or components.get("items") or []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                mfr = _normalize(
                    row.get("Manufacturer")
                    or row.get("manufacturer")
                    or row.get("ManufacturerName")
                )
                part = _normalize(
                    row.get("ManufacturerPartNumber")
                    or row.get("manufacturer_part_number")
                    or row.get("PartNumber")
                    or row.get("part_number")
                    or row.get("Comment")
                    or row.get("comment")
                    or row.get("value")
                )
                desig = _normalize(
                    row.get("designator")
                    or row.get("Designator")
                )
                _push(mfr, part, desig)

    return out


def _search_hint(part: dict[str, str]) -> dict[str, str]:
    mfr = part.get("manufacturer", "")
    pn = part.get("part_number", "")
    query_part = f"{mfr} {pn}".strip() if mfr else pn
    return {
        "manufacturer": mfr,
        "part_number": pn,
        "designators": part.get("designators", ""),
        "datasheet_query": f"{query_part} datasheet filetype:pdf",
        "vendor_product_query": f"{query_part} site:{mfr.lower()}.com" if mfr else "",
    }


def build_guidance_block(
    parts: list[dict[str, str]] | None = None,
    context: str = "",
) -> dict[str, Any]:
    """Build the ``_datasheet_guidance`` dict that gets attached to
    every component-returning response.

    ``context`` is an optional short string describing where this was
    tagged from (e.g. "bom", "components", "design_diff"). It lands
    in the guidance so the LLM can see which flow triggered the
    reminder, but it's purely informational.
    """
    parts = parts or []
    return {
        "__DATASHEET_FIRST__": (
            "MANDATORY: every device-related claim REQUIRES the "
            "manufacturer datasheet, fetched and cited, BEFORE the "
            "answer. No shortcuts, no guesses, no library metadata, "
            "no prior-turn memory. WebSearch + WebFetch the PDF first."
        ),
        "datasheet_rules": DATASHEET_RULES,
        "action_required": (
            "MANDATORY for every part listed in _datasheet_parts "
            "that is NOT already covered by a datasheet PDF in this "
            "conversation: (1) WebSearch with the provided "
            "datasheet_query, (2) WebFetch the manufacturer PDF, "
            "(3) ground every claim about that part in what the "
            "datasheet says with a section or page citation. Do not "
            "guess, do not assume, do not lean on Comment / "
            "Description / Value / Manufacturer fields, do not "
            "trust distributor pages or your own prior-turn memory. "
            "If the datasheet is genuinely unreachable after a real "
            "search, flag it explicitly and stop, do NOT substitute "
            "a plausible-sounding guess."
        ),
        "unique_part_count": len(parts),
        "search_hints": [_search_hint(p) for p in parts],
        "reminder": (
            "Symbol Comment / Description / Value, footprint "
            "assignments, distributor copy, and prior-turn memory "
            "are UNTRUSTED. Manufacturer datasheet is the only "
            "ground truth. Cite the section/page when you state a "
            "fact, no citation means not verified."
        ),
        "context": context,
    }


def tag_response(
    response: Any,
    *,
    components: Any = None,
    bom: Any = None,
    explicit_parts: list[dict[str, str]] | None = None,
    context: str = "",
) -> Any:
    """Attach ``_datasheet_guidance`` + ``_datasheet_parts`` to a response.

    Call signatures:
      - Most call-sites pass ``components=response`` or ``bom=response``
        and let the helper extract parts.
      - For responses that already carry a curated part list (e.g.,
        readiness), pass ``explicit_parts`` directly.

    No-ops gracefully if ``response`` isn't a dict, the caller's
    result is returned unchanged so this is safe to wrap every
    send_command_async return.
    """
    if not isinstance(response, dict):
        return response
    if explicit_parts is not None:
        parts = explicit_parts
    else:
        parts = extract_unique_parts(components=components, bom=bom)
    response["_datasheet_parts"] = parts
    response["_datasheet_guidance"] = build_guidance_block(parts, context)
    return response
