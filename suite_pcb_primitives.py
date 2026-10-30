"""MIT, independently implemented PCB primitive records.

Layout facts come from differential black-box synthetic files, not a third-party
codec. Component storage and library envelopes are handled by suite_pcblib_codec.
"""
from __future__ import annotations
import base64
import copy
import math
import struct
import uuid

MM_PER_UNIT = .00000254

class PcbPrimitiveError(ValueError):
    pass

class Primitive(dict):
    """Internal opaque blocks survive local edits without changing public JSON."""
    pass

LAYER_NAMES = {1:'Top Layer',32:'Bottom Layer',33:'Top Overlay',34:'Bottom Overlay',35:'Top Paste',36:'Bottom Paste',37:'Top Solder',38:'Bottom Solder',55:'Drill Guide',56:'Keep-Out Layer',73:'Drill Drawing',74:'Multi-Layer'}
LAYER_NAMES.update({i+1:f'Mid-Layer {i}' for i in range(1,31)})
LAYER_NAMES.update({i+38:f'Internal Plane {i}' for i in range(1,17)})
LAYER_NAMES.update({i+56:f'Mechanical {i}' for i in range(1,17)})
LAYER_IDS = {v.casefold():k for k,v in LAYER_NAMES.items()}
LAYER_IDS.update({'top 3d body':62,'bottom 3d body':63,'keepout':56,'keep-out':56,'multilayer':74})

TYPE_NAMES = {1:('arc','arcs'),2:('pad','pads'),3:('via','vias'),4:('track','tracks'),5:('text','text'),6:('fill','fills'),11:('region','regions'),12:('component_body','component_bodies')}
PLURAL = {v[0]:v[1] for v in TYPE_NAMES.values()}
TYPE_IDS = {v[0]:k for k,v in TYPE_NAMES.items()}
SHAPES = {1:'round',2:'rectangle',3:'octagonal',4:'rounded_rectangle'}
SHAPE_IDS = {v:k for k,v in SHAPES.items()}
SHAPE_IDS.update(oval=1,rounded_rectangle=1)
MODES = ['none','from_rule','manual']
CONNECT = ['relief','direct','no_connect']
JUSTIFICATIONS = ['bottom_left','bottom_center','bottom_right','middle_left','middle_center','middle_right','top_left','top_center','top_right']


def _read(fmt,data,offset,default=0):
    if offset+struct.calcsize('<'+fmt)>len(data):return default
    return struct.unpack_from('<'+fmt,data,offset)[0]

def _write(fmt,data,offset,value):
    end=offset+struct.calcsize('<'+fmt)
    if end>len(data):data.extend(b'\0'*(end-len(data)))
    struct.pack_into('<'+fmt,data,offset,value)

def _rounded_mm(value):return math.copysign(math.floor(abs(value)*1000000+.5000001)/1000000,value)
def _mm(data,offset):return _rounded_mm(_read('i',data,offset)*MM_PER_UNIT)
def _unit(value):return math.floor(float(value)/MM_PER_UNIT+.5) if value>=0 else math.ceil(float(value)/MM_PER_UNIT-.5)
def _putmm(data,offset,value):_write('i',data,offset,_unit(value))
def _b64(data):return base64.b64encode(data).decode('ascii')
def _unb64(value):return base64.b64decode(value or '',validate=True)
def _block(data):return struct.pack('<I',len(data))+data

def _text(data):
    try:return data.decode('utf-8')
    except UnicodeDecodeError:return data.decode('cp1252',errors='replace')

def _pstring(text):
    data=str(text).encode('utf-8')
    if len(data)>255:raise ValueError('PCB short string exceeds 255 encoded bytes')
    return bytes([len(data)])+data

def _getpstring(data):
    if not data:return ''
    return _text(data[1:1+data[0]])

def _layer(value,default=1):
    if isinstance(value,int):return value
    key=str(value).casefold()
    if key in LAYER_IDS:return LAYER_IDS[key]
    if key.startswith('layer '):return int(key[6:])
    raise ValueError(f'Unknown PCB layer {value!r}')

def _common(data):
    result={'layer':LAYER_NAMES.get(data[0],'Multi-Layer'),'net_index':_read('H',data,3,65535),'polygon_index':_read('H',data,5,65535),'component_index':_read('h',data,7,-1)}
    if data[0] not in LAYER_NAMES:result['raw_layer_id']=data[0]
    flags=[]
    bits=_read('H',data,1)
    if not bits&4:flags.append('LOCKED')
    if bits&512:flags.append('KEEPOUT')
    if flags:result['flags']=' | '.join(flags)
    return result

def _new_common(layer):
    return bytearray(bytes([layer,12,0])+b'\xff'*10)

def _patch_common(data,item,default_layer=1):
    data[0]=item['raw_layer_id'] if item.get('layer')=='Multi-Layer' and 'raw_layer_id' in item else _layer(item.get('layer',LAYER_NAMES.get(data[0],default_layer)),default_layer)
    for field,fmt,offset,default in [('net_index','H',3,65535),('polygon_index','H',5,65535),('component_index','h',7,-1)]:
        if field in item:_write(fmt,data,offset,item[field])
    if 'flags' in item:
        flags=item['flags'].upper();bits=_read('H',data,1)
        bits=(bits&~516)| (0 if 'LOCKED' in flags else 4) | (512 if 'KEEPOUT' in flags else 0)
        _write('H',data,1,bits)

_SIMPLE_FIELDS = {
  4:({'x1':13,'y1':17,'x2':21,'y2':25,'width':29},{},49,33,'00000000000000000600030100000000'),
  1:({'x':13,'y':17,'radius':21,'width':41},{'start_angle':25,'end_angle':33},60,45,'000000000000000600030100000000'),
  6:({'x1':13,'y1':17,'x2':21,'y2':25},{'rotation':29},50,37,'00000000000100000100000000'),
}

def _decode_simple(typ,data):
    mmfields,doublefields,*_= _SIMPLE_FIELDS[typ]
    item=Primitive(_common(data))
    item.update({key:_mm(data,offset) for key,offset in mmfields.items()})
    item.update({key:_read('d',data,offset) for key,offset in doublefields.items()})
    maskoffset,keepoffset={4:(35,45),1:(47,56),6:(37,46)}[typ]
    if _mm(data,maskoffset):item['solder_mask_expansion']=_mm(data,maskoffset)
    if _read('I',data,keepoffset):item['keepout_restrictions']=_read('I',data,keepoffset)
    item.blocks=[data]
    return item

def _encode_simple(typ,item):
    mmfields,doublefields,size,tail_start,tailhex = _SIMPLE_FIELDS[typ]
    blocks=getattr(item,'blocks',None)
    data=bytearray(blocks[0]) if blocks else _new_common(_layer(item.get('layer','Top Overlay' if typ in (1,4) else 'Top Layer')))
    if not blocks:
        data.extend(b'\0'*(size-len(data)))
        data[tail_start:]=bytes.fromhex(tailhex)
    _patch_common(data,item)
    for field,offset in mmfields.items():
        if field in item:_putmm(data,offset,item[field])
    for field,offset in doublefields.items():
        if field in item:_write('d',data,offset,float(item[field]))
    maskoffset,keepoffset={4:(35,45),1:(47,56),6:(37,46)}[typ]
    if 'solder_mask_expansion' in item:_putmm(data,maskoffset,item['solder_mask_expansion'])
    if 'keepout_restrictions' in item:_write('I',data,keepoffset,item['keepout_restrictions'])
    return [_block(data)]

_PAD_MM={'x':13,'y':17,'width':21,'height':25,'hole_size':45,'relief_conductor_width':68,'relief_air_gap':74,'power_plane_relief_expansion':78,'power_plane_clearance':82,'paste_mask_expansion':86,'solder_mask_expansion':90}


def _pad_ext_decode(data,extra,item):
    mode=data[62] if len(data)>62 else 0
    if mode==1:
        item['per_layer_sizes']=[{'width':_mm(data,21+i*8),'height':_mm(data,25+i*8)} for i in range(3)]
        item['per_layer_shapes']=[SHAPES.get(data[49+i],'round') for i in range(3)]
    elif mode==2 and len(extra)>=320:
        item['per_layer_sizes']=[{'width':_mm(extra,i*8),'height':_mm(extra,i*8+4)} for i in range(32)]
        item['per_layer_shapes']=[SHAPES.get(extra[256+i],'round') for i in range(32)]
        item['per_layer_corner_radii']=list(extra[288:320])
        if len(extra)>=576:
            item['per_layer_offsets']=[{'x':_mm(extra,320+i*8),'y':_mm(extra,324+i*8)} for i in range(32)]
    if mode!=2 and len(extra)>=651:
        if extra[262]:item['hole_shape']={1:'square',2:'slot'}.get(extra[262],str(extra[262]))
        item['hole_slot_length']=_mm(extra,263)
        item['hole_rotation']=_read('d',extra,267)
        radius=extra[564]
        if radius:item['corner_radius_percent']=radius
        if extra[532]==9 or radius and data[49]==1:item['shape']='rounded_rectangle'


def _decode_pad(blocks):
    data=blocks[4];extra=blocks[5]
    item=Primitive(_common(data));item['designator']=_getpstring(blocks[0])
    for field,offset in _PAD_MM.items():
        value=_mm(data,offset)
        if field in ('x','y','width','height','relief_conductor_width','relief_air_gap','power_plane_relief_expansion','power_plane_clearance') or value:item[field]=value
    item.update(shape=SHAPES.get(data[49],'round'),rotation=_read('d',data,52),is_plated=bool(data[60]),stack_mode=['simple','top_middle_bottom','full_stack'][min(2,data[62])],power_plane_connect_style=CONNECT[min(2,data[67])],relief_entries=_read('H',data,72),paste_mask_expansion_mode=MODES[min(2,data[101])],solder_mask_expansion_mode=MODES[min(2,data[102])],solder_mask_expansion_from_hole_edge=bool(data[125]),hole_slot_length=0.0,hole_rotation=0.0,raw_tail=_b64(data[61:]))
    for field,offset in [('identity_guid',126),('identity_guid_b',142)]:
        if len(data)>=offset+16:item[field]='{'+str(uuid.UUID(bytes_le=data[offset:offset+16])).upper()+'}'
    for field,offset in [('hole_positive_tolerance',162),('hole_negative_tolerance',166)]:
        value=_read('i',data,offset,0x7fffffff)
        if value!=0x7fffffff:item[field]=round(value*MM_PER_UNIT,6)
    if _read('I',data,110):item['jumper_id']=_read('I',data,110)
    _pad_ext_decode(data,extra,item)
    item.blocks=blocks
    return item


def _new_pad(item):
    data=_new_common(_layer(item.get('layer','Top Layer')));data.extend(b'\0'*(202-len(data)))
    for offset in (21,29,37):_putmm(data,offset,item.get('width',1))
    for offset in (25,33,41):_putmm(data,offset,item.get('height',1))
    data[49:52]=bytes([SHAPE_IDS.get(item.get('shape','round'),1)])*3
    data[60]=1
    _putmm(data,68,.254);_write('H',data,72,4);_putmm(data,74,.254);_putmm(data,78,.508);_putmm(data,82,.508)
    _write('H',data,114,65535 if data[0]==32 else 1)
    data[117]=1;_write('i',data,121,40000)
    data[126:142]=uuid.uuid4().bytes_le;data[142:158]=uuid.uuid4().bytes_le
    _write('i',data,162,0x7fffffff);_write('i',data,166,0x7fffffff)
    data[171]=1;_write('I',data,172,26);data[184]=1;data[185]=3
    return data


def _encode_pad(item):
    old=getattr(item,'blocks',None)
    data=bytearray(old[4]) if old else _new_pad(item)
    if item.get('raw_tail'):data[61:]=_unb64(item['raw_tail'])
    _patch_common(data,item)
    for field,offset in _PAD_MM.items():
        if field in item:_putmm(data,offset,item[field])
    shape=SHAPE_IDS.get(item.get('shape','round'),1)
    data[49]=shape
    mode=['simple','top_middle_bottom','full_stack'].index(item.get('stack_mode','simple'))
    data[62]=mode
    if not mode:data[50:52]=bytes([shape])*2
    if 'rotation' in item:_write('d',data,52,item['rotation'])
    if 'is_plated' in item:data[60]=bool(item['is_plated'])
    for field,offset,values in [('power_plane_connect_style',67,CONNECT),('paste_mask_expansion_mode',101,MODES),('solder_mask_expansion_mode',102,MODES)]:
        if field in item:data[offset]=values.index(item[field])
    if 'relief_entries' in item:_write('H',data,72,item['relief_entries'])
    if 'jumper_id' in item:_write('I',data,110,item['jumper_id'])
    if 'solder_mask_expansion_from_hole_edge' in item:data[125]=bool(item['solder_mask_expansion_from_hole_edge'])
    for field,offset in [('identity_guid',126),('identity_guid_b',142)]:
        if field in item:data[offset:offset+16]=uuid.UUID(item[field]).bytes_le
    for field,offset in [('hole_positive_tolerance',162),('hole_negative_tolerance',166)]:
        if field in item:_putmm(data,offset,item[field])
    sizes=item.get('per_layer_sizes',[]);shapes=item.get('per_layer_shapes',[])
    if mode==1:
        for i in range(3):
            s=sizes[i] if i<len(sizes) else {'width':item.get('width',1),'height':item.get('height',1)}
            _putmm(data,21+i*8,s['width']);_putmm(data,25+i*8,s['height']);data[49+i]=SHAPE_IDS.get(shapes[i],shape) if i<len(shapes) else shape
    extra=bytearray(old[5]) if old else bytearray()
    preserve_extra = old and len(extra)>=651 and all(item.get(k)==_decode_pad(old).get(k) for k in ('hole_shape','hole_slot_length','hole_rotation','corner_radius_percent','shape','width','height'))
    if mode==2:
        offsets=item.get('per_layer_offsets',[]);radii=item.get('per_layer_corner_radii',[])
        if len(extra)<320:extra=bytearray(576 if offsets else 320)
        if offsets and len(extra)<576:extra.extend(b'\0'*(576-len(extra)))
        for i in range(32):
            s=sizes[i] if i<len(sizes) else {'width':item.get('width',1),'height':item.get('height',1)}
            _putmm(extra,i*8,s['width']);_putmm(extra,i*8+4,s['height']);extra[256+i]=SHAPE_IDS.get(shapes[i],shape) if i<len(shapes) else shape;extra[288+i]=radii[i] if i<len(radii) else 0
            if offsets and i<len(offsets):_putmm(extra,320+i*8,offsets[i]['x']);_putmm(extra,324+i*8,offsets[i]['y'])
    elif not preserve_extra and (any(k in item for k in ('hole_shape','corner_radius_percent')) or item.get('shape')=='rounded_rectangle'):
        if len(extra)<651:
            extra=bytearray(651)
            for i in range(29):_putmm(extra,i*4,item.get('width',1));_putmm(extra,116+i*4,item.get('height',1));extra[232+i]=shape
            extra[628]=1;_write('I',extra,632,15);extra[636:641]=bytes.fromhex('0400800102');_putmm(extra,641,item.get('width',1));_putmm(extra,645,item.get('height',1));_write('H',extra,649,50)
        extra[262]={'round':0,'square':1,'slot':2}.get(item.get('hole_shape','round'),0)
        _putmm(extra,263,item.get('hole_slot_length',0));_write('d',extra,267,item.get('hole_rotation',0))
        radius=item.get('corner_radius_percent',50 if item.get('shape')=='rounded_rectangle' else 0)
        extra[531]=1
        extra[564:596]=bytes([radius])*32
        extra[532:564]=bytes([9 if item.get('shape')=='rounded_rectangle' else shape])*32
        extra[640]=9 if item.get('shape')=='rounded_rectangle' else shape
    blocks=[_pstring(item.get('designator','')),old[1] if old else b'\0',old[2] if old else _pstring('|&|0'),old[3] if old else b'\0',bytes(data),bytes(extra)]
    return [_block(b) for b in blocks]


_VIA_MM={'x':13,'y':17,'diameter':21,'hole_size':25,'thermal_relief_gap':32,'thermal_relief_width':38,'power_plane_relief_expansion':42,'power_plane_clearance':46,'paste_mask_expansion':50,'solder_mask_expansion':54}

def _decode_via(data):
    item=Primitive(_common(data));item.pop('layer',None)
    item.update({k:_mm(data,o) for k,o in _VIA_MM.items()})
    item.update(from_layer=LAYER_NAMES.get(data[29],f'Layer {data[29]}'),to_layer=LAYER_NAMES.get(data[30],f'Layer {data[30]}'),power_plane_connect_style=CONNECT[min(2,data[31])],thermal_relief_conductors=_read('H',data,36),solder_mask_expansion_from_hole_edge=bool(data[320]) if len(data)>320 else False,solder_mask_expansion_mode=MODES[min(2,data[62])] if len(data)>62 else 'none',diameter_stack_mode='simple',drill_layer_pair_type='through',raw_block=_b64(data))
    # Auxiliary fields occur in the extended via record.
    item['solder_mask_expansion_from_hole_edge']=bool(_read('B',data,258))
    item['solder_mask_expansion_mode']=MODES[min(2,_read('B',data,66))]
    item['diameter_stack_mode']=['simple','top_middle_bottom','full_stack'][min(2,_read('B',data,74))]
    item['drill_layer_pair_type']=['through','start','stop','end'][min(3,_read('I',data,312))]
    if item['diameter_stack_mode']!='simple':item['per_layer_diameters']=[_mm(data,75+i*4) for i in range(32)]
    if _mm(data,242)!=item['solder_mask_expansion']:item['solder_mask_expansion_back']=_mm(data,242)
    for field,offset in [('hole_positive_tolerance',291),('hole_negative_tolerance',295)]:
        if _read('i',data,offset,0x7fffffff)!=0x7fffffff:item[field]=_mm(data,offset)
    item.blocks=[data]
    return item

def _encode_via(item):
    old=getattr(item,'blocks',None)
    data=bytearray(_unb64(item['raw_block'])) if item.get('raw_block') else bytearray(old[0]) if old else _new_common(74)
    if not old and not item.get('raw_block'):
        data.extend(b'\0'*(321-len(data)));data[29]=1;data[30]=32
        _putmm(data,32,.254);_write('H',data,36,4);_putmm(data,38,.254);_putmm(data,42,.508);_putmm(data,46,.508)
        data[203:207]=bytes.fromhex('0f000301');data[254]=42;_write('i',data,291,0x7fffffff);_write('i',data,295,0x7fffffff);data[304]=30;data[308]=9;data[320]=1
    _patch_common(data,item,74)
    for field,offset in _VIA_MM.items():
        if field in item:_putmm(data,offset,item[field])
    for field,offset in [('from_layer',29),('to_layer',30)]:
        if field in item:data[offset]=_layer(item[field])
    if 'power_plane_connect_style' in item:data[31]=CONNECT.index(item['power_plane_connect_style'])
    if 'thermal_relief_conductors' in item:_write('H',data,36,item['thermal_relief_conductors'])
    diameters=item.get('per_layer_diameters',[])
    for i in range(32):_putmm(data,75+i*4,diameters[i] if i<len(diameters) else item.get('diameter',.6))
    for field,offset in [('hole_positive_tolerance',291),('hole_negative_tolerance',295),('solder_mask_expansion_back',242)]:
        if field in item:_putmm(data,offset,item[field])
    if 'solder_mask_expansion' in item and 'solder_mask_expansion_back' not in item:_putmm(data,242,item['solder_mask_expansion'])
    if 'solder_mask_expansion_mode' in item:data[66]=MODES.index(item['solder_mask_expansion_mode'])
    if 'solder_mask_expansion_from_hole_edge' in item:data[258]=bool(item['solder_mask_expansion_from_hole_edge'])
    if 'diameter_stack_mode' in item:data[74]=['simple','top_middle_bottom','full_stack'].index(item['diameter_stack_mode'])
    if 'drill_layer_pair_type' in item:_write('I',data,312,['through','start','stop','end'].index(item['drill_layer_pair_type']))
    return [_block(data)]


def _decode_wide(data):
    if not data:return {}
    # Native libraries commonly store UTF16 codepoints as comma-separated values.
    out={}
    if len(data)>=4 and _read('I',data,0)<=len(data)-4:data=data[4:4+_read('I',data,0)]
    for part in data.rstrip(b'\0').split(b'|'):
        if b'=' not in part:continue
        key,value=part.split(b'=',1)
        if key.upper().startswith(b'ENCODEDTEXT'):
            try:
                units=[int(x) for x in value.split(b',') if x]
                out[int(key[11:])]=struct.pack('<'+'H'*len(units),*units).decode('utf-16-le')
            except (ValueError,UnicodeError):pass
    return out


def _decode_text_primitive(blocks,wide,index):
    data=blocks[0];item=Primitive(_common(data))
    item.update(x=_mm(data,13),y=_mm(data,17),height=_mm(data,21),rotation=_read('d',data,27),stroke_width=_mm(data,36),text=wide.get(index,_getpstring(blocks[1])),font_name=data[46:110].decode('utf-16le',errors='replace').split('\0',1)[0],kind='stroke',justification='bottom_left',raw_geometry=_b64(data))
    for field,offset in [('mirror',35),('is_comment',40),('is_designator',41),('bold',44),('italic',45),('is_inverted',110),('use_inverted_rectangle',123)]:
        if _read('B',data,offset):item[field]=True
    for field,offset in [('inverted_border',111),('inverted_rect_text_offset',133)]:
        if _mm(data,offset):item[field]=_mm(data,offset)
    if item.get('use_inverted_rectangle'):
        item['inverted_rect_width']=_mm(data,124);item['inverted_rect_height']=_mm(data,128)
    if data[25]!=1:item['stroke_font']={0:'default',2:'sans_serif',3:'serif'}.get(data[25],str(data[25]))
    item['justification']={3:'bottom_left',6:'bottom_center',9:'bottom_right',2:'middle_left',5:'middle_center',8:'middle_right',1:'top_left',4:'top_center',7:'top_right'}.get(data[132],'bottom_left')
    item['kind']={0:'stroke',1:'true_type',2:'bar_code'}.get(data[160],'stroke')
    if data[157]:item['barcode_kind']=data[157]
    if item['kind']=='bar_code':
        for field,offset in [('barcode_full_width',137),('barcode_full_height',141),('barcode_x_margin',145),('barcode_y_margin',149)]:item[field]=_mm(data,offset)
        item['barcode_font_name']=data[161:225].decode('utf-16le',errors='replace').split('\0',1)[0]
        item['barcode_show_text']=bool(data[158]);item['barcode_inverted']=bool(data[159])
    item.blocks=blocks
    return item


def _encode_text_primitive(item,index):
    old=getattr(item,'blocks',None)
    data=bytearray(_unb64(item['raw_geometry'])) if item.get('raw_geometry') else bytearray(old[0]) if old else _new_common(_layer(item.get('layer','Top Overlay')))
    if not old and not item.get('raw_geometry'):
        data.extend(b'\0'*(252-len(data)));data[25]=1;_putmm(data,36,.1016)
        data[124:161]=bytes.fromhex('cee529007f5207000300000000a037a000200b2000400d0300400d03000000000000010100')
        data[225:]=bytes.fromhex('0106000301000000000080000000800000000000000000508ef4ff')
    _patch_common(data,item)
    for field,offset in [('x',13),('y',17),('height',21),('stroke_width',36)]:
        if field in item:_putmm(data,offset,item[field])
    if 'rotation' in item:_write('d',data,27,item['rotation'])
    font=item.get('font_name','Arial').encode('utf-16le')[:62]+b'\0\0';font=font.ljust(64,b'\0')
    data[46:110]=font
    if not old and not item.get('raw_geometry'):data[161:225]=font
    if 'barcode_font_name' in item:data[161:225]=(item['barcode_font_name'].encode('utf-16le')[:62]+b'\0\0').ljust(64,b'\0')
    for field,offset in [('mirror',35),('is_comment',40),('is_designator',41),('bold',44),('italic',45),('is_inverted',110),('use_inverted_rectangle',123),('barcode_kind',157),('barcode_show_text',158),('barcode_inverted',159)]:
        if field in item:data[offset]=int(item[field])
    for field,offset in [('inverted_border',111),('inverted_rect_text_offset',133),('inverted_rect_width',124),('inverted_rect_height',128),('barcode_full_width',137),('barcode_full_height',141),('barcode_x_margin',145),('barcode_y_margin',149)]:
        if field in item:_putmm(data,offset,item[field])
    if 'stroke_font' in item:data[25]={'default':1,'sans_serif':2,'serif':3}.get(item['stroke_font'],1)
    if 'justification' in item:data[132]={'bottom_left':3,'bottom_center':6,'bottom_right':9,'middle_left':2,'middle_center':5,'middle_right':8,'top_left':1,'top_center':4,'top_right':7}.get(item['justification'],3)
    if 'kind' in item:
        data[160]={'stroke':0,'true_type':1,'bar_code':2}[item['kind']];data[43]=bool(data[160])
    textblock=old[1] if old and item.get('text')==getattr(item,'original',{}).get('text') else bytes([len(item.get('text','').encode('cp1252',errors='replace'))])+item.get('text','').encode('cp1252',errors='replace')
    return [_block(data),_block(textblock)]


def _v7_layer(layer):
    if layer==1:return 'TOP'
    if layer==32:return 'BOTTOM'
    if 57<=layer<=72:return 'MECHANICAL'+str(layer-56)
    return LAYER_NAMES.get(layer,'MULTILAYER').replace(' ','').replace('-','').upper()


def _param_pairs(data):
    text=_text(data.rstrip(b'\0'))
    return [p.split('=',1) for p in text.split('|') if '=' in p]

def _param_dict(pairs):return {k.upper():v for k,v in pairs}
def _dim(value):
    text=str(value).strip().lower()
    if text.endswith('mil'):return round(float(text[:-3])*.0254,6)
    if text.endswith('mm'):return float(text[:-2])
    return float(text or '0')
def _pbool(value):return str(value).upper() in ('TRUE','T','1')
def _setparam(params,key,value):
    found=False
    for row in params:
        if row[0].upper()==key.upper():row[1]=str(value);found=True
    if not found:params.append([key,str(value)])

def _decode_region(typ,data):
    item=Primitive(_common(data));n=_read('I',data,18);end=22+n
    if end>len(data):raise PcbPrimitiveError('Region parameter block exceeds record')
    pairs=_param_pairs(data[22:end]);d=_param_dict(pairs)
    count=_read('I',data,end);pos=end+4
    if pos+count*16>len(data):raise PcbPrimitiveError('Region vertex array exceeds record')
    points=[{'x':round(_read('d',data,pos+i*16)*MM_PER_UNIT,6),'y':round(_read('d',data,pos+8+i*16)*MM_PER_UNIT,6)} for i in range(count)]
    item.update(sub_poly_index=int(d.get('SUBPOLYINDEX',-1)),union_index=int(d.get('UNIONINDEX',0)),arc_resolution=_dim(d.get('ARCRESOLUTION','0mil')),is_shape_based=_pbool(d.get('ISSHAPEBASED',False)),cavity_height=_dim(d.get('CAVITYHEIGHT','0mil')),param_key_order=[k for k,v in pairs])
    holepos=pos+count*16;holes=[]
    for _ in range(_read('I',data,14)):
        holecount=_read('I',data,holepos);holepos+=4
        if holepos+holecount*16>len(data):raise PcbPrimitiveError('Region hole vertices exceed record')
        holes.append([{'x':_rounded_mm(_read('d',data,holepos+i*16)*MM_PER_UNIT),'y':_rounded_mm(_read('d',data,holepos+8+i*16)*MM_PER_UNIT)} for i in range(holecount)])
        holepos+=holecount*16
    if holes:item['holes']=holes
    if typ==11:
        item.update(kind={0:'copper',1:'polygon_cutout',2:'board_cutout'}.get(int(d.get('KIND',0)),str(d.get('KIND'))),vertices=points)
        if d.get('NAME'):item['name']=d['NAME']
        if d.get('V7_LAYER')!=_v7_layer(data[0]):item['v7_layer']=d.get('V7_LAYER','')
    else:
        item.update(kind=int(d.get('KIND',0)),outline=points,name=d.get('NAME',' '),standoff_height=_dim(d.get('STANDOFFHEIGHT','0mil')),overall_height=_dim(d.get('OVERALLHEIGHT','0mil')),body_projection=int(d.get('BODYPROJECTION',0)),body_color_3d=int(d.get('BODYCOLOR3D',8421504)),body_opacity_3d=float(d.get('BODYOPACITY3D',1)),model_id=d.get('MODELID',''),model_name=d.get('MODEL.NAME',''),embedded=_pbool(d.get('MODEL.EMBED',False)),model_checksum=int(d.get('MODEL.CHECKSUM',0)),rotation_x=float(d.get('MODEL.3D.ROTX',0)),rotation_y=float(d.get('MODEL.3D.ROTY',0)),rotation_z=float(d.get('MODEL.3D.ROTZ',0)),z_offset=_dim(d.get('MODEL.3D.DZ','0mil')),model_2d_rotation=float(d.get('MODEL.2D.ROTATION',0)),model_2d_x=_dim(d.get('MODEL.2D.X','0mil')),model_2d_y=_dim(d.get('MODEL.2D.Y','0mil')),texture_center_x=d.get('TEXTURECENTERX','0mil'),texture_center_y=d.get('TEXTURECENTERY','0mil'),texture_size_x=d.get('TEXTURESIZEX','0mil'),texture_size_y=d.get('TEXTURESIZEY','0mil'),texture_rotation=d.get('TEXTUREROTATION',' 0.00000000000000E+0000'))
        if d.get('V7_LAYER')!=_v7_layer(data[0]):item['v7_layer']=d.get('V7_LAYER','')
        if d.get('IDENTIFIER'):
            try:item['identifier']=''.join(chr(int(x)) for x in d['IDENTIFIER'].split(','))
            except ValueError:item['identifier']=d['IDENTIFIER']
        item.pop('arc_resolution',None)
        if item['layer']=='Mechanical 6':item['layer']='Top 3D Body'
        if item['layer']=='Mechanical 7':item['layer']='Bottom 3D Body'
    known={'V7_LAYER','NAME','KIND','SUBPOLYINDEX','UNIONINDEX','ARCRESOLUTION','ISSHAPEBASED','CAVITYHEIGHT'}
    if typ==12:
        known.update({'STANDOFFHEIGHT','OVERALLHEIGHT','BODYPROJECTION','BODYCOLOR3D','BODYOPACITY3D',
                      'IDENTIFIER','TEXTURE','TEXTURECENTERX','TEXTURECENTERY','TEXTURESIZEX',
                      'TEXTURESIZEY','TEXTUREROTATION','MODELID','MODEL.CHECKSUM','MODEL.EMBED',
                      'MODEL.NAME','MODEL.2D.X','MODEL.2D.Y','MODEL.2D.ROTATION','MODEL.3D.ROTX',
                      'MODEL.3D.ROTY','MODEL.3D.ROTZ','MODEL.3D.DZ','MODEL.MODELTYPE','MODEL.MODELSOURCE'})
    additional=[[key,value] for key,value in pairs if key.upper() not in known]
    if additional:item['additional_parameters']=additional
    item.blocks=[data];item.params=pairs
    return item


def _encode_region(typ,item):
    old=getattr(item,'blocks',None)
    data=bytearray(old[0][:18]) if old else _new_common(_layer(item.get('layer','Top 3D Body' if typ==12 else 'Top Layer')))+b'\0'*5
    _patch_common(data,item)
    params=copy.deepcopy(getattr(item,'params',None))
    if params is None:
        params=[['V7_LAYER',_v7_layer(data[0])],['NAME',' ' if typ==12 else ''],['KIND','0'],['SUBPOLYINDEX','-1'],['UNIONINDEX','0'],['ARCRESOLUTION','0.5mil' if typ==12 else '0mil'],['ISSHAPEBASED','FALSE'],['CAVITYHEIGHT','0mil']]
        if typ==12:params += [['STANDOFFHEIGHT','0mil'],['OVERALLHEIGHT','0mil'],['BODYPROJECTION','0'],['ARCRESOLUTION','0.5mil'],['BODYCOLOR3D','8421504'],['BODYOPACITY3D','1.000'],['IDENTIFIER',''],['TEXTURE',''],['TEXTURECENTERX','0mil'],['TEXTURECENTERY','0mil'],['TEXTURESIZEX','0mil'],['TEXTURESIZEY','0mil'],['TEXTUREROTATION',' 0.00000000000000E+0000']]
    mappings={'v7_layer':'V7_LAYER','identifier':'IDENTIFIER','name':'NAME','sub_poly_index':'SUBPOLYINDEX','union_index':'UNIONINDEX','is_shape_based':'ISSHAPEBASED','cavity_height':'CAVITYHEIGHT','arc_resolution':'ARCRESOLUTION'}
    dims={'cavity_height','arc_resolution','standoff_height','overall_height','z_offset','model_2d_x','model_2d_y'}
    if typ==12:mappings.update(standoff_height='STANDOFFHEIGHT',overall_height='OVERALLHEIGHT',body_projection='BODYPROJECTION',body_color_3d='BODYCOLOR3D',body_opacity_3d='BODYOPACITY3D',model_id='MODELID',model_name='MODEL.NAME',embedded='MODEL.EMBED',model_checksum='MODEL.CHECKSUM',rotation_x='MODEL.3D.ROTX',rotation_y='MODEL.3D.ROTY',rotation_z='MODEL.3D.ROTZ',z_offset='MODEL.3D.DZ',model_2d_rotation='MODEL.2D.ROTATION',model_2d_x='MODEL.2D.X',model_2d_y='MODEL.2D.Y',texture_center_x='TEXTURECENTERX',texture_center_y='TEXTURECENTERY',texture_size_x='TEXTURESIZEX',texture_size_y='TEXTURESIZEY',texture_rotation='TEXTUREROTATION')
    for field,key in mappings.items():
        if field not in item:continue
        value=item[field]
        if typ==12 and field=='v7_layer' and not old and item.get('layer')!='Multi-Layer':continue
        if typ==12 and field in ('rotation_x','rotation_y','rotation_z','z_offset','model_2d_rotation','model_2d_x','model_2d_y') and not (old or item.get('model_id') or item.get('model_name')):continue
        if old:
            baseline=_decode_region(typ,old[0])
            if value==baseline.get(field):continue
        if field in dims:value=f'{float(value)/.0254:.6f}mil'
        elif field=='identifier':value=','.join(str(ord(c)) for c in value)
        elif isinstance(value,bool):value='TRUE' if value else 'FALSE'
        _setparam(params,key,value)
    if 'kind' in item and (not old or item['kind']!=_decode_region(typ,old[0]).get('kind')):_setparam(params,'KIND',{'copper':0,'polygon_cutout':1,'board_cutout':2}.get(item['kind'],item['kind']))
    if typ==12 and not old and item.get('model_name'):
        # New STEP-linked bodies need the type/source descriptor as well as
        # their transform. Keep the native format's stable descriptor order.
        defaults={'MODELID':'','MODEL.CHECKSUM':'0','MODEL.EMBED':'FALSE',
                  'MODEL.NAME':'','MODEL.2D.X':'0mil','MODEL.2D.Y':'0mil',
                  'MODEL.2D.ROTATION':'0.000','MODEL.3D.ROTX':'0.000',
                  'MODEL.3D.ROTY':'0.000','MODEL.3D.ROTZ':'0.000',
                  'MODEL.3D.DZ':'0mil','MODEL.MODELTYPE':'STEP',
                  'MODEL.MODELSOURCE':'Undefined'}
        values=dict(defaults);values.update({key:value for key,value in params if key in defaults})
        params=[pair for pair in params if pair[0] not in defaults]+[[key,values[key]] for key in defaults]
    for key,value in item.get('additional_parameters',[]):
        if '|' in key or '|' in value:raise ValueError('PCB parameter cannot contain the record separator')
        _setparam(params,key,value)
    if item.get('param_key_order'):
        remaining=list(params);ordered=[]
        for key in item['param_key_order']:
            at=next((i for i,p in enumerate(remaining) if p[0]==key),None)
            if at is not None:ordered.append(remaining.pop(at))
        params=ordered+remaining
    payload=('|'.join(k+'='+str(v) for k,v in params)+'\0').encode('utf-8')
    points=item.get('outline' if typ==12 else 'vertices',[])
    data += struct.pack('<I',len(payload))+payload+struct.pack('<I',len(points))
    for p in points:data += struct.pack('<dd',float(_unit(p['x'])),float(_unit(p['y'])))
    holes=item.get('holes',[])
    _write('I',data,14,len(holes))
    for hole in holes:
        data+=struct.pack('<I',len(hole))
        for p in hole:data+=struct.pack('<dd',float(_unit(p['x'])),float(_unit(p['y'])))
    return [_block(data)]


def decode_component_stream(data:bytes,wide_strings:bytes=b'',extra_streams=None):
    if len(data)<4:raise PcbPrimitiveError('Missing footprint name record')
    n=_read('I',data,0);pos=4+n
    if pos>len(data):raise PcbPrimitiveError('Truncated footprint name')
    out={'name':_getpstring(data[4:pos]),'pads':[],'vias':[],'tracks':[],'arcs':[],'fills':[],'text':[],'regions':[]}
    order=[];wide=_decode_wide(wide_strings);text_index=0
    while pos<len(data):
        typ=data[pos];pos+=1
        if typ not in TYPE_NAMES:
            out['opaque_records']=[_b64(data[pos-1:])]
            out['unsupported_primitive_types']=[typ]
            break
        count=6 if typ==2 else 2 if typ==5 else 1;blocks=[]
        for _ in range(count):
            if pos+4>len(data):raise PcbPrimitiveError('Truncated primitive block length')
            n=_read('I',data,pos);pos+=4
            if pos+n>len(data):raise PcbPrimitiveError('Primitive block exceeds component stream')
            blocks.append(data[pos:pos+n]);pos+=n
        kind,plural=TYPE_NAMES[typ]
        if typ in _SIMPLE_FIELDS:item=_decode_simple(typ,blocks[0])
        elif typ==2:item=_decode_pad(blocks)
        elif typ==3:item=_decode_via(blocks[0])
        elif typ==5:item=_decode_text_primitive(blocks,wide,text_index);text_index+=1
        else:item=_decode_region(typ,blocks[0])
        item.original=copy.deepcopy(dict(item))
        out.setdefault(plural,[]).append(item);order.append(kind)
    if order:out['primitive_order']=order
    if extra_streams:_decode_identity_streams(out,extra_streams)
    return out


def _primitive_sequence(footprint):
    pools={kind:iter(footprint.get(plural,[])) for kind,plural in PLURAL.items()}
    sequence=[]
    for kind in footprint.get('primitive_order',[]):
        if kind in pools:
            item=next(pools[kind],None)
            if item is not None:sequence.append((kind,item))
    for kind in ('pad','track','arc','via','text','region','fill','component_body'):
        sequence.extend((kind,item) for item in pools[kind])
    return sequence


def encode_component_stream(footprint:dict):
    sequence=_primitive_sequence(footprint)
    output=_block(_pstring(footprint['name']));text_index=0
    for kind,item in sequence:
        typ=TYPE_IDS[kind]
        if typ in _SIMPLE_FIELDS:blocks=_encode_simple(typ,item)
        elif typ==2:blocks=_encode_pad(item)
        elif typ==3:blocks=_encode_via(item)
        elif typ==5:blocks=_encode_text_primitive(item,text_index);text_index+=1
        else:blocks=_encode_region(typ,item)
        output+=bytes([typ])+b''.join(blocks)
    for raw in footprint.get('opaque_records',[]):output+=_unb64(raw)
    return output


_IDENTITY_TYPES={**TYPE_IDS,'region':89,'component_body':90}
_IDENTITY_NAMES={'pad':'Pad','track':'Track','arc':'Arc','via':'Via','text':'Text',
                 'region':'Region','fill':'Fill','component_body':'ComponentBody'}


def _identity_records(data):
    position=0
    while position<len(data):
        if position+4>len(data):raise PcbPrimitiveError('Truncated identity record')
        length=_read('I',data,position)
        if position+4+length>len(data):raise PcbPrimitiveError('Identity record exceeds stream')
        yield data[position:position+4+length], _param_dict(_param_pairs(data[position+4:position+4+length]))
        position+=4+length


def _decode_identity_streams(footprint,streams):
    sequence=_primitive_sequence(footprint)
    raw=streams.get('PrimitiveGuids/Data',b'')
    if len(raw)%24:raise PcbPrimitiveError('Truncated primitive GUID table')
    for position in range(0,len(raw),24):
        object_type,index=struct.unpack_from('<II',raw,position)
        if index<len(sequence) and object_type==_IDENTITY_TYPES[sequence[index][0]]:
            sequence[index][1]['guid']='{'+str(uuid.UUID(bytes_le=raw[position+8:position+24])).upper()+'}'
    for _,params in _identity_records(streams.get('UniqueIDPrimitiveInformation/Data',b'')):
        index=int(params.get('PRIMITIVEINDEX','-1'))
        if 0<=index<len(sequence) and params.get('PRIMITIVEOBJECTID')==_IDENTITY_NAMES[sequence[index][0]]:
            sequence[index][1]['unique_id']=params.get('UNIQUEID','')


def encode_identity_streams(footprint,original_streams=None):
    """Reindex public identities while retaining unmodelled owner GUID records."""
    original=original_streams or {};sequence=_primitive_sequence(footprint);extra={}
    raw=original.get('PrimitiveGuids/Data',b'')
    if len(raw)%24:raise PcbPrimitiveError('Truncated primitive GUID table')
    guids=[raw[p:p+24] for p in range(0,len(raw),24)
           if _read('I',raw,p) not in _IDENTITY_TYPES.values()]
    unique=[block for block,params in _identity_records(original.get('UniqueIDPrimitiveInformation/Data',b''))
            if params.get('PRIMITIVEOBJECTID') not in _IDENTITY_NAMES.values()]
    for index,(kind,item) in enumerate(sequence):
        if item.get('guid'):
            guids.append(struct.pack('<II',_IDENTITY_TYPES[kind],index)+uuid.UUID(item['guid'].strip('{}')).bytes_le)
        if item.get('unique_id'):
            payload=f"|PRIMITIVEINDEX={index}|PRIMITIVEOBJECTID={_IDENTITY_NAMES[kind]}|UNIQUEID={item['unique_id']}\0"
            unique.append(_block(payload.encode('cp1252',errors='replace')))
    for storage,records in [('PrimitiveGuids',guids),('UniqueIDPrimitiveInformation',unique)]:
        if records or storage+'/Data' in original:
            extra[storage+'/Data']=b''.join(records)
            extra[storage+'/Header']=struct.pack('<I',len(records))
    return extra


def encode_wide_strings(footprint):
    parts=[]
    for i,item in enumerate(footprint.get('text',[])):
        text=item.get('text','')
        if any(ord(c)>127 for c in text):
            encoded=text.encode('utf-16-le')
            units=struct.unpack('<'+'H'*(len(encoded)//2),encoded)
            parts.append('ENCODEDTEXT'+str(i)+'='+','.join(str(unit) for unit in units))
    return _block(('|'.join(parts)+'\0').encode('ascii'))
