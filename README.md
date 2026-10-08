# Altium MCP Suite

[![Tests](https://github.com/M-R-Abedini/Altium-MCP-Suite/actions/workflows/tests.yml/badge.svg)](.github/workflows/tests.yml)

[English](README.md) · [فارسی](docs/README.fa.md) · [简体中文](docs/README.zh-CN.md)

MCP servers that let an AI read and edit Altium schematics, PCBs, and libraries. Live edits run through Altium's DelphiScript engine on the project you have open; library files go through a separate Rust server.

**Windows · Python 3.12 · Live-tested on Altium Designer 26**

## What it can do

- **Schematic** — add components, place wires, net labels, and power ports on the open schematic; edit parameters; run annotation and ECO sync with no modal dialogs in the way.
- **PCB** — move components, lay tracks and vias, panelize. An offline Manhattan A* router generates track/via operations.
- **Audit** — orphan net labels, floating ports, unconnected IC pins, missing decoupling caps, designator collisions, off-grid parts; a 31-point lint sweep over schematic and PCB.
- **Calculate** — trace width, impedance, and length budgets to size a design decision. Calculators, not proof: they don't replace a field solver.
- **Libraries** — read and write `.SchLib` / `.PcbLib` inside your configured library directories.

## Servers

| Server | Scope | License |
| --- | --- | --- |
| `eda-agent` | Schematic objects and compiled nets; PCB components, pads, copper; symbol/footprint editing | Apache-2.0 |
| `coffeenmusic/altium-mcp` | Extra live Altium commands over a file-based request/response bridge | MIT |
| `altium-designer-mcp` | `.SchLib` / `.PcbLib` file access inside configured library directories | GPL-3.0-or-later |

## What this suite adds

New code is diffed against the [recorded upstream snapshots](UPSTREAM.json):

1. **One script engine, two bridges.** A shared lock serializes the two Python bridges: while the legacy bridge owns Altium's script engine, EDA polling pauses and is handed back afterwards. They never contend for the engine.
2. **No stale results, no double edits.** Requests are published atomically and carry an ID the response must echo back, so a late answer to an old request is never accepted. A timed-out edit is never replayed — the first attempt may already have landed.
3. **Electrical regression checks — four read-only tools.** Canonical pin-to-net snapshots with a SHA-256 digest; per-pin contracts (expected net, or intentional NC with a stated reason); before/after diffs; bidirectional schematic↔PCB pad parity. Full hierarchical net names stay distinct: `/Camera0/RESET` and `/Camera1/RESET` are different nets.

| Tool | Check |
| --- | --- |
| `design_connectivity_snapshot` | Canonical component/pin/net assignments + digest |
| `design_check_pin_contracts` | Expected net or intentional NC, per pin |
| `design_diff_connectivity` | Added/removed pins, changed net assignments |
| `design_check_schematic_pcb_parity` | Missing/extra PCB pads, symbol/pad net mismatches, both directions |

## Setup

Prerequisites: Git, Python 3.12, Altium on Windows. Replace the Altium path with yours.

```powershell
git clone https://github.com/M-R-Abedini/Altium-MCP-Suite.git
Set-Location Altium-MCP-Suite
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE"
```

Merge the generated `mcp.local.json` (or `codex.local.toml`) into your MCP client config and restart its connections. Open the project in Altium, then call `app_context` to confirm the active document and bridge state.

Smaller tool surface: append `"--toolset", "minimal"` to the generated `eda-agent` args. `tool_catalog` / `tool_invoke` still reach the full set.

Optional library server (needs the pinned Rust toolchain + VS C++ build tools):

```powershell
Push-Location altium-designer-mcp
cargo build --release --locked
Pop-Location
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE" --library-dir "D:\MyProject\Libraries"
```

Repeat `--library-dir` for more directories.

## Limits

- Capture freshly compiled, unfiltered nets and pads from the same project revision. `complete=true` is your assertion that the capture is whole — the tools can't see rows you never fed them, and an incomplete capture can never pass. Contracts and examples: [CONNECTIVITY_VERIFICATION.md](CONNECTIVITY_VERIFICATION.md).
- Pad/net parity checks pin assignments, not copper. It says nothing about routing, clearances, differential-pair skew, footprint orientation, or ERC/DRC — run Altium's native checks before release.
- After an edit timeout, inspect the document before retrying: the first command may already have taken effect.
- An open modal dialog blocks live execution.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests eda-agent/tests/design/test_connectivity_contracts.py -q
.\.venv\Scripts\python.exe tests/smoke_stdio.py
```

The smoke test starts the servers over stdio and runs the four connectivity tools on synthetic data. CI doesn't exercise every command against live Altium — see [REVIEW.md](REVIEW.md), [MODIFICATIONS.md](MODIFICATIONS.md), and the [test workflow](.github/workflows/tests.yml). Report issues with the Altium version, tool name, reproducer, and returned error.

## License

New coordination code and connectivity modules: [MIT](LICENSE). Bundled projects keep their own licenses and authorship — see [UPSTREAM.json](UPSTREAM.json).
