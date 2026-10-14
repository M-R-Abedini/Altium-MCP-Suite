"""Real-process admission, failure and cancellation tests; no Altium required."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import socket
import subprocess
import sys
import threading
import time

import psutil
import pytest
import engine_queue as queue


def until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('Queue condition not reached')
        time.sleep(.01)


@pytest.fixture
def service(tmp_path):
    root = tmp_path / 'queue'
    record = queue.ensure_service(root)
    try:
        yield root, record
    finally:
        try:
            process = psutil.Process(record['pid'])
            if process.create_time() == record['started']:
                process.terminate()
                process.wait(timeout=5)
        except psutil.NoSuchProcess:
            pass


@pytest.fixture
def isolated_mutex(monkeypatch, tmp_path):
    import engine_identity
    import bridge_coordination as coord
    import uuid
    sid, profile = engine_identity.windows_identity()
    test_sid = sid + '.Test.' + uuid.uuid4().hex
    monkeypatch.setattr(engine_identity, 'windows_identity', lambda: (test_sid, profile))
    monkeypatch.setattr(coord, 'LOCK', tmp_path/'native-test.lock')


def test_parallel_startup_publishes_one_service(tmp_path):
    root = tmp_path / 'queue'
    records = []
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            records = list(pool.map(lambda _: queue.ensure_service(root), range(4)))
        assert len({r['pid'] for r in records}) == 1
        assert len({r['token'] for r in records}) == 1
    finally:
        for pid in {r['pid'] for r in records}:
            process = psutil.Process(pid); process.terminate(); process.wait(timeout=5)


def test_fifo_and_background_yields(service):
    root, _ = service
    active = queue.acquire(root=root)
    order = []
    first_done, second_done = threading.Event(), threading.Event()
    def work(name, done):
        lease = queue.acquire(root=root)
        try:
            order.append(name)
            done.wait(3)
        finally:
            lease.release()
    first = threading.Thread(target=work, args=('first', first_done))
    second = threading.Thread(target=work, args=('second', second_done))
    first.start()
    until(lambda: queue.status(root)['queued'] == 1)
    second.start()
    until(lambda: queue.status(root)['queued'] == 2)
    assert queue.acquire(root=root, background=True) is None
    active.release()
    until(lambda: order == ['first'])
    first_done.set()
    until(lambda: order == ['first', 'second'])
    second_done.set(); first.join(5); second.join(5)
    until(lambda: not queue.status(root)['active'])


def test_wait_timeout_removes_ticket_without_releasing_owner(service):
    root, _ = service
    lease = queue.acquire(root=root)
    try:
        with pytest.raises(TimeoutError):
            queue.acquire(root=root, timeout=.05)
        until(lambda: queue.status(root)['queued'] == 0)
        assert queue.status(root)['active']
    finally:
        lease.release()


def test_async_cancel_does_not_leak_waiting_or_granted_ticket(service):
    root, _ = service
    async def scenario():
        lease = await queue.acquire_async(root=root)
        try:
            task = asyncio.create_task(queue.acquire_async(root=root))
            await asyncio.to_thread(until, lambda: queue.status(root)['queued'] == 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.to_thread(until, lambda: queue.status(root)['queued'] == 0)
            assert queue.status(root)['active']
        finally:
            lease.release()
        second = await queue.acquire_async(root=root)
        second.release()
    asyncio.run(scenario())


def test_dead_client_connection_releases_admission(service):
    root, _ = service
    child = subprocess.Popen([sys.executable, '-c',
        'import engine_queue as q,sys,time; x=q.acquire(root=sys.argv[1]); print("ready",flush=True); time.sleep(60)',
        str(root)], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'ready'
        assert queue.status(root)['active']
        child.terminate(); child.wait(timeout=5)
        until(lambda: not queue.status(root)['active'])
        lease = queue.acquire(root=root); lease.release()
    finally:
        if child.poll() is None:
            child.terminate(); child.wait(timeout=5)
        child.stdout.close()


def test_wrong_token_and_unknown_operation_do_not_join_queue(service):
    root, record = service
    with queue._connect(dict(record, token='x' * 64)) as sock:
        with pytest.raises(ConnectionError):
            queue._receive(sock)
    with queue._connect(record, op='execute_python') as sock:
        with pytest.raises(ConnectionError):
            queue._receive(sock)
    assert queue.status(root)['queued'] == 0


def test_live_unreachable_service_is_not_replaced(tmp_path, monkeypatch):
    import os
    record = dict(protocol=1, port=1, token='x'*64,
                  pid=os.getpid(), started=psutil.Process().create_time())
    (tmp_path/'endpoint.json').write_text(json.dumps(record))
    monkeypatch.setattr(queue, '_connect', lambda *a, **k: (_ for _ in ()).throw(ConnectionRefusedError()))
    monkeypatch.setattr(queue.subprocess, 'Popen', lambda *a, **k: pytest.fail('replacement launched'))
    with pytest.raises(ConnectionRefusedError):
        queue.acquire(root=tmp_path)


def test_pending_startup_never_launches_second_service(tmp_path, monkeypatch):
    import os
    (tmp_path/'startup-pending.json').write_text(json.dumps(dict(pid=os.getpid(), started=psutil.Process().create_time())))
    monkeypatch.setattr(queue.subprocess, 'Popen', lambda *a, **k: pytest.fail('duplicate launched'))
    with pytest.raises(RuntimeError, match='still pending'):
        queue.ensure_service(tmp_path)


@pytest.mark.skipif(sys.platform != 'win32', reason='Native mutex integration')
def test_broker_death_does_not_unlock_native_engine(service, isolated_mutex):
    import bridge_coordination as coord
    root, record = service
    admission = queue.acquire(root=root)
    native = coord.try_lock()
    assert native is not None
    try:
        process = psutil.Process(record['pid'])
        process.terminate(); process.wait(timeout=5)
        assert coord.try_lock() is None
    finally:
        coord.unlock(native)
        admission.release()


@pytest.mark.skipif(sys.platform != 'win32', reason='Native mutex integration')
def test_queue_cancellation_retains_native_mutex(service, isolated_mutex, monkeypatch):
    import bridge_coordination as coord
    root, _ = service
    monkeypatch.setattr(queue, 'queue_root', lambda: root)
    async def scenario():
        async with coord.async_engine_lock():
            task = asyncio.create_task(_wait())
            await asyncio.to_thread(until, lambda: queue.status(root)['queued'] == 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert coord.try_lock() is None
        async with coord.async_engine_lock():
            assert coord.try_lock() is None
    async def _wait():
        async with coord.async_engine_lock():
            pytest.fail('Cancelled command dispatched')
    asyncio.run(scenario())


@pytest.mark.skipif(sys.platform != 'win32', reason='Native mutex integration')
@pytest.mark.parametrize('cancel_while', ['queued', 'dispatched'])
def test_eda_async_cancel_before_dispatch_or_drain_after_dispatch(service, isolated_mutex, monkeypatch, cancel_while):
    import bridge_coordination as coord
    from test_reliability import load_function
    from pathlib import Path
    from types import SimpleNamespace
    root, _ = service
    monkeypatch.setattr(queue, 'queue_root', lambda: root)
    started, finish = threading.Event(), threading.Event()
    calls = []
    def dispatch(*args):
        calls.append(args[1]); started.set()
        assert finish.wait(5)
        return {'ok': True}
    wrapper = load_function(Path(__file__).resolve().parents[1]/'eda_stdio.py', 'coordinated_async',
        dict(async_engine_lock=coord.async_engine_lock, run_engine_worker=coord.run_engine_worker,
             dispatch_locked=dispatch))
    bridge = SimpleNamespace(is_altium_running=lambda: True, _ensure_keepalive=lambda: None,
                             config=SimpleNamespace(poll_timeout=10))
    async def scenario():
        lease = await queue.acquire_async(root=root) if cancel_while == 'queued' else None
        task = asyncio.create_task(wrapper(bridge, 'test.read'))
        try:
            if lease:
                await asyncio.to_thread(until, lambda: queue.status(root)['queued'] == 1)
            else:
                assert await asyncio.to_thread(started.wait, 3)
            task.cancel()
            if not lease:
                await asyncio.sleep(.01)
                assert not task.done() and coord.try_lock() is None
                finish.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert calls == ([] if lease else ['test.read'])
        finally:
            finish.set()
            if lease: lease.release()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
