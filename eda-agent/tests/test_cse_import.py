# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the SamacSys / Component Search Engine zip import staging.

Synthetic zips only; the .SchLib/.PcbLib payloads are dummy bytes because
this layer never parses them. Covered:

  - inspect: flat and per-part-folder layouts, .epw/extras, MPN
    derivation (member stem, LIB_ prefix strip, zip-name fallback),
    extension case-insensitivity, deterministic multi-candidate pick.
  - extract: flattening, install-plan order and parameter shapes against
    tools/library.py, optional steps dropped when inputs are missing.
  - rejection: missing file, non-zip, no recognizable libs, zip-slip
    members ('../', absolute, drive-letter) rejecting the whole archive.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from eda_agent.libimport import extract_cse_zip, inspect_cse_zip
from eda_agent.libimport.cse import _member_escapes


def make_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def cse_members(mpn: str = "STM32F103C8T6", folder: str = "", step: bool = True):
    prefix = f"{folder}/" if folder else ""
    members = {
        f"{prefix}{mpn}.SchLib": b"schlib-bytes",
        f"{prefix}{mpn}.PcbLib": b"pcblib-bytes",
    }
    if step:
        members[f"{prefix}{mpn}.stp"] = b"step-bytes"
    return members


# ---------------------------------------------------------------- inspect


def test_inspect_flat_zip(tmp_path):
    zp = make_zip(tmp_path / "LIB_STM32F103C8T6.zip", cse_members())
    info = inspect_cse_zip(zp)
    assert info["ok"] is True
    assert info["mpn"] == "STM32F103C8T6"
    assert info["schlib"] == "STM32F103C8T6.SchLib"
    assert info["pcblib"] == "STM32F103C8T6.PcbLib"
    assert info["step"] == "STM32F103C8T6.stp"
    assert info["extras"] == []
    assert info["suspicious"] == []


def test_inspect_nested_folder_with_epw_and_readme(tmp_path):
    members = cse_members(mpn="2N7002", folder="2N7002")
    members["2N7002/2N7002.epw"] = b"epw"
    members["2N7002/how-to-import.htm"] = b"readme"
    zp = make_zip(tmp_path / "LIB_2N7002.zip", members)
    info = inspect_cse_zip(zp)
    assert info["ok"] is True
    assert info["mpn"] == "2N7002"
    assert info["schlib"] == "2N7002/2N7002.SchLib"
    assert info["pcblib"] == "2N7002/2N7002.PcbLib"
    assert info["step"] == "2N7002/2N7002.stp"
    assert sorted(info["extras"]) == [
        "2N7002/2N7002.epw",
        "2N7002/how-to-import.htm",
    ]


def test_inspect_strips_lib_prefix_from_member_stem(tmp_path):
    zp = make_zip(
        tmp_path / "download.zip",
        {"LIB_AD8232ACPZ.SchLib": b"s", "LIB_AD8232ACPZ.PcbLib": b"p"},
    )
    info = inspect_cse_zip(zp)
    assert info["mpn"] == "AD8232ACPZ"


def test_inspect_mpn_prefers_member_stem_over_zip_name(tmp_path):
    # Best-effort order is SchLib stem > PcbLib stem > zip filename; a
    # package-named PcbLib stem still beats the zip's LIB_<MPN> name.
    zp = make_zip(
        tmp_path / "LIB_MAX3232.zip",
        {"SOIC127P600X175-16N.PcbLib": b"p"},
    )
    info = inspect_cse_zip(zp)
    assert info["mpn"] == "SOIC127P600X175-16N"


def test_inspect_mpn_prefers_schlib_stem_over_pcblib(tmp_path):
    zp = make_zip(
        tmp_path / "x.zip",
        {"MAX3232ESE.SchLib": b"s", "SOIC127P600X175-16N.PcbLib": b"p"},
    )
    assert inspect_cse_zip(zp)["mpn"] == "MAX3232ESE"


def test_inspect_case_insensitive_extensions(tmp_path):
    zp = make_zip(
        tmp_path / "x.zip",
        {"part.SCHLIB": b"s", "part.pcblib": b"p", "part.STEP": b"3d"},
    )
    info = inspect_cse_zip(zp)
    assert info["ok"] is True
    assert info["schlib"] == "part.SCHLIB"
    assert info["pcblib"] == "part.pcblib"
    assert info["step"] == "part.STEP"


def test_inspect_step_extension_variant(tmp_path):
    zp = make_zip(
        tmp_path / "x.zip",
        {"p.SchLib": b"s", "p.PcbLib": b"p", "p.step": b"3d"},
    )
    assert inspect_cse_zip(zp)["step"] == "p.step"


def test_inspect_multiple_candidates_is_deterministic(tmp_path):
    members = {
        "b_part.SchLib": b"s2",
        "a_part.SchLib": b"s1",
        "part.PcbLib": b"p",
    }
    zp = make_zip(tmp_path / "x.zip", members)
    first = inspect_cse_zip(zp)
    second = inspect_cse_zip(zp)
    assert first == second
    # Sorted member-name order: a_part wins, b_part becomes an extra.
    assert first["schlib"] == "a_part.SchLib"
    assert "b_part.SchLib" in first["extras"]


def test_inspect_pcblib_only_is_ok(tmp_path):
    zp = make_zip(tmp_path / "x.zip", {"QFN50P400X400X80-25N.PcbLib": b"p"})
    info = inspect_cse_zip(zp)
    assert info["ok"] is True
    assert info["schlib"] is None
    assert info["pcblib"] == "QFN50P400X400X80-25N.PcbLib"


def test_inspect_missing_file(tmp_path):
    info = inspect_cse_zip(tmp_path / "nope.zip")
    assert info["ok"] is False
    assert "not found" in info["reason"]


def test_inspect_not_a_zip(tmp_path):
    bad = tmp_path / "fake.zip"
    bad.write_text("this is not a zip")
    info = inspect_cse_zip(bad)
    assert info["ok"] is False
    assert "not a zip" in info["reason"]


def test_inspect_no_recognizable_libs(tmp_path):
    zp = make_zip(
        tmp_path / "x.zip", {"readme.txt": b"t", "model.stp": b"3d"}
    )
    info = inspect_cse_zip(zp)
    assert info["ok"] is False
    assert ".SchLib" in info["reason"]
    assert "readme.txt" in info["extras"]


def test_inspect_empty_zip(tmp_path):
    zp = make_zip(tmp_path / "x.zip", {})
    assert inspect_cse_zip(zp)["ok"] is False


def test_inspect_directory_entries_ignored(tmp_path):
    zp = tmp_path / "x.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("part/", b"")
        zf.writestr("part/part.SchLib", b"s")
        zf.writestr("part/part.PcbLib", b"p")
    info = inspect_cse_zip(zp)
    assert info["ok"] is True
    assert info["extras"] == []


def test_inspect_flags_suspicious_members(tmp_path):
    members = cse_members(mpn="OK1")
    members["../evil.pas"] = b"evil"
    zp = make_zip(tmp_path / "x.zip", members)
    info = inspect_cse_zip(zp)
    assert info["ok"] is True  # read-only inspect still reports the libs
    assert info["suspicious"] == ["../evil.pas"]


# ----------------------------------------------------- escape detection


@pytest.mark.parametrize(
    "member,escapes",
    [
        ("../evil.pas", True),
        ("..\\evil.pas", True),
        ("a/../../evil.SchLib", True),
        ("/abs/evil.SchLib", True),
        ("C:/evil.SchLib", True),
        ("C:\\evil.SchLib", True),
        ("part/part.SchLib", False),
        ("a..b.SchLib", False),  # dots inside a name are not traversal
        ("part/.hidden.SchLib", False),
    ],
)
def test_member_escapes(member, escapes):
    assert _member_escapes(member) is escapes


# ---------------------------------------------------------------- extract


def test_extract_happy_path(tmp_path):
    zp = make_zip(
        tmp_path / "LIB_STM32F103C8T6.zip",
        cse_members(folder="STM32F103C8T6"),
    )
    dest = tmp_path / "staged"
    result = extract_cse_zip(zp, dest)
    assert result["ok"] is True
    assert result["mpn"] == "STM32F103C8T6"

    # Flattened into dest with original payloads.
    schlib = dest / "STM32F103C8T6.SchLib"
    pcblib = dest / "STM32F103C8T6.PcbLib"
    step = dest / "STM32F103C8T6.stp"
    assert schlib.read_bytes() == b"schlib-bytes"
    assert pcblib.read_bytes() == b"pcblib-bytes"
    assert step.read_bytes() == b"step-bytes"
    assert sorted(result["files"]) == sorted(
        str(p) for p in (schlib, pcblib, step)
    )
    assert result["extracted"] == {
        "schlib": str(schlib),
        "pcblib": str(pcblib),
        "step": str(step),
    }

    plan = result["install_plan"]
    assert [s["tool"] for s in plan] == [
        "lib_install_library",
        "lib_install_library",
        "lib_link_footprint",
        "lib_link_3d_model",
    ]
    # Parameter shapes match tools/library.py signatures.
    assert plan[0]["params"] == {"library_path": str(schlib)}
    assert plan[1]["params"] == {"library_path": str(pcblib)}
    assert plan[2]["params"] == {
        "component_name": "STM32F103C8T6",
        "footprint_name": "STM32F103C8T6",
        "footprint_library": "STM32F103C8T6.PcbLib",
    }
    assert plan[3]["params"] == {
        "component_name": "STM32F103C8T6",
        "model_path": str(step),
    }


def test_extract_without_step_drops_3d_link(tmp_path):
    zp = make_zip(tmp_path / "x.zip", cse_members(mpn="2N7002", step=False))
    result = extract_cse_zip(zp, tmp_path / "staged")
    assert result["ok"] is True
    tools = [s["tool"] for s in result["install_plan"]]
    assert tools == [
        "lib_install_library",
        "lib_install_library",
        "lib_link_footprint",
    ]
    assert result["extracted"]["step"] is None


def test_extract_pcblib_only_skips_links_except_3d(tmp_path):
    zp = make_zip(
        tmp_path / "x.zip",
        {"QFN50P400X400X80-25N.PcbLib": b"p", "QFN50P400X400X80-25N.stp": b"3d"},
    )
    result = extract_cse_zip(zp, tmp_path / "staged")
    assert result["ok"] is True
    tools = [s["tool"] for s in result["install_plan"]]
    # No SchLib: no symbol install, no footprint link; 3D link still valid.
    assert tools == ["lib_install_library", "lib_link_3d_model"]
    assert (
        result["install_plan"][1]["params"]["component_name"]
        == "QFN50P400X400X80-25N"
    )


def test_extract_schlib_only(tmp_path):
    zp = make_zip(tmp_path / "x.zip", {"PART1.SchLib": b"s"})
    result = extract_cse_zip(zp, tmp_path / "staged")
    assert result["ok"] is True
    assert [s["tool"] for s in result["install_plan"]] == ["lib_install_library"]


def test_extract_creates_missing_dest_dir(tmp_path):
    zp = make_zip(tmp_path / "x.zip", cse_members(mpn="P1"))
    dest = tmp_path / "a" / "b" / "staged"
    result = extract_cse_zip(zp, dest)
    assert result["ok"] is True
    assert (dest / "P1.SchLib").is_file()


def test_extract_ignores_extras(tmp_path):
    members = cse_members(mpn="P1")
    members["P1.epw"] = b"epw"
    members["notes.txt"] = b"n"
    zp = make_zip(tmp_path / "x.zip", members)
    dest = tmp_path / "staged"
    result = extract_cse_zip(zp, dest)
    assert result["ok"] is True
    assert not (dest / "P1.epw").exists()
    assert not (dest / "notes.txt").exists()


def test_extract_propagates_inspect_failure(tmp_path):
    zp = make_zip(tmp_path / "x.zip", {"readme.txt": b"t"})
    result = extract_cse_zip(zp, tmp_path / "staged")
    assert result["ok"] is False
    assert ".SchLib" in result["reason"]


def test_extract_rejects_zip_slip_unrecognized_member(tmp_path):
    members = cse_members(mpn="P1")
    members["../evil.pas"] = b"evil"
    (tmp_path / "zips").mkdir()
    zp = make_zip(tmp_path / "zips" / "x.zip", members)
    dest = tmp_path / "zips" / "staged"
    result = extract_cse_zip(zp, dest)
    assert result["ok"] is False
    assert "zip-slip" in result["reason"]
    assert "../evil.pas" in result["reason"]
    # Nothing written: not the payload, not the escape target.
    assert not dest.exists() or list(dest.iterdir()) == []
    assert not (tmp_path / "zips" / "evil.pas").exists()
    assert not (tmp_path / "evil.pas").exists()


def test_extract_rejects_zip_slip_recognized_member(tmp_path):
    members = {"../evil.SchLib": b"evil", "good.PcbLib": b"p"}
    zp = make_zip(tmp_path / "x.zip", members)
    dest = tmp_path / "staged"
    result = extract_cse_zip(zp, dest)
    assert result["ok"] is False
    assert not (tmp_path / "evil.SchLib").exists()
    assert not dest.exists() or list(dest.iterdir()) == []


def test_extract_rejects_absolute_member(tmp_path):
    members = cse_members(mpn="P1")
    members["C:\\evil.SchLib"] = b"evil"
    zp = make_zip(tmp_path / "x.zip", members)
    result = extract_cse_zip(zp, tmp_path / "staged")
    assert result["ok"] is False
    assert "zip-slip" in result["reason"]


def test_extract_accepts_str_paths(tmp_path):
    zp = make_zip(tmp_path / "x.zip", cse_members(mpn="P1"))
    result = extract_cse_zip(str(zp), str(tmp_path / "staged"))
    assert result["ok"] is True
