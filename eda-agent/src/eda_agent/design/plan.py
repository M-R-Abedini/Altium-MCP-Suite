# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Design plan schema, the contract between planner and executor.

A DesignPlan is the structured output of the planner. The executor reads it
and instantiates each Part on a SchDoc, drops a net label at every PinRef,
and adds power ports for the Nets flagged as power. The schema is strict
(extra='forbid') so the planner can't smuggle ambiguity past validation.

Coordinates are millimetres relative to a Sheet's origin. The executor maps
them to Altium internal units via the existing MilsToCoord / mm_to_coord
helpers.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_REFDES_PATTERN = r"^[A-Z]+[0-9]+[A-Z]?$"

#: A net name. The leading class admits + and - as well as a letter or
#: underscore, because a supply rail conventionally carries its sign:
#: +3V3, +5V, -12V. Measured on a live board, four of its seventy nets
#: were named that way and every one was refused.
#:
#: This adds no new CHARACTER to a net name. Both signs were already
#: legal in the body, so anything downstream that copes with VCC+ copes
#: with +VCC; only the position changes.
#:
#: Still deliberately narrow. The hierarchy prefix EasyEDA puts on a
#: net inside a block, "$1I81\I2C_SCL", uses $ and backslash and is NOT
#: admitted here: those are quoting and escaping characters, this
#: pattern guards what gets written into a schematic, and the prefix
#: identifies a sheet instance rather than the net. Strip it when
#: importing a live netlist into a plan.
_NET_PATTERN = r"^[A-Za-z_+\-][A-Za-z0-9_+\-/]*$"


class PartStatus(str, Enum):
    """Whether the planner found this part in the user's libraries."""

    EXISTING = "existing"           # lib_ref resolves in the user's libs
    NEEDS_CREATION = "needs_creation"  # planner picked a part not in libs


class PinRef(BaseModel):
    """One end of a net, a refdes + pin identifier."""

    model_config = ConfigDict(extra="forbid")

    refdes: str = Field(pattern=_REFDES_PATTERN)
    pin: str = Field(min_length=1, description="Pin number or pin name")


class Part(BaseModel):
    """A component the executor will place on a sheet."""

    model_config = ConfigDict(extra="forbid")

    refdes: str = Field(pattern=_REFDES_PATTERN)
    lib_ref: str = Field(min_length=1, description="Library reference (symbol name)")
    lib_path: Optional[str] = Field(
        default=None,
        description="Absolute path to the SchLib. Optional, executor falls "
        "back to project-installed libs when omitted.",
    )
    value: Optional[str] = Field(
        default=None,
        description="Component value (e.g. '10k', '100nF', 'LM7805'). For "
        "actives this often equals lib_ref; for passives it carries the value.",
    )
    footprint: Optional[str] = Field(
        default=None,
        description="Footprint name. The executor only stamps it when set; "
        "leave None to defer to the symbol's default.",
    )
    manufacturer: Optional[str] = Field(
        default=None,
        description="Manufacturer name (e.g. 'Texas Instruments'). Stamped on "
        "the placed symbol as the 'Manufacturer' parameter so downstream BOM "
        "extraction sees a populated column. Optional, may also come from "
        "the BomLine that references this refdes.",
    )
    mpn: Optional[str] = Field(
        default=None,
        description="Manufacturer Part Number (e.g. 'LM7805CT'). Stamped on "
        "the placed symbol as the 'Manufacturer Part Number' parameter. "
        "Optional, may also come from the matching BomLine.",
    )
    status: PartStatus = PartStatus.EXISTING
    sheet: str = Field(default="main", description="Sheet name (matches Sheet.name)")
    zone: Optional[str] = Field(
        default=None,
        description="Optional Zone.name to place the part inside.",
    )
    role: Optional[str] = Field(
        default=None,
        description="Topology-aware role tag the planner assigns so the layout "
        "stage can reason about each part's purpose. For a buck converter the "
        "well-known roles are 'ic', 'inductor', 'cin_bulk', 'cin_hf', 'cout', "
        "'rfb_top', 'rfb_bot', 'cff', 'cboot', 'vin_conn', 'vout_conn'. Free-"
        "form, but stable per topology so downstream tools can match on it.",
    )
    datasheet_url: Optional[str] = Field(
        default=None,
        description="Manufacturer datasheet URL for this part. Populated by "
        "the synthesis solvers so the auditor can trace every spec back to a "
        "primary source. Stamped on the placed symbol as the 'Datasheet' "
        "parameter when present.",
    )
    rationale: Optional[str] = Field(
        default=None,
        description="One-line why-this-part, for audit logs, not sent to "
        "Altium. Useful when the planner re-iterates after a validation fail.",
    )

    def validate_atomic(self) -> list[str]:
        """Return human-readable atomic-parts contract issues for this part.

        The atomic-parts standard (KiCad Atomic, Digi-Key Library, atopile,
        JITX) says every existing symbol must carry MPN + footprint +
        datasheet URL bound at the part level so the BOM and layout come
        out complete on the first pass. Only enforced on status='existing'
        parts; needs_creation parts are escalated to the user before they
        reach a BOM.
        """
        if self.status != PartStatus.EXISTING:
            return []
        issues: list[str] = []
        if not (self.mpn and self.mpn.strip()):
            issues.append(f"{self.refdes} has no mpn")
        if not (self.footprint and self.footprint.strip()):
            issues.append(f"{self.refdes} has no footprint")
        if not (self.datasheet_url and self.datasheet_url.strip()):
            issues.append(f"{self.refdes} has no datasheet_url")
        return issues


class Net(BaseModel):
    """A logical net, connects 2+ pins via shared net labels."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=_NET_PATTERN)
    pins: list[PinRef] = Field(min_length=2)
    is_power: bool = Field(
        default=False,
        description="If True, executor adds a power port (VCC-style) instead "
        "of a plain net label.",
    )
    is_ground: bool = Field(
        default=False,
        description="If True, executor adds a GND-style power port.",
    )
    force_label: bool = Field(
        default=False,
        description="Override for the block-local-wires default: when True, "
        "the executor emits a net label at every pin even if all pins share "
        "one functional block (zone). Use sparingly, only when a wire would "
        "genuinely tangle the block (e.g. a high-fanout intra-block rail with "
        "10+ pins, a control line that would weave between five other "
        "components). Has no effect on power/ground nets, which always use "
        "port glyphs.",
    )
    force_wires: bool = Field(
        default=False,
        description="Hard override: route this net with WIRES regardless of "
        "every other rule: the power/ground flags, the conventional-rail "
        "name heuristic (a net named 'VCC' is otherwise treated as a power "
        "rail even with is_power=False), and the cross-zone label default. "
        "The explicit escape hatch when the planner wants a drawn wire on a "
        "net the rules would render as ports or labels. Mutually exclusive "
        "with force_label.",
    )
    role: Optional[str] = Field(
        default=None,
        description="Generic electrical role tag the planner asserts so "
        "downstream tools can apply principles without knowing the "
        "topology. Well-known values: 'switch' (high-dv/dt SMPS node, "
        "wants short + wide trace), 'feedback' (sensitive, route away "
        "from switch / RF), 'high_current' (wants wide trace / polygon), "
        "'analog_sensitive' (route on quiet layer, away from digital), "
        "'control' (digital control signal, moderate), 'differential' "
        "(must route as matched pair), 'clock' (length-matched, "
        "shielded). Free-form: planners may add new roles when a "
        "datasheet calls for them.",
    )

    @field_validator("pins")
    @classmethod
    def _unique_pin_endpoints(cls, pins: list[PinRef]) -> list[PinRef]:
        seen = set()
        for p in pins:
            key = (p.refdes, p.pin)
            if key in seen:
                raise ValueError(f"duplicate pin endpoint {p.refdes}.{p.pin}")
            seen.add(key)
        return pins

    @model_validator(mode="after")
    def _force_flags_exclusive(self) -> "Net":
        if self.force_label and self.force_wires:
            raise ValueError(
                f"net {self.name!r}: force_label and force_wires are "
                f"mutually exclusive")
        return self


class Zone(BaseModel):
    """A rough placement region on a sheet, signal flow guidance.

    Coordinates here are MILLIMETRES (the ``_mm`` suffixes), while the
    layout/canvas engines work in MILS. Zones are advisory grouping hints
    only; no engine reads ``origin_mm``/``size_mm`` for geometry today.
    If that ever changes, convert at the boundary (1 mm = 39.37 mils);
    feeding these values into mils math silently lands 25.4x off.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    sheet: str = Field(default="main")
    origin_mm: tuple[float, float] = Field(default=(0.0, 0.0))
    size_mm: tuple[float, float] = Field(default=(40.0, 40.0))
    role: Optional[str] = Field(
        default=None,
        description="Free-text role tag, e.g. 'power_in', 'mcu', 'usb_front_end'.",
    )


class Sheet(BaseModel):
    """A schematic sheet that the executor will create or reuse."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    title: Optional[str] = Field(default=None)
    size: str = Field(default="A4", description="Altium sheet size code (A4, A3, ...)")


class DesignRuleDelta(BaseModel):
    """A design-rule override the planner wants applied to the project.

    Two styles co-exist:

    * Schematic-planner style (legacy): ``rule_kind`` + ``scope`` +
      ``parameters`` dict, generic key/value pairs.
    * PCB-planner style: ``rule_type`` + per-rule-type typed fields
      (``net`` / ``from_net`` / ``to_net`` / mil-width / gap fields).
      The buck-layout helper emits this style so callers can dispatch
      directly to ``pcb_create_design_rule`` without re-parsing a dict.

    Both styles round-trip through JSON. Callers pick whichever fields
    they need; unused fields stay ``None``.
    """

    model_config = ConfigDict(extra="forbid")

    rule_kind: Optional[str] = Field(
        default=None, description="Legacy: e.g. 'Width', 'Clearance'"
    )
    scope: Optional[str] = Field(
        default=None, description="Legacy: net class or 'all'"
    )
    parameters: dict[str, str] = Field(default_factory=dict)

    rule_type: Optional[str] = Field(
        default=None,
        description="One of 'width', 'clearance'. Drives which of the typed "
        "fields below are populated.",
    )
    net: Optional[str] = Field(
        default=None, description="Single-net scope, used for width rules."
    )
    from_net: Optional[str] = Field(
        default=None, description="Clearance: source net."
    )
    to_net: Optional[str] = Field(
        default=None, description="Clearance: target net."
    )
    min_width_mils: Optional[float] = None
    preferred_width_mils: Optional[float] = None
    max_width_mils: Optional[float] = None
    gap_mils: Optional[float] = Field(
        default=None, description="Clearance gap in mils."
    )


class BomLine(BaseModel):
    """One BOM line, derived from the parts list, but planner-asserted."""

    model_config = ConfigDict(extra="forbid")

    refdes_list: list[str] = Field(min_length=1)
    manufacturer: Optional[str] = None
    mpn: Optional[str] = Field(default=None, description="Manufacturer part number")
    description: Optional[str] = None
    qty: int = Field(ge=1, default=1)


class DesignPlan(BaseModel):
    """Complete, validated plan that the executor consumes.

    Everything the planner produced. Round-trips through JSON cleanly so the
    orchestrator can persist plans for audit and replay.
    """

    model_config = ConfigDict(extra="forbid")

    spec: str = Field(min_length=1, description="The original natural-language spec")
    summary: str = Field(
        min_length=1,
        description="One-paragraph explanation of the topology choice, for "
        "the human reviewer, not the executor.",
    )
    topology: Optional[str] = Field(
        default=None,
        description="Topology tag the planner asserts, e.g. 'buck', 'boost', "
        "'ldo', 'opamp_filter'. Free-form, layout passes and review tools "
        "branch on it. None means the planner did not classify the design.",
    )
    sheets: list[Sheet] = Field(min_length=1)
    zones: list[Zone] = Field(default_factory=list)
    parts: list[Part] = Field(min_length=1)
    nets: list[Net] = Field(min_length=1)
    bom: list[BomLine] = Field(default_factory=list)
    design_rules: list[DesignRuleDelta] = Field(default_factory=list)
    open_questions: list[str] = Field(
        default_factory=list,
        description="Things the planner could not decide and wants the user "
        "to clarify. Empty list = the planner is confident.",
    )

    @field_validator("parts")
    @classmethod
    def _unique_refdes(cls, parts: list[Part]) -> list[Part]:
        seen: set[str] = set()
        for p in parts:
            if p.refdes in seen:
                raise ValueError(f"duplicate refdes {p.refdes}")
            seen.add(p.refdes)
        return parts

    @field_validator("nets")
    @classmethod
    def _unique_net_names(cls, nets: list[Net]) -> list[Net]:
        seen: set[str] = set()
        for n in nets:
            if n.name in seen:
                raise ValueError(f"duplicate net name {n.name}")
            seen.add(n.name)
        return nets

    @field_validator("sheets")
    @classmethod
    def _unique_sheet_names(cls, sheets: list[Sheet]) -> list[Sheet]:
        # Sheets are referenced by name (a part's / zone's ``sheet``), so a
        # duplicate name makes that lookup ambiguous.
        seen: set[str] = set()
        for s in sheets:
            if s.name in seen:
                raise ValueError(f"duplicate sheet name {s.name}")
            seen.add(s.name)
        return sheets

    @field_validator("zones")
    @classmethod
    def _unique_zone_names(cls, zones: list[Zone]) -> list[Zone]:
        # Zones are referenced by name (a part's ``zone``), so the name must
        # be unique design-wide for the reference to resolve unambiguously.
        seen: set[str] = set()
        for z in zones:
            if z.name in seen:
                raise ValueError(f"duplicate zone name {z.name}")
            seen.add(z.name)
        return zones

    def cross_check(self) -> list[str]:
        """Cross-validation that doesn't fit a single field validator.

        Returns a list of human-readable problems, empty list means clean.
        Called by the planner before returning a plan, and by the executor as
        a safety net.
        """
        problems: list[str] = []

        sheet_names = {s.name for s in self.sheets}
        zone_by_name = {z.name: z for z in self.zones}
        part_refdes = {p.refdes for p in self.parts}

        for p in self.parts:
            if p.sheet not in sheet_names:
                problems.append(f"part {p.refdes}.sheet={p.sheet!r} not in sheets")
            if p.zone is not None:
                zone = zone_by_name.get(p.zone)
                if zone is None:
                    problems.append(f"part {p.refdes}.zone={p.zone!r} not in zones")
                elif zone.sheet != p.sheet:
                    # A part can only sit in a zone that lives on its own sheet;
                    # referencing a zone on another sheet is a planner mistake
                    # that name-only membership would let slip through.
                    problems.append(
                        f"part {p.refdes} on sheet {p.sheet!r} references zone "
                        f"{p.zone!r} which is on sheet {zone.sheet!r}"
                    )

        for z in self.zones:
            if z.sheet not in sheet_names:
                problems.append(f"zone {z.name}.sheet={z.sheet!r} not in sheets")

        for n in self.nets:
            for pr in n.pins:
                if pr.refdes not in part_refdes:
                    problems.append(
                        f"net {n.name} references unknown refdes {pr.refdes}"
                    )

        # A BOM line that names a refdes the plan no longer has is a dangling
        # reference -- it slips past every field validator (the BOM is just a
        # list of strings) and would ship a wrong bill. Common after a part
        # is deleted without scrubbing the BOM.
        for bl in self.bom:
            for rd in bl.refdes_list:
                if rd not in part_refdes:
                    problems.append(
                        f"BOM line references unknown refdes {rd}"
                    )

        return problems
