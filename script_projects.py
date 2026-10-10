"""Track generated script projects without claiming user projects or edits.

SPDX-License-Identifier: MIT
Copyright (c) 2026 M-R-Abedini
"""
import hashlib
from functools import lru_cache
import json
from pathlib import Path
import re
import uuid

PROJECT_NAMES = {'eda': 'AltiumMCP-EDA.PrjScr', 'legacy': 'AltiumMCP-Legacy.PrjScr',
                 'sandbox': 'Sandbox.PrjScr'}
MARKER = 'suite-project.json'
CATALOG = 'managed-script-projects.jsonl'


@lru_cache(maxsize=2048)
def _cached_hash(path, size, modified_ns):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_hash(path):
    stat = path.stat()
    return _cached_hash(path, stat.st_size, stat.st_mtime_ns)


def _write_changed(path, text):
    try:
        if path.read_text(encoding='utf-8') == text:
            return
    except (FileNotFoundError, UnicodeError):
        pass
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(text, encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def register_project(project, kind, files):
    """Record expected file hashes; edits are never adopted as generated code."""
    project = Path(project).resolve()
    for name, digest in files.items():
        path = project.parent / name
        if path.resolve().parent != project.parent or _file_hash(path) != digest:
            raise RuntimeError(f'Generated script was changed: {path}. Preserve the edits before regenerating it.')
    record = dict(generator='Altium-MCP-Suite', schema=1, kind=kind,
                  project_path=str(project), files=files)
    _write_changed(project.parent / MARKER, json.dumps(record, sort_keys=True))


def register_sandbox_project(project):
    project = Path(project)
    files = {name: hashlib.sha256((project.parent / name).read_bytes()).hexdigest()
             for name in ('Sandbox.pas', 'Sandbox.PrjScr')}
    register_project(project, 'sandbox', files)


def _validated_record(folder, kinds):
    try:
        record = json.loads((folder / MARKER).read_text(encoding='utf-8'))
        kind = record['kind']
        project = Path(record['project_path'])
        if (record['generator'] != 'Altium-MCP-Suite' or record['schema'] != 1 or
                kind not in kinds or project.parent.resolve() != folder.resolve() or
                project.name not in {PROJECT_NAMES[kind], 'Altium_API.PrjScr'} or
                not project.is_file() or not record['files'] or project.name not in record['files']):
            return None
        for name, digest in record['files'].items():
            if '/' in name or '\\' in name or name in {'.', '..'}:
                return None
            path = folder / name
            if path.resolve().parent != folder.resolve() or _file_hash(path) != digest:
                return None
        return project.resolve()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _adopt_original_snapshot(folder, runtime, kind):
    """Recognize pre-marker snapshots only when their original digest matches."""
    if (folder / MARKER).exists() or not (folder / 'Altium_API.PrjScr').is_file():
        return
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in {'.pas', '.dfm', '.prjscr'})
    digest = hashlib.sha256(str(runtime / 'legacy-exchange').encode())
    hashes = {}
    for path in files:
        if path.resolve().parent != folder.resolve():
            return
        data = path.read_bytes()
        hashes[path.name] = hashlib.sha256(data).hexdigest()
        if kind == 'legacy' and path.name == 'Altium_API.pas':
            literal = (str(runtime / 'legacy-exchange') + '\\').replace("'", "''")
            text = data.decode('utf-8')
            if text.count(literal) != 1:
                return
            data = text.replace(literal, '__ALTIUM_MCP_EXCHANGE_DIR__').encode('utf-8')
        digest.update(path.name.encode())
        digest.update(data)
    if folder.name == kind + '-' + digest.hexdigest()[:16]:
        register_project(folder / 'Altium_API.PrjScr', kind, hashes)


def refresh_catalog(runtime):
    """Publish only intact, owned snapshots. Retain source files for rollback."""
    runtime = Path(runtime).resolve()
    projects = set()
    for folder in (runtime / 'scripts').glob('*'):
        match = re.fullmatch(r'(eda|legacy)-[0-9a-f]{16}', folder.name)
        if not match or folder.resolve().parent != (runtime / 'scripts').resolve():
            continue
        try:
            _adopt_original_snapshot(folder, runtime, match[1])
            project = _validated_record(folder, {match[1]})
        except (OSError, ValueError):
            project = None
        if project is not None:
            projects.add(project)
    sandbox = runtime / 'legacy-exchange' / 'sandbox'
    for folder in sandbox.glob('*'):
        if not re.fullmatch(r'[0-9a-f]{32}', folder.name) or folder.resolve().parent != sandbox.resolve():
            continue
        project = _validated_record(folder, {'sandbox'})
        if project is not None:
            projects.add(project)
    entries = []
    for project in sorted(projects):
        count = sum(line.startswith('DocumentPath=') for line in project.read_text(encoding='utf-8-sig').splitlines())
        entries.append(json.dumps({'project_path': str(project), 'document_count': count}))
    _write_changed(runtime / CATALOG, '\n'.join(entries) + ('\n' if entries else ''))
    return projects
