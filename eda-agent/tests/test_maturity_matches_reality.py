# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A tool advertised as "offline" must not need Altium to run.

``tool_catalog`` reports a ``maturity`` per tool, and ``offline`` is the
filter someone uses to answer "what can I do with no Altium running".
That promise matters most under the minimal toolset, where the catalog
IS the interface and there is no schema to read instead.

The classification is derived from the NAME, not the code: any tool
whose name contains ``_calc_``/``_compute_`` is assumed to be pure
maths, and every ``design_`` tool is assumed offline unless explicitly
listed. Both assumptions drift as tools are added.

They had already drifted three times when this guard was written:
``pcb_calc_polygon_area`` (queries board polygons, but matched the
``_calc_`` rule), plus ``design_lint_report`` and
``design_visual_review`` (both fetch board state, but were missing from
_DESIGN_BRIDGE). Each would fail with a bridge error for a user who
filtered on "offline" precisely to avoid that.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from eda_agent.tools.metadata import tool_metadata

TOOLS_DIR = Path(__file__).resolve().parents[1] / "src" / "eda_agent" / "tools"

_BRIDGE_CALLS = {"get_bridge", "send_command", "send_command_async"}

# COVERAGE LIMIT, stated so this guard is not over-trusted: detection is
# DIRECT-CALL ONLY. A tool that delegates to a helper which talks to the
# bridge (design_execute_plan -> execute_plan_from_json, and most of the
# rest of _DESIGN_BRIDGE) looks bridge-free from here. So a failure is
# always a real mislabel, but a pass does NOT prove a tool is offline.
# Following delegation would mean whole-program call-graph analysis, and
# a heuristic approximation of it produced far more noise than signal
# when tried, so the honest move is to bound the claim instead.


def _touches_bridge(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (func.attr if isinstance(func, ast.Attribute)
                else getattr(func, "id", ""))
        if name in _BRIDGE_CALLS:
            return True
    return False


def _tool_functions():
    """(name, module, node) for every function that looks like a tool."""
    for path in sorted(TOOLS_DIR.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8",
                                            errors="replace"))
        except SyntaxError:
            # Skipping here would silently drop every tool in the file
            # and shrink the scan without any signal. It cannot happen
            # unnoticed: test_bridge_handlers_reachable's
            # test_every_scanned_source_file_actually_parses walks all of
            # src/eda_agent, which contains this directory, and fails on
            # any file that does not parse.
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # tool_metadata falls back to "other" for anything whose name
            # is not a recognised tool, which filters out helpers.
            if tool_metadata(node.name)["category"] == "other":
                continue
            yield node.name, path.name, node


def test_offline_tools_do_not_touch_the_bridge():
    offenders = []
    checked = 0
    for name, module, node in _tool_functions():
        checked += 1
        if tool_metadata(name)["maturity"] != "offline":
            continue
        if _touches_bridge(node):
            offenders.append(f"{name} ({module})")

    # ~485 functions today. A collapse means the scan stopped matching
    # and the guard is green while checking nothing.
    assert checked > 400, (
        f"only inspected {checked} tool functions; the scan probably "
        f"stopped matching and this guard has gone blind")
    assert not offenders, (
        "these advertise maturity=offline but call the bridge, so they "
        "fail for anyone filtering for tools that work without Altium:\n  "
        + "\n  ".join(sorted(offenders))
        + "\nAdd them to _CALC_NEEDS_BRIDGE or _DESIGN_BRIDGE in "
          "tools/metadata.py.")


@pytest.mark.parametrize("name", [
    "pcb_calc_polygon_area",
    "design_lint_report",
    "design_visual_review",
])
def test_known_bridge_backed_tools_are_not_offline(name):
    """Pin the three that were mislabelled, so they cannot regress."""
    assert tool_metadata(name)["maturity"] != "offline"


@pytest.mark.parametrize("name", [
    "pcb_calc_trace_width_for_current",
    "pcb_calc_impedance",
    "design_compute_component_value",
])
def test_pure_calculators_stay_offline(name):
    """The fix must not over-correct and label real maths as live.

    These are the tools the offline filter exists to surface.
    """
    assert tool_metadata(name)["maturity"] == "offline"


# --------------------------------------------------------------------
# The same claim-vs-code check for `interaction`. "readonly" is what a
# caller filters on to find operations that cannot change the board, so
# a wrong label here is a safety claim, not a cosmetic one.
# --------------------------------------------------------------------

#: Bridge command verbs that change the design.
from tests.conftest import MUTATING_COMMAND_VERBS as _MUTATING_VERBS


def _bridge_commands(fn: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (func.attr if isinstance(func, ast.Attribute)
                else getattr(func, "id", ""))
        if name in ("send_command", "send_command_async") and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value,
                                                              str):
                out.add(first.value)
    return out


def test_readonly_tools_do_not_issue_mutating_commands():
    """A readonly claim must survive contact with the code.

    Caught pcb_create_diff_pair: the readonly rule matches "_diff_" for
    COMPARISON tools, but there it means DIFFERENTIAL, and the tool
    creates an object on the board.
    """
    offenders = []
    checked = 0
    for name, module, node in _tool_functions():
        checked += 1
        if tool_metadata(name)["interaction"] != "readonly":
            continue
        mutating = sorted(
            cmd for cmd in _bridge_commands(node)
            if any(cmd.split(".", 1)[-1].startswith(v)
                   for v in _MUTATING_VERBS))
        if mutating:
            offenders.append(f"{name} ({module}) -> {mutating}")

    assert checked > 400, (
        f"only inspected {checked} tool functions; this guard has gone "
        f"blind")
    assert not offenders, (
        "these advertise interaction=readonly but issue mutating bridge "
        "commands:\n  " + "\n  ".join(sorted(offenders))
        + "\nAdd an entry to INTERACTION_OVERRIDES in tools/metadata.py.")


def test_diff_homograph_does_not_mislabel_either_way():
    """"diff" means DIFFERENCE in some names and DIFFERENTIAL in others.

    Both readings must land correctly, so a fix for one does not break
    the other.
    """
    assert tool_metadata("pcb_create_diff_pair")["interaction"] != "readonly"
    for readonly in ("lib_diff_libraries", "proj_get_differences",
                     "pcb_get_differential_pairs", "pcb_get_diff_pair_rules"):
        assert tool_metadata(readonly)["interaction"] == "readonly", readonly


def test_interactive_tools_are_not_advertised_as_completing():
    """A tool that needs a human click has not finished the job.

    pcb_start_polygon_placement launches Altium's interactive polygon
    mode; the boundary only exists once a user clicks it out. Reporting
    it "silent" (mutates, done) tells an agent a polygon exists when
    none does. "partial" is the honest label.

    Its programmatic sibling, pcb_place_polygon_rect, really does
    complete on its own and must stay silent -- the distinction is the
    point.
    """
    assert tool_metadata("pcb_start_polygon_placement")["interaction"] == \
        "partial"
    assert tool_metadata("pcb_place_polygon_rect")["interaction"] == "silent"


def test_docstrings_saying_interactive_are_flagged_as_such():
    """Any tool describing INTERACTIVE Altium mode must not read as done.

    Scanning the docstrings rather than a hardcoded list, so a new
    interactive tool is caught when it is added.
    """
    offenders = []
    for name, module, node in _tool_functions():
        doc = (ast.get_docstring(node) or "").lower()
        interactive = ("interactive" in doc
                       and ("user must" in doc or "clicks define" in doc
                            or "right-click" in doc))
        if not interactive:
            continue
        if tool_metadata(name)["interaction"] in ("silent",):
            offenders.append(f"{name} ({module})")
    assert not offenders, (
        "these describe an interactive Altium mode needing a human, but "
        "advertise interaction=silent (mutates, complete):\n  "
        + "\n  ".join(sorted(offenders))
        + "\nMark them PARTIAL or MODAL in INTERACTION_OVERRIDES.")


def test_fixing_maturity_did_not_flip_a_renderer_to_mutating():
    """Regression: correcting maturity silently changed interaction.

    interaction_of falls back to READONLY when maturity is OFFLINE. Two
    tools were relying on that shortcut rather than on any rule about
    what they do. Correcting their maturity to live_only removed the
    shortcut and dropped design_visual_review to "silent" -- claiming a
    renderer mutates the design.

    The lesson is in the coupling: maturity and interaction are derived
    by separate rules that share a fallback, so a fix to one can move
    the other without any test noticing.
    """
    assert tool_metadata("design_visual_review")["interaction"] == "readonly"
    assert tool_metadata("design_lint_report")["interaction"] == "readonly"


def test_bridge_exception_lists_have_no_stale_members():
    """Every name in the exception lists must still exist as a tool.

    A renamed or deleted tool leaves a dead entry, and the list silently
    stops covering whatever replaced it. This checks EXISTENCE only, not
    whether the member really needs the bridge: most reach it through a
    helper (see the coverage limit above), so a usage check here would
    report false positives.
    """
    from eda_agent.tools.metadata import _CALC_NEEDS_BRIDGE, _DESIGN_BRIDGE

    known = {name for name, _module, _node in _tool_functions()}
    assert len(known) > 400, "tool scan went blind"

    stale = sorted((_DESIGN_BRIDGE | _CALC_NEEDS_BRIDGE) - known)
    assert not stale, (
        f"these are listed as needing the bridge but no such tool "
        f"exists: {stale}. Drop them, or fix the name.")
