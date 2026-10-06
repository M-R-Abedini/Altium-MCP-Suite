# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A minimal RFC 6455 server, written here rather than depended on.

EasyEDA Pro's extension API talks to the outside world by REGISTERING a
WebSocket connection to a server (``SYS_WebSocket.register``), which
means the editor dials out and this process listens. That is the reverse
of the Altium bridge, where Altium polls a directory for request files.

Only the server half is implemented, and only the parts that a single
trusted local client needs: the opening handshake, text and binary data
frames, close, ping and pong. Extension negotiation, ``permessage-deflate``
and fragmentation across many frames are deliberately absent, and a frame
this cannot honour is refused rather than half-handled.

Written in-house for the same reason the s-expression reader and the
EasyEDA part converter were: the framing rules are then verified here
instead of trusted, and the server keeps its stdlib-only footprint. The
protocol is a published standard, so nothing is guessed.

SCOPE, stated plainly: this listens on the loopback interface for one
local editor. It is not hardened for a hostile network, does not do TLS,
and must not be exposed beyond localhost.
"""

from __future__ import annotations

import base64
import hashlib
import os
import struct
from typing import Optional

__all__ = [
    "FrameError",
    "OPCODE_BINARY",
    "OPCODE_CLOSE",
    "OPCODE_PING",
    "OPCODE_PONG",
    "OPCODE_TEXT",
    "accept_key",
    "build_frame",
    "handshake_response",
    "parse_frame",
]

#: Fixed by RFC 6455 section 1.3. Concatenated with the client key before
#: hashing, which is what proves the peer spoke WebSocket rather than
#: having stumbled onto the port.
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OPCODE_CONTINUATION = 0x0
OPCODE_TEXT = 0x1
OPCODE_BINARY = 0x2
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA

#: Refuse a frame claiming more than this rather than allocating for it.
#: A local editor sending a board snapshot is comfortably inside 64 MB,
#: and an unbounded length field is how a stray connection turns into an
#: out-of-memory crash.
MAX_PAYLOAD = 64 * 1024 * 1024


class FrameError(ValueError):
    """A frame that cannot be honoured, rather than one half-parsed."""


def accept_key(client_key: str) -> str:
    """The Sec-WebSocket-Accept value for a client's Sec-WebSocket-Key.

    RFC 6455 section 4.2.2: append the GUID, take SHA-1, base64 it. The
    client checks this, so an incorrect implementation fails at connect
    time rather than silently later.
    """
    digest = hashlib.sha1((client_key.strip() + _GUID).encode("ascii"))
    return base64.b64encode(digest.digest()).decode("ascii")


def handshake_response(headers: dict[str, str]) -> bytes:
    """The 101 response for a client's request headers.

    Header names are matched case-insensitively because HTTP says they
    are case-insensitive and clients genuinely differ.
    """
    lowered = {k.lower(): v for k, v in headers.items()}
    key = lowered.get("sec-websocket-key")
    if not key:
        raise FrameError("handshake has no Sec-WebSocket-Key")
    if lowered.get("upgrade", "").lower() != "websocket":
        raise FrameError("handshake is not an Upgrade: websocket request")
    version = lowered.get("sec-websocket-version", "").strip()
    if version and version != "13":
        # 13 is the only version RFC 6455 defines. Saying so beats
        # accepting a version whose framing may differ.
        raise FrameError(f"unsupported WebSocket version {version!r}")

    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept_key(key)}\r\n"
        "\r\n"
    ).encode("ascii")


def build_frame(payload: bytes, opcode: int = OPCODE_TEXT,
                mask: bool = False) -> bytes:
    """One unfragmented frame.

    A server MUST NOT mask (RFC 6455 section 5.1), so ``mask`` defaults
    off and exists only so tests can build client frames, which MUST be
    masked. Having both directions in one function is what lets the
    round-trip test drive the real parser rather than a stub.
    """
    if opcode not in (OPCODE_CONTINUATION, OPCODE_TEXT, OPCODE_BINARY,
                      OPCODE_CLOSE, OPCODE_PING, OPCODE_PONG):
        raise FrameError(f"unknown opcode {opcode:#x}")
    if len(payload) > MAX_PAYLOAD:
        raise FrameError(
            f"payload of {len(payload)} bytes exceeds the {MAX_PAYLOAD} "
            f"byte limit")

    header = bytearray()
    header.append(0x80 | opcode)              # FIN set, no RSV bits

    length = len(payload)
    flag = 0x80 if mask else 0x00
    if length < 126:
        header.append(flag | length)
    elif length < (1 << 16):
        header.append(flag | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(flag | 127)
        header.extend(struct.pack("!Q", length))

    if not mask:
        return bytes(header) + payload

    key = os.urandom(4)
    masked = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return bytes(header) + key + masked


def parse_frame(data: bytes) -> Optional[tuple[int, bytes, int]]:
    """Parse one MESSAGE from the front of ``data``.

    Returns ``(opcode, payload, bytes_consumed)``, or None when ``data``
    holds less than a whole message. Returning None rather than raising
    is what lets a caller accumulate from a socket without having to
    know the length in advance.

    A fragmented message (FIN clear, then continuation frames) is
    reassembled here, and the reported opcode is the FIRST fragment's.
    The real EasyEDA Pro editor fragments large replies; Node's client
    never does, so no harness caught it, and the first live board with
    real component data killed the session mid-probe.

    One honest limit remains: a CONTROL frame interleaved between the
    fragments of one message (legal per the RFC) is refused rather than
    reordered, because this parser reports a consumed PREFIX and cannot
    hand frames back out of order. The editor has not been seen doing
    it; if it ever does, this error names the situation.
    """
    first_frame = _parse_single(data)
    if first_frame is None:
        return None
    fin, opcode, payload, consumed = first_frame

    if opcode == OPCODE_CONTINUATION:
        raise FrameError(
            "a continuation frame arrived with no message in progress; "
            "the stream is out of sync")
    if fin:
        return opcode, payload, consumed

    if opcode not in (OPCODE_TEXT, OPCODE_BINARY):
        raise FrameError("a control frame cannot be fragmented")

    fragments = [payload]
    total = consumed
    while True:
        nxt = _parse_single(data[total:])
        if nxt is None:
            return None
        nfin, nopcode, npayload, nconsumed = nxt
        if nopcode != OPCODE_CONTINUATION:
            raise FrameError(
                "a control frame interleaved within a fragmented message "
                "is not supported by this parser")
        fragments.append(npayload)
        total += nconsumed
        if sum(len(f) for f in fragments) > MAX_PAYLOAD:
            raise FrameError(
                f"reassembled message exceeds the {MAX_PAYLOAD} limit")
        if nfin:
            return opcode, b"".join(fragments), total


def _parse_single(data: bytes) -> Optional[tuple[bool, int, bytes, int]]:
    """One raw frame: ``(fin, opcode, payload, consumed)`` or None."""
    if len(data) < 2:
        return None

    first, second = data[0], data[1]
    if first & 0x70:
        # RSV1..3 signal an extension that was never negotiated here.
        raise FrameError("reserved bits set; no extension is supported")
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    offset = 2

    if length == 126:
        if len(data) < offset + 2:
            return None
        length = struct.unpack("!H", data[offset:offset + 2])[0]
        offset += 2
    elif length == 127:
        if len(data) < offset + 8:
            return None
        length = struct.unpack("!Q", data[offset:offset + 8])[0]
        offset += 8

    if length > MAX_PAYLOAD:
        raise FrameError(
            f"frame claims {length} bytes, over the {MAX_PAYLOAD} limit")

    if masked:
        if len(data) < offset + 4:
            return None
        key = data[offset:offset + 4]
        offset += 4
    else:
        key = b""

    if len(data) < offset + length:
        return None

    payload = data[offset:offset + length]
    if masked:
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))

    return fin, opcode, payload, offset + length
