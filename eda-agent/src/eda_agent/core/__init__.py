# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""EDA-agnostic core.

The main flows (review, board info, component and net listing) are expressed
here against a single normalized :class:`DesignSnapshot`. Each EDA backend
(Altium, KiCad) provides an adapter that fills that snapshot from its own live
API; the review engine and the neutral tools then run identically regardless of
which tool is underneath. Adding a new EDA means writing one adapter, not a new
tool surface.
"""

from .snapshot import DesignSnapshot, SnapNet, SnapPart, SnapPin
from .review_engine import review_snapshot

__all__ = [
    "DesignSnapshot",
    "SnapNet",
    "SnapPart",
    "SnapPin",
    "review_snapshot",
]
