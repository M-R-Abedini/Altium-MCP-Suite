---
name: altium-suite-setup
description: Set up or diagnose the local MCP Altium Suite plugin on Windows, adopting an existing suite or preparing its Python and library workers without changing design files.
---

# Set up MCP Altium Suite

Use this installed plugin's files, not a remembered checkout or cache-version path. Resolve the plugin root from this skill's location (two parents above `altium-suite-setup`) and read [the setup commands](../../docs/PLUGIN_PACKAGE.md). Keep dependencies and configuration in the writable per-user data directory, outside the installed plugin cache.

Run `scripts/plugin_runtime.py doctor` with an available Python 3.12 interpreter. Before configuration the bundled MCP deliberately exposes only `suite_status`; that response points to the setup script and reports the missing configuration. This is not evidence that Altium's native bridge is ready.

If a suite is already configured, inspect its explicit backend manifest and selected Python. Prefer `adopt --backend-config <file> --python <interpreter>`: it preserves the engine workspace, runtime, library executable/configuration, tool restrictions and explicit timeouts. The helper resolves the existing Altium executable from its selected settings or accepts `--altium-exe`. Do not move a live editor into a second runtime or clear ownership markers to make setup succeed.

For a new machine use `bootstrap --altium-exe <X2.EXE> --library-binary <existing Rust library executable>` and repeat `--library-dir` for intended library roots. Alternatively `--build-libraries` builds the bundled Rust source and requires its pinned toolchain and Windows C++ build prerequisites. Bootstrap creates a private Python environment and installs the packaged runtime requirements once. Altium Designer itself is a separate prerequisite and is not included. Select an existing intended installation before requesting missing paths.

Setup authorization permits the configuration/dependency work requested; do not add separate permission prompts for its routine steps. Package registration, adoption and doctor do not edit or save designs. Preserve a recoverable prior configuration, keep other client settings, and avoid leaving both a standalone suite connection and the plugin connection enabled. A production circuit is not a setup test fixture.

After setup, reconnect the plugin and verify its actual tools: discover `eda__app_context` through `suite_catalog`, then use `suite_invoke` for a read-only native check if Altium is open and that check is in scope. Test a library reader on an explicit permitted sample. Report which checks actually ran and any unavailable prerequisites; do not infer full readiness from installation or metadata alone.

If adoption refers to an obsolete checkout, use `detach` to build independent
dependencies in the private data directory before archiving the old folder.
Retain the native runtime/workspace identity and tool restrictions. Setup has
`--client` support for other AI clients; configure only clients requested by the
user and preserve unrelated settings. See [installer/client support](../../docs/INSTALLER.md).

The free core requires no account. Optional structural datasets default off;
do not enable them as a setup side effect. For checkpoints and engineering unit
normalization read [native edit safeguards](../../docs/PRODUCT_SAFETY.md).
For router setup read [native exchange limitations](../../docs/AUTOROUTE.md):
a generated SES is not proof of Altium import, complete routing or clean DRC.

For circuit work use the bundled `altium-circuit-design` skill. Library provider accounts/API access are separate from plugin installation. Normal plugin startup never installs packages, launches Altium, or rewrites the chosen native workspace.
