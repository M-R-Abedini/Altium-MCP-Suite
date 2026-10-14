"""Exercise real file-lock ownership while cancelling simulated engine work."""
import asyncio
import json
import threading
import sys
from pathlib import Path
from unittest.mock import MagicMock

import anyio
import pytest
import bridge_coordination as coord
from test_reliability import load_function

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('phase', ['stop', 'start'])
@pytest.mark.parametrize('scoped', [False, True])
@pytest.mark.skipif(sys.platform != 'win32', reason='Exercises real Windows cross-process file locking')
def test_cancel_retains_lock_until_worker_finishes(tmp_path, monkeypatch, phase, scoped):
    monkeypatch.setattr(coord, 'LOCK', tmp_path / 'engine.lock')
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def worker():
        started.set()
        assert release.wait(5), 'test did not release worker'
        finished.set()
    async def tool(*a, **kw):
        calls.append('tool')
        return {'ok': True}
    namespace = dict(OFFLINE=set(), original=tool, async_engine_lock=coord.async_engine_lock,
                     run_engine_worker=coord.run_engine_worker,
                     stop_eda=worker if phase == 'stop' else lambda: None,
                     start_eda=worker if phase == 'start' else lambda: None,
                     log_event=MagicMock(), sys=__import__('sys'))
    wrapper = load_function(ROOT / 'coffeenmusic/server/codex_stdio.py', 'coordinated', namespace)
    async def run():
        scope = anyio.CancelScope()
        async def invoke():
            with scope:
                await wrapper('read_test')
        task = asyncio.create_task(invoke())
        try:
            assert await asyncio.to_thread(started.wait, 2)
            for _ in range(2):
                scope.cancel() if scoped else task.cancel()
                await asyncio.sleep(.01)
                assert not task.done()
                handle = coord.try_lock()
                if handle is not None:
                    coord.unlock(handle)
                    pytest.fail('engine lock escaped while worker was running')
            release.set()
            try:
                await task
            except asyncio.CancelledError:
                assert not scoped
            assert finished.is_set()
            assert calls == ([] if phase == 'stop' else ['tool'])
            handle = coord.try_lock()
            assert handle is not None
            coord.unlock(handle)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize('content', ['[]', '{}', 'null', '{', '{"attempted_at":"now"}',
                                  '{"attempted_at":true}', '{"attempted_at":NaN}',
                                  '{"attempted_at":Infinity}', '{"attempted_at":-1}',
                                  '{"attempted_at":' + '9' * 400 + '}'])
def test_invalid_launch_state_never_launches(tmp_path, monkeypatch, content):
    marker = tmp_path / 'launch.json'; marker.write_text(content)
    monkeypatch.setattr(coord, 'WORKSPACE', tmp_path)
    monkeypatch.setattr(coord, 'LAUNCH_STATE', marker)
    monkeypatch.setattr(coord, '_guard_editor', lambda: {'pid': 77, 'blocked': False})
    monkeypatch.setattr(coord, '_session_started_at', lambda pid: 100)
    monkeypatch.setattr(coord, '_handler_busy', lambda: False)
    launch = MagicMock(); monkeypatch.setattr(coord.subprocess, 'Popen', launch)
    with pytest.raises(RuntimeError, match='launch state is (invalid|unreadable)'):
        coord._ensure(MagicMock(side_effect=TimeoutError()))
    launch.assert_not_called()
    assert marker.read_text() == content


@pytest.mark.skipif(sys.platform != 'win32', reason='Exercises real Windows cross-process file locking')
def test_manual_stop_confirmation_can_clear_damaged_launch_state(tmp_path, monkeypatch):
    marker = tmp_path / 'launch.json'; marker.write_text('[]')
    for name, value in {'WORKSPACE': tmp_path, 'RUNTIME': tmp_path,
                        'LOCK': tmp_path / 'engine.lock', 'LAUNCH_STATE': marker}.items():
        monkeypatch.setattr(coord, name, value)
    monkeypatch.setattr(coord, '_editor_state', lambda: {'pid': 77, 'blocked': False})
    coord.reset_after_manual_stop()
    assert not marker.exists()


def test_confirmed_manual_stop_archives_native_evidence_without_launch(tmp_path, monkeypatch):
    import contextlib
    monkeypatch.setattr(coord, 'engine_lock', contextlib.nullcontext)
    for name, value in {'WORKSPACE': tmp_path, 'RUNTIME': tmp_path,
                        'LAUNCH_STATE': tmp_path/'launch.json'}.items():
        monkeypatch.setattr(coord, name, value)
    monkeypatch.setattr(coord, '_editor_state', lambda: {'pid': 77, 'blocked': False})
    originals = {name: '{"old":"evidence"}' for name in
                 ('bridge-ready.json', 'bridge-owner.json', 'bridge-stopped.json')}
    for name, content in originals.items():
        (tmp_path/name).write_text(content)
    (tmp_path/'stop').touch()
    launch = MagicMock(); monkeypatch.setattr(coord.subprocess, 'Popen', launch)
    coord.reset_after_manual_stop()
    archive, = tmp_path.glob('manual-stop-*')
    for name, content in originals.items():
        assert not (tmp_path/name).exists() and (archive/name).read_text() == content
    assert not (tmp_path/'stop').exists()
    launch.assert_not_called()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows desktop API fault injection')
@pytest.mark.parametrize('failure', ['open', 'attach', 'send_exception', 'send_zero', 'restore', None])
def test_barrier_worker_errors_never_confirm_return(monkeypatch, failure):
    import ctypes
    from types import SimpleNamespace
    api = SimpleNamespace(GetThreadDesktop=MagicMock(return_value=123),
                          SetThreadDesktop=MagicMock(return_value=1),
                          SendMessageTimeoutW=MagicMock(return_value=1))
    desktop = MagicMock(); desktop.__int__.return_value = 456
    open_desktop = MagicMock(return_value=desktop)
    if failure == 'open': open_desktop.side_effect = OSError('desktop inaccessible')
    if failure == 'attach': api.SetThreadDesktop.return_value = 0
    if failure == 'send_exception': api.SendMessageTimeoutW.side_effect = OSError('invalid handle')
    if failure == 'send_zero': api.SendMessageTimeoutW.return_value = 0
    if failure == 'restore': api.SetThreadDesktop.side_effect = [1, 0]
    monkeypatch.setitem(sys.modules, 'win32service', SimpleNamespace(OpenDesktop=open_desktop))
    monkeypatch.setitem(sys.modules, 'win32api', SimpleNamespace(GetCurrentThreadId=lambda: 42))
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **kw: api)
    if failure:
        with pytest.raises(TimeoutError, match='ownership retained'):
            coord._ui_thread_barrier(0x123456789, 50)
    else:
        coord._ui_thread_barrier(0x123456789, 50)
        assert api.SendMessageTimeoutW.call_args.args[0] == 0x123456789
        assert api.SetThreadDesktop.call_args_list[-1].args == (123,)
        desktop.CloseDesktop.assert_called_once()
