# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Emit a KiCad board (.kicad_pcb) from a normalized parts+nets model.

The PCB side of design-execution (the counterpart of :mod:`kicad_schematic`):
declare the nets, then place a footprint per part -- a generated box footprint
whose pads are assigned to the right nets. The result is a valid .kicad_pcb that
opens in pcbnew with the ratsnest ready to route. Components sit on a grid;
auto-placement/routing is a later refinement.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .kicad_symbol import _esc, _num
from .kicad_footprint import embed_footprint
from .placement_order import order_by_connectivity

_LAYERS = """\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(9 "F.Adhes" user "F.Adhesive")
\t\t(11 "B.Adhes" user "B.Adhesive")
\t\t(13 "F.Paste" user)
\t\t(15 "B.Paste" user)
\t\t(5 "F.SilkS" user "F.Silkscreen")
\t\t(7 "B.SilkS" user "B.Silkscreen")
\t\t(1 "F.Mask" user)
\t\t(3 "B.Mask" user)
\t\t(17 "Dwgs.User" user "User.Drawings")
\t\t(19 "Cmts.User" user "User.Comments")
\t\t(21 "Eco1.User" user "User.Eco1")
\t\t(23 "Eco2.User" user "User.Eco2")
\t\t(25 "Edge.Cuts" user)
\t\t(27 "Margin" user)
\t\t(31 "F.CrtYd" user "F.Courtyard")
\t\t(29 "B.CrtYd" user "B.Courtyard")
\t\t(35 "F.Fab" user)
\t\t(33 "B.Fab" user)
\t)"""


def _pad(number: str, dx: float, dy: float, net_num: int, net_name: str,
         uid: Callable[[], str]) -> str:
    net = (f'\n\t\t\t(net {net_num} "{_esc(net_name)}")' if net_num else "")
    return (
        f'\t\t(pad "{_esc(number)}" smd rect\n'
        f'\t\t\t(at {_num(dx)} {_num(dy)})\n\t\t\t(size 1 1)\n'
        f'\t\t\t(layers "F.Cu" "F.Paste" "F.Mask"){net}\n'
        f'\t\t\t(uuid "{uid()}")\n\t\t)')


def build_pcb(parts: list[dict[str, Any]], nets: list[dict[str, Any]],
              uid: Optional[Callable[[], str]] = None,
              title: str = "",
              mod_texts: Optional[dict[str, str]] = None) -> str:
    """Build a valid .kicad_pcb string with declared nets and placed footprints.

    ``parts``: ``[{refdes, value?}]``. ``nets``: ``[{name, nodes:[{refdes,pin}]}]``.
    """
    if uid is None:
        _n = [0]

        def uid() -> str:
            _n[0] += 1
            return f"00000000-0000-0000-0000-{_n[0]:012d}"

    # Number the nets (0 is the unconnected net).
    net_num: dict[str, int] = {}
    for net in nets:
        name = net.get("name", "")
        if name and name not in net_num:
            net_num[name] = len(net_num) + 1

    # Per-part ordered pins and each pin's net.
    part_pins: dict[str, list[str]] = {}
    pin_net: dict[tuple[str, str], str] = {}
    for net in nets:
        for node in net.get("nodes", []):
            ref = node.get("refdes") or node.get("reference", "")
            pin = str(node.get("pin", ""))
            pin_net[(ref, pin)] = net.get("name", "")
            part_pins.setdefault(ref, [])
            if pin not in part_pins[ref]:
                part_pins[ref].append(pin)

    lines = ["(kicad_pcb", "\t(version 20240101)",
             '\t(generator "eda-agent")', "\t(general (thickness 1.6))",
             '\t(paper "A4")']
    if title:
        lines.append(f'\t(title_block\n\t\t(title "{_esc(title)}")\n\t)')
    lines += [_LAYERS, "\t(setup)", '\t(net 0 "")']
    for name, num in net_num.items():
        lines.append(f'\t(net {num} "{_esc(name)}")')

    # Cluster connected parts so the ratsnest is shorter than a raw grid.
    ordered = order_by_connectivity(parts, nets)
    xs: list[float] = []
    ys: list[float] = []
    for i, p in enumerate(ordered):
        ref = p.get("refdes", "")
        pins = part_pins.get(ref, ["1", "2"])
        x = 25.4 + (i % 8) * 20.32
        y = 25.4 + (i // 8) * 20.32
        xs.append(x)
        ys.append(y)
        value = p.get("value", "") or ""

        # Use the real footprint when one was resolved; else a generated box.
        mod = (mod_texts or {}).get(ref)
        if mod:
            pad_nets = {pin: pin_net.get((ref, pin), "") for pin in pins}
            embedded = embed_footprint(mod, ref, value, x, y, pad_nets,
                                       net_num, uid)
            if embedded:
                lines.append("\t" + embedded.strip())
                continue

        value = _esc(value)
        pad_lines = []
        for k, pin in enumerate(pins):
            dx = (k - (len(pins) - 1) / 2) * 2.54
            name = pin_net.get((ref, pin), "")
            pad_lines.append(_pad(pin, dx, 0, net_num.get(name, 0), name, uid))
        lines.append(
            f'\t(footprint "eda:BOX{len(pins)}"\n'
            f'\t\t(layer "F.Cu")\n\t\t(uuid "{uid()}")\n'
            f'\t\t(at {_num(x)} {_num(y)})\n'
            f'\t\t(property "Reference" "{_esc(ref)}"\n'
            f'\t\t\t(at 0 -2.5 0)\n\t\t\t(layer "F.SilkS")\n'
            f'\t\t\t(uuid "{uid()}")\n'
            '\t\t\t(effects (font (size 1 1) (thickness 0.15)))\n\t\t)\n'
            f'\t\t(property "Value" "{value}"\n'
            f'\t\t\t(at 0 2.5 0)\n\t\t\t(layer "F.Fab")\n'
            f'\t\t\t(uuid "{uid()}")\n'
            '\t\t\t(effects (font (size 1 1) (thickness 0.15)))\n\t\t)\n'
            + "\n".join(pad_lines) + "\n\t)")

    # A board outline on Edge.Cuts, enclosing the placement with a margin, so
    # the generated board is fab-ready (a PCB needs an outline).
    if xs and ys:
        m = 10.0
        x1, y1 = _num(min(xs) - m), _num(min(ys) - m)
        x2, y2 = _num(max(xs) + m), _num(max(ys) + m)
        lines.append(
            f'\t(gr_rect\n\t\t(start {x1} {y1})\n\t\t(end {x2} {y2})\n'
            '\t\t(stroke (width 0.1) (type default))\n\t\t(fill no)\n'
            f'\t\t(layer "Edge.Cuts")\n\t\t(uuid "{uid()}")\n\t)')

    lines.append(")")
    return "\n".join(lines) + "\n"
