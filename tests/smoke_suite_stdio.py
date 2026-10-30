"""Offline contract and file-operation acceptance; never invokes an editor tool.
SPDX-License-Identifier: MIT
"""
import argparse
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
import time

import jsonschema
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

COUNTS = {'eda': 457, 'legacy': 37, 'libraries': 34, 'cloud': 4,
          'suite': 3, 'supply': 4, 'route': 4, 'telemetry': 1}


async def run(root: Path, *, toolset='minimal'):
    with tempfile.TemporaryDirectory(prefix='altium-unified-smoke-') as folder:
        scratch = Path(folder)
        env = dict(os.environ)
        env.update(ALTIUM_EXE=str(scratch / 'absent/X2.EXE'),
                   ALTIUM_MCP_RUNTIME=str(scratch / 'runtime'),
                   EDA_AGENT_WORKSPACE=str(scratch / 'workspace'),
                   EDA_AGENT_POINTER_FILE=str(scratch / 'pointer.txt'),
                   ALTIUM_SUITE_PLUGIN_DATA=str(scratch / 'private'),
                   CODEX_HOME=str(scratch / 'codex'),
                   EDA_AGENT_NO_DASHBOARD='1', EDA_AGENT_TOOLSET='full', EDA_AGENT_BACKEND='altium')
        env.pop('PYTHONPATH', None)
        libconfig = scratch / 'libraries.json'
        libconfig.write_text(json.dumps({'allowed_paths': [str(scratch)]}), encoding='utf-8')
        # Absent retired executables prove both adapters run in-process.
        backends = {
            'eda': {'command': sys.executable, 'args': [str(root / 'eda_stdio.py'), '--toolset', 'full', '--no-dashboard']},
            'legacy': {'command': str(scratch / 'retired-legacy.exe'), 'args': []},
            'libraries': {'command': str(scratch / 'retired-library.exe'), 'args': [str(libconfig)]},
        }
        config = scratch / 'backends.json'
        config.write_text(json.dumps({'backends': backends}), encoding='utf-8')
        params = StdioServerParameters(command=sys.executable, args=['-B', str(root / 'suite_stdio.py'),
            '--toolset', toolset, '--backend-config', str(config)], env=env, cwd=str(root))
        started = time.perf_counter()
        async with stdio_client(params) as streams:
            async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=120)) as client:
                init = await client.initialize()
                assert init.serverInfo.name == 'mcp altium suite'
                listed = (await client.list_tools()).tools
                assert len(listed) == (3 if toolset == 'minimal' else 547)
                status = (await client.call_tool('suite_status')).structuredContent
                if toolset == 'minimal':
                    assert not any(item.get('running') for item in status['backends'].values())
                for name in ('legacy', 'libraries'):
                    assert status['backends'][name]['in_process'] is True
                initialization_ms = round((time.perf_counter() - started) * 1000, 2)
                catalogs, schemas, all_names = {}, {}, set()
                for backend, count in COUNTS.items():
                    gateway_tools, offset = [], 0
                    while True:
                        response = await client.call_tool('suite_catalog', {'backend': backend,
                            'with_schema': True, 'limit': 100, 'offset': offset})
                        assert not response.isError, response
                        data = response.structuredContent
                        assert data['complete'] and data['total'] == count, (backend, data)
                        gateway_tools.extend(data['tools'])
                        if data['next_offset'] is None:
                            break
                        offset = data['next_offset']
                    for item in gateway_tools:
                        assert item['name'] not in all_names
                        all_names.add(item['name'])
                        jsonschema.validators.validator_for(item['inputSchema']).check_schema(item['inputSchema'])
                        if 'outputSchema' in item:
                            jsonschema.validators.validator_for(item['outputSchema']).check_schema(item['outputSchema'])
                        assert not item['original_name'].startswith('_suite_')
                    catalogs[backend] = len(gateway_tools)
                    schemas[backend] = {item['original_name']: item['inputSchema'] for item in gateway_tools}
                assert len(all_names) == 544 and catalogs == COUNTS
                if toolset == 'full':
                    assert {tool.name for tool in listed} == all_names | {'suite_catalog', 'suite_invoke', 'suite_status'}
                for backend, contract in (('legacy', 'legacy-tools.json'), ('libraries', 'libraries-v0.3.0.json')):
                    expected = json.loads((root / 'contracts' / contract).read_text(encoding='utf-8'))['tools']
                    assert schemas[backend] == {tool['name']: tool['inputSchema'] for tool in expected}
                direct_parameters = StdioServerParameters(command=sys.executable,
                    args=backends['eda']['args'], env=env, cwd=str(root))
                async with stdio_client(direct_parameters) as direct_streams:
                    async with ClientSession(*direct_streams) as direct:
                        await direct.initialize()
                        expected = (await direct.list_tools()).tools
                        assert len(expected) == 457
                        assert schemas['eda'] == {tool.name: tool.inputSchema for tool in expected}

                async def invoke(name, arguments=None):
                    response = await client.call_tool('suite_invoke', {'name': name, 'arguments': arguments or {}})
                    assert not response.isError, (name, response)
                    return response.structuredContent

                await invoke('legacy__get_server_status')
                snapshot = await invoke('eda__design_connectivity_snapshot', {
                    'data': {'pins': [{'component': 'R1', 'pin': '1', 'net': 'VCC'}], 'count': 1}, 'complete': True})
                assert snapshot['count'] == 1
                await invoke('cloud__cloud_library_providers')
                sch, pcb = str(scratch / 'synthetic.SchLib'), str(scratch / 'synthetic.PcbLib')
                await invoke('libraries__write_schlib', {'filepath': sch, 'symbols': [{'name': 'ScratchSymbol',
                    'pins': [{'designator': '1', 'name': 'IN', 'x': 0, 'y': 0, 'length': 20, 'orientation': 'right'}]}]})
                assert (await invoke('libraries__list_components', {'filepath': sch}))['components'] == ['ScratchSymbol']
                await invoke('libraries__write_pcblib', {'filepath': pcb, 'auto_designator': False,
                    'footprints': [{'name': 'ScratchFootprint', 'pads': [{'designator': '1', 'x': 0, 'y': 0,
                        'width': 1, 'height': 2, 'shape': 'rectangle', 'layer': 'Top Layer'}]}]})
                await invoke('libraries__update_pad', {'filepath': pcb, 'component_name': 'ScratchFootprint',
                    'designator': '1', 'updates': {'width': 1.5}})
                pad = (await invoke('libraries__get_component', {'filepath': pcb,
                    'component_name': 'ScratchFootprint'}))['component']['pads'][0]
                assert abs(pad['width'] - 1.5) < 0.00001
                missing = await client.call_tool('suite_invoke', {'name': 'libraries__list_components',
                    'arguments': {'filepath': str(scratch / 'missing.SchLib')}})
                assert missing.isError
                assert not list((scratch / 'workspace').glob('bridge-ready*'))
                assert not (scratch / 'runtime' / 'editor-binding.json').exists()
                output = {'server': init.serverInfo.name, 'advertised_tools': len(listed),
                    'initialize_list_status_ms': initialization_ms, 'backend_tool_counts': catalogs,
                    'all_544_input_schemas_valid': True, 'legacy_and_library_contracts_preserved': True,
                    'eda_direct_schemas_preserved': True, 'retired_processes_required': False,
                    'synthetic_schlib_write_read': True, 'synthetic_pcblib_write_edit_read': True,
                    'library_missing_file_error_preserved': True, 'native_editor_commands_executed': 0}
                print(json.dumps(output, indent=2))
                return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--toolset', choices=('minimal', 'full'), default='minimal')
    options = parser.parse_args()
    asyncio.run(run(options.root.resolve(), toolset=options.toolset))
