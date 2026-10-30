# Independent SchLib format observations

This MIT codec was written using only public MCP tool schemas, black-box worker
calls, and bytes of synthetic OLE files created by those calls. No source,
tests or documentation from `altium-designer-mcp` or `oldimplementation` was
read. The oracle is not a codec runtime dependency.

Observations occurred on 2026-10-29 against released worker version 1.0.1 from
runtime `20261028T131406Z-df74cc5e`. Its allowlist was restricted to the
Electronics_AI `analysis/step1-clean-license/oracle-workspace` scratch directory.
Retained provenance there: `libraries-catalog.json`, `sch_all_req.json`,
`sch-all-read.json`, `sch_varied_req.json`, `sch-varied-read.json`,
`sch_unicode_req.json`, and `sch-authored-{all,varied,unicode}-response.json`.
The test module embeds synthetic all-primitives stream bytes and their observed
structured response, not implementation source.

## Container and records

The file is OLE compound storage. `FileHeader` has a parameter record identifying
Schematic Library Editor Binary File Version 5.0, indexed component references,
descriptions/part counts, and a font table. Components have individual `Data`
streams. Long names use storage names truncated to 31 characters and a
`SectionKeys` mapping stream.

Each record has a little-endian u32 prefix: low 24 bits give payload byte count;
the high byte gives representation (0 parameter text, 1 binary). Parameter text
is NUL-terminated, uses pipes between ordered key/value pairs, and the first `=`
divides a key/value. Unknown keys and order are retained. Zero/false fields are
often omitted. Coordinates have integer and signed `_Frac` parts in 1/100000
schematic-unit steps; negatives truncate toward zero. Ten schematic units equal
one grid square. Unicode uses `%UTF8%` twins; typed fields decode UTF-8 while raw
pairs expose the original ANSI interpretation, as observed in worker responses.

Observed IDs: 1 component header; binary 2 pin; 3 IEEE symbol; 4 label; 5 Bezier;
6 polyline; 7 polygon; 8 ellipse; 9 pie; 10 rounded rectangle; 11 elliptical arc;
12 arc; 13 line; 14 rectangle; 28 text frame; 30 image; 34 designator; 41 parameter;
44/45/46/48 model container/model/children. `PartCount` is one higher than the
exposed authored count. `AllPinCount` is preserved even if stale. Footprint model
records hold names, optional library paths, current flags, and raw parameters.

## Pins and compressed auxiliary streams

The observed pin payload contains u32 record ID, reserved byte, i16 part ID,
display-mode byte, four decoration bytes, short description, formal/electrical
bytes, flags byte, u16 length, i16 X/Y, u32 colour, then five short strings:
name, designator, swap group, part/sequence, default value. Short strings have a
one-byte encoded byte count. Unknown tails and nonzero reserved/flag bits have
preservation carriers.

Flags: bits 0-1 right/up/left/down; bit 2 hidden; bit 3 show name; bit 4 show
designator; bit 5 inaccessible; bit 6 locked. X/Y are body attachment coordinates;
length extends in the indicated direction. Selected decoration variations follow
the tool schema enumeration order.

`PinFrac`, `PinSymbolLineWidth`, `PinWideText` have a text header followed by
binary records: marker 0xD0, short pin index, u32 compressed count, zlib payload.
Fraction payload is X i32, Y i32, length u32. Text/width payloads have a byte count
and UTF-16LE parameter text. `Storage` uses the same envelope with image filenames
as keys and raw image bytes as decompressed payloads.

## Verification and practical limits

Native reads exactly matched the complete worker responses on all-primitives
and full optional-field variation fixtures. The worker successfully read three
independently authored native files; native and worker reads matched including
Unicode, multipart pins, fractional geometry and model links. Component Data,
PinFrac and PinSymbolLineWidth payloads survived cross-file rewrite unchanged.
Eight automated tests cover golden data, typed edits overriding raw values,
unknown records/streams, fonts, Unicode, embedded images, case-insensitive lookup,
pagination, append order and malformed records.

Whole-file byte identity is not claimed: the original compound-file writer
rebuilds directory/allocation metadata. Unknown library streams and font/header
parameters survive in-place writes. `opaque_records` carries unknown component
records across JSON/cross-file rewrites; `extra_streams` carries unknown component
streams. Copying library-wide unknown streams/fonts to a new file requires the
existing destination or explicit `library_header_params` option; the observed
schema exposes neither as component fields. Unobserved version-specific binary
semantics are not claimed. Altium Designer has not been launched for verification.
