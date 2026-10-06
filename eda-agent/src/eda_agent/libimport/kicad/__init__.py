# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""KiCad library reader, feeding the shared neutral model.

``.kicad_sym`` and ``.kicad_mod`` are parsed into the same neutral
document ``libimport.easyeda`` produces, so ``build_altium_plan`` is
shared rather than duplicated and the two importers cannot drift.

Nothing is re-exported here on purpose. ``reader`` holds the parsing and
the three conversions that are each easy to get backwards (millimetres
to mils, the Y axis flip that applies to footprints but not symbols, and
the pin angle convention); importing it explicitly keeps those visible
at the call site rather than behind a package-level alias.
"""
