# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Guard: the document gate, and the two tables it depends on.

WHY THE GATE EXISTS. EasyEDA fails a command aimed at the wrong document
in ways that never name the problem. Measured across 90 read-only tools
on a live schematic: 33 came back "Cannot read properties of null
(reading 'map')" and 14 never replied at all. Both were one cause, and
neither said so.

WHY CLASS PRESENCE CANNOT BE THE TEST. The runtime exposes the SAME 92
classes and 675 methods whichever document is open, and with NO document
open as well: measured both ways. So `eda[className]` is always truthy
and a guard built on it can never fire on a real editor. Document KIND
is the only discriminator.

WHY "UNKNOWN" MUST REFUSE. `currentDocumentKind()` returns 'unknown'
only after both probes ran and neither found a document, so it means
nothing is open rather than "could not tell". Treating it as unknown let
commands through, and some of them come back CLEAN:
`sch_PrimitiveWire.getAll()` returns `[]` with no document open, so
`easyeda_get_schematic_wires` reported ok:True with zero wires for a
sheet that was not open. Reading 0 where there had been 7 nearly led to
the conclusion that a working fix had regressed.

These tests read main.js as text rather than executing it. That is a
real limit: they prove the TABLES are coherent, not that the gate runs.
The .mjs harnesses are what exercise the running handler, and they are
still to be rebuilt.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_easyeda_api_calls_are_real import strip_comments

ROOT = Path(__file__).resolve().parents[1]
MAIN_JS = ROOT / "extensions" / "easyeda" / "main.js"

#: DELIBERATELY PERMISSIVE ON THE NAME. A pattern of only the characters
#: a correct command uses cannot see a misspelt one: 'design.snapshotX'
#: has an uppercase letter, so a strict pattern skipped the entry
#: entirely and the guard passed while the gate protected nothing. A
#: malformed name has to be VISIBLE here so the handler check can reject
#: it, which means matching any quoted key and judging it afterwards.
_HANDLER = re.compile(r"handlers\['([^']+)'\]")
_GATED = re.compile(r"^\s*'([^']+)':\s*'(pcb|schematic)',", re.M)


def _source() -> str:
    return strip_comments(MAIN_JS.read_text(encoding="utf-8"))


def _handlers() -> set:
    return set(_HANDLER.findall(_source()))


def _gated() -> dict:
    return dict(_GATED.findall(_source()))


def _works_anywhere() -> set:
    block = re.search(r"WORKS_ANYWHERE = \[(.*?)\];", _source(), re.S)
    assert block, "WORKS_ANYWHERE has moved or been renamed"
    return set(re.findall(r"'([^']+)'", block.group(1)))


def test_the_tables_were_found_at_all():
    """A regex that stops matching turns every test below vacuous."""
    assert len(_handlers()) > 150
    assert len(_gated()) > 15
    assert len(_works_anywhere()) >= 4


@pytest.mark.parametrize("table", ["gated", "anywhere"])
def test_every_named_command_is_a_real_handler(table):
    """A name with no handler gates nothing and exempts nothing.

    It is silent either way: the entry simply never matches a command,
    so a typo here disables the protection it was written to provide.
    """
    handlers = _handlers()
    names = _gated() if table == "gated" else _works_anywhere()
    missing = sorted(n for n in names if n not in handlers)
    assert not missing, f"{table} names commands that do not exist: {missing}"


def test_no_command_is_both_gated_and_exempt():
    """Contradictory entries. Whichever is checked first silently wins."""
    both = sorted(set(_gated()) & _works_anywhere())
    assert not both, f"listed as needing a document AND working anywhere: {both}"


def test_exempt_commands_are_only_from_the_two_document_namespaces():
    """WORKS_ANYWHERE exists to exempt pcb.* and sch.*, which the
    namespace rule would otherwise gate. Anything else there is not
    exempting anything, because nothing was gating it."""
    stray = sorted(n for n in _works_anywhere()
                   if not n.startswith(("pcb.", "sch.")))
    assert not stray, f"pointless exemptions: {stray}"


def test_gated_commands_are_outside_the_two_namespaces():
    """The table is for commands whose NAMESPACE does not say what they
    need. A pcb.* or sch.* entry is already covered, and duplicating it
    invites the two rules to disagree later."""
    redundant = sorted(n for n in _gated() if n.startswith(("pcb.", "sch.")))
    assert not redundant, (
        f"already gated by namespace, so listing them adds a second "
        f"source of truth: {redundant}")


def test_no_document_open_is_refused_rather_than_allowed():
    """The 'unknown' branch must produce a refusal, not fall through.

    Checked as source because the gate is async and reaches the editor.
    The precise failure this replaced: `if (kind !== 'pcb' && kind !==
    'schematic') return null;` allowed everything through when nothing
    was open.
    """
    src = _source()
    gate = src[src.index("async function wrongDocumentFor"):]
    gate = gate[:gate.index("\nfunction send(")]
    assert "none is open" in gate, (
        "the gate no longer refuses when no document is open")
    assert not re.search(
        r"if \(kind !== 'pcb' && kind !== 'schematic'\) return null;", gate), (
        "the fall-through for an unopened document is back")


def test_the_throw_path_still_declines_to_invent_a_kind():
    """A genuine cannot-tell must NOT be turned into a refusal.

    Without this the test above could be satisfied by refusing whenever
    the probe fails, which would break every command on an editor whose
    document probe is merely slow.
    """
    src = _source()
    gate = src[src.index("async function wrongDocumentFor"):]
    gate = gate[:gate.index("\nfunction send(")]
    catch = gate[gate.index("catch (e)"):]
    assert catch.lstrip("catch (e)").lstrip().startswith("{"), "shape changed"
    assert "return null;" in catch[:120], (
        "a failed document probe must still fall through, not refuse")
