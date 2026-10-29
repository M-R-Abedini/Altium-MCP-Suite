# Optional structural datasets

Collection is **off by default**, local only, and independent of free-tier
functionality or account registration. `suite_telemetry.py` has no upload
implementation. The MCP status tool cannot enable collection; a user must
explicitly set this private `<PLUGIN_DATA>/config.json` option:

```json
{"telemetry_opt_in": true, "edition": "free"}
```

Only JSON boolean `true` enables collection. Enterprise edition forces it off
even if the flag is true. Setting the flag false immediately stops future
collection; it does not delete previously collected files. Removing the private
`telemetry` directory deletes those existing datasets and their local salt.
Fresh installs and disabled collectors create no telemetry directory or salt.
Enterprise mode provides no collection, not a claim to remove unrelated native
logs/backups or an earlier user's retained records.

## What can be retained

Raw tool responses never reach dataset files. A fixed allowlist projects:

* Component/pin/net identities as domain-separated salted SHA-256 identifiers;
  footprint and pin-function names are also hashed.
* Public-looking MPN/value tokens (bounded ASCII token syntax), numeric
  coordinates, rotations, pad/track/via geometry, board boundary and numeric
  width/clearance/drill constraints.
* Explicit units (`mils`, `mm`, `um`) and narrowly scoped provenance flags.

Project paths, filenames, authors, company/title/free-text/notes, arbitrary
metadata, query expressions and raw DSN/SES strings are excluded. A private
32-byte salt is generated locally only after consent. Dataset files use
private file modes where supported; Windows files inherit the user's data
directory ACL. JSONL records are immutable separate shards so concurrent
clients do not interleave large appends.

This is **pseudonymization**, not proof that a design cannot be identified.
Topology, dimensions and distinctive public MPN combinations may reveal a
design; user consent remains necessary. MPN syntax validation cannot prove a
token is public. No external release, training/upload consent or global
cross-user identifier is implied by this local flag.

## Three local streams and provenance gates

1. `capture_ai(project_id, raw)` retains the most recent projected AI snapshot
   in `telemetry/pending`. `record_human` requires explicit
   `human_confirmed=True` and a matching fresh verification before writing a
   `trajectories/*.jsonl` structural delta. Moves, pin-net changes, added/removed
   components, property changes and routing changes are recorded. The label is
   **human correction candidate**; preference is `null`. A save, a validation
   result or a changed graph alone never proves that a human prefers the result
   or supplies a DPO reward. Later human labeling is separate work.
2. `record_subcircuits` extracts IC-centered one-hop hypergraphs with connected
   passive/other component records and their pin-net edges only after an ERC
   gate passes. It does not infer an IC's role from a designator.
3. `record_routing` stores a projected boundary/pads/constraints puzzle and
   routed tracks/vias only after a DRC gate passes. Puzzle and solution must
   have identical units, component/pad/net inventory, boundary and constraints.
   Raw DSN/SES files are not telemetry records.

Verification evidence must originate in the trusted host bridge adapter and
contain `kind: "erc"` or `"drc"`, `source: "bridge"`, `fresh: true`,
`complete: true`, integer `errors: 0`, an aware `observed_at_utc` within five
minutes, and `snapshot_sha256` equal to
`graph_digest(collector.anonymize(raw))`. These are an adapter contract; a
model-supplied assertion with those keys is not independently verified evidence.
The native producer must bind the actual check and readback revision under the
suite gate. Partial/stale reads and stored violation counts are insufficient.

## Gateway integration and current scope

`TelemetryCollector.observe(name, arguments, result, reader, mutating=True)` is
an async post-edit hook. A disabled collector performs no native reads. After a
successful structured PCB edit, the hook uses existing read-only `app_context`
and explicit `pcb_get_pad_connectivity(board_path=..., limit=0)` calls. It
requires an answering matching bridge, the exact active physical PCB, complete
nontruncated pad counts and explicit native units before capturing an AI pad
assignment graph. It hashes identifiers and drops all other response fields.
This readback has integer-mil resolution; it does not prove copper connectivity
or a DRC result and contains no guessed MPNs/footprint dimensions.

Automatic native schematic ERC/subcircuit collection and full DSN/SES puzzle
extraction are not enabled merely by this hook. Existing schematic compilation
can have persistence effects, and existing stored DRC reads do not certify a
fresh check. The explicit, tested dataset APIs are ready for a host that can
provide the required complete structural snapshots and evidence. No extra
compile/save/modal DRC operation runs silently to create training data.

The read-only `telemetry__status` tool reports opt-in and scope without exposing
the salt or user paths. The collector API accepts an injected data directory for
tests and multi-client hosting. Tests cover default/Enterprise disablement,
revocation, secret removal, salting, finite geometry, deltas, stale/partial
verification rejection, hypergraph neighbors, routing invariants and actual
gateway read-tool selection.
