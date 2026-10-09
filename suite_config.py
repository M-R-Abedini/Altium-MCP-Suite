"""Shared, portable configuration for the coordinated Windows MCP servers."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import uuid

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
    return Path(os.environ.get('EDA_AGENT_WORKSPACE', str(runtime_dir() / 'workspace'))).resolve()


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
        digest = hashlib.sha256(str(exchange).encode())
        for path in files:
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
        destination = runtime / 'scripts' / (kind + '-' + digest.hexdigest()[:16])
        destination.mkdir(parents=True, exist_ok=True)
        for path in files:
            target = destination / path.name
            if target.exists():
                continue
            temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
            if kind == 'legacy' and path.name == 'Altium_API.pas':
                text = path.read_text(encoding='utf-8')
                if text.count('__ALTIUM_MCP_EXCHANGE_DIR__') != 1:
                    raise RuntimeError(f'{path} must contain exactly one exchange-directory placeholder; no legacy script was generated.')
                literal = (str(exchange) + '\\').replace("'", "''")
                temporary.write_text(text.replace('__ALTIUM_MCP_EXCHANGE_DIR__', literal), encoding='utf-8')
            else:
                shutil.copy2(path, temporary)
            temporary.replace(target)
        projects[kind] = destination / 'Altium_API.PrjScr'
    return projects


def configure_environment():
    scripts = prepare_runtime()
    os.environ.setdefault('EDA_AGENT_WORKSPACE', str(workspace_dir()))
    os.environ.setdefault('ALTIUM_EXE', str(altium_exe()))
    os.environ['ALTIUM_MCP_LEGACY_SCRIPT'] = str(scripts['legacy'])
    os.environ['ALTIUM_MCP_LEGACY_EXCHANGE'] = str(runtime_dir() / 'legacy-exchange')
    return scripts
