# Bridge performance — 2026-10-08

Measured against an open Altium Designer 26.10.1 editor, using the coordinated stdio entry point and `app_get_active_document` on `USB_Debug.SchDoc`.

| Measurement | Before | Revised full mode | Revised minimal mode |
| --- | ---: | ---: | ---: |
| Request after three seconds idle, median | 8,818 ms | 3,778 ms | 3,786 ms |
| Consecutive requests, median | 74 ms | 85 ms | 88 ms |
| Advertised tools | 447 | 447 | 2 |
| Serialized advertised tool schemas, UTF-8 bytes | 743,733 | 743,733 | 4,749 |

Eight consecutive samples per run; three idle samples before and in minimal mode, five in revised full mode. The resumed request improved by about 57%. Consecutive requests showed no speed improvement. Minimal mode reduces the initial tool-schema payload by 99.36%; this is a byte measurement, not a measurement of a client's total token usage. All 447 operations remain discoverable and invokable.

## Changes and recovery rules

- Native startup publishes a new session ID after request purges and form initialization. Clean shutdown publishes a matching acknowledgement after cleanup. The coordinator records that identity only after a successful health response and binds it to the editor PID and process start time.
- Only that confirmed clean shutdown skips the old two-second and three-second health probes. A missing ready file, stale or damaged acknowledgement, process restart, or dispatcher error keeps conservative recovery.
- Modal dialogs, unresolved legacy operations, live handlers and the launch cooldown still block restart. A handler starting during the second failed health probe now also prevents launch.
- Background keepalive yields immediately when the shared lock is busy. Its response wait is capped at one second if shutdown races its ready check. It never restarts the engine. A plain foreground ping returns the already received preflight result.
- The native two-second idle release remains in place. Startup delay from Altium's launcher and script initialization remains; this change does not make cold compilation instantaneous.
- Minimal discovery uses lazily cached FastMCP schemas and argument validation. Arrays, nullable fields, enums, constraints and nested definitions match full mode. Broad catalog requests exceeding the schema cap do not construct schemas that will be omitted.
- The wrapper processes CLI backend/toolset selection before server registration. Previously, `--toolset minimal` re-executed the bare server and bypassed the coordination wrapper, causing a real request to time out after idle release.

## Validation

The selected Windows regression suite passed **259 tests**, with **9 skipped**. Native script lint reported zero errors and warnings. Tests cover ownership evidence, stale/malformed markers, all restart guards, the second-probe race, background lock release, CLI configuration, schema parity across Altium/KiCad/EasyEDA, and rejection of invalid arguments before handler execution.

The installed EDA and legacy servers initialized successfully. Native readback on the hardware project confirmed two variants, 16 exclusions, all 366 fitted-state matrix cells and the `eMMC_8GB` active variant. Hardware project, schematic and PCB file hashes were unchanged. Live read-only commands succeeded in full and minimal modes with script version `2026.10.08.perf1`.

The installed minimal server repeated the benchmark successfully: 3,768 ms median after idle, the same 4,749-byte tool list, and unchanged design hashes. A further 34 tool-wrapper and connectivity regression tests passed.

Run the same benchmark against your configured native workspace:

```powershell
.\.venv\Scripts\python.exe tests/benchmark_stdio.py --workspace "C:\Path\To\workspace" --output .runtime/latency-full.json
.\.venv\Scripts\python.exe tests/benchmark_stdio.py --workspace "C:\Path\To\workspace" --toolset minimal --output .runtime/latency-minimal.json
```

Use `--root` and `--python` to benchmark an installed copy. Optional repeated `--design-file` arguments verify that the named design files retain their hashes. Local samples are in `.runtime/performance-before.json`, `performance-after-stable.json` and `performance-minimal.json`.

To enable compact discovery, add `--toolset minimal` to the EDA server arguments and reconnect it. Query `tool_catalog` with a narrow name/category filter and `with_schema=True`, then pass the returned arguments to `tool_invoke`. Full mode remains the default for existing integrations.
