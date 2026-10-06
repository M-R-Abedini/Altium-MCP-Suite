# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Part providers: many sources, none privileged.

The requirement is that no provider is a default and all are searched
equally. That is easy to state and easy to erode: a fallback order, a
relevance sort, or a "primary" setting each quietly turns one source
into the answer for every query and hands its operator the tool surface.

So these tests assert the neutrality directly rather than trusting the
docstrings: every enabled provider is queried, the merged order is
alphabetical rather than ranked, one source cannot crowd out another,
and a provider that cannot answer says why instead of returning an
empty list that would read as "no such part".

Network is stubbed throughout (the session-wide guard in conftest
blocks urllib anyway), so none of this depends on a third party being
reachable.
"""

from __future__ import annotations

import pytest

from eda_agent.libimport.providers import (
    PartHit,
    ProviderError,
    ProviderUnavailable,
    available_providers,
    get_provider,
    search_all,
)


class _Fake:
    """A provider whose behaviour each test dictates."""

    def __init__(self, name, hits=(), raises=None, description="fake"):
        self.name = name
        self.description = description
        self._hits = list(hits)
        self._raises = raises
        self.searched = 0

    def search(self, query, limit=20):
        self.searched += 1
        if self._raises is not None:
            raise self._raises
        return [PartHit(provider=self.name, part_id=h, mpn=h)
                for h in self._hits][:limit]

    def fetch(self, part_id):
        return {"provider": self.name, "part_id": part_id}


@pytest.fixture(autouse=True)
def _configured_registry(monkeypatch):
    """Point the registry client somewhere for tests that exercise it.

    The provider ships with NO default URL on purpose: a default would
    aim every install at one company's service. So these tests have to
    configure it, exactly as a real user must. The URL is a stub host
    and the HTTP layer is faked, so nothing leaves the machine.
    """
    monkeypatch.setenv("PARTS_REGISTRY_URL", "https://registry.invalid")


@pytest.fixture(autouse=True)
def _reset_partreel_memo():
    """Clear the module-level index memo around every test.

    PartReel caches its parsed index at module scope (a fresh provider is
    built per available_providers() call, so instance state would never
    be reused). That is right for the server and wrong for tests: one
    test's stub index would otherwise answer another test's search, and
    the failure would depend on file order.
    """
    from eda_agent.libimport.providers import partreel

    partreel._MEMO, partreel._MEMO_AT = None, 0.0
    yield
    partreel._MEMO, partreel._MEMO_AT = None, 0.0


@pytest.fixture
def only_fakes(monkeypatch):
    """Replace the real registry so tests never touch a network."""
    def install(*providers):
        monkeypatch.setattr(
            "eda_agent.libimport.providers.available_providers",
            lambda: sorted(providers, key=lambda p: p.name))
        return providers
    return install


# ---------------------- neutrality ----------------------------------

def test_every_provider_is_queried(only_fakes):
    """Not "first one that answers": all of them, every time."""
    a, b, c = only_fakes(_Fake("aaa", ["1"]), _Fake("mmm", ["2"]),
                         _Fake("zzz", ["3"]))
    result = search_all("x")
    assert (a.searched, b.searched, c.searched) == (1, 1, 1)
    assert result["count"] == 3


def test_a_rich_provider_cannot_crowd_out_the_others(only_fakes):
    """The cap is per provider, so one big registry cannot dominate."""
    only_fakes(_Fake("aaa", [str(i) for i in range(100)]),
               _Fake("zzz", ["only-one"]))
    result = search_all("x", limit_per_provider=5)
    by_provider = {}
    for hit in result["hits"]:
        by_provider.setdefault(hit["provider"], []).append(hit)
    assert len(by_provider["aaa"]) == 5
    assert len(by_provider["zzz"]) == 1


def test_merged_order_is_alphabetical_not_ranked(only_fakes):
    """Ordering must carry no quality judgement.

    Sorting by anything else (hit count, response time, a preference
    list) would make one source systematically appear first, which is
    the same thing as having a default.
    """
    only_fakes(_Fake("zzz", ["z1"]), _Fake("aaa", ["a1"]))
    hits = search_all("x")["hits"]
    assert [h["provider"] for h in hits] == ["aaa", "zzz"]


def test_there_is_no_default_provider_setting():
    """A fetch must name its source; nothing supplies one implicitly."""
    import inspect

    from eda_agent.tools.parts import register_parts_tools

    captured = {}

    class _Capture:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    register_parts_tools(_Capture())
    sig = inspect.signature(captured["part_fetch"])
    provider = sig.parameters["provider"]

    # The rule is that no SOURCE is ever preferred, which is a property
    # of behaviour rather than of the signature. `provider` carries an
    # empty default so a caller can pass the self-describing `ref` a
    # search returned instead of splitting it by hand; empty selects
    # nothing. Asserting "no default" instead would forbid the ref form
    # while permitting, say, provider="partreel" as the default, which
    # is the thing actually worth preventing.
    assert provider.default in (inspect.Parameter.empty, ""), (
        f"part_fetch defaults provider to {provider.default!r}; that is "
        "a preferred source by another name")

    # The property itself: an id that names no source is REFUSED, not
    # resolved against some provider the code picked.
    import asyncio
    refused = asyncio.run(captured["part_fetch"](part_id="LM317"))
    assert refused["ok"] is False
    assert "provider" not in refused, (
        "a refusal must not attribute the request to any source")
    assert "part_search" in refused["reason"], (
        "the refusal must point at where a valid ref comes from")


# ---------------------- failure honesty ------------------------------

def test_unavailable_provider_does_not_suppress_the_others(only_fakes):
    dead = _Fake("dead", raises=ProviderUnavailable("endpoint withdrawn"))
    live = _Fake("live", ["ok1", "ok2"])
    only_fakes(dead, live)
    result = search_all("x")
    assert result["count"] == 2
    assert result["providers"]["dead"]["ok"] is False
    assert "withdrawn" in result["providers"]["dead"]["unavailable"]
    assert result["providers"]["live"]["ok"] is True


def test_unavailable_is_not_reported_as_no_results(only_fakes):
    """The distinction the whole fan-out depends on.

    An empty list from a dead endpoint would read as "this source has no
    such part", which it is in no position to claim.
    """
    only_fakes(_Fake("dead", raises=ProviderUnavailable("gone")))
    result = search_all("x")
    assert result["count"] == 0
    assert result["providers"]["dead"]["ok"] is False
    assert "unavailable" in result["providers"]["dead"]


def test_an_unexpected_exception_in_one_provider_is_contained(only_fakes):
    """A buggy provider must not take the search down with it."""
    only_fakes(_Fake("boom", raises=RuntimeError("kaboom")),
               _Fake("fine", ["a"]))
    result = search_all("x")
    assert result["count"] == 1
    assert "kaboom" in result["providers"]["boom"]["error"]


def test_no_reachable_provider_is_stated_explicitly():
    """"Nothing found" and "nothing could answer" must not look alike."""
    import asyncio

    from eda_agent.tools.parts import register_parts_tools

    captured = {}

    class _Capture:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    register_parts_tools(_Capture())

    import eda_agent.libimport.providers as prov
    original = prov.available_providers
    prov.available_providers = lambda: [
        _Fake("dead", raises=ProviderUnavailable("gone"))]
    try:
        out = asyncio.run(captured["part_search"](query="anything"))
    finally:
        prov.available_providers = original
    assert out["count"] == 0
    assert "NO provider could answer" in out["note"]


# ---------------------- real providers -------------------------------

def test_easyeda_search_refuses_rather_than_returning_empty():
    """Verified against the live service: the endpoint is withdrawn."""
    from eda_agent.libimport.providers.easyeda import EasyEdaProvider

    with pytest.raises(ProviderUnavailable) as excinfo:
        EasyEdaProvider().search("anything")
    assert "search" in str(excinfo.value).lower()


def test_partreel_filters_the_index_locally(monkeypatch):
    """PartReel has NO server-side search: ?q= is silently ignored.

    Search therefore means pulling the whole index (11.4 MB, 21,657
    parts on the live service) and filtering here, which is why the
    index is cached.
    """
    from eda_agent.libimport.providers import partreel

    index = [
        {"id": "a_stm32", "name": "STM32F103C8T6", "manufacturer": "ST",
         "family": "MCU", "keywords": ["arm"]},
        {"id": "b_res", "name": "R0603", "manufacturer": "Yageo",
         "family": "resistor", "keywords": []},
    ]
    monkeypatch.setattr(partreel, "get_json_cached",
                        lambda url, hosts, ttl: index)
    hits = partreel.PartReelProvider().search("stm32")
    assert [h.part_id for h in hits] == ["a_stm32"]
    assert hits[0].provider == "partreel"


def test_partreel_rejects_a_traversal_in_the_part_id():
    """Part ids are registry data, so they are untrusted input."""
    from eda_agent.libimport.providers.partreel import PartReelProvider

    for bad in ("../../etc/passwd", "a/b"):
        with pytest.raises(ProviderError):
            PartReelProvider().fetch(bad)


def test_kicad_local_skips_per_unit_sub_symbols(monkeypatch, tmp_path):
    """``NAME_0_1`` / ``NAME_1_1`` are unit bodies, not parts.

    Counting them would triple the hit count with entries that cannot be
    placed.
    """
    from eda_agent.libimport.providers import kicad_local

    lib = tmp_path / "MCU_Test.kicad_sym"
    lib.write_text(
        '(kicad_symbol_lib\n'
        '  (symbol "REALPART" (in_bom yes)\n'
        '    (symbol "REALPART_0_1")\n'
        '    (symbol "REALPART_1_1")\n'
        '  )\n)\n', encoding="utf-8")
    monkeypatch.setattr(kicad_local, "_symbol_dir", lambda: tmp_path)

    hits = kicad_local.KicadLocalProvider().search("realpart")
    assert [h.mpn for h in hits] == ["REALPART"]


def test_kicad_local_reports_unavailable_when_not_installed(monkeypatch):
    from eda_agent.libimport.providers import kicad_local

    monkeypatch.setattr(kicad_local, "_symbol_dir", lambda: None)
    with pytest.raises(ProviderUnavailable):
        kicad_local.KicadLocalProvider().search("anything")


# ---------------------- selection, not ranking -----------------------

def test_env_var_selects_a_subset_without_ranking(monkeypatch):
    monkeypatch.setenv("EDA_AGENT_PART_PROVIDERS", "partreel")
    names = [p.name for p in available_providers()]
    assert names == ["partreel"]

    monkeypatch.delenv("EDA_AGENT_PART_PROVIDERS")
    names = [p.name for p in available_providers()]
    assert names == sorted(names), "registry order must stay alphabetical"
    assert len(names) >= 3


def test_unknown_provider_names_the_enabled_ones():
    with pytest.raises(ProviderError) as excinfo:
        get_provider("no-such-registry")
    assert "enabled:" in str(excinfo.value)


def test_partreel_index_is_memoised_across_instances(monkeypatch):
    """available_providers() builds a fresh provider per call.

    Per-instance caching would therefore never be reused, and every
    search would re-parse the whole index (11.8 MB on the live service,
    ~0.2s each). The memo has to be module level.
    """
    from eda_agent.libimport.providers import partreel

    calls = {"n": 0}

    def fake(url, hosts, ttl):
        calls["n"] += 1
        return [{"id": "x", "name": "PART-X", "manufacturer": "M",
                 "family": "f", "keywords": []}]

    monkeypatch.setattr(partreel, "get_json_cached", fake)
    assert partreel.PartReelProvider().search("part-x")
    assert partreel.PartReelProvider().search("part-x")
    assert partreel.PartReelProvider().search("part-x")
    assert calls["n"] == 1, (
        f"index fetched {calls['n']} times across 3 provider instances; "
        f"the memo is not shared")



def _kicad_local_available() -> bool:
    """Whether KiCad's libraries are installed on THIS machine.

    Asked of the provider rather than assumed, because the two tests
    below assert against a real installed library. A developer box has
    KiCad and CI does not, so an unconditional assertion here tests the
    machine instead of the code, and fails only after a push.
    """
    from eda_agent.libimport.providers.kicad_local import _symbol_dir

    # Ask the same question the provider asks itself. Probing through
    # search() was wrong twice over: an empty query short-circuits with
    # `if not needle: return []` before any directory lookup happens, so
    # it reported success on a machine with no KiCad, and a non-empty one
    # scans every library just to answer yes or no. _symbol_dir is the
    # actual predicate, and it is what test_kicad_corpus already uses.
    return _symbol_dir() is not None


def test_search_still_works_with_no_network(monkeypatch, tmp_path):
    """A dead internet must degrade the search, not break it.

    This is the reason a LOCAL provider is in the registry at all: both
    remote sources can be withdrawn (EasyEDA's search already was) or
    put behind a login, and KiCad's installed libraries cannot.
    """
    import urllib.request

    from eda_agent.libimport.providers import search_all

    monkeypatch.setenv("EDA_AGENT_CACHE_DIR", str(tmp_path))

    def deny(*args, **kwargs):
        raise OSError("network is unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", deny)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", deny)

    result = search_all("STM32F103", limit_per_provider=3)

    # The part that must hold on ANY machine: the fan-out completes, and
    # every remote source explains itself rather than going quiet. A
    # provider that returned nothing here would read as "no such part".
    assert result["providers"]["partreel"]["ok"] is False
    assert "unavailable" in result["providers"]["partreel"] or \
        "error" in result["providers"]["partreel"]
    for name, status in result["providers"].items():
        if status.get("ok") is False:
            assert status.get("unavailable") or status.get("error"), (
                f"{name} failed without saying why, which is "
                f"indistinguishable from finding nothing")

    # The local provider still answers, but only where it is installed.
    # Asserting this unconditionally made the test depend on the machine
    # rather than on the behaviour: it passed on a developer box with
    # KiCad present and failed in CI, which has no KiCad.
    if _kicad_local_available():
        assert result["providers"]["kicad_local"]["ok"] is True
        assert result["count"] >= 1


def test_correlation_groups_across_providers(only_fakes):
    """Answers "who has this part", the question a fan-out raises."""
    a = _Fake("aaa"); b = _Fake("zzz")
    a.search = lambda q, limit=20: [
        PartHit(provider="aaa", part_id="a1", mpn="LM358")]
    b.search = lambda q, limit=20: [
        PartHit(provider="zzz", part_id="z1", mpn="lm-358")]
    only_fakes(a, b)

    groups = search_all("lm358")["by_mpn"]
    assert len(groups) == 1, "cosmetic spelling differences must fold"
    assert groups[0]["provider_count"] == 2
    # Providers listed alphabetically inside the group, for the same
    # reason the hit list is: any other order reads as a recommendation.
    assert [p["provider"] for p in groups[0]["providers"]] == ["aaa", "zzz"]


def test_correlation_does_not_fold_wildcard_part_numbers(only_fakes):
    """``...C8Tx`` is a FAMILY placeholder, not a spelling of ``...C8T6``.

    KiCad uses the x suffix to cover several variants with different
    packages and temperature grades. Merging them would assert an
    equivalence this code cannot support.
    """
    a = _Fake("aaa"); b = _Fake("zzz")
    a.search = lambda q, limit=20: [
        PartHit(provider="aaa", part_id="a1", mpn="STM32F103C8T6")]
    b.search = lambda q, limit=20: [
        PartHit(provider="zzz", part_id="z1", mpn="STM32F103C8Tx")]
    only_fakes(a, b)

    groups = search_all("stm32")["by_mpn"]
    assert len(groups) == 2, (
        "wildcard and specific part numbers were merged; that claims an "
        "equivalence between a family placeholder and one variant")


# ---------------------- downloading artefacts ------------------------

def test_download_only_takes_allowlisted_kinds(monkeypatch, tmp_path):
    """File URLs are registry data, so the extension is chosen HERE.

    Writing whatever extension the payload names is how a download turns
    into an executable. Only the known artefact kinds are taken, each
    with the suffix this code expects.
    """
    from eda_agent.libimport.providers import partreel

    monkeypatch.setattr(partreel.PartReelProvider, "fetch",
                        lambda self, pid: {
                            "id": "part_x",
                            "files": {
                                "footprint": "https://partreel.com/a.kicad_mod",
                                "symbol": "https://partreel.com/a.kicad_sym",
                                "installer": "https://partreel.com/evil.exe",
                                "preview": "https://partreel.com/p.png",
                            }})
    monkeypatch.setattr(partreel, "get_bytes",
                        lambda url, hosts: b"(footprint (version 20251024)")

    written = partreel.PartReelProvider().download("part_x", tmp_path)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["part_x.kicad_mod", "part_x.kicad_sym"], names
    assert "installer" not in written and "preview" not in written


def test_download_warns_when_the_format_is_newer_than_local_kicad(
        monkeypatch, tmp_path):
    """Observed live: the registry ships a newer format than KiCad reads.

    Format 20260206 against KiCad 10.0.1's 20251024, where the symbol
    parser refuses the file outright. A file that will not open is not a
    successful download.
    """
    from eda_agent.libimport.providers import partreel

    monkeypatch.setattr(partreel.PartReelProvider, "fetch",
                        lambda self, pid: {
                            "id": "p", "files": {
                                "symbol": "https://partreel.com/a.kicad_sym"}})
    monkeypatch.setattr(partreel, "get_bytes",
                        lambda url, hosts:
                        b"(kicad_symbol_lib\n\t(version 20260206)\n")
    monkeypatch.setattr(partreel, "_local_kicad_version", lambda: 20251024)

    written = partreel.PartReelProvider().download("p", tmp_path)
    assert "symbol" in written, "the file should still be written"
    assert "20260206" in written["symbol_warning"]
    assert "20251024" in written["symbol_warning"]


def test_download_is_quiet_when_the_format_is_readable(monkeypatch,
                                                       tmp_path):
    """No warning when the local KiCad can open it, or is unknown."""
    from eda_agent.libimport.providers import partreel

    monkeypatch.setattr(partreel.PartReelProvider, "fetch",
                        lambda self, pid: {
                            "id": "p", "files": {
                                "symbol": "https://partreel.com/a.kicad_sym"}})
    monkeypatch.setattr(partreel, "get_bytes",
                        lambda url, hosts:
                        b"(kicad_symbol_lib\n\t(version 20240101)\n")
    monkeypatch.setattr(partreel, "_local_kicad_version", lambda: 20251024)

    written = partreel.PartReelProvider().download("p", tmp_path)
    assert "symbol_warning" not in written


def test_one_failed_artefact_does_not_lose_the_others(monkeypatch,
                                                      tmp_path):
    from eda_agent.libimport.providers import partreel
    from eda_agent.libimport.providers._http import FetchError

    monkeypatch.setattr(partreel.PartReelProvider, "fetch",
                        lambda self, pid: {
                            "id": "p", "files": {
                                "symbol": "https://partreel.com/a.kicad_sym",
                                "footprint": "https://partreel.com/a.kicad_mod",
                            }})

    def flaky(url, hosts):
        if url.endswith(".kicad_sym"):
            raise FetchError("500 from registry")
        return b"(footprint (version 20251024)"

    monkeypatch.setattr(partreel, "get_bytes", flaky)
    written = partreel.PartReelProvider().download("p", tmp_path)
    assert "footprint" in written
    assert "500" in written["symbol_error"]


def test_a_source_with_nothing_to_download_says_so(only_fakes,
                                                   monkeypatch):
    """The same guarantee as the test below, on any machine.

    That one asserts against a real installed KiCad symbol, so it skips
    where KiCad is absent, which is exactly where regressions land
    unnoticed. This one uses a fake with no download method, so the
    behaviour stays covered in CI.
    """
    import asyncio

    from eda_agent.tools.parts import register_parts_tools

    fake = _Fake("nodownload", ["PART-1"])
    fake.fetch = lambda part_id: {"part_id": part_id}
    only_fakes(fake)
    monkeypatch.setattr(
        "eda_agent.libimport.providers.get_provider", lambda name: fake)

    captured = {}

    class _Capture:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    register_parts_tools(_Capture())
    out = asyncio.run(captured["part_fetch"](
        provider="nodownload", part_id="PART-1", download_dir="unused"))

    assert out["ok"] is True
    assert out["files"] == {}
    assert "no downloadable files" in out["download_note"]


@pytest.mark.skipif(not _kicad_local_available(),
                    reason="needs KiCad's libraries installed; this "
                           "asserts against a real symbol on disk")
def test_provider_without_download_says_so_rather_than_failing():
    """kicad_local locates a symbol already on disk; nothing to fetch."""
    import asyncio

    from eda_agent.tools.parts import register_parts_tools

    captured = {}

    class _Capture:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    register_parts_tools(_Capture())
    out = asyncio.run(captured["part_fetch"](
        provider="kicad_local",
        part_id="MCU_ST_STM32F1:STM32F103C8Tx",
        download_dir="unused"))
    assert out["ok"] is True
    assert out["files"] == {}
    assert "no downloadable files" in out["download_note"]


def test_part_fetch_is_not_advertised_as_readonly():
    """It writes library files when given download_dir.

    The "parts" category is offline, and offline falls back to READONLY,
    which would advertise a filesystem-touching tool as read-only. Every
    other file-writing tool in this server (lib_easyeda_import,
    lib_extract_cse_zip, proj_export_pdf, pcb_render_svg) is classified
    silent, so this matches them rather than inventing a third answer.
    """
    from eda_agent.tools.metadata import tool_metadata

    assert tool_metadata("part_fetch")["interaction"] == "silent"
    # part_search never writes, so it stays readonly.
    assert tool_metadata("part_search")["interaction"] == "readonly"


def test_every_provider_declares_what_it_yields():
    """Whether a hit is USABLE depends on the format it comes in.

    This server converts EasyEDA payloads to Altium but has NO
    KiCad->Altium path, so a kicad-only provider is a dead end for an
    Altium user. Making that visible before they spend time on a hit is
    the difference between a limitation and a trap.
    """
    for provider in available_providers():
        formats = getattr(provider, "formats", None)
        usable = getattr(provider, "usable_in", None)
        native = getattr(provider, "native_to", ())
        kind = getattr(provider, "kind", "library")
        assert kind in ("library", "catalogue"), (
            f"{provider.name} declares an unknown kind {kind!r}")
        if kind == "catalogue":
            # A catalogue yields identity and a datasheet, never
            # geometry. Declaring a format here would be a category
            # error, and would put the hit in front of an importer that
            # has nothing to import.
            assert not formats and not native, (
                f"{provider.name} is a catalogue yet declares formats or "
                f"native_to; a source that ships files is a library")
        else:
            assert formats or native, (
                f"{provider.name} declares neither formats nor native_to; "
                "a source must say what it yields, or that its parts are "
                "already in a backend's own format")
        assert usable, f"{provider.name} declares no usable_in"
        for backend in usable:
            assert backend in ("altium", "kicad"), (
                f"{provider.name} claims an unknown backend {backend!r}")


def test_a_client_points_at_its_own_service_and_stays_overridable(
        monkeypatch):
    """A default endpoint is not a preference.

    Every client here points at the service it is a client of: the
    Digi-Key client at Digi-Key, the registry client at the registry it
    was written against. What must not exist is RANKING, which the
    ordering and fan-out tests cover separately.

    The override matters just as much: the API shape is the contract,
    not the host, so an API-compatible registry must be substitutable
    without touching code.
    """
    from eda_agent.libimport.providers import partreel

    monkeypatch.delenv("PARTS_REGISTRY_URL", raising=False)
    assert partreel._base() == partreel._DEFAULT_BASE
    assert partreel._DEFAULT_BASE.startswith("https://")

    monkeypatch.setenv("PARTS_REGISTRY_URL", "https://other.invalid/")
    assert partreel._base() == "https://other.invalid", (
        "the registry must be substitutable, and a trailing slash must "
        "not survive into a joined URL")


def test_no_provider_is_consulted_only_when_another_comes_back_thin(
        only_fakes):
    """A fallback is a ranking wearing a different name.

    The fan-out must query every source unconditionally, so that a
    source cannot become "the one that answers when nothing else did",
    which is precisely the position a default endpoint could otherwise
    create.
    """
    from eda_agent.libimport.providers import search_all

    result = search_all("anything", 20)
    assert set(result["providers"]) == {p.name for p in
                                        available_providers()}, (
        "every provider must report a status, whether or not the others "
        "found anything")


def test_the_readme_table_lists_every_provider_with_its_real_kind():
    """The README states the provider set; so does the code.

    A fact stated in two places with nothing enforcing agreement is the
    defect shape that keeps surfacing in this project. Here the drift is
    especially quiet: a provider added to the registry but missing from
    the table is invisible to anyone reading the docs, and a table row
    whose `kind` disagrees with the class sends the reader looking for a
    symbol that a catalogue never had.
    """
    import re

    from tests import documentation_set

    # The provider table moved out of the README into PART_SOURCING when
    # the README was split, so the whole prose set is read rather than
    # one file.
    readme = documentation_set.prose_text()

    # Only the provider table: rows whose second cell is the kind.
    documented = {
        match.group(1): match.group(2)
        for match in re.finditer(
            r"^\|\s*`(\w+)`\s*\|\s*(library|catalogue)\s*\|",
            readme, re.MULTILINE)
    }
    assert documented, "the provider table is missing or reshaped"

    for provider in available_providers():
        assert provider.name in documented, (
            f"{provider.name} is registered but absent from the README "
            f"provider table")
        kind = getattr(provider, "kind", "library")
        assert documented[provider.name] == kind, (
            f"README calls {provider.name} a "
            f"{documented[provider.name]}, the code says {kind}")

    extra = set(documented) - {p.name for p in available_providers()}
    assert not extra, (
        f"the README documents providers that are not registered: "
        f"{sorted(extra)}")


def test_the_readme_names_the_env_var_each_catalogue_actually_reads():
    """A credential name is useless if it is the wrong one.

    The variable in the docs is what the user exports; the variable in
    the code is what gets read. Nothing else connects them, and a typo
    presents as "this provider never works" with no clue why.
    """
    import re

    from tests import documentation_set

    readme = documentation_set.prose_text()

    for provider in available_providers():
        for var in getattr(provider, "env_vars", ()):
            # Word-boundary, not substring. A README that documented
            # TME_SECRET_KEY while the code read TME_SECRET would
            # satisfy a plain `in` check and still leave the user
            # exporting a variable nothing reads.
            assert re.search(rf"\b{re.escape(var)}\b", readme), (
                f"{provider.name} reads {var} but the README never "
                f"names it exactly, so nobody can enable this source")


def test_altium_usability_matches_the_converters_that_exist():
    """A provider may only claim Altium if a conversion path exists.

    The claim has to be tied to a converter that is registered, not to
    an intention: a provider advertising Altium usability for a format
    nothing reads sends the user down a path that dead-ends.
    """
    from eda_agent.libimport.providers.base import (
        ALTIUM_CONVERTIBLE_FORMATS,
    )

    for provider in available_providers():
        if "altium" not in getattr(provider, "usable_in", ()):
            continue
        if getattr(provider, "kind", "library") == "catalogue":
            # Nothing is converted because nothing is yielded: the
            # usable_in claim covers the IDENTITY, which is tool-neutral.
            # Guarded rather than waved through, so a catalogue cannot
            # quietly start advertising files.
            assert not getattr(provider, "formats", ()), (
                f"{provider.name} is a catalogue yet yields formats")
            continue
        if "altium" in getattr(provider, "native_to", ()):
            # Already an Altium symbol, so there is no conversion to
            # dead-end in. Nativeness must be DECLARED, not inferred
            # from an empty formats tuple, or omitting formats would
            # become a way around this check.
            assert not getattr(provider, "formats", ()), (
                f"{provider.name} claims to be native to Altium yet also "
                "yields files to convert; it must be one or the other")
            continue
        formats = set(getattr(provider, "formats", ()))
        convertible = formats & set(ALTIUM_CONVERTIBLE_FORMATS)
        assert convertible, (
            f"{provider.name} claims Altium usability but none of its "
            f"formats {sorted(formats)} appears in "
            f"ALTIUM_CONVERTIBLE_FORMATS; the claim must point at a "
            f"converter that exists")


def test_every_declared_converter_is_a_registered_tool():
    """The converter map must name real tools, not intentions.

    A renamed or removed importer would otherwise leave a provider
    advertising a path that no longer exists.
    """
    from eda_agent.libimport.providers.base import (
        ALTIUM_CONVERTIBLE_FORMATS,
    )
    from eda_agent.tools import register_backend
    from eda_agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_backend(registry, "altium", "full")
    for fmt, tool in ALTIUM_CONVERTIBLE_FORMATS.items():
        assert tool in registry, (
            f"format {fmt} claims converter {tool}, which is not a "
            f"registered tool")


def test_a_hit_names_the_tool_that_converts_it():
    """A hit alone cannot say whether it is usable, so search says it.

    The formats live on the provider, not on the hit, so a caller
    reading only the result list could not tell that a KiCad-format part
    is usable on Altium at all. That is the single fact that decides
    whether a hit is a lead or a dead end.
    """
    from eda_agent.libimport.providers import _describe

    class _Fake:
        name = "fake"
        formats = ("kicad_sym", "kicad_mod")
        usable_in = ("kicad", "altium")

    hit = PartHit(provider="fake", part_id="X", mpn="X")
    out = _describe(hit, _Fake())
    assert out["formats"] == ["kicad_sym", "kicad_mod"]
    assert out["usable_in"] == ["kicad", "altium"]
    # Both formats map to the same importer; it must not be listed twice.
    assert out["import_with"] == ["lib_kicad_import"]


def test_a_format_with_no_converter_is_never_advertised():
    """Absence of a converter has to read as absence, not as silence.

    An empty ``import_with`` is the honest answer for a format nothing
    reads. Falling back to some default importer would be worse than
    saying nothing, because the caller would act on it.
    """
    from eda_agent.libimport.providers import _describe

    class _Fake:
        name = "fake"
        formats = ("some_format_nothing_reads",)
        usable_in = ("kicad",)

    out = _describe(PartHit(provider="fake", part_id="X"), _Fake())
    assert out["import_with"] == []


def test_import_with_only_ever_names_registered_tools():
    """Guards the whole surface, not just the one constant.

    ``import_with`` is what an agent acts on directly, so a stale entry
    would produce a call to a tool that does not exist.
    """
    from eda_agent.tools import register_backend
    from eda_agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_backend(registry, "altium", "full")

    for provider in available_providers():
        out = _describe_for(provider)
        for tool in out:
            assert tool in registry, (
                f"provider {provider.name} advertises {tool}, which is "
                f"not a registered tool")


def _describe_for(provider) -> list:
    from eda_agent.libimport.providers import _describe

    hit = PartHit(provider=provider.name, part_id="X")
    return _describe(hit, provider)["import_with"]


# ------------------ symbol-to-whole-part resolution ------------------

@pytest.fixture
def fake_kicad_tree(tmp_path, monkeypatch):
    """A miniature KiCad install, so these tests need no real one."""
    symbols = tmp_path / "symbols"
    footprints = tmp_path / "footprints"
    (footprints / "PKG.pretty").mkdir(parents=True)
    symbols.mkdir()

    def symbol(name, footprint_ref):
        ref = f'(property "Footprint" "{footprint_ref}" (at 0 0 0))'
        return (f'  (symbol "{name}"\n'
                f'    (property "Reference" "U" (at 0 0 0))\n'
                f'    {ref}\n'
                f'    (symbol "{name}_1_1"\n'
                f'      (pin input line (at -5.08 0 0) (length 2.54)\n'
                f'        (name "A") (number "1")))\n'
                f'  )')

    (symbols / "LIB.kicad_sym").write_text(
        "(kicad_symbol_lib (version 20251024) (generator t)\n"
        + symbol("HAS_FP", "PKG:REAL") + "\n"
        + symbol("MISSING_FP", "PKG:NOT_INSTALLED") + "\n"
        + symbol("NO_FP", "") + "\n"
        + symbol("TRAVERSAL", "../outside:SECRET") + "\n)",
        encoding="utf-8")
    body = ('(footprint "F" (layer "F.Cu")\n'
            '  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu")))')
    (footprints / "PKG.pretty" / "REAL.kicad_mod").write_text(
        body, encoding="utf-8")
    # A real file the traversal reference would reach, so the guard is
    # what stops it rather than the target happening not to exist.
    outside = tmp_path / "outside.pretty"
    outside.mkdir()
    (outside / "SECRET.kicad_mod").write_text(body, encoding="utf-8")

    monkeypatch.setenv("EDA_AGENT_KICAD_SYMBOL_DIR", str(symbols))
    monkeypatch.setenv("EDA_AGENT_KICAD_FOOTPRINT_DIR", str(footprints))
    return tmp_path


def test_a_symbols_footprint_is_resolved_to_a_real_file(fake_kicad_tree):
    """A symbol alone is half a part.

    Converted to Altium without this, the part arrives with no land
    pattern and the caller has no way to know one was available.
    """
    from eda_agent.libimport.providers.kicad_local import KicadLocalProvider

    out = KicadLocalProvider().fetch("LIB:HAS_FP")
    assert out["footprint_ref"] == "PKG:REAL"
    assert out["footprint_path"].endswith("REAL.kicad_mod")
    assert "Whole part" in out["note"]


def test_an_unresolvable_reference_reads_differently_from_none(
        fake_kicad_tree):
    """"Names a footprint you do not have" and "names none" differ.

    Only the first is worth chasing, so they must not collapse into the
    same message.
    """
    from eda_agent.libimport.providers.kicad_local import KicadLocalProvider

    provider = KicadLocalProvider()
    missing = provider.fetch("LIB:MISSING_FP")
    none = provider.fetch("LIB:NO_FP")

    assert missing["footprint_path"] == ""
    assert missing["footprint_ref"] == "PKG:NOT_INSTALLED"
    assert "not installed" in missing["note"]

    assert none["footprint_path"] == ""
    assert none["footprint_ref"] == ""
    assert "records no footprint" in none["note"]
    assert missing["note"] != none["note"]


def test_a_footprint_reference_cannot_escape_the_library_root(
        fake_kicad_tree):
    """The reference is data read out of a file, so treat it as input.

    The target of the traversal is a file that really exists, so this
    fails if the containment check is removed rather than passing
    because the path happened to lead nowhere.
    """
    from eda_agent.libimport.providers.kicad_local import (
        KicadLocalProvider,
        _resolve_footprint,
    )

    escaped = fake_kicad_tree / "outside.pretty" / "SECRET.kicad_mod"
    assert escaped.is_file(), "fixture must present a reachable target"
    assert _resolve_footprint("../outside:SECRET") is None
    assert KicadLocalProvider().fetch("LIB:TRAVERSAL")["footprint_path"] == ""


def test_locating_a_symbol_still_works_if_parsing_fails(
        fake_kicad_tree, monkeypatch):
    """A footprint lookup must not turn a good fetch into a failure."""
    from eda_agent.libimport.providers import kicad_local

    def boom(*a, **k):
        raise ValueError("unreadable")

    monkeypatch.setattr(
        "eda_agent.libimport.kicad.reader.read_kicad_symbol", boom)
    out = kicad_local.KicadLocalProvider().fetch("LIB:HAS_FP")
    assert out["symbol"] == "HAS_FP"
    assert out["footprint_path"] == ""


def test_describe_reports_the_symbols_own_datasheet(fake_kicad_tree):
    """The normalised view is what makes sources comparable.

    Without it, part_fetch returns a null summary for this provider and
    a caller comparing sources has nothing to compare.
    """
    from eda_agent.libimport.providers.kicad_local import KicadLocalProvider

    hit = KicadLocalProvider().describe("LIB:HAS_FP")
    assert hit.provider == "kicad_local"
    assert hit.package == "REAL"          # from the footprint reference
    assert hit.license                     # never silently blank
    assert hit.provenance


def test_describe_never_invents_an_mpn(fake_kicad_tree):
    """A symbol name is not a part number.

    Copying it into the MPN field would put a fabricated part number
    into a BOM, which is worse than an admitted blank.
    """
    from eda_agent.libimport.providers.kicad_local import KicadLocalProvider

    assert KicadLocalProvider().describe("LIB:HAS_FP").mpn == ""


def test_one_part_is_parsed_once_however_many_views_are_asked_for(
        fake_kicad_tree, monkeypatch):
    """part_fetch calls fetch AND describe for the same part.

    These libraries run to several megabytes, so parsing twice would
    double the cost of every call for nothing.
    """
    import eda_agent.libimport.kicad.reader as reader
    from eda_agent.libimport.providers.kicad_local import KicadLocalProvider

    calls = []
    real = reader.read_kicad_symbol
    monkeypatch.setattr(
        reader, "read_kicad_symbol",
        lambda *a, **k: (calls.append(1), real(*a, **k))[1])

    provider = KicadLocalProvider()
    provider.fetch("LIB:HAS_FP")
    provider.describe("LIB:HAS_FP")
    assert len(calls) == 1, "the same part was parsed more than once"

    provider.fetch("LIB:NO_FP")
    assert len(calls) == 2, "a different part must not reuse the memo"


def test_a_multi_unit_part_says_so_before_the_import(tmp_path, monkeypatch):
    """Converting one unit and stopping leaves most of the part behind.

    Nothing else in a fetch result would reveal that, so the count has
    to arrive with the fetch rather than only as a warning from the
    import that follows it.
    """
    from eda_agent.libimport.providers.kicad_local import KicadLocalProvider

    symbols = tmp_path / "symbols"
    symbols.mkdir()
    (symbols / "L.kicad_sym").write_text(
        '(kicad_symbol_lib (version 20251024) (generator t)\n'
        '  (symbol "DUAL"\n'
        '    (property "Reference" "U" (at 0 0 0))\n'
        '    (symbol "DUAL_1_1" (pin input line (at -5 0 0) (length 2)\n'
        '      (name "A") (number "1")))\n'
        '    (symbol "DUAL_2_1" (pin input line (at -5 0 0) (length 2)\n'
        '      (name "B") (number "2")))))',
        encoding="utf-8")
    monkeypatch.setenv("EDA_AGENT_KICAD_SYMBOL_DIR", str(symbols))

    out = KicadLocalProvider().fetch("L:DUAL")
    assert out["unit_count"] == 2
    assert "2 units" in out["units_note"]
