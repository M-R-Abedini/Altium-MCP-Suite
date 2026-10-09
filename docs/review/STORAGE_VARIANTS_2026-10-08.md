# Project persistence and native assembly variants

Verified on Altium Designer 26.10.1, Windows, 2026-10-08.

| Finding | Correction | Evidence |
| --- | --- | --- |
| Installed wrapper could not import `suite_config` | `deploy.py` includes all shared modules, package sources and scripts; initialization failure rolls back changed files. The EDA wrapper prefers the deployed package. | Installed EDA and legacy entry points initialized with their respective configured Python environments. |
| `proj_save` omitted the project definition | Select and verify the target with `DM_SetAsCurrentProject`, dispatch `SaveObject` with `FocusedProject`/`Standard`, restore previous project. Check disk membership and variant identities/records against native readback. | Scratch `.PrjPcb` retained both schematic/library members and both variants through close/reopen. Virtual ActiveBOM is excluded from disk-member checks. |
| Variant names and CSV headings collided | Return persisted `unique_id` and visible `display_name`; reject ambiguous aliases. CSV uses native descriptions and disambiguates duplicate headings without changing cells. | Two native `DM_Name="Variant"` definitions resolved to distinct GUIDs and `eMMC_8GB`/`SPI_NAND_256MB` headings. |
| No fitted-state writer; creation dispatch unreliable | Add `proj_set_variant_fitted` and `proj_get_variant_matrix`. Create/switch/update through a guarded closed-project serialization transaction with backup, native verification and recovery. | On a scratch design: 20 components, 40 matrix cells; three Not Fitted updates and one reversal to Fitted verified natively. Switching by GUID and display alias both passed. |
| Limits, owner properties and TextFrame reads inconsistent | Zero means unlimited; negative limits rejected. Unsupported pin-owner properties rejected before dispatch. Unknown/unreadable generic reads carry diagnostics. `eTextFrame` supports text and typed corner reads. Document paths use full paths. | 94 compiled pin records with limit zero, two with limit two; owner rejection and TextFrame text/location/corners checked live. |

`proj_close` also required correction: the native command targets focus, not a
`FileName` parameter. It now verifies the selected project and uses
`FocusedProjectAndDocuments`, matching Altium's shipped WorkspaceManager command
definitions. Tests exercised repeated save/close/reopen cycles.

## Variant workflow

1. Use `proj_save` to persist native UI-created variants. `proj_list_variants`
   returns their GUIDs and visible labels; an unsaved identity is explicitly
   marked `persisted:false`.
2. Obtain physical component `unique_id` values from `proj_get_variant_matrix`.
   Keep the complete hierarchical ID; a symbol's local ID is insufficient in a
   multi-sheet design.
3. Call `proj_set_variant_fitted(variant_id=..., updates=[{"unique_id": ..., "fitted": false}], project_path=...)`.
   Use `proj_set_active_variant(variant_id=..., project_path=...)` to switch.

Transactions save and close only the target `.PrjPcb`, keep a `.variants-*.bak`
backup, edit native sections, reopen, and verify the requested states. Parameter
overrides and unrelated sections are preserved. Alternate-part overrides,
duplicate/missing component IDs and ambiguous variant selectors are rejected.
Recovery never overwrites an external disk edit. Reopening can change document
focus; pass explicit project paths in subsequent calls. This is file-backed
automation verified through the native readers, not an undocumented DelphiScript
fitted-state setter. The native UI remains available for alternate-part editing.

## Installed-directory deployment

```powershell
python deploy.py "C:\Path\To\AltiumMCP" --python "C:\Path\To\Configured\python.exe" --server eda
python deploy.py "C:\Path\To\AltiumMCP" --python "C:\Path\To\Legacy\python.exe" --server legacy
```

Dependencies must already be installed in those environments. Deployment leaves
client settings, library allowlists and virtual environments intact, writes a
SHA-256 manifest, backs up replaced files and smoke-tests MCP initialization and
tool discovery from the installed directory. Restart existing MCP connections to
load changed Python modules and discover the new tools. Content-addressed script
paths let Altium recompile changed Pascal sources on bridge restart.

The library connector's `allowed_paths` remains explicit configuration. Add the
intended Dependencies directory to its allowlist, or repeat `setup.py`'s
`--library-dir`; deployment does not broaden library access.

## Validation

- Final Python selection: 195 passed, 9 skipped. Installed EDA initialized
  with 447 tools; installed legacy initialized with 37 tools, each using its
  own configured interpreter.
- Pascal build/lint: zero errors or warnings.
- Python regression checks cover deployment/rollback, persisted metadata,
  ambiguous identity, CSV collisions, codecs, preserved overrides, failed close,
  verification rollback, external edits, cancellation and invalid query input.
- Live scratch regression covers native save, library membership, limits,
  TextFrame reads, variant creation, fitted-state updates, GUID switching,
  meaningful CSV headings and close/reopen. Hardware files were not used as
  mutation targets.
- Existing idle engine release remains enabled; this work does not keep Altium's
  script engine occupied between commands.
- An installed-server read-only check of the original hardware project verified
  both variant GUIDs, all 16 exclusions and all 366 matrix cells. Design-file
  SHA-256 hashes were unchanged during that check; `eMMC_8GB` remained active.
