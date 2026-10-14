"""Windows-wide suite gate and durable editor-to-runtime binding.

The mutex is per Windows user/session, not per installation or runtime folder.
It serializes all suite editors conservatively. Binding files survive Python
exit: an abandoned mutex is not proof that Altium's script finished.
"""
import json
import math
import os
from pathlib import Path
import threading
import uuid
from functools import lru_cache

_LOCAL_GATE = threading.Lock()  # Windows mutexes are reentrant on one OS thread.


@lru_cache(maxsize=1)
def windows_identity():
    import win32api
    import win32con
    import win32profile
    import win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        sid = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
        profile = Path(win32profile.GetUserProfileDirectory(token)).resolve()
        return sid, profile
    finally:
        token.Close()


class MutexLease:
    def __init__(self, handle):
        self.handle = handle
        self.thread = threading.get_ident()

    def release(self):
        import win32api
        import win32event
        if self.thread != threading.get_ident():
            raise RuntimeError('Engine mutex must be released by its acquiring thread')
        win32event.ReleaseMutex(self.handle)
        try:
            win32api.CloseHandle(self.handle)
        finally:
            _LOCAL_GATE.release()


def try_mutex():
    """Nonblocking, with a process-local gate against asyncio reentrancy."""
    if not _LOCAL_GATE.acquire(blocking=False):
        return None
    handle, acquired = None, False
    try:
        import win32api
        import win32event
        sid, _ = windows_identity()
        handle = win32event.CreateMutex(None, False, 'Local\\AltiumMCPSuite.Engine.v1.' + sid)
        result = win32event.WaitForSingleObject(handle, 0)
        if result in (0, 0x80):  # acquired or abandoned; binding is checked separately
            lease = MutexLease(handle)
            acquired = True
            return lease
        if result != 0x102:
            raise RuntimeError('Unexpected engine mutex wait result: ' + str(result))
        return None
    finally:
        if not acquired:
            try:
                if handle is not None:
                    win32api.CloseHandle(handle)
            finally:
                _LOCAL_GATE.release()


def _record(pid, started, runtime, workspace):
    if type(pid) is not int or pid <= 0 or type(started) not in (int, float):
        raise RuntimeError('Cannot bind an invalid editor process identity')
    try:
        valid = math.isfinite(started) and 0 < started <= 253402300799
    except OverflowError:
        valid = False
    if not valid:
        raise RuntimeError('Cannot bind an invalid editor process identity')
    return dict(schema=1, pid=pid, session_started_at=started,
                runtime=str(Path(runtime).resolve()), workspace=str(Path(workspace).resolve()))


def binding_path(pid, started, root=None):
    root = Path(root) if root is not None else windows_identity()[1] / '.altium-mcp-suite' / 'coordination'
    return root / ('editor-%d-%d.json' % (pid, int(started * 1000000)))


def claim_editor(pid, started, runtime, workspace, root=None):
    """Caller holds the shared gate. A foreign runtime never takes over by TTL."""
    expected = _record(pid, started, runtime, workspace)
    target = binding_path(pid, started, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temp = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temp.write_text(json.dumps(expected), encoding='utf-8')
            try:
                os.link(temp, target)  # Atomic first publication without overwriting a winner.
            except FileExistsError:
                pass
        finally:
            temp.unlink(missing_ok=True)
    try:
        if target.stat().st_size > 16384:
            raise ValueError('oversized binding')
        actual = json.loads(target.read_text(encoding='utf-8-sig'))
        if (not isinstance(actual, dict) or type(actual.get('schema')) is not int or actual.get('schema') != 1
                or actual.get('pid') != pid or actual.get('session_started_at') != started
                or any(not isinstance(actual.get(k), str) or not Path(actual[k]).is_absolute()
                       for k in ('runtime', 'workspace'))):
            raise ValueError('invalid binding')
        _record(actual['pid'], actual['session_started_at'], actual['runtime'], actual['workspace'])
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError('Editor runtime binding cannot be verified: ' + str(target)
                           + '. Confirm native Stop before explicit ownership recovery.') from error
    if any(os.path.normcase(str(Path(actual[k]).resolve())) != os.path.normcase(expected[k]) for k in ('runtime', 'workspace')):
        raise RuntimeError('Altium editor is bound to another MCP runtime. Use ALTIUM_MCP_RUNTIME='
                           + actual['runtime'] + ' and EDA_AGENT_WORKSPACE=' + actual['workspace']
                           + ' in every client. No script was launched or ownership reset.')
    return target


def reset_editor(pid, started, root=None):
    """Only after the operator confirmed native Stop; never called on timeout."""
    _record(pid, started, Path.cwd(), Path.cwd())
    target = binding_path(pid, started, root)
    if target.exists():
        archive = target.with_name(target.name + '.manual-stop-' + uuid.uuid4().hex)
        target.replace(archive)
        return archive
    return None
