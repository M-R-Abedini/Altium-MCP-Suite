# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Vendor library-package import: inspect and stage downloaded library zips.

Pure offline staging. The Altium-side installation runs through the
existing MCP tools (lib_install_library, lib_link_footprint,
lib_link_3d_model); this package only identifies the members of a
downloaded archive, extracts them safely, and emits the ordered tool
calls needed to install them.
"""

from eda_agent.libimport.cse import extract_cse_zip, inspect_cse_zip

__all__ = [
    "extract_cse_zip",
    "inspect_cse_zip",
]
