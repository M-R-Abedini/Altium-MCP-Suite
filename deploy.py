"""Deploy coordinated Python sources/scripts, preserving local configuration.

Run with the destination's configured Python to smoke-test the installed
unified stdio entry point. Dependencies must already be installed in that Python.
"""
import argparse
import asyncio
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parent
ROOT_MODULES = ('suite_stdio.py', 'suite_cloud_libraries.py', 'configure_suite.py', 'setup.py', 'deploy.py',
                'suite_units.py', 'suite_checkpoints.py', 'suite_supply_chain.py',
                'suite_autoroute.py', 'suite_telemetry.py',
                'eda_stdio.py', 'bridge_coordination.py', 'suite_config.py', 'script_projects.py',
                'engine_identity.py', 'engine_queue.py', 'suite_reads.py',
                'suite_legacy_compat.py', 'suite_libraries_native.py', 'suite_schlib_codec.py',
                'suite_script_overlay.py', 'suite_ole.py', 'suite_pcblib_codec.py', 'suite_pcb_primitives.py')
NOTICE_FILES = ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'UPSTREAM.json', 'MODIFICATIONS.md',
                 'eda-agent/LICENSE', 'eda-agent/NOTICE', 'coffeenmusic/LICENSE')


def deployment_files(root=ROOT):
    # The plugin ZIP, direct deployment and installer share one audited source
    # closure. Hand-maintained lists previously missed codec resources, EDA wheel
    # metadata and web assets even though deployment itself reported success.
    from scripts.build_plugin import package_inputs, package_payload
    package_payload(root)  # Enforce the same license and native-wheel closure checks.
    return list(package_inputs(root).values())


async def smoke(destination, python=sys.executable, servers=('suite',)):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    with tempfile.TemporaryDirectory(prefix='altium-installed-smoke-') as scratch:
        env['ALTIUM_EXE'] = str(Path(scratch) / 'not-installed' / 'X2.EXE')
        env['ALTIUM_MCP_RUNTIME'] = str(Path(scratch) / 'runtime')
        env['EDA_AGENT_WORKSPACE'] = str(Path(scratch) / 'workspace')
        env['EDA_AGENT_POINTER_FILE'] = str(Path(scratch) / 'pointer.txt')
        env['EDA_AGENT_TOOLSET'] = 'full'
        env['EDA_AGENT_BACKEND'] = 'altium'
        # Historical selector aliases now verify the single intended gateway.
        for name in dict.fromkeys('suite' if entry == 'legacy' else entry for entry in servers):
            entrypoints = {'suite': 'suite_stdio.py', 'eda': 'eda_stdio.py'}
            script = destination / entrypoints[name]
            args = [str(script)] + (['--no-dashboard'] if name == 'eda' else [])
            params = StdioServerParameters(command=str(python), args=args, cwd=str(destination), env=env)
            with (Path(scratch) / f'{name}.log').open('w', encoding='utf-8') as log:
                try:
                    async with stdio_client(params, errlog=log) as streams:
                        async with ClientSession(*streams, read_timeout_seconds=datetime.timedelta(seconds=60)) as client:
                            initialized = await client.initialize()
                            catalog = await client.list_tools()
                            if not catalog.tools:
                                raise RuntimeError('Installed server returned no tools')
                            if name == 'suite':
                                if initialized.serverInfo.name != 'mcp altium suite':
                                    raise RuntimeError('Unexpected unified server identity')
                                if {tool.name for tool in catalog.tools} != {'suite_catalog', 'suite_invoke', 'suite_status'}:
                                    raise RuntimeError('Unexpected minimal unified tool surface')
                            print(json.dumps({'server': name, 'installed_root': str(destination),
                                              'initialized': True, 'tools': len(catalog.tools)}), flush=True)
                except BaseException:
                    log.flush()
                    print((Path(scratch) / f'{name}.log').read_text(encoding='utf-8')[-3000:], file=sys.stderr)
                    raise


def deploy(destination, root=ROOT):
    destination = destination.resolve()
    if destination == root.resolve() or root.resolve() in destination.parents:
        raise ValueError('Use a deployment directory outside the source checkout')
    files = deployment_files(root)
    backup = destination / 'repair-backups' / ('deployment-' + uuid.uuid4().hex)
    changed = []
    manifest = {}
    try:
        for source in files:
            relative = source.relative_to(root)
            data = source.read_bytes()
            manifest[str(relative)] = hashlib.sha256(data).hexdigest()
            target = destination / relative
            if target.exists() and target.read_bytes() == data:
                continue
            exists = target.exists()
            if exists:
                saved = backup / relative
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.deploy-' + uuid.uuid4().hex)
            try:
                temporary.write_bytes(data)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            changed.append((relative, exists))
        return manifest, backup, changed
    except BaseException:
        rollback(destination, backup, changed)
        raise


def rollback(destination, backup, changed):
    for relative, existed in reversed(changed):
        target = destination / relative
        if existed:
            shutil.copy2(backup / relative, target)
        else:
            target.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    parser.add_argument('--server', choices=('suite', 'eda', 'legacy', 'both', 'all'), default='suite')
    args = parser.parse_args()
    manifest, backup, changed = deploy(args.destination)
    try:
        asyncio.run(smoke(args.destination.resolve(), args.python,
                         ('suite', 'eda', 'legacy') if args.server == 'all' else
                         ('eda', 'legacy') if args.server == 'both' else (args.server,)))
    except BaseException:
        rollback(args.destination.resolve(), backup, changed)
        raise
    (args.destination / 'deployment-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({'changed_files': len(changed), 'backup_path': str(backup)}))
