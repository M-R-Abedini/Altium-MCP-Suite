# Using the Server From Your Assistant

What to ask for once the server is wired into your AI client, and what to expect back.
Everything here is identical across clients — Claude Code, Claude Desktop, Google
Antigravity, Cursor, VS Code, Gemini CLI and the rest — because the assistant drives the
same 34 tools over the same protocol. Wiring a client up is covered, per client, in
[CLIENT_SETUP.md](CLIENT_SETUP.md); the tools themselves are in [TOOLS.md](TOOLS.md).

The assistant does the engineering (IPC-7351B land patterns, pin placement, symbol
geometry) and the server does the file I/O, so a prompt describes the *part*, not the
primitives — see [AI_WORKFLOW.md](AI_WORKFLOW.md) for how the assistant gets from one to the
other, and [AGENT_GUIDE.md](AGENT_GUIDE.md) for the conventions the server itself tells it.

---

## Example Workflows

### 1. Create a single footprint

```text
Create an IPC-7351B compliant 0603 chip resistor footprint and save it to
./MyLibrary.PcbLib
```

The assistant will:

1. Calculate the land pattern using IPC-7351B
2. Generate pad coordinates, silkscreen, and courtyard
3. Call `write_pcblib` to create the file

### 2. Create a matching schematic symbol

```text
Now create a matching schematic symbol for the 0603 resistor and save it to
./MyLibrary.SchLib. Use designator "R?" and link it to the RESC1608X55N footprint.
```

### 3. Analyse an existing library

```text
Read ./ExistingLibrary.PcbLib and describe the footprints it contains.
What silkscreen style does it use?
```

The assistant will:

1. Call `read_pcblib` to read the library
2. Analyse the primitives
3. Describe the styling conventions

### 4. Match an existing style

```text
Extract the style from ./CompanyLibrary.PcbLib and create a new 0805 capacitor
footprint that matches the same style conventions.
```

The assistant will:

1. Call `extract_style` to analyse the existing library
2. Apply the same track widths, pad shapes, and layer usage
3. Create a style-matched footprint

### 5. Create a complete component library

```text
Create a chip resistor library with footprints and symbols for:
- 0201, 0402, 0603, 0805, 1206, 2010, 2512

Use IPC-7351B nominal density. Save to ./ChipResistors.PcbLib and
./ChipResistors.SchLib
```

The assistant will batch-create all components using IPC-7351B calculations.

### 6. Create from datasheet specifications

```text
Create a footprint for a QFN-24 package with:
- Body: 4mm x 4mm
- 24 pins, 0.5mm pitch
- Thermal pad: 2.5mm x 2.5mm
- Use IPC-7351B nominal density

Save to ./ICs.PcbLib
```

### 7. Edit what is already there

```text
In ./Passives.PcbLib, widen pad 1 of RESC1608X55N to 1.0 mm and move every
Top Overlay track of that footprint to Mechanical 13.
```

The assistant will use `update_pad`, `update_primitive` or `batch_update` rather than
rewriting the footprint; the rest of the library is left byte for byte as it was.

---

## Example Prompts

### Basic component creation

```text
Create an 0805 chip capacitor footprint with IPC-7351B nominal land pattern.
```

```text
Create a 2-pin polarised capacitor schematic symbol.
```

### Working with existing libraries

```text
List all components in ./MyLibrary.PcbLib
```

```text
Read ./Passives.SchLib and show me the pin configuration for the RESISTOR symbol.
```

### Style matching

```text
Analyse the silkscreen style in ./ExistingLib.PcbLib - what line width does it use?
```

```text
Create a new footprint matching the style of ./CompanyStandard.PcbLib
```

### Batch creation

```text
Create a complete SMD inductor library with sizes: 0402, 0603, 0805, 1008, 1206
```

```text
Create schematic symbols for all footprints in ./Passives.PcbLib
```

---

## Tips for Best Results

### 1. Be specific about standards

```text
Use IPC-7351B nominal density (not maximum or minimum)
```

### 2. Specify layer preferences

```text
Put silkscreen on Top Overlay, courtyard on Top Courtyard layer
```

### 3. Request style analysis first

```text
First analyse ./ExistingLib.PcbLib, then create new components matching that style
```

### 4. Provide datasheet details

When creating custom packages, provide:

- Body dimensions (L x W x H)
- Pin pitch
- Pin count and arrangement
- Thermal pad dimensions (if applicable)

### 5. Use append mode for incremental building

```text
Add an 0402 resistor footprint to the existing ./Passives.PcbLib (append mode)
```

### 6. Ask for validation

```text
Run validate_library on ./Passives.PcbLib and fix anything it reports
```

---

## When Something Goes Wrong

These are the problems that show up *after* the client is connected. A server that does not
appear in the client at all, or will not start, is a wiring problem —
[CLIENT_SETUP.md § Troubleshooting](CLIENT_SETUP.md#troubleshooting) has that checklist.

### "Access denied" error

The target path is outside the server's `allowed_paths`. Add the directory to your
`config.json` (see [README.md § Configuration](../README.md#configuration)) — or, for the
Claude Desktop extension, to its **Library folders** setting — and restart the client.

### Library won't open in Altium

The files are verified against Altium Designer 24 (the project's golden fixtures are
AD24-authored, and a corpus of hand-drawn AD24 libraries round-trips byte for byte);
older versions that read the same library format should work but are untested. The format is
binary-compatible across platforms, so a library generated on Linux or macOS opens on
Windows. Check the file was created (non-zero size) and ask the assistant to run
`validate_library` on it.

### Style extraction shows unexpected values

`extract_style` analyses every primitive in the library. A library with mixed styles
reports several values for a property; ask for the dominant one, or analyse a single
footprint with `read_pcblib`.

### The assistant's call was refused

Every tool refuses an argument or JSON key it does not document, naming the accepted ones
(`Unknown field 'widht'. Allowed fields are: […]`), and refuses a malformed primitive rather
than dropping it — so the file is never half-written. The message is meant for the
assistant; it will normally correct itself. [errors.md](errors.md) lists every error shape.

---

## Platform Notes

**Windows** is the primary platform, since Altium Designer runs there: generate libraries
straight into your Altium project folder. In JSON configuration every `\` is written `\\`.

**Linux and macOS** generate the same files — the format is binary-compatible — so use a
shared folder, cloud sync or version control to move them to the Windows machine that runs
Altium.

The server validates every file path against `allowed_paths`, so the assistant cannot read
or write outside the directories you list; keep that list to your library folders
([SECURITY.md](SECURITY.md)).

---

## Next Steps

- [CLIENT_SETUP.md](CLIENT_SETUP.md) — wiring for every MCP client
- [TOOLS.md](TOOLS.md) — every tool, parameter and example
- [AI_WORKFLOW.md](AI_WORKFLOW.md) — the IPC-7351B workflow and symbol conventions
- [AGENT_GUIDE.md](AGENT_GUIDE.md) — the unit and pin-geometry conventions; worth pasting into
  a project brief
- [ARCHITECTURE.md](ARCHITECTURE.md) — technical details; `scripts/samples/` holds
  Altium-authored example libraries
