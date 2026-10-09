import asyncio
from pathlib import Path
import pytest
from eda_agent.project_variants import (ProjectFile, enrich_variants, resolve_variant,
                                      verified_save, edit_variants)
from eda_agent.export.variant_matrix_csv import format_variant_matrix_csv

SAMPLE = ('[Design]\r\nVersion=1.0\r\nCurrentVariant=Full\r\n\r\n'
          '[Document1]\r\nDocumentPath=parts.SchLib\r\n\r\n'
          '[ProjectVariant1]\r\nUniqueId=guid-1\r\nDescription=Full\r\n'
          'VariationCount=1\r\nVariation1=Designator=U1|UniqueId=\\SHEET\\PART|Kind=1|AlternatePart=|FutureField=keep\r\n'
          'ParamVariationCount=1\r\nParamVariation1=UniqueId=\\SHEET\\PART|ParameterName=MPN|VariedValue=other\r\n\r\n'
          '[ProjectVariant2]\r\nUniqueId=guid-2\r\nDescription=Lite\r\nVariationCount=0\r\n\r\n'
          '[FutureSection]\r\nUnknown=unchanged\r\n').encode()
COMPONENTS = [{'unique_id': r'\SHEET\PART', 'designator': 'U1'},
              {'unique_id': r'\SHEET\PART2', 'designator': 'R1'}]


@pytest.mark.parametrize('codec', ['utf-8', 'utf-8-sig', 'utf-16', 'cp1252'])
def test_bulk_update_preserves_encoding_overrides_and_unknown_fields(codec):
    text = SAMPLE.decode().replace('Unknown=unchanged', 'Unknown=café')
    file = ProjectFile(text.encode(codec))
    file.fitted('guid-1', [{'unique_id': r'\SHEET\PART', 'fitted': True},
                           {'unique_id': r'\SHEET\PART2', 'fitted': False}], COMPONENTS)
    result = file.data().decode(codec)
    assert 'Kind=0|AlternatePart=|FutureField=keep' in result
    assert 'ParameterName=MPN|VariedValue=other' in result
    assert '[FutureSection]\r\nUnknown=café\r\n' in result
    assert file.variants()[0]['VariationCount'] == '2'
    assert file.variants()[1]['VariationCount'] == '0'


@pytest.mark.parametrize('updates,components', [
    ([{'unique_id': 'unknown', 'fitted': False}], COMPONENTS),
    ([{'unique_id': r'\SHEET\PART', 'fitted': False}]*2, COMPONENTS),
    ([{'unique_id': r'\SHEET\PART', 'fitted': 'false'}], COMPONENTS),
    ([{'unique_id': r'\SHEET\PART', 'fitted': False}], COMPONENTS+COMPONENTS),
])
def test_invalid_identity_or_state_cannot_modify_file(updates, components):
    file = ProjectFile(SAMPLE)
    with pytest.raises(ValueError):
        file.fitted('guid-1', updates, components)
    assert file.data() == SAMPLE


def test_alternate_parts_are_not_silently_discarded():
    file = ProjectFile(SAMPLE.replace(b'Kind=1|AlternatePart=', b'Kind=2|AlternatePart=Other'))
    with pytest.raises(ValueError, match='Alternate'):
        file.fitted('guid-1', [{'unique_id': r'\SHEET\PART', 'fitted': False}], COMPONENTS)


def test_guid_and_visible_alias_work_but_shared_dm_name_does_not(tmp_path):
    path = tmp_path/'p.PrjPcb';path.write_bytes(SAMPLE)
    listing = enrich_variants({'project_path': str(path), 'variants': [
        {'name': 'Variant', 'description': 'Full'}, {'name': 'Variant', 'description': 'Lite'}]})
    assert resolve_variant(listing['variants'], 'guid-2')['display_name'] == 'Lite'
    assert resolve_variant(listing['variants'], 'Full')['unique_id'] == 'guid-1'
    with pytest.raises(ValueError, match='ambiguous'):
        resolve_variant(listing['variants'], 'Variant')


def test_csv_collisions_never_change_cells():
    import csv, io
    text = format_variant_matrix_csv({'variants': ['A', 'A', 'A [2]', 'Component'],
                                    'rows': [{'designator': 'U1', 'cells': ['Fitted', 'Not Fitted', 'Alternate', 'Fitted']}]})
    rows = list(csv.reader(io.StringIO(text)))
    assert len(set(rows[0])) == 5
    assert rows[1] == ['U1', 'Fitted', 'Not Fitted', 'Alternate', 'Fitted']


class Bridge:
    def __init__(self, path, *, closes=True, save_changes=True):
        self.path = path;self.closes = closes;self.opened = True;self.save_changes = save_changes
        self.calls = []

    async def send_command_async(self, command, params=None):
        self.calls.append(command)
        if command == 'project.save':
            return {'success': True, 'project_path': str(self.path), 'variant_count': 2, 'document_count': 1}
        if command == 'project.get_documents':
            return [{'file_path': str(self.path.parent/'parts.SchLib')}]
        if command == 'project.get_variants':
            return {'count': 2, 'project_path': str(self.path), 'variants': [
                {'name': 'Variant', 'description': 'Full', 'variations': [
                    {'unique_id': r'\SHEET\PART', 'designator': 'U1', 'kind': 'Not Fitted' if self.save_changes else 'Fitted', 'alternate_part': ''}]},
                {'name': 'Variant', 'description': 'Lite', 'variations': []}]}
        if command == 'project.close':
            if self.closes:self.opened = False
            return {'success': self.closes, 'closed': self.closes}
        if command == 'project.open':
            self.opened = True;return {'success': True}
        if command == 'project.get_open_projects':
            return {'projects': [{'project_path': str(self.path)}] if self.opened else []}
        raise AssertionError(command)


@pytest.mark.asyncio
async def test_save_verifies_variant_records_and_library_membership(tmp_path):
    path = tmp_path/'p.PrjPcb';path.write_bytes(SAMPLE)
    bridge = Bridge(path)
    result = await verified_save(bridge, str(path))
    assert result['success'] and result['verified_documents'] == 1
    path.write_bytes(SAMPLE.replace(b'DocumentPath=parts.SchLib', b'DocumentPath=wrong.SchLib'))
    assert not (await verified_save(bridge, str(path)))['success']
    path.write_bytes(SAMPLE)
    assert not (await verified_save(Bridge(path, save_changes=False), str(path)))['success']


@pytest.mark.asyncio
async def test_failed_close_keeps_original_bytes(tmp_path):
    path = tmp_path/'p.PrjPcb';path.write_bytes(SAMPLE)
    bridge = Bridge(path, closes=False)
    result = await edit_variants(bridge, str(path), lambda f: f.create('New'), lambda *args: None)
    assert not result['success'] and path.read_bytes() == SAMPLE
    assert not list(tmp_path.glob('*.bak'))


@pytest.mark.asyncio
async def test_failed_native_verification_restores_and_reopens(tmp_path):
    path = tmp_path/'p.PrjPcb';path.write_bytes(SAMPLE)
    bridge = Bridge(path)
    async def reject(*args):raise RuntimeError('native readback disagrees')
    result = await edit_variants(bridge, str(path), lambda f: f.create('New'), reject)
    assert not result['success'] and result['recovered']
    assert bridge.opened and path.read_bytes() == SAMPLE
    assert Path(result['backup_path']).read_bytes() == SAMPLE


@pytest.mark.asyncio
async def test_external_edit_is_never_overwritten_during_recovery(tmp_path):
    path = tmp_path/'p.PrjPcb';path.write_bytes(SAMPLE)
    bridge = Bridge(path)
    async def reject(*args):
        path.write_bytes(b'external edit')
        raise RuntimeError('native readback disagrees')
    result = await edit_variants(bridge, str(path), lambda f: f.create('New'), reject)
    assert not result['success'] and not result['recovered']
    assert path.read_bytes() == b'external edit'
    assert Path(result['backup_path']).read_bytes() == SAMPLE


@pytest.mark.asyncio
async def test_cancellation_recovers_project_before_propagating(tmp_path):
    path = tmp_path/'p.PrjPcb';path.write_bytes(SAMPLE)
    bridge = Bridge(path)
    async def cancelled(*args):raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await edit_variants(bridge, str(path), lambda f: f.create('New'), cancelled)
    assert bridge.opened and path.read_bytes() == SAMPLE


@pytest.mark.asyncio
async def test_cancellation_while_close_completes_still_reopens(tmp_path):
    path = tmp_path/'p.PrjPcb';path.write_bytes(SAMPLE)
    class CancelClose(Bridge):
        async def send_command_async(self, command, params=None):
            result = await super().send_command_async(command, params)
            if command == 'project.close':
                raise asyncio.CancelledError()
            return result
    bridge = CancelClose(path)
    with pytest.raises(asyncio.CancelledError):
        await edit_variants(bridge, str(path), lambda f: f.create('New'), lambda *args: None)
    assert bridge.opened and path.read_bytes() == SAMPLE
