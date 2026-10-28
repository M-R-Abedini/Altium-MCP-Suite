"""Disk checkpoints before native edits; deliberately not an in-memory undo.

SPDX-License-Identifier: MIT
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import uuid
from typing import Any, Callable

DESIGN_SUFFIXES = {'.schdoc', '.pcbdoc', '.schlib', '.pcblib', '.prjpcb', '.outjob', '.intlib'}
# Query-named tools which can persist/modify or launch asynchronous mutation.
MUTATING_READ_NAMES = {'proj_get_nets', 'proj_export_netlist', 'design_validate',
                       'app_checkpoint', 'design_job_start', 'design_execute_plan',
                       'print_all_variants', 'proj_compile', 'proj_validate'}
READ_VERBS = {'get', 'list', 'query', 'read', 'inspect', 'status', 'context', 'ping',
              'describe', 'suggest', 'compute', 'preview', 'check', 'catalog', 'search',
              'measure', 'compare', 'find', 'report', 'analyze', 'audit', 'validate'}
OFFLINE_TOOLS = {
    'design_add_part', 'design_add_circuit_block', 'design_compose_netlist',
    'design_generate_bom', 'design_edit_plan', 'design_review_plan',
    'design_layout_schematic', 'design_apply_hierarchy', 'design_job_start',
    'design_job_result', 'design_load_fab_profile', 'design_synthesize_rules',
    'design_learn_from_layout', 'design_connect_bus',
}


def is_mutation(name: str, arguments: dict, read_only: bool | None = None) -> bool:
    """Conservative native classification; annotations are hints, not authority."""
    if name == 'tool_invoke':
        return is_mutation(arguments.get('name', ''), arguments.get('arguments') or {})
    if name in OFFLINE_TOOLS:
        return False
    if name in {'proj_get_nets', 'proj_get_connectivity', 'proj_get_connectivity_many'}:
        return bool(arguments.get('force_recompile'))
    if name in MUTATING_READ_NAMES or any(v in name.split('_') for v in ('export', 'import', 'save', 'write', 'restore', 'run')):
        return True
    words = name.split('_')
    if any(v in READ_VERBS for v in words[:3]):
        return False
    # Explicitly offline planner operations are not editor writes.
    if name.startswith(('design_session_', 'design_next_', 'design_autonomy_', 'tool_')):
        return False
    return True


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def _no_links(path: Path) -> None:
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or (hasattr(candidate, 'is_junction') and candidate.is_junction()):
            raise ValueError('Checkpoint paths must not traverse links or junctions')


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _no_links(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.checkpoint-', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def offline_restore_guard():
    """Hold the suite mutex, refuse any open Altium process or uncertainty.

    Closed editors ensure restored disk bytes cannot be overwritten by an old
    unsaved editor buffer. An inactive queue alone is insufficient evidence.
    """
    import psutil
    lease = None
    if os.name == 'nt':
        import engine_identity
        lease = engine_identity.try_mutex()
        if lease is None:
            raise RuntimeError('Native engine is busy; checkpoint restore was refused')
    try:
        for process in psutil.process_iter(['name']):
            try:
                if (process.info.get('name') or '').casefold() in {'x2.exe', 'dxp.exe', 'altiumdesigner.exe'}:
                    raise RuntimeError('Close Altium before restoring disk checkpoints; unsaved editor buffers are not restored')
            except psutil.NoSuchProcess:
                continue
        yield
    finally:
        if lease is not None:
            lease.release()


class CheckpointStore:
    """Per-user snapshots with hashes and recoverable pre-restore copies."""
    def __init__(self, data_dir: Path, *, max_bytes=512 * 1024 * 1024, restore_guard: Callable = offline_restore_guard):
        self.root = Path(data_dir) / 'backups'
        self.max_bytes = max_bytes
        self.restore_guard = restore_guard

    def create(self, files: list[str], *, tool: str = 'manual', connectivity=None,
               connectivity_status='unavailable', connectivity_target=None,
               scope='explicit targets', warnings=None) -> dict:
        if not all(isinstance(p, str) and Path(p).is_absolute() for p in files):
            raise ValueError('Checkpoints require absolute paths')
        paths = sorted({str(Path(p).absolute()) for p in files}, key=str.casefold)
        for raw in paths:
            path = Path(raw)
            if not path.is_absolute() or path.suffix.casefold() not in DESIGN_SUFFIXES:
                raise ValueError('Checkpoints accept absolute Altium design/library paths only')
            _no_links(path)
        project = digest(paths)[:24]
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '-' + uuid.uuid4().hex[:8]
        checkpoint_id = project + '/' + stamp
        folder = self.root / project / stamp
        _no_links(folder)
        entries, total = [], 0
        started = time.perf_counter()
        try:
            for index, raw in enumerate(paths):
                path = Path(raw)
                if not path.exists():
                    entries.append({'path': raw, 'existed': False})
                    continue
                if not path.is_file():
                    raise ValueError('Checkpoint target is not a regular file')
                size = path.stat().st_size
                if total + size > self.max_bytes:
                    raise ValueError('Checkpoint size limit exceeded; native edit was not dispatched')
                data = path.read_bytes()
                if total + len(data) > self.max_bytes:
                    raise ValueError('Checkpoint size limit exceeded during read; native edit was not dispatched')
                # Detect concurrent disk saves; do not label a torn copy safe.
                if hashlib.sha256(data).digest() != hashlib.sha256(path.read_bytes()).digest():
                    raise RuntimeError('Target changed during snapshot; native edit was not dispatched')
                total += len(data)
                blob = f'{index:04d}.bin'
                sha = hashlib.sha256(data).hexdigest()
                object_file = self.root / 'objects' / (sha + '.bin')
                _no_links(object_file)
                if object_file.exists():
                    if hashlib.sha256(object_file.read_bytes()).hexdigest() != sha:
                        raise ValueError('Existing checkpoint object is corrupt; native edit was not dispatched')
                else:
                    _atomic(object_file, data)
                folder.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(object_file, folder / blob)
                except OSError:
                    # Filesystems without hardlinks retain the same format.
                    _atomic(folder / blob, data)
                entries.append({'path': raw, 'existed': True, 'blob': blob,
                                'sha256': sha, 'size': len(data)})
            manifest = {'schema': 1, 'checkpoint_id': checkpoint_id, 'created_at': datetime.now(timezone.utc).isoformat(),
                        'tool': tool, 'files': entries, 'scope': scope,
                        'snapshot_kind': 'disk_only', 'unsaved_memory_captured': False,
                        'connectivity_status': connectivity_status,
                        'connectivity_target': connectivity_target,
                        'connectivity_sha256': digest(connectivity) if connectivity is not None else None,
                        'warnings': warnings or [], 'transactional_rollback': False}
            manifest['capture_ms'] = round((time.perf_counter() - started) * 1000, 3)
            _atomic(folder / 'manifest.json', json.dumps(manifest, sort_keys=True, indent=2).encode())
            return manifest
        except BaseException:
            # Incomplete folders have no manifest and never appear restorable.
            raise

    def list(self, limit=30) -> dict:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('limit must be between 1 and 100')
        output = []
        for path in sorted(self.root.glob('*/*/manifest.json'), reverse=True):
            _no_links(path)
            value = json.loads(path.read_text(encoding='utf-8'))
            output.append(value)
        output.sort(key=lambda value: value['created_at'], reverse=True)
        return {'checkpoints': output[:limit], 'total': len(output)}

    def restore(self, checkpoint_id: str) -> dict:
        import re
        if not re.fullmatch(r'[0-9a-f]{24}/[0-9TZ.]+-[0-9a-f]{8}', checkpoint_id):
            raise ValueError('Invalid checkpoint identifier')
        folder = self.root / checkpoint_id
        _no_links(folder)
        value = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        if value.get('schema') != 1 or value.get('checkpoint_id') != checkpoint_id:
            raise ValueError('Checkpoint manifest identity mismatch')
        if digest(sorted({entry['path'] for entry in value['files']}, key=str.casefold))[:24] != checkpoint_id.split('/')[0]:
            raise ValueError('Checkpoint target manifest changed')
        prepared = []
        for entry in value['files']:
            path = Path(entry['path'])
            if not path.is_absolute() or path.suffix.casefold() not in DESIGN_SUFFIXES:
                raise ValueError('Invalid checkpoint target')
            _no_links(path)
            if not entry['existed']:
                continue  # Never delete newly created user files as a side effect.
            if not re.fullmatch(r'[0-9]{4}\.bin', entry['blob']):
                raise ValueError('Invalid checkpoint blob')
            blob = folder / entry['blob']
            _no_links(blob)
            data = blob.read_bytes()
            if hashlib.sha256(data).hexdigest() != entry['sha256'] or len(data) != entry['size']:
                raise ValueError('Checkpoint digest mismatch; nothing was restored')
            prepared.append((path, data))
        with self.restore_guard():
            previous = self.create([str(path) for path, _ in prepared], tool='before_restore')
            for path, data in prepared:
                _atomic(path, data)
        return {'restored': [str(path) for path, _ in prepared], 'recovery_checkpoint': previous['checkpoint_id'],
                'transactional_rollback': False, 'reopen_documents_required': True}


def checkpoint_tools():
    return [
        {'name': 'list_checkpoints', 'description': 'List automatic/manual disk snapshots. Does not capture unsaved Altium buffers.',
         'inputSchema': {'type': 'object', 'properties': {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, 'additionalProperties': False}},
        {'name': 'create_checkpoint', 'description': 'Checkpoint explicit saved Altium design/library files without saving or editing Altium.',
         'inputSchema': {'type': 'object', 'properties': {'files': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}}}, 'required': ['files'], 'additionalProperties': False}},
        {'name': 'restore_checkpoint', 'description': 'Restore verified disk files. Requires Altium closed; keeps a recovery snapshot. No in-memory undo.',
         'inputSchema': {'type': 'object', 'properties': {'checkpoint_id': {'type': 'string'}}, 'required': ['checkpoint_id'], 'additionalProperties': False}},
    ]


class SafetyService:
    """Gateway adapter. Disk backup failures stop writes before dispatch."""
    def __init__(self, data_dir: Path, native_read):
        self.store = CheckpointStore(data_dir)
        self.native_read = native_read

    def list_tools(self):
        return checkpoint_tools()

    def call_tool(self, name, arguments):
        if name == 'list_checkpoints':
            return self.store.list(**arguments)
        if name == 'create_checkpoint':
            return self.store.create(**arguments)
        if name == 'restore_checkpoint':
            return self.store.restore(**arguments)
        raise ValueError('Unknown checkpoint tool')

    async def before(self, name, arguments, checkpoint_files=None):
        """Snapshot explicit targets and all visible project members.

        Does not save, force compile or claim atomicity with external UI edits.
        Unavailable connectivity is explicit rather than a fabricated digest.
        """
        import asyncio
        files = set()
        if checkpoint_files is not None:
            if not isinstance(checkpoint_files, list) or not all(
                    isinstance(path, str) and Path(path).is_absolute()
                    and Path(path).suffix.casefold() in DESIGN_SUFFIXES for path in checkpoint_files):
                raise ValueError('checkpoint_files must contain absolute Altium design/library paths')

        def collect(value, key=''):
            if isinstance(value, str):
                document_scope = value.startswith('doc:')
                if value.startswith('doc:'):
                    value = value[4:]
                path = Path(value)
                if (path.suffix.casefold() in DESIGN_SUFFIXES and not path.is_absolute()
                        and (document_scope or key.casefold() in {'path', 'file_path', 'document_path',
                             'board_path', 'project_path', 'library_path', 'output_path', 'checkpoint_files'})):
                    raise ValueError('Native checkpoint targets must be absolute; resolve the intended design path before editing')
                if path.is_absolute() and path.suffix.casefold() in DESIGN_SUFFIXES:
                    files.add(str(path))
            elif isinstance(value, dict):
                for item_key, item in value.items():
                    collect(item, item_key)
            elif isinstance(value, list):
                for item in value:
                    collect(item, key)

        collect(arguments)
        if checkpoint_files:
            collect(checkpoint_files)
        opaque = name in {'app_run_menu', 'obj_run_process', 'run_altium_script',
                          'create_footprints_batch', 'create_symbols_batch'}
        if opaque and not checkpoint_files:
            raise ValueError('This script/menu/specification tool has opaque write targets. Use suite_invoke with explicit checkpoint_files before dispatch.')
        notices = []
        # The document list contains project members; context also catches a
        # focused free SchLib/PcbLib absent from that list.
        context = await self.native_read('app_context', {})
        if not isinstance(context, dict) or not context.get('bridge_answering'):
            raise RuntimeError('Pre-edit checkpoint cannot verify Altium context; edit was not dispatched')
        if context.get('script_version_match') is False:
            raise RuntimeError('Pre-edit checkpoint found stale native scripts; reconnect before editing')
        collect(context.get('active_document', {}))
        documents = await self.native_read('app_list_documents', {})
        collect(documents)
        try:
            collect(await self.native_read('proj_get_focused', {}))
        except (ValueError, RuntimeError):
            if name.startswith('proj_'):
                raise RuntimeError('Cannot resolve the focused project for pre-edit backup; edit was not dispatched')
            notices.append('No focused project definition was available to snapshot.')
        if context.get('unsaved') or context.get('unsaved_detection_complete') is not True:
            notices.append('Only saved disk bytes are captured; unsaved editor buffers are not included. No automatic save is performed.')
        connectivity, status, connectivity_target = None, 'unavailable', None
        active = context.get('active_document') or {}
        board = str(active.get('file_path', ''))
        explicit_board = arguments.get('board_path')
        if board.lower().endswith('.pcbdoc') and (not explicit_board or Path(explicit_board).resolve() == Path(board).resolve()):
            try:
                connectivity = await self.native_read('pcb_get_pad_connectivity',
                                                      {'board_path': active['file_path'], 'limit': 0})
                status = 'live_physical_pad_net_assignments_not_copper_drc'
                connectivity_target = board
            except (ValueError, RuntimeError):
                notices.append('Pre-edit PCB connectivity could not be read.')
        else:
            # Net compilation can save files. Preserve a read-only preflight
            # and explicitly record that fresh topology was not captured.
            notices.append('Fresh schematic connectivity not captured: recompilation may persist documents.')
        if not files and name not in {'app_create_document', 'create_project'}:
            raise RuntimeError('No saved target could be resolved for a pre-edit checkpoint; edit was not dispatched')
        return await asyncio.to_thread(self.store.create, sorted(files), tool=name,
                                      connectivity=connectivity, connectivity_status=status,
                                      connectivity_target=connectivity_target,
                                      scope='explicit targets + active document + visible project members', warnings=notices)
