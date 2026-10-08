# Transport review and proposed issue backlog

Reviewed commit: `7cb1932977d5b1b5fb26c95a0207583889b2baf9`.

**Status:** all five findings are addressed by the current source and `tests/test_review_fixes.py`. Descriptions below preserve the pre-fix evidence. The reproducer command now runs acceptance tests for the repaired behavior.

Post-fix validation: 75 suite tests and 152 selected EDA tests passed. A live AD26 stdio smoke test compiled the updated script project, executed a read-only legacy command, received its final completion acknowledgement and restored the EDA bridge. No schematic/PCB edits were made. Circuit construction was tested through mocked backend and file fixtures; native geometry generation was not exercised on the active hardware design.

Scope: both Python bridges, coordination, their file protocols, and the legacy schematic builder. This is a targeted review, not a complete audit of all design tools.

Five defects were reproduced using actual Python functions extracted from the source, temporary IPC files and mocked Altium execution. No editor was launched and no hardware document was changed. Native consequences described below still require controlled AD26 validation.

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe docs/review/reproduce_transport_findings.py
```

The acceptance tests cover cancellation, unconsumed timeout requests, late completion, invalid response envelopes, unique batch files, unsafe annotation text and editor-session ownership.

## Proposed issues, in repair order

### 1. [P1] Legacy timeout releases ownership and attempts EDA recovery while execution is unresolved

**Sources:** `coffeenmusic/server/main.py::_execute_command_locked`, `coffeenmusic/server/codex_stdio.py::coordinated`.

After publishing a request and launching the one-shot script, simulate 120 seconds without a matching response. Python returns failure but leaves `request.json` present. The wrapper calls `start_eda` in `finally` even though this failure does not prove that the legacy handler stopped. Cancellation has the same ownership uncertainty.

**Risk:** a delayed script can consume an abandoned edit; recovery may try to start another script while the previous one still owns the engine. Existing window/modal checks do not establish completion of a non-modal handler.

**Acceptance:** withdraw unconsumed requests on every exit; keep an explicit unresolved-operation record after timeout/cancellation; refuse engine handback and further edits until completion or a deliberate recovery action is confirmed. Do not replay the edit. Test late consumption, late matching responses, cancellation and successful handback.

**Evidence:** reproducer F1 verifies the retained file and unconditional restoration attempt. A real concurrent native launch/crash was not attempted.

### 2. [P1] Legacy schematic record strings permit extra operations and lossy encoding

**Sources:** `coffeenmusic/server/main.py::build_schematic`, `coffeenmusic/server/AltiumScript/schematic_utils.pas::BuildCircuitFromSpec`.

A note containing `10Ω\nWIRE|0|0|100|100` becomes two records; its value becomes `10?` because writing uses `cp1252` with `errors="replace"`. The Pascal builder dispatches each line by its first pipe-delimited field. Free-text values are interpolated without rejecting line breaks or field delimiters. The new EDA JSON guard does not protect this separate legacy batch-file path.

**Risk:** annotation text can create unintended geometry; labels, comments or parameters can silently change. This can arise from ordinary multiline generated text, without a malicious caller.

**Acceptance:** validate all fields before writing anything; reject unescaped record/field delimiters; use strict encoding or a tested Unicode protocol; report the exact offending field. Test notes, labels, comments, parameter names/values, library paths and round-trip preservation. Keep semantic labels unchanged rather than silently sanitizing them.

**Evidence:** reproducer F3 demonstrates the extra WIRE record and replacement character. Parsing consequences follow from Pascal source inspection; no wire was created in Altium.

### 3. [P1] Schematic builder writes a different specification path from the deployed reader

**Sources:** `coffeenmusic/server/main.py::build_schematic`, `coffeenmusic/server/AltiumScript/Altium_API.pas`, `suite_config.py::prepare_runtime`.

Python writes `C:/Users/Public/altium_mcp/circuit_spec.txt`. The generated Pascal reads `ROOT_DIR + 'circuit_spec.txt'`, where `ROOT_DIR` is the per-user `legacy-exchange`. They are different directories. The pin map still uses a Public path on both sides, retaining cross-user shared state.

**Risk:** a fresh build fails to find its input. If an old specification exists in the exchange directory, it can read a different circuit from the one just requested. No stale circuit was applied during this review.

**Acceptance:** use the configured exchange directory for both specification and pin-map files; publish atomically; tie input/output to a request ID; reject stale maps. Test generated runtime scripts and Python against the same nondefault exchange directory.

**Evidence:** reproducer F2 captures Python's actual destination and verifies the differing Pascal/runtime expressions.

### 4. [P2] EDA accepts an invalid response envelope as success

**Source:** `eda-agent/src/eda_agent/bridge/altium_bridge.py::_poll_loop`, `CommandResponse.from_dict`.

Write `{"id":"other-id","success":"false","data":"accepted"}` to the expected response filename. The polling code accepts it; `success` becomes a nonempty string and is truthy. It does not check the body ID against the requested ID or enforce a boolean success field. A JSON list instead of an object also escapes as an unstructured exception.

**Risk:** malformed or incorrectly associated replies can be reported as successful edits. Per-request filenames reduce accidental mixing but are not envelope validation.

**Acceptance:** require an object envelope, matching ID, boolean success and well-formed error/protocol fields before acceptance. Fail with a structured protocol error; never repeat a mutation. Test mismatched IDs, string/integer success, arrays and valid failures.

**Evidence:** reproducer F4 passes the malformed envelope through the actual polling path.

### 5. [P2] Progress-file age cannot distinguish a long handler from an abandoned marker

**Sources:** `bridge_coordination.py::_handler_busy`, `eda-agent/scripts/altium/Main.pas::StartProgress`.

`_handler_busy` ignores progress files older than 600 seconds. `StartProgress` writes once on entry and does not refresh the timestamp while a handler executes. A still-existing marker aged 601 seconds is classified as not busy. Conversely, a crashed handler's recent marker can block recovery for ten minutes.

**Risk:** age-based decisions can allow recovery over a long-running handler or delay recovery after a crash. The reproduced classification does not prove that a particular native handler runs for ten minutes.

**Acceptance:** distinguish an in-flight marker from evidence of liveness, bind ownership to the editor session/request, and do not infer safe relaunch solely from elapsed time. Test old active markers, recent abandoned markers, editor restart and normal completion. No unsafe automatic marker deletion.

**Evidence:** reproducer F5 verifies the 601-second classification; source inspection confirms the marker is not refreshed.

## Tracking recommendation

Create five separate issues in **Altium-MCP-Suite**, with the titles, evidence and acceptance criteria above. Fix legacy ownership first, then batch-record validation and path consistency. Separate these confirmed bugs from ongoing native limitations such as single-threaded scripting, modal ECO and lack of copper verification.

GitHub issue search on 2026-10-08 returned no existing issues for this repository. No issues were published by this review; the subsequent repair is tracked in source and regression tests. Relevant upstream proposals can use these minimal reproducers after native validation.

## Recovering unresolved ownership

A timeout or cancellation cannot establish that a consumed edit stopped. The suite keeps ownership until the legacy script publishes its final request-specific completion file, or the editor process is observed to have restarted. EDA progress files from the same editor session do not expire based on age.

If the script has faulted, stop it in Altium's Script IDE and inspect the design. Only after confirming that no script is running, execute `bridge_coordination.py --confirm-script-stopped` using the server's configured runtime environment. Pass `--workspace` with its configured `EDA_AGENT_WORKSPACE` if needed. This withdraws queued requests and clears unresolved markers under the common engine lock; it does not launch a script or replay an edit. Merely closing a dialog is not confirmation that a handler stopped.
