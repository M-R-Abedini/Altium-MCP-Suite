"""Cross-runtime arbitration without launching Altium or editing a design."""
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock
from types import SimpleNamespace

import pytest
import engine_identity as identity
import bridge_coordination as coord

ROOT = Path(__file__).resolve().parents[1]


def test_foreign_runtime_cannot_bypass_unresolved_native_owner(tmp_path, monkeypatch):
    runtime_a, runtime_b = tmp_path/'a', tmp_path/'b'
    work_a, work_b = tmp_path/'wa', tmp_path/'wb'
    claim = identity.claim_editor(77, 100, runtime_a, work_a, tmp_path/'shared')
    original = claim.read_bytes()
    runtime_a.mkdir(); (runtime_a/'legacy-operation.json').write_text('unresolved mutation')
    monkeypatch.setattr(coord, 'RUNTIME', runtime_b)
    monkeypatch.setattr(coord, 'WORKSPACE', work_b)
    monkeypatch.setattr(coord, '_editor_state', lambda: dict(pid=77, blocked=False))
    monkeypatch.setattr(coord, '_session_started_at', lambda _: 100)
    real_claim = identity.claim_editor
    monkeypatch.setattr(identity, 'claim_editor', lambda p,s,r,w: real_claim(p,s,r,w,tmp_path/'shared'))
    launch = MagicMock(); probe = MagicMock()
    monkeypatch.setattr(coord.subprocess, 'Popen', launch)
    with pytest.raises(RuntimeError, match='another MCP runtime'):
        coord._ensure(probe)
    probe.assert_not_called(); launch.assert_not_called()
    assert claim.read_bytes() == original
    assert (runtime_a/'legacy-operation.json').read_text() == 'unresolved mutation'


def test_two_editors_are_rejected_even_when_one_is_foreground(monkeypatch):
    gui=SimpleNamespace(IsWindowVisible=lambda _:True,GetClassName=lambda _:'TDocumentForm',
                        IsWindowEnabled=lambda _:True,GetForegroundWindow=lambda:1,
                        EnumWindows=lambda callback,arg:[callback(h,arg) for h in (1,2)])
    monkeypatch.setitem(sys.modules,'win32gui',gui)
    monkeypatch.setitem(sys.modules,'win32process',SimpleNamespace(GetWindowThreadProcessId=lambda h:(1,h+100)))
    monkeypatch.setitem(sys.modules,'win32service',SimpleNamespace(OpenDesktop=MagicMock(side_effect=OSError('fallback'))))
    with pytest.raises(RuntimeError,match='found 2'):
        coord._editor_state()


def test_same_binding_survives_python_restart_and_pid_reuse_gets_new_identity(tmp_path):
    first = identity.claim_editor(77, 100, tmp_path/'r', tmp_path/'w', tmp_path/'shared')
    assert identity.claim_editor(77, 100, tmp_path/'r', tmp_path/'w', tmp_path/'shared') == first
    new = identity.claim_editor(77, 101, tmp_path/'different', tmp_path/'differentw', tmp_path/'shared')
    assert new != first and first.exists()


@pytest.mark.parametrize('content', ['{', '[]', '{}', 'null', '{"schema":true}', 'x'*16385])
def test_corrupt_binding_refuses_without_overwrite(tmp_path, content):
    target=identity.binding_path(77,100,tmp_path);target.write_text(content)
    with pytest.raises(RuntimeError,match='cannot be verified'):
        identity.claim_editor(77,100,tmp_path/'r',tmp_path/'w',tmp_path)
    assert target.read_text()==content


def test_explicit_stop_archives_binding_before_switching_runtime(tmp_path):
    target=identity.claim_editor(77,100,tmp_path/'a',tmp_path/'wa',tmp_path)
    old=target.read_bytes()
    archive=identity.reset_editor(77,100,tmp_path)
    assert not target.exists() and archive.read_bytes()==old
    identity.claim_editor(77,100,tmp_path/'b',tmp_path/'wb',tmp_path)
    assert json.loads(target.read_text())['runtime']==str(tmp_path/'b')


@pytest.mark.parametrize('pid,started', [(True,100),(0,100),(77,True),(77,float('nan')),(77,float('inf')),(77,10**400)])
def test_invalid_identity_cannot_publish_binding(tmp_path,pid,started):
    with pytest.raises(RuntimeError,match='invalid editor'):
        identity.claim_editor(pid,started,tmp_path/'r',tmp_path/'w',tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(sys.platform!='win32',reason='Real Windows named mutex across processes')
def test_different_runtime_processes_share_one_lock_and_no_same_thread_reentry(tmp_path,monkeypatch):
    monkeypatch.setattr(coord,'LOCK',tmp_path/'runtime-a'/'engine.lock')
    first=coord.try_lock();assert first is not None
    try:
        monkeypatch.setattr(coord,'LOCK',tmp_path/'runtime-b'/'engine.lock')
        assert coord.try_lock() is None  # One asyncio OS thread must not reenter.
        child=('import bridge_coordination as c; from pathlib import Path; '
               'c.LOCK=Path(__import__("sys").argv[1]); h=c.try_lock(); '
               'print("busy" if h is None else "acquired"); '
               'c.unlock(h) if h is not None else None')
        result=subprocess.run([sys.executable,'-c',child,str(tmp_path/'runtime-b'/'engine.lock')],
                              cwd=ROOT,capture_output=True,text=True,timeout=15)
        assert result.returncode==0 and result.stdout.strip()=='busy',result.stderr
        assert not (tmp_path/'runtime-b'/'engine.lock').exists()
    finally:
        coord.unlock(first)
    result=subprocess.run([sys.executable,'-c',child,str(tmp_path/'runtime-b'/'engine.lock')],
                          cwd=ROOT,capture_output=True,text=True,timeout=15)
    assert result.returncode==0 and result.stdout.strip()=='acquired',result.stderr


@pytest.mark.skipif(sys.platform!='win32',reason='Windows handle cleanup')
def test_file_lock_open_failure_releases_shared_mutex(tmp_path,monkeypatch):
    obstacle=tmp_path/'not-a-directory';obstacle.write_text('keep')
    monkeypatch.setattr(coord,'LOCK',obstacle/'engine.lock')
    with pytest.raises(OSError):coord.try_lock()
    monkeypatch.setattr(coord,'LOCK',tmp_path/'valid'/'engine.lock')
    lease=coord.try_lock();assert lease is not None
    coord.unlock(lease)
    assert obstacle.read_text()=='keep'
