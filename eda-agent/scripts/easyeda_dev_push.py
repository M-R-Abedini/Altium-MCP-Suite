# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Push the built .eext into a running EasyEDA Pro, no manual import.

EasyEDA's own SDK (pro-api-sdk >= 1.4.0, ``npm run debug``) starts a
WebSocket server on port 59394 and the editor connects TO it; on
connection the server immediately sends the packaged extension as
base64 and the editor installs it in place. No handshake, no flags in
the SDK's half. Message shape, read from their build/dev.ts:

    {"type": "file",
     "topic": "Dev Mode Extension Package Update",
     "content": "<base64 of the .eext>",
     "fileName": "<name>_v<version>.eext",
     "fileMimeType": "application/octet-stream"}

Whether the DESKTOP client dials that port spontaneously, or only under
a dev setting, is not documented. This script measures it: run it, and
it reports whether anything connected and what it sent. The manual
delete/import/restart cycle cost hours today; if the editor takes this
push, that cycle is gone.

Run: python scripts/easyeda_dev_push.py [seconds]
"""

from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import socket
import struct
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parents[1] / "extensions" / "easyeda"
PORT = 59394
_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _accept_websocket(conn: socket.socket) -> bool:
    """Answer the HTTP upgrade. Returns False for a non-WS probe."""
    conn.settimeout(10)
    raw = b""
    while b"\r\n\r\n" not in raw:
        chunk = conn.recv(4096)
        if not chunk:
            return False
        raw += chunk
    head = raw.decode("latin-1")
    key = None
    for line in head.split("\r\n"):
        if line.lower().startswith("sec-websocket-key:"):
            key = line.split(":", 1)[1].strip()
    if not key:
        return False
    accept = base64.b64encode(
        hashlib.sha1((key + _WS_MAGIC).encode("ascii")).digest()).decode()
    conn.sendall(
        ("HTTP/1.1 101 Switching Protocols\r\n"
         "Upgrade: websocket\r\nConnection: Upgrade\r\n"
         f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode("ascii"))
    return True


def _send_text(conn: socket.socket, text: str) -> None:
    payload = text.encode("utf-8")
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", 0x81, length)
    elif length < 65536:
        header = struct.pack("!BBH", 0x81, 126, length)
    else:
        header = struct.pack("!BBQ", 0x81, 127, length)
    conn.sendall(header + payload)


def _push_message() -> str:
    manifest = json.loads(
        (HERE / "extension.json").read_text(encoding="utf-8"))
    package = (HERE / "eda-agent-bridge.eext").read_bytes()
    return json.dumps({
        "type": "file",
        "topic": "Dev Mode Extension Package Update",
        "content": base64.b64encode(package).decode("ascii"),
        "fileName": f"{manifest['name']}_v{manifest['version']}.eext",
        "fileMimeType": "application/octet-stream",
    })


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--probe"]
    probe_only = "--probe" in sys.argv[1:]
    wait_seconds = int(args[0]) if args else 300
    message = _push_message()
    if probe_only:
        print("PROBE ONLY: a connection will be reported and nothing "
              "will be installed.")
    print(f"Package staged: {len(message)} chars of JSON "
          f"({(HERE / 'eda-agent-bridge.eext').stat().st_size} byte eext)")

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", PORT))
    server.listen(4)
    server.settimeout(1.0)
    print(f"Dev-push server on ws://127.0.0.1:{PORT} for "
          f"{wait_seconds}s. Waiting to see whether EasyEDA dials it...")

    pushed = 0
    deadline = time.time() + wait_seconds

    def _serve(conn: socket.socket, peer) -> None:
        nonlocal pushed
        try:
            if not _accept_websocket(conn):
                print(f"  {peer}: connected but not a WebSocket upgrade")
                return
            if probe_only:
                # Measure without installing.
                #
                # Whether the desktop client dials this port at all is
                # the unknown worth settling first, and it is settled by
                # the connection itself. Pushing is a separate decision:
                # it writes an extension into somebody's editor, which
                # is not a thing to do as a side effect of finding out
                # whether a socket opens.
                print(f"  {peer}: WEBSOCKET CONNECTED. Probe only, so "
                      f"nothing was pushed. The editor DOES dial this "
                      f"port, which means dev-push is available and the "
                      f"manual import cycle is avoidable.")
                pushed += 1
                return
            print(f"  {peer}: WEBSOCKET CONNECTED, pushing the package")
            _send_text(conn, message)
            pushed += 1
            # Keep the socket open briefly to catch any reply frames.
            conn.settimeout(15)
            try:
                reply = conn.recv(4096)
                if reply:
                    print(f"  {peer}: client sent {len(reply)} bytes back")
            except socket.timeout:
                pass
        except Exception as exc:                 # noqa: BLE001
            print(f"  {peer}: {exc}")
        finally:
            conn.close()

    while time.time() < deadline:
        try:
            conn, peer = server.accept()
        except socket.timeout:
            continue
        threading.Thread(target=_serve, args=(conn, peer),
                         daemon=True).start()

    server.close()
    if pushed:
        print(f"\nPushed the package {pushed} time(s). If the editor "
              f"accepted it, the extension updated in place with no "
              f"manual import.")
        return 0
    print("\nNothing connected. The desktop client does not dial the "
          "dev port spontaneously; the manual import cycle stands, or a "
          "client-side dev setting is needed first.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
