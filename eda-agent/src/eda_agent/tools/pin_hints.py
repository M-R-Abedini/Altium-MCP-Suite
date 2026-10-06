# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Say it on the reply, not only in the documentation.

ISch_Pin.Location is the pin's BODY-SIDE ROOT. The point a wire has to
sit on is PinLength away along Orientation, which obj_query exposes as
ConnectionX / ConnectionY.

THIS WAS ALREADY WRITTEN DOWN IN THREE PLACES: the obj_query docstring
says it, obj_explain_pin exists solely to show it, and the Pascal getter
carries the measurement in a comment. It still gets rediscovered, and
the reason is that none of those are in front of a caller at the moment
it matters. Reaching for `Location.X` on a pin is the obvious move, the
reply looks perfectly reasonable, and the geometry that follows is
silently one pin-length short of connecting.

So the reply itself says so. A caller that asks a pin for its location
and not its connection point gets told, in the response it is already
reading, that those are different things and which one it wants.

The check is narrow on purpose. It fires only for pins, only when the
location was asked for, and only when the connection point was NOT, so
a caller that already knows the distinction is never nagged.
"""
from __future__ import annotations

from typing import Optional

#: Only ISch_Pin has this root-versus-end split. PCB pads do not, and
#: matching them would be noise on an unrelated tool.
_PIN_TYPES = ("epin", "pin")

_LOCATION = ("location.x", "location.y")
_CONNECTION = ("connectionx", "connectiony")

HINT = (
    "Location.X and Location.Y on a pin are its BODY-SIDE ROOT, not the "
    "point a wire connects to. The electrical end is PinLength away along "
    "Orientation, and is available directly as ConnectionX and ConnectionY. "
    "Measured on a live sheet: pins at Location.X 3700 with Orientation 2 "
    "and PinLength 300 had every attached wire vertex at x 3400. Comparing "
    "a wire endpoint against Location will report a connected pin as "
    "unconnected. Use obj_explain_pin to see both points and what sits on "
    "each."
)


def _fields(properties: str) -> list:
    return [p.strip().lower() for p in str(properties or "").split(",")]


def pin_location_hint(object_type: str, properties: str) -> Optional[str]:
    """The warning to attach, or None when it does not apply."""
    if str(object_type or "").strip().lower() not in _PIN_TYPES:
        return None
    asked = _fields(properties)
    if not any(f in _LOCATION for f in asked):
        return None
    if any(f in _CONNECTION for f in asked):
        # The caller already knows the difference. Saying it again would
        # train them to ignore the field.
        return None
    return HINT
