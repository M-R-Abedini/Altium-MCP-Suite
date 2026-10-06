# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every no-argument tool must survive being called.

About half the tool surface is never named by any test. The contract
guards cover those tools' WIRE contract (the command exists, in the
right category, and every parameter it sends is one the handler reads),
but nothing exercises the Python wrapper itself: the argument
marshalling, the defaults, the response shaping. A typo there surfaces
the first time a user calls the tool, which for a live_only tool means
in front of Altium.

This calls each one with no arguments against a fake bridge and asserts
it does not die of a Python-level fault. It is deliberately shallow:
it checks that the wrapper runs, not that its result is correct.

SAFETY, and the reason this file is written the way it is: a loose
script doing this reached a live Altium session, because patching
``get_bridge`` in ``tools/*`` is not enough. ``design/executor.py`` and
``core/backends.py`` import it LATE, inside the function
(``from eda_agent.bridge import get_bridge``), so they resolve the name
from the package at call time and never see a patch applied to
``tools``. Those late paths are exactly what the design, review and
audit tools delegate to.

So the isolation here is fail-closed rather than best-effort:

* the fake is installed at ``eda_agent.bridge.get_bridge`` (the name the
  late imports resolve), at ``altium_bridge.get_bridge``, and on every
  ``tools`` module that binds it at import time
* the singleton is cleared so nothing pre-existing is handed out
* ``AltiumBridge.__init__`` and both send methods are replaced with a
  tripwire that RAISES

If a path is ever added that escapes the patches, it constructs or uses
a real bridge, the tripwire fires, and the test fails. It cannot
silently reach Altium. ``test_the_tripwire_actually_trips`` proves the
tripwire works, so this guarantee is checked rather than asserted.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

TRIPWIRE = "a REAL AltiumBridge was constructed or used; isolation leaked"


class FakeBridge:
    """Answers anything with a plausible, empty, well-formed payload.

    The keys are the union of the containers callers unpack. A wrapper
    that reads a key not listed here gets a KeyError, which is a finding
    rather than noise: it means the wrapper assumes a shape the bridge
    does not promise.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    # The application tools reach past send_command for liveness and
    # attach/detach. Answering them keeps the fault list about wrapper
    # bugs rather than about what this stub happens to implement.
    def is_altium_running(self) -> bool:
        return True

    def ping(self, *_a, **_k) -> dict:
        return {"ok": True, "pong": True}

    def ping_with_version(self, *_a, **_k) -> dict:
        return {"ok": True, "altium_script_version": "0.0.0.0",
                "version_match": True}

    def get_altium_status(self) -> dict:
        return {"running": True, "attached": True, "responsive": True}

    def attach(self, *_a, **_k) -> dict:
        return {"attached": True}

    def detach(self, *_a, **_k) -> dict:
        return {"detached": True}

    def send_command(self, command, params=None, timeout=None):
        self.calls.append((command, params or {}))
        return {
            "success": True, "count": 0, "checked": 0, "violations": 0,
            "items": [], "components": [], "nets": [], "documents": [],
            "pins": [], "results": [], "pads": [], "layers": [],
        }

    async def send_command_async(self, command, params=None, timeout=None):
        return self.send_command(command, params, timeout)


@pytest.fixture
def isolated_bridge(monkeypatch, tmp_path):
    """Install the fake everywhere, and a tripwire behind it.

    The wiring lives in ``tests.conftest.install_bridge_fake`` so that
    every bulk-invoke test shares ONE proven isolation instead of
    re-deriving it; re-derived isolation is how both incidents happened.
    The proof tests below exercise the shared helper, so any test using
    it inherits a checked guarantee, not an asserted one.
    """
    from tests.conftest import install_bridge_fake

    fake = FakeBridge()
    install_bridge_fake(monkeypatch, tmp_path, fake)
    return fake


def test_the_tripwire_actually_trips(isolated_bridge):
    """Prove the fail-closed guarantee before relying on it.

    Without this, a tripwire that had been silently defanged (a rename,
    a new construction path) would let the smoke test below reach a
    live session while still passing.
    """
    from eda_agent.bridge import altium_bridge as ab
    with pytest.raises(AssertionError, match="isolation leaked"):
        ab.AltiumBridge()


def test_the_fake_is_what_every_import_path_resolves(isolated_bridge):
    """Both the package name and the module name must yield the fake.

    The package name is the one that matters: it is what the late
    in-function imports resolve, and it is the path that leaked.
    """
    import eda_agent.bridge as bridge_pkg
    from eda_agent.bridge import altium_bridge as ab
    assert bridge_pkg.get_bridge() is isolated_bridge
    assert ab.get_bridge() is isolated_bridge

    # Exactly how design/executor.py and core/backends.py reach it.
    from eda_agent.bridge import get_bridge as late_import
    assert late_import() is isolated_bridge


def test_the_workspace_sandbox_actually_redirects(isolated_bridge, tmp_path):
    """The second escape route, proven the same way as the first.

    Tools that write their own output resolve the destination through
    ``get_config().workspace_dir``. If that patch ever stops taking,
    the smoke run below writes into the user's real workspace, which is
    how it previously left netlist.net and board_stackup.csv there and
    overwrote bom.html.
    """
    from eda_agent.config import get_config
    resolved = get_config().workspace_dir
    assert resolved == tmp_path / "workspace", (
        f"workspace_dir resolved to {resolved}, not the sandbox. Tool "
        f"output would land in the real workspace.")
    assert resolved.is_dir()


#: Faults that mean the WRAPPER is broken, as opposed to the call being
#: refused for a reason. A tool that reports a missing argument is
#: behaving correctly. A tool that raises on an unbound name is not.
_WRAPPER_FAULTS = (TypeError, NameError, AttributeError, UnboundLocalError,
                   IndexError, ZeroDivisionError)


def _no_argument_bridge_tools(backend="altium"):
    """Registered tools that take no required argument and use the bridge.

    Offline tools are excluded on purpose. They are the ones that touch
    the network and the local filesystem (part_search queries providers,
    the session and job tools read state), and a fake bridge does not
    isolate any of that. Their logic is covered at module level.

    RESTORES THE ACTIVE BACKEND, because register_backend records it in
    a process global and leaving easyeda selected would change what
    every later test resolves against.
    """
    import asyncio
    from eda_agent.core import backends
    from eda_agent.server import register_backend
    from eda_agent.tools.metadata import tool_metadata
    from eda_agent.tools.registry import ToolRegistry

    previous = backends._REGISTERED
    try:
        registry = ToolRegistry()
        register_backend(registry, backend, "full")
        tools = asyncio.run(registry.list_tools())
    finally:
        backends.set_active_backend(previous or "")
    names = [t.name for t in tools
             if not (t.inputSchema or {}).get("required")
             and tool_metadata(t.name)["maturity"] != "offline"]
    return registry, sorted(names)


#: The smallest count each backend must produce. A registry that
#: returned almost nothing would make the whole check vacuous, and this
#: file has no other way to notice.
_FLOOR = {"altium": 150, "easyeda": 100, "kicad": 20}


@pytest.mark.parametrize("backend", sorted(_FLOOR))
def test_no_argument_tools_survive_being_called(isolated_bridge, backend):
    """EVERY BACKEND, not just Altium.

    This covered altium alone, which left 247 EasyEDA wrappers with no
    Python-level exercise at all. That is not theoretical: a bulk edit
    once gave three EasyEDA search tools a reference to a parameter
    they did not declare, and the module still imported cleanly because
    the failure is at CALL time. Calling each tool once finds that in a
    second.
    """
    import asyncio

    registry, names = _no_argument_bridge_tools(backend)
    assert len(names) > _FLOOR[backend], (
        f"only {len(names)} no-argument bridge tools found on {backend}; "
        f"the registry or the schema shape changed and this guard is not "
        f"covering what it thinks")

    faults = []

    async def drive():
        for name in names:
            try:
                await registry.call_tool(name, {})
            except _WRAPPER_FAULTS as exc:
                faults.append(f"{name}: {type(exc).__name__}: {exc}")
            except AssertionError:
                raise            # the tripwire; never swallow it
            except Exception:
                pass             # refusals and domain errors are fine

    asyncio.run(drive())

    assert not faults, (
        "these tools raise a Python-level fault when called with no "
        "arguments, so they would fail the same way for a user:\n  "
        + "\n  ".join(faults))


def test_the_smoke_run_actually_reached_the_bridge(isolated_bridge):
    """A run where nothing dispatched would pass while testing nothing."""
    import asyncio

    registry, names = _no_argument_bridge_tools()

    async def drive():
        for name in names[:40]:
            try:
                await registry.call_tool(name, {})
            except AssertionError:
                raise
            except Exception:
                pass

    asyncio.run(drive())
    assert len(isolated_bridge.calls) > 10, (
        f"only {len(isolated_bridge.calls)} commands reached the fake; "
        f"the tools are failing before they dispatch, so the smoke test "
        f"is not exercising the wrappers")
