# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Turning third-party names into safe file names.

Shared by every importer, because they all take a name from a payload
(a vendor part title, a registry id) and use it as a path component.
One implementation rather than one per importer: a second copy drifts,
and the copy that misses a character is the one that crashes.

Real failures this prevents, both observed rather than theoretical:

* ``SOT-23/5 <BL>`` as a package title raised a bare ``OSError``
  (Errno 22) out of the MCP tool, because ``/`` and ``<>`` are illegal
  in a Windows path component.
* ``CON`` is a reserved device name and cannot be created with ANY
  extension, so ``CON.kicad_mod`` fails too.
* A name is untrusted input, so ``../../evil`` must not escape the
  directory the caller chose.
"""

from __future__ import annotations

__all__ = ["safe_filename"]

#: Characters Windows forbids anywhere in a path component.
_ILLEGAL = frozenset(r'<>:"/\|?*')

#: Reserved device names, rejected regardless of extension.
_RESERVED = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
})

#: Well under MAX_PATH once a directory and suffix are added.
_MAX_LEN = 120


def safe_filename(name: str, fallback: str = "part") -> str:
    r"""Make an untrusted name usable as a single path component.

    Returns ``fallback`` if nothing usable survives, so a caller never
    has to handle an empty string.
    """
    cleaned = "".join(
        "_" if (ch in _ILLEGAL or ord(ch) < 32) else ch
        for ch in str(name))
    # Trailing dots and spaces are illegal on Windows even when every
    # other character is fine.
    out = cleaned.strip(" .")
    if out.split(".")[0].upper() in _RESERVED:
        out = f"_{out}"
    return out[:_MAX_LEN] or fallback
