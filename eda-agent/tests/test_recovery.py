# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for guided crash recovery (roadmap 1.2)."""

from __future__ import annotations

from eda_agent.bridge.recovery import (
    CORRUPT_RESPONSE,
    DEAD_LOOP,
    STUCK_HANDLER,
    recovery_guidance,
    recovery_message,
)
from eda_agent.bridge.exceptions import AltiumTimeoutError


def test_guidance_for_each_fault():
    for fault in (STUCK_HANDLER, DEAD_LOOP, CORRUPT_RESPONSE):
        g = recovery_guidance(fault)
        assert g["fault"] == fault
        assert g["diagnosis"]
        assert len(g["steps"]) >= 2
        assert g["docs"]


def test_stuck_and_dead_differ():
    assert recovery_guidance(STUCK_HANDLER)["diagnosis"] != \
        recovery_guidance(DEAD_LOOP)["diagnosis"]


def test_unknown_fault_has_fallback():
    g = recovery_guidance("mystery")
    assert g["steps"]  # never empty
    assert g["fault"] == "mystery"


def test_recovery_message_is_numbered_and_actionable():
    msg = recovery_message(DEAD_LOOP)
    assert msg.startswith("Recovery:")
    assert "1)" in msg and "2)" in msg
    assert "StartMCPServer" in msg


def test_timeout_error_carries_recovery_details():
    err = AltiumTimeoutError(
        "boom", details={"recovery": recovery_guidance(STUCK_HANDLER)})
    assert err.code == "ALTIUM_TIMEOUT"
    assert err.details["recovery"]["fault"] == STUCK_HANDLER
    assert err.details["recovery"]["steps"]


def test_recovery_message_matches_guidance_steps():
    # No drift between the structured steps and the message rendering.
    for fault in (STUCK_HANDLER, DEAD_LOOP, CORRUPT_RESPONSE):
        steps = recovery_guidance(fault)["steps"]
        msg = recovery_message(fault)
        for step in steps:
            assert step in msg
