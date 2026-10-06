# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Client design data must never reach this repository.

The work is done against real, NDA-covered client projects, so every
live session handles project names, sheet names, designators and part
numbers that belong to someone else. Writing one into a comment is a
single keystroke and nothing downstream notices.

MEASURED: it happened. A handler comment recorded a precondition as
"with <a client sheet> focused", and six scratchpad probe files carried
the full absolute path to a client project. None of it was caught by
anything.

THIS GUARD NAMES NO CLIENT. A test that lists forbidden strings puts
those strings in the repository permanently and matches itself, which
is the trap the em-dash guard already documents. It matches the SHAPE
of a leak instead: an absolute path into somebody's working directory.
That is what a copied-in path looks like, and it does not depend on
knowing whose project it was.

Deliberately NOT checked here: whether a document stem like a part
number is "real". Public part numbers are legitimate in fixtures, the
corpus is already genericised, and a stem allowlist would be noise that
gets suppressed rather than fixed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]

#: Where our own code lives. reference/ is a vendored third-party
#: corpus and is not ours to rewrite.
_OWNED = ("src", "tests", "scripts/altium", "docs", "extensions")

_SUFFIXES = {".py", ".pas", ".md", ".json", ".js", ".ts", ".toml"}

_SKIP_PARTS = {"__pycache__", "node_modules", ".git", "dist"}

#: An absolute Windows path reaching into a user's own storage. A
#: client project path always looks like this, and nothing this repo
#: legitimately ships does.
_USER_PATH = re.compile(
    r"[A-Za-z]:\\{1,2}(?:Dropbox|Users|OneDrive)\\{1,2}[A-Za-z0-9_. -]+",
    re.IGNORECASE)

#: Placeholders that are obviously not anyone's real directory. Keep
#: this list tiny: every entry is a hole in the guard.
_SYNTHETIC = ("users\\test", "users\\\\test",
              "users\\user", "users\\\\user",
              "users\\username", "users\\\\username")


def _owned_files():
    for root in _OWNED:
        base = _REPO / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in _SUFFIXES:
                continue
            if _SKIP_PARTS & set(path.parts):
                continue
            yield path
    for path in _REPO.glob("*.md"):
        yield path


def _leaks(text: str):
    for hit in _USER_PATH.findall(text):
        low = hit.lower()
        if any(marker in low for marker in _SYNTHETIC):
            continue
        yield hit


@pytest.fixture(scope="module")
def offenders():
    found = []
    for path in _owned_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:                          # pragma: no cover - guard
            continue
        for hit in _leaks(text):
            found.append((path.relative_to(_REPO).as_posix(), hit))
    return found


def test_no_absolute_path_into_a_users_own_storage(offenders):
    """The shape a copied-in client path takes.

    Generic examples belong in the docs; a path that resolves on one
    machine and names a real project does not.
    """
    assert not offenders, (
        "absolute paths into private storage found. Replace with a "
        "generic example, or a tmp_path fixture in a test:\n  "
        + "\n  ".join(f"{where}: {what}" for where, what in offenders))


def test_the_guard_covers_the_files_that_get_committed():
    """A guard over an empty file set passes for the wrong reason.

    Untracked files are in scope on purpose: the leak that prompted
    this would have arrived in a brand new file.
    """
    files = list(_owned_files())
    assert len(files) > 200, (
        f"only {len(files)} files scanned, which is too few to be the "
        f"whole repository; the roots or suffix list have drifted")
    roots = {Path(f).relative_to(_REPO).parts[0] for f in files}
    for expected in ("src", "tests", "scripts", "docs"):
        assert expected in roots, f"{expected} is not being scanned"


def test_the_pattern_actually_matches_a_leak():
    """Prove the detector fires, using a fabricated path.

    Without this the guard passes identically whether the pattern works
    or matches nothing at all, which is the failure mode that let the
    real leak through in the first place.
    """
    # ASSEMBLED, never written out. Caught by this file's own last
    # test on the first run: a literal probe path is itself a match, so
    # the guard failed on the guard. Building it from fragments keeps
    # the pattern testable without putting a matchable string on disk.
    sep = chr(92)
    fake = sep.join(["C:", "Dropbox", "Work", "Co_Widget", "Board.PcbDoc"])
    assert list(_leaks(fake)), "the detector does not detect"

    doubled = (sep * 2).join(["C:", "Users", "somebody", "thing.PrjPcb"])
    assert list(_leaks(doubled)), (
        "a doubled backslash is how a path looks inside a Python or "
        "Pascal string literal, which is exactly where one gets pasted")


def test_synthetic_placeholders_are_still_allowed():
    """The guard must not push people into deleting useful examples."""
    sep = chr(92)
    assert not list(_leaks(
        sep.join(["C:", "Users", "test", "workspace", "thing.PrjPcb"])))
    assert not list(_leaks(
        sep.join(["C:", "Users", "USERNAME", "Documents"])))


def test_this_guard_names_no_client():
    """The self-match trap, asserted rather than assumed.

    A deny-list guard has to contain the very strings it forbids, so it
    fails on itself the moment it works. This one is pattern-based, and
    that property is worth pinning: a future edit that "helpfully" adds
    a literal project name would bake it into the repository forever.
    """
    text = Path(__file__).read_text(encoding="utf-8")
    hits = [h for h in _leaks(text)]
    assert not hits, (
        f"this file must not contain a real path either: {hits}")
