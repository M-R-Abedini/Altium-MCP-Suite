# External analysis: claim-by-claim verification — 2026-10-09

Baseline: `3e4f58c`; coordinated Windows wrappers, EDA file IPC, legacy native
scripts and the independent Rust library server. Severity labels in the supplied
report were not treated as evidence.

| Claim | Verified result / action |
| --- | --- |
| Unauthenticated Altium socket on `0.0.0.0` | Incorrect architecture: EDA and legacy use per-user files; Rust uses stdio. The separate optional EasyEDA socket defaults to loopback. No Altium listener to rebind. |
| Traversal through request ID | Not reproduced: Python generates IDs locally; native scanning/response/progress writes validate their alphabet. MCP callers do not provide filename IDs. Same-user access to executable IPC files remains a trust boundary. |
| Ordinary parameters become Pascal source | Incorrect: ordinary tools use JSON/spec files. Found a real shell launch in the explicit snippet sandbox; changed it to `shell=False`. Running a requested snippet is the sandbox's intended capability. |
| Continuous GUI freeze; replace loop with timer | Partly true: long native handlers occupy the GUI thread. Idle loop already pumps messages and releases the engine after two seconds. `TTimer` does not prove script-backed shortcuts can run while the engine is owned. No speculative rewrite. |
| `String[255]` truncates dispatcher JSON | No matching declaration in active bridges. They use dynamic strings and `TStringList`; 4,096-character native regression passes. Individual editor API limits are a different question. |
| Broken JSON escapes/nesting | **Confirmed; fixed.** Legacy stripped data commas/quotes and left escapes encoded. EDA key search could select a nested key or matching string value. Scoped lookup, single scalar decoding, safe JSON escaping, array-line decoding and rejection of unrepresentable legacy input now cover these cases. |
| Iterator leak on exceptions | **Confirmed cleanup gap; fixed in 23 legacy scopes.** Owner-specific iterator destruction now runs in `finally`. Normal paths already destroyed them. No sustained memory-growth/OOM claim was demonstrated; this was not a resource audit of every upstream feature. |
| Newline framing breaks text with newlines | Incorrect: MCP stdio requires newline-delimited JSON; data newlines are escaped. Native exchanges are whole JSON files. Length-prefixing stdio would break standard clients. |
| Unbounded receive buffer | **Confirmed for Rust stdin; fixed.** Limit is 64 MiB including delimiter. Oversize returns `InvalidData` and closes the transport before indefinite allocation. Supports ordinary multi-megabyte base64 payloads. Local robustness issue, not an exposed network attack. |
| Reconnect storm without jitter | Not found in Altium IPC: guarded probes, launch cooldown, sleeping file polls and deadlines already exist. Network reconnect jitter is not a relevant fix here. |
| Lock creation TOCTOU | Incorrect: `try_lock` uses nonblocking OS byte-range locking through `msvcrt.locking`, not lock-file existence. |
| Crash leaves permanent lock | Incorrect for OS locking: process death releases it. Operation markers deliberately retain uncertainty about native work. Expiring a live handler's ownership with TTL would weaken safety. |
| Stdio silently swallows tool errors | Not reproduced: FastMCP handles errors; legacy returns structured failures; Rust writes JSON-RPC parse/tool errors. Diagnostic-only catches do not establish missing replies. |
| False atomic rollback in `batch_safety.py` | Incorrect attribution: this file validates delimited fields and claims no rollback. Underlying native partial-batch risk is real; README now states it explicitly. Undo grouping is not guaranteed rollback. Rust backups/project-file variant updates have different scopes. |
| Rust socket/packet unwrap panic | Not substantiated as stated: no Altium socket in Rust, protocol parsing returns errors. Many unwraps are tests or constant regex construction. This is not proof that all binary-library parsers are panic-free. Verified transport weakness was unbounded input. |
| Python/Rust schema parity mismatch | Intentional: separate named servers expose live-editor and offline-library APIs. They are not interchangeable implementations of one API. |
| Unconditional `winreg` import | No `winreg` import found in production Python. Editor wrappers intentionally require Windows (`msvcrt`, Win32 APIs); a Linux container would not control local Altium merely by guarding imports. |
| Hardcoded AD22/23 installation | Overstated: suite accepts `ALTIUM_EXE`/setup selection and discovers `AD*/X2.EXE`, refusing ambiguity. Legacy fallback uses the standard directory; coordinated launch supplies the selected path. |
| Deployment requires Administrator/COM registration | Incorrect: deployment copies sources with backups and tests installed entry points. It does not register COM or require a protected destination. No unconditional elevation added. |
| Win32 tests on Ubuntu / wrong CI paths | Incorrect: Python runs on Windows with explicit EDA working directories; independent Rust job runs on Ubuntu. Prior PRs passed both jobs. |

Framing reference: [official MCP stdio specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio).

## Evidence

An additional P1 defect was found outside the supplied claims: the snippet
sandbox wrote hardcoded public output paths while Python read private paths,
and did not register native ownership. Each call now generates a private project
with matching paths and a unique final acknowledgement. Timeout/cancellation
retain ownership; a published result alone cannot start another engine. Native
log lists are freed, templates remain unchanged, and dialogs are left for the
operator rather than dismissed automatically. The direct-launch sandbox was
also verified against open Altium.

- Opt-in `tests/native_json_regression.py`: fourteen real DelphiScript checks passed. Covers punctuation, path/control escapes, Latin-1, a 4,096-character value and nested-key/value collisions; reproduces both original parser bugs with old helpers in the same engine.
- Final selected Python suites: 338 passed. Includes unsupported text rejected before publishing and sandbox result-before-ack, cancellation, failed launch, template preservation and path escaping.
- EDA native lint: eleven files, zero errors/warnings.
- Installed startup: 447 EDA / 37 legacy tools. Read-only `app_context`, `app_ping`, `get_all_designators`, `get_all_nets` and a harmless `run_altium_script` passed using updated native projects. All thirteen project/schematic/PCB SHA-256 hashes stayed unchanged across these checks.
- Rust regressions cover oversized frames with/without delimiters, exact limits, one-byte UTF-8 chunks, escaped text newlines, multiple frames and invalid UTF-8. Execution is checked by PR CI; this Windows host has no local Rust toolchain.

Remaining limits: long native calls can block interaction; native text support is
constrained; native bulk edits may remain partial after failure. These need
measured follow-up work, not the suggested framing/locking rewrites.

Nine additional native edit scopes now pair `PreProcess/PostProcess` and
`BeginModify/EndModify` in `finally`. This closes ordinary exception paths;
it does not undo edits or recover a DelphiScript debugger halt. Error injection
into the user's live design was not used as a validation method.
