# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The in-house RFC 6455 server framing.

Written here rather than depended on, so the rules are verified instead
of trusted. That only holds if the tests check the protocol against the
specification rather than against the implementation's own habits, so
the handshake case below uses the worked example from RFC 6455 section
1.3, whose expected output is fixed by the standard and not by this code.
"""

from __future__ import annotations

import json
import struct

import pytest

from eda_agent.bridge.websocket import (
    MAX_PAYLOAD,
    OPCODE_BINARY,
    OPCODE_CLOSE,
    OPCODE_PING,
    OPCODE_TEXT,
    FrameError,
    accept_key,
    build_frame,
    handshake_response,
    parse_frame,
)


# ---- handshake --------------------------------------------------------

def test_accept_key_matches_the_rfc_worked_example():
    """RFC 6455 section 1.3 fixes this pair.

    An independently specified expected value is the whole point: it
    cannot drift with the implementation, and a client refuses the
    connection outright if this is wrong.
    """
    assert accept_key("dGhlIHNhbXBsZSBub25jZQ==") == \
        "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_handshake_is_case_insensitive_about_header_names():
    """HTTP header names are case-insensitive and clients differ."""
    response = handshake_response({
        "UPGRADE": "websocket",
        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        "sec-websocket-version": "13",
    })
    assert response.startswith(b"HTTP/1.1 101 ")
    assert b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=" in response


def test_a_handshake_without_a_key_is_refused():
    with pytest.raises(FrameError) as excinfo:
        handshake_response({"upgrade": "websocket"})
    assert "Sec-WebSocket-Key" in str(excinfo.value)


def test_a_plain_http_request_is_refused():
    """A browser hitting the port by accident is not a WebSocket peer."""
    with pytest.raises(FrameError) as excinfo:
        handshake_response({"host": "localhost",
                            "sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ=="})
    assert "Upgrade" in str(excinfo.value)


def test_an_unsupported_version_is_refused_rather_than_assumed():
    """13 is the only version RFC 6455 defines.

    Accepting another would mean guessing at framing that may differ.
    """
    with pytest.raises(FrameError) as excinfo:
        handshake_response({
            "upgrade": "websocket",
            "sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ==",
            "sec-websocket-version": "8",
        })
    assert "8" in str(excinfo.value)


# ---- framing ----------------------------------------------------------

def test_a_server_frame_is_never_masked():
    """RFC 6455 section 5.1: a server MUST NOT mask what it sends.

    A masked server frame makes conforming clients drop the connection.
    """
    frame = build_frame(b"hello")
    assert frame[1] & 0x80 == 0, "mask bit set on a server frame"


def test_a_client_frame_round_trips_through_the_parser():
    payload = b'{"cmd":"ping"}'
    frame = build_frame(payload, mask=True)
    opcode, got, consumed = parse_frame(frame)
    assert (opcode, got, consumed) == (OPCODE_TEXT, payload, len(frame))


def test_masking_actually_changes_the_bytes_on_the_wire():
    """Otherwise "masked" would be a claim rather than a behaviour."""
    payload = b"A" * 32
    frame = build_frame(payload, mask=True)
    assert payload not in frame, "payload appears unmasked on the wire"
    assert parse_frame(frame)[1] == payload


@pytest.mark.parametrize("size", [0, 1, 125, 126, 127, 65535, 65536])
def test_every_length_encoding_boundary_round_trips(size):
    """7-bit, 16-bit and 64-bit length forms, and the edges between.

    These boundaries are where a length field is read with the wrong
    width, which yields a plausible-looking but wrong payload rather
    than an error.
    """
    payload = b"x" * size
    for masked in (False, True):
        opcode, got, consumed = parse_frame(build_frame(payload, mask=masked))
        assert got == payload and opcode == OPCODE_TEXT
        assert consumed == len(build_frame(payload, mask=masked)) or masked


def test_the_length_form_is_the_smallest_that_fits():
    """A needlessly wide length field is legal to read but wrong to write."""
    assert build_frame(b"x" * 125)[1] & 0x7F == 125
    assert build_frame(b"x" * 126)[1] & 0x7F == 126
    assert build_frame(b"x" * 65536)[1] & 0x7F == 127


# ---- partial data -----------------------------------------------------

def test_a_partial_frame_returns_none_rather_than_guessing():
    """A socket delivers arbitrary chunks, not whole frames.

    Raising here would turn normal TCP behaviour into an error; None
    lets the caller accumulate and retry.
    """
    frame = build_frame(b"a reasonably long payload to split", mask=True)
    for cut in range(1, len(frame)):
        assert parse_frame(frame[:cut]) is None, f"claimed a frame at {cut}"
    assert parse_frame(frame) is not None


def test_trailing_bytes_are_left_for_the_next_frame():
    """Two frames in one read must not consume each other."""
    one = build_frame(b"first", mask=True)
    two = build_frame(b"second", mask=True)
    opcode, payload, consumed = parse_frame(one + two)
    assert payload == b"first"
    assert consumed == len(one)
    assert parse_frame((one + two)[consumed:])[1] == b"second"


# ---- refusals ---------------------------------------------------------

def _fragmented(*parts: bytes, mask: bool = True) -> bytes:
    """A message split across frames the way the real editor splits one.

    First frame keeps its data opcode with FIN clear; the rest are
    continuations (opcode 0), the last with FIN set.
    """
    out = bytearray()
    for index, part in enumerate(parts):
        frame = bytearray(build_frame(part, mask=mask))
        if index > 0:
            frame[0] = (frame[0] & 0xF0) | 0x00      # continuation
        if index < len(parts) - 1:
            frame[0] &= 0x7F                          # clear FIN
        out.extend(frame)
    return bytes(out)


def test_a_fragmented_message_is_reassembled_whole():
    """The real editor fragments large replies; Node's client never does.

    No harness caught it, and the first live board with real component
    data killed the session mid-probe on the old refusal.
    """
    data = _fragmented(b'{"id": "x", ', b'"result": ', b'{"ok": true}}')
    opcode, payload, consumed = parse_frame(data)
    assert payload == b'{"id": "x", "result": {"ok": true}}'
    assert consumed == len(data)


def test_an_incomplete_fragment_train_waits_for_more_bytes():
    """Half a message must read as "not yet", never as a message."""
    data = _fragmented(b"first", b"second", b"third")
    assert parse_frame(data[:-4]) is None


def test_a_lone_continuation_is_a_desynced_stream():
    """A continuation with nothing in progress means bytes were lost."""
    frame = bytearray(build_frame(b"tail", mask=True))
    frame[0] = (frame[0] & 0xF0) | 0x00
    with pytest.raises(FrameError) as excinfo:
        parse_frame(bytes(frame))
    assert "out of sync" in str(excinfo.value)


def test_a_control_frame_between_fragments_is_refused_by_name():
    """Legal per the RFC, unimplementable under prefix consumption.

    The refusal names the situation so a future log reads as a known
    limit rather than a mystery.
    """
    first = bytearray(build_frame(b"half", mask=True))
    first[0] &= 0x7F
    ping = bytearray(build_frame(b"", mask=True))
    ping[0] = (ping[0] & 0xF0) | 0x09
    with pytest.raises(FrameError) as excinfo:
        parse_frame(bytes(first) + bytes(ping))
    assert "interleaved" in str(excinfo.value)


def test_reassembly_still_honours_the_payload_ceiling():
    """Many small fragments must not add up past the single-frame limit."""
    big = b"x" * (MAX_PAYLOAD // 2 + 1)
    with pytest.raises(FrameError) as excinfo:
        parse_frame(_fragmented(big, big, big))
    assert "limit" in str(excinfo.value)


def test_reserved_bits_are_refused():
    """RSV bits mean an extension nobody negotiated."""
    frame = bytearray(build_frame(b"x", mask=True))
    frame[0] |= 0x40
    with pytest.raises(FrameError):
        parse_frame(bytes(frame))


def test_an_oversized_length_is_refused_before_allocating():
    """An unbounded length field is how a stray peer causes an OOM."""
    header = bytes([0x81, 127]) + struct.pack("!Q", MAX_PAYLOAD + 1)
    with pytest.raises(FrameError) as excinfo:
        parse_frame(header)
    assert "limit" in str(excinfo.value)


def test_building_an_unknown_opcode_is_refused():
    with pytest.raises(FrameError):
        build_frame(b"x", opcode=0x3)


# ---- control frames ---------------------------------------------------

@pytest.mark.parametrize("opcode", [OPCODE_CLOSE, OPCODE_PING, OPCODE_BINARY])
def test_control_and_binary_frames_survive_the_round_trip(opcode):
    frame = build_frame(b"\x03\xe8", opcode=opcode, mask=True)
    got_opcode, payload, _ = parse_frame(frame)
    assert got_opcode == opcode
    assert payload == b"\x03\xe8"


def test_a_json_command_survives_unicode():
    """Part descriptions from LCSC are routinely non-ASCII."""
    message = json.dumps({"cmd": "place", "note": "电容 100nF"})
    frame = build_frame(message.encode("utf-8"), mask=True)
    assert json.loads(parse_frame(frame)[1].decode("utf-8"))["note"] == \
        "电容 100nF"
