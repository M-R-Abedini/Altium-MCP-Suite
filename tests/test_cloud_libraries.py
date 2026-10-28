import hashlib
import io
import json
from pathlib import Path
import stat
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest

import suite_cloud_libraries as cloud


LIB = cloud.CFB_MAGIC + bytes(1016)
REV = "a" * 40
LIBPATH = "AltiumSCHLIB/test.SchLib"


@pytest.fixture
def public_repo(monkeypatch):
    catalog = b"NAME,TYPE,DESCRIPTION,PATH\nTEST,SCHLIB,Precision test IC,AltiumSCHLIB/test.SchLib\n"
    license_data = b"GNU GENERAL PUBLIC LICENSE\nVersion 3\n"
    blobs = {cloud.CATALOG: catalog, "LICENSE": license_data, LIBPATH: LIB}
    tree = {"truncated": False, "tree": [
        {"type": "blob", "path": name, "size": len(value), "sha": cloud._git_hash(value)}
        for name, value in blobs.items()]}
    calls = []

    def fetch(url, limit, deadline):
        calls.append(url)
        assert time.monotonic() < deadline
        if url == cloud.API + "/commits/main":
            return json.dumps({"sha": REV}).encode()
        if url == cloud.API + f"/git/trees/{REV}?recursive=1":
            return json.dumps(tree).encode()
        prefix = cloud.RAW + "/" + REV + "/"
        assert url.startswith(prefix)
        return blobs[url[len(prefix):]]

    monkeypatch.setattr(cloud, "_fetch", fetch)
    return calls, blobs, tree


def test_search_download_is_revision_pinned_and_repeat_uses_cache(tmp_path, public_repo):
    calls, _, _ = public_repo
    result = cloud.search("precision TEST", str(tmp_path), kind="symbol")
    assert result["total"] == 1 and result["revision"] == REV
    assert result["results"][0]["library_path"] == LIBPATH
    assert result["license"]["declared"] == "GPL-3.0"
    assert len(calls) == 4
    record = cloud.download(LIBPATH, str(tmp_path), revision=REV)
    assert Path(record["path"]).read_bytes() == LIB
    assert Path(record["license"]["path"]).read_bytes().startswith(b"GNU")
    assert record["sha256"] == hashlib.sha256(LIB).hexdigest()
    assert record["validation"]["pin_mapping"] == "unverified"
    assert record["installed_into_altium"] is False
    assert len(calls) == 5
    assert cloud.download(LIBPATH, str(tmp_path), revision=REV)["download_cache_hit"] is True
    assert len(calls) == 5


def test_catalog_hash_mismatch_rejects_before_index_publication(tmp_path, public_repo):
    _, blobs, _ = public_repo
    blobs[cloud.CATALOG] += b"modified"
    with pytest.raises(ValueError, match="Catalog Git blob"):
        cloud.search("", str(tmp_path))
    assert not (tmp_path / "chilaboard/index.json").exists()


def test_download_rejects_revision_mismatch_and_non_catalog_path(tmp_path, public_repo):
    cloud.search("", str(tmp_path))
    with pytest.raises(ValueError, match="revision changed"):
        cloud.download(LIBPATH, str(tmp_path), revision="b" * 40)
    with pytest.raises(ValueError, match="absent"):
        cloud.download("AltiumSCHLIB/unknown.SchLib", str(tmp_path))


def test_download_detects_corrupt_cached_library(tmp_path, public_repo):
    result = cloud.download(LIBPATH, str(tmp_path))
    Path(result["path"]).write_bytes(LIB[:-1] + b"x")
    with pytest.raises(ValueError, match="pinned Git blob"):
        cloud.download(LIBPATH, str(tmp_path))


def test_download_rejects_html_and_lfs_pointer_even_with_valid_hash(tmp_path, public_repo):
    _, blobs, tree = public_repo
    value = b"<html>not an Altium library</html>"
    blobs[LIBPATH] = value
    next(item for item in tree["tree"] if item["path"] == LIBPATH).update(size=len(value), sha=cloud._git_hash(value))
    with pytest.raises(ValueError, match="compound-file"):
        cloud.download(LIBPATH, str(tmp_path))


def make_zip(path, entries):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)


def test_stage_zip_preserves_original_extracts_cad_and_license_not_executables(tmp_path):
    source = tmp_path / "vendor.zip"
    make_zip(source, [("Altium/chip.SchLib", LIB), ("Altium/chip.PcbLib", LIB),
                      ("LICENSE.txt", b"Provider terms"), ("setup.exe", b"do not run"),
                      ("installer.vbs", b"do not run")])
    result = cloud.stage_local(str(source), provider="snapmagic", cache_dir=str(tmp_path / "cache"),
                               source_url="https://www.snapeda.com/", license_note="See retained terms")
    assert Path(result["original_path"]).read_bytes() == source.read_bytes()
    assert len(result["files"]) == 3
    assert not any(Path(item["path"]).suffix in {".exe", ".vbs"} for item in result["files"])
    assert all(Path(item["path"]).is_relative_to(tmp_path / "cache") for item in result["files"])
    assert result["installed_into_altium"] is False


@pytest.mark.parametrize("name", ["../escape.SchLib", "/absolute.SchLib", "C:/drive.SchLib", "dir\\escape.SchLib",
                                 "dir/file.SchLib:ads", "CON.SchLib", "a/../b.SchLib", "a./b.SchLib"])
def test_zip_path_attacks_rejected_before_publication(tmp_path, name):
    source = tmp_path / "bad.zip"
    make_zip(source, [("good.SchLib", LIB), (name, LIB)])
    if "\\" in name:
        # Windows' zipfile writer normalizes separators; exercise a hostile
        # archive that actually retains backslashes in both filename records.
        source.write_bytes(source.read_bytes().replace(name.replace("\\", "/").encode(), name.encode()))
    cache = tmp_path / "cache"
    with pytest.raises(ValueError, match="ZIP"):
        cloud.stage_local(str(source), provider="other", cache_dir=str(cache))
    assert not (cache / "local").exists()


def test_zip_symlink_and_case_collision_rejected(tmp_path):
    source = tmp_path / "bad.zip"
    with zipfile.ZipFile(source, "w") as archive:
        link = zipfile.ZipInfo("link.SchLib")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "target")
    with pytest.raises(ValueError, match="Symlink"):
        cloud.stage_local(str(source), provider="other", cache_dir=str(tmp_path / "cache"))
    make_zip(source, [("TEST.SchLib", LIB), ("test.schlib", LIB)])
    with pytest.raises(ValueError, match="case-colliding"):
        cloud.stage_local(str(source), provider="other", cache_dir=str(tmp_path / "cache"))


def test_zip_compression_bomb_and_absent_cad_rejected(tmp_path):
    source = tmp_path / "bad.zip"
    make_zip(source, [("giant.step", b"0" * 1000000)])
    with pytest.raises(ValueError, match="expansion"):
        cloud.stage_local(str(source), provider="other", cache_dir=str(tmp_path / "cache"))
    make_zip(source, [("setup.exe", b"anything")])
    with pytest.raises(ValueError, match="no supported CAD"):
        cloud.stage_local(str(source), provider="other", cache_dir=str(tmp_path / "cache"))


def test_stage_epw_honestly_reports_no_conversion_and_uses_configured_cache(tmp_path, monkeypatch):
    source = tmp_path / "part.epw"
    source.write_bytes(b"provider-specific EPW")
    monkeypatch.setenv("ALTIUM_SUITE_CLOUD_CACHE", str(tmp_path / "cache"))
    result = cloud.call_tool("cloud_library_stage_local", {"source_path": str(source), "provider": "samacsys"})
    assert "not implemented" in result["conversion"]
    assert result["license"]["status"] == "not_provided"
    assert Path(result["files"][0]["path"]).read_bytes() == source.read_bytes()


def test_local_unknown_executable_and_invalid_cad_rejected(tmp_path):
    source = tmp_path / "setup.exe"
    source.write_bytes(b"executable")
    with pytest.raises(ValueError, match="Supported local"):
        cloud.stage_local(str(source), provider="other", cache_dir=str(tmp_path / "cache"))
    source = tmp_path / "bad.SchLib"
    source.write_bytes(b"HTML")
    with pytest.raises(ValueError, match="compound-file"):
        cloud.stage_local(str(source), provider="other", cache_dir=str(tmp_path / "cache"))


@pytest.mark.parametrize("path", ["../bad.SchLib", "AltiumSCHLIB/../../x.SchLib", "https://localhost/evil.SchLib", "AltiumSCHLIB\\x.SchLib",
                                  "AltiumSCHLIB/CON.SchLib", "AltiumSCHLIB/evil.SchLib.", "AltiumSCHLIB/evil\x01.SchLib"])
def test_download_path_validation_happens_before_network(tmp_path, monkeypatch, path):
    monkeypatch.setattr(cloud, "_fetch", lambda *args: pytest.fail("network must not be called"))
    with pytest.raises(ValueError):
        cloud.download(path, str(tmp_path))


def test_public_network_adapter_denies_arbitrary_origins_and_redirects():
    for url in ["http://api.github.com/", "https://localhost/", "https://api.github.com:443/", "https://user@api.github.com/"]:
        with pytest.raises(ValueError, match="origin"):
            cloud._fetch(url, 100, time.monotonic() + 5)
    with pytest.raises(ValueError, match="redirect"):
        cloud._NoRedirect().redirect_request(None, None, 302, "", {}, "http://localhost/")


def test_providers_do_not_claim_unauthorized_integrations_and_tools_reject_extra_args():
    status = {item["id"]: item for item in cloud.providers()["providers"]}
    assert status["chilaboard"]["automated"] is True
    assert status["snapmagic"]["automated"] is status["samacsys"]["automated"] is False
    with pytest.raises(ValueError, match="Unknown tool arguments"):
        cloud.call_tool("cloud_library_providers", {"secret": "not accepted"})


def test_catalog_accepts_current_cp1252_data_and_counts_malformed_rows():
    data = b"NAME,TYPE,DESCRIPTION,PATH\nTEST,SCHLIB,Test \xae,AltiumSCHLIB/test.SchLib\nbad,row\n"
    rows, skipped, encoding = cloud._parse_catalog(data)
    assert encoding == "cp1252" and skipped == 1 and len(rows) == 1
    assert "\u00ae" in rows[0]["DESCRIPTION"]


def test_relative_or_unconfigured_cache_and_bool_limits_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("ALTIUM_SUITE_CLOUD_CACHE", raising=False)
    with pytest.raises(ValueError, match="explicit absolute"):
        cloud._root(None)
    with pytest.raises(ValueError, match="absolute"):
        cloud._root("relative")
    with pytest.raises(ValueError, match="integer"):
        cloud.search("", str(tmp_path), limit=True)


def test_atomic_immutable_publication_handles_concurrent_identical_writers(tmp_path):
    target = tmp_path / "object.SchLib"
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: cloud._immutable(target, LIB), range(12)))
    assert results == [target] * 12 and target.read_bytes() == LIB
    assert not list(tmp_path.glob("*.tmp"))
    with pytest.raises(ValueError, match="changed"):
        cloud._immutable(target, LIB + b"modified")
    assert target.read_bytes() == LIB


def test_failed_atomic_publication_leaves_no_partial_object(tmp_path, monkeypatch):
    def fail(*_):
        raise OSError("simulated publication failure")
    monkeypatch.setattr(cloud.os, "link", fail)
    target = tmp_path / "object.SchLib"
    with pytest.raises(OSError, match="simulated"):
        cloud._immutable(target, LIB)
    assert not target.exists() and not list(tmp_path.glob("*.tmp"))
