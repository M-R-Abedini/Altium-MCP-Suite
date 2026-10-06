# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Edit an existing DesignPlan (pure Python, offline).

The authoring primitives in :mod:`eda_agent.design.plan_blocks` only ADD --
parts, blocks, buses. But authoring is iterative: a review turns up a wrong
value, a part to drop, two nets that should be one. Doing that by re-emitting
the whole plan JSON is wasteful, and the structural edits (deleting a part
means scrubbing it from every net; merging nets means moving and de-duping
pins) are exactly the error-prone bookkeeping a tool should own.

This applies an ordered list of edit operations and returns the new plan:

* ``set_part``       -- change a part's fields (value, footprint, mpn, ...).
* ``delete_part``    -- remove a part and scrub it from every net; nets left
  empty are dropped, nets left with a single pin are flagged (now floating).
* ``set_net``        -- change a net's flags / role (fix a forgotten
  power/ground flag without re-emitting the net).
* ``connect_pin``    -- tie an existing part's pin to a net (fix an
  unconnected pin); creates the net if new.
* ``disconnect_pin`` -- remove a pin from a net (undo a wrong connection).
* ``rename_net``     -- rename a net (validates the new name).
* ``merge_nets``     -- fold one net's pins into another, then drop it.

Like :func:`eda_agent.design.plan_blocks.compose_netlist`, a failing op names
its index, and the result is not re-validated here -- the tool runs
``design_validate_plan`` once at the end so floating-net flags surface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from eda_agent.design.plan import DesignPlan, Net, Part, PinRef, _NET_PATTERN

_NET_NAME_RE = re.compile(_NET_PATTERN)

# Part fields a set_part op may change (refdes is the key, not editable).
_SETTABLE_PART_FIELDS = (
    "lib_ref", "lib_path", "value", "footprint", "manufacturer", "mpn",
    "status", "sheet", "zone", "role", "datasheet_url", "rationale",
)


@dataclass
class EditResult:
    plan: DesignPlan
    notes: list[str] = field(default_factory=list)


def _net_pins_without(net: Net, refdes: str) -> list[PinRef]:
    return [p for p in net.pins if p.refdes != refdes]


# set_part fields that change a part's BOM identity, so an existing BOM
# snapshot goes stale (needs regenerating) when one is touched.
_BOM_IDENTITY_FIELDS = ("value", "footprint", "mpn", "manufacturer", "lib_ref")


def _scrub_bom(bom: list, refdes: str, notes: list) -> None:
    """Remove ``refdes`` from every BOM line; drop lines it empties."""
    kept = []
    for line in bom:
        if refdes not in line.refdes_list:
            kept.append(line)
            continue
        remaining = [rd for rd in line.refdes_list if rd != refdes]
        if not remaining:
            notes.append(
                f"dropped BOM line {line.refdes_list} (all parts deleted)")
            continue
        kept.append(line.model_copy(update={
            "refdes_list": remaining, "qty": len(remaining)}))
    bom[:] = kept


def _delete_part(parts: dict, nets: dict, bom: list, notes: list,
                 op: dict) -> None:
    refdes = op["refdes"]
    if refdes not in parts:
        raise ValueError(f"no part {refdes!r} in the plan")
    del parts[refdes]
    for name in list(nets):
        net = nets[name]
        if not any(p.refdes == refdes for p in net.pins):
            continue
        kept = _net_pins_without(net, refdes)
        if not kept:
            del nets[name]
            notes.append(f"dropped now-empty net {name!r}")
        else:
            nets[name] = net.model_construct(**{**net.__dict__, "pins": kept})
            if len(kept) < 2:
                notes.append(
                    f"net {name!r} now has one pin (floating) after "
                    f"deleting {refdes}")
    # Scrub the deleted part from the BOM too, or it dangles (the BOM keeps
    # listing a refdes the plan no longer has -- cross_check won't catch it).
    _scrub_bom(bom, refdes, notes)
    notes.append(f"deleted part {refdes}")


def _set_part(parts: dict, nets: dict, bom: list, notes: list,
              op: dict) -> None:
    refdes = op["refdes"]
    if refdes not in parts:
        raise ValueError(f"no part {refdes!r} in the plan")
    updates = {k: op[k] for k in _SETTABLE_PART_FIELDS if k in op}
    if not updates:
        raise ValueError(
            f"set_part for {refdes} lists no settable field "
            f"({', '.join(_SETTABLE_PART_FIELDS)})")
    # Re-validate the part through the model so e.g. a bad status is caught.
    parts[refdes] = Part.model_validate(
        {**parts[refdes].model_dump(), **updates})
    notes.append(f"set {refdes}: {', '.join(sorted(updates))}")
    if bom and any(k in updates for k in _BOM_IDENTITY_FIELDS):
        notes.append(
            "BOM may be stale after this change -- regenerate with "
            "design_generate_bom")


def _rename_net(parts: dict, nets: dict, bom: list, notes: list,
                op: dict) -> None:
    old, new = op["old"], op["new"]
    if old not in nets:
        raise ValueError(f"no net {old!r} in the plan")
    if new == old:
        return
    if new in nets:
        raise ValueError(
            f"net {new!r} already exists (use merge_nets to combine)")
    if not _NET_NAME_RE.match(new):
        raise ValueError(
            f"invalid net name {new!r}: must start with a letter or "
            f"underscore, then letters, digits, or _ + - /")
    net = nets.pop(old)
    nets[new] = net.model_construct(**{**net.__dict__, "name": new})
    notes.append(f"renamed net {old} -> {new}")


def _merge_nets(parts: dict, nets: dict, bom: list, notes: list,
                op: dict) -> None:
    into, frm = op["into"], op["from"]
    if into not in nets:
        raise ValueError(f"no net {into!r} (merge target) in the plan")
    if frm not in nets:
        raise ValueError(f"no net {frm!r} (merge source) in the plan")
    if into == frm:
        raise ValueError("cannot merge a net into itself")
    target = nets[into]
    seen = {(p.refdes, p.pin) for p in target.pins}
    merged = list(target.pins)
    for p in nets[frm].pins:
        if (p.refdes, p.pin) not in seen:
            merged.append(p)
            seen.add((p.refdes, p.pin))
    # Keep the target's own flags/role; just absorb the pins.
    nets[into] = target.model_construct(**{**target.__dict__, "pins": merged})
    del nets[frm]
    notes.append(f"merged net {frm} into {into}")


# Net fields a set_net op may change (name is changed via rename_net).
_SETTABLE_NET_FIELDS = (
    "is_power", "is_ground", "role", "force_label", "force_wires",
)


def _set_net(parts: dict, nets: dict, bom: list, notes: list,
             op: dict) -> None:
    name = op["net"]
    if name not in nets:
        raise ValueError(f"no net {name!r} in the plan")
    updates = {k: op[k] for k in _SETTABLE_NET_FIELDS if k in op}
    if not updates:
        raise ValueError(
            f"set_net for {name} lists no settable field "
            f"({', '.join(_SETTABLE_NET_FIELDS)})")
    # Re-validate through the model so e.g. force_label + force_wires (which
    # are mutually exclusive) is caught here rather than at emit.
    nets[name] = Net.model_validate({**nets[name].model_dump(), **updates})
    notes.append(f"set net {name}: {', '.join(sorted(updates))}")


def _connect_pin(parts: dict, nets: dict, bom: list, notes: list,
                 op: dict) -> None:
    name, refdes, pin = op["net"], op["refdes"], str(op["pin"])
    if refdes not in parts:
        raise ValueError(f"no part {refdes!r} in the plan")
    if name not in nets and not _NET_NAME_RE.match(name):
        raise ValueError(
            f"invalid net name {name!r}: must start with a letter or "
            f"underscore, then letters, digits, or _ + - /")
    ref = PinRef(refdes=refdes, pin=pin)
    if name in nets:
        net = nets[name]
        if any(p.refdes == refdes and p.pin == pin for p in net.pins):
            return     # already connected -- idempotent
        nets[name] = net.model_construct(
            **{**net.__dict__, "pins": [*net.pins, ref]})
    else:
        nets[name] = Net.model_construct(
            name=name, pins=[ref], is_power=False, is_ground=False,
            role=None, force_label=False, force_wires=False)
        notes.append(f"created net {name!r}")
    notes.append(f"connected {refdes}.{pin} to {name}")


def _disconnect_pin(parts: dict, nets: dict, bom: list, notes: list,
                    op: dict) -> None:
    name, refdes, pin = op["net"], op["refdes"], str(op["pin"])
    if name not in nets:
        raise ValueError(f"no net {name!r} in the plan")
    net = nets[name]
    kept = [p for p in net.pins if not (p.refdes == refdes and p.pin == pin)]
    if len(kept) == len(net.pins):
        raise ValueError(f"{refdes}.{pin} is not on net {name}")
    if not kept:
        del nets[name]
        notes.append(f"dropped now-empty net {name!r}")
    else:
        nets[name] = net.model_construct(**{**net.__dict__, "pins": kept})
        if len(kept) < 2:
            notes.append(f"net {name!r} now has one pin (floating)")
    notes.append(f"disconnected {refdes}.{pin} from {name}")


_EDIT_OPS = {
    "set_part": _set_part,
    "delete_part": _delete_part,
    "set_net": _set_net,
    "connect_pin": _connect_pin,
    "disconnect_pin": _disconnect_pin,
    "rename_net": _rename_net,
    "merge_nets": _merge_nets,
}


def edit_plan(plan: DesignPlan, operations: list[dict]) -> EditResult:
    """Apply an ordered list of edit operations to ``plan``.

    Args:
        plan: the DesignPlan to edit (not mutated; a new plan is returned).
        operations: ordered op dicts, each keyed by ``"op"``:

            * ``{"op": "set_part", "refdes", <field>: <value>, ...}``
            * ``{"op": "delete_part", "refdes"}``
            * ``{"op": "set_net", "net", <flag/role>: <value>, ...}``
            * ``{"op": "connect_pin", "net", "refdes", "pin"}``
            * ``{"op": "disconnect_pin", "net", "refdes", "pin"}``
            * ``{"op": "rename_net", "old", "new"}``
            * ``{"op": "merge_nets", "into", "from"}``

    Returns:
        An :class:`EditResult` with the new plan and per-op notes (dropped
        nets, newly floating nets, ...). Not re-validated here.

    Raises:
        ValueError: an unknown op or a failing op -- the message names the
            operation's index.
    """
    parts = {p.refdes: p for p in plan.parts}
    nets = {n.name: n for n in plan.nets}
    bom = list(plan.bom)
    notes: list[str] = []
    for idx, op in enumerate(operations):
        kind = (op.get("op") or "").strip().lower()
        fn = _EDIT_OPS.get(kind)
        if fn is None:
            raise ValueError(
                f"operation {idx}: unknown op {op.get('op')!r}; use "
                f"{sorted(_EDIT_OPS)}")
        try:
            fn(parts, nets, bom, notes, op)
        except (KeyError, ValueError) as exc:
            raise ValueError(f"operation {idx} ({kind}): {exc}") from exc
    new_plan = plan.model_copy(update={
        "parts": list(parts.values()),
        "nets": list(nets.values()),
        "bom": bom,
    })
    return EditResult(plan=new_plan, notes=notes)


__all__ = ["EditResult", "edit_plan"]
