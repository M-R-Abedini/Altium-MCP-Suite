# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""SamacSys / Component Search Engine "Altium Designer" zip import (offline).

A CSE download is a zip holding a .SchLib and a .PcbLib (flat, or inside a
per-part folder, sometimes next to an .epw project file) and usually a STEP
3D model (.stp/.step). This module only inspects and stages:

* :func:`inspect_cse_zip` identifies the library members and a best-effort
  MPN without extracting anything.
* :func:`extract_cse_zip` extracts ONLY the recognized members (zip-slip
  protected, flattened into ``dest_dir``) and returns an ordered install
  plan whose steps map 1:1 onto the existing MCP tools
  (lib_install_library, lib_link_footprint, lib_link_3d_model).

Driving Altium with that plan is the caller's job. Two caveats are baked
into the plan and must be resolved at execution time:

* ``footprint_name`` is a guess (the PcbLib file stem). CSE names the
  footprint inside the PcbLib after the package, not the MPN; confirm the
  real name with lib_get_footprints before linking.
* lib_link_footprint operates on the active SchLib component, so the
  symbol must be focused first (see tools/library.py).

Both entry points follow the offline error shape: ``{"ok": False,
"reason": ...}`` on failure, ``{"ok": True, ...}`` on success.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Optional

_SCHLIB_EXT = ".schlib"
_PCBLIB_EXT = ".pcblib"
_STEP_EXTS = frozenset({".stp", ".step"})

# CSE names its download zips LIB_<MPN>.zip; strip that prefix when the
# zip filename is the only MPN source.
_LIB_PREFIX = "lib_"


def _normalize(member: str) -> str:
    """Zip member names use '/', but Windows-built zips may carry '\\'."""
    return member.replace("\\", "/")


def _member_escapes(member: str) -> bool:
    """True if extracting ``member`` under a destination could escape it.

    Flags absolute paths, drive-letter paths, and any whole '..' segment.
    Filenames merely containing dots ('a..b.SchLib') are fine.
    """
    norm = _normalize(member)
    if PureWindowsPath(member).drive or PureWindowsPath(norm).drive:
        return True
    posix = PurePosixPath(norm)
    if posix.is_absolute():
        return True
    return ".." in posix.parts


def _basename(member: str) -> str:
    return PurePosixPath(_normalize(member)).name


def _stem(member: str) -> str:
    return PurePosixPath(_normalize(member)).stem


def _suffix(member: str) -> str:
    return PurePosixPath(_normalize(member)).suffix.lower()


def _strip_lib_prefix(name: str) -> str:
    if name.lower().startswith(_LIB_PREFIX) and len(name) > len(_LIB_PREFIX):
        return name[len(_LIB_PREFIX):]
    return name


def _best_effort_mpn(
    zip_path: Path, schlib: Optional[str], pcblib: Optional[str]
) -> str:
    """MPN from the SchLib stem, else PcbLib stem, else the zip filename.

    CSE names the SchLib after the part; the PcbLib stem usually matches it
    too. The zip filename (LIB_<MPN>.zip) is the last resort.
    """
    for member in (schlib, pcblib):
        if member:
            stem = _stem(member)
            if stem:
                return _strip_lib_prefix(stem)
    return _strip_lib_prefix(zip_path.stem)


def inspect_cse_zip(path: str | Path) -> dict[str, Any]:
    """Identify the library members of a CSE download zip (read-only).

    Args:
        path: filesystem path to the downloaded zip.

    Returns:
        ``{"ok": True, "mpn": str, "schlib": member-name-or-None,
        "pcblib": ..., "step": ..., "extras": [member names],
        "suspicious": [member names attempting path traversal]}``.
        ``ok`` is False (with ``reason``) when the path is missing, not a
        zip, or contains neither a .SchLib nor a .PcbLib.

    Member selection is deterministic: candidates are taken in sorted
    member-name order, surplus candidates land in ``extras``.
    """
    zip_path = Path(path)
    if not zip_path.is_file():
        return {"ok": False, "reason": f"file not found: {zip_path}"}
    if not zipfile.is_zipfile(zip_path):
        return {"ok": False, "reason": f"not a zip archive: {zip_path}"}

    with zipfile.ZipFile(zip_path) as zf:
        names = sorted(n for n in zf.namelist() if not n.endswith("/"))

    def pick(predicate) -> Optional[str]:
        for name in names:
            if predicate(_suffix(name)):
                return name
        return None

    schlib = pick(lambda s: s == _SCHLIB_EXT)
    pcblib = pick(lambda s: s == _PCBLIB_EXT)
    step = pick(lambda s: s in _STEP_EXTS)

    recognized = {schlib, pcblib, step}
    extras = [n for n in names if n not in recognized]
    suspicious = [n for n in names if _member_escapes(n)]

    if schlib is None and pcblib is None:
        return {
            "ok": False,
            "reason": "no .SchLib or .PcbLib member found in archive",
            "extras": extras,
        }

    return {
        "ok": True,
        "mpn": _best_effort_mpn(zip_path, schlib, pcblib),
        "schlib": schlib,
        "pcblib": pcblib,
        "step": step,
        "extras": extras,
        "suspicious": suspicious,
    }


def extract_cse_zip(path: str | Path, dest_dir: str | Path) -> dict[str, Any]:
    """Extract the recognized members of a CSE zip and build an install plan.

    Only the recognized members (SchLib, PcbLib, STEP) are written, each
    flattened to ``dest_dir/<basename>``. Any member attempting path
    traversal (absolute, drive-letter, or '..' segment) rejects the whole
    archive: a tampered zip is not worth salvaging.

    Args:
        path: filesystem path to the downloaded zip.
        dest_dir: directory to stage the library files into (created if
            missing).

    Returns:
        ``{"ok": True, "mpn": str, "files": [abs paths written],
        "extracted": {"schlib"/"pcblib"/"step": abs path or None},
        "install_plan": [{"tool": ..., "params": {...}}, ...]}``.

    Install plan order (steps appear only when their inputs exist):
        1. lib_install_library(schlib)
        2. lib_install_library(pcblib)
        3. lib_link_footprint(mpn, <guessed footprint>, pcblib filename)
           -- needs both libs
        4. lib_link_3d_model(<guessed footprint>, step path)
           -- needs pcblib + step; offsets stay at the tool defaults
           (mils, ignored by Altium per tools/library.py)
    """
    info = inspect_cse_zip(path)
    if not info["ok"]:
        return info
    if info["suspicious"]:
        return {
            "ok": False,
            "reason": (
                "zip member path escapes the destination (zip-slip): "
                + ", ".join(info["suspicious"])
            ),
        }

    dest = Path(dest_dir).resolve()
    dest.mkdir(parents=True, exist_ok=True)

    members = {
        key: info[key]
        for key in ("schlib", "pcblib", "step")
        if info[key] is not None
    }

    extracted: dict[str, Optional[str]] = {
        "schlib": None,
        "pcblib": None,
        "step": None,
    }
    files: list[str] = []
    with zipfile.ZipFile(path) as zf:
        for key, member in members.items():
            target = (dest / _basename(member)).resolve()
            # _member_escapes already vetoed traversal names; this guards
            # the resolved write location itself.
            if not target.is_relative_to(dest):
                return {
                    "ok": False,
                    "reason": f"zip member escapes destination: {member}",
                }
            with zf.open(member) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            extracted[key] = str(target)
            files.append(str(target))

    mpn = info["mpn"]
    plan: list[dict[str, Any]] = []
    for key in ("schlib", "pcblib"):
        if extracted[key]:
            plan.append(
                {
                    "tool": "lib_install_library",
                    "params": {"library_path": extracted[key]},
                }
            )

    footprint_guess = _stem(members["pcblib"]) if extracted["pcblib"] else None
    if extracted["schlib"] and extracted["pcblib"]:
        plan.append(
            {
                "tool": "lib_link_footprint",
                "params": {
                    "component_name": mpn,
                    "footprint_name": footprint_guess,
                    "footprint_library": _basename(members["pcblib"]),
                },
            }
        )
    if extracted["pcblib"] and extracted["step"]:
        plan.append(
            {
                "tool": "lib_link_3d_model",
                "params": {
                    "component_name": footprint_guess,
                    "model_path": extracted["step"],
                },
            }
        )

    return {
        "ok": True,
        "mpn": mpn,
        "files": files,
        "extracted": extracted,
        "install_plan": plan,
    }
