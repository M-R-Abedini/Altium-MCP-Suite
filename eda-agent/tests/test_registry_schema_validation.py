"""Minimal discovery and execution must preserve FastMCP's argument contract."""
from typing import Literal

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from eda_agent.tools import register_backend
from eda_agent.tools.registry import ToolRegistry


@pytest.mark.anyio
@pytest.mark.parametrize('backend', ['altium', 'kicad', 'easyeda'])
async def test_every_hidden_schema_matches_the_full_backend(backend):
    full = FastMCP('full-schema')
    hidden = ToolRegistry()
    register_backend(full, backend, 'full')
    register_backend(hidden, backend, 'full')
    for tool in await full.list_tools():
        assert hidden.get(tool.name).inputSchema == tool.inputSchema, tool.name


@pytest.mark.anyio
async def test_broad_catalog_does_not_build_schemas_that_will_be_omitted():
    hidden = ToolRegistry()
    register_backend(hidden, 'altium', 'full')
    reply = await hidden.get('tool_catalog').fn(with_schema=True)
    assert 'schema_omitted' in reply
    assert all('native_tool' not in spec.__dict__ for spec in await hidden.list_tools())
    narrow = await hidden.get('tool_catalog').fn(query='proj_set_variant_fitted', with_schema=True)
    assert 0 < narrow['count'] <= 40
    assert 'proj_set_variant_fitted' in {entry['name'] for entry in narrow['tools']}
    built = [spec.name for spec in await hidden.list_tools() if 'native_tool' in spec.__dict__]
    assert set(built) == {entry['name'] for entry in narrow['tools']}


@pytest.mark.anyio
async def test_invalid_arrays_and_enums_are_rejected_before_the_handler():
    hidden = ToolRegistry()
    calls = []
    @hidden.tool()
    async def update(pins: list[int], fitted: bool, mode: Literal['read', 'write'], note: str | None = None):
        calls.append((pins, fitted, mode, note))
        return {'pins': pins, 'fitted': fitted}
    for arguments in ({'pins': 42, 'fitted': True, 'mode': 'read'},
                      {'pins': [1], 'fitted': True, 'mode': 'invalid'},
                      {'pins': ['not-a-pin'], 'fitted': True, 'mode': 'read'}):
        with pytest.raises(ToolError):
            await hidden.call_tool('update', arguments)
    assert not calls
    # Match FastMCP's validated conversion instead of Python's truthy "false".
    result = await hidden.call_tool('update', {'pins': ['1', 2], 'fitted': 'false', 'mode': 'write'})
    assert result == {'pins': [1, 2], 'fitted': False}
    assert calls == [([1, 2], False, 'write', None)]
    spec = hidden.get('update')
    assert spec.inputSchema['properties']['pins']['type'] == 'array'
    assert spec.inputSchema['properties']['mode']['enum'] == ['read', 'write']
    assert spec.native_tool is spec.native_tool
