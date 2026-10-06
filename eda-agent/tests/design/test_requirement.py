# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Schema + validator + summary tests for DesignRequirement."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from eda_agent.design.requirement import (
    Constraints,
    DesignRequirement,
    Environment,
    IOKind,
    IOSpec,
    SupplyRail,
    summarize_requirement,
    validate_requirement,
)


def _full_requirement() -> DesignRequirement:
    """Representative fully-stated requirement that should validate clean."""
    return DesignRequirement(
        function="USB-powered RS-485 to Wi-Fi bridge for irrigation valves.",
        inputs=[
            IOSpec(name="VBUS", kind=IOKind.POWER, voltage_v=5.0, current_a=0.9),
            IOSpec(name="RS485", kind=IOKind.COMMS, protocol="RS-485 half duplex"),
        ],
        outputs=[
            IOSpec(name="WIFI_ANT", kind=IOKind.RF, notes="u.FL to external antenna"),
            IOSpec(name="STATUS_LED", kind=IOKind.DIGITAL),
        ],
        supply=[
            SupplyRail(name="3V3", voltage_v=3.3, current_a=0.6, tolerance_pct=3.0),
        ],
        environment=Environment(
            temp_min_c=-20.0, temp_max_c=70.0, ingress="IP54", vibration="none"
        ),
        constraints=Constraints(
            board_size_mm=(50.0, 30.0),
            layer_count_max=4,
            height_max_mm=8.0,
            cost_ceiling_usd=15.0,
            compliance=["CE", "FCC Part 15B"],
        ),
        quantities=[10, 1000],
    )


# ---------------------------------------------------------------- schema


def test_full_requirement_validates() -> None:
    req = _full_requirement()
    assert req.function.startswith("USB-powered")
    assert len(req.inputs) == 2
    assert req.constraints.layer_count_max == 4


def test_minimal_requirement_defaults() -> None:
    req = DesignRequirement(function="555 LED blinker")
    assert req.inputs == []
    assert req.outputs == []
    assert req.supply == []
    assert req.environment.temp_min_c is None
    assert req.constraints.board_size_mm is None
    assert req.quantities == []
    assert req.open_questions == []


def test_round_trip_through_json() -> None:
    req = _full_requirement()
    blob = req.model_dump_json()
    parsed = json.loads(blob)
    assert parsed["function"] == req.function
    again = DesignRequirement.model_validate_json(blob)
    assert again == req


def test_extra_field_rejected_top_level() -> None:
    with pytest.raises(ValidationError):
        DesignRequirement(function="x", topology="buck")


@pytest.mark.parametrize(
    "model, kwargs",
    [
        (IOSpec, {"name": "A", "kind": "power", "bogus": 1}),
        (SupplyRail, {"name": "3V3", "voltage_v": 3.3, "bogus": 1}),
        (Environment, {"bogus": 1}),
        (Constraints, {"bogus": 1}),
    ],
)
def test_extra_field_rejected_nested(model, kwargs) -> None:
    with pytest.raises(ValidationError):
        model(**kwargs)


def test_empty_function_rejected() -> None:
    with pytest.raises(ValidationError):
        DesignRequirement(function="")


def test_io_kind_restricted() -> None:
    with pytest.raises(ValidationError):
        IOSpec(name="X", kind="thermal")


def test_io_kind_accepts_all_documented_values() -> None:
    for kind in ("power", "analog", "digital", "comms", "rf", "mechanical"):
        io = IOSpec(name="X", kind=kind)
        assert io.kind.value == kind


def test_negative_current_rejected() -> None:
    with pytest.raises(ValidationError):
        IOSpec(name="VIN", kind=IOKind.POWER, current_a=-1.0)
    with pytest.raises(ValidationError):
        SupplyRail(name="3V3", voltage_v=3.3, current_a=-0.1)


def test_negative_rail_voltage_allowed() -> None:
    rail = SupplyRail(name="VNEG", voltage_v=-5.0)
    assert rail.voltage_v == -5.0


def test_tolerance_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        SupplyRail(name="3V3", voltage_v=3.3, tolerance_pct=0.0)


def test_constraints_bounds() -> None:
    with pytest.raises(ValidationError):
        Constraints(layer_count_max=0)
    with pytest.raises(ValidationError):
        Constraints(height_max_mm=0.0)
    with pytest.raises(ValidationError):
        Constraints(cost_ceiling_usd=-1.0)
    with pytest.raises(ValidationError):
        Constraints(board_size_mm=(0.0, 50.0))
    with pytest.raises(ValidationError):
        Constraints(board_size_mm=(50.0, -1.0))


def test_quantities_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        DesignRequirement(function="x", quantities=[10, 0])


def test_duplicate_rail_name_rejected() -> None:
    with pytest.raises(ValidationError):
        DesignRequirement(
            function="x",
            supply=[
                SupplyRail(name="3V3", voltage_v=3.3),
                SupplyRail(name="3V3", voltage_v=3.3),
            ],
        )


def test_duplicate_io_name_rejected_across_lists() -> None:
    with pytest.raises(ValidationError):
        DesignRequirement(
            function="x",
            inputs=[IOSpec(name="IO1", kind=IOKind.DIGITAL)],
            outputs=[IOSpec(name="IO1", kind=IOKind.DIGITAL)],
        )


def test_duplicate_io_name_rejected_within_list() -> None:
    with pytest.raises(ValidationError):
        DesignRequirement(
            function="x",
            inputs=[
                IOSpec(name="IO1", kind=IOKind.DIGITAL),
                IOSpec(name="IO1", kind=IOKind.ANALOG),
            ],
        )


# ------------------------------------------------- validate_requirement


def test_validate_clean_requirement_ok() -> None:
    result = validate_requirement(_full_requirement())
    assert result == {"ok": True, "issues": []}


def test_validate_open_questions_block_planning() -> None:
    req = _full_requirement()
    req = req.model_copy(update={"open_questions": ["Which Wi-Fi module family?"]})
    result = validate_requirement(req)
    assert result["ok"] is False
    assert any("Which Wi-Fi module family?" in i for i in result["issues"])


def test_validate_zero_outputs() -> None:
    req = _full_requirement().model_copy(update={"outputs": []})
    result = validate_requirement(req)
    assert result["ok"] is False
    assert any("no outputs" in i for i in result["issues"])


def test_validate_no_power_source() -> None:
    req = DesignRequirement(
        function="x",
        inputs=[IOSpec(name="SIG", kind=IOKind.ANALOG)],
        outputs=[IOSpec(name="OUT", kind=IOKind.ANALOG)],
    )
    result = validate_requirement(req)
    assert result["ok"] is False
    assert any("no power source" in i for i in result["issues"])


def test_validate_supply_rails_count_as_power_source() -> None:
    # No power input but a supply rail (e.g. battery on board) -> no
    # no-power-source issue.
    req = DesignRequirement(
        function="x",
        outputs=[IOSpec(name="OUT", kind=IOKind.ANALOG)],
        supply=[SupplyRail(name="3V3", voltage_v=3.3)],
    )
    result = validate_requirement(req)
    assert not any("no power source" in i for i in result["issues"])


def test_validate_temp_range_inverted() -> None:
    req = _full_requirement().model_copy(
        update={"environment": Environment(temp_min_c=85.0, temp_max_c=-40.0)}
    )
    result = validate_requirement(req)
    assert result["ok"] is False
    assert any("temperature range inverted" in i for i in result["issues"])


def test_validate_temp_half_open_range_ok() -> None:
    req = _full_requirement().model_copy(
        update={"environment": Environment(temp_max_c=70.0)}
    )
    assert validate_requirement(req)["ok"] is True


def test_validate_io_voltage_range_inverted() -> None:
    req = _full_requirement()
    bad = [
        IOSpec(name="VBUS", kind=IOKind.POWER, voltage_min_v=36.0, voltage_max_v=9.0),
        req.inputs[1],
    ]
    req = req.model_copy(update={"inputs": bad})
    result = validate_requirement(req)
    assert result["ok"] is False
    assert any("voltage range inverted" in i and "VBUS" in i for i in result["issues"])


def test_validate_comms_without_protocol() -> None:
    req = _full_requirement()
    bad = [req.inputs[0], IOSpec(name="RS485", kind=IOKind.COMMS)]
    req = req.model_copy(update={"inputs": bad})
    result = validate_requirement(req)
    assert result["ok"] is False
    assert any("no protocol" in i and "RS485" in i for i in result["issues"])


def test_validate_supply_rail_exceeds_input() -> None:
    req = _full_requirement().model_copy(
        update={"supply": [SupplyRail(name="12V0", voltage_v=12.0)]}
    )
    result = validate_requirement(req)
    assert result["ok"] is False
    assert any("12V0" in i and "exceeds" in i for i in result["issues"])


def test_validate_negative_rail_magnitude_compared() -> None:
    # -12V rail from a 5V input needs an inverting boost; |−12| > 5 flags.
    req = _full_requirement().model_copy(
        update={"supply": [SupplyRail(name="VNEG", voltage_v=-12.0)]}
    )
    result = validate_requirement(req)
    assert any("VNEG" in i and "exceeds" in i for i in result["issues"])


def test_validate_power_output_exceeds_input() -> None:
    req = _full_requirement()
    outs = [*req.outputs, IOSpec(name="V48_OUT", kind=IOKind.POWER, voltage_v=48.0)]
    req = req.model_copy(update={"outputs": outs})
    result = validate_requirement(req)
    assert result["ok"] is False
    assert any("V48_OUT" in i and "exceeds" in i for i in result["issues"])


def test_validate_rail_within_input_range_ok() -> None:
    # 9-36V input, 12V rail: 12 <= 36, no boost needed.
    req = DesignRequirement(
        function="x",
        inputs=[
            IOSpec(
                name="VIN", kind=IOKind.POWER, voltage_min_v=9.0, voltage_max_v=36.0
            )
        ],
        outputs=[IOSpec(name="OUT", kind=IOKind.DIGITAL)],
        supply=[SupplyRail(name="12V0", voltage_v=12.0)],
    )
    assert validate_requirement(req)["ok"] is True


def test_validate_no_input_voltage_skips_exceed_check() -> None:
    # Power input with unstated voltage: cannot compare, no exceed issue.
    req = DesignRequirement(
        function="x",
        inputs=[IOSpec(name="VIN", kind=IOKind.POWER)],
        outputs=[IOSpec(name="OUT", kind=IOKind.DIGITAL)],
        supply=[SupplyRail(name="48V0", voltage_v=48.0)],
    )
    result = validate_requirement(req)
    assert not any("exceeds" in i for i in result["issues"])


def test_validate_multiple_issues_accumulate() -> None:
    req = DesignRequirement(
        function="x",
        environment=Environment(temp_min_c=50.0, temp_max_c=0.0),
        open_questions=["Battery chemistry?"],
    )
    result = validate_requirement(req)
    assert result["ok"] is False
    assert len(result["issues"]) >= 4  # question, no outputs, no power, temp


def test_validate_deterministic() -> None:
    req = _full_requirement()
    assert validate_requirement(req) == validate_requirement(req)


# --------------------------------------------------- summarize_requirement


def test_summarize_full() -> None:
    text = summarize_requirement(_full_requirement())
    lines = text.splitlines()
    assert lines[0] == (
        "Function: USB-powered RS-485 to Wi-Fi bridge for irrigation valves."
    )
    assert "Inputs: VBUS (power, 5V, 0.9A); RS485 (comms, RS-485 half duplex)" in lines
    assert "Outputs: WIFI_ANT (rf); STATUS_LED (digital)" in lines
    assert "Supply: 3V3 3.3V @ 0.6A +/-3%" in lines
    assert "Environment: -20..70C, IP54, vibration: none" in lines
    assert (
        "Constraints: board <= 50x30mm, <= 4 layers, height <= 8mm, "
        "cost <= $15, compliance: CE, FCC Part 15B" in lines
    )
    assert "Quantities: 10, 1000" in lines


def test_summarize_minimal_omits_empty_sections() -> None:
    text = summarize_requirement(DesignRequirement(function="555 LED blinker"))
    assert text == "Function: 555 LED blinker"


def test_summarize_voltage_range_form() -> None:
    req = DesignRequirement(
        function="x",
        inputs=[
            IOSpec(
                name="VIN", kind=IOKind.POWER, voltage_min_v=9.0, voltage_max_v=36.0
            )
        ],
    )
    assert "VIN (power, 9-36V)" in summarize_requirement(req)


def test_summarize_half_open_temp_range() -> None:
    req = DesignRequirement(
        function="x", environment=Environment(temp_max_c=85.0)
    )
    assert "Environment: ?..85C" in summarize_requirement(req)


def test_summarize_open_questions_counted() -> None:
    req = DesignRequirement(function="x", open_questions=["a", "b"])
    assert "Open questions: 2 unresolved" in summarize_requirement(req)


def test_summarize_deterministic() -> None:
    req = _full_requirement()
    assert summarize_requirement(req) == summarize_requirement(req)
