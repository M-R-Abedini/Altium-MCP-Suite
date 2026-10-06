# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Diagnostic CLI subcommands, health (fast, offline) + doctor (Altium).

These exist to short-circuit "why isn't it working?" by checking the
boring failure modes (stale script cache, workspace pointer mismatch,
Altium not running, version drift) before the user spends time
debugging the real bug.
"""

from eda_agent.diag.checks import Check, Severity, Status

__all__ = ["Check", "Severity", "Status"]
