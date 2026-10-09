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
    executable = tmp_path / 'X2.EXE'; executable.touch()
    monkeypatch.setattr(setup, '__file__', str(tmp_path / 'setup.py'))
    monkeypatch.setattr(setup.suite_config, 'configure_environment', lambda: {'eda': 'eda.PrjScr', 'legacy': 'legacy.PrjScr'})
    monkeypatch.setattr(sys, 'argv', ['setup.py', '--altium-exe', str(executable), '--toolset', mode])
    setup.main()
    json_config = json.loads((tmp_path / 'mcp.local.json').read_text())['mcpServers']['eda-agent']
    toml_config = tomllib.loads((tmp_path / 'codex.local.toml').read_text())['mcp_servers']['eda-agent']
    assert toml_config == json_config
    assert json_config['args'] == [str(tmp_path / 'eda_stdio.py'), '--toolset', mode]
