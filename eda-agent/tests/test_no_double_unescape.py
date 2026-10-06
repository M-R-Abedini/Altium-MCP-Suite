# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""No Pascal handler unescapes a JSON value a second time.

ExtractJsonValue (Main.pas) ends with UnescapeJsonString, which handles
the full JSON escape set including backslash, so every value a handler
receives is already fully unescaped. A second
``StringReplace(x, '\\\\', '\\', -1)`` is a no-op for local paths but
corrupts UNC paths: ``\\\\server\\share`` loses a leading backslash and
the failure surfaces as a missing file, not a mangled path. 94 such
vestigial sites (survivors of the pre-UnescapeJsonString cascade) were
removed; this keeps the count at zero.

The ESCAPE direction (``'\\'`` to ``'\\\\'``, building JSON output) is
legitimate and deliberately not matched.
"""
from __future__ import annotations

import pathlib
import re

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "altium"

# StringReplace(<anything>, '\\', '\', ...) -- the UNESCAPE direction.
# _BS is ONE real backslash; the Pascal source has two between the first
# pair of quotes and one between the second.
_BS = "\\"
# .* rather than [^)]* in the argument position: the wrapped shape
# StringReplace(ExtractJsonValue(...), ...) carries a closing paren
# BEFORE the backslash arguments, which a paren-excluding class never
# reaches.
_UNESCAPE = re.compile(
    r"StringReplace\s*\(.*,\s*'" + re.escape(_BS * 2) + r"'\s*,\s*'"
    + re.escape(_BS) + r"'",
)


def test_no_pascal_source_unescapes_backslashes_again():
    offenders: list[str] = []
    scanned = 0
    for pas in sorted(_SCRIPTS.glob("*.pas")):
        # The build artifact is regenerated from the units; scanning it
        # would double-report every unit hit under a file nobody edits.
        if pas.name == "Altium_MCP.pas":
            continue
        text = pas.read_text(encoding="latin-1")
        scanned += 1
        for i, line in enumerate(text.splitlines(), start=1):
            if _UNESCAPE.search(line):
                offenders.append(f"{pas.name}:{i}: {line.strip()}")

    # The sibling test proves the REGEX still recognises the defect.
    # Nothing proved the guard ever reached a file, and those are
    # different failures: a moved or renamed scripts directory makes
    # the glob return nothing and this pass, while 94 handlers quietly
    # corrupt UNC paths again. The count is the half that was missing.
    assert scanned >= 8, (
        f"only {scanned} Pascal units were scanned from {_SCRIPTS}; the "
        f"sources moved and this guard is checking almost nothing")

    assert not offenders, (
        "handler re-unescapes a value ExtractJsonValue already unescaped, "
        "which corrupts UNC paths (\\\\server\\share loses a backslash):\n"
        + "\n".join(offenders))


def test_the_guard_recognizes_the_defect_it_exists_to_catch():
    """The pattern must match the exact removed-site shapes, or a passing
    guard proves only that the regex is dead."""
    removed_site = "    LibPath := StringReplace(LibPath, '\\\\', '\\', -1);"
    wrapped_site = ("    LibPath := StringReplace(ExtractJsonValue(Params, "
                    "'library_path'), '\\\\', '\\', -1);")
    escape_direction = "        Tmp := StringReplace(Tmp, '\\', '\\\\', -1);"
    assert _UNESCAPE.search(removed_site)
    assert _UNESCAPE.search(wrapped_site)
    assert not _UNESCAPE.search(escape_direction)
