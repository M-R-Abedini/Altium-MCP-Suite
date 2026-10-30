# Altium MCP Suite

[![Tests](https://github.com/M-R-Abedini/Altium-MCP-Suite/actions/workflows/tests.yml/badge.svg)](.github/workflows/tests.yml)

[English](README.md) · [فارسی](docs/README.fa.md) · [简体中文](docs/README.zh-CN.md) · [العربية](docs/README.ar.md)

**MCP Altium Suite** is a desktop Codex plugin that packages one MCP connection, circuit-design and setup skills, and the coordinated Altium workers. It lets an AI read and edit schematics, PCBs and library files while you keep Altium open. This repository remains the source of truth for both the plugin and standalone MCP installation.

**Windows · Python 3.12 · Tested live on Altium Designer 26**

## What it can do

- Read and edit the open schematic: add components, draw wires, place net labels and power ports, edit parameters, pull a BOM or netlist.
- Work the PCB: move components, lay tracks and vias, check placement clearances, panelize.
- Audit the design: orphan net labels, floating ports, unconnected IC pins, missing decoupling caps, designator collisions, off-grid parts — or 33 lint checks in one call, with optional DRC.
- Check connectivity with four read-only tools: snapshot pin-to-net assignments (SHA-256 digest), per-pin contracts (expected net, or intentional NC with a reason), before/after diffs, and schematic↔PCB pad parity in both directions.
- Calculate trace width, impedance, and length budgets. These are calculators, not a signal-integrity proof.
- Read and write `.SchLib` / `.PcbLib` library files.
- Search ChilaBoard's public catalog and download revision-pinned libraries with source/hash/license receipts. Stage authorized SnapMagic/SamacSys downloads without executing archive contents. Native geometry and pin mapping still need verification. See [unified suite and cloud workflow](docs/review/UNIFIED_SUITE_2026-10-27.md).
- Use the researched [altium-circuit-design skill](skills/altium-circuit-design/SKILL.md) for requirements, model verification, structured schematic plans and design-check evidence.
- Manage native assembly variants by GUID, bulk-update fitted states, and export their component matrix. Changes use project backups and native readback; see the [variant workflow](docs/review/STORAGE_VARIANTS_2026-10-08.md).
- Two bridges, one script engine: a shared lock keeps the two Python bridges from fighting over Altium's script engine, and every request carries an ID so a late answer to an old request is never accepted.
- Concurrent clients: a local FIFO service admits native commands in order; health traffic yields to queued work. `pcb_capture_snapshot` reads components, nets and pad assignments in one native pass. Clients can then use `design_read_snapshot` and `design_analyze_snapshot` concurrently without occupying Altium. Snapshots are historical evidence, not live edit preconditions. See [usage and measured limits](docs/review/CONCURRENT_CLIENTS_2026-10-14.md).
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

### Codex plugin

The plugin bundles its MCP definition and skills together. From a development
checkout with the Python dependencies installed, build a clean package, register
its local marketplace, and install it with the supported Codex CLI:

```powershell
.\.venv\Scripts\python.exe scripts/build_plugin.py --output dist --smoke
codex plugin marketplace add ./dist/marketplace --json
codex plugin add mcp-altium-suite@altium-mcp-suite --json
```

Run the bundled **Altium Suite Setup** workflow after installation. It can adopt
an existing tested suite or explicitly prepare a private runtime. Altium Designer
and its license remain separate prerequisites. Runtime configuration stays outside
the plugin cache; upgrades select the new package's code while preserving the
chosen editor workspace, library paths and tool restrictions. Normal startup does
not install dependencies. Avoid enabling a second standalone connection alongside
the plugin. See [installation, upgrades and verification](docs/PLUGIN_PACKAGE.md).

### Standalone MCP / development

See [transport checks and manual recovery](docs/review/TRANSPORT_FINDINGS_2026-10-08.md) for unresolved script ownership after a timeout or engine fault.

Install Git, Python 3.12, and Altium on Windows. Use your own Altium path.

```powershell
git clone https://github.com/M-R-Abedini/Altium-MCP-Suite.git
Set-Location Altium-MCP-Suite
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE"
```

Merge the generated `mcp.local.json` (or `codex.local.toml`) into your MCP client config and restart its connections. Setup now emits one `mcp-altium-suite` connection, with display identity `mcp altium suite`, and a private `suite-backends.local.json` worker manifest. Open your project in Altium, discover `app_context` with `suite_catalog`, then invoke the returned `eda__app_context` name through `suite_invoke`.

The default `--toolset minimal` advertises only `suite_catalog`, `suite_invoke` and `suite_status`. Discover an operation with `suite_catalog(query="...", backend="eda", with_schema=true)`, then call `suite_invoke(name="eda__...", arguments={...})`. Namespaces are `eda__`, `legacy__`, `libraries__`, `cloud__`, `suite__`, `supply__`, `route__` and `telemetry__`. Schemas load on demand and arguments are validated before forwarding. `--toolset full` advertises every namespaced tool; `--separate-servers` is accepted for migration but still produces the unified endpoint. Existing EDA-only measurements in the [performance review](docs/review/PERFORMANCE_2026-10-08.md) are not unified-suite benchmarks.

```powershell
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE" --library-dir "D:\MyProject\Libraries"
```

The 34 library operations run through original Python OLE2 codecs in the gateway. No Rust compiler or external library executable is needed. Repeat `--library-dir` for more directories. Setup adds only the suite's cloud staging root alongside the selected library roots. SnapMagic and SamacSys account/API access is not bundled.

For an existing three-server Codex installation, deploy and test the new entrypoint first, then use `configure_suite.py --cloud-cache <absolute-cache-path>` to preview migration. Add `--apply` to back up and migrate the three connection tables while preserving unrelated settings. See [migration and rollback](docs/review/UNIFIED_SUITE_2026-10-27.md).

## Questions and contributions

Ask setup and usage questions in [Q&A](https://github.com/M-R-Abedini/Altium-MCP-Suite/discussions/categories/q-a).
For bugs and feature requests, use [Issues](https://github.com/M-R-Abedini/Altium-MCP-Suite/issues/new/choose).
See [the contributing guide](CONTRIBUTING.md) for development setup, tests, translations, and crediting collaborators.

## License

Suite coordination, native library codecs and legacy compatibility: MIT. EDA backend: Apache-2.0. See [license scope and exceptions](THIRD_PARTY_NOTICES.md) and [upstream snapshots](UPSTREAM.json).
# Free desktop product baseline and native libraries (0.4.0)

The [0.2.0 baseline release](https://github.com/M-R-Abedini/Altium-MCP-Suite/releases/tag/v0.2.0)
and the annotated `v0.3.0` Free-Tier Desktop baseline are merged in `main`. The baseline adds free local disk
checkpoints, electrical-value normalization, component supply adapters, a local
Freerouting runner, explicit opt-in structural datasets, multi-client setup,
and a Windows installer build pipeline. No suite account is required.

Discover `suite`, `supply`, `route` and `telemetry` backends with
`suite_catalog`; the advertised minimal MCP surface remains three meta-tools.
Start with [native edit safety](docs/PRODUCT_SAFETY.md),
[supply intelligence](docs/SUPPLY_CHAIN.md), [routing limits](docs/AUTOROUTE.md),
[optional datasets](docs/TELEMETRY.md), and [installation](docs/INSTALLER.md).
These documents distinguish working public/offline features from API access
requirements and unfinished native exchange; SES generation does not establish
Altium import or a DRC-clean board. Collection is disabled by default.

The 0.4.0 development branch replaces the external library implementation with
independent Python codecs and keeps all 34 public library schemas. The 37 legacy
schemas remain compatibility aliases through the single EDA transport. Historical
releases retain their original contents and licenses.
