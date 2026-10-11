"""API validation and SVG checks paired with the AD26 native acceptance run."""
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock

import pytest
from eda_agent.tools import generic
from eda_agent.render import render_sch_svg, SchRenderOptions
from eda_agent.render.sch_svg import _bbox


def tools(monkeypatch):
    registered = {}
    class MCP:
        def tool(self, *args, **kwargs):
            def register(fn):
                registered[fn.__name__] = fn
                return fn
            return register
    bridge = AsyncMock()
    bridge.send_command_async.return_value = {'success': True}
    monkeypatch.setattr(generic, 'get_bridge', lambda: bridge)
    generic.register_generic_tools(MCP())
    return registered, bridge


@pytest.mark.asyncio
@pytest.mark.parametrize('stamps', [[], [{}], [{'designator': 'U1'}],
    [{'designator': 'U1', 'Value': ''}],
    [{'designator': 'U1', 'Value': 'A'}, {'designator': 'U1', 'Value': 'B'}],
    [{'designator': 'U1', 'invalid;key': 'A'}]])
async def test_invalid_stamps_never_reach_native_code(monkeypatch, stamps):
    api, bridge = tools(monkeypatch)
    result = await api['sch_set_components_parameters'](stamps)
    assert result['ok'] is False
    bridge.send_command_async.assert_not_called()


@pytest.mark.asyncio
async def test_footprint_stamp_preserves_explicit_target(monkeypatch):
    api, bridge = tools(monkeypatch)
    await api['sch_set_components_parameters']([
        {'designator': 'U1', 'Footprint': 'SOIC-8', 'Value': 'LM358'}],
        sheet_path='D:/design/Audio.SchDoc')
    bridge.send_command_async.assert_awaited_once_with(
        'generic.set_sch_components_parameters',
        {'stamps': 'designator=U1;Footprint=SOIC-8;Value=LM358',
         'sheet_path': 'D:/design/Audio.SchDoc'}, timeout=60.0)


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['../Audio', r'..\Audio', 'Audio:1', 'Audio.', 'Audio ', ''])
async def test_invalid_sheet_names_cannot_create_or_overwrite_files(monkeypatch, name):
    api, bridge = tools(monkeypatch)
    assert (await api['proj_add_sheet'](name=name))['success'] is False
    bridge.send_command_async.assert_not_called()


@pytest.mark.asyncio
async def test_sheet_creation_carries_project_identity(monkeypatch):
    api, bridge = tools(monkeypatch)
    await api['proj_add_sheet'](name='Audio', project_path='D:/design/Board.PrjPcb')
    bridge.send_command_async.assert_awaited_once_with('generic.add_sheet',
        {'name': 'Audio', 'project_path': 'D:/design/Board.PrjPcb'})


@pytest.mark.parametrize('flip', [False, True])
def test_notes_frames_and_labels_are_escaped_and_inside_viewport(flip):
    geometry = {'annotations': [
        {'kind': 'frame', 'x': 8000, 'y': 5000, 'x2': 10000, 'y2': 6000,
         'text': 'Frame & <test>\nSecond line'},
        {'kind': 'label', 'x': 400, 'y': 7000, 'text': 'Label & review'},
        {'kind': 'frame', 'x': 11000, 'y': 6500, 'x2': 12000, 'y2': 7500,
         'text': 'Note <review>'}]}
    assert _bbox(geometry) == (400, 5000, 12000, 7500)
    svg = render_sch_svg(geometry, SchRenderOptions(flip_y=flip))
    root = ET.fromstring(svg)
    visible = list(root.iter('{http://www.w3.org/2000/svg}text'))
    assert {'Frame & <test>', 'Second line', 'Label & review', 'Note <review>'} <= {
        node.text for node in visible}
    assert 'Frame &amp; &lt;test&gt;' in svg


def test_sheet_symbol_bounds_use_top_left_native_anchor():
    assert _bbox({'sheet_symbols': [{'x': 3000, 'y': 5000, 'w': 2000, 'h': 2000}]}) == (
        3000, 3000, 5000, 5000)
