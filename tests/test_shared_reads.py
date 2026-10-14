import asyncio
import copy
import json

import pytest
import suite_reads as reads


@pytest.fixture
def data():
    return dict(data_domain='physical_pcb', board_path='C:/test.PcbDoc', truncated=False,
                counts=dict(components=2, pads=3, nets=2, tracks=0, vias=0),
                components=[dict(designator=d, comment='10k', footprint='0603') for d in ('R1', 'R2')],
                pads=[dict(component='R1', pad='1', net='GND'),
                      dict(component='R1', pad='2', net='SIG'),
                      dict(component='R2', pad='1', net='GND')], nets=['GND', 'SIG'])


def make(data, root):
    return reads.capture(data, 1, dict(pid=12, started=1), dict(script_version='test'), root)


def test_same_snapshot_supports_parallel_independent_analysis(data, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    captured = make(data, tmp_path)
    snapshot = reads.read(captured['snapshot_id'], tmp_path)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda c: reads.analyze(snapshot, c), reads.CHECKS))
    assert results[0]['counts']['pads'] == 3
    assert results[1]['single_pad_nets'] == ['SIG']
    assert results[1]['verifies_copper'] is False
    assert results[2]['groups'][0]['quantity'] == 2
    assert not results[2]['variant_fitted_state_known']
    assert snapshot['data'] == data
    assert not snapshot['suitable_as_write_precondition']


def test_reuse_is_explicit_historical_not_automatic_live_cache(data, tmp_path):
    captured = make(data, tmp_path)
    changed = copy.deepcopy(data); changed['components'][0]['comment'] = '20k'
    second = make(changed, tmp_path)
    assert captured['snapshot_id'] != second['snapshot_id']
    assert reads.read(captured['snapshot_id'], tmp_path)['data'] == data
    assert reads.read(second['snapshot_id'], tmp_path)['data'] == changed


@pytest.mark.parametrize('invalid', ['truncated', 'missing_components', 'count', 'schematic'])
def test_partial_or_schematic_data_never_published(data, tmp_path, invalid):
    if invalid == 'truncated': data['truncated'] = True
    elif invalid == 'missing_components': data.pop('components')
    elif invalid == 'count': data['counts']['pads'] = 4
    else: data['data_domain'] = 'schematic'
    with pytest.raises(ValueError): make(data, tmp_path)
    assert not list(tmp_path.glob('*.json'))


def test_hash_and_path_validation(data, tmp_path):
    captured = make(data, tmp_path)
    with pytest.raises(ValueError): reads.read('../other', tmp_path)
    (tmp_path/(captured['snapshot_id']+'.json')).write_text('{}')
    with pytest.raises(ValueError, match='checksum'): reads.read(captured['snapshot_id'], tmp_path)


def test_section_selection_is_batch_read_and_validated(data, tmp_path):
    captured = make(data, tmp_path)
    envelope = reads.read(captured['snapshot_id'], tmp_path)
    result = reads.select_sections(envelope, ['components', 'pads'])
    assert list(result['sections']) == ['components', 'pads']
    assert result['live'] is False
    for invalid in ([], ['pads', 'pads'], ['save']):
        with pytest.raises(ValueError): reads.select_sections(envelope, invalid)


def test_tools_offline_calls_never_touch_native_and_capture_requires_wrapper(data, tmp_path, monkeypatch):
    from eda_agent.tools.registry import ToolRegistry
    from eda_agent.tools import shared_reads
    registry = ToolRegistry(); shared_reads.register_shared_read_tools(registry)
    monkeypatch.setattr(reads, '_root', lambda: tmp_path)
    captured = make(data, tmp_path)
    def forbidden(): pytest.fail('Native bridge reached by offline analysis')
    monkeypatch.setattr(shared_reads, 'get_bridge', forbidden)
    async def scenario():
        a, b = await asyncio.gather(
            registry.call_tool('design_analyze_snapshot', {'snapshot_id':captured['snapshot_id']}),
            registry.call_tool('design_read_snapshot', {'snapshot_id':captured['snapshot_id'], 'sections':['pads','components']}))
        assert a['ok'] and b['ok']
        invalid = await registry.call_tool('design_analyze_snapshot', {'snapshot_id':captured['snapshot_id'], 'checks':[]})
        assert not invalid['ok']
        from types import SimpleNamespace
        monkeypatch.setattr(shared_reads, 'get_bridge', lambda: SimpleNamespace())
        result = await registry.call_tool('pcb_capture_snapshot', {'board_path':str(tmp_path/'empty.PcbDoc')})
        assert not result['ok'] and 'entry point' in result['reason']
    asyncio.run(scenario())
