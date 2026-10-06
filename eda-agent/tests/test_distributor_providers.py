# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The credential-gated distributor catalogues.

These sources yield part IDENTITY (MPN, datasheet, stock) and never
geometry, so most of what is worth guarding is about how they FAIL. An
unconfigured or rejected catalogue that returns an empty list tells the
caller the part does not exist, which is both wrong and unrecoverable:
they stop looking.

The sharpest case here is measured rather than imagined. Mouser answers
an invalid API key with HTTP **200** and an ``Errors`` array, confirmed
against the live endpoint. Any client that judged success by status code
alone would report a rejected credential as a successful search that
matched nothing.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from eda_agent.libimport.providers._distributor import (
    DistributorProvider,
    first_string,
)
from eda_agent.libimport.providers.base import (
    ProviderError,
    ProviderUnavailable,
)
from eda_agent.libimport.providers.distributors import (
    DigiKeyProvider,
    Element14Provider,
    MouserProvider,
    NexarProvider,
    TmeProvider,
)

ALL_DISTRIBUTORS = (DigiKeyProvider, Element14Provider, MouserProvider,
                    NexarProvider, TmeProvider)


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch):
    """A developer's real keys must not change what these tests prove."""
    for cls in ALL_DISTRIBUTORS:
        for var in cls.env_vars:
            monkeypatch.delenv(var, raising=False)


def _respond(monkeypatch, payload, status=200):
    """Stub the transport with one JSON reply."""
    body = json.dumps(payload).encode("utf-8")

    class _Response(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        "eda_agent.libimport.providers._distributor.urllib.request.urlopen",
        lambda *a, **k: _Response(body))


def _raise(monkeypatch, exc):
    def _boom(*a, **k):
        raise exc
    monkeypatch.setattr(
        "eda_agent.libimport.providers._distributor.urllib.request.urlopen",
        _boom)


# ---- refusing to run unconfigured ------------------------------------

@pytest.mark.parametrize("cls", ALL_DISTRIBUTORS)
def test_unconfigured_is_unavailable_not_empty(cls):
    """"No key" must never look like "no such part".

    An empty list here would end the search: the caller has no way to
    tell a silent source from an exhaustive one.
    """
    with pytest.raises(ProviderUnavailable) as excinfo:
        cls().search("STM32F103")
    message = str(excinfo.value)
    for var in cls.env_vars:
        assert var in message, (
            f"{cls.name} must name {var}; 'not configured' is not "
            f"something the user can act on")


@pytest.mark.parametrize("cls", ALL_DISTRIBUTORS)
def test_no_credential_ships_with_the_project(cls):
    """No default key, for the same reason no default registry ships.

    A bundled credential would make this project the operator's client
    and put every user's traffic through one account.
    """
    provider = cls()
    with pytest.raises(ProviderUnavailable):
        provider._credentials()


# ---- the measured trap ------------------------------------------------

def test_an_error_under_http_200_is_not_an_empty_result(monkeypatch):
    """Measured live: Mouser answers a bad key with 200 + Errors.

    This is the single most important assertion in the file. Judged on
    status alone the search "succeeded" and found nothing.
    """
    monkeypatch.setenv("MOUSER_API_KEY", "wrong-key")
    _respond(monkeypatch, {
        "Errors": [{"Id": 0, "Code": "Invalid",
                    "Message": "Invalid unique identifier."}],
        "SearchResults": None,
    })

    with pytest.raises(ProviderUnavailable) as excinfo:
        MouserProvider().search("STM32F103")

    assert "NOT an empty result" in str(excinfo.value)


def test_an_empty_errors_key_is_still_a_success(monkeypatch):
    """These APIs send the key unconditionally; empty means fine.

    Without this the guard above would reject every successful search,
    which is the failure mode that gets a safety check deleted.
    """
    monkeypatch.setenv("MOUSER_API_KEY", "good-key")
    _respond(monkeypatch, {
        "Errors": [],
        "SearchResults": {"Parts": [
            {"ManufacturerPartNumber": "STM32F103C8T6",
             "Manufacturer": "STMicroelectronics",
             "Description": "ARM MCU",
             "DataSheetUrl": "https://example.invalid/ds.pdf"},
        ]},
    })

    hits = MouserProvider().search("STM32F103")
    assert [h.mpn for h in hits] == ["STM32F103C8T6"]
    assert hits[0].datasheet.endswith("ds.pdf")


# ---- failure classification ------------------------------------------

def test_a_rejected_key_is_unavailable_not_an_error(monkeypatch):
    """401 means "fix your key", which is a different act from "retry"."""
    monkeypatch.setenv("MOUSER_API_KEY", "expired")
    _raise(monkeypatch, urllib.error.HTTPError(
        "https://api.mouser.com/x", 401, "Unauthorized", {}, None))

    with pytest.raises(ProviderUnavailable) as excinfo:
        MouserProvider().search("anything")
    assert "MOUSER_API_KEY" in str(excinfo.value)


def test_rate_limiting_says_the_part_may_still_exist(monkeypatch):
    monkeypatch.setenv("MOUSER_API_KEY", "k")
    _raise(monkeypatch, urllib.error.HTTPError(
        "https://api.mouser.com/x", 429, "Too Many", {}, None))

    with pytest.raises(ProviderUnavailable) as excinfo:
        MouserProvider().search("anything")
    assert "may still exist" in str(excinfo.value)


def test_a_network_failure_is_unavailable_not_empty(monkeypatch):
    monkeypatch.setenv("MOUSER_API_KEY", "k")
    _raise(monkeypatch, urllib.error.URLError("no route to host"))

    with pytest.raises(ProviderUnavailable) as excinfo:
        MouserProvider().search("anything")
    assert "not evidence" in str(excinfo.value)


def test_a_server_error_is_an_error_not_a_credential_problem(monkeypatch):
    """500 must not send the user off to re-check a key that is fine."""
    monkeypatch.setenv("MOUSER_API_KEY", "k")
    _raise(monkeypatch, urllib.error.HTTPError(
        "https://api.mouser.com/x", 500, "Server Error", {}, None))

    with pytest.raises(ProviderError) as excinfo:
        MouserProvider().search("anything")
    assert not isinstance(excinfo.value, ProviderUnavailable)


# ---- credentials must not leak ---------------------------------------

def test_a_key_in_the_query_string_never_reaches_an_error_message(
        monkeypatch):
    """Mouser puts the key in the URL, so errors must not echo the URL.

    Not hypothetical: these messages are returned to the caller and end
    up in logs and transcripts. The provider does not choose where the
    key goes, but it does choose what it repeats back.
    """
    secret = "SUPERSECRETKEY123"
    monkeypatch.setenv("MOUSER_API_KEY", secret)
    _raise(monkeypatch, urllib.error.HTTPError(
        f"https://api.mouser.com/api/v1/search/keyword?apiKey={secret}",
        500, "Server Error", {}, None))

    with pytest.raises(ProviderError) as excinfo:
        MouserProvider().search("anything")
    assert secret not in str(excinfo.value), (
        "the API key leaked into an error message")


def test_a_transport_failure_does_not_echo_the_credential(monkeypatch):
    """URLError stringifies whatever it was given; check the real path."""
    secret = "ANOTHERSECRET456"
    monkeypatch.setenv("MOUSER_API_KEY", secret)
    _raise(monkeypatch, urllib.error.URLError("connection refused"))

    with pytest.raises(ProviderUnavailable) as excinfo:
        MouserProvider().search("anything")
    assert secret not in str(excinfo.value)


# ---- what a catalogue is ----------------------------------------------

@pytest.mark.parametrize("cls", ALL_DISTRIBUTORS)
def test_a_catalogue_yields_no_geometry(cls):
    """The distinction that stops a caller choosing an unbuildable part."""
    provider = cls()
    assert provider.kind == "catalogue"
    assert provider.formats == ()
    assert provider.native_to == ()


@pytest.mark.parametrize("cls", ALL_DISTRIBUTORS)
def test_endpoints_are_https_and_not_placeholders(cls):
    """Every URL here was probed live before being written down."""
    provider = cls()
    urls = [v for k, v in vars(cls).items()
            if k.endswith("_URL") and isinstance(v, str)]
    assert urls, f"{provider.name} declares no endpoint"
    for url in urls:
        assert url.startswith("https://"), f"{url} is not https"
        assert "example" not in url and "TODO" not in url


@pytest.mark.parametrize("cls", ALL_DISTRIBUTORS)
def test_the_unverified_claim_is_published_not_hidden(cls):
    """The endpoint was measured; the response shape was not.

    Saying so is the same discipline the tool catalog already applies to
    maturity: a claim nobody checked is worth less than an honest blank.
    """
    assert cls.verified_live is False, (
        "flip this only when the client has actually run against the "
        "live API with a real credential")


def test_a_catalogue_hit_states_it_carries_no_symbol(monkeypatch):
    monkeypatch.setenv("MOUSER_API_KEY", "k")
    _respond(monkeypatch, {"SearchResults": {"Parts": [
        {"ManufacturerPartNumber": "NE555P", "Manufacturer": "TI"}]}})

    hit = MouserProvider().search("NE555")[0]
    assert "no symbol or footprint" in hit.provenance


# ---- shape drift ------------------------------------------------------

def test_a_renamed_field_degrades_a_hit_rather_than_losing_the_search():
    """Distributor payloads rename fields between API versions.

    Losing the whole result set because one key moved would turn a
    cosmetic upstream change into an outage.
    """
    assert first_string({"a": {"b": "x"}}, ("a", "b")) == "x"
    assert first_string({"old": "v"}, ("new",), ("old",)) == "v"
    assert first_string({"a": None}, ("a",)) == ""
    # A non-dict midway must not raise.
    assert first_string({"a": "scalar"}, ("a", "b")) == ""


def test_a_result_missing_its_mpn_is_skipped_not_faked(monkeypatch):
    """A hit with no part number is not addressable, so it is dropped."""
    monkeypatch.setenv("MOUSER_API_KEY", "k")
    _respond(monkeypatch, {"SearchResults": {"Parts": [
        {"Manufacturer": "TI", "Description": "mystery"},
        {"ManufacturerPartNumber": "NE555P", "Manufacturer": "TI"},
    ]}})

    hits = MouserProvider().search("x")
    assert [h.mpn for h in hits] == ["NE555P"]


def test_a_non_json_body_is_reported_as_such(monkeypatch):
    monkeypatch.setenv("MOUSER_API_KEY", "k")

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        "eda_agent.libimport.providers._distributor.urllib.request.urlopen",
        lambda *a, **k: _Response(b"<html>maintenance</html>"))

    with pytest.raises(ProviderError) as excinfo:
        MouserProvider().search("x")
    assert "not JSON" in str(excinfo.value)


# ---- the signed one ---------------------------------------------------

def test_tme_signature_is_deterministic_and_key_dependent():
    """A signature that ignored the secret would authenticate nothing."""
    provider = TmeProvider()
    params = {"Token": "t", "SearchPlain": "NE555"}
    one = provider._sign(TmeProvider._SEARCH_URL, params, "secret-a")
    two = provider._sign(TmeProvider._SEARCH_URL, params, "secret-a")
    three = provider._sign(TmeProvider._SEARCH_URL, params, "secret-b")

    assert one == two, "signing must be deterministic"
    assert one != three, "the signature must depend on the secret"
    assert one != provider._sign(
        TmeProvider._SEARCH_URL, {"Token": "t", "SearchPlain": "LM358"},
        "secret-a"), "the signature must depend on the parameters"


def test_tme_signature_does_not_depend_on_parameter_order():
    """TME sorts parameters before signing; a dict order must not leak."""
    provider = TmeProvider()
    a = provider._sign("https://api.tme.eu/x",
                       {"A": "1", "B": "2"}, "s")
    b = provider._sign("https://api.tme.eu/x",
                       {"B": "2", "A": "1"}, "s")
    assert a == b


# ---- fetch ------------------------------------------------------------

def test_fetch_says_plainly_that_there_is_nothing_to_place(monkeypatch):
    monkeypatch.setenv("MOUSER_API_KEY", "k")
    _respond(monkeypatch, {"SearchResults": {"Parts": [
        {"ManufacturerPartNumber": "NE555P", "Manufacturer": "TI",
         "DataSheetUrl": "https://example.invalid/ne555.pdf"}]}})

    detail = MouserProvider().fetch("NE555P")
    assert detail["kind"] == "catalogue"
    assert detail["files"] == {}
    assert "NOT a symbol or footprint" in detail["note"]


def test_fetch_of_an_absent_part_is_an_error_not_a_blank(monkeypatch):
    monkeypatch.setenv("MOUSER_API_KEY", "k")
    _respond(monkeypatch, {"SearchResults": {"Parts": []}})

    with pytest.raises(ProviderError):
        MouserProvider().fetch("NOSUCHPART")


# ---- the base class contract -----------------------------------------

def test_the_base_refuses_to_be_used_directly():
    """A subclass that forgets search must fail loudly, not silently."""
    class Incomplete(DistributorProvider):
        name = "incomplete"
        env_vars = ()

    with pytest.raises(NotImplementedError):
        Incomplete().search("x")
