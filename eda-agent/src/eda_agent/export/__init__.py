# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Pure file-format exporters (no Altium / bridge dependency).

Each module here turns already-structured design data into a conventional
on-disk report format (CSV stackup, KiCad footprint, ...). Keeping the
formatting pure makes it unit-testable offline; the MCP tool layer fetches
the data over the bridge and hands it to these functions.
"""
