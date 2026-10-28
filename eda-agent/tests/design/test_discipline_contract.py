"""Public planner discovery must describe inputs strict tools can consume.

These probes stay offline: they exercise registered MCP callables and
exported schemas, never native editor operations.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re

import pytest
from jsonschema import Draft202012Validator

from eda_agent.design._wiring import _net_representation
from eda_agent.design.plan import DesignPlan
from eda_agent.design.requirement import DesignRequirement
from eda_agent.tools.design import register_design_tools


class _MCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


@pytest.fixture
def tools(monkeypatch):
    from eda_agent.bridge import AltiumBridge

    def native_forbidden(*args, **kwargs):
        raise AssertionError("Discovery/validation must not access Altium")

    monkeypatch.setattr(AltiumBridge, "__init__", native_forbidden)
    monkeypatch.setattr(AltiumBridge, "_execute_command", native_forbidden)
    mcp = _MCP()
    register_design_tools(mcp)
    return mcp.tools


def _call(tools, name, **kwargs):
    return asyncio.run(tools[name](**kwargs))


def _plan():
    # Synthetic two-component connectivity fixture, not a hardware design.
    return {
        "spec": "Offline planner-contract fixture",
        "summary": "Local label used to keep the synthetic block legible.",
        "sheets": [{"name": "main"}],
        "zones": [{"name": "local", "sheet": "main"}],
        "parts": [
            {"refdes": "R1", "lib_ref": "FIXTURE", "zone": "local"},
            {"refdes": "R2", "lib_ref": "FIXTURE", "zone": "local"},
        ],
        "nets": [{
            "name": "SIGNAL", "force_label": True,
            "pins": [{"refdes": "R1", "pin": "1"},
                     {"refdes": "R2", "pin": "1"}],
        }],
    }


def test_discovery_preserves_plan_contract_and_exposes_requirement_contract(tools):
    result = json.loads(json.dumps(_call(tools, "design_get_discipline")))
    assert result["schema"] == DesignPlan.model_json_schema()
    assert isinstance(result["discipline"], str)
    assert result["requirement_schema"] == DesignRequirement.model_json_schema()
    # References must resolve from each standalone exported schema, rather
    # than depending on definitions hidden in the other schema or source.
    for key in ("schema", "requirement_schema"):
        Draft202012Validator.check_schema(result[key])
    requirement = {
        "function": "5 V power feed-through",
        "inputs": [{"name": "VIN", "kind": "power", "voltage_v": 5}],
        "outputs": [{"name": "VOUT", "kind": "power", "voltage_v": 5}],
    }
    Draft202012Validator(result["requirement_schema"]).validate(requirement)
    assert _call(tools, "design_validate_requirement", requirement=requirement)["ok"]


def test_requirement_draft_is_schema_valid_but_not_planning_ready(tools):
    schema = _call(tools, "design_get_discipline")["requirement_schema"]
    requirement = {
        "function": "Processor/camera concept; exact devices unresolved",
        "open_questions": ["Obtain exact packages and authoritative pin tables"],
    }
    Draft202012Validator(schema).validate(requirement)
    result = _call(tools, "design_validate_requirement", requirement=requirement)
    assert result["ok"] is False
    assert any("authoritative pin tables" in issue for issue in result["issues"])
    invalid_nested = {"function": "Fixture", "inputs": [{"name": "VIN", "kind": "power", "voltage_guess": 5}]}
    assert not Draft202012Validator(schema).is_valid(invalid_nested)
    assert _call(tools, "design_validate_requirement", requirement=invalid_nested)["ok"] is False


def test_documented_zone_and_label_inputs_reach_actual_representation(tools):
    plan_json = _plan()
    schema = _call(tools, "design_get_discipline")["schema"]
    Draft202012Validator(schema).validate(plan_json)
    assert _call(tools, "design_validate_plan", plan_json=plan_json)["ok"]
    plan = DesignPlan.model_validate(plan_json)
    assert plan.cross_check() == []
    membership = {part.refdes: part.zone for part in plan.parts}
    assert _net_representation(plan.nets[0], membership) == "label_per_pin"
    plan.nets[0].force_label = False
    assert _net_representation(plan.nets[0], membership) == "wire"


@pytest.mark.parametrize("container,field,value", [
    ("parts", "block", "local"),
    ("nets", "comment", "No such Net field"),
])
def test_formerly_advertised_extra_fields_stay_rejected(tools, container, field, value):
    result = _call(tools, "design_get_discipline")
    candidate = copy.deepcopy(_plan())
    candidate[container][0][field] = value
    assert not Draft202012Validator(result["schema"]).is_valid(candidate)
    rejected = _call(tools, "design_validate_plan", plan_json=candidate)
    assert rejected["ok"] is False
    assert any(f"{container}.0.{field}" in error for error in rejected["errors"])


def test_explicit_model_field_guidance_resolves_against_exported_schema(tools):
    result = _call(tools, "design_get_discipline")
    references = re.findall(r"`(Part|Net)\.([a-z_]+)(?:=[^`]*)?`", result["discipline"])
    assert references, "No structured field guidance exercised"
    for model_name, field_name in references:
        assert field_name in result["schema"]["$defs"][model_name]["properties"]
