"""Independent library operations using suite-owned binary codecs.

SPDX-License-Identifier: MIT
Contract provenance: released worker's MCP tools/list, observed externally.
No implementation from the retired worker is imported or executed here.
"""
from __future__ import annotations

import asyncio
import base64
from collections import Counter, defaultdict
import copy
import csv
from datetime import datetime, timezone
import fnmatch
from functools import lru_cache
import importlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
import uuid

CONTRACT = Path(__file__).with_name("contracts") / "libraries-v0.3.0.json"
PCB_KINDS = {"pads": "pad", "vias": "via", "tracks": "track", "arcs": "arc",
             "regions": "region", "text": "text", "fills": "fill",
             "component_bodies": "component_body"}
SCH_KINDS = {"pins": "pin", "rectangles": "rectangle", "round_rects": "round_rect",
             "lines": "line", "polylines": "polyline", "polygons": "polygon",
             "arcs": "arc", "elliptical_arcs": "elliptical_arc", "pies": "pie",
             "ellipses": "ellipse", "beziers": "bezier", "images": "image",
             "text_frames": "text_frame", "labels": "label", "ieee_symbols": "ieee_symbol",
             "parameters": "parameter", "footprints": "footprint"}
IDENTITY = {"guid", "unique_id", "identity_guid", "identity_guid_b", "designator_unique_id"}
FIDELITY = {"raw_tail", "raw_block", "raw_geometry", "raw_params", "header_params",
            "param_key_order", "primitive_order", "extra_streams"}


def list_tools():
    return json.loads(CONTRACT.read_text(encoding="utf-8-sig"))["tools"]


@lru_cache(maxsize=34)
def _tool_validator(name):
    """Check each immutable public schema once, rather than on every request."""
    import jsonschema
    schema = next(tool['inputSchema'] for tool in list_tools() if tool['name'] == name)
    validator_type = jsonschema.validators.validator_for(schema)
    validator_type.check_schema(schema)
    return validator_type(schema)


def _units(kind):
    return "mm" if kind == "PcbLib" else "schematic units (10 = 1 grid)"


def _key(kind):
    return "footprints" if kind == "PcbLib" else "symbols"


def _kind(path):
    suffix = Path(path).suffix.casefold()
    if suffix not in (".pcblib", ".schlib"):
        raise ValueError("Unsupported library type; expected .PcbLib or .SchLib")
    return "PcbLib" if suffix == ".pcblib" else "SchLib"


def _find(components, name):
    for component in components:
        if component["name"].casefold() == name.casefold():
            return component
    raise ValueError(f"Component '{name}' not found in library. Available: " +
                     ", ".join(c["name"] for c in components))


def _unique(components):
    seen = set()
    for component in components:
        name = component.get("name", "")
        if not name.strip() or any(char in name for char in "|/\\\0"):
            raise ValueError("Component name must be nonempty and contain no |, slash, or NUL")
        if name.casefold() in seen:
            raise ValueError(f"Component '{name}' already exists")
        seen.add(name.casefold())


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Numbers must be finite")
    if isinstance(value, dict):
        for child in value.values():
            _finite(child)
    elif isinstance(value, list):
        for child in value:
            _finite(child)


def _identity_copy(value):
    if isinstance(value, list):
        return [_identity_copy(child) for child in value]
    if isinstance(value, dict):
        result = copy.deepcopy(value)
        result.clear()
        for key, child in value.items():
            if key in IDENTITY:
                result[key] = "{" + str(uuid.uuid4()).upper() + "}" if "guid" in key else uuid.uuid4().hex[:8].upper()
            elif key == "raw_params" or key == "header_params":
                result[key] = [[k, uuid.uuid4().hex[:8].upper() if k.casefold() == "uniqueid" else v]
                               for k, v in child]
            else:
                result[key] = _identity_copy(child)
        return result
    return value


def _semantic(value):
    if isinstance(value, dict):
        return {k: _semantic(v) for k, v in value.items()
                if k not in IDENTITY | FIDELITY and not k.startswith("_")}
    if isinstance(value, list):
        return [_semantic(v) for v in value]
    return value


def _differences(a, b, tolerance, path=""):
    if isinstance(a, (int, float)) and not isinstance(a, bool) and isinstance(b, (int, float)) and not isinstance(b, bool):
        return [] if abs(a - b) <= tolerance else [{"property": path, "a": a, "b": b}]
    if isinstance(a, dict) and isinstance(b, dict):
        result = []
        for key in sorted(a.keys() | b.keys()):
            result.extend(_differences(a.get(key), b.get(key), tolerance, f"{path}.{key}".strip(".")))
        return result
    if isinstance(a, list) and isinstance(b, list):
        result = []
        if len(a) != len(b):
            result.append({"property": path + ".count", "a": len(a), "b": len(b)})
        for index, (x, y) in enumerate(zip(a, b)):
            result.extend(_differences(x, y, tolerance, f"{path}[{index}]"))
        return result
    return [] if a == b else [{"property": path, "a": a, "b": b}]


def _stats(values, units, average=False):
    if not values:
        return None
    result = {"count": len(values), f"min_{units}": min(values), f"max_{units}": max(values),
              f"most_common_{units}": Counter(values).most_common(1)[0][0]}
    if average:
        result[f"avg_{units}"] = sum(values) / len(values)
    return result


def _metadata(component, kind):
    result = {"name": component["name"], "description": component.get("description", "")}
    if kind == "SchLib":
        result.update(designator=component.get("designator", "U?"),
                      part_count=component.get("part_count", 1), pin_count=len(component.get("pins", [])),
                      footprint_count=len(component.get("footprints", [])))
    else:
        result.update({f"{singular}_count": len(component.get(plural, [])) for plural, singular in PCB_KINDS.items()})
        result["has_3d_model"] = bool(component.get("model_3d") or component.get("component_bodies"))
    return result


_FONT_KEY = re.compile(r"^(FontName|Size|Italic|Bold|Underline|StrikeOut|Rotation|CharSet)(\d+)$", re.IGNORECASE)


def _font_tables(header):
    tables = defaultdict(list)
    for key, value in header:
        match = _FONT_KEY.fullmatch(key)
        if match:
            tables[int(match[2])].append((match[1], value))
    return dict(tables)


def _merge_font_headers(target, source):
    if target is None:
        return copy.deepcopy(source), {index: index for index in _font_tables(source)}
    result = copy.deepcopy(target)
    existing = _font_tables(result)
    canonical = {tuple(sorted((k.casefold(), v) for k, v in values)): index
                 for index, values in existing.items()}
    mapping = {}
    for old_index, values in _font_tables(source).items():
        identity = tuple(sorted((key.casefold(), value) for key, value in values))
        if identity in canonical:
            mapping[old_index] = canonical[identity]
            continue
        new_index = max(existing, default=0) + 1
        existing[new_index] = values
        canonical[identity] = new_index
        mapping[old_index] = new_index
        result.extend([[f"{key}{new_index}", value] for key, value in values])
    count = str(max(existing, default=0))
    for entry in result:
        if entry[0].casefold() == "fontidcount":
            entry[1] = count
            break
    else:
        result.append(["FontIdCount", count])
    return result, mapping


def _remap_fonts(value, mapping):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("font_id", "designator_font_id") and isinstance(child, int):
                value[key] = mapping.get(child, child)
            elif key in ("raw_params", "header_params"):
                for pair in child:
                    if pair[0].casefold() == "fontid":
                        try:
                            pair[1] = str(mapping.get(int(pair[1]), int(pair[1])))
                        except (TypeError, ValueError):
                            pass
            else:
                _remap_fonts(child, mapping)
    elif isinstance(value, list):
        for child in value:
            _remap_fonts(child, mapping)


class NativeLibraries:
    """Path-confined library service. Empty allowed_paths denies filesystem access."""

    def __init__(self, data_dir=None, allowed_paths=None, native_dispatch=None, logging_config=None):
        self.data_dir = Path(data_dir or Path.home() / ".altium-suite").resolve()
        self.allowed_paths = [Path(path).resolve() for path in (allowed_paths or [])]
        self.native_dispatch = native_dispatch
        self.logging_config = copy.deepcopy(logging_config or {})
        audit_path = self.logging_config.get('audit_log_path')
        self.audit_path = Path(audit_path).expanduser().resolve() if audit_path else None
        if self.audit_path and self.audit_path.suffix.casefold() in ('.pcblib', '.schlib'):
            raise ValueError('audit_log_path must identify a log file, not a design library')
        self._lock = threading.RLock()
        self._tools = {tool["name"]: tool for tool in list_tools()}

    def list_tools(self):
        return copy.deepcopy(list(self._tools.values()))

    def _path(self, value):
        if not isinstance(value, str) or not value.strip() or "\0" in value:
            raise ValueError("A nonempty filesystem path is required")
        path = Path(value).expanduser().resolve()
        if not any(path == root or root in path.parents for root in self.allowed_paths):
            raise PermissionError(f"Path is outside configured allowed_paths: {path}")
        return path

    def _codec(self, path):
        return importlib.import_module("suite_pcblib_codec" if _kind(path) == "PcbLib" else "suite_schlib_codec")

    def _read(self, path):
        path = self._path(str(path))
        if not path.is_file():
            raise FileNotFoundError(f"Failed to read file: {path.name}")
        result = self._codec(path).read_library(path)
        if isinstance(result, list):
            result = {_key(_kind(path)): result}
        if result.get("status") == "error":
            raise ValueError(result.get("error", "Could not read library"))
        return result

    def _components(self, path):
        return self._read(path).get(_key(_kind(path)), [])

    def _models(self, path, data):
        """Normalize codec-private model payloads without exposing them on reads."""
        supplied = getattr(data, "embedded_models", None)
        if supplied is None:
            supplied = data.get("embedded_models")
        if supplied is None and _kind(path) == "PcbLib":
            codec = self._codec(path)
            supplied = codec.read_embedded_models(path) if hasattr(codec, "read_embedded_models") else []
        result = {}
        if isinstance(supplied, dict):
            records = [dict(value, id=key) if isinstance(value, dict) else {"id": key, "data": value}
                       for key, value in supplied.items()]
        else:
            records = supplied or []
        for record in records:
            item = copy.deepcopy(record)
            identifier = item.get("id", item.get("guid"))
            if not identifier:
                raise ValueError("Embedded model has no id")
            payload = item.get("data", item.get("data_base64", item.get("base64", b"")))
            if isinstance(payload, str):
                payload = base64.b64decode(payload, validate=True)
            if not isinstance(payload, bytes):
                raise ValueError("Embedded model data must be bytes or base64")
            item.update(id=identifier, data=payload, name=item.get("name", identifier + ".step"))
            result[identifier] = item
        return result

    def _backup(self, path):
        if not path.is_file():
            return None
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup = path.with_name(path.name + "." + stamp + ".bak")
        shutil.copy2(path, backup)
        return str(backup)

    def _write(self, path, components, **options):
        path = self._path(str(path))
        _unique(components)
        validation = self._validation(components, _kind(path), authoring=True)
        if validation["error_count"]:
            raise ValueError("Library validation failed: " + "; ".join(issue["issue"] for issue in validation["issues"] if issue["severity"] == "error"))
        for component in components:
            for reference in ("model_3d", "step_model"):
                model = component.get(reference)
                if isinstance(model, dict) and model.get("filepath"):
                    model_path = Path(model["filepath"])
                    # Embedding resolves references relative to this library,
                    # rather than the server process's working directory.
                    self._path(str(model_path if model_path.is_absolute() else path.parent / model_path))
            for footprint in component.get("footprints", []):
                if footprint.get("library_path"):
                    self._path(footprint["library_path"])
        backup = self._backup(path)
        if path.is_file() and "embedded_models" not in options:
            existing = self._read(path)
            if components == existing.get(_key(_kind(path)), []):
                return existing, backup
        result = self._codec(path).write_library(path, components, **options)
        if isinstance(result, dict) and result.get("status") == "error":
            raise ValueError(result.get("error", "Could not write library"))
        return result or {}, backup

    async def call_tool_async(self, name, arguments=None):
        return await asyncio.to_thread(self.call_tool, name, arguments)

    def call_tool(self, name, arguments=None):
        with self._lock:
            started = time.monotonic()
            result = self._call_tool(name, arguments)
            if self.audit_path:
                record = {'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'tool': name,
                          'status': 'error' if result.get('status') == 'error' else 'success',
                          'duration_ms': round((time.monotonic() - started) * 1000, 3)}
                try:
                    self.audit_path.parent.mkdir(parents=True, exist_ok=True)
                    with self.audit_path.open('a', encoding='utf-8') as output:
                        output.write(json.dumps(record, ensure_ascii=True, separators=(',', ':')) + '\n')
                except OSError as exc:
                    # Never misreport a completed edit as failed because the
                    # optional audit sink became unavailable after that edit.
                    result['audit_warning'] = f'Audit log could not be written: {type(exc).__name__}'
            return result

    def _call_tool(self, name, arguments=None):
        try:
            if name not in self._tools:
                raise ValueError(f"Unknown library tool: {name}")
            args = copy.deepcopy(arguments or {})
            _tool_validator(name).validate(args)
            _finite(args)
            return self._dispatch(name, args)
        except Exception as exc:
            # Plain JSON for both direct Python callers and MCP adapters. Never
            # report successful mutations after a codec rejected the operation.
            detail = exc.message if type(exc).__module__.startswith("jsonschema") and hasattr(exc, "message") else str(exc)
            return {"status": "error", "error": detail, "tool": name}

    def _dispatch(self, name, a):
        if name == "write_libpkg":
            return self._libpkg(a)
        if name in ("list_backups", "restore_backup"):
            return self._backups(name, a)
        if name == "search_components":
            return self._search(a)
        if name in ("diff_libraries", "compare_components"):
            return self._compare(name, a)
        if name in ("merge_libraries", "copy_component_cross_library"):
            return self._cross(name, a)
        if name == "import_library":
            data = a["json_data"]
            kind = data.get("file_type")
            if kind not in ("PcbLib", "SchLib"):
                raise ValueError("json_data.file_type must be PcbLib or SchLib")
            target = self._path(a["output_path"])
            if _kind(target) != kind:
                raise ValueError("Output extension does not match imported file_type")
            key = _key(kind)
            if key not in data or not isinstance(data[key], list):
                raise ValueError(f"json_data.{key} must be an array")
            result = self._dispatch("write_pcblib" if kind == "PcbLib" else "write_schlib",
                                    {"filepath": str(target), key: data[key], "append": a.get("append", False),
                                     "_embedded_models": data.get("embedded_models", []),
                                     "_library_header_params": data.get("library_header_params")})
            result.update(file_type=kind, imported_count=len(data[key]), output_path=str(target))
            return result

        path = self._path(a["filepath"])
        kind = _kind(path)
        key = _key(kind)
        if name in ("write_pcblib", "write_schlib"):
            expected = "PcbLib" if name == "write_pcblib" else "SchLib"
            if kind != expected:
                raise ValueError(f"{name} requires .{expected}")
            components = copy.deepcopy(a[key])
            bodies, warnings = self._prepare_footprints(components, a) if kind == "PcbLib" else ([], [])
            if a.get("append") and path.exists():
                components = self._components(path) + components
            options = {k: a[k] for k in ("auto_designator", "auto_3d_body") if k in a}
            if "_embedded_models" in a:
                options["embedded_models"] = a["_embedded_models"]
            if a.get("_library_header_params"):
                options["library_header_params"] = a["_library_header_params"]
            codec_result, backup = self._write(path, components, **options)
            result = {field: codec_result[field] for field in ("geometry", "validation", "bodies", "warnings") if field in codec_result}
            singular = "footprint" if kind == "PcbLib" else "symbol"
            result.update(status="success", filepath=str(path), **{singular + "_count": len(components),
                          singular + "_names": [c["name"] for c in components]})
            if "validation" not in result:
                result["validation"] = self._validation(components, kind, authoring=True)
            if kind == "SchLib" and "geometry" not in result:
                result["geometry"] = [self._geometry(component) for component in components]
            if kind == "PcbLib":
                for summary in bodies:
                    if summary["source"] == "step":
                        written = next((fp for fp in codec_result.get("footprints", []) if fp["name"] == summary["name"]), {})
                        summary["source"] = "step-embedded" if any(body.get("embedded") for body in written.get("component_bodies", [])) else "step-external"
                        if summary['source'] == 'step-external':
                            component = next(fp for fp in components if fp['name'] == summary['name'])
                            summary['model'] = (component.get('step_model') or component.get('model_3d'))['filepath']
                result.setdefault("bodies", bodies)
                result.setdefault("warnings", warnings)
            if backup:
                result["backup"] = backup
            return result

        data = self._read(path)
        components = data.get(key, [])
        common = {"status": "success", "filepath": str(path)}
        if name in ("read_pcblib", "read_schlib", "list_components"):
            if name.startswith("read_") and name != "read_" + kind.lower():
                raise ValueError(f"{name} does not accept .{kind}")
            chosen = [_find(components, a["component_name"])] if "component_name" in a else components
            offset = a.get("offset", 0)
            chunk = chosen[offset:offset + a.get("limit", len(chosen))]
            result = dict(common, offset=offset, returned_count=len(chunk), total_count=len(chosen),
                          has_more=offset + len(chunk) < len(chosen))
            if name == "list_components":
                metadata = a.get("include_metadata", False)
                result.update(file_type=kind, include_metadata=metadata,
                              components=[_metadata(c, kind) if metadata else c["name"] for c in chunk])
            else:
                result.update({key: chunk, "units": _units(kind)})
                if kind == "PcbLib":
                    result["compact"] = a.get("compact", True)
                    if result["compact"]:
                        for component in chunk:
                            for pad in component.get("pads", []):
                                if pad.get("stack_mode", "simple").casefold() == "simple":
                                    for field in ("per_layer_sizes", "per_layer_shapes", "per_layer_widths", "per_layer_heights"):
                                        pad.pop(field, None)
            return result
        if name == "get_component":
            return dict(common, component_name=a["component_name"], component=_find(components, a["component_name"]),
                        type=kind, units=_units(kind), message=f"Retrieved {'footprint' if kind == 'PcbLib' else 'symbol'} '{a['component_name']}' from '{path}'")
        if name == "component_exists":
            names = {c["name"].casefold() for c in components}
            results = [{"name": n, "exists": n.casefold() in names} for n in a["component_names"]]
            return dict(common, results=results, checked_count=len(results),
                        exists_count=sum(r["exists"] for r in results), all_exist=all(r["exists"] for r in results))
        if name == "export_library":
            result = dict(common, file_type=kind, format=a["format"], component_count=len(components))
            if a["format"] == "json":
                result.update({key: components, "units": _units(kind)})
                if kind == "PcbLib":
                    models = self._models(path, data)
                    if models:
                        result["embedded_models"] = [{"id": model["id"], "name": model["name"],
                            "data": base64.b64encode(model["data"]).decode("ascii")} for model in models.values()]
                elif hasattr(self._codec(path), "read_library_header"):
                    # An additive fidelity carrier keeps non-default font tables
                    # and unmodelled library settings through JSON export/import.
                    result["library_header_params"] = self._codec(path).read_library_header(path)
            else:
                kinds = PCB_KINDS if kind == "PcbLib" else SCH_KINDS
                columns = ["name", "description"] + (["designator"] if kind == "SchLib" else [])
                columns += [singular + "_count" for singular in kinds.values()]
                if kind == "PcbLib":
                    columns.append("model_3d_count")
                stream = io.StringIO(newline="")
                writer = csv.DictWriter(stream, columns, lineterminator="\n")
                writer.writeheader()
                for component in components:
                    row = {field: component.get(field, "") for field in columns if not field.endswith("_count")}
                    row.update({singular + "_count": len(component.get(plural, [])) for plural, singular in kinds.items()})
                    if kind == "PcbLib":
                        row["model_3d_count"] = int(bool(component.get("model_3d")))
                    writer.writerow(row)
                result["csv"] = stream.getvalue().rstrip("\n")
            return result
        if name == "validate_library":
            context = {"embedded_models": self._models(path, data)} if kind == "PcbLib" else data
            return dict(self._validation(components, kind, data=context), filepath=str(path),
                        file_type=kind, component_count=len(components))
        if name == "extract_style":
            return dict(common, file_type=kind, style=self._style(components, kind),
                        **{("footprint_count" if kind == "PcbLib" else "symbol_count"): len(components)})
        if name.startswith("render_"):
            if (name == "render_footprint") != (kind == "PcbLib"):
                raise ValueError("Render tool does not match library type")
            component = _find(components, a["component_name"])
            result = dict(common, component_name=a["component_name"],
                          scale=a.get("scale", 2.0 if kind == "PcbLib" else 1.0),
                          render=self._render(component, kind, a))
            if kind == "SchLib":
                result["part_id"] = a.get("part_id", 1)
            return result
        if name == "extract_step_model":
            if kind != "PcbLib":
                raise ValueError("STEP extraction requires a .PcbLib")
            return self._step(path, data, a)
        if name == "repair_library":
            if kind != "PcbLib":
                raise ValueError("repair_library only supports .PcbLib")
            return self._repair(path, data, components, a)
        return self._mutate(name, a, path, kind, components)

    @staticmethod
    def _prepare_footprints(components, options):
        summaries, warnings = [], []
        for component in components:
            pads = component.get("pads", [])
            xs = [pad.get("x", 0) + sign * pad.get("width", 0) / 2 for pad in pads for sign in (-1, 1)]
            ys = [pad.get("y", 0) + sign * pad.get("height", 0) / 2 for pad in pads for sign in (-1, 1)]
            x1, x2 = (min(xs), max(xs)) if xs else (-1, 1)
            y1, y2 = (min(ys), max(ys)) if ys else (-1, 1)
            outline = [{"x": x1, "y": y2}, {"x": x1, "y": y1}, {"x": x2, "y": y1}, {"x": x2, "y": y2}]
            echoed = "primitive_order" in component or all(field in component for field in
                ("pads", "tracks", "arcs", "fills", "text", "regions", "vias"))
            if options.get("auto_designator", True) and not echoed and not any(
                    str(text.get("text", "")).casefold() == ".designator" for text in component.get("text", [])):
                component.setdefault("text", []).append({"text": ".Designator", "x": 0,
                    "y": (max(ys) if ys else 0) + 0.6, "height": 1.0,
                    "stroke_width": 0.1016, "rotation": 0, "layer": "Top Overlay"})
            step = component.get("step_model") or component.get("model_3d")
            bodies = component.get("component_bodies", [])
            summary = {"name": component["name"]}
            if step and step.get("filepath"):
                # The codec decides whether a named STEP path exists and embeds
                # it. Report its source after writing when that is available.
                summary["source"] = "step"
            elif bodies:
                for body in bodies:
                    body.setdefault("outline", copy.deepcopy(outline))
                summary.update(source="extruded", overall_height=bodies[0].get("overall_height", 0),
                               standoff_height=bodies[0].get("standoff_height", 0), assumed_height=False)
            elif options.get("auto_3d_body", False) and pads:
                component["component_bodies"] = [{"overall_height": 1.0, "standoff_height": 0.0,
                    "outline": outline, "layer": "Top 3D Body"}]
                summary.update(source="auto-extruded", overall_height=1.0, standoff_height=0.0, assumed_height=True,
                    action_required=f"No 3D body height was given for '{component['name']}', so a 1 mm placeholder was used. Confirm the real component height and supply component_bodies[].overall_height.")
            else:
                summary.update(source="none", note="No 3D body written. Set component_bodies[].overall_height to the real part height, or pass auto_3d_body:true for a flagged 1.0 mm placeholder.")
            summaries.append(summary)
            for index, track in enumerate(component.get("tracks", [])):
                if "overlay" not in track.get("layer", "").casefold():
                    continue
                for pad in pads:
                    # Segment-to-rectangle proximity, expanded by half the
                    # silk width; includes endpoints and edge crossings.
                    half = track.get("width", 0) / 2
                    px1, px2 = pad.get("x", 0) - pad.get("width", 0) / 2 - half, pad.get("x", 0) + pad.get("width", 0) / 2 + half
                    py1, py2 = pad.get("y", 0) - pad.get("height", 0) / 2 - half, pad.get("y", 0) + pad.get("height", 0) / 2 + half
                    ax, ay, bx, by = (track.get(k, 0) for k in ("x1", "y1", "x2", "y2"))
                    low, high = 0.0, 1.0
                    for origin, delta, minimum, maximum in ((ax, bx-ax, px1, px2), (ay, by-ay, py1, py2)):
                        if delta == 0:
                            if origin < minimum or origin > maximum:
                                low, high = 1, 0
                        else:
                            t1, t2 = (minimum-origin)/delta, (maximum-origin)/delta
                            low, high = max(low, min(t1, t2)), min(high, max(t1, t2))
                    if low <= high:
                        warnings.append({"component": component["name"], "type": "silk-on-pad", "track_index": index,
                                         "pad": pad.get("designator", ""), "message": "Overlay track overlaps a pad"})
        return summaries, warnings

    @staticmethod
    def _geometry(component):
        pins, points = [], []
        directions = {"right": (1, 0), "left": (-1, 0), "up": (0, 1), "down": (0, -1)}
        for pin in component.get("pins", []):
            dx, dy = directions.get(pin.get("orientation", "right"), (1, 0))
            x, y, length = pin.get("x", 0), pin.get("y", 0), pin.get("length", 30)
            end = {"x": x + dx * length, "y": y + dy * length}
            pins.append({"designator": pin.get("designator", ""), "name": pin.get("name", ""),
                         "orientation": pin.get("orientation", "right"), "body_end": {"x": x, "y": y}, "tip": end})
            points.extend([(x, y), (end["x"], end["y"])])
        for records in (component.get(k, []) for k in SCH_KINDS if k not in ("pins", "parameters", "footprints")):
            for record in records:
                for xkey, ykey in (("x", "y"), ("x1", "y1"), ("x2", "y2")):
                    if xkey in record and ykey in record:
                        points.append((record[xkey], record[ykey]))
        box = {"min_x": min(x for x, _ in points), "max_x": max(x for x, _ in points),
               "min_y": min(y for _, y in points), "max_y": max(y for _, y in points)} if points else None
        return {"name": component["name"], "pins": pins, "bounding_box": box}

    def _mutate(self, name, a, path, kind, components):
        original = copy.deepcopy(components)
        dry = a.get("dry_run", False)
        result = {"status": "dry_run" if dry else "success", "filepath": str(path), "file_type": kind}
        if "dry_run" in a or name in ("copy_component", "rename_component", "delete_component", "bulk_rename", "batch_update"):
            result["dry_run"] = dry
        if name == "delete_component":
            removed, outcomes = set(), []
            for candidate in a["component_names"]:
                found = next((c for c in components if c["name"].casefold() == candidate.casefold()), None)
                if found and found["name"].casefold() not in removed:
                    removed.add(found["name"].casefold())
                    outcomes.append({"name": candidate, "status": "would_delete" if dry else "deleted"})
                else:
                    outcomes.append({"name": candidate, "status": "not_found"})
            components[:] = [c for c in components if c["name"].casefold() not in removed]
            result.update(results=outcomes, deleted_count=len(removed), original_count=len(original), remaining_count=len(components))
        elif name in ("copy_component", "rename_component"):
            source = a["source_name"] if name == "copy_component" else a["old_name"]
            target = a["target_name"] if name == "copy_component" else a["new_name"]
            component = _find(components, source)
            if any(c["name"].casefold() == target.casefold() and (name == "copy_component" or c is not component) for c in components):
                raise ValueError(f"Component '{target}' already exists")
            if name == "copy_component":
                component = _identity_copy(component)
                components.append(component)
                if "description" in a:
                    component["description"] = a["description"]
                result.update(source_name=source, target_name=target, component_count_after=len(components),
                              message=f"{'Would copy' if dry else 'Copied'} '{source}' to '{target}'")
            else:
                result.update(old_name=source, new_name=target, message=f"{'Would rename' if dry else 'Renamed'} '{source}' to '{target}'")
            component["name"] = target
        elif name == "bulk_rename":
            expression = re.compile(a["pattern"])
            replacement = re.sub(r"\$(\d+)", r"\\g<\1>", a["replacement"])
            renames = []
            for component in components:
                new_name = expression.sub(replacement, component["name"])
                if new_name != component["name"]:
                    renames.append({"from": component["name"], "to": new_name})
                    component["name"] = new_name
            result.update(pattern=a["pattern"], replacement=a["replacement"], renames=renames, renamed_count=len(renames))
        elif name == "reorder_components":
            order, missing = [], []
            for candidate in a["component_order"]:
                found = next((c for c in components if c["name"].casefold() == candidate.casefold()), None)
                if found is None:
                    missing.append(candidate)
                elif found not in order:
                    order.append(found)
            tail = [c for c in components if c not in order]
            components[:] = order + tail
            result.update(original_order=[c["name"] for c in original], new_order=[c["name"] for c in components],
                          appended_at_end=[c["name"] for c in tail], not_in_library=missing, component_count=len(components),
                          message=f"Reordered {len(components)} components in '{path}' ({len(tail)} components appended at end)")
        elif name == "update_component":
            old = _find(components, a["component_name"])
            field = "footprint" if kind == "PcbLib" else "symbol"
            if field not in a:
                raise ValueError(f"{field} is required for .{kind}")
            component = copy.deepcopy(old)
            component.clear()
            component.update(copy.deepcopy(a[field]))
            component.setdefault("name", old["name"])
            components[components.index(old)] = component
            changes = [f"{d['property']}: {d.get('a')!r} -> {d.get('b')!r}" for d in _differences(_semantic(old), _semantic(component), 0.001)]
            result.update(component_name=a["component_name"], new_name=component["name"],
                          would_rename=old["name"] != component["name"], changes=changes,
                          message=f"{'Would update' if dry else 'Updated'} component '{a['component_name']}'")
        elif name in ("update_pad", "update_primitive"):
            if kind != "PcbLib":
                raise ValueError(f"{name} only supports .PcbLib")
            component = _find(components, a["component_name"])
            if name == "update_pad":
                primitive = next((p for p in component.get("pads", []) if p.get("designator", "").casefold() == a["designator"].casefold()), None)
                if primitive is None:
                    raise ValueError(f"Pad '{a['designator']}' not found")
                valid = {"x", "y", "width", "height", "shape", "hole_size", "rotation"}
                result["designator"] = a["designator"]
            else:
                kind_name = a["primitive_type"]
                plural = "text" if kind_name == "text" else kind_name + "s"
                records = component.get(plural, [])
                if a["index"] >= len(records):
                    raise ValueError(f"{kind_name} index {a['index']} out of range ({len(records)} records)")
                primitive = records[a["index"]]
                valid = {"track": {"x1", "y1", "x2", "y2", "width", "layer"},
                         "arc": {"x", "y", "x1", "y1", "radius", "start_angle", "end_angle", "width", "layer"},
                         "text": {"x", "y", "height", "rotation", "text", "layer"},
                         "fill": {"x", "y", "x1", "y1", "x2", "y2", "rotation", "layer"},
                         "region": {"layer"}, "via": {"x", "y", "diameter", "hole_size", "from_layer", "to_layer"}}[kind_name]
                result.update(primitive_type=kind_name, index=a["index"])
            unknown = set(a["updates"]) - valid
            if unknown:
                raise ValueError("Unsupported update properties: " + ", ".join(sorted(unknown)))
            before = copy.deepcopy(primitive)
            updates = copy.deepcopy(a["updates"])
            if name == "update_primitive" and a["primitive_type"] in ("arc", "fill"):
                for old, new in (("x1", "x"), ("y1", "y")):
                    if old in updates:
                        updates[new] = updates.pop(old)
            if "layer" in updates and updates["layer"] != primitive.get("layer"):
                primitive.pop("raw_layer_id", None)
                primitive.pop("v7_layer", None)
            followed = self._stack_update(primitive, updates)
            primitive.update(updates)
            result.update(component_name=a["component_name"], changes=_differences(_semantic(before), _semantic(primitive), 0),
                          before=before, after=primitive)
            if followed:
                result["stack_layers_updated"] = followed
        elif name.startswith("manage_schlib_"):
            if kind != "SchLib":
                raise ValueError(f"{name} only supports .SchLib")
            component = _find(components, a["component_name"])
            return self._manage(name, a, path, components, component)
        elif name == "batch_update":
            result.update(self._batch(a, kind, components))
        else:
            raise ValueError(f"Unknown operation: {name}")
        _unique(components)
        if not dry and components != original:
            _, backup = self._write(path, components, auto_designator=False)
            if backup:
                result["backup"] = backup
            result["validation"] = self._validation(components, kind, authoring=True)
        return result

    @staticmethod
    def _stack_update(primitive, updates):
        followed = 0
        mode = primitive.get("diameter_stack_mode", primitive.get("stack_mode", "simple"))
        if mode == "simple":
            return 0
        for entry in primitive.get("per_layer_sizes", []):
            if isinstance(entry, dict):
                for field in ("width", "height"):
                    if field in updates and entry.get(field) == primitive.get(field):
                        entry[field] = updates[field]
                        followed += 1
        # Tables observed in the public schema use scalar arrays or dictionaries.
        fields = {"width": ("per_layer_widths",), "height": ("per_layer_heights",),
                  "shape": ("per_layer_shapes",), "diameter": ("per_layer_diameters",)}
        for field, tables in fields.items():
            if field not in updates:
                continue
            old = primitive.get(field)
            for table in tables:
                values = primitive.get(table)
                if isinstance(values, list):
                    for index, value in enumerate(values):
                        if value == old:
                            values[index] = updates[field]
                            followed += 1
                elif isinstance(values, dict):
                    for layer, value in values.items():
                        if value == old:
                            values[layer] = updates[field]
                            followed += 1
        return followed

    def _manage(self, name, a, path, components, component):
        parameters = name.endswith("parameters")
        field = "parameters" if parameters else "footprints"
        entries = component.setdefault(field, [])
        operation = a["operation"]
        result = {"status": "success", "filepath": str(path), "component_name": a["component_name"], "operation": operation}
        if operation == "list":
            return dict(result, **{field: entries, "count": len(entries)})
        name_field = "parameter_name" if parameters else "footprint_name"
        if name_field not in a:
            raise ValueError(f"{name_field} is required for {operation}")
        candidate = a[name_field]
        found = next((entry for entry in entries if entry.get("name", "").casefold() == candidate.casefold()), None)
        if operation == "get":
            if found is None:
                raise ValueError(f"Parameter '{candidate}' not found")
            return dict(result, parameter=found)
        if operation in ("delete", "remove"):
            if found is None:
                raise ValueError(f"{name_field} '{candidate}' not found")
            entries.remove(found)
        else:
            if operation == "add" and found is not None:
                raise ValueError(f"{name_field} '{candidate}' already exists")
            if parameters and "value" not in a:
                raise ValueError("value is required for add or set")
            if found is None:
                found = {"name": candidate}
                entries.append(found)
            if parameters:
                found.update({k: a[k] for k in ("value", "hidden", "param_type", "read_only_state", "unique_id", "x", "y") if k in a})
                result["parameter"] = {"name": found["name"], "value": found["value"]}
            else:
                found.update({k: a[k] for k in ("description", "library_path") if k in a})
                if "library_path" in a:
                    self._path(a["library_path"])
                found.setdefault("is_current", not bool(entries[:-1]))
                result["footprint"] = candidate
        if operation in ("delete", "remove"):
            result["parameter" if parameters else "footprint"] = candidate
        _, backup = self._write(path, components)
        result["validation"] = self._validation(components, "SchLib", authoring=True)
        if backup:
            result["backup"] = backup
        return result

    @staticmethod
    def _batch(a, kind, components):
        operation, parameters, dry = a["operation"], a["parameters"], a.get("dry_run", False)
        changes = []
        if operation == "update_parameters":
            if kind != "SchLib":
                raise ValueError("update_parameters requires .SchLib")
            for required in ("param_name", "param_value"):
                if required not in parameters:
                    raise ValueError(f"{required} is required for {operation}")
            expression = re.compile(parameters.get("symbol_filter", ".*"))
            added, updated, symbols = 0, 0, set()
            for component in components:
                if not expression.search(component["name"]):
                    continue
                records = component.setdefault("parameters", [])
                found = next((p for p in records if p.get("name", "").casefold() == parameters["param_name"].casefold()), None)
                if found:
                    if found.get("value") == parameters["param_value"]:
                        continue
                    changes.append({"symbol": component["name"], "old_value": found.get("value", ""),
                                    "new_value": parameters["param_value"], "action": "would_update" if dry else "updated"})
                    found["value"] = parameters["param_value"]
                    updated += 1
                elif parameters.get("add_if_missing", False):
                    records.append({"name": parameters["param_name"], "value": parameters["param_value"]})
                    added += 1
                    changes.append({"symbol": component["name"], "new_value": parameters["param_value"],
                                    "action": "would_add" if dry else "added"})
                else:
                    continue
                symbols.add(component["name"])
            return {"operation": operation, "param_name": parameters["param_name"], "param_value": parameters["param_value"],
                    "updates": changes, "summary": {"parameters_added": added, "parameters_updated": updated,
                                                      "symbols_updated": len(symbols), "total_symbols": len(components)}}
        if kind != "PcbLib":
            raise ValueError(f"{operation} requires .PcbLib")
        required = ("from_width", "to_width") if operation == "update_track_width" else ("from_layer", "to_layer")
        for field in required:
            if field not in parameters:
                raise ValueError(f"{field} is required for {operation}")
        updated = 0
        for component in components:
            count = 0
            if operation == "update_track_width":
                if parameters["to_width"] <= 0:
                    raise ValueError("to_width must be positive")
                for track in component.get("tracks", []):
                    if abs(track.get("width", 0) - parameters["from_width"]) <= parameters.get("tolerance", 0.001):
                        track["width"] = parameters["to_width"]
                        count += 1
            else:
                for plural in PCB_KINDS:
                    for primitive in component.get(plural, []):
                        if primitive.get("layer", "").casefold() == parameters["from_layer"].casefold():
                            primitive["layer"] = parameters["to_layer"]
                            primitive.pop("raw_layer_id", None)
                            primitive.pop("v7_layer", None)
                            count += 1
            if count:
                updated += count
                changes.append({"component": component["name"], "primitives_updated": count})
        return {"operation": operation, "updates": changes, "summary": {"components_updated": len(changes),
                "primitives_updated": updated, "total_components": len(components)}}

    @staticmethod
    def _validation(components, kind, data=None, authoring=False):
        issues = []
        def issue(component, text, severity="warning"):
            issues.append({"component": component, "issue": text, "severity": severity})
        all_kinds = PCB_KINDS if kind == "PcbLib" else SCH_KINDS
        for component in components:
            name = component["name"]
            pins = component.get("pads" if kind == "PcbLib" else "pins", [])
            if not pins and not authoring:
                issue(name, "Footprint has no pads" if kind == "PcbLib" else "Symbol has no pins")
            if kind == "SchLib" and pins and not authoring and not any(component.get(k) for k in SCH_KINDS if k not in ("pins", "parameters", "footprints")):
                issue(name, "Symbol has pins but no body graphics (rectangles, lines, etc.)")
            seen = set()
            for pin in pins:
                designator = pin.get("designator", "")
                # Multipart symbols can repeat a designator in different parts.
                key = (designator.casefold(), pin.get("owner_part_id", 1)) if kind == "SchLib" else designator.casefold()
                if key in seen:
                    issue(name, f"Duplicate {'pad' if kind == 'PcbLib' else 'pin'} designator '{designator}'", "error")
                seen.add(key)
            for plural in all_kinds:
                for index, primitive in enumerate(component.get(plural, [])):
                    for field in ("x", "y", "x1", "y1", "x2", "y2", "radius", "width", "height", "length", "diameter", "hole_size"):
                        value = primitive.get(field)
                        if isinstance(value, (int, float)) and not math.isfinite(value):
                            issue(name, f"{plural}[{index}].{field} is not finite", "error")
                        if field in ("width", "height", "length", "diameter", "radius") and isinstance(value, (int, float)) and value <= 0:
                            issue(name, f"{plural}[{index}].{field} must be positive", "error")
                    if plural == "footprints" and len(primitive.get("description", "")) > 256:
                        issue(name, "Footprint description exceeds the Altium 365 import limit of 256 characters")
            if kind == "PcbLib":
                for i, first in enumerate(pins):
                    for second in pins[i + 1:]:
                        if first.get("layer") != second.get("layer") and "Multi-Layer" not in (first.get("layer"), second.get("layer")):
                            continue
                        # Rotated pads need a polygon test rather than an axis-aligned approximation.
                        if first.get("rotation", 0) % 180 or second.get("rotation", 0) % 180:
                            continue
                        if abs(first.get("x", 0) - second.get("x", 0)) < (first.get("width", 0) + second.get("width", 0)) / 2 and abs(first.get("y", 0) - second.get("y", 0)) < (first.get("height", 0) + second.get("height", 0)) / 2:
                            issue(name, f"Pads '{first.get('designator', '')}' and '{second.get('designator', '')}' overlap")
        if kind == "PcbLib" and data:
            models = data.get("embedded_models", {})
            referenced = set()
            for component in components:
                for body in component.get("component_bodies", []):
                    model = body.get("model_id") or body.get("model_guid")
                    if body.get("embedded") and model:
                        referenced.add(model)
                        if model not in models:
                            issue(component["name"], f"Embedded model '{model}' is missing", "error")
            for model in models.keys() - referenced:
                issue("", f"Embedded model '{model}' is not referenced by a footprint")
        errors = sum(i["severity"] == "error" for i in issues)
        return {"status": "invalid" if errors else "warnings" if issues else "valid", "error_count": errors,
                "warning_count": len(issues) - errors, "issues": issues}

    @staticmethod
    def _style(components, kind):
        if kind == "PcbLib":
            tracks, arcs, layers, shapes, heights = defaultdict(list), defaultdict(list), Counter(), Counter(), []
            for component in components:
                for plural in PCB_KINDS:
                    for primitive in component.get(plural, []):
                        layer = "Multi-Layer" if plural == "vias" else primitive.get("layer", "Multi-Layer")
                        layers[layer] += 1
                        if plural == "tracks":
                            tracks[layer].append(primitive.get("width", 0))
                        elif plural == "arcs":
                            arcs[layer].append(primitive.get("width", 0))
                        elif plural == "pads":
                            shapes[primitive.get("shape", "round").replace("_", " ").title()] += 1
                        elif plural == "text":
                            heights.append(primitive.get("height", 0))
            return {"track_widths_by_layer": {k: _stats(v, "mm", True) for k, v in tracks.items()},
                    "arc_widths_by_layer": {k: _stats(v, "mm", True) for k, v in arcs.items()},
                    "layers_used": dict(layers), "pad_shapes": dict(shapes), "text_heights": _stats(heights, "mm", True)}
        pins, widths, line, fill, text, filled, unfilled = [], [], Counter(), Counter(), Counter(), 0, 0
        for component in components:
            for plural in SCH_KINDS:
                if plural in ("parameters", "footprints"):
                    continue
                for primitive in component.get(plural, []):
                    if plural == "pins":
                        pins.append(primitive.get("length", 30))
                    if "line_width" in primitive:
                        widths.append(primitive["line_width"])
                    color = primitive.get("line_color", primitive.get("color", primitive.get("colour")))
                    if color is not None:
                        target = text if plural == "labels" else line
                        # Altium COLORREF stores red in the least significant byte.
                        target[f"#{color & 255:02X}{(color >> 8) & 255:02X}{(color >> 16) & 255:02X}"] += 1
                    if "text_color" in primitive:
                        color = primitive["text_color"]
                        text[f"#{color & 255:02X}{(color >> 8) & 255:02X}{(color >> 16) & 255:02X}"] += 1
                    area = primitive.get("area_color", primitive.get("fill_color"))
                    if area is not None:
                        fill[f"#{area & 255:02X}{(area >> 8) & 255:02X}{(area >> 16) & 255:02X}"] += 1
                    if plural == "rectangles":
                        if primitive.get("is_solid", primitive.get("filled", False)):
                            filled += 1
                        else:
                            unfilled += 1
        return {"pin_lengths": _stats(pins, "units"), "line_widths": _stats(widths, "units"),
                "line_colors": dict(line), "fill_colors": dict(fill), "text_colors": dict(text),
                "rectangles": {"filled_count": filled, "unfilled_count": unfilled}}

    def _backups(self, name, a):
        path = self._path(a["filepath"])
        matches = []
        if path.parent.exists():
            for backup in path.parent.iterdir():
                if backup.is_file() and backup.name.startswith(path.name + ".") and backup.name.endswith(".bak"):
                    self._path(str(backup))
                    stat = backup.stat()
                    matches.append({"path": str(backup), "size_bytes": stat.st_size, "modified_unix": int(stat.st_mtime),
                                    "timestamp": backup.name[len(path.name) + 1:-4]})
        matches.sort(key=lambda item: (item["modified_unix"], item["timestamp"]), reverse=True)
        if name == "list_backups":
            return {"filepath": str(path), "backups": matches, "backup_count": len(matches)}
        if a.get("backup_path"):
            backup = self._path(a["backup_path"])
            if backup == path:
                raise ValueError("Backup and target must be different files")
        elif matches:
            backup = Path(matches[0]["path"])
        else:
            raise ValueError(f"No backup files found for '{path.name}'")
        if not backup.is_file():
            raise ValueError("Backup file does not exist")
        original_size = path.stat().st_size if path.exists() else 0
        pre_restore = self._backup(path)
        self._atomic_bytes(path, backup.read_bytes())
        return {"status": "success", "filepath": str(path), "restored_from": str(backup),
                "pre_restore_backup": pre_restore, "original_size_bytes": original_size,
                "backup_size_bytes": backup.stat().st_size}

    @staticmethod
    def _atomic_bytes(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _libpkg(self, a):
        path = self._path(a["filepath"])
        if path.suffix.casefold() != ".libpkg":
            raise ValueError("Project file must have .LibPkg extension")
        documents = []
        for value in a["documents"]:
            member = self._path(value)
            _kind(member)
            relative = os.path.relpath(member, path.parent)
            if any(c in relative for c in "\n\r=[]"):
                raise ValueError("Document path contains a project-record delimiter")
            documents.append(relative)
        lines = ["[Design]", "Version=1.0", ""]
        for index, document in enumerate(documents, 1):
            lines.extend([f"[Document{index}]", f"DocumentPath={document}", ""])
        self._backup(path)
        self._atomic_bytes(path, "\r\n".join(lines).encode("utf-8"))
        return {"status": "success", "filepath": str(path), "documents": documents, "count": len(documents),
                "note": "Open in Altium and run Project > Compile Integrated Library to produce the .IntLib."}

    def _search(self, a):
        pattern_type = a.get("pattern_type", "glob")
        expression = re.compile(a["pattern"], re.IGNORECASE) if pattern_type == "regex" else None
        matches, errors, searched = [], [], 0
        for value in a["filepaths"]:
            # Reject path escapes before the best-effort read loop.
            path = self._path(value)
            try:
                components = self._components(path)
                searched += len(components)
                for component in components:
                    candidate = component["name"]
                    if expression.search(candidate) if expression else fnmatch.fnmatchcase(candidate.casefold(), a["pattern"].casefold()):
                        matches.append({"library": str(path), "name": candidate, "type": _kind(path)})
            except (OSError, ValueError) as exc:
                errors.append(f"{path}: Failed to read: {exc}")
        return {"status": "partial" if errors else "success", "pattern": a["pattern"], "pattern_type": pattern_type,
                "matches": matches, "matches_found": len(matches), "components_searched": searched,
                "libraries_searched": len(a["filepaths"]), "errors": errors or None,
                "message": f"Found {len(matches)} matches for '{a['pattern']}' across {len(a['filepaths'])} libraries ({searched} components searched)"}

    def _compare(self, name, a):
        path_a, path_b = self._path(a["filepath_a"]), self._path(a["filepath_b"])
        if _kind(path_a) != _kind(path_b):
            raise ValueError("Both libraries must have the same type")
        kind = _kind(path_a)
        first, second = self._components(path_a), self._components(path_b)
        if name == "compare_components":
            x = _find(first, a["component_a"])
            y = _find(second, a["component_b"])
            sx, sy = _semantic(x), _semantic(y)
            # Component naming identifies the operands rather than geometry.
            sx.pop("name", None)
            sy.pop("name", None)
            differences = _differences(sx, sy, a.get("tolerance", 0.001))
            kinds = PCB_KINDS if kind == "PcbLib" else SCH_KINDS
            summary = {f"{plural}_{side}": len(component.get(plural, [])) for side, component in (("a", x), ("b", y)) for plural in kinds}
            return {"status": "success", "file_type": kind,
                    "component_a": {"filepath": str(path_a), "name": a["component_a"]},
                    "component_b": {"filepath": str(path_b), "name": a["component_b"]},
                    "identical": not differences, "difference_count": len(differences), "differences": differences,
                    "summary": summary}
        x = {c["name"].casefold(): c for c in first}
        y = {c["name"].casefold(): c for c in second}
        added = [c["name"] for c in second if c["name"].casefold() not in x]
        removed = [c["name"] for c in first if c["name"].casefold() not in y]
        modified = []
        for key in x.keys() & y.keys():
            differences = _differences(_semantic(x[key]), _semantic(y[key]), 0.001)
            if differences:
                modified.append({"name": y[key]["name"], "changes": differences})
        return {"status": "success", "filepath_a": str(path_a), "filepath_b": str(path_b), "file_type": kind,
                "added": added, "removed": removed, "modified": modified,
                "summary": {"added_count": len(added), "removed_count": len(removed), "modified_count": len(modified),
                            "unchanged_count": len(x.keys() & y.keys()) - len(modified), "components_in_a": len(first), "components_in_b": len(second)}}

    def _cross(self, name, a):
        target = self._path(a["target_filepath"])
        kind = _kind(target)
        initial = self._read(target) if target.exists() else {}
        components = initial.get(_key(kind), [])
        models = self._models(target, initial) if target.exists() else {}
        header = self._codec(target).read_library_header(target) if kind == "SchLib" and target.exists() else None
        initial_count = len(components)
        warnings = []
        sources = a["source_filepaths"] if name == "merge_libraries" else [a["source_filepath"]]
        dry = a.get("dry_run", False)
        reports = []
        for value in sources:
            source = self._path(value)
            if source == target:
                raise ValueError("Source and target must be different files")
            if _kind(source) != kind:
                raise ValueError("Source and target libraries must have the same type")
            data = self._read(source)
            font_mapping = {}
            if kind == "SchLib":
                source_header = self._codec(source).read_library_header(source)
                header, font_mapping = _merge_font_headers(header, source_header)
            source_models = self._models(source, data) if kind == "PcbLib" else {}
            selected = data.get(_key(kind), [])
            if name == "copy_component_cross_library":
                selected = [_find(selected, a["component_name"])]
            report = {"source": str(source), "merged": 0, "renamed": 0, "skipped": 0}
            for old in selected:
                component = copy.deepcopy(old)
                if font_mapping:
                    component.setdefault("designator_font_id", 1)
                    _remap_fonts(component, font_mapping)
                if name == "copy_component_cross_library":
                    component["name"] = a.get("new_name", component["name"])
                    if "description" in a:
                        component["description"] = a["description"]
                existing = {c["name"].casefold() for c in components}
                if component["name"].casefold() in existing:
                    action = a.get("on_duplicate", "error")
                    if action == "skip":
                        report["skipped"] += 1
                        continue
                    if action == "error":
                        raise ValueError(f"Component '{component['name']}' already exists")
                    original_name, suffix = component["name"], 1
                    while component["name"].casefold() in existing:
                        component["name"] = f"{original_name}_{suffix}"
                        suffix += 1
                    report["renamed"] += 1
                if kind == "PcbLib":
                    if component.get("model_3d") and not any(b.get("embedded") for b in component.get("component_bodies", [])) and not a.get("preserve_external_paths", False):
                        component.pop("model_3d", None)
                        warnings.append(f"Removed external 3D model path from '{component['name']}'")
                    keep_bodies = []
                    for body in component.get("component_bodies", []):
                        model = body.get("model_id") or body.get("model_guid")
                        if model and body.get("embedded") and model not in source_models:
                            if not a.get("ignore_missing_models", False):
                                raise ValueError(f"Referenced embedded model '{model}' is missing")
                            warnings.append(f"Removed missing embedded model reference '{model}'")
                            continue
                        if model and model in source_models:
                            models[model] = source_models[model]
                        keep_bodies.append(body)
                    if "component_bodies" in component:
                        component["component_bodies"] = keep_bodies
                components.append(component)
                report["merged"] += 1
            reports.append(report)
        _unique(components)
        result = {"status": "dry_run" if dry else "success", "file_type": kind, "target_filepath": str(target)}
        if name == "copy_component_cross_library":
            result.update(source_filepath=str(self._path(a["source_filepath"])), component_name=a["component_name"],
                          target_name=a.get("new_name", a["component_name"]), target_component_count=len(components),
                          message=f"Copied '{a['component_name']}' from '{a['source_filepath']}' to '{target}'")
        else:
            result.update(dry_run=dry, initial_count=initial_count, final_count=len(components), sources=reports,
                          sources_count=len(sources), merged_count=sum(r["merged"] for r in reports),
                          renamed_count=sum(r["renamed"] for r in reports), skipped_count=sum(r["skipped"] for r in reports),
                          message=f"{'Would merge' if dry else 'Merged'} {len(components) - initial_count} components from {len(sources)} sources into '{target}' (total: {len(components)})")
        if not dry:
            _, backup = self._write(target, components, embedded_models=list(models.values()),
                                    library_header_params=header, auto_designator=False)
            result["validation"] = self._validation(components, kind, authoring=True)
            if backup:
                result["backup"] = backup
        if warnings:
            result["warnings"] = warnings
        return result

    def _step(self, path, data, a):
        models = self._models(path, data)
        selected = list(models)
        mode = a.get("mode", "auto")
        if mode == "extract_by_footprint":
            if "footprint_name" not in a:
                raise ValueError("footprint_name is required for extract_by_footprint")
            component = _find(data.get("footprints", []), a["footprint_name"])
            wanted = {body.get("model_id") for body in component.get("component_bodies", [])}
            selected = [key for key in selected if key in wanted]
        if a.get("model"):
            selected = [key for key in selected if key.casefold() == a["model"].casefold() or
                        (isinstance(models[key], dict) and str(models[key].get("name", "")).casefold() == a["model"].casefold())]
            if not selected:
                raise ValueError(f"Model '{a['model']}' not found")
        entries = []
        for key in selected:
            value = models[key]
            raw, model_name = value["data"], value["name"]
            payload = base64.b64encode(raw).decode("ascii")
            entries.append({"id": key, "name": model_name, "size_bytes": len(raw), "data_base64": payload})
        if mode == "list" or (mode == "auto" and len(entries) != 1):
            offset = a.get("offset", 0)
            chunk = entries[offset:offset + a.get("limit", len(entries))]
            return {"status": "list", "filepath": str(path),
                    "models": [{k: v for k, v in entry.items() if k != "data_base64"} for entry in chunk],
                    "total_count": len(entries), "returned_count": len(chunk), "offset": offset,
                    "has_more": offset + len(chunk) < len(entries)}
        if not entries:
            raise ValueError("No embedded STEP models found")
        if not a.get("output_path"):
            if mode == "extract_all":
                raise ValueError("output_path directory is required for extract_all")
            if len(entries) != 1:
                raise ValueError("output_path directory is required when extracting multiple models")
            entry = entries[0]
            return {"status": "success", "filepath": str(path), "model_id": entry["id"], "model_name": entry["name"],
                    "size_bytes": entry["size_bytes"], "data": entry["data_base64"], "encoding": "base64"}
        output = self._path(a["output_path"])
        outputs = []
        used_names = set()
        for entry in entries:
            if mode == "auto":
                target = output
            else:
                safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(entry["name"]).name)
                if safe_name in ("", ".", ".."):
                    safe_name = entry["id"] + ".step"
                if safe_name.casefold() in used_names:
                    name_path = Path(safe_name)
                    safe_name = name_path.stem + "_" + re.sub(r"[^A-Za-z0-9]", "", entry["id"]) + (name_path.suffix or ".step")
                used_names.add(safe_name.casefold())
                target = self._path(str(output / safe_name))
            self._atomic_bytes(target, base64.b64decode(entry["data_base64"], validate=True))
            outputs.append({"id": entry["id"], "name": entry["name"], "output_path": str(target), "size_bytes": entry["size_bytes"]})
        if mode == "auto":
            entry = outputs[0]
            return {"status": "success", "filepath": str(path), "model_id": entry["id"], "model_name": entry["name"],
                    "output_path": entry["output_path"], "size_bytes": entry["size_bytes"],
                    "message": f"STEP model extracted to '{entry['output_path']}'"}
        return {"status": "success", "filepath": str(path), "extracted_count": len(outputs), "extracted": outputs,
                "error_count": 0, "errors": [], "output_directory": str(output), "total_models": len(entries)}

    def _repair(self, path, data, components, a):
        models = self._models(path, data)
        missing, referenced = [], set()
        for component in components:
            bodies = []
            for body in component.get("component_bodies", []):
                model = body.get("model_id")
                if model and body.get("embedded"):
                    if model not in models:
                        missing.append({"component": component["name"], "model_id": model})
                        continue
                    referenced.add(model)
                bodies.append(body)
            if "component_bodies" in component:
                component["component_bodies"] = bodies
        orphaned = [key for key in models if key not in referenced]
        for key in orphaned:
            del models[key]
        dry = a.get("dry_run", False)
        after_bodies = sum(len(component.get("component_bodies", [])) for component in components)
        result = {"status": "dry_run" if dry else "success", "filepath": str(path), "dry_run": dry,
                  "before": {"model_count": len(models) + len(orphaned), "total_component_bodies": after_bodies + len(missing)},
                  "after": {"model_count": len(models), "total_component_bodies": after_bodies},
                  "repairs": {"affected_footprints": list(dict.fromkeys(item["component"] for item in missing)),
                              "orphaned_models_removed": len(orphaned), "orphaned_component_bodies_removed": len(missing)},
                  "message": "No repairs needed - library is clean" if not (orphaned or missing) else
                             f"{'Would remove' if dry else 'Removed'} {len(orphaned)} orphaned models and {len(missing)} missing model references"}
        if not dry and (orphaned or missing):
            _, backup = self._write(path, components, embedded_models=list(models.values()),
                                    replace_embedded_models=True, auto_designator=False)
            if backup:
                result["backup"] = backup
        return result

    @staticmethod
    def _render(component, kind, options):
        scale = options.get("scale", 2.0 if kind == "PcbLib" else 1.0)
        if scale <= 0:
            raise ValueError("scale must be positive")
        if kind == "SchLib":
            scale /= 10.0
        records = []
        kinds = PCB_KINDS if kind == "PcbLib" else SCH_KINDS
        for plural in kinds:
            if plural in ("parameters", "footprints"):
                continue
            for primitive in component.get(plural, []):
                if kind == "SchLib" and options.get("part_id", 1) and primitive.get("owner_part_id", 1) not in (-1, 0, options.get("part_id", 1)):
                    continue
                records.append((plural, primitive))
        points = [(0.0, 0.0)]
        for plural, record in records:
            x, y = record.get("x", record.get("x1", 0)), record.get("y", record.get("y1", 0))
            if plural == "pins":
                dx, dy = {"right": (1, 0), "left": (-1, 0), "up": (0, 1), "down": (0, -1)}.get(record.get("orientation", "right"), (1, 0))
                points += [(x, y), (x + dx * record.get("length", 30), y + dy * record.get("length", 30))]
            elif plural in ("pads", "vias"):
                width, height = record.get("width", record.get("diameter", 0)), record.get("height", record.get("diameter", 0))
                points += [(x - width / 2, y - height / 2), (x + width / 2, y + height / 2)]
            elif plural in ("arcs", "pies", "ellipses", "elliptical_arcs"):
                radius = record.get("radius", record.get("radius_x", 0))
                ry = record.get("secondary_radius", record.get("radius_y", radius))
                points += [(x - radius, y - ry), (x + radius, y + ry)]
            else:
                points.extend([(x, y), (record.get("x2", x), record.get("y2", y))])
                for point in record.get("vertices", record.get("points", record.get("outline", []))):
                    points.append((point.get("x", 0), point.get("y", 0)) if isinstance(point, dict) else (point[0], point[1]))
                if plural == "beziers":
                    points.extend((record.get(f"x{index}", 0), record.get(f"y{index}", 0)) for index in (3, 4))
        min_x, max_x = min(x for x, y in points), max(x for x, y in points)
        min_y, max_y = min(y for x, y in points), max(y for x, y in points)
        available_w, available_h = max(1, options.get("max_width", 80) - 2), max(1, options.get("max_height", 40) - 2)
        if max_x > min_x:
            scale = min(scale, max(1, available_w - 4) / (max_x - min_x))
        if max_y > min_y:
            scale = min(scale, max(1, available_h - 4) / (max_y - min_y))
        width = min(available_w, max(1, int(math.ceil((max_x - min_x) * scale)) + 5))
        height = min(available_h, max(1, int(math.ceil((max_y - min_y) * scale)) + 5))
        canvas = [[" " for _ in range(width)] for _ in range(height)]
        def pixel(x, y, marker):
            px = int(round((x - min_x) * scale)) + 2
            py = int(round((max_y - y) * scale)) + 2
            if 0 <= px < width and 0 <= py < height:
                canvas[py][px] = marker
        def line(x1, y1, x2, y2, marker):
            steps = min(100000, max(1, int(max(abs(x2 - x1), abs(y2 - y1)) * scale * 2)))
            for step in range(steps + 1):
                fraction = step / steps
                pixel(x1 + (x2 - x1) * fraction, y1 + (y2 - y1) * fraction, marker)
        pixel(0, 0, "+")
        markers = {"pads": "#", "vias": "O", "tracks": "*", "lines": "*", "pins": "~", "arcs": "o",
                   "elliptical_arcs": "o", "pies": "o", "ellipses": "O", "text": "T", "labels": "T",
                   "fills": "=", "regions": ":", "rectangles": "+", "round_rects": "(", "polylines": "*",
                   "polygons": ":", "component_bodies": "B", "images": ":", "text_frames": "=", "beziers": "~", "ieee_symbols": "@"}
        for plural, record in records:
            marker = markers[plural]
            x, y = record.get("x", record.get("x1", 0)), record.get("y", record.get("y1", 0))
            if plural == "pins":
                dx, dy = {"right": (1, 0), "left": (-1, 0), "up": (0, 1), "down": (0, -1)}.get(record.get("orientation", "right"), (1, 0))
                line(x, y, x + dx * record.get("length", 30), y + dy * record.get("length", 30), marker)
                pixel(x, y, str(record.get("designator", "~"))[:1] or "~")
            elif plural in ("tracks", "lines"):
                line(x, y, record.get("x2", x), record.get("y2", y), marker)
            elif plural == "beziers":
                control = [(record.get(f"x{index}", 0), record.get(f"y{index}", 0)) for index in (1, 2, 3, 4)]
                last = control[0]
                for step in range(1, 101):
                    t = step / 100
                    point = tuple((1-t)**3 * control[0][axis] + 3*(1-t)**2*t*control[1][axis] +
                                  3*(1-t)*t*t*control[2][axis] + t**3*control[3][axis] for axis in (0, 1))
                    line(*last, *point, marker)
                    last = point
            elif plural in ("arcs", "pies", "ellipses", "elliptical_arcs"):
                radius = record.get("radius", record.get("radius_x", 0))
                ry = record.get("secondary_radius", record.get("radius_y", radius))
                start, end = record.get("start_angle", 0), record.get("end_angle", 360)
                if end <= start:
                    end += 360
                for step in range(181):
                    angle = math.radians(start + (end - start) * step / 180)
                    pixel(x + radius * math.cos(angle), y + ry * math.sin(angle), marker)
            elif plural in ("pads", "vias"):
                w, h = record.get("width", record.get("diameter", 0)), record.get("height", record.get("diameter", 0))
                for row in range(max(1, int(h * scale)) + 1):
                    line(x - w / 2, y - h / 2 + row / max(scale, 1e-12), x + w / 2, y - h / 2 + row / max(scale, 1e-12), marker)
                pixel(x, y, str(record.get("designator", marker))[:1] or marker)
            elif plural in ("fills", "rectangles", "round_rects", "images", "text_frames"):
                x2, y2 = record.get("x2", x), record.get("y2", y)
                for x1, y1, xe, ye in ((x, y, x2, y), (x2, y, x2, y2), (x2, y2, x, y2), (x, y2, x, y)):
                    line(x1, y1, xe, ye, marker)
            elif record.get("vertices", record.get("points", record.get("outline"))):
                vertices = record.get("vertices", record.get("points", record.get("outline", [])))
                pairs = [(v["x"], v["y"]) if isinstance(v, dict) else (v[0], v[1]) for v in vertices]
                for first, second in zip(pairs, pairs[1:] + (pairs[:1] if plural in ("regions", "polygons", "component_bodies") else [])):
                    line(*first, *second, marker)
            else:
                pixel(x, y, marker)
        title = f"Footprint: {component['name']}" if kind == "PcbLib" else f"Symbol: {component['name']} (part {options.get('part_id', 1)}/{component.get('part_count', 1)})"
        counts = ", ".join(f"{plural.title()}: {sum(k == plural for k, r in records)}" for plural in kinds if plural not in ("parameters", "footprints"))
        border = "-" * (width + 2)
        legend = "Legend: " + ", ".join(f"{marker} = {plural}" for plural, marker in markers.items() if plural in kinds)
        return "\n".join([title, counts, border] + ["|" + "".join(row) + "|" for row in canvas] + [border, legend, ""])


_default_service = None


def call_tool(name, arguments=None):
    global _default_service
    if _default_service is None:
        roots = os.environ.get("ALTIUM_SUITE_ALLOWED_PATHS", "")
        _default_service = NativeLibraries(allowed_paths=[p for p in roots.split(os.pathsep) if p])
    return _default_service.call_tool(name, arguments)


async def call_tool_async(name, arguments=None):
    return await asyncio.to_thread(call_tool, name, arguments)
