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
    return state

def _handler_busy():
    now = time.time()
    for p in WORKSPACE.glob('progress_*.json'):
        try:
            if now - p.stat().st_mtime < 600:
                return True
        except OSError:
            pass
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
                if result.get('id') == request_id and result.get('success'):
                    return result.get('data')
                raise RuntimeError('EDA ping returned a protocol or command error')
            time.sleep(.02)
        raise TimeoutError('EDA ping did not answer')
    finally:
        for p in (temp, request, response, progress):
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
