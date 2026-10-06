# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Author KiCad footprint (.kicad_mod) files.

KiCad exposes no API or CLI for *creating* library content, but footprint files
are text s-expressions, so eda-agent generates them directly. This closes the
one genuine library-authoring gap versus Altium's ``lib_create_footprint``. The
output can be validated offline by rendering it with ``kicad-cli fp export svg``.
"""

from __future__ import annotations

import os
from typing import Any, Optional


def standard_footprint_dirs(cli_path: Optional[str] = None) -> list[str]:
    """Directories that hold KiCad ``.pretty`` footprint libraries.

    Checks the ``KICAD*_FOOTPRINT_DIR`` environment variables, then derives the
    bundled library path from the kicad-cli location (``<root>/bin/kicad-cli``
    -> ``<root>/share/kicad/footprints``).
    """
    dirs: list[str] = []
    for key in ("KICAD9_FOOTPRINT_DIR", "KICAD_FOOTPRINT_DIR",
                "KICAD8_FOOTPRINT_DIR", "KICAD10_FOOTPRINT_DIR"):
        v = os.environ.get(key)
        if v and os.path.isdir(v):
            dirs.append(v)
    if cli_path:
        root = os.path.dirname(os.path.dirname(cli_path))
        cand = os.path.join(root, "share", "kicad", "footprints")
        if os.path.isdir(cand):
            dirs.append(cand)
    return dirs


#: Characters that turn a name into a path. A library nickname and a
#: symbol or footprint name are IDENTIFIERS, and these functions treat
#: them as one path component each, so anything that could make a
#: component mean somewhere else is refused outright.
#:
#: The colon matters as much as the separators, and only on Windows:
#: ``os.path.join(d, "C:evil")`` returns ``"C:evil"``, because a drive
#: prefix makes the second argument absolute and the first is discarded
#: entirely. That is not traversal out of the search directory, it is
#: ignoring the search directory, and a rule written only against ``..``
#: would not have caught it.
_PATHISH = ("/", "\\", ":", "\x00")


def is_plain_name(part: str) -> bool:
    """Whether ``part`` is usable as a single path component.

    Deliberately a rejection of path syntax rather than an allow-list of
    characters. KiCad names carry ``+``, ``-``, ``.``, ``~`` and ``#``
    (``+3V3``, ``C_Polarized``, ``74HC00``), so enumerating what is
    permitted would be a guess about somebody else's naming rules and
    would refuse parts that exist.
    """
    if not part or part in (".", ".."):
        return False
    return not any(ch in part for ch in _PATHISH)


def is_inside(base: str, candidate: str) -> bool:
    """Whether ``candidate`` resolves to something under ``base``.

    The second half of the check, and the one that does not rely on
    having thought of every dangerous spelling. ``is_plain_name`` refuses
    the input; this confirms the OUTPUT landed where it was supposed to,
    after symlinks and ``..`` have been resolved.
    """
    try:
        b = os.path.realpath(base)
        c = os.path.realpath(candidate)
    except (OSError, ValueError):
        return False
    return c == b or c.startswith(b + os.sep)


def find_footprint_file(lib_id: str, search_dirs: list[str]) -> Optional[str]:
    """Resolve a footprint lib_id ("Lib:Name") to its .kicad_mod path, or None.

    A bare name (no ``:``) is looked up across every library.

    A lib_id that is not two plain names resolves to nothing. This
    function's contract is that it searches ``search_dirs``, and both
    halves of the id land in the path it builds, so without this a
    caller-supplied id could name a file outside them and still be
    answered as though it had been found in a library.
    """
    if not lib_id:
        return None
    if ":" in lib_id:
        lib, name = lib_id.split(":", 1)
        if not (is_plain_name(lib) and is_plain_name(name)):
            return None
        for d in search_dirs:
            cand = os.path.join(d, lib + ".pretty", name + ".kicad_mod")
            if os.path.isfile(cand) and is_inside(d, cand):
                return cand
        return None
    if not is_plain_name(lib_id):
        return None
    for d in search_dirs:
        try:
            for entry in os.listdir(d):
                if entry.endswith(".pretty"):
                    cand = os.path.join(d, entry, lib_id + ".kicad_mod")
                    if os.path.isfile(cand) and is_inside(d, cand):
                        return cand
        except OSError:
            continue
    return None


def _block_end(text: str, start: int) -> int:
    """Index just past the ')' that closes the '(' at ``start``."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def embed_footprint(mod_text: str, refdes: str, value: str,
                    x_mm: float, y_mm: float, pad_nets: dict[str, str],
                    net_num: dict[str, int], uid) -> Optional[str]:
    """Adapt a .kicad_mod footprint into a placed board footprint.

    Sets the position and uuid, overrides the Reference/Value, and injects
    ``(net N "NAME")`` into each pad whose number is in ``pad_nets``. Returns
    None if the text is not a footprint.
    """
    import re
    t = mod_text.strip()
    m = re.match(r'\(footprint\s+"[^"]*"', t)
    if not m:
        return None
    t = (t[:m.end()]
         + f'\n\t(at {_num(x_mm)} {_num(y_mm)})\n\t(uuid "{uid()}")'
         + t[m.end():])
    t = re.sub(r'(\(property\s+"Reference"\s+")[^"]*(")',
               lambda mm: mm.group(1) + _esc(refdes) + mm.group(2), t, count=1)
    t = re.sub(r'(\(property\s+"Value"\s+")[^"]*(")',
               lambda mm: mm.group(1) + _esc(value) + mm.group(2), t, count=1)

    out: list[str] = []
    i = 0
    while True:
        j = t.find('(pad "', i)
        if j < 0:
            out.append(t[i:])
            break
        nm = re.match(r'\(pad\s+"([^"]*)"', t[j:])
        num = nm.group(1) if nm else ""
        end = _block_end(t, j)
        if end < 0:
            out.append(t[i:])
            break
        block = t[j:end]
        name = pad_nets.get(num)
        if name and net_num.get(name):
            block = (block[:-1].rstrip()
                     + f'\n\t\t(net {net_num[name]} "{_esc(name)}")\n\t)')
        out.append(t[i:j])
        out.append(block)
        i = end
    return "".join(out)


def _esc(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def _num(v: Any) -> str:
    # Compact numeric formatting (no trailing ".0" clutter) as KiCad emits.
    f = float(v)
    return str(int(f)) if f == int(f) else str(f)


def _pad_sexpr(p: dict[str, Any]) -> str:
    number = _esc(p.get("number", ""))
    ptype = str(p.get("type", "smd")).lower()
    shape = str(p.get("shape", "rect")).lower()
    x, y = _num(p.get("x_mm", 0)), _num(p.get("y_mm", 0))
    w, h = _num(p.get("w_mm", 1)), _num(p.get("h_mm", 1))
    if ptype in ("thru_hole", "th", "pth"):
        drill = _num(p.get("drill_mm", 0.8))
        return (f'\t(pad "{number}" thru_hole {shape}\n'
                f'\t\t(at {x} {y})\n\t\t(size {w} {h})\n'
                f'\t\t(drill {drill})\n\t\t(layers "*.Cu" "*.Mask")\n\t)')
    return (f'\t(pad "{number}" smd {shape}\n'
            f'\t\t(at {x} {y})\n\t\t(size {w} {h})\n'
            f'\t\t(layers "F.Cu" "F.Paste" "F.Mask")\n\t)')


def build_footprint(name: str, pads: list[dict[str, Any]],
                    descr: str = "", tags: str = "") -> str:
    """Build a valid .kicad_mod s-expression for a footprint with pads."""
    lines = [
        f'(footprint "{_esc(name)}"',
        "\t(version 20240108)",
        '\t(generator "eda-agent")',
        '\t(layer "F.Cu")',
    ]
    if descr:
        lines.append(f'\t(descr "{_esc(descr)}")')
    if tags:
        lines.append(f'\t(tags "{_esc(tags)}")')
    lines.append(
        '\t(property "Reference" "REF**"\n\t\t(at 0 -1 0)\n'
        '\t\t(layer "F.SilkS")\n'
        '\t\t(effects (font (size 1 1) (thickness 0.15)))\n\t)')
    lines.append(
        f'\t(property "Value" "{_esc(name)}"\n\t\t(at 0 1 0)\n'
        '\t\t(layer "F.Fab")\n'
        '\t\t(effects (font (size 1 1) (thickness 0.15)))\n\t)')
    for p in pads:
        lines.append(_pad_sexpr(p))
    lines.append(")")
    return "\n".join(lines) + "\n"
