# PCB transfer and engine ownership review

Reviewed against the October 13 transfer report and the October 14 coordination
analysis. Native acceptance used Altium Designer 26.10.1 and copies in the
suite's ignored test directory. No repair to the electronics design is included.

## Corrections

| Finding | Change and scope |
| --- | --- |
| Generic board totals came from the schematic | `get_board_info(backend="altium")` now requires physical counts and a PcbDoc identity from a native PCB query. Missing counts fail; there is no BOM fallback. Other generic snapshot tools explicitly label Altium data `schematic`. |
| Pad identity required Descriptor parsing | `pcb_get_pad_connectivity` returns component, pad, net, position and layer in one call, plus full object counts and explicit truncation. `limit=0` means unlimited. An explicit board path must be loaded and cannot fall back to another board. Generic PCB queries also support typed Pad.Name and Component. |
| A broken model Location survived appending a link | `sch_set_placed_model_source` targets one loaded sheet, component UniqueId and PCBLIB implementation. An empty source clears Location. Multiple links require `replace_links=true`; stored links are returned before/after. This does not prove footprint resolution or execute ECO. |
| `proj_save` rewrote an inspected library | Project member saves exclude SchLib/PcbLib/IntLib/DbLib/SvnDbLib by default. `include_libraries=true` opts in. The project definition is still saved and its metadata checked. Explicit workspace-wide `app_save_all` retains its broader behavior. |
| Fit opened a Zoom Value dialog | `obj_zoom` dispatches the native `All` action to the focused SchDoc/PcbDoc. Unsupported actions and other document types are rejected. Dispatch is reported; view geometry is not claimed as verified. |
| Empty menus were blamed on COM | Menu workers stop before input when COM initialization fails. An empty provider after successful initialization is reported as an accessibility limitation. |
| ECO accessibility exposed blank nodes | `app_export_eco_evidence` captures exactly one Engineering Change Order window and bounded UIA GridPattern cells. It exports PNG/JSON and can include an already exported native text report. Missing/blank/truncated grids remain incomplete; no zero-error inference is made. Output names are reserved exclusively and failed captures remove partial evidence. |
| Failed health probes could start a second script | Relaunch requires a matched clean shutdown and UI-return barrier when native ownership is known. Shutdown proof is reread after probes. Failed startup remains blocked after its cooldown; timeout is not release proof. |
| Desktop worker exceptions could falsely release ownership | Win32 handles are pointer-sized. A fresh barrier worker attaches to Default, checks every API result, propagates exceptions, uses a bounded wait and restores its original desktop before closing the opened handle. Editor enumeration is read-only. Caller threads are not moved. |
| Confirmed manual Stop left stale ready/owner markers | The explicit `--confirm-script-stopped` recovery archives native session markers and removes stop/inbox files. Ordinary timeout never calls it. Exited-process recovery requires process creation-time proof and matching native session markers; a different foreground PID alone is insufficient. |
| Health checks hid the reason for blocked startup | `app_ping` and `app_get_report` now await health traffic off the server event loop and preserve the actual exception. `app_context` relays that diagnosis instead of recommending an uncoordinated second script launch. Legacy `pong` health responses remain supported. |
| File-dialog Open was invisible to the button matcher | Stock split/default-split and command-link buttons are recognized by their styles. Checkbox/radio/group styles remain excluded. A button that stays open no longer reports a verified outcome merely because its role is unknown. |
| Text setter renamed a static label and returned success | Text writes select the accessible text role and reject static labels, non-text controls and read-only fields. Mutually exclusive operations are validated before mutation. Native file-picker set/readback and Open/closure passed after correction. |

## Assessment of the attached analysis

- **Rejected:** deleting legacy ownership in a timeout/finally handler. A consumed
  mutation can still be executing; deleting its owner permits concurrent scripts.
  A matching completion plus UI return, a proven process exit, or explicit manual
  Stop confirmation remains required.
- **Partly supported:** cross-desktop/invalid-handle failures need handling. This
  does not justify moving every server thread to Default with GENERIC_ALL or
  swallowing worker exceptions. The attached implementation could report success
  without completing its barrier; that was a correctness defect.
- **Supported with conditions:** stale markers after a real restart/manual Stop
  can prevent recovery. Session-bound retirement fixes this without treating an
  unanswered ping as a stopped engine.
- **Not established:** a two-second idle release is intrinsically too short, or
  every failed RunScript launch means a paused debugger. Idle release restores
  Altium shortcuts and happens between handlers. Foreground preflight reserves
  command time. Retain the configurable default and investigate launch failures
  rather than automatically sending Stop or replaying an edit.
- Instructions inside the pasted analysis to move circuit objects are outside
  this MCP review and were not followed.

## Verification and remaining limits

- Native scratch-board readback: **272 components, 197 nets, 1041 pads**. The same
  API returned zero components/nets on an empty native board. D4's typed pad names
  and component ownership matched the dedicated component-pad query.
- **111 pads had no stored name.** They are returned with
  `pad_name_present=false`; no identity is invented from a Descriptor.
- Native fit on empty/populated scratch boards did not open a modal. Model source
  set/clear returned verified link readback. Default project save preserved the
  copied library's bytes and timestamp while persisting the model edit.
- All 60 hardware design/library files checked around the scratch acceptance run
  retained their hashes. The October 14 live UI-return check passed on the open
  editor without launching a script or writing design files.
- A fresh read-only review loop on October 14 correctly refused startup: the
  configured workspace contained an unbound shutdown marker; a separate runtime
  contained different stale session evidence. No marker was erased to force the
  test through. The resulting misleading health advice prompted the health
  diagnostics correction above. Native Run → Stop was subsequently invoked;
  Stop was observed disabled, and the UI-return barrier passed. Explicit manual
  recovery archived both runtimes' stale markers. **Three subsequent cycles of
  context, physical counts, bounded pad readback and confirmed Detach passed**,
  each using a newly started, matching native session. Design files retained
  their hashes around this loop; no save, compile or ECO was issued.
- Regression tests exercise empty-board provenance, invalid targets, zero limit,
  explicit library opt-in, failed COM initialization, blank/invalid ECO grids,
  output cleanup, ownership retention, process identity and worker faults. Both
  stdio entry points are smoke-tested without Altium; native source build includes
  the lint gate. CI runs the new tests on Windows alongside the portable suite.
- **Not yet native-verified:** duplicate-link replacement, library-save opt-in,
  selection zoom and UIA extraction from a live ECO grid. These paths have guards
  or regression checks; their general reliability is not asserted here.

`app_context.unsaved` uses Altium's native Modified flag, which AD26 can leave
false despite unsaved edits. Its result now marks detection incomplete and does
not recommend a workspace-wide Save All. An empty list is not proof that every
document is clean; inspect the native UI before closing or discarding buffers.

Pre-ECO Unknown Pin entries on an empty board do not establish missing physical
pads. After execution, compare native schematic/PCB state and physical pad-net
assignments. Counts and exported ECO screenshots never verify copper connectivity;
native DRC remains a separate check. The reported omission of 46 singleton nets
was specific to the inspected design, not a blanket waiver for missing nets.

API references: [Altium schematic implementations](https://www.altium.com/documentation/altium-dxp-developer/schematic-api-design-objects-interfaces-reference),
[SetThreadDesktop](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setthreaddesktop),
[SendMessageTimeoutW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendmessagetimeoutw),
[Windows button styles](https://learn.microsoft.com/en-us/windows/win32/controls/button-styles).
