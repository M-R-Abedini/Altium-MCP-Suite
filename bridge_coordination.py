"""Serialize Altium script engines and recover only before dispatching a command."""
import asyncio
import contextlib
import contextvars
import json
import math
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


async def run_engine_worker(function):
    """Drain non-cancellable native coordination before releasing its lock.

    AnyIO cancellation scopes and direct asyncio Task.cancel are both used
    by MCP clients. Neither can stop an already running Python thread.
    """
    import anyio
    cancelled = None
    with anyio.CancelScope(shield=True):
        worker = asyncio.get_running_loop().run_in_executor(
            None, contextvars.copy_context().run, function)
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError as error:
                cancelled = error
            except Exception:
                break  # Retrieve the worker's exception below.
        if cancelled is not None:
            try:
                worker.result()
            except Exception as error:
                log_event('cancelled_engine_worker_failed', error=str(error))
            raise cancelled
        result = worker.result()
    # Deliver scoped cancellation before the caller can dispatch its tool.
    await anyio.lowlevel.checkpoint_if_cancelled()
    return result


def _launch_attempted_at():
    try:
        previous = json.loads(LAUNCH_STATE.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return 0
    except (OSError, ValueError) as error:
        raise RuntimeError('Bridge launch state is unreadable; no script was launched. '
                           'Confirm the script is stopped, then use --confirm-script-stopped.') from error
    attempted = previous.get('attempted_at') if isinstance(previous, dict) else None
    try:
        valid = type(attempted) in (int, float) and math.isfinite(attempted) and attempted > 0
    except OverflowError:
        valid = False
    if not valid:
        raise RuntimeError('Bridge launch state is invalid; no script was launched. '
                           'Confirm the script is stopped, then use --confirm-script-stopped.')
    return attempted

# A missing ready file alone does not prove that the native engine stopped.
# Only a clean native shutdown of the session we actually pinged permits a
# fast restart. Older scripts and damaged markers keep the two-probe path.
_CLEAN_STOPS = {'engine_idle_release', 'idle_timeout',
                'coordinator_stop_file', 'command_or_external_stop'}


def _read_marker(name):
    try:
        value = json.loads((WORKSPACE / name).read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember_session(state):
    """Record native identity only after a matching health response."""
    ready = _read_marker('bridge-ready.json')
    if not state or not isinstance(ready.get('session_id'), str) or not ready['session_id']:
        return
    if not isinstance(ready.get('script_version'), str) or not ready['script_version']:
        return
    temporary = None
    try:
        owner = dict(pid=state['pid'], session_started_at=_session_started_at(state['pid']),
                     session_id=ready['session_id'], script_version=ready['script_version'])
        if _read_marker('bridge-owner.json') == owner:
            return
        target = WORKSPACE / 'bridge-owner.json'
        temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(owner), encoding='utf-8')
        temporary.replace(target)
    except Exception:
        # Failure to record identity disables the shortcut; never loses a reply.
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _cleanly_released(state):
    if not state or (WORKSPACE / 'bridge-ready.json').exists():
        return False
    owner = _read_marker('bridge-owner.json')
    stopped = _read_marker('bridge-stopped.json')
    if not isinstance(stopped.get('reason'), str) or stopped['reason'] not in _CLEAN_STOPS:
        return False
    for key in ('session_id', 'script_version'):
        if not isinstance(owner.get(key), str) or not owner[key] or stopped.get(key) != owner[key]:
            return False
    try:
        return (owner.get('pid') == state['pid']
                and owner.get('session_started_at') == _session_started_at(state['pid']))
    except Exception:
        return False

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
    # Importing configuration and offline checks does not require Windows.
    # Native engine ownership still uses the Windows cross-process lock.
    try:
        import msvcrt
    except ModuleNotFoundError as error:
        raise RuntimeError('Altium engine coordination requires Windows (msvcrt).') from error
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
    import msvcrt
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


@contextlib.contextmanager
def background_engine_lock():
    """Health traffic yields immediately to foreground work on either bridge."""
    f = try_lock()
    try:
        yield f is not None
    finally:
        if f is not None:
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

def _file_ping(timeout, *, command_pending=False):
    request_id = uuid.uuid4().hex
    request = WORKSPACE / ('request_' + request_id + '.json')
    response = WORKSPACE / ('response_' + request_id + '.json')
    progress = WORKSPACE / ('progress_' + request_id + '.json')
    temp = request.with_suffix('.json.tmp')
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    params = {'command_pending': True} if command_pending else {}
    temp.write_text(json.dumps({'protocol_version': 2, 'id': request_id, 'command': 'application.ping', 'params': params}), encoding='utf-8')
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
    state = _guard_editor()
    if _handler_busy():
        # A live handler is not a dead loop. Do not interrupt or replay it.
        log_event('busy_handler_no_restart')
        raise RuntimeError('An EDA handler is still running. No new command was queued; inspect the previous operation before retrying.')
    released = _cleanly_released(state)
    if not released:
        try:
            result = probe(2.0)
            _remember_session(state)
            LAUNCH_STATE.unlink(missing_ok=True)
            return result
        except TimeoutError:
            pass
        # A second health check avoids launching over a temporarily slow editor.
        state = _guard_editor()
        if _handler_busy():
            raise RuntimeError('An EDA handler started during the health check. No new command was queued.')
        try:
            result = probe(3.0)
            _remember_session(state)
            LAUNCH_STATE.unlink(missing_ok=True)
            return result
        except TimeoutError:
            pass
    _guard_editor()
    if _handler_busy():
        raise RuntimeError('An EDA handler started during the health check. No new command was queued.')
    if time.time() - _launch_attempted_at() < 45:
        raise TimeoutError('A bridge startup was already attempted recently. No duplicate Altium process was launched.')
    script = SCRIPT or prepare_runtime()['eda']
    executable = EXE or altium_exe()
    if not script.is_file() or not executable.is_file():
        raise RuntimeError('The Altium executable or bridge script is missing')
    ready = WORKSPACE / 'bridge-ready.json'
    ready.unlink(missing_ok=True)
    LAUNCH_STATE.parent.mkdir(parents=True, exist_ok=True)
    launch_temp = LAUNCH_STATE.with_name(LAUNCH_STATE.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        launch_temp.write_text(json.dumps({'attempted_at': time.time()}), encoding='utf-8')
        launch_temp.replace(LAUNCH_STATE)
    finally:
        launch_temp.unlink(missing_ok=True)
    # Consume the shutdown evidence before launch, even if the launcher fails.
    (WORKSPACE / 'bridge-stopped.json').unlink(missing_ok=True)
    (WORKSPACE / 'bridge-owner.json').unlink(missing_ok=True)
    command = '"%s" -RScriptingSystem:RunScript(ProjectName="%s"|ProcName="Dispatcher>StartMCPServer")' % (executable, script)
    process = subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW)
    log_event('bridge_launch', launcher_pid=process.pid, script=str(script), clean_release=released)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        time.sleep(.3)
        state = _guard_editor()
        # Startup purges orphan requests. Do not publish a health request
        # until that purge and the status-form initialization have finished.
        if not ready.exists():
            continue
        try:
            result = probe(2.0)
            _remember_session(state)
            LAUNCH_STATE.unlink(missing_ok=True)
            log_event('bridge_ready')
            return result
        except TimeoutError:
            pass
    raise TimeoutError('Altium did not start the EDA bridge. Inspect the script error or modal; no command was replayed.')

def ensure_eda(bridge, execute):
    # AltiumTimeoutError is a bridge-specific exception, not TimeoutError.
    from eda_agent.bridge.exceptions import AltiumTimeoutError
    def probe(timeout):
        try:
            return execute(bridge, 'application.ping', {'command_pending': True}, timeout)
        except AltiumTimeoutError as error:
            raise TimeoutError(str(error)) from error
    return _ensure(probe)

def start_eda():
    return _ensure(_file_ping)

def stop_eda():
    state = _guard_editor()
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    if _handler_busy():
        raise RuntimeError('An EDA handler is still running; refusing to stop it for the other bridge')
    if _cleanly_released(state):
        return
    # Only publish a stop if the polling loop actually answers. An unconsumed
    # sentinel must not kill a later session that happens to start meanwhile.
    try:
        _file_ping(2.0, command_pending=True)
    except TimeoutError:
        # A slow/unresponsive engine is not an available engine. Idle release
        # may race the ping, but only its session-bound final proof is enough.
        state = _guard_editor()
        if not _handler_busy() and _cleanly_released(state):
            return
        raise TimeoutError('EDA shutdown is not confirmed; refusing legacy handover. '
                           'Establish a session with the coordinated EDA bridge before retrying.') from None
    _remember_session(state)
    stop = WORKSPACE / 'stop'
    log_event('handover_stop')
    stop.write_text('1', encoding='utf-8')
    deadline = time.monotonic() + 5
    try:
        while time.monotonic() < deadline:
            state = _guard_editor()
            if not _handler_busy() and _cleanly_released(state):
                return
            # Native code consumes the stop file BEFORE hiding its form and
            # cleaning up. Wait for the final acknowledgement, not consumption.
            time.sleep(.05)
        raise TimeoutError('EDA shutdown is not confirmed; refusing a second bridge launch')
    finally:
        # An unconsumed sentinel must not terminate a later session.
        stop.unlink(missing_ok=True)

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
            # Normal dispatch fails closed on damaged ownership. Only this
            # explicit assertion that the native script stopped can retire it.
            try:
                pending = json.loads(marker.read_text(encoding='utf-8-sig'))
                request = Path(pending['request_path']).resolve()
                if not (request.is_relative_to(RUNTIME.resolve()) or
                        request.is_relative_to(WORKSPACE.resolve())):
                    raise ValueError('Request path is outside the configured runtime/workspace')
                if request.exists() and not request.is_file():
                    raise ValueError('Request path is not a file')
                if request.name not in {'request.json', 'unused-request.json'}:
                    raise ValueError('Request path is not a legacy inbox')
            except (ValueError, KeyError, TypeError) as error:
                archive = marker.with_name('legacy-operation.corrupt-' + uuid.uuid4().hex + '.json')
                marker.replace(archive)
                log_event('manual_ownership_archive', archive=str(archive), error=str(error))
            else:
                request.unlink(missing_ok=True)
                marker.unlink(missing_ok=True)
        # The fixed legacy inbox may survive a damaged or missing marker.
        (RUNTIME / 'legacy-exchange' / 'request.json').unlink(missing_ok=True)
        LAUNCH_STATE.unlink(missing_ok=True)
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
