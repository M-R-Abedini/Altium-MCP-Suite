# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Validator, ERC + connectivity sanity report on the focused project.

Slice C.1 scope: schematic-only checks. Bundles three reads:

    project.get_messages      , compile + ERC violations
    generic.get_unconnected_pins, floating pins
    generic.run_erc           , ensure compile/ERC are fresh first

Returns a structured ValidationReport that the planner (Claude Code) reads,
classifies, and uses to revise the DesignPlan. Each Issue is small and
LLM-friendly (category, severity, target-refdes, target-pin, target-net,
text). PCB-side validation will be a later slice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("eda_agent.design.validator")


# Severity ranking, used to compute the report's overall passed flag.
_FATAL_SEVERITIES = {"error", "fatal"}


@dataclass
class Issue:
    """One thing the validator flags. Designed to be Claude-readable."""

    category: str  # e.g. "erc", "unconnected_pin", "compile_error"
    severity: str  # "error" | "warning" | "info"
    text: str
    refdes: Optional[str] = None
    pin: Optional[str] = None
    net: Optional[str] = None
    sheet: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "text": self.text,
            "refdes": self.refdes,
            "pin": self.pin,
            "net": self.net,
            "sheet": self.sheet,
        }


@dataclass
class ValidationReport:
    """The structured validator output."""

    passed: bool = True
    project_path: Optional[str] = None
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "project_path": self.project_path,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "notes": list(self.notes),
        }


def _classify_severity(raw: Any) -> str:
    if raw is None:
        return "info"
    s = str(raw).strip().lower()
    if s in {"error", "fatal", "critical"}:
        return "error"
    if s in {"warning", "warn"}:
        return "warning"
    return "info"


def _bucket(report: ValidationReport, issue: Issue) -> None:
    if issue.severity in _FATAL_SEVERITIES:
        report.errors.append(issue)
    elif issue.severity == "warning":
        report.warnings.append(issue)
    else:
        # Info-level messages still get attached so Claude sees compile noise
        #, bucket as warnings so it shows up in the readable list.
        report.warnings.append(issue)


def _ingest_messages(report: ValidationReport, raw: Any) -> None:
    """Map project.get_messages output into Issue records.

    The Pascal handler returns ``{messages: [...], count: N}`` where each
    message has ``message``, ``severity``, ``source``. We don't try to parse
    the source string into refdes/net here, Claude can read it raw and
    pattern-match if needed.
    """
    if not isinstance(raw, dict):
        return
    for msg in raw.get("messages", []):
        if not isinstance(msg, dict):
            continue
        text = str(msg.get("message", "")).strip()
        if not text:
            continue
        sev = _classify_severity(msg.get("severity"))
        # Heuristic category, anything mentioning "ERC" goes to erc, anything
        # mentioning "Compiler" goes to compile, otherwise generic.
        upper = text.upper()
        if "ERC" in upper:
            cat = "erc"
        elif "COMPIL" in upper:
            cat = "compile"
        else:
            cat = "message"
        _bucket(
            report,
            Issue(
                category=cat,
                severity=sev,
                text=text,
                sheet=str(msg.get("source") or "") or None,
            ),
        )


def _check_atomic_parts(plan: Any) -> list[Issue]:
    """Atomic-parts contract check.

    Returns a list of ``warning`` Issues, one per status='existing' Part
    that is missing any of mpn / footprint / datasheet_url. The contract
    follows the KiCad Atomic / Digi-Key Library / atopile convention:
    every existing symbol must carry MPN + footprint + datasheet URL
    bound at the part level so the BOM and PCB come out complete on the
    first pass.

    Accepts a ``DesignPlan`` (typed) or anything with a ``parts``
    iterable whose items expose ``validate_atomic()``. The duck-typing
    keeps the validator import-cheap; ``plan.py`` already owns the
    per-part rule.
    """
    issues: list[Issue] = []
    if plan is None:
        return issues
    parts = getattr(plan, "parts", None) or []
    for part in parts:
        validator = getattr(part, "validate_atomic", None)
        if not callable(validator):
            continue
        problems = validator()
        if not problems:
            continue
        refdes = getattr(part, "refdes", None)
        sheet = getattr(part, "sheet", None)
        # Collapse the per-field issues into a single Issue text. The
        # planner gets a clearer signal from "R1 has no mpn / footprint
        # / datasheet_url" than three near-identical warnings.
        missing = [p.split(" has no ", 1)[1] for p in problems if " has no " in p]
        if missing:
            text = (
                f"{refdes} has no {' / '.join(missing)}, will produce an "
                "incomplete BOM"
            )
        else:
            text = "; ".join(problems)
        issues.append(
            Issue(
                category="atomic_parts",
                severity="warning",
                text=text,
                refdes=refdes,
                sheet=sheet,
            )
        )
    return issues


def _ingest_unconnected_pins(report: ValidationReport, raw: Any) -> None:
    if not isinstance(raw, dict):
        return
    for pin in raw.get("unconnected_pins", []):
        if not isinstance(pin, dict):
            continue
        refdes = str(pin.get("designator", "")).strip() or None
        pin_id = str(pin.get("pin_number", "") or pin.get("pin_name", "")).strip() or None
        sheet = str(pin.get("sheet", "")).strip() or None
        text = (
            f"Unconnected pin {refdes}.{pin_id}"
            if refdes and pin_id
            else "Unconnected pin"
        )
        report.errors.append(
            Issue(
                category="unconnected_pin",
                severity="error",
                text=text,
                refdes=refdes,
                pin=pin_id,
                sheet=sheet,
            )
        )


def validate(
    project_path: Optional[str] = None,
    *,
    bridge: Optional[Any] = None,
    skip_erc: bool = False,
    plan: Optional[Any] = None,
) -> ValidationReport:
    """Run the validation pipeline.

    Args:
        project_path: Optional absolute path to a .PrjPcb. Omitted means
            the focused project.
        bridge: Optional bridge to use; defaults to the global one.
        skip_erc: If True, only read existing messages (no run_erc). Used
            by tests; in production the run_erc + read pattern is the
            intended flow.
        plan: Optional DesignPlan. When supplied, the atomic-parts
            contract check runs against its parts and emits a warning
            for every status='existing' part missing mpn / footprint /
            datasheet_url.

    Returns:
        ValidationReport with passed=True iff there are zero error-level
        issues.
    """
    report = ValidationReport(project_path=project_path)

    if bridge is None:
        from eda_agent.bridge import get_bridge  # late import, needs Altium
        bridge = get_bridge()

    project_param: dict[str, Any] = {}
    if project_path:
        project_param["project_path"] = project_path

    if not skip_erc:
        try:
            bridge.send_command("generic.run_erc", {})
        except Exception as exc:
            report.notes.append(f"run_erc failed: {exc}")

    try:
        messages = bridge.send_command("project.get_messages", project_param)
        _ingest_messages(report, messages)
    except Exception as exc:
        report.notes.append(f"get_messages failed: {exc}")

    try:
        unconnected = bridge.send_command("generic.get_unconnected_pins", {})
        _ingest_unconnected_pins(report, unconnected)
    except Exception as exc:
        report.notes.append(f"get_unconnected_pins failed: {exc}")

    # Atomic-parts contract: pure-Python, no Altium round-trip. Adds
    # warnings rather than errors, so a complete-electrically design
    # with a sloppy BOM still 'passes' overall, with a clear flag list.
    if plan is not None:
        for issue in _check_atomic_parts(plan):
            _bucket(report, issue)

    report.passed = len(report.errors) == 0
    return report
