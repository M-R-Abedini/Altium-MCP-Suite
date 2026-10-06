# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The audit classifier and the dashboard's must agree on every prefix.

The audit code carried a docstring saying it mirrored the dashboard's
``componentClass()``. It did not. Measured across 25 ordinary
designators the two disagreed on TEN, and twice they landed in opposite
categories: ``CN1`` was a connector on the dashboard and a passive in
the audit, ``XTAL1`` the reverse. ``Y1``, the standard crystal prefix,
classified as "other" in the audit, so a crystal was in no class at all.

The cause was structural rather than a typo. The audit tried a small
multi-letter map and then fell back to the FIRST LETTER, and that
fallback can never agree, because the prefixes that matter are
multi-letter and their first letters belong elsewhere: LED starts with
L, TVS with T, SW with S. Both sides now match the whole letter prefix
against one table.

Two places state the same fact and nothing connected them, which is the
defect shape this project keeps finding. This is the connection. It
parses the JavaScript rather than restating it, so a prefix added on
either side and not the other fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from eda_agent.tools.audit import (
    _DESIGNATOR_CLASSES,
    _component_class_from_designator,
)

_DASHBOARD = (Path(__file__).resolve().parents[1] / "src" / "eda_agent"
              / "web" / "dashboard_static" / "index.html")


def _dashboard_table() -> dict:
    """Prefix -> class, read out of componentClass() in the dashboard."""
    html = _DASHBOARD.read_text(encoding="utf-8")
    start = html.index("function componentClass")
    body = html[start:]
    body = body[:body.index("\n}")]

    table: dict[str, str] = {}
    for line in body.splitlines():
        prefixes = re.findall(r'p === "([A-Z]+)"', line)
        returned = re.search(r'return "(\w+)"', line)
        if prefixes and returned:
            for prefix in prefixes:
                table[prefix] = returned.group(1)
    return table


def test_the_dashboard_function_was_actually_parsed():
    """A regex that stops matching would make every check below vacuous."""
    table = _dashboard_table()
    assert len(table) >= 15, (
        f"only {len(table)} prefixes parsed out of componentClass(); the "
        f"function was rewritten and this guard is reading nothing")
    # An anchor from each class, so a partial parse cannot pass either.
    for prefix, expected in (("U", "ic"), ("J", "connector"),
                             ("D", "semi"), ("R", "passive")):
        assert table.get(prefix) == expected


def test_both_sides_know_the_same_prefixes():
    dashboard = set(_dashboard_table())
    python = set(_DESIGNATOR_CLASSES)
    assert dashboard == python, (
        f"only the dashboard knows {sorted(dashboard - python)}; "
        f"only the audit knows {sorted(python - dashboard)}")


@pytest.mark.parametrize("prefix,expected", sorted(_dashboard_table().items()))
def test_every_dashboard_prefix_classifies_the_same(prefix, expected):
    """Through the real function, not the table, so the lookup is covered."""
    assert _component_class_from_designator(prefix + "1") == expected


@pytest.mark.parametrize("designator,expected", [
    # The ten that disagreed, pinned so they cannot drift back.
    ("A1", "ic"),
    ("CN1", "connector"),
    ("SW1", "connector"),
    ("S1", "connector"),
    ("LED1", "semi"),
    ("T1", "semi"),
    ("VR1", "semi"),
    ("TVS1", "semi"),
    ("Y1", "passive"),
    ("XTAL1", "passive"),
])
def test_the_previously_wrong_ones(designator, expected):
    assert _component_class_from_designator(designator) == expected


@pytest.mark.parametrize("designator", ["", "1", "?", "123", None])
def test_nothing_classifiable_is_other_rather_than_a_crash(designator):
    assert _component_class_from_designator(designator) == "other"


def test_a_multi_letter_prefix_beats_its_first_letter():
    """The property the first-letter fallback could not have.

    LED is a semiconductor although L alone is an inductor; TVS is a
    semiconductor although T alone is one too but S is a connector.
    """
    assert _component_class_from_designator("L1") == "passive"
    assert _component_class_from_designator("LED1") == "semi"
    assert _component_class_from_designator("S1") == "connector"
    assert _component_class_from_designator("TVS1") == "semi"


def test_case_and_suffix_do_not_matter():
    for des in ("ic1", "IC1", "Ic12", "IC_3", "IC99A"):
        assert _component_class_from_designator(des) == "ic", des
