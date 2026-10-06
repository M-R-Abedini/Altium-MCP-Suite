# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Two tools reported success for work they never checked had happened.

Both were found live, and neither was caught by any test, because a
handler that hardcodes success passes every assertion written against
its happy path.

* ``app_run_menu`` merged ``{"success": True}`` OVER the bridge reply,
  so a Pascal-side error came back as a success. It also turned an
  empty reply into a success. Underneath, Altium's ``RunProcess``
  accepts an unknown process id in silence, so the tool could not have
  known either way: MEASURED 2026-08-17, "Tools|Preferences" returned
  in 0.11s reporting success with no dialog ever shown.

* ``proj_sync_pcb`` reported success and ``in_sync`` three times in a
  row while a modal reading "Cannot compare a source document against
  its owner project" sat on screen unanswered.

The Python halves are driven here. The Pascal halves are checked by
reading the shipped source, which is the only option without a live
editor, so those tests assert the shape of the RESPONSE the handler
builds rather than the presence of a comment.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eda_agent.tools import application as application_module

PROJECT_PAS = (Path(__file__).resolve().parents[1]
               / "scripts" / "altium" / "Project.pas")
APPLICATION_PAS = (Path(__file__).resolve().parents[1]
                   / "scripts" / "altium" / "Application.pas")


def _capture(module, register_name, tool_name, monkeypatch, bridge):
    monkeypatch.setattr(module, "get_bridge", lambda: bridge)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    getattr(module, register_name)(DummyMcp())
    return captured[tool_name]


class _Bridge:
    """Answers execute_menu with whatever the test wants it to."""

    def __init__(self, reply):
        self.reply = reply

    async def send_command_async(self, command, params=None, timeout=None):
        return self.reply


@pytest.fixture
def run_menu(monkeypatch):
    def build(reply):
        return _capture(application_module, "register_application_tools",
                        "app_run_menu", monkeypatch, _Bridge(reply))
    return build


# --------------------------------------------------------------------
# app_run_menu: the reply belongs to the handler, not to the wrapper.
# --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_reply_with_no_verdict_does_not_gain_a_fabricated_one(
        run_menu):
    """The regression.

    ``{"success": True, **result}`` DID preserve an explicit
    ``success: false``, because the spread wins over the literal. What
    it could not survive was a reply that stated no verdict at all: the
    literal then stood, and a caller reading ``success`` got a value
    invented by the wrapper.

    Verified by mutation: restoring the old expression fails this test
    and the empty-reply one, and leaves the explicit-error case passing,
    which is how the narrower shape of the defect was established.
    """
    tool = run_menu({"menu_path": "Tools|Nonexistent",
                     "process": "Client:RunMenu"})

    out = await tool("Tools|Nonexistent")

    assert out.get("success") is not True, (
        "the handler reported no verdict, so the wrapper must not "
        "supply one; unknown is the honest answer")


@pytest.mark.asyncio
async def test_an_explicit_failure_still_survives(run_menu):
    """The half that already worked, pinned so a rewrite cannot lose it."""
    tool = run_menu({"success": False, "error": "UNKNOWN_PROCESS",
                     "menu_path": "Tools|Nonexistent"})

    out = await tool("Tools|Nonexistent")

    assert out["success"] is False
    assert out["error"] == "UNKNOWN_PROCESS"


@pytest.mark.asyncio
async def test_an_empty_reply_is_a_failure_not_a_success(run_menu):
    """``result or {"success": True}`` claimed success for None.

    A bridge that returns nothing has told you nothing. That is the one
    case where success is definitely unknown.
    """
    for reply in (None, "", []):
        tool = run_menu(reply)
        out = await tool("File|Save All")
        assert out["success"] is False, (
            f"a {reply!r} reply means the outcome is unknown, not good")
        assert "no usable reply" in out["reason"]


@pytest.mark.asyncio
async def test_a_dispatch_never_claims_the_command_executed(run_menu):
    """Dispatch is all this tool can honestly report."""
    tool = run_menu({"success": True, "dispatched": True,
                     "outcome_verified": False,
                     "menu_path": "Tools|Preferences"})

    out = await tool("Tools|Preferences")

    assert out["outcome_verified"] is False, (
        "RunProcess ignores an unknown process id in silence, so no "
        "reply from it can verify an outcome")


def test_app_run_menu_points_at_the_tool_that_does_verify():
    """A caller told 'this cannot be verified' needs somewhere to go."""
    from eda_agent.tools import uiauto

    doc = application_module.register_application_tools.__doc__ or ""
    del doc  # the tool docstring is what matters, fetched below.

    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    application_module.register_application_tools(DummyMcp())
    text = captured["app_run_menu"].__doc__

    assert "app_click_menu" in text
    # And the tool it names must exist, or the advice is a dead end.
    uiauto_names = {}

    class _Mcp2:
        def tool(self, *a, **k):
            def deco(fn):
                uiauto_names[fn.__name__] = fn
                return fn
            return deco

    uiauto.register_uiauto_tools(_Mcp2())
    assert "app_click_menu" in uiauto_names, (
        "a docstring that recommends a tool which does not exist sends "
        "the caller nowhere")


def test_the_docstring_names_only_tools_that_exist():
    """The failure mode this file exists to prevent, applied to itself.

    An earlier draft of the proj_sync_pcb docstring recommended
    ``app_click_dialog_button``, which has never existed.
    """
    import re

    from eda_agent.tools import project as project_module
    from eda_agent.tools import uiauto

    real = set()
    for module, register in ((uiauto, "register_uiauto_tools"),
                             (application_module,
                              "register_application_tools")):
        class _Mcp:
            def tool(self, *a, **k):
                def deco(fn):
                    real.add(fn.__name__)
                    return fn
                return deco
        getattr(module, register)(_Mcp())

    captured = {}

    class _Mcp3:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    project_module.register_project_tools(_Mcp3())
    text = captured["proj_sync_pcb"].__doc__ or ""

    named = set(re.findall(r"``(app_[a-z_]+)``", text))
    assert named, "the docstring must point somewhere"
    missing = named - real
    assert not missing, f"proj_sync_pcb recommends nonexistent tools: {missing}"


# --------------------------------------------------------------------
# The Pascal halves.
# --------------------------------------------------------------------

def _function_body(source: str, name: str) -> str:
    """The text from one Function header to the next one."""
    start = source.index(f"Function {name}(")
    rest = source.index("\nFunction ", start + 1)
    return source[start:rest]


def test_update_pcb_refuses_while_a_schematic_is_focused():
    """The precondition Altium enforces and the handler did not."""
    body = _function_body(PROJECT_PAS.read_text(encoding="utf-8"),
                          "Proj_UpdatePCB")

    assert "WRONG_FOCUS" in body
    assert "DM_FocusedDocument" in body
    assert body.index("WRONG_FOCUS") < body.index("SmartCompile(Project)"), (
        "the refusal must come BEFORE the compare, or Altium raises the "
        "modal first and the check is decoration")


def test_update_pcb_does_not_claim_a_verified_dialog_outcome():
    body = _function_body(PROJECT_PAS.read_text(encoding="utf-8"),
                          "Proj_UpdatePCB")

    assert '"dialog_outcome_verified":false' in body.replace(" ", "")
    # The emitted KEY, not the bare word: the comment above the fix
    # explains why the old field went, so a substring match on the word
    # alone fails on the explanation rather than on the behaviour.
    assert '"dialog_may_have_opened"' not in body, (
        "that field guessed at the dialog from unchanged counts, which "
        "cannot distinguish 'dismissed' from 'applied something counts "
        "do not measure'")


def test_the_in_sync_field_says_what_it_actually_measured():
    """``in_sync`` claimed more than a component recount can support."""
    body = _function_body(PROJECT_PAS.read_text(encoding="utf-8"),
                          "Proj_UpdatePCB")

    assert '"components_in_sync"' in body
    assert '"in_sync_note"' in body


def test_the_focus_check_was_not_copied_into_the_untested_twin():
    """Proj_UpdateSchematic is the OPPOSITE direction.

    Its correct focus has never been measured, so asserting one there
    would be inventing a precondition and could refuse valid calls. The
    twin rule cuts both ways: find the twin, then decide, rather than
    mirroring the edit by reflex.
    """
    body = _function_body(PROJECT_PAS.read_text(encoding="utf-8"),
                          "Proj_UpdateSchematic")

    assert "WRONG_FOCUS" not in body, (
        "if this direction has since been measured, replace this test "
        "with one asserting the measured behaviour")


def test_the_dashboard_treats_a_menu_dispatch_as_mutating(tmp_path):
    """The same wrong belief, in a third place.

    ``app_run_menu`` sat in the dashboard's read-only set, so the
    project cache was never cleared after it ran and the page went on
    showing pre-change data. "File|Save All" alone disproves the
    classification, and the path is arbitrary, so nothing about it can
    be assumed inert.
    """
    from eda_agent.web.dashboard import create_app

    app = create_app(workspace_dir=tmp_path)
    app.testing = True
    tools = app.test_client().get("/api/tools").get_json()["tools"]
    by_name = {t["name"]: t for t in tools}

    if "app_run_menu" not in by_name:      # backend without the tool
        pytest.skip("app_run_menu is not registered on this backend")
    assert by_name["app_run_menu"]["mutates"] is True, (
        "a tool that can dispatch File|Save All is not read-only")


def test_execute_menu_reports_dispatch_not_execution():
    """The mapped branch may dispatch, but never claims more."""
    body = _function_body(APPLICATION_PAS.read_text(encoding="utf-8"),
                          "App_ExecuteMenu")
    flat = body.replace(" ", "")

    assert '"dispatched":true' in flat
    # Every success this function can return must be marked unverified.
    assert flat.count('"success":true') == flat.count(
        '"outcome_verified":false'), (
        "a success path without outcome_verified:false is the original "
        "defect growing back")


def test_an_unmapped_menu_path_is_refused_not_guessed():
    """Found by running the shipped fix against live Altium.

    The first pass left the fallback returning success:true with a note
    admitting, in the same breath, that the branch was MEASURED opening
    nothing. Calling app_run_menu("Tools|NoSuchCommandXYZ") returned
    success:true, which is the very defect the fix was for, surviving in
    the one branch where the evidence against it was strongest.

    Refusing is only the right answer because app_click_menu exists and
    reaches arbitrary paths. Before it did, guessing was all there was.
    """
    body = _function_body(APPLICATION_PAS.read_text(encoding="utf-8"),
                          "App_ExecuteMenu")

    assert "UNMAPPED_MENU_PATH" in body
    # The CALL, not the name: the comment above the refusal explains
    # what was removed and names Client:RunMenu while doing so.
    assert "Client.SendMessage('Client:RunMenu'" not in body, (
        "passing a pipe-separated path as a MenuID was the guess; "
        "keeping it alive keeps the false success reachable")
    # The refusal has to say where to go instead.
    refusal = body[body.index("UNMAPPED_MENU_PATH"):]
    assert "app_click_menu" in refusal[:900]
