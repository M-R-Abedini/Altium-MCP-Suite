# Review and validation — 2026-10-06

## Native save and idle ownership — 2026-10-08

The user's Ctrl+S failure was reproduced as an engine-ownership problem: saving worked after the polling loop was stopped. Idle release now defaults to two seconds and background pings do not prolong it. Live AD26 validation confirmed `reason=engine_idle_release` while repeated health pings were sent, successful on-demand restart with script version `2026.10.08.local2`, and a released engine at test exit. No design files were edited or saved by the test. A startup-request race found during the first timing test was fixed with native readiness acknowledgement. The final suite includes 80 suite tests plus 152 selected EDA tests.

## Five-finding repair validation — 2026-10-08

All five follow-up findings are addressed. 75 suite tests and 152 selected EDA tests passed, including cancellation, late native completion, editor-session changes, strict response validation and batch-file isolation. A live AD26 stdio smoke test stopped/recovered the EDA loop, executed a read-only legacy command with the new final completion acknowledgement and restored EDA ownership. This exercised compilation of the updated Pascal project without changing the hardware design. Circuit-building geometry was validated with mocked backend/file tests, not by building on the active design. See `docs/review/TRANSPORT_FINDINGS_2026-10-08.md` for evidence and manual recovery.

## Transport safety review — 2026-10-08

The new guards were verified with 44 suite tests and 91 selected EDA tests. Regressions cover withdrawing an unconsumed timed-out edit, retaining in-flight progress evidence, blocking dispatch during a handler, rejecting lossy nested JSON text, and requiring explicit modal ECO opt-in. Fresh stdio sessions initialized 445 EDA, 37 legacy and 34 library tools; default ECO invocation returned without dispatch. These checks used temporary files, mocked engine state and synthetic tool data. No live document edits or native DelphiScript crash reproduction were performed in this review. Unicode transport, native dialogs and copper verification remain outside these fixes.

Reviewed the installed source snapshots and the suite's shared execution path before publication. The review concentrated on process selection, script-engine ownership, recovery, IPC correctness, stdout framing, fresh-install compatibility, configuration portability and license/source completeness. It is not a certification of every EDA operation.

## Defects corrected

1. Closing the EDA monitor stopped its polling loop. X now hides the form; explicit Detach remains available, and stop reasons are recorded.
2. A slow or modal-blocked editor could cause duplicate script launches. Recovery now checks editor state, active handlers and two probes, with a failed-launch cooldown. A successful probe clears stale cooldown state.
3. Transient headless X2 launchers could be selected as the UI target. Selection now follows the real editor window. Missing ctypes imports in mouse paths were also fixed.
4. Legacy calls stopped EDA without restoring it. Shared locking and `finally` handback restore engine ownership; offline status does not stop the engine.
5. Absolute installation paths prevented reuse on another computer. Runtime/configuration paths are configurable and script projects are generated under a per-user runtime, with content-based directory names and atomic file publication.
6. The legacy bridge used shell command parsing and printed diagnostics on MCP stdout. Launching now bypasses the shell and diagnostic prints use stderr.
7. Legacy request files were visible before writing finished, parameter dictionaries could override the dispatched command, and partial JSON responses were rewritten heuristically. Requests are atomic, command selection is authoritative, and responses are polled until valid without replaying the operation.
8. A late response from a previous request could be accepted. Python generates a request ID, DelphiScript echoes it, and Python only accepts its matching response.
9. The legacy `FastMCP(description=...)` initialization failed with the tested MCP 1.30.0 dependency. It now uses the supported `instructions` argument. Startup does not open configuration dialogs.
10. A public IPC directory exposed executable requests across Windows users. The generated legacy script and Python now agree on a per-user exchange directory. Diagnostic-file errors no longer replace the original tool result.

## Evidence

- 29 suite regression tests passed, including partial/stale response handling, non-replay, modal/busy guards, PID selection and runtime generation.
- 62 selected upstream EDA tests passed: bridge, recovery, workspace-pointer isolation, modal timeout reporting, units and WebSocket framing.
- `cargo test --locked --all-targets` passed on Windows using Rust 1.95.0. This includes 1,311 library unit tests and the integration, round-trip, property, sample and stress targets. Tests marked ignored by upstream retain that status.
- `cargo build --release --locked` also completed successfully on Windows.
- Python syntax parsing covered 511 source/test files at the publication review. `pip check` found no broken dependency requirements.
- Fresh stdio initialization enumerated 441 EDA tools, 37 legacy tools and 34 tools in the locally built release library server. The legacy initialization defect was reproduced in a new virtual environment before being fixed.
- Live AD26 checks exercised EDA stop/recovery and legacy execution/EDA handback. The final legacy wire response had `success: true` and a request ID. The open board returned an empty component list; this confirms transport, not component extraction from a populated board.
- Source exports were checked for personal installation paths and common credential/private-key patterns. Local client configuration, virtual environments, runtime messages, logs and credentials are excluded from Git. Upstream licenses and source snapshots are included.

## Scope and remaining limits

The whole upstream EDA test suite was not run: it includes live desktop interaction and optional external fixtures. Selected suites and new regression tests are reproducible using README commands. Altium-specific behavior was checked on AD26; other releases have not been exercised here. The historic 17:18 bridge exit cannot be attributed conclusively because the earlier log omitted the exit reason.

The legacy bridge still uses one request/response file pair, serialized by the suite wrappers. Run its coordinated entry point; independently starting old installations bypasses that lock. If Altium displays a modal dialog, complete or close it before retrying. A timeout does not prove that a design mutation failed, so the suite never repeats it automatically.

Source provenance is in `UPSTREAM.json`; changed upstream files are listed in `MODIFICATIONS.md`. The repository's GitHub Actions workflow runs the Python regression/selected upstream suites and Rust all-target tests on future pushes.
