"""Build an offline Windows x64 installer from verified inputs and local source.

Embedded CPython is an explicit SHA-256-pinned input, never an unverified latest
download. The suite uses original Python library services in process.
Requires Inno Setup 6+.
SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_verified(archive: Path, destination: Path, expected_sha256: str) -> None:
    """Validate every member before extraction; reject links and path traversal."""
    if len(expected_sha256) != 64 or digest(archive).lower() != expected_sha256.lower():
        raise ValueError('Embedded Python SHA-256 mismatch')
    with zipfile.ZipFile(archive) as source:
        members, total = source.infolist(), 0
        for member in members:
            name = PurePosixPath(member.filename.replace('\\', '/'))
            total += member.file_size
            if name.is_absolute() or '..' in name.parts or ':' in member.filename or ((member.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError('Unsafe archive member')
            if total > 300_000_000:
                raise ValueError('Embedded archive exceeds size limit')
        source.extractall(destination)


def prepare(args) -> dict:
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 12):
        raise ValueError('Build on Windows x64 using Python 3.12')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('altium_plugin_build', ROOT / 'scripts/build_plugin.py')
    package = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package)
    payload = package.package_payload(ROOT)
    version = json.loads(payload['plugin.json'])['version']
    stage = output / ('installer-stage-' + version)
    if stage.exists():
        raise ValueError('Stage already exists; select a fresh --output directory')
    app = stage / 'app'
    app.mkdir(parents=True)
    for name, content in payload.items():
        path = app / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    for extra in ('suite_hub.py', 'altium_installation.py', 'installer/onboarding.ps1'):
        target = app / extra
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / extra, target)
    embedded = app / 'python'
    extract_verified(args.python_embed, embedded, args.python_embed_sha256)
    if not (embedded / 'python.exe').is_file() or not (embedded / 'python312.zip').is_file():
        raise ValueError('Input is not the Windows CPython 3.12 embeddable distribution')
    (embedded / 'python312._pth').write_text('python312.zip\n.\nLib/site-packages\n..\nimport site\n', encoding='utf-8')
    wheels = stage / 'wheels'
    wheels.mkdir()
    subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', str(ROOT / 'eda-agent'), '-w', str(wheels)], check=True)
    wheel = next(wheels.glob('eda_agent-*.whl'))
    requirements = stage / 'runtime.txt'
    requirements.write_text(wheel.as_uri() + '\npillow>=11.1,<12\njsonschema>=4.26,<5\n', encoding='utf-8')
    subprocess.run([sys.executable, '-m', 'pip', 'download', '--only-binary=:all:', '-r', str(requirements),
                    '-c', str(ROOT / 'constraints-windows-py312.txt'), '-d', str(wheels)], check=True)
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-index', '--find-links', str(wheels),
                    '--target', str(embedded / 'Lib/site-packages'), '-r', str(requirements)], check=True)
    runtime_spec = importlib.util.spec_from_file_location('installer_runtime_probe', ROOT / 'scripts/plugin_runtime.py')
    runtime = importlib.util.module_from_spec(runtime_spec)
    runtime_spec.loader.exec_module(runtime)
    probe = runtime.probe_python(embedded / 'python.exe')
    provenance = {'version': version, 'python_embed_sha256': digest(args.python_embed),
                  'runtime_probe': probe,
                  'wheels': {p.name: digest(p) for p in sorted(wheels.glob('*.whl'))},
                  'signature': 'unsigned; release signing is a separate publisher operation'}
    (app / 'INSTALLER_PROVENANCE.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
    result = {'stage': str(app), 'version': version, 'compiled': False}
    if not args.prepare_only:
        iscc = args.iscc or shutil.which('ISCC.exe')
        if not iscc:
            raise ValueError('Inno Setup compiler missing; install it or pass --iscc')
        subprocess.run([str(iscc), '/DSourceRoot=' + str(app), '/DOutputRoot=' + str(output),
                        '/DAppVersion=' + version, str(ROOT / 'installer/AltiumMCPSuite.iss')], check=True)
        installer = output / 'AltiumMCPSuite-Setup-x64.exe'
        if not installer.is_file():
            raise RuntimeError('Inno Setup did not create the expected installer')
        result.update(compiled=True, installer=str(installer), sha256=digest(installer))
        (output / 'SHA256SUMS.txt').write_text(digest(installer) + '  ' + installer.name + '\n', encoding='ascii')
    (output / 'installer-artifacts.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-embed', type=Path, required=True)
    parser.add_argument('--python-embed-sha256', required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/installer')
    parser.add_argument('--iscc', type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    print(json.dumps(prepare(parser.parse_args()), indent=2))


if __name__ == '__main__':
    main()
