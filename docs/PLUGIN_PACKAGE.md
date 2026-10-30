# Codex plugin packaging and setup

MCP Altium Suite is a local Windows plugin that bundles one MCP connection,
the circuit-design skill and a setup/diagnosis skill. The canonical source is
this repository. An installed cache and a generated ZIP are deployment
artifacts, not separate copies to develop.

The package follows the [official plugin packaging format](https://developers.openai.com/plugins/build/plugins):
root `plugin.json`, root `mcp.json` and the maintained `skills/` directory.
The MCP launcher uses the host's `${PLUGIN_ROOT}` placeholder to locate its
installed files. Altium requires local execution, as described in the
[desktop plugin guidance](https://developers.openai.com/plugins/deploy/desktop-only).

## What the package includes

- `mcp-altium-suite`: one endpoint, displayed as **mcp altium suite**. Once
  configured, minimal mode advertises `suite_catalog`, `suite_invoke` and
  `suite_status`. Namespaced backend tools are discovered on demand.
- `altium-circuit-design`: requirements, sourced parts, symbol/footprint
  checks, structured schematic execution and connectivity evidence.
- `altium-suite-setup`: explicit onboarding, adopting an existing suite or
  preparing a new runtime, and diagnosis.
- Python and DelphiScript sources, independent native library codecs,
  compatibility contracts and runtime dependency requirements with license notices.

The ZIP does not include Altium Designer, a Python interpreter, private
settings, account credentials or design files. Altium and its license remain
separate prerequisites. Use Python 3.12 on Windows. Library operations execute
in the gateway through the original Python OLE2 codecs; no external library
executable, Rust compiler or crate sources are required.

## Build a clean distribution

Run from the repository root with the development environment prepared:

```powershell
.\.venv\Scripts\python.exe scripts/build_plugin.py --output dist --smoke --check-reproducible
```

The builder creates:

```text
dist/
  mcp-altium-suite-0.4.0.zip
  artifacts.json
  marketplace/
    .agents/plugins/marketplace.json
    plugins/mcp-altium-suite/
      plugin.json
      mcp.json
      skills/
      scripts/
      ... bundled runtime sources and notices
```

`--smoke` exercises the staged package's MCP initialization, minimal tool
surface and cloud provider discovery using an isolated empty backend
configuration. It does not launch Altium, read private machine settings or
require an external library executable. `--check-reproducible` compares repeated builds with
the same package inputs. The artifact manifest records the selected files
and hashes. Do not distribute a recursive copy of an installed checkout:
it may contain local configurations and repair backups.

## Install the built plugin in Codex

The following commands use the supported
[Codex plugin CLI](https://learn.chatgpt.com/docs/developer-commands). Run from
the repository root; use an absolute marketplace path if working elsewhere:

```powershell
codex plugin marketplace add .\dist\marketplace --json
$installedPlugin = codex plugin add mcp-altium-suite@altium-mcp-suite --json | ConvertFrom-Json
$pluginRoot = $installedPlugin.installedPath
```

Keep that marketplace folder available for local updates. `codex plugin list
--json` reports actual installation and enabled state; adding a marketplace
alone does not establish plugin installation. Start a new Codex session, or
reconnect the desktop client, to load the plugin's skills and MCP tools.

A Git marketplace can be used when the chosen Git ref actually contains the
plugin and marketplace manifests. Do not assume that an unmerged local
change already exists on GitHub's default branch. GitHub/local marketplace
distribution is separate from publication in OpenAI's public directory.

## Configure the runtime once

Run the bundled onboarding skill, `$altium-suite-setup`, or use the commands
below. The examples assume the Windows Python launcher can select Python
3.12; an explicit path to that interpreter is also valid.

Before configuration, the launcher exposes a setup-needed `suite_status`
response. That is a usable diagnosis path, not a claim that the native editor
is ready. Normal serving does not install dependencies or launch a second
Altium instance.

### Adopt an existing suite

Use the existing explicit backend manifest and an environment that already
has the runtime dependencies:

```powershell
py -3.12 "$pluginRoot\scripts\plugin_runtime.py" adopt --backend-config "C:\Path\To\suite-backends.local.json" --python "C:\Path\To\.venv\Scripts\python.exe" --altium-exe "C:\Program Files\Altium\AD26\X2.EXE"
py -3.12 "$pluginRoot\scripts\plugin_runtime.py" doctor
```

Adoption retains the selected native runtime/workspace identity, library
configuration, restrictions and explicit timeouts. The retired executable
path is ignored during migration. It does not
edit designs. EDA worker and in-process legacy compatibility code resolve against the current
installed plugin on each launch, so upgrading the package upgrades its code.
Private state stays outside the versioned cache.

Adoption can still depend on the selected old interpreter.
To make dependencies independent of the old checkout while retaining the same
native workspace, run `py -3.12 "$pluginRoot\scripts\plugin_runtime.py" detach`.
This creates a private regular-wheel Python environment and private library
configuration before switching state, retaining the old files for
recovery. See [multi-client and installer setup](INSTALLER.md).

Before switching an existing client, retain its configuration backup and
verify the plugin's doctor and protocol checks. Then disable the old manual
suite connection so the client exposes one intended connection. Do not
clear native ownership markers or move an open editor into another workspace
as a shortcut to setup.

### Prepare a new machine

Supply the installed Altium executable and intended library roots:

```powershell
py -3.12 "$pluginRoot\scripts\plugin_runtime.py" bootstrap --altium-exe "C:\Program Files\Altium\AD26\X2.EXE" --library-dir "D:\MyProject\Libraries"
py -3.12 "$pluginRoot\scripts\plugin_runtime.py" doctor
```

Repeat `--library-dir` for intended existing library roots. Bootstrap explicitly creates a private Python environment
and installs `requirements-runtime.txt` once. It does not install the
development test/render/dashboard extras or run package installation on
ordinary MCP calls.

The normal data directory is `CODEX_HOME/plugins/data/mcp-altium-suite`,
using `~/.codex` when `CODEX_HOME` is unset. An absolute
`ALTIUM_SUITE_PLUGIN_DATA` or `--data-dir` can select another location. Global
options precede the operation:

```powershell
py -3.12 "$pluginRoot\scripts\plugin_runtime.py" --data-dir "D:\AltiumSuiteData" doctor
```

Use the same selection for setup and launch. The launcher accepts `-DataDir`
for an explicit process invocation; plugin-configured launches normally use
the default or environment setting. `serve` is the internal gateway command,
not another setup operation. State reconfiguration keeps recoverable prior
state, and an incomplete state/template pair fails diagnosis instead of
guessing a different native runtime.

## Verify the configured plugin

After reconnecting, discover the exact live schema and invoke the names
returned by the catalog:

```text
suite_status()
suite_catalog(backend="eda", query="app_context", with_schema=true)
suite_invoke(name="eda__app_context", arguments={})
suite_catalog(backend="libraries", query="list_components", with_schema=true)
```

`suite_status` and doctor report configuration/load state. A successful
native `app_context` call with an open Altium editor supplies separate bridge
evidence. Use an explicit permitted sample library to exercise its reader.
Setup tests do not prove schematic correctness, a fresh ERC/DRC or tested
hardware. Native design mutations belong in an explicit test fixture or
intended project, with the existing ownership and target-save safeguards.

Cloud search/download and local download staging are documented in
[cloud libraries](review/CLOUD_LIBRARIES_2026-10-27.md). ChilaBoard has an
automated public adapter. SnapMagic/SamacSys official account/API access and
EPW conversion are not supplied by plugin installation. Downloaded models
still require exact part/package and pin-to-pad verification.

## Updates and source maintenance

Edit this repository's sources and `skills/`, run the tests, then rebuild the
clean package. Reinstall/refresh the built local marketplace version and
reconnect; do not edit the installed cache. Preserve private runtime state
and native workspace identity through an upgrade. A changed code package
does not authorize overwriting user designs or library roots.

Treat a distributed version as immutable. Increase `plugin.json`'s version for
a new release, rebuild and retain its ZIP checksum. For a Git marketplace,
select the published revision explicitly when reproducibility matters. Local
development rebuilds of the same version are test artifacts, not a new release.

The suite's existing [license scope](../THIRD_PARTY_NOTICES.md) remains in
effect. Original suite code, vendored backends and their dependencies retain
their own terms. The builder includes MIT and Apache-2.0 notices and rejects retired binary,
crate and Rust-source artifacts. Historical release tags keep their original
contents and licensing; they are not rewritten by this source update.

See [CONTRIBUTING](../CONTRIBUTING.md) for the source checks, package tests and
native-test boundaries. Current validation evidence should distinguish
offline protocol tests, clean artifact tests, actual plugin registration and
native Altium checks.
