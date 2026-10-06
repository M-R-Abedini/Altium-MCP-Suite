# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""``eda-agent health`` must notice a stale deployed script.

Altium compiles the copy in the install directory, not the one in this
checkout. Edit Pascal, forget ``install-scripts --force``, and Altium
keeps running the old code: a bug you just fixed reproduces exactly as
before, and the only other place the mismatch shows is ``app_ping``'s
``version_match``, which needs Altium running and the polling loop up.

So it is the one live-session problem worth catching offline. From
inside a session it costs hours to diagnose, because every symptom
points at the code you are looking at. From outside it is one file read.

``doctor`` already reports the version, but through ``_ping_altium()``,
so it cannot help before Altium is started. ``health`` is the offline
half and had four checks, none of which compared the two copies.

WARN, not FAIL. Nothing offline is affected by a stale deploy, and a
developer who has never run ``install-scripts`` is not broken, just not
set up yet, which is why that case is SKIP.
"""

from __future__ import annotations

import pathlib

import pytest

from eda_agent.diag import health
from eda_agent.diag.checks import Status

_PAS = "Const\n    SCRIPT_VERSION = '{v}';\n"


@pytest.fixture
def deploy(tmp_path, monkeypatch):
    """Redirect both the bundled and the installed script directories."""
    bundled = tmp_path / "bundled"
    installed = tmp_path / "installed"
    bundled.mkdir()
    installed.mkdir()

    import eda_agent.cli as cli
    monkeypatch.setattr(cli, "get_bundled_scripts_path", lambda: bundled)
    monkeypatch.setattr(cli, "get_default_scripts_dest", lambda: installed)
    return bundled, installed


def _write(d: pathlib.Path, version: str) -> None:
    (d / "Main.pas").write_text(_PAS.format(v=version), encoding="utf-8")


def test_matching_versions_pass(deploy):
    bundled, installed = deploy
    _write(bundled, "2026.08.04.10")
    _write(installed, "2026.08.04.10")

    check = health._check_deployed_scripts_current()

    assert check.status is Status.PASS
    assert "2026.08.04.10" in check.message


def test_a_stale_deploy_warns_and_names_both_versions(deploy):
    """The defect this exists for."""
    bundled, installed = deploy
    _write(bundled, "2026.08.05.1")
    _write(installed, "2026.08.04.10")

    check = health._check_deployed_scripts_current()

    assert check.status is Status.WARN
    assert "2026.08.04.10" in check.message, "must say what is installed"
    assert "2026.08.05.1" in check.message, "must say what it should be"
    assert "install-scripts" in check.fix
    assert "--force" in check.fix, (
        "without --force the copy is skipped and the fix appears to do "
        "nothing")


def test_nothing_installed_is_a_skip_not_a_failure(deploy):
    """A developer who has never installed the scripts is not broken."""
    bundled, _ = deploy
    _write(bundled, "2026.08.05.1")

    check = health._check_deployed_scripts_current()

    assert check.status is Status.SKIP
    assert "install-scripts" in check.fix


def test_an_unreadable_version_warns_rather_than_claiming_a_match(deploy):
    """Silence on a missing SCRIPT_VERSION would read as agreement."""
    bundled, installed = deploy
    _write(bundled, "2026.08.05.1")
    (installed / "Main.pas").write_text("Const\n    OTHER = 1;\n",
                                        encoding="utf-8")

    check = health._check_deployed_scripts_current()

    assert check.status is Status.WARN
    # Not just the status: without its own branch this falls through to
    # the comparison and reports "installed None, this tree has X",
    # which reads like a version rather than a failed read. Mutation
    # found exactly that, because asserting WARN alone still passed.
    assert "could not read" in check.message.lower()
    assert "None" not in check.message


def test_the_version_parser_handles_absence():
    assert health._script_version(pathlib.Path("no_such_file.pas")) is None


# --------------------------------------------------------------------
# Same version, different code. The quiet half of a stale deploy, and
# the half this check missed for two days in real use: Main.pas and
# Library.pas had been edited without bumping SCRIPT_VERSION, so both
# copies read 2026.08.10.2 while the deployed one was 51 lines behind,
# and the check reported "matches this tree".
# --------------------------------------------------------------------

def test_same_version_but_different_code_is_caught(deploy):
    """A version-only check cannot detect its own motivating scenario.

    The bump is manual, so the state that produces a stale deploy is
    exactly the state where the version still agrees.
    """
    bundled, installed = deploy
    _write(bundled, "2026.08.10.2")
    _write(installed, "2026.08.10.2")
    (bundled / "Library.pas").write_text(
        "Procedure Foo;\nBegin\n    Bar('with the fix');\nEnd;\n",
        encoding="utf-8")
    (installed / "Library.pas").write_text(
        "Procedure Foo;\nBegin\n    Bar('without it');\nEnd;\n",
        encoding="utf-8")

    check = health._check_deployed_scripts_current()

    assert check.status is Status.WARN, (
        "identical SCRIPT_VERSION with different code is the stale "
        "deploy that costs a session to diagnose")
    assert "Library.pas" in check.message, (
        "naming the file is what turns this into a one-command fix")
    assert "SCRIPT_VERSION" in check.fix, (
        "the fix must say the bump was skipped, not just to reinstall")


def test_a_file_missing_from_the_deploy_is_caught(deploy):
    """A partial install leaves a unit behind, which reads as absent."""
    bundled, installed = deploy
    _write(bundled, "2026.08.10.2")
    _write(installed, "2026.08.10.2")
    (bundled / "PCB.pas").write_text("Procedure Baz;\n", encoding="utf-8")

    check = health._check_deployed_scripts_current()

    assert check.status is Status.WARN
    assert "PCB.pas" in check.message


def test_line_endings_alone_are_not_a_difference(deploy):
    """The install copies across directories; CRLF is not a code change.

    Without this the check would warn on every Windows deploy, and a
    warning that is always on is one nobody reads.
    """
    bundled, installed = deploy
    _write(bundled, "2026.08.10.2")
    _write(installed, "2026.08.10.2")
    (bundled / "Utils.pas").write_text("Procedure A;\nBegin\nEnd;\n",
                                       encoding="utf-8")
    (installed / "Utils.pas").write_bytes(
        b"Procedure A;\r\nBegin\r\nEnd;\r\n")

    check = health._check_deployed_scripts_current()

    assert check.status is Status.PASS, (
        f"CRLF alone must not warn, got: {check.message}")


def test_the_check_runs_as_part_of_health(deploy):
    """Wired in, not merely defined."""
    bundled, installed = deploy
    _write(bundled, "2026.08.04.10")
    _write(installed, "2026.08.04.10")

    names = [c.name for c in health.run_health_checks()]
    assert any("deployed scripts" in n for n in names), names


def test_health_still_reports_the_other_checks(deploy):
    """Adding one must not displace the existing four."""
    bundled, installed = deploy
    _write(bundled, "1.0")
    _write(installed, "1.0")

    checks = health.run_health_checks()
    assert len(checks) >= 5
    joined = " ".join(c.name for c in checks)
    for expected in ("workspace dir", "workspace pointer",
                     "bundled DelphiScript project", "bridge constructable"):
        assert expected in joined, f"{expected} disappeared from health"
