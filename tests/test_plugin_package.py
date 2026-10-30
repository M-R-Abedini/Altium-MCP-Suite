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
                 'suite_libraries_native.py', 'suite_ole.py', 'suite_schlib_codec.py',
                 'suite_pcblib_codec.py', 'suite_pcb_primitives.py', 'suite_legacy_compat.py',
                 'suite_script_overlay.py', 'contracts/libraries-v0.3.0.json',
                 'contracts/legacy-tools.json', 'contracts/pcblib-default-board.json',
                 'coffeenmusic/server/batch_safety.py',
                 'coffeenmusic/server/symbol_placement_rules.txt',
                 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'eda-agent/LICENSE', 'eda-agent/NOTICE',
                 'coffeenmusic/LICENSE'):
        assert name in payload
    for root_document in ('.gitignore', 'CONTRIBUTING.md', 'CONNECTIVITY_VERIFICATION.md'):
        assert root_document in payload
    assert 'eda-agent/scripts/altium/Altium_MCP.pas' not in payload
    assert not any(name.startswith('altium-designer-mcp/') for name in payload)
    assert 'coffeenmusic/server/main.py' not in payload
    assert 'coffeenmusic/server/codex_stdio.py' not in payload
    assert json.loads(payload['plugin.json'])['license'] == 'MIT AND Apache-2.0'
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


@pytest.mark.parametrize('name', [
    'altium-designer-mcp/src/main.rs', 'libraries/backend.exe',
    'libraries/dependencies.crate', 'vendor/dependency/source.py',
])
def test_package_audit_rejects_retired_or_binary_implementation(source, name):
    payload = builder.package_payload(source)
    payload[name] = b'fixture'
    with pytest.raises(ValueError, match='implementation'):
        builder.audit_payload(payload)


def test_package_audit_rejects_incompatible_source_license(source):
    payload = builder.package_payload(source)
    payload['accidental_source.py'] = b'# SPDX-License-Identifier: GPL-3.0-or-later\n'
    with pytest.raises(ValueError, match='license'):
        builder.audit_payload(payload)


def test_current_source_and_upstream_inventory_exclude_retired_library_backend():
    assert not (ROOT / 'altium-designer-mcp').exists()
    upstream = json.loads((ROOT / 'UPSTREAM.json').read_text(encoding='utf-8'))
    assert set(upstream) == {'eda-agent', 'coffeenmusic'}
    assert {item['license'] for item in upstream.values()} <= {'MIT', 'Apache-2.0'}


def test_wheel_configuration_covers_all_native_project_members(source):
    members = builder.native_wheel_members(source)
    assert {'eda_agent/scripts/LegacyCompat.pas', 'eda_agent/scripts/LegacySandbox.pas'} <= set(members)
    metadata = source / 'eda-agent/pyproject.toml'
    text = metadata.read_text(encoding='utf-8')
    metadata.write_text('\n'.join(line for line in text.splitlines()
                                   if not line.startswith('"scripts/altium/LegacySandbox.pas"')), encoding='utf-8')
    with pytest.raises(ValueError, match='wheel closure.*LegacySandbox'):
        builder.package_payload(source)


def test_built_wheel_audit_detects_missing_or_stale_native_members(source, tmp_path):
    members = builder.native_wheel_members(source)
    wheel = tmp_path / 'eda_agent-fixture.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        for name, path in members.items():
            archive.writestr(name, path.read_bytes())
    assert builder.verify_native_wheel(wheel, source)['native_project_members_verified'] == len(members)
    with zipfile.ZipFile(wheel, 'w') as archive:
        for name, path in members.items():
            if name.endswith('LegacySandbox.pas'):
                continue
            archive.writestr(name, path.read_bytes())
    with pytest.raises(ValueError, match='missing.*LegacySandbox'):
        builder.verify_native_wheel(wheel, source)
    with zipfile.ZipFile(wheel, 'w') as archive:
        for name, path in members.items():
            archive.writestr(name, b'outdated' if name.endswith('LegacyCompat.pas') else path.read_bytes())
    with pytest.raises(ValueError, match='differs.*LegacyCompat'):
        builder.verify_native_wheel(wheel, source)
