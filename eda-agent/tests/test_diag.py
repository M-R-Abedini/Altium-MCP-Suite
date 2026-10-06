# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the diag (health/doctor) subcommands."""

from __future__ import annotations

from eda_agent.diag.checks import (
    Check,
    Severity,
    Status,
    format_report,
    overall_exit_code,
)


def test_format_report_groups_pass_fail_warn() -> None:
    checks = [
        Check(name="alpha", status=Status.PASS, message="hi"),
        Check(
            name="beta",
            status=Status.FAIL,
            message="broken",
            fix="reinstall",
            severity=Severity.CRITICAL,
        ),
        Check(name="gamma", status=Status.WARN, message="meh"),
        Check(name="delta", status=Status.SKIP, message="n/a"),
    ]
    report = format_report(checks, title="x")
    assert "[OK  ] alpha" in report
    assert "[FAIL] beta" in report
    assert "fix: reinstall" in report
    assert "[WARN] gamma" in report
    assert "[SKIP] delta" in report
    assert "1 passed" in report
    assert "1 critical fail" in report


def test_overall_exit_code_zero_when_all_pass() -> None:
    checks = [Check(name="a", status=Status.PASS)]
    assert overall_exit_code(checks) == 0


def test_overall_exit_code_one_on_critical_fail() -> None:
    checks = [
        Check(name="a", status=Status.PASS),
        Check(
            name="b",
            status=Status.FAIL,
            severity=Severity.CRITICAL,
        ),
    ]
    assert overall_exit_code(checks) == 1


def test_overall_exit_code_zero_on_minor_fail() -> None:
    checks = [
        Check(name="a", status=Status.PASS),
        Check(
            name="b",
            status=Status.FAIL,
            severity=Severity.MINOR,
        ),
    ]
    assert overall_exit_code(checks) == 0


def test_check_ok_property() -> None:
    assert Check(name="x", status=Status.PASS).ok is True
    assert Check(name="x", status=Status.SKIP).ok is True
    assert Check(name="x", status=Status.WARN).ok is True
    assert Check(name="x", status=Status.FAIL).ok is False


def test_health_checks_return_list() -> None:
    """Smoke test, actual results depend on local install, but the list
    must be non-empty and every entry must be a Check."""
    from eda_agent.diag.health import run_health_checks

    checks = run_health_checks()
    assert len(checks) >= 3
    for c in checks:
        assert isinstance(c, Check)
        assert c.status in {Status.PASS, Status.FAIL, Status.SKIP, Status.WARN}
