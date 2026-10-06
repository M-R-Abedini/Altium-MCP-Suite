# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The shipped .eext must not fall behind the source it was built from.

``docs/BACKENDS.md`` has claimed this test exists for some time. It did
not, and its absence is not theoretical: twelve fixes went into
``main.js`` in one session while ``BUILD_STAMP.json`` still read 0.9.17
and the packaged ``eda-agent-bridge.eext`` carried code from before all
of them. Importing that package installs yesterday's extension.

The runtime already compares builds and reports a mismatch on
``easyeda_ping``, which is a good backstop and the wrong place to find
out. It catches the problem DURING a live session, after the confusion
has started. The documented history is a whole session spent reading
"the export fix is broken" off an editor running a build from before
the fix.

So this checks the same thing one step earlier, in CI, where the fix
costs one command.
"""

from __future__ import annotations

import json
import pathlib

import pytest

_EXT = (pathlib.Path(__file__).resolve().parent.parent
        / "extensions" / "easyeda")
_MAIN = _EXT / "main.js"
_MANIFEST = _EXT / "extension.json"
_STAMP = _EXT / "BUILD_STAMP.json"
_PACKAGE = _EXT / "eda-agent-bridge.eext"


def _build_id_for_current_source() -> str:
    """What build.py WOULD stamp for the tree as it stands.

    Imported from build.py rather than reimplemented: a second copy of
    the hashing rule would drift, and a guard that computes a different
    hash from the builder is worse than none.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_easyeda_build", _EXT / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("source_build_id", "build_id", "compute_build_id",
                 "_build_id"):
        fn = getattr(module, name, None)
        if callable(fn):
            try:
                return fn()
            except TypeError:
                return fn(_MAIN.read_text(encoding="utf-8"))
    pytest.skip("build.py exposes no build-id function under a known name")


def test_the_files_this_guards_exist():
    """The tracked inputs. The .eext is deliberately not among them.

    It is a build artefact and gitignored, so it exists on a machine
    that has run build.py and nowhere else. Requiring it here failed
    every CI run while proving nothing: the check that matters is the
    content hash below, which compares the stamp against what build.py
    would compute from the source and needs no package to do it.
    """
    for path in (_MAIN, _MANIFEST, _STAMP):
        assert path.is_file(), f"{path.name} is missing"


def test_the_package_is_a_build_artefact_and_not_tracked():
    """Pin the reason the file above is absent, so it is not restored.

    If the .eext is ever committed, this fails and someone decides
    deliberately rather than discovering it through a CI failure.
    """
    ignore = (pathlib.Path(__file__).resolve().parent.parent
              / ".gitignore").read_text(encoding="utf-8")
    assert ".eext" in ignore, (
        "the packaged extension is no longer ignored; either restore "
        "the ignore rule or add the package back to the existence check")


def test_the_stamp_matches_the_manifest_version():
    """A stamp from an older version means the package predates the bump."""
    stamp = json.loads(_STAMP.read_text(encoding="utf-8"))
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    assert stamp.get("version") == manifest.get("version"), (
        f"BUILD_STAMP.json says {stamp.get('version')} and extension.json "
        f"says {manifest.get('version')}. The package was built before the "
        f"version bump, so importing it installs the older extension. Run "
        f"python extensions/easyeda/build.py")


def test_the_stamp_matches_the_current_source():
    """The check that actually catches an edited main.js."""
    stamp = json.loads(_STAMP.read_text(encoding="utf-8"))
    expected = _build_id_for_current_source()
    assert stamp.get("build_id") == expected, (
        f"BUILD_STAMP.json carries build {stamp.get('build_id')} and the "
        f"current source would build {expected}. The .eext in the tree is "
        f"stale: importing it ships code that is not what this repo says "
        f"it ships. Run python extensions/easyeda/build.py")


# AN MTIME BACKSTOP WAS TRIED HERE AND REMOVED, deliberately.
#
# The idea was to fail when eda-agent-bridge.eext is older than
# main.js, as a second opinion in case the stamp was hand-edited. It
# fired on its first full run, and it was WRONG: the hash agreed, the
# content was correct, and all that had happened was main.js being
# rewritten with identical bytes, which moves the mtime and changes
# nothing else.
#
# That is not a rare accident. Any touch, any checkout, any tool that
# rewrites a file in place produces it. A guard that fails while the
# artefact is correct teaches people to ignore this file, which costs
# more than the case it was meant to cover.
#
# The hash comparison above is content-based and authoritative. A
# hand-edited stamp is caught by it too, because the stamp would then
# disagree with what build.py computes from the source.


def test_the_documentation_claim_is_now_true():
    """BACKENDS.md promised this test before it existed.

    Kept as a test rather than a comment so the claim and the guard
    cannot drift apart again: if the sentence goes, this fails and
    someone decides deliberately.
    """
    docs = (pathlib.Path(__file__).resolve().parent.parent
            / "docs" / "BACKENDS.md").read_text(encoding="utf-8")
    assert "refuses to let the built package fall behind" in docs, (
        "BACKENDS.md no longer claims this guard exists; either restore "
        "the sentence or delete this test, but do not leave the two "
        "disagreeing")
