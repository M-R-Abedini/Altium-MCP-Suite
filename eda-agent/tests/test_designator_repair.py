# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for designator repair planning and the lib_fix_designators tool."""

from __future__ import annotations

import asyncio
import json

from mcp.server.fastmcp import FastMCP

import eda_agent.tools.library as lib
from eda_agent.design.footprint_policy import (
    designator_conventions,
    plan_designator_repairs,
)
from eda_agent.tools import register_all_tools

# The house layer is named, not "MechanicalN", and carries a large ordinal,
# the shape a modern Altium build actually reports.
LAYER = "Assembly Designator"
LAYER_ID = 67108882
OTHER_ID = 67108886


def _fp(name, *, dx=0, dy=0, cx=0, cy=0, layer=LAYER, layer_id=LAYER_ID,
        height=20, designator=True):
    texts = []
    if designator:
        texts.append({"text": ".Designator", "kind": "designator",
                      "layer": layer, "layer_id": layer_id,
                      "height": height, "x": dx, "y": dy})
    return {"name": name, "texts": texts, "primitives": [],
            "pad_center": {"x": cx, "y": cy}, "bodies": 1,
            "pads": [{"name": "1", "shape": "round"}]}


def _clean(n=6):
    return [_fp(f"OK{i}", dx=i, dy=0, cx=i, cy=0) for i in range(n)]


# --- convention inference ---------------------------------------------------
def test_conventions_come_from_the_library():
    conv = designator_conventions(_clean())
    assert conv["layer"] == LAYER
    assert conv["layer_id"] == LAYER_ID
    assert conv["height"] == 20
    assert conv["count"] == 6


def test_layer_ordinal_is_taken_from_the_convention_layer_only():
    # A footprint on a DIFFERENT layer must not contribute its ordinal.
    lib_ = _clean() + [_fp("ODD", layer="Mechanical7", layer_id=OTHER_ID)]
    assert designator_conventions(lib_)["layer_id"] == LAYER_ID


def test_no_designators_yields_empty_conventions():
    conv = designator_conventions([_fp("A", designator=False)])
    assert conv["layer_id"] is None and conv["count"] == 0


# --- repair planning --------------------------------------------------------
def test_wrong_layer_is_planned_as_a_layer_move():
    lib_ = _clean() + [_fp("ODD", layer="Mechanical7", layer_id=OTHER_ID)]
    acts = plan_designator_repairs(lib_)["actions"]
    assert len(acts) == 1
    a = acts[0]
    assert a["footprint"] == "ODD" and a["layer_id"] == LAYER_ID
    assert a["reasons"] == ["layer"]
    assert "x" not in a  # already centred: do not move it


def test_off_centre_is_planned_as_a_move_to_the_pad_centre():
    lib_ = _clean() + [_fp("OFF", dx=0, dy=0, cx=500, cy=300)]
    acts = plan_designator_repairs(lib_)["actions"]
    assert len(acts) == 1
    a = acts[0]
    assert a["footprint"] == "OFF" and (a["x"], a["y"]) == (500, 300)
    assert a["reasons"] == ["off-centre"]
    assert "layer_id" not in a  # layer was already right


def test_union_semantics_layer_or_off_centre():
    lib_ = _clean() + [
        _fp("L", layer="Mechanical7", layer_id=OTHER_ID),
        _fp("C", dx=0, dy=0, cx=500, cy=300),
        _fp("BOTH", layer="Mechanical7", layer_id=OTHER_ID, cx=500, cy=300),
    ]
    acts = {a["footprint"]: a for a in plan_designator_repairs(lib_)["actions"]}
    assert set(acts) == {"L", "C", "BOTH"}
    assert acts["BOTH"]["reasons"] == ["layer", "off-centre"]


def test_conformant_footprints_are_never_touched():
    assert plan_designator_repairs(_clean())["actions"] == []


def test_height_is_only_fixed_when_asked():
    lib_ = _clean() + [_fp("TALL", height=60)]
    assert plan_designator_repairs(lib_)["actions"] == []
    acts = plan_designator_repairs(lib_, fix_height=True)["actions"]
    assert acts[0]["footprint"] == "TALL" and acts[0]["height"] == 20


def test_missing_designator_only_created_when_asked():
    lib_ = _clean() + [_fp("NONE", designator=False, cx=100, cy=200)]
    assert plan_designator_repairs(lib_)["actions"] == []
    acts = plan_designator_repairs(lib_, add_missing=True)["actions"]
    assert len(acts) == 1
    a = acts[0]
    assert a["create"] is True and a["layer_id"] == LAYER_ID
    assert (a["x"], a["y"], a["height"]) == (100, 200, 20)


def test_missing_pad_centre_is_skipped_not_guessed():
    fp = _fp("NOPADS", designator=False)
    fp["pad_center"] = None
    plan = plan_designator_repairs(_clean() + [fp], add_missing=True)
    assert plan["actions"] == []
    assert plan["skipped"][0]["footprint"] == "NOPADS"


def test_no_target_layer_ordinal_is_skipped_not_guessed():
    # Designators exist but none carries an ordinal -> cannot address the layer.
    lib_ = [_fp(f"A{i}", layer_id=None) for i in range(3)]
    lib_.append(_fp("ODD", layer="Mechanical7", layer_id=None))
    plan = plan_designator_repairs(lib_)
    assert plan["actions"] == []
    assert plan["skipped"] and plan["skipped"][0]["footprint"] == "ODD"


# --- duplicate designators (the bug that corrupted real libraries) ----------
def test_duplicate_designators_are_never_repaired_only_reported():
    """A footprint with two .Designator strings must not be 'fixed': moving or
    creating on top of it compounds the damage. It is skipped and reported."""
    fp = _fp("DUPE")
    fp["texts"].append({"text": ".Designator", "kind": "designator",
                        "layer": LAYER, "layer_id": LAYER_ID, "height": 20,
                        "x": 900, "y": 900})
    fp["designator_count"] = 2
    plan = plan_designator_repairs(_clean() + [fp], add_missing=True)
    assert [a["footprint"] for a in plan["actions"]] == []
    assert plan["skipped"][0]["footprint"] == "DUPE"
    assert "duplicate" in plan["skipped"][0]["reason"]


def test_duplicate_designator_is_an_error_finding():
    from eda_agent.design.footprint_policy import ERROR, audit_footprint_library
    fp = _fp("DUPE")
    fp["designator_count"] = 2
    report = audit_footprint_library(_clean() + [fp])
    dupes = [f for f in report["findings"]
             if f["dimension"] == "duplicate_designator"]
    assert len(dupes) == 1
    assert dupes[0]["footprint"] == "DUPE"
    assert dupes[0]["severity"] == ERROR
    assert dupes[0]["actual"] == 2


def test_designator_count_falls_back_to_counting_texts():
    from eda_agent.design.footprint_policy import _designator_count
    fp = _fp("A")  # no designator_count key
    assert _designator_count(fp) == 1
    fp["texts"].append(dict(fp["texts"][0]))
    assert _designator_count(fp) == 2


def test_create_is_never_planned_when_a_designator_exists():
    # The exact failure mode: a designator the reader missed would otherwise be
    # duplicated. Presence of the text is the only gate.
    lib_ = _clean() + [_fp("HAS_ONE")]
    acts = plan_designator_repairs(lib_, add_missing=True)["actions"]
    assert all(not a.get("create") for a in acts)


# --- anchor vs bounding-box centre ------------------------------------------
def test_anchor_is_offset_so_the_bbox_centre_lands_on_target():
    """XLocation is a corner. Sending the pad centre as the anchor leaves the
    string hanging half its width off the part."""
    from eda_agent.design.footprint_policy import _anchor_for_center
    # bbox centre sits 300 right and 100 above the anchor
    d = {"anchor_x": 1000, "anchor_y": 2000, "coord_x": 1300, "coord_y": 2100}
    assert _anchor_for_center(d, (0, 0)) == (-300, -100)
    assert _anchor_for_center(d, (5000, 5000)) == (4700, 4900)


def test_anchor_falls_back_to_target_without_anchor_data():
    from eda_agent.design.footprint_policy import _anchor_for_center
    assert _anchor_for_center({"coord_x": 5, "coord_y": 5}, (9, 9)) == (9, 9)


def test_plan_sends_the_anchor_not_the_pad_centre():
    fp = _fp("OFF", dx=0, dy=0, cx=500, cy=300)
    fp["pad_center"] = {"x": 500, "y": 300, "coord_x": 500, "coord_y": 300}
    fp["texts"][0].update({"coord_x": 0, "coord_y": 0,
                           "anchor_x": -60, "anchor_y": -10})
    acts = plan_designator_repairs(_clean() + [fp])["actions"]
    # anchor = -60 + (500 - 0) = 440 ; -10 + (300 - 0) = 290
    assert (acts[0]["x"], acts[0]["y"]) == (440, 290)


# --- exact coordinates ------------------------------------------------------
def test_writer_uses_native_coords_when_available():
    fp = _fp("OFF", dx=0, dy=0, cx=500, cy=300)
    fp["pad_center"] = {"x": 500, "y": 300, "coord_x": 5_000_000,
                        "coord_y": 3_000_000}
    acts = plan_designator_repairs(_clean() + [fp])["actions"]
    assert (acts[0]["x"], acts[0]["y"]) == (5_000_000, 3_000_000)


def test_writer_falls_back_to_mils_without_coords():
    lib_ = _clean() + [_fp("OFF", dx=0, dy=0, cx=500, cy=300)]
    acts = plan_designator_repairs(lib_)["actions"]
    assert (acts[0]["x"], acts[0]["y"]) == (500, 300)


def test_offset_is_frame_independent():
    """The centring decision must not depend on which origin the script used.

    Offsets are differences between the designator and the pad centre, so a
    constant shift of both (a different reference origin) must not change any
    verdict. This is what kept the applied fixes correct while the reported
    coordinates were in a meaningless frame.
    """
    for shift in (0, -50000, 96063):
        lib_ = [_fp(f"OK{i}", dx=shift + i, dy=shift, cx=shift + i, cy=shift)
                for i in range(6)]
        lib_.append(_fp("OFF", dx=shift, dy=shift, cx=shift + 500,
                        cy=shift + 300))
        acts = plan_designator_repairs(lib_)["actions"]
        assert [a["footprint"] for a in acts] == ["OFF"]
        # the target is the pad centre expressed in whatever frame came in
        assert (acts[0]["x"], acts[0]["y"]) == (shift + 500, shift + 300)


# --- the edits file ---------------------------------------------------------
def test_edit_line_leaves_absent_fields_empty():
    from eda_agent.tools.library import _edit_line
    assert _edit_line({"footprint": "R1", "x": 5, "y": -6}) == \
        "R1\t\t5\t-6\t\t0"
    assert _edit_line({"footprint": "C1", "layer_id": 7, "create": True,
                       "height": 20, "x": 0, "y": 0}) == "C1\t7\t0\t0\t20\t1"


def test_edits_file_is_written_and_parsed_back(tmp_path):
    from eda_agent.tools.library import write_designator_edits
    acts = [{"footprint": "A", "x": 1, "y": 2},
            {"footprint": "B", "create": True, "layer_id": 9, "x": 0, "y": 0,
             "height": 20}]
    path, written, rejected = write_designator_edits(tmp_path, acts)
    assert rejected == [] and len(written) == 2
    rows = [ln.split("\t") for ln in
            path.read_text(encoding="utf-8").splitlines()]
    assert rows[0] == ["A", "", "1", "2", "", "0"]
    assert rows[1] == ["B", "9", "0", "0", "20", "1"]


def test_a_name_with_a_tab_is_rejected_not_corrupted(tmp_path):
    # A tab in a name would shift every later field by one column.
    from eda_agent.tools.library import write_designator_edits
    acts = [{"footprint": "GOOD", "x": 1, "y": 2},
            {"footprint": "BA\tD", "x": 3, "y": 4}]
    path, written, rejected = write_designator_edits(tmp_path, acts)
    assert [w["footprint"] for w in written] == ["GOOD"]
    assert rejected[0]["footprint"] == "BA\tD"
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


# --- the tool ---------------------------------------------------------------
class _Cfg:
    def __init__(self, d):
        self.workspace_dir = d


class _FakeBridge:
    """After the reload, geometry reads back clean -- as a real library does
    once its bounding-box cache is rebuilt -- so the loop converges in one pass.
    """

    def __init__(self, workspace, geometry=None, bulk=None, raise_bulk=False):
        self.calls = []
        self.config = _Cfg(workspace)
        self._geometry = geometry
        self._bulk = bulk or {"applied": 2, "created": 0, "failed": 0,
                              "missing": []}
        self._raise_bulk = raise_bulk
        self.reloaded = False

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, dict(params or {})))
        if command == "library.get_library_geometry":
            fps = self._geometry
            if fps is None:
                fps = _clean()
                if not self.reloaded:
                    fps = fps + [
                        _fp("ODD", layer="Mechanical7", layer_id=OTHER_ID),
                        _fp("OFF", dx=0, dy=0, cx=500, cy=300),
                    ]
            return {"library_path": "Demo.PcbLib", "total": len(fps),
                    "offset": 0, "count": len(fps), "footprints": fps}
        if command == "library.set_designators":
            if self._raise_bulk:
                raise RuntimeError("boom")
            return dict(self._bulk)
        if command == "library.reload_library":
            self.reloaded = True
            return {"reloaded": True, "library_path": "Demo.PcbLib"}
        return {}


def _call(args, monkeypatch, tmp_path, bridge=None):
    bridge = bridge or _FakeBridge(tmp_path)
    monkeypatch.setattr(lib, "get_bridge", lambda: bridge)
    m = FastMCP("t")
    register_all_tools(m)
    r = asyncio.run(m.call_tool("lib_fix_designators", args))
    c = r[0] if isinstance(r, tuple) else r
    return json.loads(c[0].text), bridge


def test_dry_run_is_the_default_and_writes_nothing(monkeypatch, tmp_path):
    res, bridge = _call({}, monkeypatch, tmp_path)
    assert res["dry_run"] is True
    assert res["planned"] == 2 and res["applied"] == 0
    commands = [c for c, _ in bridge.calls]
    assert "library.set_designators" not in commands
    assert "application.save_all" not in commands
    assert not (tmp_path / "designator_edits.tsv").exists()


def test_dry_run_reports_the_concrete_plan(monkeypatch, tmp_path):
    res, _ = _call({}, monkeypatch, tmp_path)
    acts = {a["footprint"]: a for a in res["actions"]}
    assert acts["ODD"]["layer_id"] == LAYER_ID
    assert (acts["OFF"]["x"], acts["OFF"]["y"]) == (500, 300)
    assert res["conventions"]["layer"] == LAYER


def test_apply_sends_one_bulk_call_then_saves_once(monkeypatch, tmp_path):
    res, bridge = _call({"dry_run": False}, monkeypatch, tmp_path)
    assert res["applied"] == 2 and res["failed"] == [] and res["saved"] is True
    bulk = [p for c, p in bridge.calls if c == "library.set_designators"]
    assert len(bulk) == 1  # NOT one call per footprint
    assert bulk[0]["library_path"] == "Demo.PcbLib"
    assert bulk[0]["edits_path"].endswith("designator_edits.tsv")
    assert [c for c, _ in bridge.calls].count("application.save_all") == 1
    # the per-footprint command must not be used any more
    assert "library.set_designator" not in [c for c, _ in bridge.calls]


def test_edits_file_contains_exactly_the_planned_actions(monkeypatch, tmp_path):
    _call({"dry_run": False}, monkeypatch, tmp_path)
    rows = [ln.split("\t") for ln in
            (tmp_path / "designator_edits.tsv").read_text(
                encoding="utf-8").splitlines()]
    by_name = {r[0]: r for r in rows}
    assert set(by_name) == {"ODD", "OFF"}
    assert by_name["ODD"][1] == str(LAYER_ID)   # layer move
    assert by_name["ODD"][2] == ""              # no reposition
    assert by_name["OFF"][2:4] == ["500", "300"]  # reposition
    assert by_name["OFF"][1] == ""              # no layer move


def test_missing_footprints_reported_as_failures(monkeypatch, tmp_path):
    bridge = _FakeBridge(tmp_path, bulk={"applied": 1, "created": 0,
                                         "failed": 0, "missing": ["ODD"]})
    res, _ = _call({"dry_run": False}, monkeypatch, tmp_path, bridge=bridge)
    assert res["applied"] == 1
    assert res["failed"][0] == {"footprint": "ODD",
                                "error": "not found in library"}


def test_bulk_failure_does_not_save(monkeypatch, tmp_path):
    bridge = _FakeBridge(tmp_path, raise_bulk=True)
    res, _ = _call({"dry_run": False}, monkeypatch, tmp_path, bridge=bridge)
    assert res["applied"] == 0 and res["saved"] is False
    assert res["failed"][0]["error"] == "boom"


def test_post_write_verification_catches_duplicates(monkeypatch, tmp_path):
    """A create that lands on the wrong component is invisible to the writer.
    The tool must re-read the library and shout, not report success."""
    class _DupeBridge(_FakeBridge):
        def __init__(self, ws):
            super().__init__(ws)
            self.reads = 0

        async def send_command_async(self, command, params=None, timeout=None):
            self.calls.append((command, dict(params or {})))
            if command == "library.get_library_geometry":
                self.reads += 1
                fps = _clean() + [
                    _fp("ODD", layer="Mechanical7", layer_id=OTHER_ID),
                    _fp("OFF", dx=0, dy=0, cx=500, cy=300),
                ]
                if self.reads > 1:  # the read-back, after writing
                    fps[0]["designator_count"] = 18
                return {"library_path": "Demo.PcbLib", "total": len(fps),
                        "offset": 0, "count": len(fps), "footprints": fps}
            if command == "library.set_designators":
                return {"applied": 2, "created": 1, "failed": 0, "refused": 0,
                        "missing": []}
            return {}

    res, _ = _call({"dry_run": False}, monkeypatch, tmp_path,
                   bridge=_DupeBridge(tmp_path))
    assert res["verified_no_duplicates"] is False
    assert res["duplicates_created"] == ["OK0"]
    assert any("RESTORE FROM BACKUP" in f["error"] for f in res["failed"])


def test_preexisting_duplicates_do_not_trip_the_guard(monkeypatch, tmp_path):
    """A library that ALREADY has duplicates must not make a clean write look
    like a corrupting one. The guard compares against the pre-write baseline."""
    class _PreDupeBridge(_FakeBridge):
        async def send_command_async(self, command, params=None, timeout=None):
            if command == "library.get_library_geometry":
                self.calls.append((command, dict(params or {})))
                fps = _clean() + [_fp("ALREADY_DUPED")]
                if not self.reloaded:
                    fps.insert(-1, _fp("OFF", dx=0, dy=0, cx=500, cy=300))
                fps[-1]["designator_count"] = 2  # before AND after
                return {"library_path": "Demo.PcbLib", "total": len(fps),
                        "offset": 0, "count": len(fps), "footprints": fps}
            if command == "library.set_designators":
                self.calls.append((command, dict(params or {})))
                return {"applied": 1, "created": 0, "failed": 0, "refused": 0,
                        "missing": []}
            return await super().send_command_async(command, params, timeout)

    res, _ = _call({"dry_run": False}, monkeypatch, tmp_path,
                   bridge=_PreDupeBridge(tmp_path))
    assert res["verified_no_duplicates"] is True
    assert res["preexisting_duplicates"] == ["ALREADY_DUPED"]
    assert res["failed"] == []
    # and it was never repaired
    assert "ALREADY_DUPED" not in [a["footprint"] for a in res["actions"]]


def test_post_write_verification_catches_strays(monkeypatch, tmp_path):
    """A designator left at the board origin sits ~50000 mils from its pads.
    The read-back must catch that, not report success."""
    class _StrayBridge(_FakeBridge):
        def __init__(self, ws):
            super().__init__(ws)
            self.reads = 0

        async def send_command_async(self, command, params=None, timeout=None):
            self.calls.append((command, dict(params or {})))
            if command == "library.get_library_geometry":
                self.reads += 1
                fps = _clean() + [
                    _fp("ODD", layer="Mechanical7", layer_id=OTHER_ID),
                    _fp("OFF", dx=0, dy=0, cx=500, cy=300),
                ]
                if self.reads > 1:  # after writing: OFF landed at the origin
                    fps[-1]["texts"][0]["x"] = -50000
                    fps[-1]["texts"][0]["y"] = -50000
                return {"library_path": "Demo.PcbLib", "total": len(fps),
                        "offset": 0, "count": len(fps), "footprints": fps}
            if command == "library.set_designators":
                return {"applied": 2, "created": 0, "failed": 0, "refused": 0,
                        "missing": []}
            return {}

    res, _ = _call({"dry_run": False}, monkeypatch, tmp_path,
                   bridge=_StrayBridge(tmp_path))
    assert res["verified_no_stray_designators"] is False
    assert res["strays_created"] == ["OFF"]
    assert any("RESTORE FROM BACKUP" in f["error"] for f in res["failed"])


def test_post_write_verification_passes_on_a_clean_write(monkeypatch, tmp_path):
    res, _ = _call({"dry_run": False}, monkeypatch, tmp_path)
    assert res["verified_no_duplicates"] is True
    assert res["verified_no_stray_designators"] is True
    assert "duplicates_created" not in res
    assert "strays_created" not in res


# --- convergence loop -------------------------------------------------------
class _ConvergingBridge(_FakeBridge):
    """Serves geometry that only becomes correct after a reload, mimicking the
    bounding-box cache: the first read shows an off-centre designator, and each
    reload reveals the next state."""

    def __init__(self, ws, passes_needed=2, stuck=False):
        super().__init__(ws)
        self.reloads = 0
        self.writes = 0
        self.saves = 0
        self.passes_needed = passes_needed
        self.stuck = stuck

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, dict(params or {})))
        if command == "library.get_library_geometry":
            fps = _clean()
            if self.reloads < self.passes_needed:
                # Each reload moves OFF closer, so successive plans DIFFER --
                # as a real library does when a resize changes the bounding box.
                # (With `stuck=True` the geometry never changes, which is what
                # a designator that refuses to move looks like.)
                shift = 0 if self.stuck else self.reloads * 100
                off = _fp("OFF", dx=0, dy=0, cx=500, cy=300)
                # anchor moves each pass -> the computed anchor differs -> the
                # plan differs, which is what "progress" looks like.
                off["texts"][0].update({"coord_x": 0, "coord_y": 0,
                                        "anchor_x": shift, "anchor_y": 0})
                fps.append(off)
            return {"library_path": "Demo.PcbLib", "total": len(fps),
                    "offset": 0, "count": len(fps), "footprints": fps}
        if command == "library.set_designators":
            self.writes += 1
            return {"applied": 1, "created": 0, "failed": 0, "refused": 0,
                    "missing": []}
        if command == "application.save_all":
            self.saves += 1
            return {}
        if command == "library.reload_library":
            self.reloads += 1
            return {"reloaded": True, "library_path": "Demo.PcbLib"}
        return {}


def test_apply_loops_until_converged(monkeypatch, tmp_path):
    bridge = _ConvergingBridge(tmp_path, passes_needed=2)
    res, bridge = _call({"dry_run": False}, monkeypatch, tmp_path, bridge=bridge)
    assert res["converged"] is True
    assert res["passes"] == 2
    assert bridge.writes == 2 and bridge.reloads == 2


def test_reload_happens_between_write_and_reread(monkeypatch, tmp_path):
    """Reading geometry after a write WITHOUT reloading returns a stale
    bounding box, which is how designators ended up off their footprints."""
    bridge = _ConvergingBridge(tmp_path, passes_needed=1)
    _call({"dry_run": False}, monkeypatch, tmp_path, bridge=bridge)
    seq = [c for c, _ in bridge.calls]
    w = seq.index("library.set_designators")
    r = seq.index("library.reload_library")
    nxt = seq.index("library.get_library_geometry", w)
    assert w < r < nxt, seq


def test_reload_failure_stops_after_one_pass(monkeypatch, tmp_path):
    class _NoReload(_ConvergingBridge):
        async def send_command_async(self, command, params=None, timeout=None):
            if command == "library.reload_library":
                self.calls.append((command, dict(params or {})))
                return {"reloaded": False}
            return await super().send_command_async(command, params, timeout)

    bridge = _NoReload(tmp_path, passes_needed=3)
    res, bridge = _call({"dry_run": False}, monkeypatch, tmp_path, bridge=bridge)
    assert bridge.writes == 1              # did NOT keep writing blind
    assert res["converged"] is False
    assert any("reload failed" in f["error"] for f in res["failed"])


def test_loop_stops_when_a_pass_changes_nothing(monkeypatch, tmp_path):
    """Some designators refuse to move. Re-planning the identical edit forever
    inflates `applied` with writes that never landed."""
    bridge = _ConvergingBridge(tmp_path, passes_needed=99, stuck=True)
    res, bridge = _call({"dry_run": False}, monkeypatch, tmp_path, bridge=bridge)
    assert res["converged"] is False
    assert res["passes"] == 1          # bailed the moment a pass showed no progress
    assert bridge.writes == 1
    assert res["unrepairable"] == ["OFF"]
    assert any("would not move" in f["error"] for f in res["failed"])


def test_immovable_designators_are_reported_not_counted(monkeypatch, tmp_path):
    bridge = _FakeBridge(tmp_path, bulk={"applied": 0, "created": 0, "failed": 1,
                                         "refused": 0, "missing": [],
                                         "immovable": ["OFF"]})
    res, _ = _call({"dry_run": False}, monkeypatch, tmp_path, bridge=bridge)
    assert res["applied"] == 0
    assert any(f["footprint"] == "OFF" and "did not move" in f["error"]
               for f in res["failed"])


def test_pass_count_is_bounded(monkeypatch, tmp_path):
    # Converges only after more passes than the cap allows, and each pass makes
    # progress, so only the cap can stop it.
    bridge = _ConvergingBridge(tmp_path, passes_needed=99)
    res, bridge = _call({"dry_run": False}, monkeypatch, tmp_path, bridge=bridge)
    assert res["converged"] is False
    assert res["passes"] <= 6 and bridge.writes <= 6


def test_dry_run_never_reloads(monkeypatch, tmp_path):
    bridge = _ConvergingBridge(tmp_path)
    _call({}, monkeypatch, tmp_path, bridge=bridge)
    assert bridge.reloads == 0 and bridge.writes == 0


# --- TrueType -> stroke conversion ------------------------------------------
def test_convert_to_stroke_saves_and_reloads_when_something_changed(
        monkeypatch, tmp_path):
    calls = []

    class _Bridge:
        config = _Cfg(tmp_path)

        async def send_command_async(self, command, params=None, timeout=None):
            calls.append(command)
            if command == "library.convert_designators_to_stroke":
                return {"library_path": "Demo.PcbLib", "designators": 44,
                        "converted": 3, "names": ["A", "B", "C"]}
            return {}

    monkeypatch.setattr(lib, "get_bridge", lambda: _Bridge())
    m = FastMCP("t")
    register_all_tools(m)
    r = asyncio.run(m.call_tool("lib_convert_designators_to_stroke", {}))
    c = r[0] if isinstance(r, tuple) else r
    res = json.loads(c[0].text)
    assert res["converted"] == 3 and res["saved"] is True
    # a change must be flushed AND the caches rebuilt
    assert "application.save_all" in calls
    assert "library.reload_library" in calls


def test_convert_to_stroke_no_save_when_nothing_to_convert(monkeypatch, tmp_path):
    calls = []

    class _Bridge:
        config = _Cfg(tmp_path)

        async def send_command_async(self, command, params=None, timeout=None):
            calls.append(command)
            if command == "library.convert_designators_to_stroke":
                return {"library_path": "Demo.PcbLib", "designators": 44,
                        "converted": 0, "names": []}
            return {}

    monkeypatch.setattr(lib, "get_bridge", lambda: _Bridge())
    m = FastMCP("t")
    register_all_tools(m)
    r = asyncio.run(m.call_tool("lib_convert_designators_to_stroke", {}))
    c = r[0] if isinstance(r, tuple) else r
    res = json.loads(c[0].text)
    assert res["converted"] == 0 and res["saved"] is False
    assert "application.save_all" not in calls


def test_refused_creates_are_surfaced_as_failures(monkeypatch, tmp_path):
    # The script's duplicate guard firing must never look like success.
    bridge = _FakeBridge(tmp_path, bulk={"applied": 1, "created": 0,
                                         "failed": 0, "refused": 2,
                                         "missing": []})
    res, _ = _call({"dry_run": False}, monkeypatch, tmp_path, bridge=bridge)
    assert any("2 creates refused" in f["error"] for f in res["failed"])


def test_script_side_failures_are_surfaced(monkeypatch, tmp_path):
    bridge = _FakeBridge(tmp_path, bulk={"applied": 1, "created": 1,
                                         "failed": 1, "missing": []})
    res, _ = _call({"dry_run": False}, monkeypatch, tmp_path, bridge=bridge)
    assert res["created"] == 1
    assert any("1 edits failed" in f["error"] for f in res["failed"])


def test_nothing_to_do_means_no_write_and_no_save(monkeypatch, tmp_path):
    bridge = _FakeBridge(tmp_path, geometry=_clean())
    res, bridge = _call({"dry_run": False}, monkeypatch, tmp_path, bridge=bridge)
    assert res["planned"] == 0 and res["applied"] == 0
    commands = [c for c, _ in bridge.calls]
    assert "library.set_designators" not in commands
    assert "application.save_all" not in commands
