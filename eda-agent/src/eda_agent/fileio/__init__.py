# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Headless readers for Altium binary design files (roadmap V1).

Parse ``.SchDoc`` / ``.PcbDoc`` directly, no running Altium, no license,
so a design review can run in CI on every commit. The readers emit the same
snapshot shapes the live DelphiScript bridge returns, so the offline review
engines consume either source unchanged.
"""

from .altium_sch import (
    read_schematic_components,
    read_schematic_document_info,
    read_schematic_nets,
    read_schematic_pins,
    read_schematic_wires,
    read_schdoc_records,
)
from .altium_project import read_project_sheets
from .review import (
    review_components,
    review_project_file,
    review_schematic_file,
    to_sarif,
)

__all__ = [
    "read_schematic_components",
    "read_schematic_document_info",
    "read_schematic_nets",
    "read_schematic_pins",
    "read_schematic_wires",
    "read_schdoc_records",
    "read_project_sheets",
    "review_components",
    "review_project_file",
    "review_schematic_file",
    "to_sarif",
]
