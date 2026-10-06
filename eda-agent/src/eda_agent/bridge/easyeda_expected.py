# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Compare the running extension against the one this tree builds.

The editor runs an installed copy of the extension and nothing keeps
the two in step. EasyEDA installs by version, so importing a package
whose version is already installed has no effect, and the editor
continues to run the older code while every call still succeeds.

The build id is a hash of main.js with its own BUILD_ID line
neutralised. It is the value build.py stamps into the package, so this
compares code rather than a version string that may not have been
bumped.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Optional

#: The extension source, present when the server runs from a checkout.
#: An installed wheel has no extensions directory; the check then
#: reports nothing rather than guessing.
_ROOT = pathlib.Path(__file__).resolve().parents[3]
_EXTENSION_DIR = _ROOT / "extensions" / "easyeda"
_MAIN_JS = _EXTENSION_DIR / "main.js"
_MANIFEST = _EXTENSION_DIR / "extension.json"

#: EasyEDA blocks extension network access until this permission is
#: granted. Until then the editor never opens a socket, which on this
#: side is indistinguishable from the editor being closed, so it is
#: worth naming wherever a connection problem is reported.
PERMISSION_HINT = (
    "If the extension reports that external interaction for extensions "
    "and standalone scripts is not permitted, enable that permission in "
    "EasyEDA first. Until it is on, the editor never attempts a "
    "connection."
)

_cache: dict[str, Any] = {}


def _source_stamp() -> float:
    """Modification time of the extension source, or 0 when absent.

    The cache is keyed on this. Caching the build id outright means a
    rebuild during a running session is never noticed, so the check
    keeps comparing against the value read at startup and reports a
    match while the tree has moved on.
    """
    try:
        return _MAIN_JS.stat().st_mtime
    except OSError:
        return 0.0


def expected_build() -> Optional[str]:
    """The build id this tree's main.js would stamp, or None."""
    stamp = _source_stamp()
    if _cache.get("stamp") == stamp and "build" in _cache:
        return _cache["build"]
    _cache.clear()
    _cache["stamp"] = stamp
    value = None
    if _MAIN_JS.exists():
        import sys

        sys.path.insert(0, str(_EXTENSION_DIR))
        try:
            from build import build_id            # type: ignore[import]

            value = build_id(_MAIN_JS.read_text(encoding="utf-8"))
        except Exception:                          # noqa: BLE001
            value = None
        finally:
            if sys.path and sys.path[0] == str(_EXTENSION_DIR):
                sys.path.pop(0)
    _cache["build"] = value
    return value


def expected_version() -> Optional[str]:
    """The version in extension.json, for a message a reader can act on."""
    expected_build()          # refreshes the cache when the source moved
    if "version" in _cache:
        return _cache["version"]
    value = None
    if _MANIFEST.exists():
        try:
            value = str(json.loads(
                _MANIFEST.read_text(encoding="utf-8")).get("version") or "")
        except Exception:                          # noqa: BLE001
            value = None
    _cache["version"] = value or None
    return _cache["version"]


def package_path() -> Optional[str]:
    """The .eext to import, so the message names a file to open."""
    candidate = _EXTENSION_DIR / "eda-agent-bridge.eext"
    return str(candidate) if candidate.exists() else None


def check(reported_build: Optional[str]) -> dict[str, Any]:
    """Compare the reported build against this tree's.

    Returns an empty dict when the two agree or when either side cannot
    say, so a caller can merge the result unconditionally and add
    nothing in the normal case.
    """
    wanted = expected_build()
    if not wanted or not reported_build or reported_build == wanted:
        return {}
    version = expected_version()
    where = package_path() or "extensions/easyeda/eda-agent-bridge.eext"
    return {
        "extension_outdated": True,
        "extension_build_running": reported_build,
        "extension_build_expected": wanted,
        "extension_version_expected": version,
        "extension_action": (
            f"The editor is running extension build {reported_build}; "
            f"this server expects {wanted}"
            + (f", version {version}" if version else "")
            + f". Import {where} in EasyEDA Pro under Settings, "
              "Extensions. Importing a package whose version is already "
              "installed has no effect, so check the version changed. "
            + PERMISSION_HINT
        ),
    }
