# Altium MCP Suite

[English](README.md) · [فارسی](docs/README.fa.md) · [简体中文](docs/README.zh-CN.md)

[![Tests](https://github.com/M-R-Abedini/Altium-MCP-Suite/actions/workflows/tests.yml/badge.svg)](https://github.com/M-R-Abedini/Altium-MCP-Suite/actions/workflows/tests.yml)

MCP servers for inspecting and editing Altium schematics, PCBs and libraries. Live operations run through Altium's DelphiScript engine; library-file operations use a separate Rust server.

**Windows · Python 3.12 · Live integration tested on Altium Designer 26**

## Architecture and capabilities

| Backend | Scope | License |
| --- | --- | --- |
| [eda-agent](eda-agent/README.md) | Schematic objects and compiled nets; PCB components, pads and copper; symbol/footprint editing | Apache-2.0 |
| [coffeenmusic/altium-mcp](coffeenmusic/README.md) | Additional live Altium commands through a file-based request/response bridge | MIT |
| [altium-designer-mcp](altium-designer-mcp/README.md) | Read/write `.SchLib` and `.PcbLib` files within configured library directories | GPL-3.0-or-later |

Routing includes an offline Manhattan A* planner that produces track/via operations. Trace-width, impedance and length-budget calculations support design decisions; they do not establish signal integrity or replace a field solver. Footprint auditing compares pad geometry against a manufacturer specification supplied by the caller.

Compared with running the bundled servers independently, this suite adds:

- **Script coordination:** a shared lock serializes the two Python bridges; handover pauses EDA polling while the legacy bridge owns the script engine.
- **Response integrity and recovery:** atomic request publication and request IDs prevent stale-response acceptance. Recovery checks editor/bridge state; timed-out editing commands are not automatically replayed.
- **Electrical regression checks:** four read-only tools below compare captured pin/pad assignments. Full hierarchical net names remain distinct, e.g. `/Camera0/RESET` and `/Camera1/RESET`.

| Tool | Check |
| --- | --- |
| `design_connectivity_snapshot` | Canonical component/pin/net assignments and SHA-256 digest |
| `design_check_pin_contracts` | Expected net or intentional NC with a reason, per pin |
| `design_diff_connectivity` | Added/removed pins and changed net assignments |
| `design_check_schematic_pcb_parity` | Missing/extra PCB pads and symbol/pad net mismatches, in both directions |

These are suite additions, not a KiCad runtime dependency. Comparison is against the [recorded upstream snapshots](UPSTREAM.json).

## Setup

Install Git, Python 3.12 and Altium on Windows. Substitute your actual Altium executable path:

```powershell
git clone https://github.com/M-R-Abedini/Altium-MCP-Suite.git
Set-Location Altium-MCP-Suite
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE"
```

Merge the generated `mcp.local.json` or `codex.local.toml` entries into your MCP client's configuration, then restart its connections. Open the project in Altium and call `app_context` to check the active document and bridge state.

For smaller tool discovery, append `"--toolset", "minimal"` to the generated **eda-agent** argument list: `tool_catalog` and `tool_invoke` provide access to the full collection without exposing every schema at startup.

The optional library-file server requires the pinned Rust toolchain and Visual Studio C++ build tools. Supply an existing library directory; repeat `--library-dir` for additional directories:

```powershell
Push-Location altium-designer-mcp
cargo build --release --locked
Pop-Location
.\.venv\Scripts\python.exe setup.py --altium-exe "C:\Program Files\Altium\AD26\X2.EXE" --library-dir "D:\MyProject\Libraries"
```

## Verification boundaries

Capture freshly compiled, unfiltered schematic nets and PCB pads from the same project revision. `complete=true` is the caller's coverage assertion; the tools cannot discover omitted source records. A capture marked incomplete cannot pass verification. See [input contracts and examples](CONNECTIVITY_VERIFICATION.md).

Pad/net parity does **not** verify routed copper, clearances, differential-pair skew, footprint orientation or ERC/DRC. Run Altium's native checks and inspect their results before releasing manufacturing outputs. After an editing timeout, inspect the document before retrying; the first command may have taken effect. Modal dialogs can block live execution.

## Tests and maintenance

```powershell
.\.venv\Scripts\python.exe -m pytest tests eda-agent/tests/design/test_connectivity_contracts.py -q
.\.venv\Scripts\python.exe tests/smoke_stdio.py
```

The stdio smoke test exercises server startup and the four connectivity tools with synthetic data. CI does not validate every command in live Altium. See [validation scope](REVIEW.md), [suite changes](MODIFICATIONS.md) and the [CI workflow](.github/workflows/tests.yml). Report issues with the Altium version, tool name, reproducer and returned error.

Coordination code and new connectivity modules use [MIT](LICENSE). Each bundled project retains its own license and authorship; see [UPSTREAM.json](UPSTREAM.json).
