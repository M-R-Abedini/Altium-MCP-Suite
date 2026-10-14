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
import engine_identity
import engine_queue

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


def _owner_process_ended(owner):
    """A different foreground PID alone does not prove the old editor exited."""
    import psutil
    pid, started = owner.get('pid'), owner.get('session_started_at')
    try:
        if type(pid) is not int or pid <= 0 or type(started) not in (int, float) or not math.isfinite(started) or started <= 0:
            return False
        return _session_started_at(pid) != started
    except psutil.NoSuchProcess:
        return True
    except Exception:
        # Inspection failure is uncertainty, never permission to relaunch.
        return False


def _retire_restarted_session():
    owner = _read_marker('bridge-owner.json')
    if not _owner_process_ended(owner):
        return
    # Never erase a ready file from an unbound/newer native session.
    for name in ('bridge-ready.json', 'bridge-stopped.json'):
        if (WORKSPACE / name).exists():
            marker = _read_marker(name)
            if any(not owner.get(k) or marker.get(k) != owner[k] for k in ('session_id', 'script_version')):
                return
    for name in ('bridge-ready.json', 'bridge-stopped.json', 'bridge-owner.json'):
        (WORKSPACE / name).unlink(missing_ok=True)
    log_event('retired_exited_editor_session', pid=owner['pid'])

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
                _confirm_editor_return(state)
                marker.unlink(missing_ok=True)
                completion.unlink(missing_ok=True)
                return
    except TimeoutError:
        raise
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
    gate = engine_identity.try_mutex()
    if gate is None:
        return None
    f, locked = None, False
    try:
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        f = LOCK.open('a+b')
        if f.tell() == 0:
            f.write(b'0'); f.flush()
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return None
        locked = True
        return f, gate
    finally:
        if not locked:
            try:
                if f is not None:
                    f.close()
            finally:
                gate.release()

def unlock(lease):
    import msvcrt
    f, gate = lease
    try:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        try:
            f.close()
        finally:
            gate.release()

@contextlib.contextmanager
def engine_lock():
    deadline = time.monotonic() + 180
    admission = engine_queue.acquire()
    f = None
    try:
        while (f := try_lock()) is None:
            if time.monotonic() > deadline:
                raise TimeoutError('Another Altium MCP command is still running')
            time.sleep(.1)
        yield
    finally:
        try:
            if f is not None:
                unlock(f)
        finally:
            admission.release()


@contextlib.contextmanager
def background_engine_lock():
    """Health traffic yields immediately to foreground work on either bridge."""
    try:
        admission = engine_queue.acquire(timeout=.2, background=True)
    except (OSError, ValueError, RuntimeError):
        admission = None
    f = None
    try:
        if admission is not None:
            f = try_lock()
        yield f is not None
    finally:
        try:
            if f is not None:
                unlock(f)
        finally:
            if admission is not None:
                admission.release()

@contextlib.asynccontextmanager
async def async_engine_lock():
    deadline = time.monotonic() + 180
    admission = await engine_queue.acquire_async()
    f = None
    try:
        while (f := try_lock()) is None:
            if time.monotonic() > deadline:
                raise TimeoutError('Another Altium MCP command is still running')
            await asyncio.sleep(.1)
        yield
    finally:
        try:
            if f is not None:
                unlock(f)
        finally:
            admission.release()

def _editor_state():
    # Read window metadata only. Never send input or dismiss a dialog.
    import win32gui
    import win32process
    editors = []
    def visit(hwnd, en=None):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == 'TDocumentForm':
            pid = win32process.GetWindowThreadProcessId(hwnd)[1]
            if en is None:
                en = bool(win32gui.IsWindowEnabled(hwnd))
            editors.append((hwnd, pid, en))
        return True
    try:
        import win32service, ctypes
        from ctypes import wintypes
        desk = win32service.OpenDesktop('Default', 0, False, 0x41)  # READOBJECTS | ENUMERATE
        try:
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            user32.EnumDesktopWindows.argtypes = [wintypes.HANDLE, callback_type, wintypes.LPARAM]
            user32.EnumDesktopWindows.restype = wintypes.BOOL
            user32.IsWindowEnabled.argtypes = [wintypes.HWND]
            user32.IsWindowEnabled.restype = wintypes.BOOL
            failures = []
            @callback_type
            def enum_cb(h, _):
                try:
                    visit(h, bool(user32.IsWindowEnabled(h)))
                except Exception as error:
                    failures.append(error)
                    return False
                return True
            ctypes.set_last_error(0)
            if not user32.EnumDesktopWindows(int(desk), enum_cb, 0) or failures:
                raise RuntimeError('Editor window enumeration failed')
        finally:
            desk.CloseDesktop()
    except Exception:
        editors.clear()
        win32gui.EnumWindows(visit, None)
    if not editors:
        try:
            win32gui.EnumWindows(visit, None)
        except Exception:
            pass
    # Health replies and CLI RunScript dispatch do not identify their editor.
    # Foreground preference cannot safely associate them with one of two VMs.
    if len(editors) != 1:
        raise RuntimeError('Expected one Altium editor; found %d. No script was launched.' % len(editors))
    hwnd, pid, en = editors[0]
    return {'pid': pid, 'hwnd': hwnd, 'blocked': not en}


def _ui_thread_barrier(hwnd, timeout_ms):
    """Read-only WM_NULL round trip; never sends a command or user input."""
    import ctypes
    from ctypes import wintypes
    import threading

    errors, completed = [], threading.Event()
    def worker():
        desk, original, attached = None, None, False
        try:
            import win32service, win32api
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
            user32.GetThreadDesktop.restype = wintypes.HANDLE
            user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
            user32.SetThreadDesktop.restype = wintypes.BOOL
            original = user32.GetThreadDesktop(win32api.GetCurrentThreadId())
            if not original:
                raise ctypes.WinError(ctypes.get_last_error())
            # Only this fresh thread attaches; no desktop switch or UI input.
            desk = win32service.OpenDesktop('Default', 0, False, 0xC1)
            if not user32.SetThreadDesktop(int(desk)):
                raise ctypes.WinError(ctypes.get_last_error())
            attached = True
            send = user32.SendMessageTimeoutW
            send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                             wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                             ctypes.POINTER(ctypes.c_size_t)]
            send.restype = wintypes.LPARAM
            result = ctypes.c_size_t()
            ctypes.set_last_error(0)
            # BLOCK | ABORTIFHUNG | ERRORONEXIT. WM_NULL has no side effects.
            if not send(hwnd, 0, 0, 0, 0x23, timeout_ms, ctypes.byref(result)):
                raise OSError(ctypes.get_last_error(), 'WM_NULL return barrier failed')
        except Exception as error:
            errors.append(error)
        finally:
            try:
                if attached and not user32.SetThreadDesktop(original):
                    raise ctypes.WinError(ctypes.get_last_error())
                if desk is not None:
                    desk.CloseDesktop()
            except Exception as error:
                errors.append(error)
            completed.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout_ms / 1000 + 2)
    if not completed.is_set() or errors:
        detail = str(errors[0]) if errors else 'barrier worker timed out'
        raise TimeoutError('Altium UI return could not be confirmed; engine ownership retained: ' + detail)


def _confirm_editor_return(state):
    """A final marker plus a UI round trip, under the shared engine lock.

    Our native completion tails must not pump messages after publication.
    This is a barrier for those tails, not a general DelphiScript VM-status API.
    """
    before = _editor_state()
    identity = (before['pid'], before['hwnd'], _session_started_at(before['pid']))
    if before['blocked'] or before['pid'] != state['pid']:
        raise RuntimeError('Altium changed or became blocked before engine handover')
    _ui_thread_barrier(before['hwnd'], 3000)
    after = _editor_state()
    if after['blocked'] or (after['pid'], after['hwnd'], _session_started_at(after['pid'])) != identity:
        raise RuntimeError('Altium changed or became blocked during engine handover')

def _bind_editor_runtime(state):
    engine_identity.claim_editor(state['pid'], _session_started_at(state['pid']), RUNTIME, WORKSPACE)


def _reset_editor_runtime(state):
    engine_identity.reset_editor(state['pid'], _session_started_at(state['pid']))


def _guard_editor():
    state = _editor_state()
    if state['blocked']:
        log_event('modal_blocked', pid=state['pid'])
        raise RuntimeError('Altium main window is disabled. Inspect Altium for a hidden dialog or running operation before retrying; no second script was launched.')
    _bind_editor_runtime(state)
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
    _retire_restarted_session()
    if _handler_busy():
        # A live handler is not a dead loop. Do not interrupt or replay it.
        log_event('busy_handler_no_restart')
        raise RuntimeError('An EDA handler is still running. No new command was queued; inspect the previous operation before retrying.')
    released = _cleanly_released(state)
    if released:
        _confirm_editor_return(state)
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
    state = _guard_editor()
    if _handler_busy():
        raise RuntimeError('An EDA handler started during the health check. No new command was queued.')
    # A slow ping is not evidence that the native scripting engine is free.
    # Idle shutdown may have completed DURING either probe: reread its proof.
    released = _cleanly_released(state)
    if released:
        _confirm_editor_return(state)
    else:
        owner = _read_marker('bridge-owner.json')
        old_editor = _owner_process_ended(owner)
        known_current = (owner.get('pid') == state['pid'] and
                         owner.get('session_started_at') == _session_started_at(state['pid']))
        uncertain_markers = (not old_editor and any((WORKSPACE / name).exists() for name in
                             ('bridge-owner.json', 'bridge-stopped.json')))
        attempted = _launch_attempted_at()
        pending_launch = json.loads(LAUNCH_STATE.read_text(encoding='utf-8')) if attempted else {}
        unconfirmed_start = bool(pending_launch) and not _owner_process_ended(pending_launch)
        if known_current or uncertain_markers or unconfirmed_start or (WORKSPACE / 'bridge-ready.json').exists():
            log_event('unconfirmed_shutdown_no_restart', pid=state['pid'])
            raise TimeoutError('EDA did not answer, but native shutdown is not confirmed. No second script was launched; inspect Altium or wait for confirmed idle release.')
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
        launch_temp.write_text(json.dumps({'attempted_at': time.time(), 'pid': state['pid'],
                                          'session_started_at': _session_started_at(state['pid'])}), encoding='utf-8')
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
    result = _ensure(probe)
    # A version string can be unchanged during development. The generated
    # project's content-addressed path identifies every compiled source file.
    import os
    expected = os.environ.get('ALTIUM_MCP_EDA_SCRIPT')
    actual = _read_marker('bridge-ready.json').get('project_path')
    if expected and actual and not isinstance(actual, str):
        raise RuntimeError('Native script project identity is invalid; no design command was queued.')
    if expected and actual and Path(actual).resolve() != Path(expected).resolve():
        stop_eda()
        result = _ensure(probe)
        actual = _read_marker('bridge-ready.json').get('project_path')
        if not isinstance(actual, str) or not actual or Path(actual).resolve() != Path(expected).resolve():
            raise RuntimeError('Compiled script project differs from the configured sources; no design command was queued.')
    return result

def start_eda():
    return _ensure(_file_ping)

def close_project_once(params, timeout):
    """Run one targeted native close with the polling engine released.

    Caller holds engine_lock. Shared legacy ownership blocks every bridge
    if this one-shot operation fails to finish; a close is never replayed.
    """
    from eda_agent.bridge.exceptions import AltiumCommandError, AltiumProtocolError
    from eda_agent.bridge.payload import validate_wire_text
    if not params.get('project_path'):
        raise ValueError('One-shot project close requires a resolved project_path')
    validate_wire_text(params)
    stop_eda()
    request_id = uuid.uuid4().hex
    request = WORKSPACE / 'project-close-request.json'
    response = WORKSPACE / ('close-response_' + request_id + '.json')
    completion = WORKSPACE / ('close-completed_' + request_id + '.json')
    begin_legacy(request_id, request, completion)
    temp = request.with_suffix('.json.tmp')
    launched = False
    try:
        temp.write_text(json.dumps({'id': request_id, 'params': params}), encoding='utf-8')
        temp.replace(request)
        script = SCRIPT or prepare_runtime()['eda']
        executable = EXE or altium_exe()
        command = '"%s" -RScriptingSystem:RunScript(ProjectName="%s"|ProcName="Dispatcher>CloseMCPProject")' % (executable, script)
        subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW)
        launched = True
        deadline = time.monotonic() + min(timeout, 60)
        while time.monotonic() < deadline:
            if completion.exists():
                _guard_editor()
                if _legacy_marker().exists():
                    raise AltiumProtocolError(message='Close completion did not match its native owner')
                reply = json.loads(response.read_text(encoding='utf-8-sig'))
                if not isinstance(reply, dict) or reply.get('id') != request_id or type(reply.get('success')) is not bool:
                    raise AltiumProtocolError(message='Invalid native project-close response')
                if not reply['success']:
                    error = reply.get('error') or {}
                    raise AltiumCommandError(error.get('message', 'Native close failed'), code=error.get('code', 'CLOSE_FAILED'))
                data = reply.get('data')
                if (not isinstance(data, dict) or not isinstance(data.get('project_path'), str)
                        or data['project_path'].casefold() != params['project_path'].casefold()
                        or type(data.get('closed')) is not bool or type(data.get('success')) is not bool):
                    raise AltiumProtocolError(message='Native close did not return readback for the requested project')
                return data
            if _editor_state()['blocked']:
                raise RuntimeError('Native project close is blocked; ownership retained. Inspect Altium before retrying.')
            time.sleep(.05)
        raise TimeoutError('Native project close did not confirm completion; ownership retained. No close was replayed.')
    finally:
        temp.unlink(missing_ok=True)
        request.unlink(missing_ok=True)
        if not launched:
            abandon_unlaunched_legacy(request_id)
        if not _legacy_marker().exists():
            response.unlink(missing_ok=True)
            completion.unlink(missing_ok=True)


def stop_eda():
    state = _guard_editor()
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    if _handler_busy():
        raise RuntimeError('An EDA handler is still running; refusing to stop it for the other bridge')
    if _cleanly_released(state):
        _confirm_editor_return(state)
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
            _confirm_editor_return(state)
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
                _confirm_editor_return(state)
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
        _reset_editor_runtime(state)
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
                if request.name not in {'request.json', 'unused-request.json', 'project-close-request.json'}:
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
        (WORKSPACE / 'project-close-request.json').unlink(missing_ok=True)
        LAUNCH_STATE.unlink(missing_ok=True)
        # This explicit assertion also retires stale polling-session evidence.
        # Archive it for diagnosis; a timeout never calls this reset.
        archive = WORKSPACE / ('manual-stop-' + uuid.uuid4().hex)
        for name in ('bridge-ready.json', 'bridge-owner.json', 'bridge-stopped.json'):
            path = WORKSPACE / name
            if path.exists():
                archive.mkdir(exist_ok=True)
                path.replace(archive / name)
        (WORKSPACE / 'stop').unlink(missing_ok=True)
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
