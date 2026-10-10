# Altium MCP Suite

[![Tests](https://github.com/M-R-Abedini/Altium-MCP-Suite/actions/workflows/tests.yml/badge.svg)](.github/workflows/tests.yml)

[English](README.md) · [فارسی](docs/README.fa.md) · [简体中文](docs/README.zh-CN.md) · [العربية](docs/README.ar.md)

MCP servers that let an AI read and edit your Altium schematics, PCBs, and libraries. You keep Altium open; the AI works on the project you have open, live.

**Windows · Python 3.12 · Tested live on Altium Designer 26**

## What it can do

- Read and edit the open schematic: add components, draw wires, place net labels and power ports, edit parameters, pull a BOM or netlist.
- Work the PCB: move components, lay tracks and vias, check placement clearances, panelize.
- Audit the design: orphan net labels, floating ports, unconnected IC pins, missing decoupling caps, designator collisions, off-grid parts — or 33 lint checks in one call, with optional DRC.
- Check connectivity with four read-only tools: snapshot pin-to-net assignments (SHA-256 digest), per-pin contracts (expected net, or intentional NC with a reason), before/after diffs, and schematic↔PCB pad parity in both directions.
- Calculate trace width, impedance, and length budgets. These are calculators, not a signal-integrity proof.
- Read and write `.SchLib` / `.PcbLib` library files.
- Manage native assembly variants by GUID, bulk-update fitted states, and export their component matrix. Changes use project backups and native readback; see the [variant workflow](docs/review/STORAGE_VARIANTS_2026-10-08.md).
- Two bridges, one script engine: a shared lock keeps the two Python bridges from fighting over Altium's script engine, and every request carries an ID so a late answer to an old request is never accepted.
- Script projects have distinct EDA/legacy names. On EDA startup, verified older generated projects are closed; edited sources are retained. [Lifecycle checks](docs/review/SCRIPT_PROJECT_LIFECYCLE_2026-10-10.md).

## What's still in development

- Placement assistance: batch placement and clearance checks work; finer placement tooling is still being worked on.
- Bridge reliability: every known DelphiScript crash gets fixed or guarded, but new ones still surface — this is ongoing.
- Validation so far is on Altium Designer 26; other versions haven't been exercised yet.

## Current limitations

- Native batch edits can leave partial changes after a failure; undo grouping does not guarantee rollback. Save a copy before bulk edits.

- Experimental. Some operations can crash Altium's DelphiScript engine and stop the polling loop — back up your design before letting the AI edit it.
- Native save and script-backed UI commands can wait during a handler. The loop releases the engine after about two idle seconds; background pings do not extend that deadline. The next coordinated tool call restarts it automatically.
- EDA JSON parameters above U+00FF (Ω, Chinese text) are rejected before dispatch to prevent silent `?` substitution. Full Unicode transport is not implemented.
- The EDA transport preserves UNC paths; access to the share depends on Windows permissions.
- `proj_sync_pcb` requires `allow_modal=True`; the schematic→PCB ECO still opens an interactive dialog.
- The connectivity checkers verify pin assignments, not copper. They can't see data you didn't feed them.
- An open modal dialog blocks script commands; Win32 dialog diagnostics remain available. Busy handlers reject new dispatch. Timed-out requests are withdrawn if not yet consumed; in-flight edits cannot be cancelled, so inspect the design before retrying.

## Setup

See [transport checks and manual recovery](docs/review/TRANSPORT_FINDINGS_2026-10-08.md) for unresolved script ownership after a timeout or engine fault.

Install Git, Python 3.12, and Altium on Windows. Use your own Altium path.

```powershell
git clone https://github.com/M-R-Abedini/Altium-MCP-Suite.git
Set-Location Altium-MCP-Suite
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE"
```

Merge the generated `mcp.local.json` (or `codex.local.toml`) into your MCP client config and restart its connections. Open your project in Altium and call `app_context` to check the bridge.

For a smaller tool list, pass `--toolset minimal` to `setup.py` or the EDA server. Discover an operation with `tool_catalog(query="...", with_schema=True)`, then call `tool_invoke(name="...", arguments={...})`. All operations remain available; schemas load on demand with the same input validation as full mode. See [measured latency and tool-list size](docs/review/PERFORMANCE_2026-10-08.md).

```powershell
Push-Location altium-designer-mcp
cargo build --release --locked
Pop-Location
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE" --library-dir "D:\MyProject\Libraries"
```

The library server is optional (needs the pinned Rust toolchain + VS C++ build tools). Repeat `--library-dir` for more directories.

## Questions and contributions

Ask setup and usage questions in [Q&A](https://github.com/M-R-Abedini/Altium-MCP-Suite/discussions/categories/q-a).
For bugs and feature requests, use [Issues](https://github.com/M-R-Abedini/Altium-MCP-Suite/issues/new/choose).
See [the contributing guide](CONTRIBUTING.md) for development setup, tests, translations, and crediting collaborators.

## License

Suite glue: MIT. EDA backend: Apache-2.0; legacy backend: MIT; Rust library backend: GPL-3.0-or-later. See [license scope and exceptions](THIRD_PARTY_NOTICES.md) and [upstream snapshots](UPSTREAM.json).
