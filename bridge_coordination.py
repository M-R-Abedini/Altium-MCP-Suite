"""Serialize Altium script engines and recover only before dispatching a command."""
import asyncio
import contextlib
import json
import msvcrt
import subprocess
import time
import uuid
from pathlib import Path
from suite_config import runtime_dir, workspace_dir, altium_exe, prepare_runtime

ROOT = Path(__file__).resolve().parent
RUNTIME = runtime_dir()
WORKSPACE = workspace_dir()
LOCK = RUNTIME / 'altium-engine.lock'
LAUNCH_STATE = RUNTIME / 'bridge-launch.json'
SCRIPT = None
EXE = None

def _session_started_at(pid):
    import psutil
    return psutil.Process(pid).create_time()

def _legacy_marker():
    return RUNTIME / 'legacy-operation.json'

def _guard_legacy(state):
    marker = _legacy_marker()
    if not marker.exists():
        return
    try:
        pending = json.loads(marker.read_text(encoding='utf-8'))
        import psutil
        try:
            old_session_alive = pending['session_started_at'] == _session_started_at(pending['pid'])
        except psutil.NoSuchProcess:
            old_session_alive = False
        if not old_session_alive:
            # Only an observed editor restart proves the old script cannot run.
            Path(pending['request_path']).unlink(missing_ok=True)
            marker.unlink(missing_ok=True)
            return
        if pending['pid'] != state['pid']:
            raise RuntimeError('The unresolved legacy operation belongs to another running Altium editor; no script was launched.')
        completion = Path(pending['completion_path'])
        if completion.exists():
            reply = json.loads(completion.read_text(encoding='utf-8-sig'))
            if isinstance(reply, dict) and reply.get('request_id') == pending['request_id']:
                marker.unlink(missing_ok=True)
                completion.unlink(missing_ok=True)
                return
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError('Legacy operation ownership cannot be verified; no new script was launched.') from error
    raise RuntimeError('A legacy operation has not confirmed completion. No new script was launched. Wait for completion or stop the script manually and reset ownership.')

def begin_legacy(request_id, request_path, completion_path):
    state = _guard_editor()
    marker = _legacy_marker()
    marker.parent.mkdir(parents=True, exist_ok=True)
    temp = marker.with_suffix('.json.tmp')
    temp.write_text(json.dumps(dict(request_id=request_id, pid=state['pid'],
        session_started_at=_session_started_at(state['pid']),
        request_path=str(request_path), completion_path=str(completion_path))), encoding='utf-8')
    temp.replace(marker)

def abandon_unlaunched_legacy(request_id):
    marker = _legacy_marker()
    pending = json.loads(marker.read_text(encoding='utf-8'))
    if pending['request_id'] == request_id:
        marker.unlink(missing_ok=True)

def log_event(event, **details):
    try:
        RUNTIME.mkdir(parents=True, exist_ok=True)
        with (RUNTIME / 'bridge-coordination.log').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'time': time.time(), 'event': event, **details}) + '\n')
    except OSError:
        # Diagnostics must not replace a tool's result or its original error.
        pass

def try_lock():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    f = LOCK.open('a+b')
    if f.tell() == 0:
        f.write(b'0')
        f.flush()
    f.seek(0)
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        f.close()
        return None
    return f

def unlock(f):
    f.seek(0)
    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    f.close()

@contextlib.contextmanager
def engine_lock():
    deadline = time.monotonic() + 180
    while (f := try_lock()) is None:
        if time.monotonic() > deadline:
            raise TimeoutError('Another Altium MCP command is still running')
        time.sleep(.1)
    try:
        yield
    finally:
        unlock(f)

@contextlib.asynccontextmanager
async def async_engine_lock():
    deadline = time.monotonic() + 180
    while (f := try_lock()) is None:
        if time.monotonic() > deadline:
            raise TimeoutError('Another Altium MCP command is still running')
        await asyncio.sleep(.1)
    try:
        yield
    finally:
        unlock(f)

def _editor_state():
    # Read window metadata only. Never send input or dismiss a dialog.
    import win32gui
    import win32process
    editors = []
    def visit(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == 'TDocumentForm':
            editors.append((hwnd, win32process.GetWindowThreadProcessId(hwnd)[1]))
        return True
    win32gui.EnumWindows(visit, None)
    fg = win32gui.GetForegroundWindow()
    pid = win32process.GetWindowThreadProcessId(fg)[1] if fg else None
    active = [e for e in editors if e[1] == pid]
    if len(active) == 1:
        editors = active
    if len(editors) != 1:
        raise RuntimeError('Expected one Altium editor; found %d. No script was launched.' % len(editors))
    hwnd, pid = editors[0]
    return {'pid': pid, 'blocked': not win32gui.IsWindowEnabled(hwnd)}

def _guard_editor():
    state = _editor_state()
    if state['blocked']:
        log_event('modal_blocked', pid=state['pid'])
        raise RuntimeError('Altium is blocked by a modal dialog. Close or complete it, then retry; no second script was launched.')
    _guard_legacy(state)
    return state

def _handler_busy():
    markers = list(WORKSPACE.glob('progress_*.json'))
    if not markers:
        return False
    started = _session_started_at(_editor_state()['pid'])
    for p in markers:
        try:
            if p.stat().st_mtime >= started:
                return True
            # An old editor session cannot still own the current engine.
            p.unlink(missing_ok=True)
        except OSError:
            return True  # Uncertain ownership must not authorize a relaunch.
    return False

def _file_ping(timeout):
    request_id = uuid.uuid4().hex
    request = WORKSPACE / ('request_' + request_id + '.json')
    response = WORKSPACE / ('response_' + request_id + '.json')
    progress = WORKSPACE / ('progress_' + request_id + '.json')
    temp = request.with_suffix('.json.tmp')
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    temp.write_text(json.dumps({'protocol_version': 2, 'id': request_id, 'command': 'application.ping', 'params': {}}), encoding='utf-8')
    temp.replace(request)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if response.exists():
                try:
                    result = json.loads(response.read_text(encoding='utf-8-sig'))
                except (OSError, ValueError):
                    time.sleep(.02)
                    continue
                if isinstance(result, dict) and result.get('id') == request_id and result.get('success') is True:
                    return result.get('data')
                raise RuntimeError('EDA ping returned a protocol or command error')
            time.sleep(.02)
        raise TimeoutError('EDA ping did not answer')
    finally:
        # Do not erase evidence of a ping already consumed by Altium.
        for p in (temp, request, response):
            p.unlink(missing_ok=True)

def _ensure(probe):
    _guard_editor()
    if _handler_busy():
        # A live handler is not a dead loop. Do not interrupt or replay it.
        log_event('busy_handler_no_restart')
        raise RuntimeError('An EDA handler is still running. No new command was queued; inspect the previous operation before retrying.')
    try:
        probe(2.0)
        LAUNCH_STATE.unlink(missing_ok=True)
        return
    except TimeoutError:
        pass
    # A second health check avoids launching over a temporarily slow editor.
    _guard_editor()
    if _handler_busy():
        raise RuntimeError('An EDA handler started during the health check. No new command was queued.')
    try:
        probe(3.0)
        LAUNCH_STATE.unlink(missing_ok=True)
        return
    except TimeoutError:
        pass
    _guard_editor()
    try:
        previous = json.loads(LAUNCH_STATE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = {}
    if time.time() - previous.get('attempted_at', 0) < 45:
        raise TimeoutError('A bridge startup was already attempted recently. No duplicate Altium process was launched.')
    script = SCRIPT or prepare_runtime()['eda']
    executable = EXE or altium_exe()
    if not script.is_file() or not executable.is_file():
        raise RuntimeError('The Altium executable or bridge script is missing')
    LAUNCH_STATE.parent.mkdir(parents=True, exist_ok=True)
    LAUNCH_STATE.write_text(json.dumps({'attempted_at': time.time()}), encoding='utf-8')
    command = '"%s" -RScriptingSystem:RunScript(ProjectName="%s"|ProcName="Dispatcher>StartMCPServer")' % (executable, script)
    process = subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW)
    log_event('bridge_launch', launcher_pid=process.pid, script=str(script))
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        time.sleep(.3)
        _guard_editor()
        try:
            probe(2.0)
            LAUNCH_STATE.unlink(missing_ok=True)
            log_event('bridge_ready')
            return
        except TimeoutError:
            pass
    raise TimeoutError('Altium did not start the EDA bridge. Inspect the script error or modal; no command was replayed.')

def ensure_eda(bridge, execute):
    # AltiumTimeoutError is a bridge-specific exception, not TimeoutError.
    from eda_agent.bridge.exceptions import AltiumTimeoutError
    def probe(timeout):
        try:
            return execute(bridge, 'application.ping', {}, timeout)
        except AltiumTimeoutError as error:
            raise TimeoutError(str(error)) from error
    _ensure(probe)

def start_eda():
    _ensure(_file_ping)

def stop_eda():
    _guard_editor()
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    if _handler_busy():
        raise RuntimeError('An EDA handler is still running; refusing to stop it for the other bridge')
    # Only publish a stop if the polling loop actually answers. An unconsumed
    # sentinel must not kill a later session that happens to start meanwhile.
    try:
        _file_ping(2.0)
    except TimeoutError:
        return
    stop = WORKSPACE / 'stop'
    log_event('handover_stop')
    stop.write_text('1', encoding='utf-8')
    deadline = time.monotonic() + 5
    while stop.exists() and time.monotonic() < deadline:
        time.sleep(.05)
    if stop.exists():
        stop.unlink(missing_ok=True)
        raise TimeoutError('EDA did not acknowledge the stop; refusing a second bridge launch')
    time.sleep(.5)

def reset_after_manual_stop():
    """Explicit operator assertion: the script was stopped in Altium's IDE."""
    with engine_lock():
        state = _editor_state()
        if state['blocked']:
            raise RuntimeError('Close the Altium dialog and stop its script before resetting ownership.')
        for pattern in ('request_*.json', 'progress_*.json'):
            for path in WORKSPACE.glob(pattern):
                path.unlink(missing_ok=True)
        marker = _legacy_marker()
        if marker.exists():
            pending = json.loads(marker.read_text(encoding='utf-8'))
            Path(pending['request_path']).unlink(missing_ok=True)
            marker.unlink(missing_ok=True)
        log_event('manual_ownership_reset', pid=state['pid'])

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Reset only after stopping the Altium script manually; never replays edits.')
    parser.add_argument('--confirm-script-stopped', action='store_true', required=True)
    parser.add_argument('--workspace', type=Path, default=WORKSPACE,
                        help='Use the EDA_AGENT_WORKSPACE configured for your MCP server.')
    args = parser.parse_args()
    WORKSPACE = args.workspace.resolve()
    reset_after_manual_stop()
