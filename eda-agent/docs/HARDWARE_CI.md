# Hardware CI: offline design review (opt-in fallback)

`eda-agent review --offline` reads an Altium `.SchDoc` **directly**, no
running Altium, no license, and reports component-level issues (missing MPN
/ datasheet, placeholder values, designator collisions). It exists for one
job: a CI runner or a bare file on disk where Altium can't be opened.

> **This is a fallback, not the preferred review path, and it is disabled by
> default.** It only covers the netlist-free, component-level subset: it
> cannot compile a netlist or run ERC, and an offline parser reading
> undocumented binary framing can misread a file. Whenever an Altium session
> is available, use the live tools instead (`design_lint_report`,
> `proj_run_erc`, `design_review_snapshot`, the `audit_*` family); they run
> Altium's own engines and see connectivity this reader cannot. Reach for
> the offline review only when Altium genuinely isn't available.

## Enabling it

The review is off unless you opt in, per invocation:

- **CLI:** pass `--offline`.
- **MCP tool (`design_review_file`):** set `EDA_AGENT_HEADLESS_REVIEW=1` in
  the server environment.

Without the opt-in the CLI exits `2` and the tool returns an `error`,
pointing you at the live tools above.

## Local use

```bash
eda-agent review --offline path/to/board.SchDoc          # human-readable, exit 1 on errors
eda-agent review --offline path/to/board.SchDoc --json   # full report as JSON
eda-agent review --offline path/to/board.SchDoc --sarif  # SARIF 2.1.0 for code scanning
```

Exit codes: `0` clean, `1` a finding at/above the `--fail-on` threshold
(default `error`), `2` disabled (no `--offline`) or the file could not be
read. Use `--fail-on warning` to also gate on warnings, or
`--fail-on never` to annotate without failing the build. Pass a `.PrjPcb` to
review every sheet.

## GitHub Actions

The review emits SARIF, so GitHub can render findings as inline annotations
on the pull request via `github/codeql-action/upload-sarif`. Add this
workflow (adjust the glob to your schematic path):

```yaml
name: design-review
on:
  pull_request:
    paths: ["**/*.SchDoc"]

permissions:
  contents: read
  security-events: write   # required for upload-sarif

jobs:
  review:
    runs-on: ubuntu-latest   # no Altium needed: the reader is pure Python
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install eda-agent
      - name: Review schematics
        run: |
          rc=0
          for f in $(git ls-files '*.SchDoc'); do
            eda-agent review --offline "$f" --sarif > "review-$(basename "$f").sarif" || rc=$?
          done
          # Merge is optional; upload each file below. Keep rc for gating.
          exit 0            # let the SARIF upload run; gate on findings instead
      - uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: .
```

To **fail the build** on error-severity findings instead of only annotating,
drop the `exit 0` and let the per-file exit code propagate, or add a final
gate step that greps the SARIF for `"level": "error"`.

## What it checks today

Netlist-free, component-level checks (no Altium, no compile):

- `designator_collision`, `missing_designator`, `unannotated_designator`,
  `duplicate_unique_id`: errors
- `missing_mpn`, `missing_datasheet`, `placeholder_value`, `malformed_value`
  (a passive R/C/L whose Value has no numeric magnitude): warnings
- `missing_manufacturer`, `title_block_incomplete`: info

## Connectivity (`eda-agent netlist`)

Connectivity checks are now available offline too: a geometric net solver
reconstructs the compiled netlist from the schematic geometry (pins, wires,
power ports, junctions, by-name net labels) with no Altium, then runs ERC:

```bash
eda-agent netlist --offline board.SchDoc              # human-readable
eda-agent netlist --offline board.SchDoc --sarif      # PR annotations
eda-agent netlist --offline board.SchDoc --fail-on error   # CI gate
```

- `single_pin_net` (warning): a pin connects to nothing (verify it is an
  intentional no-connect / test point)
- `net_short` (error): one physical net carries two different declared
  names (rails shorted together)

Validated envelope: wire + power port + junction + net-label connectivity
(against a live-Altium netlist, 24/24, and the design plan, 7/7). Cross-sheet
connectors are out of scope. It faithfully reports a net the schematic left
floating; for critical sign-off, still confirm against Altium's own
compiler (`proj_get_nets` / `proj_run_erc`).

## BOM (`eda-agent bom`)

```bash
eda-agent bom --offline board.SchDoc --csv            # purchasable BOM as CSV
eda-agent bom --offline project.PrjPcb --json         # aggregates all sheets
```

One line per distinct `(mpn, value, lib_reference)`, designators grouped and
naturally sorted, quantity summed: a build artifact for every commit.

All three commands are opt-in (`--offline` or `EDA_AGENT_HEADLESS_REVIEW=1`)
and off by default; when an Altium session is available, prefer its own
engines (`design_lint_report`, `proj_run_erc`, `proj_get_bom`).
