# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Online fetch for EasyEDA / LCSC component data.

Stdlib only (``urllib``), no new dependency. Hardened the same way the
CSE zip import is: HTTPS only, host allowlist, response size cap, and a
timeout, so a hostile or broken endpoint cannot hang a design session or
write somewhere it should not.

Endpoints are overridable through the environment because they are a
vendor implementation detail that has moved before:

* ``EASYEDA_API_BASE``   component/search base (default easyeda.com)
* ``EASYEDA_MODEL_BASE`` 3D model host (default modules.easyeda.com)
* ``EASYEDA_EXTRA_HOSTS`` comma list added to the allowlist

Nothing here is imported by the offline parsing path, so the converter
still works fully offline from a saved JSON payload.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

__all__ = [
    "EasyEdaFetchError",
    "fetch_component_json",
    "fetch_3d_model",
    "search_components",
]

_DEFAULT_API_BASE = "https://easyeda.com"
_DEFAULT_MODEL_BASE = "https://modules.easyeda.com"

#: Cap on any single response. Component JSON is tens of KB; a 3D model
#: is the large case and 50 MB is far beyond a legitimate one.
_MAX_BYTES = 50 * 1024 * 1024
_TIMEOUT_S = 30

_USER_AGENT = "eda-agent/0.4 (+https://github.com/salitronic/eda-agent)"


class EasyEdaFetchError(RuntimeError):
    """Any network / protocol failure, with a caller-friendly message."""


def _api_base() -> str:
    return os.environ.get("EASYEDA_API_BASE", _DEFAULT_API_BASE).rstrip("/")


def _model_base() -> str:
    return os.environ.get("EASYEDA_MODEL_BASE", _DEFAULT_MODEL_BASE).rstrip("/")


def _allowed_hosts() -> set[str]:
    hosts = set()
    for base in (_api_base(), _model_base()):
        host = urllib.parse.urlsplit(base).hostname
        if host:
            hosts.add(host.lower())
    extra = os.environ.get("EASYEDA_EXTRA_HOSTS", "")
    for h in extra.split(","):
        h = h.strip().lower()
        if h:
            hosts.add(h)
    return hosts


def _check_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise EasyEdaFetchError(
            f"refusing non-HTTPS URL: {url!r}")
    host = (parts.hostname or "").lower()
    allowed = _allowed_hosts()
    ok = any(host == a or host.endswith("." + a) for a in allowed)
    if not ok:
        raise EasyEdaFetchError(
            f"host {host!r} is not in the allowlist {sorted(allowed)}; "
            f"set EASYEDA_EXTRA_HOSTS to permit it")


def _get(url: str) -> bytes:
    _check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            data = resp.read(_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise EasyEdaFetchError(
            f"HTTP {exc.code} from {url}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise EasyEdaFetchError(f"cannot reach {url}: {exc.reason}") from exc
    except OSError as exc:
        raise EasyEdaFetchError(f"network error for {url}: {exc}") from exc
    if len(data) > _MAX_BYTES:
        raise EasyEdaFetchError(
            f"response from {url} exceeds the {_MAX_BYTES} byte cap")
    return data


def _get_json(url: str) -> dict[str, Any]:
    raw = _get(url)
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise EasyEdaFetchError(f"{url} did not return JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise EasyEdaFetchError(f"{url} returned {type(payload).__name__}, "
                                f"expected a JSON object")
    return payload


def _normalize_lcsc(lcsc_id: str) -> str:
    """Accept ``C12345``, ``c12345`` or a bare number."""
    s = str(lcsc_id).strip().upper()
    if not s:
        raise EasyEdaFetchError("empty LCSC id")
    if not s.startswith("C"):
        s = "C" + s
    if not s[1:].isdigit():
        raise EasyEdaFetchError(
            f"{lcsc_id!r} is not an LCSC id (expected C followed by digits)")
    return s


def fetch_component_json(lcsc_id: str) -> dict[str, Any]:
    """Raw component payload for an LCSC part number.

    Returns the JSON as served. Feed it to
    ``document.parse_component``; keeping fetch and parse separate is
    what lets the same payload be saved as a test fixture.
    """
    code = _normalize_lcsc(lcsc_id)
    url = f"{_api_base()}/api/products/{urllib.parse.quote(code)}/components"
    payload = _get_json(url)
    if not payload.get("success", True):
        raise EasyEdaFetchError(
            f"{code}: upstream reported failure "
            f"({payload.get('message') or 'no message'})")
    if not payload.get("result"):
        raise EasyEdaFetchError(f"{code}: no component data in the response")
    return payload


def search_components(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search LCSC/EasyEDA for parts matching ``query``.

    Returns a trimmed list of ``{lcsc_id, mpn, manufacturer, package,
    description}`` so a caller can pick before fetching the full payload.
    The upstream search response shape is not contractual, so every field
    is read defensively and a shape change degrades to blanks rather than
    an exception.
    """
    q = urllib.parse.quote(str(query).strip())
    if not q:
        raise EasyEdaFetchError("empty search query")
    url = f"{_api_base()}/api/products/search?wd={q}&limit={int(limit)}"
    try:
        payload = _get_json(url)
    except EasyEdaFetchError as exc:
        # Verified against the live service: this endpoint now answers
        # 404 (403 with a browser user-agent), and LCSC's own
        # wmsc global-search returns HTTP 200 carrying
        # {"code": 404, "ok": false, "msg": "static resource ..."}.
        # Neither is usable unauthenticated, so say so plainly instead
        # of surfacing a bare HTTP error the caller cannot act on.
        raise EasyEdaFetchError(
            "Part search has no usable machine endpoint (upstream said: "
            f"{exc}). This is not a credentials problem and logging in "
            "will not fix it: the route answers with an HTML error page "
            "and no auth challenge, LCSC's own search API returns an "
            "error body, and the LCSC results page is rendered "
            "client-side, so there is nothing to fetch or authenticate "
            "against. Search LCSC in a browser to get the part number, "
            "then import by id, which is unaffected and reliable: "
            "lib_easyeda_import(lcsc_id=\"C1234\", ...)."
        ) from exc

    result = payload.get("result") or {}
    rows = result.get("productList") or result.get("list") or []
    if not isinstance(rows, list):
        return []

    out: list[dict[str, Any]] = []
    for row in rows[: int(limit)]:
        if not isinstance(row, dict):
            continue
        attrs = row.get("dataStr", {}).get("head", {}).get("c_para", {}) \
            if isinstance(row.get("dataStr"), dict) else {}
        out.append({
            "lcsc_id": str(row.get("number")
                           or row.get("code")
                           or row.get("productCode") or "").strip(),
            "mpn": str(row.get("title")
                       or attrs.get("Manufacturer Part") or "").strip(),
            "manufacturer": str(row.get("manufacturer")
                                or attrs.get("Manufacturer") or "").strip(),
            "package": str(row.get("package")
                           or attrs.get("package") or "").strip(),
            "description": str(row.get("description") or "").strip(),
        })
    return out


def fetch_3d_model(uuid: str) -> bytes:
    """Raw 3D model bytes for a footprint's model uuid.

    EasyEDA serves an OBJ-family payload here; Altium wants STEP, so the
    caller may need a conversion step. Returned as bytes so this module
    never decides where a file lands.
    """
    u = str(uuid).strip()
    if not u:
        raise EasyEdaFetchError("empty 3D model uuid")
    return _get(f"{_model_base()}/3dmodel/{urllib.parse.quote(u)}")
