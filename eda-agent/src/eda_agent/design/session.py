# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Design session journal: the autonomy-harness backbone (roadmap 1.4).

An autonomous spec-to-board run spans many tool calls and often outlives a
single MCP client context. The journal is the durable memory that lets any
client, after a context compaction, a restart, or a model switch, pick up
exactly where the last one stopped, and lets the (forthcoming) state machine
decide the next action from recorded fact rather than chat history.

Design: an append-only JSONL file per session. Every event is one line, so a
crash mid-write loses at most the last record and never corrupts earlier
history. Current state is *derived* by replaying events, never stored
mutably: the log is the single source of truth. This module is pure Python
and Altium-agnostic; the MCP tool layer wraps it as
``design_session_*`` tools.

The canonical 13-stage pipeline (``STAGES``) is shared here so the journal
and the future ``design_next_action`` state machine reference one list.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# The 13-stage spec->board pipeline (see the roadmap's grade table). Order is
# load-bearing: the state machine walks it front to back.
STAGES = (
    "requirement",
    "architecture",
    "plan",
    "plan_verification",
    "library_readiness",
    "schematic_emit",
    "sch_to_pcb",
    "rules_stackup",
    "placement",
    "routing",
    "pours_tuning",
    "verification",
    "outputs",
)

# Terminal stage outcomes.
STATUS_OK = "ok"
STATUS_BLOCKED = "blocked"
STATUS_FAILED = "failed"

# Event kinds.
KIND_SESSION_START = "session_start"
KIND_STAGE_ENTER = "stage_enter"
KIND_STAGE_RESULT = "stage_result"
KIND_PLAN_REVISION = "plan_revision"
KIND_ARTIFACT = "artifact"
KIND_BLOCKED = "blocked"
KIND_RESOLVED = "resolved"
KIND_NOTE = "note"


@dataclass
class JournalEvent:
    seq: int
    ts: str
    kind: str
    payload: dict = field(default_factory=dict)


@dataclass
class SessionState:
    session_id: str
    requirement: str
    created: str
    event_count: int
    stage_status: dict          # stage -> "ok"|"blocked"|"failed"
    current_stage: Optional[str]
    next_stage: Optional[str]
    plan_revision: int
    open_question: Optional[str]
    artifacts: list
    complete: bool
    attempts: dict = field(default_factory=dict)  # stage -> stage_result count


class SessionJournal:
    """Append-only JSONL journal for one design session."""

    def __init__(self, path: Path, session_id: str):
        self.path = Path(path)
        self.session_id = session_id

    # --- writing ---------------------------------------------------------
    def _append(self, kind: str, payload: dict, now: Optional[datetime] = None) -> JournalEvent:
        seq = self._next_seq()
        ts = (now or datetime.now()).isoformat(timespec="seconds")
        ev = JournalEvent(seq=seq, ts=ts, kind=kind, payload=payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(ev)) + "\n")
        return ev

    def _next_seq(self) -> int:
        return sum(1 for _ in self._raw_lines()) + 1

    def start(self, requirement: str, *, now: Optional[datetime] = None) -> JournalEvent:
        return self._append(
            KIND_SESSION_START,
            {"session_id": self.session_id, "requirement": requirement},
            now=now,
        )

    def enter_stage(self, stage: str, *, now=None) -> JournalEvent:
        _check_stage(stage)
        return self._append(KIND_STAGE_ENTER, {"stage": stage}, now=now)

    def stage_result(self, stage: str, status: str, *, verdict: str = "",
                     data: Optional[dict] = None, now=None) -> JournalEvent:
        _check_stage(stage)
        if status not in (STATUS_OK, STATUS_BLOCKED, STATUS_FAILED):
            raise ValueError(f"invalid stage status: {status}")
        return self._append(
            KIND_STAGE_RESULT,
            {"stage": stage, "status": status, "verdict": verdict, "data": data or {}},
            now=now,
        )

    def plan_revision(self, revision: int, *, summary: str = "", plan_hash: str = "",
                      now=None) -> JournalEvent:
        return self._append(
            KIND_PLAN_REVISION,
            {"revision": revision, "summary": summary, "plan_hash": plan_hash},
            now=now,
        )

    def artifact(self, path: str, kind: str = "", *, now=None) -> JournalEvent:
        return self._append(KIND_ARTIFACT, {"path": path, "kind": kind}, now=now)

    def blocked(self, question: str, *, stage: str = "", now=None) -> JournalEvent:
        return self._append(KIND_BLOCKED, {"question": question, "stage": stage}, now=now)

    def resolved(self, answer: str = "", *, now=None) -> JournalEvent:
        return self._append(KIND_RESOLVED, {"answer": answer}, now=now)

    def note(self, text: str, *, now=None) -> JournalEvent:
        return self._append(KIND_NOTE, {"text": text}, now=now)

    # --- reading ---------------------------------------------------------
    def _raw_lines(self):
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(line)
        return out

    def events(self) -> list[JournalEvent]:
        evs = []
        for line in self._raw_lines():
            try:
                d = json.loads(line)
                evs.append(JournalEvent(seq=d["seq"], ts=d["ts"], kind=d["kind"],
                                        payload=d.get("payload", {})))
            except (json.JSONDecodeError, KeyError):
                continue  # tolerate a torn trailing line
        return evs

    def state(self) -> SessionState:
        return _replay(self.session_id, self.events())


def _check_stage(stage: str) -> None:
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; must be one of {STAGES}")


def _replay(session_id: str, events: list[JournalEvent]) -> SessionState:
    requirement = ""
    created = ""
    stage_status: dict[str, str] = {}
    attempts: dict[str, int] = {}
    current_stage = None
    plan_revision = 0
    open_question = None
    artifacts: list[dict] = []

    for ev in events:
        p = ev.payload
        if ev.kind == KIND_SESSION_START:
            requirement = p.get("requirement", "")
            created = created or ev.ts
        elif ev.kind == KIND_STAGE_ENTER:
            current_stage = p.get("stage")
        elif ev.kind == KIND_STAGE_RESULT:
            stage = p.get("stage")
            stage_status[stage] = p.get("status")
            attempts[stage] = attempts.get(stage, 0) + 1
            if p.get("status") == STATUS_OK and current_stage == stage:
                current_stage = None
        elif ev.kind == KIND_PLAN_REVISION:
            plan_revision = p.get("revision", plan_revision)
        elif ev.kind == KIND_ARTIFACT:
            artifacts.append({"path": p.get("path"), "kind": p.get("kind", "")})
        elif ev.kind == KIND_BLOCKED:
            open_question = p.get("question")
        elif ev.kind == KIND_RESOLVED:
            open_question = None

    # next_stage: first pipeline stage not yet marked ok.
    next_stage = None
    for st in STAGES:
        if stage_status.get(st) != STATUS_OK:
            next_stage = st
            break
    complete = next_stage is None

    return SessionState(
        session_id=session_id,
        requirement=requirement,
        created=created,
        event_count=len(events),
        stage_status=stage_status,
        current_stage=current_stage,
        next_stage=next_stage,
        plan_revision=plan_revision,
        open_question=open_question,
        artifacts=artifacts,
        complete=complete,
        attempts=attempts,
    )


class SessionStore:
    """Manages design-session journals under a root directory.

    One ``<session_id>.jsonl`` per session; the newest-modified session is
    the implicit active one when a tool is called without an id.
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.jsonl"

    def start(self, requirement: str, session_id: Optional[str] = None,
              *, now: Optional[datetime] = None) -> SessionJournal:
        now = now or datetime.now()
        sid = session_id or now.strftime("%Y%m%d-%H%M%S")
        journal = SessionJournal(self._path(sid), sid)
        journal.start(requirement, now=now)
        return journal

    def get(self, session_id: str) -> SessionJournal:
        return SessionJournal(self._path(session_id), session_id)

    def list(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted((p.stem for p in self.root.glob("*.jsonl")), reverse=True)

    def active(self) -> Optional[SessionJournal]:
        if not self.root.is_dir():
            return None
        files = sorted(self.root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime,
                       reverse=True)
        if not files:
            return None
        return SessionJournal(files[0], files[0].stem)
