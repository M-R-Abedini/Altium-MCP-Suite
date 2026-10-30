"""Independent MIT implementation of the Altium schematic-library container.

Format facts are documented in docs/clean-room/SCHLIB_FORMAT.md. No Altium
application automation or third-party library codec is used here.
"""
from __future__ import annotations

import base64
import copy
import math
import secrets
import string
import struct
import zlib
from pathlib import Path

from suite_ole import read_streams, write_streams


class SchLibFormatError(ValueError):
    pass


def _uid():
    return ''.join(secrets.choice(string.ascii_uppercase) for _ in range(8))


def _records(data):
    pos = 0
    while pos < len(data):
        if len(data) - pos < 4:
            if data[pos:].strip(b'\x00'):
                raise SchLibFormatError('Truncated schematic record length')
            break
        size = struct.unpack_from('<I', data, pos)[0]
        length, kind = size & 0xffffff, size >> 24
        if length == 0 and not data[pos:].strip(b'\x00'):
            break
        end = pos + 4 + length
        if end > len(data):
            raise SchLibFormatError('Schematic record extends beyond its stream')
        yield kind, data[pos + 4:end]
        pos = end


def _block(data, kind=0):
    if len(data) > 0xffffff:
        raise SchLibFormatError('Schematic record exceeds 24-bit size')
    return struct.pack('<I', len(data) | kind << 24) + data


def _decode_text(data):
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('cp1252', errors='replace')


def _params(data):
    # Legacy libraries use Windows ANSI; modern UTF8 fields carry their own key.
    # The five undefined CP1252 bytes must remain reversible: they occur inside
    # UTF-8 sequences for ordinary Persian characters, for example U+0641.
    text = ''.join(chr(byte) if byte in (0x81, 0x8d, 0x8f, 0x90, 0x9d)
                   else bytes([byte]).decode('cp1252') for byte in data.rstrip(b'\x00'))
    return [segment.split('=', 1) for segment in text.split('|') if '=' in segment]


def _ansi_encode(text):
    return b''.join(bytes([ord(char)]) if ord(char) in (0x81, 0x8d, 0x8f, 0x90, 0x9d)
                    else char.encode('cp1252') for char in text)


def _values(params):
    result = {k.upper(): v for k,v in params}
    for key,value in list(result.items()):
        if key.startswith('%UTF8%'):
            try: value = _ansi_encode(value).decode('utf-8')
            except (UnicodeEncodeError, UnicodeDecodeError): pass
            result[key] = value
            result[key[6:]] = value
    return result


def _text_record(params):
    parts = []
    for key, value in params:
        value = str(value)
        if '|' in key or '|' in value:
            raise ValueError('Schematic text cannot contain the | record separator; use Â¦')
        parts.append('|' + key + '=' + value)
    has_utf8 = any(k.upper().startswith('%UTF8%') for k,v in params)
    payload = b''
    for part in parts:
        try:
            ansi = _ansi_encode(part)
            if has_utf8: ansi.decode('utf-8')
            payload += ansi
        except (UnicodeEncodeError, UnicodeDecodeError):
            payload += part.encode('utf-8')
    return _block(payload + b'\x00')


def _num(d, key, default=0):
    try:
        return float(d.get(key.upper(), default))
    except (TypeError, ValueError):
        return float(default)


def _coord(d, key):
    return _num(d, key) + _num(d, key + '_Frac') / 100000


def _bool(d, key, default=False):
    return d.get(key.upper(), 'T' if default else 'F').upper() in ('T', 'TRUE', '1')


def _format(value):
    if isinstance(value, bool):
        return 'T' if value else 'F'
    if isinstance(value, float):
        return format(value, '.12g')
    return str(value)


# field -> (wire key, scalar type, default). Geometry is split into integer and
# signed fractional parts, so negative off-grid coordinates are unambiguous.
_COMMON = {
    'owner_part_id': ('OwnerPartId', int, 1),
    'owner_part_display_mode': ('OwnerPartDisplayMode', int, 0),
    'graphically_locked': ('GraphicallyLocked', bool, False),
    'disabled': ('Disabled', bool, False), 'dimmed': ('Dimmed', bool, False),
}
_POS = {'x': ('Location.X', float, 0), 'y': ('Location.Y', float, 0)}
_BOX = {'x1': ('Location.X', float, 0), 'y1': ('Location.Y', float, 0),
        'x2': ('Corner.X', float, 0), 'y2': ('Corner.Y', float, 0)}
_LINE = {'line_width': ('LineWidth', int, 1)}
_UID = {'unique_id': ('UniqueID', str, '')}
_ACCESS = {'is_not_accessible': ('IsNotAccesible', bool, True)}
_FILL = {'fill_color': ('AreaColor', int, 11599871), 'filled': ('IsSolid', bool, True),
         'transparent': ('Transparent', bool, False)}
_COLOR = {'color': ('Color', int, 128)}
_BORDER = {'line_color': ('Color', int, 128)}
_ANGLE = {'start_angle': ('StartAngle', float, 0), 'end_angle': ('EndAngle', float, 360)}
_RADIUS = {'radius': ('Radius', float, 0)}
_STYLE = {'line_style': ('LineStyle', int, 0)}


def _fields(*groups):
    out = {}
    for g in groups:
        out.update(g)
    return out


SPECS = {
    'rectangle': (14, 'rectangles', _fields(_COMMON, _BOX, _LINE, _BORDER, _FILL, {'line_style': ('LineStyleExt', int, 0)}, _UID)),
    'line': (13, 'lines', _fields(_COMMON, _ACCESS, _BOX, _LINE, _STYLE, _COLOR, _UID)),
    'polyline': (6, 'polylines', _fields(_COMMON, _ACCESS, _LINE, _STYLE, _COLOR, {'start_line_shape': ('StartLineShape', int, 0), 'end_line_shape': ('EndLineShape', int, 0), 'line_shape_size': ('LineShapeSize', int, 0), 'transparent': ('Transparent', bool, False)}, _UID)),
    'polygon': (7, 'polygons', _fields(_COMMON, _ACCESS, _LINE, _BORDER, _FILL, _STYLE, _UID)),
    'arc': (12, 'arcs', _fields(_COMMON, _ACCESS, _POS, _RADIUS, _LINE, _ANGLE, _COLOR, {'fill_color': ('AreaColor', int, 0)}, _UID)),
    'pie': (9, 'pies', _fields(_COMMON, _ACCESS, _POS, _RADIUS, _LINE, _ANGLE, {'line_color': ('Color', int, 0), 'fill_color': ('AreaColor', int, 0), 'filled': ('IsSolid', bool, True), 'transparent': ('Transparent', bool, False)}, _UID)),
    'image': (30, 'images', _fields(_COMMON, _ACCESS, _BOX, _LINE, _STYLE, {'line_color': ('Color', int, 0), 'fill_color': ('AreaColor', int, 0), 'filled': ('IsSolid', bool, False), 'transparent': ('Transparent', bool, False), 'show_border': ('ShowBorder', bool, False), 'keep_aspect': ('KeepAspect', bool, False), 'embed_image': ('EmbedImage', bool, False), 'file_name': ('FileName', str, '')}, _UID)),
    'text_frame': (28, 'text_frames', _fields(_COMMON, _ACCESS, _BOX, {'line_width': ('LineWidth', int, 0), 'color': ('Color', int, 0)}, _STYLE, {'area_color': ('AreaColor', int, 16777215), 'text_color': ('TextColor', int, 0), 'font_id': ('FontID', int, 1), 'is_solid': ('IsSolid', bool, False), 'orientation': ('Orientation', int, 0), 'show_border': ('ShowBorder', bool, True), 'alignment': ('Alignment', int, 1), 'word_wrap': ('WordWrap', bool, True), 'clip_to_rect': ('ClipToRect', bool, True), 'text': ('Text', str, ''), 'text_margin': ('TextMargin', float, .00005), 'transparent': ('Transparent', bool, False)}, _UID)),
    'bezier': (5, 'beziers', _fields(_COMMON, _ACCESS, _LINE, _COLOR, {f'{a}{n}': (f'{a.upper()}{n}', float, 0) for n in range(1, 5) for a in ('x', 'y')}, _UID)),
    'ellipse': (8, 'ellipses', _fields(_COMMON, _ACCESS, _POS, {'radius_x': ('Radius', float, 0), 'radius_y': ('SecondaryRadius', float, 0)}, _LINE, _BORDER, _FILL, _UID)),
    'round_rect': (10, 'round_rects', _fields(_COMMON, _BOX, {'corner_x_radius': ('CornerXRadius', float, 0), 'corner_y_radius': ('CornerYRadius', float, 0)}, _LINE, _STYLE, _BORDER, _FILL, _UID)),
    'elliptical_arc': (11, 'elliptical_arcs', _fields(_COMMON, _POS, _RADIUS, {'secondary_radius': ('SecondaryRadius', float, 0)}, _LINE, _ANGLE, _COLOR, {'fill_color': ('AreaColor', int, 0)}, _UID)),
    'label': (4, 'labels', _fields(_COMMON, _POS, _COLOR, {'font_id': ('FontID', int, 1), 'text': ('Text', str, ''), 'is_hidden': ('IsHidden', bool, False), 'is_mirrored': ('IsMirrored', bool, False)}, _UID)),
    'ieee_symbol': (3, 'ieee_symbols', _fields(_COMMON, _POS, {'symbol': ('Symbol', int, 1), 'scale_factor': ('ScaleFactor', float, 10), 'line_width': ('LineWidth', int, 1), 'is_mirrored': ('Mirror', bool, False), 'color': ('Color', int, 0)})),
    'parameter': (41, 'parameters', _fields(_COMMON, _POS, {'orientation': ('Orientation', int, 0), 'justification': ('Justification', int, 0), 'color': ('Color', int, 8388608), 'font_id': ('FontID', int, 1), 'hidden': ('IsHidden', bool, False), 'value': ('Text', str, '*'), 'name': ('Name', str, ''), 'read_only_state': ('ReadOnlyState', int, 0), 'param_type': ('ParamType', int, 0), 'show_name': ('ShowName', bool, False), 'hide_name': ('HideName', bool, False), 'is_configurable': ('IsConfigurable', bool, False), 'auto_position': ('NotAutoPosition', bool, True), 'is_rule': ('IsRule', bool, False), 'is_system_parameter': ('IsSystemParameter', bool, False), 'text_horz_anchor': ('TextHorzAnchor', int, 0), 'text_vert_anchor': ('TextVertAnchor', int, 0), 'description': ('Description', str, '')}, _UID, {'is_mirrored': ('IsMirrored', bool, False)})),
}
BY_ID = {v[0]: k for k, v in SPECS.items()}
JUSTIFICATIONS = ['bottom_left', 'bottom_center', 'bottom_right', 'middle_left', 'middle_center', 'middle_right', 'top_left', 'top_center', 'top_right']
# These fields are always present in the observed read contract; other defaults
# are omitted until a stored value differs from the default.
ALWAYS = {
    'owner_part_id', 'line_width', 'line_style', 'color', 'line_color', 'fill_color', 'filled',
    'transparent', 'font_id', 'text', 'value', 'name', 'hidden', 'is_hidden',
    'is_mirrored', 'read_only_state', 'param_type', 'start_angle', 'end_angle',
    'show_border', 'keep_aspect', 'embed_image', 'area_color', 'text_color',
    'is_solid', 'orientation', 'alignment', 'word_wrap', 'clip_to_rect',
    'text_margin', 'symbol', 'scale_factor', 'start_line_shape', 'end_line_shape', 'line_shape_size',
}


def _decode_shape(kind, params):
    d = _values(params)
    fields = SPECS[kind][2]
    out = {}
    for field, (wire, ty, default) in fields.items():
        if ty is float:
            value = (_coord(d, wire) if wire.upper() in d or (wire + '_Frac').upper() in d else float(default)) if wire not in ('StartAngle', 'EndAngle') else _num(d, wire, default)
        elif ty is bool:
            value = _bool(d, wire, False)
            if field == 'auto_position': value = not value
        elif ty is int:
            value = int(_num(d, wire, default))
        else:
            value = d.get(('%UTF8%' + wire).upper(), d.get(wire.upper(), default))
        # Access flags are present for selected primitives only, even when false.
        if field == 'is_not_accessible' or (field in ALWAYS and not (kind == 'parameter' and field in ('orientation', 'is_mirrored'))) or ty is float or value != default or (ty is str and value):
            out[field] = value
    if kind in ('polyline', 'polygon'):
        out['points'] = [{'x': _coord(d, f'X{i}'), 'y': _coord(d, f'Y{i}')} for i in range(1, int(_num(d, 'LocationCount')) + 1)]
    if kind in ('label', 'ieee_symbol'):
        out['rotation'] = _num(d, 'Orientation') * 90
    if kind == 'label':
        out['justification'] = JUSTIFICATIONS[min(8, max(0, int(_num(d, 'Justification'))))]
    out['raw_params'] = copy.deepcopy(params)
    return out


def _wire_value(wire, ty, value, field):
    if field == 'auto_position': value = not value
    if ty is float and wire not in ('StartAngle', 'EndAngle'):
        whole = math.trunc(value)
        frac = round((value - whole) * 100000)
        return [(wire, str(whole))] * bool(whole) + [(wire + '_Frac', str(frac))] * bool(frac)
    if wire in ('StartAngle', 'EndAngle'):
        return [(wire, f'{value:.3f}')] if value else []
    if value in (0, False, ''): return []
    result = [(wire, _format(value))]
    if ty is str and any(ord(c) > 127 for c in str(value)):
        result.append(('%UTF8%' + wire, str(value)))
    return result


def _replace(params, keys, replacement):
    keys = {k.upper() for k in keys}
    indices = [i for i, (k, _) in enumerate(params) if k.upper() in keys]
    at = indices[0] if indices else len(params)
    params[:] = [p for p in params if p[0].upper() not in keys]
    params[at:at] = [[k, v] for k, v in replacement]


def _encode_shape(kind, item, index):
    record_id, _, fields = SPECS[kind]
    raw = item.get('raw_params')
    old = _decode_shape(kind, raw) if raw else {}
    if raw:
        params = copy.deepcopy(raw)
    else:
        params = [['RECORD', str(record_id)]]
        if kind != 'parameter': params.append(['IsNotAccesible', 'T'])
        if index and kind != 'rectangle': params.append(['IndexInSheet', str(index)])
    for field, (wire, ty, default) in fields.items():
        value = item.get(field, old.get(field, default))
        if field == 'unique_id' and not value: value = _uid()
        if raw and value == old.get(field, default): continue
        # Canonical records explicitly carry owner id, widths, nonzero colours,
        # and font ids. False/zero values are represented by an absent segment.
        replacement = _wire_value(wire, ty, value, field)
        if field == 'owner_part_id' and not replacement: replacement = [(wire, '0')]
        if field == 'unique_id' and not value: continue
        _replace(params, [wire, wire + '_Frac', '%UTF8%' + wire], replacement)
    if kind in ('polyline', 'polygon'):
        points = item.get('points', old.get('points', []))
        if not raw or points != old.get('points'):
            d = _values(params)
            keys = ['LocationCount'] + [f'{a}{i}{suffix}' for i in range(1, max(len(points), int(_num(d, 'LocationCount'))) + 1) for a in ('X', 'Y') for suffix in ('', '_Frac')]
            rep = [('LocationCount', str(len(points)))]
            for n, point in enumerate(points, 1):
                rep += _wire_value(f'X{n}', float, point['x'], 'x')
                rep += _wire_value(f'Y{n}', float, point['y'], 'y')
            _replace(params, keys, rep)
    if kind in ('label', 'ieee_symbol'):
        rotation = item.get('rotation', old.get('rotation', 0))
        if not raw or rotation != old.get('rotation', 0): _replace(params, ['Orientation'], [('Orientation', str(round(rotation / 90) % 4))] if rotation else [])
    if kind == 'label':
        justification = item.get('justification', old.get('justification', 'bottom_left'))
        if justification not in JUSTIFICATIONS: raise ValueError('Unknown label justification')
        if not raw or justification != old.get('justification'):
            n = JUSTIFICATIONS.index(justification)
            _replace(params, ['Justification'], [('Justification', str(n))] if n else [])
        if 'hidden' in item: _replace(params, ['IsHidden'], [('IsHidden', 'T')] if item['hidden'] else [])
    return _text_record(params)


ELECTRICAL = ['input', 'bidirectional', 'output', 'open_collector', 'passive', 'hi_z', 'open_emitter', 'power']
DECORATIONS = ['none', 'dot', 'right_left_signal_flow', 'clock', 'active_low_input', 'analog_signal_in', 'not_logic_connection', 'postponed_output', 'open_collector', 'hi_z', 'high_current', 'pulse', 'schmitt', 'active_low_output', 'open_collector_pull_up', 'open_emitter', 'open_emitter_pull_up', 'digital_signal_in', 'shift_left', 'open_output', 'left_right_signal_flow', 'bidirectional_signal_flow']
DIRECTIONS = ['right', 'up', 'left', 'down']


def _shortstring(data, pos):
    if pos >= len(data): raise SchLibFormatError('Truncated pin string')
    n = data[pos]
    if pos + 1 + n > len(data): raise SchLibFormatError('Truncated pin string payload')
    return _decode_text(data[pos + 1:pos + 1 + n]), pos + 1 + n


def _short(value):
    value = str(value).encode('utf-8')
    if len(value) > 255: raise ValueError('Pin string exceeds 255 ANSI bytes')
    return bytes([len(value)]) + value


def _decode_pin(data):
    if len(data) < 18 or struct.unpack_from('<I', data)[0] != 2: raise SchLibFormatError('Invalid binary pin record')
    description, pos = _shortstring(data, 12)
    if pos + 13 > len(data): raise SchLibFormatError('Truncated binary pin geometry')
    formal, electric, flags, length, x, y, colour = struct.unpack_from('<BBBHhhI', data, pos)
    pos += 13
    out = {'owner_part_id': struct.unpack_from('<h', data, 5)[0], 'owner_part_display_mode': data[7],
           'formal_type': formal, 'electrical_type': ELECTRICAL[electric] if electric < len(ELECTRICAL) else str(electric),
           'orientation': DIRECTIONS[flags & 3], 'hidden': bool(flags & 4), 'show_name': bool(flags & 8),
           'show_designator': bool(flags & 16), 'is_not_accessible': bool(flags & 32), 'graphically_locked': bool(flags & 64),
           'length': length, 'x': x, 'y': y, 'colour': colour}
    for key, value in zip(('symbol_inner_edge', 'symbol_outer_edge', 'symbol_inside', 'symbol_outside'), data[8:12]):
        out[key] = DECORATIONS[value] if value < len(DECORATIONS) else str(value)
    for key in ('name', 'designator', 'swap_id_group', 'part_and_sequence', 'default_value'):
        value, pos = _shortstring(data, pos)
        if value or key in ('name', 'designator', 'part_and_sequence'): out[key] = value
    if description: out['description'] = description
    if data[4]: out['raw_reserved'] = data[4]
    if flags & 128: out['raw_flags'] = flags & 128
    if pos < len(data): out['raw_tail'] = base64.b64encode(data[pos:]).decode('ascii')
    return out


def _encode_pin(pin):
    flags = DIRECTIONS.index(pin.get('orientation', 'right')) | pin.get('raw_flags', 0)
    for key, bit, default in [('hidden', 4, False), ('show_name', 8, True), ('show_designator', 16, True), ('is_not_accessible', 32, False), ('graphically_locked', 64, False)]:
        if pin.get(key, default): flags |= bit
    electric = pin.get('electrical_type', 'passive')
    if electric == 'tristate': electric = 'hi_z'
    def enum_id(values, value):
        return values.index(value) if value in values else int(value)
    data = struct.pack('<IBhB', 2, pin.get('raw_reserved', 0), pin.get('owner_part_id', 1), pin.get('owner_part_display_mode', 0))
    data += bytes(enum_id(DECORATIONS, pin.get(key, 'none')) for key in ('symbol_inner_edge', 'symbol_outer_edge', 'symbol_inside', 'symbol_outside'))
    data += _short(pin.get('description', ''))
    data += struct.pack('<BBBHhhI', pin.get('formal_type', 1), enum_id(ELECTRICAL, electric), flags, int(pin.get('length', 30)), int(pin.get('x', 0)), int(pin.get('y', 0)), pin.get('colour', 0))
    for key, default in [('name', ''), ('designator', ''), ('swap_id_group', ''), ('part_and_sequence', '|&|'), ('default_value', '')]: data += _short(pin.get(key, default))
    data += base64.b64decode(pin.get('raw_tail', ''))
    return _block(data, 1)


def _aux_records(name, entries):
    data = _text_record([['HEADER', name], ['Weight', str(len(entries))]])
    for index, payload in entries:
        compressed = zlib.compress(payload)
        data += _block(b'\xd0' + _short(str(index)) + struct.pack('<I', len(compressed)) + compressed, 1)
    return data


def _decode_aux(data, numeric_keys=True):
    out = {}
    for kind, payload in _records(data):
        if kind != 1: continue
        if not payload or payload[0] != 0xd0: continue
        key, pos = _shortstring(payload, 1)
        if pos + 4 > len(payload): raise SchLibFormatError('Truncated pin auxiliary record')
        size = struct.unpack_from('<I', payload, pos)[0]
        out[int(key) if numeric_keys else key] = zlib.decompress(payload[pos + 4:pos + 4 + size])
    return out


def _decode_symbol(storage, streams):
    records = list(_records(streams[storage + '/Data']))
    header = next((_params(p) for k, p in records if not k and _values(_params(p)).get('RECORD') == '1'), None)
    if not header: raise SchLibFormatError(f'{storage}: no component header')
    h = _values(header)
    out = {'name': h.get('LIBREFERENCE', storage), 'description': h.get('COMPONENTDESCRIPTION', ''), 'part_count': max(1, int(_num(h, 'PartCount', 2)) - 1), 'current_part_id': int(_num(h, 'CurrentPartId', 1)), 'display_mode_count': int(_num(h, 'DisplayModeCount', 1)), 'part_id_locked': _bool(h, 'PartIDLocked'), 'source_library_name': h.get('SOURCELIBRARYNAME', '*'), 'target_file_name': h.get('TARGETFILENAME', '*'), 'all_pin_count': int(_num(h, 'AllPinCount')), 'header_params': header}
    order = []
    opaque = []
    for index, (kind, payload) in enumerate(records):
        if kind == 1 and payload[:4] == b'\x02\x00\x00\x00':
            out.setdefault('pins', []).append(_decode_pin(payload));order.append('pin');continue
        params = _params(payload) if kind == 0 else []
        d = _values(params)
        rid = int(_num(d, 'RECORD', -1))
        if rid in BY_ID:
            key = BY_ID[rid]
            out.setdefault(SPECS[key][1], []).append(_decode_shape(key, params)); order.append(key)
        elif rid == 34:
            out.update(designator=d.get('TEXT', 'U?'), designator_x=_coord(d, 'Location.X'), designator_y=_coord(d, 'Location.Y'))
            if int(_num(d, 'FontID', 1)) != 1: out['designator_font_id'] = int(_num(d, 'FontID', 1))
            if d.get('UNIQUEID'): out['designator_unique_id'] = d['UNIQUEID']
        elif rid == 45:
            fp = {'name': d.get('MODELNAME', ''), 'is_current': _bool(d, 'IsCurrent'), 'raw_params': params}
            for key, wire in [('library_path','MODELDATAFILE0'),('description','DESCRIPTION'),('unique_id','UNIQUEID')]:
                if d.get(wire): fp[key] = d[wire]
            out.setdefault('footprints', []).append(fp)
        elif rid not in (1, 44, 46, 48):
            opaque.append([index, base64.b64encode(_block(payload, kind)).decode('ascii')])
    if 'Storage' in streams and out.get('images'):
        images = _decode_aux(streams['Storage'], numeric_keys=False)
        for image in out['images']:
            payload = images.get(image.get('file_name', ''))
            if payload: image['image_data'] = base64.b64encode(payload).decode('ascii')
    if order: out['primitive_order'] = order
    if opaque: out['opaque_records'] = opaque
    for key in ('PinFrac', 'PinSymbolLineWidth', 'PinWideText'):
        if storage + '/' + key not in streams: continue
        for n, payload in _decode_aux(streams[storage + '/' + key]).items():
            pins = out.get('pins', [])
            if n >= len(pins): continue
            if key == 'PinFrac' and len(payload) >= 12:
                x, y, length = struct.unpack_from('<iiI', payload)
                pins[n]['frac'] = {'x': x, 'y': y, 'length': length}
            elif len(payload) >= 4:
                d = {k.upper():v for k,v in [part.split('=',1) for part in payload[4:].decode('utf-16le').split('|') if '=' in part]}
                if key == 'PinSymbolLineWidth':
                    pins[n]['symbol_line_width'] = int(_num(d, 'SYMBOL_LINEWIDTH'))
                else:
                    for field, wire in [('name','NAME'),('designator','DESIGNATOR'),('description','DESCRIPTION'),('default_value','DEFAULTVALUE'),('swap_id_group','SWAPIDGROUP'),('part_and_sequence','PARTANDSEQUENCE')]:
                        if wire in d: pins[n][field] = d[wire]
    extras = [[k[len(storage) + 1:], base64.b64encode(v).decode('ascii')] for k, v in streams.items() if k.startswith(storage + '/') and k[len(storage) + 1:] not in ('Data', 'PinFrac', 'PinSymbolLineWidth', 'PinWideText')]
    if extras: out['extra_streams'] = extras
    return out


def read_library(path, component_name=None, offset=0, limit=None, **options):
    streams = read_streams(path)
    if 'FileHeader' not in streams: raise SchLibFormatError('Not an Altium schematic library: FileHeader is missing')
    storages = [k[:-5] for k in streams if k.endswith('/Data')]
    symbols = [_decode_symbol(s, streams) for s in storages]
    # FileHeader supplies the intended library order, independent of OLE tree order.
    headers = list(_records(streams['FileHeader']))
    file_params = _params(headers[0][1]) if headers else []
    h = _values(file_params)
    names = [h.get(f'LIBREF{i}') for i in range(int(_num(h, 'CompCount')))]
    rank = {name.casefold(): i for i, name in enumerate(names) if name}
    symbols.sort(key=lambda s: rank.get(s['name'].casefold(), len(rank)))
    total = len(symbols)
    if component_name is not None:
        symbols = [s for s in symbols if s['name'].casefold() == component_name.casefold()]
        if not symbols: raise ValueError(f'Component {component_name!r} not found. Available symbols: {", ".join(s["name"] for s in [_decode_symbol(x, streams) for x in storages])}')
    if offset < 0 or (limit is not None and limit < 1): raise ValueError('Invalid pagination')
    selected = symbols[offset:None if limit is None else offset + limit]
    return {'filepath': str(path), 'status': 'success', 'symbols': selected, 'total_count': total, 'returned_count': len(selected), 'offset': offset, 'has_more': offset + len(selected) < len(symbols), 'units': 'schematic units (10 = 1 grid)'}


def get_component(path, name):
    return read_library(path, component_name=name)['symbols'][0]


def _header(symbol):
    raw = symbol.get('header_params')
    params = copy.deepcopy(raw) if raw else [['RECORD','1'],['IndexInSheet','-1'],['OwnerPartId','-1'],['LibraryPath','*'],['SheetPartFileName','*'],['AreaColor','11599871'],['Color','128']]
    updates = [('LibReference', symbol['name']), ('ComponentDescription', symbol.get('description', '')), ('PartCount', symbol.get('part_count', 1) + 1), ('DisplayModeCount', symbol.get('display_mode_count', 1)), ('CurrentPartId', symbol.get('current_part_id', 1)), ('SourceLibraryName', symbol.get('source_library_name', '*')), ('TargetFileName', symbol.get('target_file_name', '*')), ('AllPinCount', symbol.get('all_pin_count', len(symbol.get('pins', [])))), ('PartIDLocked', symbol.get('part_id_locked', False))]
    for key, value in updates:
        if _values(params).get(key.upper()) != _format(value): _replace(params, [key, '%UTF8%' + key], _wire_value(key, str if isinstance(value, str) else type(value), value, key) or [(key, _format(value))])
    return _text_record(params)


def _symbol_streams(symbol, storage, original=None):
    output = {storage + '/Data': _header(symbol)}
    pools = {kind: iter(symbol.get(spec[1], [])) for kind, spec in SPECS.items()}
    pools['pin'] = iter(symbol.get('pins', []))
    order = list(symbol.get('primitive_order', []))
    # Any newly added primitives follow the preserved sequence.
    consumed = {k: 0 for k in pools}
    sequence = []
    for kind in order:
        if kind not in pools: continue
        item = next(pools[kind], None)
        if item is not None: sequence.append((kind, item)); consumed[kind] += 1
    for kind in ['rectangle','pin','line','polyline','polygon','arc','pie','image','text_frame','bezier','ellipse','round_rect','elliptical_arc','label','ieee_symbol','parameter']:
        for item in pools[kind]: sequence.append((kind,item))
    blocks = [_header(symbol)]
    for index, (kind,item) in enumerate(sequence): blocks.append(_encode_pin(item) if kind == 'pin' else _encode_shape(kind,item,index))
    designator = symbol.get('designator')
    if designator is None:
        prefix = symbol.get('designator_prefix') or {'resistor':'R','capacitor':'C','inductor':'L','diode':'D','transistor':'Q','connector':'J','crystal':'Y','ic':'U'}.get(symbol.get('component_type',''),'U')
        designator = prefix + '?'
    blocks.append(_text_record([['RECORD','34'],['IndexInSheet','-1'],['OwnerPartId','-1'],['Location.X',_format(symbol.get('designator_x',-5))],['Location.Y',_format(symbol.get('designator_y',5))],['Color','8388608'],['FontID',str(symbol.get('designator_font_id',1))],['Text',designator],['Name','Designator'],['ReadOnlyState','1'],['UniqueID',symbol.get('designator_unique_id') or _uid()]]))
    footprints = symbol.get('footprints', [])
    owner_index = len(blocks)
    blocks.append(_text_record([['RECORD','44']]))
    if footprints:
        for i, fp in enumerate(footprints):
            params = copy.deepcopy(fp.get('raw_params', [['RECORD','45'],['OwnerIndex',str(owner_index)],['IndexInSheet','-1'],['Description',''],['ModelType','PCBLIB']]))
            updates = [('ModelName',fp['name']),('Description',fp.get('description','')),('IsCurrent',fp.get('is_current',i==0)),('UniqueID',fp.get('unique_id') or _values(params).get('UNIQUEID') or _uid())]
            if fp.get('library_path'):
                updates += [('DatafileCount',1),('ModelDatafile0',fp['library_path']),('ModelDatafileEntity0',fp['name']),('ModelDatafileKind0','PCBLib')]
            for key,value in updates:
                if _values(params).get(key.upper()) != _format(value): _replace(params,[key],[(key,_format(value))])
            model_index = len(blocks)
            blocks.append(_text_record(params))
            blocks.append(_text_record([['RECORD','46'],['OwnerIndex',str(model_index)]]))
            blocks.append(_text_record([['RECORD','48'],['OwnerIndex',str(model_index)]]))
    opaque = symbol.get('opaque_records')
    if opaque is None and original:
        opaque = _decode_symbol(storage, original).get('opaque_records', [])
    for index, encoded in sorted(opaque or [], key=lambda x: x[0]): blocks.insert(min(index,len(blocks)),base64.b64decode(encoded))
    output[storage + '/Data'] = b''.join(blocks)
    pins = symbol.get('pins', [])
    frac = [(i,struct.pack('<iiI',p.get('frac',{}).get('x',0),p.get('frac',{}).get('y',0),p.get('frac',{}).get('length',0))) for i,p in enumerate(pins) if any(p.get('frac',{}).values())]
    width = []
    for i,pin in enumerate(pins):
        if pin.get('symbol_line_width'):
            text = ('|SYMBOL_LINEWIDTH=' + str(pin['symbol_line_width'])).encode('utf-16le')
            width.append((i,struct.pack('<I',len(text)) + text))
    if frac: output[storage + '/PinFrac'] = _aux_records('PinFrac', frac)
    if width: output[storage + '/PinSymbolLineWidth'] = _aux_records('PinSymbolLineWidth', width)
    wide = []
    for i, pin in enumerate(pins):
        text = ''.join('|' + wire + '=' + pin[field] for field,wire in [('name','NAME'),('designator','DESIGNATOR'),('description','DESCRIPTION'),('default_value','DEFAULTVALUE'),('swap_id_group','SWAPIDGROUP'),('part_and_sequence','PARTANDSEQUENCE')] if field in pin and any(ord(c)>127 for c in pin[field]))
        if text:
            payload = text.encode('utf-16le')
            wide.append((i, struct.pack('<I', len(payload)) + payload))
    if wide: output[storage + '/PinWideText'] = _aux_records('PinWideText', wide)
    for name,encoded in symbol.get('extra_streams',[]):
        if name in ('Data','PinFrac','PinSymbolLineWidth'): continue
        output[storage + '/' + name] = base64.b64decode(encoded, validate=True)
    return output


_DEFAULT_HEADER = [('HEADER','Protel for Windows - Schematic Library Editor Binary File Version 5.0'),('Weight','47'),('MinorVersion','9'),('FontIdCount','1'),('Size1','10'),('FontName1','Times New Roman'),('UseMBCS','T'),('IsBOC','T'),('SheetStyle','9'),('BorderOn','T'),('SheetNumberSpaceSize','12'),('AreaColor','16317695'),('SnapGridOn','T'),('SnapGridSize','10'),('VisibleGridOn','T'),('VisibleGridSize','10'),('CustomX','18000'),('CustomY','18000'),('UseCustomSheet','T'),('ReferenceZonesOn','T'),('Display_Unit','0')]


def write_library(path, components=None, **options):
    symbols = components if components is not None else options.get('symbols', [])
    if not isinstance(symbols, list): raise ValueError('symbols must be a list')
    names = [s['name'].casefold() for s in symbols]
    if len(names) != len(set(names)): raise ValueError('Duplicate symbol names (case-insensitive)')
    original = read_streams(path) if Path(path).is_file() else {}
    if options.get('append') and original:
        old = read_library(path)['symbols']
        replacement = {s['name'].casefold():s for s in symbols}
        symbols = [replacement.pop(s['name'].casefold(),s) for s in old] + list(replacement.values())
    streams = dict(original)
    old_components = [k[:-5] for k in original if k.endswith('/Data')]
    active_storages = []
    global_storages = {'fileheader', 'sectionkeys', 'storage'} | {
        k.split('/')[0].casefold() for k in original if k.split('/')[0] not in old_components}
    reserved = set(global_storages)
    reserved.update(s['name'].casefold() for s in symbols)
    for symbol in symbols:
        name = symbol['name']
        if not name: raise ValueError('Symbol name cannot be empty')
        if any(c in name for c in '/\\:*?\"<>|'): raise ValueError('Symbol name contains an invalid character')
        existing = next((s for s in old_components if _decode_symbol(s,original)['name'].casefold() == name.casefold()),None)
        storage = existing or name
        if (len(storage.encode('utf-16-le')) > 62 or '!' in storage or
                storage.casefold() in [s.casefold() for s in active_storages] or
                not existing and storage.casefold() in global_storages):
            index = len(active_storages)
            storage = f'SYM_{index:06d}'
            while storage.casefold() in reserved or storage.casefold() in {s.casefold() for s in active_storages}:
                index += 1
                storage = f'SYM_{index:06d}'
        active_storages.append(storage)
        for suffix in ('Data','PinFrac','PinSymbolLineWidth'): streams.pop(storage + '/' + suffix,None)
        if existing and symbol == _decode_symbol(existing, original):
            streams.update({k:v for k,v in original.items() if k.startswith(storage + '/')})
        else:
            streams.update(_symbol_streams(symbol,storage,original if existing else None))
    for storage in old_components:
        if storage not in active_storages:
            for key in list(streams):
                if key.startswith(storage + '/'): del streams[key]
    records = list(_records(original['FileHeader'])) if 'FileHeader' in original else []
    params = copy.deepcopy(options.get('library_header_params') or (_params(records[0][1]) if records else _DEFAULT_HEADER))
    if not any(k.upper()=='UNIQUEID' for k,v in params): params.append(['UniqueID',_uid()])
    params = [[k,v] for k,v in params if not (k.upper().removeprefix('%UTF8%')=='COMPCOUNT' or any(k.upper().removeprefix('%UTF8%').startswith(x) and k.upper().removeprefix('%UTF8%')[len(x):].isdigit() for x in ('LIBREF','COMPDESCR','PARTCOUNT')))]
    params.append(['CompCount',str(len(symbols))])
    for i,symbol in enumerate(symbols): params.extend([[f'LibRef{i}',symbol['name']],[f'CompDescr{i}',symbol.get('description','')],[f'PartCount{i}',str(symbol.get('part_count',1)+1)]])
    expanded = []
    existing_keys = {k.upper() for k,v in params}
    for key,value in params:
        expanded.append([key,value])
        if not key.upper().startswith('%UTF8%') and any(ord(c)>127 for c in value) and ('%UTF8%' + key).upper() not in existing_keys:
            expanded.append(['%UTF8%' + key,value])
    streams['FileHeader'] = _text_record(expanded)
    stored_images = _decode_aux(original['Storage'], numeric_keys=False) if 'Storage' in original else {}
    for symbol in symbols:
        for image in symbol.get('images',[]):
            if image.get('embed_image'):
                stored_images[image.get('file_name','')] = base64.b64decode(image.get('image_data',''), validate=True)
    if stored_images:
        streams['Storage'] = _aux_records('Icon storage', list(stored_images.items()))
    else:
        streams.setdefault('Storage',_text_record([['HEADER','Icon storage']]))
    if any(s['name'] != storage for s,storage in zip(symbols,active_storages)):
        streams['SectionKeys'] = _text_record([['KeyCount',str(len(symbols))]] + [[f'LibRef{i}',s['name']] for i,s in enumerate(symbols)] + [[f'SectionKey{i}',storage] for i,storage in enumerate(active_storages)])
    write_streams(path,streams)
    return {'status':'success','filepath':str(path),'symbol_count':len(symbols),'symbol_names':[s['name'] for s in symbols]}


read_schlib = read_library
write_schlib = write_library


def read_library_header(path):
    """Return ordered library header/font parameters for explicit file copies."""
    data = read_streams(path).get('FileHeader')
    if data is None:
        raise SchLibFormatError('FileHeader is missing')
    records = list(_records(data))
    if not records:
        raise SchLibFormatError('FileHeader is empty')
    return _params(records[0][1])
