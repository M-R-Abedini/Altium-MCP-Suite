# Suite operations and native limits

Names below are discovery hints verified against suite source on 2026-10-27. Fetch current schemas through `suite_catalog` and pass the returned names/arguments to `suite_invoke`. These are capabilities, not a mandatory fixed sequence.

| Need | Discover | Retain |
| --- | --- | --- |
| Input contracts | `eda__design_get_discipline` | `schema` (DesignPlan) and `requirement_schema` (DesignRequirement), including nested definitions |
| Requirements | `eda__design_validate_requirement` | Normalized facts and material unresolved inputs |
| Existing models | `eda__design_snapshot_inventory`, `libraries__search_components`, `libraries__get_component` | Paths, symbol/footprint identities and geometry |
| Cloud sources | `cloud__cloud_library_providers`, `cloud__cloud_library_search`, `cloud__cloud_library_download`, `cloud__cloud_library_stage_local` | Capability status and artifact manifest |
| Preflight | `eda__design_validate_plan`, `eda__design_review_plan` | Offline schema/ERC-lite results and limits |
| Preview | `eda__design_preview_plan` | SVG, failures; cache misses may read native SchLibs |
| Emit | `eda__design_execute_plan` | `use_canvas=true`, explicit `.PrjPcb`, results; `result_detail="summary"` retains full local artifact |
| Native netlist | `eda__proj_get_nets` | Full-table query, `limit=0` for unlimited, target and freshness |
| Schematic review | `eda__design_audit_schematic`, `eda__design_validate` | Geometry separately from ERC/connectivity |
| Save | `eda__proj_save` | Explicit target, `include_libraries=false` unless intended |
| Fabrication rules | `eda__design_load_fab_profile`, `eda__design_synthesize_rules` | Actual fabricator limits and resolved constraints |
| PCB state | `eda__pcb_get_pad_connectivity`, `eda__pcb_get_unrouted_nets`, `eda__pcb_get_board_statistics`, `eda__pcb_get_design_rules` | Pad connectivity, routing state and enabled rule scope |
| PCB review | `eda__proj_get_differences`, `eda__pcb_get_clearance_violations`, `eda__pcb_run_drc` | Mapping differences; cached versus newly run checks |

## Side effects that matter

- `proj_get_nets(force_recompile=true)` saves target members and project definition before recompilation. Use when that persistence is authorized. For a strictly read-only review report dirty/freshness limits and inspect `proj_get_compile_freshness` instead. The emitter's topology verification may already provide fresh post-emit evidence; do not repeat it without reason.
- `proj_sync_pcb(allow_modal=true)` opens an interactive ECO. `pcb_run_drc(allow_modal=true)` can block the bridge in a DRC dialog. Ensure someone can handle the interaction before starting; leave these calls disabled in unattended work and report the phase as pending. Existing clearance violations do not prove a new DRC ran.
- ECO component-count equality proves presence only. Verify nets and footprint changes separately. Placing footprint geometry does not establish schematic synchronization.
- A native-write timeout leaves execution uncertain. Inspect target state/evidence before retrying; do not replay an entire placement or clear engine ownership based on elapsed time. A modal dialog or running owned script may need an observed stop. Preserve the coordination layer.
- Use `eda__sch_place_components`, `eda__sch_place_wires`, `eda__sch_place_buses`, `eda__sch_place_bus_entries`, `eda__obj_batch_modify` or relevant `legacy__` batches after discovery. Keep native writes sequential; independent offline calculations/network research can overlap.
- Query connectivity once and filter locally. Bind library paths explicitly. After unsaved SchLib edits, disk hashes cannot detect the changes: save the intended library or invalidate the extraction cache before relying on it.

If the suite is disconnected, continue sources and offline preparation. Report that missing connection rather than inventing former server tools.
