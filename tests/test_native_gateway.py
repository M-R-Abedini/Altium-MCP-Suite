"""Gateway migration regressions without retired executables or an editor.
SPDX-License-Identifier: MIT
"""
import asyncio
import json
from pathlib import Path
import sys

from mcp import types
import pytest

from suite_stdio import BackendSpec, Suite, result

ROOT = Path(__file__).resolve().parents[1]


def native_specs(tmp_path, **libraries):
    config = tmp_path / 'allowed.json'
    config.write_text(json.dumps({'allowed_paths': [str(tmp_path)]}), encoding='utf-8')
    return [BackendSpec('eda', str(tmp_path / 'absent-eda.exe')),
            BackendSpec('legacy', str(tmp_path / 'absent-legacy.exe')),
            BackendSpec('libraries', str(tmp_path / 'absent-library.exe'), [str(config)], **libraries)]


def test_migrated_executables_are_never_started_and_exact_contracts_are_retained(tmp_path):
    async def check():
        suite = Suite(native_specs(tmp_path))
        suite.install_native_backends(tmp_path / 'private')
        try:
            assert set(suite.workers) == {'eda'}
            for backend, contract in [('legacy', 'legacy-tools.json'), ('libraries', 'libraries-v0.3.0.json')]:
                tools = await suite.tools(backend)
                expected = json.loads((ROOT / 'contracts' / contract).read_text(encoding='utf-8'))['tools']
                assert {t.name: t.inputSchema for t in tools} == {t['name']: t['inputSchema'] for t in expected}
                assert suite.status()['backends'][backend]['in_process']
            path = str(tmp_path / 'test.SchLib')
            result = await suite.invoke('libraries__write_schlib', {'filepath': path, 'symbols': [{'name': 'Made'}]})
            assert not result.isError
            read = await suite.invoke('libraries__list_components', {'filepath': path})
            assert read.structuredContent['components'] == ['Made']
            assert suite.workers['eda'].task is None
        finally:
            await suite.close()
    asyncio.run(check())


def test_migrated_library_tool_restrictions_prevent_mutation(tmp_path):
    async def check():
        suite = Suite(native_specs(tmp_path, enabled_tools=['list_components']))
        suite.install_native_backends(tmp_path / 'private')
        try:
            assert [t.name for t in await suite.tools('libraries')] == ['list_components']
            path = tmp_path / 'never-written.SchLib'
            with pytest.raises(ValueError, match='disabled'):
                await suite.invoke('libraries__write_schlib', {'filepath': str(path), 'symbols': [{'name': 'No'}]})
            assert not path.exists()
        finally:
            await suite.close()
    asyncio.run(check())


def test_disabled_migrated_library_does_not_require_a_config_file(tmp_path):
    async def check():
        suite = Suite([BackendSpec('libraries', 'absent.exe', [str(tmp_path / 'absent.json')], enabled=False)])
        suite.install_native_backends(tmp_path / 'private')
        try:
            assert await suite.tools('libraries') == []
            assert suite.status()['backends']['libraries']['enabled'] is False
        finally:
            await suite.close()
    asyncio.run(check())


def test_cancelled_in_process_operation_is_drained_before_close():
    async def check():
        started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class Component:
            def list_tools(self):
                return [{'name': 'write', 'inputSchema': {'type': 'object'}}]
            async def call_tool(self, name, args):
                started.set()
                await release.wait()
                finished.set()
                return {'status': 'success'}
        suite = Suite([], components={'libraries': Component()})
        task = asyncio.create_task(suite.invoke('libraries__write'))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        closing = asyncio.create_task(suite.close())
        await asyncio.sleep(0)
        assert not finished.is_set() and not closing.done()
        release.set()
        await asyncio.wait_for(closing, 1)
        assert finished.is_set()
    asyncio.run(check())


def test_private_eda_executor_is_not_discoverable_or_directly_invocable(tmp_path):
    async def check():
        suite = Suite([BackendSpec('eda', sys.executable)])
        calls = []
        async def request(method, **args):
            calls.append((method, args))
            return types.ListToolsResult(tools=[
                types.Tool(name='pcb_get_nets', inputSchema={'type': 'object'}),
                types.Tool(name='_suite_legacy_execute', inputSchema={'type': 'object'})])
        suite.workers['eda'].request = request
        assert [t.name for t in await suite.tools('eda')] == ['pcb_get_nets']
        with pytest.raises(ValueError, match='disabled'):
            await suite.invoke('eda___suite_legacy_execute', {'command': 'anything', 'params': {}})
        assert [method for method, _ in calls] == ['list_tools']
        await suite.close()
    asyncio.run(check())


@pytest.mark.parametrize('name', ['design_connectivity_snapshot', 'design_diff_connectivity'])
def test_offline_connectivity_verification_never_requests_an_editor_checkpoint(name):
    from suite_checkpoints import is_mutation
    assert not is_mutation(name, {})


def placement_suite(tmp_path, on_read=None):
    """Stateful native stand-in: relative moves must accumulate, not overwrite."""
    suite = Suite(native_specs(tmp_path))
    suite.install_native_backends(tmp_path / 'private')
    state = {'x': 1000, 'history': []}
    async def request(method, **args):
        if method == 'list_tools':
            return types.ListToolsResult(tools=[
                types.Tool(name='pcb_get_components', inputSchema={'type': 'object'}),
                types.Tool(name='pcb_import_placement', inputSchema={'type': 'object'})])
        name = args['name']
        if name == 'pcb_get_components':
            x = state['x']
            state['history'].append(('read', x))
            if on_read is not None:
                await on_read()
            else:
                await asyncio.sleep(.01)
            return result({'components': [{'designator': 'R1', 'x': x, 'y': 2000,
                'rotation': 0, 'legacy_data': {'designator': 'R1', 'x': x - 1000,
                'y': 0, 'rotation': 0, 'layer': 'Top Layer'}}]})
        assert name == 'pcb_import_placement'
        state['x'] = args['arguments']['placements'][0]['x']
        state['history'].append(('write', state['x']))
        return result({'success': True})
    suite.workers['eda'].request = request
    return suite, state


def test_concurrent_legacy_relative_moves_accumulate_across_the_whole_sequence(tmp_path):
    async def check():
        suite, state = placement_suite(tmp_path)
        try:
            moves = await asyncio.wait_for(asyncio.gather(*[
                suite.invoke('legacy__move_components', {'cmp_designators': ['R1'], 'x_offset': 1,
                                                        'y_offset': 0}) for _ in range(2)]), 2)
            assert all(json.loads(reply.content[0].text)['success'] for reply in moves)
            assert state['x'] == 1002
            assert state['history'] == [('read', 1000), ('write', 1001), ('read', 1001), ('write', 1002)]
        finally:
            await suite.close()
    asyncio.run(check())


def test_cancelled_legacy_sequence_finishes_before_an_eda_call_can_interleave(tmp_path):
    async def check():
        started, release = asyncio.Event(), asyncio.Event()
        async def on_read():
            started.set()
            await release.wait()
        suite, state = placement_suite(tmp_path, on_read)
        try:
            move = asyncio.create_task(suite.invoke('legacy__move_components', {
                'cmp_designators': ['R1'], 'x_offset': 1, 'y_offset': 0}))
            await asyncio.wait_for(started.wait(), 1)
            move.cancel()
            with pytest.raises(asyncio.CancelledError):
                await move
            read = asyncio.create_task(suite.invoke('eda__pcb_get_components'))
            await asyncio.sleep(0)
            assert state['history'] == [('read', 1000)] and not read.done()
            release.set()
            await asyncio.wait_for(read, 1)
            assert state['history'] == [('read', 1000), ('write', 1001), ('read', 1001)]
        finally:
            release.set()
            await suite.close()
    asyncio.run(check())


def test_child_task_does_not_inherit_reentrant_native_sequence_ownership(tmp_path):
    async def check():
        suite, state = placement_suite(tmp_path)
        try:
            async with suite._native_sequence():
                read = asyncio.create_task(suite.invoke('eda__pcb_get_components'))
                await asyncio.sleep(0)
                assert state['history'] == [] and not read.done()
            await asyncio.wait_for(read, 1)
            assert state['history'] == [('read', 1000)]
        finally:
            await suite.close()
    asyncio.run(check())


def test_legacy_values_are_normalized_before_batch_validation_and_native_metadata_survives(tmp_path):
    async def check():
        suite = Suite(native_specs(tmp_path))
        suite.install_native_backends(tmp_path / 'private')
        seen = []
        async def request(method, **args):
            assert method == 'call_tool' and args['name'] == '_suite_legacy_execute'
            spec = Path(args['arguments']['params']['spec_file'])
            seen.append(spec.read_text(encoding='cp1252'))
            assert args['meta'] == {'progressToken': 'legacy-test'}
            return result({'success': True, 'result': {'placed_count': 1}}).model_copy(update={'meta': {
                'native_evidence': 'retained', 'altium_suite': {'checkpoint': {'id': 'observed-checkpoint'}}}})
        suite.workers['eda'].request = request
        arguments = {'parts': [{'designator': 'R1', 'symbol_library': 'Synthetic.SchLib',
                               'symbol': 'R', 'x': 0, 'y': 0, 'comment': '10kΩ'}]}
        try:
            response = await suite.invoke('legacy__build_schematic', arguments,
                                         meta={'progressToken': 'legacy-test'})
            assert not response.isError and seen
            assert '10k' in seen[0] and 'Ω' not in seen[0]
            assert arguments['parts'][0]['comment'] == '10kΩ'
            assert response.meta['native_evidence'] == 'retained'
            info = response.meta['altium_suite']
            assert info['checkpoint']['id'] == 'observed-checkpoint'
            assert info['normalization_warnings'][0]['field'] == 'arguments.parts[0].comment'
        finally:
            await suite.close()
    asyncio.run(check())
