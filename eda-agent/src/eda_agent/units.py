# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The unit conversion factors, defined once.

Before this module, ``0.0254`` was defined independently in six places
across two backends, under four different names, plus one inline
division. All six agreed, by luck. A physical constant stated in many
places is the same defect as any fact stated twice: nothing enforces
agreement, and the divergence, when it comes, lands as geometry that is
25.4x off in exactly one code path.

Positional literals (a part placed AT 25.4 mm) are not conversions and
do not belong here.
"""

from __future__ import annotations

#: Millimetres per mil. The definition of the mil: one thousandth of an
#: inch, and the inch is defined as exactly 25.4 mm.
MM_PER_MIL: float = 0.0254

#: Millimetres per inch, exact by definition.
MM_PER_INCH: float = 25.4

#: Mils per millimetre, the inverse spelled once.
MILS_PER_MM: float = 1.0 / MM_PER_MIL

#: Finished copper thickness in mils for one ounce per square foot.
#:
#: A CONVENTION, not a derivation: the industry treats 1 oz/ft^2 as 35
#: micrometres, and 35 / 25.4 is 1.37795..., which everyone writes as
#: 1.378. Deriving it here instead would silently change every trace
#: width and impedance this server has ever quoted, so the conventional
#: rounded value is what is stated, once.
#:
#: It was previously written three times, in impedance_sizing, in
#: trace_sizing, and inline in the current-capacity tool. All three
#: agreed, by luck, which is the same position the mils-to-mm factor
#: was in before task #43.
OZ_TO_MILS: float = 1.378
