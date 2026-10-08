# Altium MCP Suite

**[English](README.md) · [فارسی](docs/README.fa.md) · [简体中文](docs/README.zh-CN.md)**

[![Suite tests](https://github.com/M-R-Abedini/Altium-MCP-Suite/actions/workflows/tests.yml/badge.svg)](https://github.com/M-R-Abedini/Altium-MCP-Suite/actions/workflows/tests.yml)

Connect an MCP-compatible AI assistant to Altium Designer for schematic, PCB and library work. This suite brings three open-source servers together with coordinated script execution, connection recovery and explicit connectivity checks.

**Windows · Live integration tested on Altium Designer 26 · Python 3.12**

## What you can do

| Area | Capabilities |
| --- | --- |
| Schematics | Inspect components and nets, place and edit objects, build schematics from structured plans, review connectivity and layout |
| PCB | Inspect geometry and rules, place components and copper, plan routes, calculate trace widths, impedance and length budgets |
| Libraries | Create and edit symbols and footprints; compare footprint geometry with a manufacturer-derived land-pattern specification |
| Connectivity | Capture pin-to-net snapshots, check expected nets and intentional NCs, compare revisions, and check schematic pins against PCB pads |
| Review and outputs | Generate visual previews, review reports and BOMs; invoke Altium output jobs |

Connectivity checks preserve full hierarchical net names and reject malformed input. Matching but incomplete captures cannot pass verification. See [examples and input requirements](CONNECTIVITY_VERIFICATION.md).

## What the suite adds

The underlying design tools come from the included projects. The suite adds a common deployment and coordination layer, plus four connectivity verification tools.

| Using the projects separately | With this suite |
| --- | --- |
| Separate entry points and configuration | One Windows setup generates MCP JSON and Codex TOML configuration |
| Each Python bridge manages its own script execution | A shared lock and handover coordinate access to Altium's scripting engine |
| Separate bridge lifecycles | Health checks and guarded recovery; timed-out editing commands are not replayed automatically |
| Different verification surfaces | One workflow for snapshots, pin contracts, connectivity diffs and bidirectional schematic/PCB pad checks |
| Separate sources and test instructions | Recorded upstream commits, retained licenses and a suite-level CI workflow |

This comparison describes the bundled snapshots, not every current version of another MCP. The original projects remain useful independently:

| Included project | Role | License |
| --- | --- | --- |
| [eda-agent](eda-agent/README.md) | Main schematic, PCB and library automation | Apache-2.0 |
| [coffeenmusic/altium-mcp](coffeenmusic/README.md) | Additional Altium commands through the legacy bridge | MIT |
| [altium-designer-mcp](altium-designer-mcp/README.md) | Independent Rust server for Altium library-file operations | GPL-3.0-or-later |

Exact source repositories and commits are listed in [UPSTREAM.json](UPSTREAM.json).

## Quick start

Install Git, Python 3.12 and Altium Designer on Windows. A working Altium installation is required for live design operations.

```powershell
git clone https://github.com/M-R-Abedini/Altium-MCP-Suite.git
Set-Location Altium-MCP-Suite
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE"
```

Use the actual path to your Altium executable. Setup writes `mcp.local.json` and `codex.local.toml`; merge the relevant entries into your client's settings and restart its MCP connections. Existing client settings are not overwritten.

Open your project in Altium, then ask your assistant to call `app_context` before editing. It reports the active document and bridge/script state. Use the generated coordinated entry points rather than running a second copy of the old bridges alongside them.

### Optional library-file server

The Rust server is a separate build. It uses the pinned Rust 1.95.0 toolchain and requires Visual Studio C++ build tools on Windows.

```powershell
Push-Location altium-designer-mcp
cargo build --release --locked
Pop-Location
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE" --library-dir "D:\MyProject\Libraries"
```

Replace the library path with an existing directory. `--library-dir` may be repeated; setup adds the library server to the generated configuration. The executable is built at `altium-designer-mcp/target/release/altium-designer-mcp.exe`.

### Smaller tool discovery

For clients that load every tool schema into context, append `--toolset`, `minimal` to the generated **eda-agent** argument list. This exposes `tool_catalog` and `tool_invoke` for discovery and execution while retaining access to the full tool collection. Append `--no-dashboard` if you do not want the local review dashboard.

## Operating limits

- Live Altium control is Windows-only and has been exercised on AD26. Other Altium releases are not verified by this suite.
- Modal dialogs can block the bridge. Complete or close the dialog before retrying. After an editing timeout, inspect the design before retrying: the command may already have changed it.
- A clean connectivity report does not establish ERC/DRC, footprint geometry, copper connectivity or manufacturing readiness. Capture completeness and freshness must be established by the caller.
- Some tools plan or calculate a change; others apply it. Check the tool's result and scope. The suite is not a guarantee of a fully autonomous, fabrication-ready design.

## Documentation and validation

- [Connectivity verification](CONNECTIVITY_VERIFICATION.md) — tool inputs, examples and acceptance rules.
- [Review and validation scope](REVIEW.md) — publication evidence and known limits.
- [Local changes](MODIFICATIONS.md) — changes to the bundled projects.
- [CI workflow](.github/workflows/tests.yml) — Python regression/discovery tests and Rust tests.

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest tests eda-agent/tests/design/test_connectivity_contracts.py -q
.\.venv\Scripts\python.exe tests/smoke_stdio.py
```

The stdio smoke test checks server startup and the four connectivity tools. Its optional `--live` mode stops and recovers the open Altium bridge; run it only after current operations finish. CI does not validate every tool against a live Altium session.

For reproducible problems, [open an issue](https://github.com/M-R-Abedini/Altium-MCP-Suite/issues) with your Altium version, the tool name, steps to reproduce and the returned error.

## Credits and licenses

This distribution retains the original authorship and licenses. The coordination code and new connectivity modules use [MIT](LICENSE); that license does not replace the Apache-2.0, MIT or GPL licenses of the included projects. See [source provenance](UPSTREAM.json) before redistributing components.
