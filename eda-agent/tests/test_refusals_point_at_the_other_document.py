# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A wrong-document refusal must say where the operation DOES live.

"No PCB library is active" is true and unhelpful. It reports what is
missing and not that the same operation exists for the other document
kind, so the reasonable conclusion from it is that the capability is
absent. That conclusion was drawn and reported more than once, and each
time the tool existed under the other namespace.

Measured before the hint was added: 277 document-resolution refusals in
the Pascal, of which 5 named a tool.

The hint is attached in ``BuildErrorResponseDetailed`` rather than at
those 277 sites, so what this guards is the table and the call, not the
wording of each message. A new document-kind code added without a hint
fails here.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "altium"
_MAIN = (_SCRIPTS / "Main.pas").read_text(encoding="latin-1")

#: Codes that must carry a cross-document hint, because the same
#: operation genuinely exists for another document kind.
_MUST_HINT = ("NO_PCBLIB", "NO_SCHLIB", "NO_PCB", "NO_BOARD",
              "NO_SCHDOC", "NO_SCHEMATIC")

#: Codes deliberately left without one, with the reason. Listing them
#: is what makes the set above a decision rather than an oversight.
_NO_HINT_NEEDED = {
    "NO_DOCUMENT": "nothing is open at all, so there is no sibling",
    "NO_LIBRARY": "the message already says to supply library_path",
    "NO_SCH_SERVER": "an infrastructure failure, not a document mix-up",
    "USE_DEDICATED_TOOL": "the message already names the tool to call",
    # The board is the right document and the LAYER is the wrong kind, so
    # there is no sibling document to point at. The refusal already names
    # pcb_place_polygon_rect for the signal-layer case, which is the
    # redirect a hint would have added.
    "NOT_A_PLANE": "a wrong layer kind, not a wrong document, and the "
                   "message already names the tool for the other case",
    "WRONG_DOCUMENT_FOCUSED": "already names asked-for and actual",
    "WRONG_LIBRARY": "already names asked-for and actual",
    "WRONG_DOC_KIND": "already says to pass sheet_path",
    "WRONG_FOCUS": "already names the document to focus and the tool "
                   "that focuses it",
    "NOT_A_MEMBER": "the project resolved fine and the document simply is "
                    "not in it, so there is no other document to point at. "
                    "The message names the path that was asked for.",
}


def _hint_body() -> str:
    match = re.search(
        r"^Function CrossDocumentHint\b.*?(?=^(?:Function|Procedure)\s)",
        _MAIN, re.MULTILINE | re.DOTALL)
    assert match, ("CrossDocumentHint is gone; refusals no longer say where "
                   "the operation lives")
    return match.group(0)


def _codes_used_in_the_pascal() -> set[str]:
    """Every document-resolution error code the handlers actually raise."""
    codes: set[str] = set()
    for f in sorted(_SCRIPTS.glob("*.pas")):
        if f.name == "Altium_MCP.pas":
            continue  # build output, not a source
        text = f.read_text(encoding="latin-1")
        for code in re.findall(
                r"BuildErrorResponse(?:Detailed)?\(RequestId,\s*'([A-Z_]+)'",
                text):
            if re.search(r"NO_(PCB|SCH|LIB|BOARD|DOC)|WRONG_|NOT_A_|"
                         r"USE_DEDICATED|NO_FOCUS", code):
                codes.add(code)
    return codes


def test_the_scan_actually_found_the_refusals():
    """A regex that stopped matching would make this file vacuous."""
    codes = _codes_used_in_the_pascal()
    assert len(codes) >= 10, (
        f"only {len(codes)} document-resolution codes found; the scan broke "
        f"and this guard is checking nothing")
    assert "NO_PCBLIB" in codes


def test_the_hint_is_actually_applied_to_the_message():
    """A table nothing reads would pass every check below."""
    builder = re.search(
        r"^Function BuildErrorResponseDetailed\b.*?(?=^Function BuildErrorResponse\b)",
        _MAIN, re.MULTILINE | re.DOTALL)
    assert builder, "BuildErrorResponseDetailed not found"
    body = builder.group(0)
    assert "CrossDocumentHint(ErrorCode)" in body, (
        "the hint table is never consulted, so no refusal carries one")
    assert "ErrorMsg + ' ' + Hint" in body, (
        "the hint is computed and never appended to the message")


def test_a_handler_that_already_points_is_not_double_hinted():
    """A specific pointer beats the generic one and must win."""
    builder = _MAIN[_MAIN.index("Function BuildErrorResponseDetailed"):]
    assert "Not MessageNamesATool(ErrorMsg)" in builder, (
        "without this, a message that already names a tool gets the "
        "generic hint bolted on after it")


@pytest.mark.parametrize("code", _MUST_HINT)
def test_every_document_kind_code_carries_a_hint(code):
    assert f"'{code}'" in _hint_body(), (
        f"{code} refuses without saying where the operation does live, "
        f"which reads as the capability being absent")


@pytest.mark.parametrize("code", sorted(_codes_used_in_the_pascal()))
def test_every_code_is_either_hinted_or_listed_as_not_needing_one(code):
    """The point of the guard: a NEW code cannot slip in unconsidered."""
    hinted = f"'{code}'" in _hint_body()
    assert hinted or code in _NO_HINT_NEEDED, (
        f"{code} is a document-resolution refusal with no cross-document "
        f"hint and no entry in _NO_HINT_NEEDED. Either add it to "
        f"CrossDocumentHint, or record here why it needs none")


@pytest.mark.parametrize("code,why", sorted(_NO_HINT_NEEDED.items()))
def test_the_exemptions_are_still_real_codes(code, why):
    """An exemption for a code nobody raises is dead weight that hides
    the next one."""
    assert code in _codes_used_in_the_pascal(), (
        f"{code} is exempted but no handler raises it any more")
    assert why.strip()


def test_the_hint_names_a_namespace_that_exists():
    body = _hint_body()
    for namespace in ("pcb_", "lib_", "sch_", "obj_"):
        assert namespace in body, (
            f"the hint never mentions the {namespace} namespace")
