# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The server module itself starts on every backend.

Everything else builds a server by calling register_backend into a
throwaway registry. That is the right shape for a unit test and it
skips the thing a user's MCP client actually does: import
``eda_agent.server``, whose registration happens at MODULE level and
is keyed on ``EDA_AGENT_BACKEND``. An import-time failure there means
the server does not start at all, and no other test would see it.

Each case runs in a SUBPROCESS because that module-level state cannot
be re-initialised in-process: importing it once fixes the backend for
the life of the interpreter, so an in-process loop would test the
first backend three times.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

_PROBE = """
import asyncio, json, warnings
warnings.filterwarnings("ignore")
import eda_agent.server as s
tools = asyncio.run(s.mcp.list_tools())
print(json.dumps({
    "backend": s.ACTIVE_BACKEND,
    "name": s.mcp.name,
    "count": len(tools),
    "names": sorted(t.name for t in tools),
}))
"""

#: A floor, not a target. The point is "the surface is there", not a
#: number that has to be edited whenever a tool is added.
_MINIMUM = {"altium": 300, "easyeda": 150, "kicad": 80}

#: One tool per backend that must be reachable, chosen because its
#: absence would mean that backend's own registration silently did
#: not run.
_SIGNATURE = {
    "altium": "app_ping",
    "easyeda": "easyeda_ping",
    "kicad": "kicad_ping",
}


def _start(backend: str) -> dict:
    # INHERIT the environment and override one variable. Building a
    # minimal env from scratch dropped whatever makes the package
    # importable and every case failed with ModuleNotFoundError, which
    # tests the harness rather than the server.
    import os

    env = dict(os.environ)
    env["EDA_AGENT_BACKEND"] = backend
    env.pop("EDA_AGENT_TOOLSET", None)          # full surface, not minimal
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True, text=True, timeout=180, env=env,
    )
    assert proc.returncode == 0, (
        f"the server failed to start on {backend}:\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("backend", ["altium", "easyeda", "kicad"])
def test_the_server_starts_and_registers_its_surface(backend):
    info = _start(backend)
    assert info["backend"] == backend, (
        f"asked for {backend}, the server came up as {info['backend']}")
    assert info["name"] == f"eda-agent-{backend}"
    assert info["count"] >= _MINIMUM[backend], (
        f"{backend} advertised only {info['count']} tools, below the "
        f"{_MINIMUM[backend]} floor: registration probably part-failed")


@pytest.mark.parametrize("backend", ["altium", "easyeda", "kicad"])
def test_the_backend_own_tools_are_actually_there(backend):
    """A server that starts having registered only the shared tools
    looks healthy and can do nothing with the editor."""
    info = _start(backend)
    assert _SIGNATURE[backend] in info["names"], (
        f"{backend} started without {_SIGNATURE[backend]}, so its own "
        f"registration did not run")


def test_the_backends_do_not_advertise_each_others_tools():
    """Selecting a backend is how a user gets a surface they can
    actually call; leaking the other's tools hands them dead ends."""
    easyeda = set(_start("easyeda")["names"])
    altium = set(_start("altium")["names"])

    assert not {n for n in easyeda if n.startswith("kicad_")}
    assert not {n for n in altium if n.startswith("easyeda_")}
    assert "design_execute_plan" in altium
    assert "design_execute_plan" not in easyeda, (
        "the Altium-only plan executor is advertised on EasyEDA, where "
        "it would fail at its first bridge call")
