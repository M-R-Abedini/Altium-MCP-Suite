# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""PartReel: an open, no-login registry of verified KiCad parts.

DISCLOSURE, because it affects how much weight to give this source: the
registry was proposed to this project by the person who runs it (issue
#12). It is included on equal footing with every other provider and is
NOT a default, which is the point of the provider layer.

Measured against the live service rather than taken from the proposal:

* ``/api/v1/parts.json`` returns the WHOLE index, 21,657 parts, 11.4 MB.
* ``/api/v1/parts/<id>.json`` returns detail: ``mpn_pattern``,
  ``datasheet``, ``dimensions_source``, ``license``, ``files``,
  ``provenance``, ``tier``, ``verified``.
* There is NO server-side search. ``?q=`` and ``?search=`` are accepted
  and silently ignored, returning the full index either way.

That last point drives the design here: searching means pulling the
index and filtering locally, so the index is cached on disk. Without
caching, every query would move 11.4 MB.

The index records carry only ``id/name/category/family/manufacturer/
keywords/pins/verified``; the provenance fields live on the per-part
detail, so a hit is enriched lazily and only when asked for.
"""

from __future__ import annotations

import os
import time as _time
from typing import Any

from eda_agent.libimport._names import safe_filename
from eda_agent.libimport.providers._http import (
    FetchError,
    get_bytes,
    get_json_cached,
)
from eda_agent.libimport.providers.base import (
    PartHit,
    ProviderError,
    ProviderUnavailable,
)

__all__ = ["PartReelProvider"]

#: Parsed index memo, shared across provider instances because
#: available_providers() builds a fresh provider per call.
_MEMO: Any = None
_MEMO_AT: float = 0.0


#: The registry this client is a client OF, exactly as the Digi-Key
#: client points at Digi-Key and the Mouser client at Mouser. Verified
#: live: ``/api/v1/parts.json`` serves 21,657 parts over plain HTTP with
#: no auth and no key.
#:
#: A default here is not a preference. The neutrality this module cares
#: about is about RANKING, and that is enforced elsewhere and by tests:
#: this source is registered alphabetically among the others, is queried
#: in the same fan-out, is never consulted as a fallback when another
#: source comes back thin, and has no path to the front of a result
#: list. Naming its own endpoint is what makes it a working peer rather
#: than a switch nobody turns on.
_DEFAULT_BASE = "https://partreel.com"


def _base() -> str:
    """The registry to query, overridable.

    ``PARTS_REGISTRY_URL`` points this client at any API-compatible
    registry, which is the reason the URL is a constant rather than
    hardcoded at each call site: the API shape is the contract, not the
    host. Unset, it queries the registry it was written against.
    """
    return (os.environ.get("PARTS_REGISTRY_URL", "").strip().rstrip("/")
            or _DEFAULT_BASE)


def _hosts() -> set[str]:
    from urllib.parse import urlparse

    host = (urlparse(_base()).hostname or "").lower()
    allowed = {host} if host else set()
    extra = os.environ.get("PARTS_REGISTRY_ASSET_HOSTS", "")
    allowed |= {h.strip().lower() for h in extra.split(",") if h.strip()}
    return allowed



def _declared_version(data: bytes) -> int:
    """The ``(version NNNNNNNN)`` a KiCad s-expression file declares."""
    import re

    m = re.search(rb"\(version\s+(\d{8})\)", data[:400])
    return int(m.group(1)) if m else 0


def _local_kicad_version() -> int:
    """Format version the INSTALLED KiCad writes, read from its own libs.

    Comparing against the local install is the only meaningful check:
    a file is not "too new" in the abstract, only relative to the KiCad
    that has to open it.
    """
    from eda_agent.libimport.providers.kicad_local import _symbol_dir

    root = _symbol_dir()
    if root is None:
        return 0
    for path in sorted(root.glob("*.kicad_sym"))[:1]:
        try:
            return _declared_version(path.read_bytes()[:400])
        except OSError:
            return 0
    return 0


class PartReelProvider:
    """Search and fetch from a PartReel-compatible registry."""

    name = "partreel"
    description = (
        "Open registry of verified KiCad parts, no login. Run by a third "
        "party (proposed in issue #12 by its operator). Point "
        "PARTS_REGISTRY_URL at any API-compatible registry to substitute "
        "another. Yields KiCad files, usable in Altium via "
        "lib_kicad_import.")

    formats = ("kicad_mod", "kicad_sym", "glb")
    usable_in = ("kicad", "altium")

    #: The index is large and changes slowly; a day is a reasonable
    #: bound on staleness against re-downloading 11.4 MB per query.
    index_ttl_s = 86400.0

    def _raw_index(self) -> list[dict[str, Any]]:
        url = f"{_base()}/api/v1/parts.json"
        try:
            data = get_json_cached(url, _hosts(), self.index_ttl_s)
        except FetchError as exc:
            raise ProviderUnavailable(f"partreel index: {exc}") from exc
        if isinstance(data, dict):
            for key in ("parts", "data", "results"):
                if isinstance(data.get(key), list):
                    return data[key]
            return []
        return data if isinstance(data, list) else []

    def _index(self) -> list[tuple[str, str, str, str, str]]:
        """Compact searchable index: (id, name, manufacturer, family, hay).

        The disk cache alone still costs ~0.2s per search, because it
        re-parses 11.8 MB of JSON every time and an MCP server answers
        many searches per session. Holding the PARSED index in memory
        would cost far more RAM than it saves, so this keeps only the
        five fields a search reads, with the haystack pre-lowered.

        Module-level rather than per-instance: available_providers()
        constructs a fresh provider for every call, so instance state
        would never be reused.
        """
        global _MEMO, _MEMO_AT
        now = _time.time()
        if _MEMO is not None and (now - _MEMO_AT) < self.index_ttl_s:
            return _MEMO

        compact: list[tuple[str, str, str, str, str]] = []
        for row in self._raw_index():
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", ""))
            manufacturer = str(row.get("manufacturer", ""))
            family = str(row.get("family", ""))
            keywords = row.get("keywords")
            hay = " ".join([
                name, manufacturer, family, str(row.get("category", "")),
                " ".join(str(k) for k in keywords)
                if isinstance(keywords, list) else "",
            ]).lower()
            compact.append((str(row.get("id", "")), name, manufacturer,
                            family, hay))
        _MEMO, _MEMO_AT = compact, now
        return compact

    def search(self, query: str, limit: int = 20) -> list[PartHit]:
        needle = str(query or "").strip().lower()
        if not needle:
            return []
        hits: list[PartHit] = []
        for part_id, name, manufacturer, family, hay in self._index():
            if needle not in hay:
                continue
            hits.append(PartHit(
                provider=self.name,
                part_id=part_id,
                mpn=name,
                manufacturer=manufacturer,
                description=family,
            ))
            if len(hits) >= max(1, int(limit)):
                break
        return hits

    def fetch(self, part_id: str) -> dict[str, Any]:
        pid = str(part_id or "").strip()
        if not pid:
            raise ProviderError("empty part id")
        # Guard the path segment: an id is registry data, and a slash or
        # traversal in it must not reshape the URL.
        if "/" in pid or ".." in pid:
            raise ProviderError(f"refusing suspicious part id: {pid!r}")
        url = f"{_base()}/api/v1/parts/{pid}.json"
        try:
            data = get_json_cached(url, _hosts(), self.index_ttl_s)
        except FetchError as exc:
            raise ProviderError(f"partreel fetch {pid}: {exc}") from exc
        if not isinstance(data, dict):
            raise ProviderError(f"partreel returned no detail for {pid}")
        return data


    #: Artefacts worth downloading, and the extension each must have.
    #: An allowlist rather than "whatever the payload names": the file
    #: URLs are registry data, and writing an arbitrary extension from
    #: untrusted JSON is how a download turns into an executable.
    DOWNLOADABLE = {
        "footprint": ".kicad_mod",
        "symbol": ".kicad_sym",
        "model_3d": ".glb",
    }

    def download(self, part_id: str, dest_dir) -> dict[str, str]:
        """Fetch this part's library files into ``dest_dir``.

        Returns ``{kind: written path}``. Only the kinds in
        :attr:`DOWNLOADABLE` are taken, and each is written with the
        extension this code expects rather than one derived from the
        URL: the registry names those files, and a name is not a
        promise about content.
        """
        from pathlib import Path

        detail = self.fetch(part_id)
        files = detail.get("files")
        if not isinstance(files, dict):
            return {}

        out = Path(dest_dir)
        out.mkdir(parents=True, exist_ok=True)
        # The id is registry data and becomes a filename.
        stem = safe_filename(detail.get("id") or part_id)

        written: dict[str, str] = {}
        for kind, suffix in self.DOWNLOADABLE.items():
            url = files.get(kind)
            if not isinstance(url, str) or not url:
                continue
            try:
                data = get_bytes(url, _hosts())
            except FetchError as exc:
                # One missing artefact must not lose the others.
                written[f"{kind}_error"] = str(exc)
                continue
            path = out / f"{stem}{suffix}"
            path.write_bytes(data)
            written[kind] = str(path)

            # A downloaded file that the installed KiCad cannot open is
            # not a successful download. Observed live: the registry
            # ships format 20260206 while KiCad 10.0.1 writes 20251024,
            # and its symbol parser refuses the newer file outright
            # ("Unable to load library"). The footprint parser is more
            # tolerant, so this is checked per file rather than assumed.
            declared = _declared_version(data[:400])
            local = _local_kicad_version()
            if declared and local and declared > local:
                written[f"{kind}_warning"] = (
                    f"file declares KiCad format {declared} but the "
                    f"installed KiCad writes {local}; it may refuse to "
                    f"open this file. Upgrade KiCad, or use the part's "
                    f"other formats.")
        return written

    def describe(self, part_id: str) -> PartHit:
        """A hit enriched with the provenance the detail endpoint holds."""
        d = self.fetch(part_id)
        files = d.get("files") if isinstance(d.get("files"), dict) else {}
        return PartHit(
            provider=self.name,
            part_id=str(d.get("id", part_id)),
            mpn=str(d.get("mpn_pattern") or d.get("name", "")),
            manufacturer=str(d.get("manufacturer", "")),
            description=str(d.get("description") or d.get("family", "")),
            datasheet=str(d.get("datasheet", "")),
            provenance=str(d.get("dimensions_source", "")),
            license=str(d.get("license", "")),
            extra={"files": files, "tier": d.get("tier"),
                   "verified": d.get("verified")},
        )
