# Altium MCP Suite

[![Tests](https://github.com/M-R-Abedini/Altium-MCP-Suite/actions/workflows/tests.yml/badge.svg)](.github/workflows/tests.yml)

[English](README.md) · [فارسی](docs/README.fa.md) · [简体中文](docs/README.zh-CN.md) · [العربية](docs/README.ar.md)

MCP servers that let an AI read and edit your Altium schematics, PCBs, and libraries. You keep Altium open; the AI works on the project you have open, live.

**Windows · Python 3.12 · Tested live on Altium Designer 26**

## What it can do

- Read and edit the open schematic: add components, draw wires, place net labels and power ports, edit parameters, pull a BOM or netlist.
- Work the PCB: move components, lay tracks and vias, check placement clearances, panelize.
- Audit the design: orphan net labels, floating ports, unconnected IC pins, missing decoupling caps, designator collisions, off-grid parts — or one 31-point lint sweep over schematic and PCB.
- Check connectivity with four read-only tools: snapshot pin-to-net assignments (SHA-256 digest), per-pin contracts (expected net, or intentional NC with a reason), before/after diffs, and schematic↔PCB pad parity in both directions.
- Calculate trace width, impedance, and length budgets. These are calculators, not a signal-integrity proof.
- Read and write `.SchLib` / `.PcbLib` library files.
- Two bridges, one script engine: a shared lock keeps the two Python bridges from fighting over Altium's script engine, and every request carries an ID so a late answer to an old request is never accepted.

## What's still in development

- Placement assistance: batch placement and clearance checks work; finer placement tooling is still being worked on.
- Bridge reliability: every known DelphiScript crash gets fixed or guarded, but new ones still surface — this is ongoing.
- Validation so far is on Altium Designer 26; other versions haven't been exercised yet.

## Current limitations

- Experimental. Some operations can crash Altium's DelphiScript engine and stop the polling loop — back up your design before letting the AI edit it.
- While the loop runs, some of Altium's own script-backed buttons go unresponsive. Detach to give Altium back its engine.
- Text beyond Latin-1 (Ω, Chinese characters) arrives as `?`.
- Network paths must be mapped drives; UNC paths don't open.
- The schematic→PCB ECO opens a modal dialog — there is no silent API for it.
- The connectivity checkers verify pin assignments, not copper. They can't see data you didn't feed them.
- An open modal dialog blocks everything until you close it.

## Setup

Install Git, Python 3.12, and Altium on Windows. Use your own Altium path.

```powershell
git clone https://github.com/M-R-Abedini/Altium-MCP-Suite.git
Set-Location Altium-MCP-Suite
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE"
```

Merge the generated `mcp.local.json` (or `codex.local.toml`) into your MCP client config and restart its connections. Open your project in Altium and call `app_context` to check the bridge.

```powershell
Push-Location altium-designer-mcp
cargo build --release --locked
Pop-Location
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE" --library-dir "D:\MyProject\Libraries"
```

The library server is optional (needs the pinned Rust toolchain + VS C++ build tools). Repeat `--library-dir` for more directories.

## License

New suite code is MIT. Bundled projects keep their own licenses — see [UPSTREAM.json](UPSTREAM.json).
