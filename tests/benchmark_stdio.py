"""Read-only latency benchmark against an open Altium editor.

Uses the same entry point for all samples. Idle samples intentionally allow
native shortcuts to regain the script engine; no design edits or saves occur.
"""
import argparse
import asyncio
import datetime
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def benchmark(args):
    env = dict(os.environ)
    if args.workspace:
        env['EDA_AGENT_WORKSPACE'] = str(args.workspace)
    if args.runtime:
        env['ALTIUM_MCP_RUNTIME'] = str(args.runtime)
    params = StdioServerParameters(command=str(args.python),
        args=[str(args.root / 'eda_stdio.py'), '--no-dashboard', '--toolset', args.toolset], cwd=str(args.root), env=env)
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in args.design_file}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    samples = []
    started = time.perf_counter()
    with args.output.with_suffix('.log').open('w', encoding='utf-8') as log:
        async with stdio_client(params, errlog=log) as streams:
            async with ClientSession(*streams, read_timeout_seconds=datetime.timedelta(seconds=120)) as client:
                await client.initialize()
                initialized_ms = (time.perf_counter() - started) * 1000
                if args.toolset == 'minimal':
                    discovery = await client.call_tool('tool_catalog', {'query': 'app_get_active_document', 'with_schema': True})
                    if discovery.isError:
                        raise RuntimeError(discovery)
                async def sample(group):
                    start = time.perf_counter()
                    name = 'tool_invoke' if args.toolset == 'minimal' else 'app_get_active_document'
                    arguments = {'name': 'app_get_active_document', 'arguments': {}} if args.toolset == 'minimal' else {}
                    reply = await client.call_tool(name, arguments)
                    elapsed = (time.perf_counter() - start) * 1000
                    if reply.isError:
                        raise RuntimeError(reply)
                    payload = reply.structuredContent
                    if payload is None:
                        payload = json.loads(next(item.text for item in reply.content if hasattr(item, 'text')))
                    if args.toolset == 'minimal':
                        if 'error' in payload:
                            raise RuntimeError(payload)
                        payload = payload['result']
                    if not isinstance(payload, dict) or not payload.get('file_path'):
                        raise RuntimeError(payload)
                    row = {'group': group, 'ms': round(elapsed, 2)}
                    samples.append(row)
                    print(json.dumps(row), flush=True)
                await sample('initial')
                for _ in range(args.warm):
                    await sample('warm')
                for _ in range(args.idle):
                    await asyncio.sleep(args.idle_seconds)
                    await sample('after_idle')
                listing = await client.list_tools()
                schema_bytes = len(json.dumps([t.model_dump() for t in listing.tools], ensure_ascii=False).encode('utf-8'))
    summary = {'root': str(args.root), 'toolset': args.toolset, 'initialized_ms': round(initialized_ms, 2),
               'tools': len(listing.tools), 'advertised_schema_bytes': schema_bytes, 'samples': samples,
               'design_files_unchanged': all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == digest for p,digest in hashes.items())}
    for group in ('warm', 'after_idle'):
        values = [s['ms'] for s in samples if s['group'] == group]
        summary[group] = {'median_ms': round(statistics.median(values), 2),
                          'min_ms': min(values), 'max_ms': max(values), 'count': len(values)} if values else None
    if not summary['design_files_unchanged']:
        raise RuntimeError('A design file changed during the read-only benchmark')
    args.output.write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k != 'samples'}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--runtime', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--toolset', choices=('full', 'minimal'), default='full')
    parser.add_argument('--design-file', type=Path, action='append', default=[])
    parser.add_argument('--warm', type=int, default=8)
    parser.add_argument('--idle', type=int, default=3)
    parser.add_argument('--idle-seconds', type=float, default=3)
    asyncio.run(benchmark(parser.parse_args()))
