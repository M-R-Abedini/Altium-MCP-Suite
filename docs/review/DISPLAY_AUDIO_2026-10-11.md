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
| Clean documents rewritten on save | Project-member save no longer forces Modified=true and only writes dirty members. A second native project save left every scratch SchDoc timestamp unchanged. Changed footprint fields persisted across reopen. The reported Wireless mapping error was not reproduced, so its root cause is not claimed fixed. |
| Newly created project not focused | `proj_add_sheet` accepts an explicit loaded `project_path`; missing targets cannot fall back to another project. Existing files/open documents are refused before creation. Native creation in the explicitly selected empty project succeeded while another project was focused; repeating the name failed without changing file bytes. |

## Still under investigation

- **Project close can block the native UI (P1):** final installed acceptance
  passed through model updates, render, and saves, then `proj_close` on the
  scratch Findings project stopped returning. The main window and monitor were
  disabled; dialog diagnostics found no visible dialog, and the client timed out
  after 80 seconds. The busy-handler marker was retained. No close request was
  replayed, editor process terminated, or hardware changes discarded. Cleanup
  and restoring hardware focus are not claimed completed for this attempt.
- **Stale compiled multipart instances after deletion (P1):** force compile was
  insufficient in the reported board session; reopening restored correct counts.
  No safe root-cause fix has been demonstrated. Verify the saved/reopened netlist
  before accepting multipart deletion or package changes. A replay test must
  compare native instances, compiled pins, and ERC before and after reopen.
- **Legacy completion versus actual engine return (P2):** request-specific
  completion and shared ownership guards already exist, but a completion file
  cannot by itself prove that DelphiScript has returned. The occasional rapid
  handover failure still needs a reproducible native release signal. No arbitrary
  delay or automatic edit replay was added.
- Native PNG export limitations, screenshot resolution, and a helper started
  with mismatched environment values are not evidence of electrical defects.
  The workspace mismatch is now prevented; other observations retain their
  original scope.

## Verification

- 190 suite Python tests and 111 targeted EDA tests passed locally. Newly added
  checks run in GitHub CI; native behavior cannot be reproduced by a Python fake.
- DelphiScript build/lint passed without suppressing rules.
- Installed-directory initialization passed for both wrappers (448 EDA tools,
  37 legacy tools). A fresh installed EDA client verified source identity,
  multipart stamp counts, annotation SVG output, and clean-member save behavior.
- Native tests used disposable projects/libraries. All 14 hardware `.PrjPcb`,
  `.SchDoc`, and `.PcbDoc` hashes were unchanged after recovery and acceptance.
  An empty sheet initially added to the focused hardware project during testing
  was removed from membership and moved into the scratch project. Pre-existing
  unsaved project metadata was not saved, discarded, or declared clean.

Restart existing MCP connections after deploying the Python changes. To change
an intentionally bound workspace, stop both servers/native scripts first, then
archive the runtime's identity file and configure the new path identically for
both wrappers. Never remove that identity as an automatic mismatch recovery.
