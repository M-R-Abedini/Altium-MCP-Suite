"""Batch scalability, failure honesty, cache identity and topology acceptance."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from eda_agent.design.canvas import BusEntry, BusSegment, SchematicCanvas
from eda_agent.design.emitter import EmitResult, _emit_buses, _emit_bus_entries, _emit_parameter_stamps, _ensure_sheet_loaded, emit_canvas
from eda_agent.design.orchestrator import _compact_result, _empty_result_dict, _verify_emitted_plan
from eda_agent.design.plan import DesignPlan
from eda_agent.design.symbols import SymbolBBox, SymbolCache, SymbolExtractor, SymbolModel


class Bridge:
    def __init__(self, reply): self.reply, self.calls = reply, []
    def send_command(self, command, params=None, **kwargs):
        self.calls.append((command, params or {}))
        return self.reply(command, params or {}) if callable(self.reply) else self.reply


def test_bus_payload_is_bounded_by_kinds_not_item_count():
    bridge = Bridge(lambda cmd, p: {"placed": len(next(iter(p.values())).split("~~")), "failed": 0,
                                    "total": len(next(iter(p.values())).split("~~"))})
    result = EmitResult()
    _emit_buses([BusSegment(1, 2, 3, i) for i in range(8)], bridge, result, "main")
    _emit_bus_entries([BusEntry(3, i, 53, i + 50) for i in range(32)], bridge, result, "main")
    assert len(bridge.calls) == 2 and result.ok
    assert (result.buses_emitted, result.bus_entries_emitted) == (8, 32)
    assert bridge.calls[1][1]["entries"].endswith("x1=3;y1=31;x2=53;y2=81")


@pytest.mark.parametrize("reply", [{}, {"placed": 2, "failed": 0, "total": 1},
                                  {"placed": 0, "failed": 1, "total": 1}])
def test_failed_or_old_bus_handler_cannot_report_success(reply):
    result = EmitResult()
    _emit_buses([BusSegment(0, 0, 0, 100)], Bridge(reply), result, "main")
    assert not result.ok and result.notes


def test_stamp_visibility_is_one_pass_with_compatible_old_handler_fallback():
    parts = [SimpleNamespace(refdes="R1")]
    for acknowledgement, expected_calls in [(True, 1), (False, 4)]:
        b = Bridge({"success": True, "hidden_parameters_applied": acknowledgement})
        result = EmitResult()
        _emit_parameter_stamps(parts, {"R1": {"Manufacturer": "A"}}, Path("a.SchDoc"), b, result)
        assert result.ok and len(b.calls) == expected_calls
    b = Bridge({"success": False, "failed": 1})
    r = EmitResult()
    _emit_parameter_stamps(parts, {"R1": {"Value": "1k"}}, Path("a.SchDoc"), b, r)
    assert not r.ok


def test_emission_saves_only_explicit_target_and_records_commands(tmp_path):
    target = str(tmp_path / "scratch.PrjPcb")
    bridge = Bridge({"success": True})
    result = emit_canvas(SchematicCanvas(), target, bridge)
    assert result.ok
    assert bridge.calls[-1] == ("project.save", {"project_path": target, "include_libraries": "false"})
    assert all(c != "application.save_all" for c, _ in bridge.calls)
    assert [t["command"] for t in result.timings] == [c for c, _ in bridge.calls]
    assert all(set(t) == {"command", "ok", "elapsed_ms"} for t in result.timings)


def test_sheet_creation_never_attaches_to_focused_project(tmp_path):
    b = Bridge({"success": True})
    result = EmitResult()
    assert _ensure_sheet_loaded(tmp_path/'x.PrjPcb', 'main', b, result)
    assert b.calls[0][1]['add_to_project'] == 'false'
    assert b.calls[1] == ('project.add_document', {
        'document_path':str(tmp_path/'x__main.SchDoc'), 'project_path':str(tmp_path/'x.PrjPcb')})
    b = Bridge(lambda c,p: {'success':c != 'project.add_document'})
    result = EmitResult()
    assert _ensure_sheet_loaded(tmp_path/'y.PrjPcb','main',b,result) is None
    assert not result.ok


def test_summary_is_published_after_placements_and_retains_full_artifact(tmp_path):
    out = _empty_result_dict(str(tmp_path / "x.PrjPcb"))
    out["canvas"] = {"instances": [{"refdes": "R1"}], "large": "x" * 140000}
    out["placed"] = [{"refdes": "R1", "x_mils": 123}]
    _compact_result(out, {"spec": "test"})
    assert out["result_detail"] == "summary" and out["canvas"] is None
    saved = json.loads(Path(out["details_path"]).read_text(encoding="utf8"))
    assert saved["result"]["canvas"]["instances"][0]["refdes"] == "R1"
    assert out["placed"] == saved["result"]["placed"]


def _plan():
    return DesignPlan.model_validate({"spec": "x", "summary": "x", "sheets": [{"name": "main"}],
        "parts": [{"refdes": "R1", "lib_ref": "R"}, {"refdes": "R2", "lib_ref": "R"}],
        "nets": [{"name": "A", "pins": [{"refdes": "R1", "pin": "1"}, {"refdes": "R2", "pin": "1"}]},
                 {"name": "B", "pins": [{"refdes": "R1", "pin": "2"}, {"refdes": "R2", "pin": "2"}]}]})


def _netlist(short=False):
    return {"pins": [{"component": r, "pin": p, "net": "X" if p == "1" or short else "Y"}
                     for r in ("R1", "R2") for p in ("1", "2")]}


@pytest.mark.parametrize("reply", [{}, {"pins": [], "truncated": True},
                                  {"pins": [], "count": 1}, {"pins": [None]}])
def test_unavailable_verification_is_incomplete_not_passed(reply):
    out = _empty_result_dict("x")
    _verify_emitted_plan(_plan(), "x", Bridge(reply), out)
    assert not out["ok"] and out["net_verification"]["status"] == "incomplete"


def test_native_topology_catches_short_and_allows_unplanned_nc():
    for short in (False, True):
        reply = _netlist(short)
        reply["pins"].append({"component": "U9", "pin": "NC", "net": "?"})
        out = _empty_result_dict("x")
        _verify_emitted_plan(_plan(), "x", Bridge(reply), out)
        assert out["net_verification"]["status"] == ("failed" if short else "passed")
        assert out["ok"] is not short


def test_topology_forces_fresh_target_compile_and_rejects_planned_unassigned():
    b = Bridge(_netlist())
    out = _empty_result_dict('target')
    _verify_emitted_plan(_plan(),'target',b,out)
    assert b.calls[0][1]['project_path']=='target'
    assert b.calls[0][1]['force_recompile']=='true'
    reply = _netlist()
    reply['pins'][0]['net']='?'
    _verify_emitted_plan(_plan(),'target',Bridge(reply),out)
    assert not out['ok'] and out['net_verification']['status']=='incomplete'


def _symbol(path, name="R"):
    return SymbolModel(lib_path=str(path), lib_ref=name, pins=(), body_bbox=SymbolBBox(-1,-1,1,1))


def test_cache_same_named_libraries_and_preserved_mtime_are_separate(tmp_path):
    a, b = tmp_path / "a" / "lib.SchLib", tmp_path / "b" / "lib.SchLib"
    a.parent.mkdir(); b.parent.mkdir(); a.write_text("aa"); b.write_text("bb")
    cache = SymbolCache(tmp_path / "cache")
    cache.put(_symbol(a))
    assert cache.get(str(a), "R") and cache.get(str(b), "R") is None
    stamp = a.stat().st_mtime_ns
    a.write_text("cc"); os.utime(a, ns=(stamp,stamp))
    assert cache.get(str(a), "R") is None


def test_refreshing_one_symbol_does_not_revive_stale_peer(tmp_path):
    p = tmp_path / "lib.SchLib"; p.write_text("v1")
    cache = SymbolCache(tmp_path / "cache")
    cache.put(_symbol(p, "R")); cache.put(_symbol(p, "C"))
    p.write_text("v2")
    cache.put(_symbol(p, "C"))
    assert cache.get(str(p), "C") and cache.get(str(p), "R") is None


def test_extraction_hashes_library_at_batch_boundaries_and_rejects_midbatch_edit(tmp_path, monkeypatch):
    path = tmp_path / "lib.SchLib"; path.write_text("v1")
    cache = SymbolCache(tmp_path / "cache")
    reads = []
    original = cache._read_identity
    monkeypatch.setattr(cache, "_read_identity", lambda p: (reads.append(p), original(p))[1])
    bridge = Bridge(lambda c,p: {"name": p["component_name"], "pins": []})
    extractor = SymbolExtractor(bridge, cache)
    result = extractor.extract_many([(str(path), str(i)) for i in range(20)])
    assert len(result) == 20 and len(reads) == 2
    def changed(c,p):
        path.write_text("v2")
        return {"name": p["component_name"], "pins": []}
    extractor.bridge = Bridge(changed)
    assert extractor.extract_many([(str(path), "new")]) == {}
