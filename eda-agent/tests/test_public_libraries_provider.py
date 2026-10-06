# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Openly published KiCad libraries, indexed from GitHub.

This provider exists so that `partreel` is not the only source that
answers without a credential, so the assertions that matter are about
staying a good citizen of a host that owes us nothing: one request per
repository, a cache that makes repeat searches free, an honest
User-Agent, and a rate limit treated as "back off" rather than "no such
part".

One rule here is not a style preference but a published restriction.
`gitlab.com/robots.txt` carries `Disallow: /api/v*`, so the GitLab API
is off limits to this client and a test enforces it. KiCad's canonical
symbol repository lives there, which is why this provider serves
footprints and leaves KiCad symbols to `kicad_local`.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from eda_agent.libimport.providers import public_libraries as pl
from eda_agent.libimport.providers.base import (
    ProviderError,
    ProviderUnavailable,
)

_TREE = {
    "truncated": False,
    "tree": [
        {"type": "blob", "path": "Package_SO.pretty/SOIC-8.kicad_mod"},
        {"type": "blob", "path": "Package_SO.pretty/TSSOP-14.kicad_mod"},
        {"type": "blob", "path": "symbols/JLCPCB-Analog.kicad_sym"},
        {"type": "blob", "path": "Archived-Symbols/OLD-PART.kicad_sym"},
        {"type": "blob", "path": "README.md"},
        {"type": "tree", "path": "Package_SO.pretty"},
    ],
}


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Never read or write a developer's real cache during tests."""
    monkeypatch.setenv("EDA_AGENT_CACHE_DIR", str(tmp_path))


def _serve(monkeypatch, payload, calls=None):
    body = json.dumps(payload).encode("utf-8")

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _open(request, *a, **k):
        if calls is not None:
            calls.append(request.full_url if hasattr(request, "full_url")
                         else str(request))
        return _Response(body)

    monkeypatch.setattr(
        "eda_agent.libimport.providers.public_libraries."
        "urllib.request.urlopen", _open)


def _raise(monkeypatch, exc):
    def _boom(*a, **k):
        raise exc
    monkeypatch.setattr(
        "eda_agent.libimport.providers.public_libraries."
        "urllib.request.urlopen", _boom)


# ---- the published restriction ---------------------------------------

def test_this_provider_never_touches_the_gitlab_api():
    """gitlab.com/robots.txt says `Disallow: /api/v*`.

    Measured, not assumed. KiCad's canonical symbol repository is hosted
    there, which makes this the tempting shortcut precisely because it
    is the one place the index would be easiest to build.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(pl))
    # Only real string CONSTANTS, never comments or docstrings. The
    # module explains this rule in prose that necessarily names the
    # forbidden host, and a guard that scanned raw text would match its
    # own rationale and pass even after the rule was broken.
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)

    urls = [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value not in docstrings]

    offenders = [u for u in urls if "gitlab.com/api" in u.replace(" ", "")]
    assert not offenders, (
        f"gitlab.com/robots.txt disallows /api/v*, but this module builds "
        f"{offenders}; index GitHub instead")


def test_the_client_identifies_itself():
    """GitHub requires a User-Agent and blocks requests without one.

    Naming the project rather than impersonating a browser is what lets
    the host identify and, if it ever needs to, throttle this traffic.
    """
    assert pl._UA and "eda-agent" in pl._UA
    assert "Mozilla" not in pl._UA, (
        "impersonating a browser is not how to be a good citizen of an "
        "API that is being used by permission")


# ---- rate limiting ----------------------------------------------------

@pytest.mark.parametrize("code", [403, 429])
def test_a_rate_limit_is_unavailable_never_an_empty_result(monkeypatch, code):
    """GitHub answers an exhausted anonymous budget with 403, not 429.

    Both must mean "back off", because an empty list would tell the
    caller the footprint does not exist and end the search.
    """
    _raise(monkeypatch, urllib.error.HTTPError(
        "https://api.github.com/x", code, "rate limited", {}, None))

    with pytest.raises(ProviderUnavailable) as excinfo:
        pl.PublicLibrariesProvider().search("SOIC")
    assert "not evidence" in str(excinfo.value)


def test_a_network_failure_is_unavailable_not_empty(monkeypatch):
    _raise(monkeypatch, urllib.error.URLError("offline"))
    with pytest.raises(ProviderUnavailable):
        pl.PublicLibrariesProvider().search("SOIC")


# ---- the cache --------------------------------------------------------

def test_a_whole_index_costs_one_request_per_repository(monkeypatch):
    """Walking directories would cost hundreds and exhaust the budget."""
    calls: list[str] = []
    _serve(monkeypatch, _TREE, calls)

    pl._load_index(refresh=True)

    assert len(calls) == len(pl._SOURCES), (
        f"expected one request per repository, made {len(calls)}")
    assert all("recursive=1" in c for c in calls), (
        "a non-recursive listing would need one request per directory")


def test_a_second_search_costs_nothing(monkeypatch):
    """The cache is what keeps interactive search inside 60 per hour."""
    calls: list[str] = []
    _serve(monkeypatch, _TREE, calls)

    provider = pl.PublicLibrariesProvider()
    provider.search("SOIC")
    first = len(calls)
    provider.search("TSSOP")

    assert len(calls) == first, "the cached index must be reused"
    assert first > 0


def test_a_corrupt_cache_rebuilds_instead_of_failing(monkeypatch, tmp_path):
    (tmp_path / "eda-agent" / "public-libraries").mkdir(parents=True)
    (tmp_path / "eda-agent" / "public-libraries" / "index.json").write_text(
        "{not json", encoding="utf-8")
    _serve(monkeypatch, _TREE)

    assert pl._load_index(), "a corrupt cache must not be fatal"


def test_a_stale_cache_beats_no_answer_when_github_is_down(monkeypatch):
    """An outdated land pattern the user can audit beats "no such part"."""
    _serve(monkeypatch, _TREE)
    pl._load_index(refresh=True)

    _raise(monkeypatch, urllib.error.URLError("offline"))
    entries = pl._load_index(refresh=True)

    assert entries, "the stale cache should have been used"


# ---- what the index contains -----------------------------------------

def test_archived_parts_are_excluded(monkeypatch):
    """An archived part looks identical to a current one in a hit.

    Shipping a withdrawn land pattern is exactly what this project
    audits for elsewhere, so it is filtered at the index rather than
    left to the caller to notice.
    """
    _serve(monkeypatch, _TREE)
    paths = [e["path"] for e in pl._load_index(refresh=True)]

    assert not any("rchive" in p for p in paths), (
        "archived directories must not reach a result")
    assert any("SOIC-8" in p for p in paths)


def test_non_library_files_are_ignored(monkeypatch):
    _serve(monkeypatch, _TREE)
    paths = [e["path"] for e in pl._load_index(refresh=True)]
    assert not any(p.endswith(".md") for p in paths)


def test_a_truncated_tree_is_recorded_rather_than_looking_small(monkeypatch):
    """Silence here would present a partial index as a complete one."""
    _serve(monkeypatch, {"truncated": True, "tree": _TREE["tree"]})
    entries = pl._load_index(refresh=True)
    assert any(e.get("truncated") for e in entries)


# ---- hits -------------------------------------------------------------

def test_a_hit_is_addressable_and_states_its_licence(monkeypatch):
    _serve(monkeypatch, _TREE)
    hits = pl.PublicLibrariesProvider().search("SOIC-8")

    assert hits
    hit = hits[0]
    assert "::" in hit.part_id, "hits must be repository-qualified"
    assert hit.license, "the declared licence must travel with the hit"
    assert hit.provider == "public_libraries"


def test_two_repositories_can_hold_the_same_footprint_name(monkeypatch):
    """Qualifying by repository is what keeps both addressable."""
    _serve(monkeypatch, _TREE)
    hits = pl.PublicLibrariesProvider().search("SOIC-8", limit=50)
    assert len({h.part_id for h in hits}) == len(hits), (
        "part_ids must be unique across repositories")


def test_fetch_returns_a_raw_url_and_names_the_converter(monkeypatch):
    _serve(monkeypatch, _TREE)
    provider = pl.PublicLibrariesProvider()
    hit = provider.search("SOIC-8")[0]

    detail = provider.fetch(hit.part_id)
    assert detail["url"].startswith("https://raw.githubusercontent.com/")
    assert detail["format"] == "kicad_mod"
    assert "lib_kicad_import" in detail["note"]


def test_noassertion_is_not_presented_as_permissive(monkeypatch):
    """Blank or unclassified licence terms are not an all-clear."""
    _serve(monkeypatch, _TREE)
    provider = pl.PublicLibrariesProvider()
    detail = provider.fetch(provider.search("SOIC-8")[0].part_id)
    assert "not that it is unrestricted" in detail["note"]


def test_a_malformed_part_id_is_refused_clearly(monkeypatch):
    _serve(monkeypatch, _TREE)
    with pytest.raises(ProviderError) as excinfo:
        pl.PublicLibrariesProvider().fetch("no-separator")
    assert "::" in str(excinfo.value)
