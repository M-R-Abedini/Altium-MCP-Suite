"""Opt-in, local-only structural datasets. Never retain raw EDA responses.

This is pseudonymization, not a promise that circuit topology is anonymous.
Only the explicit structural allowlist below can reach disk. There is no
network uploader, and a save/verification event is never a DPO preference.
SPDX-License-Identifier: MIT
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading
from typing import Any
import uuid

MAX_ITEMS = 100_000
_PUBLIC_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+\-]{0,95}\Z")
_KINDS = {"ic", "resistor", "capacitor", "inductor", "diode", "transistor", "connector", "other"}


def data_directory() -> Path:
    """Resolve writable user data without writing into the plugin package."""
    explicit = os.environ.get("ALTIUM_SUITE_PLUGIN_DATA")
    if explicit:
        path = Path(explicit)
    else:
        path = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "plugins/data/mcp-altium-suite"
    if not path.is_absolute():
        raise ValueError("The suite data directory must be absolute")
    return path.resolve()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def graph_digest(graph: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(graph)).hexdigest()


def _items(value: Any) -> list:
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        raise ValueError("A bounded list of structural records is required")
    return value


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("Structural coordinates and constraints must be finite numbers")
    if abs(value) > 1e12:
        raise ValueError("Structural number exceeds the supported range")
    return float(value)


def _public(value: Any) -> str | None:
    # Public MPN/value tokens only; package/library names are hashed instead.
    return value if isinstance(value, str) and _PUBLIC_TOKEN.fullmatch(value) else None


class TelemetryCollector:
    """A private collector controlled only by persistent, explicit opt-in.

    `config.json` has `telemetry_opt_in: true` and optional `edition`.
    Enterprise mode disables all writes even when the opt-in flag is true.
    Instances do not create directories/salts while disabled.
    """

    def __init__(self, data_dir: Path | str | None = None):
        self.data_dir = Path(data_dir).resolve() if data_dir is not None else data_directory()
        self.root = self.data_dir / "telemetry"
        self._lock = threading.RLock()

    def enabled(self) -> bool:
        path = self.data_dir / "config.json"
        if not path.exists():
            return False
        try:
            config = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return False
        return (isinstance(config, dict) and config.get("telemetry_opt_in") is True
                and str(config.get("edition", "free")).casefold() != "enterprise")

    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled(), "default_enabled": False,
                "storage": "local_only", "network_upload_implemented": False,
                "privacy": "salted identifiers and structural allowlist; topology may still identify a design",
                "preference_labels_inferred_from_saves": False}

    @staticmethod
    def list_tools() -> list[dict[str, Any]]:
        return list_tools()

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if name != "status" or arguments not in (None, {}):
            raise ValueError("Only the read-only telemetry status tool is exposed")
        return self.status()

    async def observe(self, name: str, arguments: dict[str, Any], result: Any,
                      reader, *, mutating: bool = False) -> dict[str, Any]:
        """Gateway post-call hook, using existing read-only native tools.

        Successful PCB edits capture physical pad assignments. Schematic reads
        currently cannot certify complete, fresh ERC graphs without invoking
        compilation; this hook never starts that operation implicitly. Save
        and validation calls never become human-attributed preference labels.
        `reader(tool_name, args)` is the host's restricted native dispatcher.
        """
        if not self.enabled():
            return {"collected": False, "reason": "disabled"}
        if not mutating:
            return {"collected": False, "reason": "no_automatic_preference_or_verified_dataset_inference"}
        payload = getattr(result, "structuredContent", result)
        if (getattr(result, "isError", False) or not isinstance(payload, dict)
                or payload.get("ok") is False or payload.get("success") is False
                or payload.get("error") or payload.get("errors")):
            return {"collected": False, "reason": "unsuccessful_or_unstructured_edit"}

        async def read(tool, params):
            output = reader(tool, params)
            output = await output if inspect.isawaitable(output) else output
            if getattr(output, "isError", False):
                raise ValueError("Read-only native capture failed")
            output = getattr(output, "structuredContent", output)
            if not isinstance(output, dict):
                raise ValueError("Read-only native capture returned no structured object")
            return output

        # Do not probe the native editor for non-PCB/library/schematic events.
        original = name.split("__", 1)[-1]
        target = next((arguments[key] for key in ("board_path", "file_path", "document_path")
                       if isinstance(arguments.get(key), str)), "")
        if target and not target.casefold().endswith(".pcbdoc"):
            return {"collected": False, "reason": "automatic_capture_currently_physical_pcb_only"}
        if not target and not original.startswith("pcb_"):
            return {"collected": False, "reason": "automatic_capture_currently_physical_pcb_only"}
        context = await read("app_context", {})
        active = context.get("active_document", {}).get("file_path")
        if (context.get("bridge_answering") is not True or context.get("script_version_match") is not True
                or not isinstance(active, str) or not active.casefold().endswith(".pcbdoc")):
            return {"collected": False, "reason": "no_verified_active_pcb"}
        target = target or active
        if os.path.normcase(str(Path(target).resolve())) != os.path.normcase(str(Path(active).resolve())):
            return {"collected": False, "reason": "target_is_not_active_pcb"}
        data = await read("pcb_get_pad_connectivity", {"board_path": target, "limit": 0})
        raw = physical_pad_graph(data, target)
        output = self.capture_ai(target, raw)
        return {**output, "data_scope": "physical_pad_assignments",
                "coordinate_resolution": "native integer mil readback",
                "copper_connectivity_verified": False, "human_preference_inferred": False}

    def _salt(self) -> bytes:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root / "salt.bin"
        try:
            with path.open("xb") as stream:
                stream.write(secrets.token_bytes(32))
            path.chmod(0o600)
        except FileExistsError:
            pass
        salt = path.read_bytes()
        if len(salt) != 32:
            raise ValueError("Invalid private telemetry salt; collection stopped")
        return salt

    @staticmethod
    def _identifier(salt: bytes, domain: str, value: Any) -> str:
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise ValueError("A structural identifier must be text or an integer")
        encoded = str(value).encode("utf-8")
        if not encoded or len(encoded) > 4096:
            raise ValueError("Invalid structural identifier length")
        return hashlib.sha256(salt + b"\0" + domain.encode("ascii") + b"\0" + encoded).hexdigest()

    def anonymize(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Return a strict structural projection; free text/paths are dropped.

        Call only after explicit consent: even deriving the stable local salt
        is a collection action. Units must be supplied by the producer.
        """
        if not self.enabled():
            raise ValueError("Telemetry is disabled; enable it explicitly in private config.json")
        if not isinstance(raw, dict) or raw.get("units") not in {"mils", "mm", "um"}:
            raise ValueError("A structural snapshot with explicit mils/mm/um units is required")
        salt = self._salt()
        identify = lambda domain, value: self._identifier(salt, domain, value)
        graph: dict[str, Any] = {"schema": 1, "units": raw["units"], "components": [],
                                 "nets": [], "pads": [], "tracks": [], "vias": [], "boundary": []}
        component_ids = set()
        for item in _items(raw.get("components", [])):
            if not isinstance(item, dict):
                raise ValueError("Component records must be objects")
            cid = identify("component", item["id"])
            if cid in component_ids:
                raise ValueError("Duplicate component identifier")
            component_ids.add(cid)
            output = {"id": cid, "kind": item.get("kind") if item.get("kind") in _KINDS else "other",
                      "pins": []}
            for name in ("mpn", "value"):
                token = _public(item.get(name))
                if token is not None:
                    output[name] = token
            if item.get("footprint"):
                output["footprint"] = identify("footprint", item["footprint"])
            for name in ("x", "y", "rotation"):
                if name in item:
                    output[name] = _number(item[name])
            for pin in _items(item.get("pins", [])):
                record = {"pin": identify("pin", pin["pin"]), "net": identify("net", pin["net"])}
                # A free-text functional name may contain a project secret.
                if pin.get("function"):
                    record["function"] = identify("function", pin["function"])
                output["pins"].append(record)
            output["pins"].sort(key=lambda pin: (pin["pin"], pin["net"]))
            graph["components"].append(output)
        for item in _items(raw.get("nets", [])):
            output = {"id": identify("net", item["id"]), "pins": []}
            for pin in _items(item.get("pins", [])):
                output["pins"].append({"component": identify("component", pin["component"]),
                                       "pin": identify("pin", pin["pin"])})
            output["pins"].sort(key=lambda pin: (pin["component"], pin["pin"]))
            graph["nets"].append(output)
        for collection, numbers in (("pads", ("x", "y", "width", "height")),
                                    ("tracks", ("x1", "y1", "x2", "y2", "width")),
                                    ("vias", ("x", "y", "diameter", "hole"))):
            for item in _items(raw.get(collection, [])):
                output = {name: _number(item[name]) for name in numbers if name in item}
                for name in ("net", "layer", "low_layer", "high_layer", "component", "pin"):
                    if name in item:
                        output[name] = identify(name, item[name])
                graph[collection].append(output)
        for point in _items(raw.get("boundary", [])):
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError("Board boundary points must contain x/y coordinates")
            graph["boundary"].append([_number(point[0]), _number(point[1])])
        # Numeric constraints only: expressions/query text are deliberately excluded.
        constraints = raw.get("constraints", {})
        if not isinstance(constraints, dict):
            raise ValueError("constraints must be an object")
        graph["constraints"] = {name: _number(constraints[name]) for name in
                                ("clearance", "track_width", "via_diameter", "via_hole")
                                if name in constraints}
        for collection in ("components", "nets", "pads", "tracks", "vias"):
            graph[collection].sort(key=canonical_bytes)
        return graph

    def _project(self, project_id: str) -> str:
        return self._identifier(self._salt(), "project", project_id)

    def _write(self, path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_bytes(canonical_bytes(record) + b"\n")
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _append(self, stream: str, record: dict[str, Any]) -> str:
        # One immutable JSONL shard per record avoids corruption when multiple
        # AI clients collect concurrently; each shard is directly concatenable.
        directory = self.root / stream
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = directory / (date + "-" + record["record_id"] + ".jsonl")
        with path.open("xb") as output:
            output.write(canonical_bytes(record) + b"\n")
        path.chmod(0o600)
        return record["record_id"]

    def capture_ai(self, project_id: str, raw: dict[str, Any]) -> dict[str, Any]:
        """Store only the most recent consenting AI structural snapshot."""
        with self._lock:
            if not self.enabled():
                return {"collected": False, "reason": "disabled"}
            graph = self.anonymize(raw)
            project = self._project(project_id)
            record = {"schema": 1, "project": project, "graph": graph,
                      "snapshot_sha256": graph_digest(graph), "source": "ai",
                      "recorded_at_utc": datetime.now(timezone.utc).isoformat()}
            self._write(self.root / "pending" / (project + ".json"), record)
            return {"collected": True, "snapshot_sha256": record["snapshot_sha256"]}

    @staticmethod
    def _verification(evidence: dict[str, Any], graph: dict[str, Any], kind: str) -> None:
        if (not isinstance(evidence, dict) or evidence.get("kind") != kind
                or evidence.get("source") != "bridge" or evidence.get("fresh") is not True
                or evidence.get("complete") is not True or type(evidence.get("errors")) is not int
                or evidence["errors"] != 0 or evidence.get("snapshot_sha256") != graph_digest(graph)):
            raise ValueError("Fresh, complete, zero-error bridge verification bound to this snapshot is required")
        try:
            observed = datetime.fromisoformat(evidence["observed_at_utc"])
            if observed.utcoffset() is None:
                raise ValueError
            age = (datetime.now(timezone.utc) - observed).total_seconds()
            if not 0 <= age <= 300:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ValueError("Verification must have a UTC-aware observation within the last five minutes") from None

    def record_human(self, project_id: str, raw: dict[str, Any], *, human_confirmed: bool = False,
                     evidence: dict[str, Any] | None = None) -> dict[str, Any]:
        """Record a correction candidate only after explicit human attribution.

        No preference/reward is inferred: those labels need a separate human
        decision and are deliberately absent from the stored training record.
        """
        with self._lock:
            if not self.enabled():
                return {"collected": False, "reason": "disabled"}
            if human_confirmed is not True:
                return {"collected": False, "reason": "human_attribution_required"}
            graph = self.anonymize(raw)
            self._verification(evidence, graph, "erc" if not graph["tracks"] else "drc")
            project = self._project(project_id)
            path = self.root / "pending" / (project + ".json")
            if not path.exists():
                return {"collected": False, "reason": "no_ai_snapshot"}
            before = json.loads(path.read_bytes())["graph"]
            delta = structural_delta(before, graph)
            record = {"schema": 1, "record_id": uuid.uuid4().hex, "project": project,
                      "ai": before, "human": graph, "delta": delta,
                      "label": "human_correction_candidate", "preference": None,
                      "recorded_at_utc": datetime.now(timezone.utc).isoformat()}
            identifier = self._append("trajectories", record)
            path.unlink()
            return {"collected": True, "record_id": identifier, "delta": delta}

    def record_subcircuits(self, project_id: str, raw: dict[str, Any],
                           evidence: dict[str, Any]) -> dict[str, Any]:
        """Extract one-hop IC-centered hypergraphs only from an ERC-clean graph."""
        with self._lock:
            if not self.enabled():
                return {"collected": False, "reason": "disabled"}
            graph = self.anonymize(raw)
            self._verification(evidence, graph, "erc")
            project = self._project(project_id)
            count = 0
            for center in graph["components"]:
                if center["kind"] != "ic":
                    continue
                connected = {pin["net"] for pin in center["pins"]}
                members = [part for part in graph["components"]
                           if part["id"] == center["id"] or any(pin["net"] in connected for pin in part["pins"])]
                # All constituent hyperedges come from pin bindings, even when
                # the producer did not supply a separate `nets` collection.
                edges = [{"net": net, "pins": [{"component": part["id"], "pin": pin["pin"]}
                           for part in members for pin in part["pins"] if pin["net"] == net]}
                         for net in sorted(connected)]
                self._append("subcircuits", {"schema": 1, "record_id": uuid.uuid4().hex,
                             "project": project, "center": center["id"], "components": members,
                             "hyperedges": edges, "verification": "fresh_complete_erc_zero_errors"})
                count += 1
            return {"collected": bool(count), "subcircuit_count": count}

    def record_routing(self, project_id: str, puzzle_raw: dict[str, Any],
                       routed_raw: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
        """Store geometry projections, never raw DSN/SES source text."""
        with self._lock:
            if not self.enabled():
                return {"collected": False, "reason": "disabled"}
            puzzle, routed = self.anonymize(puzzle_raw), self.anonymize(routed_raw)
            self._verification(evidence, routed, "drc")
            if not puzzle["boundary"] or not puzzle["pads"]:
                raise ValueError("A routing puzzle needs explicit board boundary and pad geometry")
            invariant = lambda value: canonical_bytes({name: value[name] for name in
                               ("units", "components", "nets", "pads", "boundary", "constraints")})
            if invariant(puzzle) != invariant(routed):
                raise ValueError("Routing solution does not match the puzzle geometry/netlist/constraints")
            record = {"schema": 1, "record_id": uuid.uuid4().hex, "project": self._project(project_id),
                      "puzzle": puzzle, "solution": routed,
                      "verification": "fresh_complete_drc_zero_errors"}
            identifier = self._append("routing_puzzles", record)
            return {"collected": True, "record_id": identifier}


def physical_pad_graph(data: dict[str, Any], expected_board: str) -> dict[str, Any]:
    """Project an actual `pcb_get_pad_connectivity` result without comments.

    A pad assignment is not a copper connectivity/DRC result. Physical native
    inventory is retained without guessing component MPN, kind or geometry
    dimensions that this read does not provide.
    """
    board = data.get("board_path")
    pads = data.get("pads")
    if (data.get("data_domain") != "physical_pcb" or data.get("truncated") is not False
            or not isinstance(board, str) or os.path.normcase(str(Path(board).resolve())) !=
            os.path.normcase(str(Path(expected_board).resolve()))
            or not isinstance(pads, list) or data.get("units") != "mil"
            or type(data.get("matched")) is not int or data["matched"] != len(pads)
            or data.get("count") != len(pads) or data.get("counts", {}).get("pads") != len(pads)):
        raise ValueError("A complete matching native physical pad read is required")
    components, nets, projected = {}, {}, []
    for index, item in enumerate(pads):
        if not isinstance(item, dict) or any(not isinstance(item.get(key), str) for key in
                                           ("component", "pad", "net", "layer")):
            raise ValueError("Invalid native pad identity")
        component, pin, net = item["component"], item["pad"], item["net"]
        # Free/unnamed pads receive scoped synthetic IDs; no assumption that
        # all free pads or all netless pads are electrically connected.
        if not net:
            net = "unconnected-pad:" + str(index)
        if component:
            part = components.setdefault(component, {"id": component, "kind": "other", "pins": []})
            part["pins"].append({"pin": pin or "unnamed-pad:" + str(index), "net": net})
        else:
            component = "free-pad:" + str(index)
        nets.setdefault(net, {"id": net, "pins": []})["pins"].append({"component": component,
                                                                          "pin": pin or str(index)})
        projected.append({"component": component, "pin": pin or str(index), "net": net,
                          "x": _number(item["x"]), "y": _number(item["y"]), "layer": item["layer"]})
    return {"units": "mils", "components": list(components.values()), "nets": list(nets.values()),
            "pads": projected}


def structural_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Compare already projected graphs without adding raw fields or labels."""
    if before.get("units") != after.get("units"):
        raise ValueError("Snapshots use different units")
    previous = {part["id"]: part for part in before.get("components", [])}
    current = {part["id"]: part for part in after.get("components", [])}
    moved, bindings, changed = [], [], []
    for identifier in sorted(previous.keys() & current.keys()):
        a, b = previous[identifier], current[identifier]
        if any(a.get(key) != b.get(key) for key in ("x", "y", "rotation")):
            moved.append({"component": identifier,
                          "before": {key: a[key] for key in ("x", "y", "rotation") if key in a},
                          "after": {key: b[key] for key in ("x", "y", "rotation") if key in b}})
        if a.get("pins") != b.get("pins"):
            bindings.append({"component": identifier, "before": a.get("pins", []), "after": b.get("pins", [])})
        if any(a.get(key) != b.get(key) for key in ("mpn", "value", "footprint")):
            changed.append(identifier)
    return {"added_components": sorted(current.keys() - previous.keys()),
            "removed_components": sorted(previous.keys() - current.keys()),
            "moved_components": moved, "pin_binding_changes": bindings,
            "component_property_changes": changed,
            "routing_changed": any(before.get(key) != after.get(key) for key in ("tracks", "vias"))}


def list_tools() -> list[dict[str, Any]]:
    return [{"name": "status", "description": "Inspect local structural dataset opt-in/privacy status. Does not collect designs or enable telemetry.",
             "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
             "annotations": {"readOnlyHint": True, "openWorldHint": False}}]


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if name != "status" or arguments not in (None, {}):
        raise ValueError("Only the read-only telemetry status tool is exposed")
    return TelemetryCollector().status()
