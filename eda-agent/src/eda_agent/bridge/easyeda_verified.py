# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Which EasyEDA commands have actually round-tripped against a live editor.

``verified_live`` started as a constant False, which was honest but
useless: it could only ever say "nothing here is proven", and flipping
it by hand would turn a measurement into an opinion. This project has
been bitten by exactly that before, when published tool maturity was
DERIVED rather than measured and advertised 121 tools as simulator
tested that the simulator rejects.

So verification is recorded per command, by the smoke script, from a
real editor. Nothing else writes this file. A command absent from it is
unverified, and absence is the default rather than something to argue
about.

The record is deliberately NOT committed. It describes one machine's
one session against one version of EasyEDA, and shipping it would
present someone else's measurement as this user's.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

__all__ = ["load_verified", "record_verified", "sample_of", "shape_of",
           "verified_path"]

#: path -> (mtime_ns, parsed record). See load_verified.
_CACHE: dict = {}


def verified_path() -> Path:
    """Where the record lives. Overridable so tests never touch the real one."""
    configured = os.environ.get("EDA_AGENT_EASYEDA_VERIFIED", "").strip()
    if configured:
        return Path(configured)
    return (Path(__file__).resolve().parents[3]
            / "extensions" / "easyeda" / "verified.json")


def load_verified() -> dict[str, Any]:
    """The recorded verification, or an empty record.

    An unreadable or corrupt file reads as "nothing verified" rather
    than raising. The consequence of getting this wrong is a claim that
    something works, so the failure direction has to be toward the
    modest answer.
    """
    path = verified_path()
    try:
        # Cached on the file's mtime: _call consults this on every
        # command, and rereading a JSON file per tool call is waste the
        # moment two calls happen in one session. A new smoke run
        # changes the mtime and drops the cache.
        stat = path.stat()
        cached = _CACHE.get(str(path))
        if cached is not None and cached[0] == stat.st_mtime_ns:
            return cached[1]
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"commands": {}, "editor": None, "recorded_at": None,
                "shapes": {}, "samples": {}}
    if not isinstance(data, dict) or not isinstance(
            data.get("commands"), dict):
        return {"commands": {}, "editor": None, "recorded_at": None}
    # Records written before shapes were captured have no such key, and
    # a missing shape has to read as "not measured" rather than raise.
    if not isinstance(data.get("shapes"), dict):
        data["shapes"] = {}
    if not isinstance(data.get("samples"), dict):
        data["samples"] = {}
    _CACHE[str(path)] = (stat.st_mtime_ns, data)
    return data


def record_verified(commands: dict[str, bool], editor: Optional[str],
                    recorded_at: str,
                    shapes: "Optional[dict[str, str]]" = None,
                    samples: "Optional[dict[str, str]]" = None) -> Path:
    """Write the record. Only a live harness may call this.

    Two do: ``easyeda_smoke.py`` and the shape-harvest half of
    ``easyeda_tool_sweep.py``. Both issue raw commands to a real editor
    and read the raw replies. Nothing that INFERS a command's outcome
    from something else belongs here, which is why the sweep records
    its harvest and not its tool verdicts: a tool can fan out to several
    commands, or refuse on its own arguments before sending anything.

    ``commands`` maps a command name to whether it returned usable data.
    A command that answered but came back EMPTY is False: on a loaded
    board an empty result means the response shape was misread, which is
    the failure this whole exercise looks for and must never be filed as
    a success.

    ``shapes`` maps a command to the FIELD NAMES its result carried.
    Nothing offline can establish those: the published API reference
    lists methods, not the shape of what they return, so a tool written
    against a guessed key reads nothing and reports a clean empty
    result. A live session is the only place the answer exists, and
    printing it to a terminal loses it as soon as the buffer scrolls.
    """
    path = verified_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # MERGE, never replace. A run can only probe the document context
    # that is open: with a PCB tab the sch.* probes are set aside by
    # name, and with a schematic tab the pcb.* ones are. Writing the
    # payload flat meant the second connection ERASED the first, which
    # is exactly what happened: a schematic run wiped 20 PCB shapes, 20
    # samples and every pcb.* verified flag, so tools measured in an
    # earlier session went back to reporting unverified.
    #
    # A command this run did NOT probe keeps whatever was established
    # before. A command it DID probe takes the new answer, including a
    # newly failing one, because the editor really can change.
    previous = load_verified()
    merged_commands = dict(previous.get("commands") or {})
    merged_commands.update({k: bool(v) for k, v in commands.items()})
    merged_shapes = dict(previous.get("shapes") or {})
    merged_shapes.update({k: str(v) for k, v in (shapes or {}).items()})
    merged_samples = dict(previous.get("samples") or {})
    merged_samples.update({k: str(v) for k, v in (samples or {}).items()})

    payload = {
        "commands": dict(sorted(merged_commands.items())),
        "shapes": dict(sorted(merged_shapes.items())),
        # One truncated example item per command. The shapes give the
        # KEY names; the next tranche of audits was blocked one level
        # deeper, on value FORMATS (is a rule value a number or an
        # object, is tenting a flag or a sign), and only an example
        # answers that.
        "samples": dict(sorted(merged_samples.items())),
        "editor": editor,
        "recorded_at": recorded_at,
        "note": ("Written by scripts/easyeda_smoke.py against a live "
                 "EasyEDA Pro. Not committed: it describes one machine's "
                 "session, not a property of this project."),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def is_verified(command: str) -> bool:
    """Has this exact command returned usable data from a real editor?"""
    return bool(load_verified()["commands"].get(command))


def verified_summary() -> dict[str, Any]:
    """Counts for a status report, without asserting anything untrue."""
    record = load_verified()
    commands = record["commands"]
    return {
        "verified_commands": sorted(k for k, v in commands.items() if v),
        "verified_count": sum(1 for v in commands.values() if v),
        "recorded_at": record.get("recorded_at"),
        "editor": record.get("editor"),
    }


def shape_of(command: str) -> str:
    """The field names this command's result carried, when measured.

    Empty when no live session has recorded it. That is the honest
    answer: an audit written against a field nobody has seen is a guess,
    and this is how to tell the two apart.
    """
    return str(load_verified().get("shapes", {}).get(command, ""))


def sample_of(command: str) -> str:
    """A truncated example of this command's reply item, when measured.

    Empty when no live session has recorded one, which is the honest
    answer for the same reason shape_of gives it.
    """
    return str(load_verified().get("samples", {}).get(command, ""))

