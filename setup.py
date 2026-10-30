"""Configure the suite without changing any MCP client's existing settings."""
import argparse
import json
from pathlib import Path
import sys
import suite_config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--altium-exe', required=True, type=Path)
    parser.add_argument('--library-dir', action='append', default=[], type=Path)
    parser.add_argument('--toolset', choices=('full', 'minimal'), default='minimal',
                        help='minimal advertises suite discovery tools; every backend operation remains reachable')
    parser.add_argument('--separate-servers', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--library-binary', type=Path,
                        help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.altium_exe.is_file():
        parser.error('Altium X2.EXE does not exist at that path')
    root = Path(__file__).resolve().parent
    for directory in args.library_dir:
        if not directory.is_dir():
            parser.error('Library directory does not exist: ' + str(directory))
    settings = {'altium_exe': str(args.altium_exe.resolve())}
    (root / 'local-settings.json').write_text(json.dumps(settings, indent=2), encoding='utf-8')
    scripts = suite_config.configure_environment()
    shared_env = {'ALTIUM_MCP_RUNTIME': str(suite_config.runtime_dir()),
                  'EDA_AGENT_WORKSPACE': str(suite_config.workspace_dir())}
    cloud_cache = suite_config.runtime_dir() / 'cloud-libraries'
    cloud_cache.mkdir(parents=True, exist_ok=True)
    config = {'mcpServers': {
        'eda-agent': {'command': sys.executable, 'args': [str(root / 'eda_stdio.py'), '--toolset', args.toolset], 'env': shared_env},
        'altium': {'command': sys.executable, 'args': [str(root / 'suite_legacy_compat.py')], 'env': shared_env},
    }}
    library_config = root / 'libraries.local.json'
    library_settings = {}
    if library_config.exists():
        try:
            library_settings = json.loads(library_config.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError) as error:
            parser.error('Cannot read existing library configuration: ' + str(error))
        if not isinstance(library_settings, dict) or not isinstance(library_settings.get('allowed_paths', []), list):
            parser.error('Existing library configuration must contain an allowed_paths list')
        if not all(isinstance(path, str) for path in library_settings.get('allowed_paths', [])):
            parser.error('Existing allowed_paths must contain path strings')
    allowed_paths = list(library_settings.get('allowed_paths', []))
    for path in [str(p.resolve()) for p in args.library_dir] + [str(cloud_cache)]:
        if path.casefold() not in {existing.casefold() for existing in allowed_paths}:
            allowed_paths.append(path)
    library_settings['allowed_paths'] = allowed_paths
    library_config.write_text(json.dumps(library_settings, indent=2), encoding='utf-8')
    config['mcpServers']['altium-libraries'] = {'command': sys.executable, 'args': [str(root / 'suite_libraries_native.py'), str(library_config)]}
    backend_names = {'eda-agent': 'eda', 'altium': 'legacy', 'altium-libraries': 'libraries'}
    backends = {}
    for name, server in config['mcpServers'].items():
        worker = dict(server, cwd=str(root), timeout_seconds=900)
        if name == 'eda-agent':
            worker['args'] = [str(root / 'eda_stdio.py'), '--toolset', 'full', '--no-dashboard']
        backends[backend_names[name]] = worker
    backend_config = root / 'suite-backends.local.json'
    backend_config.write_text(json.dumps({'backends': backends}, indent=2), encoding='utf-8')
    config = {'mcpServers': {'mcp-altium-suite': {
        'command': sys.executable,
        'args': [str(root / 'suite_stdio.py'), '--toolset', args.toolset,
                 '--backend-config', str(backend_config)],
        'env': dict(shared_env, ALTIUM_SUITE_CLOUD_CACHE=str(cloud_cache)),
        'startup_timeout_sec': 60, 'tool_timeout_sec': 900,
    }}}
    (root / 'mcp.local.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
    toml = []
    for name, server in config['mcpServers'].items():
        toml += [f'[mcp_servers.{name}]', 'command = '+json.dumps(server['command']), 'args = '+json.dumps(server['args'])]
        for key in ('startup_timeout_sec', 'tool_timeout_sec'):
            if key in server:
                toml.append(key + ' = ' + str(server[key]))
        toml.append('')
        if server.get('env'):
            toml += [f'[mcp_servers.{name}.env]']
            toml += [key + ' = ' + json.dumps(value) for key, value in server['env'].items()]
            toml.append('')
    (root / 'codex.local.toml').write_text('\n'.join(toml), encoding='utf-8')
    print('Created local MCP client configuration. Merge it into your client settings; restart the MCP servers once.')
    print('Runtime scripts:', scripts['eda'], scripts['legacy'])


if __name__ == '__main__':
    main()
