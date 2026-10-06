# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A marker missing its type's fields must be refused, not drawn.

``generateIndicatorMarkers`` is how a review points at a defect on the
board instead of describing where it is. Each shape carries a ``type``
and only the fields that type uses, taken from
``IDMT_IndicatorMarkerShape`` and ``EDMT_IndicatorMarkerType`` in the
official reference:

    point      x, y
    line       startX, startY, endX, endY
    rectangle  left, right, top, bottom
    circle     x, y, r
    arc        startX, startY, endX, endY, angle

The editor accepts a shape whose fields do not match its type and draws
it somewhere unintended. A review pointing confidently at the wrong
place is worse than one that fails, so the extension validates before
sending and this pins that it does.

The validation lives in the extension, so these tests read the JS
rather than calling it. That is the honest scope: they check the table
and the refusal exist and match the reference, not that the editor
behaves.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_JS = _ROOT / "extensions" / "easyeda" / "main.js"
_REF = (_ROOT / "reference" / "easyeda-api-skill" / "references")

#: What the reference says each type uses.
_EXPECTED = {
    "point": {"x", "y"},
    "line": {"startX", "startY", "endX", "endY"},
    "rectangle": {"left", "right", "top", "bottom"},
    "circle": {"x", "y", "r"},
    "arc": {"startX", "startY", "endX", "endY", "angle"},
}


def _marker_fields() -> dict[str, set]:
    """The MARKER_FIELDS table as the extension actually declares it."""
    src = _JS.read_text(encoding="utf-8")
    block = re.search(r"const MARKER_FIELDS = \{(.*?)\n\};", src, re.S)
    assert block, "MARKER_FIELDS is gone; marker validation is not happening"
    out = {}
    for name, fields in re.findall(r"(\w+):\s*\[([^\]]*)\]", block.group(1)):
        out[name] = set(re.findall(r"'(\w+)'", fields))
    return out


def test_the_table_was_actually_read():
    assert len(_marker_fields()) == 5


@pytest.mark.parametrize("kind,fields", sorted(_EXPECTED.items()))
def test_each_type_declares_the_fields_the_reference_gives_it(kind, fields):
    table = _marker_fields()
    assert kind in table, f"{kind} is not a validated marker type"
    assert table[kind] == fields, (
        f"{kind} validates {sorted(table[kind])}; the reference gives it "
        f"{sorted(fields)}. A field checked that the type does not use "
        f"refuses valid markers; one missing lets a marker through to be "
        f"drawn in the wrong place")


@pytest.mark.skipif(not _REF.is_dir(), reason="official reference not cloned")
def test_the_five_types_are_the_documented_enum():
    """A sixth type appearing would be silently unusable here."""
    enum = (_REF / "enums" / "EDMT_IndicatorMarkerType.md").read_text(
        encoding="utf-8")
    # EITHER QUOTE. The vendor writes the enum values into a markdown
    # table, and which quote character it uses is their formatting
    # rather than the API. It changed: the reference published in
    # August wrote `"arc"` and the current one writes `'arc'`, along
    # with switching the prose from Chinese to English. The five values
    # are identical.
    #
    # Matching only double quotes made this read NOTHING and compare an
    # empty set, so a cosmetic edit upstream failed as though the
    # marker types had disappeared.
    documented = set(re.findall(r"""`['"]([a-z]+)['"]`""", enum))
    assert documented, (
        "no enum values parsed out of EDMT_IndicatorMarkerType.md. The "
        "reference's table format has changed again; fix the parser "
        "rather than the expectation")
    assert documented == set(_EXPECTED), (
        f"the reference documents {sorted(documented)} and this file "
        f"expects {sorted(_EXPECTED)}")
    assert set(_marker_fields()) == documented, (
        f"the extension validates {sorted(_marker_fields())}, the enum "
        f"documents {sorted(documented)}")


def _run_validation(marker) -> str:
    """Execute the extension's REAL validation text against one marker.

    Reading the source and checking that the throw appears before the
    call is not enough. Disabling the condition with ``if (false && ...)``
    leaves the text in place and the order intact, and an earlier
    version of this file passed exactly that mutation. So the actual
    code is extracted verbatim and run.

    Returns the refusal message, or "" when the marker was accepted.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available to execute the extension code")
    src = _JS.read_text(encoding="utf-8")

    table = re.search(r"const MARKER_FIELDS = \{.*?\n\};", src, re.S)
    loop = re.search(
        r"  const markers = raw\.map\(\(m, i\) => \{.*?\n  \}\);", src, re.S)
    assert table and loop, "the marker table or its validation loop is gone"

    harness = "\n".join([
        table.group(0),
        "const raw = [" + json.dumps(marker) + "];",
        "try {",
        loop.group(0),
        "  console.log('ACCEPTED');",
        "} catch (e) { console.log('REFUSED: ' + e.message); }",
    ])
    with tempfile.TemporaryDirectory() as tmp:
        script = pathlib.Path(tmp) / "check.mjs"
        script.write_text(harness, encoding="utf-8")
        out = subprocess.run([node, str(script)], capture_output=True,
                             text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    line = out.stdout.strip()
    return "" if line == "ACCEPTED" else line


def test_a_well_formed_marker_is_accepted():
    assert _run_validation({"type": "circle", "x": 1, "y": 2, "r": 3}) == ""


@pytest.mark.parametrize("marker,missing", [
    ({"type": "circle", "x": 1, "y": 2}, "r"),
    ({"type": "point", "x": 1}, "y"),
    ({"type": "rectangle", "left": 0, "right": 1, "top": 2}, "bottom"),
    ({"type": "line", "startX": 0, "startY": 0, "endX": 1}, "endY"),
    ({"type": "arc", "startX": 0, "startY": 0, "endX": 1, "endY": 1},
     "angle"),
])
def test_a_marker_missing_its_own_field_is_refused(marker, missing):
    """Executed, not pattern-matched, so a disabled check fails here."""
    message = _run_validation(marker)
    assert message.startswith("REFUSED"), (
        f"a {marker['type']} with no {missing} was ACCEPTED and would be "
        f"drawn somewhere unintended")
    assert missing in message


def test_an_unknown_type_is_refused_and_lists_the_valid_ones():
    message = _run_validation({"type": "triangle", "x": 1, "y": 2})
    assert message.startswith("REFUSED")
    for kind in _EXPECTED:
        assert kind in message


def test_a_non_numeric_coordinate_is_refused():
    """A string coordinate is what a JSON client sends by accident."""
    message = _run_validation({"type": "point", "x": "10", "y": 2})
    assert message.startswith("REFUSED")


def test_an_unknown_type_names_the_valid_ones():
    src = _JS.read_text(encoding="utf-8")
    assert "expected " in src and "Object.keys(MARKER_FIELDS)" in src, (
        "an unknown marker type should list the types that are valid, "
        "rather than failing with the one that is not")


def test_clearing_exists_and_takes_no_marker_handle():
    """The API removes markers as a SET, so the tool must not imply
    otherwise by accepting one."""
    src = _JS.read_text(encoding="utf-8")
    assert "handlers['editor.clear_findings']" in src
    assert "removeIndicatorMarkers" in src
