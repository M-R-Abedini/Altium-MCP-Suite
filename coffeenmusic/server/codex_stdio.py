import asyncio
import contextlib
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from suite_config import configure_environment
configure_environment()
from bridge_coordination import async_engine_lock, stop_eda, start_eda, log_event
with contextlib.redirect_stdout(sys.stderr):
    from main import mcp
original = mcp._tool_manager.call_tool
OFFLINE = {'get_server_status', 'ensure_altium_script_skill'}

async def coordinated(*args, **kwargs):
    name = kwargs.get('name') or (args[0] if args else '')
    if name in OFFLINE:
        return await original(*args, **kwargs)
    async with async_engine_lock():
        await asyncio.to_thread(stop_eda)
        try:
            return await original(*args, **kwargs)
        finally:
            # Return the engine after the one-shot script, even if its tool
            # failed. Never retry the operation itself.
            try:
                await asyncio.to_thread(start_eda)
            except Exception as error:
                log_event('handover_restore_failed', error=str(error))
                print('EDA bridge restoration failed: ' + str(error), file=sys.stderr)

mcp._tool_manager.call_tool = coordinated
if __name__ == '__main__':
    mcp.run(transport='stdio')
