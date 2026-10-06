# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Headless schematic review (roadmap V1: hardware CI).

Runs the component-level review checks that need no netlist: the ones a
reviewer flags first: directly on a parsed ``.SchDoc``, with no Altium and
no license.

**This is an opt-in fallback, not the preferred review path.** It parses
Altium's on-disk binaries without the application, so it only sees the
netlist-free subset (missing MPN / datasheet / manufacturer, placeholder
values, designator collisions, missing designators): it cannot compile a
netlist, run ERC, or judge connectivity. The live-Altium tools
(``design_lint_report``, ``proj_run_erc``, ``design_review_snapshot``, the
``audit_*`` family) run Altium's own engines and are always the right
choice when a session is available. The offline reader exists only for the
case where Altium genuinely can't be opened (a CI runner, a file on disk).

Because a parser reading undocumented binary framing can silently
misread a file, this surface is **disabled by default** and must be
explicitly enabled per use: see ``headless_review_enabled`` and the
``--offline`` CLI flag. Do not present it as the default way to review a
design.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .altium_sch import (
    read_schematic_components,
    read_schematic_document_info,
    read_schematic_nets,
)

# Severities, ordered.
ERROR = "error"
WARNING = "warning"
INFO = "info"

# Values that indicate an unfilled placeholder rather than a real value.
_PLACEHOLDERS = {
    "", "?", "??", "tbd", "todo", "value", "val", "xxx", "n/a", "na",
    "placeholder", "dnp", "changeme", "change me", "fixme",
}

# R/C/L are the IPC-standard reference designator prefixes for the passive
# families (resistor / capacitor / inductor). A real passive value always
# carries a numeric magnitude (10k, 100n, 4u7, 0R), so a digitless value on
# one of these is a defect, typically a library reference or description
# ("RES", "Capacitor") that leaked into the Value field. This is deliberately
# conservative: annotated values like "10k 1%" or "100n 0603" keep a digit
# and are NOT flagged, so it stays low-noise on real, messy schematics.
_PASSIVE_DESIGNATOR = re.compile(r"^[RCL]\d", re.IGNORECASE)


def _is_passive_designator(designator: str) -> bool:
    return bool(_PASSIVE_DESIGNATOR.match(designator.strip()))

# --- opt-in gate ------------------------------------------------------------
# The headless file-reader review is DISABLED BY DEFAULT and is never the
# preferred path (see the module docstring). Enable it only for the explicit
# no-Altium case: set this env var truthy (for the MCP tool) or pass the
# ``--offline`` flag on the CLI. The gate lives at the two exposed surfaces
# (the ``design_review_file`` tool and the ``eda-agent review`` command); the
# parsing functions below stay pure so they remain unit-testable.
HEADLESS_REVIEW_ENV = "EDA_AGENT_HEADLESS_REVIEW"
_TRUTHY = {"1", "true", "yes", "on"}

HEADLESS_DISABLED_MESSAGE = (
    "Headless file-reader review is disabled by default and is NOT the "
    "preferred way to review a design. It parses Altium's binaries with no "
    "running Altium and no license, so it only covers component-level checks: "
    "it cannot compile a netlist or run ERC, and an offline parser can "
    "misread a file. Prefer the live-Altium tools (design_lint_report, "
    "proj_run_erc, design_review_snapshot, the audit_* family). To use the "
    "offline fallback anyway, pass --offline on the CLI or set "
    f"{HEADLESS_REVIEW_ENV}=1 for the MCP tool."
)


def headless_review_enabled() -> bool:
    """True only if the offline file-reader review was explicitly opted in."""
    return os.environ.get(HEADLESS_REVIEW_ENV, "").strip().lower() in _TRUTHY


def _finding(check: str, severity: str, designator: str, message: str) -> dict:
    return {"check": check, "severity": severity,
            "designator": designator, "message": message}


def review_document_info(info: dict[str, Any]) -> list[dict]:
    """Flag incomplete title-block metadata (Title / Revision missing)."""
    findings: list[dict] = []
    if not (info.get("title") or "").strip():
        findings.append(_finding(
            "title_block_incomplete", INFO, "",
            "schematic has no Title in the title block"))
    if not (info.get("revision") or "").strip():
        findings.append(_finding(
            "title_block_incomplete", INFO, "",
            "schematic has no Revision in the title block"))
    return findings


def review_components(components: list[dict[str, Any]]) -> list[dict]:
    """Run the netlist-free review checks over a parsed component list."""
    findings: list[dict] = []

    # Designator collisions / missing / unannotated.
    seen: dict[str, int] = {}
    for c in components:
        d = (c.get("designator") or "").strip()
        if not d:
            findings.append(_finding(
                "missing_designator", ERROR, "",
                f"component {c.get('lib_reference', '?')!r} has no designator"))
            continue
        # Altium leaves "R?" / "C?" before annotation: an unannotated part
        # has no stable identity for the BOM or the netlist.
        if "?" in d:
            findings.append(_finding(
                "unannotated_designator", ERROR, d,
                f"{d} is unannotated (run annotation before release)"))
        seen[d] = seen.get(d, 0) + 1
    for d, n in sorted(seen.items()):
        if n > 1:
            findings.append(_finding(
                "designator_collision", ERROR, d,
                f"designator {d} used by {n} components"))

    # Duplicate UniqueID: a copy-paste artifact that breaks ECO / variant
    # tracking (each placed part must have a distinct UniqueID). Empty IDs
    # are ignored here: that's a different (and rarer) concern.
    uid_owners: dict[str, list[str]] = {}
    for c in components:
        uid = (c.get("unique_id") or "").strip()
        if uid:
            uid_owners.setdefault(uid, []).append(
                (c.get("designator") or "?").strip() or "?")
    for uid, owners in sorted(uid_owners.items()):
        if len(owners) > 1:
            findings.append(_finding(
                "duplicate_unique_id", ERROR, ",".join(sorted(owners)),
                f"UniqueID {uid} shared by {len(owners)} components "
                f"({', '.join(sorted(owners))}): breaks ECO/variant tracking"))

    # Per-component BOM / hygiene checks.
    for c in components:
        d = (c.get("designator") or "").strip() or "?"
        if not (c.get("mpn") or "").strip():
            findings.append(_finding(
                "missing_mpn", WARNING, d, f"{d} has no manufacturer part number"))
        if not (c.get("manufacturer") or "").strip():
            findings.append(_finding(
                "missing_manufacturer", INFO, d, f"{d} has no manufacturer"))
        if not (c.get("datasheet") or "").strip():
            findings.append(_finding(
                "missing_datasheet", WARNING, d, f"{d} has no datasheet link"))
        raw_val = (c.get("value") or "").strip()
        val = raw_val.lower()
        if val in _PLACEHOLDERS and val != "":
            findings.append(_finding(
                "placeholder_value", WARNING, d,
                f"{d} value looks like a placeholder: {c.get('value')!r}"))
        elif (raw_val and val not in _PLACEHOLDERS
                and _is_passive_designator(d)
                and not any(ch.isdigit() for ch in raw_val)):
            findings.append(_finding(
                "malformed_value", WARNING, d,
                f"{d} is a passive with a non-numeric value {c.get('value')!r} "
                f"(a library reference or description likely leaked into Value)"))

    return findings


# SARIF severity mapping (SARIF levels are error | warning | note).
_SARIF_LEVEL = {ERROR: "error", WARNING: "warning", INFO: "note"}

# Human-readable rule descriptions for the SARIF tool driver.
_RULE_HELP = {
    "missing_designator": "A component has no reference designator.",
    "unannotated_designator": "A designator still contains '?' (not annotated).",
    "designator_collision": "Two or more components share a designator.",
    "cross_sheet_designator_collision":
        "A designator names different physical parts on different sheets.",
    "duplicate_unique_id": "Two or more components share a UniqueID.",
    "title_block_incomplete": "The title block is missing Title or Revision.",
    "missing_mpn": "A component has no manufacturer part number.",
    "missing_manufacturer": "A component has no manufacturer.",
    "missing_datasheet": "A component has no datasheet link.",
    "placeholder_value": "A component value looks like an unfilled placeholder.",
    "malformed_value": "A passive (R/C/L) has a non-numeric value.",
    "single_pin_net": "A net has only one pin (unconnected).",
    "net_short": "One physical net carries two different declared names.",
}


def review_connectivity(solved: dict[str, Any]) -> list[dict]:
    """Connectivity (ERC-style) checks over a solved netlist.

    Consumes the output of ``netlist_solver.solve_nets`` /
    ``solve_schematic_nets`` (``{nets, pin_nets, name_conflicts}``) and
    returns findings:

    - ``single_pin_net`` (WARNING): a net with exactly one pin, so the pin
      connects to nothing. May be intentional (a no-connect, a test point,
      an unused gate input tied off elsewhere), so it is a warning, not an
      error.
    - ``net_short`` (ERROR): one physical net carries two different declared
      names (net labels / power ports): the named nets are shorted together.

    NOTE: soundness depends entirely on the netlist being correct. The
    geometric solver is validated against live Altium for wire/port/junction
    topology; run this on a topology the solver is trusted for. It is
    deliberately NOT auto-wired into the file review for that reason.
    """
    findings: list[dict] = []
    for name, members in sorted(solved.get("nets", {}).items()):
        if len(members) == 1:
            m = members[0]
            desig = f"{m['component']}.{m['pin']}"
            findings.append(_finding(
                "single_pin_net", WARNING, desig,
                f"{desig} is the only pin on net {name!r}: it connects to "
                f"nothing (verify it is an intentional no-connect / test point)"))
    for conf in solved.get("name_conflicts", []):
        names = ", ".join(conf.get("names", []))
        findings.append(_finding(
            "net_short", ERROR, "",
            f"one net carries conflicting names ({names}): these nets are "
            f"shorted together"))
    return findings


def review_library_components(components: list[dict[str, Any]]) -> list[dict]:
    """Library-hygiene checks over parsed .SchLib component headers."""
    findings: list[dict] = []
    for c in components:
        name = c.get("name") or c.get("lib_reference") or "?"
        ref = (c.get("lib_reference") or "").strip()
        if not (c.get("description") or "").strip():
            findings.append(_finding(
                "library_missing_description", WARNING, name,
                f"library part {name!r} has no description"))
        # Altium's default "Component_N" name means the part was never named.
        if re.fullmatch(r"Component_\d+", ref):
            findings.append(_finding(
                "placeholder_component_name", ERROR, name,
                f"library part {name!r} still has a default Component_N name"))
    return findings


def review_library_file(path: str | Path) -> dict[str, Any]:
    """Headless hygiene review of a .SchLib (component header level)."""
    from .altium_schlib import read_schlib_components

    components = read_schlib_components(path)
    findings = review_library_components(components)
    summary = {ERROR: 0, WARNING: 0, INFO: 0}
    for f in findings:
        summary[f["severity"]] = summary.get(f["severity"], 0) + 1
    return {
        "file": str(path),
        "component_count": len(components),
        "findings": findings,
        "summary": summary,
    }


def review_cross_sheet(components_by_sheet: dict[str, list[dict]]) -> list[dict]:
    """Project-level checks that span sheets (per-sheet review can't see them).

    ``cross_sheet_designator_collision`` (ERROR): the same reference
    designator names two or more DIFFERENT physical parts on different
    sheets. This is distinct from a legitimate multi-part component (a relay
    or dual op-amp placed as U1A / U1B across sheets), which shares ONE
    UniqueID, so the collision is only reported when the occurrences carry
    two or more distinct UniqueIDs. When UniqueIDs are absent a collision
    cannot be proven, so nothing is reported (kept conservative on purpose).
    """
    findings: list[dict] = []
    # designator -> {sheet_name: set(unique_ids)}  ('' = unknown UniqueID)
    by_desig: dict[str, dict[str, set]] = {}
    for sheet_name, comps in components_by_sheet.items():
        for c in comps:
            d = (c.get("designator") or "").strip()
            if not d or "?" in d:
                continue  # missing / unannotated handled per-sheet
            uid = (c.get("unique_id") or "").strip()
            by_desig.setdefault(d, {}).setdefault(sheet_name, set()).add(uid)

    for d, sheetmap in sorted(by_desig.items()):
        if len(sheetmap) < 2:
            continue  # single-sheet -> the per-sheet collision check owns it
        distinct_uids = {u for uids in sheetmap.values() for u in uids if u}
        if len(distinct_uids) >= 2:
            involved = sorted(sheetmap)
            findings.append({
                "check": "cross_sheet_designator_collision",
                "severity": ERROR,
                "designator": d,
                "message": (
                    f"designator {d} names {len(distinct_uids)} different "
                    f"physical parts across sheets {', '.join(involved)} "
                    f"(distinct UniqueIDs): breaks ECO / the BOM"),
                "sheet": involved[0],
            })
    return findings


def review_project_file(path: str | Path) -> dict[str, Any]:
    """Review every schematic sheet of a .PrjPcb project (headless).

    Resolves the project's .SchDoc sheets (via the .PrjPcbStructure),
    reviews each, and aggregates. Returns ``{file, sheet_count, sheets:[
    per-sheet report], findings, summary}`` where ``findings`` carries a
    ``sheet`` key per finding. Falls back to reviewing ``path`` directly if
    it is itself a .SchDoc.
    """
    from .altium_project import read_project_sheets

    path = Path(path)
    if path.suffix.lower() == ".schdoc":
        return review_schematic_file(path)
    if path.suffix.lower() == ".schlib":
        return review_library_file(path)

    sheets = read_project_sheets(path)
    all_findings: list[dict] = []
    sheet_reports: list[dict] = []
    components_by_sheet: dict[str, list[dict]] = {}
    for sheet in sheets:
        rep = review_schematic_file(sheet)
        sheet_reports.append(rep)
        components_by_sheet[sheet.name] = read_schematic_components(sheet)
        for f in rep["findings"]:
            all_findings.append({**f, "sheet": sheet.name})

    # Project-level checks that no single sheet can see.
    all_findings.extend(review_cross_sheet(components_by_sheet))

    summary = {ERROR: 0, WARNING: 0, INFO: 0}
    for f in all_findings:
        summary[f["severity"]] = summary.get(f["severity"], 0) + 1
    return {
        "file": str(path),
        "sheet_count": len(sheets),
        "component_count": sum(r["component_count"] for r in sheet_reports),
        "sheets": sheet_reports,
        "findings": all_findings,
        "summary": summary,
    }


def to_sarif(report: dict[str, Any], *, tool_version: str = "") -> dict[str, Any]:
    """Convert a review report to SARIF 2.1.0 (GitHub code-scanning format).

    Emitting SARIF lets the review post inline annotations on a pull
    request via GitHub's ``upload-sarif`` action: the adoption path for
    "every commit gets a design review".
    """
    findings = report.get("findings", [])
    rule_ids = sorted({f["check"] for f in findings} | set(_RULE_HELP))
    rules = [
        {"id": rid,
         "shortDescription": {"text": _RULE_HELP.get(rid, rid)},
         "helpUri": "https://github.com/salitronic/eda-agent"}
        for rid in rule_ids
    ]
    default_uri = str(report.get("file", "")).replace("\\", "/")
    results = []
    for f in findings:
        # Per-sheet findings (project review) point at their own sheet file.
        uri = str(f.get("sheet") or default_uri).replace("\\", "/")
        results.append({
            "ruleId": f["check"],
            "level": _SARIF_LEVEL.get(f["severity"], "warning"),
            "message": {"text": f["message"]},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": uri},
                },
            }],
        })
    driver = {"name": "eda-agent", "informationUri":
              "https://github.com/salitronic/eda-agent", "rules": rules}
    if tool_version:
        driver["version"] = tool_version
    return {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [{"tool": {"driver": driver}, "results": results}],
    }


def review_schematic_file(
    path: str | Path, *, check_connectivity: bool = False
) -> dict[str, Any]:
    """Parse a .SchDoc and return a structured headless review report.

    Returns ``{file, component_count, findings, summary}`` where summary
    counts findings by severity. Deterministic; no Altium.

    ``check_connectivity`` (default off): also run the geometric net solver
    and add connectivity findings (``single_pin_net``, ``net_short``). It is
    OPT-IN because the solver is validated against live Altium for wire /
    port / junction / by-name topology but not yet for every net-label edge
    case: enabling it on an unvalidated board could add false findings. A
    solver error degrades gracefully (component checks still return).
    """
    components = read_schematic_components(path)
    nets = read_schematic_nets(path)
    doc_info = read_schematic_document_info(path)
    findings = review_components(components) + review_document_info(doc_info)
    if check_connectivity:
        try:
            from .netlist_solver import solve_schematic_nets
            findings += review_connectivity(solve_schematic_nets(path))
        except (ValueError, OSError, KeyError):
            pass  # never let a solver hiccup break the component review
    summary = {ERROR: 0, WARNING: 0, INFO: 0}
    for f in findings:
        summary[f["severity"]] = summary.get(f["severity"], 0) + 1
    return {
        "file": str(path),
        "document": doc_info,
        "component_count": len(components),
        # Declared net names (labels + power ports): informational inventory,
        # not the compiled netlist. No single-use "typo" heuristic here: on a
        # single sheet a once-used label legitimately names a local net.
        "net_names": [n["name"] for n in nets],
        "findings": findings,
        "summary": summary,
    }
