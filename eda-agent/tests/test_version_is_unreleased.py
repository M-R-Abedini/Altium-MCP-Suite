# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The declared version must not be one that already shipped.

``tests/test_version_is_consistent.py`` checks that the two places the
version is written agree with each other. Both can agree and still be
wrong: after a release is tagged, the tree keeps the released number
until someone remembers to bump it, and nothing notices.

That is what happened. ``v0.4.0`` was tagged, then sixteen commits
landed on top while ``pyproject.toml`` and ``eda_agent.__version__``
both still said ``0.4.0``. Anyone installing from source got a build
calling itself a version that had already shipped as something else.

It matters most for bug reports. ``.github/ISSUE_TEMPLATE/bug_report.md``
asks reporters to paste ``eda-agent --version``, so a stale number sends
a maintainer to the wrong tree to reproduce, and the reporter looks
wrong when the bug is not there.

The check is deliberately one-directional: it fails only when the
declared version is FOUND among the tags. A repository with no tags at
all is inconclusive rather than broken, which is the shallow-clone case
in CI, so that passes. What it will not do is pass quietly when git
itself failed, because "the command errored so nothing matched" is the
shape of a check that has stopped checking.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PYPROJECT = REPO / "pyproject.toml"


def _declared_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    head = text.split("\n[tool.", 1)[0]
    match = re.search(r'^version\s*=\s*"([^"]+)"', head, re.M)
    assert match, "no [project] version found in pyproject.toml"
    return match.group(1)


def _git(*args) -> subprocess.CompletedProcess:
    # encoding is explicit: text=True would decode with the locale codec,
    # which is cp1252 here and dies on bytes it cannot map.
    return subprocess.run(["git", *args], cwd=str(REPO), capture_output=True,
                          encoding="utf-8", errors="replace")


def _tags() -> list[str]:
    result = _git("tag", "--list")
    if result.returncode != 0:
        pytest.skip(f"git tag failed: {result.stderr.strip()[:120]}")
    return [t.strip() for t in result.stdout.splitlines() if t.strip()]


def test_git_is_usable_here():
    """Guard the guard: a failing git must not read as 'no tags matched'."""
    result = _git("rev-parse", "--git-dir")
    if result.returncode != 0:
        pytest.skip("not a git checkout")
    assert result.stdout.strip(), "git answered but named no git dir"


def test_the_declared_version_was_not_already_tagged():
    tags = _tags()
    if not tags:
        # Shallow clone or a fresh repo. Inconclusive, not a failure.
        return
    version = _declared_version()
    clashing = [t for t in tags if t.lstrip("vV") == version]
    assert not clashing, (
        f"pyproject.toml declares version {version}, but {clashing[0]} is "
        "already tagged, so this tree calls itself a version that has "
        "already shipped. Bump the version in pyproject.toml AND "
        "src/eda_agent/__init__.py before releasing. Commits since that "
        f"tag: {len(_commits_since(clashing[0]))}.")


def test_ci_fetches_the_tags_this_check_needs():
    """The precondition, asserted, because without it this file is inert.

    ``test_the_declared_version_was_not_already_tagged`` treats "no tags"
    as inconclusive and passes. That is right for a developer's shallow
    clone and wrong for CI, which is the only place these run unattended:
    ``actions/checkout`` fetches no tags by default, so the check would
    find none, pass, and report nothing forever.

    So the workflow must ask for them. This asserts that it still does,
    since deleting one line of YAML would otherwise silently disarm the
    check without touching a single test.
    """
    workflow = REPO / ".github" / "workflows" / "tests.yml"
    if not workflow.is_file():
        pytest.skip("no tests.yml workflow in this checkout")
    text = workflow.read_text(encoding="utf-8")
    assert "actions/checkout" in text, (
        "the workflow no longer checks out the repo; this test is reading "
        "the wrong file")
    # Comments do not count. The comment above the checkout step explains
    # why fetch-depth is set and therefore contains the literal string, so
    # a substring search over the whole file stays green after the actual
    # setting is deleted. Verified by mutation: it did.
    settings = {line.split("#", 1)[0].strip()
                for line in text.splitlines()
                if not line.lstrip().startswith("#")}
    fetches_tags = ("fetch-depth: 0" in settings) or \
                   ("fetch-tags: true" in settings)
    assert fetches_tags, (
        "the CI checkout does not fetch tags (needs 'fetch-depth: 0' or "
        "'fetch-tags: true'), so test_the_declared_version_was_not_already"
        "_tagged sees an empty tag list and passes without checking "
        "anything. A version that has already shipped would reach a "
        "release unnoticed.")


def _commits_since(tag: str) -> list[str]:
    result = _git("log", "--oneline", f"{tag}..HEAD")
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def test_the_version_is_ahead_of_every_tag_that_parses():
    """A bump must go forwards, so 0.3.0 after v0.4.0 also fails."""
    tags = _tags()
    if not tags:
        return
    version = _declared_version()

    def parts(text: str):
        bits = text.lstrip("vV").split(".")
        if len(bits) < 2 or not all(b.isdigit() for b in bits[:3]):
            return None
        return tuple(int(b) for b in bits[:3])

    mine = parts(version)
    if mine is None:
        pytest.skip(f"declared version {version!r} is not plain numeric")
    higher = [t for t in tags
              if (p := parts(t)) is not None and p >= mine]
    assert not higher, (
        f"declared version {version} is not ahead of existing tag(s) "
        f"{sorted(higher)[:3]}. A release number must move forwards.")
