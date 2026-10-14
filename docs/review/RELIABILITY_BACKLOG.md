# Reliability backlog

Prioritized on 2026-10-14 after the PCB-transfer review. Confirmed defects,
platform limitations and untested paths are tracked separately. Hardware design
changes are outside this backlog.

| ID | Priority | Evidence / scope | Next acceptance criterion | Status |
| --- | --- | --- | --- | --- |
| REL-01 | P1 | Antigravity and Codex used different runtimes, hence different file locks and native ownership markers for one Altium engine. | Different-runtime clients serialize through one Windows lock; a client cannot bypass another runtime's unresolved ownership. Match local configs and test restart/read/Detach. | Fixed; [native and regression evidence](SHARED_ENGINE_2026-10-14.md). Existing clients must reconnect. |
| REL-02 | P1 | AD26 Modified flag can be false for unsaved edits; context detection is explicitly incomplete. | Project save verifies document content persistence beyond membership/variant metadata, with scratch unsaved edits and library exclusion. | Open |
| REL-03 | P1 | Native faults/timeouts can leave partial changes. No general atomic rollback exists; current wrappers retain ownership and prohibit replay. | Record affected targets and partial/unknown outcomes; validate one bounded operation's recovery before extending it. Do not label all native commands crash-free. | Open, platform + API scope |
| REL-04 | P2 | JSON transport rejects characters above U+00FF; full Unicode is absent. | Native round-trip of Persian, Chinese and Ω without replacement, including file paths and existing readback. | Open, capability gap |
| REL-05 | P2 | Exact ECO evidence export exists; live UIA GridPattern extraction remains unverified. | Compare exported evidence with an actual scratch ECO dialog; unreadable data stays incomplete. | Native acceptance pending |
| REL-06 | P2 | Duplicate model-link replacement, library-save opt-in and selection zoom have no full native acceptance yet. | Exercise each branch on disposable fixtures and verify saved/read-back results. These are not confirmed bugs merely because they lack acceptance. | Native acceptance pending |
| REL-07 | P2 | Antigravity points to updated coordinated entry points; minimal mode is now configured and tested through independent stdio. Its Flash-model workflow has not been tested inside the client. | Validate model-driven discovery and scratch actions in Antigravity. Independent stdio tests alone do not establish model/client acceptance. | Configuration fixed; Flash-client acceptance pending |
| REL-08 | P2 | Multi-client lock competition had no admission order; native-independent review work should not occupy the script engine. Large successful replies also delayed the following health request in dispatcher diagnostics. | FIFO foreground admissions, cancelled queue removal, native ownership retained on service failure, one-pass physical snapshots and concurrent offline readback. | Implemented; [native acceptance and measured limits](CONCURRENT_CLIENTS_2026-10-14.md). Clients must reconnect. |

Review loop: reproduce → isolate → fix → regression tests → native scratch or
read-only check → update evidence. An unavailable acceptance surface stays
pending; it is not reported as passed. Stop after a completed priority tranche
rather than mixing a transport rewrite into unrelated native changes.
