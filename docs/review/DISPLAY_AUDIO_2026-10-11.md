# Display/audio workflow reliability

Validated with Altium Designer Enterprise 26.10.1 on Windows, 2026-10-11.
Source: the user's board-workflow findings, checked against the suite code and
isolated native test projects. The hardware project was not a mutation target.

## Confirmed defects and changes

| Finding | Change and acceptance evidence |
|---|---|
| Different EDA/legacy workspaces | Both wrappers bind to one runtime-owned `workspace-identity.json`. An omitted override adopts the recorded workspace; a conflicting override fails before launch. Setup emits identical runtime/workspace environment values. Concurrent startup regression verifies one winner without manifest overwrite. |
| Same version, different native sources | Preflight compares the ready marker's content-addressed project path with the configured generation. A mismatch requires confirmed shutdown and a verified restart before dispatch. `app_context.coordination` exposes both paths and the native session. Native checks returned `script_hash_match=true`. Missing identity on an older standalone script remains explicitly unknown. |
| Footprint reported applied without changing the model | Placement and parameter updates write the selected `ISch_Implementation.ModelName`, `IsCurrent`, and PCBLIB datafile `EntityName`. Other model types and link locations are preserved. Ambiguous model selection is refused. Readback after save/close/reopen verified the model and entity names on both placed test symbols. This does not verify library availability or pad compatibility. |
| Negative multipart batch counts | `updated`, `failed`, and `total` count requested stamps; `updated + failed = total`. Separate instance counters report all matching symbols. One stamp matching two symbols plus one missing designator returned `updated=1, failed=1, total=2, updated_instances=2`. Every requested field is read back. Invalid batches and missing explicit sheets fail before editing. |
| Unsafe component replication | `sch_replicate_component` returns `REPLICATION_UNSUPPORTED` before mutation. Native before/after queries verified unchanged source properties and object count. Independent library placement is the available workaround; package UID/ECO association still needs explicit verification. Transactional replication is not implemented. |
| Sheet entry coordinates | Native sheet-symbol Location is normalized to top-left `(min X, max Y)`. Entry success requires matching Side, DistanceFromTop, and absolute Location after registration. Four sides of a 2000-by-2000 mil symbol passed native coordinate checks. Invalid sides and out-of-edge distances are rejected. |
| Compile-mask API gap | Added `sch_place_compile_mask`, `eCompileMask` queries, and typed Collapsed/corner updates. Native placement and toggling read back correctly. On this AD26 workflow `Collapsed=true` disables the exclusion. The new acceptance test checks state and geometry; it does not independently prove ERC/hierarchy exclusion. |
| Custom-sheet size silently ineffective | Set effective `SheetSizeX/Y` as well as custom dimensions; return actual dimensions and verify them. The old setter reproduced 20000-by-15000 instead of requested 32000-by-20000 mils; the fixed setter returned the requested effective size. |
| Missing annotations and inactive multipart pins in SVG | Native geometry includes labels/text frames/notes, and filters component children by CurrentPartID while retaining common part-zero primitives. Native SVG contained escaped frame/note text. LM358 part A rendered pins 1/2/3/4/8; part B rendered 4/5/6/7/8. XML and bounding-box regressions cover annotations and top-left sheet-symbol anchors. Font/layout fidelity remains approximate. |
| Project-member save and unreliable dirty flags | The initial dirty-only change passed on one fixture but was superseded: AD26 can display an unsaved schematic while `IServerDocument.Modified=false`. Native project-scoped Standard save now persists target members and the definition without forcing Modified=true. It can rewrite clean members; unchanged component properties were verified after two saves. The reported Wireless mapping error was not reproduced, so its root cause is not claimed fixed. |
| Newly created project not focused | `proj_add_sheet` accepts an explicit loaded `project_path`; missing targets cannot fall back to another project. Existing files/open documents are refused before creation. Native creation in the explicitly selected empty project succeeded while another project was focused; repeating the name failed without changing file bytes. |

## Native lifecycle follow-up

Version `2026.10.11.lifecycle1` addresses the three open lifecycle findings and
two save defects exposed by their native regression fixtures:

- **Design-project close (P1):** the suite wrapper resolves the target, confirms
  polling shutdown, then invokes `Dispatcher>CloseMCPProject` once. Unsupported transport text is rejected before stopping or publishing. The native
  procedure verifies the focused document's project owner, closes that project,
  and confirms removal before publishing the request-specific final marker.
  Clean close and save/close/reopen of edited scratch schematics succeeded in
  AD26. The original polling-loop path still refuses design-project close.
  This avoids the observed execution context; Altium's internal hang mechanism
  is not established. Empty projects or ambiguous/shared document ownership
  remain explicit failures rather than falling back to another project.
- **Deleted multipart ghosts (P1):** generic component deletion unregisters the
  native object instead of only removing its container membership. The iterator
  is destroyed first; the freed object is never reused. Transaction finalization
  and compile-cache invalidation run even on failure. The old implementation
  left 16 compiled pins and two ERC findings after removing one of two symbols.
  The fix returned eight pins and one finding without reopen, with no reference
  to the deleted designator. Save/close/reopen preserved those counts. Altium
  documents that [unregistering frees the object](https://www.altium.com/documentation/altium-dxp-developer/isch-document-interface).
  Native Undo/Redo was not part of this acceptance test.
- **Premature engine handover (P2):** completion now also requires a bounded
  `SendMessageTimeoutW(WM_NULL)` round trip to the enabled editor window, with
  process/window identity checked before and after. Ownership remains intact
  on timeout or identity change. Three native tests published completion before
  a 1.2-second non-pumping tail; the barrier waited 1.187/1.203/1.203 seconds and
  only retired ownership after return. This relies on this suite's completion
  tails issuing no further message pumps or native commands, enforced by a
  regression. It is not a general DelphiScript VM-status API. Microsoft's
  [API contract](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendmessagetimeoutw)
  defines the bounded message round trip; it does not establish Altium behavior.
- **Forced compile saves unrelated projects (P1):** both explicit forced compile
  and query-triggered forced compile now save only the resolved target project.
  Failure to dispatch the definition save aborts compilation. Baseline testing
  of the old workspace-wide save wrote one generated document UID into the
  hardware project definition. The exact original bytes were restored; final
  hardware hash checks include that definition.
- **Unsaved edits missed by the SDK flag (P1):** native Standard save persists
  target members before the project definition. A text-frame edit showing an
  unsaved UI marker, despite a false SDK Modified flag, survived close/reopen.
  This correctness requirement supersedes the earlier dirty-only optimization.

## Remaining limits

- `save=false` can open Altium's Save/Discard/Cancel dialog when its SDK dirty
  flags miss an unsaved edit. The wrapper reports the blocker and retains
  ownership; it neither discards edits nor repeats the close. Cancellation was
  tested, followed by native completion and successful guarded handback.
  `app_context.unsaved` and dirty counts inherit this SDK limitation and are not
  proof that every editor buffer is saved.
- Native Standard save can rewrite clean target members. Save operations are
  project-scoped, but byte/timestamp stability is not promised. Synthetic free
  documents retain the existing per-document save path.
- Native PNG export limitations and screenshot resolution are unchanged. The
  original Wireless file-mapping error remains unconfirmed. Compile-mask state
  readback is tested; full hierarchy/BOM/PCB exclusion is not certified here.

## Disabled-window reporting

A disabled main window with no detected dialog is now reported as
`blocked_without_visible_dialog=true` and `needs_a_human=true`. Enumeration
cannot rule out a hidden modal or a running handler. The dialog driver returns
failure in that state, including after pressing a preceding dialog's button;
an empty list is not proof of completion. It sends no additional input and
does not authorize a second script launch. A visible modal is still classified
by its actual message and buttons.

`app_detach` previously swallowed stop failures and reported successful
detachment. The suite wrapper now uses the coordinator's guarded stop, waits
for its session-bound final shutdown marker, and propagates a failure. The tool
only detaches its local keep-alive after that confirmation. It saves no
documents, and a later coordinated call can restart the bridge. The final
marker and the bounded editor-thread round trip are both required before
another script can launch. Completion alone no longer retires ownership.

## Verification

- Lifecycle follow-up: 205 suite tests and 175 targeted EDA tests passed; native
  build/lint reported zero errors and warnings. Final deployment initialized
  both installed wrappers (448 EDA / 37 legacy tools). Installed AD26 acceptance
  passed eight consecutive read-only legacy/EDA handovers with matching native
  source identity, refused a missing close target, and preserved source component
  properties through repeated project saves. Separate native fixtures verified
  clean close, persisted edited text through save/close/reopen, multipart pin/ERC
  counts before and after reopen, and the three delayed-return barriers above.
  Audio was restored as the active hardware sheet, final Detach confirmed engine
  release, and all 14 hardware file hashes matched the original baseline.
- Close-guard follow-up: 192 suite tests and 152 targeted EDA tests passed;
  native build/lint reported zero errors and warnings. Installed initialization
  passed for both wrappers (448 EDA / 37 legacy tools). Native AD26 acceptance
  verified refusal with both `save=false` and `save=true`, unchanged active
  document, project membership, scratch file bytes and timestamps, successful
  Detach, and automatic restart into a new matching native session. A final
  Detach passed; all 14 hardware file hashes remained unchanged. Tests for a
  disabled window with no detectable dialog and failed shutdown are injected
  regressions; no hidden modal or hung handler was deliberately recreated.
- 190 suite Python tests and 111 targeted EDA tests passed locally. Newly added
  checks run in GitHub CI; native behavior cannot be reproduced by a Python fake.
- DelphiScript build/lint passed without suppressing rules.
- Installed-directory initialization passed for both wrappers (448 EDA tools,
  37 legacy tools). A fresh installed EDA client verified source identity,
  multipart stamp counts, annotation SVG output, and the initial dirty-only save
  behavior, superseded by the lifecycle follow-up above.
- Native tests used disposable projects/libraries. All 14 hardware `.PrjPcb`,
  `.SchDoc`, and `.PcbDoc` hashes were unchanged after recovery and acceptance.
  An empty sheet initially added to the focused hardware project during testing
  was removed from membership and moved into the scratch project. Pre-existing
  unsaved project metadata was not saved, discarded, or declared clean.

Restart existing MCP connections after deploying the Python changes. To change
an intentionally bound workspace, stop both servers/native scripts first, then
archive the runtime's identity file and configure the new path identically for
both wrappers. Never remove that identity as an automatic mismatch recovery.
