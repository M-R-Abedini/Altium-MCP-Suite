"""Independent service regression cases using only synthetic library data.

SPDX-License-Identifier: MIT
"""
import asyncio
import base64
import copy
import json
from pathlib import Path

import pytest

from suite_libraries_native import NativeLibraries, list_tools
from suite_ole import read_streams, write_streams


def good(service, name, arguments):
    result = service.call_tool(name, arguments)
    assert result.get("status") != "error", (name, result)
    return result


@pytest.fixture
def libraries(tmp_path):
    service = NativeLibraries(data_dir=tmp_path / "state", allowed_paths=[tmp_path])
    sch = tmp_path / "source.SchLib"
    pcb = tmp_path / "source.PcbLib"
    good(service, "write_schlib", {"filepath": str(sch), "symbols": [
        {"name": "A", "description": "synthetic symbol", "pins": [
            {"designator": "1", "name": "IN", "x": 0, "y": 0, "length": 20, "orientation": "right"}]},
        {"name": "B", "description": "second symbol"}]})
    good(service, "write_pcblib", {"filepath": str(pcb), "auto_designator": False, "footprints": [
        {"name": "A", "description": "synthetic footprint", "pads": [
            {"designator": "1", "x": 0, "y": 0, "width": 1, "height": 2, "shape": "rectangle", "layer": "Top Layer"}],
         "tracks": [{"x1": -2, "y1": 3, "x2": 2, "y2": 3, "width": 0.2, "layer": "Top Overlay"}]},
        {"name": "B"}]})
    return service, sch, pcb


TOOLS = [t["name"] for t in list_tools()]


@pytest.mark.parametrize("tool", TOOLS)
def test_every_released_tool_has_a_functional_synthetic_case(libraries, tmp_path, monkeypatch, tool):
    service, sch, pcb = libraries
    s, p = str(sch), str(pcb)
    target = str(tmp_path / "target.SchLib")
    if tool == "write_schlib":
        result = good(service, tool, {"filepath": target, "symbols": [{"name": "Created"}]})
        assert good(service, "list_components", {"filepath": target})["components"] == ["Created"]
    elif tool == "write_pcblib":
        target = str(tmp_path / "target.PcbLib")
        good(service, tool, {"filepath": target, "footprints": [{"name": "Created"}], "auto_designator": False})
        assert good(service, "list_components", {"filepath": target})["components"] == ["Created"]
    elif tool in ("read_pcblib", "read_schlib"):
        args = {"filepath": p if tool == "read_pcblib" else s, "limit": 1, "offset": 1}
        result = good(service, tool, args)
        assert result["returned_count"] == 1 and result["total_count"] == 2
        assert result["footprints" if tool == "read_pcblib" else "symbols"][0]["name"] == "B"
    elif tool == "list_components":
        result = good(service, tool, {"filepath": s, "include_metadata": True})
        assert result["components"][0]["pin_count"] == 1
    elif tool == "get_component":
        assert good(service, tool, {"filepath": p, "component_name": "a"})["component"]["name"] == "A"
    elif tool == "component_exists":
        result = good(service, tool, {"filepath": s, "component_names": ["a", "Absent"]})
        assert [r["exists"] for r in result["results"]] == [True, False]
    elif tool == "extract_style":
        result = good(service, tool, {"filepath": p})
        assert result["style"]["track_widths_by_layer"]["Top Overlay"]["count"] == 1
    elif tool == "validate_library":
        result = good(service, tool, {"filepath": s})
        assert result["warning_count"] >= 1 and result["error_count"] == 0
    elif tool == "export_library":
        result = good(service, tool, {"filepath": s, "format": "json"})
        assert [c["name"] for c in result["symbols"]] == ["A", "B"]
        assert "A,synthetic symbol" in good(service, tool, {"filepath": s, "format": "csv"})["csv"]
    elif tool == "import_library":
        exported = good(service, "export_library", {"filepath": s, "format": "json"})
        good(service, tool, {"output_path": target, "json_data": exported})
        assert good(service, "list_components", {"filepath": target})["components"] == ["A", "B"]
    elif tool == "write_libpkg":
        project = tmp_path / "set.LibPkg"
        good(service, tool, {"filepath": str(project), "documents": [s, p]})
        assert "DocumentPath=source.SchLib" in project.read_text()
    elif tool == "delete_component":
        result = good(service, tool, {"filepath": s, "component_names": ["a", "Absent"]})
        assert result["deleted_count"] == 1
        assert good(service, "list_components", {"filepath": s})["components"] == ["B"]
    elif tool == "copy_component":
        good(service, tool, {"filepath": s, "source_name": "A", "target_name": "Copy"})
        assert good(service, "component_exists", {"filepath": s, "component_names": ["Copy"]})["all_exist"]
    elif tool == "rename_component":
        good(service, tool, {"filepath": s, "old_name": "A", "new_name": "Renamed"})
        assert good(service, "list_components", {"filepath": s})["components"] == ["Renamed", "B"]
    elif tool == "bulk_rename":
        result = good(service, tool, {"filepath": s, "pattern": "^(A|B)$", "replacement": "Part_$1"})
        assert result["renamed_count"] == 2
        assert good(service, "list_components", {"filepath": s})["components"] == ["Part_A", "Part_B"]
    elif tool == "copy_component_cross_library":
        good(service, tool, {"source_filepath": s, "target_filepath": target, "component_name": "A"})
        assert good(service, "get_component", {"filepath": target, "component_name": "A"})["component"]["pins"][0]["designator"] == "1"
    elif tool == "merge_libraries":
        result = good(service, tool, {"source_filepaths": [s, s], "target_filepath": target, "on_duplicate": "rename"})
        assert result["merged_count"] == 4 and result["renamed_count"] == 2
        assert good(service, "list_components", {"filepath": target})["total_count"] == 4
    elif tool == "reorder_components":
        good(service, tool, {"filepath": s, "component_order": ["B"]})
        assert good(service, "list_components", {"filepath": s})["components"] == ["B", "A"]
    elif tool == "update_component":
        component = good(service, "get_component", {"filepath": s, "component_name": "A"})["component"]
        component["description"] = "changed"
        good(service, tool, {"filepath": s, "component_name": "A", "symbol": component})
        assert good(service, "get_component", {"filepath": s, "component_name": "A"})["component"]["description"] == "changed"
    elif tool == "search_components":
        result = good(service, tool, {"filepaths": [s, p], "pattern": "A"})
        assert result["matches_found"] == 2 and result["components_searched"] == 4
    elif tool in ("render_footprint", "render_symbol"):
        result = good(service, tool, {"filepath": p if tool == "render_footprint" else s, "component_name": "A"})
        assert "Legend:" in result["render"] and "A" in result["render"] and "1" in result["render"]
    elif tool == "manage_schlib_parameters":
        good(service, tool, {"filepath": s, "component_name": "A", "operation": "add", "parameter_name": "Value", "value": "100"})
        assert good(service, tool, {"filepath": s, "component_name": "A", "operation": "get", "parameter_name": "value"})["parameter"]["value"] == "100"
    elif tool == "manage_schlib_footprints":
        good(service, tool, {"filepath": s, "component_name": "A", "operation": "add", "footprint_name": "A"})
        assert good(service, tool, {"filepath": s, "component_name": "A", "operation": "list"})["footprints"][0]["name"] == "A"
    elif tool == "batch_update":
        good(service, tool, {"filepath": p, "operation": "update_track_width", "parameters": {"from_width": 0.2, "to_width": 0.3}})
        assert good(service, "get_component", {"filepath": p, "component_name": "A"})["component"]["tracks"][0]["width"] == pytest.approx(0.3, abs=0.00001)
    elif tool == "update_pad":
        good(service, tool, {"filepath": p, "component_name": "A", "designator": "1", "updates": {"width": 1.5}})
        assert good(service, "get_component", {"filepath": p, "component_name": "A"})["component"]["pads"][0]["width"] == pytest.approx(1.5, abs=0.00001)
    elif tool == "update_primitive":
        good(service, tool, {"filepath": p, "component_name": "A", "primitive_type": "track", "index": 0, "updates": {"layer": "Bottom Overlay"}})
        assert good(service, "get_component", {"filepath": p, "component_name": "A"})["component"]["tracks"][0]["layer"] == "Bottom Overlay"
    elif tool == "diff_libraries":
        assert good(service, tool, {"filepath_a": s, "filepath_b": s})["summary"]["unchanged_count"] == 2
    elif tool == "compare_components":
        assert good(service, tool, {"filepath_a": s, "component_a": "A", "filepath_b": s, "component_b": "A"})["identical"]
    elif tool == "list_backups":
        good(service, "rename_component", {"filepath": s, "old_name": "A", "new_name": "BeforeBackup"})
        assert good(service, tool, {"filepath": s})["backup_count"] == 1
    elif tool == "restore_backup":
        original = sch.read_bytes()
        good(service, "rename_component", {"filepath": s, "old_name": "A", "new_name": "BeforeRestore"})
        result = good(service, tool, {"filepath": s})
        assert sch.read_bytes() == original and Path(result["pre_restore_backup"]).is_file()
    elif tool == "extract_step_model":
        # Isolate extraction from binary-model discovery, whose codec is tested
        # separately. The source is a synthetic ISO-10303 text payload.
        encoded = base64.b64encode(b"ISO-10303-21;\nEND-ISO-10303-21;\n").decode()
        monkeypatch.setattr(service, "_read", lambda path: {"footprints": [], "embedded_models": {"model": encoded}})
        output = tmp_path / "extracted.step"
        result = good(service, tool, {"filepath": p, "output_path": str(output)})
        assert output.read_bytes().startswith(b"ISO-10303-21;") and result["size_bytes"] > 0
    elif tool == "repair_library":
        monkeypatch.setattr(service, "_read", lambda path: {"footprints": [], "embedded_models": {"orphan": ""}})
        result = good(service, tool, {"filepath": p, "dry_run": True})
        assert result["repairs"]["orphaned_models_removed"] == 1
    else:
        pytest.fail(f"No behavioral case for {tool}")


def test_contract_has_exactly_34_unique_tools():
    assert len(TOOLS) == len(set(TOOLS)) == 34
    observed = json.loads((Path(__file__).parents[1] / "contracts" / "libraries-v0.3.0.json").read_text())
    assert list_tools() == observed["tools"]


def test_ole_small_large_empty_nested_and_difat(tmp_path):
    target = tmp_path / "container.cfb"
    streams = {"FileHeader": b"header", "Library/Data": b"abc" * 199,
               "Library/Large": b"x" * (8 * 1024 * 1024), "Empty": b""}
    streams.update({f"Folder/Item{i}": bytes([i]) * (i * 57) for i in range(40)})
    write_streams(target, streams)
    assert read_streams(target) == streams


def test_deny_empty_roots_escape_and_invalid_arguments(tmp_path):
    service = NativeLibraries(allowed_paths=[tmp_path / "allowed"])
    assert service.call_tool("write_schlib", {"filepath": str(tmp_path / "escape.SchLib"), "symbols": []})["status"] == "error"
    assert NativeLibraries().call_tool("list_backups", {"filepath": str(tmp_path / "x.SchLib")})["status"] == "error"
    assert service.call_tool("read_schlib", {"filepath": "x", "limit": 0})["status"] == "error"
    assert service.call_tool("read_schlib", {})["status"] == "error"
    assert service.call_tool("unknown", {})["status"] == "error"


def test_dry_run_collision_and_rejected_update_leave_bytes_unchanged(libraries):
    service, sch, pcb = libraries
    before = sch.read_bytes()
    assert service.call_tool("rename_component", {"filepath": str(sch), "old_name": "A", "new_name": "B"})["status"] == "error"
    good(service, "delete_component", {"filepath": str(sch), "component_names": ["A"], "dry_run": True})
    assert sch.read_bytes() == before
    before = pcb.read_bytes()
    result = service.call_tool("update_primitive", {"filepath": str(pcb), "component_name": "A", "primitive_type": "track", "index": 0, "updates": {"text": "unsupported"}})
    assert result["status"] == "error" and pcb.read_bytes() == before


def test_schlib_unknown_stream_survives_mutation(tmp_path):
    service = NativeLibraries(allowed_paths=[tmp_path])
    path = tmp_path / "unknown.SchLib"
    good(service, "write_schlib", {"filepath": str(path), "symbols": [{"name": "A"}]})
    streams = read_streams(path)
    streams["PrivateExtension/Unmodelled"] = bytes(range(256)) * 7
    write_streams(path, streams)
    good(service, "rename_component", {"filepath": str(path), "old_name": "A", "new_name": "B"})
    assert read_streams(path)["PrivateExtension/Unmodelled"] == streams["PrivateExtension/Unmodelled"]


def test_async_adapter(tmp_path):
    service = NativeLibraries(allowed_paths=[tmp_path])
    result = asyncio.run(service.call_tool_async("list_backups", {"filepath": str(tmp_path / "a.SchLib")}))
    assert result["backup_count"] == 0


def test_stack_updates_follow_shared_values_and_keep_layer_overrides():
    pad = {"stack_mode": "top_middle_bottom", "width": 1, "height": 2, "shape": "rectangle",
           "per_layer_sizes": [{"width": 1, "height": 2}, {"width": 4, "height": 2}, {"width": 1, "height": 5}]}
    NativeLibraries._stack_update(pad, {"width": 3, "height": 6})
    assert pad["per_layer_sizes"] == [{"width": 3, "height": 6}, {"width": 4, "height": 6}, {"width": 3, "height": 5}]


@pytest.mark.parametrize("operation", ["copy_component_cross_library", "merge_libraries"])
def test_cross_library_different_fonts_merge_and_remap_without_changing_existing_symbols(tmp_path, operation):
    import suite_schlib_codec as codec
    source, target = tmp_path / "source.SchLib", tmp_path / "target.SchLib"
    codec.write_library(source, [{"name": "Imported", "labels": [{"x": 0, "y": 0, "text": "custom font", "font_id": 1}]}])
    header = codec.read_library_header(source)
    for pair in header:
        if pair[0] == "FontName1":
            pair[1] = "Arial"
    codec.write_library(source, codec.read_library(source)["symbols"], library_header_params=header)
    codec.write_library(target, [{"name": "Existing", "labels": [{"x": 0, "y": 0, "text": "existing font", "font_id": 1}]}])
    original_source = source.read_bytes()
    service = NativeLibraries(allowed_paths=[tmp_path])
    arguments = {"target_filepath": str(target)}
    if operation == "merge_libraries":
        arguments["source_filepaths"] = [str(source)]
    else:
        arguments.update(source_filepath=str(source), component_name="Imported")
    good(service, operation, arguments)
    result = codec.read_library(target)["symbols"]
    assert result[0]["labels"][0]["font_id"] == 1
    assert result[1]["labels"][0]["font_id"] == 2
    assert result[1]["designator_font_id"] == 2
    combined = dict(codec.read_library_header(target))
    assert combined["FontName1"] == "Times New Roman" and combined["FontName2"] == "Arial"
    assert source.read_bytes() == original_source


def test_step_models_survive_export_import_copy_and_repair_removes_orphan(tmp_path):
    service = NativeLibraries(allowed_paths=[tmp_path])
    step = tmp_path / "model.step"
    payload = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
    step.write_bytes(payload)
    source = tmp_path / "with-model.PcbLib"
    result = good(service, "write_pcblib", {"filepath": str(source), "auto_designator": False,
        "footprints": [{"name": "Modelled", "step_model": {"filepath": str(step)}}]})
    assert result["bodies"][0]["source"] == "step-embedded"
    exported = good(service, "export_library", {"filepath": str(source), "format": "json"})
    assert base64.b64decode(exported["embedded_models"][0]["data"]) == payload
    imported = tmp_path / "imported.PcbLib"
    good(service, "import_library", {"output_path": str(imported), "json_data": exported})
    inline = good(service, "extract_step_model", {"filepath": str(imported)})
    assert base64.b64decode(inline["data"]) == payload
    copied = tmp_path / "copied.PcbLib"
    good(service, "copy_component_cross_library", {"source_filepath": str(source), "target_filepath": str(copied), "component_name": "Modelled"})
    assert base64.b64decode(good(service, "extract_step_model", {"filepath": str(copied)})["data"]) == payload
    good(service, "delete_component", {"filepath": str(imported), "component_names": ["Modelled"]})
    dry = good(service, "repair_library", {"filepath": str(imported), "dry_run": True})
    assert dry["repairs"]["orphaned_models_removed"] == 1
    good(service, "repair_library", {"filepath": str(imported)})
    assert good(service, "extract_step_model", {"filepath": str(imported), "mode": "list"})["total_count"] == 0


def test_authoring_designator_placeholder_and_silk_warning_are_explicit(tmp_path):
    service = NativeLibraries(allowed_paths=[tmp_path])
    path = tmp_path / "auto.PcbLib"
    footprint = {"name": "Auto", "pads": [{"designator": "1", "x": 1, "y": 2, "width": 1, "height": 2,
        "layer": "Top Layer", "shape": "rectangle"}], "tracks": [
        {"x1": 0, "y1": 2, "x2": 2, "y2": 2, "width": 0.2, "layer": "Top Overlay"}]}
    result = good(service, "write_pcblib", {"filepath": str(path), "auto_3d_body": True, "footprints": [footprint]})
    assert result["bodies"][0]["assumed_height"] and result["warnings"][0]["type"] == "silk-on-pad"
    component = good(service, "get_component", {"filepath": str(path), "component_name": "Auto"})["component"]
    assert component["text"][0]["text"] == ".Designator"
    assert component["text"][0]["y"] == pytest.approx(3.6, abs=0.00001)
    assert component["component_bodies"][0]["overall_height"] == 1
    good(service, "write_pcblib", {"filepath": str(path), "footprints": [component]})
    after = good(service, "get_component", {"filepath": str(path), "component_name": "Auto"})["component"]
    assert len(after["text"]) == len(component["text"])


def test_read_echo_write_keeps_original_bytes_and_unknown_pcb_component_stream(libraries):
    service, sch, pcb = libraries
    for path, reader, writer, key in ((sch, "read_schlib", "write_schlib", "symbols"),
                                      (pcb, "read_pcblib", "write_pcblib", "footprints")):
        components = good(service, reader, {"filepath": str(path)})[key]
        before = path.read_bytes()
        good(service, writer, {"filepath": str(path), key: json.loads(json.dumps(components))})
        assert path.read_bytes() == before
    streams = read_streams(pcb)
    streams["A/PrivateExtension"] = b"unknown component stream\0" * 31
    write_streams(pcb, streams)
    good(service, "update_pad", {"filepath": str(pcb), "component_name": "A", "designator": "1", "updates": {"width": 2}})
    assert read_streams(pcb)["A/PrivateExtension"] == streams["A/PrivateExtension"]

@pytest.mark.parametrize('suffix,write_tool,read_tool,field', [
    ('PcbLib','write_pcblib','read_pcblib','footprints'),
    ('SchLib','write_schlib','read_schlib','symbols'),
])
def test_unicode_long_colliding_and_reserved_component_names(tmp_path, suffix, write_tool, read_tool, field):
    service = NativeLibraries(allowed_paths=[tmp_path])
    target = tmp_path / ('names.' + suffix)
    names = ['FileHeader', 'Library', 'FP_000002', 'SYM_000003', 'Ω_فارسی_😀',
             'same_prefix_' + 'a' * 40, 'same_prefix_' + 'a' * 39 + 'b', '😀' * 20]
    components = [{'name': name} for name in names]
    good(service, write_tool, {'filepath': str(target), field: components})
    assert [c['name'] for c in good(service, read_tool, {'filepath': str(target)})[field]] == names
    good(service, 'rename_component', {'filepath': str(target), 'old_name': names[4], 'new_name': '改名_😀'})
    after = good(service, 'list_components', {'filepath': str(target)})['components']
    assert after == names[:4] + ['改名_😀'] + names[5:]


def test_pcblib_unicode_text_uses_utf16_units_and_survives_unrelated_edit(tmp_path):
    service = NativeLibraries(allowed_paths=[tmp_path])
    target = tmp_path / 'unicode.PcbLib'
    text = 'Ω µ فارسی 😀'
    good(service, 'write_pcblib', {'filepath': str(target), 'auto_designator': False, 'footprints': [
        {'name': 'Unicode', 'text': [{'text': text, 'x': 0, 'y': 0, 'height': 1, 'layer': 'Top Overlay'}]}]})
    streams = read_streams(target)
    assert b'55357,56832' in streams['Unicode/WideStrings']
    assert good(service, 'get_component', {'filepath': str(target), 'component_name': 'Unicode'})['component']['text'][0]['text'] == text
    good(service, 'rename_component', {'filepath': str(target), 'old_name': 'Unicode', 'new_name': 'Renamed'})
    assert good(service, 'get_component', {'filepath': str(target), 'component_name': 'Renamed'})['component']['text'][0]['text'] == text


def test_embedding_relative_path_is_confined_against_library_directory(tmp_path, monkeypatch):
    root = tmp_path / 'allowed'
    nested = root / 'nested'
    nested.mkdir(parents=True)
    (tmp_path / 'secret.step').write_text('PRIVATE')
    monkeypatch.chdir(nested)
    service = NativeLibraries(allowed_paths=[root])
    target = root / 'target.PcbLib'
    result = service.call_tool('write_pcblib', {'filepath': str(target), 'footprints': [
        {'name': 'Test', 'model_3d': {'filepath': '../secret.step'}}]})
    assert result['status'] == 'error' and 'allowed_paths' in result['error']
    assert not target.exists()


def test_native_pcblib_unicode_container_convention_and_board_record(tmp_path):
    import struct
    from suite_pcblib_codec import _take, _params
    service = NativeLibraries(allowed_paths=[tmp_path])
    target = tmp_path / 'native-compatible.PcbLib'
    good(service, 'write_pcblib', {'filepath': str(target), 'auto_designator': False,
         'footprints': [{'name': 'Ω_فارسی_😀'}, {'name': 'Second'}]})
    streams = read_streams(target)
    assert 'Ω_فارسی_😀/Parameters' in streams
    params = _params(_take(streams['Ω_فارسی_😀/Parameters'])[0])
    # Independently observed from an Altium-created/saved Unicode footprint.
    assert params['UNICODE'] == 'EXISTS'
    assert params['UNICODE__PATTERN'] == '937,95,1601,1575,1585,1587,1740,95,55357,56832'
    assert params['PATTERN'] == 'O_?????_??'
    toc, toc_end = _take(streams['Library/ComponentParamsTOC/Data'])
    assert toc == b'Name=O_?????_??|Pad Count=0|Height=0|Description=\r\nName=Second|Pad Count=0|Height=0|Description=\r\n\0'
    assert toc_end == len(streams['Library/ComponentParamsTOC/Data'])
    assert streams['Library/ComponentParamsTOC/Header'] == struct.pack('<I', 1)
    assert streams['Library/Header'] == struct.pack('<I', 1)
    header, offset = _take(streams['Library/Data'])
    assert struct.unpack_from('<I', streams['Library/Data'], offset)[0] == 2
    metadata = _params(header)
    assert metadata['V9_MASTERSTACK_NAME'] == 'Master layer stack'
    assert metadata['V9_MASTERSTACK_ID'].startswith('{')
    assert b'__SUITE_' not in header
    template = (Path(__file__).parents[1] / 'contracts' / 'pcblib-default-board.json').read_text()
    assert 'mamzi' not in template
    assert streams['Library/LayerKindMapping/Header'] == struct.pack('<I', 1)


def test_pcblib_native_ansi_identity_and_unicode_rewrite_markers(tmp_path):
    from suite_pcblib_codec import _take, _pairs, write_library, read_library
    target = tmp_path / 'accented.PcbLib'
    name = 'café_Ł_😀'
    write_library(target, [{'name': name, 'description': 'Ω description'}])
    write_library(target, read_library(target)['footprints'])
    streams = read_streams(target)
    identity, _ = _take(streams[name + '/Data'])
    assert identity == b'\x09caf\xe9_L_??'
    # Native Unicode markers bracket the record exactly once on repeated edits.
    parameters = _pairs(_take(streams[name + '/Parameters'])[0])
    assert [pair for pair in parameters if pair[0] == 'UNICODE'] == [('UNICODE', 'EXISTS')] * 2
    assert read_library(target)['footprints'][0]['name'] == name


def test_primitive_identities_survive_mixed_order_and_reorder(tmp_path):
    import struct
    import uuid
    service = NativeLibraries(allowed_paths=[tmp_path])
    target = tmp_path / 'identities.PcbLib'
    first, second = '{11111111-2222-3333-4444-555555555555}', '{AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}'
    good(service, 'write_pcblib', {'filepath': str(target), 'auto_designator': False, 'footprints': [{
        'name': 'IDs', 'primitive_order': ['track', 'pad'],
        'pads': [{'designator': '1', 'x': 0, 'y': 0, 'width': 1, 'height': 1,
                  'shape': 'rectangle', 'layer': 'Top Layer', 'guid': first, 'unique_id': 'PAD_ID'}],
        'tracks': [{'x1': 1, 'y1': 2, 'x2': 3, 'y2': 4, 'width': .2,
                    'layer': 'Top Overlay', 'guid': second, 'unique_id': 'TRACK_ID'}]}]})
    component = good(service, 'get_component', {'filepath': str(target), 'component_name': 'IDs'})['component']
    assert component['pads'][0]['guid'] == first
    assert component['tracks'][0]['unique_id'] == 'TRACK_ID'
    streams = read_streams(target)
    # Altium's identity table indexes the combined primitive sequence.
    assert streams['IDs/PrimitiveGuids/Data'][:8] == struct.pack('<II', 4, 0)
    owner = struct.pack('<II', 85, 0) + uuid.UUID('{99999999-8888-7777-6666-555555555555}').bytes_le
    streams['IDs/PrimitiveGuids/Data'] = owner + streams['IDs/PrimitiveGuids/Data']
    streams['IDs/PrimitiveGuids/Header'] = struct.pack('<I', 3)
    write_streams(target, streams)
    component = good(service, 'get_component', {'filepath': str(target), 'component_name': 'IDs'})['component']
    component['primitive_order'] = ['pad', 'track']
    good(service, 'update_component', {'filepath': str(target), 'component_name': 'IDs', 'footprint': component})
    after = read_streams(target)
    assert after['IDs/PrimitiveGuids/Data'][:24] == owner
    assert after['IDs/PrimitiveGuids/Data'][24:32] == struct.pack('<II', 2, 0)
    read = good(service, 'get_component', {'filepath': str(target), 'component_name': 'IDs'})['component']
    assert read['pads'][0]['guid'] == first and read['pads'][0]['unique_id'] == 'PAD_ID'
    assert read['tracks'][0]['guid'] == second


@pytest.mark.parametrize('primitive_type,plural,geometry', [
    ('region', 'regions', {'vertices': [{'x': 0, 'y': 0}, {'x': 2, 'y': 0}, {'x': 0, 'y': 2}]}),
    ('component_body', 'component_bodies', {'outline': [{'x': 0, 'y': 0}, {'x': 2, 'y': 0}, {'x': 0, 'y': 2}], 'overall_height': 1}),
])
def test_unmodelled_parameter_pairs_survive_json_copy_and_edit(tmp_path, primitive_type, plural, geometry):
    service = NativeLibraries(allowed_paths=[tmp_path])
    source, copy = tmp_path / 'params.PcbLib', tmp_path / 'copy.PcbLib'
    extra = [['FUTURE_SETTING', 'preserve me'], ['ANOTHER', '42']]
    good(service, 'write_pcblib', {'filepath': str(source), 'auto_designator': False,
         'footprints': [{'name': 'Custom', plural: [{**geometry, 'layer': 'Top Layer', 'additional_parameters': extra}]}]})
    component = good(service, 'get_component', {'filepath': str(source), 'component_name': 'Custom'})['component']
    assert component[plural][0]['additional_parameters'] == extra
    # Exercise the actual JSON boundary; private codec attributes cannot help.
    component = json.loads(json.dumps(component))
    good(service, 'write_pcblib', {'filepath': str(copy), 'auto_designator': False, 'footprints': [component]})
    if primitive_type == 'component_body':
        component[plural][0]['layer'] = 'Bottom Layer'
        good(service, 'update_component', {'filepath': str(copy), 'component_name': 'Custom', 'footprint': component})
    else:
        good(service, 'update_primitive', {'filepath': str(copy), 'component_name': 'Custom',
             'primitive_type': primitive_type, 'index': 0, 'updates': {'layer': 'Bottom Layer'}})
    assert good(service, 'get_component', {'filepath': str(copy), 'component_name': 'Custom'})['component'][plural][0]['additional_parameters'] == extra


def test_step_external_flag_does_not_embed_existing_file(tmp_path):
    from suite_pcblib_codec import read_embedded_models
    step = tmp_path / 'synthetic.step'
    step.write_text('ISO-10303-21;\nEND-ISO-10303-21;\n')
    service = NativeLibraries(allowed_paths=[tmp_path])
    target = tmp_path / 'external.PcbLib'
    result = good(service, 'write_pcblib', {'filepath': str(target), 'auto_designator': False,
        'footprints': [{'name': 'External', 'step_model': {'filepath': str(step), 'embed': False}}]})
    assert result['bodies'][0] == {'name': 'External', 'source': 'step-external', 'model': str(step)}
    assert read_embedded_models(target) == []
    body = good(service, 'get_component', {'filepath': str(target), 'component_name': 'External'})['component']['component_bodies'][0]
    assert body['embedded'] is False and body['model_id'] == ''
    failed = service.call_tool('write_pcblib', {'filepath': str(tmp_path / 'missing.PcbLib'),
        'footprints': [{'name': 'Missing', 'step_model': {'filepath': str(tmp_path / 'missing.step'), 'embed': True}}]})
    assert failed['status'] == 'error' and not (tmp_path / 'missing.PcbLib').exists()


def test_configured_audit_is_metadata_only_and_reports_sink_failure(tmp_path):
    audit = tmp_path / 'audit' / 'libraries.jsonl'
    service = NativeLibraries(allowed_paths=[tmp_path], logging_config={'audit_log_path': str(audit)})
    result = service.call_tool('read_pcblib', {'filepath': str(tmp_path / 'SECRET_NAME.PcbLib')})
    assert result['status'] == 'error'
    record = json.loads(audit.read_text())
    assert set(record) == {'timestamp_utc', 'tool', 'status', 'duration_ms'}
    assert record['tool'] == 'read_pcblib' and record['status'] == 'error'
    assert 'SECRET_NAME' not in audit.read_text()
    blocked = tmp_path / 'directory'
    blocked.mkdir()
    service = NativeLibraries(allowed_paths=[tmp_path], logging_config={'audit_log_path': str(blocked)})
    target = tmp_path / 'logged.SchLib'
    result = good(service, 'write_schlib', {'filepath': str(target), 'symbols': [{'name': 'Created'}]})
    assert target.exists() and 'audit_warning' in result
