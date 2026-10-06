# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Both halves of the bridge must know the same commands.

The EasyEDA backend is two programs that have to agree: Python sends
``category.action`` strings and ``extensions/easyeda/main.js`` answers
them. Nothing in either file enforces the other's vocabulary, so a
rename on one side produces a tool that fails only when someone runs it
against a live editor, which is the most expensive place to find out.

This is the systematic form of a pattern that cost four separate
defects in one session: a fix applied to one copy of duplicated logic
and not its twin. The destructive-method list, the DRC arguments, the
ranking in the guidance module and the reach of the API guard all had
the same shape. Comparing the two vocabularies mechanically is cheaper
than remembering.

Two directions, and they fail differently:

* CALLED BUT NOT HANDLED is a broken tool. The command goes out and the
  extension answers UNKNOWN_COMMAND.
* HANDLED BUT NEVER CALLED is usually dead weight, and occasionally
  deliberate. The exceptions are listed with their reason, so the list
  stays a decision rather than an oversight.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PY = _ROOT / "src" / "eda_agent" / "tools" / "easyeda.py"
_JS = _ROOT / "extensions" / "easyeda" / "main.js"

#: Handlers kept on purpose with no Python caller, and why.
_INTENTIONAL_ORPHANS = {
    "proj.delete_project": (
        "EasyEDA has no project-delete API, so the Python tool refuses "
        "locally without sending anything. The handler stays because the "
        "extension is reachable by anything speaking the protocol, not "
        "only by this server, and it should explain itself there too "
        "rather than calling an undefined method."),
}


def _called() -> set[str]:
    src = _PY.read_text(encoding="utf-8")
    return set(re.findall(r'_call\(\s*"([a-z_]+\.[a-z_0-9]+)"', src))


def _handled() -> set[str]:
    src = _JS.read_text(encoding="utf-8")
    return set(re.findall(r"handlers\['([a-z_]+\.[a-z_0-9]+)'\]", src))


_CALLED = _called()
_HANDLED = _handled()


def test_both_vocabularies_were_actually_read():
    """A regex that stops matching would make this file vacuous."""
    assert len(_CALLED) > 150, (
        f"only {len(_CALLED)} commands parsed out of easyeda.py; the call "
        f"shape changed and this guard is comparing almost nothing")
    assert len(_HANDLED) > 150, (
        f"only {len(_HANDLED)} handlers parsed out of main.js")


def test_every_command_python_sends_has_a_handler():
    missing = sorted(_CALLED - _HANDLED)
    assert not missing, (
        "these commands are sent by a tool and answered by no handler, so "
        "the tool fails against a live editor and passes every test "
        "here:\n  " + "\n  ".join(missing))


def test_every_handler_is_reachable_or_listed_as_deliberate():
    orphans = sorted(_HANDLED - _CALLED - set(_INTENTIONAL_ORPHANS))
    assert not orphans, (
        "these handlers exist and nothing calls them. Either wire them to "
        "a tool, delete them, or add them to _INTENTIONAL_ORPHANS with "
        "the reason:\n  " + "\n  ".join(orphans))


@pytest.mark.parametrize("command,reason", sorted(_INTENTIONAL_ORPHANS.items()))
def test_the_deliberate_orphans_still_exist(command, reason):
    """An exemption for a handler nobody kept is dead weight that hides
    the next one."""
    assert command in _HANDLED, (
        f"{command} is exempted as a deliberate orphan but no handler "
        f"defines it any more; remove the exemption")
    assert command not in _CALLED, (
        f"{command} is exempted as having no caller, but Python calls it "
        f"now; remove the exemption so the orphan check covers it")
    assert len(reason) > 40
