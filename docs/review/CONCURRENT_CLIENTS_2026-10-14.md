# Concurrent MCP clients

Codex and Antigravity can submit work concurrently. A shared local FIFO service
orders native admissions; each admitted client still acquires the Windows
engine mutex and validates native ownership before dispatch. Python analysis of
an explicitly captured PCB snapshot runs independently of that queue.

## Usage

Use the coordinated `eda_stdio.py` and legacy `codex_stdio.py` entry points with
identical `ALTIUM_MCP_RUNTIME` and `EDA_AGENT_WORKSPACE` in both clients. Full and
minimal toolsets have the same behavior. In minimal mode discover these tools
with `tool_catalog(query=..., with_schema=True)`, then use `tool_invoke`.

1. `pcb_capture_snapshot(board_path=<absolute loaded PcbDoc>)` reads physical
   component inventory, net names, pad assignments and counts in one native
   iterator pass. It returns a snapshot ID, counts and capture time.
2. Pass that ID to `design_read_snapshot`, selecting any of `counts`,
   `components`, `pads`, `nets` together. Default: counts only.
3. Different clients can simultaneously call `design_analyze_snapshot` for
   `inventory`, `pad_assignments`, `bom` (all by default), without accessing
   Altium. Analysis is offloaded from each MCP event loop. Python threads do
   not promise CPU acceleration; independent client processes can run in parallel.
4. `app_queue_status` reports admission depth without launching a service or
   native script. `active` means a granted connection, not proof of execution.

Captures always read native state anew. Reuse is explicit: an old snapshot is
historical evidence, even immediately after capture. No cache age or Modified
flag establishes freshness. GUI edits may occur without a reliable dirty flag.
Snapshots cannot authorize edits or prove copper connectivity, successful ECO,
schematic equivalence or native variant fitted state. The BOM is grouped physical
inventory, not a manufacturing BOM with validated variants.

## Service and failure behavior

- Starts hidden on the first foreground native call; listens only on numeric
  IPv4 loopback with a random 256-bit token in the private runtime. It accepts
  bounded JSON admission/status messages, never commands, pickle or executable
  code. Idle service exits after 90 seconds. Startup is serialized and a pending
  live startup cannot be replaced by a second process.
- At most 128 waiting admissions; waiting clients have a 180-second timeout.
  Foreground requests are FIFO by admission arrival, not tool invocation time.
  Background health calls yield when work is active or queued.
- Queued EDA cancellation closes its ticket before dispatch. Once dispatched,
  cancellation drains the native worker before releasing both locks. Disconnects
  remove admissions; existing native mutex and durable ownership checks still
  apply. Broker death does not prove native completion and never resets markers
  or replays a design command. A live unreachable service is not replaced.
- Snapshots are content-addressed, checksum verified and limited to 64 MiB.
  They are stored only under the runtime's `read-snapshots` directory. There is
  no automatic deletion: remove unneeded snapshot files when clients no longer
  reference them. Standalone upstream entry points explicitly refuse capture.

## Validation

AD 26.10.1, Windows, native version `2026.10.14.queue2`. Three native capture/
two-client-analysis/readback cycles passed: 272 components, 197 nets, 1,041 pads,
1,993 tracks and zero vias. All 69 project/design/library file hashes were
unchanged; no compile, save, ECO or design edit was performed. Final Detach was
confirmed. Independent stdio exercised the configured Antigravity environment;
this does not constitute a Flash-model UI acceptance test.

The first loop found a genuine post-response delay: Dispatcher parsed the
`error` field at the end of successful 144-KiB replies, walking their entire
data container after publishing the response. The next health probes timed out.
Error details are now parsed only when the preceding envelope `success` is
false. Three consecutive native cycles then completed without that failure.

The final loop also verified simultaneous native reads from two clients. Native
snapshot capture median was 7,301 ms over three samples (range 7,147–9,045 ms);
one separate component+pad read pair took 7,928 ms. Two clients' offline analyses
completed in 37–40 ms. This small, variable sample establishes functionality,
not a universal speedup. MCP capture timings include discovery/transport.

Gate-only benchmark, 100 samples per path: the mutex alone had a 0.23-ms median;
FIFO + mutex had a 14.67-ms median / 25.11-ms p95 in the synchronous path.
Async admission (used by both Python MCP tool paths) had a 2.33-ms median /
4.22-ms p95. The measured service start was 492 ms. Cold startup, native handler
time and waiting behind other clients are additional costs. Original logs remain
private under `.runtime/queue-*`. These are local measurements, not guarantees.

Regression validation: 261 root tests, 78 selected EDA discovery/physical-read
tests and all three offline stdio initializations passed. Native lint scanned
11 files with zero errors or warnings. The service's real-process tests cover
FIFO, competing startup, timeout, disconnect, authentication, cancellation and
retaining the native mutex when the service dies.

REL-02 (save persistence), REL-03 (partial native outcomes), REL-04 (Unicode)
and the other unverified native paths remain in [the backlog](RELIABILITY_BACKLOG.md).
