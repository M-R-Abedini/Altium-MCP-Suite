"""Configuration failures and explicit recovery, using only temporary files."""
import builtins
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock

import pytest
import bridge_coordination as coord
import suite_config


@pytest.mark.parametrize('content', ['{', '[]', 'null', '{"altium_exe":[]}',
                                   '{"altium_exe":false}', '{"altium_exe":""}'])
def test_bad_settings_report_the_file_and_recovery_step(tmp_path, monkeypatch, content):
    monkeypatch.setattr(suite_config, 'ROOT', tmp_path)
    settings = tmp_path / 'local-settings.json'
    settings.write_text(content)
    with pytest.raises(RuntimeError, match='local-settings.json'):
        suite_config.settings()
    assert settings.read_text() == content


def test_settings_accept_bom_and_absence(tmp_path, monkeypatch):
    monkeypatch.setattr(suite_config, 'ROOT', tmp_path)
    assert suite_config.settings() == {}
    (tmp_path / 'local-settings.json').write_text('{"altium_exe":"C:/Altium/X2.EXE"}', encoding='utf-8-sig')
    assert suite_config.settings()['altium_exe'] == 'C:/Altium/X2.EXE'


@pytest.mark.parametrize('count', [0, 2])
def test_optimized_python_cannot_generate_an_invalid_script(tmp_path, count):
    for directory in ('eda-agent/scripts/altium', 'coffeenmusic/server/AltiumScript'):
        (tmp_path / directory).mkdir(parents=True)
    template = tmp_path / 'coffeenmusic/server/AltiumScript/Altium_API.pas'
    template.write_text('__ALTIUM_MCP_EXCHANGE_DIR__' * count)
    program = ('import sys; from pathlib import Path; import suite_config; '
               'suite_config.ROOT = Path(sys.argv[1]); suite_config.prepare_runtime()')
    env = dict(os.environ, ALTIUM_MCP_RUNTIME=str(tmp_path / 'runtime'))
    result = subprocess.run([sys.executable, '-O', '-c', program, str(tmp_path)],
                            cwd=suite_config.ROOT, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'exactly one exchange-directory placeholder' in result.stderr
    assert not list((tmp_path / 'runtime/scripts').glob('legacy-*/Altium_API.pas'))


@pytest.mark.parametrize('content', [b'{', b'[]', b'null', b'{}', b'\xff',
                                    b'{"request_path":[]}'])
def test_explicit_reset_archives_bad_marker_and_withdraws_pending_requests(tmp_path, monkeypatch, content):
    runtime, work = tmp_path / 'runtime', tmp_path / 'work'
    runtime.mkdir(); work.mkdir()
    exchange = runtime / 'legacy-exchange'; exchange.mkdir()
    inbox = exchange / 'request.json'; inbox.write_text('pending edit')
    queued = work / 'request_edit.json'; queued.write_text('queued edit')
    progress = work / 'progress_edit.json'; progress.write_text('{}')
    launch = runtime / 'launch.json'; launch.write_text('[]')
    for name, value in {'RUNTIME': runtime, 'WORKSPACE': work, 'LAUNCH_STATE': launch}.items():
        monkeypatch.setattr(coord, name, value)
    monkeypatch.setattr(coord, 'engine_lock', contextlib.nullcontext)
    monkeypatch.setattr(coord, '_editor_state', lambda: {'pid': 77, 'blocked': False})
    launcher = MagicMock(); monkeypatch.setattr(coord.subprocess, 'Popen', launcher)
    marker = coord._legacy_marker(); marker.write_bytes(content)
    with pytest.raises(RuntimeError, match='ownership cannot be verified'):
        coord._guard_legacy({'pid': 77})
    assert marker.read_bytes() == content
    coord.reset_after_manual_stop()
    assert not marker.exists()
    assert not inbox.exists() and not queued.exists() and not progress.exists() and not launch.exists()
    archive, = runtime.glob('legacy-operation.corrupt-*.json')
    assert archive.read_bytes() == content
    launcher.assert_not_called()


@pytest.mark.parametrize('target', ['external', 'directory', 'internal'])
def test_reset_preserves_invalid_marker_targets(tmp_path, monkeypatch, target):
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    protected = (tmp_path / 'design.SchDoc' if target == 'external' else
                 runtime / 'design.SchDoc' if target == 'internal' else runtime)
    design = runtime / 'design.SchDoc' if target == 'directory' else protected
    design.write_text('design')
    monkeypatch.setattr(coord, 'RUNTIME', runtime)
    monkeypatch.setattr(coord, 'WORKSPACE', runtime)
    monkeypatch.setattr(coord, 'LAUNCH_STATE', runtime / 'launch.json')
    monkeypatch.setattr(coord, 'engine_lock', contextlib.nullcontext)
    monkeypatch.setattr(coord, '_editor_state', lambda: {'pid': 77, 'blocked': False})
    coord._legacy_marker().write_text(json.dumps({'request_path': str(protected)}))
    coord.reset_after_manual_stop()
    assert design.read_text() == 'design'
    assert len(list(runtime.glob('legacy-operation.corrupt-*.json'))) == 1


def test_manual_reset_withdraws_legacy_inbox_even_without_a_marker(tmp_path, monkeypatch):
    inbox = tmp_path / 'legacy-exchange' / 'request.json'
    inbox.parent.mkdir(); inbox.write_text('pending edit')
    monkeypatch.setattr(coord, 'RUNTIME', tmp_path)
    monkeypatch.setattr(coord, 'WORKSPACE', tmp_path)
    monkeypatch.setattr(coord, 'LAUNCH_STATE', tmp_path / 'launch.json')
    monkeypatch.setattr(coord, 'engine_lock', contextlib.nullcontext)
    monkeypatch.setattr(coord, '_editor_state', lambda: {'pid': 77, 'blocked': False})
    coord.reset_after_manual_stop()
    assert not inbox.exists()


def test_engine_lock_explains_unsupported_platform_before_creating_files(tmp_path, monkeypatch):
    original = builtins.__import__
    def unavailable(name, *args, **kwargs):
        if name == 'msvcrt':
            raise ModuleNotFoundError('msvcrt')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', unavailable)
    monkeypatch.setattr(coord, 'LOCK', tmp_path / 'engine.lock')
    with pytest.raises(RuntimeError, match='requires Windows'):
        coord.try_lock()
    assert not coord.LOCK.exists()
