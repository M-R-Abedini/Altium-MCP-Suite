"""Client configuration, private-runtime migration and offline installer contracts."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tomllib
from types import SimpleNamespace
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import configure_suite as clients
import altium_installation
import suite_hub
from scripts import plugin_runtime as runtime

spec = importlib.util.spec_from_file_location('product_installer_build', ROOT / 'installer/build_installer.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.mark.parametrize('client,key', [('claude-desktop', 'mcpServers'), ('claude-code', 'mcpServers'),
                                     ('cursor', 'mcpServers'), ('windsurf', 'mcpServers'), ('vscode', 'servers')])
def test_json_clients_preserve_unrelated_and_are_idempotent(client, key):
    before = {key: {'other': {'command': 'unchanged', 'env': {'KEY': 'private'}}},
              'projects': {'unrelated': {'data': 1}}, 'feature': [1, 2]}
    definition = {'command': 'python', 'args': ['space path/launcher.py'], 'env': {'DATA': 'private'}}
    rendered, changed = clients.render_client(json.dumps(before), client, definition)
    assert changed
    after = json.loads(rendered)
    assert after[key]['other'] == before[key]['other'] and after['projects'] == before['projects']
    assert after['feature'] == [1, 2]
    assert after[key][clients.SERVER_NAME]['args'] == ['space path/launcher.py']
    assert clients.render_client(rendered, client, definition) == (rendered, False)


def test_codex_tables_preserve_comments_settings_and_plugin_duplicate():
    original = '# preserved\nmodel = "existing"\n[mcp_servers.other]\ncommand = "other"\n'
    definition = {'command': 'python', 'args': ['suite'], 'env': {'DATA': 'value'}}
    rendered, changed = clients.render_client(original, 'codex', definition)
    assert changed and rendered.startswith(original.rstrip())
    assert tomllib.loads(rendered)['mcp_servers']['other'] == {'command': 'other'}
    repeated, _ = clients.render_client(rendered, 'codex', definition)
    assert tomllib.loads(repeated) == tomllib.loads(rendered)
    enabled = '[plugins."mcp-altium-suite@altium-mcp-suite"]\nenabled = true\n'
    assert clients.render_client(enabled, 'codex', definition) == (enabled, False)


def test_jsonc_failure_preflights_all_clients_before_writes(tmp_path):
    root = tmp_path / 'package'; (root / 'scripts').mkdir(parents=True)
    (root / 'scripts/plugin_runtime.py').touch()
    appdata = tmp_path / 'roaming'
    cursor = tmp_path / '.cursor/mcp.json'; cursor.parent.mkdir()
    cursor.write_text('{ // keep this comment\n "mcpServers": {} }')
    with pytest.raises(ValueError, match='JSONC'):
        clients.configure_clients('all', root, Path(sys.executable), tmp_path / 'data',
                                  home=tmp_path, appdata=appdata, apply=True)
    assert not (tmp_path / '.codex/config.toml').exists()
    assert not (appdata / 'Claude/claude_desktop_config.json').exists()
    assert '// keep' in cursor.read_text()


def test_client_apply_keeps_exact_backup_and_plan_is_read_only(tmp_path):
    root = tmp_path / 'package'; (root / 'scripts').mkdir(parents=True)
    (root / 'scripts/plugin_runtime.py').touch()
    config = tmp_path / 'cursor.json'
    old = b'{"other":true,"mcpServers":{"old":{"command":"x"}}}\r\n'
    config.write_bytes(old)
    data = tmp_path / 'private'
    plan = clients.configure_clients('cursor', root, sys.executable, data, config=config)
    assert not plan['applied'] and config.read_bytes() == old and not data.exists()
    done = clients.configure_clients('cursor', root, sys.executable, data, config=config, apply=True)
    assert done['applied'] and (Path(done['backup']) / 'cursor.json').read_bytes() == old
    assert json.loads(config.read_text())['mcpServers']['old'] == {'command': 'x'}


def make_adopted(tmp_path):
    old = tmp_path / 'old'; old.mkdir()
    (old / 'eda_stdio.py').touch()
    (old / 'coffeenmusic/server').mkdir(parents=True)
    (old / 'coffeenmusic/server/codex_stdio.py').touch()
    altium = tmp_path / 'X2.EXE'; altium.touch()
    binary = old / 'rust.exe'; binary.write_bytes(b'not executed in this test')
    config = old / 'libraries.json'; config.write_text(json.dumps({'allowed_paths': [str(tmp_path)], 'logging': {'level': 'warn'}}))
    common = {'ALTIUM_EXE': str(altium), 'ALTIUM_MCP_RUNTIME': str(tmp_path / 'native'),
              'EDA_AGENT_WORKSPACE': str(tmp_path / 'workspace')}
    template = {'backends': {
        'eda': {'command': sys.executable, 'args': [str(old / 'eda_stdio.py')], 'env': common,
                'disabled_tools': ['danger'], 'timeout_seconds': 77},
        'legacy': {'command': sys.executable, 'args': [str(old / 'coffeenmusic/server/codex_stdio.py')],
                   'env': dict(common), 'enabled_tools': ['only'], 'startup_timeout_seconds': 35},
        'libraries': {'command': str(binary), 'args': [str(config)], 'disabled_tools': ['write']}}}
    path = tmp_path / 'backends.json'; path.write_text(json.dumps(template))
    data = tmp_path / 'private'; runtime.adopt(path, sys.executable, data, probe=False)
    return data, old, template


def test_detach_preserves_identity_restrictions_and_copies_dependencies(tmp_path, monkeypatch):
    data, old, before = make_adopted(tmp_path)
    monkeypatch.setattr(runtime.sys, 'platform', 'win32')
    monkeypatch.setattr(runtime, 'install_runtime', lambda python, root: None)
    def fake_run(command, **kwargs):
        if '-m' in command and 'venv' in command:
            python = Path(command[-1]) / 'Scripts/python.exe'; python.parent.mkdir(parents=True); python.touch()
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runtime.subprocess, 'run', fake_run)
    runtime.detach(data, ROOT, probe=False)
    state, after = runtime.checked_state(data)
    assert state['mode'] == 'self-contained'
    assert Path(state['python']).is_relative_to(data)
    assert after['backends']['eda']['disabled_tools'] == ['danger']
    assert after['backends']['eda']['timeout_seconds'] == 77
    assert after['backends']['legacy']['enabled_tools'] == ['only']
    assert after['backends']['legacy']['startup_timeout_seconds'] == 35
    for name in ('eda', 'legacy'):
        assert after['backends'][name]['env']['ALTIUM_MCP_RUNTIME'] == before['backends'][name]['env']['ALTIUM_MCP_RUNTIME']
        assert after['backends'][name]['env']['EDA_AGENT_WORKSPACE'] == before['backends'][name]['env']['EDA_AGENT_WORKSPACE']
    library = after['backends']['libraries']
    assert Path(library['command']).is_relative_to(data)
    assert json.loads(Path(library['args'][0]).read_text()) == json.loads((old / 'libraries.json').read_text())
    assert list((data / 'backups').glob('*/state.json'))


def test_failed_detach_does_not_publish_partial_state(tmp_path, monkeypatch):
    data, _, _ = make_adopted(tmp_path)
    before = (data / 'state.json').read_bytes()
    monkeypatch.setattr(runtime.sys, 'platform', 'win32')
    monkeypatch.setattr(runtime.subprocess, 'run', lambda *a, **kw: None)
    def failed(*args):
        raise RuntimeError('install failed')
    monkeypatch.setattr(runtime, 'install_runtime', failed)
    with pytest.raises(RuntimeError, match='install failed'):
        runtime.detach(data, ROOT, probe=False)
    assert (data / 'state.json').read_bytes() == before


def test_runtime_install_is_regular_wheel_not_editable(tmp_path, monkeypatch):
    (tmp_path / 'requirements-runtime.txt').write_text('-c constraints.txt\n-e ./eda-agent\npillow>=11\n')
    observed = []
    def capture(command, **kwargs):
        observed.append(Path(command[-1]).read_text())
    monkeypatch.setattr(runtime.subprocess, 'run', capture)
    runtime.install_runtime(Path(sys.executable), tmp_path)
    assert '-e ' not in observed[0]
    assert (tmp_path / 'eda-agent').as_uri() in observed[0]
    assert '-c "' + (tmp_path / 'constraints.txt').as_posix() + '"' in observed[0]


def test_real_pip_parses_runtime_paths_with_spaces(tmp_path, monkeypatch):
    root = tmp_path / 'package with spaces'; root.mkdir()
    (root / 'requirements-runtime.txt').write_text('-c constraints.txt\n-e ./eda-agent\njsonschema>=4\n')
    (root / 'constraints.txt').write_text('jsonschema==4.26.0\n')
    (root / 'eda-agent').mkdir()
    parsed = []
    def parse_real(command, **kwargs):
        # Exercise pip's real recursive requirements/options parser without installing.
        from pip._internal.network.session import PipSession
        from pip._internal.req.req_file import parse_requirements
        parsed.extend(parse_requirements(command[-1], session=PipSession()))
    monkeypatch.setattr(runtime.subprocess, 'run', parse_real)
    runtime.install_runtime(Path(sys.executable), root)
    assert any(item.constraint and item.requirement == 'jsonschema==4.26.0' for item in parsed)
    assert any(item.requirement == (root / 'eda-agent').as_uri() and not item.is_editable for item in parsed)


def test_altium_discovery_is_bounded_and_startup_plan_is_honest(tmp_path):
    root = tmp_path / 'Altium/AD26'; root.mkdir(parents=True)
    (root / 'X2.EXE').touch()
    found = altium_installation.detect_installations([root, root, tmp_path / 'missing'])
    assert len(found) == 1 and found[0]['version_hint'] == '26' and not found[0]['version_verified']
    project = tmp_path / 'bridge.PrjScr'; project.touch()
    plan = altium_installation.startup_plan(project)
    assert not plan['automatic_registration_applied'] and plan['auto_loads_project']
    assert not plan['auto_executes_bridge']


def test_embedded_archive_hash_and_traversal_are_checked_before_extraction(tmp_path):
    archive = tmp_path / 'python.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('../escape.txt', 'bad')
    with pytest.raises(ValueError, match='SHA-256'):
        installer.extract_verified(archive, tmp_path / 'extracted', '0' * 64)
    with pytest.raises(ValueError, match='Unsafe archive'):
        installer.extract_verified(archive, tmp_path / 'extracted', installer.digest(archive))
    assert not (tmp_path / 'escape.txt').exists() and not (tmp_path / 'extracted').exists()


def test_optional_registration_never_stores_plaintext(tmp_path, monkeypatch):
    monkeypatch.setattr(suite_hub, 'protect_token', lambda token: 'encrypted-value')
    result = suite_hub.save_registration(tmp_path, 'secret-token')
    assert result['stored'] and not result['service_verified']
    assert not result['free_core_requires_registration']
    assert 'secret-token' not in (tmp_path / 'auth.json').read_text()


def test_rust_corresponding_source_vendor_config_is_relocatable(tmp_path, monkeypatch):
    crate = tmp_path / 'crate'; crate.mkdir()
    (crate / 'Cargo.lock').write_text('lock')
    (crate / 'Cargo.toml').write_text('[package]\nname="example"\n')
    binary = tmp_path / 'rust.exe'; binary.write_bytes(b'binary')
    output = tmp_path / 'out'; output.mkdir()
    def vendor(command, cwd, **kwargs):
        assert command == ['cargo', 'vendor', '--locked', 'vendor']
        destination = Path(cwd) / command[-1]; destination.mkdir()
        (destination / 'dependency.txt').write_text('source')
        os.utime(destination / 'dependency.txt', (1, 1))
        return SimpleNamespace(stdout='[source.vendored-sources]\ndirectory = "vendor"\n')
    monkeypatch.setattr(installer.subprocess, 'run', vendor)
    archive = installer.source_archive(crate, output, 'cargo', tmp_path / 'target', binary)
    with zipfile.ZipFile(archive) as zipped:
        config = zipped.read('altium-designer-mcp/.cargo/config.toml').decode()
        assert 'directory = "vendor"' in config and str(tmp_path) not in config
        assert zipped.read('altium-designer-mcp/vendor/dependency.txt') == b'source'
        provenance = json.loads(zipped.read('altium-designer-mcp/BINARY_PROVENANCE.json'))
        assert provenance['binary_sha256'] == installer.digest(binary)
