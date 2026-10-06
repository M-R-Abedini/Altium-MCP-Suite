# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Windows UI automation, for Altium operations that only exist as dialogs.

Everything else in this project talks to Altium through its scripting
API. This package exists for the operations that have no API at all:
Update From Libraries is a multi-page wizard ending in an Engineering
Change Order, documented by Altium as dialogs rather than processes,
with no command-line, batch or scripting route in either the vendor
documentation or a twelve-repository corpus of community scripts.

WHY THIS CAN WORK AT ALL. The MCP server is a SEPARATE PROCESS from
Altium. Altium's script engine is single threaded, so a modal dialog
blocks it completely and the bridge goes silent until the dialog
closes. This side is unaffected: it is only waiting on a response file
that has not arrived. So the window where Altium can report nothing is
exactly the window where this can act.

WHAT IT CANNOT DO. While the modal is up the bridge is blocked, so
nothing here can ask Altium what is on screen. Every decision is made
from the Win32 view alone: window class, caption, and control text. A
grid of pending changes reads as a control with no accessible text, so
an ECO's contents cannot be fully enumerated before it is executed.
That limit is the reason for the dry-run mode and the step plan: the
click path is declared up front, checked against what is actually on
screen, and abandoned rather than improvised when they disagree.
"""
