# Contributing to Altium MCP Suite

Help is welcome with reproducible bug reports, Windows setup, documentation,
translations, bridge reliability, and tests. Start with a small change that you
can verify on its own.

## Questions and bug reports

Use [Q&A](https://github.com/M-R-Abedini/Altium-MCP-Suite/discussions/categories/q-a)
for setup and usage questions. When an answer solves your question, mark it as
the answer so other users can find it. Use
[Issues](https://github.com/M-R-Abedini/Altium-MCP-Suite/issues/new/choose) for
reproducible defects and feature requests. Search existing topics first.

For bridge problems, include the suite commit (`git rev-parse HEAD`), Windows,
Python and Altium versions, MCP client, server, tool name, input, expected
result, and actual response. Include whether a modal dialog was open and which
document type was active. The issue form asks for these details.

Share a minimal synthetic design when a file is needed to reproduce a problem.
Remove private design data, credentials, personal paths, and unrelated contents
from logs and screenshots before posting. Generated `*.local.json`,
`codex.local.toml`, `local-settings.json`, `suite-backends.local.json`, generated
deployment manifests and repair backups stay local. The development checkout is
the source of truth; an installed runtime is not a second development branch.

## Set up a development checkout

Install Git and Python 3.12 on Windows. Fork this repository if you do not have
write access, clone your fork, and run these commands from its root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
git switch -c docs/my-change
```

For a live bridge check, follow [Setup](README.md#setup) with your own Altium
executable path. The Python checks below do not require opening a real design.

## Verify your change

Run the suite regression tests from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

For changes in the bundled EDA server, run the relevant tests from its directory
so its `tests` package is resolved correctly. For example, changes to tool
discovery use:

```powershell
Push-Location eda-agent
..\.venv\Scripts\python.exe -m pytest tests/test_tool_catalog.py tests/test_minimal_toolset.py tests/test_registry_schema_validation.py -q
Pop-Location
```

The exact Python regression groups and Rust command run on GitHub are in
[the CI workflow](.github/workflows/tests.yml). If you change the Rust library
server, use its pinned toolchain and run:

```powershell
Push-Location altium-designer-mcp
cargo test --locked --all-targets
Pop-Location
```

The Altium editor backend and its real file-lock/UI tests require Windows.
Portable root tests run on Linux in CI; native Windows API tests are explicitly
skipped there. Both `pytest tests -q` and `python -m pytest tests -q` work from
the repository root. Linux checks do not establish Altium compatibility.

Check the original coordinated Python entry points without an Altium installation:

```powershell
.\.venv\Scripts\python.exe tests/smoke_stdio.py
```

This test uses isolated runtime paths and a deliberately nonexistent executable;
it checks protocol startup, schemas and offline tools. `--live` uses the real
configured editor and also checks native handover and recovery.

The unified server is the default client entrypoint. Root tests exercise its
minimal/full protocol, schema validation, cancellation and result forwarding.
For the new batch and planner contracts, run from the EDA test directory:

```powershell
Push-Location eda-agent
..\.venv\Scripts\python.exe -m pytest tests/design/test_performance_batches.py tests/design/test_discipline_contract.py tests/design/test_buses.py -q
Pop-Location
```

To compare all three real backend catalogs and input schemas without native
editor operations, supply a built library executable to the separate smoke:

```powershell
.\.venv\Scripts\python.exe tests/smoke_suite_stdio.py --library-command "C:\Path\To\altium-designer-mcp.exe"
```

This does not require an Altium installation. The complete real-backend smoke
does require the Rust executable; an absent backend is not a passing result.
Plugin artifact verification is described below and does not require that
binary or a real Altium editor.

## Plugin development

The plugin combines the unified MCP connection, circuit-design skill and setup
workflow. Keep one maintained source for each skill in `skills/`; generated
packages must not become an independent copy to edit. The normal runtime uses
`suite_catalog`, `suite_invoke` and `suite_status`, with `eda__`, `legacy__`,
`libraries__` and `cloud__` names discovered on demand.

Build a clean distribution from the checkout:

```powershell
.\.venv\Scripts\python.exe scripts/build_plugin.py --output dist --smoke --check-reproducible
```

The build produces a local marketplace and deterministic ZIP. Package tests
must inspect the built inputs, skills, notices and bundled source rather than
loading private user configuration or relying on a developer's virtualenv.
Follow [plugin packaging and setup](docs/PLUGIN_PACKAGE.md) for the actual
marketplace installation, onboarding and doctor commands. Install one suite
connection; avoid leaving the manual connection and plugin connection enabled
together. Python dependencies are prepared explicitly during setup, not
downloaded on every MCP call.

Native Altium remains a Windows dependency with its own license. Protocol and
offline artifact checks do not establish live schematic/PCB compatibility,
successful ERC/DRC, or hardware correctness. Provider models remain unverified
until their exact package and pin/pad mapping have supporting evidence.

Documentation changes should have working links and commands that match the
current code. Before committing, run `git diff --check` and inspect `git diff`.

Altium integration tests are opt-in. For a live check, use a backed-up copy or a
synthetic project and record the Altium version, tool calls, and observed result.
Distinguish mock or offline test results from live validation in your PR.

## Open a pull request

Commit and push your branch, then open a PR against `main`. Explain the problem,
the resulting behavior, and how you verified it. Include a regression test for
a behavior change when it can catch the original problem. For documentation,
describe the link and command checks instead.

The English README and translations live in `README.md` and `docs/README.*.md`.
Update affected instructions together. Keep tool names and code readable in the
right-to-left Persian and Arabic pages using their existing `dir="ltr"` markup.

Credit people who worked on the change. For a jointly authored commit, ask each
collaborator which GitHub-linked email or no-reply address they want used, then
add a `Co-authored-by: Name <email>` trailer after a blank line in the commit
message. See [GitHub's co-author instructions](https://docs.github.com/en/pull-requests/how-tos/commit-changes/creating-a-commit-with-multiple-authors).
Keep those trailers when squash-merging a jointly authored PR.

## Runtime scripts

Edit source templates in the repository, then redeploy and restart the MCP
connections. Runtime script snapshots are immutable: editing one makes
regeneration fail explicitly and excludes it from automatic project closure.
Copy a generated project outside the runtime first if you need an editable
debug project. Older intact generations and completed private sandboxes close
on EDA startup; their files remain on disk for diagnosis and rollback. See
[the native lifecycle checks](docs/review/SCRIPT_PROJECT_LIFECYCLE_2026-10-10.md).

## Bundled sources and licenses

This suite includes snapshots of three upstream projects. Their source
repositories and snapshot commits are recorded in [UPSTREAM.json](UPSTREAM.json).
Preserve their license notices; original suite code uses [MIT](LICENSE).
Bundled EDA code is Apache-2.0, legacy code is MIT with file-specific exceptions,
and Rust library code is GPL-3.0-or-later. Follow
[the license scope](THIRD_PARTY_NOTICES.md); keep Apache modification notices and
attribution, and retain GPL terms when changing or redistributing the Rust backend.
Do not relabel copied upstream code as MIT. Explain changes to bundled code so
maintainers can distinguish suite fixes from the original snapshot.
