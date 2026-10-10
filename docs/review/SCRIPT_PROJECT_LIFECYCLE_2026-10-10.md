# Script project lifecycle

Validated on Altium Designer 26.10.1, Windows, 2026-10-10.

## Cause and correction

The Pascal units divide schematic, PCB, library, project and transport operations.
The defect was repeated **projects**, not that division of source code.
Each template update creates a new content-addressed script directory to force
Altium to recompile. Older projects stayed open in the Projects panel.

Generated projects now use `AltiumMCP-EDA.PrjScr` and
`AltiumMCP-Legacy.PrjScr`. An atomic ownership catalog lists intact generations
and private sandboxes. Expected SHA-256 hashes exclude changed files; old
unmarked generations are accepted only if their original directory digest
matches. Sources remain on disk for rollback.

EDA startup closes eligible older projects before publishing readiness.
It skips the executing project, unexpected document membership, sources outside
the generated directory, and every resident source editor. A script editor
returned `Modified=False` immediately after `SetModified(True)`; an open buffer
therefore cannot be assumed clean. Close the editor yourself when finished, or
copy the project outside the runtime for debugging.

Closing an unloaded script project requires loading and focusing its source
editor, checking that document's project owner, then issuing
`FocusedProjectAndDocuments`. `DM_SetAsCurrentProject` alone was insufficient.
Removal is read back and previous editor focus is restored. Directly issuing
`ProjectAndDocuments` with a filename closed the active hardware project during
the investigation; it was reopened, all 13 design-file hashes were unchanged,
and that command is excluded by a regression test.

## Validation

- The originally repeated script projects were closed; the hardware project
  remained open alongside one EDA script project. Three old generations with
  unverifiable hashes were closed explicitly after confirming no unsaved changes;
  they were not added to the automatic catalog.
- Python regressions cover reload identity, idempotence, changed generated files,
  malformed/external manifests, original-digest migration, sandbox ownership and
  native focus/readiness guards.
- The native lifecycle check covers a retained open source editor, cleanup after
  that editor closes, completed sandbox cleanup, repeated engine restarts and
  refusal to close the executing bridge.
- Local checks: 176 suite tests and 194 EDA CI tests passed. Both deployed
  wrappers initialized successfully (447 EDA tools, 37 legacy tools).
  Final native readback showed one script project, the original PCB editor
  focused, no unsaved documents, and 13 unchanged design-file hashes.

The catalog closes project views, not files on disk. Raw standalone EDA scripts
without bound suite paths skip this cleanup. Restart existing MCP connections
after deployment so they load the new Python generation and ownership logic.
