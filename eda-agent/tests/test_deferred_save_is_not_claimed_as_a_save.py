# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""No tool may promise a save that this bridge defers.

Writes here MARK a document dirty and return. That is deliberate:
marking is cheap and app_save_all flushes at a checkpoint. The hazard was
never the design, it was the NAME. The procedure was called
SaveDocByPath, and at 84 call sites people read the name rather than the
four-line body, so:

  Generic.pas carried a comment asserting it "does SetModified +
  DoFileSave, which writes directly to disk"

  a response field was called sheets_saved and counted documents that had
  only been marked

  lib_delete_footprint and lib_rename_footprint told the user they saved
  the .PcbLib

REPORTED: lib_delete_footprint returned success, the footprint was still
in the file, and the timestamp was unchanged until an explicit proj_save
on the LibPkg. The delete had happened, in memory, and nothing said so.

It is now MarkDocDirtyByPath. These tests hold the line.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
SCRIPTS = REPO / "scripts" / "altium"
TOOLS = REPO / "src" / "eda_agent" / "tools"


def _pascal_sources():
    for path in sorted(SCRIPTS.glob("*.pas")):
        if path.name == "Altium_MCP.pas":         # generated bundle
            continue
        yield path


def _decommented(text: str) -> str:
    text = re.sub(r"\{[^}]*\}", " ", text, flags=re.S)
    return re.sub(r"//.*", " ", text)


def test_the_marking_procedure_is_not_called_a_save():
    """The name is the thing that misled every one of those readers."""
    offenders = []
    for path in _pascal_sources():
        code = _decommented(path.read_text(encoding="utf-8", errors="replace"))
        if re.search(r"\bSaveDocByPath\b", code):
            offenders.append(path.name)
    assert not offenders, (
        f"SaveDocByPath is back in {offenders}. It marks a document dirty "
        f"and never writes; naming it a save produced a false comment, a "
        f"false response field and two false tool docstrings")


def test_it_still_only_marks():
    """If it ever grows a DoFileSave, the name has to change back.

    This is not a style rule. The whole point of the rename is that the
    name matches the body, so the body is pinned too.
    """
    main = _decommented((SCRIPTS / "Main.pas").read_text(
        encoding="utf-8", errors="replace"))
    start = main.index("Procedure MarkDocDirtyByPath")
    body = main[start:main.index("\nEnd;", start)]
    assert "SetModified" in body
    assert "DoFileSave" not in body, (
        "MarkDocDirtyByPath now writes, so it is misnamed. Either rename "
        "it or move the write out")


@pytest.mark.parametrize("tool", ["lib_delete_footprint", "lib_rename_footprint"])
def test_the_library_writers_do_not_claim_to_save(tool):
    """Both used to say they saved the .PcbLib."""
    text = (TOOLS / "library.py").read_text(encoding="utf-8")
    # The paren anchors the name: without it lib_delete_footprint matched
    # lib_delete_footprint_primitives, which is a different tool.
    start = text.index(f"async def {tool}(")
    block = text[start:text.index('"""', text.index('"""', start) + 3)]

    claim = re.search(r"(then saves|saves the \.PcbLib|saved to disk)", block,
                      re.I)
    assert claim is None, (
        f"{tool} claims {claim.group(0)!r}. It marks the library dirty and "
        f"app_save_all is what writes")
    assert re.search(r"\bmarks?\b", block, re.I), (
        f"{tool} should say what it actually does to the library")


def test_the_delete_reply_says_the_write_is_deferred():
    """A docstring is not enough; the reply is what a caller reads.

    The user who reported this had success in hand and no reason to
    suspect the file was untouched.
    """
    text = (SCRIPTS / "Library.pas").read_text(encoding="utf-8",
                                               errors="replace")
    start = text.index("Function Lib_DeleteFootprint(")
    body = text[start:text.index("\nEnd;", start)]
    assert '"pending_save":true' in body, (
        "the delete reply must state that the file has not been written")
    assert '"written_to_disk":false' in body


def test_no_response_field_calls_a_marked_document_saved():
    """sheets_saved counted documents that were only marked."""
    offenders = []
    for path in _pascal_sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(_decommented(text).splitlines(), 1):
            if '"sheets_saved"' in line or "'sheets_saved'" in line:
                offenders.append(f"{path.name}:{i}")
    assert not offenders, (
        f"a response field still reports marked documents as saved: "
        f"{offenders}")
