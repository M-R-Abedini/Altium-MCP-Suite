# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""EasyEDA / LCSC as a part provider.

Asymmetric on purpose, because the service is:

* FETCH by LCSC id works, unauthenticated, and is what the converter
  already uses.
* SEARCH does not exist any more. Verified against the live service:
  the EasyEDA search route answers 404/403 with an HTML error page and
  no auth challenge, LCSC's own global-search returns HTTP 200 carrying
  ``{"code": 404, "ok": false}``, and the LCSC results page is rendered
  client-side so fetching it yields zero part numbers.

So :meth:`search` raises ``ProviderUnavailable`` rather than returning
an empty list. The distinction matters in a multi-provider fan-out: an
empty list would read as "EasyEDA has no such part", which is a claim
this provider is in no position to make.
"""

from __future__ import annotations

from typing import Any

from eda_agent.libimport.providers.base import (
    PartHit,
    ProviderError,
    ProviderUnavailable,
)

__all__ = ["EasyEdaProvider"]


class EasyEdaProvider:
    """Fetch-by-id against EasyEDA's component API."""

    name = "easyeda"
    description = (
        "EasyEDA / LCSC component data, no login. Fetch by LCSC part "
        "number works; SEARCH is unavailable because the upstream "
        "endpoint was withdrawn (not a credentials problem). Get the "
        "part number from LCSC in a browser.")

    #: What a fetch yields, and which EDA tools can consume it. Stated
    #: because it decides whether a hit is usable at all: this server
    #: has an EasyEDA->Altium converter but NO KiCad->Altium path, so a
    #: KiCad-only provider is a dead end for an Altium user.
    formats = ("easyeda_json",)
    usable_in = ("altium", "kicad")

    def search(self, query: str, limit: int = 20) -> list[PartHit]:
        raise ProviderUnavailable(
            "EasyEDA/LCSC no longer expose a public part-search endpoint, "
            "so this provider cannot search. Fetching a known LCSC part "
            "number still works.")

    def fetch(self, part_id: str) -> dict[str, Any]:
        from eda_agent.libimport.easyeda.fetch import (
            EasyEdaFetchError,
            fetch_component_json,
        )

        try:
            return fetch_component_json(str(part_id))
        except EasyEdaFetchError as exc:
            raise ProviderError(f"easyeda fetch {part_id}: {exc}") from exc

    def describe(self, part_id: str) -> PartHit:
        from eda_agent.libimport.easyeda import parse_component

        comp = parse_component(self.fetch(part_id))
        return PartHit(
            provider=self.name,
            part_id=str(part_id),
            mpn=comp.mpn,
            manufacturer=comp.manufacturer,
            package=comp.package,
            description=comp.description,
            datasheet=comp.datasheet,
            # EasyEDA states no origin for its geometry, and saying so is
            # itself useful: an imported footprint is a vendor drawing,
            # not a datasheet-verified land pattern.
            provenance="",
            license="",
            extra={"warnings": list(comp.warnings)},
        )
