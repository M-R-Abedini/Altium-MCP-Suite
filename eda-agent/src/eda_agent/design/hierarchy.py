# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Automatic multi-sheet hierarchy for dense plans. Pure offline.

A plan past ~20 parts stops fitting one readable sheet. This module decides
the split: :func:`plan_hierarchy` partitions the parts by signal connectivity
(via :mod:`eda_agent.design.partition`, Kernighan-Lin min-cut), keeps every
zone intact on one sheet, names each child sheet from its dominant zone role,
derives the inter-sheet ports from the signal nets the cut severs, and emits
the top-sheet op list (sheet symbols + sheet entries + TOC) in the exact
parameter shapes of the ``sch_place_sheet_symbol`` / ``sch_place_sheet_entry``
/ ``sch_generate_toc`` MCP tools. :func:`apply_hierarchy` then produces a NEW
DesignPlan with parts and zones re-homed onto the child sheets.

Power and ground nets never become ports: the executor renders them as power
ports on every sheet (see ``_wiring.represent_net``), so a VCC net spanning
three sheets is already continuous through the global power-port namespace.
Only signal nets crossing the cut need sheet entries.

Port direction is inferred structurally: the net's most-connected part
(highest plan-wide pin-endpoint count -- the IC / hub) is taken as the
driver, its sheet emits ``output``, every other sheet receives ``input``.
When the maximum is tied across sheets the port degrades to
``bidirectional``. No pin electrical types exist in the plan, so this is a
heuristic, not ERC truth.

Top-sheet op coordinates are MILS (schematic canvas units). The plan's zone
geometry stays mm and is untouched here.
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional, Union

from eda_agent.design._wiring import _is_ground_net, _is_power_net
from eda_agent.design.partition import partition_netlist
from eda_agent.design.plan import DesignPlan, Sheet

# Top-sheet geometry (mils). One column of sheet symbols right of the TOC
# frame, wrapping to a fresh column when the current one runs out of sheet.
_TOC_BOX = (200, 200, 2400, 1800)
_SYMBOL_X1 = 2800
_SYMBOL_WIDTH = 1400
_SYMBOL_MIN_HEIGHT = 400
_SYMBOL_GAP = 300
_COLUMN_TOP = 7000
_COLUMN_BOTTOM = 400
_COLUMN_PITCH = _SYMBOL_WIDTH + 400
_ENTRY_FIRST = 100
_ENTRY_PITCH = 150

_DEFAULT_TOP_NAME = "top"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def plan_hierarchy(
    plan: Union[DesignPlan, dict],
    max_parts_per_sheet: int = 20,
) -> dict[str, Any]:
    """Propose a multi-sheet hierarchy for ``plan``.

    Returns ``{"ok": False, "reason": ...}`` on bad input, otherwise::

        {"ok": True,
         "split": bool,            # False = plan fits one sheet, all else empty
         "top_sheet": str,         # name of the top (index) sheet
         "sheets": [{"name", "refdes": [...], "zones": [...], "part_count"}],
         "ports": [{"net", "from_sheet", "to_sheet", "io_type"}],
         "top_sheet_ops": [{"tool", "params": {...}}, ...],  # coords in MILS
         "cut_nets": int}          # nets spanning >1 sheet (rails included)

    Zones are atomic: a zone's parts always land on one sheet together, so a
    zone larger than ``max_parts_per_sheet`` forces an oversize sheet rather
    than a split zone. Deterministic for a given plan.
    """
    plan_obj = _coerce_plan(plan)
    if isinstance(plan_obj, str):
        return {"ok": False, "reason": plan_obj}
    if max_parts_per_sheet < 1:
        return {"ok": False, "reason": "max_parts_per_sheet must be >= 1"}

    n_parts = len(plan_obj.parts)
    if n_parts <= max_parts_per_sheet:
        return {
            "ok": True,
            "split": False,
            "reason": f"{n_parts} parts fit one sheet "
                      f"(threshold {max_parts_per_sheet})",
            "top_sheet": plan_obj.sheets[0].name,
            "sheets": _current_sheet_summary(plan_obj),
            "ports": [],
            "top_sheet_ops": [],
            "cut_nets": 0,
        }

    cluster_of, cluster_parts, cluster_zone = _cluster_units(plan_obj)
    cluster_nets = _nets_as_clusters(plan_obj, cluster_of)
    groups = _partition_clusters(
        cluster_parts, cluster_nets, n_parts, max_parts_per_sheet)

    top_name = _DEFAULT_TOP_NAME
    names = _name_groups(groups, cluster_parts, cluster_zone, plan_obj,
                         reserved={top_name})

    sheets: list[dict[str, Any]] = []
    sheet_of_refdes: dict[str, str] = {}
    for name, group in zip(names, groups):
        refdes: list[str] = []
        zones: list[str] = []
        for cid in group:
            refdes.extend(cluster_parts[cid])
            if cluster_zone.get(cid):
                zones.append(cluster_zone[cid])
        refdes.sort()
        zones.sort()
        for r in refdes:
            sheet_of_refdes[r] = name
        sheets.append({
            "name": name,
            "refdes": refdes,
            "zones": zones,
            "part_count": len(refdes),
        })

    ports = _infer_ports(plan_obj, sheet_of_refdes)
    cut_nets = _count_cut_nets(plan_obj, sheet_of_refdes)
    ops = _top_sheet_ops([s["name"] for s in sheets], ports)

    return {
        "ok": True,
        "split": True,
        "top_sheet": top_name,
        "sheets": sheets,
        "ports": ports,
        "top_sheet_ops": ops,
        "cut_nets": cut_nets,
    }


def apply_hierarchy(
    plan: Union[DesignPlan, dict],
    hierarchy: dict[str, Any],
) -> DesignPlan:
    """Return a NEW DesignPlan with parts/zones re-homed per ``hierarchy``.

    Pure: the input plan is never mutated. A non-split hierarchy returns a
    deep copy of the plan unchanged. The result carries the top sheet first,
    then one child Sheet per hierarchy sheet; every part's ``sheet`` and every
    zone's ``sheet`` is rewritten to its new home so ``cross_check()`` stays
    clean. Raises ``ValueError`` on a malformed or failed hierarchy dict.
    """
    plan_obj = _coerce_plan(plan)
    if isinstance(plan_obj, str):
        raise ValueError(plan_obj)
    if not isinstance(hierarchy, dict) or not hierarchy.get("ok"):
        raise ValueError("hierarchy is not a successful plan_hierarchy result")

    new_plan = plan_obj.model_copy(deep=True)
    if not hierarchy.get("split"):
        return new_plan

    sheet_specs = hierarchy.get("sheets") or []
    top_name = hierarchy.get("top_sheet") or _DEFAULT_TOP_NAME
    if not sheet_specs:
        raise ValueError("split hierarchy carries no sheets")

    sheet_of_refdes: dict[str, str] = {}
    sheet_of_zone: dict[str, str] = {}
    for spec in sheet_specs:
        for r in spec.get("refdes", []):
            sheet_of_refdes[r] = spec["name"]
        for z in spec.get("zones", []):
            sheet_of_zone[z] = spec["name"]

    size = new_plan.sheets[0].size if new_plan.sheets else "A4"
    new_sheets = [Sheet(name=top_name, title="Top", size=size)]
    for spec in sheet_specs:
        new_sheets.append(Sheet(name=spec["name"], title=spec["name"], size=size))
    new_plan.sheets = new_sheets

    fallback = sheet_specs[0]["name"]
    for part in new_plan.parts:
        part.sheet = sheet_of_refdes.get(part.refdes, fallback)
    for zone in new_plan.zones:
        # A zone with no parts has no group; park it on the top sheet.
        zone.sheet = sheet_of_zone.get(zone.name, top_name)

    return new_plan


# ---------------------------------------------------------------------------
# Clustering (zones stay atomic)
# ---------------------------------------------------------------------------


def _coerce_plan(plan: Union[DesignPlan, dict]) -> Union[DesignPlan, str]:
    """Validate dict input; pass DesignPlan through. Returns an error string
    (not an exception) on failure so callers keep the {"ok": False} shape."""
    if isinstance(plan, DesignPlan):
        return plan
    try:
        return DesignPlan.model_validate(plan)
    except Exception as exc:  # pydantic.ValidationError, TypeError
        return f"invalid plan: {exc}"


def _cluster_units(
    plan: DesignPlan,
) -> tuple[dict[str, str], dict[str, list[str]], dict[str, Optional[str]]]:
    """Partition units: one node per zone, one per unzoned part.

    Returns (refdes -> cluster id, cluster id -> sorted refdes list,
    cluster id -> zone name or None). The ``zone:`` / ``part:`` prefixes
    keep the two namespaces from colliding.
    """
    cluster_of: dict[str, str] = {}
    cluster_parts: dict[str, list[str]] = {}
    cluster_zone: dict[str, Optional[str]] = {}
    for part in plan.parts:
        if part.zone:
            cid = f"zone:{part.zone}"
            cluster_zone[cid] = part.zone
        else:
            cid = f"part:{part.refdes}"
            cluster_zone[cid] = None
        cluster_of[part.refdes] = cid
        cluster_parts.setdefault(cid, []).append(part.refdes)
    for members in cluster_parts.values():
        members.sort()
    return cluster_of, cluster_parts, cluster_zone


def _nets_as_clusters(
    plan: DesignPlan, cluster_of: dict[str, str],
) -> list[list[str]]:
    """Each net as the list of cluster ids it touches (duplicates kept --
    partition's adjacency dedupes; rails are dropped there by fan-out)."""
    nets: list[list[str]] = []
    for net in plan.nets:
        members = [cluster_of[p.refdes] for p in net.pins
                   if p.refdes in cluster_of]
        nets.append(members)
    return nets


def _partition_clusters(
    cluster_parts: dict[str, list[str]],
    cluster_nets: list[list[str]],
    n_parts: int,
    max_parts_per_sheet: int,
) -> list[list[str]]:
    """Split clusters into groups, raising the group count until every group
    fits ``max_parts_per_sheet`` parts or no further split is possible.

    partition_netlist balances CLUSTER counts, not part counts, so the first
    cut can leave one group oversize; retrying with one more group is the
    bounded escape. A single oversize zone can never fit and is accepted.
    """
    cluster_ids = sorted(cluster_parts)
    n_clusters = len(cluster_ids)
    n_groups = max(2, math.ceil(n_parts / max_parts_per_sheet))

    best: list[list[str]] = [cluster_ids]
    while n_groups <= n_clusters:
        result = partition_netlist(cluster_ids, cluster_nets, n_groups=n_groups)
        group_of = result["group_of"]
        groups: list[list[str]] = [[] for _ in range(result["n_groups"])]
        for cid, g in group_of.items():
            groups[g].append(cid)
        groups = [sorted(g) for g in groups if g]
        best = groups
        oversize = any(
            sum(len(cluster_parts[c]) for c in g) > max_parts_per_sheet
            and len(g) > 1
            for g in groups
        )
        if not oversize:
            break
        n_groups += 1
    return best


# ---------------------------------------------------------------------------
# Sheet naming
# ---------------------------------------------------------------------------


def _sanitize_sheet_name(raw: str) -> str:
    """Lowercase, non-alphanumerics collapsed to underscores."""
    name = re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")
    return name


def _name_groups(
    groups: list[list[str]],
    cluster_parts: dict[str, list[str]],
    cluster_zone: dict[str, Optional[str]],
    plan: DesignPlan,
    reserved: set[str],
) -> list[str]:
    """Name each group from its dominant zone ROLE (most parts wins, ties
    alphabetical), falling back to the dominant zone NAME, then ``block_N``.
    Names are unique across groups and the reserved (top sheet) set."""
    role_of_zone = {z.name: (z.role or "") for z in plan.zones}
    taken = set(reserved)
    names: list[str] = []
    for i, group in enumerate(groups):
        weight: dict[str, int] = {}        # candidate label -> part count
        fallback_weight: dict[str, int] = {}
        for cid in group:
            zone = cluster_zone.get(cid)
            if not zone:
                continue
            n = len(cluster_parts[cid])
            role = role_of_zone.get(zone, "")
            if role:
                weight[role] = weight.get(role, 0) + n
            fallback_weight[zone] = fallback_weight.get(zone, 0) + n
        candidate = ""
        for pool in (weight, fallback_weight):
            if pool:
                candidate = min(pool, key=lambda k: (-pool[k], k))
                break
        base = _sanitize_sheet_name(candidate) or f"block_{i + 1}"
        name = base
        suffix = 2
        while name in taken:
            name = f"{base}_{suffix}"
            suffix += 1
        taken.add(name)
        names.append(name)
    return names


def _current_sheet_summary(plan: DesignPlan) -> list[dict[str, Any]]:
    """The plan's existing sheet assignment in the hierarchy 'sheets' shape."""
    by_sheet: dict[str, dict[str, Any]] = {
        s.name: {"name": s.name, "refdes": [], "zones": [], "part_count": 0}
        for s in plan.sheets
    }
    for part in plan.parts:
        entry = by_sheet.get(part.sheet)
        if entry is not None:
            entry["refdes"].append(part.refdes)
            entry["part_count"] += 1
    for zone in plan.zones:
        entry = by_sheet.get(zone.sheet)
        if entry is not None:
            entry["zones"].append(zone.name)
    for entry in by_sheet.values():
        entry["refdes"].sort()
        entry["zones"].sort()
    return list(by_sheet.values())


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------


def _infer_ports(
    plan: DesignPlan, sheet_of_refdes: dict[str, str],
) -> list[dict[str, str]]:
    """Inter-sheet ports from signal nets that span sheets.

    Power/ground nets (flags OR the conventional-rail names ``_wiring``
    recognises) are excluded -- they ride power-port glyphs, not sheet
    entries. Direction: the sheet holding the net's most-connected part
    (plan-wide pin-endpoint count) drives -> ``output`` from it, ``input``
    into every other sheet. A cross-sheet tie on that maximum degrades to
    ``bidirectional`` pairwise entries.
    """
    conn_count: dict[str, int] = {}
    for net in plan.nets:
        for pin in net.pins:
            conn_count[pin.refdes] = conn_count.get(pin.refdes, 0) + 1

    ports: list[dict[str, str]] = []
    for net in plan.nets:
        if _is_power_net(net) or _is_ground_net(net):
            continue
        sheet_max: dict[str, int] = {}
        for pin in net.pins:
            sheet = sheet_of_refdes.get(pin.refdes)
            if sheet is None:
                continue
            score = conn_count.get(pin.refdes, 0)
            if score > sheet_max.get(sheet, -1):
                sheet_max[sheet] = score
        if len(sheet_max) < 2:
            continue
        top_score = max(sheet_max.values())
        drivers = sorted(s for s, v in sheet_max.items() if v == top_score)
        if len(drivers) == 1:
            driver = drivers[0]
            for other in sorted(s for s in sheet_max if s != driver):
                ports.append({
                    "net": net.name,
                    "from_sheet": driver,
                    "to_sheet": other,
                    "io_type": "output",
                })
        else:
            # Ambiguous driver: pairwise bidirectional in sorted order.
            sheets = sorted(sheet_max)
            for i in range(len(sheets)):
                for j in range(i + 1, len(sheets)):
                    ports.append({
                        "net": net.name,
                        "from_sheet": sheets[i],
                        "to_sheet": sheets[j],
                        "io_type": "bidirectional",
                    })
    ports.sort(key=lambda p: (p["net"], p["from_sheet"], p["to_sheet"]))
    return ports


def _count_cut_nets(
    plan: DesignPlan, sheet_of_refdes: dict[str, str],
) -> int:
    """Nets (rails included) whose pins land on more than one sheet."""
    cut = 0
    for net in plan.nets:
        sheets = {sheet_of_refdes.get(p.refdes) for p in net.pins}
        sheets.discard(None)
        if len(sheets) > 1:
            cut += 1
    return cut


# ---------------------------------------------------------------------------
# Top-sheet ops (mils)
# ---------------------------------------------------------------------------


def _top_sheet_ops(
    sheet_names: list[str], ports: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Op list for the top sheet: TOC frame, then one sheet symbol per child
    with its sheet entries. All coordinates MILS. Op shape is
    ``{"tool": <mcp tool name>, "params": {<tool's parameters>}}``.
    """
    # Entries per sheet: a port is an output entry (right side) on its
    # from_sheet and an input entry (left side) on its to_sheet;
    # bidirectional sits left on both. Deduped per (sheet, net) -- a net
    # driving several sheets still gets one entry on the driver symbol.
    entries: dict[str, list[tuple[str, str, str]]] = {n: [] for n in sheet_names}

    def _add(sheet: str, net: str, io_type: str, side: str) -> None:
        bucket = entries.setdefault(sheet, [])
        if all(e[0] != net for e in bucket):
            bucket.append((net, io_type, side))

    for p in ports:
        if p["io_type"] == "bidirectional":
            _add(p["from_sheet"], p["net"], "bidirectional", "left")
            _add(p["to_sheet"], p["net"], "bidirectional", "left")
        else:
            _add(p["from_sheet"], p["net"], "output", "right")
            _add(p["to_sheet"], p["net"], "input", "left")

    ops: list[dict[str, Any]] = [{
        "tool": "sch_generate_toc",
        "params": {
            "x1": _TOC_BOX[0], "y1": _TOC_BOX[1],
            "x2": _TOC_BOX[2], "y2": _TOC_BOX[3],
            "title": "Table of Contents",
        },
    }]

    x1 = _SYMBOL_X1
    y_top = _COLUMN_TOP
    for name in sheet_names:
        sheet_entries = sorted(entries.get(name, []))
        per_side = max(
            sum(1 for e in sheet_entries if e[2] == "left"),
            sum(1 for e in sheet_entries if e[2] == "right"),
        )
        height = max(_SYMBOL_MIN_HEIGHT,
                     _ENTRY_FIRST * 2 + _ENTRY_PITCH * per_side)
        if y_top - height < _COLUMN_BOTTOM and y_top != _COLUMN_TOP:
            # Column full: start the next one to the right.
            x1 += _COLUMN_PITCH
            y_top = _COLUMN_TOP
        ops.append({
            "tool": "sch_place_sheet_symbol",
            "params": {
                "x1": x1,
                "y1": y_top - height,
                "x2": x1 + _SYMBOL_WIDTH,
                "y2": y_top,
                "sheet_file_name": f"{name}.SchDoc",
                "sheet_name": name,
            },
        })
        offsets = {"left": _ENTRY_FIRST, "right": _ENTRY_FIRST}
        for net, io_type, side in sheet_entries:
            ops.append({
                "tool": "sch_place_sheet_entry",
                "params": {
                    "sheet_name": name,
                    "entry_name": net,
                    "io_type": io_type,
                    "side": side,
                    "distance_from_top": offsets[side],
                },
            })
            offsets[side] += _ENTRY_PITCH
        y_top -= height + _SYMBOL_GAP
    return ops
