# Batch execution performance fixes, 26 October 2026

This change reduces repeated bridge dispatch and sheet iteration in prepared
schematic execution. It does not eliminate Altium's RunScript startup or change
the two-second idle release required for normal editor shortcuts.

## Using the batch path

Resolve libraries and validate the complete DesignPlan first. Submit it once
with `design_execute_plan(plan_json=..., project_path=..., use_canvas=True,
result_detail="summary")`. Inspect `ok`, `failures`, `net_verification`, and
`net_mismatches` before accepting the result. The returned `placed` records
remain available in summary mode. `details_path` points to a unique local JSON
artifact containing the complete plan and result, including the canvas.
`canvas_snapshot_path` remains available for layout learning.

`result_detail="full"` is the compatibility default. No existing field is
silently removed from that mode. If publishing a summary artifact fails,
execution retains the full response and explains the fallback. Summary mode
is explicitly refused with the legacy executor.

`timings_ms` separates symbol extraction/layout, emission, native verification,
and total execution. `native_calls` records emitter command names, elapsed
milliseconds, and success flags, without duplicating design payloads. These
are local measurements; they do not include model thinking time or separately
attribute queue, native handler, and process-start overhead.

## Changes

- Bus segments and entries use one native batch per kind and sheet, with one
  native transaction and redraw per batch. Each reply reports indexed results,
  placed, failed, and total counts. Invalid decimal coordinates, including
  signed 32-bit overflow, are rejected before creating any object. Native
  failures are reported as partial/unknown when appropriate; there is no
  promise of atomic rollback. Unsupported old handlers fail explicitly.
- Manufacturer, MPN, and datasheet visibility are applied and read back during
  the existing component metadata pass. Older handlers without the capability
  acknowledgement retain the three-pass compatibility fallback.
- Symbol cache identity includes normalized full library path and SHA256
  content, preventing same-name library collisions and stale entries when
  timestamps are preserved. Extraction batches hash each library at their
  boundaries instead of once per symbol. A changed library invalidates that
  batch rather than mixing revisions. Unsaved native library edits still
  require explicit cache invalidation; disk hashes cannot detect them.
- Legacy `build_schematic` groups complete PART and metadata records by library
  using a stable sort. Wires and other primitives remain after placements.
  The existing 1200 ms native library-open wait is retained.
- New sheets are created without implicitly attaching them to the focused
  project, then explicitly attached to the requested target. Attachment or
  activation failure is fatal. Persistence saves only the explicit target
  project, with libraries excluded; it does not call `application.save_all`.
- Successful canvas emission now requires native compiled topology verification
  using a fresh compile of the target project. Missing, malformed, truncated,
  or conflicting native data cannot pass. Unplanned NC pins are allowed;
  unassigned planned pins are incomplete. Shared topology checks detect opens,
  shorts, and missing planned pins regardless of native net renaming.

## Evidence and limits

The real emitter with a fake bridge sends 12 rather than 15 commands for
10/100/500 components with wires, labels, metadata and power objects. Adding
8 buses and 32 entries to the 500-part case sends 14 rather than 55 commands.
Post-emission topology verification adds one command outside the emitter.
These are dispatch counts, not measured native or model speedups.

For 100 alternating parts across four libraries, stable grouping changes
library switches from 100 to four. The corresponding static sum of requested
1200 ms sleeps changes from 120 seconds to 4.8 seconds. This is not a native
wall-clock benchmark.

A native scratch-only two-resistor plan passed component placement, metadata
stamping, target save and compiled two-net topology verification. Native bulk
happy-path acceptance placed eight buses and 32 entries and queried their
counts and locations successfully. The singular baseline was interrupted by
an EFOpenError at the existing guarded `SL.LoadFromFile`; no timing ratio is
claimed. Altium's IDE paused before the existing catch, consistent with
first-chance exception handling. Global debugger preferences were not changed.
The deliberately malformed batch initially exposed a throwing StrToInt
preflight. The final implementation uses nonthrowing lexical/range validation;
native BAD, empty, fractional and positive/negative int32-overflow requests
all returned INVALID_COORDINATES without mutation or modal. Native counts
remained eight buses after these five refusals. The two bulk commands took
1227.144 ms and 1223.669 ms in that run, including bridge/preflight overhead.
There is no completed equivalent singular baseline for a speed ratio.

Detailed local commands, payloads, test logs and native outcomes are retained
separately from the distributable package. The reproducible offline cases are
in `eda-agent/tests/design/test_performance_batches.py` and the related design
tests. All native design mutations in this validation targeted newly created
scratch fixtures; production designs were not modified.

The measured cold-start architecture limit remains: roughly 4–5 seconds after
idle versus roughly 0.1–0.2 seconds for warm reads in the prior baseline.
Batching amortizes this cost; a persistent native extension or equivalent
architecture needs a separate implementation and benchmark.
