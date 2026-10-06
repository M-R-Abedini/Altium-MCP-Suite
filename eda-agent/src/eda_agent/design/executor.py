# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Executor, instantiate a DesignPlan in Altium.

Slice B.1 scope: parts placement. Opens or creates the project, creates
SchDoc(s) for each plan.sheet, places every existing-lib part at a
grid-computed position, saves. Returns a structured result.

Wiring (net labels at pin coordinates) is Slice B.2, needs a new Pascal
helper to look up pin world coords on a placed component instance.

The executor is mechanical: it consumes a validated plan, calls existing
MCP-style commands via the bridge, and reports outcomes. It does not
reason. Reasoning belongs to the planner (Claude Code).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from eda_agent.design.canvas import POWER_RAIL_CLUSTER_RADIUS_MILS
from eda_agent.design.layout import PlacedPart, compute_layout
from eda_agent.design.plan import DesignPlan, Net, Part, PartStatus, PinRef
from eda_agent.design.router import (
    _STUB_CLEARANCE_MILS,
    _STUB_LEN_MILS,
    _STUB_MIN_LEN_MILS,
    _adaptive_stub_length,
    _l_path_collisions,
    _path_collisions,
    _path_length,
    _pin_direction_vector,
    _route_l_path,
    _route_s_bend,
    _route_signal_pins,
    _segment_crosses_rect,
    _stub_endpoints,
)

logger = logging.getLogger("eda_agent.design.executor")


@dataclass
class PartFailure:
    """One part the executor could not place."""

    refdes: str
    reason: str
    code: str


# First-time loading a SchLib in Altium (especially a 400-component one) can
# take 20-30s. Bump the per-call budget so the executor doesn't time out on
# the first place. Subsequent calls are fast because the lib stays cached.
_PLACE_TIMEOUT_S = 60.0
_LABEL_TIMEOUT_S = 30.0
_SAVE_TIMEOUT_S = 60.0
_PARAM_STAMP_TIMEOUT_S = 30.0


@dataclass
class NetMismatch:
    """One net-verification mismatch from the post-emission check."""

    code: str               # 'NET_SHORT' | 'NET_OPEN' | 'NET_MISSING_PIN'
    plan_net: str           # name of the plan-side Net affected
    actual_nets: list[str]  # actual Altium net(s) involved (sorted)
    pins: list[str]         # involved (refdes.pin) entries
    text: str               # human-readable description


@dataclass
class ExecutorResult:
    """What ``execute_plan`` returns."""

    ok: bool = True
    project_path: str = ""
    sheets_touched: list[str] = field(default_factory=list)
    placed: list[PlacedPart] = field(default_factory=list)
    failures: list[PartFailure] = field(default_factory=list)
    needs_creation: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    nets_labelled: int = 0
    power_ports_placed: int = 0
    net_mismatches: list[NetMismatch] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "project_path": self.project_path,
            "sheets_touched": list(self.sheets_touched),
            "placed": [
                {
                    "refdes": p.refdes,
                    "sheet": p.sheet,
                    "x_mils": p.x_mils,
                    "y_mils": p.y_mils,
                    "rotation": p.rotation,
                }
                for p in self.placed
            ],
            "failures": [
                {"refdes": f.refdes, "reason": f.reason, "code": f.code}
                for f in self.failures
            ],
            "needs_creation": list(self.needs_creation),
            "notes": list(self.notes),
            "nets_labelled": self.nets_labelled,
            "power_ports_placed": self.power_ports_placed,
            "net_mismatches": [
                {
                    "code": m.code,
                    "plan_net": m.plan_net,
                    "actual_nets": list(m.actual_nets),
                    "pins": list(m.pins),
                    "text": m.text,
                }
                for m in self.net_mismatches
            ],
        }


# Pure-Python helpers moved to ``_wiring.py`` so both this legacy executor
# and the new canvas-based pipeline read from the same source of truth.
# Re-exported here under the same names for backward compat: anything
# that did ``from eda_agent.design.executor import _detect_junctions``
# keeps working.
from eda_agent.design._wiring import (
    _bom_lookup,
    _detect_junctions,
    _ground_style,
    _is_ground_net,
    _net_representation,
    _part_parameters,
    _power_port_orientation,
    _sheet_path,
)


def execute_plan(plan: DesignPlan, project_path: str, *, bridge: Optional[Any] = None) -> ExecutorResult:
    """Instantiate the plan in Altium.

    Args:
        plan: A validated DesignPlan (caller is responsible for validation;
            this function re-runs cross_check defensively).
        project_path: Absolute path to the .PrjPcb (created if absent).
        bridge: Optional bridge to use; defaults to the global one. Tests
            inject a fake to avoid Altium round-trips.

    Returns:
        ExecutorResult, ok=True iff all existing-lib parts placed and
        no needs_creation parts were present.
    """
    result = ExecutorResult(project_path=project_path)

    # Defensive cross-check (caller should have run it, but cheap to repeat).
    cross = plan.cross_check()
    if cross:
        result.ok = False
        result.notes.extend(cross)
        return result

    # Halt early on needs_creation parts, escalate to caller, do not place
    # partials that would mislead a reviewer about completeness.
    needs_creation = [p.refdes for p in plan.parts if p.status == PartStatus.NEEDS_CREATION]
    if needs_creation:
        result.ok = False
        result.needs_creation = needs_creation
        result.notes.append(
            "Plan contains needs_creation parts; refusing to instantiate a "
            "partial design. Resolve those parts (pick existing or author "
            "a new symbol) and re-run."
        )
        return result

    if bridge is None:
        from eda_agent.bridge import get_bridge  # late import, needs Altium
        bridge = get_bridge()

    project = Path(project_path).expanduser().resolve()

    # Open or create the project.
    if project.exists():
        try:
            bridge.send_command("project.open", {"project_path": str(project)})
            result.notes.append(f"Opened existing project: {project}")
        except Exception as exc:
            result.ok = False
            result.notes.append(f"project.open failed: {exc}")
            return result
    else:
        try:
            bridge.send_command(
                "project.create",
                {"project_path": str(project), "project_type": "PCB"},
            )
            result.notes.append(f"Created project: {project}")
        except Exception as exc:
            result.ok = False
            result.notes.append(f"project.create failed: {exc}")
            return result

    # Ensure each sheet exists. Skip if already present (project.open will have
    # picked up sheets attached to the project file).
    sheets_to_create: list[tuple[str, Path]] = []
    for sheet in plan.sheets:
        target = _sheet_path(project, sheet.name)
        sheets_to_create.append((sheet.name, target))

    for sheet_name, sheet_path in sheets_to_create:
        if sheet_path.exists():
            # Sheet on disk, make sure it is loaded into the editor so
            # set_active_document can target it. project.open does not
            # always pull child sheets into the editor.
            try:
                bridge.send_command(
                    "application.run_process",
                    {
                        "process_name": "WorkspaceManager:OpenObject",
                        "parameters": (
                            "ObjectKind=Document|FileName=" + str(sheet_path)
                        ),
                    },
                )
                result.notes.append(f"Sheet loaded: {sheet_path}")
            except Exception as exc:
                result.ok = False
                result.notes.append(f"OpenObject failed for {sheet_name}: {exc}")
                return result
            continue
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
            return result

    # Compute placement for every part.
    placements = compute_layout(plan)
    placement_by_refdes = {p.refdes: p for p in placements}

    bom_lookup = _bom_lookup(plan)

    # Group parts by sheet so we set the active document once per sheet.
    parts_by_sheet: dict[str, list[Part]] = {}
    for p in plan.parts:
        parts_by_sheet.setdefault(p.sheet, []).append(p)

    for sheet_name, parts in parts_by_sheet.items():
        sheet_path = _sheet_path(project, sheet_name)
        try:
            bridge.send_command(
                "application.set_active_document", {"file_path": str(sheet_path)}
            )
            result.sheets_touched.append(sheet_name)
        except Exception as exc:
            for part in parts:
                result.failures.append(
                    PartFailure(
                        refdes=part.refdes,
                        reason=f"could not activate sheet {sheet_name}: {exc}",
                        code="SHEET_ACTIVATE_FAILED",
                    )
                )
            continue

        # Bulk-place every part for this sheet in a single IPC call.
        # generic.place_sch_components_from_library wraps the whole batch
        # in one PreProcess/PostProcess + one GraphicallyInvalidate, which
        # is 10-100x faster than looping the singular variant.
        placement_ops: list[str] = []
        parts_to_place: list[Part] = []
        for part in parts:
            placement = placement_by_refdes.get(part.refdes)
            if placement is None:
                result.failures.append(
                    PartFailure(
                        refdes=part.refdes,
                        reason="layout did not produce a placement",
                        code="LAYOUT_GAP",
                    )
                )
                continue
            placement_ops.append(
                f"library_path={part.lib_path or ''};"
                f"lib_reference={part.lib_ref};"
                f"x={placement.x_mils};"
                f"y={placement.y_mils};"
                f"designator={part.refdes};"
                f"rotation={placement.rotation};"
                f"footprint={part.footprint or ''}"
            )
            parts_to_place.append(part)

        if placement_ops:
            try:
                place_response = bridge.send_command(
                    "generic.place_sch_components_from_library",
                    {"placements": "~~".join(placement_ops)},
                    timeout=_PLACE_TIMEOUT_S * max(1, len(placement_ops) // 4),
                )
            except Exception as exc:
                for part in parts_to_place:
                    result.failures.append(
                        PartFailure(
                            refdes=part.refdes,
                            reason=f"bulk place failed: {exc}",
                            code="PLACE_FAILED",
                        )
                    )
                continue
            # Pascal returns {"placed", "failed", "total", "failed_refdes"?}
            # failed_refdes is a comma-separated "DESIG:CODE,DESIG:CODE" list of
            # parts the handler silently skipped (LibRef missing, lib resolve
            # failed, or LoadComponentFromLibrary returned nil). Without this
            # parsing, the bulk handler's partial success is invisible: every
            # part lands in result.placed even when only N-1 actually made it.
            failed_payload = ""
            if isinstance(place_response, dict):
                failed_payload = str(place_response.get("failed_refdes", "") or "")
            failed_map: dict[str, str] = {}
            if failed_payload:
                for entry in failed_payload.split(","):
                    entry = entry.strip()
                    if not entry:
                        continue
                    if ":" in entry:
                        refdes, code = entry.split(":", 1)
                    else:
                        refdes, code = entry, "PLACE_FAILED"
                    failed_map[refdes.strip()] = code.strip() or "PLACE_FAILED"
            for part in parts_to_place:
                if part.refdes in failed_map:
                    code = failed_map[part.refdes]
                    result.failures.append(
                        PartFailure(
                            refdes=part.refdes,
                            reason=(
                                f"Pascal place handler reported {code} for "
                                f"lib_ref={part.lib_ref!r} from "
                                f"lib_path={part.lib_path!r}. The symbol was "
                                "not added to the SchDoc."
                            ),
                            code=code,
                        )
                    )
                else:
                    result.placed.append(placement_by_refdes[part.refdes])

        # Bulk-stamp Value / Manufacturer / MPN / Footprint on every just-placed
        # part. Bulk handler iterates the sheet once, applies all params under
        # ONE PreProcess/PostProcess. Saves N-1 IPC round trips.
        stamp_ops: list[str] = []
        for part in parts_to_place:
            stamp_payload = _part_parameters(part, bom_lookup)
            if not stamp_payload:
                continue
            fields = [f"designator={part.refdes}"]
            for k, v in stamp_payload.items():
                if not k or v is None:
                    continue
                vs = str(v).strip()
                if not vs:
                    continue
                # The batch field separator is ';' so we strip stray ones from
                # values to keep the encoding safe. Values with '=' are fine
                # because GetBatchField splits each field at the FIRST '='.
                fields.append(f"{k}={vs.replace(';', ',')}")
            if len(fields) > 1:
                stamp_ops.append(";".join(fields))

        if stamp_ops:
            try:
                bridge.send_command(
                    "generic.set_sch_components_parameters",
                    {
                        "stamps": "~~".join(stamp_ops),
                        "sheet_path": str(_sheet_path(project, sheet_name)),
                    },
                    timeout=_PARAM_STAMP_TIMEOUT_S * max(1, len(stamp_ops) // 8),
                )
            except Exception as exc:
                result.notes.append(
                    f"bulk param-stamp for sheet {sheet_name} failed: {exc}"
                )

    # Wiring stage, drop a net label at every plan-defined pin endpoint.
    # Power and ground nets get power ports instead of plain labels.
    placed_refdes = {p.refdes for p in result.placed}
    if placed_refdes:
        _place_net_labels(
            plan, placed_refdes, parts_by_sheet, project, bridge, result,
            placement_by_refdes=placement_by_refdes,
        )

    # Save everything we touched.
    try:
        bridge.send_command("application.save_all", {}, timeout=_SAVE_TIMEOUT_S)
        result.notes.append("save_all completed")
    except Exception as exc:
        result.notes.append(f"save_all failed: {exc}")
        result.ok = False

    # Net verification: query Altium's compiled netlist and check that
    # the as-built topology matches the plan. Catches shorts (two plan
    # nets merged into one Altium net) and opens (one plan net split
    # across multiple Altium nets). Bridging through pins is invisible
    # to ERC (which sees one valid net and no floating pins), so this
    # is the only line of defence.
    try:
        _verify_nets(plan, str(project), bridge, result)
    except Exception as exc:
        result.notes.append(f"net verification skipped: {exc}")

    if result.failures or result.net_mismatches:
        result.ok = False

    return result


def _verify_nets(
    plan: DesignPlan,
    project_path: str,
    bridge: Any,
    result: ExecutorResult,
) -> None:
    """Compare planned net topology to Altium's compiled netlist.

    For every plan-side Net, every (refdes, pin) it lists must land
    on the SAME Altium-side net (its name may differ -- Altium
    auto-generates names like ``NetC1_1`` for unlabelled nets, that's
    fine as long as the topology matches). Different plan-nets must
    land on DIFFERENT Altium-nets.

    Mismatches are surfaced as ``NetMismatch`` entries with codes:

    - ``NET_SHORT``: two or more plan nets ended up on the same
      Altium-net (a wire bridged them). This is the failure ERC
      doesn't catch -- Altium sees one valid net so ERC's "no
      floating pin" check passes silently.
    - ``NET_OPEN``: one plan net's pins split across multiple
      Altium-nets (a wire is missing).
    - ``NET_MISSING_PIN``: a planned pin doesn't appear in the
      compiled netlist at all.

    Power and ground nets carry their name via the port glyph and
    typically match the plan name exactly; signal nets get auto-
    generated names that we treat as opaque tokens.
    """
    netlist = bridge.send_command(
        "project.get_nets",
        {"limit": "10000", "project_path": project_path},
        timeout=_LABEL_TIMEOUT_S,
    )
    if not isinstance(netlist, dict):
        result.notes.append("net verification: get_nets returned non-dict")
        return
    # A bridge that doesn't actually implement get_nets (mock / fake)
    # returns no ``pins`` key at all; treat that as "verification not
    # available" rather than "every pin is missing".
    if "pins" not in netlist:
        result.notes.append("net verification skipped: bridge did not return a netlist")
        return

    # pin (refdes, pin_id) -> actual Altium net name
    actual: dict[tuple[str, str], str] = {}
    for entry in netlist.get("pins", []) or []:
        comp = str(entry.get("component", ""))
        pin = str(entry.get("pin", ""))
        net = str(entry.get("net", ""))
        if comp and pin:
            actual[(comp, pin)] = net

    # For each plan net: which actual nets do its pins end up on?
    plan_to_actual: dict[str, list[str]] = {}
    for n in plan.nets:
        nets_seen: list[str] = []
        for pr in n.pins:
            actual_net = actual.get((pr.refdes, str(pr.pin)))
            if actual_net is None:
                result.net_mismatches.append(NetMismatch(
                    code="NET_MISSING_PIN",
                    plan_net=n.name,
                    actual_nets=[],
                    pins=[f"{pr.refdes}.{pr.pin}"],
                    text=(
                        f"plan net {n.name!r} pin {pr.refdes}.{pr.pin} is "
                        "not present in the compiled netlist"
                    ),
                ))
                continue
            nets_seen.append(actual_net)
        plan_to_actual[n.name] = nets_seen
        unique = sorted(set(nets_seen))
        if len(unique) > 1:
            pins_repr = [f"{pr.refdes}.{pr.pin}" for pr in n.pins]
            result.net_mismatches.append(NetMismatch(
                code="NET_OPEN",
                plan_net=n.name,
                actual_nets=unique,
                pins=pins_repr,
                text=(
                    f"plan net {n.name!r} pins are split across "
                    f"{len(unique)} actual nets ({', '.join(unique)}) -- "
                    "a wire is missing"
                ),
            ))

    # Multiple plan nets sharing one actual net -> short.
    actual_to_plan: dict[str, set[str]] = {}
    for plan_name, nets_seen in plan_to_actual.items():
        for a in nets_seen:
            actual_to_plan.setdefault(a, set()).add(plan_name)
    for actual_name, plan_names in actual_to_plan.items():
        if len(plan_names) > 1:
            offenders = sorted(plan_names)
            pins = sorted(
                f"{pr.refdes}.{pr.pin}"
                for n in plan.nets
                if n.name in plan_names
                for pr in n.pins
            )
            result.net_mismatches.append(NetMismatch(
                code="NET_SHORT",
                plan_net=", ".join(offenders),
                actual_nets=[actual_name],
                pins=pins,
                text=(
                    f"plan nets {offenders} are SHORTED -- all merged "
                    f"into actual net {actual_name!r} (ERC doesn't catch "
                    "this; a wire is bridging pins it shouldn't)"
                ),
            ))


# 100-mil stub wire length between a pin's electrical hot end and the net
# label / power port that attaches to it. ERC reports "Floating net labels"
# when a label sits exactly on a pin endpoint without an intervening wire,
# so every label/port gets pulled out along the pin's vector by this much.
# Stub-length constants and the pin-direction / adaptive-length / stub-
# endpoint helpers were extracted to ``design.router`` so this file
# stays focused on orchestration + Altium IPC. They are re-imported at
# the top of this module so the rest of the executor keeps using the
# same names.
#
# _ground_style / _power_port_orientation / _net_representation /
# _detect_junctions / _bom_lookup / _part_parameters / _sheet_path
# moved to ``_wiring.py`` and re-exported via the import block near the
# top of this file.

# _route_signal_pins moved to design.router; re-imported below.




def _place_net_labels(
    plan: DesignPlan,
    placed_refdes: set[str],
    parts_by_sheet: dict[str, list[Part]],
    project: Path,
    bridge: Any,
    result: ExecutorResult,
    *,
    placement_by_refdes: dict[str, Any] | None = None,
) -> None:
    """For each plan net, drop a net label or power port at every endpoint pin.

    Reads pin world coordinates via ``generic.get_sch_component_pins``,
    a helper in Pascal that iterates ePin children of a placed
    eSchComponent on the active sheet. Pin lookups are cached per sheet
    activation to avoid redundant IPC round-trips.

    ``placement_by_refdes`` is the layout output. When supplied, signal-net
    wire routing avoids component-body bboxes derived from each part's
    pin count via ``_bbox_half``.
    """
    # Component-body obstacles for wire-collision-aware routing (task #50).
    # Query REAL BoundingRectangle from Altium so the router and the
    # downstream audit see the same world. Falls back to a body-only
    # estimate if the query fails (e.g. Pascal hasn't been updated).
    def _estimate_body_half(pc: int) -> int:
        if pc >= 16:
            return 600
        if pc >= 4:
            return 350
        return 150

    pin_count_for_obstacles: dict[str, int] = {p.refdes: 0 for p in plan.parts}
    for net in plan.nets:
        for pin in net.pins:
            pin_count_for_obstacles[pin.refdes] = (
                pin_count_for_obstacles.get(pin.refdes, 0) + 1
            )

    obstacles_by_sheet: dict[str, list[tuple[int, int, int, int]]] = {}
    if placement_by_refdes:
        # Group placements by sheet to do one query per sheet.
        placements_by_sheet: dict[str, list[Any]] = {}
        for p in placement_by_refdes.values():
            placements_by_sheet.setdefault(p.sheet, []).append(p)

        for sheet, sheet_placements in placements_by_sheet.items():
            sheet_p = str(_sheet_path(project, sheet))
            real_bboxes: dict[str, tuple[int, int, int, int]] = {}
            try:
                resp = bridge.send_command(
                    "generic.query_objects",
                    {
                        "object_type": "eSchComponent",
                        "scope": f"doc:{sheet_p}",
                        "properties": (
                            "Designator.Text,"
                            "BoundingRectangle.X1,BoundingRectangle.Y1,"
                            "BoundingRectangle.X2,BoundingRectangle.Y2"
                        ),
                    },
                )
                for row in (resp or {}).get("objects", []):
                    refdes = str(row.get("Designator.Text", "")).strip()
                    if not refdes:
                        continue
                    try:
                        x1 = int(row["BoundingRectangle.X1"])
                        y1 = int(row["BoundingRectangle.Y1"])
                        x2 = int(row["BoundingRectangle.X2"])
                        y2 = int(row["BoundingRectangle.Y2"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    real_bboxes[refdes] = (
                        min(x1, x2), min(y1, y2),
                        max(x1, x2), max(y1, y2),
                    )
            except Exception as exc:
                # Documented fallback, but say so. Without the real
                # rectangles the router avoids ESTIMATED bodies derived
                # from pin count, so wires can end up crossing a
                # component that the estimate made too small. That
                # surfaces later as a routing complaint with no stated
                # cause, and the most likely trigger is version skew:
                # a deployed script older than this Python.
                real_bboxes = {}
                result.notes.append(
                    f"sheet {sheet!r}: real component bounding boxes "
                    f"unavailable ({type(exc).__name__}), routing fell "
                    f"back to pin-count body estimates; check the "
                    f"deployed SCRIPT_VERSION matches this build")

            for p in sheet_placements:
                if p.refdes in real_bboxes:
                    obstacles_by_sheet.setdefault(sheet, []).append(
                        real_bboxes[p.refdes]
                    )
                else:
                    half = _estimate_body_half(
                        pin_count_for_obstacles.get(p.refdes, 2)
                    )
                    obstacles_by_sheet.setdefault(sheet, []).append((
                        p.x_mils - half, p.y_mils - half,
                        p.x_mils + half, p.y_mils + half,
                    ))
    refdes_to_sheet: dict[str, str] = {p.refdes: p.sheet for p in plan.parts}
    # zone is the functional-block identifier per discipline rule 3.
    # None means the part isn't assigned to a block: those parts fall
    # into the implicit "unzoned" group when deciding wire vs label.
    refdes_to_zone: dict[str, Optional[str]] = {
        p.refdes: p.zone for p in plan.parts
    }

    # (sheet_name, refdes) -> {pin_id -> (x_mils, y_mils, orientation, pin_length_mils)}
    # orientation is the TRotationBy90 enum (0=right, 1=up, 2=left, 3=down)
    # returned by Gen_GetSchComponentPins; pin_length_mils is how far the
    # electrical hot end sits from Pin.Location along that vector.
    sheet_pin_cache: dict[
        tuple[str, str], dict[str, tuple[int, int, int, int]]
    ] = {}

    nets_by_sheet: dict[str, list[Net]] = {}
    for net in plan.nets:
        sheets_touched = {refdes_to_sheet.get(p.refdes) for p in net.pins}
        sheets_touched.discard(None)
        for sheet in sheets_touched:
            nets_by_sheet.setdefault(sheet, []).append(net)

    for sheet_name, nets in nets_by_sheet.items():
        sheet_path = _sheet_path(project, sheet_name)
        try:
            bridge.send_command(
                "application.set_active_document", {"file_path": str(sheet_path)}
            )
        except Exception as exc:
            result.notes.append(
                f"could not activate sheet {sheet_name} for wiring: {exc}"
            )
            continue

        # Per-sheet bulk buffers:
        # - wires: stubs + signal-net Manhattan routing
        # - labels: signal-net naming labels
        # - ports: power-rail / ground glyphs
        pending_wires: list[tuple[int, int, int, int]] = []
        pending_labels: list[tuple[str, int, int]] = []
        pending_ports: list[tuple[str, int, int, str, int]] = []

        # Stagger counter: how many stubs have already been emitted
        # from this (refdes, direction) pair on this sheet. Each
        # subsequent same-direction stub gets +100 mil so the L-path
        # bends don't share a column. Without this two pins on the
        # same connector both bend at the same x and the wires sit on
        # top of each other.
        stagger_counter: dict[tuple[str, int, int], int] = {}

        sheet_p = str(_sheet_path(project, sheet_name))

        # Whole-sheet pin dump: ONE IPC call replaces N per-component
        # get_sch_component_pins calls. Pascal iterates the sheet once
        # and returns every pin keyed by refdes.
        try:
            dump = bridge.send_command(
                "generic.get_sch_doc_pins",
                {"sheet_path": str(sheet_path)},
                timeout=_LABEL_TIMEOUT_S,
            )
        except Exception as exc:
            result.notes.append(
                f"sheet pin dump for {sheet_name} failed, falling back: {exc}"
            )
            dump = None
        if dump:
            for row in dump.get("pins", []):
                rd = str(row.get("refdes", "")).strip()
                if not rd:
                    continue
                try:
                    px = int(row.get("x_mils", 0))
                    py = int(row.get("y_mils", 0))
                except (TypeError, ValueError):
                    continue
                try:
                    orient = int(row.get("orientation", 0))
                except (TypeError, ValueError):
                    orient = 0
                try:
                    plen = int(row.get("pin_length_mils", 0))
                except (TypeError, ValueError):
                    plen = 0
                pn = str(row.get("pin_number", "")).strip()
                nm = str(row.get("pin_name", "")).strip()
                entry = (px, py, orient, plen)
                cache_key = (sheet_name, rd)
                pm = sheet_pin_cache.setdefault(cache_key, {})
                if pn:
                    pm[pn] = entry
                if nm and nm not in pm:
                    pm[nm] = entry

        # Two-pass per sheet:
        #
        # Pass 1 - stub emission: for every (net, pin) compute the stub
        #   endpoint, emit the stub wire, and record the stub_end so we
        #   know where each net's pins terminate on the sheet.
        #
        # Pass 2 - routing / labels / ports: each net's L-path router
        #   sees not just component bboxes as obstacles but ALSO every
        #   OTHER net's stub_end as a small point-obstacle. Without
        #   this, a power-port spoke from one pin can run straight
        #   along an x-column that another net's stub_end happens to
        #   sit on, bridging the two nets (Altium auto-junctions at
        #   coincident endpoints, so the wires merge into one net
        #   silently).

        sheet_net_actions: dict[str, list[tuple[Any, tuple[int, int], int]]] = {}

        for net in nets:
            net_actions: list[tuple[Any, tuple[int, int], int]] = []
            for pin_ref in net.pins:
                if pin_ref.refdes not in placed_refdes:
                    result.failures.append(
                        PartFailure(
                            refdes=pin_ref.refdes,
                            reason=(
                                f"net {net.name} references unplaced "
                                f"refdes {pin_ref.refdes}"
                            ),
                            code="UNPLACED_REFDES",
                        )
                    )
                    continue
                if refdes_to_sheet.get(pin_ref.refdes) != sheet_name:
                    # Cross-sheet net, only label the pin on its home sheet.
                    continue

                # Whole-sheet pin dump already populated sheet_pin_cache
                # above. If it failed, fall back to a per-part query.
                cache_key = (sheet_name, pin_ref.refdes)
                if cache_key not in sheet_pin_cache:
                    try:
                        info = bridge.send_command(
                            "generic.get_sch_component_pins",
                            {
                                "designator": pin_ref.refdes,
                                "sheet_path": str(_sheet_path(project, sheet_name)),
                            },
                            timeout=_LABEL_TIMEOUT_S,
                        )
                    except Exception as exc:
                        result.failures.append(
                            PartFailure(
                                refdes=pin_ref.refdes,
                                reason=f"get_sch_component_pins failed: {exc}",
                                code="PIN_LOOKUP_FAILED",
                            )
                        )
                        sheet_pin_cache[cache_key] = {}
                        continue

                    pin_map: dict[str, tuple[int, int, int, int]] = {}
                    for row in (info or {}).get("pins", []):
                        try:
                            x = int(row.get("x_mils", 0))
                            y = int(row.get("y_mils", 0))
                        except (TypeError, ValueError):
                            continue
                        try:
                            orient = int(row.get("orientation", 0))
                        except (TypeError, ValueError):
                            orient = 0
                        try:
                            plen = int(row.get("pin_length_mils", 0))
                        except (TypeError, ValueError):
                            plen = 0
                        pn = str(row.get("pin_number", "")).strip()
                        nm = str(row.get("pin_name", "")).strip()
                        entry = (x, y, orient, plen)
                        if pn:
                            pin_map[pn] = entry
                        if nm and nm not in pin_map:
                            pin_map[nm] = entry
                    sheet_pin_cache[cache_key] = pin_map

                pin_map = sheet_pin_cache[cache_key]
                entry = pin_map.get(pin_ref.pin)
                if entry is None:
                    result.failures.append(
                        PartFailure(
                            refdes=pin_ref.refdes,
                            reason=(
                                f"pin {pin_ref.pin} not found on placed "
                                f"component (have: {sorted(pin_map.keys())})"
                            ),
                            code="PIN_NOT_FOUND",
                        )
                    )
                    continue

                x, y, pin_orient, pin_len = entry
                dx_dir, dy_dir = _pin_direction_vector(pin_orient)
                stagger_key = (pin_ref.refdes, dx_dir, dy_dir)
                extra = stagger_counter.get(stagger_key, 0) * 100
                stagger_counter[stagger_key] = (
                    stagger_counter.get(stagger_key, 0) + 1
                )
                (hot_x, hot_y), (end_x, end_y) = _stub_endpoints(
                    x, y, pin_orient, pin_len,
                    obstacles=obstacles_by_sheet.get(sheet_name, []),
                    extra_length_mils=extra,
                )
                # Every pin still gets a stub wire from the pin endpoint
                # outward to the label / port / routing-junction point.
                # Tag the net so junction detection stays net-aware (a dot
                # only where SAME-net wires meet, never bridging two nets).
                pending_wires.append((hot_x, hot_y, end_x, end_y, net.name))
                net_actions.append((pin_ref, (end_x, end_y), pin_orient))

            if net_actions:
                sheet_net_actions[net.name] = net_actions

        # End Pass 1. Build the sheet-wide stub-end point cloud so each
        # net's routing can avoid running through other nets' stub_ends.
        all_stub_end_points: set[tuple[int, int]] = set()
        for actions in sheet_net_actions.values():
            for _, (ex, ey), _ in actions:
                all_stub_end_points.add((ex, ey))

        # Pass 2: routing / labels / ports per net.
        for net in nets:
            net_actions = sheet_net_actions.get(net.name)
            if not net_actions:
                continue
            own_stub_ends: set[tuple[int, int]] = {a[1] for a in net_actions}
            other_stub_end_bboxes = [
                (x - 50, y - 50, x + 50, y + 50)
                for (x, y) in all_stub_end_points
                if (x, y) not in own_stub_ends
            ]
            routing_obstacles = (
                list(obstacles_by_sheet.get(sheet_name, []))
                + other_stub_end_bboxes
            )

            stub_ends: list[tuple[int, int]] = [a[1] for a in net_actions]

            representation = _net_representation(net, refdes_to_zone)

            if representation == "port":
                # Rails: power-port consolidation (task #51). Group pins by
                # proximity (Manhattan radius), place ONE rail glyph per
                # cluster, wire every pin in the cluster to the glyph via
                # the same obstacle-aware Manhattan router used for signals.
                # Cuts the buck's 18 stacked GND/VIN/VOUT glyphs down to a
                # handful and frees vertical space around each pin.
                is_gnd = _is_ground_net(net)
                style = _ground_style(net.name) if is_gnd else "bar"
                # Greedy clustering: each stub_end joins the first cluster
                # whose nearest member is within CLUSTER_RADIUS_MILS. Shared
                # with the pipeline preview so apply matches the canvas.
                # Asymmetric by kind (same rule as the pipeline's
                # _cluster_radius_for_net): ground glyphs are narrow, so
                # per-pin drops on a decap row read cleanly at the tight
                # radius; power BARS carry the net name, wider than a
                # decap column pitch, so nearby rail pins share one bar.
                CLUSTER_RADIUS_MILS = (
                    POWER_RAIL_CLUSTER_RADIUS_MILS if is_gnd else 1000
                )
                clusters: list[list[int]] = []  # list of indices into net_actions
                for i, (_, (ex, ey), _) in enumerate(net_actions):
                    joined = False
                    for cl in clusters:
                        for j in cl:
                            mx, my = net_actions[j][1]
                            if abs(ex - mx) + abs(ey - my) <= CLUSTER_RADIUS_MILS:
                                cl.append(i)
                                joined = True
                                break
                        if joined:
                            break
                    if not joined:
                        clusters.append([i])

                sheet_obstacles = routing_obstacles
                for cl in clusters:
                    cluster_pts = [net_actions[i][1] for i in cl]
                    cluster_orients = [net_actions[i][2] for i in cl]
                    # Port location: centroid X, then above the topmost pin
                    # (VCC, glyph goes up) or below the bottom pin (GND).
                    centroid_x = (
                        sum(pt[0] for pt in cluster_pts) // len(cluster_pts)
                    )
                    centroid_x = (centroid_x // 100) * 100
                    if is_gnd:
                        port_y = min(pt[1] for pt in cluster_pts) - 400
                    else:
                        port_y = max(pt[1] for pt in cluster_pts) + 400
                    port_y = (port_y // 100) * 100
                    # Re-derive an orientation FROM the average pin orient
                    # in the cluster (still respects rail-direction rule).
                    port_orient = _power_port_orientation(
                        cluster_orients[0], is_ground=is_gnd
                    )
                    pending_ports.append(
                        (net.name, centroid_x, port_y, style, port_orient)
                    )
                    result.power_ports_placed += 1
                    # Wire each pin in the cluster to the port.
                    for pt in cluster_pts:
                        if pt == (centroid_x, port_y):
                            continue
                        for seg in _route_l_path(
                            pt[0], pt[1], centroid_x, port_y, sheet_obstacles
                        ):
                            pending_wires.append((*seg, net.name))
            elif representation == "wire":
                # Block-local net: every pin lives in the same zone, so the
                # connection is visually traceable within that block. Wire
                # the stub ends together, no label.
                for seg in _route_signal_pins(
                    stub_ends, routing_obstacles
                ):
                    pending_wires.append((*seg, net.name))

            else:  # representation == "label_per_pin"
                # Cross-block net OR planner-asserted force_label: drop a
                # net label at every pin's stub end. No inter-pin wires;
                # connectivity is established by the shared label name.
                for (end_x, end_y) in stub_ends:
                    pending_labels.append((net.name, end_x, end_y))
                    result.nets_labelled += 1

        # End of net/pin loops for this sheet: flush the three batches.
        if pending_wires:
            wires_payload = "~~".join(
                f"x1={w[0]};y1={w[1]};x2={w[2]};y2={w[3]}"
                for w in pending_wires
            )
            try:
                bridge.send_command(
                    "generic.place_wires",
                    {"wires": wires_payload},
                    timeout=_LABEL_TIMEOUT_S * max(1, len(pending_wires) // 8),
                )
            except Exception as exc:
                result.notes.append(
                    f"bulk stub-wires for sheet {sheet_name} failed: {exc}"
                )

            # Emit junction dots wherever 3+ wires meet or a wire ends
            # mid-segment of another wire. Altium's auto-junction-on-
            # compile sometimes misses these, particularly when wires
            # are scripted-placed rather than user-drawn. Explicit
            # eJunction objects give the schematic the conventional
            # dot at every T-junction.
            junctions = _detect_junctions(pending_wires)
            if junctions:
                junctions_payload = "~~".join(
                    f"x={jx};y={jy}" for (jx, jy) in junctions
                )
                try:
                    bridge.send_command(
                        "generic.place_junctions",
                        {"junctions": junctions_payload},
                        timeout=_LABEL_TIMEOUT_S * max(1, len(junctions) // 8),
                    )
                except Exception as exc:
                    result.notes.append(
                        f"bulk junctions for sheet {sheet_name} failed: {exc}"
                    )

        if pending_labels:
            labels_payload = "~~".join(
                f"text={t};x={x};y={y};orientation=0"
                for (t, x, y) in pending_labels
            )
            try:
                bridge.send_command(
                    "generic.place_net_labels",
                    {"labels": labels_payload},
                    timeout=_LABEL_TIMEOUT_S * max(1, len(pending_labels) // 8),
                )
            except Exception as exc:
                result.notes.append(
                    f"bulk net-labels for sheet {sheet_name} failed: {exc}"
                )

        if pending_ports:
            ports_payload = "~~".join(
                f"text={t};x={x};y={y};style={style};orientation={orient}"
                for (t, x, y, style, orient) in pending_ports
            )
            try:
                bridge.send_command(
                    "generic.place_power_ports",
                    {"ports": ports_payload},
                    timeout=_LABEL_TIMEOUT_S * max(1, len(pending_ports) // 8),
                )
            except Exception as exc:
                result.notes.append(
                    f"bulk power-ports for sheet {sheet_name} failed: {exc}"
                )


def execute_plan_from_json(
    plan_json: str,
    project_path: str,
    *,
    bridge: Optional[Any] = None,
) -> ExecutorResult:
    """Convenience wrapper: parse + validate JSON, then run executor."""
    import json as _json

    result = ExecutorResult(project_path=project_path)
    try:
        payload = _json.loads(plan_json)
    except _json.JSONDecodeError as exc:
        result.ok = False
        result.notes.append(f"invalid JSON: {exc}")
        return result

    try:
        plan = DesignPlan.model_validate(payload)
    except ValidationError as exc:
        result.ok = False
        result.notes.extend(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        )
        return result

    return execute_plan(plan, project_path, bridge=bridge)
