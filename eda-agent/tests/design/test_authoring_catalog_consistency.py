# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Pin the authoring catalogs (blocks, edit ops, footprint families) to
their documentation.

The tool-count test catches a stale ``### Design agent (N tools)`` header,
but not a catalog whose CONTENTS drifted -- a new circuit block missing
from the README's ``design_add_circuit_block`` list, or a new footprint
family the tool docstring never mentions. Those are silent: the LLM reads
the docs to pick a block / family / op name, so a missing entry means it
never discovers the capability. This asserts every code-level catalog
entry is documented.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from eda_agent.design.footprint_gen import generate_footprint  # noqa: F401
from eda_agent.design.plan_blocks import _BLOCKS, BLOCK_SPECS
from eda_agent.design.plan_edit import _EDIT_OPS

_ROOT = Path(__file__).resolve().parents[2]

# Verbs that start a tool name (verb_noun shape). Used to tell a tool
# reference (lib_add_pins) from a field / concept name (lib_ref, lib_path)
# that happens to share the prefix.
_TOOL_VERBS = frozenset({
    "add", "create", "compose", "connect", "edit", "generate", "validate",
    "list", "snapshot", "execute", "describe", "review", "audit", "get",
    "set", "place", "move", "run", "delete", "modify", "batch", "apply",
    "load", "synthesize", "learn", "preview", "suggest", "compute", "plan",
})


def _tool_defs() -> dict[str, set[str]]:
    """Map every @mcp.tool name to its parameter names."""
    defs: dict[str, set[str]] = {}
    for f in (_ROOT / "src" / "eda_agent" / "tools").glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                getattr(d, "attr", "") == "tool"
                or (isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "tool")
                for d in node.decorator_list
            ):
                defs[node.name] = {
                    a.arg for a in node.args.args + node.args.kwonlyargs}
    return defs


def _actual_tool_names() -> set[str]:
    return set(_tool_defs())


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def test_blocks_specs_cover_every_block():
    assert set(_BLOCKS) == set(BLOCK_SPECS)


def test_every_block_in_readme_and_tool_docstring():
    """The block list is stated in the README and in the docstrings.

    It used to live in the README's tool table. That table was a
    hand-curated copy of a generated file and was deleted, and the
    generated reference truncates the docstring before the block names,
    so the list would have disappeared from the documentation entirely.
    It is small and genuinely useful, so it moved to the feature list
    rather than being dropped.
    """
    readme = _read("README.md")
    row = next(l for l in readme.splitlines()
               if "design_add_circuit_block" in l
               and "Canonical circuit blocks" in l)
    design_py = _read("src/eda_agent/tools/design.py")
    for block in _BLOCKS:
        assert f"`{block}`" in row, f"{block} missing from README block list"
        assert f"``{block}``" in design_py, \
            f"{block} missing from design.py docstrings"


def test_every_edit_op_documented():
    design_py = _read("src/eda_agent/tools/design.py")
    documented = set(re.findall(r'\{"op": "([a-z_]+)"', design_py))
    assert set(_EDIT_OPS) <= documented, \
        f"undocumented edit ops: {set(_EDIT_OPS) - documented}"


def test_discipline_references_no_phantom_tools():
    # Every tool-shaped name (verb_noun) the discipline names must be a real
    # @mcp.tool -- catches a stale reference like `lib_add_pin` (the tool is
    # `lib_add_pins`) that would send the planner to a non-existent tool.
    disc = _read("src/eda_agent/design/discipline.py")
    referenced = set(re.findall(
        r"\b((?:design|lib|pcb|proj|obj|sch|app|route|gen)_[a-z_]+)\b", disc))
    actual = _actual_tool_names()
    phantom = [
        r for r in referenced
        if r not in actual
        and len(r.split("_")) > 1 and r.split("_")[1] in _TOOL_VERBS
    ]
    assert not phantom, f"discipline references non-existent tools: {phantom}"


def test_discipline_tool_call_params_are_real():
    # Keyword args shown in the discipline's tool-call signatures (e.g.
    # `design_validate_plan(plan_json=...)`) must be real parameters of that
    # tool -- a stale/renamed param in the docs would make the planner pass
    # an argument the tool rejects.
    disc = _read("src/eda_agent/design/discipline.py")
    defs = _tool_defs()
    problems: list[str] = []
    for m in re.finditer(
        r"\b((?:design|lib|pcb|proj|obj|sch)_[a-z_]+)\(([^)]*)\)", disc
    ):
        name, argstr = m.group(1), m.group(2)
        if name not in defs:
            continue
        for kw in re.findall(r"(\w+)\s*=", argstr):
            if kw not in defs[name]:
                problems.append(f"{name}(...{kw}=...) -- no such param")
    assert not problems, problems


def test_every_footprint_family_documented():
    fam_src = _read("src/eda_agent/design/footprint_gen.py")
    families = set(re.findall(r'if fam == "([a-z]+)":', fam_src))
    assert families, "no families parsed -- regex drifted from the source"
    lib = _read("src/eda_agent/tools/library.py")
    discipline = _read("src/eda_agent/design/discipline.py")
    for fam in families:
        assert fam in lib, f"family {fam} missing from library.py docstring"
        assert fam in discipline, f"family {fam} missing from discipline rule"
