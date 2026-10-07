# Connectivity verification tools

Four read-only MCP tools add explicit electrical contracts and before/after
checks to the existing Altium backend. They are an independent implementation
inspired by KAT's workflow, not a KiCad dependency or copied checker.

| Tool | Purpose |
| --- | --- |
| `design_connectivity_snapshot` | Canonical pin-to-net data and SHA-256 digest |
| `design_check_pin_contracts` | Exact expected net or intentional NC per pin |
| `design_diff_connectivity` | Added/removed pins and changed net assignments |
| `design_check_schematic_pcb_parity` | Bidirectional symbol/PCB pad comparison |

## Capture and check

Use `proj_get_nets` for the intended project, without component/net filters.
Force a fresh compile using the existing project tools and select a sufficiently
large limit. If the returned count reaches that limit, obtain a larger capture
before declaring it complete. These tools do not themselves compile, access
Altium, save files, or verify freshness. `complete=true` is the caller's explicit
assertion of capture coverage; the checker cannot discover omitted rows.

Pass the result to `design_connectivity_snapshot`:

```json
{
  "data": {
    "pins": [
      {"component": "J1", "pin": "1", "net": "/Camera0/RESET"},
      {"component": "J2", "pin": "1", "net": "/Camera1/RESET"},
      {"component": "U1", "pin": "99", "net": ""}
    ],
    "count": 3
  },
  "complete": true
}
```

The pin numbers in this example are synthetic, not an RV1106 pinout. Store the
returned snapshot alongside project revision/file hashes, capture date and
compile evidence. The digest describes electrical assignments only: it does not
include component values, footprints, geometry or source file hashes.

Pass that snapshot as `actual` to `design_check_pin_contracts` with:

```json
{
  "contracts": [
    {"component": "J1", "pin": "1", "net": "/Camera0/RESET"},
    {"component": "J2", "pin": "1", "net": "/Camera1/RESET"},
    {"component": "U1", "pin": "99", "nc": true, "reason": "Synthetic NC example"}
  ],
  "require_all_pins": true
}
```

Real contracts must be transcribed from manufacturer/reference documents and
kept with their citations. With `require_all_pins=false`, a pass covers only the
specified contracts, not unspecified pins. NC requires a reason and cannot hide
a missing pin. Altium's empty/`?` net is treated as unconnected; literal net name
`NC` is an electrical net, not a no-connect flag.

Identical repeated physical component/pin assignments (multi-unit or stacked
pins) deduplicate; conflicting repetitions fail. Full hierarchical net names and
physical component designators remain intact. Source payloads with failures,
missing net fields, empty pin sets, truncation, or inconsistent counts fail.

## Compare changes and PCB pads

Give two snapshots as `before`/`after` to `design_diff_connectivity`. Output
includes only changes and an unchanged count to reduce token usage. A net rename
is an exact assignment change; it is not automatically classified as a short or
approved ECO. `ok=true` means comparison succeeded, not that changes are safe.

For PCB parity, pass the schematic snapshot as `schematic`. Collect
`pcb_get_component_pads` for every component in the same scope, and pass:

```json
{
  "board": {
    "complete": true,
    "components": [
      {"designator": "J1", "pads": [{"name": "1", "net": "/Camera0/RESET"}], "pad_count": 1}
    ]
  },
  "waivers": []
}
```

One component result may also be supplied directly as `board` with `complete`.
Multiple pads sharing a number are allowed only when their nets agree.
Unnumbered mechanical pads use `pin=""` for an explicit waiver. Extra thermal
pads, schematic-only pins and mechanical pads must be accounted for explicitly:

```json
{"component":"U3","pin":"EP","code":"PAD_WITHOUT_SYMBOL",
 "reason":"Manufacturer specifies exposed pad on GND","net":"GND"}
```

Only `PAD_WITHOUT_SYMBOL` and `SYMBOL_WITHOUT_PAD` can be waived. The waiver must
match the expected net (default empty); unused/duplicate waivers fail so stale
exceptions do not silently approve a changed design. Shared-pin net mismatches
cannot be waived. This checks pad assignments, not actual copper connectivity,
land-pattern dimensions, placement or DRC. Use the existing footprint audit and
native Altium ERC/DRC for those separate checks.

Check `status`: `passed`, `failed`, `incomplete`, or `invalid_input`. A matching
but incomplete capture never passes. All four tools are pure Python and work in
the existing full and minimal Altium MCP toolsets without new dependencies.

Validation: `eda-agent/tests/design/test_connectivity_contracts.py` exercises
hierarchy collisions, NC, omissions, duplicate pins, diffs, split pads, extra
thermal pads, incomplete data and structured tool errors. Tool registration,
metadata and catalog tests cover discovery.

Verified on 2026-10-07: 94 related regression/catalog tests and 32 additional
metadata/simulator/documentation tests passed. Fresh stdio initialization listed
445 EDA tools, 37 legacy tools and 34 library tools. The Codex-installed EDA
server also exposed all four new tools; real stdio calls with synthetic inputs
verified hierarchy mismatch detection, identical-snapshot diff, pad parity and
invalid-input rejection without calling the Altium bridge. This is transport
and verifier validation, not a live audit of the user's board.

Pre-publication re-review on the same date: 134 related tests passed, including
the existing post-emission topology verifier. `tests/smoke_stdio.py` now makes
the four pure verification calls reproducible over real stdio, and GitHub CI
runs the connectivity and MCP discovery tests on future pushes.

The local source and installed Python package were updated together with a
backup under `AltiumMCP/repair-backups/20261007-connectivity-*` containing prior
files and a hash manifest. An already-running MCP process retains its old
registry: reconnect/restart Codex once to discover the new tools. Client
configuration and Pascal scripts were not changed.
