"""Verified edits of native PrjPcb variants while the target project is closed.

The public Workspace Manager interfaces expose variation readers but no
fitted-state writer. Preserve the native serialization, then require native
readback after reopening. Never patch an open project's backing file.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
import re
import uuid


class ProjectFile:
    def __init__(self, data: bytes):
        self.encoding = 'utf-8-sig' if data.startswith(b'\xef\xbb\xbf') else 'utf-8'
        if data.startswith((b'\xff\xfe', b'\xfe\xff')):
            self.encoding = 'utf-16'
        try:
            self.text = data.decode(self.encoding)
        except UnicodeDecodeError:
            self.encoding = 'cp1252'
            self.text = data.decode(self.encoding)  # never replace undecodable bytes
        self.newline = '\r\n' if '\r\n' in self.text else '\n'

    def sections(self):
        matches = list(re.finditer(r'^\[([^\]\r\n]+)\][^\r\n]*\r?\n?', self.text, re.M))
        names = [m[1].casefold() for m in matches]
        if len(set(names)) != len(names):
            raise ValueError('Duplicate project sections; refusing ambiguous serialization')
        return [(m[1], m.end(), matches[i+1].start() if i+1 < len(matches) else len(self.text))
                for i, m in enumerate(matches)]

    def fields(self, section):
        for name, start, end in self.sections():
            if name == section:
                pairs = re.findall(r'^([^=\r\n]+)=(.*)\r?$', self.text[start:end], re.M)
                keys = [k.casefold() for k, _ in pairs]
                if len(set(keys)) != len(keys):
                    raise ValueError('Duplicate project keys; refusing ambiguous serialization')
                return {k: v.rstrip('\r') for k, v in pairs}
        raise ValueError(f'Missing section: {section}')

    def set(self, section, key, value):
        if any(c in str(value) for c in '\r\n'):
            raise ValueError('Newlines are not allowed in a project field')
        for name, start, end in self.sections():
            if name != section:
                continue
            body = self.text[start:end]
            pattern = re.compile(r'^' + re.escape(key) + r'=[^\r\n]*', re.M)
            if pattern.search(body):
                body = pattern.sub(lambda _: f'{key}={value}', body)
            else:
                body = body.rstrip('\r\n') + self.newline + f'{key}={value}' + self.newline*2
            self.text = self.text[:start] + body + self.text[end:]
            return
        raise ValueError(f'Missing section: {section}')

    def variants(self):
        return [dict(self.fields(name), section=name) for name, _, _ in self.sections()
                if re.fullmatch(r'ProjectVariant\d+', name)]

    def data(self):
        return self.text.encode(self.encoding)

    def create(self, label):
        if not label.strip() or any(c in label for c in '\r\n|'):
            raise ValueError('Variant display name must be nonempty and contain no newline or pipe')
        if any(v.get('Description') == label for v in self.variants()):
            raise ValueError('Variant display name already exists')
        indices = [int(v['section'][14:]) for v in self.variants()]
        section = 'ProjectVariant' + str(max(indices, default=0) + 1)
        uid = str(uuid.uuid4())
        fields = {'UniqueId': uid, 'Description': label, 'AllowFabrication': '0',
                  'ParameterCount': '0', 'VariationCount': '0',
                  'AllowVariationPastMask': '1', 'ParamVariationCount': '0'}
        self.text = self.text.rstrip('\r\n') + self.newline*2 + f'[{section}]' + self.newline
        self.text += self.newline.join(f'{k}={v}' for k, v in fields.items()) + self.newline
        return uid

    def fitted(self, uid, updates, components):
        variants = [v for v in self.variants() if v.get('UniqueId') == uid]
        if len(variants) != 1:
            raise ValueError('Variant UniqueId is missing or ambiguous')
        variant = variants[0]
        keys = [row.get('unique_id') for row in components]
        counts = Counter(keys)
        index = {row['unique_id']: row for row in components if row.get('unique_id')}
        seen = set()
        normalized = []
        for update in updates:
            key = update['unique_id']
            if key in seen or counts[key] != 1:
                raise ValueError(f'Duplicate, missing or ambiguous component UniqueId: {key}')
            if type(update['fitted']) is not bool:
                raise ValueError('fitted must be a boolean')
            seen.add(key)
            row = index[key]
            if any(c in key + row['designator'] for c in '|\r\n'):
                raise ValueError('Invalid component identity serialization')
            normalized.append((key, row['designator'], update['fitted']))
        entries = [variant.get(f'Variation{i}', '')
                   for i in range(1, int(variant.get('VariationCount', '0'))+1)]
        if any(not entry for entry in entries):
            raise ValueError('Incomplete existing variation records')
        for key, designator, fitted in normalized:
            matches = [i for i, entry in enumerate(entries)
                       if dict(item.split('=', 1) for item in entry.split('|') if '=' in item).get('UniqueId') == key]
            if len(matches) > 1:
                raise ValueError('Duplicate existing variation UniqueId')
            kind = '0' if fitted else '1'
            if matches:
                i = matches[0]
                parts = dict(item.split('=', 1) for item in entries[i].split('|') if '=' in item)
                if parts.get('Kind') == '2' or parts.get('AlternatePart'):
                    raise ValueError('Alternate-part variations require an explicit alternate-part workflow')
                if parts.get('Designator') != designator:
                    raise ValueError('Component designator changed; refresh the native component identities')
                entries[i] = re.sub(r'(^|\|)Kind=[^|]*', lambda m: m[1] + 'Kind=' + kind, entries[i])
            else:
                entries.append(f'Designator={designator}|UniqueId={key}|Kind={kind}|AlternatePart=')
        for i, entry in enumerate(entries, 1):
            self.set(variant['section'], f'Variation{i}', entry)
        self.set(variant['section'], 'VariationCount', len(entries))


def enrich_variants(listing):
    """Associate disk GUIDs only with unambiguous native display labels."""
    path = listing.get('project_path')
    saved = ProjectFile(Path(path).read_bytes()).variants() if path else []
    variants = listing.get('variants', [])
    counts = Counter(v.get('description') for v in variants)
    for variant in variants:
        label = variant.get('description') or variant.get('name', '')
        matches = [v for v in saved if v.get('Description') == label]
        variant['display_name'] = label
        variant['unique_id'] = (matches[0].get('UniqueId', '')
                                if len(matches) == 1 and counts[variant.get('description')] == 1 else '')
        variant['persisted'] = bool(variant['unique_id'])
    return listing


def resolve_variant(variants, selector):
    exact = [v for v in variants if v.get('unique_id') and v['unique_id'] == selector]
    aliases = [v for v in variants if selector in (v.get('display_name'), v.get('description'), v.get('name'))]
    matches = exact or aliases
    if len(matches) != 1:
        raise ValueError('Variant selector is missing or ambiguous; use the UniqueId from proj_list_variants')
    if not matches[0].get('unique_id'):
        raise ValueError('Save the project to persist this variant before editing it')
    return matches[0]


async def verified_save(bridge, project_path=None):
    params = {'project_path': project_path} if project_path else {}
    saved = await bridge.send_command_async('project.save', params)
    if not saved.get('success'):
        return saved
    path = Path(saved['project_path'])
    native = await bridge.send_command_async('project.get_variants', {'project_path': str(path)})
    documents = await bridge.send_command_async('project.get_documents', {'project_path': str(path)})
    file = ProjectFile(path.read_bytes())
    disk_variants = file.variants()
    disk_docs = [file.fields(section).get('DocumentPath', '') for section, _, _ in file.sections()
                 if re.fullmatch(r'Document\d+', section)]
    errors = []
    if len(disk_variants) != saved.get('variant_count') or native.get('count') != saved.get('variant_count'):
        errors.append('variant count')
    for variant in native.get('variants', []):
        matches = [v for v in disk_variants if v.get('Description') == variant.get('description')]
        if len(matches) != 1 or not matches[0].get('UniqueId'):
            errors.append('variant identity')
            continue
        disk = matches[0]
        expected = {(v['unique_id'], v['designator'], v['kind'], v.get('alternate_part', ''))
                    for v in variant.get('variations', [])}
        actual = set()
        for i in range(1, int(disk.get('VariationCount', '0'))+1):
            parts = dict(item.split('=', 1) for item in disk.get(f'Variation{i}', '').split('|') if '=' in item)
            actual.add((parts.get('UniqueId'), parts.get('Designator'),
                        {'0': 'Fitted', '1': 'Not Fitted', '2': 'Alternate'}.get(parts.get('Kind')), parts.get('AlternatePart', '')))
        if actual != expected or len(actual) != len(expected):
            errors.append('component variation records')
    # All registered logical members, including SchLib/PcbLib, must be on disk.
    native_docs = documents if isinstance(documents, list) else documents.get('documents', [])
    normalize = lambda value: str((path.parent / value).resolve()).casefold()
    actual_paths = {normalize(value) for value in disk_docs}
    verified_documents = 0
    for doc in native_docs:
        if doc.get('document_kind', '').casefold() == 'virtualbom':
            continue  # generated in-memory ActiveBOM has no DocumentN record
        verified_documents += 1
        if normalize(doc.get('file_path') or doc.get('full_path') or '') not in actual_paths:
            errors.append('document membership')
    if len(native_docs) != saved.get('document_count'):
        errors.append('native document readback')
    if errors:
        return dict(saved, success=False, verified=False, error='Disk verification failed: ' + ', '.join(sorted(set(errors))))
    return dict(saved, verified=True, definition_saved=True, verified_documents=verified_documents, verified_variants=len(disk_variants))


@contextmanager
def project_lock(path):
    """Nonblocking interprocess guard for suite variant transactions."""
    lock = path.with_suffix(path.suffix + '.mcp-variant.lock')
    stream = lock.open('a+b')
    try:
        stream.seek(0)
        if not stream.read(1):
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        if __import__('os').name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        stream.close()


async def edit_variants(bridge, project_path, mutate, verify):
    """Save, close, atomically edit, reopen, and verify; recover on failure."""
    saved = await verified_save(bridge, project_path)
    if not saved.get('success'):
        return saved
    path = Path(saved['project_path'])
    if path.suffix.lower() != '.prjpcb':
        return {'success': False, 'error': 'Variant editing requires a native .PrjPcb'}
    with project_lock(path):
        original = path.read_bytes()
        file = ProjectFile(original)
        identity = mutate(file)  # validate before closing anything
        edited = file.data()
        backup = path.with_name(path.name + '.variants-' + uuid.uuid4().hex + '.bak')
        written = False
        recovery_error = ''
        async def reopen():
            await bridge.send_command_async('project.open', {'project_path': str(path)})
            listing = await bridge.send_command_async('project.get_open_projects', {})
            if not any(str(p.get('project_path', '')).casefold() == str(path).casefold()
                       for p in listing.get('projects', [])):
                raise RuntimeError('Target project did not reopen')
        def replace(data):
            temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                temporary.write_bytes(data)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        try:
            closed = await bridge.send_command_async('project.close', {'project_path': str(path), 'save': False})
            if not closed.get('closed') or not closed.get('success'):
                return {'success': False, 'error': 'Project did not close; no file was changed'}
            if path.read_bytes() != original:
                raise RuntimeError('Project changed on disk while closing; edit aborted')
            backup.write_bytes(original)
            replace(edited)
            written = True
            await reopen()
            await verify(str(path), identity)
            return {'success': True, 'verified': True, 'project_path': str(path),
                    'backup_path': str(backup), 'unique_id': identity,
                    'method': 'closed_project_native_serialization'}
        except BaseException as exc:
            async def recover():
                nonlocal recovery_error
                try:
                    if written:
                        current = await bridge.send_command_async('project.get_open_projects', {})
                        if any(str(p.get('project_path', '')).casefold() == str(path).casefold() for p in current.get('projects', [])):
                            result = await bridge.send_command_async('project.close', {'project_path': str(path), 'save': False})
                            if not result.get('closed'):
                                raise RuntimeError('Recovery close failed; backup retained')
                        if path.read_bytes() != edited:
                            raise RuntimeError('External file change detected; backup retained without overwriting it')
                        replace(original)
                    await reopen()
                except Exception as error:
                    recovery_error = str(error)
            task = asyncio.create_task(recover())
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
            if isinstance(exc, asyncio.CancelledError):
                raise
            return {'success': False, 'verified': False, 'error': str(exc),
                    'backup_path': str(backup) if backup.exists() else None,
                    'recovered': not recovery_error, 'recovery_error': recovery_error}
