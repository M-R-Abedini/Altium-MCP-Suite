# Cloud sources and local staging

Discover `cloud__cloud_library_providers` first, then current search/download/staging schemas. Distinguish finding metadata, downloading whole libraries and verifying an exact component.

## ChilaBoard

The [catalog](https://chilaboard.github.io/Altium-Library/) uses [chilaboard/Altium-Library](https://github.com/chilaboard/Altium-Library). Search its metadata with `cloud__cloud_library_search`, select returned identifiers, then use `cloud__cloud_library_download` according to the current schema. A hit can reference a complete multi-component SchLib/PcbLib, not an individual verified part.

Retain revision, URL, hashes and manifest in staging; keep originals unchanged. Inspect with `libraries__list_components`, `libraries__get_component` and `libraries__validate_library` before choosing exact symbols/footprints. Structural checks do not compare against datasheets.

The repository declares GPL-3.0. Keep its actual license and record scope; do not infer commercial redistribution rights for every contained third-party artifact. Internal design use, redistributing library sources and distributing hardware are different actions; inspect relevant terms if redistribution is requested.

## SamacSys / Altium Library Loader

Altium's [support notice](https://www.altium.com/documentation/knowledge-base/altium-designer/altium-library-loader-support) identifies this as third-party SamacSys software. [SamacSys](https://supplyframe.com/samacsys) links its component platform. Use official account/plugin/download flows; the suite does not establish a private API or silently manage credentials.

Use `cloud__cloud_library_stage_local` for authorized local native files/ZIPs, then inspect. EPW is not automatically a native Altium library; do not claim conversion unless a supported converter produced inspectable SchLib/PcbLib output. A required provider login or interaction can remain pending while independent design work proceeds.

## SnapMagic / SnapEDA

The [official plugin page](https://www.snapeda.com/plugins/) provides an Altium integration. Its [API page](https://www.snapeda.com/get-api/) requests access, not a public unauthenticated download contract. Use a documented configured API when available; otherwise official web/plugin downloads and local staging. Do not invent endpoints or scrape authenticated sessions.

## Evidence attached to adopted models

Record provider URL, exact MPN/package, date/revision, original artifact path/hash, license/terms status, selected symbol/footprint, datasheet page/revision, checks and unresolved differences. Describe actual states such as downloaded/unverified, structurally checked, pin/package checked and native-placement checked. These are evidence labels, not certifications; provider badges do not replace circuit-specific review.

## Component sourcing alongside library selection

Discover the `supply__` schemas before selecting an orderable part. Use `supply__search_isee_sisoog` for Iranian-market observations and `supply__search_jlcpcb_lcsc` for JLCPCB assembly listings. An exact JLCPCB `Cxxxx` code is more reliable than an MPN search whose public page may require dynamic API access. Distinguish Basic, Extended, promotional Extended and unknown; absent stock counts or price tiers must stay unknown. LCSC distribution stock does not establish JLCPCB assembly stock.

ISee's robots policy restricts automated keyword/filter and internal API routes. The adapter supports an authorized dated local catalog or an exact public product URL; otherwise it returns an explicit search handoff. Keep Toman and IRR labels intact. Recheck the destination seller before purchase. Do not treat an unavailable provider or old local snapshot as an out-of-stock result.

`supply__search_global_distributors` uses configured Nexar, DigiKey, Mouser and OEMsecrets account access. Keep credentials in private configuration/environment, never in tool arguments or design evidence. `supply__audit_bom_availability` aggregates exact MPNs and reports observation gaps, lifecycle warnings and sourced Basic-part candidates. Supplier alternatives remain unverified until authoritative pin/package/electrical checks pass. See [supply adapter details](../../../docs/SUPPLY_CHAIN.md) for configuration, caching and access limits.
