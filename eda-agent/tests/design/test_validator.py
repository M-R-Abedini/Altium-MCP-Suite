# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Validator tests with a fake bridge, no Altium."""

from __future__ import annotations

from typing import Any

from eda_agent.design.validator import (
    Issue,
    ValidationReport,
    _classify_severity,
    validate,
)


class _FakeBridge:
    def __init__(
        self,
        messages: list[dict[str, Any]] | None = None,
        unconnected: list[dict[str, Any]] | None = None,
        fail_commands: set[str] | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.messages = messages or []
        self.unconnected = unconnected or []
        self.fail_commands = fail_commands or set()

    def send_command(self, command: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((command, params or {}))
        if command in self.fail_commands:
            raise RuntimeError(f"forced failure on {command}")
        if command == "project.get_messages":
            return {"messages": self.messages, "count": len(self.messages)}
        if command == "generic.get_unconnected_pins":
            return {
                "unconnected_pins": self.unconnected,
                "count": len(self.unconnected),
            }
        return {"ok": True}


def test_validator_clean_project_passes() -> None:
    bridge = _FakeBridge(messages=[], unconnected=[])
    report = validate(bridge=bridge)
    assert report.passed is True
    assert report.errors == []
    cmds = [c for c, _ in bridge.calls]
    assert "generic.run_erc" in cmds
    assert "project.get_messages" in cmds
    assert "generic.get_unconnected_pins" in cmds


def test_validator_classifies_erc_error() -> None:
    bridge = _FakeBridge(
        messages=[
            {
                "message": "[ERC Error] Floating Net Label NetVCC on sheet main",
                "severity": "Error",
                "source": "main.SchDoc",
            }
        ]
    )
    report = validate(bridge=bridge)
    assert report.passed is False
    assert len(report.errors) == 1
    assert report.errors[0].category == "erc"
    assert report.errors[0].severity == "error"
    assert report.errors[0].sheet == "main.SchDoc"


def test_validator_classifies_compile_error() -> None:
    bridge = _FakeBridge(
        messages=[
            {
                "message": "Compiler Error: Duplicate component R1",
                "severity": "Error",
                "source": "main.SchDoc",
            }
        ]
    )
    report = validate(bridge=bridge)
    assert report.passed is False
    assert report.errors[0].category == "compile"


def test_validator_warning_does_not_fail_overall() -> None:
    bridge = _FakeBridge(
        messages=[
            {
                "message": "[ERC Warning] Net has only one connection",
                "severity": "Warning",
                "source": "main.SchDoc",
            }
        ]
    )
    report = validate(bridge=bridge)
    assert report.passed is True
    assert report.warnings


def test_validator_unconnected_pins_become_errors() -> None:
    bridge = _FakeBridge(
        unconnected=[
            {
                "designator": "U1",
                "pin_number": "5",
                "pin_name": "VOUT",
                "sheet": "main.SchDoc",
            },
            {
                "designator": "R3",
                "pin_number": "1",
                "pin_name": "1",
                "sheet": "main.SchDoc",
            },
        ]
    )
    report = validate(bridge=bridge)
    assert report.passed is False
    pins = [e for e in report.errors if e.category == "unconnected_pin"]
    assert len(pins) == 2
    assert {e.refdes for e in pins} == {"U1", "R3"}


def test_validator_skip_erc_skips_run_erc() -> None:
    bridge = _FakeBridge()
    validate(bridge=bridge, skip_erc=True)
    cmds = [c for c, _ in bridge.calls]
    assert "generic.run_erc" not in cmds
    assert "project.get_messages" in cmds


def test_validator_handles_get_messages_failure() -> None:
    bridge = _FakeBridge(fail_commands={"project.get_messages"})
    report = validate(bridge=bridge)
    assert any("get_messages failed" in n for n in report.notes)
    # Should still try the unconnected-pins query
    cmds = [c for c, _ in bridge.calls]
    assert "generic.get_unconnected_pins" in cmds


def test_validator_serializes_to_dict() -> None:
    bridge = _FakeBridge(
        messages=[
            {
                "message": "[ERC Error] Floating",
                "severity": "Error",
                "source": "main.SchDoc",
            }
        ]
    )
    report = validate(bridge=bridge)
    blob = report.to_dict()
    assert blob["passed"] is False
    assert blob["errors"][0]["text"] == "[ERC Error] Floating"
    assert isinstance(blob["warnings"], list)


def test_classify_severity() -> None:
    assert _classify_severity("Error") == "error"
    assert _classify_severity("FATAL") == "error"
    assert _classify_severity("Warning") == "warning"
    assert _classify_severity("info") == "info"
    assert _classify_severity(None) == "info"
    assert _classify_severity("anything else") == "info"


def test_validator_passes_project_path_through() -> None:
    bridge = _FakeBridge()
    validate("C:\\proj.PrjPcb", bridge=bridge)
    msg_calls = [params for cmd, params in bridge.calls if cmd == "project.get_messages"]
    assert msg_calls and msg_calls[0]["project_path"] == "C:\\proj.PrjPcb"
