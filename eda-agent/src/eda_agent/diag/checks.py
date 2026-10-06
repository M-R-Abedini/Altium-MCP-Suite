# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Common types for diagnostic checks.

Each check is a function returning a ``Check`` record. Health and doctor
just compose lists of checks; the printer is shared.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    WARN = "warn"


class Severity(str, Enum):
    """How fatal is a FAIL on this check.

    A 'critical' fail makes the whole run exit non-zero.
    A 'minor' fail still exits zero, useful for nice-to-have signals.
    """
    CRITICAL = "critical"
    MINOR = "minor"


@dataclass
class Check:
    name: str
    status: Status
    message: str = ""
    fix: Optional[str] = None
    severity: Severity = Severity.CRITICAL
    details: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in (Status.PASS, Status.SKIP, Status.WARN)


def format_report(checks: list[Check], title: str = "") -> str:
    """Render a simple text report for stdout."""
    lines: list[str] = []
    if title:
        lines.append(title)
        lines.append("=" * len(title))
        lines.append("")

    icons = {
        Status.PASS: "OK  ",
        Status.FAIL: "FAIL",
        Status.SKIP: "SKIP",
        Status.WARN: "WARN",
    }
    for c in checks:
        head = f"  [{icons[c.status]}] {c.name}"
        if c.message:
            head += f", {c.message}"
        lines.append(head)
        if c.status == Status.FAIL and c.fix:
            lines.append(f"           fix: {c.fix}")

    fails = [c for c in checks if c.status == Status.FAIL and c.severity == Severity.CRITICAL]
    warns = [c for c in checks if c.status == Status.WARN]
    skips = [c for c in checks if c.status == Status.SKIP]
    passes = [c for c in checks if c.status == Status.PASS]
    lines.append("")
    lines.append(
        f"  {len(passes)} passed, {len(warns)} warn, {len(skips)} skipped, "
        f"{len(fails)} critical fail"
    )
    return "\n".join(lines)


def overall_exit_code(checks: list[Check]) -> int:
    """0 if no critical failures, 1 otherwise."""
    for c in checks:
        if c.status == Status.FAIL and c.severity == Severity.CRITICAL:
            return 1
    return 0
