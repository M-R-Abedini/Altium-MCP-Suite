# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Emit a KiCad schematic (.kicad_sch) from a normalized parts+nets model.

This is the KiCad counterpart of Altium's design-execution step: turn an
abstract design (parts and the nets joining them) into a real KiCad schematic
file. Symbols are embedded as generated box symbols and connectivity is carried
by global labels (name-based), which is valid KiCad and renders/opens in
Eeschema. Wire routing is a later refinement; this establishes a valid emitter.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .kicad_symbol import _esc, _num, _pin_sexpr
from .placement_order import order_by_connectivity


def _embedded_symbol(name: str, n_pins: int) -> str:
    """A box symbol embedded in a .kicad_sch lib_symbols block. The schematic
    parser needs a fuller field set than a standalone .kicad_sym symbol."""
    bare = name.split(":", 1)[-1]  # sub-symbol names drop the library prefix
    per_side = (n_pins + 1) // 2
    h = max(7.62, per_side * 2.54 + 2.54)
    hw, hh = _num(15.24 / 2), _num(h / 2)
    nhw, nhh = _num(-15.24 / 2), _num(-h / 2)
    pins = []
    for i in range(n_pins):
        left = i < per_side
        row = i if left else i - per_side
        y = (h / 2 - 2.54) - row * 2.54
        pins.append(_pin_sexpr({
            "number": str(i + 1), "name": f"P{i + 1}",
            "x_mm": -7.62 if left else 7.62, "y_mm": y,
            "angle": 0 if left else 180, "type": "passive"}))
    def prop(k: str, v: str, y: str, hide: bool = False) -> str:
        hide_s = "\n\t\t\t\t(hide yes)" if hide else ""
        return (f'\t\t\t(property "{k}" "{v}"\n\t\t\t\t(at 0 {y} 0)\n'
                f'\t\t\t\t(show_name no)\n\t\t\t\t(do_not_autoplace no){hide_s}\n'
                '\t\t\t\t(effects (font (size 1.27 1.27)))\n\t\t\t)')
    # KiCad symbols require all four standard properties.
    props = "\n".join([
        prop("Reference", "U", _num(h / 2 + 1.27)),
        prop("Value", _esc(name), _num(-h / 2 - 1.27)),
        prop("Footprint", "", "0", hide=True),
        prop("Datasheet", "", "0", hide=True),
    ])
    return (
        f'\t\t(symbol "{_esc(name)}"\n'
        '\t\t\t(pin_names (offset 0.254))\n'
        '\t\t\t(exclude_from_sim no)\n\t\t\t(in_bom yes)\n'
        '\t\t\t(on_board yes)\n\t\t\t(in_pos_files yes)\n'
        '\t\t\t(duplicate_pin_numbers_are_jumpers no)\n'
        + props + "\n"
        # Sub-symbols use the bare name (no library prefix): "Device:R" -> "R".
        f'\t\t\t(symbol "{_esc(bare)}_0_1"\n'
        f'\t\t\t\t(rectangle\n\t\t\t\t\t(start {nhw} {hh})\n'
        f'\t\t\t\t\t(end {hw} {nhh})\n'
        '\t\t\t\t\t(stroke (width 0.254) (type default))\n'
        '\t\t\t\t\t(fill (type background))\n\t\t\t\t)\n\t\t\t)\n'
        f'\t\t\t(symbol "{_esc(bare)}_1_1"\n'
        + "\n".join(pins) + "\n\t\t\t)\n\t\t)")


def _grid(i: int, cols: int, dx: float, dy: float,
          x0: float, y0: float) -> tuple[float, float]:
    return x0 + (i % cols) * dx, y0 + (i // cols) * dy


def build_schematic(parts: list[dict[str, Any]], nets: list[dict[str, Any]],
                    uid: Optional[Callable[[], str]] = None,
                    title: str = "",
                    symbols: Optional[dict[str, str]] = None,
                    part_symbol: Optional[dict[str, str]] = None) -> str:
    """Build a valid .kicad_sch string placing every part and labelling nets.

    ``parts``: ``[{refdes, lib_ref?, value?, pin_count?}]``.
    ``nets``: ``[{name, nodes: [{refdes, pin}]}]`` (used for pin counts + labels).
    ``uid``: a UUID factory (defaults to a deterministic counter so output is
    reproducible; the tool passes uuid4).
    """
    if uid is None:
        _n = [0]

        def uid() -> str:  # deterministic, reproducible ids
            _n[0] += 1
            return f"00000000-0000-0000-0000-{_n[0]:012d}"

    # Pin count per part, from the nets.
    pin_count: dict[str, int] = {}
    for net in nets:
        for node in net.get("nodes", []):
            ref = node.get("refdes") or node.get("reference", "")
            pin_count[ref] = max(pin_count.get(ref, 0), 0)
    for net in nets:
        seen: dict[str, set] = {}
        for node in net.get("nodes", []):
            ref = node.get("refdes") or node.get("reference", "")
            seen.setdefault(ref, set()).add(node.get("pin", ""))
        for ref, pins in seen.items():
            pin_count[ref] = pin_count.get(ref, 0) + len(pins)

    symbols = symbols or {}
    part_symbol = part_symbol or {}

    # One embedded box symbol per distinct pin-count (only for parts that did
    # not resolve to a real library symbol), plus each resolved real symbol.
    def _symbol_name(n: int) -> str:
        return f"eda:BOX{n}"

    def _lib_id_for(ref: str) -> str:
        lid = part_symbol.get(ref)
        if lid and lid in symbols:
            return lid
        return _symbol_name(max(2, pin_count.get(ref, 2)))

    counts_used = sorted({max(2, pin_count.get(p["refdes"], 2)) for p in parts
                          if part_symbol.get(p["refdes"]) not in symbols})
    lib_syms = [_embedded_symbol(_symbol_name(n), n) for n in counts_used]
    lib_syms += ["\t\t" + symbols[lid].strip() for lid in sorted(symbols)]

    lines = ["(kicad_sch", "\t(version 20240101)",
             '\t(generator "eda-agent")', f'\t(uuid "{uid()}")',
             '\t(paper "A3")']
    if title:
        lines.append(f'\t(title_block\n\t\t(title "{_esc(title)}")\n\t)')
    lines.append("\t(lib_symbols")
    lines.extend(lib_syms)
    lines.append("\t)")

    # Placed symbol instances on a grid, connected parts clustered.
    for i, p in enumerate(order_by_connectivity(parts, nets)):
        ref = p.get("refdes", "")
        x, y = _grid(i, 8, 25.4, 30.48, 25.4, 25.4)
        value = _esc(p.get("value", "") or p.get("lib_ref", "") or "")
        lines.append(
            "\t(symbol\n"
            f'\t\t(lib_id "{_lib_id_for(ref)}")\n'
            f'\t\t(at {_num(x)} {_num(y)} 0)\n\t\t(unit 1)\n'
            "\t\t(in_bom yes)\n\t\t(on_board yes)\n\t\t(dnp no)\n"
            f'\t\t(uuid "{uid()}")\n'
            f'\t\t(property "Reference" "{_esc(ref)}"\n'
            f'\t\t\t(at {_num(x)} {_num(y - 12.7)} 0)\n'
            "\t\t\t(effects (font (size 1.27 1.27))))\n"
            f'\t\t(property "Value" "{value}"\n'
            f'\t\t\t(at {_num(x)} {_num(y + 12.7)} 0)\n'
            "\t\t\t(effects (font (size 1.27 1.27))))\n"
            "\t)")

    # Net connectivity carried by global labels on a grid below the parts.
    for j, net in enumerate(nets):
        name = net.get("name", "")
        if not name:
            continue
        x, y = _grid(j, 8, 25.4, 7.62, 25.4, 200.0)
        lines.append(
            f'\t(global_label "{_esc(name)}"\n\t\t(shape bidirectional)\n'
            f'\t\t(at {_num(x)} {_num(y)} 0)\n'
            f'\t\t(effects (font (size 1.27 1.27)) (justify left))\n'
            f'\t\t(uuid "{uid()}")\n\t)')

    lines.append('\t(sheet_instances\n\t\t(path "/"\n\t\t\t(page "1")\n\t\t)\n\t)')
    lines.append("\t(embedded_fonts no)")
    lines.append(")")
    return "\n".join(lines) + "\n"
