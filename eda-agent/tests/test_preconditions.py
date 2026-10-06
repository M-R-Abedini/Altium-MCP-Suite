# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Precondition gating: the flags that decide whether a tool may run.

_fetch_state derives every flag from one call to
``application.get_open_documents``, and it had no test at all. That is
the same setup that let _list_sheets silently return a single-sheet
sentinel for as long as it existed: a helper that parses a bridge
response, degrades quietly when the shape is not what it expects, and is
never exercised.

The shape here is easy to get wrong in two ways, so both are pinned
against what the Pascal actually sends:

* App_GetOpenDocuments answers with a BARE JSON ARRAY, and send_command
  returns response.data unwrapped, so the value is a list, not a dict.
* It fills document_kind from DM_DocumentKind, whose values are the
  cDocKind_* constants: 'SCH', 'PCB', 'PCBLIB', 'SCHLIB'. A parser that
  expected 'SchDoc' or '.SchDoc' would match nothing and report every
  precondition as unmet, refusing valid work.
"""

from __future__ import annotations

import pytest

from eda_agent.preconditions import (
    Precondition,
    PreconditionError,
    _fetch_state,
    _state_cache,
    check_preconditions,
)


class _Bridge:
    def __init__(self, docs):
        self.docs = docs
        self.calls = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append(command)
        return self.docs


def _doc(kind, name):
    """One entry in the shape App_GetOpenDocuments really emits."""
    return {"document_kind": kind, "file_name": name,
            "file_path": rf"C:\p\{name}", "loaded": True}


@pytest.fixture(autouse=True)
def _clear_cache():
    """State is cached across calls; a stale snapshot would make these
    pass or fail on ordering rather than on behaviour."""
    _state_cache.invalidate()
    yield
    _state_cache.invalidate()


@pytest.mark.asyncio
async def test_the_kind_strings_altium_actually_sends_are_recognised():
    """cDocKind_Sch='SCH', cDocKind_Pcb='PCB', cDocKind_PcbLib='PCBLIB',
    cDocKind_Schlib='SCHLIB' (workspace-manager API reference)."""
    bridge = _Bridge([
        _doc("SCH", "main.SchDoc"),
        _doc("PCB", "board.PcbDoc"),
        _doc("PCBLIB", "parts.PcbLib"),
        _doc("SCHLIB", "parts.SchLib"),
    ])
    state = await _fetch_state(bridge)
    assert state["has_schematic"] is True
    assert state["has_pcb"] is True
    assert state["has_pcb_lib"] is True
    assert state["has_sch_lib"] is True
    assert state["has_project"] is True


@pytest.mark.asyncio
async def test_a_bare_array_is_what_gets_parsed():
    """The response is a LIST. Requiring a dict here would zero every
    flag and refuse work that is perfectly valid."""
    state = await _fetch_state(_Bridge([_doc("PCB", "board.PcbDoc")]))
    assert state["has_pcb"] is True
    assert state["documents"], "the document list was dropped"


@pytest.mark.asyncio
async def test_nothing_open_means_every_flag_is_false():
    state = await _fetch_state(_Bridge([]))
    assert not any(state[k] for k in
                   ("has_project", "has_pcb", "has_schematic",
                    "has_pcb_lib", "has_sch_lib"))


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, {}, "unexpected", 42])
async def test_an_unusable_answer_does_not_raise(payload):
    """A bad answer must degrade to "nothing open", not explode: the
    caller is asking whether it may proceed, not doing the work yet."""
    state = await _fetch_state(_Bridge(payload))
    assert state["has_pcb"] is False


@pytest.mark.asyncio
async def test_a_missing_precondition_names_itself_and_how_to_fix_it():
    bridge = _Bridge([_doc("SCH", "main.SchDoc")])
    with pytest.raises(PreconditionError) as excinfo:
        await check_preconditions(bridge, Precondition.HAS_PCB)
    details = excinfo.value.details
    failed = {m["precondition"] for m in details["missing"]}
    assert "HAS_PCB" in failed
    hint = next(m["hint"] for m in details["missing"]
                if m["precondition"] == "HAS_PCB")
    assert hint, "a precondition failure with no hint costs a round trip"


@pytest.mark.asyncio
async def test_a_satisfied_precondition_lets_the_call_through():
    bridge = _Bridge([_doc("PCB", "board.PcbDoc")])
    await check_preconditions(bridge, Precondition.HAS_PCB)


@pytest.mark.asyncio
async def test_a_library_does_not_satisfy_the_document_preconditions():
    """An open PcbLib is not an open PcbDoc. Conflating them would let a
    board tool run against a library and fail downstream instead of
    being refused with a reason."""
    bridge = _Bridge([_doc("PCBLIB", "parts.PcbLib")])
    state = await _fetch_state(bridge)
    assert state["has_pcb_lib"] is True
    assert state["has_pcb"] is False
    with pytest.raises(PreconditionError):
        await check_preconditions(bridge, Precondition.HAS_PCB)
