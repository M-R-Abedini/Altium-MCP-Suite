"""Local setup hub. Free features work without an account or network login.

No registration service is shipped. An optional externally issued registration
token can be stored with Windows DPAPI for future use; it grants no fabricated
entitlement and is never required by the gateway.
SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from scripts import plugin_runtime


def protect_token(token: str) -> str:
    """Encrypt under the current Windows account; never store plaintext secrets."""
    if sys.platform != 'win32':
        raise ValueError('Token storage requires Windows DPAPI')
    if not token or len(token) > 16384 or '\x00' in token:
        raise ValueError('Registration token must contain 1–16384 characters without NUL')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(token.encode('utf-8'))
    source = Blob(len(token.encode('utf-8')), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    crypt.CryptProtectData.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt.CryptProtectData.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not crypt.CryptProtectData(ctypes.byref(source), 'MCP Altium Suite optional registration',
                                 None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return base64.b64encode(ctypes.string_at(target.data, target.size)).decode('ascii')
    finally:
        kernel.LocalFree(target.data)


def save_registration(data: Path, token: str) -> dict:
    encrypted = protect_token(token)
    output = {'schema': 1, 'protection': 'windows-dpapi-current-user', 'token_protected': encrypted,
              'registered_at_utc': datetime.now(timezone.utc).isoformat(),
              'service_verified': False, 'free_core_requires_registration': False}
    plugin_runtime.atomic_json(Path(data) / 'auth.json', output)
    return {'stored': True, 'service_verified': False, 'free_core_requires_registration': False}


def bundled_setup(data: Path, root: Path, altium_exe: Path, *, runtime=None, workspace=None) -> dict:
    """Adopt the installer-owned embedded interpreter and isolated Rust worker."""
    python = root / 'python/python.exe'
    binary = root / 'libraries/altium-designer-mcp.exe'
    for path, name in ((python, 'Bundled Python'), (binary, 'Bundled Rust worker'), (altium_exe, 'Altium')):
        plugin_runtime.existing_file(path, name)
    previous = None
    if (Path(data) / 'state.json').exists():
        _, previous = plugin_runtime.checked_state(Path(data))
        plugin_runtime.validate_template(previous)
    prior_env = previous['backends']['eda']['env'] if previous else {}
    native = Path(runtime or prior_env.get('ALTIUM_MCP_RUNTIME') or Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'AltiumMCPSuite')
    identity = native / 'workspace-identity.json'
    existing = plugin_runtime.read_object(identity) if identity.is_file() else {}
    work = Path(workspace or prior_env.get('EDA_AGENT_WORKSPACE') or existing.get('workspace', native / 'workspace'))
    if previous:
        for key, selected in (('ALTIUM_MCP_RUNTIME', native), ('EDA_AGENT_WORKSPACE', work)):
            if os.path.normcase(str(selected.resolve())) != os.path.normcase(str(Path(prior_env[key]).resolve())):
                raise ValueError('Installer setup cannot replace the existing native workspace identity')
    plugin_runtime.validate_workspace(native, work)
    cloud = Path(data) / 'cloud-libraries'
    config = Path(data) / 'libraries.json'
    settings = (plugin_runtime.read_object(previous['backends']['libraries']['args'][0]) if previous else
                plugin_runtime.read_object(config) if config.exists() else {'allowed_paths': []})
    if not isinstance(settings.get('allowed_paths'), list) or not all(isinstance(p, str) and Path(p).is_absolute() for p in settings['allowed_paths']):
        raise ValueError('Existing library allowlist is invalid')
    audit = settings.get('logging', {}).get('audit_log_path')
    if audit and not Path(audit).is_absolute():
        prior_cwd = previous['backends']['libraries'].get('cwd') if previous else str(data)
        if not prior_cwd:
            raise ValueError('A relative audit_log_path needs the prior library worker cwd; make its destination absolute before installer setup')
        settings['logging']['audit_log_path'] = str((Path(prior_cwd) / audit).resolve())
    settings['allowed_paths'] = list(dict.fromkeys(settings['allowed_paths'] + [str(cloud.resolve())]))
    plugin_runtime.probe_python(python)
    # Configuration is user data; never place it under the installed package.
    if config.exists():
        backup = Path(data) / 'config-backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup.mkdir(parents=True)
        (backup / config.name).write_bytes(config.read_bytes())
    plugin_runtime.atomic_json(config, settings)
    common = {'ALTIUM_EXE': str(altium_exe.resolve()), 'ALTIUM_MCP_RUNTIME': str(native.resolve()),
              'EDA_AGENT_WORKSPACE': str(work.resolve()), 'EDA_AGENT_NO_DASHBOARD': '1', 'EDA_AGENT_BACKEND': 'altium'}
    template = {'backends': {
        'eda': {'command': str(python), 'args': [str(root / 'eda_stdio.py'), '--toolset', 'full', '--no-dashboard'], 'env': common},
        'legacy': {'command': str(python), 'args': [str(root / 'coffeenmusic/server/codex_stdio.py')], 'env': common},
        'libraries': {'command': str(binary), 'args': [str(config)]}}}
    if previous:
        for name, spec in template['backends'].items():
            old = previous['backends'][name]
            for key in ('enabled', 'enabled_tools', 'disabled_tools', 'timeout_seconds', 'startup_timeout_seconds'):
                if key in old:
                    spec[key] = old[key]
            if name in {'eda', 'legacy'}:
                # Retain actual coordinator identity even if a new installer used a default.
                spec['env'] = dict(old['env'], ALTIUM_EXE=str(altium_exe.resolve()))
    plugin_runtime.validate_template(template)
    state = {'schema': plugin_runtime.STATE_SCHEMA, 'python': str(python), 'cloud_cache': str(cloud.resolve()),
             'altium_exe': str(altium_exe.resolve()), 'mode': 'installer',
             'configured_at_utc': datetime.now(timezone.utc).isoformat()}
    plugin_runtime.persist(Path(data), state, template)
    return plugin_runtime.doctor(Path(data), root)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    sub = parser.add_subparsers(dest='operation')
    sub.add_parser('status')
    sub.add_parser('discover-altium')
    setup = sub.add_parser('setup-bundled')
    setup.add_argument('--altium-exe', type=Path, required=True)
    setup.add_argument('--runtime-dir', type=Path)
    setup.add_argument('--workspace', type=Path)
    setup.add_argument('--client', choices=('codex', 'claude-desktop', 'claude-code', 'cursor', 'windsurf', 'vscode', 'all'))
    sub.add_parser('register-token', help='Read an optional externally issued token from stdin; no network request')
    args = parser.parse_args(argv)
    data, root = plugin_runtime.data_directory(args.data_dir), args.root.resolve()
    if args.operation == 'discover-altium':
        from altium_installation import detect_installations
        output = {'installations': detect_installations()}
    elif args.operation == 'register-token':
        output = save_registration(data, sys.stdin.read(16385).strip())
    elif args.operation == 'setup-bundled':
        output = bundled_setup(data, root, args.altium_exe, runtime=args.runtime_dir, workspace=args.workspace)
        if output['ready'] and args.client:
            from configure_suite import configure_clients
            output['clients'] = configure_clients(args.client, root, root / 'python/python.exe', data, apply=True)
    else:
        output = plugin_runtime.doctor(data, root)
        output.update(free_core_requires_registration=False, registration_service_available=False,
                      token_stored=(data / 'auth.json').is_file(),
                      setup_command='suite_hub.py setup-bundled --altium-exe <X2.EXE> --client <client>')
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if args.operation == 'setup-bundled' and not output['ready'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
