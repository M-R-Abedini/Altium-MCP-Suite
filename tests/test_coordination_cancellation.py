"""Exercise real file-lock ownership while cancelling simulated engine work."""
import asyncio
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

import anyio
import pytest
import bridge_coordination as coord
from test_reliability import load_function

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('phase', ['stop', 'start'])
@pytest.mark.parametrize('scoped', [False, True])
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
    monkeypatch.setattr(coord, '_guard_editor', lambda: None)
    monkeypatch.setattr(coord, '_handler_busy', lambda: False)
    launch = MagicMock(); monkeypatch.setattr(coord.subprocess, 'Popen', launch)
    with pytest.raises(RuntimeError, match='launch state is (invalid|unreadable)'):
        coord._ensure(MagicMock(side_effect=TimeoutError()))
    launch.assert_not_called()
    assert marker.read_text() == content


def test_manual_stop_confirmation_can_clear_damaged_launch_state(tmp_path, monkeypatch):
    marker = tmp_path / 'launch.json'; marker.write_text('[]')
    for name, value in {'WORKSPACE': tmp_path, 'RUNTIME': tmp_path,
                        'LOCK': tmp_path / 'engine.lock', 'LAUNCH_STATE': marker}.items():
        monkeypatch.setattr(coord, name, value)
    monkeypatch.setattr(coord, '_editor_state', lambda: {'pid': 77, 'blocked': False})
    coord.reset_after_manual_stop()
    assert not marker.exists()
