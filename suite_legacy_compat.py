"""MIT legacy API compatibility, executed through the suite's one EDA transport.

The 37 public wrappers below are adapted from coffeenmusic/server/main.py.
Copyright (c) 2025 Siddharth Ahuja. MIT License; see coffeenmusic/LICENSE.
No legacy launcher, exchange polling loop or bridge instance is imported.
"""
from __future__ import annotations
import asyncio
import base64
from contextvars import ContextVar
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from typing import Any, Dict, Optional
import uuid
from mcp import types
from mcp.server.fastmcp import FastMCP, Context
from mcp.server.fastmcp.utilities.types import Image as MCPImage
from coffeenmusic.server.batch_safety import validate_batch_fields, validate_legacy_json_text
try:
    import win32gui, win32ui, win32con, win32api
except ImportError:
    pass
logger = logging.getLogger(__name__)
MCP_DIR = Path(__file__).resolve().parent / 'coffeenmusic/server'
_current: ContextVar = ContextVar('legacy_compat_service')
class _BridgeProxy:
    @property
    def config(self):
        return _current.get().config
    async def execute_command(self, command, params):
        return await _current.get().execute_command(command, params)
altium_bridge = _BridgeProxy()

async def get_all_component_property_names(ctx: Context) -> str:
    '\n    Get all available component property names (JSON keys) from all components\n    \n    Returns:\n        str: JSON array with all unique property names\n    '
    logger.info('Getting all component property names')
    response = await altium_bridge.execute_command('get_all_component_data', {})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error getting component data: {error_msg}')
        return json.dumps({'error': f'Failed to get component data: {error_msg}'})
    components_data = response.get('result', [])
    if not components_data:
        logger.info('No component data found')
        return json.dumps({'error': 'No component data found'})
    try:
        if isinstance(components_data, str):
            components_list = json.loads(components_data)
        else:
            components_list = components_data
        property_names = set()
        for component in components_list:
            property_names.update(component.keys())
        property_list = sorted(list(property_names))
        logger.info(f'Found {len(property_list)} unique property names')
        return json.dumps(property_list, indent=2)
    except Exception as e:
        logger.error(f'Error processing component data: {e}')
        return json.dumps({'error': f'Failed to process component data: {str(e)}'})

async def get_component_property_values(ctx: Context, property_name: str) -> str:
    '\n    Get values of a specific property for all components\n    \n    Args:\n        property_name (str): The name of the property to get values for\n    \n    Returns:\n        str: JSON array with objects containing designator and property value\n    '
    logger.info(f'Getting values for property: {property_name}')
    response = await altium_bridge.execute_command('get_all_component_data', {})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error getting component data: {error_msg}')
        return json.dumps({'error': f'Failed to get component data: {error_msg}'})
    components_data = response.get('result', [])
    if not components_data:
        logger.info('No component data found')
        return json.dumps({'error': 'No component data found'})
    try:
        if isinstance(components_data, str):
            components_list = json.loads(components_data)
        else:
            components_list = components_data
        property_values = []
        for component in components_list:
            designator = component.get('designator')
            if designator and property_name in component:
                property_values.append({'designator': designator, 'value': component.get(property_name)})
        logger.info(f"Found {len(property_values)} components with property '{property_name}'")
        return json.dumps(property_values, indent=2)
    except Exception as e:
        logger.error(f'Error processing component data: {e}')
        return json.dumps({'error': f'Failed to process component data: {str(e)}'})

async def get_symbol_placement_rules(ctx: Context) -> str:
    '\n    Get schematic symbol placement rules from a local configuration file\n    \n    Returns:\n        str: JSON object with rules for placing pins on schematic symbols\n    '
    logger.info('Getting symbol placement rules')
    rules_file_path = MCP_DIR / 'symbol_placement_rules.txt'
    if not rules_file_path.exists():
        logger.info('Symbol placement rules file not found, suggesting creation')
        default_rules = 'Only place pins on the left and right side of the symbol. Place power rail pins at the upper right, ground pins in the bottom left, no connect pins in the bottom right, inputs on the left, outputs on the right, and try to group other pins together by similar functionality (for example, SPI, I2C, RGMII, etc.). Always separate groups by 100mil gaps unless there is extra spacing, then space out groups equal distance from each other. '
        message = {'success': False, 'error': f'Rules file not found at: {rules_file_path}', 'message': f'Let the user know that they can optionally update the file {rules_file_path} with custom symbol placement rules. Suggested content: {default_rules}'}
        return json.dumps(message, indent=2)
    try:
        with open(rules_file_path, 'r') as f:
            rules_content = f.read()
        logger.info('Successfully read symbol placement rules file')
        result = {'success': True, 'message': f'Modify {rules_file_path} with custom symbol placement instructions', 'rules': rules_content}
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f'Error reading symbol placement rules file: {e}')
        return json.dumps({'success': False, 'error': f'Failed to read rules file: {str(e)}'}, indent=2)

async def get_library_symbol_reference(ctx: Context) -> str:
    '\n    Get the currently open symbol from a schematic library to use as reference for creating a new symbol.\n    This tool should be used before creating a new symbol to understand the structure of existing symbols.\n    \n    Returns:\n        str: JSON object with the reference symbol data including pins, their types, positions, and orientations\n    '
    logger.info('Getting library symbol reference data')
    response = await altium_bridge.execute_command('get_library_symbol_reference', {})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error getting symbol reference: {error_msg}')
        return json.dumps({'error': f'Failed to get symbol reference: {error_msg}'})
    symbol_data = response.get('result', {})
    if not symbol_data:
        logger.info('No symbol reference data found')
        return json.dumps({'error': 'No symbol reference data found or no symbol is currently selected in the library'})
    logger.info(f'Retrieved symbol reference data')
    return json.dumps(symbol_data, indent=2)

async def search_library_symbol(ctx: Context, symbol_name: str, library_path: str='') -> str:
    '\n    Search for a symbol by name in a schematic library (.SchLib) and navigate to it.\n    Supports partial name matching (case-insensitive). Returns all matches and navigates\n    to the best match (exact match preferred, otherwise first partial match).\n\n    This tool will automatically open the library file in Altium if a path is provided,\n    so no SchLib needs to be open beforehand.\n\n    Args:\n        symbol_name (str): Name or partial name of the symbol to search for\n        library_path (str): Full file path to the .SchLib file (e.g. "C:\\Libraries\\MyParts.SchLib").\n                           The tool will open this file in Altium if it is not already open.\n                           If empty, uses the currently open library.\n                           If no library is open and no path is provided, ask the user for the file path.\n\n    Returns:\n        str: JSON object with search results including matches, navigated symbol, and full symbol list\n    '
    logger.info(f"Searching for symbol: {symbol_name} in library: {library_path or '(current)'}")
    params = {'symbol_name': symbol_name}
    if library_path:
        params['library_path'] = library_path
    response = await altium_bridge.execute_command('search_library_symbol', params)
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error searching for symbol: {error_msg}')
        return json.dumps({'error': f'Failed to search for symbol: {error_msg}'})
    result = response.get('result', {})
    if not result:
        logger.info('No search results returned')
        return json.dumps({'error': 'No results returned from symbol search'})
    logger.info(f"Symbol search complete. Found: {result.get('found', False)}")
    return json.dumps(result, indent=2)

async def create_schematic_symbol(ctx: Context, symbol_name: str, description: str, pins: list, part_count: int=1, graphics: list=None) -> str:
    '\n    Before executing, run get_symbol_placement_rules first.\n\n    For Altium API guidance while scripting, use the "altium-script" skill\n    (ensure_altium_script_skill reports whether it is installed).\n\n    Also look for a similar existing symbol to use as a style reference\n    before drawing: search a company/library .SchLib for a comparable part\n    (same category - op-amp, comparator, MCU, regulator, diode...) with\n    search_library_symbol, then dump it with get_symbol_primitives and\n    mirror its conventions (body style, pin lengths, pin name visibility,\n    grid spacing, glyph shapes). This produces symbols consistent with the\n    user\'s library. If no symbol library is available to reference, that is\n    fine - skip this step and proceed with the defaults below; do not treat\n    a missing reference as an error.\n\n    Create a new schematic symbol in the current library with the specified pins\n    Instructions: pins should be grouped together via function and only placed on\n                  the left and right side in 100 mil increments\n\n    Pin name inversion/overbar: To show an overbar on a pin name (for active-low signals),\n                  place a backslash after EACH character that should be overbarred.\n                  Examples: R\\E\\S\\E\\T\\ renders as RESET with overbar.\n                           C\\S\\/A0 renders as CS with overbar followed by /A0 without overbar.\n                  Do NOT use ~{...} or other notation — only the backslash-per-character format works in Altium.\n\n    Args:\n        symbol_name (str): Name of the symbol to create\n        description (str): Description of the schematic symbol\n        pins (list): List of pin data in format\n                    "pin_number|pin_name|pin_type|pin_orientation|x|y[|owner_part_id[|length[|show_name[|show_designator]]]]"\n                    Pin types: eElectricHiZ, eElectricInput, eElectricIO, eElectricOpenCollector,\n                               eElectricOpenEmitter, eElectricOutput, eElectricPassive, eElectricPower\n                    Pin orientations: eRotate0 (right), eRotate90 (down), eRotate180 (left), eRotate270 (up)\n                    X,Y coordinates in mils\n                    owner_part_id (optional): Part number the pin belongs to (1-based).\n                               Use 0 for pins shared across all parts (e.g. power/GND).\n                               Defaults to 1 if omitted. Only needed for multi-part symbols.\n                    length (optional): pin length in mils (default 300)\n                    show_name / show_designator (optional): 1 or 0 to show/hide\n                               the pin name / number (e.g. op-amp pins often hide names)\n        part_count (int): Number of parts in the symbol (default 1).\n                         Use >1 for multi-part symbols like quad op-amps or hex buffers.\n        graphics (list, optional): Explicit body graphics. When given, the\n                    default auto-sized body rectangle is NOT drawn - the\n                    graphics fully define the symbol body (triangles for\n                    op-amps, diode glyphs, etc.). Entry formats (coordinates\n                    in mils; part = owner part id, 1-based; width 0-3 =\n                    zero/small/medium/large; solid 1 or 0):\n                    - "line|part|width|x1|y1|x2|y2"\n                    - "polyline|part|width|x1|y1|x2|y2|..." (any number of vertices)\n                    - "polygon|part|width|solid|x1|y1|x2|y2|..." (closed/filled shape)\n                    - "rectangle|part|width|solid|x1|y1|x2|y2"\n                    - "arc|part|width|cx|cy|radius|start_angle|end_angle" (degrees CCW from 3 o\'clock)\n                    - "elliptical_arc|part|width|cx|cy|radius|secondary_radius|start_angle|end_angle"\n                    - "ellipse|part|width|solid|cx|cy|radius|secondary_radius"\n                    - "label|part|x|y|text" (free text annotation)\n                    Tip: to reproduce an existing symbol\'s style, dump it first\n                    with get_symbol_primitives and mirror its primitives.\n\n    Returns:\n        str: JSON object with the result of the component creation\n    '
    logger.info(f'Creating schematic symbol: {symbol_name} with {len(pins)} pins, {part_count} part(s)')
    params = {'symbol_name': symbol_name, 'description': description, 'part_count': part_count, 'pins': pins}
    if graphics:
        params['graphics'] = graphics
    response = await altium_bridge.execute_command('create_schematic_symbol', params)
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error creating symbol: {error_msg}')
        return json.dumps({'success': False, 'error': f'Failed to create symbol: {error_msg}'})
    result = response.get('result', {})
    logger.info(f'Symbol {symbol_name} created successfully with {len(pins)} pins')
    return json.dumps(result, indent=2)

async def get_schematic_data(ctx: Context, cmp_designators: list) -> str:
    '\n    Get schematic data for components in Altium\n    \n    Args:\n        cmp_designators (list): List of designators of the components (e.g., ["R1", "C5", "U3"])\n    \n    Returns:\n        str: JSON object with schematic component data for requested designators\n    '
    logger.info(f'Getting schematic data for components: {cmp_designators}')
    response = await altium_bridge.execute_command('get_schematic_data', {})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error getting schematic data: {error_msg}')
        return json.dumps({'error': f'Failed to get schematic data: {error_msg}'})
    schematic_data = response.get('result', [])
    if not schematic_data:
        logger.info('No schematic data found')
        return json.dumps({'error': 'No schematic data found'})
    try:
        if isinstance(schematic_data, str):
            schematic_list = json.loads(schematic_data)
        else:
            schematic_list = schematic_data
        components = []
        missing_designators = []
        for designator in cmp_designators:
            found = False
            for component in schematic_list:
                if component.get('designator') == designator:
                    components.append(component)
                    found = True
                    break
            if not found:
                missing_designators.append(designator)
        result = {'components': components}
        if missing_designators:
            result['missing_designators'] = missing_designators
            logger.info(f'Some designators not found in schematic data: {missing_designators}')
        logger.info(f'Found schematic data for {len(components)} components')
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f'Error processing schematic data: {e}')
        return json.dumps({'error': f'Failed to process schematic data: {str(e)}'})

async def get_pcb_layers(ctx: Context) -> str:
    '\n    Get detailed information about all layers in the current Altium PCB\n    \n    Returns:\n        str: JSON object with detailed layer information including copper layers, \n             mechanical layers, and special layers with their properties\n    '
    logger.info('Getting detailed PCB layer information')
    response = await altium_bridge.execute_command('get_pcb_layers', {})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error getting PCB layers: {error_msg}')
        return json.dumps({'error': f'Failed to get PCB layers: {error_msg}'})
    layers_data = response.get('result', [])
    if not layers_data:
        logger.info('No PCB layers found')
        return json.dumps({'message': 'No PCB layers found in the current document'})
    logger.info(f'Retrieved PCB layers data')
    return json.dumps(layers_data, indent=2)

async def set_pcb_layer_visibility(ctx: Context, layer_names: list, visible: bool) -> str:
    '\n    Set visibility for specified PCB layers\n    \n    Args:\n        layer_names (list): List of layer names to modify (e.g., ["Top Layer", "Bottom Layer", "Mechanical 1"])\n        visible (bool): Whether to show (True) or hide (False) the specified layers\n        \n    Returns:\n        str: JSON object with the result of the operation\n    '
    logger.info(f'Setting layers visibility: {layer_names} to {visible}')
    response = await altium_bridge.execute_command('set_pcb_layer_visibility', {'layer_names': layer_names, 'visible': visible})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error setting layer visibility: {error_msg}')
        return json.dumps({'success': False, 'error': f'Failed to set layer visibility: {error_msg}'})
    result = response.get('result', {})
    logger.info(f'Layer visibility set successfully')
    return json.dumps(result, indent=2)

async def get_component_data(ctx: Context, cmp_designators: list) -> str:
    '\n    Get all data for components in Altium\n    \n    Args:\n        cmp_designators (list): List of designators of the components (e.g., ["R1", "C5", "U3"])\n    \n    Returns:\n        str: JSON object with all component data for requested designators\n    '
    logger.info(f'Getting data for components: {cmp_designators}')
    response = await altium_bridge.execute_command('get_all_component_data', {})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error getting component data: {error_msg}')
        return json.dumps({'error': f'Failed to get component data: {error_msg}'})
    component_data = response.get('result', [])
    if not component_data:
        logger.info('No component data found')
        return json.dumps({'error': 'No component data found'})
    try:
        if isinstance(component_data, str):
            component_list = json.loads(component_data)
        else:
            component_list = component_data
        components = []
        missing_designators = []
        for designator in cmp_designators:
            found = False
            for component in component_list:
                if component.get('designator') == designator:
                    components.append(component)
                    found = True
                    break
            if not found:
                missing_designators.append(designator)
        result = {'components': components}
        if missing_designators:
            result['missing_designators'] = missing_designators
            logger.info(f'Some designators not found: {missing_designators}')
        logger.info(f'Found data for {len(components)} components')
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f'Error processing component data: {e}')
        return json.dumps({'error': f'Failed to process component data: {str(e)}'})

async def get_selected_components_coordinates(ctx: Context) -> str:
    '\n    Get coordinates and positioning information for selected components in Altium layout\n    \n    Returns:\n        str: JSON array with positioning data (designator, x, y, rotation, width, height)\n    '
    logger.info('Getting coordinates for selected components')
    response = await altium_bridge.execute_command('get_selected_components_coordinates', {})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error getting selected components coordinates: {error_msg}')
        return json.dumps({'error': f'Failed to get selected components coordinates: {error_msg}'})
    components_coords = response.get('result', [])
    if not components_coords:
        logger.info('No selected components found')
        return json.dumps({'message': 'No components are currently selected in the layout'})
    logger.info(f'Retrieved positioning data for selected components')
    return json.dumps(components_coords, indent=2)

async def get_all_designators(ctx: Context) -> str:
    '\n    Get all component designators from the current Altium board\n    \n    Returns:\n        str: JSON array of all component designators on the current board\n    '
    logger.info('Getting all component designators')
    response = await altium_bridge.execute_command('get_all_component_data', {})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error getting component data: {error_msg}')
        return json.dumps({'error': f'Failed to get component data: {error_msg}'})
    component_data = response.get('result', [])
    if not component_data:
        logger.info('No component data found')
        return json.dumps({'error': 'No component data found'})
    try:
        if isinstance(component_data, str):
            component_list = json.loads(component_data)
        else:
            component_list = component_data
        designators = [comp.get('designator') for comp in component_list if 'designator' in comp]
        logger.info(f'Found {len(designators)} designators')
        return json.dumps(designators)
    except Exception as e:
        logger.error(f'Error processing component data: {e}')
        return json.dumps({'error': f'Failed to process component data: {str(e)}'})

async def get_component_pins(ctx: Context, cmp_designators: list) -> str:
    '\n    Get pin data for components in Altium\n\n    Args:\n        cmp_designators (list): List of designators of the components (e.g., ["R1", "C5", "U3"])\n\n    Returns:\n        str: JSON array, one entry per component with its placement info\n             (x/y in mils relative to the board origin, rotation in degrees\n             counterclockwise, layer) and a "pins" list. Per pin:\n             - x/y: absolute pad position (mils, relative to board origin)\n             - dx/dy: pad offset from the component origin in the footprint\'s\n               rotation-0 frame. To predict a pad position for a planned\n               placement: mirror dx (dx = -dx) if placing on the bottom layer,\n               rotate (dx, dy) counterclockwise by the planned rotation, then\n               add the planned component x/y.\n             - rotation: the pad\'s own rotation (NOT the component rotation)\n             - net, layer, width, height, shape\n    '
    logger.info(f'Getting pin data for components: {cmp_designators}')
    response = await altium_bridge.execute_command('get_component_pins', {'designators': cmp_designators})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error getting pin data: {error_msg}')
        return json.dumps({'error': f'Failed to get pin data: {error_msg}'})
    pins_data = response.get('result', [])
    if not pins_data:
        logger.info(f'No pin data found for designators: {cmp_designators}')
        return json.dumps({'message': 'No pin data found for the specified components'})
    logger.info(f'Retrieved pin data for components')
    return json.dumps(pins_data, indent=2)

async def run_altium_script(ctx: Context, script: str, timeout_seconds: int=120) -> str:
    '\n    Run a DelphiScript snippet inside an isolated Altium sandbox and report\n    what happened, step by step.\n\n    Use this to develop and verify Altium API code before relying on it.\n    Altium has no headless test mode and its failure modes are hostile: a\n    runtime error leaves the script PAUSED IN THE DEBUGGER with no dialog,\n    after which every later script run silently does nothing until the\n    debugger is stopped (Ctrl+F3) or Altium is restarted. This tool detects\n    that state and reports exactly which statement died.\n\n    Each call uses a separate project. A crash still occupies Altium\'s shared\n    script engine; other tools remain blocked until completion or manual recovery.\n\n    Writing the script:\n    - Call SandboxLog(\'...\') before each risky statement. The log is flushed\n      after every call, so the last logged line identifies what failed.\n    - Assign findings to the string variable ResultText - it is returned.\n    - DelphiScript has NO inline variable declarations. Reuse the provided\n      scratch variables: S1..S3 (String), I1..I3 and B1 (Integer),\n      Obj1..Obj5 (IDispatch), List1 (TStringList), IntMan, DbDoc.\n    - try/except does NOT catch runtime errors such as bad conversions or\n      invalid API calls, so it cannot be relied on to keep a script alive.\n    - The sandbox is standalone: helpers and constants from the production\n      units (TrimJSON, AddJSONProperty, ...) are NOT available; REPLACEALL is.\n    - Never register objects into a library document and never write to shared\n      or network library paths. Verify the target document kind first\n      (ObjectID 32 = schematic, 33 = symbol library).\n\n    For API guidance - interfaces, object models, worked examples - use the\n    "altium-script" skill. ensure_altium_script_skill reports whether that\n    skill is installed and can install it.\n\n    Args:\n        script (str): DelphiScript statements to execute (body only).\n        timeout_seconds (int): How long to wait for completion (default 120).\n\n    Returns:\n        str: JSON with success, the step log, the script\'s ResultText, and on\n             failure the last step reached plus whether Altium\'s script\n             executor is now wedged and needs recovery.\n    '
    return json.dumps(await _current.get().run_script(script, timeout_seconds), indent=2)

async def ensure_altium_script_skill(ctx: Context, install: bool=False) -> str:
    '\n    Check whether the "altium-script" skill is installed, and optionally\n    install it.\n\n    That skill documents the Altium DelphiScript API - interfaces, object\n    models, worked examples, conventions, and how to discover undocumented\n    processes - and is the reference to consult before writing scripts for\n    run_altium_script or debugging Altium API calls.\n\n    Source: https://github.com/coffeenmusic/altium-scripts-skill\n\n    Installing writes into the user\'s skills directory, so it only happens when\n    install=True is passed explicitly. Skills load at client startup, so a\n    newly installed skill becomes available after restarting the client.\n\n    Args:\n        install (bool): Install the skill if missing (requires git on PATH).\n\n    Returns:\n        str: JSON with installed (bool), the path checked, and the next step.\n    '
    skill_dir = Path.home() / '.claude' / 'skills' / 'altium-script'
    repo = 'https://github.com/coffeenmusic/altium-scripts-skill'
    if (skill_dir / 'SKILL.md').exists():
        return json.dumps({'installed': True, 'path': str(skill_dir), 'note': 'Use the altium-script skill for API guidance.'}, indent=2)
    if not install:
        return json.dumps({'installed': False, 'path': str(skill_dir), 'source': repo, 'next_step': 'Call again with install=true to clone it, or install manually into the path above.'}, indent=2)
    try:
        skill_dir.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(['git', 'clone', '--depth', '1', repo, str(skill_dir)], capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            return json.dumps({'installed': False, 'path': str(skill_dir), 'error': (proc.stderr or proc.stdout)[:400], 'hint': f'Requires git on PATH; otherwise download {repo} and extract to the path above.'}, indent=2)
        return json.dumps({'installed': True, 'path': str(skill_dir), 'source': repo, 'next_step': 'Restart the client so the skill is loaded.'}, indent=2)
    except Exception as e:
        return json.dumps({'installed': False, 'path': str(skill_dir), 'error': str(e)[:300]}, indent=2)

async def get_footprint_primitives(ctx: Context, library_path: str='', footprint_name: str='') -> str:
    '\n    Read the primitives of footprints in a PCB library (.PcbLib).\n\n    Modes:\n    - footprint_name omitted: inventory of every footprint with per-type\n      primitive counts (pads, tracks, arcs, fills, texts, regions, vias,\n      component_bodies)\n    - footprint_name given (exact, case-insensitive): full geometry dump -\n      pads (position, rotation, layer, sizes/shape per stack, hole size/\n      type/width/rotation, plating), tracks, arcs, fills, texts, regions\n      (outline vertices). Coordinates in mils; shapes and hole types as raw\n      Altium enum ints; layers as names. 3D component bodies are models,\n      not 2D primitives, and are excluded.\n    - footprint_name "*": full dump of every footprint\n\n    Use as the reference when recreating or validating footprints, and to\n    survey what a library requires.\n\n    Args:\n        library_path (str, optional): Full path to the .PcbLib. Omit to use\n            the currently focused PCB library (an already-open library is\n            only focused, never reloaded).\n        footprint_name (str, optional): Exact footprint name, or "*".\n\n    Returns:\n        str: JSON - inventory: {library_name, footprint_count, footprints:\n             [{name, description, <type counts>}]}; dump: primitives list\n             per footprint\n    '
    logger.info(f'Getting footprint primitives (library={library_path}, footprint={footprint_name})')
    response = await altium_bridge.execute_command('get_footprint_primitives', {'library_path': library_path, 'footprint_name': footprint_name})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        return json.dumps({'success': False, 'error': f'Failed to get footprint primitives: {error_msg}'})
    result = response.get('result', {})
    return json.dumps(result, indent=2) if not isinstance(result, str) else result

async def create_footprints_batch(ctx: Context, spec_file: str) -> str:
    '\n    Create many PCB footprints in a single Altium script run.\n\n    The batch equivalent of create_pcb_footprint with far broader coverage:\n    through-hole and SMD pads (full pad stack, holes, slots, plating,\n    rotation), tracks, arcs, fills, texts, and regions on any layer.\n    Verified by exact round-trips of complete production footprint\n    libraries. Prefer this for bulk imports/migrations; use\n    get_footprint_primitives on an existing footprint to learn the exact\n    field conventions.\n\n    Args:\n        spec_file (str): Path to a plain-text spec file, one record per line\n            (coords in mils, layers as names, shapes/hole types as raw\n            Altium enum ints, booleans as 1/0):\n            FPLIB|<path to .PcbLib>   (optional first line: opens/focuses)\n            FOOTPRINT|<name>|<description>\n            PAD|name|x|y|rot|layer|plated|hole_size|hole_type|hole_width|hole_rot|top_x|top_y|top_shape[|corner_pct[|mode|mid_x|mid_y|mid_shape|bot_x|bot_y|bot_shape]]\n            TRACK|x1|y1|x2|y2|width|layer\n            ARC|cx|cy|radius|start_angle|end_angle|width|layer\n            FILL|x1|y1|x2|y2|rotation|layer\n            TEXT|x|y|size|width|rotation|layer|mirror|ttf|text\n            REGION|layer|kind|x1|y1|x2|y2|...\n\n    Returns:\n        str: JSON with created count, primitive_errors, failed names\n    '
    logger.info(f'Creating footprints batch from {spec_file}')
    response = await altium_bridge.execute_command('create_footprints_batch', {'spec_file': spec_file})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        return json.dumps({'success': False, 'error': f'Failed batch footprint creation: {error_msg}'})
    result = response.get('result', {})
    return json.dumps(result, indent=2) if not isinstance(result, str) else result

async def build_schematic(ctx: Context, parts: list, wires: list=None, junctions: list=None, net_labels: list=None, power_ports: list=None, notes: list=None) -> str:
    '\n    Build a wired schematic on a NEW sheet from a circuit description.\n\n    READ dev/SCHEMATIC_CONVENTIONS.md BEFORE CALLING. This tool places exactly\n    what it is given; it does not lay out or correct spacing. The conventions\n    file records the drafting rules that make the result readable, each one\n    learned by getting it wrong. The ones that most often produce a plausible\n    but wrong sheet:\n\n      * A pin\'s connection point is NOT Pin.Location - it is PinLength further\n        along the pin. Do not guess coordinates. Call with parts only first,\n        read the returned pin_map, then call again with wires routed from those\n        measured coordinates.\n      * Never run a riser in a pin\'s connection column; it drives through every\n        pin sharing that column.\n      * Give a shunt part one grid of wire between its pin and the node it taps,\n        rather than putting the junction on the pin.\n      * A net label that does not TOUCH its wire names nothing.\n      * A shunt part occupies the band below its rail. Do not route another net\n        through that band - the wire will pass through the symbol body.\n      * A ground port\'s "GND" label is always drawn (ShowNetName cannot hide it)\n        and occupies ~300 mil below the port. Keep wires out of that band.\n\n    Args:\n        parts: component dicts. Required keys: designator, symbol_library,\n            symbol, x, y (mils). Optional: design_item_id, orientation (0-3),\n            mirror (0/1), comment, description, footprint,\n            parameters ({name: value}).\n        wires: each a flat list of alternating x,y in mils, e.g.\n            [3900, 3000, 5300, 3000, 5300, 4900] - two segments, three points.\n        junctions: [{"x":..,"y":..}] - only where 3+ branches actually meet.\n        net_labels: [{"x":..,"y":..,"orientation":0,"text":"LED+"}] - the\n            coordinate must lie on a wire.\n        power_ports: [{"x":..,"y":..,"orientation":3,"style":5,"text":"GND",\n            "show_net_name":false}]. Harvest style/orientation from an existing\n            sheet rather than guessing: 5 = digital ground, 2 = supply bar.\n        notes: [{"x":..,"y":..,"text":".."}] free text.\n\n    Returns:\n        JSON with the created sheet name, counts, and the pin map - every\n        placed pin\'s true connection point, for routing a follow-up call.\n    '
    logger.info(f'Building schematic: {len(parts)} parts')
    try:
        validate_batch_fields(dict(parts=parts, wires=wires, junctions=junctions, net_labels=net_labels, power_ports=power_ports, notes=notes))
    except ValueError as error:
        return json.dumps({'success': False, 'error': str(error), 'command_sent': False})
    lines = []
    for p in sorted(parts, key=lambda item: str(item.get('symbol_library', '')).casefold()):
        for key in ('designator', 'symbol_library', 'symbol', 'x', 'y'):
            if key not in p:
                return json.dumps({'success': False, 'error': f"part missing required key '{key}': {p}"})
        lines.append('PART|{}|{}|{}|{}|{}|{}|{}|{}'.format(p['designator'], p['symbol_library'], p['symbol'], p.get('design_item_id', ''), int(p['x']), int(p['y']), int(p.get('orientation', 0)), 1 if p.get('mirror') else 0))
        if p.get('comment'):
            lines.append(f"COMMENT|{p['comment']}")
        if p.get('footprint'):
            lines.append(f"FOOTPRINT|{p['footprint']}")
        if p.get('description'):
            lines.append(f"DESCRIPTION|{p['description']}")
        for name, val in (p.get('parameters') or {}).items():
            lines.append(f'PARAM|{name}|{val}')
    for route in wires or []:
        if len(route) < 4 or len(route) % 2:
            return json.dumps({'success': False, 'error': f'wire needs an even count of >=4 coords: {route}'})
        lines.append('WIRE|' + '|'.join((str(int(v)) for v in route)))
    for j in junctions or []:
        lines.append(f"JUNCTION|{int(j['x'])}|{int(j['y'])}")
    for n in net_labels or []:
        lines.append('NETLABEL|{}|{}|{}|{}'.format(int(n['x']), int(n['y']), int(n.get('orientation', 0)), n['text']))
    for pw in power_ports or []:
        lines.append('POWER|{}|{}|{}|{}|{}|{}'.format(int(pw['x']), int(pw['y']), int(pw.get('orientation', 3)), int(pw.get('style', 5)), pw['text'], 1 if pw.get('show_net_name') else 0))
    for nt in notes or []:
        lines.append(f"NOTE|{int(nt['x'])}|{int(nt['y'])}|{nt['text']}")
    capture_id = uuid.uuid4().hex
    spec_path = altium_bridge.config.exchange_dir / ('circuit_' + capture_id + '.txt')
    pm = altium_bridge.config.exchange_dir / ('pin_map_' + capture_id + '.txt')
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = spec_path.with_suffix('.txt.tmp')
    temporary.write_text('\n'.join(lines) + '\n', encoding='cp1252', errors='strict')
    temporary.replace(spec_path)
    try:
        response = await altium_bridge.execute_command('build_circuit', {'spec_file': str(spec_path), 'pin_map_file': str(pm)})
    finally:
        spec_path.unlink(missing_ok=True)
    if not response.get('success', False):
        return json.dumps({'success': False, 'error': response.get('error', 'unknown error')})
    result = response.get('result', {})
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return result
    pin_map = {}
    if pm.is_file():
        for line in pm.read_text(encoding='cp1252', errors='strict').splitlines():
            f = line.strip().split('|')
            if len(f) == 5 and f[0] == 'PIN':
                pin_map.setdefault(f[1], {})[f[2]] = [int(f[3]), int(f[4])]
        pm.unlink(missing_ok=True)
    result['pin_map'] = pin_map
    result['pin_map_note'] = 'Absolute electrical connection points. Route wires from these, not from predicted offsets.'
    return json.dumps(result, indent=2)

async def create_symbols_batch(ctx: Context, spec_file: str) -> str:
    '\n    Create many schematic symbols in a single Altium script run.\n\n    Use instead of repeated create_schematic_symbol calls when creating\n    more than a handful of symbols (bulk library imports/migrations): one\n    script launch instead of one per symbol, and pipe-delimited plain text\n    instead of JSON so field text (commas, brackets, spaces) is preserved\n    exactly. Verified by exact round-trips of a complete 284-symbol\n    production library.\n\n    Args:\n        spec_file (str): Path to a plain-text spec file, one record per line:\n            LIBRARY|<path to .SchLib>   (optional first line: opens/focuses;\n                                         an already-open library is only\n                                         focused, never reloaded)\n            SYMBOL|<name>|<description>|<part_count>\n            PIN|<same pipe fields as create_schematic_symbol pins>\n            GRAPHIC|<same entry format as create_schematic_symbol graphics>\n            Each SYMBOL line starts a new symbol; PIN/GRAPHIC lines belong\n            to the most recent SYMBOL.\n\n    Returns:\n        str: JSON object with created count and a failed name list\n    '
    logger.info(f'Creating symbols batch from {spec_file}')
    response = await altium_bridge.execute_command('create_symbols_batch', {'spec_file': spec_file})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error in batch symbol creation: {error_msg}')
        return json.dumps({'success': False, 'error': f'Failed batch creation: {error_msg}'})
    result = response.get('result', {})
    return json.dumps(result, indent=2) if not isinstance(result, str) else result

async def get_symbol_primitives(ctx: Context, library_path: str='', symbol_name: str='') -> str:
    "\n    Read the graphic primitives of symbols in a schematic library (.SchLib).\n\n    Two modes:\n    - symbol_name omitted: inventory of every symbol in the library with\n      per-type primitive counts (pins, rectangles, lines, polylines,\n      polygons, arcs, ellipses, beziers, labels, ...). Use this to survey\n      what drawing features a library's symbols require.\n    - symbol_name given (exact match, case-insensitive): full geometry dump\n      of that symbol - every primitive with coordinates in mils, plus pin\n      details (number, name, electrical type, orientation, length,\n      owner_part_id). Use this as the reference/spec when recreating or\n      validating a symbol.\n\n    Args:\n        library_path (str, optional): Full path to the .SchLib file to open.\n            Omit to use the schematic library currently focused in Altium.\n        symbol_name (str, optional): Exact symbol (LibReference) name to dump.\n\n    Returns:\n        str: JSON object - inventory mode: {library_name, symbol_count,\n             symbols: [{name, description, part_count, <type counts>}]};\n             dump mode: {library_name, symbol_name, description, part_count,\n             primitives: [...]}\n    "
    logger.info(f'Getting symbol primitives (library={library_path}, symbol={symbol_name})')
    response = await altium_bridge.execute_command('get_symbol_primitives', {'library_path': library_path, 'symbol_name': symbol_name})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error getting symbol primitives: {error_msg}')
        return json.dumps({'success': False, 'error': f'Failed to get symbol primitives: {error_msg}'})
    result = response.get('result', {})
    return json.dumps(result, indent=2) if not isinstance(result, str) else result

async def get_all_nets(ctx: Context) -> str:
    '\n    Return every unique net name in the active PCB document.\n\n    Returns\n    -------\n    str :\n        A JSON array of net names, e.g. ["GND", "VCC33", "USB_D+", ...]\n    '
    logger.info('Getting all nets')
    response = await altium_bridge.execute_command('get_all_nets', {})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error getting nets: {error_msg}')
        return json.dumps({'error': f'Failed to get nets: {error_msg}'})
    return json.dumps(response.get('result', []), indent=2)

async def create_net_class(ctx: Context, class_name: str, net_names: list) -> str:
    '\n    Create a new net class and add specified nets to it\n    \n    Args:\n        class_name (str): Name of the net class to create or modify\n        net_names (list): List of net names to add to the class\n    \n    Returns:\n        str: JSON object with the result of the operation\n    '
    logger.info(f"Creating net class '{class_name}' with {len(net_names)} nets")
    response = await altium_bridge.execute_command('create_net_class', {'class_name': class_name, 'net_names': net_names})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error creating net class: {error_msg}')
        return json.dumps({'success': False, 'error': f'Failed to create net class: {error_msg}'})
    result = response.get('result', {})
    logger.info(f"Net class '{class_name}' created/modified successfully")
    return json.dumps(result, indent=2)

async def set_component_position(ctx: Context, cmp_designator: str, x: float, y: float, rotation: float=-1) -> str:
    '\n    Set a component\'s absolute position in the PCB layout\n    \n    Args:\n        cmp_designator (str): Designator of the component to position (e.g., "R1", "C5", "U3")\n        x (float): Absolute X position in mils\n        y (float): Absolute Y position in mils\n        rotation (float): Rotation angle in degrees (0-360), use -1 to keep current rotation\n    \n    Returns:\n        str: JSON object with the result of the position operation\n    '
    logger.info(f'Setting component {cmp_designator} position to X:{x}, Y:{y}, Rotation:{rotation}')
    response = await altium_bridge.execute_command('set_component_position', {'designator': cmp_designator, 'x': x, 'y': y, 'rotation': rotation})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error setting component position: {error_msg}')
        return json.dumps({'success': False, 'error': f'Failed to set component position: {error_msg}'})
    result = response.get('result', {})
    logger.info(f'Component position set successfully')
    return json.dumps({'success': True, 'result': result}, indent=2)

async def move_components(ctx: Context, cmp_designators: list, x_offset: float, y_offset: float, rotation: float=0) -> str:
    '\n    Move components by RELATIVE offset from their current position (not absolute positioning)\n    \n    IMPORTANT: This moves components BY the offset amount, not TO a position.\n    For absolute positioning, use set_component_position instead.\n    \n    Args:\n        cmp_designators (list): List of designators of the components to move (e.g., ["R1", "C5", "U3"])\n        x_offset (float): X offset distance in mils (positive = right, negative = left)\n        y_offset (float): Y offset distance in mils (positive = up, negative = down)\n        rotation (float): New absolute rotation angle in degrees (0-360), if 0 the rotation is not changed\n    \n    Returns:\n        str: JSON object with the result of the move operation\n    '
    logger.info(f'Moving components: {cmp_designators} by X:{x_offset}, Y:{y_offset}, Rotation:{rotation}')
    response = await altium_bridge.execute_command('move_components', {'designators': cmp_designators, 'x_offset': x_offset, 'y_offset': y_offset, 'rotation': rotation})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error moving components: {error_msg}')
        return json.dumps({'success': False, 'error': f'Failed to move components: {error_msg}'})
    result = response.get('result', {})
    logger.info(f'Components moved successfully')
    return json.dumps({'success': True, 'result': result}, indent=2)

async def place_components(ctx: Context, placements: list) -> str:
    '\n    Place multiple components at absolute positions in a single Altium transaction.\n\n    This is the batch version of set_component_position - prefer it whenever\n    placing more than one component, since every tool call is a full round\n    trip into Altium. The whole batch is one undo step.\n\n    Coordinate conventions: x/y are in mils relative to the board origin;\n    rotation is in degrees counterclockwise.\n\n    Args:\n        placements (list): One dict per component:\n            - designator (str, required): e.g. "R1"\n            - x (float, required): absolute X position in mils\n            - y (float, required): absolute Y position in mils\n            - rotation (float, optional): absolute rotation in degrees (0-360).\n              Omit (or pass -1) to keep the current rotation.\n            - layer (str, optional): "top" or "bottom" to set the board side\n              (the footprint is mirrored when flipped). Omit to keep the\n              current side.\n\n    Example:\n        placements=[{"designator": "R1", "x": 1000, "y": 2000, "rotation": 90},\n                    {"designator": "C5", "x": 1050, "y": 2000, "layer": "bottom"}]\n\n    Returns:\n        str: JSON object with placed_count, missing_designators, and the final\n             x/y/rotation/layer of each placed component as Altium reports them\n    '
    logger.info(f'Placing {len(placements)} components')
    entries = []
    errors = []
    for idx, placement in enumerate(placements):
        if not isinstance(placement, dict):
            errors.append(f'placements[{idx}] must be an object')
            continue
        designator = str(placement.get('designator', '')).strip()
        x = placement.get('x')
        y = placement.get('y')
        if not designator or x is None or y is None:
            errors.append(f'placements[{idx}] must include designator, x, and y')
            continue
        if '|' in designator:
            errors.append(f"placements[{idx}] designator must not contain '|'")
            continue
        rotation = placement.get('rotation', -1)
        layer = str(placement.get('layer', '') or '').strip().lower()
        if layer not in ('', 'top', 'bottom'):
            errors.append(f"placements[{idx}] layer must be 'top' or 'bottom'")
            continue
        try:
            entry = f'{designator}|{float(x)}|{float(y)}|{float(rotation)}|{layer}'
        except (TypeError, ValueError):
            errors.append(f'placements[{idx}] x, y, and rotation must be numbers')
            continue
        entries.append(entry)
    if errors:
        return json.dumps({'success': False, 'error': '; '.join(errors)})
    if not entries:
        return json.dumps({'success': False, 'error': 'No placements provided'})
    response = await altium_bridge.execute_command('place_components', {'placements': entries})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error placing components: {error_msg}')
        return json.dumps({'success': False, 'error': f'Failed to place components: {error_msg}'})
    result = response.get('result', {})
    logger.info(f'Placed components successfully')
    return json.dumps({'success': True, 'result': result}, indent=2)

async def get_net_connections(ctx: Context, cmp_designators: list=None, max_pads_per_net: int=40) -> str:
    '\n    Get net connectivity and airline (unrouted connection) lengths for the\n    nets touching the given components.\n\n    Use this to plan or score a placement: it shows every pad on each net -\n    including pads of OTHER components outside the given set (e.g. an input\n    filter the cluster must connect to) - plus the net\'s minimum-spanning-tree\n    airline length in mils. Shorter airlines on critical nets (switching\n    loops, input/output capacitors) mean a better placement; non-critical\n    nets (enables, set resistors, feedback dividers) may be lengthened to\n    buy routing space. Plane nets like GND have many pads and a meaningless\n    airline - judge them by proximity to plane connections instead.\n\n    Args:\n        cmp_designators (list, optional): Components whose nets to analyze\n            (e.g. ["U12", "R42"]). Omit to use the current Altium selection.\n        max_pads_per_net (int): Nets with more pads than this (e.g. GND)\n            return only pads belonging to the given components, plus the\n            total pad_count. Their airline is also skipped. Default 40.\n\n    Returns:\n        str: JSON object with one entry per net: pad_count,\n             airline_mst_mils (None for large nets), and pads\n             [{designator, pin, x, y}, ...] in mils relative to the board\n             origin. Large nets set pads_truncated=true.\n    '
    logger.info(f'Getting net connections (designators={cmp_designators})')
    params = {}
    if cmp_designators:
        params['designators'] = cmp_designators
    response = await altium_bridge.execute_command('get_net_connections', params)
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error getting net connections: {error_msg}')
        return json.dumps({'success': False, 'error': f'Failed to get net connections: {error_msg}'})
    result = response.get('result', {})
    if isinstance(result, str):
        result = json.loads(result)
    target_set = set(cmp_designators or result.get('targets', []))
    nets = {}
    for pad in result.get('pads', []):
        nets.setdefault(pad['net'], []).append(pad)
    out_nets = []
    for name in result.get('net_names', []):
        pads = nets.get(name, [])
        entry = {'net': name, 'pad_count': len(pads)}
        if len(pads) <= max_pads_per_net:
            entry['airline_mst_mils'] = round(_mst_length([(p['x'], p['y']) for p in pads]), 1)
            entry['pads'] = [{'designator': p['designator'], 'pin': p['pin'], 'x': p['x'], 'y': p['y']} for p in pads]
        else:
            entry['airline_mst_mils'] = None
            entry['pads_truncated'] = True
            entry['pads'] = [{'designator': p['designator'], 'pin': p['pin'], 'x': p['x'], 'y': p['y']} for p in pads if not target_set or p['designator'] in target_set]
        out_nets.append(entry)
    out_nets.sort(key=lambda e: (e['airline_mst_mils'] is None, -(e['airline_mst_mils'] or 0)))
    logger.info(f'Net connections: {len(out_nets)} nets')
    return json.dumps({'net_count': len(out_nets), 'nets': out_nets}, indent=2)

async def check_orientation(ctx: Context, cmp_designators: list=None, min_improvement_mils: float=25) -> str:
    "\n    Advisory check: find 2-pad passives whose rotation could be improved.\n\n    For each 2-pad component in the target set, every orthogonal rotation\n    (0/90/180/270) is scored in place, summing one term per pad:\n    - routable nets (few pads): the net's total airline (MST) length with\n      this pad at the candidate position - so a pad that merely slides along\n      a pass-through flow (e.g. a bulk cap in a power chain) scores as\n      nearly free, while a genuine detour costs its real length\n    - plane nets (many pads, e.g. GND): distance to the nearest same-net\n      pad - a capacitor's ground-return loop is local, so proximity to the\n      IC's GND/thermal pad is what matters\n    A component is reported when a different rotation beats the current one\n    by at least min_improvement_mils.\n\n    This is advisory, not pass/fail: it measures geometry only and knows\n    nothing about net criticality. Suggestions matter for loop-critical\n    parts (capacitor ground returns, snubbers, input/output filters) and\n    should usually be ignored for parts whose orientation is electrically\n    arbitrary (pull-ups, strapping resistors, enables) or where a datasheet\n    layout recommendation says otherwise. Weigh suggestions with judgement\n    rather than applying them blindly. If a suggestion with\n    bbox_changes=true is applied (a 90-degree change alters the body\n    outline), re-run check_placement afterwards.\n\n    Args:\n        cmp_designators (list, optional): Components to check. Omit to use\n            the current Altium selection. Components with more or fewer than\n            2 pads are skipped.\n        min_improvement_mils (float): Only report components where the best\n            rotation improves the connection score by at least this many\n            mils (default 25).\n\n    Returns:\n        str: JSON object with checked_count and suggestions, each having\n             designator, current_rotation, suggested_rotation,\n             improvement_mils, bbox_changes, and a per-pad breakdown of\n             nearest same-net distances at the current vs suggested rotation.\n    "
    import math
    logger.info(f'Checking orientation (designators={cmp_designators})')
    if not cmp_designators:
        sel_resp = await altium_bridge.execute_command('get_selected_components_coordinates', {})
        if not sel_resp.get('success', False):
            return json.dumps({'success': False, 'error': sel_resp.get('error', 'Unknown error')})
        sel = sel_resp.get('result', [])
        if isinstance(sel, str):
            sel = json.loads(sel)
        cmp_designators = [c['designator'] for c in sel if 'designator' in c]
        if not cmp_designators:
            return json.dumps({'success': False, 'error': 'No components selected and no designators given'})
    pins_resp = await altium_bridge.execute_command('get_component_pins', {'designators': cmp_designators})
    if not pins_resp.get('success', False):
        return json.dumps({'success': False, 'error': pins_resp.get('error', 'Unknown error')})
    comps = pins_resp.get('result', [])
    if isinstance(comps, str):
        comps = json.loads(comps)
    nets_resp = await altium_bridge.execute_command('get_net_connections', {'designators': cmp_designators})
    if not nets_resp.get('success', False):
        return json.dumps({'success': False, 'error': nets_resp.get('error', 'Unknown error')})
    net_data = nets_resp.get('result', {})
    if isinstance(net_data, str):
        net_data = json.loads(net_data)
    net_pads = {}
    for p in net_data.get('pads', []):
        net_pads.setdefault(p['net'], []).append((p['designator'], p['x'], p['y']))
    PLANE_NET_PAD_COUNT = 40
    suggestions = []
    checked = 0
    for comp in comps:
        pins = comp.get('pins', [])
        if len(pins) != 2 or 'x' not in comp:
            continue
        mirror = comp.get('layer') == 'Bottom Layer'

        def score(rotation):
            """Hybrid per-pad score (see docstring); None if no pad scores."""
            total, detail = (0.0, [])
            for pin in pins:
                net = pin.get('net', '')
                cands = [(x, y) for d, x, y in net_pads.get(net, []) if d != comp['designator']] if net else []
                if not cands:
                    continue
                dx = -pin['dx'] if mirror else pin['dx']
                ox, oy = _rotate_offset(dx, pin['dy'], rotation)
                px, py = (comp['x'] + ox, comp['y'] + oy)
                if len(cands) > PLANE_NET_PAD_COUNT:
                    value = min((math.dist((px, py), c) for c in cands))
                    metric = 'nearest_return_mils'
                else:
                    value = _mst_length(cands + [(px, py)])
                    metric = 'net_airline_mils'
                total += value
                detail.append({'pin': pin['name'], 'net': net, metric: round(value, 1)})
            return (total, detail) if detail else None
        current = comp.get('rotation', 0) % 360
        cur = score(current)
        if cur is None:
            continue
        checked += 1
        best_rot, best = (current, cur)
        for r in (0, 90, 180, 270):
            s = score(r)
            if s is not None and s[0] < best[0]:
                best_rot, best = (r, s)
        improvement = cur[0] - best[0]
        if best_rot != current and improvement >= min_improvement_mils:
            suggestions.append({'designator': comp['designator'], 'current_rotation': current, 'suggested_rotation': best_rot, 'improvement_mils': round(improvement, 1), 'bbox_changes': (best_rot - current) % 180 != 0, 'current_pads': cur[1], 'suggested_pads': best[1]})
    suggestions.sort(key=lambda s: -s['improvement_mils'])
    logger.info(f'Orientation check: {len(suggestions)} suggestions from {checked} components')
    return json.dumps({'checked_count': checked, 'min_improvement_mils': min_improvement_mils, 'suggestion_count': len(suggestions), 'suggestions': suggestions}, indent=2)

async def check_placement(ctx: Context, cmp_designators: list=None, clearance_mils: float=6) -> str:
    '\n    Verify component placement: find overlaps and clearance violations.\n\n    Checks each target component against every other component on the same\n    side of the board. Bounding boxes (which include silkscreen) are used as\n    a fast prefilter; close pairs are then measured precisely with Altium\'s\n    primitive-to-primitive distance, so reported distances are true minimum\n    distances between any primitives (pads, silk, etc.) of the two parts.\n\n    Run this after placing components - a screenshot is not verification.\n\n    Args:\n        cmp_designators (list, optional): Components to check (e.g. ["U12", "R42"]).\n            Omit to check the components currently selected in Altium.\n        clearance_mils (float): Minimum allowed primitive-to-primitive distance\n            in mils (default 6). Pairs closer than this are reported.\n\n    Returns:\n        str: JSON object with checked_count, violation_count, and a violations\n             list. Each violation has a/b designators, type\n             ("bounding_box_overlap" = the parts\' outlines intersect, or\n             "clearance" = distance below threshold), distance_mils (0 =\n             touching/overlapping copper or silk), overlap sizes when boxes\n             intersect, and the other part\'s x/y position. An empty violations\n             list means the placement is clean at the given clearance.\n    '
    logger.info(f'Checking placement (designators={cmp_designators}, clearance={clearance_mils})')
    params = {'clearance_mils': clearance_mils}
    if cmp_designators:
        params['designators'] = cmp_designators
    response = await altium_bridge.execute_command('check_placement', params)
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error checking placement: {error_msg}')
        return json.dumps({'success': False, 'error': f'Failed to check placement: {error_msg}'})
    result = response.get('result', {})
    logger.info(f"Placement check complete: {result.get('violation_count', '?')} violations")
    return json.dumps(result, indent=2)

async def get_screenshot(ctx: Context, view_type: str='pcb', zoom_to: list=None):
    '\n    Take a screenshot of the Altium window, returned as viewable image content.\n\n    Args:\n        view_type (str): Type of view to capture - \'pcb\' or \'sch\'\n        zoom_to (list, optional): List of component designators (e.g. ["U12", "R42"]).\n            PCB view only: Altium zooms to the bounding box of these components\n            (plus a margin) before the capture, so the components fill the frame.\n            Omit to capture at the current zoom level.\n\n    Returns:\n        Image content of the captured window plus a JSON metadata text block\n        (window title, size, zoomed component count)\n    '
    logger.info(f'Taking screenshot of Altium {view_type} window (zoom_to={zoom_to})')
    try:
        params = {'view_type': view_type.lower()}
        if zoom_to:
            params['designators'] = zoom_to
        response = await altium_bridge.execute_command('take_view_screenshot', params)
        if not response.get('success', False):
            error_msg = response.get('error', 'Unknown error')
            logger.error(f'Error focusing {view_type} document: {error_msg}')
            return json.dumps({'success': False, 'error': f'Failed to focus the correct document type: {error_msg}'})
        import threading
        import queue
        import datetime
        from PIL import Image
        result_queue = queue.Queue()
        # Context variables do not propagate into the GDI capture thread.
        exchange_dir = altium_bridge.config.exchange_dir

        def capture_screenshot_thread():
            try:
                altium_windows = []
                altium_fallback_windows = []

                def collect_altium_windows(hwnd, _):
                    if win32gui.IsWindowVisible(hwnd):
                        title = win32gui.GetWindowText(hwnd)
                        if 'Altium' in title and '.PrjPcb' in title:
                            altium_windows.append({'handle': hwnd, 'title': title, 'class_name': win32gui.GetClassName(hwnd), 'rect': win32gui.GetWindowRect(hwnd)})
                        elif 'Altium' in title:
                            altium_fallback_windows.append({'handle': hwnd, 'title': title, 'class_name': win32gui.GetClassName(hwnd), 'rect': win32gui.GetWindowRect(hwnd)})
                    return True
                win32gui.EnumWindows(collect_altium_windows, 0)
                if not altium_windows and altium_fallback_windows:
                    altium_windows = altium_fallback_windows
                if not altium_windows:
                    result_queue.put({'success': False, 'error': f'No Altium windows found for {view_type} view'})
                    return
                window = altium_windows[0]
                hwnd = window['handle']
                left, top, right, bottom = window['rect']
                width = right - left
                height = bottom - top
                if width <= 0 or height <= 0:
                    result_queue.put({'success': False, 'error': f'Invalid window dimensions: {width}x{height}'})
                    return
                try:
                    win32gui.SetForegroundWindow(hwnd)
                    time.sleep(0.5)
                except Exception as e:
                    logger.warning(f'Could not bring window to foreground: {e}')
                try:
                    hwndDC = win32gui.GetWindowDC(hwnd)
                    mfcDC = win32ui.CreateDCFromHandle(hwndDC)
                    saveDC = mfcDC.CreateCompatibleDC()
                    saveBitMap = win32ui.CreateBitmap()
                    saveBitMap.CreateCompatibleBitmap(mfcDC, width, height)
                    saveDC.SelectObject(saveBitMap)
                    saveDC.BitBlt((0, 0), (width, height), mfcDC, (0, 0), win32con.SRCCOPY)
                    bmpinfo = saveBitMap.GetInfo()
                    bmpstr = saveBitMap.GetBitmapBits(True)
                    img = Image.frombuffer('RGB', (bmpinfo['bmWidth'], bmpinfo['bmHeight']), bmpstr, 'raw', 'BGRX', 0, 1)
                    try:
                        exchange_dir.mkdir(parents=True, exist_ok=True)
                        debug_filename = str(exchange_dir / f'screenshot_{view_type}.png')
                        img.save(debug_filename)
                        logger.info(f'Saved debug screenshot to {debug_filename}')
                    except Exception as save_error:
                        logger.warning(f'Could not save debug screenshot to {debug_filename}: {save_error}')
                        debug_filename = None
                    win32gui.DeleteObject(saveBitMap.GetHandle())
                    saveDC.DeleteDC()
                    mfcDC.DeleteDC()
                    win32gui.ReleaseDC(hwnd, hwndDC)
                    buffer = io.BytesIO()
                    img.save(buffer, format='PNG')
                    buffer.seek(0)
                    img_base64 = base64.b64encode(buffer.read()).decode('utf-8')
                    result_queue.put({'success': True, 'width': width, 'height': height, 'window_title': window['title'], 'window_class': window['class_name'], 'view_type': view_type, 'image_format': 'PNG', 'encoding': 'base64', 'debug_file': debug_filename, 'image_data': img_base64})
                except Exception as e:
                    import traceback
                    trace = traceback.format_exc()
                    logger.error(f'GDI screenshot error: {e}\n{trace}')
                    result_queue.put({'success': False, 'error': f'GDI screenshot failed: {str(e)}', 'traceback': trace})
            except Exception as e:
                import traceback
                result_queue.put({'success': False, 'error': f'Screenshot thread error: {str(e)}', 'traceback': traceback.format_exc()})
        thread = threading.Thread(target=capture_screenshot_thread)
        thread.daemon = True
        thread.start()
        thread.join(timeout=10)
        if thread.is_alive():
            logger.error('Screenshot thread timed out')
            return json.dumps({'success': False, 'error': 'Screenshot operation timed out'})
        if result_queue.empty():
            logger.error('Screenshot thread did not return a result')
            return json.dumps({'success': False, 'error': 'Screenshot thread did not return a result'})
        result = result_queue.get()
        if not result.get('success', False):
            error_msg = result.get('error', 'Unknown error')
            logger.error(f'Screenshot error: {error_msg}')
            return json.dumps({'success': False, 'error': error_msg})
        logger.info(f"Screenshot taken successfully, size: {result['width']}x{result['height']}")
        image_base64 = result.pop('image_data')
        result.pop('encoding', None)
        zoom_info = response.get('result', {})
        if isinstance(zoom_info, dict) and 'zoomed_component_count' in zoom_info:
            result['zoomed_component_count'] = zoom_info['zoomed_component_count']
        return [json.dumps(result), MCPImage(data=base64.b64decode(image_base64), format='png')]
    except Exception as e:
        logger.error(f'Error in screenshot function: {str(e)}')
        return json.dumps({'success': False, 'error': f'Failed to take screenshot: {str(e)}'})

async def layout_duplicator(ctx: Context) -> str:
    '\n    First step of layout duplication. Selects source components and returns data to match with destination components.\n    \n    Returns:\n        str: JSON object with source and destination component data for matching\n    '
    logger.info('Starting layout duplication - selection phase')
    response = await altium_bridge.execute_command('layout_duplicator', {})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error in layout duplication selection: {error_msg}')
        return json.dumps({'success': False, 'error': f'Failed to start layout duplication: {error_msg}'})
    components_data = response.get('result', {})
    if not components_data:
        logger.info('No component data found')
        return json.dumps({'success': False, 'error': 'No component data returned from Altium'})
    try:
        if isinstance(components_data, str):
            result_json = json.loads(components_data)
            if not result_json.get('success', True):
                logger.info(f"Source component selection issue: {result_json.get('message', 'Unknown issue')}")
                return json.dumps(result_json)
    except Exception as e:
        logger.error(f'Error parsing layout duplicator result: {e}')
    logger.info(f'Retrieved layout duplicator component data')
    return json.dumps(components_data, indent=2)

async def layout_duplicator_apply(ctx: Context, source_designators: list, destination_designators: list) -> str:
    '\n    Second step of layout duplication. Applies the layout of source components to destination components.\n    \n    Args:\n        source_designators (list): List of source component designators (e.g., ["R1", "C5", "U3"])\n        destination_designators (list): List of destination component designators (e.g., ["R10", "C15", "U7"])\n    \n    Returns:\n        str: JSON object with the result of the layout duplication\n    '
    logger.info(f'Applying layout duplication from {source_designators} to {destination_designators}')
    response = await altium_bridge.execute_command('layout_duplicator_apply', {'source_designators': source_designators, 'destination_designators': destination_designators})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error applying layout duplication: {error_msg}')
        return json.dumps({'success': False, 'error': f'Failed to apply layout duplication: {error_msg}'})
    result = response.get('result', {})
    logger.info(f'Layout duplication applied successfully')
    return json.dumps(result, indent=2)

async def get_pcb_rules(ctx: Context) -> str:
    '\n    Get all design rules from the current Altium PCB\n    \n    Returns:\n        str: JSON array of PCB design rules with their properties\n    '
    logger.info('Getting PCB design rules')
    response = await altium_bridge.execute_command('get_pcb_rules', {})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error getting PCB rules: {error_msg}')
        return json.dumps({'error': f'Failed to get PCB rules: {error_msg}'})
    rules_data = response.get('result', [])
    if not rules_data:
        logger.info('No PCB rules found')
        return json.dumps({'message': 'No PCB rules found in the current document'})
    logger.info(f'Retrieved PCB rules data')
    return json.dumps(rules_data, indent=2)

async def get_pcb_layer_stackup(ctx: Context) -> str:
    '\n    Get the detailed layer stackup information from the current Altium PCB including\n    copper thickness, dielectric materials, constants, and heights\n    \n    Returns:\n        str: JSON object with detailed layer stackup information\n    '
    logger.info('Getting PCB layer stackup information')
    response = await altium_bridge.execute_command('get_pcb_layer_stackup', {})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error getting PCB layer stackup: {error_msg}')
        return json.dumps({'error': f'Failed to get PCB layer stackup: {error_msg}'})
    stackup_data = response.get('result', {})
    if not stackup_data:
        logger.info('No PCB layer stackup found')
        return json.dumps({'message': 'No PCB layer stackup found in the current document'})
    logger.info(f'Retrieved PCB layer stackup data')
    return json.dumps(stackup_data, indent=2)

async def get_output_job_containers(ctx: Context) -> str:
    '\n    Get all available output job containers from a specified OutJob file\n    \n    Args:\n        outjob_path (str): Path to the OutJob file (optional, will use first open OutJob if not provided)\n    \n    Returns:\n        str: JSON array with all output job containers and their properties\n    '
    logger.info('Getting output job containers from the first open OutJob')
    response = await altium_bridge.execute_command('get_output_job_containers', {})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error getting output job containers: {error_msg}')
        return json.dumps({'error': f'Failed to get output job containers: {error_msg}'})
    containers_data = response.get('result', [])
    if not containers_data:
        logger.info('No output job containers found')
        return json.dumps({'message': 'No output job containers found'})
    logger.info(f'Retrieved output job containers data')
    return containers_data

async def run_output_jobs(ctx: Context, container_names: list) -> str:
    '\n    Run specified output job containers\n    \n    Args:\n        container_names (list): List of container names to run\n    \n    Returns:\n        str: JSON object with results of running the output jobs\n    '
    logger.info(f'Running output jobs')
    logger.info(f'Containers to run: {container_names}')
    response = await altium_bridge.execute_command('run_output_jobs', {'container_names': container_names})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error running output jobs: {error_msg}')
        return json.dumps({'error': f'Failed to run output jobs: {error_msg}'})
    result_data = response.get('result', {})
    logger.info(f'Output jobs execution completed')
    if isinstance(result_data, str):
        return result_data
    return json.dumps(result_data, indent=2)

async def create_pcb_footprint(ctx: Context, footprint_name: str, description: str, pads: list, courtyard_x_mm: float=0, courtyard_y_mm: float=0) -> str:
    '\n    Create a new PCB footprint in the currently active PcbLib document.\n    The PcbLib (e.g. Discrete.PcbLib) must be the focused document in Altium.\n\n    Pad format: each element is "pad_number|x_mm|y_mm|width_mm|height_mm|shape"\n                shape options: Rect (default), Round, Oval\n                Coordinates are in mm relative to component origin (0,0).\n                Pin 1 is indicated by a gap in the top-left silkscreen corner.\n\n    Courtyard & silkscreen are auto-generated from pad extents + 0.25 mm margin\n    unless courtyard_x_mm / courtyard_y_mm are provided explicitly (half-dimensions).\n\n    Args:\n        footprint_name (str): Footprint name as it will appear in the library\n        description (str): Description string\n        pads (list): List of pad definitions, e.g. ["1|-0.9|0.55|1.0|0.8|Rect", ...]\n        courtyard_x_mm (float): Half-width of courtyard in mm (0 = auto)\n        courtyard_y_mm (float): Half-height of courtyard in mm (0 = auto)\n\n    Returns:\n        str: JSON object with result\n    '
    logger.info(f'Creating PCB footprint: {footprint_name} with {len(pads)} pads')
    response = await altium_bridge.execute_command('create_pcb_footprint', {'footprint_name': footprint_name, 'description': description, 'pads': pads, 'courtyard_x_mm': courtyard_x_mm, 'courtyard_y_mm': courtyard_y_mm})
    if not response.get('success', False):
        error_msg = response.get('error', 'Unknown error')
        logger.error(f'Error creating footprint: {error_msg}')
        return json.dumps({'success': False, 'error': f'Failed to create footprint: {error_msg}'})
    result = response.get('result', {})
    logger.info(f'Footprint {footprint_name} created successfully')
    return json.dumps(result, indent=2)

async def get_server_status(ctx: Context) -> str:
    'Get the current status of the Altium MCP server'
    status = {'server': 'Running', 'altium_exe': altium_bridge.config.altium_exe_path, 'script_path': altium_bridge.config.script_path, 'altium_found': os.path.exists(altium_bridge.config.altium_exe_path), 'script_found': os.path.exists(altium_bridge.config.script_path)}
    return json.dumps(status, indent=2)

def _mst_length(points: list) -> float:
    """Minimum-spanning-tree length over (x, y) points (Prim's algorithm)."""
    n = len(points)
    if n < 2:
        return 0.0
    import math
    in_tree = [False] * n
    best = [float('inf')] * n
    best[0] = 0.0
    total = 0.0
    for _ in range(n):
        u = min((i for i in range(n) if not in_tree[i]), key=lambda i: best[i])
        in_tree[u] = True
        total += best[u]
        ux, uy = points[u]
        for v in range(n):
            if not in_tree[v]:
                d = math.dist((ux, uy), points[v])
                if d < best[v]:
                    best[v] = d
    return total

def _rotate_offset(dx: float, dy: float, degrees: float) -> tuple:
    """Rotate a rotation-0 pad offset counterclockwise by the given angle."""
    import math
    rad = math.radians(degrees)
    return (dx * math.cos(rad) - dy * math.sin(rad), dx * math.sin(rad) + dy * math.cos(rad))

TOOL_FUNCTIONS = [get_all_component_property_names, get_component_property_values, get_symbol_placement_rules, get_library_symbol_reference, search_library_symbol, create_schematic_symbol, get_schematic_data, get_pcb_layers, set_pcb_layer_visibility, get_component_data, get_selected_components_coordinates, get_all_designators, get_component_pins, run_altium_script, ensure_altium_script_skill, get_footprint_primitives, create_footprints_batch, build_schematic, create_symbols_batch, get_symbol_primitives, get_all_nets, create_net_class, set_component_position, move_components, place_components, get_net_connections, check_orientation, check_placement, get_screenshot, layout_duplicator, layout_duplicator_apply, get_pcb_rules, get_pcb_layer_stackup, get_output_job_containers, run_output_jobs, create_pcb_footprint, get_server_status]

class LegacyCompat:
    """The dispatcher invokes existing EDA MCP tools in the same worker session."""
    def __init__(self, dispatch, config=None):
        self.dispatch = dispatch
        config = config or {}
        runtime = Path(config.get('runtime_dir', os.environ.get('ALTIUM_MCP_RUNTIME', Path.home() / '.altium-suite')))
        self.config = SimpleNamespace(
            altium_exe_path=config.get('altium_exe', os.environ.get('ALTIUM_EXE', '')),
            script_path=config.get('script_path', str(Path(__file__).resolve().parent / 'eda-agent/scripts/altium/Altium_API.PrjScr')),
            exchange_dir=runtime / 'legacy-data')
        self.server = FastMCP('AltiumMCP', instructions='Altium integration through the Model Context Protocol')
        for function in TOOL_FUNCTIONS:
            self.server.add_tool(function)

    async def list_tools(self):
        return await self.server.list_tools()

    async def call_tool(self, name, arguments):
        token = _current.set(self)
        try:
            content = await self.server.call_tool(name, arguments)
            if isinstance(content, tuple):
                return types.CallToolResult(content=content[0], structuredContent=content[1])
            return types.CallToolResult(content=content)
        finally:
            _current.reset(token)

    async def _call(self, name, arguments):
        result = await self.dispatch(name, arguments)
        if isinstance(result, types.CallToolResult):
            if result.isError:
                raise ValueError('; '.join(c.text for c in result.content if c.type == 'text'))
            if result.structuredContent is not None:
                return result.structuredContent
            text = [c.text for c in result.content if c.type == 'text']
            if len(text) != 1:
                raise ValueError('Expected one JSON EDA response')
            return json.loads(text[0])
        if isinstance(result, str):
            return json.loads(result)
        return result

    async def run_script(self, script, timeout_seconds):
        if timeout_seconds <= 0:
            return {'success': False, 'error': 'timeout_seconds must be positive; no script was launched'}
        try:
            return await self._call('_suite_legacy_execute', {'command': 'run_altium_script', 'params': {'script': script, 'timeout_seconds': timeout_seconds}})
        except Exception as error:
            return {'success': False, 'error': str(error)}

    async def _move(self, command, params):
        before = await self._call('pcb_get_components', {})
        components = {item['designator']: item for item in before.get('components', [])}
        if command == 'set_component_position':
            requested = [dict(designator=params['designator'], x=params['x'], y=params['y'], rotation=params['rotation'])]
        elif command == 'move_components':
            requested = []
            for name in params['designators']:
                item = components.get(name)
                if item:
                    legacy = item['legacy_data']
                    requested.append(dict(designator=name,
                        x=legacy['x'] + params['x_offset'], y=legacy['y'] + params['y_offset'],
                        rotation=params['rotation'] if params['rotation'] else -1))
        else:
            requested = []
            for entry in params['placements']:
                name, x, y, rotation, layer = entry.split('|')
                requested.append(dict(designator=name, x=float(x), y=float(y), rotation=float(rotation), side=layer))
        operations = []
        for placement in requested:
            name = placement['designator']
            if ',' in name or '|' in name:
                raise ValueError('Component designator cannot contain the EDA placement separators')
            item = components.get(name)
            if not item:
                continue
            legacy = item['legacy_data']
            operation = dict(designator=name, x=placement['x'] + item['x'] - legacy['x'],
                             y=placement['y'] + item['y'] - legacy['y'])
            if placement.get('rotation', -1) >= 0:
                operation['rotation'] = placement['rotation']
            if placement.get('side'):
                operation['side'] = placement['side']
            operations.append(operation)
        if operations:
            data = await self._call('pcb_import_placement', {'placements': operations})
            if data.get('error') or data.get('failed', 0):
                raise ValueError(str(data.get('error', 'Native component placement failed')))
        names = params['designators'] if command == 'move_components' else [item['designator'] for item in requested]
        missing = [name for name in names if name not in components]
        if command == 'set_component_position':
            if missing:
                return {'success': False, 'error': 'Component not found: ' + missing[0]}
            after = await self._call('pcb_get_components', {})
            current = next(item['legacy_data'] for item in after['components'] if item['designator'] == names[0])
            return {'success': True, 'result': dict(
                designator=names[0], new_x=current['x'], new_y=current['y'], rotation=current['rotation'])}
        if command == 'move_components':
            return {'moved_count': len(operations), 'missing_designators': missing}
        after = await self._call('pcb_get_components', {})
        final = {item['designator']: item['legacy_data'] for item in after.get('components', [])}
        placed = [{key: final[name][key] for key in ('designator', 'x', 'y', 'rotation', 'layer')}
                  for name in names if name in final]
        return {'placed_count': len(operations), 'missing_designators': missing, 'components': placed}

    async def execute_command(self, command, params):
        validate_legacy_json_text(params)
        try:
            data = {}
            if command == 'get_all_nets':
                data = await self._call('pcb_get_nets', {})
                result = data.get('nets', [])
            elif command == 'create_net_class':
                names = params['net_names']
                if not params['class_name']:
                    return {'success': True, 'result': {'success': False, 'error': 'No class name provided'}}
                if not names:
                    return {'success': True, 'result': {'success': False, 'error': 'No net names provided'}}
                # Delphi TStringList.CommaText accepts doubled quotes inside
                # quoted fields, preserving commas within a single net name.
                csv = ','.join('"' + str(name).replace('"', '""') + '"' for name in names) if any(
                    ',' in str(name) or '"' in str(name) for name in names) else ','.join(names)
                data = await self._call('pcb_create_net_class', {'name': params['class_name'], 'nets': csv})
                result = dict(data, success=True)
            elif command in {'get_all_component_data', 'get_selected_components_coordinates'}:
                data = await self._call('pcb_get_components', {})
                result = []
                for item in data.get('components', []):
                    legacy = dict(item['legacy_data'])
                    selected = legacy.pop('selected')
                    if command == 'get_selected_components_coordinates':
                        if not selected:
                            continue
                        legacy.pop('name', None)
                        legacy.pop('description', None)
                    result.append(legacy)
            elif command in {'set_component_position', 'move_components', 'place_components'}:
                result = await self._move(command, params)
            else:
                response = await self._call('_suite_legacy_execute', {'command': command, 'params': params})
                return response
            if isinstance(data, dict) and (data.get('success') is False or data.get('ok') is False or data.get('error')):
                return {'success': False, 'error': data.get('error', data.get('reason', 'EDA operation failed'))}
            return {'success': True, 'result': result}
        except Exception as error:
            return {'success': False, 'error': str(error)}
