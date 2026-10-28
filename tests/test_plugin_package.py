"""Observable package contents and actual clean-artifact protocol checks."""
import asyncio
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('altium_plugin_builder', ROOT / 'scripts/build_plugin.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'source'
    for relative, path in builder.package_inputs(ROOT).items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    return root


def test_same_inputs_produce_identical_zip_and_manifest(source, tmp_path):
    first = builder.build(source, tmp_path / 'one', check_reproducible=True)
    second = builder.build(source, tmp_path / 'two', check_reproducible=True)
    assert Path(first['zip']).read_bytes() == Path(second['zip']).read_bytes()
    assert first['zip_sha256'] == second['zip_sha256']
    with zipfile.ZipFile(first['zip']) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
        contents = json.loads(archive.read('PACKAGE_CONTENTS.json'))
        for name, entry in contents['files'].items():
            data = archive.read(name)
            assert hashlib.sha256(data).hexdigest() == entry['sha256']
            assert len(data) == entry['size']


def test_private_generated_and_binary_sentinels_are_excluded(source, tmp_path):
    sentinel = b'THIS PRIVATE FILE MUST NOT BE SHIPPED'
    excluded = ['.env', 'local-settings.json', 'suite-backends.local.json',
                '.venv/secret.py', 'repair-backups/old.py', 'deployment-manifest.json',
                'coffeenmusic/server/config.json', 'coffeenmusic/server/screenshot_sch.png',
                'coffeenmusic/server/__pycache__/cached.py',
                'altium-designer-mcp/target/release/altium-designer-mcp.exe',
                'altium-designer-mcp/src/private.local.json',
                'altium-designer-mcp/.tmp/runtime.py']
    for name in excluded:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(sentinel)
    payload = builder.package_payload(source)
    assert not set(excluded) & set(payload)
    assert not any(sentinel in data for data in payload.values())
    assert not any(Path(name).suffix.lower() in {'.exe', '.dll', '.pyc', '.bak', '.log'} for name in payload)


def test_all_runtime_sources_and_license_material_are_present(source):
    payload = builder.package_payload(source)
    for name in ('suite_stdio.py', 'scripts/launch_plugin.ps1', 'scripts/plugin_runtime.py',
                 'eda-agent/src/eda_agent/server.py', 'eda-agent/scripts/altium/Main.pas',
                 'coffeenmusic/server/main.py', 'altium-designer-mcp/Cargo.lock',
                 'altium-designer-mcp/src/altium/pcblib/assets/file_version_info.txt',
                 'altium-designer-mcp/docs/AGENT_GUIDE.md',
                 'altium-designer-mcp/scripts/samples/symbols.SchLib',
                 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'eda-agent/LICENSE', 'eda-agent/NOTICE',
                 'coffeenmusic/LICENSE', 'altium-designer-mcp/LICENCE'):
        assert name in payload
    for root_document in ('.gitignore', 'CONTRIBUTING.md', 'CONNECTIVITY_VERIFICATION.md'):
        assert root_document in payload
    assert 'eda-agent/scripts/altium/Altium_MCP.pas' not in payload
    rust_files = {path.relative_to(source).as_posix() for path in
                  (source / 'altium-designer-mcp/src').rglob('*') if path.is_file()}
    assert rust_files <= set(payload)
    assert json.loads(payload['PACKAGE_CONTENTS.json'])['native_binary_bundled'] is False


def test_onboarding_skill_links_and_portable_launch_resolve(source):
    payload = builder.package_payload(source)
    identity = json.loads(payload['plugin.json'])
    server = json.loads(payload['mcp.json'])['mcpServers']['mcp-altium-suite']
    assert identity['extensions']['com.openai']['onboardingSkill'] == './skills/altium-suite-setup/SKILL.md'
    assert server['cwd'] == '${PLUGIN_ROOT}'
    assert '${PLUGIN_ROOT}/scripts/launch_plugin.ps1' in server['args']
    for name, data in payload.items():
        if name.startswith('skills/') and name.endswith('.md'):
            for link in re.findall(r'\[[^\]]+\]\(([^)]+)\)', data.decode('utf-8')):
                if link.startswith(('https:', 'http:', '#')):
                    continue
                resolved = (source / name).parent.joinpath(link.split('#', 1)[0]).resolve()
                assert resolved.is_relative_to(source.resolve()) and resolved.is_file(), (name, link)


def test_rebuild_replaces_only_its_managed_marketplace(source, tmp_path):
    output = tmp_path / 'out'
    built = builder.build(source, output)
    user_file = output / 'unrelated.txt'; user_file.write_text('keep')
    again = builder.build(source, output)
    assert built['zip_sha256'] == again['zip_sha256'] and user_file.read_text() == 'keep'
    wrong = tmp_path / 'wrong' / 'marketplace'; wrong.mkdir(parents=True)
    (wrong / 'user-data.txt').write_text('keep this too')
    with pytest.raises(ValueError, match='not owned'):
        builder.build(source, wrong.parent)
    assert (wrong / 'user-data.txt').read_text() == 'keep this too'


def test_invalid_output_or_missing_required_source_is_rejected(source):
    with pytest.raises(ValueError, match='source root'):
        builder.build(source, source)
    (source / 'scripts/launch_plugin.ps1').unlink()
    with pytest.raises(ValueError, match='Missing required'):
        builder.package_payload(source)


def test_clean_staged_artifact_serves_real_minimal_mcp_without_native_tools(source, tmp_path):
    result = builder.build(source, tmp_path / 'dist')
    outcome = asyncio.run(builder.smoke(result['plugin_root']))
    assert outcome == {'initialized': True, 'advertised_tools': 3,
                       'cloud_provider_dispatch': True, 'native_editor_or_workers_started': False}
    marketplace = json.loads((Path(result['marketplace']) / '.agents/plugins/marketplace.json').read_text())
    assert marketplace['plugins'][0]['source']['path'] == './plugins/mcp-altium-suite'
