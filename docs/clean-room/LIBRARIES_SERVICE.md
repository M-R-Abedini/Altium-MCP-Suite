# Independent library service

The suite owns `suite_libraries_native.py` and `suite_ole.py`; both are original
MIT-licensed Python implementations. They do not import, launch, inspect, or
depend on the former library worker implementation.

`contracts/libraries-v0.3.0.json` records the 34 tool descriptions, annotations,
and JSON input schemas observed through the released executable's MCP
`tools/list` interface. This is interface evidence, rather than implementation
source. Dynamic paths and identifiers from oracle responses are not embedded
as canned answers in the implementation or tests.

Development observations used synthetic libraries in an isolated allowlisted
scratch directory. No user design library was opened. The service's independently
chosen algorithms implement component edits, pagination, search, style
aggregation, validation, JSON/CSV interchange, ASCII previews, backup/restore,
STEP extraction, model repair, and project-package generation. The separate
[SchLib](SCHLIB_FORMAT.md) and [PcbLib](PCBLIB_FORMAT.md) codec notes describe their
binary-format observations and native verification limits.

The CFB writer was implemented from Microsoft's public MS-CFB container format
specification, especially header, FAT, mini-FAT, directory, and DIFAT structures:

- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-cfb/05060311-bfce-4b12-874d-71fd4ce63aea
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-cfb/c5d235f7-b73c-4ec5-bf8d-5c08306cd023
- https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-cfb/a94d7445-c4be-49cd-b6b9-2f4abc663817

## Fidelity and verification

The unchanged read/JSON-echo/write path keeps the original file bytes. Edits
preserve unmodelled stream payloads through the codecs. When a container must
be rewritten, its directory allocation, free space, and storage metadata are
rebuilt; preservation of those container bytes is not claimed for edited files.
ASCII previews are schematic approximations, and their character layout and
diagnostic wording need not match the released executable byte for byte.

The service adds `library_header_params` to schematic JSON exports to retain
non-default font tables and unmodelled library header settings on import.
Cross-library schematic copies and merges unite font tables and remap known
font references, including non-default designator font references. Unknown
opaque records remain opaque and are preserved without interpreting them.

`tests/test_libraries_native.py` exercises every observed tool against synthetic
libraries. Additional cases cover path confinement, rejected operations without
mutation, backups and exact restoration, font remapping, unchanged echo writes,
unknown stream preservation, STEP export/import/copy/extraction/repair, explicit
placeholder heights, silk overlap warnings, and stacked-pad overrides. CFB
tests include miniature, normal, empty, nested, and DIFAT-sized streams.
