# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Guard: every eda.* call the extension makes exists in the REAL runtime.

The previous EasyEDA suite was written alongside the extension from the
same assumptions, so it agreed with the code and could not contradict
it. Nothing in it had ever seen the editor. This file is grounded
instead in a surface captured FROM a running EasyEDA Pro: 92 classes and
675 methods, recorded through system.capabilities.

On its first run it found ``pcb_Document.autoRouting``, which the
extension called and the runtime does not have. The class exists, so
this was not a missing document context: it was a method name that had
never been checked against anything. Every call died on "is not a
function" after passing the confirm gate, and the handler was written to
report ``{routed: true}``.

A method here is a fact about the editor rather than about this
repository, so a failure means one of two things and they need
different fixes:

* the extension calls something that does not exist, which is a bug; or
* the captured surface is out of date, which means recapturing it
  against the editor rather than editing the expectation to match.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAIN_JS = ROOT / "extensions" / "easyeda" / "main.js"
SURFACE = ROOT / "tests" / "fixtures" / "easyeda_live_api_surface.json"

#: ``eda.<Class>.<method>(``. Deliberately not matching ``eda[name]``:
#: the reflective shim resolves those at runtime from caller input, so
#: they are not a claim by this file's author about what exists.
_CALL = re.compile(r"\beda\.([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")

#: Methods the extension names deliberately while knowing the runtime
#: may not have them. Each needs a reason, because an unexplained entry
#: here is how a real defect gets waved through.
_EXPECTED_ABSENT: dict[tuple[str, str], str] = {}


def _surface() -> dict:
    return json.loads(SURFACE.read_text(encoding="utf-8"))


def strip_comments(src: str) -> str:
    """JavaScript source with comments blanked out.

    A CALL INSIDE A COMMENT IS NOT A CALL. Scanning raw source made this
    guard fail on itself: the fix for the defect it found explains the
    defect, and the explanation names the method, so the comment
    describing a removed call read as the call still being there.

    String state is tracked rather than assumed, because ``//`` appears
    inside perfectly ordinary values here (``ws://127.0.0.1``), and
    treating those as the start of a comment would silently discard the
    rest of the line along with any call on it.

    Newlines are preserved so line numbers still line up.
    """
    out = []
    i, n = 0, len(src)
    quote = None
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if quote:
            out.append(ch)
            if ch == "\\":                       # escaped char, take both
                if i + 1 < n:
                    out.append(nxt)
                    i += 1
            elif ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i < n and not (src[i] == "*" and i + 1 < n and src[i + 1] == "/"):
                if src[i] == "\n":
                    out.append("\n")
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _calls() -> list[tuple[str, str]]:
    src = strip_comments(MAIN_JS.read_text(encoding="utf-8"))
    return sorted(set(_CALL.findall(src)))


def test_surface_fixture_describes_a_real_capture():
    """The fixture must say where it came from, or it is just an opinion."""
    data = _surface()
    assert "live" in data["source"].lower()
    assert data["captured"]
    assert data["extension_build"], "record which build answered"
    assert data["class_count"] == len(data["classes"])
    assert data["class_count"] > 50, "a capture this small is a failed capture"


def test_extension_calls_only_methods_the_runtime_has():
    classes = _surface()["classes"]
    calls = _calls()
    assert len(calls) > 100, (
        "almost no eda.* calls were found, so the regex has stopped "
        "matching and this guard is passing vacuously")

    missing = []
    for cls, method in calls:
        if (cls, method) in _EXPECTED_ABSENT:
            continue
        if cls not in classes:
            missing.append(f"{cls}.{method}: class absent from the runtime")
        elif method not in classes[cls]:
            missing.append(
                f"{cls}.{method}: class exists, method does not. "
                f"Nearest: "
                f"{sorted(m for m in classes[cls] if m[:4] == method[:4]) or 'none'}")
    assert not missing, (
        "the extension calls methods the live runtime does not have:\n  "
        + "\n  ".join(missing))


@pytest.mark.parametrize("cls,method", sorted(_EXPECTED_ABSENT))
def test_expected_absences_are_still_absent(cls, method):
    """An allowance that is no longer needed must be removed, not kept.

    A stale entry here silently excuses a real call from the check
    above.
    """
    classes = _surface()["classes"]
    assert method not in classes.get(cls, []), (
        f"{cls}.{method} now EXISTS in the runtime, so its entry in "
        f"_EXPECTED_ABSENT is stale and is hiding it from the guard")
