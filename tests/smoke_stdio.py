"""Unified MCP smoke. --live additionally verifies native EDA engine recovery.

The default run uses synthetic library files and pure connectivity operations.
It never discovers or opens an editor document. Live mode shares the configured
EDA workspace and requires an explicitly prepared scratch-only Altium session.
SPDX-License-Identifier: MIT
"""
import argparse
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from smoke_suite_stdio import run as unified_smoke

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main(live=False):
    await unified_smoke(ROOT, toolset='full')
    if not live:
        return
    parameters = StdioServerParameters(command=sys.executable,
        args=['-B', str(ROOT / 'eda_stdio.py'), '--toolset', 'full', '--no-dashboard'],
        env=dict(os.environ), cwd=str(ROOT))
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=160)) as client:
            await client.initialize()
            import bridge_coordination as coordination
            established = await client.call_tool('app_context', {})
            assert not established.isError and established.structuredContent['bridge_answering'], established
            with coordination.engine_lock():
                coordination.stop_eda()
            recovered = await client.call_tool('app_context', {})
            assert not recovered.isError, recovered
            context = recovered.structuredContent
            assert context['bridge_answering'] and context['script_version_match'], context
            with coordination.engine_lock():
                coordination.stop_eda()
            print(json.dumps({'server': 'eda', 'live_recovery': True,
                'engine_released_at_exit': True}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--root', type=Path, default=ROOT)
    options = parser.parse_args()
    ROOT = options.root.resolve()
    sys.path.insert(0, str(ROOT))
    asyncio.run(main(options.live))
