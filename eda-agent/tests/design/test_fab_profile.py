# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Fab capability profile validation tests.

All numbers below are SYNTHETIC test-fixture values, not any real fab's
capabilities.
"""

from __future__ import annotations

import json

import pytest

from eda_agent.design.fab_profile import (
    FabProfile,
    copper_layers,
    dielectric_spans,
    load_fab_profile,
)


def _profile_dict(**overrides) -> dict:
    d = {
        "name": "TestFab synthetic profile",
        "source": "synthetic test fixture, not a real fab",
        "copper_layer_counts": [2, 4],
        "min_track_mils": 7.0,
        "min_gap_mils": 8.0,
        "min_drill_mils": 9.0,
        "min_annular_ring_mils": 5.0,
        "min_hole_to_hole_mils": 11.0,
        "min_mask_sliver_mils": 3.0,
        "min_silk_width_mils": 4.0,
        "stackups": [
            {
                "name": "test-2L",
                "layers": [
                    {"name": "Top", "kind": "copper",
                     "thickness_mils": 1.4, "copper_oz": 1.0},
                    {"name": "Core", "kind": "core",
                     "thickness_mils": 6.0, "er": 4.0},
                    {"name": "Bottom", "kind": "copper",
                     "thickness_mils": 1.4, "copper_oz": 1.0},
                ],
            },
        ],
    }
    d.update(overrides)
    return d


def _four_layer_stackup() -> dict:
    return {
        "name": "test-4L",
        "layers": [
            {"name": "Top", "kind": "copper",
             "thickness_mils": 1.4, "copper_oz": 1.0},
            {"name": "PP1", "kind": "prepreg", "thickness_mils": 2.0,
             "er": 3.8},
            {"name": "Core1", "kind": "core", "thickness_mils": 4.0,
             "er": 4.4},
            {"name": "Mid1", "kind": "copper", "thickness_mils": 0.7,
             "copper_oz": 0.5},
            {"name": "Core2", "kind": "core", "thickness_mils": 20.0,
             "er": 4.4},
            {"name": "Mid2", "kind": "copper", "thickness_mils": 0.7,
             "copper_oz": 0.5},
            {"name": "PP2", "kind": "prepreg", "thickness_mils": 6.0,
             "er": 3.8},
            {"name": "Bottom", "kind": "copper",
             "thickness_mils": 1.4, "copper_oz": 1.0},
        ],
    }


# --- loading -----------------------------------------------------------------

def test_load_from_dict_ok():
    res = load_fab_profile(_profile_dict())
    assert res["ok"] is True
    prof = res["profile"]
    assert isinstance(prof, FabProfile)
    assert prof.min_track_mils == 7.0
    assert prof.stackups[0].name == "test-2L"


def test_load_passthrough_for_profile_instance():
    prof = load_fab_profile(_profile_dict())["profile"]
    res = load_fab_profile(prof)
    assert res["ok"] is True
    assert res["profile"] is prof


def test_load_from_json_file(tmp_path):
    p = tmp_path / "fab.json"
    p.write_text(json.dumps(_profile_dict()), encoding="utf-8")
    res = load_fab_profile(p)
    assert res["ok"] is True
    assert res["profile"].name == "TestFab synthetic profile"
    # str path also works
    assert load_fab_profile(str(p))["ok"] is True


def test_load_missing_file():
    res = load_fab_profile("Z:/nope/does_not_exist.json")
    assert res["ok"] is False
    assert "not found" in res["reason"]


def test_load_bad_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    res = load_fab_profile(p)
    assert res["ok"] is False


def test_load_non_object_json(tmp_path):
    p = tmp_path / "list.json"
    p.write_text("[1, 2]", encoding="utf-8")
    res = load_fab_profile(p)
    assert res["ok"] is False
    assert "object" in res["reason"]


# --- profile validation --------------------------------------------------------

@pytest.mark.parametrize("field", [
    "min_track_mils", "min_gap_mils", "min_drill_mils",
    "min_annular_ring_mils", "min_hole_to_hole_mils",
    "min_mask_sliver_mils", "min_silk_width_mils",
])
def test_rejects_nonpositive_minimums(field):
    assert load_fab_profile(_profile_dict(**{field: -1.0}))["ok"] is False
    assert load_fab_profile(_profile_dict(**{field: 0.0}))["ok"] is False


def test_rejects_empty_name():
    assert load_fab_profile(_profile_dict(name=""))["ok"] is False


def test_rejects_empty_copper_layer_counts():
    assert load_fab_profile(
        _profile_dict(copper_layer_counts=[]))["ok"] is False


def test_rejects_zero_copper_layer_count():
    assert load_fab_profile(
        _profile_dict(copper_layer_counts=[0, 2]))["ok"] is False


def test_rejects_extra_field():
    assert load_fab_profile(_profile_dict(bogus=1))["ok"] is False


def test_rejects_missing_required_field():
    d = _profile_dict()
    del d["min_drill_mils"]
    assert load_fab_profile(d)["ok"] is False


def test_empty_stackup_list_is_allowed():
    res = load_fab_profile(_profile_dict(stackups=[]))
    assert res["ok"] is True
    assert res["profile"].stackups == []


# --- stackup validation --------------------------------------------------------

def _with_layers(layers):
    return _profile_dict(stackups=[{"name": "bad", "layers": layers}])


def test_rejects_empty_stackup_layers():
    assert load_fab_profile(_with_layers([]))["ok"] is False


def test_rejects_single_copper_stackup():
    res = load_fab_profile(_with_layers([
        {"name": "Top", "kind": "copper", "thickness_mils": 1.4},
        {"name": "Core", "kind": "core", "thickness_mils": 6.0},
    ]))
    assert res["ok"] is False


def test_rejects_dielectric_outer_layer():
    res = load_fab_profile(_with_layers([
        {"name": "Core", "kind": "core", "thickness_mils": 6.0},
        {"name": "Top", "kind": "copper", "thickness_mils": 1.4},
        {"name": "Bottom", "kind": "copper", "thickness_mils": 1.4},
    ]))
    assert res["ok"] is False


def test_rejects_adjacent_copper_layers():
    res = load_fab_profile(_with_layers([
        {"name": "Top", "kind": "copper", "thickness_mils": 1.4},
        {"name": "Mid", "kind": "copper", "thickness_mils": 1.4},
        {"name": "Core", "kind": "core", "thickness_mils": 6.0},
        {"name": "Bottom", "kind": "copper", "thickness_mils": 1.4},
    ]))
    assert res["ok"] is False
    # the validator names the offence
    assert "adjacent copper" in res["reason"]


def test_rejects_er_on_copper():
    res = load_fab_profile(_with_layers([
        {"name": "Top", "kind": "copper", "thickness_mils": 1.4, "er": 4.0},
        {"name": "Core", "kind": "core", "thickness_mils": 6.0},
        {"name": "Bottom", "kind": "copper", "thickness_mils": 1.4},
    ]))
    assert res["ok"] is False


def test_rejects_copper_oz_on_dielectric():
    res = load_fab_profile(_with_layers([
        {"name": "Top", "kind": "copper", "thickness_mils": 1.4},
        {"name": "Core", "kind": "core", "thickness_mils": 6.0,
         "copper_oz": 1.0},
        {"name": "Bottom", "kind": "copper", "thickness_mils": 1.4},
    ]))
    assert res["ok"] is False


def test_rejects_nonpositive_layer_thickness():
    res = load_fab_profile(_with_layers([
        {"name": "Top", "kind": "copper", "thickness_mils": 0.0},
        {"name": "Core", "kind": "core", "thickness_mils": 6.0},
        {"name": "Bottom", "kind": "copper", "thickness_mils": 1.4},
    ]))
    assert res["ok"] is False


def test_rejects_unknown_layer_kind():
    res = load_fab_profile(_with_layers([
        {"name": "Top", "kind": "copper", "thickness_mils": 1.4},
        {"name": "Core", "kind": "foam", "thickness_mils": 6.0},
        {"name": "Bottom", "kind": "copper", "thickness_mils": 1.4},
    ]))
    assert res["ok"] is False


def test_rejects_stackup_copper_count_not_offered():
    # a 4-copper stackup when the fab only offers 2-layer boards
    d = _profile_dict(copper_layer_counts=[2],
                      stackups=[_four_layer_stackup()])
    res = load_fab_profile(d)
    assert res["ok"] is False
    assert "4 copper layers" in res["reason"]


# --- helpers -----------------------------------------------------------------

def test_copper_layers_top_to_bottom():
    d = _profile_dict(stackups=[_profile_dict()["stackups"][0],
                                _four_layer_stackup()])
    prof = load_fab_profile(d)["profile"]
    names = [c.name for c in copper_layers(prof.stackups[1])]
    assert names == ["Top", "Mid1", "Mid2", "Bottom"]


def test_dielectric_spans_simple():
    prof = load_fab_profile(_profile_dict())["profile"]
    spans = dielectric_spans(prof.stackups[0])
    assert len(spans) == 1
    assert spans[0].height_mils == 6.0
    assert spans[0].er == 4.0
    assert spans[0].kind == "core"
    assert spans[0].ply_count == 1


def test_dielectric_spans_combined_plies():
    prof = load_fab_profile(
        _profile_dict(stackups=[_four_layer_stackup()]))["profile"]
    spans = dielectric_spans(prof.stackups[0])
    assert len(spans) == 3
    # Top..Mid1: 2 mil er 3.8 prepreg + 4 mil er 4.4 core
    top = spans[0]
    assert top.height_mils == pytest.approx(6.0)
    assert top.er == pytest.approx((2.0 * 3.8 + 4.0 * 4.4) / 6.0)  # 4.2
    assert top.kind == "prepreg"
    assert top.ply_count == 2
    # middle and bottom spans are single-ply
    assert spans[1].height_mils == 20.0 and spans[1].ply_count == 1
    assert spans[2].height_mils == 6.0 and spans[2].kind == "prepreg"


def test_dielectric_span_er_none_when_a_ply_lacks_er():
    layers = [
        {"name": "Top", "kind": "copper", "thickness_mils": 1.4},
        {"name": "PP", "kind": "prepreg", "thickness_mils": 2.0},  # no er
        {"name": "Core", "kind": "core", "thickness_mils": 4.0, "er": 4.4},
        {"name": "Bottom", "kind": "copper", "thickness_mils": 1.4},
    ]
    prof = load_fab_profile(_with_layers(layers))["profile"]
    span = dielectric_spans(prof.stackups[0])[0]
    assert span.er is None
    assert span.height_mils == pytest.approx(6.0)


def test_validation_is_deterministic():
    a = load_fab_profile(_profile_dict())["profile"]
    b = load_fab_profile(_profile_dict())["profile"]
    assert a == b
