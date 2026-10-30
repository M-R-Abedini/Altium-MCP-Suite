"""Real protocol/worker tests for the unified endpoint; no native design writes."""
import asyncio
from datetime import timedelta
import importlib.util
import json
from pathlib import Path
import sys

import pytest
import jsonschema
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from suite_stdio import BackendSpec, Suite, load_specs


def fixture_spec(tmp_path, name="eda", **extra):
    return BackendSpec(name, sys.executable,
                       [str(ROOT / "tests/suite_fake_worker.py"), str(tmp_path / "marker.txt")], **extra)


def test_config_rejects_unknown_and_malformed(tmp_path):
    path = tmp_path / "backends.json"
    for raw in ({"other": {}}, {"backends": {"unknown": {"command": "a"}}},
                {"backends": {"eda": {"command": "a", "env": {"A": 5}}}},
                {"backends": {"eda": {"command": "a", "enabled_tools": "all"}}},
                {"backends": {"eda": {"command": "a", "timeout_seconds": -1}}}):
        path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises((ValueError, TypeError)):
            load_specs(path)


def test_status_is_lazy_and_hides_environment(tmp_path):
    suite = Suite([fixture_spec(tmp_path, env={"TOKEN": "secret-value"})])
    rendered = json.dumps(suite.status())
    assert "secret-value" not in rendered and "TOKEN" not in rendered
    assert not suite.workers["eda"].running and suite.workers["eda"].task is None


def test_actor_preserves_errors_images_metadata_and_validates(tmp_path):
    async def check():
        suite = Suite([fixture_spec(tmp_path)])
        try:
            listing = await suite.catalog(query="echo", backend="eda", with_schema=True)
            assert listing["total"] == 1 and listing["complete"]
            assert listing["tools"][0]["name"] == "eda__echo"
            assert listing["tools"][0]["inputSchema"]["properties"]["value"]["type"] == "integer"
            echo = await suite.invoke("eda__echo", {"value": 3})
            assert echo.structuredContent == {"name": "echo", "arguments": {"value": 3}}
            failure = await suite.invoke("eda__failure")
            assert failure.isError and failure.structuredContent == {"partial": True}
            image = await suite.invoke("eda__image")
            assert image.content[0].type == "image" and image.content[0].data == "aGVsbG8="
            assert image.meta == {"retained": True}
            with pytest.raises(Exception):
                await suite.invoke("eda__echo", {"value": "not integer"})
            with pytest.raises(ValueError, match="Unknown or disabled"):
                await suite.invoke("eda__nonexistent")
        finally:
            await suite.close()
    asyncio.run(check())


def test_cancelled_dispatched_call_drains_without_replay(tmp_path):
    async def check():
        suite = Suite([fixture_spec(tmp_path)])
        try:
            task = asyncio.create_task(suite.invoke("eda__slow"))
            for _ in range(200):
                if (tmp_path / "marker.txt").exists():
                    break
                await asyncio.sleep(.02)
            assert (tmp_path / "marker.txt").read_text() == "started"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            echo = await suite.invoke("eda__echo")
            assert not echo.isError
            assert (tmp_path / "marker.txt").read_text() == "completed"
        finally:
            await suite.close()
    asyncio.run(check())


def test_restrictions_and_disabled_backends(tmp_path):
    async def check():
        suite = Suite([fixture_spec(tmp_path, disabled_tools=["blocked"]),
                       fixture_spec(tmp_path, name="legacy", enabled=False),
                       fixture_spec(tmp_path, name="libraries", enabled_tools=["echo"])])
        try:
            listing = await suite.catalog()
            assert not any(t["name"] == "eda__blocked" for t in listing["tools"])
            assert [t["name"] for t in listing["tools"] if t["source"] == "libraries"] == ["libraries__echo"]
            with pytest.raises(ValueError, match="disabled"):
                await suite.invoke("libraries__slow")
            with pytest.raises(ValueError, match="disabled"):
                await suite.invoke("eda__blocked")
            with pytest.raises(ValueError, match="disabled EDA"):
                await suite.invoke("eda__tool_invoke", {"name": "blocked", "arguments": {}})
            assert suite.workers["legacy"].task is None
            assert not listing["backend_errors"]
        finally:
            await suite.close()
    asyncio.run(check())


def test_cancelled_queued_call_is_never_dispatched(tmp_path):
    async def check():
        suite = Suite([fixture_spec(tmp_path)])
        try:
            first = asyncio.create_task(suite.invoke("eda__slow", {"value": 1}))
            for _ in range(200):
                if (tmp_path / "marker.txt").exists():
                    break
                await asyncio.sleep(.02)
            assert (tmp_path / "marker.txt").read_text() == "started1"
            queued = asyncio.create_task(suite.invoke("eda__slow", {"value": 2}))
            await asyncio.sleep(.03)
            # Native admission waits before the worker queue so compatibility
            # read/modify/write sequences cannot be interleaved.
            assert not queued.done()
            queued.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queued
            await first
            await suite.invoke("eda__echo")
            assert (tmp_path / "marker.txt").read_text() == "completed1"
        finally:
            await suite.close()
    asyncio.run(check())


def test_partial_catalog_reports_unavailable_backend(tmp_path):
    async def check():
        suite = Suite([fixture_spec(tmp_path), BackendSpec("libraries", str(tmp_path / "missing.exe"))])
        try:
            listing = await suite.catalog()
            assert listing["total"] == 6 and not listing["complete"]
            assert "libraries" in listing["backend_errors"]
            assert suite.status()["backends"]["libraries"]["failure_type"]
        finally:
            await suite.close()
    asyncio.run(check())


def test_cloud_extension_has_same_discovery_and_validation():
    class Cloud:
        @staticmethod
        def list_tools():
            return [{"name": "search", "description": "search cloud", "inputSchema": {
                "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}]

        @staticmethod
        def call_tool(name, arguments):
            return {"tool": name, "query": arguments["query"]}

    async def check():
        suite = Suite([], cloud=Cloud)
        catalog = await suite.catalog(backend="cloud", with_schema=True)
        assert catalog["tools"][0]["name"] == "cloud__search"
        assert (await suite.invoke("cloud__search", {"query": "STM32"})).structuredContent["query"] == "STM32"
        with pytest.raises(Exception):
            await suite.invoke("cloud__search", {})
    asyncio.run(check())


def test_reused_validation_preserves_nested_references_errors_and_input_defaults():
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": {"item": {"type": "object", "properties": {"code": {"type": "string", "minLength": 2}},
                             "required": ["code"], "additionalProperties": False}},
        "type": "object", "properties": {"items": {"type": "array", "items": {"$ref": "#/$defs/item"}},
                                          "optional": {"type": "boolean", "default": True}},
        "required": ["items"], "additionalProperties": False,
    }
    class Cloud:
        calls = []

        @staticmethod
        def list_tools():
            return [{"name": "nested", "description": "nested validation fixture", "inputSchema": schema}]

        @staticmethod
        def call_tool(name, arguments):
            Cloud.calls.append(arguments.copy())
            return {"arguments": arguments}

    async def check():
        suite = Suite([], cloud=Cloud)
        valid = {"items": [{"code": "OK"}]}
        for _ in range(2):
            response = await suite.invoke("cloud__nested", valid)
            assert response.structuredContent == {"arguments": valid}
        assert "optional" not in valid
        for invalid in ({"items": [{"code": 3}]}, {"items": [{}], "extra": True},
                        {"items": [{"code": "x", "unknown": 1}]}, {}):
            with pytest.raises(jsonschema.ValidationError) as original:
                jsonschema.validate(invalid, schema)
            with pytest.raises(jsonschema.ValidationError) as forwarded:
                await suite.invoke("cloud__nested", invalid)
            assert forwarded.value.message == original.value.message
            assert list(forwarded.value.path) == list(original.value.path)
            assert list(forwarded.value.schema_path) == list(original.value.schema_path)
        assert Cloud.calls == [valid, valid]
    asyncio.run(check())


def test_protocol_prompt_discovery_does_not_fetch_tool_schemas(tmp_path):
    config = tmp_path / "backends.json"
    config.write_text(json.dumps({"backends": {
        name: {"command": sys.executable,
               "args": [str(ROOT / "tests/suite_fake_worker.py"), str(tmp_path / (name + ".marker"))]}
        for name in ("eda", "libraries")
    }}))

    async def check():
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "suite_stdio.py"),
            "--backend-config", str(config), "--toolset", "minimal", "--no-cloud", "--no-product"])
        async with stdio_client(params) as streams:
            async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=20)) as client:
                await client.initialize()
                await client.list_tools()
                prompts = await client.list_prompts()
                assert [prompt.name for prompt in prompts.prompts] == ["eda__design", "libraries__design"]
                status = (await client.call_tool("suite_status", {})).structuredContent
                assert all(backend["running"] and not backend["catalog_loaded"]
                           for backend in status["backends"].values())
                assert not list(tmp_path.glob("*.catalog"))
                prompt = await client.get_prompt("eda__design")
                assert prompt.messages[0].content.text == "design"
                assert not list(tmp_path.glob("*.catalog"))
                await client.call_tool("suite_catalog", {"backend": "eda"})
                assert (tmp_path / "eda.marker.catalog").read_text() == "listed"
                assert not (tmp_path / "libraries.marker.catalog").exists()
    asyncio.run(check())


@pytest.mark.parametrize("toolset", ["minimal", "full"])
def test_protocol_initialize_discover_forward_and_prompt(tmp_path, toolset):
    config = tmp_path / "backends.json"
    spec = fixture_spec(tmp_path)
    config.write_text(json.dumps({"backends": {"eda": {"command": spec.command, "args": spec.args}}}))

    async def check():
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "suite_stdio.py"),
            "--backend-config", str(config), "--toolset", toolset, "--no-cloud", "--no-product"])
        async with stdio_client(params) as streams:
            async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=20)) as client:
                initialized = await client.initialize()
                assert initialized.serverInfo.name == "mcp altium suite"
                listed = await client.list_tools()
                names = {tool.name for tool in listed.tools}
                assert {"suite_catalog", "suite_invoke", "suite_status"} <= names
                assert len(names) == (3 if toolset == "minimal" else 9)
                status = (await client.call_tool("suite_status", {})).structuredContent
                assert status["backends"]["eda"]["running"] == (toolset == "full")
                catalog = await client.call_tool("suite_catalog", {"query": "echo", "with_schema": True})
                assert catalog.structuredContent["tools"][0]["name"] == "eda__echo"
                forwarded = await client.call_tool("suite_invoke", {"name": "eda__echo", "arguments": {"value": 7}})
                assert forwarded.structuredContent["arguments"] == {"value": 7}
                progress = []

                async def on_progress(value, total, message):
                    progress.append((value, total, message))

                await client.call_tool("suite_invoke", {"name": "eda__echo"}, progress_callback=on_progress)
                assert progress == [(100, 100, "fixture completed")]
                failure = await client.call_tool("suite_invoke", {"name": "eda__failure"})
                assert failure.isError and failure.structuredContent == {"partial": True}
                image = await client.call_tool("suite_invoke", {"name": "eda__image"})
                assert image.content[0].type == "image" and image.meta == {"retained": True}
                if toolset == "full":
                    direct = await client.call_tool("eda__echo", {"value": 9})
                    assert direct.structuredContent["arguments"] == {"value": 9}
                prompts = await client.list_prompts()
                assert [p.name for p in prompts.prompts] == ["eda__design"]
                prompt = await client.get_prompt("eda__design")
                assert prompt.messages[0].content.text == "design"
    asyncio.run(check())
