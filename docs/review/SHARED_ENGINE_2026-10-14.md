# Cross-client engine ownership

REL-01 from [the reliability backlog](RELIABILITY_BACKLOG.md).
Validated on Windows / Altium Designer 26.10.1, 2026-10-14.

## Reproduction

Antigravity used one runtime/workspace and Codex used another. Each installation
locked its own `altium-engine.lock` and inspected only its own operation markers.
A second client could therefore miss a native operation already owned by the
first. A file lock's existence was not the defect; the lock identities differed.

The review loop also found a startup side effect: upstream workspace preparation
writes a machine-global pointer before a native command reaches the coordinator.
Foreign-client initialization could repoint a subsequent script startup. Picking
one of two editor processes by foreground window also did not establish which
process answered a file-based health probe or received CLI RunScript dispatch.

## Corrections

- A Windows named mutex in the Local namespace uses the process token's user SID,
  independent of installation path and Runtime. A non-reentrant in-process gate
  prevents two asyncio tasks on one OS thread from recursively entering it. The
  old runtime file lock remains inside this gate for compatibility with clients
  using that same directory. Background traffic still yields immediately.
- A durable editor binding in the Windows token's profile directory records PID,
  process creation time, runtime and workspace. Foreign runtimes are rejected
  before health requests or launch. Python exit, an abandoned mutex, a timeout
  and idle release do not erase it. Process creation time separates PID reuse.
  Confirmed manual Stop recovery archives the current binding explicitly.
- Generated EDA scripts contain their workspace; the generation hash includes
  it. Suite clients default to a private pointer file and do not rewrite the
  ProgramData pointer. Raw upstream scripts retain their legacy fallback.
- Multiple Altium editors are rejected instead of guessing from foreground focus.
  The supported configuration is one editor shared by coordinated clients.
- The existing Antigravity configuration was backed up and aligned with Codex's
  runtime/workspace. Its EDA surface now uses `minimal`; other server entries
  were preserved. No credentials or private client configuration enter Git.

## Verification loop

1. Regression reproduced different-runtime contention and retained a simulated
   unresolved native owner. The conflicting client sent no ping and launched
   no script. Corrupt bindings and invalid identities fail without overwrite.
2. A real Windows subprocess could not enter the gate while a different-runtime
   process held it. Same-thread reentry was refused; acquisition succeeded after
   release. File-open failure did not strand the mutex. Existing cancellation
   tests retain ownership until the non-cancellable worker finishes.
3. Native acceptance used two fresh candidate-source stdio sessions with the
   shared client environment and minimal discovery/schema/invocation. Concurrent
   reads agreed on **272 components, 197 nets and 1041 pads**. A deliberately
   foreign runtime initialized but its native request was rejected; no launch
   record was created and the public workspace pointer retained its bytes.
4. **Three start/context/read/confirmed-Detach cycles passed**, with matching
   native script generations. All **69** inspected project/design/library files
   retained their hashes. No design save, compile, ECO or mutation was issued.

Native version: `2026.10.14.identity1`. Source build passed its lint gate.
Deployment includes `engine_identity.py`; installed stdio initialization must
continue to work without Altium.

## Scope and remaining work

This proves candidate server behavior and minimal MCP discovery, not a Flash
model's behavior inside Antigravity. REL-07 retains that separate acceptance
requirement. Existing Python server processes must reconnect to load the new
gate and environment. Old independently launched scripts/servers that bypass
the coordinated wrappers remain outside this protection. Do not migrate an
unmanaged running script by deleting its ownership files.

Win32 dialog-driving tools intentionally remain available when a native command
is blocked by a modal. The script gate does not serialize arbitrary external UI
changes; it is not a transaction lock for the entire desktop.

The gate serializes suite clients for one Windows user/session conservatively.
It does not support simultaneous editors, other users' sessions, or universal
rollback of native changes. The dirty-flag, partial-operation and Unicode work
remains in REL-02 through REL-04; this change does not claim to resolve it.

References: [Windows mutex ownership](https://learn.microsoft.com/en-us/windows/win32/sync/using-mutex-objects),
[wait/abandoned-mutex semantics](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject),
[token profile directory](https://learn.microsoft.com/en-us/windows/win32/api/userenv/nf-userenv-getuserprofiledirectoryw).
