# Second external review — 2026-10-09

Checked against suite commit `293b9d9` and the subsequent fixes in this review.
The supplied analysis contains useful findings, but its suggested order puts
README corrections ahead of recovery failures. Runtime correctness came first.

| Claim | Assessment and result |
| --- | --- |
| Corrupt legacy marker blocks manual recovery | Confirmed. Normal dispatch still refuses uncertain ownership. After explicit confirmation that the native script stopped, manual reset archives malformed metadata, withdraws pending requests and clears launch state. Marker targets outside the configured runtime/workspace are preserved. |
| Settings JSON crashes startup without useful guidance | Confirmed for configuration discovery. Invalid JSON, non-object data and invalid executable values now identify the file and repair step; UTF-8 BOM is accepted. Errors remain visible instead of silently choosing another editor. |
| Production placeholder assertion disappears under `python -O` | Confirmed. An explicit exception now prevents generation of a malformed legacy script. Regression checks execute optimized Python with missing and duplicate placeholders. |
| Keepalive depends on thread name | Confirmed. Coordination now identifies the bridge's actual keepalive thread. Renaming it keeps nonblocking background behavior; a foreground thread with that name still dispatches normally. |
| Direct pytest cannot import suite modules | Reproduced before the fix. A root-test bootstrap now resolves suite modules for pytest entry points and IDE collection. |
| Non-Windows collection fails on `msvcrt` | Confirmed. Windows locking imports only when used and reports the platform requirement. Native lock/UI tests are explicitly skipped on other platforms; a Linux CI job runs portable root checks. The editor backend remains Windows-only. |
| Offline smoke requires an Altium installation | Confirmed. Standalone and deployment smoke checks now use isolated state and a deliberately nonexistent executable. The standalone smoke runs in Windows CI. Real editor calls still validate the executable. |
| README says 31 lint checks | Confirmed: 30 native entries plus 3 BOM checks; DRC is additional. Updated all four README pages and the stale code comment. |
| README rejects UNC despite upstream transport fix | Confirmed contradiction. The EDA decoder preserves UNC backslashes. Removed the categorical failure claim; actual share access still depends on Windows permissions. No network-share live test was available. |
| Translations omit assembly variants | Confirmed. Persian, Arabic and Chinese now include fitted-state updates, unique identity, matrix export and the same workflow link. |
| Root license/provenance presentation is incomplete | Confirmed. Added explicit scope, file-specific exception, all three license identifiers and existing license paths. Deployment now carries the license texts and EDA notice too. Removed unlabeled file/byte counts; upstream snapshot commits remain unchanged. |
| Modified Apache files lack notices | Confirmed for `altium_bridge.py` and `bridge/payload.py`; notices added, preserving original attribution. Also marked the changed `tools/review.py`. |
| Subprocess/stdio means GPL obligations cannot affect other code | Too categorical. The GNU FAQ considers communication semantics as well as process boundaries. This review does not certify the entire suite as an aggregate or change upstream licensing. |
| One-third of tests are brittle text matches | Some source checks are sensitive to formatting; the numerical estimate was not substantiated. Native source checks still guard important cleanup/order invariants. They complement behavioral and opt-in native checks; no broad rewrite was justified. |
| `%` command interpolation is itself a launch vulnerability | Not established. Launch uses no shell; Windows rejects literal quotes and pipes in valid file names, and both paths must be existing files. Replacing `%` with another interpolator would not validate Altium's process grammar. No speculative launcher rewrite. |
| Zero-assertion publication test is neutral | Incorrect. Successful return is the required behavior: diagnostic write failures must not replace a tool result with an exception. |
| Pillow is unused | Incorrect: legacy server imports and uses `PIL.Image` for screenshots. Dependency retained. |
| Rotating a component to exactly zero is impossible | Incorrect suite-wide: `set_component_position(rotation=0)` and batch placements support it. The legacy `move_components` uses zero to preserve rotation; changing that established default would alter existing calls. |
| GPL source tree is untouched / license texts are present | License texts are present, including Rust's British-spelled `LICENCE`. The Rust source tree was modified by the previous transport fix; its GPL terms remain in force. |
| Old modification title, informal Chinese phrase, missing license links | Corrected alongside the documentation. Badge placement and shorter translated link labels do not affect capabilities. Windows manual Rust checks and Linux CI checks are different evidence, not contradictory results. |

## Validation

Regression checks cover malformed state without replay or launch, preservation
of an external design file, optimized template validation, renamed background
threads, and a foreground thread with the old background name. Offline MCP
smoke uses no editor and verifies initialization plus four connectivity tools.
- Windows root regression: **165 passed**, including direct `pytest` invocation.
- Selected EDA bridge, discovery, connectivity and variant regression: **194 passed**.
- Offline stdio: EDA **447 tools**, legacy **37 tools**; all four connectivity
  checks verified. The existing Rust binary also initialized with **34 tools**;
  that is a startup check, not a rebuilt Rust-source test.
- Deployment initialized both Python entry points from the installed directory
  using an isolated runtime and nonexistent Altium path.
- Altium Designer 26.10.1 live checks passed: `app_context`, `app_ping`,
  `get_all_designators`, `get_all_nets` and a harmless private sandbox snippet.
  SHA-256 hashes of all **13** project/schematic/PCB files were unchanged.
- Linux portable checks and Rust source compilation run in GitHub CI. This
  workstation has no Rust toolchain; no Rust source changed in this review.
