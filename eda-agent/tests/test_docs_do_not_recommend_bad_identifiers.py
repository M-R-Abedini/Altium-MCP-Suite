# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The API reference must not recommend identifiers the linter rejects.

``docs/altium-delphiscript/`` is this project's own extraction of the
DelphiScript API, written to be the thing you consult before writing
Pascal. ``scripts/altium/lint.py`` keeps a deny-list of identifiers that
do not exist in Altium, each one added after it failed at runtime.

Those two disagreed. The reference documented ``ObjectId : TObjectId``
as "``eSchDoc`` for a sheet, ``eSchLib`` for a library", and documented
``Client.GetDocumentCount`` as a real method, while the linter rejected
both by name as undeclared. Following the reference produced code the
linter refused, and if a call site slipped past the regex it faulted
where ``Try/Except`` cannot reach and stopped the polling loop.

So the deny-list is the authority and the prose has to agree with it. A
deny-listed name may still APPEAR in the docs, and should: saying "there
is no eSchDoc, test for eSchLib instead" is exactly what stops the next
person reaching for it. What is not allowed is presenting one as usable
API, which in this reference means a bolded signature at the start of a
line, the shape every real entry uses.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs" / "altium-delphiscript"
LINT = REPO / "scripts" / "altium" / "lint.py"

#: The reference's own entry format: a line that opens with a bolded
#: name or signature. Prose that merely names an identifier does not
#: match, which is what lets a warning mention it.
_ENTRY = re.compile(r"^\s*\*\*`([A-Za-z_][\w.]*)")


def _denied() -> dict[str, str]:
    """Every identifier lint.py rejects, to its suggested replacement."""
    tree = ast.parse(LINT.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        target = getattr(node.targets[0], "id", "")
        if target not in ("KNOWN_WRONG_E_IDENTS", "KNOWN_WRONG_METHOD_NAMES"):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for key, value in zip(node.value.keys, node.value.values):
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                hint = (value.value if isinstance(value, ast.Constant)
                        else "")
                out[key.value] = hint
    return out


def test_no_doc_entry_documents_a_denied_identifier():
    denied = _denied()
    assert len(denied) > 10, (
        f"only {len(denied)} deny-listed identifiers parsed out of "
        f"lint.py; the parse broke and this guard proves nothing")

    offenders = []
    for path in sorted(DOCS.rglob("*.md")):
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace")
                    .split("\n"), 1):
            match = _ENTRY.match(line)
            if match and match.group(1) in denied:
                name = match.group(1)
                offenders.append(
                    f"{path.relative_to(REPO).as_posix()}:{lineno}  "
                    f"{name}  (lint says: {denied[name]})")

    assert not offenders, (
        "these are documented as usable API but the linter rejects them "
        "as undeclared, so following the reference produces code that "
        "faults at runtime:\n  " + "\n  ".join(offenders)
        + "\nRewrite the entry to say the name does not exist and what "
          "to use instead.")


def test_mentioning_a_denied_identifier_in_prose_is_allowed():
    """The warnings that replaced the two bad entries must survive.

    If this ever fails, the check has tightened from "do not document
    it as API" to "do not name it", which would delete the warnings
    that are the reason anyone stops using these.
    """
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in DOCS.rglob("*.md"))
    assert "There is no `eSchDoc` constant" in text
    assert "There is no `GetDocumentCount`" in text


def test_the_entry_pattern_matches_a_real_entry():
    """A regex that matched nothing would pass the check above."""
    assert _ENTRY.match("**`GetDocumentCount : Integer`**")
    assert _ENTRY.match("  **`ObjectId : TObjectId`**: something")
    assert _ENTRY.match("**`GetDocumentCount`**").group(1) == \
        "GetDocumentCount"
    # Prose that merely names one must NOT match.
    assert not _ENTRY.match("There is no `eSchDoc` constant. Writing it")
    assert not _ENTRY.match("along with `eSchDocument` and `ePcbDoc`.")
