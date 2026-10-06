# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Canonical circuit-block authoring (pure Python, offline).

Hand-authoring a netlist in plan JSON is boilerplate-heavy: a decoupling
network is four capacitors, eight pin endpoints, and two net merges that
must all line up; a voltage divider is two resistors plus a fresh midpoint
net. This module emits the canonical *topology* of common blocks and folds
it into an existing :class:`~eda_agent.design.plan.DesignPlan` -- allocating
unique refdes, wiring every pin to the right net, and tagging power/ground
and roles -- so one call does what was a dozen careful JSON edits.

It is deliberately **naming-agnostic**: it hardcodes NO part library. The
caller (the planner) supplies every part identity -- ``lib_ref``, ``value``,
``footprint``, pin names -- chosen semantically from the live inventory.
The builder only owns the wiring pattern, never the part choice. That keeps
it usable against any library and any naming convention.

The blocks:

* ``decoupling``     -- N bypass caps across a power rail and ground.
* ``pullup``         -- a resistor from a signal to a rail.
* ``pulldown``       -- a resistor from a signal to ground.
* ``series_resistor``-- a resistor bridging two nets (termination / limit).
* ``voltage_divider``-- top/bottom resistors with a fresh midpoint net.
* ``rc_lowpass``     -- series R + shunt C to ground (fresh output net).
* ``rc_highpass``    -- series C + shunt R to ground (AC coupling).
* ``led_indicator``  -- series R + LED between two nets (fresh mid node).
* ``crystal``        -- crystal across two oscillator nets + a matched
  pair of load caps to ground.
* ``pi_filter``      -- shunt C, series L/ferrite, shunt C (fresh output).
* ``mosfet_low_side``-- gate series R + gate pull-down + FET switching a
  load low-side from a control signal.
* ``mosfet_high_side``- gate series R + gate pull-up + PMOS switching a
  load high-side (source at the rail).

Every block returns a :class:`BlockResult` carrying the augmented plan plus
exactly what it added, so the planner can report and the offline ERC-lite
(:mod:`eda_agent.design.plan_erc`) can re-validate before the Altium
round-trip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from eda_agent.design.plan import DesignPlan, Net, Part, PinRef, _NET_PATTERN

# Nets are materialized leniently (model_construct) so a net can grow one
# pin at a time, which also skips the schema's name-pattern check. Validate
# new net names here so a bad name (e.g. "3V3", which must start with a
# letter or underscore) raises a clear error at authoring time instead of
# surfacing later as a confusing downstream validation failure.
_NET_NAME_RE = re.compile(_NET_PATTERN)


@dataclass
class BlockResult:
    """A block folded into a plan: the new plan plus what changed."""

    plan: DesignPlan
    added_refdes: list[str] = field(default_factory=list)
    added_nets: list[str] = field(default_factory=list)
    extended_nets: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --- shared mechanics ---------------------------------------------------

class _Builder:
    """Mutable view over a plan's parts/nets while a block is folded in.

    Holds parts as a list and nets as an insertion-ordered dict so pin
    merges and fresh-net creation stay O(1) and deterministic, then
    rebuilds an immutable DesignPlan at the end.
    """

    def __init__(self, plan: DesignPlan):
        self._plan = plan
        self.parts: list[Part] = list(plan.parts)
        # Nets are held as mutable dicts while pins accumulate -- the Net
        # model forbids <2 pins, so a net being built one endpoint at a
        # time can't be a live Net until it is complete. Materialized in
        # result().
        self.nets: dict[str, dict] = {
            n.name: n.model_dump() for n in plan.nets}
        self.added_refdes: list[str] = []
        self.added_nets: list[str] = []
        self.extended_nets: list[str] = []
        self.notes: list[str] = []
        # Applied to every part the block creates unless the call passes
        # its own lib_path -- lets a multi-library project pin where the
        # block's passives come from.
        self.default_lib_path: str | None = None

    def next_refdes(self, prefix: str) -> str:
        """Lowest free ``<prefix><n>`` not already used by any part."""
        used = set()
        for p in self.parts:
            rd = p.refdes
            if rd.startswith(prefix) and rd[len(prefix):].isdigit():
                used.add(int(rd[len(prefix):]))
        n = 1
        while n in used:
            n += 1
        return f"{prefix}{n}"

    def fresh_net_name(self, base: str) -> str:
        """A net name based on ``base`` that does not collide."""
        if base not in self.nets:
            return base
        i = 2
        while f"{base}_{i}" in self.nets:
            i += 1
        return f"{base}_{i}"

    def add_part(self, **kw) -> Part:
        if not kw.get("lib_path"):
            kw["lib_path"] = self.default_lib_path
        part = Part(**kw)
        self.parts.append(part)
        self.added_refdes.append(part.refdes)
        return part

    def connect(self, net_name: str, refdes: str, pin: str, *,
                is_power: bool = False, is_ground: bool = False,
                role: str | None = None) -> None:
        """Tie ``refdes.pin`` to ``net_name``, creating the net if new.

        Power/ground flags and ``role`` apply only when the net is
        created here -- an existing net keeps the caller's own flags, so
        a block never silently re-types a rail it merely taps into.
        """
        ref = {"refdes": refdes, "pin": str(pin)}
        net = self.nets.get(net_name)
        if net is None:
            if not _NET_NAME_RE.match(net_name):
                raise ValueError(
                    f"invalid net name {net_name!r}: a net name must start "
                    f"with a letter or underscore, then letters, digits, or "
                    f"_ + - /")
            self.nets[net_name] = {
                "name": net_name, "pins": [ref], "is_power": is_power,
                "is_ground": is_ground, "role": role,
                "force_label": False, "force_wires": False}
            self.added_nets.append(net_name)
        else:
            if any(p["refdes"] == ref["refdes"] and p["pin"] == ref["pin"]
                   for p in net["pins"]):
                return
            net["pins"].append(ref)
            if net_name not in self.added_nets \
                    and net_name not in self.extended_nets:
                self.extended_nets.append(net_name)

    def result(self) -> BlockResult:
        # Materialize nets leniently: a net the block under-populated (a
        # pullup's signal endpoint the caller has yet to wire) stays a
        # 1-pin net here. The result is intentionally NOT validated -- the
        # tool runs design_validate_plan, which reports such a net as the
        # floating-net error the caller must resolve.
        nets = []
        for n in self.nets.values():
            pins = [PinRef(refdes=p["refdes"], pin=p["pin"])
                    for p in n["pins"]]
            nets.append(Net.model_construct(**{**n, "pins": pins}))
        plan = self._plan.model_copy(update={
            "parts": self.parts,
            "nets": nets,
        })
        return BlockResult(
            plan=plan, added_refdes=self.added_refdes,
            added_nets=self.added_nets, extended_nets=self.extended_nets,
            notes=self.notes)


def _pins(params: dict, default: tuple[str, str]) -> tuple[str, str]:
    """Two pin names from params['pins'], falling back to default."""
    p = params.get("pins")
    if not p:
        return default
    if len(p) < 2:
        raise ValueError("pins needs two pin names")
    return str(p[0]), str(p[1])


def _require(params: dict, *keys: str) -> None:
    missing = [k for k in keys if not params.get(k)]
    if missing:
        raise ValueError(
            f"block needs {', '.join(missing)} in params")


def _two_pin(b: _Builder, params: dict, prefix: str,
             net_a: str, net_b: str, *,
             a_power=False, a_ground=False, b_power=False, b_ground=False,
             role: str | None = None) -> str:
    """Place one 2-pin part (R/C/L/D) bridging net_a (pin1) and net_b."""
    p1, p2 = _pins(params, ("1", "2"))
    rd = b.next_refdes(prefix)
    b.add_part(
        refdes=rd, lib_ref=params["lib_ref"], value=params.get("value"),
        footprint=params.get("footprint"),
        manufacturer=params.get("manufacturer"), mpn=params.get("mpn"),
        datasheet_url=params.get("datasheet_url"),
        sheet=params.get("sheet", "main"), role=role)
    b.connect(net_a, rd, p1, is_power=a_power, is_ground=a_ground)
    b.connect(net_b, rd, p2, is_power=b_power, is_ground=b_ground)
    return rd


# --- blocks -------------------------------------------------------------

def _decoupling(b: _Builder, params: dict) -> None:
    _require(params, "power_net", "ground_net", "lib_ref")
    count = int(params.get("count", 1))
    if count < 1:
        raise ValueError("decoupling count must be >= 1")
    p1, p2 = _pins(params, ("1", "2"))
    for _ in range(count):
        rd = b.next_refdes(params.get("refdes_prefix", "C"))
        b.add_part(
            refdes=rd, lib_ref=params["lib_ref"], value=params.get("value"),
            footprint=params.get("footprint"),
            manufacturer=params.get("manufacturer"), mpn=params.get("mpn"),
            datasheet_url=params.get("datasheet_url"),
            sheet=params.get("sheet", "main"), role="decoup_cap")
        b.connect(params["power_net"], rd, p1, is_power=True)
        b.connect(params["ground_net"], rd, p2, is_ground=True)


def _pullup(b: _Builder, params: dict) -> None:
    _require(params, "signal_net", "rail_net", "lib_ref")
    _two_pin(b, params, params.get("refdes_prefix", "R"),
             params["rail_net"], params["signal_net"],
             a_power=True, role="pullup")


def _pulldown(b: _Builder, params: dict) -> None:
    _require(params, "signal_net", "ground_net", "lib_ref")
    _two_pin(b, params, params.get("refdes_prefix", "R"),
             params["signal_net"], params["ground_net"],
             b_ground=True, role="pulldown")


def _series_resistor(b: _Builder, params: dict) -> None:
    _require(params, "net_a", "net_b", "lib_ref")
    _two_pin(b, params, params.get("refdes_prefix", "R"),
             params["net_a"], params["net_b"], role="series")


def _voltage_divider(b: _Builder, params: dict) -> None:
    _require(params, "rail_net", "ground_net", "lib_ref_top",
             "lib_ref_bottom")
    out = params.get("output_net") or b.fresh_net_name("VDIV_OUT")
    p1, p2 = _pins(params, ("1", "2"))
    # top: rail -> output
    rt = b.next_refdes(params.get("refdes_prefix", "R"))
    b.add_part(refdes=rt, lib_ref=params["lib_ref_top"],
               value=params.get("value_top"),
               footprint=params.get("footprint_top"),
               sheet=params.get("sheet", "main"), role="rdiv_top")
    b.connect(params["rail_net"], rt, p1, is_power=True)
    b.connect(out, rt, p2, role="feedback")
    # bottom: output -> ground
    rb = b.next_refdes(params.get("refdes_prefix", "R"))
    b.add_part(refdes=rb, lib_ref=params["lib_ref_bottom"],
               value=params.get("value_bottom"),
               footprint=params.get("footprint_bottom"),
               sheet=params.get("sheet", "main"), role="rdiv_bot")
    b.connect(out, rb, p1)
    b.connect(params["ground_net"], rb, p2, is_ground=True)
    b.notes.append(f"divider output net = {out}")


def _rc_lowpass(b: _Builder, params: dict) -> None:
    _require(params, "input_net", "ground_net", "lib_ref_r", "lib_ref_c")
    out = params.get("output_net") or b.fresh_net_name("RC_OUT")
    rp1, rp2 = _pins(params, ("1", "2"))
    # series R: input -> output
    rd = b.next_refdes(params.get("r_prefix", "R"))
    b.add_part(refdes=rd, lib_ref=params["lib_ref_r"],
               value=params.get("value_r"),
               footprint=params.get("footprint_r"),
               sheet=params.get("sheet", "main"), role="filter_r")
    b.connect(params["input_net"], rd, rp1)
    b.connect(out, rd, rp2)
    # shunt C: output -> ground
    cd = b.next_refdes(params.get("c_prefix", "C"))
    b.add_part(refdes=cd, lib_ref=params["lib_ref_c"],
               value=params.get("value_c"),
               footprint=params.get("footprint_c"),
               sheet=params.get("sheet", "main"), role="filter_c")
    b.connect(out, cd, "1")
    b.connect(params["ground_net"], cd, "2", is_ground=True)
    b.notes.append(f"filter output net = {out}")


def _rc_highpass(b: _Builder, params: dict) -> None:
    _require(params, "input_net", "ground_net", "lib_ref_r", "lib_ref_c")
    out = params.get("output_net") or b.fresh_net_name("RC_OUT")
    cp1, cp2 = _pins(params, ("1", "2"))
    # series C: input -> output (blocks DC, passes AC -- the high-pass /
    # AC-coupling element).
    cd = b.next_refdes(params.get("c_prefix", "C"))
    b.add_part(refdes=cd, lib_ref=params["lib_ref_c"],
               value=params.get("value_c"),
               footprint=params.get("footprint_c"),
               sheet=params.get("sheet", "main"), role="filter_c")
    b.connect(params["input_net"], cd, cp1)
    b.connect(out, cd, cp2)
    # shunt R: output -> ground (sets the corner / provides a DC path).
    rd = b.next_refdes(params.get("r_prefix", "R"))
    b.add_part(refdes=rd, lib_ref=params["lib_ref_r"],
               value=params.get("value_r"),
               footprint=params.get("footprint_r"),
               sheet=params.get("sheet", "main"), role="filter_r")
    b.connect(out, rd, "1")
    b.connect(params["ground_net"], rd, "2", is_ground=True)
    b.notes.append(f"filter output net = {out}")


def _led_indicator(b: _Builder, params: dict) -> None:
    _require(params, "anode_net", "cathode_net", "lib_ref_r", "lib_ref_led")
    mid = params.get("mid_net") or b.fresh_net_name("LED_A")
    # series R: anode_net -> mid
    rd = b.next_refdes(params.get("r_prefix", "R"))
    b.add_part(refdes=rd, lib_ref=params["lib_ref_r"],
               value=params.get("value_r"),
               footprint=params.get("footprint_r"),
               sheet=params.get("sheet", "main"), role="led_limit")
    b.connect(params["anode_net"], rd, "1")
    b.connect(mid, rd, "2")
    # LED: mid (anode) -> cathode_net
    ap, kp = _pins(params, ("A", "K"))
    dd = b.next_refdes(params.get("led_prefix", "D"))
    b.add_part(refdes=dd, lib_ref=params["lib_ref_led"],
               value=params.get("value_led"),
               footprint=params.get("footprint_led"),
               sheet=params.get("sheet", "main"), role="indicator")
    b.connect(mid, dd, ap)
    b.connect(params["cathode_net"], dd, kp)


def _crystal(b: _Builder, params: dict) -> None:
    _require(params, "xin_net", "xout_net", "ground_net", "lib_ref",
             "cap_lib_ref")
    xp1, xp2 = _pins(params, ("1", "2"))
    # The crystal across the two oscillator nets.
    yd = b.next_refdes(params.get("refdes_prefix", "Y"))
    b.add_part(refdes=yd, lib_ref=params["lib_ref"],
               value=params.get("value"),
               footprint=params.get("footprint"),
               manufacturer=params.get("manufacturer"),
               mpn=params.get("mpn"),
               datasheet_url=params.get("datasheet_url"),
               sheet=params.get("sheet", "main"), role="crystal")
    b.connect(params["xin_net"], yd, xp1)
    b.connect(params["xout_net"], yd, xp2)
    # Two load caps, ONE shared value so the pair stays matched (the
    # crystal datasheet's CL applies equally to both legs).
    cp1, cp2 = _pins(params, ("1", "2"))
    cval = params.get("cap_value")
    cfp = params.get("cap_footprint")
    cprefix = params.get("cap_prefix", "C")
    for net, role in ((params["xin_net"], "crystal_cap_l"),
                      (params["xout_net"], "crystal_cap_r")):
        cd = b.next_refdes(cprefix)
        b.add_part(refdes=cd, lib_ref=params["cap_lib_ref"], value=cval,
                   footprint=cfp, sheet=params.get("sheet", "main"),
                   role=role)
        b.connect(net, cd, cp1)
        b.connect(params["ground_net"], cd, cp2, is_ground=True)


def _pi_filter(b: _Builder, params: dict) -> None:
    _require(params, "input_net", "ground_net", "lib_ref_l", "lib_ref_c")
    out = params.get("output_net") or b.fresh_net_name("PI_OUT")
    cval = params.get("value_c")
    cfp = params.get("footprint_c")
    cprefix = params.get("c_prefix", "C")
    # input shunt cap -> ground
    cin = b.next_refdes(cprefix)
    b.add_part(refdes=cin, lib_ref=params["lib_ref_c"], value=cval,
               footprint=cfp, sheet=params.get("sheet", "main"),
               role="pi_cap_in")
    b.connect(params["input_net"], cin, "1")
    b.connect(params["ground_net"], cin, "2", is_ground=True)
    # series L / ferrite: input -> output
    ld = b.next_refdes(params.get("l_prefix", "L"))
    b.add_part(refdes=ld, lib_ref=params["lib_ref_l"],
               value=params.get("value_l"),
               footprint=params.get("footprint_l"),
               sheet=params.get("sheet", "main"), role="pi_series")
    b.connect(params["input_net"], ld, "1")
    b.connect(out, ld, "2")
    # output shunt cap -> ground
    cout = b.next_refdes(cprefix)
    b.add_part(refdes=cout, lib_ref=params["lib_ref_c"], value=cval,
               footprint=cfp, sheet=params.get("sheet", "main"),
               role="pi_cap_out")
    b.connect(out, cout, "1")
    b.connect(params["ground_net"], cout, "2", is_ground=True)
    b.notes.append(f"pi-filter output net = {out}")


def _mosfet_low_side(b: _Builder, params: dict) -> None:
    _require(params, "control_net", "load_net", "ground_net", "lib_ref_r",
             "lib_ref_fet")
    gate = params.get("gate_net") or b.fresh_net_name("GATE")
    gp = params.get("fet_pins") or ("G", "D", "S")
    if len(gp) < 3:
        raise ValueError("fet_pins needs gate, drain, source names")
    g_pin, d_pin, s_pin = str(gp[0]), str(gp[1]), str(gp[2])
    sheet = params.get("sheet", "main")
    # Gate series resistor: control signal -> gate (damps ringing / limits
    # the MCU pin's drive into the gate charge).
    rg = b.next_refdes(params.get("r_prefix", "R"))
    b.add_part(refdes=rg, lib_ref=params["lib_ref_r"],
               value=params.get("value_r_gate"),
               footprint=params.get("footprint_r"), sheet=sheet,
               role="gate_series")
    b.connect(params["control_net"], rg, "1")
    b.connect(gate, rg, "2")
    # The FET: drain switches the load, source to ground.
    q = b.next_refdes(params.get("fet_prefix", "Q"))
    b.add_part(refdes=q, lib_ref=params["lib_ref_fet"],
               value=params.get("value_fet"),
               footprint=params.get("footprint_fet"),
               manufacturer=params.get("manufacturer"), mpn=params.get("mpn"),
               datasheet_url=params.get("datasheet_url"), sheet=sheet,
               role="switch")
    b.connect(gate, q, g_pin)
    b.connect(params["load_net"], q, d_pin)
    b.connect(params["ground_net"], q, s_pin, is_ground=True)
    # Gate pull-down keeps the FET off while the MCU pin floats (reset /
    # boot). On by default -- the safe choice for a switch.
    if params.get("pulldown", True):
        rpd = b.next_refdes(params.get("r_prefix", "R"))
        b.add_part(refdes=rpd,
                   lib_ref=params.get("lib_ref_r_pulldown")
                   or params["lib_ref_r"],
                   value=params.get("value_r_pulldown"),
                   footprint=params.get("footprint_r"), sheet=sheet,
                   role="gate_pulldown")
        b.connect(gate, rpd, "1")
        b.connect(params["ground_net"], rpd, "2", is_ground=True)
    b.notes.append(f"gate net = {gate}")


def _mosfet_high_side(b: _Builder, params: dict) -> None:
    _require(params, "control_net", "load_net", "rail_net", "lib_ref_r",
             "lib_ref_fet")
    gate = params.get("gate_net") or b.fresh_net_name("GATE")
    # PMOS pin order is gate, SOURCE, drain -- the source sits at the high
    # rail (the mirror of the low-side NMOS whose source is at ground).
    gp = params.get("fet_pins") or ("G", "S", "D")
    if len(gp) < 3:
        raise ValueError("fet_pins needs gate, source, drain names")
    g_pin, s_pin, d_pin = str(gp[0]), str(gp[1]), str(gp[2])
    sheet = params.get("sheet", "main")
    # Gate series resistor: control signal -> gate.
    rg = b.next_refdes(params.get("r_prefix", "R"))
    b.add_part(refdes=rg, lib_ref=params["lib_ref_r"],
               value=params.get("value_r_gate"),
               footprint=params.get("footprint_r"), sheet=sheet,
               role="gate_series")
    b.connect(params["control_net"], rg, "1")
    b.connect(gate, rg, "2")
    # The PMOS: source to the rail (V+), drain switches the load.
    q = b.next_refdes(params.get("fet_prefix", "Q"))
    b.add_part(refdes=q, lib_ref=params["lib_ref_fet"],
               value=params.get("value_fet"),
               footprint=params.get("footprint_fet"),
               manufacturer=params.get("manufacturer"), mpn=params.get("mpn"),
               datasheet_url=params.get("datasheet_url"), sheet=sheet,
               role="switch")
    b.connect(gate, q, g_pin)
    b.connect(params["rail_net"], q, s_pin, is_power=True)
    b.connect(params["load_net"], q, d_pin)
    # Gate pull-UP to the rail keeps the PMOS off while the control floats
    # (Vgs = 0). On by default -- the safe choice (mirror of the low-side
    # pull-down to ground).
    if params.get("pullup", True):
        rpu = b.next_refdes(params.get("r_prefix", "R"))
        b.add_part(refdes=rpu,
                   lib_ref=params.get("lib_ref_r_pullup")
                   or params["lib_ref_r"],
                   value=params.get("value_r_pullup"),
                   footprint=params.get("footprint_r"), sheet=sheet,
                   role="gate_pullup")
        b.connect(gate, rpu, "1")
        b.connect(params["rail_net"], rpu, "2", is_power=True)
    b.notes.append(f"gate net = {gate}")


_BLOCKS = {
    "decoupling": _decoupling,
    "pullup": _pullup,
    "pulldown": _pulldown,
    "series_resistor": _series_resistor,
    "voltage_divider": _voltage_divider,
    "rc_lowpass": _rc_lowpass,
    "rc_highpass": _rc_highpass,
    "led_indicator": _led_indicator,
    "crystal": _crystal,
    "pi_filter": _pi_filter,
    "mosfet_low_side": _mosfet_low_side,
    "mosfet_high_side": _mosfet_high_side,
}


# Optional params every block accepts. ``pins`` overrides the two default
# pin names; ``sheet`` homes the parts; ``lib_path`` pins their library.
_COMMON_OPTIONAL = ("sheet", "lib_path", "pins")

# The param contract per block, as data -- the single source of truth the
# introspection tool reports and add_block checks unknown params against.
# Keep ``required`` in sync with each block's _require() call (the
# consistency test in test_plan_blocks asserts this).
BLOCK_SPECS: dict[str, dict] = {
    "decoupling": {
        "summary": "N bypass caps across a power rail and ground.",
        "required": ["power_net", "ground_net", "lib_ref"],
        "optional": ["count", "value", "footprint", "manufacturer", "mpn",
                     "datasheet_url", "refdes_prefix"],
        "creates_nets": [],
    },
    "pullup": {
        "summary": "A resistor from a signal up to a rail.",
        "required": ["signal_net", "rail_net", "lib_ref"],
        "optional": ["value", "footprint", "refdes_prefix", "manufacturer",
                     "mpn", "datasheet_url"],
        "creates_nets": [],
    },
    "pulldown": {
        "summary": "A resistor from a signal down to ground.",
        "required": ["signal_net", "ground_net", "lib_ref"],
        "optional": ["value", "footprint", "refdes_prefix", "manufacturer",
                     "mpn", "datasheet_url"],
        "creates_nets": [],
    },
    "series_resistor": {
        "summary": "A resistor bridging two nets (termination / limit).",
        "required": ["net_a", "net_b", "lib_ref"],
        "optional": ["value", "footprint", "refdes_prefix", "manufacturer",
                     "mpn", "datasheet_url"],
        "creates_nets": [],
    },
    "voltage_divider": {
        "summary": "Top/bottom resistors with a fresh midpoint output net.",
        "required": ["rail_net", "ground_net", "lib_ref_top",
                     "lib_ref_bottom"],
        "optional": ["value_top", "value_bottom", "footprint_top",
                     "footprint_bottom", "output_net", "refdes_prefix"],
        "creates_nets": ["output_net (default VDIV_OUT)"],
    },
    "rc_lowpass": {
        "summary": "Series R + shunt C to ground (fresh output net).",
        "required": ["input_net", "ground_net", "lib_ref_r", "lib_ref_c"],
        "optional": ["value_r", "value_c", "footprint_r", "footprint_c",
                     "output_net", "r_prefix", "c_prefix"],
        "creates_nets": ["output_net (default RC_OUT)"],
    },
    "rc_highpass": {
        "summary": "Series C + shunt R to ground -- high-pass / AC "
                   "coupling (fresh output net).",
        "required": ["input_net", "ground_net", "lib_ref_r", "lib_ref_c"],
        "optional": ["value_r", "value_c", "footprint_r", "footprint_c",
                     "output_net", "r_prefix", "c_prefix"],
        "creates_nets": ["output_net (default RC_OUT)"],
    },
    "led_indicator": {
        "summary": "Series R + LED between two nets (fresh mid node).",
        "required": ["anode_net", "cathode_net", "lib_ref_r", "lib_ref_led"],
        "optional": ["value_r", "value_led", "footprint_r", "footprint_led",
                     "mid_net", "r_prefix", "led_prefix"],
        "creates_nets": ["mid_net (default LED_A)"],
    },
    "crystal": {
        "summary": "Crystal across two oscillator nets + a matched pair of "
                   "load caps to ground.",
        "required": ["xin_net", "xout_net", "ground_net", "lib_ref",
                     "cap_lib_ref"],
        "optional": ["value", "cap_value", "footprint", "cap_footprint",
                     "refdes_prefix", "cap_prefix", "manufacturer", "mpn",
                     "datasheet_url"],
        "creates_nets": [],
    },
    "pi_filter": {
        "summary": "Shunt C, series L/ferrite, shunt C (fresh output net).",
        "required": ["input_net", "ground_net", "lib_ref_l", "lib_ref_c"],
        "optional": ["value_l", "value_c", "footprint_l", "footprint_c",
                     "output_net", "l_prefix", "c_prefix"],
        "creates_nets": ["output_net (default PI_OUT)"],
    },
    "mosfet_low_side": {
        "summary": "Gate series R + gate pull-down + FET driving a load "
                   "low-side from a control signal.",
        "required": ["control_net", "load_net", "ground_net", "lib_ref_r",
                     "lib_ref_fet"],
        "optional": ["value_r_gate", "value_r_pulldown", "lib_ref_r_pulldown",
                     "value_fet", "footprint_r", "footprint_fet", "gate_net",
                     "fet_pins", "pulldown", "r_prefix", "fet_prefix",
                     "manufacturer", "mpn", "datasheet_url"],
        "creates_nets": ["gate_net (default GATE)"],
    },
    "mosfet_high_side": {
        "summary": "Gate series R + gate pull-up + PMOS driving a load "
                   "high-side from a control signal (source at the rail).",
        "required": ["control_net", "load_net", "rail_net", "lib_ref_r",
                     "lib_ref_fet"],
        "optional": ["value_r_gate", "value_r_pullup", "lib_ref_r_pullup",
                     "value_fet", "footprint_r", "footprint_fet", "gate_net",
                     "fet_pins", "pullup", "r_prefix", "fet_prefix",
                     "manufacturer", "mpn", "datasheet_url"],
        "creates_nets": ["gate_net (default GATE)"],
    },
}


def describe_blocks() -> list[dict]:
    """Return the param contract for every block, for discoverability.

    Each entry: ``{name, summary, required, optional, creates_nets}``
    where ``optional`` already folds in the params every block accepts
    (``sheet`` / ``lib_path`` / ``pins``). The planner reads this to call
    a block with the right parameter names instead of guessing.
    """
    out: list[dict] = []
    for name in _BLOCKS:
        spec = BLOCK_SPECS[name]
        out.append({
            "name": name,
            "summary": spec["summary"],
            "required": list(spec["required"]),
            "optional": list(spec["optional"]) + list(_COMMON_OPTIONAL),
            "creates_nets": list(spec["creates_nets"]),
        })
    return out


def _unknown_params(block: str, params: dict) -> list[str]:
    """Param keys the block does not recognise (likely typos)."""
    spec = BLOCK_SPECS.get(block)
    if not spec:
        return []
    known = set(spec["required"]) | set(spec["optional"]) \
        | set(_COMMON_OPTIONAL)
    return sorted(k for k in params if k not in known)


def add_block(plan: DesignPlan, block: str, params: dict) -> BlockResult:
    """Fold a canonical circuit ``block`` into ``plan``.

    Args:
        plan: the DesignPlan to extend (returned unmodified; the result
            carries a new plan).
        block: one of the keys in ``BLOCK_SPECS`` (see
            ``describe_blocks()``): ``decoupling``, ``pullup``,
            ``pulldown``, ``series_resistor``, ``voltage_divider``,
            ``rc_lowpass``, ``rc_highpass``, ``led_indicator``,
            ``crystal``, ``pi_filter``, ``mosfet_low_side``,
            ``mosfet_high_side``.
        params: block parameters. Common keys: part identity (``lib_ref``
            / ``lib_ref_top`` / ..., ``value``, ``footprint``,
            ``manufacturer``, ``mpn``, ``datasheet_url``), ``lib_path``
            (applied to every part the block creates), net names (per
            block), ``pins`` (a 2-tuple overriding the default pin names),
            ``refdes_prefix``, and ``sheet``.

    Returns:
        A :class:`BlockResult` whose ``plan`` includes the new parts and
        net wiring. The plan is NOT re-validated here; run it through
        ``design_validate_plan`` for the schema + ERC-lite gate.

    Raises:
        ValueError: unknown block name or a missing required parameter.
    """
    key = block.strip().lower()
    fn = _BLOCKS.get(key)
    if fn is None:
        raise ValueError(
            f"unknown block {block!r}; use one of {sorted(_BLOCKS)}")
    params = params or {}
    b = _Builder(plan)
    b.default_lib_path = params.get("lib_path")
    fn(b, params)
    # Surface params the block did not recognise -- a typo'd optional key
    # (e.g. 'val' for 'value') would otherwise be silently dropped and the
    # part emitted without its value, an error ERC can't catch.
    unknown = _unknown_params(key, params)
    if unknown:
        b.notes.append(f"ignored unrecognised params: {unknown}")
    return b.result()


def add_component(
    plan: DesignPlan,
    *,
    refdes: str,
    lib_ref: str,
    connections: dict,
    value: str | None = None,
    footprint: str | None = None,
    lib_path: str | None = None,
    sheet: str = "main",
    manufacturer: str | None = None,
    mpn: str | None = None,
    datasheet_url: str | None = None,
    role: str | None = None,
    power_nets: tuple = (),
    ground_nets: tuple = (),
    net_roles: dict | None = None,
) -> BlockResult:
    """Add one part and wire its pins to named nets, in one call.

    The atomic authoring primitive the blocks build on: where
    :func:`add_block` owns a canonical multi-part topology and
    :func:`connect_bus` wires a wide interface, this places a single
    arbitrary part -- an MCU, a connector, a regulator -- and folds each
    of its pins into a net. ``connections`` is a pin->net map, the shape a
    datasheet pinout reads in; pins that map to the same net (an IC's five
    VCC pins) are merged onto one net automatically, so the caller never
    maintains a net's pin list by hand.

    It is naming-agnostic: the caller supplies the part identity and the
    net names; this owns only the pin->net folding and refdes/flag
    bookkeeping.

    Args:
        plan: the DesignPlan to extend.
        refdes: the new part's designator (must be free in the plan).
        lib_ref: its library symbol name.
        connections: ``{pin: net_name}``. A net named here is created if
            absent, or merged into if it already exists.
        value / footprint / lib_path / sheet / manufacturer / mpn /
            datasheet_url / role: standard Part fields.
        power_nets / ground_nets: net names to flag as power / ground when
            this call creates them (an existing net keeps its own flags).
        net_roles: ``{net_name: role}`` applied to nets created here.

    Returns:
        A :class:`BlockResult` carrying the augmented plan.

    Raises:
        ValueError: ``refdes`` already in the plan, or empty
            ``connections``.
    """
    if not refdes:
        raise ValueError("refdes is required")
    if any(p.refdes == refdes for p in plan.parts):
        raise ValueError(f"refdes {refdes!r} already exists in the plan")
    if not connections:
        raise ValueError("connections must map at least one pin to a net")

    power = set(power_nets or ())
    ground = set(ground_nets or ())
    roles = net_roles or {}
    b = _Builder(plan)
    b.default_lib_path = lib_path
    b.add_part(refdes=refdes, lib_ref=lib_ref, value=value,
               footprint=footprint, manufacturer=manufacturer, mpn=mpn,
               datasheet_url=datasheet_url, sheet=sheet, role=role)
    for pin, net in connections.items():
        b.connect(net, refdes, str(pin), is_power=net in power,
                  is_ground=net in ground, role=roles.get(net))
    return b.result()


def connect_bus(
    plan: DesignPlan,
    endpoints: list[dict],
    *,
    net_names: list[str] | None = None,
    net_prefix: str = "D",
    start_index: int = 0,
) -> BlockResult:
    """Wire a parallel bus across two or more parts in one call.

    A wide interface -- an 8-bit data bus between an MCU and a display,
    an address bus to a memory -- is otherwise N near-identical net
    objects, where it is easy to misalign bit i of one side with bit i+1
    of the other. This joins the i-th pin of every endpoint into one net
    per bit, so the alignment is structural and the whole bus is a single
    call. It creates NO parts and owns NO part choice -- the endpoints
    reference parts already in the plan.

    The nets it makes share the exact same part-set, which is the bus
    signature :func:`eda_agent.design.buses.detect_buses` looks for, so a
    >=4-bit bus authored here is automatically drawn as a bus glyph by the
    schematic pipeline.

    Args:
        plan: the DesignPlan to extend.
        endpoints: one dict per part on the bus, ``{"refdes": str,
            "pins": [pin, ...]}``. Every endpoint must list the same
            number of pins (the bus width); bit i joins ``pins[i]`` of
            each endpoint.
        net_names: explicit name per bit (length must equal the width).
            When omitted, bits are named ``f"{net_prefix}{start_index+i}"``
            (e.g. D0, D1, ...).
        net_prefix: prefix for auto-named bits.
        start_index: first bit number for auto-naming.

    Returns:
        A :class:`BlockResult` carrying the augmented plan.

    Raises:
        ValueError: fewer than two endpoints, mismatched widths, a
            ``net_names`` length that does not match the width, or an
            endpoint referencing a refdes not in the plan.
    """
    if len(endpoints) < 2:
        raise ValueError("a bus needs >= 2 endpoints")
    widths = {len(ep.get("pins", [])) for ep in endpoints}
    if len(widths) != 1:
        raise ValueError(
            "every endpoint must list the same number of pins (bus width)")
    width = widths.pop()
    if width < 1:
        raise ValueError("bus needs >= 1 bit")
    if net_names is not None and len(net_names) != width:
        raise ValueError(
            f"net_names has {len(net_names)} names but the bus is "
            f"{width} bits wide")
    existing = {p.refdes for p in plan.parts}
    for ep in endpoints:
        if ep.get("refdes") not in existing:
            raise ValueError(f"unknown refdes {ep.get('refdes')!r}")

    b = _Builder(plan)
    for i in range(width):
        name = net_names[i] if net_names else f"{net_prefix}{start_index + i}"
        for ep in endpoints:
            b.connect(name, ep["refdes"], str(ep["pins"][i]))
    b.notes.append(
        f"bus: {width} bits across {[ep['refdes'] for ep in endpoints]}")
    return b.result()


# Param keys an add_part operation forwards to add_component.
_ADD_PART_KEYS = (
    "value", "footprint", "lib_path", "sheet", "manufacturer", "mpn",
    "datasheet_url", "role", "power_nets", "ground_nets", "net_roles",
)


def compose_netlist(plan: DesignPlan, operations: list[dict]) -> BlockResult:
    """Apply many authoring operations to a plan in one pass.

    The bulk form of the authoring primitives (same principle as preferring
    a batch Altium tool over looping the singular one): instead of N
    round-trips, one call threads the plan through an ordered list of
    ``add_part`` / ``add_block`` / ``connect_bus`` operations and returns
    the final plan. Each operation sees the result of the previous one, so
    a part added early is available to a bus wired later.

    Args:
        plan: the DesignPlan to extend.
        operations: ordered list of op dicts, each with an ``"op"`` key:

            * ``{"op": "add_part", "refdes", "lib_ref", "connections",
              ...}`` -- forwards the optional add_component fields
              (``value``, ``footprint``, ``lib_path``, ``power_nets``, ...).
            * ``{"op": "add_block", "block", "params"}``.
            * ``{"op": "connect_bus", "endpoints", "net_names"?,
              "net_prefix"?, "start_index"?}``.

    Returns:
        A :class:`BlockResult` for the FINAL plan, with ``added_refdes`` /
        ``added_nets`` / ``extended_nets`` accumulated across all ops and
        ``notes`` carrying each op's notes. Not re-validated here; the tool
        runs design_validate_plan once at the end.

    Raises:
        ValueError: an operation has an unknown ``"op"``, a missing key, or
            fails -- the message names the failing operation's index so the
            caller can pinpoint it.
    """
    current = plan
    added_refdes: list[str] = []
    added_nets: list[str] = []
    extended_nets: list[str] = []
    notes: list[str] = []
    for idx, op in enumerate(operations):
        kind = (op.get("op") or "").strip().lower()
        try:
            if kind == "add_part":
                extra = {k: op[k] for k in _ADD_PART_KEYS if k in op}
                res = add_component(
                    current, refdes=op["refdes"], lib_ref=op["lib_ref"],
                    connections=op["connections"], **extra)
            elif kind == "add_block":
                res = add_block(current, op["block"], op.get("params", {}))
            elif kind == "connect_bus":
                res = connect_bus(
                    current, op["endpoints"],
                    net_names=op.get("net_names"),
                    net_prefix=op.get("net_prefix", "D"),
                    start_index=op.get("start_index", 0))
            else:
                raise ValueError(
                    f"unknown op {op.get('op')!r}; use add_part / add_block "
                    f"/ connect_bus")
        except (ValueError, KeyError, TypeError) as exc:
            label = kind or "?"
            raise ValueError(f"operation {idx} ({label}): {exc}") from exc
        current = res.plan
        added_refdes.extend(res.added_refdes)
        # a net created by an early op then tapped later moves from
        # added -> still added; keep the first classification, dedupe.
        for n in res.added_nets:
            if n not in added_nets:
                added_nets.append(n)
        for n in res.extended_nets:
            if n not in extended_nets and n not in added_nets:
                extended_nets.append(n)
        notes.extend(res.notes)
    return BlockResult(
        plan=current, added_refdes=added_refdes, added_nets=added_nets,
        extended_nets=extended_nets, notes=notes)


__all__ = [
    "BlockResult", "add_block", "add_component", "compose_netlist",
    "connect_bus", "describe_blocks", "BLOCK_SPECS",
]
