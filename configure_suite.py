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
CLIENTS = ('codex', 'claude-desktop', 'claude-code', 'cursor', 'windsurf', 'vscode')


def client_config_paths(home=None, appdata=None):
    """Known Windows user-scope files; explicit --config overrides profile variants."""
    explicit_home = home is not None
    home = Path(home or Path.home())
    roaming = Path(appdata or os.environ.get('APPDATA', home / 'AppData/Roaming'))
    devin = roaming / 'devin/mcp_config.json'
    legacy_windsurf = home / '.codeium/windsurf/mcp_config.json'
    return {
        'codex': (Path(os.environ['CODEX_HOME']) / 'config.toml'
                  if not explicit_home and os.environ.get('CODEX_HOME') else home / '.codex/config.toml'),
        'claude-desktop': roaming / 'Claude/claude_desktop_config.json',
        'claude-code': home / '.claude.json',
        'cursor': home / '.cursor/mcp.json',
        'windsurf': legacy_windsurf if legacy_windsurf.exists() and not devin.exists() else devin,
        'vscode': roaming / 'Code/User/mcp.json',
    }


def launcher_definition(destination, python, data_directory):
    """Use private runtime state; never bake a developer's backend manifest into clients."""
    root, python, data = (Path(p).resolve() for p in (destination, python, data_directory))
    if not (root / 'scripts/plugin_runtime.py').is_file() or not python.is_file():
        raise ValueError('Install the suite launcher and Python before configuring clients')
    return {'command': str(python), 'args': [str(root / 'scripts/plugin_runtime.py'),
            '--data-dir', str(data), '--plugin-root', str(root), 'serve'],
            'env': {'ALTIUM_SUITE_PLUGIN_DATA': str(data)}}


def render_client(text, client, definition):
    """Preserve unrelated settings. Refuse JSONC rather than silently dropping comments."""
    if client not in CLIENTS:
        raise ValueError('Unknown AI client: ' + client)
    if client == 'codex':
        original = tomllib.loads(text)
        servers = original.get('mcp_servers', {})
        if not isinstance(servers, dict):
            raise ValueError('mcp_servers must be a table')
        # A plugin already registers this endpoint. Do not enable a duplicate.
        plugins = original.get('plugins', {})
        if not isinstance(plugins, dict):
            raise ValueError('plugins must be a table')
        for key, value in plugins.items():
            if key.startswith('mcp-altium-suite@') and isinstance(value, dict) and value.get('enabled'):
                return text, False
        expected = json.loads(json.dumps(original))
        new = dict(definition, startup_timeout_sec=60, tool_timeout_sec=900, enabled=True)
        expected.setdefault('mcp_servers', {})[SERVER_NAME] = new
        pieces, cursor = [], 0
        headers = list(_section_headers(text))
        for i, (start, path) in enumerate(headers):
            if path[:2] == ('mcp_servers', SERVER_NAME):
                pieces.append(text[cursor:start])
                cursor = headers[i + 1][0] if i + 1 < len(headers) else len(text)
        pieces.append(text[cursor:])
        rendered = ''.join(pieces).rstrip() + '\n\n[mcp_servers.mcp-altium-suite]\n'
        for key, value in new.items():
            if key != 'env':
                rendered += key + ' = ' + json.dumps(value) + '\n'
        rendered += '\n[mcp_servers.mcp-altium-suite.env]\n'
        rendered += ''.join(key + ' = ' + json.dumps(value) + '\n' for key, value in new['env'].items())
        if tomllib.loads(rendered) != expected:
            raise ValueError('Cannot safely update inline TOML; use a separate server table')
        return rendered, rendered != text
    try:
        original = json.loads(text or '{}')
    except json.JSONDecodeError as error:
        raise ValueError('Client file is not strict JSON (possibly JSONC). Use the client UI or export strict JSON; no file changed') from error
    if not isinstance(original, dict):
        raise ValueError('Client configuration must be an object')
    key = 'servers' if client == 'vscode' else 'mcpServers'
    servers = original.setdefault(key, {})
    if not isinstance(servers, dict):
        raise ValueError(key + ' must be an object')
    new = dict(definition)
    if client == 'vscode':
        new['type'] = 'stdio'
    if servers.get(SERVER_NAME) == new:
        return text, False
    servers[SERVER_NAME] = new
    return json.dumps(original, ensure_ascii=False, indent=2) + '\n', True


def configure_clients(client, destination, python, data_directory, *, apply=False,
                      config=None, home=None, appdata=None):
    """Preflight all clients, then atomically write each with byte-exact backups.

    --all means all supported clients, not automatic installation of those apps.
    Any preflight error leaves every client untouched. Publication failure restores
    previously published files only when they still match this operation's bytes.
    """
    selected = CLIENTS if client == 'all' else (client,)
    if config is not None and len(selected) != 1:
        raise ValueError('--config requires one specific --client')
    definition = launcher_definition(destination, python, data_directory)
    paths = client_config_paths(home, appdata)
    prepared = []
    for name in selected:
        if name not in paths:
            raise ValueError('Unknown AI client: ' + name)
        raw_path = Path(config).absolute() if config else paths[name].absolute()
        if raw_path.is_symlink():
            raise ValueError('Refusing a linked client configuration: ' + str(raw_path))
        path = raw_path.resolve()
        old = path.read_bytes() if path.exists() else None
        rendered, changed = render_client(old.decode('utf-8-sig') if old else '', name, definition)
        prepared.append((name, path, old, rendered.encode('utf-8'), changed))
    summary = {'applied': False, 'clients': [{'client': n, 'config': str(p), 'changed': c}
                for n, p, _, _, c in prepared], 'reconnect_required': True}
    if not apply:
        return summary
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    backup = Path(data_directory) / 'client-config-backups' / stamp
    backup.mkdir(parents=True, exist_ok=False)
    published = []
    try:
        for name, path, old, new, changed in prepared:
            if not changed:
                continue
            current = path.read_bytes() if path.exists() else None
            if current != old:
                raise RuntimeError('Client config changed during preparation; retry: ' + str(path))
            if old is not None:
                (backup / (name + path.suffix)).write_bytes(old)
            _atomic_write(path, new)
            published.append((path, old, new))
    except BaseException:
        for path, old, new in reversed(published):
            if path.exists() and path.read_bytes() == new:
                if old is None:
                    path.unlink()
                else:
                    _atomic_write(path, old)
        raise
    summary.update(applied=True, backup=str(backup))
    (backup / 'manifest.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return summary


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
    parser.add_argument('--config', type=Path)
    parser.add_argument('--client', choices=(*CLIENTS, 'all'))
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--destination', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--python', type=Path)
    parser.add_argument('--cloud-cache', type=Path)
    parser.add_argument('--apply', action='store_true', help='otherwise print a read-only migration plan')
    args = parser.parse_args()
    destination = args.destination.resolve()
    python = args.python or destination / '.venv/Scripts/python.exe'
    if args.client:
        data = args.data_dir or Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'plugins/data/mcp-altium-suite'
        output = configure_clients(args.client, destination, python, data.resolve(),
                                   apply=args.apply, config=args.config)
    else:
        if args.cloud_cache is None:
            parser.error('--cloud-cache is required for migration of old standalone servers')
        output = migrate((args.config or Path.home() / '.codex/config.toml').resolve(), destination,
                         python.resolve(), args.cloud_cache.resolve(), args.apply)
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
