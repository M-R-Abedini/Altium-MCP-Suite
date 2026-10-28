"""Migrate three existing Codex MCP connections to one, preserving worker settings.

This does not restart clients or invoke Altium. A byte-for-byte config backup and
the prior backend manifest are retained before the atomic config replacement.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tomllib
import uuid

OLD_NAMES = {'eda-agent': 'eda', 'altium': 'legacy', 'altium-libraries': 'libraries'}
SERVER_NAME = 'mcp-altium-suite'


def _section_headers(text):
    """Find real table headers, ignoring header-like text in multiline strings."""
    position = 0
    multiline = None
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if multiline is None and stripped.startswith('['):
            try:
                value = tomllib.loads(stripped)
                path = []
                while isinstance(value, dict) and len(value) == 1:
                    key, value = next(iter(value.items()))
                    path.append(key)
                yield position, tuple(path)
            except tomllib.TOMLDecodeError:
                pass
        i = 0
        quote = None
        while i < len(line):
            if multiline:
                if multiline == '"""' and line[i] == '\\':
                    i += 2
                    continue
                if line.startswith(multiline, i):
                    i += 3
                    multiline = None
                else:
                    i += 1
            elif quote:
                if quote == '"' and line[i] == '\\':
                    i += 2
                elif line[i] == quote:
                    quote = None
                    i += 1
                else:
                    i += 1
            elif line[i] == '#':
                break
            elif line.startswith('"""', i) or line.startswith("'''", i):
                multiline = line[i:i + 3]
                i += 3
            elif line[i] in ('"', "'"):
                quote = line[i]
                i += 1
            else:
                i += 1
        position += len(line)


def render_migration(text, destination, python, manifest_path, cloud_cache):
    parsed = tomllib.loads(text)
    servers = parsed.get('mcp_servers', {})
    if SERVER_NAME in servers:
        raise ValueError('Unified server already exists; inspect it before changing configuration')
    missing = set(OLD_NAMES) - set(servers)
    if missing:
        raise ValueError('Expected existing servers are missing: ' + ', '.join(sorted(missing)))
    backends = {}
    for old, new in OLD_NAMES.items():
        server = servers[old]
        if not isinstance(server.get('command'), str) or 'url' in server:
            raise ValueError(old + ': migration requires a local stdio worker')
        if server.get('env_vars'):
            raise ValueError(old + ': explicit env_vars need manual worker mapping')
        worker = {key: server[key] for key in
                  ('command', 'args', 'env', 'cwd', 'enabled_tools', 'disabled_tools') if key in server}
        worker.setdefault('args', [])
        worker.setdefault('cwd', str(destination))
        worker['timeout_seconds'] = server.get('tool_timeout_sec', 900)
        worker['startup_timeout_seconds'] = server.get(
            'startup_timeout_sec', server.get('startup_timeout_ms', 60000) / 1000)
        worker['enabled'] = server.get('enabled', True)
        if new == 'eda':
            args = list(worker['args'])
            for index, argument in enumerate(args):
                if argument == '--toolset' and index + 1 < len(args):
                    args[index + 1] = 'full'
                    break
                if argument.startswith('--toolset='):
                    args[index] = '--toolset=full'
                    break
            else:
                args += ['--toolset', 'full']
            if '--no-dashboard' not in args:
                args.append('--no-dashboard')
            worker['args'] = args
        backends[new] = worker
    headers = list(_section_headers(text))
    removed = []
    pieces = []
    cursor = 0
    for index, (start, path) in enumerate(headers):
        if len(path) >= 2 and path[0] == 'mcp_servers' and path[1] in OLD_NAMES:
            end = headers[index + 1][0] if index + 1 < len(headers) else len(text)
            pieces.append(text[cursor:start])
            cursor = end
            removed.append(path[1])
    pieces.append(text[cursor:])
    if set(removed) != set(OLD_NAMES):
        raise ValueError('Existing servers must use their own TOML tables; no configuration changed')
    remaining = ''.join(pieces)
    remaining_parsed = tomllib.loads(remaining)
    if any(name in remaining_parsed.get('mcp_servers', {}) for name in OLD_NAMES):
        raise ValueError('Could not remove all old server definitions')
    new_server = {
        'command': str(python),
        'args': [str(destination / 'suite_stdio.py'), '--toolset', 'minimal',
                 '--backend-config', str(manifest_path)],
        'startup_timeout_sec': 60, 'tool_timeout_sec': 900, 'enabled': True,
        'env': {'ALTIUM_SUITE_CLOUD_CACHE': str(cloud_cache)},
    }
    lines = ['', '# Unified local Altium suite: one connection, lazy coordinated workers.',
             '[mcp_servers.mcp-altium-suite]']
    for key, value in new_server.items():
        if key != 'env':
            lines.append(key + ' = ' + json.dumps(value))
    lines += ['', '[mcp_servers.mcp-altium-suite.env]',
              'ALTIUM_SUITE_CLOUD_CACHE = ' + json.dumps(str(cloud_cache)), '']
    result = remaining.rstrip('\r\n') + '\n' + '\n'.join(lines)
    actual = tomllib.loads(result)
    expected = dict(parsed)
    expected['mcp_servers'] = {key: value for key, value in servers.items() if key not in OLD_NAMES}
    expected['mcp_servers'][SERVER_NAME] = new_server
    if actual != expected:
        raise ValueError('Migration would change unrelated settings; no configuration changed')
    return result, {'backends': backends}


def _atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def migrate(config, destination, python, cloud_cache, apply=False):
    original = config.read_bytes()
    manifest_path = destination / 'suite-backends.local.json'
    result, manifest = render_migration(original.decode('utf-8-sig'), destination,
                                        python, manifest_path, cloud_cache)
    summary = {'server': SERVER_NAME, 'workers': list(manifest['backends']),
               'config': str(config), 'manifest': str(manifest_path), 'applied': False}
    if not apply:
        return summary
    if not (destination / 'suite_stdio.py').is_file() or not python.is_file():
        raise ValueError('Install and test the suite entrypoint and Python before migrating')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    backup = config.parent / 'backups' / ('altium-suite-' + stamp)
    backup.mkdir(parents=True, exist_ok=False)
    shutil.copy2(config, backup / config.name)
    old_manifest = manifest_path.read_bytes() if manifest_path.exists() else None
    if old_manifest is not None:
        (backup / manifest_path.name).write_bytes(old_manifest)
    if config.read_bytes() != original:
        raise RuntimeError('Client configuration changed during preparation; migration aborted')
    try:
        _atomic_write(manifest_path, json.dumps(manifest, indent=2).encode('utf-8'))
        if config.read_bytes() != original:
            raise RuntimeError('Client configuration changed before publication; migration aborted')
        _atomic_write(config, result.encode('utf-8'))
    except BaseException:
        if old_manifest is None:
            manifest_path.unlink(missing_ok=True)
        else:
            _atomic_write(manifest_path, old_manifest)
        raise
    summary.update(applied=True, backup=str(backup),
                   config_sha256=hashlib.sha256(config.read_bytes()).hexdigest())
    (backup / 'migration.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path.home() / '.codex/config.toml')
    parser.add_argument('--destination', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--python', type=Path)
    parser.add_argument('--cloud-cache', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='otherwise print a read-only migration plan')
    args = parser.parse_args()
    destination = args.destination.resolve()
    python = args.python or destination / '.venv/Scripts/python.exe'
    print(json.dumps(migrate(args.config.resolve(), destination, python.resolve(),
                             args.cloud_cache.resolve(), args.apply), indent=2))


if __name__ == '__main__':
    main()
