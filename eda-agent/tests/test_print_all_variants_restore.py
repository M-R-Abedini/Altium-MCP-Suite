# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""``proj_print_all_variants`` must not claim a restore it did not do.

The tool walks every variant, setting each one active in turn to export
its PDF. That leaves the project on the LAST variant it touched, so it
puts the original back at the end.

The restore is best-effort by design, since failing to put a variant
back should not lose the PDFs that were already produced. What it must
not do is report success regardless. ``restored`` previously echoed the
variant name whether or not the call worked, so a caller could not tell
a restored project from one left on the wrong variant.

That distinction has teeth. The active variant decides which components
are Not Fitted, so anything variant-sensitive run afterwards acts on
the wrong set: a BOM, an export, or
``pcb_apply_dnp_paste_exclusion`` stripping stencil paste off parts the
other variant does fit, which reaches the fab as unsoldered
components.
"""

from __future__ import annotations

import pytest

from eda_agent.tools import project as project_module


def _capture_tool(monkeypatch, bridge, tmp_path):
    monkeypatch.setattr(project_module, "get_bridge", lambda: bridge)
    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    project_module.register_project_tools(DummyMcp())
    return captured["proj_print_all_variants"]


class _Bridge:
    """Answers the variant walk, optionally failing the final restore."""

    def __init__(self, *, fail_restore_to=None):
        self.fail_restore_to = fail_restore_to
        self.calls: list[tuple[str, dict]] = []

    async def send_command_async(self, command, params=None, timeout=None):
        params = params or {}
        self.calls.append((command, params))
        if command == "project.get_variants":
            return {"variants": [{"name": "Assembly-A"},
                                 {"name": "Assembly-B"}]}
        if command == "project.get_active_variant":
            return {"name": "Assembly-A"}
        if command == "project.set_active_variant":
            if (self.fail_restore_to
                    and params.get("variant_name") == self.fail_restore_to
                    and self._is_the_final_restore()):
                raise RuntimeError("variant is locked by the editor")
            return {"success": True}
        if command == "project.export_pdf":
            return {"success": True}
        return {}

    def _is_the_final_restore(self) -> bool:
        """True once every variant has already been exported."""
        exports = sum(1 for c, _ in self.calls if c == "project.export_pdf")
        return exports >= 2

    def variant_sets(self) -> list[str]:
        return [p.get("variant_name") for c, p in self.calls
                if c == "project.set_active_variant"]


@pytest.mark.asyncio
async def test_a_successful_restore_names_the_variant(monkeypatch, tmp_path):
    bridge = _Bridge()
    tool = _capture_tool(monkeypatch, bridge, tmp_path)

    out = await tool(output_dir=str(tmp_path))

    assert out["restored"] == "Assembly-A"
    assert "restore_error" not in out
    assert out["count"] == 2
    # The original really was set back after the walk.
    assert bridge.variant_sets()[-1] == "Assembly-A"


@pytest.mark.asyncio
async def test_a_failed_restore_is_reported_not_claimed(monkeypatch,
                                                        tmp_path):
    """The bug: `restored` used to echo the name either way."""
    bridge = _Bridge(fail_restore_to="Assembly-A")
    tool = _capture_tool(monkeypatch, bridge, tmp_path)

    out = await tool(output_dir=str(tmp_path))

    assert out["restored"] is None, (
        "a failed restore must not report the variant as restored")
    assert out["restore_error"], "the reason must be surfaced"
    assert out["active_variant_unknown"] is True
    assert "variant-sensitive" in out["note"]


@pytest.mark.asyncio
async def test_a_failed_restore_still_keeps_the_exports(monkeypatch,
                                                        tmp_path):
    """Best-effort is the right shape: do not lose finished work.

    If the restore raised out of the tool, a locked variant at the end
    would discard the record of every PDF already produced.
    """
    bridge = _Bridge(fail_restore_to="Assembly-A")
    tool = _capture_tool(monkeypatch, bridge, tmp_path)

    out = await tool(output_dir=str(tmp_path))

    assert out["count"] == 2
    assert [e["variant"] for e in out["exported"]] == ["Assembly-A",
                                                       "Assembly-B"]
