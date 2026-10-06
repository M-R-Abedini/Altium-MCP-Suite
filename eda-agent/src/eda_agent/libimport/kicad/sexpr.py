# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Minimal s-expression reader for KiCad library files.

Hand written rather than pulled in as a dependency: the grammar is tiny
(atoms, quoted strings, nested lists) and a parser is far easier to keep
honest than a third-party one whose escaping rules have to be trusted.

The one part that genuinely bites is string escaping. KiCad quotes any
value containing whitespace or parentheses and escapes ``"`` and ``\\``
inside it, so a footprint named ``2.5"`` or a description containing
``(see note)`` will break a naive split-on-whitespace reader. Both are
covered by the tests.
"""

from __future__ import annotations

from typing import Union

__all__ = ["SExpr", "dumps", "loads"]

#: A parsed node: either an atom (str) or a list of nodes.
SExpr = Union[str, list]


def loads(text: str) -> list:
    """Parse one s-expression document into nested lists.

    Returns the top-level list, e.g. ``["kicad_symbol_lib", [...], ...]``.
    Raises ValueError on malformed input rather than returning something
    partially parsed, because a half-read library is worse than none.
    """
    pos = 0
    length = len(text)

    def skip_ws() -> None:
        nonlocal pos
        while pos < length:
            ch = text[pos]
            if ch in " \t\r\n":
                pos += 1
            elif ch == "#":
                # Not part of the KiCad grammar, but harmless to allow.
                while pos < length and text[pos] != "\n":
                    pos += 1
            else:
                return

    def read_string() -> str:
        nonlocal pos
        pos += 1  # opening quote
        out: list[str] = []
        while pos < length:
            ch = text[pos]
            if ch == "\\":
                # KiCad escapes only " and \, but pass anything else
                # through literally rather than guessing at it.
                nxt = text[pos + 1] if pos + 1 < length else ""
                out.append(nxt if nxt in '"\\' else "\\" + nxt)
                pos += 2
                continue
            if ch == '"':
                pos += 1
                return "".join(out)
            out.append(ch)
            pos += 1
        raise ValueError("unterminated string in s-expression")

    def read_atom() -> str:
        nonlocal pos
        start = pos
        while pos < length and text[pos] not in ' \t\r\n()"':
            pos += 1
        return text[start:pos]

    def read_list() -> list:
        nonlocal pos
        pos += 1  # opening paren
        out: list = []
        while True:
            skip_ws()
            if pos >= length:
                raise ValueError("unterminated list in s-expression")
            ch = text[pos]
            if ch == ")":
                pos += 1
                return out
            if ch == "(":
                out.append(read_list())
            elif ch == '"':
                out.append(read_string())
            else:
                atom = read_atom()
                if not atom:
                    raise ValueError(
                        f"unexpected character {text[pos]!r} at {pos}")
                out.append(atom)

    skip_ws()
    if pos >= length or text[pos] != "(":
        raise ValueError("document does not start with '('")
    result = read_list()
    skip_ws()
    return result


def _quote(atom: str) -> str:
    if atom and not any(c in atom for c in ' \t\r\n()"\\'):
        return atom
    escaped = atom.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def dumps(node: SExpr) -> str:
    """Serialise back to text. Used by the round-trip test."""
    if isinstance(node, list):
        return "(" + " ".join(dumps(n) for n in node) + ")"
    return _quote(str(node))


def find_all(node: list, tag: str) -> list:
    """Every direct child list whose head is ``tag``."""
    return [n for n in node
            if isinstance(n, list) and n and n[0] == tag]


def find(node: list, tag: str):
    """First direct child list whose head is ``tag``, else None."""
    for child in find_all(node, tag):
        return child
    return None


def value(node: list, tag: str, index: int = 1, default=None):
    """``index``-th element of the first ``tag`` child, else ``default``."""
    child = find(node, tag)
    if child is None or len(child) <= index:
        return default
    return child[index]


def floats(node) -> list[float]:
    """Every element of ``node`` that parses as a number."""
    out: list[float] = []
    for item in node or []:
        if isinstance(item, str):
            try:
                out.append(float(item))
            except ValueError:
                continue
    return out
