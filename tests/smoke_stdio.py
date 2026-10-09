"""Read-only MCP smoke test. --live additionally exercises Altium recovery."""
import argparse
import asyncio
import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main(live=False):
    env = dict(os.environ)
    with tempfile.TemporaryDirectory(prefix='altium-suite-smoke-') as scratch:
        if not live:
            # Protocol/schema checks must not discover or launch a real editor.
            env['ALTIUM_EXE'] = str(Path(scratch) / 'not-installed' / 'X2.EXE')
            env['ALTIUM_MCP_RUNTIME'] = str(Path(scratch) / 'runtime')
            env['EDA_AGENT_WORKSPACE'] = str(Path(scratch) / 'workspace')
            env['EDA_AGENT_POINTER_FILE'] = str(Path(scratch) / 'pointer.txt')
            env['EDA_AGENT_TOOLSET'] = 'full'
            env['EDA_AGENT_BACKEND'] = 'altium'
            env.pop('PYTHONPATH', None)
        for name, script in [('eda', ROOT / 'eda_stdio.py'), ('legacy', ROOT / 'coffeenmusic/server/codex_stdio.py')]:
            args = [str(script)] + (['--no-dashboard'] if name == 'eda' else [])
            parameters = StdioServerParameters(command=sys.executable, args=args, env=env)
            with (Path(scratch) / (name + '.log')).open('w', encoding='utf-8') as log:
                try:
                    async with stdio_client(parameters, errlog=log) as streams:
                        async with ClientSession(*streams, read_timeout_seconds=datetime.timedelta(seconds=160)) as client:
                            await client.initialize()
                            listing = await client.list_tools()
                            assert listing.tools
                            print(json.dumps({'server':name, 'tools':len(listing.tools), 'initialized':True}), flush=True)
                            if name == 'eda':
                                # Pure verification calls: no document or bridge access.
                                async def verify_call(tool, arguments):
                                    response = await client.call_tool(tool, arguments)
                                    assert not response.isError, response
                                    return response.structuredContent or json.loads(
                                        next(block.text for block in response.content if block.type == 'text'))
                                sample = {'pins': [
                                    {'component':'J1', 'pin':'1', 'net':'/Cam0/RESET'},
                                    {'component':'J2', 'pin':'1', 'net':'/Cam1/RESET'}], 'count':2}
                                captured = await verify_call('design_connectivity_snapshot',
                                                             {'data':sample, 'complete':True})
                                assert captured['count'] == 2
                                eco = await verify_call('proj_sync_pcb', {})
                                assert eco['command_sent'] is False and eco['ok'] is False
                                mismatch = await verify_call('design_check_pin_contracts', {
                                    'actual':captured, 'contracts':[
                                        {'component':'J2', 'pin':'1', 'net':'/Cam0/RESET'}]})
                                assert mismatch['status'] == 'failed'
                                assert mismatch['finding_count'] == 1
                                same = await verify_call('design_diff_connectivity',
                                                         {'before':captured, 'after':captured})
                                assert same['complete'] and same['change_count'] == 0
                                parity = await verify_call('design_check_schematic_pcb_parity', {
                                    'schematic':captured, 'board':{'complete':True, 'components':[
                                        {'designator':pin['component'], 'pads':[
                                            {'name':pin['pin'], 'net':pin['net']}]} for pin in sample['pins']]}})
                                assert parity['status'] == 'passed'
                                invalid = await verify_call('design_connectivity_snapshot', {'data':{}})
                                assert invalid['status'] == 'invalid_input'
                                print(json.dumps({'server':name, 'connectivity_tools_verified':4}), flush=True)
                            if name == 'legacy':
                                reply = await client.call_tool('get_server_status', {})
                                assert not reply.isError
                            if live:
                                import bridge_coordination as coordination
                                if name == 'eda':
                                    # Establish the current editor session before
                                    # testing a stop, including after an editor restart.
                                    established = await client.call_tool('app_context', {})
                                    assert not established.isError and established.structuredContent['bridge_answering'], established
                                    with coordination.engine_lock():
                                        coordination.stop_eda()
                                    reply = await client.call_tool('app_context', {})
                                    result = reply.structuredContent
                                    assert not reply.isError and result['bridge_answering'] and result['script_version_match'], reply
                                else:
                                    reply = await client.call_tool('get_all_designators', {})
                                    assert not reply.isError
                                    value = reply.structuredContent
                                    if value is None:
                                        value = json.loads(next(block.text for block in reply.content if block.type == 'text'))
                                    if isinstance(value, dict) and 'error' in value:
                                        assert value['error'] == 'No component data found', value
                                    with coordination.engine_lock():
                                        from eda_agent.tools.application import _bundled_script_version
                                        assert coordination._file_ping(3)['script_version'] == _bundled_script_version()
                                        activity = coordination.WORKSPACE / 'activity.log'
                                        offset = activity.stat().st_size if activity.exists() else 0
                                        deadline = time.monotonic() + 5
                                        released = False
                                        while time.monotonic() < deadline:
                                            try:
                                                coordination._file_ping(.4)
                                            except TimeoutError:
                                                released = True
                                                break
                                            time.sleep(.15)
                                        assert released, 'Health pings held the script engine beyond its idle deadline'
                                        assert 'reason=engine_idle_release' in activity.read_bytes()[offset:].decode('utf-8', errors='replace')
                                        coordination.start_eda()
                                        assert coordination._file_ping(3)['script_version'] == _bundled_script_version()
                                        coordination.stop_eda()
                                    print(json.dumps({'server':name, 'idle_release_despite_pings':True,
                                                      'recovered_after_idle':True, 'engine_released_at_exit':True}), flush=True)
                                print(json.dumps({'server':name, 'live_recovery_or_handover':True}), flush=True)
                except BaseException:
                    log.flush()
                    print((Path(scratch) / (name + '.log')).read_text(encoding='utf-8')[-5000:], file=sys.stderr)
                    raise
        binary = ROOT / 'altium-designer-mcp/target/release/altium-designer-mcp.exe'
        if binary.exists():
            config = Path(scratch) / 'libraries.json'
            config.write_text(json.dumps({'allowed_paths': [scratch]}), encoding='utf-8')
            parameters = StdioServerParameters(command=str(binary), args=[str(config)], env=env)
            async with stdio_client(parameters) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    listing = await client.list_tools()
                    assert listing.tools
                    print(json.dumps({'server':'libraries', 'tools':len(listing.tools), 'initialized':True}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--root', type=Path, default=ROOT)
    options = parser.parse_args()
    ROOT = options.root.resolve()
    sys.path.insert(0, str(ROOT))
    asyncio.run(main(options.live))
