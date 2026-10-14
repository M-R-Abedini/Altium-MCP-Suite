"""Shared, portable configuration for the coordinated Windows MCP servers."""
from pathlib import Path
import hashlib
import json
import os
import uuid
from script_projects import PROJECT_NAMES, CATALOG, register_project, refresh_catalog

ROOT = Path(__file__).resolve().parent


def settings():
    path = ROOT / 'local-settings.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise RuntimeError(f'Cannot read {path}. Repair the JSON or regenerate it with setup.py --altium-exe.') from error
    if not isinstance(value, dict):
        raise RuntimeError(f'{path} must contain a JSON object. Regenerate it with setup.py --altium-exe.')
    executable = value.get('altium_exe')
    if executable is not None and (not isinstance(executable, str) or not executable.strip()):
        raise RuntimeError(f'{path}: altium_exe must be a non-empty path string.')
    return value


def runtime_dir():
    default = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'AltiumMCPSuite'
    return Path(os.environ.get('ALTIUM_MCP_RUNTIME', str(default))).resolve()


def workspace_dir():
    runtime = runtime_dir()
    identity = runtime / 'workspace-identity.json'
    try:
        saved = json.loads(identity.read_text(encoding='utf-8'))
        raw = saved['workspace']
        if not isinstance(raw, str) or not raw.strip() or not Path(raw).is_absolute() or saved.get('schema') != 1:
            raise ValueError('invalid workspace identity')
        recorded = Path(raw).resolve()
    except FileNotFoundError:
        recorded = None
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError(f'Cannot validate {identity}; repair the shared workspace configuration before starting either server.') from error
    explicit = os.environ.get('EDA_AGENT_WORKSPACE')
    selected = Path(explicit).resolve() if explicit else recorded or runtime / 'workspace'
    if recorded is not None and os.path.normcase(str(selected)) != os.path.normcase(str(recorded)):
        raise RuntimeError(f'Workspace mismatch: requested {selected}, shared runtime {runtime} uses {recorded}. Set EDA_AGENT_WORKSPACE identically for both servers; no script was launched.')
    return selected


def bind_workspace():
    """Publish a complete identity once; concurrent servers cannot overwrite it."""
    runtime = runtime_dir()
    selected = workspace_dir()
    runtime.mkdir(parents=True, exist_ok=True)
    target = runtime / 'workspace-identity.json'
    temporary = runtime / ('workspace-identity.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps({'schema': 1, 'workspace': str(selected)}), encoding='utf-8')
        try:
            os.link(temporary, target)
        except FileExistsError:
            pass
        # Compare even when the other process won the publication race.
        os.environ['EDA_AGENT_WORKSPACE'] = str(selected)
        workspace_dir()
    finally:
        temporary.unlink(missing_ok=True)
    return selected


def altium_exe():
    value = os.environ.get('ALTIUM_EXE') or settings().get('altium_exe')
    if value:
        return Path(value)
    candidates = list((Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Altium').glob('AD*/X2.EXE'))
    if len(candidates) != 1:
        raise RuntimeError('Set ALTIUM_EXE or run setup.py --altium-exe with the intended X2.EXE path.')
    return candidates[0]


def prepare_runtime():
    """Copy scripts to a content-addressed path so Altium recompiles changes.

    Generates only runtime files; never changes the machine-wide EDA workspace
    pointer. The EDA server writes that pointer when it actually starts.
    """
    runtime = runtime_dir()
    exchange = runtime / 'legacy-exchange'
    exchange.mkdir(parents=True, exist_ok=True)
    sources = {'eda': ROOT / 'eda-agent/scripts/altium',
               'legacy': ROOT / 'coffeenmusic/server/AltiumScript'}
    projects = {}
    for kind, source in sources.items():
        files = sorted(p for p in source.iterdir() if p.suffix.lower() in {'.pas', '.dfm', '.prjscr'})
        contents = {path: path.read_bytes() for path in files}
        workspace = workspace_dir()
        digest = hashlib.sha256(('script-project-lifecycle-v1:' + str(exchange)).encode())
        if kind == 'eda':
            digest.update(('workspace:' + str(workspace)).encode())
        for path in files:
            digest.update(path.name.encode())
            digest.update(contents[path])
        destination = runtime / 'scripts' / (kind + '-' + digest.hexdigest()[:16])
        destination.mkdir(parents=True, exist_ok=True)
        project = destination / PROJECT_NAMES[kind]
        hashes = {}
        for path in files:
            name = PROJECT_NAMES[kind] if path.suffix.lower() == '.prjscr' else path.name
            target = destination / name
            data = contents[path]
            if kind == 'eda' and path.name == 'Main.pas':
                text = data.decode('utf-8')
                for token, value in [('__ALTIUM_MCP_SCRIPT_PROJECT__', project),
                                     ('__ALTIUM_MCP_SCRIPT_CATALOG__', runtime / CATALOG),
                                     ('__ALTIUM_MCP_WORKSPACE__', workspace)]:
                    if text.count(token) != 1:
                        raise RuntimeError(f'{path} must contain exactly one {token} placeholder.')
                    text = text.replace(token, str(value).replace("'", "''"))
                data = text.encode('utf-8')
            if kind == 'legacy' and path.name == 'Altium_API.pas':
                text = data.decode('utf-8')
                if text.count('__ALTIUM_MCP_EXCHANGE_DIR__') != 1:
                    raise RuntimeError(f'{path} must contain exactly one exchange-directory placeholder; no legacy script was generated.')
                literal = (str(exchange) + '\\').replace("'", "''")
                data = text.replace('__ALTIUM_MCP_EXCHANGE_DIR__', literal).encode('utf-8')
            hashes[name] = hashlib.sha256(data).hexdigest()
            if target.exists():
                continue
            temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
            temporary.write_bytes(data)
            temporary.replace(target)
        register_project(project, kind, hashes)
        projects[kind] = project
    refresh_catalog(runtime)
    return projects


def configure_environment():
    bind_workspace()
    scripts = prepare_runtime()
    os.environ['ALTIUM_MCP_RUNTIME'] = str(runtime_dir())
    os.environ['ALTIUM_MCP_EDA_SCRIPT'] = str(scripts['eda'])
    os.environ.setdefault('EDA_AGENT_WORKSPACE', str(workspace_dir()))
    # Suite scripts contain their workspace. Upstream workspace preparation
    # must not repoint another installation through the machine-global file.
    os.environ.setdefault('EDA_AGENT_POINTER_FILE', str(runtime_dir() / 'workspace-path.txt'))
    os.environ.setdefault('ALTIUM_EXE', str(altium_exe()))
    os.environ['ALTIUM_MCP_LEGACY_SCRIPT'] = str(scripts['legacy'])
    os.environ['ALTIUM_MCP_LEGACY_EXCHANGE'] = str(runtime_dir() / 'legacy-exchange')
    return scripts
