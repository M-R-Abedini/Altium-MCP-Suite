# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Pytest fixtures for real-Altium integration tests.

Unlike the simulator-based tests in the parent ``tests/`` directory, these
fixtures wire to a live Altium Designer process. Setting
``EDA_AGENT_INTEGRATION=1`` opts in; without it every test here is skipped
at collection, before any fixture runs. In CI mode the env var is set and
missing preconditions become hard failures rather than skips.

**Why the gate is at collection and not inside the fixtures.** It used to
be inside them: ``real_bridge`` constructed a bridge, called
``is_altium_running()``, then pinged, and only skipped once the ping came
back empty. So a plain ``pytest tests/`` on a machine with Altium open
sent real requests into the user's live session. Worse, ``project.open``
in ``fixture_project_loaded`` runs before anything can skip, so a run with
the polling loop up would have opened the fixture project in whatever
Altium the user was working in and taken the focus.

Skipping is what these tests do by default, so the cheapest correct
version of that is to skip without touching the bridge at all. A test
that must reach a live Altium to discover it should not have is not
isolated, whatever its result says.
"""

import os
from pathlib import Path

import pytest

from eda_agent.bridge.altium_bridge import AltiumBridge, PROTOCOL_VERSION
from eda_agent.bridge.exceptions import AltiumNotRunningError, AltiumTimeoutError


FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE_PROJECT = FIXTURES_DIR / "EDAAgentTest.PrjPcb"

REQUIRED_ENV = "EDA_AGENT_INTEGRATION"

_HERE = Path(__file__).resolve().parent


def pytest_collection_modifyitems(config, items):
    """Skip every live-Altium test unless the opt-in env var is set.

    Marking at collection means no fixture in this directory is ever
    entered, so no bridge is constructed and no request file is written.
    """
    if os.environ.get(REQUIRED_ENV) == "1":
        return
    skip = pytest.mark.skip(
        reason=(f"live-Altium test: set {REQUIRED_ENV}=1 to run. Skipped "
                "without contacting the bridge, so a running Altium "
                "session is left untouched."))
    for item in items:
        try:
            path = Path(str(item.fspath)).resolve()
        except (OSError, ValueError):
            continue
        if path == _HERE or _HERE in path.parents:
            item.add_marker(skip)


def _enforce_or_skip(reason: str) -> None:
    """Hard-fail in CI mode (env set), soft-skip otherwise."""
    if os.environ.get(REQUIRED_ENV) == "1":
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def real_bridge() -> AltiumBridge:
    """Bridge bound to a live Altium with the script loaded.

    Tests that use this fixture run against a real Altium process. The fixture
    pings Altium first to verify the polling loop is active and the protocol
    versions match.
    """
    bridge = AltiumBridge()
    if not bridge.is_altium_running():
        _enforce_or_skip(
            "Altium Designer is not running. Start it and load Altium_API.PrjScr."
        )

    info = bridge.ping_with_version()
    if info is None:
        _enforce_or_skip(
            "Altium responded but ping failed, is StartMCPServer running in the script project?"
        )

    bridge.attach()
    yield bridge
    bridge.detach()


@pytest.fixture(scope="session")
def fixture_project_loaded(real_bridge) -> Path:
    """Ensure the EDAAgentTest fixture project is loaded into Altium.

    Returns the project path. The fixture project is checked into the repo
    at tests/integration/fixtures/EDAAgentTest.PrjPcb and contains a known
    set of components/sheets/PCB primitives the tests can rely on.
    """
    if not FIXTURE_PROJECT.exists():
        _enforce_or_skip(
            f"Fixture project missing: {FIXTURE_PROJECT}. "
            "See tests/integration/fixtures/README for how to (re)build it."
        )

    # Open the project (idempotent, Altium ignores duplicate opens).
    real_bridge.send_command(
        "project.open", {"project_path": str(FIXTURE_PROJECT)}, timeout=15.0
    )

    # Verify the focused project matches.
    focused = real_bridge.send_command("project.get_focused", timeout=5.0)
    if not focused or "EDAAgentTest" not in (focused.get("project_name") or ""):
        _enforce_or_skip(
            "Could not focus the EDAAgentTest fixture project after opening."
        )

    yield FIXTURE_PROJECT
