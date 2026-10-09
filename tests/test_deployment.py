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
    assert settings.read_text() == '{"local":true}'
    deploy.rollback(destination, backup, changed)
    assert wrapper.read_text() == 'old wrapper'
    assert not (destination/'suite_config.py').exists()
    assert settings.read_text() == '{"local":true}'
