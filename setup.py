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
    args = parser.parse_args()
    if not args.altium_exe.is_file():
        parser.error('Altium X2.EXE does not exist at that path')
    root = Path(__file__).resolve().parent
    settings = {'altium_exe': str(args.altium_exe.resolve())}
    (root / 'local-settings.json').write_text(json.dumps(settings, indent=2), encoding='utf-8')
    scripts = suite_config.configure_environment()
    config = {'mcpServers': {
        'eda-agent': {'command': sys.executable, 'args': [str(root / 'eda_stdio.py')]},
        'altium': {'command': sys.executable, 'args': [str(root / 'coffeenmusic/server/codex_stdio.py')]},
    }}
    binary = root / 'altium-designer-mcp/target/release/altium-designer-mcp.exe'
    if args.library_dir:
        for directory in args.library_dir:
            if not directory.is_dir():
                parser.error('Library directory does not exist: ' + str(directory))
        library_config = root / 'libraries.local.json'
        library_config.write_text(json.dumps({'allowed_paths': [str(p.resolve()) for p in args.library_dir]}, indent=2), encoding='utf-8')
        config['mcpServers']['altium-libraries'] = {'command': str(binary), 'args': [str(library_config)]}
    (root / 'mcp.local.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
    toml = []
    for name, server in config['mcpServers'].items():
        toml += [f'[mcp_servers.{name}]', 'command = '+json.dumps(server['command']), 'args = '+json.dumps(server['args']), '']
    (root / 'codex.local.toml').write_text('\n'.join(toml), encoding='utf-8')
    print('Created local MCP client configuration. Merge it into your client settings; restart the MCP servers once.')
    print('Runtime scripts:', scripts['eda'], scripts['legacy'])
    if args.library_dir and not binary.is_file():
        print('Build the library server with cargo build --release --locked before enabling altium-libraries.')


if __name__ == '__main__':
    main()
