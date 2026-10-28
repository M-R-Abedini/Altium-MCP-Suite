"""One MCP endpoint for the coordinated Altium tools and cloud libraries.

Worker processes are implementation details: clients initialize only this
server. Keeping the existing wrappers preserves their native engine ownership
and cancellation safeguards instead of creating a second Altium transport.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
import importlib
import json
import os
from pathlib import Path
import sys
from typing import Any

import jsonschema
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

ROOT = Path(__file__).resolve().parent
BACKENDS = ("eda", "legacy", "libraries")
SERVER_NAME = "mcp altium suite"
INSTRUCTIONS = """One endpoint provides Altium design, native operations, library file
editing, and cloud library discovery. Call suite_catalog with a query and
with_schema=true, then suite_invoke with its exact namespaced tool name and
arguments. eda__ tools drive the coordinated EDA engine; legacy__ tools retain
the one-shot Altium operations; libraries__ tools edit library files; cloud__
tools discover and stage external libraries. Discover before declaring a
capability absent. Inspect the intended project/document before edits: PcbDoc,
SchDoc, PcbLib and SchLib tools target different documents. Prefer batch design
operations. Cloud models require datasheet/pin/package verification before use.
Coordinates in native design tools are mils unless their schema says otherwise.
"""


def result(value: dict, *, error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(value, ensure_ascii=False))],
        structuredContent=value, isError=error,
    )


@dataclass
class BackendSpec:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    timeout_seconds: float = 900
    startup_timeout_seconds: float = 60
    enabled: bool = True
    enabled_tools: list[str] | None = None
    disabled_tools: list[str] = field(default_factory=list)

    def allows(self, name: str) -> bool:
        return (self.enabled and name not in self.disabled_tools and
                (self.enabled_tools is None or name in self.enabled_tools))


def load_specs(path: Path | None = None) -> list[BackendSpec]:
    if path is None:
        shared = {"EDA_AGENT_NO_DASHBOARD": "1", "EDA_AGENT_TOOLSET": "full"}
        return [
            BackendSpec("eda", sys.executable,
                        [str(ROOT / "eda_stdio.py"), "--toolset", "full", "--no-dashboard"], shared),
            BackendSpec("legacy", sys.executable,
                        [str(ROOT / "coffeenmusic/server/codex_stdio.py")], shared),
            BackendSpec("libraries", str(ROOT / "altium-designer-mcp/target/release/altium-designer-mcp.exe"),
                        [str(ROOT / "libraries.local.json")]),
        ]
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or not isinstance(payload.get("backends"), dict):
        raise ValueError("Backend configuration must contain a backends object")
    specs = []
    for name, raw in payload["backends"].items():
        if name not in BACKENDS or not isinstance(raw, dict):
            raise ValueError(f"Invalid backend definition: {name}")
        unknown = set(raw) - (set(BackendSpec.__dataclass_fields__) - {"name"})
        if unknown:
            raise ValueError(f"Unsupported configuration keys for {name}: {sorted(unknown)}")
        spec = BackendSpec(name=name, **raw)
        if not isinstance(spec.command, str) or not spec.command.strip():
            raise ValueError(f"Backend {name} needs a command")
        if not isinstance(spec.args, list) or not all(isinstance(v, str) for v in spec.args):
            raise ValueError(f"Backend {name}: args must be strings")
        if not isinstance(spec.env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in spec.env.items()):
            raise ValueError(f"Backend {name}: env must map strings to strings")
        if spec.cwd is not None and not isinstance(spec.cwd, str):
            raise ValueError(f"Backend {name}: cwd must be a string")
        if not isinstance(spec.enabled, bool):
            raise ValueError(f"Backend {name}: enabled must be boolean")
        for key in ("enabled_tools", "disabled_tools"):
            names = getattr(spec, key)
            if key == "enabled_tools" and names is None:
                continue
            if not isinstance(names, list) or not all(isinstance(v, str) for v in names):
                raise ValueError(f"Backend {name}: {key} must be tool names")
        for key in ("timeout_seconds", "startup_timeout_seconds"):
            seconds = getattr(spec, key)
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 < seconds <= 86400:
                raise ValueError(f"Backend {name}: {key} must be between 0 and 86400")
        specs.append(spec)
    return specs


class Worker:
    """Own every stdio context in one task; never replay dispatched commands.

    A cancelled request which has not started is skipped. A dispatched request
    runs to completion in the actor, so its child retains the native lock until
    it observes completion. Client cancellation cannot kill an Altium script.
    """
    def __init__(self, spec: BackendSpec):
        self.spec = spec
        self.queue: asyncio.Queue = asyncio.Queue()
        self.task: asyncio.Task | None = None
        self.running = False
        self.failure: str | None = None
        self.capabilities: types.ServerCapabilities | None = None

    async def request(self, method: str, **params):
        if not self.spec.enabled:
            raise ValueError(f"Backend {self.spec.name} is disabled")
        if self.task is None:
            self.task = asyncio.create_task(self._run(), name=f"suite-{self.spec.name}")
        if self.task.done():
            raise RuntimeError(f"Backend {self.spec.name} stopped ({self.failure or 'closed'}); reconnect the suite")
        future = asyncio.get_running_loop().create_future()
        await self.queue.put((future, method, params))
        # Normal cancellation cancels only this future. The actor is independent.
        return await future

    async def _run(self):
        current = None
        try:
            environment = dict(os.environ)
            environment.update(self.spec.env)
            parameters = StdioServerParameters(command=self.spec.command, args=self.spec.args,
                                               env=environment, cwd=self.spec.cwd)
            async with stdio_client(parameters, errlog=sys.stderr) as streams:
                async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=self.spec.timeout_seconds)) as session:
                    async with asyncio.timeout(self.spec.startup_timeout_seconds):
                        initialized = await session.initialize()
                    self.capabilities = initialized.capabilities
                    self.running = True
                    while True:
                        job = await self.queue.get()
                        if job is None:
                            break
                        current, method, params = job
                        if current.cancelled():
                            current = None
                            continue
                        try:
                            # Prompt discovery needs only initialization capabilities,
                            # not every tool schema from the backend.
                            value = self.capabilities if method == "_capabilities" else await getattr(session, method)(**params)
                        except Exception as exc:
                            if not current.done():
                                current.set_exception(exc)
                        else:
                            if not current.done():
                                current.set_result(value)
                        current = None
        except BaseException as exc:
            self.failure = type(exc).__name__
            error = RuntimeError(f"Backend {self.spec.name} unavailable ({self.failure}); reconnect the suite")
            if current is not None and not current.done():
                current.set_exception(error)
            while not self.queue.empty():
                queued = self.queue.get_nowait()
                if queued is not None and not queued[0].done():
                    queued[0].set_exception(error)
        finally:
            self.running = False

    async def close(self):
        if self.task is not None and not self.task.done():
            # Drain work already dispatched before closing the transport.
            await self.queue.put(None)
            await asyncio.shield(self.task)


class Suite:
    def __init__(self, specs: list[BackendSpec], *, toolset="minimal", cloud=None):
        self.workers = {spec.name: Worker(spec) for spec in specs}
        self.toolset = toolset
        self.cloud = cloud
        self._catalogs: dict[str, list[types.Tool]] = {}
        self._validators: dict[tuple[str, str], Any] = {}
        self._catalog_locks = {name: asyncio.Lock() for name in (*self.workers, "cloud")}

    @property
    def backend_names(self):
        names = list(self.workers)
        if self.cloud is not None:
            names.append("cloud")
        return names

    async def tools(self, backend: str) -> list[types.Tool]:
        if backend not in self.backend_names:
            raise ValueError(f"Unknown backend {backend!r}; choose {self.backend_names}")
        async with self._catalog_locks[backend]:
            if backend in self._catalogs:
                return self._catalogs[backend]
            if backend == "cloud":
                native = [types.Tool.model_validate(raw) for raw in await asyncio.to_thread(self.cloud.list_tools)]
            else:
                worker = self.workers[backend]
                if not worker.spec.enabled:
                    return []
                native, cursor, seen = [], None, set()
                while True:
                    page = await worker.request("list_tools", cursor=cursor)
                    native.extend(tool for tool in page.tools if worker.spec.allows(tool.name))
                    cursor = page.nextCursor
                    if not cursor:
                        break
                    if cursor in seen:
                        raise RuntimeError(f"Backend {backend} repeated a catalog cursor")
                    seen.add(cursor)
            if len({tool.name for tool in native}) != len(native):
                raise ValueError(f"Backend {backend} contains duplicate tool names")
            self._catalogs[backend] = native
            return native

    async def catalog(self, query="", backend=None, with_schema=False, limit=30, offset=0):
        if not isinstance(query, str) or not isinstance(with_schema, bool):
            raise ValueError("query must be a string and with_schema a boolean")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a nonnegative integer")
        names = [backend] if backend is not None else self.backend_names
        if any(name not in self.backend_names for name in names):
            raise ValueError(f"Unknown backend; choose {self.backend_names}")
        listings = await asyncio.gather(*(self.tools(name) for name in names), return_exceptions=True)
        items, errors = [], {}
        words = query.casefold().split()
        for source, listing in zip(names, listings):
            if isinstance(listing, BaseException):
                errors[source] = str(listing)
                continue
            for tool in listing:
                qualified = source + "__" + tool.name
                haystack = (qualified + " " + (tool.description or "")).casefold()
                if not all(word in haystack for word in words):
                    continue
                item = {"name": qualified, "source": source, "original_name": tool.name,
                        "description": tool.description if with_schema else (tool.description or "")[:240]}
                if tool.annotations is not None:
                    item["annotations"] = tool.annotations.model_dump(by_alias=True, exclude_none=True)
                if with_schema:
                    item["inputSchema"] = tool.inputSchema
                    if tool.outputSchema is not None:
                        item["outputSchema"] = tool.outputSchema
                items.append(item)
        items.sort(key=lambda item: item["name"])
        return {"tools": items[offset:offset + limit], "total": len(items), "offset": offset,
                "next_offset": offset + limit if offset + limit < len(items) else None,
                "complete": not errors, "backend_errors": errors}

    async def invoke(self, name: str, arguments: dict | None = None, *, meta=None, progress_callback=None):
        if not isinstance(name, str) or "__" not in name:
            raise ValueError("Use the exact namespaced name returned by suite_catalog")
        backend, original = name.split("__", 1)
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be a JSON object")
        tool = next((tool for tool in await self.tools(backend) if tool.name == original), None)
        if tool is None:
            raise ValueError(f"Unknown or disabled tool: {name}")
        key = (backend, original)
        validator = self._validators.get(key)
        if validator is None:
            validator_class = jsonschema.validators.validator_for(tool.inputSchema)
            validator_class.check_schema(tool.inputSchema)
            validator = validator_class(tool.inputSchema)
            self._validators[key] = validator
        # Match jsonschema.validate's error selection, including nested $defs,
        # while checking the stable registered schema only once per tool.
        error = jsonschema.exceptions.best_match(validator.iter_errors(arguments))
        if error is not None:
            raise error
        if backend == "cloud":
            return result(await asyncio.to_thread(self.cloud.call_tool, original, arguments))
        worker = self.workers[backend]
        # The EDA meta-invoker must not bypass a migrated per-tool restriction.
        if backend == "eda" and original == "tool_invoke":
            nested = arguments.get("name")
            if not isinstance(nested, str) or not worker.spec.allows(nested):
                raise ValueError(f"Unknown or disabled EDA tool: {nested}")
        return await worker.request("call_tool", name=original, arguments=arguments,
                                    meta=meta, progress_callback=progress_callback)

    def status(self):
        backends = {name: {"enabled": worker.spec.enabled, "running": worker.running,
                           "failure_type": worker.failure,
                           "catalog_loaded": name in self._catalogs,
                           "tool_count": len(self._catalogs[name]) if name in self._catalogs else None,
                           "queued_requests": worker.queue.qsize(),
                           "tool_restrictions": bool(worker.spec.disabled_tools or worker.spec.enabled_tools is not None)}
                    for name, worker in self.workers.items()}
        if self.cloud is not None:
            backends["cloud"] = {"enabled": True, "in_process": True,
                                 "catalog_loaded": "cloud" in self._catalogs}
        return {"server": SERVER_NAME, "toolset": self.toolset, "backends": backends,
                "architecture": "one client endpoint; lazy coordinated internal workers",
                "native_editor_probed": False}

    async def close(self):
        await asyncio.gather(*(worker.close() for worker in self.workers.values()))


META_TOOLS = [
    types.Tool(name="suite_catalog", description="Discover all Altium Suite capabilities by query/backend. Request with_schema=true before unfamiliar calls. Does not execute design operations.",
               inputSchema={"type": "object", "properties": {
                   "query": {"type": "string", "default": ""},
                   "backend": {"type": ["string", "null"], "enum": ["eda", "legacy", "libraries", "cloud", None]},
                   "with_schema": {"type": "boolean", "default": False},
                   "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
                   "offset": {"type": "integer", "minimum": 0, "default": 0}}, "additionalProperties": False},
               annotations=types.ToolAnnotations(readOnlyHint=True, openWorldHint=False)),
    types.Tool(name="suite_invoke", description="Run an exact namespaced tool discovered through suite_catalog, using its arguments schema. Preserves native errors, structured results and images. Can modify designs/library files depending on selected tool.",
               inputSchema={"type": "object", "properties": {"name": {"type": "string"},
                   "arguments": {"type": ["object", "null"], "default": None}},
                   "required": ["name"], "additionalProperties": False},
               annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)),
    types.Tool(name="suite_status", description="Inspect suite backend configuration/load status without starting a worker or probing the editor. No secrets or command environment values are returned.",
               inputSchema={"type": "object", "additionalProperties": False},
               annotations=types.ToolAnnotations(readOnlyHint=True, openWorldHint=False)),
]


def create_server(suite: Suite) -> Server:
    @asynccontextmanager
    async def lifespan(server):
        try:
            yield suite
        finally:
            await suite.close()

    server = Server(SERVER_NAME, version="1.0.0", instructions=INSTRUCTIONS, lifespan=lifespan)

    @server.list_tools()
    async def list_tools():
        advertised = list(META_TOOLS)
        if suite.toolset == "full":
            # A full listing must fail visibly if a configured backend cannot
            # supply its tools; never silently advertise a truncated surface.
            for backend in suite.backend_names:
                for tool in await suite.tools(backend):
                    advertised.append(tool.model_copy(update={"name": backend + "__" + tool.name,
                                                               "description": f"[{backend}] " + (tool.description or "")}))
        return advertised

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        if name == "suite_catalog":
            return result(await suite.catalog(**arguments))
        if name == "suite_status":
            return result(suite.status())
        if name == "suite_invoke":
            target, params = arguments["name"], arguments.get("arguments")
        else:
            target, params = name, arguments
        request = server.request_context
        progress_token = request.meta.progressToken if request.meta is not None else None

        async def progress(value, total=None, message=None):
            if progress_token is not None:
                await request.session.send_progress_notification(progress_token, value, total, message)

        return await suite.invoke(target, params,
                                  meta=request.meta.model_dump(by_alias=True, exclude_none=True) if request.meta else None,
                                  progress_callback=progress if progress_token is not None else None)

    @server.list_prompts()
    async def list_prompts():
        async def backend_prompts(source, worker):
            if not worker.spec.enabled:
                return []
            capabilities = await worker.request("_capabilities")
            if capabilities is None or capabilities.prompts is None:
                return []
            prompts = []
            cursor, seen = None, set()
            while True:
                page = await worker.request("list_prompts", cursor=cursor)
                prompts.extend(prompt.model_copy(update={"name": source + "__" + prompt.name}) for prompt in page.prompts)
                cursor = page.nextCursor
                if not cursor:
                    break
                if cursor in seen:
                    raise ValueError(f"Backend {source} repeated a prompt cursor")
                seen.add(cursor)
            return prompts
        # These are protocol-only reads. Independent workers can initialize in
        # parallel; native editor operations retain their shared engine guards.
        listings = await asyncio.gather(*(backend_prompts(source, worker)
                                         for source, worker in suite.workers.items()))
        return [prompt for listing in listings for prompt in listing]

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict[str, str] | None = None):
        if "__" not in name:
            raise ValueError("Use a namespaced prompt name")
        source, original = name.split("__", 1)
        if source not in suite.workers:
            raise ValueError("Unknown prompt backend")
        return await suite.workers[source].request("get_prompt", name=original, arguments=arguments)

    return server


async def serve(options):
    cloud = None
    if not options.no_cloud:
        try:
            cloud = importlib.import_module("suite_cloud_libraries")
        except ModuleNotFoundError as exc:
            if exc.name != "suite_cloud_libraries":
                raise
    suite = Suite(load_specs(options.backend_config), toolset=options.toolset, cloud=cloud)
    server = create_server(suite)
    async with stdio_server() as streams:
        await server.run(*streams, server.create_initialization_options())


def main():
    parser = argparse.ArgumentParser(description="Unified mcp altium suite endpoint")
    parser.add_argument("--toolset", choices=("minimal", "full"), default="minimal")
    parser.add_argument("--backend-config", type=Path)
    parser.add_argument("--no-cloud", action="store_true", help="Do not load the optional cloud library module")
    options = parser.parse_args()
    asyncio.run(serve(options))


if __name__ == "__main__":
    main()
