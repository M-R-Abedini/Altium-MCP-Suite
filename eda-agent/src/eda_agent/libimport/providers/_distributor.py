# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Shared machinery for the distributor and aggregator catalogues.

These sources answer a different question from the library providers.
EasyEDA and the local libraries hand back GEOMETRY: a symbol, a
footprint, something to place. A distributor hands back IDENTITY: the
manufacturer part number, the datasheet URL, the lifecycle status, what
is actually in stock. You cannot place a Digi-Key hit on a schematic,
and pretending otherwise would be the more damaging kind of lie because
the caller only finds out after choosing the part.

So they declare ``kind = "catalogue"`` and no importable format at all.
What they are FOR is the step before the symbol: deciding which part to
use, and getting the datasheet that every other check in this project
measures against.

Every endpoint constant in this module was probed live before it was
written down. That matters more than it sounds: of eight candidate APIs
checked, three (SnapEDA, LCSC, Arrow's keyword path) answered 404 on the
URL recalled for them and were dropped rather than shipped broken. A
401 or 403 is the useful answer here, because it proves the host and
path exist and refused us only for lack of a credential, which is
exactly what an unconfigured provider should report.

CREDENTIALS: every provider here needs one, none ships with a default,
and an unconfigured provider raises :class:`ProviderUnavailable` naming
the exact environment variables it wants. That is deliberately the same
treatment the parts registry gets. A source that silently returned
nothing when unconfigured would read as "this part does not exist".
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Sequence

from eda_agent.libimport.providers.base import (
    PartHit,
    ProviderError,
    ProviderUnavailable,
)

__all__ = ["DistributorProvider"]

#: Network timeout. A part search is interactive, so a source that has
#: not answered by now should be reported as slow rather than block the
#: other providers in the fan-out.
_TIMEOUT = 20

#: Substrings that mark an error payload returned under HTTP 200.
#: Mouser does exactly this: an invalid API key comes back as 200 with
#: ``{"Errors":[{"Code":"Invalid",...}]}``, measured live. A client that
#: checked only the status code would report a rejected key as a
#: successful search that happened to match nothing, which is the single
#: worst failure mode this whole layer exists to prevent.
_ERROR_KEYS = ("Errors", "errors", "error", "ErrorMessage")


class DistributorProvider:
    """A credential-gated catalogue of part identity and datasheets.

    Subclasses supply the endpoint, the auth, and the response mapping.
    This base owns the parts that must not vary: refusing to run
    unconfigured, refusing to treat an error payload as an empty result,
    and never putting a credential where it could be logged.
    """

    #: What a hit from this source IS, as opposed to what it is about.
    #: "catalogue" = identity, datasheet, availability, NO geometry.
    #: "library" = symbol or footprint files you can actually import.
    kind = "catalogue"

    #: Nothing is downloadable, so nothing is convertible. This is a
    #: statement about the source, not an omission to be filled in later.
    formats: tuple = ()
    native_to: tuple = ()

    #: The identity and the datasheet are tool-neutral: an MPN is as
    #: useful to a KiCad user as to an Altium one. Nothing is imported
    #: from here, which ``kind`` states and ``formats`` confirms.
    usable_in = ("altium", "kicad")

    #: Whether this client has ever been exercised against the live API
    #: with a real credential. False means the ENDPOINT was verified to
    #: exist but the request and response shapes have not been confirmed
    #: by this project. Published rather than assumed, for the same
    #: reason tool maturity is measured rather than derived: a claim
    #: nobody checked is worth less than an honest blank.
    verified_live = False

    #: Environment variables this provider needs, all of them required.
    env_vars: tuple = ()

    name = ""
    description = ""

    # ---- credentials -------------------------------------------------

    def _credentials(self) -> dict[str, str]:
        """The configured credentials, or refuse and say what is missing.

        Names the variables rather than saying "not configured", because
        the caller cannot act on the latter.
        """
        found = {}
        missing = []
        for var in self.env_vars:
            value = os.environ.get(var, "").strip()
            if value:
                found[var] = value
            else:
                missing.append(var)
        if missing:
            raise ProviderUnavailable(
                f"{self.name} needs {' and '.join(missing)}; set "
                f"{'them' if len(missing) > 1 else 'it'} to enable this "
                f"source. No credential ships with this project.")
        return found

    # ---- transport ---------------------------------------------------

    def _request(
        self,
        url: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """One HTTP call, returning parsed JSON.

        Failures are classified rather than merged: a credential problem
        is :class:`ProviderUnavailable` (configure something), anything
        else is :class:`ProviderError` (the query failed). The fan-out
        reports those differently and only one of them means the part
        might still exist elsewhere.
        """
        request = urllib.request.Request(
            url, data=body, method=method,
            headers={"User-Agent": "eda-agent", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ProviderUnavailable(
                    f"{self.name} rejected the credential in "
                    f"{' / '.join(self.env_vars)} (HTTP {exc.code}). The "
                    f"endpoint is reachable, so this is a key problem, "
                    f"not an outage.") from exc
            if exc.code == 429:
                raise ProviderUnavailable(
                    f"{self.name} rate-limited this client (HTTP 429); "
                    f"the part may still exist.") from exc
            raise ProviderError(
                f"{self.name} returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderUnavailable(
                f"{self.name} unreachable: {exc}. This is not evidence "
                f"the part does not exist.") from exc

        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except ValueError as exc:
            raise ProviderError(
                f"{self.name} returned a body that is not JSON") from exc

        self._reject_error_payload(payload)
        return payload

    def _reject_error_payload(self, payload: Any) -> None:
        """Refuse an error delivered under a success status code.

        Measured against the live Mouser API, which answers an invalid
        key with HTTP 200 and an ``Errors`` array. Without this the
        search would look like it ran and matched nothing.
        """
        if not isinstance(payload, dict):
            return
        for key in _ERROR_KEYS:
            problem = payload.get(key)
            # An empty list or empty string is the SUCCESS case for these
            # APIs: they include the key unconditionally. Only a
            # populated value is an actual error.
            if not problem:
                continue
            detail = json.dumps(problem)[:200]
            raise ProviderUnavailable(
                f"{self.name} returned an error under HTTP 200: {detail}. "
                f"This is usually a rejected or missing credential; it is "
                f"NOT an empty result.")

    # ---- helpers for subclasses --------------------------------------

    @staticmethod
    def _query(url: str, params: dict[str, str]) -> str:
        return f"{url}?{urllib.parse.urlencode(params)}"

    def _hit(self, part_id: str, **fields: Any) -> PartHit:
        """A hit attributed to this source, with provenance stated.

        Provenance is filled in here rather than left to each subclass so
        that no catalogue hit can arrive claiming to be a verified part.
        """
        fields.setdefault(
            "provenance",
            f"{self.name} catalogue entry; identity and datasheet only, "
            f"no symbol or footprint")
        return PartHit(provider=self.name, part_id=str(part_id), **fields)

    # ---- contract ----------------------------------------------------

    def search(self, query: str, limit: int = 20) -> list[PartHit]:
        raise NotImplementedError

    def fetch(self, part_id: str) -> dict[str, Any]:
        """Detail for one part.

        The default re-runs the search and matches exactly, which every
        catalogue here supports without a second endpoint.
        """
        for hit in self.search(part_id, limit=50):
            if hit.part_id == part_id or hit.mpn == part_id:
                detail = hit.to_dict()
                detail["kind"] = self.kind
                detail["files"] = {}
                detail["note"] = (
                    f"{self.name} supplies part identity and a datasheet, "
                    f"NOT a symbol or footprint. Build the part from the "
                    f"datasheet, or find geometry through a library "
                    f"provider.")
                return detail
        raise ProviderError(f"{self.name} has no part {part_id!r}")


def first_string(source: Any, *paths: Sequence[str]) -> str:
    """First non-empty string reachable by any of ``paths``.

    Distributor payloads nest inconsistently and rename fields between
    API versions. Walking several candidate paths and taking the first
    hit keeps one renamed field from emptying a whole result, instead of
    raising on a shape that is merely different from the documented one.
    """
    for path in paths:
        node: Any = source
        for step in path:
            if isinstance(node, dict):
                node = node.get(step)
            else:
                node = None
                break
        if isinstance(node, (str, int, float)) and str(node).strip():
            return str(node).strip()
    return ""
