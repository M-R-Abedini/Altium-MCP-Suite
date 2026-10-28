"""Explicit plugin setup and read-only diagnosis; ordinary serving never installs.

Private configuration persists outside the plugin cache. Each launch resolves
the current package's workers, so installing an updated plugin updates its code.
SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STATE_SCHEMA = 1


def install_runtime(python, root):
    """Install a regular wheel, never an editable dependency on a disposable cache."""
    root = Path(root).resolve()
    lines = []
    for line in (root / 'requirements-runtime.txt').read_text(encoding='utf-8').splitlines():
        if line.strip() == '-e ./eda-agent':
            lines.append((root / 'eda-agent').as_uri())
        elif line.startswith('-c '):
            # pip parses option lines with POSIX shlex even on Windows.
            lines.append('-c "' + (root / line[3:].strip()).as_posix() + '"')
        else:
            lines.append(line)
    with tempfile.TemporaryDirectory(prefix='altium-runtime-install-') as scratch:
        requirements = Path(scratch) / 'requirements.txt'
        requirements.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)],
                       cwd=root, check=True, stdout=sys.stderr)


def detach(data, plugin_root=ROOT, *, probe=True):
    """Materialize independent dependencies while preserving the native workspace.

    The old environment and state remain usable until all replacements validate.
    Each launch still resolves Python source from the currently selected plugin.
    """
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 12):
        raise ValueError('Detach must run on Windows using Python 3.12')
    state, template = checked_state(data)
    validate_template(template)
    identifier = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    private = data / 'runtimes' / identifier
    python = private / 'python/Scripts/python.exe'
    private.mkdir(parents=True, exist_ok=False)
    subprocess.run([sys.executable, '-m', 'venv', str(private / 'python')], check=True, stdout=sys.stderr)
    install_runtime(python, plugin_root)
    library = template['backends']['libraries']
    binary = private / 'libraries/altium-designer-mcp.exe'
    binary.parent.mkdir(parents=True)
    shutil.copy2(library['command'], binary)
    config = private / 'libraries/config.json'
    library_settings = read_object(library['args'][0])
    audit = library_settings.get('logging', {}).get('audit_log_path')
    if audit and not Path(audit).is_absolute():
        # The Rust process resolves relative audit paths from its working dir.
        library_settings['logging']['audit_log_path'] = str((Path(library.get('cwd') or plugin_root) / audit).resolve())
    atomic_json(config, library_settings)
    for name in ('eda', 'legacy'):
        template['backends'][name]['command'] = str(python)
    template = remap_template(template, plugin_root)
    library = template['backends']['libraries']
    library['command'], library['args'] = str(binary), [str(config)]
    library['cwd'] = str(binary.parent)
    if probe:
        probe_python(python)
    validate_template(template)
    state.update(python=str(python), mode='self-contained', configured_at_utc=datetime.now(timezone.utc).isoformat())
    persist(data, state, template)
    return doctor(data, plugin_root, probe=probe)


def data_directory(explicit=None):
    if explicit:
        path = Path(explicit)
    elif os.environ.get('ALTIUM_SUITE_PLUGIN_DATA'):
        path = Path(os.environ['ALTIUM_SUITE_PLUGIN_DATA'])
    else:
        codex = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
        path = codex / 'plugins/data/mcp-altium-suite'
    if not path.is_absolute():
        raise ValueError('Plugin data directory must be absolute')
    return path.resolve()


def read_object(path):
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError(f'Expected a JSON object: {path}')
    return value


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(json.dumps(value, sort_keys=True, indent=2).encode('utf-8'))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def existing_file(value, name):
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f'{name} must be an existing absolute file path')
    return path.resolve()


def probe_python(python):
    # Import only dependencies; never import native suite configuration here.
    script = ('import sys,json,importlib.metadata as m; '
              'import mcp,jsonschema,pydantic,psutil,PIL,networkx,numpy,olefile; '
              'exec("import win32api,comtypes" if sys.platform == "win32" else ""); '
              'print(json.dumps({"python":list(sys.version_info[:3]),'
              '"packages":{n:m.version(n) for n in ["mcp","jsonschema","pydantic","psutil","Pillow","networkx","numpy","olefile"]}}))')
    completed = subprocess.run([str(python), '-I', '-c', script], capture_output=True,
                               text=True, timeout=45, check=False)
    if completed.returncode:
        raise ValueError('Selected Python lacks required runtime dependencies; run bootstrap or install requirements-runtime.txt')
    info = json.loads(completed.stdout)
    if info['python'][:2] != [3, 12]:
        raise ValueError('The tested plugin runtime requires Python 3.12')
    return info


def _altium_from_template(template, explicit=None):
    candidates = []
    if explicit:
        candidates.append(explicit)
    for worker in template.get('backends', {}).values():
        if worker.get('env', {}).get('ALTIUM_EXE'):
            candidates.append(worker['env']['ALTIUM_EXE'])
    eda = template.get('backends', {}).get('eda', {})
    args = eda.get('args', [])
    if args:
        settings = Path(args[0]).parent / 'local-settings.json'
        if settings.is_file():
            value = read_object(settings).get('altium_exe')
            if value:
                candidates.append(value)
    if not candidates:
        raise ValueError('Provide --altium-exe; the selected backend has no discoverable Altium installation')
    return existing_file(candidates[0], 'Altium executable')


def validate_template(template):
    backends = template.get('backends')
    if not isinstance(backends, dict) or not {'eda', 'legacy', 'libraries'} <= set(backends):
        raise ValueError('A complete suite needs eda, legacy and libraries backends')
    if set(backends) - {'eda', 'legacy', 'libraries'}:
        raise ValueError('Unexpected backend in template')
    allowed = {'command', 'args', 'env', 'cwd', 'timeout_seconds',
               'startup_timeout_seconds', 'enabled', 'enabled_tools', 'disabled_tools'}
    for name, spec in backends.items():
        if not isinstance(spec, dict) or set(spec) - allowed:
            raise ValueError(f'Invalid or unsupported backend settings: {name}')
        existing_file(spec.get('command', ''), name + ' executable')
        if not isinstance(spec.get('args'), list) or not all(isinstance(x, str) for x in spec['args']):
            raise ValueError(f'{name} args must be a list of strings')
        if not isinstance(spec.get('env', {}), dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in spec.get('env', {}).items()):
            raise ValueError(f'{name} environment must map strings to strings')
        if 'enabled' in spec and not isinstance(spec['enabled'], bool):
            raise ValueError(f'{name} enabled must be boolean')
        for key in ('enabled_tools', 'disabled_tools'):
            if key == 'enabled_tools' and spec.get(key) is None:
                continue
            if key in spec and (not isinstance(spec[key], list) or not all(isinstance(x, str) for x in spec[key])):
                raise ValueError(f'{name} {key} must be tool names')
        for key in ('timeout_seconds', 'startup_timeout_seconds'):
            if key in spec and (isinstance(spec[key], bool) or not isinstance(spec[key], (float, int)) or not 0 < spec[key] <= 86400):
                raise ValueError(f'{name} invalid {key}')
    for name, filename in (('eda', 'eda_stdio.py'), ('legacy', 'codex_stdio.py')):
        args = backends[name]['args']
        if not args or Path(args[0]).name != filename:
            raise ValueError(f'{name}: only the coordinated suite wrapper can be adopted')
        if backends[name].get('env', {}).get('EDA_AGENT_BACKEND', 'altium') != 'altium':
            raise ValueError('This plugin requires the Altium backend')
        for index, argument in enumerate(args):
            if argument.startswith('--backend=') and argument != '--backend=altium':
                raise ValueError('This plugin requires the Altium backend')
            if argument == '--backend' and (index + 1 == len(args) or args[index + 1] != 'altium'):
                raise ValueError('This plugin requires the Altium backend')
    envs = [backends[name].get('env', {}) for name in ('eda', 'legacy')]
    for key in ('ALTIUM_MCP_RUNTIME', 'EDA_AGENT_WORKSPACE'):
        values = [env.get(key) for env in envs]
        if not all(isinstance(v, str) and Path(v).is_absolute() for v in values):
            raise ValueError(f'Both native workers need an explicit absolute {key}')
        if os.path.normcase(str(Path(values[0]).resolve())) != os.path.normcase(str(Path(values[1]).resolve())):
            raise ValueError(f'Native workers disagree on {key}; no configuration changed')
    library = backends['libraries']
    if len(library['args']) != 1:
        raise ValueError('The library worker needs exactly one configuration-file argument')
    configuration = existing_file(library['args'][0], 'Library configuration')
    settings = read_object(configuration)
    if not isinstance(settings.get('allowed_paths'), list) or not all(isinstance(x, str) and Path(x).is_absolute() for x in settings['allowed_paths']):
        raise ValueError('Library allowed_paths must contain absolute path strings')
    validate_workspace(Path(envs[0]['ALTIUM_MCP_RUNTIME']), Path(envs[0]['EDA_AGENT_WORKSPACE']))
    return settings


def validate_workspace(runtime, workspace):
    if not runtime.is_absolute() or not workspace.is_absolute():
        raise ValueError('Native runtime and workspace must be absolute')
    identity = runtime / 'workspace-identity.json'
    if identity.is_file():
        recorded = read_object(identity)
        raw = recorded.get('workspace')
        if recorded.get('schema') != 1 or not isinstance(raw, str) or not Path(raw).is_absolute():
            raise ValueError('Invalid persisted native workspace identity; no configuration changed')
        if os.path.normcase(str(Path(raw).resolve())) != os.path.normcase(str(workspace.resolve())):
            raise ValueError('Persisted native workspace identity conflicts with the selected manifest')


def remap_template(template, plugin_root):
    """Upgrade-safe resolution: runtime settings persist; source paths do not."""
    value = json.loads(json.dumps(template))
    root = Path(plugin_root).resolve()
    for name, script in (('eda', root / 'eda_stdio.py'),
                         ('legacy', root / 'coffeenmusic/server/codex_stdio.py')):
        if not script.is_file():
            raise ValueError(f'Plugin package is missing its {name} wrapper')
        worker = value['backends'][name]
        worker['args'][0] = str(script)
        worker['cwd'] = str(root)
        # Do not inherit a KiCad/EasyEDA selection from an unrelated client.
        worker.setdefault('env', {}).setdefault('EDA_AGENT_BACKEND', 'altium')
        if name == 'eda':
            args = worker['args']
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
    return value


def load_state(data):
    state = read_object(data / 'state.json')
    if state.get('schema') != STATE_SCHEMA:
        raise ValueError('Unsupported plugin state schema; run setup again')
    existing_file(state.get('python', ''), 'Python runtime')
    cloud = Path(state.get('cloud_cache', ''))
    if not cloud.is_absolute():
        raise ValueError('Cloud cache must be absolute')
    template = read_object(data / 'backend-template.json')
    return state, template


def persist(data, state, template):
    data.mkdir(parents=True, exist_ok=True)
    if (data / 'state.json').exists() or (data / 'backend-template.json').exists():
        backup = data / 'backups' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
        backup.mkdir(parents=True)
        for name in ('state.json', 'backend-template.json'):
            if (data / name).exists():
                shutil.copy2(data / name, backup / name)
    # Store the pair in state last; on interruption state hash prevents use of
    # an uncommitted template rather than guessing or launching another engine.
    state['backend_template_sha256'] = hashlib.sha256(json.dumps(template, sort_keys=True).encode()).hexdigest()
    atomic_json(data / 'backend-template.json', template)
    atomic_json(data / 'state.json', state)


def checked_state(data):
    state, template = load_state(data)
    digest = hashlib.sha256(json.dumps(template, sort_keys=True).encode()).hexdigest()
    if state.get('backend_template_sha256') != digest:
        raise ValueError('Plugin state/template pair is incomplete; restore its backup or run setup')
    return state, template


def adopt(backend_config, python, data, altium_exe=None, cloud_cache=None, probe=True):
    python = existing_file(python, 'Python runtime')
    template = read_object(existing_file(backend_config, 'Backend configuration'))
    library_settings = validate_template(template)
    altium = _altium_from_template(template, altium_exe)
    runtime = Path(template['backends']['eda']['env']['ALTIUM_MCP_RUNTIME'])
    cloud = Path(cloud_cache) if cloud_cache else runtime / 'cloud-libraries'
    if not cloud.is_absolute():
        raise ValueError('Cloud cache must be absolute')
    if probe:
        probe_python(python)
    for name in ('eda', 'legacy'):
        template['backends'][name]['env']['ALTIUM_EXE'] = str(altium)
    state = {'schema': STATE_SCHEMA, 'python': str(python), 'cloud_cache': str(cloud.resolve()),
             'altium_exe': str(altium), 'mode': 'adopted',
             'configured_at_utc': datetime.now(timezone.utc).isoformat()}
    persist(data, state, template)
    cloud_allowed = any(cloud.resolve().is_relative_to(Path(path).resolve()) for path in library_settings['allowed_paths'])
    return {'configured': True, 'data_directory': str(data), 'mode': 'adopted',
            'backends': list(template['backends']), 'cloud_cache_allowed': cloud_allowed,
            'native_editor_probed': False, 'client_reconnect_needed': True}


def doctor(data, plugin_root=ROOT, probe=True):
    output = {'configured': False, 'ready': False, 'data_directory': str(data),
              'platform': sys.platform, 'native_editor_probed': False,
              'altium_is_separate_prerequisite': True, 'issues': []}
    try:
        state, template = checked_state(data)
        output['configured'] = True
        library_settings = validate_template(template)
        resolved = remap_template(template, plugin_root)
        output['backends'] = {name: {'enabled': spec.get('enabled', True),
                                     'executable_exists': Path(spec['command']).is_file()}
                              for name, spec in resolved['backends'].items()}
        output['altium_executable_exists'] = Path(state['altium_exe']).is_file()
        if not output['altium_executable_exists']:
            output['issues'].append('The configured Altium executable is missing')
        cloud = Path(state['cloud_cache'])
        output['cloud_cache_allowed'] = any(cloud.resolve().is_relative_to(Path(path).resolve()) for path in library_settings['allowed_paths'])
        if not output['cloud_cache_allowed']:
            output['issues'].append('Cloud cache is outside the library worker allowlist')
        if probe:
            output['python_runtime'] = probe_python(state['python'])
        if sys.platform != 'win32':
            output['issues'].append('Native Altium automation requires Windows')
        output['ready'] = not output['issues']
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        output['issues'].append(str(error))
    return output


def launch_configuration(data, plugin_root=ROOT):
    state, template = checked_state(data)
    validate_template(template)
    resolved = remap_template(template, plugin_root)
    encoded = json.dumps(resolved, sort_keys=True, indent=2).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    manifest = data / 'launch-manifests' / (digest + '.json')
    if manifest.exists():
        if manifest.read_bytes() != encoded:
            raise ValueError('A generated launch manifest was changed; inspect it before reconnecting')
    else:
        atomic_json(manifest, resolved)
    return state, manifest


def bootstrap(args, data, plugin_root=ROOT):
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 12):
        raise ValueError('Bootstrap must run on Windows using Python 3.12')
    altium = existing_file(args.altium_exe, 'Altium executable')
    root = Path(plugin_root).resolve()
    for relative in ('requirements-runtime.txt', 'constraints-windows-py312.txt',
                     'eda-agent/pyproject.toml', 'eda_stdio.py', 'suite_stdio.py',
                     'coffeenmusic/server/codex_stdio.py'):
        existing_file(root / relative, 'Packaged ' + relative)
    binary = data / 'rust-build/release/altium-designer-mcp.exe' if args.build_libraries else existing_file(args.library_binary, 'Library worker executable')
    runtime = Path(args.runtime_dir) if args.runtime_dir else Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'AltiumMCPSuite'
    identity = runtime / 'workspace-identity.json'
    if args.workspace:
        workspace = Path(args.workspace)
    elif identity.is_file():
        raw = read_object(identity).get('workspace')
        if not isinstance(raw, str):
            raise ValueError('Invalid persisted native workspace identity')
        workspace = Path(raw)
    else:
        workspace = runtime / 'workspace'
    validate_workspace(runtime, workspace)
    runtime, workspace = runtime.resolve(), workspace.resolve()
    cloud = data / 'cloud-libraries'
    roots = [str(Path(path).resolve()) for path in args.library_dir]
    if not all(Path(path).is_dir() for path in roots):
        raise ValueError('Library directories must already exist')
    library_config = data / 'libraries.json'
    library_settings = read_object(library_config) if library_config.is_file() else {'allowed_paths': []}
    if not isinstance(library_settings.get('allowed_paths'), list) or not all(isinstance(path, str) and Path(path).is_absolute() for path in library_settings['allowed_paths']):
        raise ValueError('Existing library configuration has an invalid allowlist')
    existing_roots = library_settings['allowed_paths']
    library_settings['allowed_paths'] = list(dict.fromkeys(existing_roots + roots + [str(cloud)]))
    environment = data / 'python-3.12'
    python = environment / 'Scripts/python.exe'
    data.mkdir(parents=True, exist_ok=True)
    if not python.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(environment)], check=True)
    install_runtime(python, root)
    if args.build_libraries:
        env = dict(os.environ, CARGO_TARGET_DIR=str(data / 'rust-build'))
        subprocess.run(['cargo', 'build', '--release', '--locked'], cwd=root / 'altium-designer-mcp',
                       env=env, check=True, stdout=sys.stderr)
    cloud.mkdir(parents=True, exist_ok=True)
    if library_config.exists():
        saved = data / 'backups' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]) / library_config.name
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(library_config, saved)
    atomic_json(library_config, library_settings)
    common = {'ALTIUM_EXE': str(altium), 'ALTIUM_MCP_RUNTIME': str(runtime),
              'EDA_AGENT_WORKSPACE': str(workspace), 'EDA_AGENT_NO_DASHBOARD': '1',
              'EDA_AGENT_BACKEND': 'altium'}
    template = {'backends': {
        'eda': {'command': str(python), 'args': [str(root / 'eda_stdio.py'), '--toolset', 'full', '--no-dashboard'],
                'env': common, 'timeout_seconds': 900},
        'legacy': {'command': str(python), 'args': [str(root / 'coffeenmusic/server/codex_stdio.py')],
                   'env': common, 'timeout_seconds': 900},
        'libraries': {'command': str(binary), 'args': [str(library_config)], 'timeout_seconds': 180},
    }}
    validate_template(template)
    probe_python(python)
    state = {'schema': STATE_SCHEMA, 'python': str(python), 'cloud_cache': str(cloud),
             'altium_exe': str(altium), 'mode': 'bootstrapped',
             'configured_at_utc': datetime.now(timezone.utc).isoformat()}
    persist(data, state, template)
    return doctor(data, root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--plugin-root', type=Path, default=ROOT)
    parser.add_argument('--client', choices=('codex', 'claude-desktop', 'claude-code', 'cursor', 'windsurf', 'vscode', 'all'),
                        help='Configure clients after successful setup (otherwise no client files change)')
    sub = parser.add_subparsers(dest='operation', required=True)
    sub.add_parser('doctor')
    adopt_parser = sub.add_parser('adopt')
    adopt_parser.add_argument('--backend-config', type=Path, required=True)
    adopt_parser.add_argument('--python', type=Path, required=True)
    adopt_parser.add_argument('--altium-exe', type=Path)
    adopt_parser.add_argument('--cloud-cache', type=Path)
    fresh = sub.add_parser('bootstrap')
    fresh.add_argument('--altium-exe', type=Path, required=True)
    choice = fresh.add_mutually_exclusive_group(required=True)
    choice.add_argument('--library-binary', type=Path)
    choice.add_argument('--build-libraries', action='store_true')
    fresh.add_argument('--library-dir', type=Path, action='append', default=[])
    fresh.add_argument('--runtime-dir', type=Path)
    fresh.add_argument('--workspace', type=Path)
    sub.add_parser('serve')
    sub.add_parser('detach', help='Replace adopted dependencies with a private independent environment')
    clients = sub.add_parser('configure-clients')
    clients.add_argument('--apply', action='store_true')
    clients.add_argument('--config', type=Path)
    args = parser.parse_args(argv)
    data = data_directory(args.data_dir)
    try:
        if args.operation == 'adopt':
            output = adopt(args.backend_config, args.python, data, args.altium_exe, args.cloud_cache)
        elif args.operation == 'bootstrap':
            output = bootstrap(args, data, args.plugin_root)
        elif args.operation == 'doctor':
            output = doctor(data, args.plugin_root)
        elif args.operation == 'detach':
            output = detach(data, args.plugin_root)
        elif args.operation == 'configure-clients':
            if not args.client:
                raise ValueError('configure-clients requires --client')
            sys.path.insert(0, str(args.plugin_root))
            from configure_suite import configure_clients
            state, _ = checked_state(data)
            output = configure_clients(args.client, args.plugin_root, state['python'], data,
                                       apply=args.apply, config=args.config)
        else:
            state, manifest = launch_configuration(data, args.plugin_root)
            env = dict(os.environ, ALTIUM_SUITE_CLOUD_CACHE=state['cloud_cache'],
                       ALTIUM_SUITE_PLUGIN_DATA=str(data))
            completed = subprocess.run([state['python'], '-u', str(args.plugin_root / 'suite_stdio.py'),
                                        '--toolset', 'minimal', '--backend-config', str(manifest)],
                                       cwd=args.plugin_root, env=env, check=False)
            return completed.returncode
        if args.client and args.operation in {'adopt', 'bootstrap', 'detach'}:
            if output.get('ready', output.get('configured', False)):
                sys.path.insert(0, str(args.plugin_root))
                from configure_suite import configure_clients
                state, _ = checked_state(data)
                output['client_configuration'] = configure_clients(args.client, args.plugin_root, state['python'], data, apply=True)
        print(json.dumps(output, indent=2))
        return 1 if args.operation in {'doctor', 'bootstrap'} and not output['ready'] else 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print('MCP Altium Suite setup: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
