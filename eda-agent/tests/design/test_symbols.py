# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for design.symbols: SymbolModel, cache, extractor parsing.

Coverage targets:
- pin_by_id: designator wins, name fallback, missing returns None.
- _bbox_from_pins: tight enclosure of body-attach ends, padded.
- parse_symbol_from_details: handles Pascal v8 (with length) and
  back-compat for older responses that lacked the length field.
- SymbolCache: put -> get round trips; mtime invalidates; rehydrates
  by SymbolModel.from_dict.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from eda_agent.design.symbols import (
    SymbolBBox,
    SymbolCache,
    SymbolModel,
    SymbolPin,
    _bbox_from_pins,
    parse_symbol_from_details,
    pin_direction,
)


def _pin(des: str, x: int, y: int, orientation: int = 0, length: int = 200,
         name: str = "", electrical: str = "passive") -> SymbolPin:
    return SymbolPin(
        designator=des, name=name or des, x=x, y=y,
        orientation=orientation, length=length,
        electrical_type=electrical,
    )


def _model(*pins: SymbolPin) -> SymbolModel:
    bbox = _bbox_from_pins(pins)
    return SymbolModel(
        lib_path="/fake.SchLib", lib_ref="X",
        pins=pins, body_bbox=bbox,
    )


def test_pin_by_id_designator_wins():
    sym = _model(_pin("1", 0, 0, name="VCC"), _pin("2", 100, 0, name="GND"))
    assert sym.pin_by_id("1").name == "VCC"
    assert sym.pin_by_id("2").name == "GND"


def test_pin_by_id_falls_back_to_name():
    sym = _model(_pin("1", 0, 0, name="VCC"), _pin("2", 100, 0, name="GND"))
    assert sym.pin_by_id("VCC").designator == "1"
    assert sym.pin_by_id("GND").designator == "2"


def test_pin_by_id_missing_returns_none():
    sym = _model(_pin("1", 0, 0))
    assert sym.pin_by_id("99") is None
    assert sym.pin_by_id("NOPE") is None


def test_bbox_padded_when_pins_collapse():
    # All pins at origin -> bbox still gets non-zero area via padding.
    bbox = _bbox_from_pins(tuple([_pin("1", 0, 0, length=0)]))
    assert bbox.width > 0
    assert bbox.height > 0


def test_bbox_encloses_body_attach_ends():
    # Pin endpoint at (200, 0) facing right (orientation 0, length 200) ->
    # body-attach end is at (0, 0). Pin endpoint at (-200, 0) facing left
    # (orientation 2, length 200) -> body-attach end is at (0, 0). The
    # bbox should be a small square around the origin, padded.
    p1 = _pin("1", 200, 0, orientation=0, length=200)
    p2 = _pin("2", -200, 0, orientation=2, length=200)
    bbox = _bbox_from_pins((p1, p2))
    assert bbox.x_min < 0 < bbox.x_max
    assert bbox.y_min < 0 < bbox.y_max


def test_pin_direction_quadrants():
    # 0=right (+X), 1=up (+Y), 2=left (-X), 3=down (-Y).
    assert pin_direction(0) == (1, 0)
    assert pin_direction(1) == (0, 1)
    assert pin_direction(2) == (-1, 0)
    assert pin_direction(3) == (0, -1)
    # mod 4 normalisation -- callers can pass raw rotation/90 sums.
    assert pin_direction(4) == (1, 0)
    assert pin_direction(7) == (0, -1)


def test_parse_symbol_from_details_with_length():
    """Pascal v2026.05.15.8+ includes pin.length; parser should use it."""
    details = {
        "name": "GenericIC",
        "description": "Precision timer",
        "pins": [
            {"designator": "8", "name": "VCC", "x": -200, "y": 350,
             "orientation": 2, "length": 200, "electrical_type": "power"},
            {"designator": "1", "name": "GND", "x": -200, "y": -350,
             "orientation": 2, "length": 200, "electrical_type": "power"},
        ],
    }
    model = parse_symbol_from_details(details, lib_path="/x.SchLib")
    assert model.lib_ref == "GenericIC"
    assert model.description == "Precision timer"
    assert len(model.pins) == 2
    assert model.pins[0].length == 200
    assert model.pins[0].electrical_type == "power"


def test_parse_symbol_from_details_back_compat_no_length():
    """Older Pascal responses lacked length; parser falls back to 200."""
    details = {
        "name": "R",
        "pins": [
            {"designator": "1", "name": "1", "x": -100, "y": 0,
             "orientation": 2, "electrical_type": "passive"},
        ],
    }
    model = parse_symbol_from_details(details, lib_path="/x.SchLib")
    assert model.pins[0].length == 200


def test_parse_symbol_from_details_empty_pins_safe():
    """A symbol with no pins (rare, but possible for graphics-only blocks)
    should still produce a valid SymbolModel without crashing."""
    model = parse_symbol_from_details({"name": "Logo"}, lib_path="/x.SchLib")
    assert model.lib_ref == "Logo"
    assert model.pins == ()
    # bbox still has a sensible (non-zero) default so collision math works.
    assert model.body_bbox.width > 0


def test_symbol_cache_put_get_round_trip(tmp_path: Path):
    lib_path = tmp_path / "x.SchLib"
    lib_path.write_text("dummy", encoding="utf-8")
    cache = SymbolCache(tmp_path / ".cache")
    model = SymbolModel(
        lib_path=str(lib_path), lib_ref="R10k",
        pins=(_pin("1", -100, 0), _pin("2", 100, 0)),
        body_bbox=SymbolBBox(x_min=-50, y_min=-50, x_max=50, y_max=50),
    )
    cache.put(model)
    got = cache.get(str(lib_path), "R10k")
    assert got is not None
    assert got.lib_ref == "R10k"
    assert len(got.pins) == 2
    assert got.pins[0].designator == "1"


def test_symbol_cache_mtime_invalidates(tmp_path: Path):
    """Touching the SchLib mtime must invalidate the cached entry."""
    lib_path = tmp_path / "x.SchLib"
    lib_path.write_text("v1", encoding="utf-8")
    cache = SymbolCache(tmp_path / ".cache")
    model = SymbolModel(
        lib_path=str(lib_path), lib_ref="R",
        pins=(_pin("1", 0, 0),),
        body_bbox=SymbolBBox(x_min=-1, y_min=-1, x_max=1, y_max=1),
    )
    cache.put(model)
    assert cache.is_fresh(str(lib_path))

    # Bump mtime forward by 5s (some filesystems have 1s mtime granularity).
    new_mtime = os.path.getmtime(lib_path) + 5
    os.utime(lib_path, (new_mtime, new_mtime))

    assert not cache.is_fresh(str(lib_path))
    assert cache.get(str(lib_path), "R") is None


def test_symbol_cache_missing_returns_none(tmp_path: Path):
    cache = SymbolCache(tmp_path / ".cache")
    assert cache.get("/does/not/exist.SchLib", "X") is None


def test_symbol_model_dict_round_trip():
    sym = SymbolModel(
        lib_path="/x.SchLib", lib_ref="X",
        pins=(_pin("1", -100, 0, orientation=2, length=150,
                   name="A", electrical="input"),),
        body_bbox=SymbolBBox(x_min=-50, y_min=-50, x_max=50, y_max=50),
        designator_prefix="U", description="An IC",
    )
    d = sym.to_dict()
    sym2 = SymbolModel.from_dict(d)
    assert sym2.lib_ref == sym.lib_ref
    assert sym2.pins == sym.pins
    assert sym2.body_bbox == sym.body_bbox
    assert sym2.designator_prefix == sym.designator_prefix
    assert sym2.description == sym.description
