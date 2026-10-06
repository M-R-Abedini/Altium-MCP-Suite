# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""``maturity=simulator`` must match what the simulator actually answers.

The label is published through ``tool_catalog``, and under the minimal
toolset the catalog IS the interface, so it is the only thing a client
has to decide whether an operation can be tried without Altium running.

It used to be derived from a tool's CATEGORY, on the assumption that a
category with any simulator handler had them all. That was wrong on more
than half the bridge-backed surface: 131 tools advertised coverage for
commands the simulator answers with UNKNOWN_ACTION, and 18 it fully
answers were published as live_only. This test replaces the assumption
with a measurement and fails if the two ever drift apart.

Two rules the measurement has to respect, both learned by getting them
wrong first:

* A tool is covered only when EVERY command it sends is. A tool with one
  unanswered call cannot run to completion, so partial coverage is no
  coverage. Eight tools sit exactly there (``lib_move_components`` and
  ``sch_place_components`` among them).
* A CATCH-ALL handler is not coverage. ``_handle_audit`` answers any
  ``audit.*`` action with a canned ``{checked, violations, items}``, so a
  naive probe reports all 35 audit tools as covered when the simulator
  implements none of those checks. Detected by asking each category for
  a deliberately absent action: if that succeeds, the category proves
  nothing per-action. Without this the set inflates from 75 to 107.

Two ways to raise the number that were measured and rejected, so nobody
spends the effort twice:

* Implementing more handlers is a long tail, not a lever. Of the 250
  bridge-backed tools outside the set, 241 are blocked by exactly one
  command, spread over 231 distinct commands. There is no handler worth
  writing for the label alone; write one when a test needs it.
* Registering the audit actions per-name (so the category stops being a
  catch-all) would promote ~32 tools and prove nothing new. Its only
  real value would be catching a Python command name that no Pascal
  handler implements, and
  ``test_bridge_handlers_reachable.test_every_command_python_sends_has_a_handler``
  already does that by scanning Audit.pas itself. A simulator-side
  registry would be a hand-maintained mirror of the same facts, free to
  drift from the source it copies.
"""

from __future__ import annotations

import ast
import json
import tempfile
from pathlib import Path

import pytest

from eda_agent.tools.metadata import _SIMULATOR_TOOLS, tool_metadata
from tests.altium_simulator import AltiumSimulator

TOOLS_DIR = Path(__file__).resolve().parents[1] / "src" / "eda_agent" / "tools"

#: An action no handler can plausibly implement, used to unmask catch-alls.
_ABSENT_ACTION = "__definitely_not_a_real_action__"

_UNKNOWN = {"UNKNOWN_ACTION", "UNKNOWN_COMMAND"}


def _own_nodes(fn: ast.AST) -> list[ast.AST]:
    """Nodes belonging to ``fn`` itself, not to functions nested in it."""
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _commands_by_tool() -> dict[str, list[str]]:
    """Every bridge command each tool sends, by literal first argument.

    Commands built at runtime (an f-string, a variable) are invisible
    here. That direction is safe: a tool whose command cannot be read
    statically sends no literal, so it never enters the set and is
    published as live_only, which is the conservative answer.
    """
    out: dict[str, list[str]] = {}
    for path in sorted(TOOLS_DIR.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:  # pragma: no cover - caught by the parse guard
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if tool_metadata(fn.name)["category"] == "other":
                continue
            cmds = {
                node.args[0].value
                for node in _own_nodes(fn)
                if isinstance(node, ast.Call)
                and getattr(node.func, "attr", None)
                in ("send_command", "send_command_async")
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            }
            if cmds:
                out[fn.name] = sorted(cmds)
    return out


def _error_code(sim: AltiumSimulator, command: str) -> str | None:
    try:
        reply = json.loads(sim._dispatch(command, {}, "probe"))
    except Exception:
        # A handler that throws on empty params still exists; only an
        # explicit unknown-action reply means it is missing.
        return None
    return (reply.get("error") or {}).get("code")


def _catch_all_categories(sim: AltiumSimulator, categories) -> set[str]:
    return {
        cat for cat in categories
        if _error_code(sim, f"{cat}.{_ABSENT_ACTION}") not in _UNKNOWN
    }


def _measure() -> tuple[set[str], set[str], dict[str, list[str]]]:
    """(fully covered, catch-all categories, commands by tool)."""
    sends = _commands_by_tool()
    with tempfile.TemporaryDirectory() as tmp:
        sim = AltiumSimulator(tmp)
        categories = {c.split(".", 1)[0] for cs in sends.values() for c in cs}
        catch_all = _catch_all_categories(sim, categories)

        def covered(command: str) -> bool:
            if command.split(".", 1)[0] in catch_all:
                return False
            return _error_code(sim, command) not in _UNKNOWN

        full = {t for t, cs in sends.items() if all(covered(c) for c in cs)}
    return full, catch_all, sends


def _as_source(names) -> str:
    """The measured set formatted exactly as the source literal."""
    import textwrap

    body = " ".join(f'"{name}",' for name in sorted(names))
    return ("_SIMULATOR_TOOLS = frozenset({\n"
            + "\n".join(textwrap.wrap(body, 68, initial_indent="    ",
                                      subsequent_indent="    "))
            + "\n})")


def test_simulator_label_matches_the_simulator():
    measured, _catch_all, sends = _measure()

    assert len(sends) > 250, (
        f"only {len(sends)} tools found sending a literal bridge command; "
        f"the AST scan has gone blind and this guard proves nothing")

    over = sorted(_SIMULATOR_TOOLS - measured)
    under = sorted(measured - _SIMULATOR_TOOLS)

    detail = []
    if over:
        detail.append(
            "These are published as simulator-covered but the simulator "
            "does not implement every command they send:\n  "
            + "\n  ".join(f"{t} -> {sends.get(t, [])}" for t in over))
    if under:
        detail.append(
            "The simulator fully answers these but they are published as "
            "live_only:\n  " + "\n  ".join(under))
    if detail:
        # Emit the corrected set ready to paste. It is 75 names and
        # changes whenever the simulator gains a handler, so a failure
        # that only says "these are wrong" leaves the reader to rebuild
        # it by hand, which is how a guard gets worked around rather
        # than fixed.
        detail.append("Replace _SIMULATOR_TOOLS in tools/metadata.py "
                      "with:\n\n" + _as_source(measured))
    assert not detail, (
        "_SIMULATOR_TOOLS in tools/metadata.py disagrees with the "
        "simulator.\n\n" + "\n\n".join(detail))


def test_partial_coverage_is_not_counted_as_coverage():
    """One unanswered command ends the run, so it is not covered.

    Pinned by name because the rule is easy to relax by accident: an
    "any command answered" measurement reads as a reasonable
    simplification right up until a tool advertised as runnable without
    Altium stops halfway through.
    """
    measured, _catch_all, sends = _measure()
    with tempfile.TemporaryDirectory() as tmp:
        sim = AltiumSimulator(tmp)
        for tool in ("lib_move_components", "sch_place_components",
                     "lib_auto_link_3d_models"):
            cmds = sends[tool]
            answered = [c for c in cmds if _error_code(sim, c) not in _UNKNOWN]
            assert answered, f"{tool} should have at least one answered command"
            assert len(answered) < len(cmds), (
                f"{tool} is now fully covered; it was chosen as a partial "
                f"example, so pick another or drop it from this test")
            assert tool not in measured
            assert tool not in _SIMULATOR_TOOLS


def test_catch_all_handlers_are_detected_and_excluded():
    """``_handle_audit`` answers anything; that is not per-action coverage.

    If this ever reports no catch-all, either the simulator gained real
    per-action audit handlers (good, and the measured set grows) or the
    detection broke (bad, and the set silently inflates by 32). Either
    way it should be looked at rather than assumed.
    """
    _measured, catch_all, _sends = _measure()
    assert catch_all == {"audit"}, (
        f"expected exactly the audit category to be a catch-all, got "
        f"{sorted(catch_all)}. If the simulator gained real audit "
        f"handlers, re-measure _SIMULATOR_TOOLS; if a new catch-all "
        f"appeared, it is inflating the published coverage claim.")


def test_the_absent_action_probe_is_actually_absent():
    """The catch-all detector depends on this action existing nowhere."""
    source = (Path(__file__).resolve().parents[1]
              / "tests" / "altium_simulator.py").read_text(encoding="utf-8")
    assert _ABSENT_ACTION not in source


@pytest.mark.parametrize("name", [
    # Answered by a real per-action handler.
    "app_get_version", "proj_get_bom", "lib_add_pins", "obj_query",
])
def test_known_covered_tools_are_labelled_simulator(name):
    assert tool_metadata(name)["maturity"] == "simulator"


@pytest.mark.parametrize("name", [
    # audit_* whose only command falls to the catch-all.
    "audit_find_acute_angles", "audit_variant_not_fitted",
    # PCB work the simulator has no handler for.
    "pcb_apply_dnp_paste_exclusion",
])
def test_uncovered_tools_are_not_labelled_simulator(name):
    assert tool_metadata(name)["maturity"] != "simulator"


def test_every_listed_tool_still_exists():
    """A renamed tool would leave a dead entry claiming coverage."""
    known = set(_commands_by_tool())
    stale = sorted(_SIMULATOR_TOOLS - known)
    assert not stale, (
        f"_SIMULATOR_TOOLS names tools that no longer send a bridge "
        f"command (renamed or deleted): {stale}")
