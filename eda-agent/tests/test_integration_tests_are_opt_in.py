# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""``pytest tests/`` must not touch a running Altium.

The tests under ``tests/integration/`` drive a real Altium Designer. They
are meant to be opt-in, and they are: without ``EDA_AGENT_INTEGRATION=1``
they skip. The question this file answers is what they do BEFORE
deciding to skip.

The answer used to be: quite a lot. ``real_bridge`` built an
``AltiumBridge``, asked whether Altium was running, and pinged it,
skipping only once the ping came back empty. So the skip was reached by
writing request files into the live workspace. ``fixture_project_loaded``
went further and called ``project.open``, which has no skip in front of
it at all, so a run against a healthy polling loop would have opened the
fixture project in the session the user was working in.

That was found the way these things usually are. A full suite run left
four unanswered ``application.ping`` request files in the workspace,
timestamped inside the run. Nothing broke, only because the polling loop
happened to be down; a healthy loop would have answered them and then
opened a project.

The repo has a workflow rule that says do not run the tests during a
live bridge session. This file is that rule made structural, because a
rule someone has to remember is not isolation.

The end-to-end test is the one that matters: it runs the integration
directory in a subprocess with the workspace redirected somewhere
disposable, and asserts nothing was written there. The unit tests below
it explain a failure faster but prove less.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_INTEGRATION = _ROOT / "tests" / "integration"
_CONFTEST = _INTEGRATION / "conftest.py"


def _load_conftest():
    spec = importlib.util.spec_from_file_location(
        "integration_conftest_under_test", _CONFTEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeItem:
    """The two attributes the hook uses."""

    def __init__(self, path: pathlib.Path):
        self.fspath = str(path)
        self.markers: list = []

    def add_marker(self, marker) -> None:
        self.markers.append(marker)


def _run_hook(monkeypatch, env_value, paths):
    module = _load_conftest()
    if env_value is None:
        monkeypatch.delenv(module.REQUIRED_ENV, raising=False)
    else:
        monkeypatch.setenv(module.REQUIRED_ENV, env_value)
    items = [_FakeItem(p) for p in paths]
    module.pytest_collection_modifyitems(config=None, items=items)
    return items


def test_integration_items_are_skipped_without_the_opt_in(monkeypatch):
    items = _run_hook(monkeypatch, None,
                      [_INTEGRATION / "test_round_trip.py"])
    assert items[0].markers, (
        "an integration test was left runnable without "
        "EDA_AGENT_INTEGRATION=1, so its fixtures would contact a live "
        "Altium session")


def test_the_opt_in_still_runs_them(monkeypatch):
    """The gate must not make the tests unrunnable in CI."""
    items = _run_hook(monkeypatch, "1",
                      [_INTEGRATION / "test_round_trip.py"])
    assert not items[0].markers, (
        "EDA_AGENT_INTEGRATION=1 is the documented way to run these and "
        "must not be skipped")


def test_ordinary_tests_are_left_alone(monkeypatch):
    """The hook may fire for the whole session, so it must filter."""
    items = _run_hook(monkeypatch, None, [
        _ROOT / "tests" / "test_regression.py",
        _ROOT / "tests" / "unit" / "test_whatever.py",
    ])
    for item in items:
        assert not item.markers, (
            f"{item.fspath} is not an integration test but was skipped; "
            "the path filter is too broad and is hiding real tests")


def test_running_the_directory_writes_no_request_file(tmp_path):
    """The claim that matters, checked end to end.

    A unit test on the hook cannot prove that no fixture ran. This runs
    the real directory through a real pytest with the workspace pointed
    at an empty temporary directory, then asserts the directory is still
    empty. Any bridge contact leaves a ``request_*.json`` behind, which
    is exactly how the original problem was spotted.
    """
    env = dict(os.environ)
    env.pop("EDA_AGENT_INTEGRATION", None)
    env["EDA_AGENT_WORKSPACE"] = str(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/integration", "-q",
         "--no-header", "-p", "no:cacheprovider"],
        cwd=str(_ROOT), env=env, capture_output=True, text=True,
        timeout=600)

    leftovers = sorted(p.name for p in tmp_path.rglob("request_*.json"))
    assert not leftovers, (
        "running tests/integration without the opt-in contacted the "
        f"bridge and left {leftovers}. A live Altium session would have "
        "received these.")

    assert result.returncode == 0, (
        "the integration directory should skip cleanly, not error:\n"
        + result.stdout[-2000:])
    assert "skipped" in result.stdout, (
        "expected the integration tests to report as skipped:\n"
        + result.stdout[-2000:])
