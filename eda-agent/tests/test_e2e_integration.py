# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""End-to-end bridge integration tests.

These tests exercise the real AltiumBridge, the real per-request file IPC,
and the AltiumSimulator lifecycle.

What we verify:
  - Bridge mechanics (sync/async send, error raising, protocol versioning)
  - Simulator lifecycle (start/stop, malformed/empty request handling)
  - UTF-8 encoding at the IPC boundary
"""

import asyncio
import json
import time
from pathlib import Path

import pytest

from tests.altium_simulator import AltiumSimulator, SIM_PROTOCOL_VERSION
from tests.conftest import wait_until
from eda_agent.bridge.altium_bridge import AltiumBridge, CommandRequest, PROTOCOL_VERSION
from eda_agent.bridge.exceptions import AltiumCommandError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_async(coro):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _event_loop():
    """Give each test a fresh event loop, and close it afterwards.

    Do NOT probe with ``asyncio.get_event_loop()``: since 3.12 that
    CREATES a loop when none is set (with a DeprecationWarning) instead
    of raising, so a try/except RuntimeError around it never fires and
    the loop it just made is never closed. That loop is then collected
    at interpreter shutdown, where __del__ closes its self-pipe socket
    after Windows has already run WSACleanup, printing an ignored
    "OSError: [WinError 10093]" at the end of every full-suite run.

    Owning the loop outright is both deterministic and quieter.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    loop.close()
    asyncio.set_event_loop(None)


# =========================================================================
# BRIDGE INTEGRATION
# =========================================================================

class TestBridgeIntegration:
    """Test the bridge itself works correctly against a live simulator."""

    def test_bridge_sync_command(self, e2e_bridge):
        """send_command (sync) works end-to-end."""
        result = e2e_bridge.send_command("application.ping", timeout=5.0)
        assert result == "pong"

    def test_bridge_async_command(self, e2e_bridge):
        """send_command_async works end-to-end."""
        result = run_async(
            e2e_bridge.send_command_async("application.ping", timeout=5.0)
        )
        assert result == "pong"

    def test_bridge_ping_method(self, e2e_bridge):
        """Bridge.ping() returns True when simulator is running."""
        assert e2e_bridge.ping() is True

    def test_bridge_command_error_raised(self, e2e_bridge):
        """Bridge raises AltiumCommandError for error responses."""
        with pytest.raises(AltiumCommandError):
            e2e_bridge.send_command("application.unknown_action", timeout=5.0)

    def test_bridge_error_has_code_and_message(self, e2e_bridge):
        """Error responses produce AltiumCommandError with populated fields."""
        with pytest.raises(AltiumCommandError) as exc_info:
            e2e_bridge.send_command("application.unknown_action", timeout=5.0)
        error = exc_info.value
        assert error.code
        assert error.message

    def test_unrelated_response_invisible(self, e2e_bridge):
        """A foreign caller's response file does not interfere with our poll."""
        # Per-request files: this file belongs to no current request and the
        # bridge should never poll for it.
        ws = e2e_bridge.config.workspace_dir
        ws.mkdir(parents=True, exist_ok=True)
        foreign = ws / "response_foreigncaller.json"
        foreign.write_text(
            json.dumps({
                "protocol_version": PROTOCOL_VERSION,
                "id": "foreigncaller",
                "success": True,
                "data": "stale",
                "error": None,
            }),
            encoding="utf-8",
        )

        result = e2e_bridge.send_command("application.ping", timeout=5.0)
        assert result == "pong"
        # The unrelated file is left alone, we never touch responses we don't own.
        assert foreign.exists()

    def test_unknown_command_category_raises(self, e2e_bridge):
        """Completely unknown category -> UNKNOWN_COMMAND error from simulator."""
        with pytest.raises(AltiumCommandError) as exc_info:
            e2e_bridge.send_command("bogus.action", timeout=5.0)
        assert exc_info.value.code == "UNKNOWN_COMMAND"

    def test_no_dot_in_command_raises(self, e2e_bridge):
        """Command without dot -> UNKNOWN_COMMAND error."""
        with pytest.raises(AltiumCommandError) as exc_info:
            e2e_bridge.send_command("nodotcommand", timeout=5.0)
        assert exc_info.value.code == "UNKNOWN_COMMAND"


# =========================================================================
# UTF-8 ENCODING AT THE IPC BOUNDARY
# =========================================================================

class TestEncoding:
    """Verify both sides write/read UTF-8 with no encoding ambiguity.

    Pascal escapes any non-ASCII byte as \\u00XX so output is pure ASCII;
    it is therefore valid UTF-8 by construction. Python writes UTF-8.
    """

    def test_response_is_utf8(self, altium_sim):
        """Simulator writes UTF-8 responses readable as JSON."""
        rid = "testencoding001"
        request_path = altium_sim.workspace_dir / f"request_{rid}.json"
        response_path = altium_sim.workspace_dir / f"response_{rid}.json"

        request_data = {
            "protocol_version": SIM_PROTOCOL_VERSION,
            "id": rid,
            "command": "application.ping",
            "params": {},
        }
        with open(request_path, "w", encoding="utf-8") as f:
            json.dump(request_data, f)

        # Wait for the file to exist AND parse. The simulator writes the
        # response straight to its final name, with no tmp+rename, which
        # is exactly what Main.pas WriteResponseFile does -- so exists()
        # goes true partway through the write and a reader that parses
        # immediately can catch a truncated file. The bridge's poll loop
        # tolerates that by retrying on JSONDecodeError; this test
        # hand-rolled a reader without the same hardening and failed
        # under CPU load with a JSONDecodeError roughly 1 run in 25.
        deadline = time.monotonic() + 5.0
        data = None
        while time.monotonic() < deadline:
            if response_path.exists():
                try:
                    with open(response_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    break
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass    # mid-write, look again
            time.sleep(0.01)

        assert response_path.exists()
        assert data is not None, (
            "the response never became parseable within the deadline")
        assert data["id"] == rid
        assert data["success"] is True
        assert data["protocol_version"] == SIM_PROTOCOL_VERSION


# =========================================================================
# SIMULATOR LIFECYCLE
# =========================================================================

class TestSimulatorLifecycle:
    """Test the simulator's own start/stop/cleanup behavior."""

    def test_start_and_stop(self, tmp_path):
        """Simulator starts and stops cleanly."""
        sim = AltiumSimulator(str(tmp_path))
        assert sim.running is False
        sim.start()
        assert sim.running is True
        sim.stop()
        assert sim.running is False

    def test_stop_via_stop_file(self, tmp_path):
        """Simulator stops when 'stop' file appears."""
        sim = AltiumSimulator(str(tmp_path))
        sim.start()
        assert sim.running is True

        (tmp_path / "stop").write_text("", encoding="utf-8")
        wait_until(lambda: sim.running is False,
                   message="the simulator loop should have stopped")
        sim.stop()

    def test_cleanup_removes_ipc_files(self, tmp_path):
        """Simulator cleanup removes leftover per-request files on stop."""
        sim = AltiumSimulator(str(tmp_path))
        (tmp_path / "request_leftover.json").write_text("{}", encoding="utf-8")
        (tmp_path / "response_leftover.json").write_text("{}", encoding="utf-8")

        sim.start()
        sim.stop()

        assert not list(tmp_path.glob("request_*.json"))
        assert not list(tmp_path.glob("response_*.json"))

    def test_double_start_is_idempotent(self, tmp_path):
        """Starting twice does not create duplicate threads."""
        sim = AltiumSimulator(str(tmp_path))
        sim.start()
        thread1 = sim._thread
        sim.start()  # second start should be no-op
        assert sim._thread is thread1
        sim.stop()

    def test_malformed_request_ignored(self, tmp_path):
        """Simulator handles malformed JSON gracefully."""
        sim = AltiumSimulator(str(tmp_path))
        sim.start()

        (tmp_path / "request_malformed1.json").write_text("not json at all", encoding="utf-8")
        wait_until(lambda: not (tmp_path / "request_malformed1.json").exists(),
                   message="a malformed request must still be removed")
        assert sim.running is True
        sim.stop()

    def test_empty_request_ignored(self, tmp_path):
        """Simulator handles empty request file gracefully."""
        sim = AltiumSimulator(str(tmp_path))
        sim.start()

        (tmp_path / "request_empty1.json").write_text("", encoding="utf-8")
        wait_until(lambda: not (tmp_path / "request_empty1.json").exists(),
                   message="an empty request must still be removed")
        assert sim.running is True
        sim.stop()

    def test_stop_server_command_stops_simulator(self, altium_sim, e2e_bridge):
        """application.stop_server command halts the simulator."""
        assert altium_sim.running is True
        result = e2e_bridge.send_command("application.stop_server", timeout=5.0)
        assert result["stopped"] is True
        wait_until(lambda: altium_sim.running is False,
                   message="the simulator loop should have stopped")


# =========================================================================
# UNREADABLE REQUESTS
# =========================================================================

class TestUnreadableRequestIsNotSilentlyDropped:
    """A request that cannot be read must never vanish without an answer.

    The simulator used to delete the request file on ANY read failure and
    return, so no response was ever written and the caller could only
    report a timeout. On Windows that read fails transiently whenever
    something else holds the file open for an instant (indexer, sync
    client, AV), and under CPU load it destroyed roughly one request in
    25: measured 7/25 and 5/25 failing runs before the fix and 0/25
    after, with the bridge trace showing first_seen_ms=-1 and the
    simulator loop demonstrably alive the whole time.

    These pin the behaviour deterministically, because a load experiment
    is not something anyone will re-run.
    """

    def _send(self, sim, rid, command="application.ping"):
        path = sim.workspace_dir / f"request_{rid}.json"
        path.write_text(json.dumps({
            "protocol_version": SIM_PROTOCOL_VERSION,
            "id": rid,
            "command": command,
            "params": {},
        }), encoding="utf-8")
        return sim.workspace_dir / f"response_{rid}.json"

    def _await_response(self, response_path, seconds=5.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if response_path.exists():
                try:
                    return json.loads(
                        response_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass        # written directly, so a partial read is normal
            time.sleep(0.01)
        return None

    def test_a_transient_read_failure_is_retried_not_destroyed(
        self, altium_sim, monkeypatch
    ):
        """Three failed reads then success must still answer the call."""
        real_read_text = Path.read_text
        state = {"fails": 3}

        def flaky(self, *args, **kwargs):
            if self.name.startswith("request_") and state["fails"] > 0:
                state["fails"] -= 1
                raise PermissionError(32, "sharing violation")
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", flaky)
        response_path = self._send(altium_sim, "transientread01")
        data = self._await_response(response_path)

        assert data is not None, (
            "the request was dropped: a transient read failure must be "
            "retried, not treated as a dead request")
        assert data["success"] is True
        assert state["fails"] == 0, "the failures were not actually injected"

    def test_a_permanently_unreadable_request_gets_a_reason(
        self, altium_sim, monkeypatch
    ):
        """Giving up is fine. Giving up SILENTLY is not: the caller then
        waits out its whole deadline and reports a plain timeout, which
        reads like a wedged polling loop and points at the wrong fault."""
        real_read_text = Path.read_text

        def always_fails(self, *args, **kwargs):
            if self.name.startswith("request_"):
                raise PermissionError(32, "sharing violation")
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", always_fails)
        response_path = self._send(altium_sim, "deadread000001")
        data = self._await_response(response_path)

        assert data is not None, (
            "no response at all -- the caller can only time out and has "
            "nothing to act on")
        assert data["success"] is False
        assert data["error"]["code"] == "REQUEST_UNREADABLE", data["error"]
        assert "retry" in data["error"]["message"].lower()

    def test_the_request_file_does_not_survive_to_be_reprocessed(
        self, altium_sim, monkeypatch
    ):
        """Retrying must stay bounded. The original code deleted the file
        immediately precisely so a poisoned request could not be picked up
        forever; keeping it alive indefinitely would trade a dropped call
        for a stuck loop."""
        real_read_text = Path.read_text

        def always_fails(self, *args, **kwargs):
            if self.name.startswith("request_"):
                raise PermissionError(32, "sharing violation")
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", always_fails)
        request_path = altium_sim.workspace_dir / "request_stuckfile0001.json"
        response_path = self._send(altium_sim, "stuckfile0001")
        assert self._await_response(response_path) is not None
        assert not request_path.exists(), (
            "the unreadable request was left behind and will be retried "
            "forever, blocking every later call")
