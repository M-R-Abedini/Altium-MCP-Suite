# Local changes — 2026-10-06

Upstream authorship and license files are retained. `UPSTREAM.json` records the source snapshots imported into this repository. The following changes were made for this distribution by M-R-Abedini:

- `eda-agent/src/eda_agent/bridge/process_manager.py`: select the visible Altium editor instead of the first transient X2 process.
- `eda-agent/src/eda_agent/ui/windows.py`: import the ctypes names used by mouse operations.
- `eda-agent/scripts/altium/{Main,StatusForm,Dispatcher}.pas`: hide the monitor on X; keep explicit Detach; log stop reasons; local script version `2026.10.06.local1`.
- `coffeenmusic/server/main.py`: support current FastMCP initialization; direct subprocess launch without a shell; stderr diagnostics; noninteractive configuration validation; atomic requests; complete JSON response polling without content rewriting or command replay; request/response IDs; per-user exchange path.
- `coffeenmusic/server/AltiumScript/Altium_API.pas`: exchange-directory template populated in generated runtime scripts; echo the request ID in responses.
- New root configuration, setup and coordination modules: shared engine lock, guarded recovery, restored EDA control after legacy calls, portable per-user runtime, content-addressed script projects, regression tests and setup documentation.

The Rust library-server source is preserved from its recorded upstream snapshot. No claim is made that every tool or Altium version has been exercised. Original upstream development examples and test fixtures are retained as upstream material; their machine-specific example paths are not this project's local configuration.
