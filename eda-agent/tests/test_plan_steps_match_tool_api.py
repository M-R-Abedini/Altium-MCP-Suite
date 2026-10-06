# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Guard: emitted tool-call plans must match the real tool signatures.

Several modules return a PLAN of this server's own MCP tool calls for a
caller to execute later (the EasyEDA to Altium importer, the CSE
importer, the hierarchy planner). Nothing executes them at build time,
so a plan can stay perfectly self-consistent while every argument name
is invented, and the mistake only surfaces when a user runs it against
Altium.

That is not hypothetical: the EasyEDA Altium emitter was written against
an imagined stateless API (``library_path``/``component_name`` on every
call, ``orientation``, ``height``, ``pcb_library_path``) and all of its
steps would have failed. Its own unit tests passed the whole time,
because they only compared the plan against the emitter's assumptions.

Two checks here:

* a DYNAMIC one that builds a real plan and validates each step against
  the registered function signature. This is the strong check.
* a STATIC one that scans the source for step dicts. It is a backstop
  for emitters this file cannot easily construct. It deliberately
  understands ``dict(base, x=...)`` as well as ``{"x": ...}``, because
  the original bug used the ``dict()`` form and a literal-only scan
  would have missed it entirely.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import pkgutil
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "eda_agent"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "easyeda_soic8.json"


class _Capture:
    """Stands in for the MCP server, keeping every registered function."""

    def __init__(self) -> None:
        self.fns: dict = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.fns[fn.__name__] = fn
            return fn
        return deco

    # Newer registrations may also declare prompts/resources; accept and
    # ignore them so a new decorator does not break this guard.
    def prompt(self, *args, **kwargs):
        return self.tool()

    def resource(self, *args, **kwargs):
        return self.tool()


@pytest.fixture(scope="module")
def signatures() -> dict:
    """Every registered tool name -> its signature."""
    import eda_agent.tools as tools_pkg

    cap = _Capture()
    for mod_info in pkgutil.iter_modules(tools_pkg.__path__):
        try:
            mod = importlib.import_module(f"eda_agent.tools.{mod_info.name}")
        except Exception:
            continue
        for attr in dir(mod):
            if not attr.startswith("register_"):
                continue
            fn = getattr(mod, attr)
            if callable(fn):
                try:
                    fn(cap)
                except Exception:
                    # A registrar needing more than an mcp object is not
                    # this guard's problem; others still register.
                    continue
    # ~476 today. A near-empty registry would not pass silently (every
    # step would report "no such tool"), but it would blame the PLAN for
    # what is really a registration failure, so say which one broke.
    assert len(cap.fns) > 300, (
        f"only {len(cap.fns)} tools registered; registration broke, so "
        f"any plan mismatch reported below is misleading")
    return {n: inspect.signature(f) for n, f in cap.fns.items()}


def _check(name: str, keys, signatures: dict, where: str) -> list[str]:
    sig = signatures.get(name)
    if sig is None:
        return [f"{where}: no such tool {name!r}"]
    unknown = sorted(set(keys) - set(sig.parameters))
    if unknown:
        return [f"{where}: {name} got unknown args {unknown}; "
                f"valid: {sorted(sig.parameters)}"]
    return []


# --------------------------------------------------------------------
# Dynamic: build a real plan and validate every step.
# --------------------------------------------------------------------

def test_easyeda_altium_plan_matches_tool_signatures(signatures):
    from eda_agent.libimport.easyeda import parse_component
    from eda_agent.libimport.easyeda.altium import build_altium_plan

    comp = parse_component(json.loads(FIXTURE.read_text(encoding="utf-8")))
    plan = build_altium_plan(comp, "T.SchLib", "T.PcbLib")
    assert plan["steps"]

    problems: list[str] = []
    for step in plan["steps"]:
        name, args = step["tool"], step["args"]
        problems += _check(name, args, signatures, "altium plan")
        sig = signatures.get(name)
        if sig is not None:
            required = {n for n, p in sig.parameters.items()
                        if p.default is inspect.Parameter.empty}
            missing = sorted(required - set(args))
            if missing:
                problems.append(f"altium plan: {name} missing {missing}")
    assert not problems, "\n".join(problems)


# --------------------------------------------------------------------
# Static: scan every module for step dicts.
# --------------------------------------------------------------------

def _arg_keys(node: ast.AST):
    """Literal keys of a step's argument mapping, or None if unresolvable.

    Handles both ``{"a": 1}`` and ``dict(base, a=1)``. For the ``dict()``
    form only the explicit keywords are returned: whatever ``base``
    contributes cannot be resolved statically, so this under-reports
    rather than inventing keys.
    """
    if isinstance(node, ast.Dict):
        keys = [k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        # A ``**spread`` shows up as a None key; the mapping is partial.
        if any(k is None for k in node.keys):
            return None
        return keys
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "dict"):
        return [kw.arg for kw in node.keywords if kw.arg]
    return None


def _iter_step_dicts(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value if isinstance(k, ast.Constant) else None
                for k in node.keys]
        if "tool" not in keys:
            continue
        tool_node = node.values[keys.index("tool")]
        if not (isinstance(tool_node, ast.Constant)
                and isinstance(tool_node.value, str)):
            continue
        for holder in ("params", "args"):
            if holder in keys:
                yield node.lineno, tool_node.value, node.values[
                    keys.index(holder)]
                break


#: Modules known to emit tool-call plans. If one stops being scanned the
#: guard has gone blind for it.
_EMITTER_MODULES = {
    "libimport/easyeda/altium.py",
    "design/hierarchy.py",
    "libimport/cse.py",
}


def test_no_module_emits_a_step_with_invented_arguments(signatures):
    problems: list[str] = []
    scanned = 0
    seen_modules: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for lineno, name, holder in _iter_step_dicts(tree):
            keys = _arg_keys(holder)
            if keys is None:
                continue
            scanned += 1
            seen_modules.add(path.relative_to(SRC).as_posix())
            problems += _check(
                name, keys, signatures,
                f"{path.relative_to(SRC.parent.parent)}:{lineno}")

    # 22 today across three modules. A bare "> 0" would still pass if a
    # refactor changed the step shape and coverage collapsed to one, so
    # assert a real floor AND that every known emitter is still seen.
    # A guard that has gone blind is worse than no guard: the file still
    # looks covered.
    assert scanned >= 18, (
        f"static scanner inspected only {scanned} step dicts; the step "
        f"shape probably changed and this guard has gone blind")
    missing = _EMITTER_MODULES - seen_modules
    assert not missing, (
        f"no step dicts found in {sorted(missing)}; either the module "
        f"stopped emitting plans or the scanner stopped matching it")
    assert not problems, "\n".join(problems)


def test_the_static_scanner_actually_catches_a_bad_step(signatures):
    """The guard above is worthless if the scanner silently matches none.

    This pins the two shapes it must understand, including the
    ``dict(base, ...)`` form that the real bug used.
    """
    literal = ast.parse(
        'x = {"tool": "lib_add_pins", "args": {"library_path": 1}}')
    call = ast.parse(
        'y = {"tool": "lib_add_pins", "args": dict(base, component_name=1)}')

    for tree, expected in ((literal, "library_path"),
                           (call, "component_name")):
        found = list(_iter_step_dicts(tree))
        assert found, "scanner failed to see the step dict"
        _, name, holder = found[0]
        keys = _arg_keys(holder)
        assert expected in keys
        assert _check(name, keys, signatures, "probe"), \
            f"scanner did not flag {expected}"
