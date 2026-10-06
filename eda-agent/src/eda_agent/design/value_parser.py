# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Parse and format engineering component-value strings.

Plans store component values as human strings (``"4.7k"``, ``"100nF"``, the
RKM code ``"4k7"``), but every calculator and check in the design engine works
in base SI floats. This module bridges the two: a robust parser that accepts
the forms an engineer actually writes, and a formatter that renders a float
back to the conventional notation.

Supported input forms:

* SI-prefixed: ``4.7k``, ``100n``, ``10uF``, ``0.1uF``, ``1M``, ``2.2``.
* RKM / IEC 60062 (the prefix letter stands in for the decimal point):
  ``4k7`` = 4.7 k, ``2R2`` = 2.2, ``R47`` = 0.47, ``4n7`` = 4.7 n, ``1M5``.
* Optional unit suffix ``F`` / ``H`` / ``ohm`` / ``Ω`` is stripped.

Prefix case matters: ``M`` is mega (1e6), ``m`` is milli (1e-3). ``R`` is the
ohm marker (multiplier 1). Pure Python.
"""

from __future__ import annotations

import re

# Case-sensitive: M (mega) != m (milli). K accepted as a kilo alias for k.
_PREFIX: dict[str, float] = {
    "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "R": 1.0,
    "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9,
}
_PFX_CHARS = "pnumRkKMG"

# RKM: digits, a prefix letter as the decimal point, more digits (4k7, R47).
_RKM = re.compile(rf"^(\d*)([{_PFX_CHARS}])(\d+)$")
# Trailing prefix: a number then an optional prefix letter (4.7k, 100, 10u).
_TRAILING = re.compile(rf"^(\d*\.?\d+)\s*([{_PFX_CHARS}]?)$")


def parse_value(text: str) -> float:
    """Parse an engineering value string to a base SI float (ohms / farads /
    henries). Raises ``ValueError`` on anything it cannot read."""
    if not isinstance(text, str):
        raise ValueError(f"value must be a string, got {type(text).__name__}")
    s = text.strip().replace("µ", "u").replace("Ω", "")
    low = s.lower()
    for unit in ("ohms", "ohm"):
        if low.endswith(unit):
            s = s[: -len(unit)]
            break
    s = s.strip()
    # Strip a trailing farad/henry unit letter, but only when what precedes it
    # is a digit or a prefix (so we never eat a bare prefix like the 'm' of mF).
    if len(s) >= 2 and s[-1] in "FHfh" and (s[-2].isdigit() or s[-2] in _PREFIX):
        s = s[:-1].strip()
    if not s:
        raise ValueError(f"empty value {text!r}")

    m = _RKM.match(s)
    if m:
        whole, prefix, frac = m.groups()
        return float(f"{whole or '0'}.{frac}") * _PREFIX[prefix]

    m = _TRAILING.match(s)
    if m:
        num, prefix = m.groups()
        return float(num) * (_PREFIX[prefix] if prefix else 1.0)

    raise ValueError(f"cannot parse value {text!r}")


def try_parse_value(text: str):
    """Like :func:`parse_value` but returns ``None`` instead of raising."""
    try:
        return parse_value(text)
    except (ValueError, TypeError):
        return None


_FORMAT_TABLE = [
    (1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""),
    (1e-3, "m"), (1e-6, "u"), (1e-9, "n"), (1e-12, "p"),
]


def format_value(value: float, unit: str = "") -> str:
    """Render a base SI float as a conventional value string.

    Picks the prefix that puts the mantissa in ``[1, 1000)`` (``4700`` ->
    ``"4.7k"``, ``100e-9`` -> ``"100n"``). ``unit`` is appended verbatim.
    """
    if value == 0:
        return f"0{unit}"
    av = abs(value)
    for mult, prefix in _FORMAT_TABLE:
        if av >= mult:
            return f"{value / mult:g}{prefix}{unit}"
    return f"{value / 1e-12:g}p{unit}"


__all__ = [
    "parse_value",
    "try_parse_value",
    "format_value",
]
