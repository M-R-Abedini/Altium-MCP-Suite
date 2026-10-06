# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Sheet enumeration for the schematic audit.

_list_sheets decides WHICH sheets the audit looks at, and it degrades
silently: when enumeration yields nothing it returns a single-sheet
sentinel and the audit runs on the active document alone. That is a
sensible fallback and a terrible failure mode, because a multi-sheet
project then reports clean on the strength of one sheet.

It was reached every time. Proj_GetDocuments answers with a BARE JSON
ARRAY as its data and send_command returns response.data unwrapped, so
the value is a list; the parser required a dict and skipped the whole
loop. Found by diffing the response keys Python reads against the keys
the Pascal writes.

These pin the SHAPE THE PASCAL ACTUALLY SENDS, not the shape the parser
happened to expect, which is the distinction that let this live.
"""

from __future__ import annotations

import pytest

from eda_agent.design.audit import _list_sheets


class _Bridge:
    """Returns one canned payload, recording what was asked for."""

    def __init__(self, payload):
        self.payload = payload
        self.sent = []

    def send_command(self, command, params=None, **kwargs):
        self.sent.append((command, params or {}))
        return self.payload


#: What Proj_GetDocuments really returns: a bare array, one object per
#: document, keys file_path / file_name / document_kind.
_REAL_SHAPE = [
    {"file_path": r"C:\p\main.SchDoc", "file_name": "main.SchDoc",
     "document_kind": "SCH"},
    {"file_path": r"C:\p\power.SchDoc", "file_name": "power.SchDoc",
     "document_kind": "SCH"},
    {"file_path": r"C:\p\board.PcbDoc", "file_name": "board.PcbDoc",
     "document_kind": "PCB"},
]


def test_the_bare_array_the_pascal_sends_is_parsed():
    """The regression itself: a list must not fall through to the
    sentinel."""
    bridge = _Bridge(_REAL_SHAPE)
    sheets = _list_sheets(bridge, None)
    assert sheets == [r"C:\p\main.SchDoc", r"C:\p\power.SchDoc"]
    assert sheets != [""], (
        "fell through to the single-sheet sentinel, so the audit would "
        "silently examine only the active document")


def test_non_schematic_documents_are_excluded():
    """A .PcbDoc in the same project must not be audited as a sheet."""
    sheets = _list_sheets(_Bridge(_REAL_SHAPE), None)
    assert not any(s.lower().endswith(".pcbdoc") for s in sheets)


def test_a_wrapped_array_still_works():
    """Other handlers do wrap their arrays; both shapes are accepted."""
    payload = {"documents": _REAL_SHAPE}
    assert _list_sheets(_Bridge(payload), None) == [
        r"C:\p\main.SchDoc", r"C:\p\power.SchDoc"]


@pytest.mark.parametrize("payload", [[], {}, None, "unexpected"])
def test_an_empty_or_odd_answer_falls_back_to_the_sentinel(payload):
    """The fallback is correct behaviour when there is genuinely nothing
    to enumerate; it just must not be the ONLY outcome."""
    assert _list_sheets(_Bridge(payload), None) == [""]


def test_a_bridge_error_falls_back_rather_than_propagating():
    class _Boom:
        def send_command(self, *a, **k):
            raise RuntimeError("bridge down")

    assert _list_sheets(_Boom(), None) == [""]


def test_the_project_path_is_forwarded_when_given():
    bridge = _Bridge(_REAL_SHAPE)
    _list_sheets(bridge, r"C:\p\proj.PrjPcb")
    command, params = bridge.sent[0]
    assert command == "project.get_documents"
    assert params["project_path"] == r"C:\p\proj.PrjPcb"
