# Independent PCB-library codec

`suite_pcblib_codec.py`, `suite_pcb_primitives.py`, and `suite_ole.py` are original
MIT Python implementations. Format observations came from synthetic files,
isolated public-interface probes, Microsoft's MS-CFB specification, and files
created and saved in our own Altium session. No retired implementation source
or bundled implementation assets were used.

`contracts/pcblib-default-board.json` contains sanitized board settings from our
Altium-created empty PCB library. The writer supplies fresh file paths, times,
and identifiers. `contracts/windows-1252-best-fit.json` records independently
generated Microsoft `WideCharToMultiByte(1252, 0)` observations on UTF-16 code
units. These data let the writer reproduce the Windows ANSI spelling used for
native component lookup on every supported Python host.

The component table and component identity record use ANSI short strings.
Unicode component names use Unicode CFB storage names and decimal UTF-16 code
units in `UNICODE__PATTERN`; descriptions use `UNICODE__DESCRIPTION`. A
supplementary character contributes two surrogate units. Unicode text primitives
use their component's `WideStrings` stream. The component parameter table is one
block containing all rows, with a header record count of one.

## Native verification and limits

Fresh native Altium reads accepted generated libraries containing pads, tracks,
text, component bodies, and a long component-name alias. Independent synthetic
regressions also cover arcs, fills, vias, regions, primitive order and identity,
layer stacks, additional parameter pairs, embedded and external STEP references,
and preservation of unknown stream payloads and unsupported primitive tails.
This is exercised coverage, not a claim that all Altium properties or versions
have been tested.

The headless codec retains full Unicode names, descriptions, and text through
their Unicode carriers. Altium's component-name getter has a verified disk-load
boundary: after loading a fresh file, `Ω_فارسی_😀` is reported as the ANSI spelling
`O_?????_??`. This happens for both our generated file and a byte-for-byte copy
of a file created and saved by Altium itself. The original document still open
after a UI rename can report the full Unicode name from memory. Unicode
descriptions loaded correctly from our generated file. Native getter fallback
therefore does not establish loss of the persisted Unicode carrier, and the
writer does not claim to change Altium's getter behavior.

The 34-tool service regressions and the separate schematic and PCB primitive
codec tests run without Altium. Native acceptance uses separate synthetic files;
ordinary unit tests do not establish native compatibility for untested features.
