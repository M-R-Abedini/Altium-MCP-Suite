# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Bring the vendored reference clones up to their upstream HEAD.

WHY THIS EXISTS. ``reference/`` is gitignored, so nothing in the repo
records which commit of a third-party reference this tree was read
against, and nothing notices when one drifts. CI clones the EasyEDA
reference fresh on every run, so it always sees upstream HEAD; a
developer machine sees whatever was cloned, possibly years ago.

That gap is not theoretical. The EasyEDA reference here sat three weeks
behind, and in that time the vendor changed the quoting in its enum
tables and moved the prose from Chinese to English. A guard that parsed
those tables passed locally and failed the moment CI put the current
version in front of it. Same code, same test, different reference.

Only ``easyeda-api-skill`` is read by code or tests. The rest are
documentation, and are updated too because a stale answer to "what does
this API do" is the expensive kind.

Usage:
    python scripts/update_reference.py             # all clones
    python scripts/update_reference.py easyeda     # substring match
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REFERENCE = Path(__file__).resolve().parents[1] / "reference"


def _git(repo: Path, *args: str) -> tuple[int, str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True)
    return result.returncode, (result.stdout + result.stderr).strip()


def _describe(repo: Path) -> str:
    _, out = _git(repo, "log", "-1", "--format=%h %ad", "--date=short")
    return out


def _default_branch(repo: Path) -> str | None:
    """The branch origin points at, without assuming it is called main.

    These clones span nine years of upstream history, so some are on
    master and some on main. Asking is cheaper than guessing wrong and
    reporting a repo as unreachable when it is simply named differently.
    """
    code, out = _git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if code == 0 and out.startswith("origin/"):
        return out.split("/", 1)[1]
    for guess in ("main", "master"):
        if _git(repo, "rev-parse", "--verify", f"origin/{guess}")[0] == 0:
            return guess
    return None


def update(repo: Path) -> tuple[str, str]:
    """Return (status, detail) for one clone. Never raises."""
    # A DIRTY CLONE IS LEFT ALONE. These are read-only references, so an
    # edit in one is far more likely to be an accident worth keeping
    # than a change worth discarding, and this script is not the place
    # to decide that.
    code, out = _git(repo, "status", "--porcelain")
    if code != 0:
        return "error", out.splitlines()[0] if out else "git status failed"
    if out:
        return "skipped", "has local changes"

    before = _describe(repo)

    # A SHALLOW CLONE CANNOT FAST-FORWARD. Its history stops at a
    # boundary, so the local commit is not reachable from upstream and
    # git reports the two as diverged when they are simply not joined
    # up. Measured here: a clone that was three weeks behind refused to
    # move with "cannot fast-forward", which reads as a conflict and was
    # nothing of the kind.
    #
    # Deepening is the non-destructive repair. It is done before the
    # ordinary fetch so the rest of this runs on connected history.
    if (repo / ".git" / "shallow").exists():
        code, out = _git(repo, "fetch", "--unshallow", "origin")
        if code != 0:
            return "error", "shallow, and could not be deepened"

    code, out = _git(repo, "fetch", "--quiet", "origin")
    if code != 0:
        return "error", out.splitlines()[-1] if out else "fetch failed"

    branch = _default_branch(repo)
    if branch is None:
        return "error", "no origin/main or origin/master to track"

    # FAST FORWARD ONLY. Anything that would need a merge means this
    # clone has diverged from upstream, which is a thing to look at
    # rather than to resolve automatically in a maintenance script.
    code, out = _git(repo, "merge", "--ff-only", f"origin/{branch}")
    if code != 0:
        return "error", "cannot fast-forward; the clone has diverged"

    after = _describe(repo)
    if before == after:
        return "current", after
    return "updated", f"{before}  ->  {after}"


def main(argv: list[str]) -> int:
    if not REFERENCE.is_dir():
        print(f"no {REFERENCE}", file=sys.stderr)
        return 1

    wanted = argv[1] if len(argv) > 1 else ""
    repos = sorted(d for d in REFERENCE.iterdir()
                   if (d / ".git").exists() and wanted.lower() in d.name.lower())
    if not repos:
        print(f"no reference clone matches {wanted!r}", file=sys.stderr)
        return 1

    width = max(len(d.name) for d in repos)
    failures = 0
    for repo in repos:
        status, detail = update(repo)
        if status == "error":
            failures += 1
        print(f"{repo.name:<{width}}  {status:<8} {detail}")

    # A FETCH THAT FAILED IS REPORTED AS A FAILURE. Otherwise this
    # prints a wall of green while leaving the reference exactly as
    # stale as it was, which is the state it exists to end.
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
