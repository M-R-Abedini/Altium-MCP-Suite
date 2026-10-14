"""Immutable physical PCB snapshots for concurrent, native-independent analysis.

There is deliberately no automatic live cache: unsaved GUI edits do not reliably
advance Altium's Modified flag. Every capture reads native state; reuse is an
explicit historical read identified by a content hash. No write accepts this
hash as proof that the editor still matches the snapshot.
"""
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

SECTIONS = ('counts', 'components', 'pads', 'nets')
CHECKS = ('inventory', 'pad_assignments', 'bom')


def _root():
    from suite_config import runtime_dir
    return runtime_dir() / 'read-snapshots'


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')


def validate(data):
    if (not isinstance(data, dict) or data.get('data_domain') != 'physical_pcb'
            or data.get('truncated') is not False or not data.get('board_path')):
        raise ValueError('A complete native physical PCB read is required')
    counts = data.get('counts')
    if not isinstance(counts, dict):
        raise ValueError('Missing physical counts')
    for name in ('components', 'pads', 'nets'):
        if type(counts.get(name)) is not int or counts[name] < 0:
            raise ValueError('Invalid physical counts')
        if not isinstance(data.get(name), list) or len(data[name]) != counts[name]:
            raise ValueError('Incomplete ' + name + ' readback')
    for item in data['components']:
        if not isinstance(item, dict) or any(not isinstance(item.get(k), str) for k in ('designator', 'comment', 'footprint')):
            raise ValueError('Invalid component readback')
    for item in data['pads']:
        if not isinstance(item, dict) or any(not isinstance(item.get(k), str) for k in ('component', 'pad', 'net')):
            raise ValueError('Invalid pad readback')
    if any(not isinstance(net, str) for net in data['nets']):
        raise ValueError('Invalid net readback')


def capture(data, started, editor, health, root=None):
    validate(data)
    envelope = dict(schema=1, captured_at=time.time(), capture_started_at=started,
                    editor=editor, script_version=health.get('script_version'),
                    consistency='single_native_handler', live=False,
                    suitable_as_write_precondition=False, data=data)
    content = _encoded(envelope)
    if len(content) > 64 * 1024 * 1024:
        raise ValueError('Snapshot exceeds 64 MiB')
    identity = hashlib.sha256(content).hexdigest()
    root = Path(root) if root is not None else _root()
    root.mkdir(parents=True, exist_ok=True)
    target = root / (identity + '.json')
    temporary = root / (identity + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(content)
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.read_bytes() != content:
                raise RuntimeError('Snapshot integrity conflict')
    finally:
        temporary.unlink(missing_ok=True)
    return dict(ok=True, snapshot_id=identity, board_path=data['board_path'],
                captured_at=envelope['captured_at'], counts=data['counts'],
                live=False, suitable_as_write_precondition=False,
                consistency=envelope['consistency'])


def read(identity, root=None):
    if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{64}', identity):
        raise ValueError('snapshot_id must be a SHA-256 identifier')
    root = Path(root) if root is not None else _root()
    path = root / (identity + '.json')
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('Snapshot exceeds 64 MiB')
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != identity:
        raise ValueError('Snapshot checksum mismatch')
    envelope = json.loads(content)
    if envelope.get('schema') != 1:
        raise ValueError('Unsupported snapshot schema')
    validate(envelope.get('data'))
    return envelope


def select_sections(envelope, sections):
    if not sections or len(sections) != len(set(sections)) or any(s not in SECTIONS for s in sections):
        raise ValueError('sections must be unique names from: ' + ', '.join(SECTIONS))
    return dict(ok=True, captured_at=envelope['captured_at'], live=False,
                board_path=envelope['data']['board_path'],
                suitable_as_write_precondition=False,
                sections={s: envelope['data'][s] for s in sections})


def analyze(envelope, check):
    data = envelope['data']
    if check == 'inventory':
        references = Counter(c['designator'] for c in data['components'])
        return dict(counts=data['counts'], blank_designators=references.get('', 0),
                    duplicate_designators=sorted(k for k, n in references.items() if k and n > 1),
                    empty_footprints=sorted(c['designator'] for c in data['components'] if not c['footprint']))
    if check == 'pad_assignments':
        by_net = defaultdict(int)
        for pad in data['pads']:
            if pad['net']:
                by_net[pad['net']] += 1
        return dict(netless_pads=sum(not p['net'] for p in data['pads']),
                    free_pads=sum(not p['component'] for p in data['pads']),
                    unnamed_pads=sum(not p['pad'] for p in data['pads']),
                    single_pad_nets=sorted(k for k, n in by_net.items() if n == 1),
                    nets_without_pads=sorted(set(data['nets']) - set(by_net)),
                    verifies_copper=False,
                    interpretation='Pad assignments only; netless/single-pad entries are observations, not DRC failures.')
    if check == 'bom':
        groups = defaultdict(list)
        for component in data['components']:
            groups[(component['comment'], component['footprint'])].append(component['designator'])
        return dict(source='physical_pcb_inventory', variant_fitted_state_known=False,
                    groups=[dict(comment=k[0], footprint=k[1], quantity=len(v), designators=sorted(v))
                            for k, v in sorted(groups.items())])
    raise ValueError('Unsupported analysis: ' + str(check))
