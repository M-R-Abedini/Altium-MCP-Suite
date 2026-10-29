"""Local Freerouting orchestration and capability-gated Altium exchange.

Altium's documented return format is RTE, whereas Freerouting emits SES.
There is deliberately no guessed RunProcess command or lossy SES importer.
Qualified native exchange adapters can be injected; local DSN -> SES works
independently and never uploads board files.
SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable, Protocol
import uuid

from suite_telemetry import data_directory

MAX_DESIGN_BYTES = 128 * 1024 * 1024


def _file(value: str | Path, suffix: str | None = None) -> Path:
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("An existing absolute local file is required")
    if suffix and path.suffix.casefold() != suffix:
        raise ValueError(f"Expected a {suffix} file")
    if path.is_symlink():
        raise ValueError("Routing inputs/executables must not be symbolic links")
    if path.stat().st_size > MAX_DESIGN_BYTES and suffix:
        raise ValueError("Routing design exceeds the 128 MiB limit")
    return path.resolve()


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _specctra(path: Path, expected: bytes) -> None:
    # A bounded, conservative envelope check, not a claim of full DSN parsing.
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_DESIGN_BYTES:
        raise ValueError("Router output is absent, empty or too large")
    with path.open("rb") as source:
        prefix = source.read(4096).lstrip(b"\xef\xbb\xbf \r\n\t")
    if not prefix.lower().startswith(b"(" + expected):
        raise ValueError("The file does not have the expected Specctra envelope")


class ExchangeAdapter(Protocol):
    """Installed-version-tested adapter, supplied by the host integration.

    Must preserve document identity, apply the host's shared native lock and
    honor modal guards. SES support requires a verified translator; renaming
    .ses to .rte is never a translator.
    """
    supports_dsn_export: bool
    supports_ses_import: bool

    async def context(self, board_path: str) -> dict[str, Any]: ...
    async def export_dsn(self, board_path: str, destination: str, allow_modal: bool) -> dict[str, Any]: ...
    async def import_ses(self, board_path: str, session_path: str, allow_modal: bool) -> dict[str, Any]: ...
    async def statistics(self, board_path: str) -> dict[str, Any]: ...
    async def fresh_drc(self, board_path: str, allow_modal: bool) -> dict[str, Any]: ...


class RoutingProvider(Protocol):
    """Extension point; remote providers are not installed or enabled here."""
    def route(self, input_path: Path, output_path: Path, **options: Any) -> dict[str, Any]: ...


@dataclass
class FreeroutingProvider:
    executable: Path | None = None
    jar: Path | None = None
    java: Path | None = None
    expected_sha256: str | None = None
    runner: Callable = subprocess.run

    def command(self, input_path: Path, output_path: Path, *, passes: int,
                work_dir: Path) -> list[str]:
        if type(passes) is not int or not 1 <= passes <= 1000:
            raise ValueError("max_passes must be an integer between 1 and 1000")
        if bool(self.executable) == bool(self.jar):
            raise ValueError("Configure exactly one local Freerouting executable or JAR")
        target = _file(self.executable or self.jar)
        if self.expected_sha256 and _hash(target) != self.expected_sha256.casefold():
            raise ValueError("Freerouting executable/JAR SHA-256 does not match private configuration")
        if self.jar:
            java = self.java or (Path(shutil.which("java")) if shutil.which("java") else None)
            if java is None:
                raise ValueError("A compatible Java runtime is required for the configured JAR")
            command = [str(_file(java)), "-Djava.awt.headless=true", "-jar", str(target)]
        else:
            command = [str(target)]
        return command + ["-de", str(input_path), "-do", str(output_path), "-mp", str(passes),
                          "-da", "--gui.enabled=false", "--api_server.enabled=false",
                          "--feature_flags.save_jobs=false", "--logging.file.enabled=false",
                          "--user_data_path=" + str(work_dir / "settings")]

    def route(self, input_path: Path, output_path: Path, *, max_passes: int = 10,
              timeout_seconds: int = 600, work_dir: Path | None = None) -> dict[str, Any]:
        """Run a bounded local process; failed jobs never publish a session."""
        source = _file(input_path, ".dsn")
        _specctra(source, b"pcb")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 7200:
            raise ValueError("timeout_seconds must be an integer between 1 and 7200")
        if output_path.exists():
            raise ValueError("Routing output must be a new file; existing sessions are not overwritten")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if work_dir is None:
            with tempfile.TemporaryDirectory(prefix="altium-suite-router-") as temporary:
                return self.route(source, output_path, max_passes=max_passes,
                                  timeout_seconds=timeout_seconds, work_dir=Path(temporary))
        command = self.command(source, output_path, passes=max_passes, work_dir=work_dir)
        began = time.monotonic()
        try:
            # No shell; no arbitrary flags/remote URLs/API credentials accepted.
            # Process logs stay private and are not reflected verbatim into MCP.
            with tempfile.TemporaryFile() as log:
                completed = self.runner(command, cwd=work_dir, stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=subprocess.STDOUT,
                                        timeout=timeout_seconds, check=False)
            if completed.returncode:
                raise ValueError(f"Freerouting exited with code {completed.returncode}; no session published")
            _specctra(output_path, b"session")
        except BaseException:
            output_path.unlink(missing_ok=True)
            raise
        return {"provider": "freerouting_local", "elapsed_seconds": round(time.monotonic() - began, 3),
                "input_sha256": _hash(source), "session_sha256": _hash(output_path),
                "session_path": str(output_path), "board_uploaded": False,
                "routed_session_created": True, "altium_imported": False,
                "drc_verified": False,
                "note": "SES creation is not an Altium-compatible RTE conversion or a DRC-clean guarantee."}


def metric_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Only compare actually observed numeric metrics, retaining units."""
    output = {}
    for field in ("track_count", "via_count", "unrouted_connections", "total_trace_length_mils"):
        a, b = before.get(field), after.get(field)
        if (isinstance(a, (int, float)) and not isinstance(a, bool)
                and isinstance(b, (int, float)) and not isinstance(b, bool)
                and math.isfinite(a) and math.isfinite(b)):
            output[field] = {"before": a, "after": b, "delta": b - a}
    return output


class AutorouteService:
    """Gateway service. Native exchange is unavailable until qualified."""

    def __init__(self, data_dir: Path | str | None = None, *,
                 provider: RoutingProvider | None = None, exchange: ExchangeAdapter | None = None):
        self.data_dir = Path(data_dir).resolve() if data_dir is not None else data_directory()
        self.provider, self.exchange = provider, exchange
        self._native_lock = asyncio.Lock()

    @staticmethod
    def list_tools() -> list[dict[str, Any]]:
        return list_tools()

    def _configured_provider(self) -> RoutingProvider:
        if self.provider is not None:
            return self.provider
        path = self.data_dir / "config.json"
        value = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
        raw = value.get("freerouting", {})
        executable = raw.get("executable") or os.environ.get("ALTIUM_SUITE_FREEROUTING_EXE")
        jar = raw.get("jar") or os.environ.get("ALTIUM_SUITE_FREEROUTING_JAR")
        java = raw.get("java") or os.environ.get("ALTIUM_SUITE_JAVA")
        if not executable and not jar:
            raise ValueError("Configure a local Freerouting executable or JAR in private config.json; no router is downloaded on tool invocation")
        return FreeroutingProvider(Path(executable) if executable else None,
                                   Path(jar) if jar else None, Path(java) if java else None,
                                   raw.get("sha256"))

    def status(self) -> dict[str, Any]:
        return {"local_provider": "freerouting", "free_tier": True,
                "dsn_export_available": bool(self.exchange and self.exchange.supports_dsn_export),
                "ses_import_available": bool(self.exchange and self.exchange.supports_ses_import),
                "default_altium_return_format": "RTE", "automatic_ses_to_rte_conversion": False,
                "cloud_providers_implemented": [], "remote_upload_enabled": False,
                "next_step": "Supply a local DSN export for local routing; a tested native adapter is needed for automatic Altium exchange."}

    def route_dsn(self, input_path: str, *, max_passes: int = 10,
                  timeout_seconds: int = 600) -> dict[str, Any]:
        source = _file(input_path, ".dsn")
        _specctra(source, b"pcb")
        provider = self._configured_provider()
        job = self.data_dir / "routing" / uuid.uuid4().hex
        job.mkdir(parents=True, exist_ok=False)
        # The input copy isolates user files and prevents concurrent editor
        # changes or adjacent .rules files from silently altering the job.
        isolated_input = job / "input.dsn"
        shutil.copyfile(source, isolated_input)
        session = job / "result.ses"
        output = provider.route(isolated_input, session, max_passes=max_passes,
                                timeout_seconds=timeout_seconds, work_dir=job)
        receipt = {"schema": 1, **output, "created_at_utc": datetime.now(timezone.utc).isoformat()}
        (job / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        return receipt

    async def _context(self, board_path: str) -> dict[str, Any]:
        board = _file(board_path, ".pcbdoc")
        if self.exchange is None:
            raise ValueError("Native Specctra exchange has no verified adapter. Export DSN through Altium File > Export > Specctra Design. Altium imports RTE; a raw SES must not be renamed or sent to a guessed process.")
        context = await self.exchange.context(str(board))
        if (context.get("bridge_answering") is not True or context.get("script_version_match") is not True
                or context.get("modal_open") is not False or context.get("in_flight") is not False):
            raise ValueError("Native exchange requires an answering current bridge, no modal dialog and no in-flight native command")
        active = context.get("active_document", {}).get("file_path")
        if not active or os.path.normcase(str(Path(active).resolve())) != os.path.normcase(str(board)):
            raise ValueError("The explicitly requested PCB must be the active document")
        return context

    async def export_specctra_dsn(self, board_path: str, *, allow_modal: bool = False) -> dict[str, Any]:
        async with self._native_lock:
            await self._context(board_path)
            if not self.exchange.supports_dsn_export:
                raise ValueError("This installed Altium adapter has no qualified DSN exporter")
            job = self.data_dir / "routing" / uuid.uuid4().hex
            job.mkdir(parents=True, exist_ok=False)
            destination = job / "export.dsn"
            output = await self.exchange.export_dsn(board_path, str(destination), allow_modal)
            if output.get("success") is not True:
                raise ValueError("Native exporter did not acknowledge successful completion")
            _specctra(destination, b"pcb")
            return {"exported": True, "dsn_path": str(destination), "sha256": _hash(destination),
                    "board_disk_sha256": _hash(_file(board_path, ".pcbdoc")), "board_modified": False}

    async def import_specctra_ses(self, board_path: str, session_path: str, *,
                                  allow_modal: bool = False, expected_board_sha256: str,
                                  checkpoint_id: str) -> dict[str, Any]:
        """Import only through a qualified adapter with an existing checkpoint.

        The host must validate the checkpoint ID and hold its suite-wide
        admission gate. A failure can leave native edits and is never retried.
        """
        async with self._native_lock:
            board, session = _file(board_path, ".pcbdoc"), _file(session_path, ".ses")
            _specctra(session, b"session")
            if not checkpoint_id or not isinstance(checkpoint_id, str):
                raise ValueError("A host-validated pre-edit checkpoint is required")
            if _hash(board) != expected_board_sha256:
                raise ValueError("PCB disk content changed since export; import refused")
            context = await self._context(board_path)
            if context.get("unsaved"):
                raise ValueError("Native import requires a saved PCB matching the exported design")
            if not self.exchange.supports_ses_import:
                raise ValueError("The adapter has no verified SES-to-Altium translator; native Altium expects RTE")
            before = await self.exchange.statistics(board_path)
            import_started = time.time()
            imported = await self.exchange.import_ses(board_path, session_path, allow_modal)
            if imported.get("success") is not True:
                return {"imported": False, "drc_verified": False, "checkpoint_id": checkpoint_id,
                        "possible_partial_native_edit": True, "automatic_retry": False}
            after = await self.exchange.statistics(board_path)
            drc = await self.exchange.fresh_drc(board_path, allow_modal)
            # Stored violation_count=0 alone never proves a fresh DRC run.
            observed = drc.get("observed_at_unix")
            fresh = (drc.get("drc_triggered") is True and drc.get("completed") is True
                     and drc.get("complete") is True and isinstance(observed, (float, int))
                     and not isinstance(observed, bool) and import_started <= observed <= time.time()
                     and drc.get("board_path") == board_path)
            clean = fresh and type(drc.get("violation_count")) is int and drc["violation_count"] == 0
            return {"imported": True, "drc_verified": clean, "drc_fresh": fresh,
                    "metric_diff": metric_diff(before, after), "checkpoint_id": checkpoint_id,
                    "native_rollback_available": False,
                    "next_step": "Review native DRC and connectivity before manufacturing."}

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        params = arguments or {}
        functions = {"status": self.status, "route_dsn": self.route_dsn,
                     "export_specctra_dsn": self.export_specctra_dsn,
                     "import_specctra_ses": self.import_specctra_ses}
        if name not in functions:
            raise ValueError("Unknown routing tool")
        function = functions[name]
        if inspect.iscoroutinefunction(function):
            return await function(**params)
        return await asyncio.to_thread(function, **params)

    async def call_tool_async(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Explicit async dispatch entry point for in-process suite adapters."""
        return await self.call_tool(name, arguments)


def list_tools() -> list[dict[str, Any]]:
    """Routing namespace schemas; export/import advertise adapter prerequisites."""
    string = {"type": "string"}
    definitions = [
        ("status", "Inspect local routing and native exchange adapter capabilities without opening Altium.", {}, [], True),
        ("route_dsn", "Run configured local Freerouting on an explicit DSN; produce a private SES. Does not import into Altium, upload a board or certify DRC.",
         {"input_path": string, "max_passes": {"type": "integer", "minimum": 1, "maximum": 1000},
          "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 7200}}, ["input_path"], False),
        ("export_specctra_dsn", "Export the explicitly active PCB via a qualified native adapter. Currently requires adapter setup; fallback is Altium File > Export > Specctra Design.",
         {"board_path": string, "allow_modal": {"type": "boolean", "default": False}}, ["board_path"], False),
        ("import_specctra_ses", "Import only through a verified SES translator, host-validated checkpoint and matching PCB hash; run post-import fresh DRC and report metric differences. Raw SES is not native Altium RTE.",
         {"board_path": string, "session_path": string, "allow_modal": {"type": "boolean", "default": False},
          "expected_board_sha256": {"type": "string", "pattern": "^[a-fA-F0-9]{64}$"}, "checkpoint_id": string},
         ["board_path", "session_path", "expected_board_sha256", "checkpoint_id"], False),
    ]
    return [{"name": name, "description": description,
             "inputSchema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
             "annotations": {"readOnlyHint": readonly, "openWorldHint": False}}
            for name, description, properties, required, readonly in definitions]


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Synchronous in-process backend entry point for the gateway worker pool."""
    return asyncio.run(AutorouteService().call_tool(name, arguments))
