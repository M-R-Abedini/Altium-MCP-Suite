"""Independent client-publication recovery and profile-isolation checks."""
import json
import importlib.util
from pathlib import Path
import sys
import tomllib
from types import SimpleNamespace
import zipfile

import pytest

import configure_suite as clients


def package(tmp_path):
    root = tmp_path / "installed product"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/plugin_runtime.py").touch()
    return root


def test_client_symlink_refused_without_mutating_target(tmp_path):
    root = package(tmp_path)
    unrelated = tmp_path / "unrelated.json"
    before = b'{"unrelated_setting": "preserve exactly"}\r\n'
    unrelated.write_bytes(before)
    linked = tmp_path / "cursor.json"
    try:
        linked.symlink_to(unrelated)
    except OSError as error:
        pytest.skip(f"This test account cannot create file symlinks: {error}")
    with pytest.raises(ValueError, match="link"):
        clients.configure_clients("cursor", root, sys.executable, tmp_path / "private",
                                  config=linked, apply=True)
    assert unrelated.read_bytes() == before
    assert linked.is_symlink()


def test_multi_client_publication_failure_restores_previous_bytes(tmp_path, monkeypatch, capsys):
    root = package(tmp_path)
    home, roaming = tmp_path / "home", tmp_path / "roaming"
    paths = clients.client_config_paths(home, roaming)
    before = {}
    for name, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = (b'# keep formatting\r\nmodel = "configured"\r\n' if name == "codex" else
               b'{ "private_credential" : "must-not-be-printed", "arbitrary" : [1,2,3] }\r\n')
        path.write_bytes(raw)
        before[name] = raw
    atomic_write = clients._atomic_write
    failed = []
    def injected_failure(path, data):
        if path == paths["claude-desktop"]:
            failed.append(str(path))
            raise OSError("simulated storage failure")
        return atomic_write(path, data)
    monkeypatch.setattr(clients, "_atomic_write", injected_failure)
    with pytest.raises(OSError, match="storage failure"):
        clients.configure_clients("all", root, sys.executable, tmp_path / "private",
                                  home=home, appdata=roaming, apply=True)
    assert failed
    for name, path in paths.items():
        assert path.read_bytes() == before[name]
    captured = capsys.readouterr()
    assert "must-not-be-printed" not in captured.out + captured.err


def test_codex_home_override_is_honored_for_default_profile(tmp_path, monkeypatch):
    custom = tmp_path / "custom codex home"
    monkeypatch.setenv("CODEX_HOME", str(custom))
    assert clients.client_config_paths()["codex"] == custom / "config.toml"


def test_explicit_home_keeps_profile_tests_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "unrelated"))
    home = tmp_path / "requested profile"
    assert clients.client_config_paths(home=home)["codex"] == home / ".codex/config.toml"


def test_corresponding_source_vendor_paths_survive_archive_relocation(tmp_path, monkeypatch):
    build_script = Path(__file__).resolve().parents[1] / "installer/build_installer.py"
    spec = importlib.util.spec_from_file_location("independent_installer_review", build_script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    crate = tmp_path / "original crate"
    crate.mkdir()
    (crate / "Cargo.lock").write_text("# fixture\nversion=4\n")
    (crate / "LICENCE").write_text("Fixture license retained")
    binary = tmp_path / "fixture.exe"
    binary.write_bytes(b"Packaging fixture only")
    output = tmp_path / "release"
    output.mkdir()

    def vendor_response(command, **kwargs):
        # Cargo's source-replacement config retains the path argument given to
        # cargo vendor. Model that documented output without fetching crates.
        path_argument = command[-1]
        vendor = Path(path_argument)
        if not vendor.is_absolute():
            vendor = Path(kwargs["cwd"]) / vendor
        vendor.mkdir()
        (vendor / "fixture-dependency").mkdir()
        (vendor / "fixture-dependency/LICENSE").write_text("Fixture dependency license")
        config = '[source.crates-io]\nreplace-with="vendored-sources"\n[source.vendored-sources]\ndirectory=' + json.dumps(path_argument) + '\n'
        return SimpleNamespace(stdout=config, returncode=0)

    monkeypatch.setattr(builder.subprocess, "run", vendor_response)
    archive = builder.source_archive(crate, output, "cargo", tmp_path / "target", binary)
    extracted = tmp_path / "different machine directory"
    with zipfile.ZipFile(archive) as source:
        source.extractall(extracted)
    relocated = extracted / "altium-designer-mcp"
    config = tomllib.loads((relocated / ".cargo/config.toml").read_text())
    vendor_path = Path(config["source"]["vendored-sources"]["directory"])
    assert not vendor_path.is_absolute()
    assert (relocated / vendor_path / "fixture-dependency/LICENSE").read_text() == "Fixture dependency license"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI is a native credential-storage API")
def test_optional_token_real_dpapi_roundtrip_without_plaintext_persistence():
    import base64
    import ctypes
    from ctypes import wintypes
    from suite_hub import protect_token

    token = "generated-test-only-token-\u03a9"
    encrypted = base64.b64decode(protect_token(token))
    assert token.encode("utf-8") not in encrypted
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(encrypted)
    source = Blob(len(encrypted), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                       ctypes.POINTER(Blob)]
    crypt.CryptUnprotectData.restype = wintypes.BOOL
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    assert crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    try:
        assert ctypes.string_at(target.data, target.size).decode("utf-8") == token
    finally:
        kernel.LocalFree(target.data)
