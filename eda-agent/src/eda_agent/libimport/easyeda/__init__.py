# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""EasyEDA / LCSC component converter for KiCad and Altium.

Independent implementation from EasyEDA's own published format
specification. No third-party converter source was consulted; in
particular the AGPL-licensed easyeda2kicad is not a reference, so this
package stays cleanly Apache-2.0 like the rest of the project.

Layers, each usable on its own:

* :mod:`shapes`   shape-string parsing, pure and offline
* :mod:`document` normalized component model (mils, Y-up, origin relative)
* :mod:`kicad`    ``.kicad_sym`` / ``.kicad_mod`` text emitters
* :mod:`altium`   ordered MCP-tool install plan (no file format needed,
                  because the bridge already exposes library authoring)
* :mod:`fetch`    optional online LCSC/EasyEDA client, stdlib only

The parse and emit path never imports :mod:`fetch`, so a saved JSON
payload converts with no network at all, which is also how the tests
run.

DATASHEET DISCIPLINE: an imported footprint is a vendor's drawing, not
ground truth. Audit it against the manufacturer land pattern with
``lib_audit_footprint_vs_datasheet`` before trusting it in a design.
"""

from eda_agent.libimport.easyeda.altium import build_altium_plan
from eda_agent.libimport.easyeda.document import (
    EasyEdaComponent,
    EasyEdaFootprint,
    EasyEdaSymbol,
    parse_component,
)
from eda_agent.libimport.easyeda.kicad import (
    footprint_to_kicad_mod,
    symbol_to_kicad_sym,
)

__all__ = [
    "EasyEdaComponent",
    "EasyEdaFootprint",
    "EasyEdaSymbol",
    "build_altium_plan",
    "footprint_to_kicad_mod",
    "parse_component",
    "symbol_to_kicad_sym",
]
