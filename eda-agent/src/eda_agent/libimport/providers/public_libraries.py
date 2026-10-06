# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Openly published KiCad libraries, no login and no key.

Exists so that ``partreel`` is not the only source that answers without
a credential. A single no-auth provider is a single point of dependence,
and the whole reason this layer fans out is that no one source should be
load-bearing.

WHAT THIS SERVES. Overwhelmingly FOOTPRINTS: land patterns you can
import when you know the part but not its geometry. That is measured,
not assumed, and it corrects a plausible-sounding guess: Digi-Key's
KiCad repository contains 936 footprints and ZERO symbols, and KiCad's
own footprint repository holds 12,011. Only the JLCPCB library ships
symbols, 20 libraries of them.

WHY NO KICAD SYMBOLS. Two independent reasons, either sufficient. The
GitHub mirror of ``kicad-symbols`` returns no ``.kicad_sym`` blobs at
all, because the modern layout stores each library as a
``.kicad_symdir`` DIRECTORY of per-symbol files. The canonical host for
that is GitLab, and **gitlab.com/robots.txt carries
``Disallow: /api/v*``**, which is exactly the endpoint an index would
have to walk. So this provider does not touch GitLab. KiCad's symbols
are already served by ``kicad_local``, which reads them off disk with no
network at all.

RESPECTING THE HOSTS. Every source here is reached through GitHub's
documented API rather than by scraping pages. The client identifies
itself in the User-Agent, which GitHub requires; it builds a whole index
in ONE recursive tree request per repository rather than walking
directories; it caches that index on disk so repeated searches cost
nothing; and it treats HTTP 429 as "back off", never as "no results".
Anonymous GitHub allows 60 requests an hour, and a full index of all
three repositories costs three.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from eda_agent.libimport.providers.base import (
    PartHit,
    ProviderError,
    ProviderUnavailable,
)

__all__ = ["PublicLibrariesProvider"]

_TIMEOUT = 25

#: How long a cached index stays good. These repositories change over
#: days, not minutes, and a stale footprint is far cheaper than
#: exhausting a 60-per-hour budget on every search.
_CACHE_TTL = 7 * 24 * 3600

#: GitHub requires a User-Agent and blocks requests without one. Naming
#: the project rather than impersonating a browser is the honest form,
#: and it lets the host identify this traffic if it ever needs to.
_UA = "eda-agent (https://github.com/salitronic/eda-agent)"

#: The repositories indexed, each reachable in one recursive tree call.
#: `licence` is what the GitHub API reports, NOT what this project
#: believes: two of the three report NOASSERTION, meaning they carry
#: terms the API could not classify, and that is surfaced rather than
#: smoothed into a confident-looking answer.
_SOURCES: tuple[dict[str, Any], ...] = (
    {
        "repo": "KiCad/kicad-footprints",
        "branch": "master",
        "licence": "NOASSERTION (see the repository)",
        "note": "KiCad's official footprint library",
    },
    {
        "repo": "Digi-Key/digikey-kicad-library",
        "branch": "master",
        "licence": "NOASSERTION (see the repository)",
        "note": "Digi-Key's published footprints; no symbols in this repo",
    },
    {
        "repo": "CDFER/JLCPCB-Kicad-Library",
        "branch": "main",
        "licence": "MIT",
        "note": "JLCPCB basic and preferred parts, symbols and footprints",
    },
)

_TREE_URL = "https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1"
_RAW_URL = "https://raw.githubusercontent.com/{repo}/{branch}/{path}"


def _cache_dir() -> Path:
    configured = os.environ.get("EDA_AGENT_CACHE_DIR", "").strip()
    root = Path(configured) if configured else Path.home() / ".cache"
    return root / "eda-agent" / "public-libraries"


def _fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            # GitHub answers an exhausted anonymous budget with 403 and a
            # rate-limit header, so the two codes mean the same thing
            # here. Never an empty result: the part may well exist.
            raise ProviderUnavailable(
                "GitHub rate-limited this client (anonymous requests are "
                "capped at 60 per hour). The cached index is used when "
                "present; this is not evidence the part does not exist."
            ) from exc
        raise ProviderError(
            f"GitHub returned HTTP {exc.code} for the library index") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderUnavailable(
            f"cannot reach GitHub to index the public libraries: {exc}"
        ) from exc
    except ValueError as exc:
        raise ProviderError("GitHub returned a non-JSON index") from exc


def _index_one(source: dict[str, Any]) -> list[dict[str, str]]:
    """Every library file in one repository, from a single request."""
    payload = _fetch_json(_TREE_URL.format(repo=source["repo"],
                                           branch=source["branch"]))
    entries = []
    for node in payload.get("tree", []):
        if node.get("type") != "blob":
            continue
        path = node.get("path", "")
        if not path.endswith((".kicad_sym", ".kicad_mod")):
            continue
        # Archived directories are kept out of results rather than
        # filtered by the caller: an archived part looks identical to a
        # current one in a hit, and shipping a withdrawn land pattern is
        # exactly the failure this project audits for elsewhere.
        if "rchive" in path:
            continue
        entries.append({
            "repo": source["repo"],
            "branch": source["branch"],
            "path": path,
            "licence": source["licence"],
        })
    if payload.get("truncated"):
        # Silence here would look like a small repository. Say it.
        entries.append({"repo": source["repo"], "branch": source["branch"],
                        "path": "", "licence": source["licence"],
                        "truncated": "1"})
    return entries


def _load_index(refresh: bool = False) -> list[dict[str, str]]:
    """The merged index, from disk when fresh enough.

    A cache miss costs one request per repository. A hit costs nothing,
    which is what keeps an interactive search inside a 60-per-hour
    budget.
    """
    path = _cache_dir() / "index.json"
    if not refresh and path.is_file():
        try:
            age = time.time() - path.stat().st_mtime
            if age < _CACHE_TTL:
                return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A corrupt cache must not be fatal; fall through and rebuild.
            pass

    merged: list[dict[str, str]] = []
    failures: list[str] = []
    for source in _SOURCES:
        try:
            merged.extend(_index_one(source))
        except ProviderError as exc:
            # One unreachable repository must not empty the others.
            failures.append(f"{source['repo']}: {exc}")

    if not merged:
        # Prefer a stale cache to no answer at all: an outdated land
        # pattern the user can audit beats "no such part".
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        raise ProviderUnavailable(
            "could not index any public library repository"
            + (": " + "; ".join(failures) if failures else ""))

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(merged), encoding="utf-8")
    except OSError:
        # An unwritable cache is a performance problem, not a failure.
        pass
    return merged


class PublicLibrariesProvider:
    """Openly published KiCad libraries, indexed from GitHub."""

    name = "public_libraries"
    description = (
        "Openly published KiCad libraries (KiCad's own footprints, "
        "Digi-Key's footprints, and JLCPCB symbols and footprints). No "
        "login and no key. Mostly LAND PATTERNS rather than symbols, "
        "which is what you want when the part is chosen but its geometry "
        "is not. Indexed through GitHub's documented API, one request "
        "per repository, cached on disk for a week.")

    kind = "library"
    formats = ("kicad_sym", "kicad_mod")
    usable_in = ("kicad", "altium")

    def _entries(self) -> list[dict[str, str]]:
        return [e for e in _load_index() if e.get("path")]

    def search(self, query: str, limit: int = 20) -> list[PartHit]:
        needle = (query or "").strip().lower()
        hits: list[PartHit] = []
        for entry in self._entries():
            path = entry["path"]
            stem = path.rsplit("/", 1)[-1]
            name = stem.rsplit(".", 1)[0]
            if needle and needle not in name.lower():
                continue
            is_symbol = path.endswith(".kicad_sym")
            hits.append(PartHit(
                provider=self.name,
                # Repository-qualified so the same footprint name in two
                # libraries stays addressable.
                part_id=f"{entry['repo']}::{path}",
                mpn="",
                description=(
                    f"{'symbol library' if is_symbol else 'footprint'} "
                    f"from {entry['repo']}"),
                package="" if is_symbol else name,
                provenance=f"openly published library: {entry['repo']}",
                license=entry.get("licence", ""),
                extra={"repo": entry["repo"], "branch": entry["branch"],
                       "path": path,
                       "format": "kicad_sym" if is_symbol else "kicad_mod"},
            ))
            if len(hits) >= limit:
                break
        return hits

    def fetch(self, part_id: str) -> dict[str, Any]:
        repo, _, path = (part_id or "").partition("::")
        if not repo or not path:
            raise ProviderError(
                f"part_id must be '<owner/repo>::<path>', got {part_id!r}")
        for entry in self._entries():
            if entry["repo"] == repo and entry["path"] == path:
                url = _RAW_URL.format(repo=repo, branch=entry["branch"],
                                      path=path)
                return {
                    "provider": self.name,
                    "part_id": part_id,
                    "repo": repo,
                    "path": path,
                    "url": url,
                    "license": entry.get("licence", ""),
                    "format": ("kicad_sym" if path.endswith(".kicad_sym")
                               else "kicad_mod"),
                    "files": {},
                    "note": (
                        "Convert with lib_kicad_import. The licence shown "
                        "is what the repository declares; NOASSERTION "
                        "means it carries terms that were not classified, "
                        "not that it is unrestricted."),
                }
        raise ProviderError(f"no {path!r} in {repo!r}")
