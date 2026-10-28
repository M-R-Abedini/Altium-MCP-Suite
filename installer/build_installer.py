"""Build an offline Windows x64 installer from verified inputs and local source.

Embedded CPython is an explicit SHA-256-pinned input, never an unverified latest
download. Rust is built locally, with vendored Corresponding Source delivered
beside the installer and inside the installed product. Requires Inno Setup 6+.
SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
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


def source_archive(crate: Path, output: Path, cargo: str, target: Path, binary: Path) -> Path:
    """Vendor exact locked dependencies and include rebuild instructions/licenses."""
    with tempfile.TemporaryDirectory(prefix='altium-rust-source-') as scratch:
        source = Path(scratch) / 'altium-designer-mcp'
        shutil.copytree(crate, source, ignore=shutil.ignore_patterns(
            'target', '.git', '.venv', '__pycache__', 'dist', '*.log', '*.local.*', '.env', '*.bak'))
        vendor = source / 'vendor'
        completed = subprocess.run([cargo, 'vendor', '--locked', 'vendor'], cwd=source,
                                   check=True, capture_output=True, text=True)
        (source / '.cargo').mkdir(exist_ok=True)
        (source / '.cargo/config.toml').write_text(completed.stdout, encoding='utf-8')
        (source / 'REBUILD.md').write_text(
            '# Corresponding Source\n\nThis source accompanies the isolated GPL-3.0-or-later library executable.\n'
            'Install the Rust toolchain pinned in rust-toolchain.toml and the Windows C++ build tools.\n'
            'Run `cargo build --release --locked --offline` from this directory on Windows x64.\n'
            'All vendored dependencies retain their original license files.\n', encoding='utf-8')
        (source / 'BINARY_PROVENANCE.json').write_text(json.dumps({
            'binary_sha256': digest(binary), 'cargo_lock_sha256': digest(crate / 'Cargo.lock'),
            'build_command': 'cargo build --release --locked',
            'source_license': 'GPL-3.0-or-later'}, indent=2), encoding='utf-8')
        archive = output / 'altium-designer-mcp-corresponding-source.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, strict_timestamps=False) as zipped:
            for path in sorted(source.rglob('*')):
                if path.is_file():
                    zipped.write(path, path.relative_to(Path(scratch)).as_posix())
        return archive


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
    # Mirror wheel licenses and exact package hashes for auditing the distribution.
    licenses = app / 'licenses'
    licenses.mkdir(exist_ok=True)
    shutil.copy2(ROOT / 'altium-designer-mcp/LICENCE', licenses / 'altium-designer-mcp-GPL-3.0-or-later.txt')
    cargo_target = args.cargo_target.resolve() if args.cargo_target else stage / 'cargo-target'
    env = dict(os.environ, CARGO_TARGET_DIR=str(cargo_target))
    subprocess.run([args.cargo, 'build', '--release', '--locked'], cwd=ROOT / 'altium-designer-mcp', env=env, check=True)
    binary = cargo_target / 'release/altium-designer-mcp.exe'
    (app / 'libraries').mkdir()
    shutil.copy2(binary, app / 'libraries/altium-designer-mcp.exe')
    corresponding = source_archive(ROOT / 'altium-designer-mcp', output, args.cargo, cargo_target, binary)
    shutil.copy2(corresponding, licenses / corresponding.name)
    runtime_spec = importlib.util.spec_from_file_location('installer_runtime_probe', ROOT / 'scripts/plugin_runtime.py')
    runtime = importlib.util.module_from_spec(runtime_spec)
    runtime_spec.loader.exec_module(runtime)
    probe = runtime.probe_python(embedded / 'python.exe')
    provenance = {'version': version, 'python_embed_sha256': digest(args.python_embed),
                  'rust_binary_sha256': digest(binary), 'rust_corresponding_source_sha256': digest(corresponding),
                  'runtime_probe': probe,
                  'wheels': {p.name: digest(p) for p in sorted(wheels.glob('*.whl'))},
                  'signature': 'unsigned; release signing is a separate publisher operation'}
    (app / 'INSTALLER_PROVENANCE.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
    result = {'stage': str(app), 'version': version, 'corresponding_source': str(corresponding), 'compiled': False}
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
        (output / 'SHA256SUMS.txt').write_text(digest(installer) + '  ' + installer.name + '\n' +
            digest(corresponding) + '  ' + corresponding.name + '\n', encoding='ascii')
    (output / 'installer-artifacts.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-embed', type=Path, required=True)
    parser.add_argument('--python-embed-sha256', required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/installer')
    parser.add_argument('--cargo', default='cargo')
    parser.add_argument('--cargo-target', type=Path, help='Reuse an explicit local Cargo build cache')
    parser.add_argument('--iscc', type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    print(json.dumps(prepare(parser.parse_args()), indent=2))


if __name__ == '__main__':
    main()
