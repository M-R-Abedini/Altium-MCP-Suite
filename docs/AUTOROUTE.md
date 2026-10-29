# Local routing and Altium exchange

`suite_autoroute.py` provides a free local DSN → SES job runner. Nothing in this
module uploads a board, downloads a router, starts Java automatically, or
declares a generated session manufacturing-ready. Freerouting is an external
optional dependency with its own license. Its files are not copied into the
plugin source ZIP by this module.

## Configure a local router

Install a trusted [Freerouting release](https://github.com/freerouting/freerouting/releases).
Its native CLI avoids a separate Java dependency. The JAR alternative requires
a compatible installed Java runtime. Configure **one** in the private suite
data directory's `config.json`; other settings can remain in that same object:

```json
{
  "freerouting": {
    "executable": "C:/EDA/Freerouting/freerouting-cli.exe",
    "sha256": "REPLACE_WITH_THE_64_HEX_SHA256_OF_YOUR_EXECUTABLE"
  }
}
```

For a JAR use `jar` and optionally `java` instead of `executable`. Environment
alternatives are `ALTIUM_SUITE_FREEROUTING_EXE`,
`ALTIUM_SUITE_FREEROUTING_JAR`, and `ALTIUM_SUITE_JAVA`. The optional checksum
checks local executable/JAR identity; it does not certify the executable's
publisher. Do not put command-line flags into these path fields.

Discover the `route` namespace and invoke `route__route_dsn` with an absolute
`input_path`, optional `max_passes` (1–1000; default 10), and
`timeout_seconds` (1–7200; default 600). It copies the explicit DSN into a unique
`<PLUGIN_DATA>/routing/<job-id>/` directory, invokes Freerouting with an argument
array and no shell, disables GUI/API-server/analytics/job saving, and publishes
`result.ses` only on successful process completion with a session envelope.
Adjacent `.rules` files are deliberately not imported implicitly. Configure the
actual routing constraints in the DSN; arbitrary advanced-rule passthrough is
not implemented. The receipt retains checksums and elapsed time. It explicitly
reports `drc_verified: false` and `altium_imported: false`.

## Altium compatibility boundary

[Altium's Specctra documentation](https://www.altium.com/documentation/altium-designer/design-tools-interfacing/specctra-router)
describes DSN export and the export-options dialog. The
[format support table](https://www.altium.com/documentation/altium-designer/design-tools-interfacing)
lists DSN and **RTE** import. Freerouting's
[CLI documentation](https://github.com/freerouting/freerouting/blob/master/docs/command_line_arguments.md)
describes DSN input and SES output. These do not establish direct Altium SES
import. Renaming an SES to RTE does not convert its contents.

Accordingly, automatic `export_specctra_dsn` and `import_specctra_ses` are
capability-gated orchestration methods. The default installation has **no
qualified native exchange adapter**, no guessed `RunProcess` command and no
SES-to-RTE translator. Its status and errors say so before attempting edits.
For now export through Altium **File → Export → Specctra Design**, route that
DSN locally, and use a tested Altium-compatible exchange workflow for any
return import. Direct SES import is unfinished functionality, not a successful
round trip hidden behind a wrapper.

A host can inject an `ExchangeAdapter` that was tested against its installed
Altium version. It must hold the suite's existing cross-process admission and
native mutex throughout exchange and enforce document identity. The service
also checks current bridge/script status, explicit target, no modal/in-flight
command, unchanged saved PCB hash, an existing host-validated checkpoint and
explicit `allow_modal` handling. The host must validate `checkpoint_id`; a
nonempty string alone is not evidence of a real backup.

After an acknowledged import it compares observed track count, via count,
unrouted connection count and total trace length in mils. DRC is certified only
when the adapter supplies a complete, freshly completed DRC run bound to the
same PCB with a timestamp after import and zero violations. Reading zero
previously stored violations does not pass this gate. DelphiScript rollback is
not transactional; never retry a failed import automatically.

`RoutingProvider` is an extension interface. DeepPCB/cloud RL implementations,
credentials, subscriptions and remote transfer are not implemented or enabled.
No free capability is locked behind account registration in this module.

## Validation

Offline tests exercise process failure/timeout/partial output, checksums, input
isolation, modal/wrong/stale-document guards and fresh DRC evidence. These
tests use an injected test adapter and do not establish native Altium exchange.
A separate development smoke can run an actual downloaded, checksum-verified
Freerouting CLI on a public or synthetic DSN; its receipt demonstrates local
DSN → SES only. User projects are not smoke-test fixtures.

The 2026-10-28 Windows smoke used the official native CLI v2.5.0 release ZIP
(published SHA-256 `5750dc0bed261eefae29d79784301b478af978fd472407d77e01e85dbb794857`).
A small two-net fixture produced a session with four wire paths and one via
in approximately 0.30 seconds. This tiny fixture is not a product throughput
benchmark. An initial public fixture containing a keepout exposed an upstream
GraalVM `ObstacleArea` serialization exception in that native binary; the
bounded job timed out and published no session. The native CLI therefore has
known input limitations; the JAR is another adapter option, not a tested fix
for that failure. No Altium import or native DRC was executed in this smoke.
