# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every layer name we send must be one the Pascal actually knows.

GetLayerFromString maps a name to a TLayer and falls back to
**eTopLayer** for anything it does not recognise. That fallback is the
problem: a misspelt layer does not raise, it silently puts silkscreen,
mask or assembly art onto TOP COPPER, and the first symptom is a
fabricated board.

The names currently match. This is here so they keep matching: the map
is edited whenever a new layer is supported, and a typo in it would be
invisible until someone read a Gerber.

Reading the Pascal is the point. Asserting the map against a list
hand-copied into this file would agree with itself no matter what the
bridge accepts, which is the failure this whole class of check exists to
avoid.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_UTILS = (Path(__file__).resolve().parents[1]
          / "scripts" / "altium" / "Utils.pas")


def _accepted_layer_names() -> set[str]:
    """The exact set GetLayerFromString maps, read from the source."""
    text = _UTILS.read_text(encoding="utf-8", errors="replace")
    start = text.index("Function GetLayerFromString")
    end = text.index("\nEnd;", start)
    names = set(re.findall(r"^\s*'([A-Za-z0-9_]+)'\s*:", text[start:end], re.M))
    assert len(names) > 40, (
        f"only parsed {len(names)} layer names; the Pascal shape changed "
        f"and this test is no longer reading what it thinks it is")
    return names


def test_the_pascal_still_falls_back_silently():
    """The premise. If the fallback ever becomes an error, the risk this
    test guards against is gone and the test can go with it."""
    text = _UTILS.read_text(encoding="utf-8", errors="replace")
    start = text.index("Function GetLayerFromString")
    body = text[start:text.index("\nEnd;", start)]
    assert re.search(r"Else\s+Result\s*:=\s*eTopLayer", body), (
        "GetLayerFromString no longer defaults to eTopLayer; re-read this "
        "test's premise before trusting it")


def test_every_altium_emitter_layer_is_known_to_the_bridge():
    """The importer's neutral-id -> Altium-layer map."""
    from eda_agent.libimport.easyeda.altium import _ALTIUM_LAYER

    accepted = _accepted_layer_names()
    unknown = sorted(n for n in _ALTIUM_LAYER.values() if n not in accepted)
    assert not unknown, (
        f"these layer names are not in GetLayerFromString, so anything "
        f"drawn on them lands on TOP COPPER instead: {unknown}")


def test_the_bottom_side_layer_ids_all_resolve_to_bottom_layers():
    """_BOTTOM_SIDE_LAYERS drives text mirroring. An id that is not
    actually a bottom layer would mirror top-side text, which reads
    backwards on the finished board."""
    from eda_agent.libimport.easyeda.altium import (
        _ALTIUM_LAYER, _BOTTOM_SIDE_LAYERS,
    )

    for layer_id in _BOTTOM_SIDE_LAYERS:
        name = _ALTIUM_LAYER.get(layer_id)
        if name is None:
            continue        # id with no Altium destination, nothing drawn
        assert "Bottom" in name or name == "Mechanical14", (
            f"neutral id {layer_id} is treated as bottom-side but maps to "
            f"{name!r}")


def test_no_top_layer_id_is_marked_bottom_side():
    """The inverse, which is the one that silently mirrors good text."""
    from eda_agent.libimport.easyeda.altium import (
        _ALTIUM_LAYER, _BOTTOM_SIDE_LAYERS,
    )

    for layer_id, name in _ALTIUM_LAYER.items():
        if name.startswith("Top"):
            assert layer_id not in _BOTTOM_SIDE_LAYERS, (
                f"{name} (id {layer_id}) is marked bottom-side, so text on "
                f"it would be mirrored and read backwards")


@pytest.mark.parametrize("name", ["TopOverlay", "BottomOverlay", "MultiLayer",
                                  "KeepOutLayer", "Mechanical13",
                                  "Mechanical14"])
def test_the_names_this_project_relies_on_are_present(name):
    """Spot-checks with a reason: these are the destinations the importer
    picks for silkscreen, assembly art, keepout and through-hole pads."""
    assert name in _accepted_layer_names()


def test_multilayer_capitalisation_is_exact():
    """The Case is exact-match, and both spellings appear in this
    codebase's own strings. Only one of them works."""
    accepted = _accepted_layer_names()
    assert "MultiLayer" in accepted
    assert "Multilayer" not in accepted, (
        "if the Pascal ever accepts both, the note in this test is stale")
