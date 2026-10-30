import threading
import sys
import os
import argparse
from pathlib import Path


def configure_cli(argv):
    # Set import-time selection before registering the server. Otherwise its
    # CLI re-exec switches to eda_agent.server and drops this coordination
    # wrapper (including shared locks, restart and idle-release handling).
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument('--toolset')
    parser.add_argument('--backend')
    options, _ = parser.parse_known_args(argv)
    for name, value in [('EDA_AGENT_TOOLSET', options.toolset), ('EDA_AGENT_BACKEND', options.backend)]:
        if value is not None:
            os.environ[name] = value


configure_cli(sys.argv[1:])
# Prefer deployed sources to an unrelated editable package in the interpreter.
sys.path.insert(0, str(Path(__file__).resolve().parent / 'eda-agent' / 'src'))
from suite_config import configure_environment
configure_environment()
from bridge_coordination import (engine_lock, async_engine_lock, run_engine_worker,
                                 background_engine_lock, ensure_eda, stop_eda, close_project_once)
from eda_agent.bridge.altium_bridge import AltiumBridge

original = AltiumBridge._execute_command

def coordinated(self, command, params, timeout):
    background = threading.current_thread() is getattr(self, '_keepalive_thread', None)
    if background:
        with background_engine_lock() as acquired:
            if not acquired:
                return {'engine_busy': True}
            if not (self.config.workspace_dir / 'bridge-ready.json').exists():
                return {'engine_released': True}
            # A stop can race the ready check. Bound this health-only wait so
            # it cannot hold the shared lock for five seconds ahead of a user.
            return original(self, command, params, min(timeout, 1.0))
    with engine_lock():
        return dispatch_locked(self, command, params, timeout)


def dispatch_locked(self, command, params, timeout):
    if command == 'application.stop_server':
        # A stop response is written before DelphiScript returns. Wait
        # for session-bound final shutdown evidence, without relaunching.
        stop_eda()
        return {'success': True, 'engine_release_confirmed': True}
    if command == 'suite.execute_script':
        from suite_script_overlay import execute_overlay
        return execute_overlay(self, original, params, timeout)
    health = ensure_eda(self, original)
    if command == 'suite.capture_snapshot':
        import time
        from bridge_coordination import _guard_editor, _session_started_at
        from suite_reads import capture
        board_path = params.get('board_path', '')
        if not Path(board_path).is_absolute() or not board_path.lower().endswith('.pcbdoc'):
            raise ValueError('An absolute loaded PcbDoc path is required')
        state = _guard_editor()
        editor = dict(pid=state['pid'], started=_session_started_at(state['pid']))
        started = time.time()
        # One native iterator pass; no other client's command can run
        # between sections and no focus-dependent second read is needed.
        data = original(self, 'pcb.get_pad_connectivity',
                        dict(board_path=board_path, include_components=True, limit=0), timeout)
        return capture(data, started, editor, health)
    if command == 'project.close':
        target = dict(params)
        if not target.get('project_path'):
            target['project_path'] = original(self, 'project.get_focused', {}, timeout)['project_path']
        if not target['project_path'].lower().endswith('.prjscr'):
            return close_project_once(target, timeout)
    if command == 'application.ping' and not params:
        # The foreground preflight already answered this exact query.
        return health
    return original(self, command, params, timeout)


async def coordinated_async(self, command, params=None, timeout=None):
    from eda_agent.bridge.exceptions import AltiumNotRunningError
    if not self.is_altium_running():
        raise AltiumNotRunningError()
    self._ensure_keepalive()
    if timeout is None:
        timeout = self.config.poll_timeout
    # Waiting is cancellable. After dispatch, cancellation drains the native
    # worker before releasing either admission or the Windows engine mutex.
    async with async_engine_lock():
        return await run_engine_worker(lambda: dispatch_locked(self, command, params or {}, timeout))

AltiumBridge._execute_command = coordinated
AltiumBridge.send_command_async = coordinated_async
AltiumBridge.suite_shared_reads = True
from eda_agent.server import main, mcp

@mcp.tool()
async def _suite_legacy_execute(command: str, params: dict) -> dict:
    """Internal compatibility adapter; only the suite legacy namespace exposes it."""
    import json
    from coffeenmusic.server.batch_safety import validate_legacy_json_text
    from eda_agent.bridge import get_bridge
    validate_legacy_json_text(params)
    bridge = get_bridge()
    if command == 'run_altium_script':
        return await bridge.send_command_async('suite.execute_script', params,
            timeout=float(params.get('timeout_seconds', 120)))
    data = await bridge.send_command_async('legacy.' + command,
        {'arguments': params, 'pretty_params': json.dumps(params, indent=2)})
    # Business results already have their historical shape; only the common
    # EDA transport envelope is translated here.
    return {'success': True, 'result': data}

# Keep private compatibility commands callable only by the gateway's direct
# adapter while preserving the 457-tool public EDA discovery contract.
_public_list_tools = mcp.list_tools
async def _suite_public_tools():
    return [tool for tool in await _public_list_tools() if not tool.name.startswith('_suite_')]
mcp.list_tools = _suite_public_tools
mcp._mcp_server.list_tools()(_suite_public_tools)

if __name__ == '__main__':
    main()
