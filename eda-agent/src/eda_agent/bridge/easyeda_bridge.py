# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Talk to EasyEDA Pro through its extension API.

THE CONNECTION RUNS THE OTHER WAY from the Altium bridge. Altium polls a
directory for request files, so this process writes and waits. EasyEDA
Pro's extension API dials out instead (``SYS_WebSocket.register``), so
this process LISTENS and the editor connects to it. Nothing here can
start EasyEDA or make it connect; until the extension does, every call
reports the source as unreachable and says how to start it.

Requests are correlated by id, the same way the Altium bridge matches
``response_<id>.json`` to its request, so a slow reply cannot be
mistaken for the answer to a later question.

WHAT IS VERIFIED. The framing is RFC 6455 and is tested against the
specification's own worked example. The transport shape comes from
EasyEDA's published extension API. What has NOT been exercised is a live
editor: no part of this has round-tripped against EasyEDA Pro, which is
why ``verified_live`` is False and why the health report says so rather
than implying a working link.

LOOPBACK ONLY. This listens for one local editor. It is not hardened for
a hostile network and binds to 127.0.0.1 unless told otherwise.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import uuid
from typing import Any, Optional

from eda_agent.bridge.websocket import (
    OPCODE_CLOSE,
    OPCODE_PING,
    OPCODE_PONG,
    OPCODE_TEXT,
    FrameError,
    build_frame,
    handshake_response,
    parse_frame,
)

__all__ = [
    "EasyEdaBridge",
    "EasyEdaNotReachableError",
    "get_easyeda_bridge",
]

#: Loopback by default. An editor extension runs on the same machine, and
#: binding wider would expose a command channel that executes edits.
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8787

#: The range EasyEDA's own bridge server scans. Matching it means the
#: extension finds this server without a port being configured, and a
#: port already in use stops being a dead end.
PORT_RANGE_START = 49620
PORT_RANGE_END = 49629

#: How long a single command may take. Generous because a board-wide
#: query in a browser runtime is not fast, bounded because a hung editor
#: must not wedge the server.
_DEFAULT_TIMEOUT = 30.0


#: Returned by GET /health so a scanning client can tell this server
#: apart from whatever else happens to be on the port.
SERVICE_ID = "eda-agent-bridge"


class _HealthProbe(Exception):
    """Not a WebSocket peer. Answered and closed, not an error worth logging."""


class EasyEdaNotReachableError(RuntimeError):
    """No editor is connected, so the request was never delivered.

    Deliberately distinct from a command that ran and failed. "Nothing
    is listening" and "EasyEDA refused that edit" call for different
    responses, and collapsing them would send the user to debug the
    wrong end.
    """


def _host() -> str:
    return os.environ.get("EDA_AGENT_EASYEDA_HOST", "").strip() or _DEFAULT_HOST


def _port() -> int:
    raw = os.environ.get("EDA_AGENT_EASYEDA_PORT", "").strip()
    if not raw:
        return _DEFAULT_PORT
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_PORT


class EasyEdaBridge:
    """A WebSocket server that one EasyEDA extension connects to."""

    @property
    def verified_live(self) -> bool:
        """Has ANY command round-tripped against a live EasyEDA Pro?

        Read from the record the smoke script writes, never hardcoded.
        A constant here could only ever be an opinion, and this project
        has already been burned by published metadata that was derived
        rather than measured.

        False on a fresh checkout, and that is the correct answer.
        """
        from eda_agent.bridge.easyeda_verified import load_verified

        return any(load_verified()["commands"].values())

    def verified_live_for(self, command: str) -> bool:
        """Has THIS command round-tripped against a live editor?

        The global flag above answers "has anything ever worked", which
        after the first successful session is true forever and says
        nothing about the tool at hand. Twenty commands verified and
        forty-five not is a distinction worth keeping: a tool built on
        pcb.components has been seen working, one built on
        pcb.attributes has been seen hanging, and reporting the same
        flag for both launders the second with the first's evidence.
        """
        from eda_agent.bridge.easyeda_verified import is_verified

        return is_verified(command)

    def __init__(self) -> None:
        self._server: Optional[socket.socket] = None
        self._client: Optional[socket.socket] = None
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._connected_at: Optional[float] = None
        self._bound_port: Optional[int] = None
        # Every connected editor runtime, keyed by its socket.
        #
        # EasyEDA injects its API PER DOCUMENT TYPE: a PCB tab and a
        # schematic tab are separate runtimes, and pcb_* simply does
        # not exist in the schematic one. With a single connection the
        # second tab to connect evicted the first, so the sch-to-PCB
        # flow, which is the whole point of the tool, could never run
        # in one session. Altium reaches both from one connection, and
        # this is what closes that gap.
        #
        # The value is {buffer, context, at}. Each connection needs its
        # OWN frame buffer: they interleave on the wire, and a shared
        # buffer would hand one editor's half-frame to the other.
        self._conns: "dict[socket.socket, dict[str, Any]]" = {}

        #: Extension build id -> when this process first saw it. The id
        #: is a content hash and carries no ordering, so first-seen is
        #: what makes one build "newer" than another.
        self._build_first_seen: "dict[str, float]" = {}
        #: Builds retired because a newer one appeared. Reported rather
        #: than discarded: "your editor was running three builds" is the
        #: explanation for a fix that looked like it did not work.
        self._retired_builds: "set[str]" = set()

    # ---- lifecycle ---------------------------------------------------

    def start(self) -> dict[str, Any]:
        """Listen for the editor. Returns where it is listening."""
        if self._server is not None:
            return self.status()

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        # NOT SO_REUSEADDR on Windows. There it means something close to
        # the opposite of the Unix behaviour: it permits binding a port
        # another socket is ALREADY listening on, so two bridges both
        # "succeed" on the same port and then compete for connections.
        # Port scanning depends on a taken port failing to bind, so with
        # SO_REUSEADDR the scan can never move past the first candidate.
        #
        # SO_EXCLUSIVEADDRUSE is the Windows option that makes bind()
        # refuse when the port is in use. Elsewhere, SO_REUSEADDR keeps
        # its usual meaning of reclaiming a TIME_WAIT port, which is
        # what a restart needs.
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            server.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        else:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Try the range EasyEDA's own bridge uses, in order, unless a
        # port was named explicitly. The extension scans the same range
        # and identifies the server by its /health reply, so neither
        # side needs a port agreed by hand and a taken port is no longer
        # a dead end.
        candidates = ([_port()] if os.environ.get("EDA_AGENT_EASYEDA_PORT",
                                                 "").strip()
                      else list(range(PORT_RANGE_START, PORT_RANGE_END + 1))
                      + [_DEFAULT_PORT])
        bound = None
        for candidate in candidates:
            try:
                server.bind((_host(), candidate))
            except OSError:
                continue
            bound = candidate
            break

        if bound is None:
            server.close()
            raise EasyEdaNotReachableError(
                f"no free port for the EasyEDA bridge. Tried "
                f"{candidates[0]}-{candidates[-1]} on {_host()}. Set "
                f"EDA_AGENT_EASYEDA_PORT to a free one.")
        self._bound_port = bound
        server.listen(1)
        server.settimeout(0.5)
        self._server = server
        self._stop.clear()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        return self.status()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            for sock in (self._client, self._server):
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
            self._client = None
            self._server = None
            self._connected_at = None

    def _accept_loop(self) -> None:
        while not self._stop.is_set() and self._server is not None:
            try:
                conn, _ = self._server.accept()
            except (socket.timeout, OSError):
                continue
            try:
                self._handshake(conn)
            except _HealthProbe:
                # Discovery, not a peer. Close and keep listening.
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            except (FrameError, OSError):
                # A stray connection is not an error worth propagating
                # from a background thread; the editor will retry.
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            with self._lock:
                # Keep the earlier connection. It used to be closed
                # here, so opening a PCB tab silently killed the
                # schematic one and every later sch_* call failed with
                # "not connected" while a schematic sat open on screen.
                self._conns[conn] = {"buffer": bytearray(),
                                     "context": None,
                                     "context_at": 0.0,
                                     "at": time.time()}
                self._client = conn
                self._buffer = self._conns[conn]["buffer"]
                self._connected_at = time.time()
                self._evict_dead_locked()

    def _handshake(self, conn: socket.socket) -> None:
        conn.settimeout(5.0)
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = conn.recv(4096)
            if not chunk:
                raise FrameError("connection closed during handshake")
            raw += chunk
            if len(raw) > 16384:
                raise FrameError("handshake headers are implausibly large")

        head = raw.split(b"\r\n\r\n", 1)[0].decode("latin-1")
        lines = head.split("\r\n")
        request_line = lines[0] if lines else ""
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                name, _, value = line.partition(":")
                headers[name.strip()] = value.strip()

        # A plain GET /health, no Upgrade. This is how a client finds the
        # right server: EasyEDA's own bridge scans a port range and reads
        # a service identifier back, rather than having a port configured
        # by hand. Answering it means the extension can DISCOVER this
        # server and, just as importantly, retry until it appears.
        #
        # Without that, register() fails silently whenever nothing is
        # listening at the moment of the call and never tries again,
        # which is exactly how a correct extension and a correct server
        # can sit side by side and never meet.
        # The Upgrade header's VALUE is "websocket"; "upgrade" is what
        # the Connection header says. Testing the wrong one classified
        # every real handshake as a health probe.
        lowered = {k.lower(): v.lower() for k, v in headers.items()}
        if lowered.get("upgrade", "") != "websocket":
            if request_line.startswith("GET /health"):
                # The connected builds are published here, not only in
                # the diagnostics reply, because /health is the ONE
                # observation an extension instance can make without a
                # socket of its own.
                #
                # Measured: an instance reported "NOT connected,
                # attempts 0, retry timer NOT ARMED" while this server
                # showed the editor connected. Both were true. A
                # mid-session re-import loads a second copy of the
                # module, and the editor fires onStartupFinished only at
                # startup, so the new copy serves the menu while the
                # older copy keeps the socket. Module state can only
                # ever describe the copy you are asking, and the copy
                # you are asking is the one that did nothing.
                #
                # This server sees every copy, so it is the only place
                # the question has an answer.
                with self._lock:
                    builds = sorted(
                        {str(info["build"]) for info in self._conns.values()
                         if info.get("build")})
                body = json.dumps({
                    "service": SERVICE_ID,
                    "status": "ok",
                    "editor_connected": self.connected,
                    "editor_builds": builds,
                }).encode("utf-8")
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Access-Control-Allow-Origin: *\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
                    + body)
                raise _HealthProbe()
            conn.sendall(b"HTTP/1.1 404 Not Found\r\n"
                         b"Content-Length: 0\r\n\r\n")
            raise _HealthProbe()

        conn.sendall(handshake_response(headers))

    # ---- state -------------------------------------------------------

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._client is not None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "listening": self._server is not None,
                "host": _host(),
                # The port actually bound, which is not the
                # requested one when the range was scanned.
                "port": self._bound_port or _port(),
                "editor_connected": self._client is not None,
                # One editor runtime per open document type. Reporting
                # only a boolean hid the case this bridge now handles:
                # with a PCB and a schematic both connected, "connected:
                # true" said nothing about whether the schematic was
                # among them, and a sch_* failure looked like a bug in
                # the tool rather than a tab nobody had opened.
                "editors_connected": len(self._conns),
                # "unidentified" was read as a failed probe, and with a
                # single connection nothing is ever probed: routing only
                # learns contexts when there is a choice to make. Saying
                # "not probed" keeps a design decision from looking like
                # a broken editor, and it stopped a reconnection test
                # from drawing a conclusion the data did not support.
                "editor_contexts": sorted(
                    str(info.get("context")
                        or ("not probed (single connection)"
                            if len(self._conns) == 1 else "unidentified"))
                    for info in self._conns.values()),
                # WHICH BUILD IS ANSWERING. Re-importing the extension
                # leaves the previous instance running with its socket
                # open, so an editor can hold several builds at once and
                # a command lands on whichever is picked. Reporting the
                # set is what turns "the fix did not work" into "you are
                # talking to the old one".
                "editor_builds": sorted(
                    {str(info["build"]) for info in self._conns.values()
                     if info.get("build")}),
                "builds_retired": sorted(self._retired_builds),
                "connected_seconds": (
                    round(time.time() - self._connected_at, 1)
                    if self._connected_at else None),
                # Stated, not implied. The transport can be up and the
                # command vocabulary still unproven against a live app.
                "verified_live": self.verified_live,
            }

    # ---- commands ----------------------------------------------------

    def send_editor_command(self, command: str, params: Optional[dict] = None,
                     timeout: float = _DEFAULT_TIMEOUT) -> dict[str, Any]:
        """Run one command in the editor and return its reply."""
        # Route to the runtime that owns this namespace. Only when more
        # than one editor is connected: with a single connection there
        # is nothing to choose, and identifying it would spend a round
        # trip to reach the same socket.
        namespace = command.split(".", 1)[0]
        if len(self._conns) > 1 and namespace in self._NAMESPACE_CONTEXT:
            self._learn_contexts()
            with self._lock:
                chosen = self._select_locked(command)
                if chosen is not None:
                    self._activate_locked(chosen)

        with self._lock:
            client = self._client
        if client is None:
            raise EasyEdaNotReachableError(
                # The port actually BOUND, never the requested one. With
                # scanning they differ routinely, and this message is
                # what someone reads when nothing connects: naming the
                # wrong port sends them to check a socket that was never
                # opened.
                f"no EasyEDA editor is connected. This server listens on "
                f"{_host()}:{self._bound_port or _port()} and the editor "
                f"dials out to it, so "
                f"start the eda-agent extension in EasyEDA Pro "
                f"(Settings > Extensions) and point it here. If the "
                f"extension reports that external interaction for "
                f"extensions and standalone scripts is not permitted, "
                f"enable that permission in EasyEDA first: until it is "
                f"on, the editor never attempts a socket and nothing on "
                f"this side can see the difference from an editor that "
                f"is simply closed. Nothing was "
                f"sent, so this is not evidence the command would fail.")

        request_id = uuid.uuid4().hex
        message = json.dumps({
            "id": request_id, "command": command, "params": params or {},
        }).encode("utf-8")

        try:
            client.sendall(build_frame(message, opcode=OPCODE_TEXT))
        except OSError as exc:
            self._drop_client()
            raise EasyEdaNotReachableError(
                f"the editor connection dropped while sending: {exc}"
            ) from exc

        return self._await_reply(request_id, timeout, client)

    def _await_reply(self, request_id: str, timeout: float,
                     client: "Optional[socket.socket]" = None
                     ) -> dict[str, Any]:
        """Wait for one reply on the connection the request went out on.

        The socket is passed in rather than re-read from self. Routing
        can rebind the active connection between the send and the
        reply, and re-reading would then wait on the OTHER editor's
        socket and consume its frames: a schematic command could eat a
        PCB command's answer. With one connection the two were always
        the same object, which is why nothing here needed to say so
        before.
        """
        deadline = time.time() + timeout
        if client is None:
            with self._lock:
                client = self._client
        while time.time() < deadline:
            if client is None or client.fileno() < 0:
                raise EasyEdaNotReachableError(
                    "the editor disconnected before replying")

            frame = self._next_frame(client, deadline)
            if frame is None:
                continue
            opcode, payload = frame

            if opcode == OPCODE_CLOSE:
                self._drop_client()
                raise EasyEdaNotReachableError("the editor closed the link")
            if opcode == OPCODE_PING:
                try:
                    client.sendall(build_frame(payload, opcode=OPCODE_PONG))
                except OSError:
                    self._drop_client()
                continue
            if opcode != OPCODE_TEXT:
                continue

            try:
                reply = json.loads(payload.decode("utf-8", "replace"))
            except ValueError:
                continue
            # Ignore replies to earlier requests rather than returning
            # one as the answer to this question.
            if isinstance(reply, dict) and reply.get("id") == request_id:
                return reply

        raise EasyEdaNotReachableError(
            f"no reply within {timeout}s. The editor is connected but did "
            f"not answer, which usually means the extension raised.")

    def _next_frame(self, client: socket.socket,
                    deadline: float) -> Optional[tuple[int, bytes]]:
        # This connection's OWN buffer, looked up by socket rather than
        # taken from self._buffer. Two editors interleave on the wire,
        # and a buffer that belongs to whichever connection was
        # activated last would hand one editor's half-frame to the
        # other: the frame parser would then read a length prefix from
        # the middle of somebody else's message.
        buffer = self._buffer_for(client)
        parsed = parse_frame(bytes(buffer))
        if parsed is not None:
            opcode, payload, consumed = parsed
            del buffer[:consumed]
            return opcode, payload

        client.settimeout(max(0.05, min(1.0, deadline - time.time())))
        try:
            chunk = client.recv(65536)
        except socket.timeout:
            return None
        except OSError as exc:
            self._drop_client()
            raise EasyEdaNotReachableError(
                f"the editor connection dropped: {exc}") from exc
        if not chunk:
            self._drop_client()
            raise EasyEdaNotReachableError("the editor closed the link")
        buffer.extend(chunk)
        return None

    def _buffer_for(self, client: socket.socket) -> bytearray:
        """The frame buffer belonging to one connection.

        Falls back to the shared buffer for a socket the pool has never
        seen, which keeps the single-connection path working even if a
        caller reaches _next_frame with a socket acquired some other
        way.
        """
        with self._lock:
            info = self._conns.get(client)
            if info is not None:
                return info["buffer"]
            return self._buffer

    def _note_build_locked(self, sock, build: str) -> None:
        """Record which extension build a socket is running, and retire
        the superseded ones. Caller holds the lock.

        A SUPERSEDED INSTANCE KEEPS ANSWERING. EasyEDA does not tear the
        old extension down on re-import: the previous instance keeps
        running and keeps its socket open, so one editor process held
        SEVEN connections across three builds at once. They cannot be
        told apart by document context, because every one of them says
        "schematic".

        Preferring the newest CONNECTION does not fix it either. Every
        instance reattaches on its own timer, so a stale one becomes the
        most recent connection a few seconds later, and which build
        answers a given command is then a coin toss. That is how a fix
        verified against one build was measured as absent minutes later,
        and it is worse than confusing: a write can be executed by the
        build whose bug it was fixing.

        So the build itself decides. The newest build observed wins, and
        connections on any other are dropped once a newer one is known.
        A build is "newer" by when it was FIRST SEEN here, since the id
        is a content hash and carries no ordering of its own.
        """
        info = self._conns.get(sock)
        if info is None:
            return
        info["build"] = build
        self._build_first_seen.setdefault(build, time.time())

        newest = max(self._build_first_seen.items(), key=lambda kv: kv[1])[0]
        if build == newest and len(self._build_first_seen) > 1:
            # This socket is on the current build, so anything on an
            # older one is a leftover instance. Never drop the last
            # connection: an old build answering is better than none.
            for other, other_info in list(self._conns.items()):
                if other is sock or len(self._conns) <= 1:
                    continue
                if other_info.get("build") and other_info["build"] != newest:
                    self._retired_builds.add(other_info["build"])
                    self._conns.pop(other, None)
                    try:
                        other.close()
                    except OSError:
                        pass
        elif build != newest:
            # Learned about a stale instance. Leave it in place if it is
            # all there is; the block above retires it as soon as the
            # current build is seen again.
            info["superseded_by"] = newest

    def _evict_dead_locked(self) -> None:
        """Forget sockets that are closed. Caller holds the lock.

        A tab the user closed leaves a dead socket behind, and routing
        to it would refuse a command the OTHER editor could have run.
        """
        for sock in [s for s in self._conns if s.fileno() < 0]:
            self._conns.pop(sock, None)

    #: Which document runtime a command namespace needs. Namespaces not
    #: listed here exist in every runtime (lib, proj, sys, system, dmt,
    #: editor), so they run on whichever editor is connected and are
    #: never worth a second round trip to place.
    _NAMESPACE_CONTEXT = {"pcb": "pcb", "sch": "schematic"}

    #: Commands whose namespace does not say which runtime they need.
    #:
    #: The namespaces above were once thought to be the whole story, and
    #: export and design were listed as running anywhere. They do not:
    #: every command here reaches a pcb_* or sch_* class, so routing one
    #: to the other runtime sends it somewhere it cannot work while a
    #: connection that could have run it sits idle.
    #:
    #: Derived from the class family each handler actually touches, and
    #: kept in step with the same table in the extension.
    _COMMAND_CONTEXT = {
        "design.snapshot": "pcb",
        "design.run_drc": "pcb",
        "design.run_erc": "schematic",
        "export.bom": "pcb",
        "export.dxf": "pcb",
        "export.model_3d": "pcb",
        "export.gerber": "pcb",
        "export.ipc2581": "pcb",
        "export.ipcd356": "pcb",
        "export.netlist": "pcb",
        "export.altium": "pcb",
        "export.pdf": "pcb",
        "export.pick_and_place": "pcb",
        "export.test_points": "pcb",
        "export.flying_probe": "pcb",
        "export.dsn": "pcb",
        "export.pads": "pcb",
        "export.pcb_info": "pcb",
        "export.schematic_document": "schematic",
        "export.schematic_netlist": "schematic",
        "export.sch_bom": "schematic",
        "export.simulation_netlist": "schematic",
    }

    def _select_locked(self, command: str) -> Optional[socket.socket]:
        """Pick the connection that can actually run this command.

        Falls back to the most recent connection rather than refusing:
        a wrong guess produces the editor's own error, while refusing
        would fail a command that would have worked on a single
        connection. This must never be worse than one connection was.
        """
        namespace = command.split(".", 1)[0]
        wanted = (self._COMMAND_CONTEXT.get(command)
                  or self._NAMESPACE_CONTEXT.get(namespace))
        if wanted is not None:
            for sock, info in sorted(self._conns.items(),
                                     key=lambda kv: kv[1]["at"],
                                     reverse=True):
                if info.get("context") == wanted:
                    return sock
        if self._client in self._conns:
            return self._client
        newest = sorted(self._conns.items(), key=lambda kv: kv[1]["at"],
                        reverse=True)
        return newest[0][0] if newest else None

    def _activate_locked(self, sock: socket.socket) -> None:
        self._client = sock
        info = self._conns.get(sock)
        if info is not None:
            self._buffer = info["buffer"]
            self._connected_at = info["at"]

    #: How long a learned document context is trusted, in seconds.
    #:
    #: Caching it forever was the first design, justified by "EasyEDA
    #: gives each document runtime its own extension host, so the answer
    #: cannot change without the socket being replaced". That is an
    #: assumption, not a measurement, and the extension reads the
    #: context with getCurrentPcbInfo / getCurrentSchematicInfo, whose
    #: names say they report the ACTIVE tab rather than a fixed identity
    #: of the connection. If one socket does serve whatever tab is in
    #: front, a cached context goes stale the moment somebody clicks
    #: another tab, and routing then sends pcb.* to a connection now
    #: showing a schematic: a wrong answer that looks like a right one.
    #:
    #: Re-asking costs one ping. Being wrong costs a command executed
    #: against the wrong document, so it is re-asked until somebody has
    #: measured which way EasyEDA actually behaves.
    _CONTEXT_TTL_SECONDS = 30.0

    def _learn_contexts(self) -> None:
        """Ask each connection which document it is, if we do not know.

        Lazily, and again once the last answer is older than the TTL.
        """
        with self._lock:
            self._evict_dead_locked()
            now = time.time()
            unknown = [
                s for s, i in self._conns.items()
                if i.get("context") is None
                or now - i.get("context_at", 0.0) > self._CONTEXT_TTL_SECONDS
            ]
        for sock in unknown:
            with self._lock:
                if sock not in self._conns:
                    continue
                self._activate_locked(sock)
            try:
                reply = self.send_editor_command("system.ping", timeout=8.0)
            except Exception:                          # noqa: BLE001
                # A connection that cannot answer a ping is not usable
                # for routing, and nothing else will ever notice.
                # `fileno() < 0` only becomes true once this side calls
                # close(), so a socket whose peer vanished stays in the
                # table forever: it was reported as an unidentified
                # editor, cost the full ping timeout on every context
                # refresh, and could be picked as the fallback target.
                #
                # A MISSED PING IS NOT PROOF OF DEATH, and this has to
                # fail toward keeping the connection. The bridge
                # serialises calls, so a context probe competing with a
                # long read times out while the editor is perfectly
                # healthy. Evicting on that drops a working editor and
                # the user sees tools refuse for no reason.
                #
                # So: three strikes, and NEVER the last connection. An
                # editor that cannot be pinged is still better than no
                # editor at all, and if it really is gone the next
                # accept replaces it anyway. Measured going the other
                # way first, where two strikes took a live schematic
                # out of the table.
                with self._lock:
                    info = self._conns.get(sock)
                    if info is not None:
                        info["ping_misses"] = info.get("ping_misses", 0) + 1
                        if info["ping_misses"] >= 3 and len(self._conns) > 1:
                            self._conns.pop(sock, None)
                            try:
                                sock.close()
                            except OSError:
                                pass
                continue
            result = reply.get("result") or {}
            document = str(result.get("document") or "")
            build = str(result.get("build") or "")
            with self._lock:
                if sock in self._conns:
                    self._conns[sock]["context"] = document or "unknown"
                    self._conns[sock]["context_at"] = time.time()
                    self._conns[sock]["ping_misses"] = 0
                    # Which BUILD answered. Re-importing the extension
                    # leaves the previous instance running with its
                    # socket open, so the editor can hold several at
                    # once, and they are indistinguishable by document
                    # context because they all report the same one.
                    if build:
                        self._note_build_locked(sock, build)

    def _drop_client(self) -> None:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                except OSError:
                    pass
                self._conns.pop(self._client, None)
            self._client = None
            self._connected_at = None
            self._buffer = bytearray()
            # Fall back to another live editor rather than reporting
            # nothing connected while one is still open.
            self._evict_dead_locked()
            remaining = sorted(self._conns.items(),
                               key=lambda kv: kv[1]["at"], reverse=True)
            if remaining:
                self._activate_locked(remaining[0][0])

    def ping(self) -> dict[str, Any]:
        """Liveness, reported honestly when nothing is connected."""
        if not self.connected:
            raise EasyEdaNotReachableError(
                f"no EasyEDA editor connected on {_host()}:{_port()}")
        reply = self.send_editor_command("system.ping", timeout=5.0)
        return {"success": True, "editor": reply.get("result", {}),
                "verified_live": self.verified_live}


_BRIDGE: Optional[EasyEdaBridge] = None


def get_easyeda_bridge() -> EasyEdaBridge:
    """The process-wide bridge, LISTENING by the time it is returned.

    Starting it here is the whole point. EasyEDA dials out, so nothing
    can connect until this process is listening, and the accessor is the
    only place that knows a bridge is about to be used. Creating one
    without starting it produced a backend that could never work: the
    server never listened, the extension had nothing to discover, and
    every tool reported "no editor connected" forever. Both halves can
    be perfect and never meet.

    Every test starts the bridge explicitly, which is exactly why none
    of them could see this.

    A failure to bind is not raised. The tools report unreachability as
    data, and a server that cannot listen is a reason the editor is
    unreachable rather than a reason to fail an unrelated call.
    """
    global _BRIDGE
    if _BRIDGE is None:
        _BRIDGE = EasyEdaBridge()
    if not _BRIDGE.status()["listening"]:
        try:
            _BRIDGE.start()
        except EasyEdaNotReachableError:
            pass
    return _BRIDGE
