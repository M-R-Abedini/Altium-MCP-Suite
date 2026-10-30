"""Real subprocess checks for immutable package roots and explicit private state."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def snapshot(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def clean_environment(data):
    env = dict(os.environ, ALTIUM_SUITE_PLUGIN_DATA=str(data), CODEX_HOME=str(data / 'codex'))
    # Do not let the test runner's imports or invocation flags make the test pass.
    for name in ('PYTHONDONTWRITEBYTECODE', 'PYTHONPYCACHEPREFIX', 'PYTHONPATH'):
        env.pop(name, None)
    return env


def stage_product(root):
    root.mkdir()
    names = ('suite_stdio.py', 'suite_hub.py', 'suite_units.py', 'suite_checkpoints.py',
             'suite_supply_chain.py', 'suite_telemetry.py', 'suite_autoroute.py',
             'suite_cloud_libraries.py', 'configure_suite.py', 'altium_installation.py',
             'scripts/plugin_runtime.py')
    for name in names:
        target = root / name; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    return root


def test_hub_imports_and_product_state_do_not_change_clean_package(tmp_path):
    package = stage_product(tmp_path / 'clean package')
    data = tmp_path / 'explicit private data'
    design = tmp_path / 'fixture.SchDoc'; design.write_bytes(b'local checkpoint fixture')
    script = package / 'check.py'
    script.write_text('''import json,os,sys,runpy
from pathlib import Path
runpy.run_path(str(Path(__file__).with_name("suite_hub.py")),run_name="hub_entry")
import suite_stdio,suite_units,suite_cloud_libraries,suite_autoroute,configure_suite,altium_installation
from scripts.plugin_runtime import data_directory
from suite_checkpoints import CheckpointStore
from suite_supply_chain import SupplyChain
from suite_telemetry import TelemetryCollector
data=data_directory()
checkpoint=CheckpointStore(data).create([sys.argv[1]])
supply=SupplyChain()
supply._cache_put("offline-fixture", {"stock":None})
assert supply._cache_get("offline-fixture")["stock"] is None
assert TelemetryCollector().enabled() is False
print(json.dumps({"data":str(data),"bytecode_disabled":sys.dont_write_bytecode,
                  "checkpoint":checkpoint["checkpoint_id"]}))
''', encoding='utf-8')
    before = snapshot(package)
    hub_status = subprocess.run([sys.executable, str(package / 'suite_hub.py'), '--data-dir', str(data), 'status'],
        cwd=package, env=clean_environment(data), check=True, capture_output=True, text=True, timeout=45)
    hub = json.loads(hub_status.stdout)
    assert hub['data_directory'] == str(data) and hub['configured'] is False
    assert not hub['free_core_requires_registration'] and not data.exists()
    assert snapshot(package) == before
    completed = subprocess.run([sys.executable, str(script), str(design)], cwd=package,
        env=clean_environment(data), check=True, capture_output=True, text=True, timeout=45)
    output = json.loads(completed.stdout)
    assert output['bytecode_disabled'] is True and Path(output['data']) == data
    assert snapshot(package) == before and not list(package.rglob('__pycache__'))
    assert (data / 'cache/supply_cache.db').is_file()
    assert list((data / 'backups').glob('*/*/manifest.json'))
    assert not (data / 'telemetry').exists()
    assert design.read_bytes() == b'local checkpoint fixture'


def test_gateway_entry_and_worker_child_keep_package_immutable(tmp_path):
    package = stage_product(tmp_path / 'clean gateway')
    (package / 'readonly_dependency.py').write_text('VALUE=7\n', encoding='utf-8')
    (package / 'worker.py').write_text('''import os,sys
import readonly_dependency
from mcp.server.fastmcp import FastMCP
server=FastMCP("bytecode-fixture")
@server.tool()
def probe() -> dict:
 return {"bytecode_disabled":sys.dont_write_bytecode,
         "environment_flag":os.environ.get("PYTHONDONTWRITEBYTECODE"),"value":readonly_dependency.VALUE}
server.run(transport="stdio")
''', encoding='utf-8')
    script = package / 'check.py'
    script.write_text('''import asyncio,json,sys,runpy
from pathlib import Path
entry=runpy.run_path(str(Path(__file__).with_name("suite_stdio.py")),run_name="gateway_entry")
Suite,BackendSpec=entry["Suite"],entry["BackendSpec"]
import readonly_dependency,suite_units,suite_checkpoints,suite_telemetry
async def main():
 suite=Suite([BackendSpec("libraries",sys.executable,[str(Path(__file__).with_name("worker.py"))],
                         env={"PYTHONDONTWRITEBYTECODE":""})])
 try:
  result=await suite.invoke("libraries__probe",{})
  assert not result.isError, result
  child=result.structuredContent or json.loads(result.content[0].text)
  print(json.dumps({"gateway_bytecode_disabled":sys.dont_write_bytecode,"child":child}))
 finally: await suite.close()
asyncio.run(main())
''', encoding='utf-8')
    before = snapshot(package)
    completed = subprocess.run([sys.executable, str(script)], cwd=package,
        env=clean_environment(tmp_path / 'data'), check=True, capture_output=True, text=True, timeout=45)
    output = json.loads(completed.stdout)
    assert output['gateway_bytecode_disabled']
    assert output['child'] == {'bytecode_disabled': True, 'environment_flag': '1', 'value': 7}
    assert snapshot(package) == before and not list(package.rglob('__pycache__'))


def test_isolated_runtime_probe_blocks_startup_hook_bytecode(tmp_path):
    """-I ignores PYTHON* env; -B must protect imports before the script body."""
    import venv
    from scripts import plugin_runtime
    environment = tmp_path / 'private runtime'
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    site = environment / ('Lib/site-packages' if os.name == 'nt' else
                           f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages')
    site.mkdir(parents=True, exist_ok=True)
    package = tmp_path / 'readonly package'; package.mkdir()
    marker = tmp_path / 'startup-executed.txt'
    (package / 'startup_probe.py').write_text(
        'from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("started")\n', encoding='utf-8')
    (site / 'readonly-test.pth').write_text('import sys; sys.path.insert(0, ' + repr(str(package))
                                          + '); import startup_probe\n', encoding='utf-8')
    before = snapshot(package)
    # A private minimal venv deliberately lacks MCP dependencies. The import
    # failure is expected, but the real Python startup hook must execute safely.
    with pytest.raises(ValueError, match='lacks required runtime dependencies'):
        plugin_runtime.probe_python(python)
    assert marker.read_text() == 'started'
    assert snapshot(package) == before and not list(package.rglob('__pycache__'))


@pytest.mark.skipif(sys.platform != 'win32', reason='Real Windows PowerShell launcher')
def test_configured_powershell_launch_propagates_readonly_and_private_data(tmp_path):
    from scripts import plugin_runtime
    package = tmp_path / 'configured package'; (package / 'scripts').mkdir(parents=True)
    for name in ('plugin_runtime.py', 'launch_plugin.ps1'):
        shutil.copyfile(ROOT / 'scripts' / name, package / 'scripts' / name)
    shutil.copyfile(ROOT / 'configure_suite.py', package / 'configure_suite.py')
    (package / 'eda_stdio.py').touch()
    (package / 'coffeenmusic/server').mkdir(parents=True)
    (package / 'coffeenmusic/server/codex_stdio.py').touch()  # persisted old manifest
    (package / 'suite_legacy_compat.py').touch()
    (package / 'suite_libraries_native.py').touch()
    (package / 'readonly_dependency.py').write_text('VALUE=11\n')
    (package / 'suite_stdio.py').write_text('''import json,os,sys
import readonly_dependency
print(json.dumps({"environment_flag":os.environ.get("PYTHONDONTWRITEBYTECODE"),
                  "bytecode_disabled":sys.dont_write_bytecode,
                  "data":os.environ.get("ALTIUM_SUITE_PLUGIN_DATA")}))
''')
    altium = tmp_path / 'X2.EXE'; altium.touch()
    library = tmp_path / 'libraries.json'; library.write_text(json.dumps({'allowed_paths': [str(tmp_path)]}))
    common = {'ALTIUM_EXE': str(altium), 'ALTIUM_MCP_RUNTIME': str(tmp_path / 'native'),
              'EDA_AGENT_WORKSPACE': str(tmp_path / 'workspace')}
    template = {'backends': {
        'eda': {'command': sys.executable, 'args': [str(package / 'eda_stdio.py')], 'env': common},
        'legacy': {'command': sys.executable, 'args': [str(package / 'coffeenmusic/server/codex_stdio.py')], 'env': common},
        'libraries': {'command': sys.executable, 'args': [str(library)]}}}
    manifest = tmp_path / 'backends.json'; manifest.write_text(json.dumps(template))
    data = tmp_path / 'selected private data'
    plugin_runtime.adopt(manifest, sys.executable, data, probe=False)
    before = snapshot(package)
    configured = subprocess.run([sys.executable, str(package / 'scripts/plugin_runtime.py'),
        '--data-dir', str(data), '--client', 'cursor', 'configure-clients', '--config', str(tmp_path / 'cursor.json')],
        cwd=tmp_path, env=clean_environment(tmp_path / 'wrong inherited data'),
        check=True, capture_output=True, text=True, timeout=45)
    assert json.loads(configured.stdout)['applied'] is False
    assert not (tmp_path / 'cursor.json').exists() and snapshot(package) == before
    completed = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File', str(package / 'scripts/launch_plugin.ps1'), '-DataDir', str(data)],
        cwd=tmp_path, env=clean_environment(tmp_path / 'wrong inherited data'),
        check=True, capture_output=True, text=True, timeout=45)
    output = json.loads(completed.stdout)
    assert output == {'environment_flag': '1', 'bytecode_disabled': True, 'data': str(data)}
    assert snapshot(package) == before and not list(package.rglob('__pycache__'))
    assert not (tmp_path / 'wrong inherited data').exists()
