"""Configuration and native-wiring checks; live timing is in smoke_stdio --live."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from pathlib import Path

import pytest
import bridge_coordination as coord
from pydantic import ValidationError
from eda_agent.config import AltiumConfig, MCPRuntimeConfig

ROOT = Path(__file__).resolve().parents[1]


def test_idle_release_default_survives_an_older_config(tmp_path):
    config=AltiumConfig(workspace_dir=tmp_path)
    config.config_file_path.write_text(json.dumps({'auto_shutdown_ms':600000}))
    config.reload_runtime_config()
    assert config.runtime.engine_idle_release_ms==2000
    config.write_runtime_config()
    assert json.loads(config.config_file_path.read_text())['engine_idle_release_ms']==2000


def test_idle_release_can_be_disabled_explicitly_but_not_negative():
    assert MCPRuntimeConfig(engine_idle_release_ms=0).engine_idle_release_ms==0
    with pytest.raises(ValidationError):MCPRuntimeConfig(engine_idle_release_ms=-1)


def test_native_idle_deadline_is_between_handlers_and_pings_do_not_renew_it():
    source=(ROOT/'eda-agent/scripts/altium/Dispatcher.pas').read_text(encoding='utf-8')
    loop=source.split('While Running Do',1)[1]
    assert loop.index("StopReason := 'engine_idle_release'")<loop.index('HadRequest := ProcessSingleRequest(0)')
    assert "If (StatusLastCommand <> 'application.ping') Or StatusWorkReserved Then\n                    LastWorkMs := LastActivityMs;" in loop
    assert "LowerCase(ExtractJsonValue(Params, 'command_pending')) = 'true'" in source
    main=(ROOT/'eda-agent/scripts/altium/Main.pas').read_text(encoding='utf-8')
    assert "ExtractJsonValue(Content, 'engine_idle_release_ms')" in main
    assert 'EngineIdleReleaseMs  := 2000;' in main


def test_launch_probe_waits_until_native_startup_has_finished(tmp_path,monkeypatch):
    work=tmp_path/'work';work.mkdir()
    script=tmp_path/'script.PrjScr';script.touch();exe=tmp_path/'X2.exe';exe.touch()
    for name,value in {'WORKSPACE':work,'RUNTIME':tmp_path,'LAUNCH_STATE':tmp_path/'state.json',
                       'SCRIPT':script,'EXE':exe}.items():monkeypatch.setattr(coord,name,value)
    monkeypatch.setattr(coord,'_guard_editor',lambda:None)
    monkeypatch.setattr(coord,'_handler_busy',lambda:False)
    launched=[]
    monkeypatch.setattr(coord.subprocess,'CREATE_NO_WINDOW',0,raising=False)
    monkeypatch.setattr(coord.subprocess,'Popen',lambda *a,**kw:launched.append(True) or SimpleNamespace(pid=1))
    waits=[]
    def sleep(seconds):
        waits.append(seconds)
        if len(waits)==2:(work/'bridge-ready.json').write_text('{}')
    monkeypatch.setattr(coord.time,'sleep',sleep)
    calls=[]
    def probe(timeout):
        calls.append(timeout)
        if not launched:raise TimeoutError()
        assert (work/'bridge-ready.json').exists()
        return {'pong':True}
    coord._ensure(probe)
    assert calls==[2.0,3.0,2.0] and len(waits)==2


def test_foreground_preflight_reserves_time_for_real_command(monkeypatch):
    monkeypatch.setattr(coord,'_ensure',lambda probe:probe(2))
    execute=MagicMock(return_value={'pong':True})
    bridge=object()
    coord.ensure_eda(bridge,execute)
    execute.assert_called_once_with(bridge,'application.ping',{'command_pending':True},2)
