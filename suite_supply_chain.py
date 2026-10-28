"""Read-only component sourcing with explicit provenance and unknown states.

Public HTML is parsed as data, never executed. Distributor keys live only in
the user data directory/environment, not in the plugin or MCP arguments.
SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import html
from html.parser import HTMLParser
import inspect
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

import jsonschema

TTL_SECONDS = 6 * 60 * 60
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
PROVIDERS = ("nexar", "digikey", "mouser", "oemsecrets")
HOSTS = {"isee.sisoog.com", "jlcpcb.com", "www.lcsc.com", "api.nexar.com",
         "identity.nexar.com", "api.digikey.com", "api.mouser.com", "beta.api.oemsecrets.com"}
ENV_KEYS = {
    "nexar": {"access_token": "NEXAR_ACCESS_TOKEN", "client_id": "NEXAR_CLIENT_ID", "client_secret": "NEXAR_CLIENT_SECRET"},
    "digikey": {"access_token": "DIGIKEY_ACCESS_TOKEN", "client_id": "DIGIKEY_CLIENT_ID"},
    "mouser": {"api_key": "MOUSER_API_KEY"},
    "oemsecrets": {"api_key": "OEMSECRETS_API_KEY"},
}


def list_tools() -> list[dict[str, Any]]:
    """Return portable tool schemas; the gateway adds the supply__ namespace."""
    common = {"query": {"type": "string", "minLength": 3, "maxLength": 75},
              "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
              "refresh": {"type": "boolean", "default": False}}
    definitions = [
        ("search_isee_sisoog", "Search an authorized local ISee catalog or inspect a public ISee product URL. Automated keyword search is restricted by ISee robots policy; unknown is never out-of-stock.",
         {**common, "product_url": {"type": "string", "maxLength": 2048}}, ["query"]),
        ("search_jlcpcb_lcsc", "Inspect public JLCPCB/LCSC catalog data by MPN or C-code. Returns sourced Basic/Extended classification and available stock/prices; absent dynamic fields remain unknown.", common, ["query"]),
        ("search_global_distributors", "Search configured Nexar, DigiKey, Mouser and OEMsecrets read-only APIs. Keys come from private config/environment. Alternatives remain unverified candidates.",
         {**common, "providers": {"type": "array", "uniqueItems": True, "minItems": 1,
                                  "items": {"type": "string", "enum": list(PROVIDERS)}}}, ["query"]),
        ("audit_bom_availability", "Audit unique MPNs from an explicit BOM or the configured read-only active-project reader against selected markets. Unknown/missing credentials do not establish shortage or manufacturing readiness.",
         {"bom": {"type": "array", "maxItems": 500, "items": {"type": "object", "properties": {
             "mpn": {"type": "string", "maxLength": 75}, "manufacturer": {"type": "string", "maxLength": 200},
             "quantity": {"type": "integer", "minimum": 1, "maximum": 100000000},
             "reference": {"type": "string", "maxLength": 200}, "lcsc_code": {"type": "string", "pattern": "^C[0-9]+$"}},
             "additionalProperties": False}},
          "markets": {"type": "array", "uniqueItems": True, "minItems": 1,
                      "items": {"type": "string", "enum": ["iran", "jlcpcb", "global"]}},
          "refresh": {"type": "boolean", "default": False}}, []),
    ]
    return [{"name": name, "description": description,
             "inputSchema": {"type": "object", "properties": props, "required": required, "additionalProperties": False},
             "annotations": {"readOnlyHint": True, "destructiveHint": False,
                             "idempotentHint": True, "openWorldHint": True}}
            for name, description, props, required in definitions]


def data_directory() -> Path:
    """Use the persistent plugin data root, never a package-relative cache."""
    explicit = os.environ.get("ALTIUM_SUITE_PLUGIN_DATA")
    root = Path(explicit) if explicit else Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "plugins/data/mcp-altium-suite"
    if not root.is_absolute():
        raise ValueError("Supply data directory must be absolute")
    return root.resolve()


def timestamp(seconds: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if seconds is None else seconds, timezone.utc).isoformat()


def safe_url(value: Any, base: str = "") -> str | None:
    """Return display-only HTTP(S) URLs, excluding embedded credentials."""
    if not isinstance(value, str):
        return None
    parsed = urllib.parse.urlsplit(urllib.parse.urljoin(base, html.unescape(value)))
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    return urllib.parse.urlunsplit(parsed)


def number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if value >= 0 and value < float("inf") else None
    if not isinstance(value, str):
        return None
    value = value.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    match = re.fullmatch(r"\s*([$€£¥]?)[\s]*([0-9]+(?:[,٬][0-9]{3})*(?:\.[0-9]+)?)\s*", value)
    if not match:
        return None
    text = match[2].replace(",", "").replace("٬", "")
    return float(text) if "." in text else int(text)


def lifecycle(raw: Any) -> str:
    value = re.sub(r"\s*\(last updated:.*\)\s*$", "", str(raw or "").strip(), flags=re.I).casefold()
    if value in {"active", "production", "in production"}:
        return "Active"
    if "not recommended" in value or value == "nrnd":
        return "NRND"
    if "obsolete" in value or "discontinued" in value:
        return "Obsolete"
    if value in {"eol", "end of life", "last time buy"} or value.startswith("eol ("):
        return "EOL"
    return "Unknown"


def offer(provider: str, mpn: Any, **fields: Any) -> dict[str, Any]:
    row = {"provider": provider, "mpn": str(mpn or ""), "manufacturer": None,
           "vendor": provider, "stock_count": None, "stock_status": "unknown", "price_breaks": [],
           "currency": None, "package": None, "datasheet_url": None, "product_url": None,
           "lifecycle": "Unknown", "lifecycle_raw": None, "assembly_class": "unknown",
           "alternatives": [], "pin_compatibility": "unverified"}
    row.update(fields)
    if row["stock_count"] is not None:
        row["stock_status"] = "in_stock" if row["stock_count"] > 0 else "out_of_stock"
    return row


class Page(HTMLParser):
    """Minimal inert HTML reader for JSON-LD, visible labels and source links."""
    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.products: list[dict] = []
        self.visible: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._script = False
        self._jsonld = False
        self._style = False
        self._buffer: list[str] = []
        self._link: tuple[str, list[str]] | None = None
        self.feed(source)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script":
            self._script = True
            self._jsonld = values.get("type", "").lower() == "application/ld+json"
            self._buffer = []
        if tag == "style":
            self._style = True
        if tag == "a":
            self._link = (values.get("href") or "", [])

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            if self._jsonld:
                try:
                    self._collect(json.loads("".join(self._buffer)))
                except (ValueError, TypeError):
                    pass
            self._script = self._jsonld = False
        if tag == "style":
            self._style = False
        if tag == "a" and self._link:
            self.links.append((self._link[0], " ".join(self._link[1])))
            self._link = None

    def handle_data(self, data: str) -> None:
        if self._script:
            if self._jsonld:
                self._buffer.append(data)
        elif not self._style and data.strip():
            self.visible.append(" ".join(data.split()))
            if self._link:
                self._link[1].append(data.strip())

    def _collect(self, value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                self._collect(item)
        elif isinstance(value, dict):
            kind = value.get("@type", "")
            if kind == "Product" or isinstance(kind, list) and "Product" in kind:
                self.products.append(value)
            self._collect(value.get("@graph", []))
            if value.get("@type") == "ItemList":
                for item in value.get("itemListElement", []):
                    self._collect(item.get("item", item) if isinstance(item, dict) else item)


def parse_jlcpcb(source: str, url: str) -> list[dict[str, Any]]:
    """Parse public catalog data; do not derive stock/tier counts from prices."""
    page = Page(source)
    rows = []
    for product in page.products:
        code = str(product.get("sku", ""))
        if not re.fullmatch(r"C\d+", code):
            continue
        brand = product.get("brand") or {}
        schema_offer = product.get("offers") or {}
        schema_offer = schema_offer[0] if isinstance(schema_offer, list) and schema_offer else schema_offer
        availability = str(schema_offer.get("availability", "")).rsplit("/", 1)[-1]
        stock_status = {"InStock": "in_stock", "OutOfStock": "out_of_stock", "Discontinued": "unavailable"}.get(availability, "unknown")
        assembly = "unknown"
        # Only standalone rendered labels near this exact MPN, not translated
        # navigation such as "Basic/Promotional Extended Parts".
        for index, text in enumerate(page.visible):
            if text == str(product.get("mpn", product.get("name"))):
                neighbors = page.visible[max(0, index - 1):index + 3]
                for candidate in ("Basic", "Extended", "Promotional Extended"):
                    if candidate in neighbors:
                        assembly = candidate.lower().replace(" ", "_")
        package = None
        for index, text in enumerate(page.visible[:-1]):
            if text == "Package":
                package = page.visible[index + 1]
                break
        datasheet = next((safe_url(href, url) for href, title in page.links
                          if "datasheet" in title.lower() or title == "Download" and ".pdf" in href.lower()), None)
        prices = []
        if number(schema_offer.get("price")) is not None:
            prices = [{"quantity": 1, "unit_price": number(schema_offer["price"]), "currency": schema_offer.get("priceCurrency")}]
        rows.append(offer("jlcpcb", product.get("mpn", product.get("name")), lcsc_code=code,
                          manufacturer=brand.get("name") if isinstance(brand, dict) else str(brand),
                          stock_status=stock_status, assembly_class=assembly, package=package,
                          currency=schema_offer.get("priceCurrency"), price_breaks=prices,
                          price_range={"low": number(schema_offer.get("lowPrice")), "high": number(schema_offer.get("highPrice"))},
                          product_url="https://jlcpcb.com/partdetail/" + code, datasheet_url=datasheet,
                          evidence="public_html_jsonld", dynamic_fields_complete=False))
    return rows


def parse_isee(source: str, url: str) -> list[dict[str, Any]]:
    """Extract displayed seller rows, preserving Toman vs IRR explicitly."""
    page = Page(source)
    name = page.products[0].get("mpn", page.products[0].get("name", "")) if page.products else ""
    rows, seen = [], set()
    def element(block: str, css: str) -> str:
        found = re.search(r'<(?:span|a)\b[^>]*class=["\'][^"\']*\b' + re.escape(css) + r'\b[^"\']*["\'][^>]*>(.*?)</(?:span|a)>', block, re.S | re.I)
        return " ".join(Page(found[1]).visible) if found else ""
    for block in re.findall(r'<tr\b[^>]*class=["\'][^"\']*seller-table__content[^"\']*["\'][^>]*>.*?</tr>', source, re.S | re.I):
        vendor = element(block, "seller-name")
        title = element(block, "seller-code") or name
        price_text = element(block, "seller-price")
        currency = "TOMAN" if "تومان" in price_text else "IRR" if "ریال" in price_text else None
        price = number(price_text.replace("تومان", "").replace("ریال", "").strip()) if currency else None
        status = "out_of_stock" if "seller-not-available" in block else "in_stock" if 'seller-available' in block else "unknown"
        link = re.search(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*class=["\'][^"\']*seller-buy-btn', block, re.S | re.I)
        product_url = safe_url(link[1], url) if link else url
        key = (vendor, title, product_url)
        if key in seen:
            continue
        seen.add(key)
        rows.append(offer("isee_sisoog", title, vendor=vendor or None, stock_status=status,
                          currency=currency, price_breaks=[{"quantity": 1, "unit_price": price, "currency": currency}] if price is not None else [],
                          product_url=product_url, source_url=url,
                          price_stale_warning=bool(element(block, "seller-outOfDateLabel")), evidence="public_html_seller_row"))
    return rows


@dataclass
class Response:
    body: bytes
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)


class ProviderError(RuntimeError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


class _RestrictedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        # Never forward credentials to a different origin or downgrade HTTPS.
        if parsed.scheme != "https" or parsed.netloc != urllib.parse.urlsplit(req.full_url).netloc:
            raise ProviderError("redirect_rejected", "Provider redirected to a different origin")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_request(method: str, url: str, headers: dict, body: bytes | None) -> Response:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.build_opener(_RestrictedRedirect()).open(req, timeout=20) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise ProviderError("response_too_large", "Provider response exceeds the 5 MiB limit")
            return Response(data, response.status, dict(response.headers))
    except urllib.error.HTTPError as error:
        # Do not expose request URL, provider response or API keys on errors.
        return Response(b"", error.code, dict(error.headers))
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProviderError("network_unavailable", "Provider network request failed") from None


class SupplyChain:
    """Persistent read-only provider engine; HTTP/BOM/clock are injectable."""
    def __init__(self, data_dir: Path | str | None = None, *, http: Callable = http_request,
                 bom_reader: Callable | None = None, clock: Callable[[], float] = time.time,
                 sleeper: Callable = asyncio.sleep):
        if data_dir is not None and not Path(data_dir).is_absolute():
            raise ValueError("Supply data directory must be absolute")
        self.data_dir = Path(data_dir).resolve() if data_dir is not None else data_directory()
        self.http = http
        self.bom_reader = bom_reader
        self.clock = clock
        self.sleeper = sleeper
        self._rate_lock = threading.Lock()
        self._next_request: dict[str, float] = {}
        self._tokens: dict[str, tuple[str, float]] = {}
        self._validators = {tool["name"]: jsonschema.Draft202012Validator(tool["inputSchema"]) for tool in list_tools()}

    @staticmethod
    def list_tools() -> list[dict[str, Any]]:
        return list_tools()

    def _config(self) -> dict[str, Any]:
        path = self.data_dir / "config.json"
        if not path.exists():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        value = raw.get("supply_chain", {}) if isinstance(raw, dict) else None
        if not isinstance(value, dict):
            raise ValueError("Private config supply_chain must be an object")
        return value

    def _credentials(self, provider: str) -> dict[str, str]:
        raw = self._config().get(provider, {})
        if not isinstance(raw, dict):
            raise ValueError("Provider configuration must be an object")
        return {key: str(os.environ.get(env) or raw.get(key) or "") for key, env in ENV_KEYS[provider].items()}

    def _database(self) -> sqlite3.Connection:
        cache = self.data_dir / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(cache / "supply_cache.db", timeout=10)
        db.execute("CREATE TABLE IF NOT EXISTS searches (key TEXT PRIMARY KEY, fetched REAL NOT NULL, payload TEXT NOT NULL)")
        return db

    def _cache_get(self, key: str) -> dict | None:
        with closing(self._database()) as db:
            found = db.execute("SELECT fetched,payload FROM searches WHERE key=?", (key,)).fetchone()
        if found and 0 <= self.clock() - found[0] < TTL_SECONDS:
            value = json.loads(found[1])
            value["cache"] = {"hit": True, "age_seconds": self.clock() - found[0], "ttl_seconds": TTL_SECONDS}
            return value
        return None

    def _cache_put(self, key: str, payload: dict) -> None:
        with closing(self._database()) as db, db:
            db.execute("INSERT OR REPLACE INTO searches VALUES (?,?,?)", (key, self.clock(), json.dumps(payload, ensure_ascii=False)))

    async def _request(self, method: str, url: str, *, data: dict | None = None,
                       headers: dict | None = None, form: dict | None = None) -> Any:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in HOSTS or parsed.port not in {None, 443} or parsed.username or parsed.password:
            raise ValueError("Request is outside supported HTTPS provider origins")
        hdr = {"User-Agent": "Altium-MCP-Suite/0.3 sourcing (read-only)", "Accept": "application/json,text/html"}
        hdr.update(headers or {})
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            hdr["Content-Type"] = "application/json"
        if form is not None:
            body = urllib.parse.urlencode(form).encode()
            hdr["Content-Type"] = "application/x-www-form-urlencoded"
        for attempt in range(3):
            with self._rate_lock:
                now = time.monotonic()
                # Mouser's public limit is 30/minute; other public sources use
                # the same conservative ceiling. Retries consume a slot.
                slot = max(now, self._next_request.get(parsed.hostname, now))
                self._next_request[parsed.hostname] = slot + 2.1
            if slot > now:
                await self.sleeper(slot - now)
            try:
                response = await self.http(method, url, hdr, body) if inspect.iscoroutinefunction(self.http) else await asyncio.to_thread(self.http, method, url, hdr, body)
            except ProviderError as error:
                if error.status != "network_unavailable" or attempt == 2:
                    raise
                await self.sleeper(0.5 * (2 ** attempt))
                continue
            if len(response.body) > MAX_RESPONSE_BYTES:
                raise ProviderError("response_too_large", "Provider response exceeds limit")
            if response.status == 200:
                content_type = response.headers.get("Content-Type", response.headers.get("content-type", ""))
                text = response.body.decode("utf-8-sig", "replace")
                if "json" in content_type or text.lstrip().startswith(("{", "[")):
                    try:
                        return json.loads(text)
                    except ValueError:
                        raise ProviderError("invalid_response", "Provider returned invalid JSON") from None
                return text
            if response.status in {429, 500, 502, 503, 504} and attempt < 2:
                retry = response.headers.get("Retry-After", "")
                # Long quota waits require a later request, not a blocked MCP.
                if retry.isdigit() and int(retry) > 10:
                    raise ProviderError("rate_limited", "Provider quota exhausted; retry later")
                await self.sleeper(min(10, int(retry)) if retry.isdigit() else .5 * (2 ** attempt))
                continue
            status = "credentials_rejected" if response.status in {401, 403} else "rate_limited" if response.status == 429 else "provider_unavailable"
            raise ProviderError(status, f"Provider HTTP {response.status}; availability remains unknown")
        raise ProviderError("provider_unavailable", "Provider request did not complete")

    async def _cached(self, provider: str, query: str, loader: Callable, refresh: bool, identity: dict | None = None) -> dict:
        scope = hashlib.sha256(json.dumps(identity or {}, sort_keys=True).encode()).hexdigest()
        key = hashlib.sha256(json.dumps([provider, query.casefold(), scope]).encode()).hexdigest()
        if not refresh:
            existing = await asyncio.to_thread(self._cache_get, key)
            if existing is not None:
                return existing
        try:
            rows, notes = await loader()
            payload = {"provider": provider, "query": query, "status": "ok" if rows else "no_results",
                       "results": rows, "notes": notes, "observed_at_utc": timestamp(self.clock()),
                       "cache": {"hit": False, "age_seconds": 0, "ttl_seconds": TTL_SECONDS}}
            # Only successful provider responses are cached. Never let missing
            # keys or transient failures poison a six-hour availability cache.
            await asyncio.to_thread(self._cache_put, key, payload)
            return payload
        except ProviderError as error:
            return {"provider": provider, "query": query, "status": error.status, "results": [],
                    "message": str(error), "availability": "unknown", "observed_at_utc": timestamp(self.clock())}
        except (AttributeError, KeyError, TypeError, ValueError):
            # A changed/malformed provider schema is not a confirmed empty
            # catalog. Do not echo payloads which may contain account data.
            return {"provider": provider, "query": query, "status": "invalid_response", "results": [],
                    "message": "Provider response did not match its supported data contract",
                    "availability": "unknown", "observed_at_utc": timestamp(self.clock())}

    async def search_isee_sisoog(self, query: str, limit: int = 10, refresh: bool = False, product_url: str | None = None) -> dict:
        if product_url:
            parsed = urllib.parse.urlsplit(product_url)
            if parsed.scheme != "https" or parsed.hostname != "isee.sisoog.com" or parsed.port not in {None, 443} or not re.match(r"^/(?:nc-)?products/\d+/", parsed.path) or parsed.query or parsed.username or parsed.password:
                raise ValueError("Use an exact public https://isee.sisoog.com/products/<id>/<name> or nc-products URL")
            async def load():
                content = await self._request("GET", product_url)
                if not isinstance(content, str):
                    raise ProviderError("invalid_response", "ISee did not return a product page")
                rows = parse_isee(content, product_url)
                if not rows and not Page(content).products:
                    raise ProviderError("parser_unavailable", "ISee page format changed or access was blocked")
                return rows, ["Confirm stock and price at the seller. ISee is an aggregator; zero displayed sellers is not a global stock finding."]
            response = await self._cached("isee_sisoog", query, load, refresh, {"url": product_url})
        else:
            catalog = self._config().get("isee_catalog")
            if not catalog:
                return {"provider": "isee_sisoog", "query": query, "status": "authorized_catalog_required", "results": [],
                        "availability": "unknown", "search_url": "https://isee.sisoog.com/",
                        "message": "ISee robots.txt disallows automated /api/v1 and /products?filters= keyword routes. Supply an exact public product_url or configure an authorized local isee_catalog JSON export."}
            path = Path(catalog)
            if not path.is_absolute() or not path.is_file():
                raise ValueError("Configured isee_catalog must be an existing absolute JSON file")
            if path.stat().st_size > MAX_RESPONSE_BYTES:
                raise ValueError("Authorized ISee catalog exceeds 5 MiB")
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(raw, dict) or not isinstance(raw.get("results"), list) or not raw.get("observed_at_utc"):
                raise ValueError("ISee catalog requires results and observed_at_utc; do not invent an observation date")
            rows = [item for item in raw["results"] if isinstance(item, dict) and query.casefold() in str(item.get("mpn", "")).casefold()]
            response = {"provider": "isee_sisoog", "query": query, "status": "local_snapshot", "results": rows,
                        "observed_at_utc": raw["observed_at_utc"], "live": False,
                        "notes": ["Authorized local snapshot; recheck the seller before purchase."]}
        response["results"] = response["results"][:limit]
        return response

    async def search_jlcpcb_lcsc(self, query: str, limit: int = 10, refresh: bool = False) -> dict:
        code = query.upper() if re.fullmatch(r"[cC]\d+", query) else None
        url = "https://jlcpcb.com/partdetail/" + code if code else "https://jlcpcb.com/parts/componentSearch?" + urllib.parse.urlencode({"searchTxt": query})
        async def load():
            source = await self._request("GET", url)
            if not isinstance(source, str):
                raise ProviderError("invalid_response", "JLCPCB did not return catalog HTML")
            rows = parse_jlcpcb(source, url)
            if not rows:
                raise ProviderError("dynamic_catalog_unavailable", "Public HTML contains no structured part records. Supply an exact C-code or use approved JLCPCB API access; empty dynamic HTML is not zero stock.")
            if code:
                rows = [row for row in rows if row["lcsc_code"] == code]
            return rows, ["Public catalog observation; quantity, tiers and datasheet may require dynamic/approved API access. Recheck before assembly. LCSC stock is separate from JLCPCB assembly inventory."]
        result = await self._cached("jlcpcb", query, load, refresh)
        result["search_url"] = url
        result["api_access_url"] = "https://api.jlcpcb.com/"
        result["results"] = result["results"][:limit]
        return result

    async def _nexar(self, query: str, keys: dict, limit: int) -> list[dict]:
        token = keys.get("access_token")
        if not token:
            if not keys.get("client_id") or not keys.get("client_secret"):
                raise ProviderError("not_configured", "Set Nexar client credentials or NEXAR_ACCESS_TOKEN")
            identity = hashlib.sha256((keys["client_id"] + keys["client_secret"]).encode()).hexdigest()
            saved = self._tokens.get(identity)
            if saved and saved[1] > self.clock() + 30:
                token = saved[0]
            else:
                auth = await self._request("POST", "https://identity.nexar.com/connect/token", form={"grant_type": "client_credentials", "scope": "supply.domain", "client_id": keys["client_id"], "client_secret": keys["client_secret"]})
                token = auth.get("access_token") if isinstance(auth, dict) else None
                if not token:
                    raise ProviderError("credentials_rejected", "Nexar did not issue an access token")
                self._tokens[identity] = (token, self.clock() + min(float(auth.get("expires_in", 3600)), 86400))
        graph = """query Supply($q:String!,$limit:Int!){supSearchMpn(q:$q,limit:$limit){hits results{part{mpn manufacturer{name} bestDatasheet{url} specs{attribute{shortname} displayValue} sellers{company{name} offers{inventoryLevel clickUrl prices{price quantity currency}}}}}}}"""
        payload = await self._request("POST", "https://api.nexar.com/graphql", data={"query": graph, "variables": {"q": query, "limit": limit}}, headers={"Authorization": "Bearer " + token})
        if not isinstance(payload, dict) or payload.get("errors"):
            raise ProviderError("api_error", "Nexar GraphQL query failed; inspect account/schema access")
        rows = []
        for result in (payload.get("data") or {}).get("supSearchMpn", {}).get("results", []):
            part = result.get("part") or {}
            state = next((s.get("displayValue") for s in part.get("specs", []) if (s.get("attribute") or {}).get("shortname") == "lifecyclestatus"), None)
            for seller in part.get("sellers", []):
                for value in seller.get("offers", []):
                    rows.append(offer("nexar", part.get("mpn"), manufacturer=(part.get("manufacturer") or {}).get("name"), vendor=(seller.get("company") or {}).get("name"),
                                      stock_count=number(value.get("inventoryLevel")), product_url=safe_url(value.get("clickUrl")),
                                      datasheet_url=safe_url((part.get("bestDatasheet") or {}).get("url")), lifecycle=lifecycle(state), lifecycle_raw=state,
                                      price_breaks=[{"quantity": number(p.get("quantity")), "unit_price": number(p.get("price")), "currency": p.get("currency")} for p in value.get("prices", [])]))
        return rows

    async def _digikey(self, query: str, keys: dict, limit: int) -> list[dict]:
        if not keys.get("client_id") or not keys.get("access_token"):
            raise ProviderError("not_configured", "Set DIGIKEY_CLIENT_ID and a current DIGIKEY_ACCESS_TOKEN")
        payload = await self._request("POST", "https://api.digikey.com/products/v4/search/keyword", data={"Keywords": query, "Limit": limit, "Offset": 0},
                                      headers={"Authorization": "Bearer " + keys["access_token"], "X-DIGIKEY-Client-Id": keys["client_id"], "X-DIGIKEY-Locale-Currency": "USD", "X-DIGIKEY-Locale-Site": "US", "X-DIGIKEY-Locale-Language": "en"})
        if not isinstance(payload, dict) or not isinstance(payload.get("Products"), list):
            raise ProviderError("api_error", "DigiKey returned no product collection")
        rows = []
        for part in payload["Products"]:
            status = part.get("ProductStatus") or {}
            state = status.get("Status") if isinstance(status, dict) else status
            variations = part.get("ProductVariations") or [{}]
            for variant in variations:
                rows.append(offer("digikey", part.get("ManufacturerProductNumber"), manufacturer=(part.get("Manufacturer") or {}).get("Name"), vendor="DigiKey",
                                  stock_count=number(variant.get("QuantityAvailableforPackageType", part.get("QuantityAvailable"))),
                                  datasheet_url=safe_url(part.get("DatasheetUrl")), product_url=safe_url(part.get("ProductUrl")),
                                  lifecycle=lifecycle(state), lifecycle_raw=state, currency="USD",
                                  price_breaks=[{"quantity": number(p.get("BreakQuantity")), "unit_price": number(p.get("UnitPrice")), "currency": "USD"} for p in variant.get("StandardPricing", [])]))
        return rows

    async def _mouser(self, query: str, keys: dict, limit: int) -> list[dict]:
        if not keys.get("api_key"):
            raise ProviderError("not_configured", "Set MOUSER_API_KEY")
        if len(query) > 40:
            raise ValueError("Mouser part-number search accepts at most 40 characters")
        url = "https://api.mouser.com/api/v1/search/partnumber?" + urllib.parse.urlencode({"apiKey": keys["api_key"]})
        payload = await self._request("POST", url, data={"SearchByPartRequest": {"mouserPartNumber": query, "partSearchOptions": "Exact"}})
        if not isinstance(payload, dict) or payload.get("Errors"):
            raise ProviderError("api_error", "Mouser search returned an API error")
        rows = []
        for part in (payload.get("SearchResults") or {}).get("Parts", [])[:limit]:
            availability = str(part.get("Availability", ""))
            stock = re.fullmatch(r"\s*([0-9,]+)\s+(?:In Stock|In stock)\s*", availability)
            state = part.get("LifecycleStatus")
            alternatives = [{"mpn": value, "pin_compatibility": "unverified", "source": "supplier_suggested"} for value in part.get("SuggestedReplacement", "").split("|") if value]
            rows.append(offer("mouser", part.get("ManufacturerPartNumber"), manufacturer=part.get("Manufacturer"), vendor="Mouser",
                              stock_count=number(stock[1]) if stock else None, availability_raw=availability,
                              datasheet_url=safe_url(part.get("DataSheetUrl")), product_url=safe_url(part.get("ProductDetailUrl")),
                              lifecycle=lifecycle(state), lifecycle_raw=state, alternatives=alternatives,
                              price_breaks=[{"quantity": number(p.get("Quantity")), "unit_price": number(p.get("Price")), "currency": p.get("Currency")} for p in part.get("PriceBreaks", [])]))
        return rows

    async def _oemsecrets(self, query: str, keys: dict, limit: int) -> list[dict]:
        if not keys.get("api_key"):
            raise ProviderError("not_configured", "Set OEMSECRETS_API_KEY")
        payload = await self._request("GET", "https://beta.api.oemsecrets.com/partsearch?" + urllib.parse.urlencode({"apiKey": keys["api_key"], "searchTerm": query, "currency": "USD"}))
        if not isinstance(payload, dict) or not isinstance(payload.get("stock"), list):
            raise ProviderError("api_error", "OEMsecrets returned no stock collection")
        rows = []
        for part in payload["stock"][:limit]:
            state = part.get("life_cycle")
            rows.append(offer("oemsecrets", part.get("part_number"), vendor=(part.get("distributor") or {}).get("distributor_name"),
                              stock_count=number(part.get("quantity_in_stock")), lifecycle=lifecycle(state), lifecycle_raw=state,
                              datasheet_url=safe_url(part.get("datasheet_url")), product_url=safe_url(part.get("buy_now_url")),
                              price_breaks=[{"quantity": number(p.get("unit_break")), "unit_price": number(p.get("unit_price")), "currency": currency}
                                            for currency, values in (part.get("prices") or {}).items() for p in values]))
        return rows

    async def search_global_distributors(self, query: str, providers: list[str] | None = None, limit: int = 10, refresh: bool = False) -> dict:
        selected = list(PROVIDERS) if providers is None else providers
        async def search(provider: str):
            keys = self._credentials(provider)
            async def load():
                rows = await getattr(self, "_" + provider)(query, keys, limit)
                return rows, ["Supplier suggestions do not prove pin, package, firmware or electrical compatibility."]
            return await self._cached(provider, query, load, refresh, {"credentials": keys, "limit": limit})
        results = await asyncio.gather(*(search(provider) for provider in selected))
        return {"query": query, "providers": results, "results": [row for response in results for row in response["results"]],
                "complete": all(response["status"] in {"ok", "no_results"} for response in results),
                "pin_compatible_alternatives": [], "alternative_verification": "Requires authoritative datasheet pin/package/electrical comparison; never inferred from stock search."}

    async def audit_bom_availability(self, bom: list[dict] | None = None, markets: list[str] | None = None, refresh: bool = False) -> dict:
        source = "explicit_bom"
        if bom is None:
            if self.bom_reader is None:
                raise ValueError("Provide bom or configure the read-only active-project BOM reader")
            value = self.bom_reader()
            bom = await value if inspect.isawaitable(value) else value
            source = "active_project_bridge"
            self._validators["audit_bom_availability"].validate({"bom": bom})
        unique: dict[tuple[str, str], dict] = {}
        missing = []
        for row in bom:
            mpn = str(row.get("mpn", "")).strip()
            if not mpn:
                missing.append(row.get("reference", ""))
                continue
            identity = (mpn.casefold(), row.get("manufacturer", "").casefold())
            entry = unique.setdefault(identity, {"mpn": mpn, "manufacturer": row.get("manufacturer"), "quantity": 0, "references": [], "lcsc_code": row.get("lcsc_code")})
            entry["quantity"] += row.get("quantity", 1)
            entry["references"].append(row.get("reference", ""))
        selected = ["iran", "jlcpcb", "global"] if markets is None else markets
        # Bounded concurrency keeps a large BOM from filling the HTTP rate queue.
        semaphore = asyncio.Semaphore(3)
        async def audit(entry: dict):
            async with semaphore:
                checks = {}
                if "iran" in selected:
                    checks["iran"] = await self.search_isee_sisoog(entry["mpn"], refresh=refresh)
                if "jlcpcb" in selected:
                    checks["jlcpcb"] = await self.search_jlcpcb_lcsc(entry.get("lcsc_code") or entry["mpn"], refresh=refresh)
                if "global" in selected:
                    checks["global"] = await self.search_global_distributors(entry["mpn"], refresh=refresh)
                offers = [row for check in checks.values() for row in check.get("results", [])]
                exact = [row for row in offers if row.get("mpn", "").strip().casefold() == entry["mpn"].casefold()
                         and (not entry["manufacturer"] or (row.get("manufacturer") or "").casefold() == entry["manufacturer"].casefold())]
                flags = []
                if any(row.get("lifecycle") in {"NRND", "Obsolete", "EOL"} for row in exact):
                    flags.append("lifecycle_risk")
                sufficient = [row for row in exact if row.get("stock_count") is not None and row["stock_count"] >= entry["quantity"]]
                if not exact:
                    flags.append("availability_unverified")
                elif sufficient:
                    flags.append("observed_sufficient_stock")
                else:
                    flags.append("quantity_unverified_or_insufficient")
                if exact and all(row.get("stock_count") == 0 for row in exact):
                    flags.append("observed_out_of_stock")
                return {**entry, "checks": checks, "flags": flags,
                        "basic_part_candidates": [row for row in exact if row.get("assembly_class") == "basic"],
                        "substitution_candidates": [], "electrical_compatibility_verified": False}
        parts = await asyncio.gather(*(audit(entry) for entry in unique.values()))
        return {"source": source, "unique_mpn_count": len(parts), "missing_mpn_references": missing,
                "parts": parts, "markets": selected, "ready_for_manufacturing": False,
                "notes": ["Availability snapshot only; no purchase or design changes. Stock cannot establish electrical compatibility, authenticity, lead time or DRC/ERC readiness."]}

    async def call_tool_async(self, name: str, arguments: dict | None = None) -> dict:
        if name not in self._validators:
            raise ValueError("Unknown supply tool: " + name)
        arguments = {} if arguments is None else arguments
        self._validators[name].validate(arguments)
        return await getattr(self, name)(**arguments)

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Synchronous gateway adapter; call in a worker thread from asyncio."""
        return asyncio.run(self.call_tool_async(name, arguments))


_default: SupplyChain | None = None
_default_lock = threading.Lock()


def call_tool(name: str, arguments: dict | None = None) -> dict:
    global _default
    with _default_lock:
        if _default is None:
            _default = SupplyChain()
        engine = _default
    return engine.call_tool(name, arguments)
