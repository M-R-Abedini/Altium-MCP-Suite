# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Author KiCad symbol (.kicad_sym) library content.

The schematic-symbol counterpart to :mod:`kicad_footprint`: KiCad exposes no API
for creating symbols, but ``.kicad_sym`` files are text s-expressions, so
eda-agent generates them directly. This closes the symbol half of the
library-authoring gap versus Altium's ``lib_create_symbol``. Output validates by
rendering with ``kicad-cli sym export svg``.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from .kicad_footprint import _block_end, is_inside, is_plain_name


def standard_symbol_dirs(cli_path: Optional[str] = None) -> list[str]:
    """Directories holding KiCad ``.kicad_sym`` symbol libraries."""
    dirs: list[str] = []
    for key in ("KICAD9_SYMBOL_DIR", "KICAD_SYMBOL_DIR", "KICAD8_SYMBOL_DIR",
                "KICAD10_SYMBOL_DIR"):
        v = os.environ.get(key)
        if v and os.path.isdir(v):
            dirs.append(v)
    if cli_path:
        root = os.path.dirname(os.path.dirname(cli_path))
        cand = os.path.join(root, "share", "kicad", "symbols")
        if os.path.isdir(cand):
            dirs.append(cand)
    return dirs


def extract_symbol(lib_id: str, search_dirs: list[str]) -> Optional[str]:
    """Extract a symbol block ("Lib:Name") from a .kicad_sym library, renamed
    to its full lib_id for embedding in a schematic's lib_symbols. None if not
    found.

    The library half of the id becomes a FILENAME, so it has to be a
    plain name. Nothing upstream constrains it: ``DesignPlan.Part.lib_ref``
    is validated for length and nothing else, and it reaches here from a
    caller-supplied plan. Without this, an id could name a
    ``.kicad_sym`` anywhere on disk, and this function would report it as
    a symbol found in one of ``search_dirs``.

    The symbol name is not part of the path, so it is left alone. It is
    searched for inside the file's text.
    """
    if not lib_id or ":" not in lib_id:
        return None
    lib, name = lib_id.split(":", 1)
    if not is_plain_name(lib):
        return None
    for d in search_dirs:
        f = os.path.join(d, lib + ".kicad_sym")
        if not os.path.isfile(f) or not is_inside(d, f):
            continue
        try:
            txt = open(f, "r", encoding="utf-8").read()
        except OSError:
            continue
        key = '(symbol "%s"' % name
        i = txt.find(key)
        if i < 0:
            continue
        end = _block_end(txt, i)
        if end < 0:
            continue
        block = txt[i:end]
        # Top-level name becomes the full lib_id; sub-symbols keep the bare name.
        return block.replace(key, '(symbol "%s:%s"' % (lib, name), 1)
    return None


def _esc(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def _num(v: Any) -> str:
    f = float(v)
    return str(int(f)) if f == int(f) else str(f)


_PIN_TYPES = {
    "input", "output", "bidirectional", "tri_state", "passive", "free",
    "unspecified", "power_in", "power_out", "open_collector", "open_emitter",
    "no_connect",
}


def _pin_sexpr(p: dict[str, Any]) -> str:
    ptype = str(p.get("type", "passive")).lower()
    if ptype not in _PIN_TYPES:
        ptype = "passive"
    x, y = _num(p.get("x_mm", 0)), _num(p.get("y_mm", 0))
    angle = _num(p.get("angle", 0))
    length = _num(p.get("length", 2.54))
    name = _esc(p.get("name", "~"))
    number = _esc(p.get("number", ""))
    return (
        f'\t\t\t(pin {ptype} line\n'
        f'\t\t\t\t(at {x} {y} {angle})\n\t\t\t\t(length {length})\n'
        f'\t\t\t\t(name "{name}" (effects (font (size 1.27 1.27))))\n'
        f'\t\t\t\t(number "{number}" (effects (font (size 1.27 1.27))))\n'
        f'\t\t\t)')


def build_symbol(name: str, pins: list[dict[str, Any]],
                 reference: str = "U", body_w_mm: float = 10.16,
                 body_h_mm: float = 7.62) -> str:
    """The ``(symbol ...)`` block for one symbol with a body rectangle+pins."""
    hw, hh = _num(body_w_mm / 2), _num(body_h_mm / 2)
    nhw, nhh = _num(-body_w_mm / 2), _num(-body_h_mm / 2)
    lines = [
        f'\t(symbol "{_esc(name)}"',
        '\t\t(pin_names (offset 0.254))',
        '\t\t(in_bom yes)\n\t\t(on_board yes)',
        f'\t\t(property "Reference" "{_esc(reference)}"\n'
        f'\t\t\t(at 0 {_num(body_h_mm / 2 + 1.27)} 0)\n'
        '\t\t\t(effects (font (size 1.27 1.27))))',
        f'\t\t(property "Value" "{_esc(name)}"\n'
        f'\t\t\t(at 0 {_num(-body_h_mm / 2 - 1.27)} 0)\n'
        '\t\t\t(effects (font (size 1.27 1.27))))',
        f'\t\t(symbol "{_esc(name)}_0_1"',
        f'\t\t\t(rectangle\n\t\t\t\t(start {nhw} {hh})\n'
        f'\t\t\t\t(end {hw} {nhh})\n'
        '\t\t\t\t(stroke (width 0.254) (type default))\n'
        '\t\t\t\t(fill (type background))\n\t\t\t)',
        '\t\t)',
        f'\t\t(symbol "{_esc(name)}_1_1"',
    ]
    for p in pins:
        lines.append(_pin_sexpr(p))
    lines.append('\t\t)')
    lines.append('\t)')
    return "\n".join(lines)


def build_symbol_lib(name: str, pins: list[dict[str, Any]],
                     reference: str = "U", body_w_mm: float = 10.16,
                     body_h_mm: float = 7.62) -> str:
    """A complete single-symbol ``.kicad_sym`` library."""
    return ("(kicad_symbol_lib\n\t(version 20240101)\n"
            '\t(generator "eda-agent")\n'
            + build_symbol(name, pins, reference, body_w_mm, body_h_mm)
            + "\n)\n")


def insert_symbol(existing: str, name: str, pins: list[dict[str, Any]],
                  reference: str = "U", body_w_mm: float = 10.16,
                  body_h_mm: float = 7.62) -> str:
    """Insert a symbol into an existing ``.kicad_sym`` library string, before
    its final closing paren."""
    block = build_symbol(name, pins, reference, body_w_mm, body_h_mm)
    idx = existing.rstrip().rfind(")")
    if idx == -1:
        return build_symbol_lib(name, pins, reference, body_w_mm, body_h_mm)
    return existing.rstrip()[:idx] + block + "\n)\n"
