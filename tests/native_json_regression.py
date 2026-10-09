"""Opt-in read-only regression checks executed by the real DelphiScript engine.

Run from the suite virtualenv while Altium is open. Uses shared ownership and
records completion; does not edit or save design documents. Output is under
.runtime/native-json-<uuid>. A compilation fault leaves ownership unresolved.
"""
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def literal(text):
    return "'" + text.replace("'", "''") + "'"


def main():
    import bridge_coordination as coord
    from suite_config import altium_exe
    scratch = ROOT / '.runtime' / ('native-json-' + uuid.uuid4().hex)
    scratch.mkdir(parents=True)
    result_file = scratch / 'result.json'
    request_id = uuid.uuid4().hex
    utils = (ROOT / 'coffeenmusic/server/AltiumScript/json_utils.pas').read_text(encoding='utf-8')
    helpers = utils.split('// Function to create a JSON name-value pair')[0]
    old_legacy = subprocess.check_output(['git', 'show', '3e4f58c:coffeenmusic/server/AltiumScript/json_utils.pas'], cwd=ROOT, text=True)
    old_legacy = old_legacy.split('// Helper function to escape JSON strings')[0]
    old_legacy = old_legacy.replace('RemoveChar', 'OldRemoveChar').replace('TrimJSON', 'OldTrimJSON')
    old_main = subprocess.check_output(['git', 'show', '3e4f58c:eda-agent/scripts/altium/Main.pas'], cwd=ROOT, text=True)
    old_main = old_main[old_main.index('Function IsWhitespaceOrColon('):old_main.index('{..............................................................................}', old_main.index('Function ExtractJsonValue('))]
    for name in ['IsWhitespaceOrColon', 'IsDelimiter', 'HexDigitValue', 'UnescapeJsonString', 'ExtractJsonValue']:
        old_main = old_main.replace(name, 'Old' + name)

    checks = []
    for index, text in enumerate(['10k, "precision"', r'C:\temp\file.schlib', 'first\nsecond\t"quote"', 'comma, end,', 'x' * 4096]):
        expression = f'TrimJSON({literal(json.dumps(text) + ",")}) = {literal(text)}'
        if '\n' in text or '\t' in text:
            expected = literal('first') + ' + #10 + ' + literal('second') + ' + #9 + ' + literal('"quote"')
            expression = f'TrimJSON({literal(json.dumps(text) + ",")}) = ({expected})'
        checks.append((f'legacy_scalar_{index}', expression))
    for index, payload in enumerate([
        {'inner': {'value': 'wrong'}, 'value': 'right'},
        {'label': 'value', 'value': 'right'},
        {'items': [{'value': 'wrong'}], 'value': 'right'},
        {'label': r'a\"value', 'value': 'right'},
    ]):
        encoded = literal(json.dumps(payload))
        checks.append((f'top_level_{index}', f'LegacyJsonValue({encoded}, \'value\') = \'right\''))
    checks.extend([
        ('control_output', "JSONEscapeString(#0 + #8 + #12 + #13 + #10) = '\\u0000\\u0008\\u000C\\u000D\\u000A'"),
        ('latin1_input', "TrimJSON('\"caf\\u00e9\"') = ('caf' + Chr(233))"),
        ('latin1_output', "JSONEscapeString(Chr(233)) = '\\u00E9'"),
        ('old_comma_bug_reproduced', "OldTrimJSON('\"a,b\"') <> 'a,b'"),
        ('old_nested_key_bug_reproduced', "OldExtractJsonValue('{\"inner\":{\"value\":\"wrong\"},\"value\":\"right\"}', 'value') <> 'right'"),
    ])
    statements = []
    for index, (name, expression) in enumerate(checks):
        comma = ',' if index else ''
        statements.append(f"    If ({expression}) Then Output := Output + '{comma}\"{name}\":true' Else Output := Output + '{comma}\"{name}\":false';")
    native = helpers + old_legacy + old_main + '\nProcedure Run;\nVar Output : String; Lines : TStringList;\nBegin\n'
    native += f"    Output := '{{\"request_id\":\"{request_id}\",\"checks\":{{';\n"
    native += '\n'.join(statements) + "\n    Output := Output + '}}';\n"
    native += f'    Lines := TStringList.Create;\n    Try Lines.Text := Output; Lines.SaveToFile({literal(str(result_file))}); Finally Lines.Free; End;\nEnd;\n'
    (scratch / 'NativeJson.pas').write_text(native, encoding='utf-8')
    template = (ROOT / 'coffeenmusic/server/SandboxScript/Sandbox.PrjScr').read_text()
    project = scratch / 'NativeJson.PrjScr'
    project.write_text(template.replace('Sandbox.pas', 'NativeJson.pas'))
    with coord.engine_lock():
        coord.stop_eda()
        coord.begin_legacy(request_id, scratch / 'unused-request.json', result_file)
        cmd = f'"{altium_exe()}" -RScriptingSystem:RunScript(ProjectName="{project}"|ProcName="NativeJson>Run")'
        subprocess.Popen(cmd, shell=False, creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            try:
                result = json.loads(result_file.read_text(encoding='utf-8-sig'))
                if result.get('request_id') == request_id:
                    break
            except (OSError, ValueError):
                pass
            time.sleep(.1)
        else:
            raise TimeoutError('Native test did not finish; ownership retained. Inspect Altium before retrying.')
        coord._guard_editor()  # Retire only the confirmed native completion.
    print(json.dumps({'report': str(result_file), **result}))
    if not all(result['checks'].values()):
        raise AssertionError('Native JSON regression failed')


if __name__ == '__main__':
    main()
