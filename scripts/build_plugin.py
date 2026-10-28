"""Build a clean, reproducible plugin ZIP and local marketplace from source.

No executable dependencies, user configuration or build caches are bundled.
SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = (
    'plugin.json', 'mcp.json', 'requirements-runtime.txt', 'constraints-windows-py312.txt',
    'suite_stdio.py', 'suite_cloud_libraries.py', 'configure_suite.py', 'setup.py', 'deploy.py',
    'eda_stdio.py', 'bridge_coordination.py', 'suite_config.py', 'script_projects.py',
    'engine_identity.py', 'engine_queue.py', 'suite_reads.py', 'README.md',
    'LICENSE', 'THIRD_PARTY_NOTICES.md', 'UPSTREAM.json', 'MODIFICATIONS.md',
    '.gitignore', 'CONTRIBUTING.md', 'CONNECTIVITY_VERIFICATION.md',
    'scripts/plugin_runtime.py', 'scripts/launch_plugin.ps1', 'scripts/build_plugin.py',
    'eda-agent/pyproject.toml', 'eda-agent/README.md', 'eda-agent/LICENSE', 'eda-agent/NOTICE',
    'coffeenmusic/LICENSE', 'coffeenmusic/README.md',
    'altium-designer-mcp/Cargo.toml', 'altium-designer-mcp/Cargo.lock',
    'altium-designer-mcp/rust-toolchain.toml', 'altium-designer-mcp/rustfmt.toml',
    'altium-designer-mcp/LICENCE', 'altium-designer-mcp/README.md',
)
TREE_RULES = {
    'eda-agent/src': {'.py', '.html', '.css', '.js'},
    'eda-agent/scripts/altium': {'.pas', '.dfm', '.prjscr'},
    'coffeenmusic/server': {'.py', '.pas', '.dfm', '.prjscr', '.txt'},
    'skills': {'.md', '.yaml', '.yml', '.json', '.py', '.svg', '.png'},
    'docs': {'.md'},
    'altium-designer-mcp/src': {'.rs', '.txt'},
    'altium-designer-mcp/tests': {'.rs', '.py'},
    'altium-designer-mcp/docs': {'.md'},
    'altium-designer-mcp/config': {'.json'},
    'altium-designer-mcp/scripts': {'.ps1', '.py', '.md', '.json', '.txt', '.pas', '.prjscr', '.schlib', '.pcblib', '.bmp'},
}
SKIP_PARTS = {'.git', '.venv', '__pycache__', '.pytest_cache', 'target', 'dist',
              'repair-backups', '.runtime', '.tmp'}
PRIVATE_NAMES = {'config.json', 'local-settings.json', 'libraries.local.json', 'mcp.local.json',
                 'suite-backends.local.json', 'deployment-manifest.json', 'codex.local.toml'}


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def _safe_source(path, root):
    relative = path.relative_to(root)
    if any(part in SKIP_PARTS or part.startswith('.') for part in relative.parts) and relative.as_posix() != '.gitignore':
        return False
    if path.name.lower() in PRIVATE_NAMES or path.name.lower().startswith('.env'):
        return False
    if any(token in path.name.lower() for token in ('.local.', '.backup', '.bak', '.tmp')):
        return False
    if 'tests' in relative.parts and relative.parts[0] != 'altium-designer-mcp':
        return False
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise ValueError(f'Package input is a link: {relative}')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Package input escapes source root: {relative}')
    return True


def package_inputs(root=ROOT):
    root = Path(root).resolve()
    paths = {}
    for name in ROOT_FILES:
        path = root / name
        if not path.is_file():
            raise ValueError(f'Missing required package input: {name}')
        if not _safe_source(path, root):
            raise ValueError(f'Invalid explicit package input: {name}')
        paths[name] = path
    for directory, suffixes in TREE_RULES.items():
        for path in (root / directory).rglob('*'):
            if path.is_file() and path.suffix.lower() in suffixes and _safe_source(path, root):
                if path.relative_to(root).as_posix() == 'eda-agent/scripts/altium/Altium_MCP.pas':
                    project = root / 'eda-agent/scripts/altium/Altium_API.PrjScr'
                    if not any(line.strip().casefold() == 'documentpath=altium_mcp.pas'
                               for line in project.read_text(encoding='utf-8-sig').splitlines()):
                        continue  # Unreferenced generated build output is not source.
                paths[path.relative_to(root).as_posix()] = path
    for needed in ('skills/altium-circuit-design/SKILL.md', 'skills/altium-suite-setup/SKILL.md',
                   'docs/PLUGIN_PACKAGE.md', 'altium-designer-mcp/src/main.rs',
                   'altium-designer-mcp/src/mcp/server.rs', 'coffeenmusic/server/symbol_placement_rules.txt'):
        if needed not in paths:
            raise ValueError(f'Package is incomplete: {needed}')
    return dict(sorted(paths.items()))


def package_payload(root=ROOT):
    root = Path(root).resolve()
    payload = {name: path.read_bytes() for name, path in package_inputs(root).items()}
    manifest = json.loads(payload['plugin.json'])
    if manifest.get('name') != 'mcp-altium-suite' or not isinstance(manifest.get('version'), str):
        raise ValueError('Unexpected plugin identity/version')
    mcp = json.loads(payload['mcp.json'])
    servers = mcp.get('mcpServers', {})
    if set(servers) != {'mcp-altium-suite'}:
        raise ValueError('The plugin must bundle exactly one suite connection')
    server = servers['mcp-altium-suite']
    if server.get('cwd') != '${PLUGIN_ROOT}' or not any('${PLUGIN_ROOT}/scripts/launch_plugin.ps1' == arg for arg in server.get('args', [])):
        raise ValueError('Portable MCP launch must use the documented PLUGIN_ROOT placeholder')
    onboarding = manifest.get('extensions', {}).get('com.openai', {}).get('onboardingSkill', '')
    if not onboarding.startswith('./') or '..' in Path(onboarding).parts or onboarding[2:] not in payload:
        raise ValueError('The declared onboarding skill must be inside the package')
    contents = {'schema': 1, 'name': manifest['name'], 'version': manifest['version'],
                'files': {name: {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
                          for name, data in payload.items()},
                'license_scope': 'See THIRD_PARTY_NOTICES.md and per-backend license/notice files.',
                'native_binary_bundled': False}
    payload['PACKAGE_CONTENTS.json'] = json_bytes(contents)
    return dict(sorted(payload.items()))


def zip_payload(payload):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in sorted(payload.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data, compresslevel=6)
    return output.getvalue()


def _remove_owned(path, base):
    path, base = Path(path), Path(base).resolve()
    resolved = path.resolve()
    if resolved == base or not resolved.is_relative_to(base) or path.is_symlink():
        raise ValueError('Refusing cleanup outside the explicit output root')
    if path.exists():
        shutil.rmtree(path)


def build(root=ROOT, output=None, check_reproducible=False):
    root = Path(root).resolve()
    output = Path(output or root / 'dist').resolve()
    if output == root or output in root.parents:
        raise ValueError('Output must not replace the source root or one of its parents')
    payload = package_payload(root)
    archive = zip_payload(payload)
    if check_reproducible and archive != zip_payload(package_payload(root)):
        raise RuntimeError('Inputs changed or repeated ZIP build was not byte-identical')
    identity = json.loads(payload['plugin.json'])
    output.mkdir(parents=True, exist_ok=True)
    stage = output / ('.stage-' + uuid.uuid4().hex)
    package = stage / 'plugins' / identity['name']
    package.mkdir(parents=True)
    try:
        for name, data in payload.items():
            target = package / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        marketplace = {'name': 'altium-mcp-suite', 'interface': {'displayName': 'Altium MCP Suite'},
                       'plugins': [{'name': identity['name'],
                                    'source': {'source': 'local', 'path': './plugins/' + identity['name']},
                                    'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_USE'},
                                    'category': 'Productivity'}]}
        catalog = stage / '.agents/plugins/marketplace.json'
        catalog.parent.mkdir(parents=True)
        catalog.write_bytes(json_bytes(marketplace))
        destination = output / 'marketplace'
        if destination.exists():
            old_catalog = destination / '.agents/plugins/marketplace.json'
            if not old_catalog.is_file() or json.loads(old_catalog.read_bytes()).get('name') != marketplace['name']:
                raise ValueError('Existing marketplace output is not owned by this builder')
            _remove_owned(destination, output)
        stage.replace(destination)
        zip_path = output / f"{identity['name']}-{identity['version']}.zip"
        temporary = zip_path.with_name(zip_path.name + '.tmp-' + uuid.uuid4().hex)
        try:
            temporary.write_bytes(archive)
            os.replace(temporary, zip_path)
        finally:
            temporary.unlink(missing_ok=True)
        result = {'name': identity['name'], 'version': identity['version'],
                  'zip': str(zip_path), 'zip_sha256': hashlib.sha256(archive).hexdigest(),
                  'zip_size': len(archive), 'files': len(payload),
                  'marketplace': str(destination),
                  'plugin_root': str(destination / 'plugins' / identity['name']),
                  'reproducible_checked': bool(check_reproducible), 'native_binary_bundled': False}
        (output / 'artifacts.json').write_bytes(json_bytes(result))
        return result
    finally:
        _remove_owned(stage, output)


async def smoke(plugin_root, python=sys.executable):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    root = Path(plugin_root).resolve()
    with tempfile.TemporaryDirectory(prefix='altium-plugin-smoke-') as temporary:
        scratch = Path(temporary)
        config = scratch / 'backends.json'
        config.write_text('{"backends":{}}', encoding='utf-8')
        env = dict(os.environ, ALTIUM_SUITE_CLOUD_CACHE=str(scratch / 'cloud'),
                   ALTIUM_EXE=str(scratch / 'absent/X2.EXE'),
                   ALTIUM_MCP_RUNTIME=str(scratch / 'runtime'),
                   EDA_AGENT_WORKSPACE=str(scratch / 'workspace'))
        env.pop('PYTHONPATH', None)
        parameters = StdioServerParameters(command=str(python), args=[str(root / 'suite_stdio.py'),
            '--backend-config', str(config)], env=env, cwd=str(scratch))
        with (scratch / 'stderr.log').open('w', encoding='utf-8') as log:
            async with stdio_client(parameters, errlog=log) as streams:
                async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=30)) as client:
                    info = await client.initialize()
                    tools = await client.list_tools()
                    assert info.serverInfo.name == 'mcp altium suite'
                    assert {tool.name for tool in tools.tools} == {'suite_catalog', 'suite_invoke', 'suite_status'}
                    response = await client.call_tool('suite_invoke', {'name': 'cloud__cloud_library_providers'})
                    assert not response.isError
                    providers = response.structuredContent
                    assert {entry['id'] for entry in providers['providers']} == {'chilaboard', 'snapmagic', 'samacsys'}
        assert not (scratch / 'runtime').exists()
        return {'initialized': True, 'advertised_tools': 3, 'cloud_provider_dispatch': True,
                'native_editor_or_workers_started': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    parser.add_argument('--smoke', action='store_true', help='exercise clean artifact MCP without Altium or Rust')
    parser.add_argument('--check-reproducible', action='store_true')
    args = parser.parse_args(argv)
    result = build(ROOT, args.output, args.check_reproducible)
    if args.smoke:
        result['smoke'] = asyncio.run(smoke(result['plugin_root']))
        (args.output.resolve() / 'artifacts.json').write_bytes(json_bytes(result))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
