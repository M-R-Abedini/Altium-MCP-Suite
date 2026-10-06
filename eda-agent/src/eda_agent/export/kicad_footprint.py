# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""KiCad footprint (.kicad_mod) writer.

Complements the KiCad *symbol* exporter (tools/library.py): given a footprint's
pad geometry read from an Altium PcbLib, emit a KiCad 6+ S-expression
footprint. Pure -- the tool layer fetches the pads over the bridge and calls
``format_kicad_footprint``.

Coordinate convention: Altium pad positions are in mils, relative to the
footprint origin, y-up. KiCad footprints are mm, y-DOWN. So x converts
straight (mil->mm) and y is negated. Pad rotation is negated with the y-flip.
"""

from __future__ import annotations

from typing import Any

from eda_agent.units import MM_PER_MIL

# Altium TopShape -> KiCad pad shape. Octagonal has no KiCad equal; roundrect
# is the conventional substitute.
_SHAPE_MAP = {
    "round": "circle",
    "rounded": "circle",
    "circle": "circle",
    "rectangular": "rect",
    "rectangle": "rect",
    "rect": "rect",
    "roundrectangle": "roundrect",
    "roundedrectangular": "roundrect",
    "roundrect": "roundrect",
    "octagonal": "roundrect",
}


def _esc(s: Any) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _mm(mils: Any) -> float:
    try:
        return float(mils) * MM_PER_MIL
    except (TypeError, ValueError):
        return 0.0


def _pad_shape(raw: Any) -> str:
    return _SHAPE_MAP.get(str(raw or "").strip().lower().replace(" ", ""), "roundrect")


def _pad_layers(pad: dict[str, Any], is_smd: bool) -> list[str]:
    # SMD pads get the paste layer at 1:1 (no solder_paste_margin emitted):
    # Altium pad data carries no per-pad paste expansion, and stencil
    # shrink is a fab decision -- KiCad applies its own board-level margin
    # if the user sets one. Fine-pitch users should review the stencil.
    if not is_smd:
        return ["*.Cu", "*.Mask"]
    side = str(pad.get("layer", "top")).strip().lower()
    if side in ("bottom", "b", "bottomlayer"):
        return ["B.Cu", "B.Paste", "B.Mask"]
    return ["F.Cu", "F.Paste", "F.Mask"]


def _format_pad(pad: dict[str, Any]) -> str:
    name = _esc(pad.get("name", ""))
    hole = _mm(pad.get("hole_mils", 0))
    is_smd = hole <= 0
    ptype = "smd" if is_smd else "thru_hole"
    shape = _pad_shape(pad.get("shape"))

    x = _mm(pad.get("x_mils", 0)) + 0.0
    y = -_mm(pad.get("y_mils", 0)) + 0.0   # KiCad y is downward; +0.0 kills -0.0
    sx = _mm(pad.get("size_x_mils", 0))
    sy = _mm(pad.get("size_y_mils", 0))
    rot = pad.get("rotation", 0)
    try:
        rot = (-float(rot)) % 360
    except (TypeError, ValueError):
        rot = 0.0

    at = f"(at {x:.4f} {y:.4f}{'' if rot == 0 else f' {rot:.0f}'})"
    layers = " ".join(f'"{lyr}"' for lyr in _pad_layers(pad, is_smd))
    parts = [
        f'  (pad "{name}" {ptype} {shape} {at} '
        f'(size {sx:.4f} {sy:.4f})'
    ]
    if not is_smd:
        parts.append(f" (drill {hole:.4f})")
    parts.append(f" (layers {layers})")
    if shape == "roundrect":
        parts.append(" (roundrect_rratio 0.25)")
    parts.append(")")
    return "".join(parts)


def format_kicad_footprint(
    name: str,
    pads: list[dict[str, Any]],
    *,
    description: str = "",
    generator: str = "eda_agent",
) -> str:
    """Build a ``.kicad_mod`` S-expression for a footprint.

    ``pads`` is a list of dicts with: ``name``, ``x_mils``, ``y_mils``,
    ``size_x_mils``, ``size_y_mils``, ``shape`` (Altium TopShape),
    ``layer`` (``top``/``bottom``/``multi``), ``hole_mils`` (0 = SMD),
    ``rotation`` (degrees). Through-hole pads (hole > 0) get a drill and the
    all-copper layer set; SMD pads get the side's Cu/Paste/Mask set. Returns
    the file text with a trailing newline.
    """
    nm = _esc(name)
    # A footprint is SMD-attr unless it has any through-hole pad.
    any_tht = any(_mm(p.get("hole_mils", 0)) > 0 for p in pads)
    attr = "through_hole" if any_tht else "smd"

    lines = [
        f'(footprint "{nm}" (version 20211014) (generator {generator})',
        '  (layer "F.Cu")',
        f'  (attr {attr})',
    ]
    if description:
        lines.append(f'  (descr "{_esc(description)}")')
    lines += [
        f'  (fp_text reference "REF**" (at 0 -1) (layer "F.SilkS")',
        '    (effects (font (size 1 1) (thickness 0.15))))',
        f'  (fp_text value "{nm}" (at 0 1) (layer "F.Fab")',
        '    (effects (font (size 1 1) (thickness 0.15))))',
    ]
    for pad in pads:
        lines.append(_format_pad(pad))
    lines.append(")")
    return "\n".join(lines) + "\n"
