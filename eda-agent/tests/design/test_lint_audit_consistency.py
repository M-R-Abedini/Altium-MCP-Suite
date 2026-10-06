# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Cross-touchpoint consistency for the design-lint audit set.

A new audit needs to touch:
  - scripts/altium/Audit.pas        -- Pascal handler + dispatcher case
  - src/eda_agent/tools/audit.py    -- Python MCP wrapper
  - src/eda_agent/tools/review.py   -- LINT_AUDIT_LIST + LINT_SEVERITY
  - src/eda_agent/web/dashboard_static/index.html  -- SECTION_META label

If any of those drift out of sync, the lint sweep either skips an audit
silently (the bug fixed in the iteration that added LINT_AUDIT_LIST), or
shows orphan rows the agent can't navigate. These tests prove the four
touchpoints agree.
"""

from __future__ import annotations

import re
from pathlib import Path

from eda_agent.tools.review import LINT_AUDIT_LIST, LINT_SEVERITY


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_PAS = REPO_ROOT / "scripts" / "altium" / "Audit.pas"
DASHBOARD_HTML = REPO_ROOT / "src" / "eda_agent" / "web" / "dashboard_static" / "index.html"

# The three Python-side BOM audits don't have a Pascal handler -- they're
# computed off project.get_bom in Python. They DO have a LINT_SEVERITY
# tag and a SECTION_META entry, but no dispatcher branch.
BOM_SIDE_AUDITS = {
    "find_unconnected_ic_pins",
    "find_pin_net_name_mismatches",
    "find_missing_decoupling",
}


def _audit_pas_dispatcher_actions() -> set[str]:
    """Parse the `If Action = 'X' Then` cascade in HandleAuditCommand."""
    text = AUDIT_PAS.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(r"Action = '([a-z_]+)'", re.IGNORECASE)
    return set(pattern.findall(text))


def _dashboard_section_meta_keys() -> set[str]:
    """Pull the audit keys defined in the dashboard's SECTION_META JS object."""
    text = DASHBOARD_HTML.read_text(encoding="utf-8", errors="replace")
    # SECTION_META is a JS object literal: `find_xxx: { group: "..", label: ".." }`
    start = text.find("const SECTION_META = {")
    if start < 0:
        return set()
    # Take until the closing brace + trailing semicolon
    end = text.find("};", start)
    block = text[start:end] if end > start else text[start:]
    pattern = re.compile(r"^\s+([a-z_]+):\s*\{", re.MULTILINE)
    return set(pattern.findall(block))


def test_pascal_dispatcher_matches_lint_audit_list():
    """Every Audit.pas dispatcher action matches a LINT_AUDIT_LIST entry's
    command suffix, and vice versa.

    LINT_AUDIT_LIST entries are (section_name, "audit.<command>"). The
    Pascal dispatcher cases use the bare command (no "audit." prefix).
    Note section_name and command often differ -- e.g. section
    "component_param_visibility" runs Pascal command "validate_component_params".
    """
    dispatcher_actions = _audit_pas_dispatcher_actions()
    list_commands = set()
    for _name, command in LINT_AUDIT_LIST:
        suffix = command[len("audit."):] if command.startswith("audit.") else command
        list_commands.add(suffix)
    only_in_dispatcher = dispatcher_actions - list_commands
    only_in_list = list_commands - dispatcher_actions
    assert not only_in_dispatcher, (
        f"Audit.pas has dispatcher entries missing from LINT_AUDIT_LIST: "
        f"{sorted(only_in_dispatcher)}"
    )
    assert not only_in_list, (
        f"LINT_AUDIT_LIST has entries missing a Pascal dispatcher branch: "
        f"{sorted(only_in_list)}"
    )


def test_every_audit_has_severity():
    """Every LINT_AUDIT_LIST entry has a LINT_SEVERITY tag."""
    missing = [name for name, _ in LINT_AUDIT_LIST if name not in LINT_SEVERITY]
    assert not missing, (
        f"LINT_AUDIT_LIST entries missing a severity classification: "
        f"{sorted(missing)}"
    )


def test_severity_keys_are_either_pascal_or_bom():
    """LINT_SEVERITY entries are either in LINT_AUDIT_LIST or are BOM-side."""
    list_names = {name for name, _ in LINT_AUDIT_LIST}
    valid = list_names | BOM_SIDE_AUDITS
    stray = [k for k in LINT_SEVERITY if k not in valid]
    assert not stray, (
        f"LINT_SEVERITY classifies audits that don't exist anywhere: "
        f"{sorted(stray)}"
    )


def test_severity_values_are_known():
    """Only 'critical' / 'warning' / 'info' are valid severity buckets."""
    valid = {"critical", "warning", "info"}
    bad = {k: v for k, v in LINT_SEVERITY.items() if v not in valid}
    assert not bad, f"Unknown severity values: {bad}"


def test_dashboard_section_meta_covers_every_audit():
    """Every audit (Pascal + BOM-side) has a SECTION_META label in the dashboard."""
    meta_keys = _dashboard_section_meta_keys()
    expected = {name for name, _ in LINT_AUDIT_LIST} | BOM_SIDE_AUDITS
    missing = expected - meta_keys
    assert not missing, (
        f"Dashboard SECTION_META missing labels for: {sorted(missing)}"
    )


def test_dashboard_section_meta_has_no_orphans():
    """SECTION_META entries either name a real audit or a known extra key.

    Known extras: `drc` is the optional Altium Design Rule Check pseudo-
    section that the lint sweep includes when ``run_drc=True``. It has a
    SECTION_META label but is not in LINT_AUDIT_LIST because it runs via
    a different pcb.run_drc command and is opt-in.
    """
    KNOWN_EXTRAS = {"drc"}
    meta_keys = _dashboard_section_meta_keys()
    expected = {name for name, _ in LINT_AUDIT_LIST} | BOM_SIDE_AUDITS | KNOWN_EXTRAS
    orphans = meta_keys - expected
    assert not orphans, (
        f"Dashboard SECTION_META has labels for audits that no longer "
        f"exist: {sorted(orphans)}. Either re-add the audit or remove "
        f"the SECTION_META entry."
    )
