# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""House style: no em or en dashes anywhere in the tracked sources.

The rule is not typographic taste. Docstrings are served verbatim to MCP
clients and error strings are shown to users mid-task, so the punctuation
is part of the product's voice, and a dash that reads as an aside in one
renderer reads as a hyphen in another. Commas, colons, semicolons and
parentheses say the same thing unambiguously.

Enforced as a test because the alternative is re-sweeping the tree by
hand every few months. The sweep that produced this file cleared 572
dashes from 68 files, and a third of the substitutions needed a
judgement a blanket replace cannot make: paired dashes are a
parenthetical and take commas, a following subordinate clause takes a
comma, and what explains what precedes it takes a colon.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Written as escapes, not literals, so this file obeys the rule it
# enforces. With the characters spelled out it would match itself and
# fail the moment it was tracked, and the obvious fix, exempting the
# guard by path, is the kind of exemption that quietly grows.
_EN, _EM = chr(0x2013), chr(0x2014)

DASHES = {_EN: "en dash", _EM: "em dash"}
DASH_RE = re.compile("[" + _EN + _EM + "]")

# Text formats only. Binary fixtures (.SchDoc, .PcbDoc, images) are opaque
# and third-party library files are not ours to restyle.
CHECKED_SUFFIXES = {
    ".py", ".pas", ".md", ".txt", ".toml", ".cfg", ".yml", ".yaml",
    ".html", ".css", ".js", ".json",
}

# Vendored or externally-authored text that must stay byte-identical.
EXEMPT_PREFIXES = (
    "reference/",
    "tests/fixtures/",
)


def _tracked_text_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.splitlines()
    files = []
    for rel in out:
        if not rel or rel.startswith(EXEMPT_PREFIXES):
            continue
        path = REPO / rel
        if path.suffix.lower() in CHECKED_SUFFIXES and path.is_file():
            files.append(path)
    return files


def test_no_em_or_en_dashes_in_tracked_sources():
    offenders = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in DASH_RE.finditer(line):
                rel = path.relative_to(REPO).as_posix()
                offenders.append(
                    f"{rel}:{lineno} ({DASHES[match.group()]}): {line.strip()[:90]}"
                )

    assert not offenders, (
        f"{len(offenders)} em/en dash(es) found. Use a comma for an aside, a "
        "colon when what follows explains what precedes, a semicolon between "
        "two independent clauses, or a hyphen in a numeric range:\n  "
        + "\n  ".join(offenders[:25])
    )


def test_the_check_actually_looks_at_the_repo():
    """A guard that finds no files would pass silently forever."""
    files = _tracked_text_files()
    assert len(files) > 100, f"only {len(files)} files scanned; the glob is wrong"
    assert any(f.suffix == ".py" for f in files)
    assert any(f.suffix == ".pas" for f in files)
    assert any(f.suffix == ".md" for f in files)


def test_the_pattern_matches_a_real_dash():
    """Cheap proof the regex is not a no-op after an encoding change."""
    assert DASH_RE.search("a " + _EM + " b")
    assert DASH_RE.search("5" + _EN + "10")
    assert not DASH_RE.search("a - b")
    assert not DASH_RE.search("non-blocking")
