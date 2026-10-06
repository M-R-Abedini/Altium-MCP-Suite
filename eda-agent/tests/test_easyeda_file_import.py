# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Sending a file INTO the editor, and refusing the wrong one.

``importAutoRouteSesFile``, ``importAutoRouteJsonFile`` and
``importAutoLayoutJsonFile`` take a File. The bridge carries JSON, so
the file crosses as base64 and the extension rebuilds it. That is the
mirror of ``packedFile``, which has carried files the other way for a
long time; the inbound half was recorded as missing plumbing and is
about twenty lines.

THE EXTENSION MATTERS AND HAS NO SYMPTOM. The editor identifies the
format from the filename, so a JSON sent as a session file is accepted
and produces a board nobody asked for. Both sides check the extension
against the kind, and these tests pin that, because the failure it
prevents is invisible.

The Python half is tested here. The extension half is exercised by
``test_easyeda_marker_shapes``-style extraction elsewhere; what matters
on this side is that a bad request never reaches the bridge at all.
"""

from __future__ import annotations

import asyncio
import base64

import pytest

import eda_agent.tools.easyeda as ez


class _FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


@pytest.fixture
def call_import(monkeypatch):
    """The registered tool, with the bridge replaced by a recorder."""
    sent = {}

    def fake_call(command, params=None, timeout=None):
        sent["command"] = command
        sent["params"] = params
        return {"ok": True, "imported": True}

    monkeypatch.setattr(ez, "_call", fake_call)
    mcp = _FakeMcp()
    ez.register_easyeda_tools(mcp)
    fn = mcp.tools["easyeda_import_routing"]

    def run(**kwargs):
        sent.clear()
        return asyncio.run(fn(**kwargs)), sent

    return run


@pytest.fixture
def ses_file(tmp_path):
    path = tmp_path / "routed.ses"
    path.write_bytes(b"(session routed.ses)")
    return path


def test_a_valid_session_file_is_sent_as_base64(call_import, ses_file):
    out, sent = call_import(kind="ses", path=str(ses_file), confirm=True)
    assert sent["command"] == "pcb.import_routing"
    payload = sent["params"]["file"]
    assert payload["name"] == "routed.ses"
    assert base64.b64decode(payload["base64"]) == b"(session routed.ses)"


def test_the_extension_must_match_the_kind(call_import, tmp_path):
    """The failure with no symptom.

    A JSON sent as a session file is accepted by the editor and
    produces a board nobody asked for, so it is refused here rather
    than passed on.
    """
    wrong = tmp_path / "routed.json"
    wrong.write_bytes(b"{}")
    out, sent = call_import(kind="ses", path=str(wrong), confirm=True)
    assert out["ok"] is False
    assert ".ses" in out["reason"]
    assert not sent, "a mismatched file reached the bridge"


def test_nothing_happens_without_confirmation(call_import, ses_file):
    out, sent = call_import(kind="ses", path=str(ses_file))
    assert out["ok"] is False
    assert "confirm=True" in out["reason"]
    assert not sent, "an unconfirmed import reached the bridge"


def test_the_layout_kind_warns_about_placement_not_routing(
        call_import, tmp_path):
    """The three kinds destroy different things, and the refusal says
    which."""
    layout = tmp_path / "placed.json"
    layout.write_bytes(b"{}")
    out, _ = call_import(kind="layout_json", path=str(layout))
    assert "PLACEMENT" in out["reason"]

    routing = tmp_path / "routed.json"
    routing.write_bytes(b"{}")
    out, _ = call_import(kind="route_json", path=str(routing))
    assert "routing" in out["reason"]


@pytest.mark.parametrize("kind", ["", "dsn", "ses_file", "SES"])
def test_an_unknown_kind_is_refused_and_lists_the_valid_ones(
        call_import, kind, ses_file):
    out, sent = call_import(kind=kind, path=str(ses_file), confirm=True)
    assert out["ok"] is False
    for valid in ("ses", "route_json", "layout_json"):
        assert valid in out["reason"]
    assert not sent


def test_a_missing_file_is_reported_before_anything_is_sent(call_import):
    out, sent = call_import(kind="ses", path="nowhere.ses", confirm=True)
    assert out["ok"] is False
    assert "no file at" in out["reason"]
    assert not sent


def test_the_path_is_required(call_import):
    out, sent = call_import(kind="ses", confirm=True)
    assert out["ok"] is False
    assert not sent


def test_binary_content_survives_the_encoding(call_import, tmp_path):
    """A session file is not necessarily text, and base64 must not
    mangle it."""
    raw = bytes(range(256))
    path = tmp_path / "binary.ses"
    path.write_bytes(raw)
    _out, sent = call_import(kind="ses", path=str(path), confirm=True)
    assert base64.b64decode(sent["params"]["file"]["base64"]) == raw
