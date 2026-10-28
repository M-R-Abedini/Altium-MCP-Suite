"""Router process isolation, unsupported exchange and fresh-DRC contracts."""
import asyncio
import hashlib
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

import pytest

from suite_autoroute import AutorouteService, FreeroutingProvider, list_tools, metric_diff


def files(tmp_path):
    dsn = tmp_path / "board.dsn"
    dsn.write_text('(pcb board (resolution mil 1000))')
    exe = tmp_path / "freerouting.exe"
    exe.write_bytes(b"TEST ONLY")
    return dsn, exe


def test_local_command_has_no_shell_telemetry_or_gui(tmp_path):
    dsn, exe = files(tmp_path)
    seen = []
    def runner(command, **kwargs):
        seen.append((command, kwargs))
        Path(command[command.index("-do") + 1]).write_text('(session board (routes))')
        return SimpleNamespace(returncode=0)
    provider = FreeroutingProvider(executable=exe, runner=runner)
    result = AutorouteService(tmp_path / "private", provider=provider).route_dsn(str(dsn))
    command, options = seen[0]
    assert command[0] == str(exe)
    assert "-da" in command and "--gui.enabled=false" in command and "--api_server.enabled=false" in command
    assert options.get("shell", False) is False
    assert result["routed_session_created"] and not result["drc_verified"] and not result["altium_imported"]
    assert Path(command[command.index("-de") + 1]) != dsn
    assert dsn.read_text() == '(pcb board (resolution mil 1000))'


@pytest.mark.parametrize("mode", ["exit", "absent", "malformed", "timeout"])
def test_failed_job_never_publishes_session(tmp_path, mode):
    dsn, exe = files(tmp_path)
    output = tmp_path / "result.ses"
    def runner(command, **kwargs):
        if mode == "timeout":
            output.write_text("partial")
            raise subprocess.TimeoutExpired(command, 1)
        if mode == "malformed":
            output.write_text("garbage")
        return SimpleNamespace(returncode=2 if mode == "exit" else 0)
    provider = FreeroutingProvider(executable=exe, runner=runner)
    with pytest.raises((ValueError, subprocess.TimeoutExpired)):
        provider.route(dsn, output, work_dir=tmp_path)
    assert not output.exists()


def test_no_overwrite_or_wrong_hash(tmp_path):
    dsn, exe = files(tmp_path)
    output = tmp_path / "result.ses"
    output.write_text("keep")
    with pytest.raises(ValueError, match="new file"):
        FreeroutingProvider(executable=exe).route(dsn, output)
    assert output.read_text() == "keep"
    with pytest.raises(ValueError, match="SHA-256"):
        FreeroutingProvider(executable=exe, expected_sha256="0" * 64).command(
            dsn, tmp_path / "new.ses", passes=2, work_dir=tmp_path)


def test_requires_configured_local_router(tmp_path):
    dsn, _ = files(tmp_path)
    with pytest.raises(ValueError, match="Configure a local"):
        AutorouteService(tmp_path).route_dsn(str(dsn))
    assert not (tmp_path / "routing").exists()


def test_unqualified_native_exchange_does_not_touch_design(tmp_path):
    board = tmp_path / "board.PcbDoc"
    board.write_bytes(b"USER DESIGN")
    service = AutorouteService(tmp_path / "private")
    assert not service.status()["ses_import_available"]
    with pytest.raises(ValueError, match="no verified adapter"):
        asyncio.run(service.export_specctra_dsn(str(board)))
    assert board.read_bytes() == b"USER DESIGN"
    assert not (tmp_path / "private").exists()


class FakeExchange:
    supports_dsn_export = True
    supports_ses_import = True

    def __init__(self, board, *, modal=False, fresh=True, unsaved=False):
        self.board, self.modal, self.fresh, self.unsaved = str(board), modal, fresh, unsaved
        self.calls = []

    async def context(self, board):
        self.calls.append("context")
        return {"bridge_answering": True, "script_version_match": True, "modal_open": self.modal,
                "in_flight": False, "active_document": {"file_path": board},
                "unsaved": [board] if self.unsaved else []}

    async def export_dsn(self, board, output, allow_modal):
        self.calls.append("export")
        Path(output).write_text("(pcb board)")
        return {"success": True}

    async def import_ses(self, board, session, allow_modal):
        self.calls.append("import")
        return {"success": True}

    async def statistics(self, board):
        self.calls.append("stats")
        return {"track_count": 3 if "import" in self.calls else 1,
                "via_count": 2, "total_trace_length_mils": 50}

    async def fresh_drc(self, board, allow_modal):
        self.calls.append("drc")
        return {"drc_triggered": self.fresh, "completed": True, "complete": True,
                "observed_at_unix": time.time(), "board_path": board, "violation_count": 0}


def test_adapter_export_and_post_import_metrics(tmp_path):
    board = tmp_path / "board.PcbDoc"
    board.write_bytes(b"saved")
    adapter = FakeExchange(board)
    service = AutorouteService(tmp_path / "private", exchange=adapter)
    exported = asyncio.run(service.export_specctra_dsn(str(board)))
    assert exported["exported"] and Path(exported["dsn_path"]).is_file()
    ses = tmp_path / "out.ses"
    ses.write_text("(session board)")
    result = asyncio.run(service.import_specctra_ses(str(board), str(ses),
        expected_board_sha256=hashlib.sha256(b"saved").hexdigest(), checkpoint_id="host-validated"))
    assert result["imported"] and result["drc_verified"]
    assert result["metric_diff"]["track_count"]["delta"] == 2
    assert adapter.calls[-4:] == ["stats", "import", "stats", "drc"]


@pytest.mark.parametrize("reason", ["modal", "unsaved", "unsupported", "changed"])
def test_import_preflight_failure_never_dispatches(tmp_path, reason):
    board = tmp_path / "board.PcbDoc"
    board.write_bytes(b"saved")
    ses = tmp_path / "out.ses"
    ses.write_text("(session board)")
    adapter = FakeExchange(board, modal=reason == "modal", unsaved=reason == "unsaved")
    adapter.supports_ses_import = reason != "unsupported"
    service = AutorouteService(tmp_path / "private", exchange=adapter)
    with pytest.raises(ValueError):
        asyncio.run(service.import_specctra_ses(str(board), str(ses),
            expected_board_sha256="0" * 64 if reason == "changed" else hashlib.sha256(b"saved").hexdigest(),
            checkpoint_id="host-validated"))
    assert "import" not in adapter.calls


def test_stored_zero_violations_do_not_count_as_fresh_drc(tmp_path):
    board = tmp_path / "board.PcbDoc"
    board.write_bytes(b"saved")
    ses = tmp_path / "out.ses"
    ses.write_text("(session board)")
    service = AutorouteService(tmp_path / "private", exchange=FakeExchange(board, fresh=False))
    result = asyncio.run(service.import_specctra_ses(str(board), str(ses),
        expected_board_sha256=hashlib.sha256(b"saved").hexdigest(), checkpoint_id="host-validated"))
    assert result["imported"] and not result["drc_verified"] and not result["drc_fresh"]


def test_schema_and_metric_precision():
    import jsonschema
    for tool in list_tools():
        jsonschema.Draft202012Validator.check_schema(tool["inputSchema"])
    assert metric_diff({"via_count": 2, "total_trace_length_mils": 3.25},
                       {"via_count": 1, "total_trace_length_mils": 4.5}) == {
                       "via_count": {"before": 2, "after": 1, "delta": -1},
                       "total_trace_length_mils": {"before": 3.25, "after": 4.5, "delta": 1.25}}
