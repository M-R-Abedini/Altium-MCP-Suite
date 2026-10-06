# Using altium-designer-mcp

Invariants for an agent driving this server. For the per-tool parameter schema
and a worked example of every tool, call `tools/list` (the schema travels with
the server) or read `docs/TOOLS.md`. This guide covers only the conventions that
the per-tool schema does not.

## Responsibility split

The **agent** supplies the intelligence — it computes footprint land patterns,
pin positions, and symbol geometry (from datasheets, IPC-7351, etc.). The
**tool** only validates and writes those primitives to the Altium binary
format. The server never invents geometry; pass it exact, finished numbers.

## Coordinate units (this trips up most callers)

- **`.PcbLib` (footprints):** millimetres. Pad/track/arc/region coordinates and
  sizes are all mm.
- **`.SchLib` (symbols):** schematic units where **10 units = 1 grid square**
  (≈ 100 mil). These are *not* millimetres. Graphic primitives accept fractional
  values; pin coordinates are integers.

## Pin geometry (the counter-intuitive one)

For a schematic pin, `(x, y)` is the **body-attach (inner) end** — the end that
touches the symbol body — **not** the connection tip. `orientation` is the
direction the pin **points outward**:

- `left`  → tip at `x - length` (pin sits on the body's left edge)
- `right` → tip at `x + length` (right edge)
- `up`    → tip at `y + length` (top edge)
- `down`  → tip at `y - length` (bottom edge)

So a left-side pin uses `"left"` with its `(x, y)` on the left edge of the body
rectangle; the tip extends further left. The `write_schlib` response echoes each
pin's computed `body_end`/`tip` so you can verify placement without opening
Altium.

## Overbar names and multi-part symbols

Overbar (active-low) pin names use a backslash **after** each barred character
(`R\E\S\E\T\`); multi-unit parts set `part_count` + per-pin `owner_part_id`. Full
conventions and examples:
[AI_WORKFLOW.md § Symbol Pin Conventions](AI_WORKFLOW.md#symbol-pin-conventions).

## Symbol draw order

Schematic records render in the order they are stored, so a filled body drawn
after a pin hides that pin's name. You do not have to manage this: a symbol you
author from JSON is written body-graphics-first, so `"filled": true` bodies sit
behind their pin names. Keep bodies solid — a transparent body is not the fix
for hidden pin names. Supply `primitive_order` only to reproduce an order you
read off an existing file; it overrides the default.

## Description length

The Altium 365 library importer refuses a component whose description exceeds
**256 characters**, and it names neither the library nor the component at
fault. Altium Designer itself opens and reads such a library whole, so a longer
`description` is written as asked — and every write and `validate_library`
reports it as a warning naming the component and the overshoot. If a library
is bound for a workspace, keep footprint, symbol and footprint-link
descriptions inside the limit; put the long-form reasoning in your own notes,
not the description field.

## Filesystem sandbox

Only paths inside the configured `allowed_paths` (`config.json`, or the folders
granted with `--allow`) can be read or written. Requests outside the sandbox are
rejected. Writes create a timestamped
`.bak` of any existing file first.

## Typical build flow

1. Gather the real pinout/dimensions (datasheet, land-pattern drawing).
2. Compute geometry (the agent's job).
3. `write_pcblib` for footprints, `write_schlib` for symbols.
4. Link each symbol to its footprint(s) via the symbol's `footprints` field
   (`name` + optional `library_path`).
5. `write_libpkg` to group the `.SchLib` + `.PcbLib` for compilation.

Compiling to an `.IntLib` is a one-click step inside Altium; this server only
produces the source documents.
