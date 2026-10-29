# Native edit safeguards

Product mode is enabled by default. Minimal MCP advertisement remains three
meta-tools. Checkpoint operations are discovered with
`suite_catalog(backend="suite", with_schema=true)` as
`suite__create_checkpoint`, `suite__list_checkpoints`, and
`suite__restore_checkpoint`.

## Electrical values

Before native EDA/legacy dispatch, value/comment and component parameter value
fields normalize engineering unit characters. Examples: `10kΩ` → `10k`,
`100Ω` → `100R`, `4Ω7` → `4R7`, `10µF`/`10μF` → `10uF`, `±` → `+/-`,
`°C` → `degC`, and typographic dashes → `-`. Warnings appear in MCP response
`_meta.altium_suite.normalization_warnings`; input objects are not mutated.
Net names, pin identifiers, file paths, arbitrary scripts and opaque payloads
are never transliterated. Unknown characters above U+00FF in these value
fields fail before dispatch, with field location and Unicode codepoints.
The existing native transport still rejects lossy text in other fields.
The Latin-1 micro sign U+00B5 is representable, but is normalized for consistent
values; Greek mu U+03BC and the ohm signs are above the native boundary.

## Automatic disk snapshots

Native EDA/legacy writes conservatively trigger a checkpoint. Read-only queries
do not. Nested `eda__tool_invoke` calls use the same target schema, restrictions
and safeguards. The suite reads the current native context and visible project
members, combines explicit target paths, and backs up supported Altium files
to `<PLUGIN_DATA>/backups/<target-set-hash>/<UTC-time-id>/`. New absent target
files are recorded as absent; nothing is silently saved to make a backup.
Digests and manifests publish atomically. Failed, oversized or changing disk
snapshots stop the edit before dispatch. Tool results retain their original
errors/images/content and receive a checkpoint receipt in response metadata.

An opaque script, menu operation or external symbol/footprint specification
must use `suite_invoke` with `checkpoint_files: ["C:/absolute/target.SchLib"]`
beside the ordinary `arguments`. These paths are not sent to the backend.
The caller must enumerate all actual intended write targets. The guard cannot
infer arbitrary program effects; this is recovery assistance, not a filesystem
sandbox. Existing standalone worker connections bypass gateway safeguards.

Snapshots contain **saved disk bytes**, not unsaved Altium buffers. DelphiScript
still has no transactional rollback on a partial batch failure. Warnings make
that limit explicit. PCB checkpoints include a digest of live physical
pad-to-net assignments, which is not a copper DRC. Fresh schematic topology
is currently recorded as unavailable because recompilation may persist files;
no synthetic digest is presented as verified connectivity. A checkpoint never
forces a save or fresh compile. Explicit project members outside the currently
visible project list must be supplied in `checkpoint_files`.

The gateway serializes its own native preflight/edit/readback calls. Other
clients and the user's UI can still change focus or save files between bridge
calls. Existing cross-process engine locks protect native dispatch, not the
entire gateway checkpoint sequence. Inspect actual targets and receipts; this
does not provide isolation from concurrent human editing.

## Restore

Restore validates every stored file hash before replacing any target, refuses
links/junctions and path traversal, and acquires the existing Windows suite
mutex. **Altium must be closed**: an idle queue or missing ready marker is not
proof that restoring an open/dirty document is safe. It captures current disk
files in a separate recovery checkpoint first. Files created after the snapshot
are not deleted. Each replacement is atomic, but a multi-file restore is not
a filesystem transaction; its recovery checkpoint survives interruption.
Reopen the restored files afterwards. Backups may contain confidential design
data and remain local; telemetry consent does not govern their retention.

`--no-product` disables product adapters and gateway checkpoint enforcement for
explicit transport diagnostics/legacy compatibility. Normal plugin launching
never adds this flag. It is not intended for protected design editing.
