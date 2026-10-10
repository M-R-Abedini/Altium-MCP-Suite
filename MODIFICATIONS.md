# Local changes

## Added 2026-10-10

- TextFrame Corner writes now use the typed ISch_TextFrame interface, matching the already supported read path. The old setter accepted the name but did nothing. The LCD note was resized away from signal ports and read back at 18200mil. A regression checks that the declared interface and write dispatch both exist. During local development a misplaced duplicate TF declaration failed native compilation; it was corrected before release, the error dismissed, and the bridge restarted with matching reviewed templates.

- EDA native script `2026.10.10.review6`: guard schematic `Name` reads/writes by interface type, add `eNote` to generic CRUD, and implement custom sheet size with `UseCustomSheet` instead of the nonexistent `eSheetCustom` enum. Standard sheet selection explicitly disables custom dimensions.
- `Gen_BatchModify` now reads `matched` from the child response's `data` object. The JSON parser intentionally reads direct members; the former envelope-level lookup returned zero after a successful mutation. Failed child operations retain `operation_failed` instead of becoming an unmatched filter. A single modern operation is recognized by its `scope=` prefix even when no `~~` separator is present. No automatic replay was added.
- Native AD26.10.1 validation: unsupported sheet-symbol `Name` is unreadable without stopping the bridge; notes can be queried/deleted; a 23300x16500mil custom Core sheet survives save/reopen and has no sheet-boundary ERC findings. Five library pin writes returned five matches and their types were read back from the saved library. A single C131 operation returned one match; a single missing-document operation preserved the NO_SCHEMATIC child error. `app_ping` confirms deployed/bundled version equality.
- Validation: 55 focused pytest tests passed (`test_sch_properties_match_their_interface`, `test_delphiscript_lint`, `test_version_is_consistent`, `test_batch_tools`); monolithic build lint scanned 11 units with zero errors/warnings. No Computer Use fallback was needed.
- Open limitations: `lib_reload_library` is PCB-library specific; its SchLib invocation closed the document but reported reopening failure. `app_save_all` can write loaded libraries as well as project sheets; check `still_dirty` and independently verify critical edits from saved/reopened data. Neither issue is claimed fixed here.

## Added 2026-10-09

- Second external review: allow explicit manual recovery from corrupt legacy ownership while retaining the marker for diagnosis; validate settings and Pascal templates even under optimized Python; classify keepalive by thread identity. Add direct-pytest bootstrapping, portable regression CI and installation-independent stdio smoke checks. Synchronize four README pages and document license scope. See [verified claims](docs/review/SECOND_ANALYSIS_2026-10-09.md).

- External analysis: fix legacy JSON punctuation/escapes and native direct-member lookup, reject unsupported input before publication, protect 23 iterator scopes, and bound Rust stdin frames to 64 MiB. The snippet sandbox now generates matching private paths, retains ownership until native completion, avoids shell launch and leaves dialogs for the operator. See [claim-by-claim findings and native validation](docs/review/EXTERNAL_ANALYSIS_2026-10-09.md). EDA script version: `2026.10.09.review1`.

- Follow-up: cancellation during engine stop/start now drains the worker under the shared lock before propagating cancellation (asyncio and AnyIO). Launch state is validated and published atomically; malformed state fails with manual recovery instructions. Installed live tests verified legacy read-only handover, idle release/recovery, and cancellation during real native stop/start.

- Legacy handover now requires the current editor session's final clean-shutdown acknowledgement. Ping timeouts and stop-file consumption no longer authorize a second script launch; pending stop files are withdrawn on failure. See [targeted review, open findings and regression evidence](docs/review/REVIEW_2026-10-09.md).

## Added 2026-10-08

- Performance follow-up (`2026.10.08.perf1`): native clean-stop/session acknowledgements avoid five seconds of failed probes after idle release. Editor-session, modal, legacy ownership, busy-handler and launch-cooldown guards still apply. Background health traffic uses a nonblocking lock and a one-second maximum wait; plain foreground ping reuses its confirmed health response. Minimal mode now lazily builds exact FastMCP schemas and validates arguments; CLI selection no longer re-execs past the coordination wrapper. Setup exposes `--toolset`; read-only measurements and regression evidence are in [performance review](docs/review/PERFORMANCE_2026-10-08.md).

- Storage/variant follow-up: installed-directory deployment with dependency manifest and initialization checks; project-definition save and close now verify native focus; `proj_save` verifies persisted document/variant metadata. Native variant GUIDs/display descriptions, guarded creation/switching and bulk fitted-state updates are supported with backups and native readback. Zero-limit semantics, unsupported owner-property rejection and typed TextFrame queries are corrected. See [validation and workflow](docs/review/STORAGE_VARIANTS_2026-10-08.md). Script version: `2026.10.08.storage3`.

- Native-save follow-up: added `engine_idle_release_ms` (default 2000; 0 disables), independent of the legacy disconnect timer. Background pings do not renew it. Foreground preflight reserves enough time for the actual command. Release occurs between handlers, without saving documents. The coordinated entry point restarts the loop on demand.
- Startup now publishes `bridge-ready.json` after request purges and form initialization; the coordinator waits for it before publishing a launch probe. This removes the startup-request race exposed by short idle release. Normal shutdown removes the marker, and Python background keepalive treats its absence as idle release rather than a fault.
- Script version is `2026.10.08.local2`. Tests cover configuration migration, foreground reservation, startup readiness and live release despite repeated pings, followed by automatic recovery. User-confirmed native Ctrl+S worked immediately after releasing the previously held engine.

- Follow-up transport review: legacy commands persist editor-session ownership and require a request-specific final completion acknowledgement before EDA handback. Timeout/cancellation withdraw unconsumed requests and retain unresolved ownership; a confirmed editor restart or explicit manual-stop reset can retire it. Generic bridge errors no longer open blocking error dialogs.
- The schematic builder validates all pipe-delimited fields and uses strict cp1252 encoding before writing. Each build uses unique specification/pin-map paths in the configured per-user exchange; Pascal receives those paths explicitly. Public shared paths and stale pin-map reuse are removed from this workflow.
- EDA response envelopes now require a matching request ID, boolean success, valid protocol version type and structured failure fields. Progress ownership no longer expires after 600 seconds: markers are compared with the editor process start time, and uncertain current-session markers block relaunch.
- Acceptance tests for all five findings are in `tests/test_review_fixes.py`; review evidence and manual recovery instructions are in `docs/review/TRANSPORT_FINDINGS_2026-10-08.md`.

- `bridge_coordination.py`: a busy handler now rejects subsequent dispatch instead of allowing another request to queue. The second health-check race has the same guard.
- `eda-agent/src/eda_agent/bridge/altium_bridge.py`: remove an unconsumed request when polling exits, including timeout/modal errors. Already-consumed edits cannot be cancelled; their progress markers remain intact. No automatic replay was added.
- `eda-agent/src/eda_agent/bridge/payload.py`: reject JSON parameter text above U+00FF before publication instead of silently substituting `?`. Nested keys/values and batch strings are covered; external batch files are outside this check. This does not implement Unicode transport.
- `proj_sync_pcb` requires `allow_modal=True`. The transport also rejects direct `project.update_pcb` calls without an explicit boolean opt-in. The native ECO dialog remains interactive.
- New regressions in `tests/test_transport_safety.py`; stdio smoke additionally checks that default ECO invocation sends no command. No Altium Pascal code or hardware design was modified.

## Added 2026-10-07

- Independent MIT-licensed connectivity verification modules and four read-only MCP tools: canonical snapshots, exact pin contracts, connectivity diffs, and bidirectional schematic/PCB pad parity. Full net names are preserved; incomplete inputs cannot pass; NC and extra-pad waivers require explicit intent. Registered with the existing Altium full/minimal toolsets, with no new dependencies or Pascal changes. See `CONNECTIVITY_VERIFICATION.md` and `eda-agent/tests/design/test_connectivity_contracts.py`.

Upstream authorship and license files are retained. `UPSTREAM.json` records the source snapshots imported into this repository. The following changes were made for this distribution by M-R-Abedini:

- `eda-agent/src/eda_agent/bridge/process_manager.py`: select the visible Altium editor instead of the first transient X2 process.
- `eda-agent/src/eda_agent/ui/windows.py`: import the ctypes names used by mouse operations.
- `eda-agent/scripts/altium/{Main,StatusForm,Dispatcher}.pas`: hide the monitor on X; keep explicit Detach; log stop reasons; local script version `2026.10.06.local1`.
- `coffeenmusic/server/main.py`: support current FastMCP initialization; direct subprocess launch without a shell; stderr diagnostics; noninteractive configuration validation; atomic requests; complete JSON response polling without content rewriting or command replay; request/response IDs; per-user exchange path.
- `coffeenmusic/server/AltiumScript/Altium_API.pas`: exchange-directory template populated in generated runtime scripts; echo the request ID in responses.
- New root configuration, setup and coordination modules: shared engine lock, guarded recovery, restored EDA control after legacy calls, portable per-user runtime, content-addressed script projects, regression tests and setup documentation.

The Rust library-server source is preserved from its recorded upstream snapshot. No claim is made that every tool or Altium version has been exercised. Original upstream development examples and test fixtures are retained as upstream material; their machine-specific example paths are not this project's local configuration.
