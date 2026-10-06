# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Guard: argument coercion, and geometry the editor will actually accept.

Every case here comes from driving a live EasyEDA editor, and every one
of them was a defect the previous suite passed on.

Two families:

* A JSON STRING WHERE A LIST WAS DECLARED. MCP clients send these
  routinely. ``list("[\\"0402\\"]")`` is eight single characters, so
  ``lib_Footprint.search`` received eight arguments, hit an overload
  that never answers, and the connection was dead for the whole
  timeout. The symptom was a hung editor, not a bad argument, which is
  why it survived so long.

* GEOMETRY THE EDITOR SILENTLY REJECTS. A schematic wire is a list of
  flat ``[x1, y1, x2, y2]`` SEGMENTS, measured off a live sheet:
  ``[[400, -200, 300, -200], [300, -200, 200, -200]]``. Three call sites
  sent ``[[x, y], [x, y]]``, which is a list of malformed segments;
  ``create`` returned null and drew nothing. So the single wire, the
  bulk wires and the bus had none of them ever drawn anything.
"""

from __future__ import annotations

import pytest

from eda_agent.tools.easyeda import _as_list, _points


def _refused(result) -> bool:
    return isinstance(result, dict) and result.get("ok") is False


# --------------------------------------------------------------------
# _as_list
# --------------------------------------------------------------------

def test_none_is_an_empty_list():
    assert _as_list(None, "args") == []


def test_a_list_passes_through_unchanged():
    value = [1, "two", [3]]
    assert _as_list(value, "args") == value


def test_a_json_string_is_parsed_not_iterated():
    """The defect: iterating the string handed the editor its characters."""
    assert _as_list('["0402"]', "args") == ["0402"]
    assert _as_list("[[0,0],[10,0]]", "args") == [[0, 0], [10, 0]]


def test_an_empty_string_is_an_empty_list():
    assert _as_list("   ", "args") == []


def test_a_string_that_is_not_json_is_refused():
    out = _as_list("not json", "args")
    assert _refused(out)
    assert "not JSON" in out["reason"]


def test_json_that_is_not_a_list_is_refused():
    out = _as_list('{"a": 1}', "args")
    assert _refused(out)
    assert "not a list" in out["reason"]


def test_a_non_list_non_string_is_refused():
    out = _as_list(42, "args")
    assert _refused(out)
    assert "must be a list" in out["reason"]


# --------------------------------------------------------------------
# _points
# --------------------------------------------------------------------

def test_a_polyline_of_pairs_is_accepted():
    assert _points([[0, 0], [10, 0]], "points", 2) == [[0.0, 0.0], [10.0, 0.0]]


def test_a_json_string_of_points_is_accepted():
    assert _points("[[0,0],[10,0]]", "points", 2) == [[0.0, 0.0], [10.0, 0.0]]


def test_too_few_points_is_refused():
    assert _refused(_points([[0, 0]], "points", 2))
    assert _refused(_points([], "points", 2))


def test_a_pour_needs_three_points_to_enclose_anything():
    assert _refused(_points([[0, 0], [10, 0]], "points", 3))
    assert not _refused(_points([[0, 0], [10, 0], [10, 10]], "points", 3))


def test_zero_length_geometry_is_refused():
    """Every point identical draws something invisible that connects nothing."""
    out = _points([[5, 5], [5, 5]], "points", 2)
    assert _refused(out)
    assert "zero length" in out["reason"]


def test_a_malformed_pair_names_its_index():
    out = _points([[0, 0], [1]], "points", 2)
    assert _refused(out)
    assert "points[1]" in out["reason"]


def test_a_non_numeric_point_is_refused_rather_than_raising():
    out = _points([[0, 0], ["a", "b"]], "points", 2)
    assert _refused(out)
    assert "not numeric" in out["reason"]


def test_a_string_entry_is_not_treated_as_a_sequence():
    """"ab"[0] and "ab"[1] are characters, which would pass a naive check."""
    out = _points(["ab", "cd"], "points", 2)
    assert _refused(out)
    assert "must be an [x, y] pair" in out["reason"]


# --------------------------------------------------------------------
# Segments: the form the editor reports and accepts.
# --------------------------------------------------------------------

def test_whole_segments_survive_intact():
    """Keeping only the first two numbers would halve every segment.

    That mattered: geometry read back from the editor is already in
    segment form, and it has to be able to go straight in again.
    """
    out = _points([[400, -200, 300, -200]], "points", 2, allow_segments=True)
    assert out == [[400.0, -200.0, 300.0, -200.0]]


def test_a_single_segment_is_a_valid_wire():
    """The minimum counts POINTS, and one segment already carries two.

    Applying the pair-form minimum to segments rejects the commonest
    wire there is.
    """
    assert not _refused(
        _points([[0, 0, 10, 0]], "points", 2, allow_segments=True))


def test_segments_are_rejected_when_not_allowed():
    assert _refused(_points([[0, 0, 10, 0]], "points", 3))


def test_mixing_pairs_and_segments_is_refused():
    out = _points([[0, 0], [0, 0, 1, 1]], "points", 2, allow_segments=True)
    assert _refused(out)
    assert "mixes" in out["reason"]


def test_a_zero_length_segment_is_refused():
    out = _points([[5, 5, 5, 5]], "points", 2, allow_segments=True)
    assert _refused(out)
    assert "zero length" in out["reason"]


def test_a_three_number_entry_is_neither_and_is_refused():
    out = _points([[0, 0, 1]], "points", 2, allow_segments=True)
    assert _refused(out)
    assert "3 value(s)" in out["reason"]


@pytest.mark.parametrize("geometry", [
    # The form a live sheet reports, with illustrative coordinates: a
    # list of flat segments, y negative above the origin.
    [[400, -200, 300, -200], [300, -200, 200, -200]],
    [[1000, -900, 900, -900]],
])
def test_geometry_read_from_a_live_sheet_is_accepted_unchanged(geometry):
    out = _points(geometry, "points", 2, allow_segments=True)
    assert out == [[float(n) for n in seg] for seg in geometry]


# --------------------------------------------------------------------
# Polylines: an entry is a whole run, not necessarily one segment.
#
# The official WIRE format documents `dots` as a list of polylines, each
# a continuous flat run X1 Y1 X2 Y2 X3 Y3 ..., and its own example
# carries a six-number entry. Accepting only 2 and 4 rejected any wire
# with a bend in one entry, including geometry read straight back out of
# a live sheet. The earlier four-number measurement was the two-point
# case, not the rule.
# --------------------------------------------------------------------

@pytest.mark.parametrize("geometry", [
    # Verbatim from the official format documentation's own example.
    [[310, 550, 400, 550, 400, 460]],
    [[310, 550, 400, 550, 400, 460], [480, 460, 400, 460]],
    [[0, 0, 10, 0, 10, 10, 20, 10]],
])
def test_a_polyline_longer_than_one_segment_is_accepted(geometry):
    out = _points(geometry, "points", 2, allow_segments=True)
    assert out == [[float(n) for n in run] for run in geometry]


def test_runs_of_different_lengths_are_not_a_mix():
    """One bent wire and one straight one is ordinary, not an error.

    Comparing raw entry widths would reject it, which is why the check
    is pair-form versus run-form rather than equal widths.
    """
    out = _points([[0, 0, 1, 1, 2, 2], [3, 3, 4, 4]], "points", 2,
                  allow_segments=True)
    assert not _refused(out)


@pytest.mark.parametrize("geometry", [[[0, 0, 1]], [[0, 0, 1, 1, 2]]])
def test_an_odd_coordinate_count_is_still_refused(geometry):
    """A dangling coordinate means the caller has lost the shape."""
    out = _points(geometry, "points", 2, allow_segments=True)
    assert _refused(out)


def test_a_collapsed_three_point_run_is_refused():
    """Checked across the whole run, not just its two ends.

    A run can start and finish elsewhere while collapsing in the
    middle, so testing only the endpoints would pass a wire that
    connects nothing.
    """
    out = _points([[5, 5, 5, 5, 5, 5]], "points", 2, allow_segments=True)
    assert _refused(out)
    assert "collapses" in out["reason"]


def test_a_long_run_is_still_refused_when_segments_are_not_allowed():
    assert _refused(_points([[0, 0, 10, 0, 20, 0]], "points", 2))
