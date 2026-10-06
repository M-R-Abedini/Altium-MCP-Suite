# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The extension build must be current, and health must be able to say so.

Two separate properties, and both had gone unguarded.

The first is a repository invariant: ``main.js`` and the committed
``BUILD_STAMP.json`` must describe the same code. EasyEDA installs by
VERSION, so re-importing a package whose version already matches is a
silent no-op, and an edit that never made it into a build is therefore
invisible from inside the editor. ``build.py`` refuses to build in that
state, but nothing stopped the source being committed without a build.

The second is that the health check can actually reach PASS. It used to
compare ``main.js`` byte-for-byte against ``dist/index.js``, which the
build never produces: it stamps BUILD_ID and strips ``export``. So the
files always differed, the check warned seconds after a successful
build, and PASS was unreachable. A warning that is always on is one
nobody reads, and it was the only signal that the editor half was stale.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

from eda_agent.diag import health

_EXT = pathlib.Path(__file__).resolve().parents[1] / "extensions" / "easyeda"
_MAIN = _EXT / "main.js"
_STAMP = _EXT / "BUILD_STAMP.json"

pytestmark = pytest.mark.skipif(
    not _MAIN.is_file(),
    reason="source install without the EasyEDA extension")


def _build_module():
    """The real build script, so the hash has exactly one definition."""
    spec = importlib.util.spec_from_file_location(
        "_easyeda_build_under_test", _EXT / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_stamp_matches_the_committed_source():
    """An edit to main.js that was never built must not reach a commit.

    This is the EasyEDA twin of a stale Altium deploy, and the worse of
    the two: EasyEDA's importer treats a same-version package as already
    installed, so the editor keeps running the old code and reports
    nothing at all.
    """
    stamp = json.loads(_STAMP.read_text(encoding="utf-8"))
    expected = _build_module().build_id(_MAIN.read_text(encoding="utf-8"))

    assert stamp["build_id"] == expected, (
        f"main.js hashes to {expected} but BUILD_STAMP.json records "
        f"{stamp['build_id']}. Run `python extensions/easyeda/build.py`, "
        f"which will also refuse the build if the version needs bumping.")


def test_health_computes_the_same_id_as_the_build_script():
    """Two hash definitions would drift, and the drift would show up as
    a staleness warning that rebuilding could never clear."""
    assert (health._easyeda_build_id(_MAIN)
            == _build_module().build_id(_MAIN.read_text(encoding="utf-8")))


def test_a_changed_source_changes_the_id(tmp_path):
    source = tmp_path / "main.js"
    source.write_text("const BUILD_ID = 'dev';\nconst A = 1;\n",
                      encoding="utf-8")
    (tmp_path / "build.py").write_bytes((_EXT / "build.py").read_bytes())
    first = health._easyeda_build_id(source)

    source.write_text("const BUILD_ID = 'dev';\nconst A = 2;\n",
                      encoding="utf-8")
    second = health._easyeda_build_id(source)

    assert first and second and first != second


def test_the_stamped_id_is_read_back_out_of_a_build(tmp_path):
    built = tmp_path / "index.js"
    built.write_text("function a(){}\nconst BUILD_ID = 'abc123def456';\n",
                     encoding="utf-8")
    assert health._stamped_build_id(built) == "abc123def456"


def test_a_missing_file_reads_as_unknown_not_as_agreement(tmp_path):
    """None must mean "could not tell", never "matches"."""
    assert health._stamped_build_id(tmp_path / "nope.js") is None
    assert health._easyeda_build_id(tmp_path / "nope.js") is None


def test_the_check_passes_on_a_freshly_built_tree():
    """The regression. PASS was structurally unreachable.

    Skipped when nothing is built, which is the normal state in CI:
    dist/ is gitignored. On a developer machine that has run the build,
    this is the assertion that the old comparison could never satisfy.
    """
    if not (_EXT / "dist" / "index.js").is_file():
        pytest.skip("extension not built in this tree")

    stamp = json.loads(_STAMP.read_text(encoding="utf-8"))
    expected = _build_module().build_id(_MAIN.read_text(encoding="utf-8"))
    if stamp["build_id"] != expected:
        pytest.skip("tree has unbuilt edits; the other test covers that")

    check = health._check_easyeda_extension_built()
    assert check.status.name == "PASS", (
        f"a current build must not warn, got {check.status.name}: "
        f"{check.message}")


def test_source_and_build_differ_in_bytes_which_is_why_that_test_failed():
    """The build is not a copy, so byte equality was never achievable.

    Stated as a test because the old check encoded the opposite belief,
    and anyone tempted to simplify back to a content comparison should
    see this fail first.
    """
    built = _EXT / "dist" / "index.js"
    if not built.is_file():
        pytest.skip("extension not built in this tree")

    assert (built.read_text(encoding="utf-8")
            != _MAIN.read_text(encoding="utf-8")), (
        "if these ever match, the build stopped stamping BUILD_ID and "
        "stripping export, and the staleness check needs rethinking")
