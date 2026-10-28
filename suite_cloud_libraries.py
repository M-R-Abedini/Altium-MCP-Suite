"""Public library discovery and immutable, untrusted CAD download staging.

No browser cookies, provider credentials, CAD automation or execution is used.
The only automated remote adapter is ChilaBoard's published GitHub catalog.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

REPOSITORY = "https://github.com/chilaboard/Altium-Library"
API = "https://api.github.com/repos/chilaboard/Altium-Library"
RAW = "https://raw.githubusercontent.com/chilaboard/Altium-Library"
CATALOG = "DB_Chilaboard_Altium-Library.csv"
CFB_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
LIBRARIES = {".schlib", ".pcblib", ".intlib"}
CAD = LIBRARIES | {".step", ".stp", ".epw"}
MAX_FILE = 64 * 1024 * 1024
MAX_EXPANDED = 128 * 1024 * 1024
TTL = 24 * 3600
VALIDATION = {"container": "not_checked", "pin_mapping": "unverified",
              "footprint_geometry": "unverified", "electrical_suitability": "unverified"}


def _schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False}


def list_tools():
    string = {"type": "string"}
    cache = {"type": "string", "description": "Absolute staging/cache directory; defaults to configured ALTIUM_SUITE_CLOUD_CACHE."}
    tools = [
        ("cloud_library_providers", "Show supported cloud library adapters, official access links and integration limitations.", _schema({}), True),
        ("cloud_library_search", "Search ChilaBoard's public component catalog; cache a revision-pinned index. Results are unverified library candidates, not validated components.",
         _schema({"query": string, "cache_dir": cache,
                  "kind": {"type": "string", "enum": ["all", "symbol", "footprint"]},
                  "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                  "offset": {"type": "integer", "minimum": 0},
                  "refresh": {"type": "boolean"}}, ["query"]), False),
        ("cloud_library_download", "Download a complete ChilaBoard SchLib/PcbLib selected from search into immutable staging, verifying its Git blob hash and recording source/license/SHA256. Does not install or validate pin mapping.",
         _schema({"library_path": string, "cache_dir": cache,
                  "revision": {"type": "string", "description": "Optional exact catalog revision returned by search; mismatch is rejected."}},
                 ["library_path"]), False),
        ("cloud_library_stage_local", "Stage an already downloaded local CAD library or ZIP from SnapMagic/SamacSys/another authorized source, retaining original bytes and provenance. Extracts CAD data only; never runs plugins or converts EPW.",
         _schema({"source_path": string, "cache_dir": cache,
                  "provider": {"type": "string", "enum": ["snapmagic", "samacsys", "other"]},
                  "source_url": string, "license_note": string},
                 ["source_path", "provider"]), False),
    ]
    return [{"name": name, "description": description, "inputSchema": schema,
             "annotations": {"readOnlyHint": readonly, "destructiveHint": False,
                             "idempotentHint": name != "cloud_library_stage_local",
                             "openWorldHint": name != "cloud_library_providers"}}
            for name, description, schema, readonly in tools]


def providers():
    return {"providers": [
        {"id": "chilaboard", "status": "public_catalog_and_download", "automated": True,
         "catalog_url": "https://chilaboard.github.io/Altium-Library/", "repository": REPOSITORY,
         "license": "Repository declares GPL-3.0; exact LICENSE retained per revision.",
         "download_unit": "whole .SchLib/.PcbLib, which can contain multiple components"},
        {"id": "snapmagic", "status": "official_plugin_or_api_access_required", "automated": False,
         "official_plugin_url": "https://www.snapeda.com/plugins/",
         "api_access_url": "https://snapeda.com/get-api/",
         "next_step": "Use the official logged-in Altium plugin/download, then stage the local CAD files. API integration requires documented access supplied by SnapMagic; no private endpoint is assumed.",
         "license": "Provider and downloaded file terms apply; not inferred from availability."},
        {"id": "samacsys", "status": "official_library_loader_handoff", "automated": False,
         "official_url": "https://supplyframe.com/samacsys",
         "search_url": "https://componentsearchengine.com/",
         "altium_support_url": "https://www.altium.com/documentation/knowledge-base/altium-designer/altium-library-loader-support",
         "next_step": "Download using the official account/Library Loader, let that tool convert EPW if needed, then stage its resulting SchLib/PcbLib. This adapter neither launches the loader nor accesses its account.",
         "license": "Provider and downloaded file terms apply; not inferred from availability."},
    ], "validation": dict(VALIDATION)}


def _integer(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _text(value, name, maximum=4096, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > maximum or "\x00" in value:
        raise ValueError(f"Invalid {name}")
    return value


def _root(value):
    value = value or os.environ.get("ALTIUM_SUITE_CLOUD_CACHE")
    if not value:
        raise ValueError("Set ALTIUM_SUITE_CLOUD_CACHE or pass an explicit absolute cache_dir")
    path = Path(_text(value, "cache_dir"))
    if not path.is_absolute():
        raise ValueError("cache_dir must be absolute")
    # Resolve explicit user-chosen root, then forbid redirected child paths.
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _child(root, *parts):
    path = root.joinpath(*parts)
    for part in [path, *list(path.parents)[:len(path.relative_to(root).parts)]]:
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("Cache contains a symlink/junction")
    if not path.resolve().is_relative_to(root):
        raise ValueError("Cache path escapes staging root")
    return path


def _immutable(path, data):
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ValueError("Cache object is a symlink/junction")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        try:
            # Same-directory hard-link publication is atomic and refuses an
            # existing destination. Readers never observe a partial object.
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or path.stat().st_size != len(data) or path.read_bytes() != data:
                raise ValueError(f"Existing cache object has changed: {path}")
    finally:
        temporary.unlink(missing_ok=True)
    return path


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError("Unexpected download redirect rejected")


def _fetch(url, limit, deadline):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"api.github.com", "raw.githubusercontent.com"} or parsed.username or parsed.password or parsed.port:
        raise ValueError("Unsupported remote download origin")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Cloud library operation deadline exceeded")
    request = urllib.request.Request(url, headers={"User-Agent": "mcp-altium-suite/cloud-libraries", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=min(15, remaining)) as response:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > limit:
                raise ValueError("Remote file exceeds size limit")
            data = bytearray()
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError("Cloud library operation deadline exceeded")
                chunk = response.read(min(65536, limit + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError("Remote file exceeds size limit")
            return bytes(data)
    except urllib.error.HTTPError as error:
        if error.code in {403, 429}:
            raise RuntimeError("Public GitHub request was rate-limited or forbidden; retry later or use the existing catalog cache.") from error
        raise RuntimeError(f"Public library request failed (HTTP {error.code})") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Public library network request failed: {error.reason}") from error


def _git_hash(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def _repo_path(value):
    _text(value, "library_path", 1000)
    _archive_path(value)
    path = PurePosixPath(value)
    if path.is_absolute() or "\\" in value or any(part in {"", ".", ".."} or ":" in part for part in value.split("/")):
        raise ValueError("Invalid repository library path")
    if path.suffix.lower() not in LIBRARIES or len(path.parts) != 2 or path.parts[0] not in {"AltiumSCHLIB", "AltiumPCBLIB"}:
        raise ValueError("Only catalog Altium library paths are accepted")
    return value


def _parse_catalog(data):
    try:
        text = data.decode("utf-8-sig")
        encoding = "utf-8-sig"
    except UnicodeDecodeError:
        text = data.decode("cp1252")
        encoding = "cp1252"
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, [])
    if not {"NAME", "TYPE", "DESCRIPTION", "PATH"}.issubset(header):
        raise ValueError("Unsupported ChilaBoard catalog columns")
    rows, skipped = [], 0
    for row in reader:
        if not row:
            continue
        if len(row) != len(header):
            skipped += 1
            continue
        item = dict(zip(header, row))
        if item["TYPE"].upper() not in {"PCBLIB", "SCHLIB"}:
            skipped += 1
            continue
        try:
            _repo_path(item["PATH"])
        except ValueError:
            skipped += 1
            continue
        # Last source column contains a URL header and timestamp values.
        rows.append({key: value for key, value in item.items() if not key.startswith("https://")})
    if not rows:
        raise ValueError("ChilaBoard catalog contains no usable library rows")
    return rows, skipped, encoding


def _catalog(root, refresh=False):
    pointer = _child(root, "chilaboard", "index.json")
    if pointer.exists() and not refresh:
        index = json.loads(pointer.read_text(encoding="utf-8"))
        if not re.fullmatch(r"[0-9a-f]{40}", str(index.get("revision", ""))):
            raise ValueError("Invalid cached catalog revision")
        if index.get("schema") == 1 and 0 <= time.time() - index["fetched_at"] < TTL:
            return index, True
    deadline = time.monotonic() + 45
    revision = json.loads(_fetch(API + "/commits/main", 2 * 1024 * 1024, deadline))["sha"]
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid upstream revision")
    tree = json.loads(_fetch(API + f"/git/trees/{revision}?recursive=1", 4 * 1024 * 1024, deadline))
    if tree.get("truncated"):
        raise ValueError("Incomplete upstream repository tree")
    blobs = {item["path"]: {"sha": item["sha"], "size": item.get("size", 0)}
             for item in tree["tree"] if item.get("type") == "blob"}
    data = _fetch(f"{RAW}/{revision}/{CATALOG}", 8 * 1024 * 1024, deadline)
    if _git_hash(data) != blobs.get(CATALOG, {}).get("sha"):
        raise ValueError("Catalog Git blob hash does not match pinned tree")
    license_data = _fetch(f"{RAW}/{revision}/LICENSE", 256 * 1024, deadline)
    if _git_hash(license_data) != blobs.get("LICENSE", {}).get("sha"):
        raise ValueError("License Git blob hash does not match pinned tree")
    rows, skipped, encoding = _parse_catalog(data)
    base = _child(root, "chilaboard", revision)
    _immutable(_child(root, "chilaboard", revision, CATALOG), data)
    _immutable(_child(root, "chilaboard", revision, "LICENSE"), license_data)
    index = {"schema": 1, "revision": revision, "fetched_at": time.time(), "repository": REPOSITORY,
             "catalog_sha256": hashlib.sha256(data).hexdigest(), "encoding": encoding,
             "skipped_rows": skipped, "rows": rows, "blobs": blobs,
             "license": {"declared": "GPL-3.0" if b"GNU GENERAL PUBLIC LICENSE" in license_data and b"Version 3" in license_data else "See retained LICENSE",
                         "source_url": f"{RAW}/{revision}/LICENSE", "path": str(base / "LICENSE"),
                         "sha256": hashlib.sha256(license_data).hexdigest()}}
    pointer.parent.mkdir(parents=True, exist_ok=True)
    temporary = _child(root, "chilaboard", uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, pointer)
    return index, False


def search(query, cache_dir=None, kind="all", limit=20, offset=0, refresh=False):
    query = _text(query, "query", 500, empty=True).casefold()
    if kind not in {"all", "symbol", "footprint"} or not isinstance(refresh, bool):
        raise ValueError("Invalid kind or refresh")
    _integer(limit, "limit", 1, 100)
    _integer(offset, "offset", 0, 1000000)
    index, hit = _catalog(_root(cache_dir), refresh)
    expected = {"symbol": "SCHLIB", "footprint": "PCBLIB"}.get(kind)
    tokens = query.split()
    matches = [row for row in index["rows"]
               if (not expected or row["TYPE"].upper() == expected)
               and all(token in " ".join(row.values()).casefold() for token in tokens)]
    return {"provider": "chilaboard", "revision": index["revision"], "catalog_cache_hit": hit,
            "catalog_fetched_at": index["fetched_at"], "catalog_sha256": index["catalog_sha256"],
            "catalog_encoding": index["encoding"], "skipped_rows": index["skipped_rows"],
            "total": len(matches), "offset": offset, "limit": limit,
            "results": [{"name": row["NAME"], "kind": "symbol" if row["TYPE"] == "SCHLIB" else "footprint",
                         "description": row["DESCRIPTION"], "library_path": row["PATH"],
                         "fields": row, "source_url": f"{RAW}/{index['revision']}/{urllib.parse.quote(row['PATH'], safe='/')}",
                         "validation": dict(VALIDATION)} for row in matches[offset:offset + limit]],
            "license": index["license"]}


def _inspect(data, suffix):
    result = dict(VALIDATION)
    if suffix.lower() in LIBRARIES:
        if len(data) < 512 or not data.startswith(CFB_MAGIC):
            raise ValueError("Library is not an Altium compound-file container (HTML/LFS pointers are rejected)")
        result["container"] = "compound_file_header_verified_only"
    return result


def download(library_path, cache_dir=None, revision=None):
    library_path = _repo_path(library_path)
    root = _root(cache_dir)
    index, _ = _catalog(root)
    if revision is not None and revision != index["revision"]:
        raise ValueError("Catalog revision changed; search again before downloading")
    if not any(row["PATH"] == library_path for row in index["rows"]):
        raise ValueError("Library is absent from the published component catalog")
    blob = index["blobs"].get(library_path)
    if not blob or blob["size"] > MAX_FILE:
        raise ValueError("Library is missing or exceeds the 64 MiB limit")
    url = f"{RAW}/{index['revision']}/{urllib.parse.quote(library_path, safe='/')}"
    path = _child(root, "chilaboard", index["revision"], *PurePosixPath(library_path).parts)
    hit = path.exists()
    if hit and path.stat().st_size > MAX_FILE:
        raise ValueError("Cached library exceeds size limit")
    data = path.read_bytes() if hit else _fetch(url, MAX_FILE, time.monotonic() + 30)
    if len(data) != blob["size"] or _git_hash(data) != blob["sha"]:
        raise ValueError("Downloaded/cached library does not match pinned Git blob")
    validation = _inspect(data, path.suffix)
    _immutable(path, data)
    manifest = {"schema": 1, "provider": "chilaboard", "repository": REPOSITORY,
                "revision": index["revision"], "source_url": url, "library_path": library_path,
                "path": str(path), "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "git_blob_sha1": blob["sha"], "license": index["license"],
                "validation": validation, "installed_into_altium": False,
                "next_step": "Read and validate the staged library with libraries backend. Verify MPN/package, symbol-to-pad numbers and datasheet geometry before placing into an explicit project."}
    metadata = path.with_name(path.name + ".provenance.json")
    _immutable(metadata, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    return {**manifest, "manifest_path": str(metadata), "download_cache_hit": hit}


def _archive_path(name):
    if not isinstance(name, str) or not name or len(name) > 1000 or "\\" in name or "\x00" in name:
        raise ValueError("Unsafe ZIP member name")
    parts = name.rstrip("/").split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{x}" for x in range(1, 10)), *(f"LPT{x}" for x in range(1, 10))}
    if any(part in {"", ".", ".."} or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
           or part.endswith((".", " ")) or part.split(".")[0].upper() in reserved for part in parts):
        raise ValueError("Unsafe ZIP member path")
    return parts


def stage_local(source_path, provider, cache_dir=None, source_url="", license_note=""):
    if provider not in {"snapmagic", "samacsys", "other"}:
        raise ValueError("Invalid local library provider")
    source = Path(_text(source_path, "source_path"))
    if not source.is_absolute() or not source.is_file() or source.is_symlink():
        raise ValueError("source_path must be an absolute regular file")
    suffix = source.suffix.lower()
    if suffix not in CAD | {".zip"}:
        raise ValueError("Supported local inputs: SchLib/PcbLib/IntLib/STEP/STP/EPW/ZIP")
    _text(source_url, "source_url", empty=True)
    _text(license_note, "license_note", empty=True)
    if source_url:
        parsed_source = urllib.parse.urlsplit(source_url)
        if parsed_source.scheme not in {"https", "http"} or not parsed_source.hostname or parsed_source.username or parsed_source.password:
            raise ValueError("source_url must be an HTTP(S) provenance link without credentials")
    if source.stat().st_size > MAX_FILE:
        raise ValueError("Input exceeds the 64 MiB limit")
    with source.open("rb") as input_file:
        data = input_file.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise ValueError("Input exceeds the 64 MiB limit")
    digest = hashlib.sha256(data).hexdigest()
    root = _root(cache_dir)
    # Full validation before publishing any original or extracted files.
    members = []
    if suffix == ".zip":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > 2000 or sum(info.file_size for info in infos) > MAX_EXPANDED:
                raise ValueError("Archive exceeds member/expanded-size limits")
            seen = set()
            for info in infos:
                # ZipInfo normalizes backslashes on Windows; orig_filename
                # retains the archive's untrusted name before normalization.
                parts = _archive_path(info.orig_filename)
                key = "/".join(parts).casefold()
                if key in seen:
                    raise ValueError("Duplicate/case-colliding ZIP member path")
                seen.add(key)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode) or info.flag_bits & 1:
                    raise ValueError("Symlink/encrypted ZIP member rejected")
                if info.is_dir():
                    continue
                if info.file_size > MAX_FILE or info.file_size / max(1, info.compress_size) > 200:
                    raise ValueError("Archive member exceeds expansion limits")
                name = PurePosixPath(info.filename)
                is_license = name.name.lower().startswith(("license", "licence", "copying", "notice")) and name.suffix.lower() in {"", ".txt", ".md"}
                if name.suffix.lower() not in CAD and not is_license:
                    continue
                content = archive.read(info)
                if len(content) != info.file_size:
                    raise ValueError("ZIP member size mismatch")
                validation = _inspect(content, name.suffix)
                members.append((parts, content, validation))
        if not any(Path(parts[-1]).suffix.lower() in CAD for parts, _, _ in members):
            raise ValueError("ZIP contains no supported CAD library/model files")
    else:
        members.append((["library" + suffix], data, _inspect(data, suffix)))
    base = _child(root, "local", provider, digest)
    original = _child(root, "local", provider, digest, "original" + suffix)
    _immutable(original, data)
    files = []
    for parts, content, validation in members:
        target = _child(root, "local", provider, digest, "files", *parts)
        _immutable(target, content)
        files.append({"path": str(target), "archive_path": "/".join(parts) if suffix == ".zip" else None,
                      "sha256": hashlib.sha256(content).hexdigest(), "size": len(content), "validation": validation})
    manifest = {"schema": 1, "provider": provider, "source_url": source_url or None,
                "source_path": str(source), "original_path": str(original), "original_sha256": digest,
                "license": {"status": "user_supplied_note" if license_note else "not_provided",
                            "note": license_note or "Review original provider/file terms before reuse or redistribution."},
                "files": files, "installed_into_altium": False,
                "conversion": "EPW conversion is not implemented; use the official Library Loader if required.",
                "next_step": "Inspect staged SchLib/PcbLib with libraries backend; verify against manufacturer datasheet. Only then import/place into the explicit project."}
    # Provenance may differ on repeated staging of identical bytes; keep each receipt.
    receipt = _child(root, "local", provider, digest, "receipts", uuid.uuid4().hex + ".json")
    _immutable(receipt, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    return {**manifest, "manifest_path": str(receipt), "staging_path": str(base)}


def call_tool(name, arguments=None):
    arguments = {} if arguments is None else arguments
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object")
    functions = {"cloud_library_providers": providers, "cloud_library_search": search,
                 "cloud_library_download": download, "cloud_library_stage_local": stage_local}
    if name not in functions:
        raise ValueError(f"Unknown cloud library tool: {name}")
    allowed = set(next(item["inputSchema"]["properties"] for item in list_tools() if item["name"] == name))
    if set(arguments) - allowed:
        raise ValueError("Unknown tool arguments: " + ", ".join(sorted(set(arguments) - allowed)))
    try:
        return functions[name](**arguments)
    except TypeError as error:
        raise ValueError(f"Invalid arguments for {name}: {error}") from error
