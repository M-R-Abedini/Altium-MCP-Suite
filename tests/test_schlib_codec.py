"""MIT clean-room SchLib tests. Golden records came only from a black-box
synthetic MCP write/read on 2026-10-29; see SCHLIB_FORMAT.md.
"""
import base64
import copy
import json
import struct

import pytest

from suite_ole import read_streams, write_streams
from suite_schlib_codec import SchLibFormatError, get_component, read_library, write_library

GOLDEN_STREAM_HEX = {'AllShapes/Data': '150100007c5245434f52443d317c4c69625265666572656e63653d416c6c5368617065737c436f6d706f6e656e744465736372697074696f6e3d436f6d706c657465207368617065737c50617274436f756e743d347c446973706c61794d6f6465436f756e743d327c496e646578496e53686565743d2d317c4f776e65725061727449643d2d317c43757272656e745061727449643d327c4c696272617279506174683d2a7c536f757263654c6962726172794e616d653d2a7c53686565745061727446696c654e616d653d2a7c54617267657446696c654e616d653d2a7c416c6c50696e436f756e743d317c41726561436f6c6f723d31313539393837317c436f6c6f723d3132387c5061727449444c6f636b65643d5400a30000007c5245434f52443d31347c49734e6f74416363657369626c653d547c4f776e65725061727449643d317c4c6f636174696f6e2e583d31307c4c6f636174696f6e2e593d31307c436f726e65722e583d31307c436f726e65722e593d31307c4c696e6557696474683d317c436f6c6f723d3132387c41726561436f6c6f723d31313539393837317c4973536f6c69643d547c556e6971756549443d4257525550535859002b000001020000000002000101030b0c04646573630302651e000f00e7ff7b00000001500234320147036162630164950000007c5245434f52443d31337c49734e6f74416363657369626c653d547c496e646578496e53686565743d327c4f776e65725061727449643d317c4c6f636174696f6e2e583d31307c4c6f636174696f6e2e593d31307c436f726e65722e583d31307c436f726e65722e593d31307c4c696e6557696474683d317c436f6c6f723d3132387c556e6971756549443d4f4a454e5150454e00a50000007c5245434f52443d367c49734e6f74416363657369626c653d547c496e646578496e53686565743d337c4f776e65725061727449643d317c4c696e6557696474683d317c436f6c6f723d3132387c4c6f636174696f6e436f756e743d337c58315f467261633d32353030307c59313d2d317c59315f467261633d2d35303030307c58323d31307c59323d31307c58333d32307c556e6971756549443d4e4944534659584100c20000007c5245434f52443d377c49734e6f74416363657369626c653d547c496e646578496e53686565743d347c4f776e65725061727449643d317c4c696e6557696474683d317c436f6c6f723d3132387c41726561436f6c6f723d31313539393837317c4973536f6c69643d547c4c6f636174696f6e436f756e743d337c58315f467261633d32353030307c59313d2d317c59315f467261633d2d35303030307c58323d31307c59323d31307c58333d32307c556e6971756549443d4358534e53504b5a00980000007c5245434f52443d31327c49734e6f74416363657369626c653d547c496e646578496e53686565743d357c4f776e65725061727449643d317c4c6f636174696f6e2e583d31307c4c6f636174696f6e2e593d31307c5261646975733d31307c4c696e6557696474683d317c456e64416e676c653d3336302e3030307c436f6c6f723d3132387c556e6971756549443d504b46475243545700970000007c5245434f52443d397c49734e6f74416363657369626c653d547c496e646578496e53686565743d367c4f776e65725061727449643d317c4c6f636174696f6e2e583d31307c4c6f636174696f6e2e593d31307c5261646975733d31307c4c696e6557696474683d317c456e64416e676c653d3336302e3030307c4973536f6c69643d547c556e6971756549443d534e4950595a4944008b0000007c5245434f52443d33307c49734e6f74416363657369626c653d547c496e646578496e53686565743d377c4f776e65725061727449643d317c4c6f636174696f6e2e583d31307c4c6f636174696f6e2e593d31307c436f726e65722e583d31307c436f726e65722e593d31307c4c696e6557696474683d317c556e6971756549443d4843584b5241545500e70000007c5245434f52443d32387c49734e6f74416363657369626c653d547c496e646578496e53686565743d387c4f776e65725061727449643d317c4c6f636174696f6e2e583d31307c4c6f636174696f6e2e593d31307c436f726e65722e583d31307c436f726e65722e593d31307c41726561436f6c6f723d31363737373231357c466f6e7449443d317c53686f77426f726465723d547c416c69676e6d656e743d317c576f7264577261703d547c436c6970546f526563743d547c546578743d7478747c546578744d617267696e5f467261633d357c556e6971756549443d4b464154574e4b4600a00000007c5245434f52443d357c49734e6f74416363657369626c653d547c496e646578496e53686565743d397c4f776e65725061727449643d317c4c696e6557696474683d317c436f6c6f723d3132387c4c6f636174696f6e436f756e743d347c58313d31307c59313d31307c58323d31307c59323d31307c58333d31307c59333d31307c58343d31307c59343d31307c556e6971756549443d445954534e59444100b70000007c5245434f52443d387c49734e6f74416363657369626c653d547c496e646578496e53686565743d31307c4f776e65725061727449643d317c4c6f636174696f6e2e583d31307c4c6f636174696f6e2e593d31307c5261646975733d31307c5365636f6e646172795261646975733d31307c4c696e6557696474683d317c436f6c6f723d3132387c41726561436f6c6f723d31313539393837317c4973536f6c69643d547c556e6971756549443d55504b505152575400d50000007c5245434f52443d31307c49734e6f74416363657369626c653d547c496e646578496e53686565743d31317c4f776e65725061727449643d317c4c6f636174696f6e2e583d31307c4c6f636174696f6e2e593d31307c436f726e65722e583d31307c436f726e65722e593d31307c436f726e6572585261646975733d31307c436f726e6572595261646975733d31307c4c696e6557696474683d317c436f6c6f723d3132387c41726561436f6c6f723d31313539393837317c4973536f6c69643d547c556e6971756549443d56514c574c51464300ac0000007c5245434f52443d31317c49734e6f74416363657369626c653d547c496e646578496e53686565743d31327c4f776e65725061727449643d317c4c6f636174696f6e2e583d31307c4c6f636174696f6e2e593d31307c5261646975733d31307c5365636f6e646172795261646975733d31307c4c696e6557696474683d317c456e64416e676c653d3336302e3030307c436f6c6f723d3132387c556e6971756549443d415651484746434c00830000007c5245434f52443d347c49734e6f74416363657369626c653d547c496e646578496e53686565743d31337c4f776e65725061727449643d317c4c6f636174696f6e2e583d31307c4c6f636174696f6e2e593d31307c436f6c6f723d3132387c466f6e7449443d317c546578743d7478747c556e6971756549443d464156534845544d007a0000007c5245434f52443d337c49734e6f74416363657369626c653d547c496e646578496e53686565743d31347c4f776e65725061727449643d317c53796d626f6c3d31307c4c6f636174696f6e2e583d31307c4c6f636174696f6e2e593d31307c5363616c65466163746f723d31307c4c696e6557696474683d3100680000007c5245434f52443d34317c496e646578496e53686565743d31357c4f776e65725061727449643d317c436f6c6f723d383338383630387c466f6e7449443d317c546578743d68656c6c6f7c4e616d653d56616c75657c556e6971756549443d455a55424b56555800960000007c5245434f52443d33347c496e646578496e53686565743d2d317c4f776e65725061727449643d2d317c4c6f636174696f6e2e583d2d357c4c6f636174696f6e2e593d357c436f6c6f723d383338383630387c466f6e7449443d317c546578743d553f7c4e616d653d44657369676e61746f727c526561644f6e6c7953746174653d317c556e6971756549443d4843584b58434857000b0000007c5245434f52443d343400d10000007c5245434f52443d34357c4f776e6572496e6465783d31387c496e646578496e53686565743d2d317c4465736372697074696f6e3d7c4d6f64656c4e616d653d506b677c4d6f64656c547970653d5043424c49427c4461746166696c65436f756e743d317c4d6f64656c4461746166696c65303d736f6d652e5063624c69627c4d6f64656c4461746166696c65456e74697479303d506b677c4d6f64656c4461746166696c654b696e64303d5043424c69627c497343757272656e743d547c556e6971756549443d4742575047504b5a00190000007c5245434f52443d34367c4f776e6572496e6465783d313900190000007c5245434f52443d34387c4f776e6572496e6465783d313900',
 'AllShapes/PinFrac': '190000007c4845414445523d50696e467261637c5765696768743d31001b000001d0013014000000789c4b616060b0f8ffffff17460606001c3d048f',
 'AllShapes/PinSymbolLineWidth': '240000007c4845414445523d50696e53796d626f6c4c696e6557696474687c5765696768743d310036000001d001302f000000789c53636060a86108668864f0657062f067f0618807624f063f0657867020edc210c2e0c160cb60c40000817205ef',
 'FileHeader': 'd40100007c4845414445523d50726f74656c20666f722057696e646f7773202d20536368656d61746963204c69627261727920456469746f722042696e6172792046696c652056657273696f6e20352e307c5765696768743d34377c4d696e6f7256657273696f6e3d397c556e6971756549443d455a5552574253547c466f6e744964436f756e743d317c53697a65313d31307c466f6e744e616d65313d54696d6573204e657720526f6d616e7c5573654d4243533d547c4973424f433d547c53686565745374796c653d397c426f726465724f6e3d547c53686565744e756d626572537061636553697a653d31327c41726561436f6c6f723d31363331373639357c536e6170477269644f6e3d547c536e61704772696453697a653d31307c56697369626c65477269644f6e3d547c56697369626c654772696453697a653d31307c437573746f6d583d31383030307c437573746f6d593d31383030307c557365437573746f6d53686565743d547c5265666572656e63655a6f6e65734f6e3d547c446973706c61795f556e69743d307c436f6d70436f756e743d317c4c6962526566303d416c6c5368617065737c436f6d704465736372303d436f6d706c657465207368617065737c50617274436f756e74303d3400',
 'Storage': '150000007c4845414445523d49636f6e2073746f7261676500'}

GOLDEN_SYMBOL = json.loads('{"all_pin_count": 1, "arcs": [{"color": 128, "end_angle": 360.0, "fill_color": 0, "is_not_accessible": true, "line_width": 1, "owner_part_id": 1, "radius": 10.0, "raw_params": [["RECORD", "12"], ["IsNotAccesible", "T"], ["IndexInSheet", "5"], ["OwnerPartId", "1"], ["Location.X", "10"], ["Location.Y", "10"], ["Radius", "10"], ["LineWidth", "1"], ["EndAngle", "360.000"], ["Color", "128"], ["UniqueID", "PKFGRCTW"]], "start_angle": 0.0, "unique_id": "PKFGRCTW", "x": 10.0, "y": 10.0}], "beziers": [{"color": 128, "is_not_accessible": true, "line_width": 1, "owner_part_id": 1, "raw_params": [["RECORD", "5"], ["IsNotAccesible", "T"], ["IndexInSheet", "9"], ["OwnerPartId", "1"], ["LineWidth", "1"], ["Color", "128"], ["LocationCount", "4"], ["X1", "10"], ["Y1", "10"], ["X2", "10"], ["Y2", "10"], ["X3", "10"], ["Y3", "10"], ["X4", "10"], ["Y4", "10"], ["UniqueID", "DYTSNYDA"]], "unique_id": "DYTSNYDA", "x1": 10.0, "x2": 10.0, "x3": 10.0, "x4": 10.0, "y1": 10.0, "y2": 10.0, "y3": 10.0, "y4": 10.0}], "current_part_id": 2, "description": "Complete shapes", "designator": "U?", "designator_unique_id": "HCXKXCHW", "designator_x": -5.0, "designator_y": 5.0, "display_mode_count": 2, "ellipses": [{"fill_color": 11599871, "filled": true, "is_not_accessible": true, "line_color": 128, "line_width": 1, "owner_part_id": 1, "radius_x": 10.0, "radius_y": 10.0, "raw_params": [["RECORD", "8"], ["IsNotAccesible", "T"], ["IndexInSheet", "10"], ["OwnerPartId", "1"], ["Location.X", "10"], ["Location.Y", "10"], ["Radius", "10"], ["SecondaryRadius", "10"], ["LineWidth", "1"], ["Color", "128"], ["AreaColor", "11599871"], ["IsSolid", "T"], ["UniqueID", "UPKPQRWT"]], "transparent": false, "unique_id": "UPKPQRWT", "x": 10.0, "y": 10.0}], "elliptical_arcs": [{"color": 128, "end_angle": 360.0, "fill_color": 0, "line_width": 1, "owner_part_id": 1, "radius": 10.0, "raw_params": [["RECORD", "11"], ["IsNotAccesible", "T"], ["IndexInSheet", "12"], ["OwnerPartId", "1"], ["Location.X", "10"], ["Location.Y", "10"], ["Radius", "10"], ["SecondaryRadius", "10"], ["LineWidth", "1"], ["EndAngle", "360.000"], ["Color", "128"], ["UniqueID", "AVQHGFCL"]], "secondary_radius": 10.0, "start_angle": 0.0, "unique_id": "AVQHGFCL", "x": 10.0, "y": 10.0}], "footprints": [{"is_current": true, "library_path": "some.PcbLib", "name": "Pkg", "raw_params": [["RECORD", "45"], ["OwnerIndex", "18"], ["IndexInSheet", "-1"], ["Description", ""], ["ModelName", "Pkg"], ["ModelType", "PCBLIB"], ["DatafileCount", "1"], ["ModelDatafile0", "some.PcbLib"], ["ModelDatafileEntity0", "Pkg"], ["ModelDatafileKind0", "PCBLib"], ["IsCurrent", "T"], ["UniqueID", "GBWPGPKZ"]], "unique_id": "GBWPGPKZ"}], "header_params": [["RECORD", "1"], ["LibReference", "AllShapes"], ["ComponentDescription", "Complete shapes"], ["PartCount", "4"], ["DisplayModeCount", "2"], ["IndexInSheet", "-1"], ["OwnerPartId", "-1"], ["CurrentPartId", "2"], ["LibraryPath", "*"], ["SourceLibraryName", "*"], ["SheetPartFileName", "*"], ["TargetFileName", "*"], ["AllPinCount", "1"], ["AreaColor", "11599871"], ["Color", "128"], ["PartIDLocked", "T"]], "ieee_symbols": [{"color": 0, "is_mirrored": false, "line_width": 1, "owner_part_id": 1, "raw_params": [["RECORD", "3"], ["IsNotAccesible", "T"], ["IndexInSheet", "14"], ["OwnerPartId", "1"], ["Symbol", "10"], ["Location.X", "10"], ["Location.Y", "10"], ["ScaleFactor", "10"], ["LineWidth", "1"]], "rotation": 0.0, "scale_factor": 10.0, "symbol": 10, "x": 10.0, "y": 10.0}], "images": [{"embed_image": false, "fill_color": 0, "filled": false, "is_not_accessible": true, "keep_aspect": false, "line_color": 0, "line_style": 0, "line_width": 1, "owner_part_id": 1, "raw_params": [["RECORD", "30"], ["IsNotAccesible", "T"], ["IndexInSheet", "7"], ["OwnerPartId", "1"], ["Location.X", "10"], ["Location.Y", "10"], ["Corner.X", "10"], ["Corner.Y", "10"], ["LineWidth", "1"], ["UniqueID", "HCXKRATU"]], "show_border": false, "transparent": false, "unique_id": "HCXKRATU", "x1": 10.0, "x2": 10.0, "y1": 10.0, "y2": 10.0}], "labels": [{"color": 128, "font_id": 1, "is_hidden": false, "is_mirrored": false, "justification": "bottom_left", "owner_part_id": 1, "raw_params": [["RECORD", "4"], ["IsNotAccesible", "T"], ["IndexInSheet", "13"], ["OwnerPartId", "1"], ["Location.X", "10"], ["Location.Y", "10"], ["Color", "128"], ["FontID", "1"], ["Text", "txt"], ["UniqueID", "FAVSHETM"]], "rotation": 0.0, "text": "txt", "unique_id": "FAVSHETM", "x": 10.0, "y": 10.0}], "lines": [{"color": 128, "is_not_accessible": true, "line_style": 0, "line_width": 1, "owner_part_id": 1, "raw_params": [["RECORD", "13"], ["IsNotAccesible", "T"], ["IndexInSheet", "2"], ["OwnerPartId", "1"], ["Location.X", "10"], ["Location.Y", "10"], ["Corner.X", "10"], ["Corner.Y", "10"], ["LineWidth", "1"], ["Color", "128"], ["UniqueID", "OJENQPEN"]], "unique_id": "OJENQPEN", "x1": 10.0, "x2": 10.0, "y1": 10.0, "y2": 10.0}], "name": "AllShapes", "parameters": [{"color": 8388608, "font_id": 1, "hidden": false, "name": "Value", "owner_part_id": 1, "param_type": 0, "raw_params": [["RECORD", "41"], ["IndexInSheet", "15"], ["OwnerPartId", "1"], ["Color", "8388608"], ["FontID", "1"], ["Text", "hello"], ["Name", "Value"], ["UniqueID", "EZUBKVUX"]], "read_only_state": 0, "unique_id": "EZUBKVUX", "value": "hello", "x": 0.0, "y": 0.0}], "part_count": 3, "part_id_locked": true, "pies": [{"end_angle": 360.0, "fill_color": 0, "filled": true, "is_not_accessible": true, "line_color": 0, "line_width": 1, "owner_part_id": 1, "radius": 10.0, "raw_params": [["RECORD", "9"], ["IsNotAccesible", "T"], ["IndexInSheet", "6"], ["OwnerPartId", "1"], ["Location.X", "10"], ["Location.Y", "10"], ["Radius", "10"], ["LineWidth", "1"], ["EndAngle", "360.000"], ["IsSolid", "T"], ["UniqueID", "SNIPYZID"]], "start_angle": 0.0, "transparent": false, "unique_id": "SNIPYZID", "x": 10.0, "y": 10.0}], "pins": [{"colour": 123, "default_value": "d", "description": "desc", "designator": "42", "electrical_type": "output", "formal_type": 3, "frac": {"length": 500, "x": 100, "y": -200}, "graphically_locked": true, "hidden": true, "is_not_accessible": true, "length": 30, "name": "P", "orientation": "up", "owner_part_display_mode": 1, "owner_part_id": 2, "part_and_sequence": "abc", "show_designator": false, "show_name": false, "swap_id_group": "G", "symbol_inner_edge": "dot", "symbol_inside": "pulse", "symbol_line_width": 2, "symbol_outer_edge": "clock", "symbol_outside": "schmitt", "x": 15, "y": -25}], "polygons": [{"fill_color": 11599871, "filled": true, "is_not_accessible": true, "line_color": 128, "line_style": 0, "line_width": 1, "owner_part_id": 1, "points": [{"x": 0.25, "y": -1.5}, {"x": 10.0, "y": 10.0}, {"x": 20.0, "y": 0.0}], "raw_params": [["RECORD", "7"], ["IsNotAccesible", "T"], ["IndexInSheet", "4"], ["OwnerPartId", "1"], ["LineWidth", "1"], ["Color", "128"], ["AreaColor", "11599871"], ["IsSolid", "T"], ["LocationCount", "3"], ["X1_Frac", "25000"], ["Y1", "-1"], ["Y1_Frac", "-50000"], ["X2", "10"], ["Y2", "10"], ["X3", "20"], ["UniqueID", "CXSNSPKZ"]], "transparent": false, "unique_id": "CXSNSPKZ"}], "polylines": [{"color": 128, "end_line_shape": 0, "is_not_accessible": true, "line_shape_size": 0, "line_style": 0, "line_width": 1, "owner_part_id": 1, "points": [{"x": 0.25, "y": -1.5}, {"x": 10.0, "y": 10.0}, {"x": 20.0, "y": 0.0}], "raw_params": [["RECORD", "6"], ["IsNotAccesible", "T"], ["IndexInSheet", "3"], ["OwnerPartId", "1"], ["LineWidth", "1"], ["Color", "128"], ["LocationCount", "3"], ["X1_Frac", "25000"], ["Y1", "-1"], ["Y1_Frac", "-50000"], ["X2", "10"], ["Y2", "10"], ["X3", "20"], ["UniqueID", "NIDSFYXA"]], "start_line_shape": 0, "transparent": false, "unique_id": "NIDSFYXA"}], "primitive_order": ["rectangle", "pin", "line", "polyline", "polygon", "arc", "pie", "image", "text_frame", "bezier", "ellipse", "round_rect", "elliptical_arc", "label", "ieee_symbol", "parameter"], "rectangles": [{"fill_color": 11599871, "filled": true, "line_color": 128, "line_style": 0, "line_width": 1, "owner_part_id": 1, "raw_params": [["RECORD", "14"], ["IsNotAccesible", "T"], ["OwnerPartId", "1"], ["Location.X", "10"], ["Location.Y", "10"], ["Corner.X", "10"], ["Corner.Y", "10"], ["LineWidth", "1"], ["Color", "128"], ["AreaColor", "11599871"], ["IsSolid", "T"], ["UniqueID", "BWRUPSXY"]], "transparent": false, "unique_id": "BWRUPSXY", "x1": 10.0, "x2": 10.0, "y1": 10.0, "y2": 10.0}], "round_rects": [{"corner_x_radius": 10.0, "corner_y_radius": 10.0, "fill_color": 11599871, "filled": true, "line_color": 128, "line_style": 0, "line_width": 1, "owner_part_id": 1, "raw_params": [["RECORD", "10"], ["IsNotAccesible", "T"], ["IndexInSheet", "11"], ["OwnerPartId", "1"], ["Location.X", "10"], ["Location.Y", "10"], ["Corner.X", "10"], ["Corner.Y", "10"], ["CornerXRadius", "10"], ["CornerYRadius", "10"], ["LineWidth", "1"], ["Color", "128"], ["AreaColor", "11599871"], ["IsSolid", "T"], ["UniqueID", "VQLWLQFC"]], "transparent": false, "unique_id": "VQLWLQFC", "x1": 10.0, "x2": 10.0, "y1": 10.0, "y2": 10.0}], "source_library_name": "*", "target_file_name": "*", "text_frames": [{"alignment": 1, "area_color": 16777215, "clip_to_rect": true, "color": 0, "font_id": 1, "is_not_accessible": true, "is_solid": false, "line_style": 0, "line_width": 0, "orientation": 0, "owner_part_id": 1, "raw_params": [["RECORD", "28"], ["IsNotAccesible", "T"], ["IndexInSheet", "8"], ["OwnerPartId", "1"], ["Location.X", "10"], ["Location.Y", "10"], ["Corner.X", "10"], ["Corner.Y", "10"], ["AreaColor", "16777215"], ["FontID", "1"], ["ShowBorder", "T"], ["Alignment", "1"], ["WordWrap", "T"], ["ClipToRect", "T"], ["Text", "txt"], ["TextMargin_Frac", "5"], ["UniqueID", "KFATWNKF"]], "show_border": true, "text": "txt", "text_color": 0, "text_margin": 5e-05, "transparent": false, "unique_id": "KFATWNKF", "word_wrap": true, "x1": 10.0, "x2": 10.0, "y1": 10.0, "y2": 10.0}]}')

def golden(tmp_path):
    path = tmp_path / 'golden.SchLib'
    write_streams(path, {k: bytes.fromhex(v) for k,v in GOLDEN_STREAM_HEX.items()})
    return path


def test_reads_every_observed_primitive_and_multipart_pin(tmp_path):
    response = read_library(golden(tmp_path))
    assert response['symbols'] == [GOLDEN_SYMBOL]
    assert response['total_count'] == response['returned_count'] == 1
    assert response['units'] == 'schematic units (10 = 1 grid)'


def test_cross_file_write_preserves_all_component_record_payloads(tmp_path):
    source = golden(tmp_path)
    destination = tmp_path / 'copy.SchLib'
    write_library(destination, read_library(source)['symbols'])
    assert read_library(destination)['symbols'] == [GOLDEN_SYMBOL]
    original = read_streams(source)
    written = read_streams(destination)
    for key in ('AllShapes/Data', 'AllShapes/PinFrac', 'AllShapes/PinSymbolLineWidth'):
        assert written[key] == original[key]


def test_typed_edits_override_raw_params_without_losing_unknowns(tmp_path):
    path = golden(tmp_path)
    symbol = get_component(path, 'allshapes')
    rectangle = symbol['rectangles'][0]
    rectangle['raw_params'].insert(3, ['FutureProperty', 'retained'])
    rectangle['x1'] = -3.125
    rectangle['filled'] = False
    rectangle['owner_part_display_mode'] = 2
    write_library(path, [symbol])
    result = get_component(path, 'ALLSHAPES')['rectangles'][0]
    assert result['x1'] == -3.125
    assert result['filled'] is False
    assert result['owner_part_display_mode'] == 2
    assert ['FutureProperty', 'retained'] in result['raw_params']


def test_unknown_records_streams_and_fonts_survive_modified_write(tmp_path):
    path = golden(tmp_path)
    streams = read_streams(path)
    record = b'|RECORD=913|Unknown=opaque|Counter=42\x00'
    block = struct.pack('<I',len(record)) + record
    streams['AllShapes/Data'] += block
    streams['AllShapes/FutureData'] = b'\x00opaque\xff'
    streams['OtherStorage/Future'] = b'library-level'
    streams['FileHeader'] = streams['FileHeader'].replace(b'Times New Roman', b'Arial          ')
    write_streams(path,streams)
    symbol = get_component(path,'AllShapes')
    symbol['parameters'][0]['value'] = 'edited'
    write_library(path,[symbol])
    after = read_streams(path)
    assert block in after['AllShapes/Data']
    assert after['AllShapes/FutureData'] == b'\x00opaque\xff'
    assert after['OtherStorage/Future'] == b'library-level'
    assert b'Arial          ' in after['FileHeader']
    destination = tmp_path / 'unknown-copy.SchLib'
    write_library(destination, read_library(path)['symbols'])
    assert block in read_streams(destination)['AllShapes/Data']


def test_authors_unicode_pin_fraction_images_and_long_names(tmp_path):
    path = tmp_path / 'unicode.SchLib'
    name = 'A component with a name longer than thirty one characters Ω'
    image_bytes = b'PNG synthetic payload'
    write_library(path,[{
        'name': name, 'description':'中文 café Ω', 'part_count':3,
        'pins':[{'name':'Ω汉','designator':'1','x':-20,'y':30,'length':25,
                 'orientation':'left','electrical_type':'power','owner_part_id':3,
                 'frac':{'x':12500,'y':-25000,'length':50000},'symbol_line_width':2}],
        'labels':[{'text':'中文 Ω','x':1.23456,'y':-2.34567,'rotation':90}],
        'images':[{'x1':0,'y1':0,'x2':10,'y2':10,'embed_image':True,
                   'file_name':'image.png','image_data':base64.b64encode(image_bytes).decode()}],
    }])
    symbol = get_component(path,name.lower())
    assert symbol['name'] == name
    assert symbol['description'] == '中文 café Ω'
    assert symbol['part_count'] == 3
    assert symbol['pins'][0]['name'] == 'Ω汉'
    assert symbol['pins'][0]['frac'] == {'x':12500,'y':-25000,'length':50000}
    assert symbol['pins'][0]['electrical_type'] == 'power'
    assert symbol['labels'][0]['text'] == '中文 Ω'
    assert symbol['labels'][0]['x'] == 1.23456
    assert symbol['labels'][0]['rotation'] == 90
    assert base64.b64decode(symbol['images'][0]['image_data']) == image_bytes
    assert 'SectionKeys' in read_streams(path)


def test_library_order_append_and_case_insensitive_lookup(tmp_path):
    path = tmp_path / 'parts.SchLib'
    write_library(path,[{'name':'Zulu'},{'name':'Alpha'}])
    write_library(path,[{'name':'Beta'}],append=True)
    assert [s['name'] for s in read_library(path)['symbols']] == ['Zulu','Alpha','Beta']
    assert get_component(path,'aLpHa')['name'] == 'Alpha'
    response = read_library(path,offset=1,limit=1)
    assert response['symbols'][0]['name'] == 'Alpha'
    assert response['has_more'] is True
    with pytest.raises(ValueError,match='Available symbols'):
        get_component(path,'missing')


def test_noop_preserves_every_existing_stream_payload(tmp_path):
    path = golden(tmp_path)
    before = read_streams(path)
    write_library(path,read_library(path)['symbols'])
    assert read_streams(path) == before


def test_rejects_duplicate_names_invalid_text_and_truncated_records(tmp_path):
    path = tmp_path / 'bad.SchLib'
    with pytest.raises(ValueError,match='Duplicate'):
        write_library(path,[{'name':'A'},{'name':'a'}])
    with pytest.raises(ValueError,match='separator'):
        write_library(path,[{'name':'A','description':'a|b'}])
    path = golden(tmp_path)
    streams = read_streams(path)
    streams['AllShapes/Data'] = struct.pack('<I',500) + b'x'
    write_streams(path,streams)
    with pytest.raises(SchLibFormatError,match='beyond'):
        read_library(path)
