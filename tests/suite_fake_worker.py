"""Protocol fixture for gateway tests; never accesses Altium or user files."""
import asyncio
import json
from pathlib import Path
import sys

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

server = Server("fixture-worker")
marker = Path(sys.argv[1])


@server.list_tools()
async def tools():
    marker.with_name(marker.name + ".catalog").write_text("listed", encoding="utf-8")
    listing = [types.Tool(name=name, description="fixture " + name,
                       inputSchema={"type": "object", "properties": {"value": {"type": "integer"}},
                                    "additionalProperties": False})
            for name in ("echo", "failure", "image", "slow", "tool_invoke", "blocked")]
    next(tool for tool in listing if tool.name == "tool_invoke").inputSchema = {
        "type": "object", "properties": {"name": {"type": "string"}, "arguments": {"type": "object"}},
        "required": ["name"], "additionalProperties": False}
    return listing


@server.call_tool()
async def call(name, arguments):
    if name == "slow":
        marker.write_text("started" + str(arguments.get("value", "")), encoding="utf-8")
        await asyncio.sleep(.2)
        marker.write_text("completed" + str(arguments.get("value", "")), encoding="utf-8")
    if name == "echo":
        context = server.request_context
        if context.meta is not None and context.meta.progressToken is not None:
            await context.session.send_progress_notification(context.meta.progressToken, 100, 100, "fixture completed")
    if name == "failure":
        return types.CallToolResult(content=[types.TextContent(type="text", text="native failed")],
                                    structuredContent={"partial": True}, isError=True)
    if name == "image":
        return types.CallToolResult(content=[types.ImageContent(type="image", data="aGVsbG8=", mimeType="image/png")],
                                    structuredContent={"image": True}, _meta={"retained": True})
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(arguments))],
                                structuredContent={"name": name, "arguments": arguments})


@server.list_prompts()
async def prompts():
    return [types.Prompt(name="design", description="fixture prompt")]


@server.get_prompt()
async def prompt(name, arguments=None):
    return types.GetPromptResult(messages=[types.PromptMessage(role="user", content=types.TextContent(type="text", text=name))])


async def main():
    async with stdio_server() as streams:
        await server.run(*streams, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
