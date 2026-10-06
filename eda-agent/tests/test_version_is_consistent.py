# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The version is written in two places and both must agree.

``pyproject.toml`` decides what pip installs and what PyPI shows.
``eda_agent.__version__`` is what the running program reports: it is
what ``eda-agent --version`` prints and what the SARIF output stamps
into ``tool_version``. Bumping one and not the other produces a build
whose own reports disagree with the package that contains them, and a
bug report whose stated version is wrong.

Nothing tied them together before, and they are edited by hand at
release time, which is the moment least likely to catch it.

Also checks that ``--version`` exists at all. Both
``.github/ISSUE_TEMPLATE/bug_report.md`` and ``CONTRIBUTING.md`` tell
reporters to run it, and until this test was written the flag was not
defined, so the first instruction a bug reporter followed produced an
argparse error.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PYPROJECT = REPO / "pyproject.toml"


def _pyproject_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    # The [project] version, not a dependency pin: anchored to the start
    # of a line and taken before any [tool.*] section begins.
    head = text.split("\n[tool.", 1)[0]
    match = re.search(r'^version\s*=\s*"([^"]+)"', head, re.M)
    assert match, "no [project] version found in pyproject.toml"
    return match.group(1)


def test_package_version_matches_pyproject():
    from eda_agent import __version__

    declared = _pyproject_version()
    assert __version__ == declared, (
        f"eda_agent.__version__ is {__version__!r} but pyproject.toml "
        f"declares {declared!r}. Bump both, or the installed package "
        f"reports a version it is not.")


def test_the_version_looks_like_a_version():
    """Guards against an empty or placeholder string passing the match."""
    declared = _pyproject_version()
    assert re.fullmatch(r"\d+\.\d+\.\d+([.-]\w+)?", declared), (
        f"{declared!r} is not a recognisable version")


def test_the_cli_reports_the_version():
    """`eda-agent --version` must work and agree with the package.

    Run as a subprocess rather than by calling ``main``: ``--version``
    is an argparse action that exits, and the point is that the command
    a user is told to run actually succeeds.
    """
    from eda_agent import __version__

    result = subprocess.run(
        [sys.executable, "-m", "eda_agent.server", "--version"],
        capture_output=True, text=True, encoding="utf-8", cwd=REPO,
    )
    assert result.returncode == 0, (
        f"`--version` exited {result.returncode}. The bug-report template "
        f"tells reporters to run it.\nstderr: {result.stderr[:400]}")
    assert __version__ in result.stdout, (
        f"`--version` printed {result.stdout.strip()!r}, which does not "
        f"contain the package version {__version__!r}")


def _script_version() -> str:
    text = (REPO / "scripts" / "altium" / "Main.pas").read_text(
        encoding="utf-8", errors="replace")
    match = re.search(r"SCRIPT_VERSION\s*=\s*'([^']+)'", text)
    assert match, "SCRIPT_VERSION not found in Main.pas"
    return match.group(1)


def test_the_release_procedure_names_the_current_script_version():
    """Step 0 tells the verifier which version to expect from app_ping.

    That number is written into the document by hand. Bumping
    SCRIPT_VERSION without it makes the first instruction wrong in the
    worst direction: the verifier pings a correctly deployed script,
    sees a version that does not match what the page told them to
    expect, and concludes the deploy failed. Everything after step 0
    then gets read as suspect.

    Skipped rather than failed when the document is absent: it is a
    per-release artifact, and a release that has not written one yet
    should not be blocked by this.
    """
    doc = REPO / "docs" / "RELEASE_VERIFICATION.md"
    if not doc.is_file():
        pytest.skip("no docs/RELEASE_VERIFICATION.md for this release")

    text = doc.read_text(encoding="utf-8", errors="replace")
    version = _script_version()
    assert version in text, (
        f"docs/RELEASE_VERIFICATION.md does not mention SCRIPT_VERSION "
        f"{version!r}. Step 0 tells the reader which version app_ping "
        f"should report, so a stale number there reads as a failed "
        f"deploy of a correct build.")

    # And it must not still name an older one alongside it.
    others = {v for v in re.findall(r"\b20\d\d\.\d\d\.\d\d\.\d+\b", text)
              if v != version}
    assert not others, (
        f"the release procedure names other script versions {sorted(others)} "
        f"as well as the current {version!r}; the reader cannot tell which "
        f"app_ping should report.")


def test_the_release_procedure_names_the_current_package_version():
    """Step 0 pins the package version too, and it drifts the same way.

    ``app_ping`` returns ``mcp_server_version`` beside
    ``altium_script_version``, and the procedure tells the reader what
    both should say. The script version already has a check above; this
    is the same defect for the other number, and the same failure mode:
    a stale figure makes a correct install look broken.

    It bites harder here because the package version is bumped by hand
    at release time, the moment this document is also being rewritten.
    """
    doc = REPO / "docs" / "RELEASE_VERIFICATION.md"
    if not doc.is_file():
        pytest.skip("no docs/RELEASE_VERIFICATION.md for this release")

    text = doc.read_text(encoding="utf-8", errors="replace")
    if "mcp_server_version" not in text:
        pytest.skip("the procedure does not pin the package version")

    version = _pyproject_version()
    assert f"`{version}`" in text, (
        f"docs/RELEASE_VERIFICATION.md pins mcp_server_version but does "
        f"not name the current package version {version!r}. A reader "
        f"comparing app_ping against a stale number would read a correct "
        f"install as a failed one.")


def test_the_docs_that_reference_the_flag_still_do():
    """If the docs stop asking for it, this file's premise changes.

    Not a style rule. The CLI flag exists because those two documents
    instruct users to run it; if both stop, revisit whether the flag and
    this test are still earning their place.
    """
    referenced_in = [
        p for p in (REPO / ".github" / "ISSUE_TEMPLATE" / "bug_report.md",
                    REPO / "CONTRIBUTING.md")
        if p.is_file() and "eda-agent --version" in p.read_text(
            encoding="utf-8", errors="replace")
    ]
    assert referenced_in, (
        "neither the bug-report template nor CONTRIBUTING mentions "
        "`eda-agent --version` any more")
