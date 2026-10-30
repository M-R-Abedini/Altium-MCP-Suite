# Step 1 verification — 0.4.0 candidate

## Baseline and scope

PR #14 was merged into `main` at
`d9729ff749c056492c42c7f94b8ec15816e9a4ee`. The annotated `v0.3.0` tag
identifies that Free-Tier Desktop baseline. Historical feature branches were
deleted only after verifying that their tips were ancestors of `main` and
creating a private recovery bundle. This step is developed on
`feat/step1-clean-license-and-backends`.

The current source and package closure remove the former library implementation,
Rust executable/build, crate vendoring and corresponding-source installer
pipeline. Historical Git commits and tags retain their original content and
licenses; they have not been rewritten. `UPSTREAM.json` and
`THIRD_PARTY_NOTICES.md` describe the remaining bundled MIT and Apache-2.0
sources. Runtime dependencies retain their own licenses.

## Interface and transport

The original Python library service implements all 34 observed `libraries__`
interfaces. Contract schemas came from public MCP discovery; format probes used
synthetic files and independent container specifications, rather than the
retired implementation source. Provenance and format limits are recorded in
`clean-room/LIBRARIES_SERVICE.md`, `SCHLIB_FORMAT.md` and `PCBLIB_FORMAT.md`.

All 37 `legacy__` interfaces remain discoverable with their released schemas.
Existing EDA aliases and private compatibility handlers use one EDA worker and
native dispatcher. Arbitrary script snippets compile into that same managed
project. Complete legacy read/modify/write sequences share a reentrant admission
guard; cancellation drains dispatched work before another operation proceeds.

## Completed checks

The complete suites were run without additional exclusion or skip flags:

- Root: `python -m pytest tests -q --tb=short` — **611 passed**.
- From `eda-agent`: `python -m pytest tests -q --tb=short` — **4675 passed,
  115 skipped** by existing environment, optional-backend and fixture conditions.
- `python scripts/build_plugin.py --smoke --check-reproducible` — passed.
- An actual EDA wheel was built and checked with
  `python scripts/build_plugin.py --check-wheel <wheel-directory>` — all **16**
  native project members matched their source bytes.
- Full gateway smoke against the staged ZIP contents — passed synthetic SchLib
  creation/read and PcbLib creation/edit/read without either retired executable.
  All **544** capability schemas across eight namespaces were valid; the 34
  library and 37 legacy contracts matched the captured interface contracts.
  Full discovery advertises these capabilities plus three gateway meta-tools.

| Namespace | Capabilities |
| --- | ---: |
| eda | 457 |
| legacy | 37 |
| libraries | 34 |
| cloud | 4 |
| suite | 3 |
| supply | 4 |
| route | 4 |
| telemetry | 1 |

Actual Altium acceptance used synthetic scratch documents: generated SchLib
metadata and pin/rectangle geometry; generated PcbLib loading and pad geometry;
libraries containing tracks, text, bodies and long-name aliases; legacy layer
and symbol reads; and a snippet returning a result and step log through the
shared EDA dispatcher. Ordinary tests are isolated from the running editor.

## Coverage limits

Catalog/schema verification of 544 capabilities is not a claim that every
capability was executed against a live design. Synthetic codec and regression
coverage is distinct from native acceptance, and does not certify every Altium
version or undocumented file property.

On the tested Altium version, a component-name getter after disk reload returns
an ANSI fallback for some Unicode names. An exact-byte copy of an Altium-created
and saved control exhibits the same behavior. Unicode carriers remain in the
file; Unicode descriptions loaded correctly. See `clean-room/PCBLIB_FORMAT.md`.
Native editing also retains the documented non-transactional rollback and
conservative request-character limitations.
