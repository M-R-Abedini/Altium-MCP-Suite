import json
from pathlib import Path
import tomllib

import pytest

import configure_suite as migration


CONFIG = '''# personal config stays byte-for-byte
model = "existing-model"
instructions = """
[mcp_servers.altium]
This is not a real table.
"""
[projects.'D:\\example']
trust_level = "trusted"
[mcp_servers.altium]
command = "legacy-python"
args = ["legacy.py"]
disabled_tools = ["run_altium_script"]
[mcp_servers.altium.env]
ALTIUM_MCP_RUNTIME = "same-runtime"
[mcp_servers.other]
command = "other-server"
enabled = true
[mcp_servers.eda-agent]
command = "eda-python"
args = ["eda_stdio.py", "--toolset", "minimal"]
[mcp_servers.eda-agent.env]
EDA_AGENT_WORKSPACE = "existing-workspace"
[mcp_servers.altium-libraries]
command = "external-library.exe"
args = ["existing-config.json"]
tool_timeout_sec = 180
startup_timeout_sec = 75
'''


def test_migration_preserves_other_settings_and_exact_worker_paths(tmp_path):
    text, manifest = migration.render_migration(CONFIG, tmp_path, tmp_path / 'python.exe',
                                                tmp_path / 'backends.json', tmp_path / 'cloud')
    parsed = tomllib.loads(text)
    assert set(parsed['mcp_servers']) == {'other', 'mcp-altium-suite'}
    assert parsed['model'] == 'existing-model'
    assert parsed['instructions'] == tomllib.loads(CONFIG)['instructions']
    assert '[mcp_servers.other]\ncommand = "other-server"\nenabled = true\n' in text
    assert manifest['backends']['libraries']['command'] == 'external-library.exe'
    assert manifest['backends']['libraries']['args'] == ['existing-config.json']
    assert manifest['backends']['libraries']['timeout_seconds'] == 180
    assert manifest['backends']['libraries']['startup_timeout_seconds'] == 75
    assert manifest['backends']['legacy']['disabled_tools'] == ['run_altium_script']
    assert manifest['backends']['eda']['env'] == {'EDA_AGENT_WORKSPACE': 'existing-workspace'}
    assert manifest['backends']['eda']['args'] == ['eda_stdio.py', '--toolset', 'full', '--no-dashboard']
    manifest_path = tmp_path / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest))
    from suite_stdio import load_specs
    specs = {spec.name: spec for spec in load_specs(manifest_path)}
    assert specs['libraries'].timeout_seconds == 180
    assert specs['libraries'].startup_timeout_seconds == 75


def test_apply_backs_up_config_and_manifest_and_dry_run_has_no_side_effects(tmp_path):
    config = tmp_path / 'config.toml'; config.write_bytes(CONFIG.encode())
    destination = tmp_path / 'suite'; destination.mkdir()
    (destination / 'suite_stdio.py').touch()
    python = destination / 'python.exe'; python.touch()
    manifest = destination / 'suite-backends.local.json'; manifest.write_text('{"old": true}')
    preview = migration.migrate(config, destination, python, tmp_path / 'cloud')
    assert not preview['applied'] and not (tmp_path / 'backups').exists()
    result = migration.migrate(config, destination, python, tmp_path / 'cloud', apply=True)
    backup = Path(result['backup'])
    assert (backup / 'config.toml').read_bytes() == CONFIG.encode()
    assert json.loads((backup / manifest.name).read_text()) == {'old': True}
    assert set(json.loads(manifest.read_text())['backends']) == {'eda', 'legacy', 'libraries'}
    assert result['applied']
    with pytest.raises(ValueError, match='already exists'):
        migration.migrate(config, destination, python, tmp_path / 'cloud', apply=True)


def test_failed_config_publication_restores_previous_manifest(tmp_path, monkeypatch):
    config = tmp_path / 'config.toml'; config.write_bytes(CONFIG.encode())
    destination = tmp_path / 'suite'; destination.mkdir()
    (destination / 'suite_stdio.py').touch()
    python = destination / 'python.exe'; python.touch()
    manifest = destination / 'suite-backends.local.json'; manifest.write_bytes(b'prior-manifest')
    original_write = migration._atomic_write
    def fail_config(path, content):
        if path == config:
            raise OSError('publication refused')
        original_write(path, content)
    monkeypatch.setattr(migration, '_atomic_write', fail_config)
    with pytest.raises(OSError, match='publication refused'):
        migration.migrate(config, destination, python, tmp_path / 'cloud', apply=True)
    assert config.read_bytes() == CONFIG.encode()
    assert manifest.read_bytes() == b'prior-manifest'


@pytest.mark.parametrize('change', [
    lambda text: text.replace('command = "legacy-python"', 'url = "https://example.invalid"'),
    lambda text: text + '\n[mcp_servers.mcp-altium-suite]\ncommand = "existing"\n',
    lambda text: text.replace('command = "legacy-python"', 'command = "legacy-python"\nenv_vars = ["TOKEN"]'),
])
def test_unhandled_configurations_fail_before_writes(tmp_path, change):
    with pytest.raises(ValueError):
        migration.render_migration(change(CONFIG), tmp_path, tmp_path / 'python.exe',
                                   tmp_path / 'backends.json', tmp_path / 'cloud')
