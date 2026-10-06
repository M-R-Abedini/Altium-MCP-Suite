# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Does KiCad itself accept the files this converter writes?

Every other check on the writer compares its output against OUR reader,
which is lenient by design: it takes what it understands and ignores the
rest. That makes the whole round-trip suite blind to output KiCad simply
refuses, and the converter shipped exactly that. The writer emitted

    (pin input line inverted (at ...) ...)

with TWO graphic-style tokens where the grammar has one. Our reader saw
the second token, read "line", and dropped the bubble -- a lost flag.
KiCad's answer is harsher: ``Unable to load library``. Every symbol
exported with an inverted pin was unopenable, and nothing in the suite
said so.

So this runs KiCad's own parser over generated files. It is the only
check here whose authority is not this codebase.

TWO TRAPS in kicad-cli, both learned by watching it:
  * it exits 0 on a parse failure and reports the problem only in its
    output, so the exit code proves nothing;
  * it fails to write when the output directory does not exist, which
    reads like a parse failure and is not.
Both are handled below.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.kicad_libs

#: kicad-cli reports these while still exiting 0.
_FAILURE_MARKERS = ("unable to load", "error loading", "failed to load",
                    "parse error", "expecting")


@pytest.fixture(scope="module")
def kicad_cli():
    """The kicad-cli next to the installed libraries, or skip."""
    from eda_agent.libimport.providers.kicad_local import _symbol_dir

    root = _symbol_dir()
    if root is None:
        pytest.skip("no KiCad installation found")
    # <install>/share/kicad/symbols -> <install>/bin/kicad-cli.exe
    for candidate in (root.parents[2] / "bin" / "kicad-cli.exe",
                      root.parents[2] / "bin" / "kicad-cli"):
        if candidate.is_file():
            return candidate
    pytest.skip(f"kicad-cli not found near {root}")


def _run(cli: Path, args: list[str]) -> str:
    proc = subprocess.run([str(cli), *args], capture_output=True, text=True,
                          timeout=180)
    return f"{proc.stdout}\n{proc.stderr}"


def _assert_kicad_loaded(output: str, what: str) -> None:
    lowered = output.lower()
    for marker in _FAILURE_MARKERS:
        assert marker not in lowered, (
            f"KiCad refused the {what} this converter wrote:\n{output}")
    assert "plotting" in lowered, (
        f"KiCad plotted nothing from the {what}; it may have been "
        f"rejected silently:\n{output}")


@pytest.fixture(scope="module")
def source_symbol():
    """A real multi-unit symbol carrying inverted and clock pins.

    Chosen because those are the pins whose graphic style the writer got
    wrong; a plain resistor would pass with the bug in place.
    """
    from eda_agent.libimport.kicad.reader import read_kicad_symbol
    from eda_agent.libimport.providers.kicad_local import _symbol_dir

    text = (_symbol_dir() / "4xxx.kicad_sym").read_text(
        encoding="utf-8", errors="replace")
    comp = read_kicad_symbol(text, name="14528")
    decorated = [s for s in comp.symbol.shapes
                 if s.kind == "pin" and (s.dot or s.clock)]
    assert decorated, "this symbol no longer carries a decorated pin"
    assert comp.unit_count > 1, "this symbol is no longer multi-unit"
    return comp


def test_kicad_loads_the_symbol_we_write(kicad_cli, source_symbol, tmp_path):
    from eda_agent.libimport.easyeda.kicad import symbol_to_kicad_sym

    lib = tmp_path / "gen.kicad_sym"
    lib.write_text(symbol_to_kicad_sym(source_symbol), encoding="utf-8")
    out = tmp_path / "svg"
    out.mkdir()

    _assert_kicad_loaded(
        _run(kicad_cli, ["sym", "export", "svg", "--output", str(out),
                         str(lib)]),
        "symbol library")


def test_kicad_sees_every_sub_part_we_declare(kicad_cli, source_symbol,
                                              tmp_path):
    """Loading is not enough; the units have to arrive.

    A writer that collapsed every pin into unit 1 would still produce a
    file KiCad opens happily, with one gate instead of three.
    """
    from eda_agent.libimport.easyeda.kicad import symbol_to_kicad_sym

    lib = tmp_path / "gen.kicad_sym"
    lib.write_text(symbol_to_kicad_sym(source_symbol), encoding="utf-8")
    out = tmp_path / "svg"
    out.mkdir()

    output = _run(kicad_cli, ["sym", "export", "svg", "--output", str(out),
                              str(lib)])
    _assert_kicad_loaded(output, "symbol library")
    plotted = sorted(p.name for p in out.glob("*.svg"))
    assert len(plotted) == source_symbol.unit_count, (
        f"declared {source_symbol.unit_count} sub-parts, KiCad plotted "
        f"{len(plotted)}: {plotted}")


def test_kicad_loads_the_footprint_we_write(kicad_cli, tmp_path):
    from eda_agent.libimport.easyeda.kicad import footprint_to_kicad_mod
    from eda_agent.libimport.kicad.reader import read_kicad_footprint
    from eda_agent.libimport.providers.kicad_local import _footprint_dir

    src = _footprint_dir() / "Package_DIP.pretty" / "DIP-16_W7.62mm.kicad_mod"
    if not src.is_file():
        pytest.skip("the sampled footprint is not installed")
    comp = read_kicad_footprint(src.read_text(encoding="utf-8",
                                              errors="replace"))

    # kicad-cli takes a .pretty DIRECTORY, not a lone .kicad_mod.
    pretty = tmp_path / "gen.pretty"
    pretty.mkdir()
    (pretty / "GEN.kicad_mod").write_text(
        footprint_to_kicad_mod(comp), encoding="utf-8")
    out = tmp_path / "fpsvg"
    out.mkdir()  # kicad-cli will NOT create this, and says so confusingly

    _assert_kicad_loaded(
        _run(kicad_cli, ["fp", "export", "svg", "--output", str(out),
                         str(pretty)]),
        "footprint library")
    assert list(out.glob("*.svg")), "no footprint was plotted"


def test_the_check_can_actually_fail(kicad_cli, source_symbol, tmp_path):
    """Guards the guard.

    _assert_kicad_loaded passes on any output that lacks a known marker,
    so a kicad-cli that changed its wording, or a stub binary, would make
    the tests above vacuous. Feed it the exact malformation the writer
    used to emit and require a refusal.
    """
    from eda_agent.libimport.easyeda.kicad import symbol_to_kicad_sym

    text = symbol_to_kicad_sym(source_symbol)
    broken = text.replace("(pin input inverted ", "(pin input line inverted ")
    assert broken != text, "the fixture no longer has an inverted pin"

    lib = tmp_path / "broken.kicad_sym"
    lib.write_text(broken, encoding="utf-8")
    out = tmp_path / "svg"
    out.mkdir()

    output = _run(kicad_cli, ["sym", "export", "svg", "--output", str(out),
                              str(lib)])
    with pytest.raises(AssertionError):
        _assert_kicad_loaded(output, "symbol library")


# ------------------- the whole corpus, not one part -------------------
#
# One symbol proves the writer can emit a valid file; it does not prove
# it does so for the shapes, styles and unit layouts real libraries
# contain. kicad-cli takes a whole .kicad_sym and a whole .pretty, so a
# few hundred generated parts cost one invocation each -- about three
# seconds for the pair.


def _concat_library(header: str, bodies: list[str]) -> str:
    """Wrap many generated symbol bodies in one .kicad_sym.

    ``symbol_to_kicad_sym`` writes a single-symbol library, and
    kicad-cli validates a library at a time. Splitting off the header
    line and the closing paren is safe because this is OUR output and
    its shape is fixed; doing it to arbitrary KiCad files would not be.

    The header comes from the WRITER and is never a literal here.
    Hardcoding "(version 20251024)" made the corpus currency check
    unable to see a wrong version in the writer at all: the harness
    supplied a correct header over a broken one, so the test passed for
    a reason that had nothing to do with the code under test. Caught by
    mutating the writer's version and watching this pass while the
    single-symbol check failed.
    """
    return header + "\n" + "\n".join(bodies) + "\n)\n"


@pytest.fixture(scope="module")
def generated_corpus(tmp_path_factory):
    """One symbol and one footprint from every installed library."""
    import re

    from eda_agent.libimport.easyeda.kicad import (
        footprint_to_kicad_mod,
        symbol_to_kicad_sym,
    )
    from eda_agent.libimport.kicad.reader import (
        read_kicad_footprint,
        read_kicad_symbol,
    )
    from eda_agent.libimport.providers.kicad_local import (
        _footprint_dir,
        _symbol_dir,
    )

    base = tmp_path_factory.mktemp("generated")
    sym_root, fp_root = _symbol_dir(), _footprint_dir()
    if sym_root is None or fp_root is None:
        pytest.skip("KiCad libraries are not installed")

    bodies: list[str] = []
    header = ""
    units = 0
    # One definition per NAME. A .kicad_sym cannot hold two symbols
    # called the same thing, and the stock libraries do repeat one
    # across files (7400 exists in both 74xx and 74xx_IEEE, with five
    # units and four). Concatenating both makes KiCad keep one and plot
    # fewer units than were written, which looks exactly like a writer
    # that dropped some -- a property of the format, not of this code.
    taken: set[str] = set()
    for lib in sorted(sym_root.glob("*.kicad_sym")):
        text = lib.read_text(encoding="utf-8", errors="replace")
        for name in re.findall(r'^\t\(symbol "([^"]+)"', text, re.M)[:1]:
            comp = read_kicad_symbol(text, name=name)
            if not any(s.kind == "pin" for s in comp.symbol.shapes):
                continue
            if comp.symbol.name in taken:
                continue
            taken.add(comp.symbol.name)
            one = symbol_to_kicad_sym(comp)
            if not header:
                header = one.split("\n", 1)[0]
            bodies.append(one.split("\n", 1)[1].rsplit(")", 1)[0].rstrip())
            units += max(1, comp.unit_count)

    sym_lib = base / "all.kicad_sym"
    sym_lib.write_text(_concat_library(header, bodies), encoding="utf-8")

    pretty = base / "gen.pretty"
    pretty.mkdir()
    footprints = 0
    for lib in sorted(fp_root.glob("*.pretty")):
        for mod in sorted(lib.glob("*.kicad_mod"))[:1]:
            comp = read_kicad_footprint(
                mod.read_text(encoding="utf-8", errors="replace"))
            if not any(s.kind == "pad" for s in comp.footprint.shapes):
                continue
            (pretty / f"{lib.stem}__{mod.stem}.kicad_mod").write_text(
                footprint_to_kicad_mod(comp), encoding="utf-8")
            footprints += 1

    return {"symbol_lib": sym_lib, "symbols": len(bodies), "units": units,
            "pretty": pretty, "footprints": footprints, "base": base}


def test_the_generated_corpus_is_large_enough(generated_corpus):
    """A shrunken corpus would make the two checks below vacuous."""
    assert generated_corpus["symbols"] > 100
    assert generated_corpus["footprints"] > 100


def test_kicad_loads_every_symbol_we_generate(generated_corpus):
    """Several hundred real parts, written out, through KiCad's parser.

    Also asserts the plotted count: KiCad emits one SVG per sub-part, so
    a writer that quietly flattened multi-part symbols would still load
    cleanly and plot fewer files than the units it was given.
    """
    out = generated_corpus["base"] / "symsvg"
    out.mkdir(exist_ok=True)
    from eda_agent.libimport.providers.kicad_local import _symbol_dir

    root = _symbol_dir()
    cli = root.parents[2] / "bin" / "kicad-cli.exe"
    if not cli.is_file():
        pytest.skip("kicad-cli not found")

    output = _run(cli, ["sym", "export", "svg", "--output", str(out),
                        str(generated_corpus["symbol_lib"])])
    _assert_kicad_loaded(output, "generated symbol corpus")
    plotted = len(list(out.glob("*.svg")))
    assert plotted == generated_corpus["units"], (
        f"wrote {generated_corpus['units']} sub-parts, KiCad plotted "
        f"{plotted}")


def test_kicad_loads_every_footprint_we_generate(generated_corpus):
    out = generated_corpus["base"] / "fpsvg"
    out.mkdir(exist_ok=True)
    from eda_agent.libimport.providers.kicad_local import _symbol_dir

    root = _symbol_dir()
    cli = root.parents[2] / "bin" / "kicad-cli.exe"
    if not cli.is_file():
        pytest.skip("kicad-cli not found")

    output = _run(cli, ["fp", "export", "svg", "--output", str(out),
                        str(generated_corpus["pretty"])])
    _assert_kicad_loaded(output, "generated footprint corpus")
    plotted = len(list(out.glob("*.svg")))
    assert plotted == generated_corpus["footprints"], (
        f"wrote {generated_corpus['footprints']} footprints, KiCad plotted "
        f"{plotted}")


def test_kicad_considers_our_files_already_current(kicad_cli, source_symbol,
                                                   tmp_path):
    """A file must not contradict its own format declaration.

    This writer declared version 20211014 while emitting 2024 syntax
    such as ``(hide yes)``. KiCad tolerated the mix and silently
    migrated the file on open -- the same shape of wrongness as the
    two-token pin style, which was also tolerated right up until it
    wasn't.

    ``upgrade`` is the check that catches it: it rewrites anything not
    already current, so "was not updated" is KiCad stating that the file
    matches what it would have written itself.
    """
    from eda_agent.libimport.easyeda.kicad import (
        footprint_to_kicad_mod,
        symbol_to_kicad_sym,
    )
    from eda_agent.libimport.kicad.reader import read_kicad_footprint
    from eda_agent.libimport.providers.kicad_local import _footprint_dir

    lib = tmp_path / "gen.kicad_sym"
    lib.write_text(symbol_to_kicad_sym(source_symbol), encoding="utf-8")
    assert "not updated" in _run(kicad_cli, ["sym", "upgrade", str(lib)]), (
        "KiCad rewrote our symbol library, so it was not in current form")

    src = _footprint_dir() / "Package_DIP.pretty" / "DIP-16_W7.62mm.kicad_mod"
    if not src.is_file():
        pytest.skip("the sampled footprint is not installed")
    pretty = tmp_path / "gen.pretty"
    pretty.mkdir()
    (pretty / "GEN.kicad_mod").write_text(
        footprint_to_kicad_mod(read_kicad_footprint(
            src.read_text(encoding="utf-8", errors="replace"))),
        encoding="utf-8")
    assert "not updated" in _run(kicad_cli, ["fp", "upgrade", str(pretty)]), (
        "KiCad rewrote our footprint, so it was not in current form")


def test_the_whole_generated_corpus_is_already_current(generated_corpus):
    """Every generated part, judged by KiCad's own upgrader.

    Stronger than the load checks above. ``upgrade`` rewrites anything
    not already in current form, so "was not updated" over a few hundred
    parts is KiCad saying the whole batch matches what it would have
    written itself -- not merely that it could parse it.

    That distinction is the one this file exists for: the two-token pin
    style parsed fine in our reader and was refused outright by KiCad,
    and the 2021 format header parsed fine in KiCad and was silently
    migrated. Tolerated is not the same as correct at either level.

    NOTE that upgrade MUTATES on failure. Both paths are throwaway
    fixtures, so a rewrite costs nothing but the failed assertion.
    """
    from eda_agent.libimport.providers.kicad_local import _symbol_dir

    root = _symbol_dir()
    cli = root.parents[2] / "bin" / "kicad-cli.exe"
    if not cli.is_file():
        pytest.skip("kicad-cli not found")

    sym_out = _run(cli, ["sym", "upgrade",
                         str(generated_corpus["symbol_lib"])])
    assert "not updated" in sym_out, (
        f"KiCad rewrote {generated_corpus['symbols']} generated symbols, so "
        f"they were not in current form:\n{sym_out}")

    fp_out = _run(cli, ["fp", "upgrade", str(generated_corpus["pretty"])])
    assert "not updated" in fp_out, (
        f"KiCad rewrote {generated_corpus['footprints']} generated "
        f"footprints, so they were not in current form:\n{fp_out}")
