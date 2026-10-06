# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A review that read nothing must not read as a clean design.

Each backend has a one-call review that fans out to many checks and
folds the answers into a summary. That summary is the thing a reviewer
reads, and it is the one place where "I could not look" and "I looked
and it was fine" collapse into the same words if nobody is careful.

They collapsed. ``easyeda_review_board`` answered "23 audits run, 0
violations" with no editor connected, because audits_run reported the
size of the audit registry, which is a fact about the code rather than
a measurement of the board. The refusals were listed correctly further
down, so the detail was right and the headline said the opposite.

The other two were checked because of it, which is the point of writing
this once for all three: ``kicad_full_review`` reported ok:True with
every section failed, and ``design_review_snapshot`` said nothing at
all when it fetched nothing, leaving an empty list to be noticed.

The tests are shaped around the FAILURE, not the tool: each aggregator
is driven with everything underneath it broken, and the reply must not
be mistakable for a healthy design.
"""

from __future__ import annotations

import asyncio

import pytest


class _Mcp:
    def __init__(self):
        self.tools: dict = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def _healthy_looking(reply: dict) -> bool:
    """Would a reviewer skimming this conclude the design is fine?

    Only the fields a summary is read by. Detail buried under a key
    like `refused` is not what someone acts on when the headline says
    the run succeeded.
    """
    if reply.get("ok") is True:
        return True
    # Altium's aggregator carries no ok, so it is judged on whether it
    # says anywhere that nothing was read.
    if "ok" not in reply:
        words = " ".join(str(v) for k, v in reply.items()
                         if k.startswith("_")).lower()
        return "nothing was read" not in words
    return False


def test_the_easyeda_review_does_not_call_an_unreadable_board_clean(
        monkeypatch):
    import eda_agent.bridge.easyeda_bridge as bridge_mod
    from eda_agent.bridge.easyeda_bridge import EasyEdaNotReachableError
    from eda_agent.tools.easyeda import register_easyeda_tools

    mcp = _Mcp()
    register_easyeda_tools(mcp)

    class _Unreachable:
        verified_live = False

        def verified_live_for(self, command):
            return False

        def send_editor_command(self, command, params=None, timeout=30.0):
            raise EasyEdaNotReachableError("no editor connected")

    monkeypatch.setattr(bridge_mod, "get_easyeda_bridge",
                        lambda: _Unreachable())

    reply = asyncio.run(mcp.tools["easyeda_review_board"]())

    assert not _healthy_looking(reply), (
        f"a review during which nothing could be read looks like a "
        f"successful one: {dict(list(reply.items())[:4])}")
    assert reply.get("total_violations") == 0
    assert reply.get("audits_run") == 0, (
        "audits_run is reporting how many audits EXIST, not how many "
        "produced a result")


def test_the_kicad_review_does_not_report_success_with_every_section_failed(
        monkeypatch, tmp_path):
    """Every section fails when there is no project to read."""
    from eda_agent.tools.kicad import register_kicad_tools

    from eda_agent.tools import kicad as kicad_mod

    mcp = _Mcp()
    try:
        register_kicad_tools(mcp)
    except Exception as exc:                           # noqa: BLE001
        pytest.skip(f"kicad tools did not register: {exc}")

    if "kicad_full_review" not in mcp.tools:
        pytest.skip("kicad_full_review is not registered")

    # The bridge must CONNECT and then everything under it must fail.
    # Letting it refuse at the connection step instead was the first
    # version of this test, and it passed while never reaching the
    # summary it exists to check: a mutation restoring ok:True
    # unconditionally went undetected. A test that cannot reach the
    # line it guards is not a weaker test, it is not a test.
    class _ConnectsThenFails:
        def kicad_cli_path(self):
            return str(tmp_path / "kicad-cli")

        def sch_file_path(self):
            return str(tmp_path / "board.kicad_sch")

        def board_file_path(self):
            return str(tmp_path / "board.kicad_pcb")

        def __getattr__(self, name):
            def _boom(*a, **k):
                raise RuntimeError("the KiCad API stopped answering")
            return _boom

    monkeypatch.setattr(kicad_mod, "get_kicad_bridge",
                        lambda: _ConnectsThenFails())

    reply = asyncio.run(mcp.tools["kicad_full_review"]())
    assert isinstance(reply, dict)
    assert reply.get("reason") is None or "sections_run" in reply, (
        f"the review refused before aggregating, so this test is not "
        f"reaching the summary: {reply.get('reason')}")

    assert reply.get("sections_run") == [], (
        f"sections were produced from a backend that fails everything: "
        f"{reply.get('sections_run')}")
    assert not _healthy_looking(reply), (
        f"a review in which every section failed reports success: "
        f"{dict(list(reply.items())[:4])}")


def test_the_altium_review_says_so_when_it_fetched_nothing(monkeypatch):
    """Driven, not read.

    A first version of this checked that the source contained the key,
    which is the kind of guard that survives the defect it exists to
    catch: several branches build this reply, and a structural check
    cannot tell whether the one that runs is the one it matched. So the
    tool is called with every section failing and the reply is read.
    """
    from eda_agent.tools import review as review_mod

    mcp = _Mcp()
    review_mod.register_review_tools(mcp)

    class _FailingBridge:
        """Answers every request by failing, as an unreachable editor
        would once each section tried to use it."""

        def send_command(self, *a, **k):
            raise RuntimeError("Altium is not reachable")

        def __getattr__(self, name):
            def _boom(*a, **k):
                raise RuntimeError("Altium is not reachable")
            return _boom

    monkeypatch.setattr(review_mod, "get_bridge", lambda: _FailingBridge())

    reply = asyncio.run(mcp.tools["design_review_snapshot"]())
    assert isinstance(reply, dict)

    assert reply.get("_sections_fetched") == [], (
        f"sections were fetched from a bridge that fails everything: "
        f"{reply.get('_sections_fetched')}")
    assert not _healthy_looking(reply), (
        f"a review that fetched nothing does not say so anywhere a "
        f"reader would notice: {sorted(reply)}")


def test_every_review_aggregator_reports_what_it_examined():
    """Ran is not the same as found something, on any backend.

    All three of these collapse "I read nothing" into "there is nothing"
    if they report only how many sections or audits ran. Each has now
    been fixed once for that at the ok/count level; this holds the
    second half in place, which is that a caller can see the SIZE of
    what was looked at and not just its shape.

    Checked as a property of all three together rather than one at a
    time, because the two that had it were fixed on separate days and
    the third was only noticed while writing this.
    """
    import inspect

    from eda_agent.tools import easyeda as easyeda_mod
    from eda_agent.tools import kicad as kicad_mod

    easyeda_src = inspect.getsource(easyeda_mod.register_easyeda_tools)
    kicad_src = inspect.getsource(kicad_mod)

    # review_board counts what its audits examined.
    assert '"examined": total_examined' in easyeda_src, (
        "easyeda_review_board no longer reports how much its audits "
        "examined, so 0 violations is unqualified again")
    # Both snapshot-shaped reviews count what their sections carried.
    for name, source in (("easyeda_review_snapshot", easyeda_src),
                         ("kicad_full_review", kicad_src)):
        assert '"sections_with_data"' in source, (
            f"{name} reports which sections RAN but not which carried "
            f"anything, so a review of empty reads reads as a review of "
            f"an empty design")
        assert "scope_warning" in source, (
            f"{name} does not warn when every section came back empty")
