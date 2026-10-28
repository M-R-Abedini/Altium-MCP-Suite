"""Offline sourcing contracts: provenance, API shapes, quotas and unknowns."""
import asyncio
import json
from pathlib import Path
import sys
import urllib.parse

import jsonschema
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from suite_supply_chain import (MAX_RESPONSE_BYTES, TTL_SECONDS, ProviderError, Response,
                                SupplyChain, lifecycle, list_tools, number, parse_isee,
                                parse_jlcpcb)


def run(coro):
    return asyncio.run(coro)


async def no_sleep(_):
    pass


class HTTP:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, Response):
            return value
        return Response(json.dumps(value).encode(), headers={"Content-Type": "application/json"})


JLC = '''<html><script type="application/ld+json">{"@type":"Product","mpn":"0603WAF1002T5E","sku":"C25804","brand":{"name":"UNI-ROYAL"},"offers":{"@type":"AggregateOffer","priceCurrency":"USD","lowPrice":0.0018,"highPrice":0.0011,"availability":"https://schema.org/InStock"}}</script><div>0603WAF1002T5E</div><span>Basic</span><div>Package</div><span>0603</span><a href="https://example.com/resistor.pdf">Datasheet</a></html>'''
ISEE = '''<script type="application/ld+json">{"@type":"Product","mpn":"AMS1117-3.3"}</script><table><tr class="seller-table__content"><td><a href="/shop_info/1/test" class="seller-name">Test Store</a><span class="seller-code">AMS1117-3.3</span></td><td><span class="seller-available">موجود</span><span class="seller-price digits">۱۲٬۳۴۵ تومان</span><span class="seller-outOfDateLabel">Old price</span></td><td><a href="/api/v1/products/nc-out/123" class="seller-buy-btn">Buy</a></td></tr></table>'''


def engine(tmp_path, http=None, **kwargs):
    return SupplyChain(tmp_path, http=http or HTTP(), sleeper=no_sleep, **kwargs)


def config(tmp_path, value):
    (tmp_path / "config.json").write_text(json.dumps({"supply_chain": value}), encoding="utf-8")


def test_tools_are_closed_schemas_and_read_only():
    tools = list_tools()
    assert len(tools) == 4
    for tool in tools:
        jsonschema.Draft202012Validator.check_schema(tool["inputSchema"])
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["inputSchema"]["additionalProperties"] is False


@pytest.mark.parametrize("value,expected", [("۱۲٬۳۴۵", 12345), ("$0.0018", .0018),
                                              ("0", 0), ("1,000 In Stock", None),
                                              (True, None), (-1, None), (float("nan"), None)])
def test_numbers_do_not_guess_prices_or_currency(value, expected):
    assert number(value) == expected


@pytest.mark.parametrize("value,expected", [("Active", "Active"), ("NRND", "NRND"),
                                           ("Not Recommended for New Designs", "NRND"),
                                           ("Production (Last Updated: 2 years ago)", "Active"),
                                           ("Obsolete", "Obsolete"), ("EOL", "EOL"), (None, "Unknown")])
def test_lifecycle_preserves_unknown(value, expected):
    assert lifecycle(value) == expected


def test_jlc_public_parser_never_invents_tiers_or_stock_count():
    [row] = parse_jlcpcb(JLC, "https://jlcpcb.com/partdetail/C25804")
    assert row["assembly_class"] == "basic"
    assert row["stock_count"] is None and row["stock_status"] == "in_stock"
    assert row["price_breaks"] == []
    assert row["price_range"]["low"] == .0018
    assert row["package"] == "0603"
    assert row["datasheet_url"] == "https://example.com/resistor.pdf"
    assert row["pin_compatibility"] == "unverified"


def test_isee_parser_preserves_toman_and_deduplicates_mobile_rows():
    rows = parse_isee(ISEE + ISEE, "https://isee.sisoog.com/products/1/AMS1117")
    assert len(rows) == 1
    row = rows[0]
    assert row["currency"] == "TOMAN"
    assert row["price_breaks"] == [{"quantity": 1, "unit_price": 12345, "currency": "TOMAN"}]
    assert row["stock_status"] == "in_stock" and row["stock_count"] is None
    assert row["price_stale_warning"] is True
    assert row["product_url"] == "https://isee.sisoog.com/api/v1/products/nc-out/123"


def test_isee_keyword_route_is_an_explicit_handoff_without_network(tmp_path):
    client = HTTP()
    value = run(engine(tmp_path, client).call_tool_async("search_isee_sisoog", {"query": "STM32F103C8T6"}))
    assert value["status"] == "authorized_catalog_required" and value["availability"] == "unknown"
    assert client.calls == []


def test_isee_public_product_only_and_persistent_cache(tmp_path):
    client = HTTP(Response(ISEE.encode()))
    args = {"query": "AMS1117", "product_url": "https://isee.sisoog.com/products/1/AMS1117"}
    assert run(engine(tmp_path, client).call_tool_async("search_isee_sisoog", args))["cache"]["hit"] is False
    second = run(engine(tmp_path, HTTP()).call_tool_async("search_isee_sisoog", args))
    assert second["cache"]["hit"] is True and len(client.calls) == 1
    for url in ("https://evil.test/products/1/x", "https://isee.sisoog.com/api/v1/search", "https://isee.sisoog.com/products?filters=x"):
        with pytest.raises(ValueError):
            run(engine(tmp_path).call_tool_async("search_isee_sisoog", {**args, "product_url": url}))


def test_authorized_isee_catalog_is_dated_and_never_live(tmp_path):
    catalog = tmp_path / "authorized.json"
    catalog.write_text(json.dumps({"results": [{"mpn": "AMS1117-3.3", "currency": "TOMAN", "stock_count": 10}], "observed_at_utc": "2026-10-01T00:00:00Z"}))
    config(tmp_path, {"isee_catalog": str(catalog)})
    value = run(engine(tmp_path).call_tool_async("search_isee_sisoog", {"query": "AMS1117"}))
    assert value["live"] is False and value["observed_at_utc"].startswith("2026-10-01")


def test_jlc_cache_expires_and_refreshes(tmp_path):
    now = [1000.0]
    client = HTTP(Response(JLC.encode()), Response(JLC.encode()), Response(JLC.encode()))
    provider = engine(tmp_path, client, clock=lambda: now[0])
    args = {"query": "C25804"}
    assert not run(provider.call_tool_async("search_jlcpcb_lcsc", args))["cache"]["hit"]
    assert run(provider.call_tool_async("search_jlcpcb_lcsc", args))["cache"]["hit"]
    now[0] += TTL_SECONDS + 1
    assert not run(provider.call_tool_async("search_jlcpcb_lcsc", args))["cache"]["hit"]
    assert not run(provider.call_tool_async("search_jlcpcb_lcsc", {**args, "refresh": True}))["cache"]["hit"]
    assert len(client.calls) == 3


def test_dynamic_empty_jlc_page_is_unknown_not_out_of_stock(tmp_path):
    provider = engine(tmp_path, HTTP(Response(b'<html>0 found <script>dynamicData()</script></html>')))
    value = run(provider.call_tool_async("search_jlcpcb_lcsc", {"query": "AMS1117"}))
    assert value["status"] == "dynamic_catalog_unavailable" and value["availability"] == "unknown"


def test_mouser_request_and_normalization_do_not_leak_key(tmp_path, monkeypatch):
    monkeypatch.setenv("MOUSER_API_KEY", "top-secret-test-key")
    response = {"Errors": [], "SearchResults": {"Parts": [{"ManufacturerPartNumber": "AMS1117-3.3", "Manufacturer": "AMS", "Availability": "1,234 In Stock", "LifecycleStatus": "NRND", "SuggestedReplacement": "OTHER123", "DataSheetUrl": "https://example.com/ds.pdf", "PriceBreaks": [{"Quantity": 10, "Price": "$0.12", "Currency": "USD"}]}]}}
    http = HTTP(response)
    value = run(engine(tmp_path, http).call_tool_async("search_global_distributors", {"query": "AMS1117-3.3", "providers": ["mouser"]}))
    assert value["results"][0]["stock_count"] == 1234
    assert value["results"][0]["lifecycle"] == "NRND"
    assert value["results"][0]["alternatives"][0]["pin_compatibility"] == "unverified"
    method, url, _, body = http.calls[0]
    assert method == "POST" and urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["apiKey"] == ["top-secret-test-key"]
    assert json.loads(body)["SearchByPartRequest"]["partSearchOptions"] == "Exact"
    assert "top-secret" not in json.dumps(value)
    assert b"top-secret" not in (tmp_path / "cache/supply_cache.db").read_bytes()


def test_digikey_v4_shape_and_headers(tmp_path):
    config(tmp_path, {"digikey": {"client_id": "id", "access_token": "token"}})
    http = HTTP({"Products": [{"ManufacturerProductNumber": "LM1117", "Manufacturer": {"Name": "TI"}, "QuantityAvailable": 12, "ProductStatus": {"Status": "Active"}, "ProductVariations": [{"StandardPricing": [{"BreakQuantity": 1, "UnitPrice": .2}]}]}]})
    result = run(engine(tmp_path, http).call_tool_async("search_global_distributors", {"query": "LM1117", "providers": ["digikey"]}))
    assert result["results"][0]["stock_count"] == 12
    assert result["results"][0]["price_breaks"][0]["currency"] == "USD"
    assert http.calls[0][1] == "https://api.digikey.com/products/v4/search/keyword"
    assert http.calls[0][2]["X-DIGIKEY-Client-Id"] == "id"


def test_nexar_client_credentials_and_graphql_variables(tmp_path):
    config(tmp_path, {"nexar": {"client_id": "id", "client_secret": "secret"}})
    http = HTTP({"access_token": "access-token", "expires_in": 3600}, {"data": {"supSearchMpn": {"results": [{"part": {"mpn": "STM32F103C8T6", "manufacturer": {"name": "ST"}, "specs": [{"attribute": {"shortname": "lifecyclestatus"}, "displayValue": "Active"}], "sellers": [{"company": {"name": "Distributor"}, "offers": [{"inventoryLevel": 50, "prices": [{"quantity": 1, "price": 2.5, "currency": "USD"}]}]}]}}]}}})
    value = run(engine(tmp_path, http).call_tool_async("search_global_distributors", {"query": "STM32F103C8T6", "providers": ["nexar"]}))
    assert value["results"][0]["stock_count"] == 50 and value["results"][0]["lifecycle"] == "Active"
    assert urllib.parse.parse_qs(http.calls[0][3].decode())["scope"] == ["supply.domain"]
    assert json.loads(http.calls[1][3])["variables"]["q"] == "STM32F103C8T6"
    assert "STM32F103C8T6" not in json.loads(http.calls[1][3])["query"]


def test_oemsecrets_contract(tmp_path):
    config(tmp_path, {"oemsecrets": {"api_key": "key"}})
    http = HTTP({"stock": [{"part_number": "ESP32-S3", "quantity_in_stock": 7, "life_cycle": "Obsolete", "distributor": {"distributor_name": "Vendor"}, "prices": {"USD": [{"unit_break": "1", "unit_price": "2.2"}]}}]})
    result = run(engine(tmp_path, http).call_tool_async("search_global_distributors", {"query": "ESP32-S3", "providers": ["oemsecrets"]}))
    assert result["results"][0]["lifecycle"] == "Obsolete"
    assert result["results"][0]["price_breaks"] == [{"quantity": 1, "unit_price": 2.2, "currency": "USD"}]
    assert http.calls[0][1].startswith("https://beta.api.oemsecrets.com/partsearch?")


def test_missing_keys_are_not_stock_failures_and_make_no_http_calls(tmp_path, monkeypatch):
    from suite_supply_chain import ENV_KEYS
    for values in ENV_KEYS.values():
        for env in values.values():
            monkeypatch.delenv(env, raising=False)
    http = HTTP()
    result = run(engine(tmp_path, http).call_tool_async("search_global_distributors", {"query": "AMS1117"}))
    assert not result["complete"] and http.calls == []
    assert {item["status"] for item in result["providers"]} == {"not_configured"}


def test_retry_budget_and_long_rate_limit_no_cache_poison(tmp_path):
    http = HTTP(Response(b"", 503), Response(JLC.encode()))
    value = run(engine(tmp_path, http).call_tool_async("search_jlcpcb_lcsc", {"query": "C25804"}))
    assert value["status"] == "ok" and len(http.calls) == 2
    http2 = HTTP(Response(b"", 429, {"Retry-After": "3600"}))
    value = run(engine(tmp_path, http2).call_tool_async("search_jlcpcb_lcsc", {"query": "C25805"}))
    assert value["status"] == "rate_limited" and len(http2.calls) == 1


def test_http_errors_never_echo_secret_urls_or_provider_body(tmp_path, monkeypatch):
    monkeypatch.setenv("MOUSER_API_KEY", "test-hidden-key")
    http = HTTP(Response(b'raw test-hidden-key', 401))
    result = run(engine(tmp_path, http).call_tool_async("search_global_distributors", {"query": "AMS1117", "providers": ["mouser"]}))
    assert result["providers"][0]["status"] == "credentials_rejected"
    assert "test-hidden-key" not in json.dumps(result)


def test_limits_and_schema_validation_precede_http(tmp_path):
    provider = engine(tmp_path)
    for args in ({"query": "aa"}, {"query": "AMS1117", "limit": 1000}, {"query": "AMS1117", "api_key": "secret"}):
        with pytest.raises(jsonschema.ValidationError):
            run(provider.call_tool_async("search_jlcpcb_lcsc", args))
    with pytest.raises(ValueError):
        run(provider.call_tool_async("unknown", {}))


def test_audit_deduplicates_mpn_and_reports_unknown_without_inventing_readiness(tmp_path):
    provider = engine(tmp_path)
    result = run(provider.call_tool_async("audit_bom_availability", {"bom": [{"mpn": "AMS1117", "quantity": 2, "reference": "U1"}, {"mpn": "AMS1117", "quantity": 1, "reference": "U2"}, {"reference": "R1"}], "markets": ["iran"]}))
    assert result["unique_mpn_count"] == 1 and result["parts"][0]["quantity"] == 3
    assert result["missing_mpn_references"] == ["R1"]
    assert result["parts"][0]["flags"] == ["availability_unverified"]
    assert result["ready_for_manufacturing"] is False


def test_async_active_project_reader_and_exact_part_stock(tmp_path):
    async def reader():
        return [{"mpn": "0603WAF1002T5E", "lcsc_code": "C25804", "quantity": 2}]
    provider = engine(tmp_path, HTTP(Response(JLC.encode())), bom_reader=reader)
    result = run(provider.call_tool_async("audit_bom_availability", {"markets": ["jlcpcb"]}))
    assert result["source"] == "active_project_bridge"
    assert result["parts"][0]["flags"] == ["quantity_unverified_or_insufficient"]
    assert len(result["parts"][0]["basic_part_candidates"]) == 1


def test_audit_reader_malformed_data_is_rejected(tmp_path):
    provider = engine(tmp_path, bom_reader=lambda: [{"mpn": "A", "quantity": -1}])
    with pytest.raises(jsonschema.ValidationError):
        run(provider.call_tool_async("audit_bom_availability", {}))
    with pytest.raises(ValueError, match="Provide bom"):
        run(engine(tmp_path).call_tool_async("audit_bom_availability", {}))


def test_changed_api_contract_is_unknown_and_never_cached_as_empty(tmp_path):
    config(tmp_path, {"mouser": {"api_key": "key"}})
    http = HTTP({"SearchResults": {"Parts": [None]}})
    result = run(engine(tmp_path, http).call_tool_async("search_global_distributors", {"query": "AMS1117", "providers": ["mouser"]}))
    assert result["providers"][0]["status"] == "invalid_response"
    assert result["providers"][0]["availability"] == "unknown"
    http2 = HTTP({"SearchResults": {"Parts": []}})
    second = run(engine(tmp_path, http2).call_tool_async("search_global_distributors", {"query": "AMS1117", "providers": ["mouser"]}))
    assert second["providers"][0]["status"] == "no_results" and len(http2.calls) == 1


def test_oversized_response_and_unexpected_port_rejected(tmp_path):
    provider = engine(tmp_path, HTTP(Response(b"x" * (MAX_RESPONSE_BYTES + 1))))
    value = run(provider.call_tool_async("search_jlcpcb_lcsc", {"query": "C25804"}))
    assert value["status"] == "response_too_large"
    with pytest.raises(ValueError):
        run(provider.call_tool_async("search_isee_sisoog", {"query": "AMS1117", "product_url": "https://isee.sisoog.com:9999/products/1/AMS1117"}))
