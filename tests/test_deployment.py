from pathlib import Path
import deploy


def test_deployment_includes_import_dependencies_and_preserves_local_settings(tmp_path):
    destination = tmp_path/'installed'
    destination.mkdir()
    settings = destination/'local-settings.json';settings.write_text('{"local":true}')
    wrapper = destination/'eda_stdio.py';wrapper.write_text('old wrapper')
    manifest, backup, changed = deploy.deploy(destination)
    for module in deploy.ROOT_MODULES:
        assert module in manifest
        assert (destination/module).read_bytes() == (deploy.ROOT/module).read_bytes()
    for license_file in deploy.NOTICE_FILES:
        assert (destination/license_file).read_bytes() == (deploy.ROOT/license_file).read_bytes()
    assert str(Path('eda-agent/src/eda_agent/project_variants.py')) in manifest
    assert (destination / '.gitignore').read_bytes() == (deploy.ROOT / '.gitignore').read_bytes()
    assert settings.read_text() == '{"local":true}'
    deploy.rollback(destination, backup, changed)
    assert wrapper.read_text() == 'old wrapper'
    assert not (destination/'suite_config.py').exists()
    assert settings.read_text() == '{"local":true}'


def test_deployment_discovers_each_bundled_skill_and_excludes_caches():
    files = {path.relative_to(deploy.ROOT).as_posix() for path in deploy.deployment_files()}
    expected = {path.relative_to(deploy.ROOT).as_posix() for path in
                (deploy.ROOT / 'skills').glob('*/SKILL.md')}
    assert expected and expected <= files
    assert not any('__pycache__' in path for path in files)
    if 'skills/altium-suite-setup/SKILL.md' in expected:
        assert {'scripts/plugin_runtime.py', 'scripts/launch_plugin.ps1',
                'docs/PLUGIN_PACKAGE.md', 'requirements-runtime.txt', 'mcp.json',
                'plugin.json'} <= files
