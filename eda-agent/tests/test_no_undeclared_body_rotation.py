# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""``IPCB_ComponentBody`` has no ``Rotation``, and assigning it is fatal.

MEASURED on Altium 26.9.1.9: ``lib_link_3d_model`` with ``rotation_z``
set raised the modal "Undeclared identifier: Rotation", never returned,
and left the polling loop dead. The whole bridge had to be restarted by
hand.

What makes this worth a guard rather than a comment is that the code
LOOKED defended. The assignment sat inside a ``Try ... Except`` with a
``DidRotation`` flag, so it read as an adjustment that would simply
report false if the API refused. An undeclared identifier is not an
exception in DelphiScript, it is a compile fault raised when the
function is first CALLED, and no ``Except`` can catch it. The guard
could never have fired, and because DelphiScript compiles a function
lazily the fault stayed invisible until someone passed a rotation.

Same family as the ``ObjectIDToObjectName`` fault: an identifier nobody
verified, dormant for as long as its branch went unexercised.

The rotation is reachable, but on the MODEL and before it is attached
(``Model.SetState`` in AutoSTEPplacer.pas). Its four arguments are
undocumented, so implementing it means measuring them first, not
guessing.
"""

from __future__ import annotations

import pathlib
import re

PASCAL = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "altium"
LIBRARY_PY = (pathlib.Path(__file__).resolve().parents[1] / "src"
              / "eda_agent" / "tools" / "library.py")


def _sources():
    return sorted(PASCAL.glob("*.pas"))


def test_no_pascal_assigns_rotation_on_a_component_body():
    """The exact statement that took the bridge down."""
    offenders = []
    for path in _sources():
        for n, line in enumerate(path.read_text(
                encoding="utf-8", errors="replace").splitlines(), 1):
            code = line.split("{")[0]
            if re.search(r"\bBody\s*\.\s*Rotation\s*:=", code):
                offenders.append(f"{path.name}:{n}: {line.strip()}")

    assert not offenders, (
        "assigning Body.Rotation raises an uncatchable Undeclared "
        "identifier fault that kills the polling loop:\n"
        + "\n".join(offenders))


def test_the_tool_does_not_promise_a_rotation_it_cannot_apply():
    """The docstring told callers rotation_z 'sets the body's Rotation'.

    A caller who believed it paid with the bridge. Tested in the
    opposite direction from the Pascal guard: the code being gone is
    not the same as the contract saying so.
    """
    text = LIBRARY_PY.read_text(encoding="utf-8")
    start = text.index("def lib_link_3d_model")
    doc = text[start:start + 4000]

    assert "rotation_z: NOT APPLIED" in doc, (
        "lib_link_3d_model must state that rotation_z is not applied")
    assert "sets the body's Rotation" not in doc, (
        "the docstring still promises the assignment that was fatal")
