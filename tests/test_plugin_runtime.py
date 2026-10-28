"""Isolated runtime/configuration and real Windows launcher checks; no Altium."""
import asyncio
from datetime import timedelta
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('suite_plugin_runtime', ROOT / 'scripts/plugin_runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def make_template(tmp_path):
    old = tmp_path / 'old package'; old.mkdir()
    (old / 'eda_stdio.py').touch()
    (old / 'coffeenmusic/server').mkdir(parents=True)
    (old / 'coffeenmusic/server/codex_stdio.py').touch()
    altium = tmp_path / 'X2.EXE'; altium.touch()
    allowed = tmp_path / 'libraries'; allowed.mkdir()
    libconfig = tmp_path / 'libraries.json'
    libconfig.write_text(json.dumps({'allowed_paths': [str(tmp_path)], 'logging': {'level': 'warn'}}))
    common = {'ALTIUM_MCP_RUNTIME': str(tmp_path / 'native-runtime'),
              'EDA_AGENT_WORKSPACE': str(tmp_path / 'native-workspace'), 'ALTIUM_EXE': str(altium)}
    template = {'backends': {
        'eda': {'command': sys.executable, 'args': [str(old / 'eda_stdio.py'), '--toolset', 'minimal'],
                'env': dict(common), 'timeout_seconds': 123, 'disabled_tools': ['blocked'], 'enabled_tools': None},
        'legacy': {'command': sys.executable, 'args': [str(old / 'coffeenmusic/server/codex_stdio.py')],
                   'env': dict(common), 'startup_timeout_seconds': 42},
        'libraries': {'command': sys.executable, 'args': [str(libconfig)], 'timeout_seconds': 180},
    }}
    path = tmp_path / 'backends.json'; path.write_text(json.dumps(template))
    return path, template, altium


def make_root(path):
    path.mkdir(parents=True)
    (path / 'eda_stdio.py').touch()
    (path / 'coffeenmusic/server').mkdir(parents=True)
    (path / 'coffeenmusic/server/codex_stdio.py').touch()
    return path


def test_adopt_preserves_settings_and_upgrade_uses_current_package(tmp_path):
    path, original, altium = make_template(tmp_path)
    data = tmp_path / 'private-data'
    result = runtime.adopt(path, Path(sys.executable), data, probe=False)
    assert result['cloud_cache_allowed'] and result['configured']
    state, template = runtime.checked_state(data)
    assert template == original
    assert state['altium_exe'] == str(altium)
    first = make_root(tmp_path / 'version one')
    second = make_root(tmp_path / 'version two')
    _, first_manifest = runtime.launch_configuration(data, first)
    _, repeated = runtime.launch_configuration(data, first)
    assert first_manifest == repeated
    _, upgraded = runtime.launch_configuration(data, second)
    assert upgraded != first_manifest
    resolved = json.loads(upgraded.read_text())['backends']
    assert resolved['eda']['args'] == [str(second / 'eda_stdio.py'), '--toolset', 'full', '--no-dashboard']
    assert resolved['legacy']['args'][0] == str(second / 'coffeenmusic/server/codex_stdio.py')
    assert resolved['eda']['env'] == dict(original['backends']['eda']['env'], EDA_AGENT_BACKEND='altium')
    assert resolved['eda']['disabled_tools'] == ['blocked']
    assert resolved['libraries'] == original['backends']['libraries']
    assert resolved['eda']['timeout_seconds'] == 123
    assert runtime.checked_state(data)[1] == original


def test_tampered_manifest_and_state_pair_are_refused(tmp_path):
    path, _, _ = make_template(tmp_path)
    data = tmp_path / 'private'
    runtime.adopt(path, Path(sys.executable), data, probe=False)
    root = make_root(tmp_path / 'package')
    _, manifest = runtime.launch_configuration(data, root)
    manifest.write_bytes(b'changed')
    with pytest.raises(ValueError, match='launch manifest was changed'):
        runtime.launch_configuration(data, root)
    template_path = data / 'backend-template.json'
    altered = json.loads(template_path.read_text()); altered['backends']['eda']['timeout_seconds'] = 1
    template_path.write_text(json.dumps(altered))
    with pytest.raises(ValueError, match='state/template pair'):
        runtime.checked_state(data)


def test_invalid_workspace_cannot_write_adoption_state(tmp_path):
    path, template, _ = make_template(tmp_path)
    template['backends']['legacy']['env']['EDA_AGENT_WORKSPACE'] = str(tmp_path / 'foreign')
    path.write_text(json.dumps(template))
    data = tmp_path / 'private'
    with pytest.raises(ValueError, match='disagree'):
        runtime.adopt(path, Path(sys.executable), data, probe=False)
    assert not data.exists()


@pytest.mark.parametrize('selection', ['environment', 'cli'])
def test_non_altium_backend_selection_is_refused(tmp_path, selection):
    path, template, _ = make_template(tmp_path)
    if selection == 'environment':
        template['backends']['eda']['env']['EDA_AGENT_BACKEND'] = 'kicad'
    else:
        template['backends']['eda']['args'] += ['--backend', 'easyeda']
    path.write_text(json.dumps(template))
    data = tmp_path / 'private'
    with pytest.raises(ValueError, match='Altium backend'):
        runtime.adopt(path, Path(sys.executable), data, probe=False)
    assert not data.exists()


@pytest.mark.parametrize('record', [{'schema': 1, 'workspace': 'relative'}, {'schema': 2, 'workspace': 'C:/wrong'}])
def test_malformed_persisted_workspace_is_not_normalized_into_validity(tmp_path, record):
    path, template, _ = make_template(tmp_path)
    native = Path(template['backends']['eda']['env']['ALTIUM_MCP_RUNTIME']); native.mkdir()
    (native / 'workspace-identity.json').write_text(json.dumps(record))
    with pytest.raises(ValueError, match='persisted native workspace identity'):
        runtime.adopt(path, Path(sys.executable), tmp_path / 'private', probe=False)


def test_explicit_readoption_retains_recoverable_previous_pair(tmp_path):
    path, template, _ = make_template(tmp_path)
    data = tmp_path / 'private'
    runtime.adopt(path, Path(sys.executable), data, probe=False)
    old_state = (data / 'state.json').read_bytes()
    old_template = (data / 'backend-template.json').read_bytes()
    template['backends']['eda']['timeout_seconds'] = 456
    path.write_text(json.dumps(template))
    runtime.adopt(path, Path(sys.executable), data, probe=False)
    backup = next((data / 'backups').iterdir())
    assert (backup / 'state.json').read_bytes() == old_state
    assert (backup / 'backend-template.json').read_bytes() == old_template
    assert runtime.checked_state(data)[1]['backends']['eda']['timeout_seconds'] == 456


def test_data_root_is_stable_and_explicit_paths_are_absolute(tmp_path, monkeypatch):
    monkeypatch.delenv('ALTIUM_SUITE_PLUGIN_DATA', raising=False)
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    assert runtime.data_directory() == tmp_path / 'codex/plugins/data/mcp-altium-suite'
    monkeypatch.setenv('ALTIUM_SUITE_PLUGIN_DATA', str(tmp_path / 'override'))
    assert runtime.data_directory() == tmp_path / 'override'
    assert runtime.data_directory(tmp_path / 'explicit') == tmp_path / 'explicit'
    with pytest.raises(ValueError, match='absolute'):
        runtime.data_directory('relative')


def test_doctor_is_readonly_and_no_native_or_subprocess_when_probe_disabled(tmp_path, monkeypatch):
    path, _, _ = make_template(tmp_path)
    data = tmp_path / 'private'
    runtime.adopt(path, Path(sys.executable), data, probe=False)
    root = make_root(tmp_path / 'package')
    before = {p.relative_to(data): p.read_bytes() for p in data.rglob('*') if p.is_file()}
    monkeypatch.setattr(runtime.subprocess, 'run', lambda *a, **k: pytest.fail('Unexpected subprocess'))
    output = runtime.doctor(data, root, probe=False)
    assert output['configured'] and output['cloud_cache_allowed']
    assert not output['native_editor_probed']
    after = {p.relative_to(data): p.read_bytes() for p in data.rglob('*') if p.is_file()}
    assert before == after


def test_actual_python_dependency_probe_does_not_load_native_suite():
    info = runtime.probe_python(Path(sys.executable))
    assert info['python'][:2] == [3, 12]
    assert {'mcp', 'jsonschema', 'pydantic', 'psutil', 'Pillow', 'networkx', 'numpy', 'olefile'} <= set(info['packages'])


def test_normal_serve_only_starts_current_gateway_without_installing(tmp_path, monkeypatch):
    path, _, _ = make_template(tmp_path)
    data = tmp_path / 'private'
    runtime.adopt(path, Path(sys.executable), data, probe=False)
    package = make_root(tmp_path / 'new package')
    calls = []
    def execute(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runtime.subprocess, 'run', execute)
    result = runtime.main(['--data-dir', str(data), '--plugin-root', str(package), 'serve'])
    assert result == 0 and len(calls) == 1
    command, options = calls[0]
    assert Path(command[0]).samefile(sys.executable)
    assert command[1:4] == ['-B', '-u', str(package / 'suite_stdio.py')]
    assert command[4:7] == ['--toolset', 'minimal', '--backend-config']
    assert options['cwd'] == package
    assert options['env']['ALTIUM_SUITE_CLOUD_CACHE'] == runtime.checked_state(data)[0]['cloud_cache']
    assert not (tmp_path / 'native-runtime').exists()


def test_bootstrap_preflights_workspace_before_installing_dependencies(tmp_path, monkeypatch):
    altium = tmp_path / 'X2.EXE'; altium.touch()
    binary = tmp_path / 'library.exe'; binary.touch()
    native = tmp_path / 'runtime'; native.mkdir()
    (native / 'workspace-identity.json').write_text(json.dumps({'schema': 1, 'workspace': 'relative'}))
    args = SimpleNamespace(altium_exe=altium, build_libraries=False, library_binary=binary,
                           runtime_dir=native, workspace=None, library_dir=[])
    monkeypatch.setattr(runtime.sys, 'platform', 'win32')
    monkeypatch.setattr(runtime.subprocess, 'run', lambda *a, **k: pytest.fail('Install ran before preflight'))
    data = tmp_path / 'private'
    with pytest.raises(ValueError, match='workspace'):
        runtime.bootstrap(args, data, ROOT)
    assert not data.exists()


def test_bootstrap_rerun_keeps_library_roots_options_and_backup(tmp_path, monkeypatch):
    altium = tmp_path / 'X2.EXE'; altium.touch()
    binary = tmp_path / 'library.exe'; binary.touch()
    old = tmp_path / 'oldlibs'; old.mkdir()
    extra = tmp_path / 'newlibs'; extra.mkdir()
    data = tmp_path / 'private'; data.mkdir()
    previous = {'allowed_paths': [str(old)], 'logging': {'level': 'warn'}}
    (data / 'libraries.json').write_text(json.dumps(previous))
    args = SimpleNamespace(altium_exe=altium, build_libraries=False, library_binary=binary,
                           runtime_dir=tmp_path / 'runtime', workspace=tmp_path / 'workspace', library_dir=[extra])
    calls = []
    def execute(command, **kwargs):
        calls.append(command)
        if command[1:3] == ['-m', 'venv']:
            python = Path(command[3]) / 'Scripts/python.exe'; python.parent.mkdir(parents=True); python.touch()
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runtime.sys, 'platform', 'win32')
    monkeypatch.setattr(runtime.subprocess, 'run', execute)
    monkeypatch.setattr(runtime, 'probe_python', lambda *a: {'python': [3, 12, 0]})
    output = runtime.bootstrap(args, data, ROOT)
    assert output['ready']
    current = json.loads((data / 'libraries.json').read_text())
    assert current['allowed_paths'] == [str(old), str(extra), str(data / 'cloud-libraries')]
    assert current['logging'] == previous['logging']
    assert any(json.loads(p.read_text()) == previous for p in (data / 'backups').rglob('libraries.json'))
    assert any(command[1:4] == ['-m', 'pip', 'install'] for command in calls)


def launcher_parameters(root, data):
    return StdioServerParameters(command='powershell.exe', args=['-NoLogo', '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File', str(root / 'scripts/launch_plugin.ps1'), '-DataDir', str(data)],
        env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}, cwd=str(data.parent))


@pytest.mark.skipif(sys.platform != 'win32', reason='Real Windows PowerShell launcher')
def test_unconfigured_launcher_status_needs_no_python_and_preserves_unicode(tmp_path):
    async def check():
        data = tmp_path / 'not configured'
        async with stdio_client(launcher_parameters(ROOT, data)) as streams:
            async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=20)) as client:
                info = await client.initialize()
                assert info.serverInfo.name == 'mcp altium suite'
                listed = await client.list_tools()
                assert [tool.name for tool in listed.tools] == ['suite_status']
                status = await client.call_tool('suite_status', {})
                assert status.structuredContent['setup_required'] is True
                assert status.structuredContent['configured'] is False
                assert not status.structuredContent['native_editor_probed']
                unicode_name = 'شماتیک_日本語'
                invalid = await client.call_tool(unicode_name, {})
                assert invalid.isError and unicode_name in invalid.content[0].text
        assert not data.exists()
    asyncio.run(check())


@pytest.mark.skipif(sys.platform != 'win32', reason='Real Windows PowerShell launcher')
def test_configured_launcher_relays_unicode_stderr_and_repeated_launch(tmp_path):
    path, _, _ = make_template(tmp_path)
    data = tmp_path / 'private'
    runtime.adopt(path, Path(sys.executable), data, probe=False)
    package = make_root(tmp_path / 'installed package')
    (package / 'scripts').mkdir()
    for name in ('plugin_runtime.py', 'launch_plugin.ps1'):
        shutil.copyfile(ROOT / 'scripts' / name, package / 'scripts' / name)
    (package / 'suite_stdio.py').write_text('''import asyncio,json,sys
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
server=Server("mcp altium suite")
@server.list_tools()
async def tools():
 return [types.Tool(name="echo",inputSchema={"type":"object","properties":{"text":{"type":"string"}}})]
@server.call_tool()
async def call(name,arguments):
 print("relay diagnostic",file=sys.stderr,flush=True)
 return types.CallToolResult(content=[types.TextContent(type="text",text=arguments["text"])],structuredContent={"text":arguments["text"]})
async def main():
 async with stdio_server() as streams: await server.run(*streams,server.create_initialization_options())
asyncio.run(main())
''', encoding='utf-8')
    async def check():
        for index in range(2):
            with (tmp_path / f'child-{index}.log').open('w', encoding='utf-8') as log:
                async with stdio_client(launcher_parameters(package, data), errlog=log) as streams:
                    async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=30)) as client:
                        await client.initialize()
                        value = 'شماتیک، مقاومت Ω 日本語'
                        response = await client.call_tool('echo', {'text': value})
                        assert response.structuredContent == {'text': value}
            assert 'relay diagnostic' in (tmp_path / f'child-{index}.log').read_text()
        assert len(list((data / 'launch-manifests').glob('*.json'))) == 1
        assert not (tmp_path / 'native-runtime').exists()
    asyncio.run(check())
