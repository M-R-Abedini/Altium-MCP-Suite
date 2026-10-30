"""Independent Altium PCB-library container and STEP-model codec.

SPDX-License-Identifier: MIT
Provenance: Microsoft MS-CFB, Altium public API units, and isolated synthetic
MCP/OLE differential observations. No upstream Rust implementation was read.
"""
from __future__ import annotations

import base64
import copy
from datetime import datetime
from functools import lru_cache
import hashlib
import json
import re
from pathlib import Path
import struct
import uuid
import zlib

from suite_ole import read_streams, write_streams

U32 = struct.Struct('<I')
MAX_STREAM = 256 * 1024 * 1024


class Component(dict):
    """Public fields plus private lossless stream observations."""
    pass


class Library(dict):
    pass


def block(data: bytes) -> bytes:
    return U32.pack(len(data)) + data


def _take(data: bytes, offset=0):
    if offset + 4 > len(data):
        raise ValueError('Truncated PCB stream block length')
    length = U32.unpack_from(data, offset)[0]
    if length > MAX_STREAM or offset + 4 + length > len(data):
        raise ValueError('Invalid or oversized PCB stream block')
    return data[offset + 4:offset + 4 + length], offset + 4 + length


def _text(data):
    data = data.rstrip(b'\x00')
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('cp1252', errors='replace')


def _pairs(data):
    return [tuple(_text(part) for part in item.split(b'=', 1))
            for item in data.rstrip(b'\x00').split(b'|') if b'=' in item]


def _params(data):
    return {key.upper(): value for key, value in _pairs(data)}


def _param_bytes(pairs):
    return ('|' + '|'.join(str(k) + '=' + str(v) for k, v in pairs) + '\x00').encode('cp1252', errors='replace')


def _pstring(text):
    encoded = str(text).encode('cp1252', errors='replace')
    if len(encoded) > 255:
        raise ValueError('PCB library name exceeds the binary string limit')
    return bytes([len(encoded)]) + encoded


def _new_board_header(path):
    """Instantiate functional settings from our own Altium-created blank file.

    A four-field envelope is readable by other file parsers but is insufficient
    for Altium's native board loader. The sanitized application-output data is
    documented independently from the formerly distributed worker.
    """
    template = json.loads((Path(__file__).parent / 'contracts' / 'pcblib-default-board.json').read_text(encoding='utf-8'))
    identifiers = ['{' + str(uuid.uuid4()).upper() + '}' for _ in range(template['guid_count'])]
    now = datetime.now()
    substitutions = {'__SUITE_FILEPATH__': str(path), '__SUITE_DATE__': now.strftime('%Y-%m-%d'),
                     '__SUITE_TIME__': now.strftime('%H:%M:%S')}
    substitutions.update({f'__SUITE_GUID_{index}__': identifier for index, identifier in enumerate(identifiers)})
    pattern = re.compile('|'.join(re.escape(key) for key in substitutions))
    return _param_bytes([(key, pattern.sub(lambda match: substitutions[match[0]], value))
                         for key, value in template['parameters']])


def _section_mapping(streams):
    raw = streams.get('SectionKeys', b'')
    if not raw:
        return {}
    try:
        payload, _ = _take(raw)
    except ValueError:
        payload = raw
    params = _params(payload)
    output = {}
    for index in range(int(params.get('KEYCOUNT', '0'))):
        original, section = params.get(f'LIBREF{index}'), params.get(f'SECTIONKEY{index}')
        if original and section:
            output[original] = section
    return output


def _names(streams):
    data = streams.get('Library/Data', b'')
    if not data:
        raise ValueError('Missing PCB Library/Data stream')
    _, offset = _take(data)
    if offset + 4 > len(data):
        raise ValueError('PCB library component table is truncated')
    count = U32.unpack_from(data, offset)[0]; offset += 4
    if count > 100000:
        raise ValueError('PCB component count exceeds safety limit')
    names = []
    for _ in range(count):
        item, offset = _take(data, offset)
        if not item or item[0] > len(item) - 1:
            raise ValueError('Malformed PCB component name')
        names.append(_text(item[1:1 + item[0]]))
    return names


def _unicode_parameter(params, key, default=''):
    encoded = params.get('UNICODE__' + key)
    if encoded:
        units = [int(value) for value in encoded.split(',') if value]
        return struct.pack('<' + 'H' * len(units), *units).decode('utf-16-le')
    return params.get(key, default)


def _unicode_codes(value):
    raw = value.encode('utf-16-le')
    return ','.join(str(unit) for unit in struct.unpack('<' + 'H' * (len(raw)//2), raw))


@lru_cache(maxsize=1)
def _ansi_best_fit_table():
    contract = Path(__file__).parent / 'contracts' / 'windows-1252-best-fit.json'
    return {int(unit): bytes(encoded) for unit, encoded in
            json.loads(contract.read_text(encoding='utf-8'))['mapping'].items()}


def _ansi_fallback(value):
    # Altium resolves Unicode storage names through the Windows ANSI best-fit
    # spelling. Python's replacement encoder differs (Omega -> O, and a
    # supplementary character -> two question marks, one per UTF-16 unit).
    raw = value.encode('utf-16-le')
    table = _ansi_best_fit_table()
    return b''.join(table.get(unit, chr(unit).encode('cp1252', errors='replace'))
                    for unit in struct.unpack('<' + 'H' * (len(raw) // 2), raw)).decode('cp1252', errors='replace')


def _component_entries(streams):
    """Resolve native ANSI table entries to Unicode component storages.

    Native Altium writes Unicode storage names, ANSI short-string fallbacks,
    and UNICODE__PATTERN UTF-16 values. Consequently a table name alone need
    not identify the storage. Parameters and the component name record supply
    the link, while SectionKeys handles long storage aliases.
    """
    from suite_pcb_primitives import decode_component_stream
    sections = [key[:-5] for key in streams if key.count('/') == 1 and key.endswith('/Data')
                and key[:-5] + '/Parameters' in streams]
    information = {}
    for section in sections:
        payload, _ = _take(streams[section + '/Parameters'])
        params = _params(payload)
        name_payload, _ = _take(streams[section + '/Data'])
        data_name = _text(name_payload[1:1 + name_payload[0]]) if name_payload else section
        information[section] = (params, data_name)
    mapping = _section_mapping(streams)
    ordered = []
    for name in _names(streams):
        direct = _component_section(name, mapping, streams)
        selected = direct if direct in information and direct not in ordered else next(
            (section for section, (params, data_name) in information.items()
             if section not in ordered and (data_name == name or params.get('PATTERN') == name
                                           or _unicode_parameter(params, 'PATTERN', data_name) == name)), None)
        if selected is None:
            raise ValueError(f'PCB component storage is missing: {name}')
        ordered.append(selected)
    ordered.extend(section for section in sections if section not in ordered)
    return [(section, *information[section]) for section in ordered]


def _component_section(name, mapping, streams):
    section = mapping.get(name, name)
    if section + '/Data' not in streams:
        # Earlier producers sometimes used an ANSI directory API on UTF-8.
        ansi_alias = section.encode('utf-8').decode('cp1252', errors='replace')
        if ansi_alias + '/Data' in streams:
            return ansi_alias
    return section


def read_embedded_models(path):
    streams = read_streams(path)
    models, offset = [], 0
    payload = streams.get('Library/Models/Data', b'')
    while offset < len(payload):
        raw, offset = _take(payload, offset)
        metadata = _params(raw)
        index = len(models)
        compressed = streams.get(f'Library/Models/{index}')
        data = None
        if compressed is not None:
            decompressor = zlib.decompressobj()
            data = decompressor.decompress(compressed, MAX_STREAM + 1)
            if len(data) > MAX_STREAM or decompressor.unconsumed_tail or not decompressor.eof:
                raise ValueError('Invalid or oversized embedded STEP stream')
        models.append({'id': metadata.get('ID', ''), 'name': metadata.get('NAME', ''),
                       'index': index, 'embedded': metadata.get('EMBED', '').upper() == 'TRUE',
                       'data': data, 'metadata': _pairs(raw)})
    return models


def read_library(path, **options):
    from suite_pcb_primitives import decode_component_stream
    path = Path(path)
    streams = read_streams(path)
    mapping = _section_mapping(streams)
    footprints = []
    models = read_embedded_models(path)
    by_id = {model['id'].casefold(): model for model in models}
    for section, params, data_name in _component_entries(streams):
        key = section + '/Data'
        if key not in streams:
            raise ValueError(f'PCB component storage is missing: {section}')
        component_streams = {key[len(section) + 1:]: data for key, data in streams.items()
                             if key.startswith(section + '/')}
        decoded = decode_component_stream(component_streams['Data'],
                                          component_streams.get('WideStrings', b''), component_streams)
        component = Component(decoded)
        component['name'] = _unicode_parameter(params, 'PATTERN', data_name)
        component['description'] = _unicode_parameter(params, 'DESCRIPTION', '')
        for body in component.get('component_bodies', []):
            model = by_id.get(str(body.get('model_id', '')).casefold())
            if model or body.get('model_name'):
                component['model_3d'] = {'filepath': (model or {}).get('name') or body.get('model_name', ''),
                                         'rotation': body.get('rotation_z', 0.0), 'x_offset': body.get('model_2d_x', 0.0),
                                         'y_offset': body.get('model_2d_y', 0.0), 'z_offset': body.get('z_offset', 0.0)}
                break
        component.raw_streams = component_streams
        component.original = copy.deepcopy(dict(component))
        footprints.append(component)
    result = Library(status='success', filepath=str(path), units='mm', compact=False,
                     footprints=footprints, offset=0, returned_count=len(footprints),
                     total_count=len(footprints), has_more=False)
    result.raw_streams = streams
    result.embedded_models = models
    return result


def _description_stream(name, description, old=None):
    pairs = _pairs(_take(old)[0]) if old else [('PATTERN', name), ('HEIGHT', '0mil'),
                                            ('DESCRIPTION', description), ('ITEMGUID', ''), ('REVISIONGUID', '')]
    values = {'PATTERN': _ansi_fallback(name), 'DESCRIPTION': _ansi_fallback(description)}
    seen = set()
    output = []
    for key, value in pairs:
        if key.upper() in ('UNICODE', 'UNICODE__PATTERN', 'UNICODE__DESCRIPTION'):
            continue
        output.append((key, values.get(key.upper(), value))); seen.add(key.upper())
    output.extend((key, value) for key, value in values.items() if key not in seen)
    unicode_fields = [(key, value) for key, value in [('PATTERN', name), ('DESCRIPTION', description)]
                      if any(ord(char) > 127 for char in value)]
    if unicode_fields or any(key.upper().startswith('UNICODE__') for key, _ in output):
        output.insert(0, ('UNICODE', 'EXISTS'))
        output.extend(('UNICODE__' + key, _unicode_codes(value)) for key, value in unicode_fields)
        output.append(('UNICODE', 'EXISTS'))
    return block(_param_bytes(output))


def _default_outline(fp):
    pads = fp.get('pads', [])
    if pads:
        x1 = min(p['x'] - p['width'] / 2 for p in pads); x2 = max(p['x'] + p['width'] / 2 for p in pads)
        y1 = min(p['y'] - p['height'] / 2 for p in pads); y2 = max(p['y'] + p['height'] / 2 for p in pads)
    else:
        x1 = y1 = -1.0; x2 = y2 = 1.0
    return [{'x': x1, 'y': y2}, {'x': x1, 'y': y1}, {'x': x2, 'y': y1}, {'x': x2, 'y': y2}]


def _author_models(path, footprints, models):
    for fp in footprints:
        model = fp.get('step_model') or fp.get('model_3d')
        if not model or not model.get('filepath'):
            continue
        # Echoed read results already link an existing body/model. Do not add one.
        if fp.get('component_bodies') and any(body.get('model_id') or body.get('model_name') for body in fp['component_bodies']):
            continue
        candidate = Path(model['filepath'])
        if not candidate.is_absolute():
            candidate = path.parent / candidate
        embedded = candidate.is_file() and model.get('embed', True)
        if fp.get('step_model') and model.get('embed', True) and not candidate.is_file():
            raise ValueError(f"STEP file not found for footprint {fp['name']!r}: {model['filepath']!r}; use embed:false for an external reference")
        model_id = '{' + str(uuid.uuid4()).upper() + '}' if embedded else ''
        model_name = candidate.name if embedded else str(model['filepath'])
        if embedded:
            content = candidate.read_bytes()
            if len(content) > MAX_STREAM:
                raise ValueError('Embedded model exceeds size limit')
            models.append({'id': model_id, 'name': model_name, 'data': content, 'embedded': True})
        fp.setdefault('component_bodies', []).append({
            'overall_height': 0.0, 'outline': _default_outline(fp), 'model_id': model_id,
            'model_name': model_name, 'embedded': embedded,
            'rotation_x': 0.0, 'rotation_y': 0.0, 'rotation_z': model.get('rotation', 0),
            'model_2d_x': 0.0, 'model_2d_y': 0.0, 'z_offset': model.get('z_offset', 0),
        })


def write_library(path, components, **options):
    """Write typed components with original non-target streams retained."""
    from suite_pcb_primitives import encode_component_stream, encode_wide_strings, encode_identity_streams
    path = Path(path)
    components = copy.deepcopy(list(components))
    streams = read_streams(path) if path.exists() else {}
    old_names = _names(streams) if streams.get('Library/Data') else []
    old_mapping = _section_mapping(streams)
    old_sections = {section for section, _, _ in _component_entries(streams)} if old_names else set()
    original_models = read_embedded_models(path) if path.exists() else []
    models = [] if options.get('replace_embedded_models') else list(original_models)
    for item in options.get('embedded_models', []) or []:
        value = dict(item)
        if isinstance(value.get('data'), str):
            value['data'] = base64.b64decode(value['data'], validate=True)
        if not any(model['id'].casefold() == value['id'].casefold() for model in models):
            models.append(value)
    _author_models(path, components, models)
    for key in list(streams):
        if key.split('/')[0] in old_sections:
            del streams[key]
    names = [str(fp['name']) for fp in components]
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError('Duplicate footprint names are not allowed')
    header = None
    if 'Library/Data' in streams:
        header, _ = _take(streams['Library/Data'])
    if header is None:
        header = _new_board_header(path)
    streams['Library/Data'] = block(header) + U32.pack(len(names)) + b''.join(block(_pstring(_ansi_fallback(name))) for name in names)
    # This stream contains one board record, independent of footprint count.
    streams['Library/Header'] = U32.pack(1)
    streams.setdefault('FileHeader', U32.pack(27) + _pstring('PCB 6.0 Binary Library File')
                       + struct.pack('<d', 5.01) + U32.pack(8) + _pstring('SUITEPCB'))
    streams.setdefault('Library/EmbeddedFonts', U32.pack(0))
    streams.setdefault('Library/LayerKindMapping/Data', block('1.0\x00'.encode('utf-16-le')) + U32.pack(0) + U32.pack(0))
    streams.setdefault('Library/LayerKindMapping/Header', U32.pack(1))
    streams.setdefault('Library/PadViaLibrary/Data', block(_param_bytes([
        ('PADVIALIBRARY.LIBRARYID', '{' + str(uuid.uuid4()).upper() + '}'),
        ('PADVIALIBRARY.LIBRARYNAME', '<Local>'), ('PADVIALIBRARY.DISPLAYUNITS', '1')])))
    streams.setdefault('Library/PadViaLibrary/Header', U32.pack(0))
    streams.setdefault('Library/Textures/Data', b'')
    streams.setdefault('Library/Textures/Header', U32.pack(0))
    toc = []
    section_pairs = []
    global_storages = {key.split('/')[0].casefold() for key in streams}
    reserved_names = global_storages | {name.casefold() for name in names}
    assigned_sections = set()
    for index, fp in enumerate(components):
        name = str(fp['name'])
        section = name if len(name.encode('utf-16-le')) <= 62 and not any(c in name for c in '/\\:!') else None
        if section is None or section.casefold() in global_storages or section.casefold() in assigned_sections:
            alias_index = index
            section = f'FP_{alias_index:06d}'
            while section.casefold() in reserved_names or section.casefold() in assigned_sections:
                alias_index += 1
                section = f'FP_{alias_index:06d}'
        assigned_sections.add(section.casefold())
        if section != name:
            section_pairs.extend([(f'LIBREF{len(section_pairs)//2}', name), (f'SECTIONKEY{len(section_pairs)//2}', section)])
        original = getattr(fp, 'raw_streams', {})
        for key, data in original.items():
            streams[section + '/' + key] = data
        encoded_fp = dict(fp, name=_ansi_fallback(name))
        encoded = encode_component_stream(encoded_fp)
        if isinstance(encoded, tuple):
            encoded, extra = encoded
            streams.update({section + '/' + key: data for key, data in extra.items()})
        # Component identity short strings use the same ANSI spelling as the
        # library table; primitive text/designator encodings remain untouched.
        _, identity_end = _take(encoded)
        streams[section + '/Data'] = block(_pstring(_ansi_fallback(name))) + encoded[identity_end:]
        streams.update({section + '/' + key: data for key, data in encode_identity_streams(fp, original).items()})
        streams[section + '/Header'] = U32.pack(len(fp.get('primitive_order', [])) or sum(
            len(fp.get(key, [])) for key in ('pads', 'tracks', 'arcs', 'fills', 'vias', 'text', 'regions', 'component_bodies')))
        streams[section + '/Parameters'] = _description_stream(name, fp.get('description', ''), original.get('Parameters'))
        # Unicode text lives in the per-component UTF-16 code-unit table. The
        # legacy short-string text record is merely an ANSI fallback.
        if fp.get('text') != getattr(fp, 'original', {}).get('text') or not original.get('WideStrings'):
            streams[section + '/WideStrings'] = encode_wide_strings(fp)
        toc.append(_ansi_fallback(f"Name={name}|Pad Count={len(fp.get('pads', []))}|Height=0|Description={fp.get('description', '')}\r\n").encode('cp1252'))
    if section_pairs:
        streams['SectionKeys'] = block(('|' + '|'.join(str(k) + '=' + str(v) for k, v in
            [('KEYCOUNT', len(section_pairs)//2), *section_pairs]) + '\x00').encode('utf-8'))
    else:
        streams.pop('SectionKeys', None)
    streams['Library/ComponentParamsTOC/Data'] = block(b''.join(toc) + b'\0')
    streams['Library/ComponentParamsTOC/Header'] = U32.pack(1)
    models_payload = []
    for key in list(streams):
        if key.startswith('Library/Models/') and key.split('/')[-1].isdigit():
            del streams[key]
    for index, model in enumerate(models):
        metadata = model.get('metadata') or [('EMBED', 'TRUE' if model.get('data') is not None else 'FALSE'),
                    ('MODELSOURCE', 'Undefined'), ('ID', model['id']), ('ROTX', '0.000'), ('ROTY', '0.000'),
                    ('ROTZ', '0.000'), ('DZ', '0'), ('CHECKSUM', '0'), ('NAME', model['name'])]
        models_payload.append(block(_param_bytes(metadata).lstrip(b'|')))
        if model.get('data') is not None:
            streams[f'Library/Models/{index}'] = zlib.compress(model['data'])
    streams['Library/Models/Data'] = b''.join(models_payload)
    streams['Library/Models/Header'] = U32.pack(len(models))
    streams.setdefault('Library/ModelsNoEmbed/Data', b'')
    streams.setdefault('Library/ModelsNoEmbed/Header', U32.pack(0))
    write_streams(path, streams)
    return read_library(path)
