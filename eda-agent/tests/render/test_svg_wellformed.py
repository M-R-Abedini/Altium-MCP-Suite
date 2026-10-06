# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""SVG well-formedness regression tests.

A live visual-review run caught the schematic renderer leaving its
``flip_y`` ``<g>`` wrapper unclosed (245 opens / 244 closes), which makes
the SVG invalid XML -- browsers render it only via error recovery. These
tests parse the output as XML and check group balance for both renderers
and both flip states so it can't regress.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from eda_agent.render import (
    PcbRenderOptions,
    SchRenderOptions,
    render_pcb_svg,
    render_sch_svg,
)


def _g_balanced(svg: str) -> bool:
    return svg.count("<g ") + svg.count("<g>") == svg.count("</g>")


_SCH_GEOMETRY = {
    "doc": "t.SchDoc",
    "components": [
        {"des": "R1", "x": 1000, "y": 1000,
         "x1": 900, "y1": 850, "x2": 1100, "y2": 1150},
    ],
    "pins": [{"des": "R1", "x": 900, "y": 1000, "ex": 800, "ey": 1000}],
    "wires": [{"x1": 800, "y1": 1000, "x2": 500, "y2": 1000}],
    "net_labels": [{"text": "VOUT", "x": 600, "y": 1010}],
    "power_ports": [{"text": "GND", "x": 1000, "y": 600, "style": 2}],
}

_PCB_GEOMETRY = {
    "doc": "t.PcbDoc",
    "outline": [{"x": 0, "y": 0}, {"x": 2000, "y": 0},
                {"x": 2000, "y": 1000}, {"x": 0, "y": 1000}],
    "bbox": {"x1": 0, "y1": 0, "x2": 2000, "y2": 1000,
             "width": 2000, "height": 1000},
    "tracks": [{"layer": "TopLayer", "x1": 100, "y1": 100,
                "x2": 900, "y2": 100, "width": 8, "net": "N"}],
    "pads": [{"layer": "TopLayer", "x": 100, "y": 100, "width": 60,
              "height": 60, "shape": "Round", "net": "N", "name": "1"}],
}


@pytest.mark.parametrize("flip_y", [True, False])
def test_schematic_svg_is_well_formed(flip_y):
    svg = render_sch_svg(_SCH_GEOMETRY, SchRenderOptions(flip_y=flip_y))
    assert _g_balanced(svg), "unbalanced <g>/</g> in schematic SVG"
    ET.fromstring(svg)  # raises ParseError if malformed
    assert svg.rstrip().endswith("</svg>")


@pytest.mark.parametrize("flip_y", [True, False])
def test_pcb_svg_is_well_formed(flip_y):
    # design_visual_review renders the PCB without the interactive legend
    # (its foreignObject HTML is not well-formed XML and breaks
    # standalone-SVG rasterization). That is the path under test here.
    svg = render_pcb_svg(
        _PCB_GEOMETRY,
        PcbRenderOptions(flip_y=flip_y, interactive_legend=False),
    )
    assert _g_balanced(svg), "unbalanced <g>/</g> in PCB SVG"
    ET.fromstring(svg)
    assert svg.rstrip().endswith("</svg>")
