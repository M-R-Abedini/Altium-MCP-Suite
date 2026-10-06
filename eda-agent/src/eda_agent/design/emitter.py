# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""SchematicCanvas -> Altium, one-shot batched IPC.

The emitter is intentionally dumb: it makes zero layout decisions and
zero geometry computations. Everything it writes was decided by the
pipeline / canvas. If the canvas has 9 components, 24 wires, 5 power
ports, the emitter makes one IPC call per kind to push them all to
Altium under one PreProcess/PostProcess each.

Compare to the legacy executor.py path: that file interleaves layout
decisions (where pin X is, what stub length to use, whether to consolidate
ports) with Altium IPC calls. The new pipeline does all of that in pure
Python; this module is just the transcription stage.

Failure modes the emitter surfaces (not hides):
- Project create/open failure -> stop, return result with `ok=False`.
- Sheet create/open failure -> stop, return result with `ok=False`.
- Bulk place reported a failed_refdes -> per-refdes EmitFailure record.
- Any bulk wire/label/port call raises -> note added, emit continues so
  partial-success is visible rather than silent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from eda_agent.design._wiring import _sheet_path
from eda_agent.bridge.payload import payload_safe
from eda_agent.design.canvas import SchematicCanvas, SymbolInstance

logger = logging.getLogger("eda_agent.design.emitter")


# Per-call timeouts. Generous because the first place call into a fresh
# SchLib can take 20-30s to populate the editor's component cache.
_PLACE_TIMEOUT_S = 60.0
_PARAM_TIMEOUT_S = 30.0
_BULK_TIMEOUT_S = 30.0
_SAVE_TIMEOUT_S = 60.0


@dataclass
class EmitFailure:
    """One Altium-reported failure during transcription."""

    refdes: str
    code: str
    reason: str


@dataclass
class EmitResult:
    project_path: str = ""
    ok: bool = True
    sheets_emitted: list[str] = field(default_factory=list)
    placed_refdes: list[str] = field(default_factory=list)
    wires_emitted: int = 0
    labels_emitted: int = 0
    power_ports_emitted: int = 0
    junctions_emitted: int = 0
    buses_emitted: int = 0
    bus_entries_emitted: int = 0
    failures: list[EmitFailure] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def emit_canvas(
    canvas: SchematicCanvas,
    project_path: str,
    bridge: Any,
    *,
    parameter_stamps: Optional[dict[str, dict[str, str]]] = None,
) -> EmitResult:
    """Transcribe a SchematicCanvas to a fresh-or-reused Altium project.

    Args:
        canvas: The fully populated SchematicCanvas from pipeline.
        project_path: Absolute path to the target .PrjPcb. Created if
            it does not exist.
        bridge: AltiumBridge (or anything exposing send_command).
        parameter_stamps: Optional {refdes: {param_name: value}} to stamp
            on each placed component after placement. Useful for
            Value / Manufacturer / MPN / Datasheet binding.

    Returns:
        EmitResult with one entry per sheet emitted and per failed refdes.
    """
    result = EmitResult(project_path=project_path)
    project = Path(project_path)

    _open_or_create_project(project, bridge, result)
    if not result.ok:
        return result

    for sheet in canvas.sheets:
        _emit_sheet(canvas, sheet.name, project, bridge, result,
                    parameter_stamps=parameter_stamps)

    try:
        bridge.send_command("application.save_all", {}, timeout=_SAVE_TIMEOUT_S)
        result.notes.append("save_all completed")
    except Exception as exc:
        result.ok = False
        result.notes.append(f"save_all failed: {exc}")

    return result


def _open_or_create_project(
    project: Path, bridge: Any, result: EmitResult
) -> None:
    """Open the .PrjPcb if it already exists, else create it."""
    if project.exists():
        try:
            bridge.send_command("project.open", {"project_path": str(project)})
            result.notes.append(f"Opened project: {project}")
        except Exception as exc:
            result.ok = False
            result.notes.append(f"project.open failed: {exc}")
        return
    try:
        bridge.send_command(
            "project.create",
            {"project_path": str(project), "project_type": "PCB"},
        )
        result.notes.append(f"Created project: {project}")
    except Exception as exc:
        result.ok = False
        result.notes.append(f"project.create failed: {exc}")


def _ensure_sheet_loaded(
    project: Path, sheet_name: str, bridge: Any, result: EmitResult
) -> Optional[Path]:
    """Open the sheet at its canonical path or create it; return the path."""
    sheet_path = _sheet_path(project, sheet_name)
    if sheet_path.exists():
        try:
            bridge.send_command(
                "application.run_process",
                {
                    "process_name": "WorkspaceManager:OpenObject",
                    "parameters": "ObjectKind=Document|FileName=" + str(sheet_path),
                },
            )
            # Says what was REQUESTED, not what happened. App_RunProcess
            # fires Altium's RunProcess and answers success
            # unconditionally, because RunProcess reports no status, so
            # "loaded" would be asserted on no evidence and a failed load
            # produced two contradictory notes. The outcome is checked one
            # step later: set_active_document refuses a document that is
            # not loaded (NOT_LOADED), which aborts this sheet.
            result.notes.append(f"Requested load of existing sheet: {sheet_path}")
        except Exception as exc:
            result.ok = False
            result.notes.append(f"OpenObject failed for {sheet_name}: {exc}")
            return None
        return sheet_path
    try:
        bridge.send_command(
            "application.create_document",
            {
                "kind": "SCH",
                "file_path": str(sheet_path),
                "name": sheet_name,
                "add_to_project": "true",
            },
        )
        result.notes.append(f"Created sheet: {sheet_path}")
    except Exception as exc:
        result.ok = False
        result.notes.append(f"create_document failed for {sheet_name}: {exc}")
        return None
    # create_document's add_to_project=true attaches the new doc to whatever
    # is the currently FOCUSED project. Right after project.create, the
    # focused project may still be "Free Documents" (or a previously open
    # project), so the new sheet ends up orphaned. Explicitly attach it
    # to OUR target project to guarantee membership; the call is a no-op
    # if the doc is already a member.
    try:
        bridge.send_command(
            "project.add_document",
            {
                "document_path": str(sheet_path),
                "project_path": str(project),
            },
        )
    except Exception as exc:
        # Not fatal -- the sheet is on disk; project.add_document may
        # have other names across Altium versions. Surface the warning
        # but keep going; the user can manually attach if needed.
        result.notes.append(
            f"explicit project.add_document failed for {sheet_name} -> "
            f"{project}: {exc}. If the sheet shows as 'Free Documents', "
            f"add it to the project manually or check the bridge handler."
        )
    return sheet_path


def _emit_sheet(
    canvas: SchematicCanvas,
    sheet_name: str,
    project: Path,
    bridge: Any,
    result: EmitResult,
    *,
    parameter_stamps: Optional[dict[str, dict[str, str]]] = None,
) -> None:
    """Emit one sheet's worth of components, wires, labels, ports, junctions."""
    sheet_path = _ensure_sheet_loaded(project, sheet_name, bridge, result)
    if sheet_path is None:
        return
    try:
        bridge.send_command(
            "application.set_active_document", {"file_path": str(sheet_path)}
        )
        result.sheets_emitted.append(sheet_name)
    except Exception as exc:
        result.notes.append(f"set_active_document {sheet_name} failed: {exc}")
        return

    # 1. Bulk place components.
    instances = canvas.instances_on(sheet_name)
    if instances:
        _emit_placements(instances, bridge, result)
    # 2. Stamp Value / Manufacturer / MPN / Footprint / etc.
    if instances and parameter_stamps:
        _emit_parameter_stamps(
            instances, parameter_stamps, sheet_path, bridge, result
        )
        _emit_text_positions(instances, sheet_path, bridge, result)
    # 3. Bulk wires.
    wires = canvas.wires_on(sheet_name)
    if wires:
        _emit_wires(wires, bridge, result, sheet_name)
    # 3b. Buses + bus entries (before labels, so the bus exists when the
    # per-signal net labels are placed on the entries' wire stubs).
    buses = canvas.buses_on(sheet_name)
    if buses:
        _emit_buses(buses, bridge, result, sheet_name)
    bus_entries = canvas.bus_entries_on(sheet_name)
    if bus_entries:
        _emit_bus_entries(bus_entries, bridge, result, sheet_name)
    # 4. Bulk junctions (after wires).
    junctions = canvas.junctions_on(sheet_name)
    if junctions:
        _emit_junctions(junctions, bridge, result, sheet_name)
    # 5. Bulk labels.
    labels = canvas.labels_on(sheet_name)
    if labels:
        _emit_labels(labels, bridge, result, sheet_name)
    # 6. Bulk power ports.
    ports = canvas.power_ports_on(sheet_name)
    if ports:
        _emit_power_ports(ports, bridge, result, sheet_name)


def _emit_placements(
    instances: list[SymbolInstance], bridge: Any, result: EmitResult
) -> None:
    ops: list[str] = []
    for inst in instances:
        ops.append(
            f"library_path={inst.symbol.lib_path};"
            f"lib_reference={inst.symbol.lib_ref};"
            f"x={inst.x};y={inst.y};"
            f"designator={inst.refdes};"
            f"rotation={inst.rotation};"
            f"footprint="
        )
    try:
        resp = bridge.send_command(
            "generic.place_sch_components_from_library",
            {"placements": "~~".join(ops)},
            timeout=_PLACE_TIMEOUT_S * max(1, len(ops) // 4),
        )
    except Exception as exc:
        for inst in instances:
            result.failures.append(EmitFailure(
                refdes=inst.refdes, code="PLACE_FAILED",
                reason=f"bulk place raised: {exc}",
            ))
        return
    # Parse Pascal's failed_refdes (bug #86 fix). Pascal returns
    # "DESIG:CODE,DESIG:CODE" for any place op that silently skipped.
    failed_map: dict[str, str] = {}
    if isinstance(resp, dict):
        raw = str(resp.get("failed_refdes", "") or "")
        for entry in raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" in entry:
                rd, code = entry.split(":", 1)
                failed_map[rd.strip()] = code.strip() or "PLACE_FAILED"
            else:
                failed_map[entry] = "PLACE_FAILED"
    for inst in instances:
        if inst.refdes in failed_map:
            result.failures.append(EmitFailure(
                refdes=inst.refdes,
                code=failed_map[inst.refdes],
                reason=(
                    f"Pascal place handler skipped "
                    f"lib_ref={inst.symbol.lib_ref!r} from "
                    f"lib_path={inst.symbol.lib_path!r}"
                ),
            ))
        else:
            result.placed_refdes.append(inst.refdes)


def _emit_parameter_stamps(
    instances: list[SymbolInstance],
    parameter_stamps: dict[str, dict[str, str]],
    sheet_path: Path,
    bridge: Any,
    result: EmitResult,
) -> None:
    ops: list[str] = []
    for inst in instances:
        stamps = parameter_stamps.get(inst.refdes)
        if not stamps:
            continue
        # A third variant of the payload sanitiser used to live here: it
        # substituted ";" but not "~~", and left the designator raw. A
        # plan-authored parameter value carrying "~~" would therefore
        # end its operation early and forge an extra stamp -- silently,
        # because the payload stays syntactically valid. One shared rule
        # now, from the bridge layer that owns the grammar.
        fields = [f"designator={payload_safe(inst.refdes)}"]
        for k, v in stamps.items():
            if not k or v is None:
                continue
            vs = str(v).strip()
            if not vs:
                continue
            fields.append(f"{payload_safe(k)}={payload_safe(vs)}")
        if len(fields) > 1:
            ops.append(";".join(fields))
    if not ops:
        return
    try:
        bridge.send_command(
            "generic.set_sch_components_parameters",
            {"stamps": "~~".join(ops), "sheet_path": str(sheet_path)},
            timeout=_PARAM_TIMEOUT_S * max(1, len(ops) // 8),
        )
    except Exception as exc:
        result.notes.append(f"parameter stamp pass failed: {exc}")
        return

    # Discipline rule 16: Manufacturer, Manufacturer Part Number, and
    # Datasheet must be invisible on the schematic body by default --
    # only Designator + Value are user-facing. The stamping API creates
    # the parameters with default visibility, so we sweep the placed
    # sheet right after stamping and flip IsHidden=true on those three
    # parameter names across every component.
    _DISCIPLINE_HIDDEN_PARAMS = (
        "Manufacturer",
        "Manufacturer Part Number",
        "Datasheet",
    )
    for param_name in _DISCIPLINE_HIDDEN_PARAMS:
        try:
            bridge.send_command(
                "generic.modify_objects",
                {
                    "object_type": "eParameter",
                    "scope": f"doc:{sheet_path}",
                    "filter": f"Name={param_name}",
                    "set": "IsHidden=true",
                },
                timeout=_PARAM_TIMEOUT_S,
            )
        except Exception as exc:
            result.notes.append(
                f"hide-{param_name} pass failed: {exc}"
            )


def _emit_wires(
    wires: list, bridge: Any, result: EmitResult, sheet_name: str
) -> None:
    payload = "~~".join(
        f"x1={w.x1};y1={w.y1};x2={w.x2};y2={w.y2}" for w in wires
    )
    try:
        bridge.send_command(
            "generic.place_wires",
            {"wires": payload},
            timeout=_BULK_TIMEOUT_S * max(1, len(wires) // 8),
        )
        result.wires_emitted += len(wires)
    except Exception as exc:
        result.notes.append(f"place_wires for {sheet_name} failed: {exc}")


def _emit_junctions(
    junctions: list, bridge: Any, result: EmitResult, sheet_name: str
) -> None:
    payload = "~~".join(f"x={j.x};y={j.y}" for j in junctions)
    try:
        bridge.send_command(
            "generic.place_junctions",
            {"junctions": payload},
            timeout=_BULK_TIMEOUT_S * max(1, len(junctions) // 8),
        )
        result.junctions_emitted += len(junctions)
    except Exception as exc:
        result.notes.append(f"place_junctions for {sheet_name} failed: {exc}")


def _emit_buses(
    buses: list, bridge: Any, result: EmitResult, sheet_name: str
) -> None:
    # The Altium bridge exposes single-object place_bus / place_bus_entry (no
    # bulk variant). Buses are few (one short line per IC bus stub), so the
    # per-object loop is fine. Stop on the first failure so a broken bridge
    # doesn't spam one note per segment.
    for b in buses:
        try:
            bridge.send_command(
                "generic.place_bus",
                {"x1": str(b.x1), "y1": str(b.y1),
                 "x2": str(b.x2), "y2": str(b.y2)},
                timeout=_BULK_TIMEOUT_S,
            )
            result.buses_emitted += 1
        except Exception as exc:
            result.notes.append(f"place_bus for {sheet_name} failed: {exc}")
            break


def _emit_bus_entries(
    entries: list, bridge: Any, result: EmitResult, sheet_name: str
) -> None:
    for e in entries:
        try:
            bridge.send_command(
                "generic.place_bus_entry",
                {"x1": str(e.x1), "y1": str(e.y1),
                 "x2": str(e.x2), "y2": str(e.y2)},
                timeout=_BULK_TIMEOUT_S,
            )
            result.bus_entries_emitted += 1
        except Exception as exc:
            result.notes.append(
                f"place_bus_entry for {sheet_name} failed: {exc}")
            break


def _emit_text_positions(
    instances: list, sheet_path: Path, bridge: Any, result: EmitResult
) -> None:
    """Mirror text_placement's collision-free annotation anchors onto the
    live sheet (Designator / Comment sub-object Locations). Skips
    instances the pass left at defaults."""
    ops: list[str] = []
    for inst in instances:
        dp = getattr(inst, "designator_pos", None)
        if dp is None:
            continue
        fields = [
            f"designator={payload_safe(inst.refdes)}",
            f"dx={dp[0]}", f"dy={dp[1]}",
        ]
        vp = getattr(inst, "value_pos", None)
        if vp is not None:
            fields.append(f"vx={vp[0]}")
            fields.append(f"vy={vp[1]}")
        ops.append(";".join(fields))
    if not ops:
        return
    try:
        bridge.send_command(
            "generic.set_sch_text_positions",
            {"positions": "~~".join(ops), "sheet_path": str(sheet_path)},
            timeout=_PARAM_TIMEOUT_S * max(1, len(ops) // 8),
        )
    except Exception as exc:
        result.notes.append(f"text position pass failed: {exc}")


def _emit_labels(
    labels: list, bridge: Any, result: EmitResult, sheet_name: str
) -> None:
    payload = "~~".join(
        f"text={l.text};x={l.x};y={l.y};orientation={l.orientation}"
        f";justification={getattr(l, 'justification', 0)}"
        for l in labels
    )
    try:
        bridge.send_command(
            "generic.place_net_labels",
            {"labels": payload},
            timeout=_BULK_TIMEOUT_S * max(1, len(labels) // 8),
        )
        result.labels_emitted += len(labels)
    except Exception as exc:
        result.notes.append(f"place_net_labels for {sheet_name} failed: {exc}")


def _emit_power_ports(
    ports: list, bridge: Any, result: EmitResult, sheet_name: str
) -> None:
    # Power-port orientation: VCC up (1), GND down (3), determined per-glyph.
    payload = "~~".join(
        f"text={p.text};x={p.x};y={p.y};style={p.style};orientation={3 if 'gnd' in p.style.lower() else 1}"
        for p in ports
    )
    try:
        bridge.send_command(
            "generic.place_power_ports",
            {"ports": payload},
            timeout=_BULK_TIMEOUT_S * max(1, len(ports) // 8),
        )
        result.power_ports_emitted += len(ports)
    except Exception as exc:
        result.notes.append(f"place_power_ports for {sheet_name} failed: {exc}")
