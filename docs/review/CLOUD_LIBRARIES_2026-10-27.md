# Cloud libraries in MCP Altium Suite

The `cloud` backend provides four tools through the suite catalog/invoker:

- `cloud__cloud_library_providers`: provider capabilities and official access paths.
- `cloud__cloud_library_search`: token-based, case-insensitive search of ChilaBoard's public catalog; `kind=all|symbol|footprint`, `limit=1..100`, pagination with `offset`, explicit `refresh`.
- `cloud__cloud_library_download`: stage a whole catalog library by `library_path`, optionally enforcing the `revision` returned by search.
- `cloud__cloud_library_stage_local`: stage an already downloaded local SchLib/PcbLib/IntLib/STEP/STP/EPW/ZIP with `provider=snapmagic|samacsys|other`, source URL and optional license note.

The cache root comes from an explicit absolute `cache_dir` argument or configured
`ALTIUM_SUITE_CLOUD_CACHE`. Add that root to the library-file backend's existing
allowed paths during setup so its inspection tools can read the staged files.
The adapter does not initialize Altium or alter a design.

## ChilaBoard

The official [catalog website](https://chilaboard.github.io/Altium-Library/)
loads `DB_Chilaboard_Altium-Library.csv` from the
[upstream repository](https://github.com/chilaboard/Altium-Library). At the
tested revision `c2c77cd22f600a76dbb533d135093c72bd7573e1`, the CSV contains
10,979 data records and uses Windows-1252 encoding. These are catalog records,
not 10,979 independently downloaded CAD files. Its paths point to complete
SchLib/PcbLib libraries, sometimes containing multiple components.

The adapter resolves a commit, downloads its recursive tree, catalog and
LICENSE, and verifies each Git blob identity. Catalogs expire after 24 hours;
`refresh=true` requests a fresh revision. Library downloads verify the pinned
size and Git blob SHA1, calculate SHA256, and retain an adjacent provenance
manifest. Cached files are verified before reuse. An explicit revision mismatch
fails, avoiding a silent substitution after the catalog changes.

The upstream [LICENSE](https://github.com/chilaboard/Altium-Library/blob/main/LICENSE)
declares GPL version 3. The exact license bytes and hash are retained for the
pinned revision. The suite does not bundle or relicense this library collection.
No claim about a finished board's licensing is made by the adapter.

## SnapMagic and Altium Library Loader

[SnapMagic's official API page](https://www.snapeda.com/get-api/) offers access
by request, including Altium formats. This adapter does not assume an undocumented
public endpoint or use browser cookies. The
[official plugin instructions](https://www.snapeda.com/plugins/) describe its
Altium plugin and login flow. Use that authorized workflow and pass downloaded
CAD files to the local staging tool. Direct SnapMagic API automation remains
unimplemented until documented provider access is supplied.

[Altium identifies Library Loader as a third-party SamacSys tool](https://www.altium.com/documentation/knowledge-base/altium-designer/altium-library-loader-support).
The current [SamacSys/Supplyframe page](https://supplyframe.com/samacsys)
links to [Component Search Engine](https://componentsearchengine.com/).
Use the official account/loader workflow, then stage the resulting CAD files.
EPW is retained as original data but is not converted by this adapter; the
official loader handles that conversion. The adapter does not install or run
vendor plugins or change their account settings.

## Staging and verification

Local originals are retained byte-for-byte under a content-addressed directory.
ZIP files extract CAD data and license/notice text only; executable/script files
are never extracted or run. The implementation rejects traversal, absolute paths,
Windows reserved names/alternate streams, duplicate case-insensitive names,
symlinks, encrypted members, unsafe cache redirects, oversized files, and
excessive archive expansion. Downloads use HTTPS to fixed GitHub origins, reject
redirects, and have size, socket-timeout and operation-deadline limits.

A compound-file signature check rejects HTML or Git LFS pointers disguised as
Altium libraries. Its returned status is deliberately
`compound_file_header_verified_only`; it does not certify the full library,
pin mapping, footprint dimensions, or electrical suitability. Continue with the
library backend's read/list/validate tools, compare the exact manufacturer part
and package to the datasheet, check symbol-pin to footprint-pad numbers, and only
then place the selected component into the explicit design project.

## Validation evidence

`python -m pytest tests/test_cloud_libraries.py -q`: **31 passed**. Tests cover
revision/hash enforcement, caching, malformed catalogs, actual CP1252 input,
HTML rejection, ZIP traversal/reserved names/symlinks/case collisions/expansion,
original preservation, executable exclusion, explicit provider limitations,
concurrent immutable publication and interrupted publication cleanup.

Live public tests downloaded and retained without modification:

- `AltiumSCHLIB/conn_header_1.00mm_st.SchLib`, 10,752 bytes,
  SHA256 `fd930039689fddc7198164efe1956572b860e60b86169b31447842de3971d1e9`.
- `AltiumPCBLIB/CONN_HDR_1.00MM_ST_SMDE.PcbLib`, 141,312 bytes,
  SHA256 `2ea516e8f7dde64c2cfd6d05c1f44eced2a9a59816ce51e4fe820816315ee4d1`.

Both use the pinned revision above. The symbol library's compound container was
also parsed independently using `olefile`, showing `FileHeader`,
`HDRTST_1X10/Data` and `Storage` streams. This is container evidence only;
neither part has been certified for production by these checks.

The actual installed Rust MCP executable also read both downloads in a separate
MCP session with an isolated allowlist containing only the test cache. Its
`list_components`, `get_component` and `validate_library` calls passed:
the SchLib contains one symbol (10 pins, one footprint link), and the PcbLib
contains seven footprints. Both validation responses report zero errors and
zero warnings. Source hashes remained unchanged. These samples demonstrate both
file paths are usable by the suite library backend; they are independent
samples, not a verified matching symbol/footprint pair.
