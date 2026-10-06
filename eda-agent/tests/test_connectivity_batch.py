# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the connectivity-batching work:
- get_connectivity_many Python tool
- BulkHintTracker aggressive threshold for expensive tools
- Docstring steering for get_nets / get_connectivity
"""

from __future__ import annotations

import pytest

from eda_agent.tools.bulk_hints import BulkHintTracker


@pytest.fixture(autouse=True)
def _reset_tracker():
    BulkHintTracker.reset()
    yield
    BulkHintTracker.reset()


class TestExpensiveThreshold:
    def test_expensive_tools_registered(self):
        for name in (
            "proj_get_nets",
            "proj_get_connectivity",
            "proj_get_component_info",
            "proj_get_bom",
        ):
            assert name in BulkHintTracker._EXPENSIVE

    def test_get_connectivity_trips_at_second_call(self):
        first = BulkHintTracker.record_and_hint("proj_get_connectivity")
        assert first is None
        second = BulkHintTracker.record_and_hint("proj_get_connectivity")
        assert second is not None
        assert second["bulk_tool"] == "proj_get_connectivity_many"
        assert "proj_get_connectivity_many" in second["hint"]

    def test_get_component_info_trips_at_second_call(self):
        first = BulkHintTracker.record_and_hint("proj_get_component_info")
        assert first is None
        second = BulkHintTracker.record_and_hint("proj_get_component_info")
        assert second is not None
        assert second["bulk_tool"] == "proj_get_component_info_many"
        assert "proj_get_component_info_many" in second["hint"]

    def test_get_nets_trips_at_second_call_and_points_to_unfiltered(self):
        BulkHintTracker.record_and_hint("proj_get_nets")
        hint = BulkHintTracker.record_and_hint("proj_get_nets")
        assert hint is not None
        # get_nets' "bulk" is itself, the nudge is to call it ONCE
        # unfiltered and filter locally.
        assert hint["bulk_tool"] == "proj_get_nets"
        assert "no filters" in hint["hint"].lower() or "once" in hint["hint"].lower()

    def test_non_expensive_singular_still_threshold_3(self):
        # create_object is non-expensive, should still need 3 calls.
        assert BulkHintTracker.record_and_hint("obj_create") is None
        assert BulkHintTracker.record_and_hint("obj_create") is None
        tripped = BulkHintTracker.record_and_hint("obj_create")
        assert tripped is not None


class TestGetConnectivityManyOrchestration:
    @pytest.mark.asyncio
    async def test_packs_designators_with_double_tilde(self, monkeypatch):
        captured: dict = {}

        class FakeBridge:
            async def send_command_async(self, command, params=None, timeout=None):
                captured["command"] = command
                captured["params"] = params
                return {
                    "components": [
                        {"designator": "U1", "pins": []},
                        {"designator": "R1", "pins": []},
                    ],
                    "matched": 2,
                    "requested": 3,
                    "not_found": ["Q99"],
                }

        monkeypatch.setattr(
            "eda_agent.tools.project.get_bridge", lambda: FakeBridge()
        )
        from eda_agent.tools import project

        captured_tools = {}

        class DummyMcp:
            def tool(self):
                def decorator(fn):
                    captured_tools[fn.__name__] = fn
                    return fn
                return decorator

        project.register_project_tools(DummyMcp())
        result = await captured_tools["proj_get_connectivity_many"](
            designators=["U1", "R1", "Q99"],
        )
        assert captured["command"] == "project.get_connectivity_batch"
        assert captured["params"]["designators"] == "U1~~R1~~Q99"
        assert result["matched"] == 2
        assert "Q99" in result["not_found"]

    @pytest.mark.asyncio
    async def test_empty_list_returns_error(self, monkeypatch):
        class FakeBridge:
            async def send_command_async(self, command, params=None, timeout=None):
                raise AssertionError("bridge should not be called")

        monkeypatch.setattr(
            "eda_agent.tools.project.get_bridge", lambda: FakeBridge()
        )
        from eda_agent.tools import project

        captured_tools = {}

        class DummyMcp:
            def tool(self):
                def decorator(fn):
                    captured_tools[fn.__name__] = fn
                    return fn
                return decorator

        project.register_project_tools(DummyMcp())
        result = await captured_tools["proj_get_connectivity_many"](designators=[])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_strips_empty_and_whitespace_designators(self, monkeypatch):
        captured: dict = {}

        class FakeBridge:
            async def send_command_async(self, command, params=None, timeout=None):
                captured["params"] = params
                return {"matched": 1, "requested": 1, "not_found": []}

        monkeypatch.setattr(
            "eda_agent.tools.project.get_bridge", lambda: FakeBridge()
        )
        from eda_agent.tools import project

        captured_tools = {}

        class DummyMcp:
            def tool(self):
                def decorator(fn):
                    captured_tools[fn.__name__] = fn
                    return fn
                return decorator

        project.register_project_tools(DummyMcp())
        await captured_tools["proj_get_connectivity_many"](
            designators=["U1", "", "  ", "R1 "]
        )
        # Whitespace stripped, empties dropped.
        assert captured["params"]["designators"] == "U1~~R1"


class TestGetComponentInfoManyOrchestration:
    @pytest.mark.asyncio
    async def test_packs_designators_and_routes_to_batch_command(self, monkeypatch):
        captured: dict = {}

        class FakeBridge:
            async def send_command_async(self, command, params=None, timeout=None):
                captured["command"] = command
                captured["params"] = params
                captured["timeout"] = timeout
                return {
                    "components": [
                        {"designator": "U1", "comment": "MCU", "pins": []},
                        {"designator": "R1", "comment": "10k", "pins": []},
                    ],
                    "matched": 2,
                    "requested": 3,
                    "not_found": ["Q99"],
                }

        monkeypatch.setattr(
            "eda_agent.tools.project.get_bridge", lambda: FakeBridge()
        )
        from eda_agent.tools import project

        captured_tools = {}

        class DummyMcp:
            def tool(self):
                def decorator(fn):
                    captured_tools[fn.__name__] = fn
                    return fn
                return decorator

        project.register_project_tools(DummyMcp())
        result = await captured_tools["proj_get_component_info_many"](
            designators=["U1", "R1", "Q99"],
        )
        assert captured["command"] == "project.get_component_info_batch"
        assert captured["params"]["designators"] == "U1~~R1~~Q99"
        # Default flags omitted from the wire payload.
        assert "with_pin_nets" not in captured["params"]
        assert "with_parameters" not in captured["params"]
        assert result["matched"] == 2
        assert "Q99" in result["not_found"]

    @pytest.mark.asyncio
    async def test_empty_list_returns_error(self, monkeypatch):
        class FakeBridge:
            async def send_command_async(self, command, params=None, timeout=None):
                raise AssertionError("bridge should not be called")

        monkeypatch.setattr(
            "eda_agent.tools.project.get_bridge", lambda: FakeBridge()
        )
        from eda_agent.tools import project

        captured_tools = {}

        class DummyMcp:
            def tool(self):
                def decorator(fn):
                    captured_tools[fn.__name__] = fn
                    return fn
                return decorator

        project.register_project_tools(DummyMcp())
        result = await captured_tools["proj_get_component_info_many"](designators=[])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_strips_whitespace_and_propagates_flags(self, monkeypatch):
        captured: dict = {}

        class FakeBridge:
            async def send_command_async(self, command, params=None, timeout=None):
                captured["params"] = params
                captured["timeout"] = timeout
                return {"matched": 1, "requested": 1, "not_found": []}

        monkeypatch.setattr(
            "eda_agent.tools.project.get_bridge", lambda: FakeBridge()
        )
        from eda_agent.tools import project

        captured_tools = {}

        class DummyMcp:
            def tool(self):
                def decorator(fn):
                    captured_tools[fn.__name__] = fn
                    return fn
                return decorator

        project.register_project_tools(DummyMcp())
        await captured_tools["proj_get_component_info_many"](
            designators=["U1", "", "  ", "R1 "],
            with_pin_nets=False,
            with_parameters=False,
            timeout=90.0,
        )
        # Whitespace stripped, empties dropped.
        assert captured["params"]["designators"] == "U1~~R1"
        # False flags get serialized for Pascal-side parsing.
        assert captured["params"]["with_pin_nets"] == "false"
        assert captured["params"]["with_parameters"] == "false"
        assert captured["timeout"] == 90.0
