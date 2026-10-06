# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every EasyEDA API call must pass an argument count the API accepts.

The existing guard, ``test_easyeda_api_calls_are_real``, checks that a
method NAME exists. That is why ten argument-level defects shipped
undetected, six of which break the call outright:

* ``getPrimitivesInRegion(x1, y1, x2, y2)`` where the API takes
  ``(left, right, top, bottom)``, so Y went where X was expected and a
  plausible list came back either way
* ``pcb_Drc.check()`` with no arguments, where the signature is
  ``check(strict, userInterface, includeVerboseError)``: the zero-argument
  form returns a boolean, so DRC could never return violations
* ``dmt_Panel.createPanel(name)`` where the API takes none

This checks the count against the official reference cloned at
``reference/easyeda-api-skill``. Counting is deliberate and its limits are
worth stating: it catches too many and too few arguments, and it CANNOT
catch a wrong ORDER of same-typed parameters, which is what the
``getPrimitivesInRegion`` defect actually was. Order needs a human or a
type-aware check; this stops the cheaper half mechanically.

Optional parameters are marked in the reference by the detail section
rather than the table, so the acceptable range is treated as
zero-to-declared unless a call passes MORE than declared. That keeps the
guard free of false alarms while still catching the over-supply case,
which is what ``createPanel`` was.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_CLASSES = _ROOT / "reference" / "easyeda-api-skill" / "references" / "classes"
_MAIN_JS = _ROOT / "extensions" / "easyeda" / "main.js"

pytestmark = pytest.mark.skipif(
    not _CLASSES.is_dir(),
    reason="the official reference is not cloned at reference/easyeda-api-skill",
)

#: method-name(param, param) as it appears in each class's method table
_SIG = re.compile(r"\[(\w+)\(([^)]*)\)\]\(\./")

#: eda.<instance>.<method>( in our extension
_CALL = re.compile(r"\beda\.([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\s*\(")


def _official() -> dict[str, dict[str, int]]:
    """instance name -> {method: declared parameter count}."""
    out: dict[str, dict[str, int]] = {}
    for f in sorted(_CLASSES.glob("*.md")):
        head, _, rest = f.stem.partition("_")
        instance = f"{head.lower()}_{rest}" if rest else head.lower()
        methods: dict[str, int] = {}
        for name, params in _SIG.findall(f.read_text(encoding="utf-8")):
            count = len([p for p in params.split(",") if p.strip()])
            # A class can list an overload twice; keep the widest arity,
            # since passing fewer is legal and passing more is the defect.
            methods[name] = max(methods.get(name, 0), count)
        if methods:
            out[instance] = methods
    return out


def _arg_count(source: str, open_paren: int) -> int | None:
    """Count top-level arguments in the call starting at open_paren.

    Returns None when the call spans constructs this cannot count
    honestly (nested template literals, a spread). Counting those wrong
    would produce a false failure, and a guard that cries wolf gets
    disabled, so it declines instead.
    """
    depth, i, n = 0, open_paren, len(source)
    args, current_has_content, saw_spread = 0, False, False
    while i < n:
        ch = source[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return None if saw_spread else args + (1 if current_has_content else 0)
        elif ch in "\"'`":
            quote, i = ch, i + 1
            while i < n and source[i] != quote:
                i += 2 if source[i] == "\\" else 1
            current_has_content = True
        elif depth == 1 and ch == ",":
            args += 1
            current_has_content = False
        elif depth == 1 and source.startswith("...", i):
            saw_spread = True
        elif not ch.isspace():
            current_has_content = True
        i += 1
    return None


def _calls():
    """(instance, method, line, argument count) for every call site."""
    src = _MAIN_JS.read_text(encoding="utf-8")
    for m in _CALL.finditer(src):
        count = _arg_count(src, m.end() - 1)
        if count is None:
            continue
        line = src.count("\n", 0, m.start()) + 1
        yield m.group(1), m.group(2), line, count


_OFFICIAL = _official()
_CALLS = list(_calls())


def test_the_reference_actually_parsed():
    """A regex that stopped matching would make every check vacuous."""
    assert len(_OFFICIAL) >= 100, (
        f"only {len(_OFFICIAL)} classes parsed out of the reference; the "
        f"method-table format changed and this guard reads nothing")
    total = sum(len(v) for v in _OFFICIAL.values())
    assert total >= 500, f"only {total} signatures parsed"
    # An anchor whose real arity is known by hand.
    assert _OFFICIAL["pcb_Document"]["getPrimitivesInRegion"] == 5


def test_our_call_sites_were_actually_found():
    assert len(_CALLS) >= 120, (
        f"only {len(_CALLS)} call sites parsed from main.js; the extraction "
        f"broke and this guard is checking almost nothing")


@pytest.mark.parametrize(
    "instance,method,line,count",
    [c for c in _CALLS if c[0] in _OFFICIAL and c[1] in _OFFICIAL[c[0]]],
    ids=lambda v: str(v),
)
def test_no_call_passes_more_arguments_than_the_api_declares(
        instance, method, line, count):
    declared = _OFFICIAL[instance][method]
    assert count <= declared, (
        f"main.js:{line} calls eda.{instance}.{method} with {count} "
        f"arguments; the official reference declares {declared}. Extra "
        f"arguments are silently dropped, so the call appears to work "
        f"while doing something else")


def test_every_method_we_call_exists_in_the_reference():
    """Names too, since a typo is the cheapest way to reach undefined."""
    unknown = []
    for instance, method, line, _ in _CALLS:
        if instance not in _OFFICIAL:
            continue  # instance-level absence is reported separately
        if method not in _OFFICIAL[instance]:
            unknown.append(f"main.js:{line} eda.{instance}.{method}")
    assert not unknown, (
        "these methods are not in the official reference:\n  "
        + "\n  ".join(sorted(set(unknown))))


def test_instances_we_call_are_documented_classes():
    missing = sorted({i for i, _, _, _ in _CALLS if i not in _OFFICIAL})
    assert not missing, (
        f"we call these with no matching class doc: {missing}. Either the "
        f"instance name is misspelt, in which case it resolves to undefined "
        f"at runtime rather than raising, or the reference needs updating")


# --------------------------------------------------------------------
# The Python side reaches the API too, through the reflective shim.
#
# The guard above scans main.js, and the shim moved call sites out of
# it: easyeda.py sends {"class_name": ..., "method": ..., "args": [...]}
# through system.invoke, and nothing checked those. That gap hid a real
# regression. The DRC confirmation re-checked a clean board with
# "args": [] while the handler had been corrected to request the array
# overload, so a genuinely clean board would have been reported as not
# clean. Same defect as the handler had, in the file nobody was reading.
# --------------------------------------------------------------------

_TOOLS_PY = _ROOT / "src" / "eda_agent" / "tools" / "easyeda.py"

#: {"class_name": "X", "method": "y", "args": [...]} in either key order
_SHIM = re.compile(
    r'"class_name"\s*:\s*(?P<cls>"[A-Za-z0-9_]+"|[A-Za-z_][A-Za-z0-9_]*)\s*,'
    r'\s*"method"\s*:\s*"(?P<meth>[A-Za-z0-9_]+)"\s*,'
    r'\s*"args"\s*:\s*\[(?P<args>[^\]]*)\]',
    re.S)


def _shim_calls():
    """(line, class or None, method, argument count) per literal site.

    The class is None when it is a variable, which the DRC confirmation
    does deliberately to serve both checkers. The method and its
    argument count are still literal, so arity is still checkable
    against whichever classes declare that method.
    """
    src = _TOOLS_PY.read_text(encoding="utf-8")
    for m in _SHIM.finditer(src):
        raw = m.group("cls")
        cls = raw.strip('"') if raw.startswith('"') else None
        body = m.group("args").strip()
        count = 0 if not body else len(
            [a for a in re.split(r",(?![^\[\]{}]*[\]}])", body) if a.strip()])
        yield src.count("\n", 0, m.start()) + 1, cls, m.group("meth"), count


_SHIM_CALLS = list(_shim_calls())


def test_the_shim_call_sites_were_found():
    """A regex that stops matching makes the checks below vacuous."""
    assert len(_SHIM_CALLS) >= 3, (
        f"only {len(_SHIM_CALLS)} literal shim calls parsed from "
        f"easyeda.py; the payload shape changed and this reads nothing")


@pytest.mark.parametrize("line,cls,method,count", _SHIM_CALLS,
                         ids=lambda v: str(v))
def test_shim_calls_pass_an_argument_count_the_api_accepts(
        line, cls, method, count):
    if cls is not None:
        declared = _OFFICIAL.get(cls, {}).get(method)
        assert declared is not None, (
            f"easyeda.py:{line} invokes {cls}.{method}, which the official "
            f"reference does not declare")
        assert count <= declared, (
            f"easyeda.py:{line} sends {count} arguments to {cls}.{method}; "
            f"the reference declares {declared}")
        return

    # Variable class: check against every class declaring that method,
    # and require them to agree, otherwise the site is ambiguous.
    arities = {c: m[method] for c, m in _OFFICIAL.items() if method in m}
    assert arities, (
        f"easyeda.py:{line} invokes .{method} on a variable class, and no "
        f"documented class declares that method at all")
    assert len(set(arities.values())) == 1, (
        f"easyeda.py:{line} invokes .{method} on a variable class whose "
        f"candidates disagree on arity: {arities}")
    declared = next(iter(arities.values()))
    assert count <= declared, (
        f"easyeda.py:{line} sends {count} arguments to .{method}; the "
        f"documented arity is {declared} ({sorted(arities)})")


def test_the_drc_confirmation_asks_for_the_array_overload():
    """The regression this section exists for, pinned by behaviour.

    check() returns a boolean unless includeVerboseError is true. A
    confirmation that sends no arguments always sees a boolean and so
    always overturns a clean board, which is the exact opposite of what
    it is for.
    """
    sites = [(line, count) for line, _c, meth, count in _SHIM_CALLS
             if meth == "check"]
    assert sites, "the DRC confirmation no longer invokes check"
    for line, count in sites:
        assert count == 3, (
            f"easyeda.py:{line} invokes check with {count} arguments; it "
            f"needs all three, with includeVerboseError true, or it reads "
            f"the boolean overload and calls every clean board dirty")


# --------------------------------------------------------------------
# Dynamic dispatch, which the pattern scan above cannot see.
#
# librarySearch resolves its class at runtime through eda[className] and
# calls .search.apply(), so none of its call sites match
# `eda.<instance>.<method>(`. That is the same shape of hole as the shim
# gap: a real call site that moved out of the pattern being watched.
#
# It matters here specifically because the classes DISAGREE on arity.
# symbolType exists on Symbol and Device only, so itemsOfPage sits at
# position 5 for those two and position 4 for Footprint and 3DModel.
# Building one argument list for all four would put the page size into
# the classification slot and quietly return nothing.
# --------------------------------------------------------------------

def _library_search_classes() -> set[str]:
    """Every class librarySearch is called with, read from main.js."""
    src = _MAIN_JS.read_text(encoding="utf-8")
    return set(re.findall(r"librarySearch\(\s*'([A-Za-z0-9_]+)'", src))


def test_the_library_search_callers_were_found():
    classes = _library_search_classes()
    assert len(classes) >= 4, (
        f"only {sorted(classes)} parsed as librarySearch callers; the "
        f"helper was renamed or its callers changed shape")


@pytest.mark.parametrize("cls", sorted(_library_search_classes()))
def test_every_dynamically_dispatched_search_exists(cls):
    assert cls in _OFFICIAL, f"librarySearch is called with unknown {cls}"
    assert "search" in _OFFICIAL[cls], (
        f"{cls} has no search method in the official reference")


@pytest.mark.parametrize("cls", sorted(_library_search_classes()))
def test_the_symbol_type_split_matches_the_reference(cls):
    """The fact the whole argument build hangs off, checked not assumed.

    main.js names the classes carrying symbolType in one list. If the
    reference and that list ever disagree, the paging arguments land in
    the wrong slots for the class that moved.
    """
    src = _MAIN_JS.read_text(encoding="utf-8")
    listed = re.search(
        r"LIB_SEARCH_HAS_SYMBOL_TYPE\s*=\s*\[([^\]]*)\]", src)
    assert listed, "LIB_SEARCH_HAS_SYMBOL_TYPE is gone"
    has_symbol_type = set(re.findall(r"'([A-Za-z0-9_]+)'", listed.group(1)))

    declared = _OFFICIAL[cls]["search"]
    # 6 = key, libraryUuid, classification, symbolType, itemsOfPage, page
    # 5 = the same without symbolType
    assert declared in (5, 6), (
        f"{cls}.search declares {declared} parameters; this guard assumes "
        f"the documented five or six and needs revisiting")
    expected_in_list = declared == 6
    assert (cls in has_symbol_type) is expected_in_list, (
        f"{cls}.search declares {declared} parameters, so it "
        f"{'does' if expected_in_list else 'does not'} carry symbolType, "
        f"but main.js {'omits' if expected_in_list else 'includes'} it in "
        f"LIB_SEARCH_HAS_SYMBOL_TYPE. The paging arguments would land in "
        f"the wrong slots for this class")
