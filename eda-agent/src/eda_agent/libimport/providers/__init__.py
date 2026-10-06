# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Part providers, queried equally, with no default.

:func:`search_all` fans out to every enabled provider and returns their
hits attributed to their source. There is no preferred provider, no
fallback order, and no relevance ranking across sources: merged results
are ordered alphabetically by provider then by part, which carries no
quality judgement.

That neutrality is enforced by the code, not left to convention. There
is no "default provider" setting to point somewhere, and a caller that
wants one source names it explicitly. A preferred provider would quietly
become the answer to every query and hand its operator the whole tool
surface, which is exactly what a multi-provider layer exists to avoid.

A provider that cannot answer reports WHY. Fan-out never converts an
unavailable source into an empty result, because "the endpoint is gone"
and "no such part exists" are different answers and only one of them is
a reason to stop looking.

Enable a subset with ``EDA_AGENT_PART_PROVIDERS`` (comma-separated).
Unset means all of them; the variable selects, it never ranks.
"""

from __future__ import annotations

import os
from typing import Any

from eda_agent.libimport.providers.base import (
    ALTIUM_CONVERTIBLE_FORMATS,
    PartHit,
    PartProvider,
    ProviderError,
    ProviderUnavailable,
)
from eda_agent.libimport.providers.altium_local import (
    AltiumLocalProvider,
)
from eda_agent.libimport.providers.distributors import (
    DigiKeyProvider,
    Element14Provider,
    MouserProvider,
    NexarProvider,
    TmeProvider,
)
from eda_agent.libimport.providers.easyeda import EasyEdaProvider
from eda_agent.libimport.providers.kicad_local import KicadLocalProvider
from eda_agent.libimport.providers.partreel import PartReelProvider
from eda_agent.libimport.providers.public_libraries import (
    PublicLibrariesProvider,
)

__all__ = [
    "PartHit",
    "PartProvider",
    "ProviderError",
    "ProviderUnavailable",
    "available_providers",
    "get_provider",
    "search_all",
]

#: Registered in alphabetical order to make the absence of precedence
#: visible in the source. Adding one here must never imply a ranking.
#:
#: Two kinds sit side by side deliberately. The LIBRARY providers yield
#: geometry you can place; the CATALOGUE providers yield identity and a
#: datasheet and no geometry at all. They are not tiers and neither is a
#: fallback for the other: they answer different questions, and a search
#: reports which kind each hit came from rather than ordering one above
#: the other.
_ALL: tuple = (
    AltiumLocalProvider,
    DigiKeyProvider,
    EasyEdaProvider,
    Element14Provider,
    KicadLocalProvider,
    MouserProvider,
    NexarProvider,
    PartReelProvider,
    PublicLibrariesProvider,
    TmeProvider,
)


def available_providers() -> list[PartProvider]:
    """Every enabled provider, alphabetically by name."""
    selected = os.environ.get("EDA_AGENT_PART_PROVIDERS", "").strip()
    wanted = {p.strip().lower() for p in selected.split(",") if p.strip()}
    out = [cls() for cls in _ALL]
    if wanted:
        out = [p for p in out if p.name in wanted]
    return sorted(out, key=lambda p: p.name)


def get_provider(name: str) -> PartProvider:
    """One provider by name, for a caller that has already chosen."""
    key = str(name or "").strip().lower()
    for provider in available_providers():
        if provider.name == key:
            return provider
    known = ", ".join(p.name for p in available_providers()) or "none"
    raise ProviderError(f"unknown provider {name!r}; enabled: {known}")


def search_all(query: str, limit_per_provider: int = 20) -> dict[str, Any]:
    """Query EVERY enabled provider and merge the hits.

    Returns ``{"hits": [...], "providers": {name: status}, "count": n}``.

    One provider failing never suppresses the others, and its failure is
    reported per provider rather than folded into the result list, so a
    thin set of hits can be told apart from a source that was down.
    """
    hits: list[PartHit] = []
    status: dict[str, Any] = {}
    by_provider: dict[str, Any] = {}

    for provider in available_providers():
        try:
            found = provider.search(query, limit_per_provider)
        except ProviderUnavailable as exc:
            status[provider.name] = {"ok": False, "unavailable": str(exc)}
            continue
        except ProviderError as exc:
            status[provider.name] = {"ok": False, "error": str(exc)}
            continue
        except Exception as exc:  # noqa: BLE001 - one bad provider only
            status[provider.name] = {
                "ok": False, "error": f"{type(exc).__name__}: {exc}"}
            continue
        hits.extend(found)
        by_provider[provider.name] = provider
        status[provider.name] = {
            "ok": True,
            "count": len(found),
            "kind": getattr(provider, "kind", "library"),
            "formats": list(getattr(provider, "formats", ())),
            "usable_in": list(getattr(provider, "usable_in", ())),
            # Whether this project has ever exercised the client against
            # the live API with a real credential. Published rather than
            # assumed: the endpoints were measured, the response shapes
            # were not, and conflating those two would be the same
            # derived-instead-of-measured claim this project already
            # rejected once for tool maturity.
            "verified_live": bool(getattr(provider, "verified_live", True)),
        }

    # Neutral ordering. NOT relevance: sorting by anything else would
    # make one source systematically appear first.
    hits.sort(key=lambda h: h.sort_key())
    return {
        "count": len(hits),
        "providers": status,
        "hits": [_describe(h, by_provider.get(h.provider)) for h in hits],
        "by_mpn": _correlate(hits),
    }


def _describe(hit: PartHit, provider: Any) -> dict[str, Any]:
    """A hit plus the next step that turns it into a real part.

    A hit on its own says nothing about whether it can be used: the
    formats live on the provider, so a caller reading only the result
    could not tell that a KiCad-format part is usable on Altium. The
    answer is derived from ALTIUM_CONVERTIBLE_FORMATS, the same constant
    that gates a provider's ``usable_in`` claim, so a format with no
    converter can never be advertised as importable.
    """
    out = hit.to_dict()
    formats = list(getattr(provider, "formats", ()))
    out["formats"] = formats
    out["usable_in"] = list(getattr(provider, "usable_in", ()))
    # "library" = geometry you can place. "catalogue" = identity and a
    # datasheet, nothing to import. Without this the two are told apart
    # only by an EMPTY import_with list, and absence is far too quiet a
    # signal for a difference this consequential: a caller would find out
    # that a distributor hit has no symbol only after choosing the part.
    out["kind"] = getattr(provider, "kind", "library")
    tools = []
    for fmt in formats:
        tool = ALTIUM_CONVERTIBLE_FORMATS.get(fmt)
        if tool and tool not in tools:
            tools.append(tool)
    # Named per format rather than as one blanket tool: a provider
    # publishing several formats may need a different importer for each.
    out["import_with"] = tools
    return out


def _normalise_mpn(mpn: str) -> str:
    """Fold ONLY the cosmetic differences in how sources spell an MPN.

    Case, spaces and punctuation carry no meaning, so they are dropped
    before comparing.

    What this deliberately does NOT do is fold wildcards. KiCad writes
    ``STM32F103C8Tx`` where a registry writes ``STM32F103C8T6``, and the
    ``x`` is a family placeholder covering several variants with
    different packages and temperature grades. Treating them as one part
    would assert an equivalence this code cannot support, and the whole
    point of surfacing provenance is to avoid that kind of claim. They
    stay separate groups, and a human decides.
    """
    return "".join(c for c in str(mpn).lower() if c.isalnum())


def _correlate(hits: list[PartHit]) -> list[dict[str, Any]]:
    """Which providers carry each part, with no source preferred.

    Answers the question a multi-provider search actually raises: "who
    has this part, and what does each of them know about it?" Providers
    are listed alphabetically inside every group for the same reason the
    hit list is: any other order would read as a recommendation.
    """
    groups: dict[str, dict[str, Any]] = {}
    for hit in hits:
        key = _normalise_mpn(hit.mpn) or hit.part_id.lower()
        entry = groups.setdefault(key, {"mpn": hit.mpn, "providers": []})
        entry["providers"].append({
            "provider": hit.provider,
            "part_id": hit.part_id,
            # Surfaced per source, because they differ: a registry may
            # state provenance and a license where a symbol library
            # states neither, and that difference is the useful signal.
            # NOTE these come from the SEARCH hit. A provider whose
            # index is thinner than its detail endpoint (PartReel states
            # a license on fetch but not in the index) shows blank here,
            # and blank means "not stated in the index", not "none".
            "provenance": hit.provenance,
            "license": hit.license,
            "datasheet": hit.datasheet,
        })
    for entry in groups.values():
        entry["providers"].sort(key=lambda p: p["provider"])
        entry["provider_count"] = len(entry["providers"])
    # Group order follows the same neutral rule as the hit list.
    return sorted(groups.values(),
                  key=lambda e: (e["mpn"].lower(), -e["provider_count"]))
