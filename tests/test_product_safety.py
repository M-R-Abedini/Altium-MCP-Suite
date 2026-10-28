"""Observable pre-dispatch text and disk recovery guarantees."""
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sys

import pytest
from mcp import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from suite_checkpoints import CheckpointStore, SafetyService, is_mutation
from suite_units import electrical_value, normalize_arguments
from suite_stdio import Suite, BackendSpec


@pytest.mark.parametrize('raw,expected', [
    ('10kΩ', '10k'), ('100Ω', '100R'), ('4Ω7', '4R7'), ('4k7Ω', '4k7'),
    ('10µF', '10uF'), ('4.7μH', '4.7uH'), ('±5%', '+/-5%'),
    ('-40–85°C', '-40-85degC'), ('Ohm Ω', 'Ohm Ohm'),
])
def test_electrical_magnitude_is_preserved(raw, expected):
    assert electrical_value(raw) == expected


def test_normalization_never_renames_identifiers_paths_or_input():
    original = {'value': '10kΩ', 'parameters': {'Tolerance': '±1%'}, 'net_name': 'Ω',
                'designator': 'µ1', 'path': 'C:/Ω.SchDoc', 'notes': 'فارسی'}
    before = json.dumps(original)
    output, warnings = normalize_arguments(original)
    assert output['value'] == '10k' and output['parameters']['Tolerance'] == '+/-1%'
    assert {k: output[k] for k in ('net_name', 'designator', 'path', 'notes')} == {k: original[k] for k in ('net_name', 'designator', 'path', 'notes')}
    assert len(warnings) == 2 and json.dumps(original) == before
    assert '10kΩ' not in json.dumps(warnings, ensure_ascii=False)
    with pytest.raises(ValueError, match=r'U\+0641'):
        normalize_arguments({'value': 'فارسی'})


def test_plan_json_value_only():
    output, warnings = normalize_arguments({'plan_json': json.dumps({'parts': [{'value': '1μF', 'ref': 'Ω'}]})})
    assert json.loads(output['plan_json'])['parts'][0] == {'value': '1uF', 'ref': 'Ω'}
    assert len(warnings) == 1


@contextmanager
def available():
    yield


def test_disk_snapshot_restore_and_recovery_no_delete(tmp_path):
    source = tmp_path / 'board.PcbDoc'; source.write_bytes(b'before')
    new = tmp_path / 'new.SchDoc'
    store = CheckpointStore(tmp_path / 'data', restore_guard=available)
    saved = store.create([str(source), str(new)], connectivity={'nets': ['GND']})
    assert saved['connectivity_sha256'] and not saved['unsaved_memory_captured']
    assert store.list()['total'] == 1
    source.write_bytes(b'edited'); new.write_bytes(b'new file')
    response = store.restore(saved['checkpoint_id'])
    assert source.read_bytes() == b'before' and new.read_bytes() == b'new file'
    store.restore(response['recovery_checkpoint'])
    assert source.read_bytes() == b'edited'


def test_digest_corruption_refuses_entire_restore(tmp_path):
    first = tmp_path / 'a.SchDoc'; first.write_bytes(b'A')
    second = tmp_path / 'b.SchDoc'; second.write_bytes(b'B')
    store = CheckpointStore(tmp_path / 'data', restore_guard=available)
    saved = store.create([str(first), str(second)])
    first.write_bytes(b'A2'); second.write_bytes(b'B2')
    (store.root / saved['checkpoint_id'] / '0001.bin').write_bytes(b'bad')
    with pytest.raises(ValueError, match='digest'):
        store.restore(saved['checkpoint_id'])
    assert first.read_bytes() == b'A2' and second.read_bytes() == b'B2'
    with pytest.raises(ValueError):
        store.restore('../../outside')


def test_failed_guard_and_size_limits_do_not_write(tmp_path):
    @contextmanager
    def busy():
        raise RuntimeError('busy')
        yield
    source = tmp_path / 'a.SchDoc'; source.write_bytes(b'old')
    store = CheckpointStore(tmp_path / 'data', restore_guard=busy)
    saved = store.create([str(source)])
    source.write_bytes(b'new')
    with pytest.raises(RuntimeError, match='busy'):
        store.restore(saved['checkpoint_id'])
    assert source.read_bytes() == b'new'
    with pytest.raises(ValueError, match='limit'):
        CheckpointStore(tmp_path / 'small', max_bytes=1).create([str(source)])
    with pytest.raises(ValueError, match='absolute'):
        store.create(['relative.SchDoc'])


@pytest.mark.parametrize('name,args,expected', [
    ('sch_place_wire', {}, True), ('obj_batch_modify', {}, True), ('create_symbols_batch', {}, True),
    ('proj_get_nets', {'force_recompile': True}, True), ('proj_get_nets', {}, False),
    ('pcb_get_pad_connectivity', {}, False), ('obj_query', {}, False),
    ('tool_invoke', {'name': 'obj_batch_modify'}, True), ('app_run_menu', {}, True),
])
def test_conservative_mutation_classification(name, args, expected):
    assert is_mutation(name, args) is expected


@pytest.mark.parametrize('name', ['proj_get_connectivity', 'proj_get_connectivity_many'])
def test_forced_connectivity_compile_is_a_persisting_operation(name):
    assert not is_mutation(name, {})
    assert is_mutation(name, {'force_recompile': True})


def test_parameter_assignment_values_are_normalized():
    output, notices = normalize_arguments({'assignments': [{'param_name': 'Resistance', 'param_value': '100Ω'}]})
    assert output['assignments'][0] == {'param_name': 'Resistance', 'param_value': '100R'}
    assert len(notices) == 1


def test_offline_design_authoring_never_requires_live_altium():
    for name in ('design_add_part', 'design_add_circuit_block', 'design_compose_netlist',
                 'design_layout_schematic', 'design_apply_hierarchy', 'design_job_result'):
        assert not is_mutation(name, {})


def test_document_scope_and_project_definition_are_snapshotted(tmp_path):
    active = tmp_path / 'active.SchDoc'; active.write_bytes(b'active')
    external = tmp_path / 'external.SchDoc'; external.write_bytes(b'external')
    project = tmp_path / 'project.PrjPcb'; project.write_bytes(b'project')
    async def read(name, args):
        if name == 'app_context':
            return {'bridge_answering': True, 'script_version_match': True,
                    'active_document': {'file_path': str(active)}}
        if name == 'app_list_documents':
            return [{'file_path': str(active), 'project_path': str(project)}]
        if name == 'proj_get_focused':
            return {'project_path': str(project)}
        raise AssertionError('Unexpected preflight operation')
    service = SafetyService(tmp_path / 'data', read)
    checkpoint = asyncio.run(service.before('obj_batch_modify', {'scope': 'doc:' + str(external)}))
    assert {entry['path'] for entry in checkpoint['files']} == {str(active), str(external), str(project)}
    with pytest.raises(ValueError, match='opaque'):
        asyncio.run(service.before('obj_run_process', {}))


@pytest.mark.parametrize('arguments,files', [
    ({'library_path': 'relative.SchLib'}, None),
    ({'scope': 'doc:relative.SchDoc'}, None),
    ({}, ['relative.SchDoc']),
])
def test_relative_targets_fail_before_native_preflight(tmp_path, arguments, files):
    async def never_read(name, args):
        raise AssertionError('Unresolved target must not reach native code')
    service = SafetyService(tmp_path / 'data', never_read)
    with pytest.raises(ValueError, match='absolute'):
        asyncio.run(service.before('obj_run_process', arguments, files))
    assert not (tmp_path / 'data').exists()


def test_preflight_snapshots_active_free_library_and_explicit_targets(tmp_path):
    source = tmp_path / 'free.SchLib'; source.write_bytes(b'library')
    calls = []
    async def read(name, args):
        calls.append(name)
        if name == 'app_context':
            return {'bridge_answering': True, 'script_version_match': True,
                    'active_document': {'file_path': str(source)}, 'unsaved_detection_complete': False}
        if name == 'app_list_documents':
            return []
        if name == 'proj_get_focused':
            raise RuntimeError('No focused project for a free library')
        raise AssertionError('Must not compile or save')
    service = SafetyService(tmp_path / 'data', read)
    checkpoint = asyncio.run(service.before('lib_add_component', {}))
    assert checkpoint['files'][0]['sha256']
    assert checkpoint['warnings'] and checkpoint['connectivity_status'] == 'unavailable'
    assert calls == ['app_context', 'app_list_documents', 'proj_get_focused']


def test_gateway_sanitizes_before_dispatch_preserves_images_and_meta(tmp_path):
    class Worker:
        def __init__(self):
            self.spec = BackendSpec('eda', 'unused')
            self.calls = []
        async def request(self, method, **kwargs):
            self.calls.append(kwargs)
            return types.CallToolResult(content=[types.ImageContent(type='image', data='aA==', mimeType='image/png')],
                                        structuredContent={'ok': True}, _meta={'existing': 'keep'})
    async def check():
        suite = Suite([BackendSpec('eda', 'unused')])
        worker = Worker(); suite.workers['eda'] = worker
        suite._catalogs['eda'] = [types.Tool(name='sch_set_value', inputSchema={'type': 'object', 'properties': {'value': {'type': 'string'}}})]
        response = await suite.invoke('eda__sch_set_value', {'value': '100Ω'})
        assert worker.calls[0]['arguments'] == {'value': '100R'}
        assert response.content[0].type == 'image' and response.meta['existing'] == 'keep'
        assert response.meta['altium_suite']['normalization_warnings']
    asyncio.run(check())


def test_default_product_discovery_stays_lazy_and_private(tmp_path):
    from datetime import timedelta
    import os
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    root = Path(__file__).resolve().parents[1]
    empty = tmp_path / 'backends.json'; empty.write_text('{"backends":{}}')
    async def check():
        params = StdioServerParameters(command=sys.executable,
                    args=[str(root / 'suite_stdio.py'), '--backend-config', str(empty)],
                    env={**os.environ, 'ALTIUM_SUITE_PLUGIN_DATA': str(tmp_path / 'private')})
        async with stdio_client(params) as streams:
            async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=20)) as client:
                await client.initialize()
                assert len((await client.list_tools()).tools) == 3
                for backend, expected in [('suite', 'suite__restore_checkpoint'),
                                          ('supply', 'supply__audit_bom_availability'),
                                          ('telemetry', 'telemetry__status')]:
                    response = await client.call_tool('suite_catalog', {'backend': backend, 'with_schema': True})
                    assert not response.isError and response.structuredContent['complete']
                    assert expected in {tool['name'] for tool in response.structuredContent['tools']}
                listed = await client.call_tool('suite_invoke', {'name': 'suite__list_checkpoints'})
                assert listed.structuredContent['total'] == 0
    asyncio.run(check())
    assert not (tmp_path / 'private/telemetry').exists()
