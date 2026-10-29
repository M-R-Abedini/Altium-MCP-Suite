"""Local dataset privacy and provenance contracts; no native/network access."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import asyncio

import pytest

from suite_telemetry import TelemetryCollector, graph_digest, structural_delta


def raw_graph():
    return {"units": "mils", "project_path": "C:/SecretCompany/Camera.pcbdoc",
            "author": "PERSON_SECRET", "title": "COMPANY_SECRET",
            "components": [
                {"id": "U1_PRIVATE", "kind": "ic", "mpn": "STM32F103C8T6", "x": 10, "y": 20,
                 "footprint": "SecretLibrary/path:PRIVATE", "notes": "PRIVATE_NOTES",
                 "pins": [{"pin": "1", "net": "NET_PRIVATE", "function": "PIN_PRIVATE"}]},
                {"id": "C1_PRIVATE", "kind": "capacitor", "value": "100nF", "x": 20, "y": 20,
                 "pins": [{"pin": "1", "net": "NET_PRIVATE"}]}],
            "pads": [{"x": 1, "y": 2, "width": 3, "height": 3, "net": "NET_PRIVATE"}],
            "boundary": [[0, 0], [100, 0], [100, 100], [0, 100]],
            "constraints": {"clearance": 5, "company": "COMPANY_SECRET"}}


def collector(tmp_path, config=None):
    (tmp_path / "config.json").write_text(json.dumps(config or {"telemetry_opt_in": True}), encoding="utf-8")
    return TelemetryCollector(tmp_path)


def evidence(graph, kind="erc"):
    return {"kind": kind, "source": "bridge", "fresh": True, "complete": True, "errors": 0,
            "snapshot_sha256": graph_digest(graph), "observed_at_utc": datetime.now(timezone.utc).isoformat()}


def test_default_disabled_has_no_files(tmp_path):
    instance = TelemetryCollector(tmp_path)
    assert not instance.enabled()
    assert instance.capture_ai("secret", raw_graph()) == {"collected": False, "reason": "disabled"}
    assert instance.record_human("secret", raw_graph())["reason"] == "disabled"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("config", [{"telemetry_opt_in": "true"}, {"telemetry_opt_in": 1},
                                   {"telemetry_opt_in": True, "edition": "Enterprise"}])
def test_explicit_boolean_optin_and_enterprise_off(tmp_path, config):
    instance = collector(tmp_path, config)
    assert not instance.enabled()
    assert not instance.capture_ai("secret", raw_graph())["collected"]
    assert not (tmp_path / "telemetry").exists()


def test_projection_strips_paths_free_text_and_salts_identifiers(tmp_path):
    instance = collector(tmp_path)
    graph = instance.anonymize(raw_graph())
    rendered = json.dumps(graph)
    assert "STM32F103C8T6" in rendered and "100nF" in rendered
    for secret in ("SecretCompany", "Camera", "PERSON_SECRET", "COMPANY_SECRET", "U1_PRIVATE",
                   "SecretLibrary", "PRIVATE_NOTES", "NET_PRIVATE", "PIN_PRIVATE", "C1_PRIVATE"):
        assert secret not in rendered
    assert graph == instance.anonymize(raw_graph())
    different = tmp_path / "another"
    different.mkdir()
    assert graph != collector(different).anonymize(raw_graph())
    instance.capture_ai("C:/SecretCompany/Camera.pcbdoc", raw_graph())
    for path in (tmp_path / "telemetry").rglob("*.json"):
        assert "SecretCompany" not in path.read_text()


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "100"])
def test_invalid_geometry_never_serialized(tmp_path, bad):
    instance = collector(tmp_path)
    raw = raw_graph()
    raw["components"][0]["x"] = bad
    with pytest.raises(ValueError, match="finite numbers"):
        instance.capture_ai("x", raw)
    assert not list((tmp_path / "telemetry").rglob("*.json"))


def test_order_independent_component_graph(tmp_path):
    instance = collector(tmp_path)
    a = raw_graph()
    b = deepcopy(a)
    b["components"].reverse()
    assert instance.anonymize(a) == instance.anonymize(b)


def test_delta_tracks_moves_added_parts_and_pin_corrections(tmp_path):
    instance = collector(tmp_path)
    before = raw_graph()
    after = deepcopy(before)
    after["components"][0]["x"] = 42
    after["components"][0]["pins"][0]["net"] = "corrected"
    after["components"].append({"id": "C2", "kind": "capacitor", "value": "1uF"})
    delta = structural_delta(instance.anonymize(before), instance.anonymize(after))
    assert len(delta["moved_components"]) == len(delta["pin_binding_changes"]) == len(delta["added_components"]) == 1


def test_save_does_not_imply_human_preference(tmp_path):
    instance = collector(tmp_path)
    instance.capture_ai("project", raw_graph())
    assert instance.record_human("project", raw_graph())["reason"] == "human_attribution_required"
    assert not (tmp_path / "telemetry/trajectories").exists()
    graph = instance.anonymize(raw_graph())
    result = instance.record_human("project", raw_graph(), human_confirmed=True, evidence=evidence(graph))
    assert result["collected"]
    records = list((tmp_path / "telemetry/trajectories").glob("*.jsonl"))
    stored = json.loads(records[0].read_text())
    assert stored["preference"] is None and stored["label"] == "human_correction_candidate"
    assert not instance.record_human("project", raw_graph(), human_confirmed=True,
                                     evidence=evidence(graph))["collected"]


@pytest.mark.parametrize("change", [
    {"fresh": False}, {"complete": False}, {"errors": 1}, {"errors": False},
    {"source": "model"}, {"snapshot_sha256": "a" * 64}, {"kind": "drc"},
    {"observed_at_utc": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()},
    {"observed_at_utc": "2026-10-28T12:00:00"},
])
def test_stale_partial_unbound_or_nonzero_erc_rejected(tmp_path, change):
    instance = collector(tmp_path)
    graph = instance.anonymize(raw_graph())
    proof = evidence(graph)
    proof.update(change)
    with pytest.raises(ValueError):
        instance.record_subcircuits("project", raw_graph(), proof)
    assert not (tmp_path / "telemetry/subcircuits").exists()


def test_erc_clean_hypergraph_has_passive_neighbors(tmp_path):
    instance = collector(tmp_path)
    graph = instance.anonymize(raw_graph())
    assert instance.record_subcircuits("project", raw_graph(), evidence(graph))["subcircuit_count"] == 1
    record = json.loads(next((tmp_path / "telemetry/subcircuits").glob("*.jsonl")).read_text())
    assert len(record["components"]) == 2 and len(record["hyperedges"][0]["pins"]) == 2


def test_routing_puzzle_requires_matching_geometry_and_fresh_drc(tmp_path):
    instance = collector(tmp_path)
    puzzle = raw_graph()
    routed = deepcopy(puzzle)
    routed["tracks"] = [{"x1": 1, "y1": 2, "x2": 3, "y2": 4, "width": 5, "net": "NET_PRIVATE"}]
    proof = evidence(instance.anonymize(routed), "drc")
    assert instance.record_routing("project", puzzle, routed, proof)["collected"]
    wrong = deepcopy(puzzle)
    wrong["boundary"][0][0] = 1
    with pytest.raises(ValueError, match="does not match"):
        instance.record_routing("project", wrong, routed, proof)
    record = json.loads(next((tmp_path / "telemetry/routing_puzzles").glob("*.jsonl")).read_text())
    assert record["solution"]["tracks"] and "NET_PRIVATE" not in json.dumps(record)


def test_revoked_optin_stops_future_writes(tmp_path):
    instance = collector(tmp_path)
    instance.capture_ai("project", raw_graph())
    existing = {str(path): path.read_bytes() for path in (tmp_path / "telemetry").rglob("*") if path.is_file()}
    (tmp_path / "config.json").write_text('{"telemetry_opt_in": false}')
    assert not instance.capture_ai("project", raw_graph())["collected"]
    assert existing == {str(path): path.read_bytes() for path in (tmp_path / "telemetry").rglob("*") if path.is_file()}


def test_observer_uses_explicit_readonly_native_tools_and_no_preference(tmp_path):
    instance = collector(tmp_path)
    target = str(tmp_path / "secret.PcbDoc")
    calls = []
    async def read(name, arguments):
        calls.append((name, arguments))
        if name == "app_context":
            return {"bridge_answering": True, "script_version_match": True,
                    "active_document": {"file_path": target}}
        assert name == "pcb_get_pad_connectivity"
        return {"board_path": target, "units": "mil", "data_domain": "physical_pcb",
                "counts": {"pads": 1}, "count": 1, "matched": 1, "truncated": False,
                "pads": [{"component": "U_PRIVATE", "pad": "1", "net": "NET_PRIVATE",
                          "layer": "TopLayer", "x": 2, "y": 3}]}
    result = asyncio.run(instance.observe("eda__pcb_place_tracks", {}, {"placed": 1}, read, mutating=True))
    assert result["collected"] and result["data_scope"] == "physical_pad_assignments"
    assert calls[-1][1] == {"board_path": target, "limit": 0}
    pending = next((tmp_path / "telemetry/pending").glob("*.json")).read_text()
    assert "U_PRIVATE" not in pending and "NET_PRIVATE" not in pending and "secret.PcbDoc" not in pending
    assert not (tmp_path / "telemetry/trajectories").exists()
    calls.clear()
    output = asyncio.run(instance.observe("eda__proj_save", {}, {"success": True}, read))
    assert not output["collected"] and calls == []


def test_observer_disabled_makes_no_native_reads(tmp_path):
    async def no_read(*args):
        raise AssertionError("No reads allowed")
    assert not asyncio.run(TelemetryCollector(tmp_path).observe(
        "eda__pcb_place_tracks", {}, {"success": True}, no_read, mutating=True))["collected"]
