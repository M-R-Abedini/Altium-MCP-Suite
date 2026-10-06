# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Design requirement schema, the contract between user intent and planner.

A DesignRequirement is the structured capture of what the board must do,
filled in BEFORE any topology or part selection happens. The schema is
strict (extra='forbid') so a requirement can't carry ambiguity past
validation, and ``open_questions`` is the explicit parking spot for
anything the capturing agent could not pin down: an unstated assumption
goes there as a question for the user instead of being silently guessed.
Planning must not proceed while ``open_questions`` is non-empty;
``validate_requirement`` enforces that.

Electrical units are SI with the unit in the field name (``voltage_v``,
``current_a``, ``temp_min_c``). Mechanical dimensions are MILLIMETRES
(``board_size_mm``, ``height_max_mm``), matching the plan schema's zones.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class IOKind(str, Enum):
    """Electrical/physical nature of an external interface."""

    POWER = "power"            # supply input or regulated output rail
    ANALOG = "analog"          # continuous signal (sensor, audio, DAC out)
    DIGITAL = "digital"        # logic-level signal (GPIO, enable, PWM)
    COMMS = "comms"            # protocol bus (UART, I2C, SPI, CAN, USB, ETH)
    RF = "rf"                  # radio path (antenna, coax)
    MECHANICAL = "mechanical"  # non-electrical interface (mounting, shaft)


class IOSpec(BaseModel):
    """One external interface of the board, an input or an output.

    Direction comes from which list it sits in (``DesignRequirement.inputs``
    vs ``.outputs``), not from a field here. Fill only the fields the kind
    needs: voltage/current for power, protocol for comms/digital, leave the
    rest None. A power IO should carry either ``voltage_v`` (nominal) or the
    min/max pair (acceptance range); a comms IO without ``protocol`` is a
    capture gap that ``validate_requirement`` flags.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        description="Interface name as the user knows it, e.g. 'VIN', "
        "'UART_DEBUG', 'MOTOR_OUT'. Unique across inputs+outputs.",
    )
    kind: IOKind
    voltage_v: Optional[float] = Field(
        default=None,
        description="Nominal voltage in volts. Negative for negative rails.",
    )
    voltage_min_v: Optional[float] = Field(
        default=None,
        description="Lower bound of the acceptable voltage range in volts, "
        "e.g. 9.0 for a '9-36V' automotive input.",
    )
    voltage_max_v: Optional[float] = Field(
        default=None,
        description="Upper bound of the acceptable voltage range in volts.",
    )
    current_a: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Maximum continuous current in amperes (sourced for an "
        "output, drawn for an input).",
    )
    protocol: Optional[str] = Field(
        default=None,
        description="Protocol/standard for comms or digital IO, e.g. 'I2C', "
        "'UART 115200', 'USB 2.0 FS', 'CAN FD', '4-20mA'. Free-form.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Free-text qualifiers that don't fit the typed fields, "
        "e.g. 'isolated', 'hot-pluggable', 'ESD exposed'.",
    )


class SupplyRail(BaseModel):
    """A rail the finished board must provide internally or externally.

    Distinct from a power IOSpec: an input IOSpec is what the world feeds
    the board; a SupplyRail is what the design must generate (and what the
    architecture step sizes regulators against).
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        description="Rail name, e.g. '3V3', '5V0', 'VANA'. Unique per "
        "requirement.",
    )
    voltage_v: float = Field(
        description="Rail voltage in volts. Negative for negative rails."
    )
    current_a: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Maximum load current in amperes the rail must supply.",
    )
    tolerance_pct: Optional[float] = Field(
        default=None,
        gt=0.0,
        description="Allowed deviation from nominal in percent, e.g. 2.0 "
        "for a +/-2% rail. Drives regulator accuracy and feedback divider "
        "tolerance choices.",
    )


class Environment(BaseModel):
    """Operating environment. Every field optional; None means unstated.

    An unstated field is NOT a license to assume benign conditions: if the
    application hints at a harsh environment, the capturing agent should add
    an open question rather than leave these None.
    """

    model_config = ConfigDict(extra="forbid")

    temp_min_c: Optional[float] = Field(
        default=None,
        description="Minimum ambient operating temperature in Celsius.",
    )
    temp_max_c: Optional[float] = Field(
        default=None,
        description="Maximum ambient operating temperature in Celsius.",
    )
    ingress: Optional[str] = Field(
        default=None,
        description="Ingress protection requirement, e.g. 'IP67', "
        "'conformal coating', 'indoor dry'. Free-form.",
    )
    vibration: Optional[str] = Field(
        default=None,
        description="Vibration/shock exposure, e.g. 'automotive', "
        "'handheld drops', 'none (benchtop)'. Free-form.",
    )


class Constraints(BaseModel):
    """Physical, fabrication and commercial limits on the design."""

    model_config = ConfigDict(extra="forbid")

    board_size_mm: Optional[tuple[float, float]] = Field(
        default=None,
        description="Maximum board envelope (width, height) in MILLIMETRES.",
    )
    layer_count_max: Optional[int] = Field(
        default=None,
        ge=1,
        description="Maximum PCB layer count, e.g. 2 or 4.",
    )
    height_max_mm: Optional[float] = Field(
        default=None,
        gt=0.0,
        description="Maximum assembled component height in MILLIMETRES "
        "(enclosure clearance).",
    )
    cost_ceiling_usd: Optional[float] = Field(
        default=None,
        gt=0.0,
        description="Maximum BOM cost per unit in USD.",
    )
    compliance: list[str] = Field(
        default_factory=list,
        description="Compliance/certification tags the design must meet, "
        "e.g. ['CE', 'FCC Part 15B', 'IEC 62368-1']. Free-form tags.",
    )

    @field_validator("board_size_mm")
    @classmethod
    def _positive_board_size(
        cls, v: Optional[tuple[float, float]]
    ) -> Optional[tuple[float, float]]:
        if v is not None and (v[0] <= 0.0 or v[1] <= 0.0):
            raise ValueError("board_size_mm dimensions must be positive")
        return v


class DesignRequirement(BaseModel):
    """Complete requirement capture the planner consumes.

    Round-trips through JSON cleanly so the orchestrator can persist the
    requirement next to the plan for audit. The capture loop is: fill what
    the user stated, put every gap into ``open_questions``, ask the user,
    fold the answers back in, repeat until ``validate_requirement`` returns
    ok. Only then does architecture/planning start.
    """

    model_config = ConfigDict(extra="forbid")

    function: str = Field(
        min_length=1,
        description="What the board does, in the user's words. One to three "
        "sentences of free text, e.g. 'Battery-powered soil moisture sensor "
        "that reports over LoRa every 10 minutes.'",
    )
    inputs: list[IOSpec] = Field(
        default_factory=list,
        description="Everything the outside world feeds the board: supply "
        "inputs, sensor signals, buses where the board is a peripheral.",
    )
    outputs: list[IOSpec] = Field(
        default_factory=list,
        description="Everything the board drives or provides: regulated "
        "rails offered externally, actuator drives, buses where the board "
        "is the controller, indicators.",
    )
    supply: list[SupplyRail] = Field(
        default_factory=list,
        description="Internal rails the design must generate (regulator "
        "outputs). Leave empty when the architecture step should derive "
        "them from the parts it picks.",
    )
    environment: Environment = Field(
        default_factory=Environment,
        description="Operating environment. Defaults to all-unstated.",
    )
    constraints: Constraints = Field(
        default_factory=Constraints,
        description="Physical/fab/cost limits. Defaults to unconstrained.",
    )
    quantities: list[int] = Field(
        default_factory=list,
        description="Expected build quantities per stage, e.g. [5, 100, "
        "5000] for proto/pilot/production. Drives DFM and part-selection "
        "tradeoffs (hand-solderable packages vs cost-optimised).",
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description="Questions for the user covering every fact this "
        "requirement does NOT state but the design depends on. An unstated "
        "assumption goes here as a question: it is never guessed. MUST be "
        "empty before planning proceeds; validate_requirement fails while "
        "any remain.",
    )

    @field_validator("quantities")
    @classmethod
    def _positive_quantities(cls, v: list[int]) -> list[int]:
        for q in v:
            if q < 1:
                raise ValueError(f"quantity {q} must be >= 1")
        return v

    @field_validator("supply")
    @classmethod
    def _unique_rail_names(cls, rails: list[SupplyRail]) -> list[SupplyRail]:
        seen: set[str] = set()
        for r in rails:
            if r.name in seen:
                raise ValueError(f"duplicate supply rail name {r.name}")
            seen.add(r.name)
        return rails

    @model_validator(mode="after")
    def _unique_io_names(self) -> "DesignRequirement":
        # IOs are referenced by name in discussion and downstream plans, so
        # the name must be unique across BOTH lists for the reference to
        # resolve unambiguously.
        seen: set[str] = set()
        for io in [*self.inputs, *self.outputs]:
            if io.name in seen:
                raise ValueError(f"duplicate IO name {io.name}")
            seen.add(io.name)
        return self


def _power_input_max_v(req: DesignRequirement) -> Optional[float]:
    """Highest available input-supply magnitude in volts, None if unstated."""
    best: Optional[float] = None
    for io in req.inputs:
        if io.kind != IOKind.POWER:
            continue
        for v in (io.voltage_v, io.voltage_max_v):
            if v is not None and (best is None or abs(v) > best):
                best = abs(v)
    return best


def validate_requirement(req: DesignRequirement) -> dict:
    """Cross-checks beyond field validation. Returns ``{"ok", "issues"}``.

    ``ok`` is True only when ``issues`` is empty. Catches contradictions
    (inverted ranges, rails above the input supply with no boost stated)
    and capture gaps (no outputs, no power source, unresolved open
    questions). This is the gate the orchestrator runs before handing the
    requirement to the architecture step.
    """
    issues: list[str] = []

    # Planning gate: every open question must be resolved first.
    for q in req.open_questions:
        issues.append(f"unresolved open question: {q}")

    # A board with no outputs does nothing observable.
    if not req.outputs:
        issues.append("no outputs defined: the design would do nothing")

    # A board with no power source can't run. Mechanical-only boards exist
    # but are out of scope for an electrical planner.
    has_power_in = any(io.kind == IOKind.POWER for io in req.inputs)
    if not has_power_in and not req.supply:
        issues.append(
            "no power source: no power-kind input and no supply rails defined"
        )

    # Inverted environment temperature range.
    env = req.environment
    if (
        env.temp_min_c is not None
        and env.temp_max_c is not None
        and env.temp_min_c > env.temp_max_c
    ):
        issues.append(
            f"temperature range inverted: temp_min_c={env.temp_min_c} > "
            f"temp_max_c={env.temp_max_c}"
        )

    # Inverted voltage range on any IO.
    for io in [*req.inputs, *req.outputs]:
        if (
            io.voltage_min_v is not None
            and io.voltage_max_v is not None
            and io.voltage_min_v > io.voltage_max_v
        ):
            issues.append(
                f"IO {io.name!r}: voltage range inverted "
                f"({io.voltage_min_v} > {io.voltage_max_v})"
            )

    # Comms IO without a protocol is a capture gap, not a guessable detail.
    for io in [*req.inputs, *req.outputs]:
        if io.kind == IOKind.COMMS and not (io.protocol and io.protocol.strip()):
            issues.append(f"comms IO {io.name!r} has no protocol")

    # Rails (and power outputs) above every stated input supply need a boost
    # or inverting stage; flag so the user confirms instead of the planner
    # assuming. Magnitude comparison so a -5V rail from a +5V input (charge
    # pump territory) is not silently waved through either.
    max_in = _power_input_max_v(req)
    if max_in is not None:
        for rail in req.supply:
            if abs(rail.voltage_v) > max_in:
                issues.append(
                    f"supply rail {rail.name!r} ({rail.voltage_v}V) exceeds "
                    f"the highest power input ({max_in}V); requires a "
                    f"boost/inverting stage: confirm this is intended"
                )
        for io in req.outputs:
            if io.kind != IOKind.POWER:
                continue
            for v in (io.voltage_v, io.voltage_max_v):
                if v is not None and abs(v) > max_in:
                    issues.append(
                        f"power output {io.name!r} ({v}V) exceeds the "
                        f"highest power input ({max_in}V); requires a "
                        f"boost/inverting stage: confirm this is intended"
                    )
                    break

    return {"ok": not issues, "issues": issues}


def _fmt_num(x: float) -> str:
    """Render 3.0 as '3' and 3.3 as '3.3'."""
    return f"{x:g}"


def _describe_io(io: IOSpec) -> str:
    parts = [io.kind.value]
    if io.voltage_min_v is not None and io.voltage_max_v is not None:
        parts.append(f"{_fmt_num(io.voltage_min_v)}-{_fmt_num(io.voltage_max_v)}V")
    elif io.voltage_v is not None:
        parts.append(f"{_fmt_num(io.voltage_v)}V")
    if io.current_a is not None:
        parts.append(f"{_fmt_num(io.current_a)}A")
    if io.protocol:
        parts.append(io.protocol)
    return f"{io.name} ({', '.join(parts)})"


def _describe_rail(rail: SupplyRail) -> str:
    s = f"{rail.name} {_fmt_num(rail.voltage_v)}V"
    if rail.current_a is not None:
        s += f" @ {_fmt_num(rail.current_a)}A"
    if rail.tolerance_pct is not None:
        s += f" +/-{_fmt_num(rail.tolerance_pct)}%"
    return s


def summarize_requirement(req: DesignRequirement) -> str:
    """Short text block for embedding in ``DesignPlan.summary``.

    One labelled line per populated section; unstated sections are omitted
    so the summary stays dense. Deterministic for a given requirement.
    """
    lines = [f"Function: {req.function.strip()}"]

    if req.inputs:
        lines.append("Inputs: " + "; ".join(_describe_io(io) for io in req.inputs))
    if req.outputs:
        lines.append("Outputs: " + "; ".join(_describe_io(io) for io in req.outputs))
    if req.supply:
        lines.append("Supply: " + "; ".join(_describe_rail(r) for r in req.supply))

    env = req.environment
    env_bits: list[str] = []
    if env.temp_min_c is not None or env.temp_max_c is not None:
        lo = _fmt_num(env.temp_min_c) if env.temp_min_c is not None else "?"
        hi = _fmt_num(env.temp_max_c) if env.temp_max_c is not None else "?"
        env_bits.append(f"{lo}..{hi}C")
    if env.ingress:
        env_bits.append(env.ingress)
    if env.vibration:
        env_bits.append(f"vibration: {env.vibration}")
    if env_bits:
        lines.append("Environment: " + ", ".join(env_bits))

    con = req.constraints
    con_bits: list[str] = []
    if con.board_size_mm is not None:
        con_bits.append(
            f"board <= {_fmt_num(con.board_size_mm[0])}x"
            f"{_fmt_num(con.board_size_mm[1])}mm"
        )
    if con.layer_count_max is not None:
        con_bits.append(f"<= {con.layer_count_max} layers")
    if con.height_max_mm is not None:
        con_bits.append(f"height <= {_fmt_num(con.height_max_mm)}mm")
    if con.cost_ceiling_usd is not None:
        con_bits.append(f"cost <= ${_fmt_num(con.cost_ceiling_usd)}")
    if con.compliance:
        con_bits.append("compliance: " + ", ".join(con.compliance))
    if con_bits:
        lines.append("Constraints: " + ", ".join(con_bits))

    if req.quantities:
        lines.append("Quantities: " + ", ".join(str(q) for q in req.quantities))
    if req.open_questions:
        lines.append(f"Open questions: {len(req.open_questions)} unresolved")

    return "\n".join(lines)
