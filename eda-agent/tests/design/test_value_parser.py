# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Engineering value-string parser / formatter tests."""

from __future__ import annotations

import pytest

from eda_agent.design.value_parser import (
    format_value,
    parse_value,
    try_parse_value,
)


@pytest.mark.parametrize("text,expected", [
    # SI-prefixed
    ("4.7k", 4700.0),
    ("4.7kF", 4700.0),           # stray unit stripped
    ("100", 100.0),
    ("2.2", 2.2),
    ("1M", 1e6),
    ("10uF", 10e-6),
    ("0.1uF", 0.1e-6),
    ("100nF", 100e-9),
    ("4.7pF", 4.7e-12),
    ("10mH", 10e-3),
    ("470R", 470.0),
    ("470ohm", 470.0),
    ("1k5", 1500.0),
    # RKM / IEC 60062
    ("4k7", 4700.0),
    ("2R2", 2.2),
    ("R47", 0.47),
    ("4n7", 4.7e-9),
    ("2p2", 2.2e-12),
    ("1M5", 1.5e6),
    ("4K7", 4700.0),             # uppercase kilo alias
])
def test_parse_known_forms(text, expected):
    assert parse_value(text) == pytest.approx(expected)


def test_mega_vs_milli_case_sensitive():
    assert parse_value("1M") == pytest.approx(1e6)
    assert parse_value("1m") == pytest.approx(1e-3)
    assert parse_value("4M7") == pytest.approx(4.7e6)
    assert parse_value("4m7") == pytest.approx(4.7e-3)


def test_micro_sign_normalized():
    assert parse_value("10µF") == pytest.approx(10e-6)


@pytest.mark.parametrize("bad", [
    "", "   ", "abc", "10kk", "k", "1.2.3", "10x", "..", "R",
])
def test_parse_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_value(bad)


def test_try_parse_returns_none_on_garbage():
    assert try_parse_value("10kk") is None
    assert try_parse_value("4k7") == pytest.approx(4700.0)


def test_non_string_raises():
    with pytest.raises(ValueError):
        parse_value(4700)        # type: ignore[arg-type]


@pytest.mark.parametrize("value,unit,expected", [
    (4700.0, "", "4.7k"),
    (100e-9, "F", "100nF"),
    (10e-6, "F", "10uF"),
    (2.2, "", "2.2"),
    (1e6, "", "1M"),
    (0.47, "", "470m"),
    (0, "F", "0F"),
])
def test_format_value(value, unit, expected):
    assert format_value(value, unit) == expected


@pytest.mark.parametrize("text", [
    "4.7k", "100nF", "10uF", "2R2", "4k7", "1M5", "470R", "10mH",
])
def test_parse_format_round_trip(text):
    v = parse_value(text)
    # format then re-parse must recover the same magnitude.
    assert parse_value(format_value(v)) == pytest.approx(v)
