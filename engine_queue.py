"""Local FIFO admission service. Native execution stays inside guarded clients.

The service accepts only acquire/status messages, never Python or Altium code.
A granted connection is held until its caller finishes. Losing this service
does not release the existing Windows engine mutex or native ownership markers.
"""
import contextlib
import asyncio
import hmac
import json
import os
from pathlib import Path
import secrets
import select
import socket
import socketserver
import subprocess
import sys
import threading
import time
import uuid

PROTOCOL = 1
MAX_MESSAGE = 4096


def queue_root():
    from suite_config import runtime_dir
    return runtime_dir() / 'engine-queue'


def _send(sock, value):
    sock.sendall((json.dumps(value, separators=(',', ':')) + '\n').encode())


def _receive(sock):
    # One frame per direction. Chunked reads avoid a syscall/GIL handoff for
    # every byte; extra frames/data are rejected rather than buffered away.
    data = bytearray()
    while len(data) <= MAX_MESSAGE:
        chunk = sock.recv(MAX_MESSAGE + 1 - len(data))
        if not chunk:
            raise ConnectionError('Queue connection closed')
        data.extend(chunk)
        if b'\n' in data:
            frame, extra = data.split(b'\n', 1)
            if extra or len(frame) > MAX_MESSAGE:
                raise ValueError('Invalid queue frame')
            value = json.loads(frame)
            if not isinstance(value, dict):
                raise ValueError('Invalid queue message')
            return value
    raise ValueError('Queue message too large')


class QueueServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, token):
        self.token = token
        self.condition = threading.Condition()
        self.waiters = []
        self.active = None
        self.last_activity = time.monotonic()
        super().__init__(('127.0.0.1', 0), QueueHandler)


class QueueHandler(socketserver.BaseRequestHandler):
    def handle(self):
        server, sock = self.server, self.request
        ticket = uuid.uuid4().hex
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(3)
            request = _receive(sock)
            token = request.get('token')
            if not isinstance(token, str) or not hmac.compare_digest(token, server.token):
                return
            if request.get('protocol') != PROTOCOL:
                return
            with server.condition:
                server.last_activity = time.monotonic()
                if request.get('op') == 'status':
                    _send(sock, dict(protocol=PROTOCOL, queued=len(server.waiters),
                                     active=server.active is not None))
                    return
                if request.get('op') != 'acquire':
                    return
                if len(server.waiters) >= 128:
                    _send(sock, {'error': 'Engine queue is full'})
                    return
                if request.get('background') and (server.active or server.waiters):
                    _send(sock, {'busy': True})
                    return
                server.waiters.append(ticket)
                while server.active or server.waiters[0] != ticket:
                    server.condition.wait(.05)
                    if select.select([sock], [], [], 0)[0]:
                        # Waiting clients have no further messages to send.
                        # EOF or early release both cancel only this ticket.
                        return
                server.waiters.remove(ticket)
                server.active = ticket
                _send(sock, {'granted': True})
            # No lease TTL: a long operation must never lose its place merely
            # because time passed. TCP EOF follows client/process termination.
            sock.settimeout(None)
            sock.recv(1)
        except (OSError, ValueError):
            pass
        finally:
            with server.condition:
                if ticket in server.waiters:
                    server.waiters.remove(ticket)
                if server.active == ticket:
                    server.active = None
                server.last_activity = time.monotonic()
                server.condition.notify_all()


@contextlib.contextmanager
def _startup_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'startup.lock').open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        deadline = time.monotonic() + 10
        while True:
            try:
                stream.seek(0)
                if sys.platform == 'win32':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Engine queue startup is busy')
                time.sleep(.02)
        try:
            yield
        finally:
            stream.seek(0)
            if sys.platform == 'win32':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _endpoint(root):
    path = root / 'endpoint.json'
    try:
        if path.stat().st_size > MAX_MESSAGE:
            raise ValueError('Oversized endpoint')
        record = json.loads(path.read_text(encoding='utf-8'))
        if (record.get('protocol') != PROTOCOL or type(record.get('port')) is not int
                or not 0 < record['port'] < 65536 or type(record.get('pid')) is not int
                or not isinstance(record.get('started'), (int, float))
                or not isinstance(record.get('token'), str) or len(record['token']) != 64):
            raise ValueError('Invalid endpoint')
        return record
    except FileNotFoundError:
        return None


def _connect(record, op='status', background=False, timeout=3):
    # The endpoint is always numeric IPv4; no hostname or remote host is accepted.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(min(3, timeout))
        sock.connect(('127.0.0.1', record['port']))
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(timeout)
        _send(sock, dict(token=record['token'], protocol=PROTOCOL,
                         op=op, background=background))
        return sock
    except BaseException:
        sock.close()
        raise


def _alive(record):
    import psutil
    try:
        return psutil.Process(record['pid']).create_time() == record['started']
    except psutil.NoSuchProcess:
        return False


def ensure_service(root=None):
    root = Path(root) if root is not None else queue_root()
    # Verify process identity without an extra TCP status round-trip. The
    # acquire connection itself authenticates the endpoint before granting.
    record = _endpoint(root)
    if record and _alive(record):
        return record
    with _startup_lock(root):
        record = _endpoint(root)
        if record:
            if _alive(record):
                return record  # Acquisition authenticates; never replace a live service.
            (root / 'endpoint.json').unlink(missing_ok=True)
        pending_path = root / 'startup-pending.json'
        if pending_path.exists():
            pending = json.loads(pending_path.read_text(encoding='utf-8'))
            if _alive(pending):
                raise RuntimeError('Engine queue startup is still pending; no second service was launched')
            pending_path.unlink()
        with (root / 'service.log').open('ab') as log:
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                      '--serve', str(root)], stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=log, close_fds=True,
                                     creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        import psutil
        pending_path.write_text(json.dumps(dict(pid=child.pid, started=psutil.Process(child.pid).create_time())), encoding='utf-8')
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            record = _endpoint(root)
            if record:
                with _connect(record) as sock:
                    if _receive(sock).get('protocol') == PROTOCOL:
                        pending_path.unlink(missing_ok=True)
                        return record
            if child.poll() is not None:
                raise RuntimeError('Engine queue failed to start; inspect engine-queue/service.log')
            time.sleep(.02)
        raise TimeoutError('Engine queue did not become ready')


class QueueLease:
    def __init__(self, sock):
        self.sock = sock

    def release(self):
        try:
            if isinstance(self.sock, socket.socket):
                self.sock.sendall(b'\n')
            else:
                self.sock.write(b'\n')
        except (OSError, RuntimeError):
            pass  # Broker loss cannot override a native result/error.
        finally:
            self.sock.close()


def acquire(timeout=180, background=False, root=None):
    root = Path(root) if root is not None else queue_root()
    # Health traffic never launches a service or waits behind foreground work.
    record = _endpoint(root) if background else ensure_service(root)
    if record is None:
        return None
    sock = _connect(record, 'acquire', background, timeout)
    try:
        reply = _receive(sock)
        if reply.get('busy'):
            sock.close()
            return None
        if reply.get('granted') is not True:
            raise RuntimeError(reply.get('error', 'Engine queue rejected request'))
        return QueueLease(sock)
    except BaseException:
        sock.close()
        raise


async def acquire_async(timeout=180, root=None):
    """Cancellation closes a waiting connection; no thread keeps its ticket."""
    record = await asyncio.to_thread(ensure_service, root)
    reader, writer = await asyncio.open_connection('127.0.0.1', record['port'], limit=MAX_MESSAGE)
    try:
        writer.write((json.dumps(dict(token=record['token'], protocol=PROTOCOL,
                                      op='acquire', background=False)) + '\n').encode())
        await writer.drain()
        reply = json.loads(await asyncio.wait_for(reader.readline(), timeout))
        if reply.get('granted') is not True:
            raise RuntimeError(reply.get('error', 'Engine queue rejected request'))
        return QueueLease(writer)
    except BaseException:
        writer.close()
        raise


def status(root=None):
    root = Path(root) if root is not None else queue_root()
    record = _endpoint(root)
    if record is None:
        return dict(running=False, queued=0, active=False)
    with _connect(record) as sock:
        return dict(running=True, **_receive(sock))


def serve(root):
    import psutil
    token = secrets.token_hex(32)
    with QueueServer(token) as server:
        root.mkdir(parents=True, exist_ok=True)
        path = root / 'endpoint.json'
        temporary = root / ('endpoint.' + uuid.uuid4().hex + '.tmp')
        record = dict(protocol=PROTOCOL, port=server.server_address[1], token=token,
                      pid=os.getpid(), started=psutil.Process().create_time())
        temporary.write_text(json.dumps(record), encoding='utf-8')
        temporary.replace(path)
        server.timeout = 1
        try:
            while True:
                server.handle_request()
                with server.condition:
                    if not server.active and not server.waiters and time.monotonic() - server.last_activity > 90:
                        break
        finally:
            # Leave endpoint evidence for PID-verified restart. A disconnect
            # never clears native engine ownership or replays a design command.
            pass


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != '--serve':
        raise SystemExit('Use --serve DIRECTORY')
    serve(Path(sys.argv[2]))
