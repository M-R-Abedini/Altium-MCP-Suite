# Component supply intelligence (Free Tier)

The `supply__` tools perform read-only sourcing. They do not buy components,
change a design, grant third-party API access, or certify alternate parts.
Discover their current schemas through `suite_catalog(backend="supply",
with_schema=true)` and invoke the exact returned names with `suite_invoke`.

## Provider support and evidence boundaries

| Tool/provider | Implemented access | Material boundary |
| --- | --- | --- |
| `supply__search_isee_sisoog` | Authorized local JSON catalog search; public exact product URL seller-row parsing | ISee's published robots policy excludes automated keyword/filter and internal API routes. Keyword search without a configured catalog returns `authorized_catalog_required`, not zero stock. |
| `supply__search_jlcpcb_lcsc` | Public JLCPCB HTML/JSON-LD, especially an exact `C25804`-style code | Basic/Extended labels come from the rendered part page. Dynamic quantity, tiers and datasheet fields can remain unknown. LCSC distribution inventory is distinct from JLCPCB assembly inventory. |
| `supply__search_global_distributors` / Nexar | OAuth client credentials or supplied current bearer token, `supSearchMpn` GraphQL | Requires a suitable supply API entitlement; reported lifecycle is attributed to Nexar, not assumed to be directly manufacturer verified. |
| DigiKey | Product Information v4 keyword POST, client ID and current OAuth token | This adapter does not implement an interactive OAuth/account consent flow or refresh tokens. |
| Mouser | Official Search API part-number POST | Search API key required; exact search, up to 50 results, conservative rate limiting. |
| OEMsecrets | Documented HTTPS beta Part Search API | User API key/entitlement required; beta service availability can vary. |

All implemented adapters are available in the Free Tier. Provider fees,
credentials, regional availability and provider-specific quotas are separate.
No private browser cookies or undocumented authenticated endpoints are used.

ISee's own terms require checking the destination seller for current details.
Returned ISee buy links can be the site's public redirect links; the suite
returns these links without following internal `/api/v1` redirects. It keeps
displayed **TOMAN** and **IRR** distinct and never assumes a conversion.

JLCPCB's supported Parts API requires approval through its platform. This
adapter does not guess its account-specific authentication/signing contract.
MPN searches whose HTML contains no structured records report
`dynamic_catalog_unavailable`. An empty JavaScript-rendered result is not
evidence of zero matching parts or zero stock. Exact C-codes are the preferred
public lookup. Public aggregate prices are returned as a range, never invented
quantity price breaks; supplier inconsistencies remain visible in that range.

## Private configuration

Keep configuration in `<PLUGIN_DATA>/config.json`, outside `${PLUGIN_ROOT}`.
The root is `ALTIUM_SUITE_PLUGIN_DATA` when configured, otherwise
`CODEX_HOME/plugins/data/mcp-altium-suite` (default `~/.codex/...`). The module
does not edit this file or print keys. An example configuration shape is:

```json
{
  "supply_chain": {
    "isee_catalog": "C:/Users/example/Sourcing/authorized-isee.json",
    "nexar": {"client_id": "YOUR_ID", "client_secret": "YOUR_SECRET"},
    "digikey": {"client_id": "YOUR_ID", "access_token": "CURRENT_TOKEN"},
    "mouser": {"api_key": "YOUR_SEARCH_KEY"},
    "oemsecrets": {"api_key": "YOUR_KEY"}
  }
}
```

Environment overrides are `NEXAR_ACCESS_TOKEN`, `NEXAR_CLIENT_ID`,
`NEXAR_CLIENT_SECRET`, `DIGIKEY_CLIENT_ID`, `DIGIKEY_ACCESS_TOKEN`,
`MOUSER_API_KEY` and `OEMSECRETS_API_KEY`. Credentials are never accepted as
tool arguments. Protect the data directory with the user's OS access controls;
the JSON file is not an encrypted secret vault. Nexar OAuth tokens obtained by
the adapter remain in memory. Provider errors do not echo request URLs, HTTP
bodies or keys.

An authorized ISee catalog has this explicit snapshot shape:

```json
{
  "observed_at_utc": "2026-10-28T12:00:00Z",
  "results": [{
    "mpn": "AMS1117-3.3", "vendor": "Example seller",
    "stock_count": null, "stock_status": "unknown", "currency": "TOMAN",
    "price_breaks": [], "product_url": "https://example.com/part"
  }]
}
```

Use actual authorized data and observation dates. Local snapshots always
report `live=false`; the adapter does not refresh their prices automatically.

## Caching, latency and failure handling

Successful provider observations are cached for six hours in
`<PLUGIN_DATA>/cache/supply_cache.db`. Cache keys include a nonreversible digest
of the account context and query; secrets are not persisted in cache keys or
payloads. `refresh=true` bypasses cached observations. Responses retain the
observation timestamp and cache age. Failed requests, missing keys, quota
errors and malformed/dynamic pages are not cached as valid empty inventory.

Provider HTTP requests have a 20-second timeout and a 5 MiB response cap.
Requests are spaced by at least 2.1 seconds per provider host within an engine;
429 and transient failures have a three-attempt budget. A long `Retry-After`
returns immediately with a retry-later status. Bulk BOM checking limits
concurrent parts to three; global provider reads can run independently.
The transport accepts injected HTTP, clock, sleep and BOM functions for fully
offline tests. Redirects to a different origin or HTTP downgrade are rejected.

## BOM availability audit

`supply__audit_bom_availability` accepts a canonical `bom` array with `mpn`,
optional `manufacturer`, `quantity`, `reference` and `lcsc_code`. If omitted,
the embedding gateway can supply a read-only active-project `bom_reader`.
An unavailable reader fails explicitly instead of treating an empty BOM as a
passed project. Repeated exact MPN/manufacturer rows are aggregated, with
missing MPN references reported separately.

Checks are grouped by Iranian prototyping, JLCPCB assembly and global markets.
Stock flags require exact MPN/manufacturer matches. A known positive stock
status without a numeric count cannot establish sufficient quantity. Basic
part candidates are exact observed Basic listings; alternate components are
never silently substituted. `ready_for_manufacturing` remains false because
supply checks alone cannot establish electrical suitability, authenticity,
ERC/DRC, lead time or order acceptance.

No provider contract establishes verified pin-compatible alternatives. Supplier
replacement suggestions are marked `pin_compatibility=unverified`; authoritative
pin numbering, package, power, timing and functional checks are required before
any substitution.

## Primary sources checked 2026-10-28

- [ISee robots policy](https://isee.sisoog.com/robots.txt) and
  [seller-information terms](https://isee.sisoog.com/terms_conditions).
- [JLCPCB public parts library](https://jlcpcb.com/parts),
  [sample exact public part](https://jlcpcb.com/partdetail/C25804), and
  [official API access process](https://jlcpcb.com/help/article/jlcpcb-online-api-available-now).
- [Nexar OAuth](https://nexar.freshdesk.com/support/solutions/articles/101000471994-authorization),
  [GraphQL migration/examples](https://support.nexar.com/support/solutions/articles/101000525962-transitioning-from-restful-api-into-graphql-api), and
  [lifecycle attributes](https://support.nexar.com/support/solutions/articles/101000434626-supply-lifecycle-information-for-parts).
- [DigiKey v4 KeywordSearch](https://developer.digikey.com/products/product-information-v4/productsearch/keywordsearch) and
  [official Swagger contract](https://developer.digikey.com/node/2357/oas-download).
- [Mouser Search API](https://www.mouser.com/en/api-search/) and
  [official v1 Swagger](https://api.mouser.com/api/docs/V1).
- [OEMsecrets Part Search contract](https://beta.api.oemsecrets.com/documentation/).

Public ISee product HTML and JLCPCB C25804 HTML were retrieved during local
development. Authenticated global adapters have offline contract tests; no
user API keys or live account calls were available for end-to-end validation.
