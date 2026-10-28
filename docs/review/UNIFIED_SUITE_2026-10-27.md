# Unified Altium Suite

The client connects to one stdio server named **mcp altium suite**, configured
under `mcp-altium-suite`. The existing three backends run as lazy internal
workers. Their native shared mutex, ownership checks, idle release and error
handling remain authoritative. Combining connections does not eliminate
Altium's cold script-start delay or permit simultaneous native writes.

## Discovery and invocation

Minimal mode advertises three tools without starting a worker:

```text
suite_status()
suite_catalog(backend="eda", query="app_context", with_schema=true)
suite_invoke(name="eda__app_context", arguments={})
```

`suite_status` reports configuration/load state; it is not an editor health
probe. Catalog tools use `eda__`, `legacy__`, `libraries__` and `cloud__` names.
Pagination uses `offset`, `limit` and `next_offset`. Check `complete` and
`backend_errors` before concluding a capability is absent. Specify a backend
when known to avoid loading unrelated schemas.

The gateway validates arguments against the discovered original input schema.
It preserves child error flags, structured content, images and metadata. A
dispatched call is not replayed after cancellation. Failure during a native
write still requires inspecting the target before another attempt.

`suite_stdio.py --toolset full` exposes the complete namespaced tool list.
Default minimal mode reduces the initial schema payload, not the number of
available backend operations. Original worker commands and their credentials,
if any, belong only in private local configuration, not reports or source
control. Migrated tool allow/deny lists remain effective at gateway dispatch.

## Cloud libraries

Discover the current schemas rather than copying partial argument examples:

```text
suite_catalog(backend="cloud", query="cloud_library", with_schema=true)
suite_invoke(name="cloud__cloud_library_providers", arguments={})
suite_invoke(name="cloud__cloud_library_search", arguments={"query":"header","limit":5})
```

Select an actual returned `library_path` and `revision`, then invoke
`cloud__cloud_library_download`. A result is a whole original SchLib/PcbLib,
possibly containing many components. It is not a certified individual part.
Downloads record source revision, URL, Git blob hash, SHA256 and retained
license. Use `libraries__list_components`, `libraries__get_component` and
`libraries__validate_library`, then compare pins/pads/package geometry against
the manufacturer's applicable datasheet before adoption.

The [ChilaBoard catalog](https://chilaboard.github.io/Altium-Library/) and its
[source repository](https://github.com/chilaboard/Altium-Library) supply public
metadata and native library downloads. Their retained license applies; a
successful download does not establish all downstream redistribution rights.

[SnapMagic's official plugin](https://www.snapeda.com/plugins/) and
[API access request](https://www.snapeda.com/get-api/) are supported access
paths. [Altium's Library Loader support notice](https://www.altium.com/documentation/knowledge-base/altium-designer/altium-library-loader-support)
identifies the SamacSys integration. This release stages authorized local
native downloads/ZIPs from these services using
`cloud__cloud_library_stage_local`; it does not automate their logins or claim
an undocumented API. EPW conversion requires the official supported loader.
ZIP staging retains original bytes, extracts supported CAD/license data only,
and rejects traversal, unsafe paths, links and excessive expansion.

Set `ALTIUM_SUITE_CLOUD_CACHE` to an absolute staging root, or supply an
explicit cache directory per cloud call. The Rust library worker must allow
that root before it can inspect downloads. `setup.py` adds the generated
suite cache root alongside explicitly requested local library directories.
The Rust backend directly supports SchLib/PcbLib; an IntLib may require native
extraction or conversion before inspection.

## Install and migrate

New installations use `setup.py`; it generates one connection by default and
does not modify client configuration. `deploy.py` now includes the unified
entrypoint/cloud module/skill source and defaults to an installed minimal
stdio smoke test. It preserves private settings and can restore files from
its deployment backup if smoke initialization fails.

Existing Codex installations with all three original local stdio definitions
can use:

```powershell
.\.venv\Scripts\python.exe configure_suite.py --destination "C:\Path\To\InstalledSuite" --cloud-cache "C:\Path\To\SuiteCache"
```

This previews migration. After verifying deployed files, add `--apply`.
Migration stores worker paths/args/env/restrictions in
`suite-backends.local.json`, switches the three old connection tables to one,
and verifies unrelated parsed settings are equal. It refuses preexisting
unified definitions or unsupported configurations instead of overwriting
them. The original config and prior manifest are backed up under the config
directory's `backups/altium-suite-<UTC stamp>-<id>/`.

Only the three original entries are replaced. Reconnect the MCP client to
load the new registry. Do not kill unrelated Python/editor processes to make
the connection reload. Existing in-flight native work must finish first.

For rollback, close the suite connection, restore the exact backed-up
`config.toml` and previous manifest (or remove a newly created manifest), then
reconnect. Deployed-file backups are separate from configuration backups.

Copy `skills/altium-circuit-design` into the client's discoverable skill
directory to install `$altium-circuit-design`. The skill links compact
engineering, cloud and actual tool-workflow references. Validation and
acceptance results for this local installation are recorded in the workspace
report, not inferred from the presence of tools.

The client settings used here follow [official MCP configuration documentation](https://developers.openai.com/codex/mcp/).
