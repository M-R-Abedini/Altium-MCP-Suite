"""Deploy coordinated Python sources/scripts, preserving local configuration.

Run with the destination's configured Python to smoke-test both installed
stdio entry points. Dependencies must already be installed in that Python.
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
ROOT_MODULES = ('eda_stdio.py', 'bridge_coordination.py', 'suite_config.py')
NOTICE_FILES = ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'UPSTREAM.json', 'MODIFICATIONS.md',
                 'eda-agent/LICENSE', 'eda-agent/NOTICE', 'coffeenmusic/LICENSE',
                 'altium-designer-mcp/LICENCE')


def deployment_files(root=ROOT):
    files = [root / name for name in (*ROOT_MODULES, *NOTICE_FILES)]
    for directory in ('eda-agent/src', 'eda-agent/scripts/altium', 'coffeenmusic/server'):
        for path in (root / directory).rglob('*'):
            relative = path.relative_to(root / directory)
            if any(part in {'.venv', '__pycache__', 'tests'} for part in relative.parts):
                continue
            if path.suffix.lower() in {'.py', '.pas', '.dfm', '.prjscr', '.txt'}:
                files.append(path)
    if not all(path.is_file() for path in files):
        raise RuntimeError('Deployment is missing an entry point or shared module')
    return sorted(files)


async def smoke(destination, python=sys.executable, servers=('eda', 'legacy')):
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
        for name in servers:
            script = destination / ('eda_stdio.py' if name == 'eda' else 'coffeenmusic/server/codex_stdio.py')
            args = [str(script)] + (['--no-dashboard'] if name == 'eda' else [])
            params = StdioServerParameters(command=str(python), args=args, cwd=str(destination), env=env)
            with (Path(scratch) / f'{name}.log').open('w', encoding='utf-8') as log:
                try:
                    async with stdio_client(params, errlog=log) as streams:
                        async with ClientSession(*streams, read_timeout_seconds=datetime.timedelta(seconds=60)) as client:
                            await client.initialize()
                            catalog = await client.list_tools()
                            if not catalog.tools:
                                raise RuntimeError('Installed server returned no tools')
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
    parser.add_argument('--server', choices=('eda', 'legacy', 'both'), default='both')
    args = parser.parse_args()
    manifest, backup, changed = deploy(args.destination)
    try:
        asyncio.run(smoke(args.destination.resolve(), args.python,
                         ('eda', 'legacy') if args.server == 'both' else (args.server,)))
    except BaseException:
        rollback(args.destination.resolve(), backup, changed)
        raise
    (args.destination / 'deployment-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({'changed_files': len(changed), 'backup_path': str(backup)}))
