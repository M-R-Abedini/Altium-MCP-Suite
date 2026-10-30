"""The generated JSON and TOML must agree on the requested tool surface."""
import argparse
import ast
import json
import os
from pathlib import Path
import sys
import tomllib

import pytest
import setup

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('arguments', [['--toolset', 'minimal'], ['--toolset=minimal', 'serve', '--no-dashboard']])
def test_wrapper_selects_surface_before_import_and_preserves_coordination(monkeypatch, arguments):
    monkeypatch.setenv('EDA_AGENT_TOOLSET', 'full')
    monkeypatch.setenv('EDA_AGENT_BACKEND', 'altium')
    tree = ast.parse((ROOT / 'eda_stdio.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'configure_cli')
    namespace = {'argparse': argparse, 'os': os}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<stdio-configuration>', 'exec'), namespace)
    namespace['configure_cli'](arguments)
    assert os.environ['EDA_AGENT_TOOLSET'] == 'minimal'
    assert os.environ['EDA_AGENT_BACKEND'] == 'altium'
    call = next(i for i,n in enumerate(tree.body) if isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Call) and getattr(n.value.func, 'id', '') == 'configure_cli')
    server_import = next(i for i,n in enumerate(tree.body) if isinstance(n, ast.ImportFrom) and n.module == 'eda_agent.server')
    assert call < server_import


@pytest.mark.parametrize('mode', ['full', 'minimal'])
def test_generated_clients_select_the_same_toolset(tmp_path, monkeypatch, mode):
    monkeypatch.setenv('ALTIUM_MCP_RUNTIME',str(tmp_path/'runtime'))
    monkeypatch.setenv('EDA_AGENT_WORKSPACE',str(tmp_path/'workspace'))
    executable = tmp_path / 'X2.EXE'; executable.touch()
    monkeypatch.setattr(setup, '__file__', str(tmp_path / 'setup.py'))
    monkeypatch.setattr(setup.suite_config, 'configure_environment', lambda: {'eda': 'eda.PrjScr', 'legacy': 'legacy.PrjScr'})
    monkeypatch.setattr(sys, 'argv', ['setup.py', '--altium-exe', str(executable), '--toolset', mode])
    setup.main()
    json_config = json.loads((tmp_path / 'mcp.local.json').read_text())['mcpServers']['mcp-altium-suite']
    toml_config = tomllib.loads((tmp_path / 'codex.local.toml').read_text())['mcp_servers']['mcp-altium-suite']
    assert toml_config == json_config
    assert json_config['args'] == [str(tmp_path / 'suite_stdio.py'), '--toolset', mode,
                                  '--backend-config', str(tmp_path / 'suite-backends.local.json')]
    assert len(json.loads((tmp_path / 'mcp.local.json').read_text())['mcpServers']) == 1
    backends = json.loads((tmp_path / 'suite-backends.local.json').read_text())['backends']
    assert backends['eda']['args'] == [str(tmp_path / 'eda_stdio.py'), '--toolset', 'full', '--no-dashboard']
    assert backends['eda']['env'] == backends['legacy']['env']
    assert backends['legacy']['args'] == [str(tmp_path / 'suite_legacy_compat.py')]


def test_deprecated_separate_connections_migrate_to_one_gateway(tmp_path, monkeypatch):
    monkeypatch.setenv('ALTIUM_MCP_RUNTIME', str(tmp_path / 'runtime'))
    monkeypatch.setenv('EDA_AGENT_WORKSPACE', str(tmp_path / 'workspace'))
    executable = tmp_path / 'X2.EXE'; executable.touch()
    monkeypatch.setattr(setup, '__file__', str(tmp_path / 'setup.py'))
    monkeypatch.setattr(setup.suite_config, 'configure_environment', lambda: {'eda': 'eda.PrjScr', 'legacy': 'legacy.PrjScr'})
    monkeypatch.setattr(sys, 'argv', ['setup.py', '--altium-exe', str(executable), '--separate-servers'])
    setup.main()
    config = json.loads((tmp_path / 'mcp.local.json').read_text())['mcpServers']
    assert set(config) == {'mcp-altium-suite'}
    assert '--toolset' in config['mcp-altium-suite']['args']
    assert (tmp_path / 'suite-backends.local.json').is_file()


def test_setup_rerun_retains_existing_library_paths_and_options(tmp_path, monkeypatch):
    monkeypatch.setenv('ALTIUM_MCP_RUNTIME', str(tmp_path / 'runtime'))
    monkeypatch.setenv('EDA_AGENT_WORKSPACE', str(tmp_path / 'workspace'))
    executable = tmp_path / 'X2.EXE'; executable.touch()
    binary = tmp_path / 'library-server.exe'; binary.touch()
    existing_root = tmp_path / 'existing-libraries'; existing_root.mkdir()
    extra_root = tmp_path / 'extra-libraries'; extra_root.mkdir()
    library_config = tmp_path / 'libraries.local.json'
    library_config.write_text(json.dumps({'allowed_paths': [str(existing_root)],
                                         'logging': {'level': 'warn', 'audit_log_path': None}}))
    monkeypatch.setattr(setup, '__file__', str(tmp_path / 'setup.py'))
    monkeypatch.setattr(setup.suite_config, 'configure_environment', lambda: {'eda': 'eda', 'legacy': 'legacy'})
    for additional in (['--library-dir', str(extra_root)], []):
        monkeypatch.setattr(sys, 'argv', ['setup.py', '--altium-exe', str(executable),
                                        '--library-binary', str(binary)] + additional)
        setup.main()
    saved = json.loads(library_config.read_text())
    assert saved['allowed_paths'] == [str(existing_root), str(extra_root), str(tmp_path / 'runtime' / 'cloud-libraries')]
    assert saved['logging'] == {'level': 'warn', 'audit_log_path': None}
    workers = json.loads((tmp_path / 'suite-backends.local.json').read_text())['backends']
    assert workers['libraries']['args'] == [str(tmp_path / 'suite_libraries_native.py'), str(library_config)]
