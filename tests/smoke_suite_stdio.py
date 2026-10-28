"""Offline/read-only real-backend acceptance for the unified MCP endpoint."""
import argparse
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import tomllib

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(root: Path, library_command: Path, client_config: Path | None = None):
    with tempfile.TemporaryDirectory(prefix="altium-unified-smoke-") as folder:
        scratch = Path(folder)
        env = dict(os.environ)
        env.update(ALTIUM_EXE=str(scratch / "absent/X2.EXE"),
                   ALTIUM_MCP_RUNTIME=str(scratch / "runtime"),
                   EDA_AGENT_WORKSPACE=str(scratch / "workspace"),
                   EDA_AGENT_POINTER_FILE=str(scratch / "pointer.txt"),
                   EDA_AGENT_NO_DASHBOARD="1", EDA_AGENT_TOOLSET="full", EDA_AGENT_BACKEND="altium")
        env.pop("PYTHONPATH", None)
        libconfig = scratch / "libraries.json"
        libconfig.write_text(json.dumps({"allowed_paths": [str(scratch)]}))
        config = scratch / "backends.json"
        backends = {
            "eda": {"command": sys.executable, "args": [str(root / "eda_stdio.py"), "--toolset", "full", "--no-dashboard"]},
            "legacy": {"command": sys.executable, "args": [str(root / "coffeenmusic/server/codex_stdio.py")]},
            "libraries": {"command": str(library_command), "args": [str(libconfig)]},
        }
        if client_config is not None:
            configured = tomllib.loads(client_config.read_text(encoding="utf-8"))["mcp_servers"]
            for source, original in (("eda", "eda-agent"), ("legacy", "altium"), ("libraries", "altium-libraries")):
                current = configured[original]
                backends[source]["command"] = current["command"]
                # Test the candidate source wrappers with the actual installed
                # interpreter/binary and narrowly copied original backend env.
                backends[source]["env"] = dict(current.get("env", {}))
                backends[source]["env"].update({key: env[key] for key in (
                    "ALTIUM_EXE", "ALTIUM_MCP_RUNTIME", "EDA_AGENT_WORKSPACE", "EDA_AGENT_POINTER_FILE",
                    "EDA_AGENT_NO_DASHBOARD", "EDA_AGENT_TOOLSET", "EDA_AGENT_BACKEND")})
        config.write_text(json.dumps({"backends": backends}))
        params = StdioServerParameters(command=sys.executable, args=[str(root / "suite_stdio.py"),
            "--backend-config", str(config)], env=env)
        started = time.perf_counter()
        async with stdio_client(params) as streams:
            async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=120)) as client:
                init = await client.initialize()
                assert init.serverInfo.name == "mcp altium suite"
                listed = await client.list_tools()
                assert {t.name for t in listed.tools} == {"suite_catalog", "suite_invoke", "suite_status"}
                status = (await client.call_tool("suite_status")).structuredContent
                assert not any(item.get("running") for item in status["backends"].values())
                initialization_ms = round((time.perf_counter() - started) * 1000, 2)
                catalogs = {}
                schema_equal = {}
                for backend in ("eda", "legacy", "libraries"):
                    response = await client.call_tool("suite_catalog", {"backend": backend, "with_schema": True, "limit": 100})
                    assert not response.isError, response
                    data = response.structuredContent
                    assert data["complete"] and data["total"] > 0, data
                    catalogs[backend] = data["total"]
                    gateway_tools = list(data["tools"])
                    while data["next_offset"] is not None:
                        page = await client.call_tool("suite_catalog", {"backend": backend, "with_schema": True,
                            "limit": 100, "offset": data["next_offset"]})
                        assert not page.isError
                        data = page.structuredContent
                        gateway_tools.extend(data["tools"])
                    direct_parameters = StdioServerParameters(command=backends[backend]["command"],
                        args=backends[backend]["args"], env={**env, **backends[backend].get("env", {})})
                    async with stdio_client(direct_parameters) as direct_streams:
                        async with ClientSession(*direct_streams) as direct:
                            await direct.initialize()
                            original_tools, cursor = [], None
                            while True:
                                direct_page = await direct.list_tools(cursor=cursor)
                                original_tools.extend(direct_page.tools)
                                cursor = direct_page.nextCursor
                                if not cursor:
                                    break
                    expected = {tool.name: tool.inputSchema for tool in original_tools}
                    actual = {tool["original_name"]: tool["inputSchema"] for tool in gateway_tools}
                    assert actual == expected, backend
                    schema_equal[backend] = True
                legacy = await client.call_tool("suite_invoke", {"name": "legacy__get_server_status"})
                assert not legacy.isError, legacy
                snapshot = await client.call_tool("suite_invoke", {"name": "eda__design_connectivity_snapshot", "arguments": {
                    "data": {"pins": [{"component": "R1", "pin": "1", "net": "VCC"}], "count": 1}, "complete": True}})
                assert not snapshot.isError and snapshot.structuredContent["count"] == 1, snapshot
                cloud = await client.call_tool("suite_catalog", {"backend": "cloud", "with_schema": True})
                cloud_available = not cloud.isError and cloud.structuredContent["complete"]
                if cloud_available:
                    providers = await client.call_tool("suite_invoke", {"name": "cloud__cloud_library_providers"})
                    assert not providers.isError, providers
                    catalogs["cloud"] = cloud.structuredContent["total"]
                # Calling a library reader with a nonexistent permitted file
                # tests real dispatch and error fidelity, without creating it.
                missing = await client.call_tool("suite_invoke", {"name": "libraries__list_components", "arguments": {
                    "filepath": str(scratch / "missing.SchLib")}})
                assert missing.isError, missing
                assert "validation" not in " ".join(block.text for block in missing.content if block.type == "text").lower(), missing
                sample = Path(__file__).resolve().parents[1] / "altium-designer-mcp/scripts/samples/symbols.SchLib"
                shutil.copyfile(sample, scratch / "sample.SchLib")
                read = await client.call_tool("suite_invoke", {"name": "libraries__list_components", "arguments": {
                    "filepath": str(scratch / "sample.SchLib")}})
                assert not read.isError, read
                assert not list((scratch / "workspace").glob("bridge-ready*"))
                output = {"server": init.serverInfo.name, "advertised_tools": len(listed.tools),
                          "initialize_list_status_ms": initialization_ms, "backend_tool_counts": catalogs,
                          "original_input_schemas_preserved": schema_equal,
                          "legacy_status_forwarded": True, "eda_offline_connectivity_forwarded": True,
                          "library_missing_file_error_preserved": True, "cloud_available": cloud_available,
                          "library_real_fixture_read": True,
                          "native_editor_launched": False}
                print(json.dumps(output, indent=2))
                return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--library-command", type=Path)
    parser.add_argument("--client-config", type=Path, help="Use only the three existing backend commands/env from this client TOML")
    options = parser.parse_args()
    root = options.root.resolve()
    library = options.library_command or root / "altium-designer-mcp/target/release/altium-designer-mcp.exe"
    asyncio.run(run(root, library, options.client_config))
