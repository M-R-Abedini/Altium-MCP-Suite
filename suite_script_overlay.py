"""Compile a snippet as a command handler in the canonical persistent EDA project.

SPDX-License-Identifier: MIT
"""
from __future__ import annotations


def render_script_unit(script: str) -> str:
    from coffeenmusic.server.batch_safety import validate_legacy_json_text
    if not isinstance(script, str) or not script.strip():
        raise ValueError('script must contain DelphiScript statements')
    validate_legacy_json_text(script)
    return '''{ SPDX-License-Identifier: MIT }
Const REPLACEALL = 1;
Var SuiteSandboxSteps : TStringList;
    SuiteSandboxLogPath : String;
Procedure SandboxLog(MessageText : String);
Begin
    SuiteSandboxSteps.Add(MessageText);
    SuiteSandboxSteps.SaveToFile(SuiteSandboxLogPath);
End;
Function LegacySandboxExecute(Params, RequestId : String) : String;
Var
    ResultText, S1, S2, S3, StepJson : String;
    I1, I2, I3, B1, StepIndex : Integer;
    Obj1, Obj2, Obj3, Obj4, Obj5 : IDispatch;
    IntMan : IIntegratedLibraryManager;
    DbDoc : IDatabaseLibDocument;
    List1 : TStringList;
    Succeeded : Boolean;
Begin
    SuiteSandboxSteps := TStringList.Create;
    List1 := Nil;
    SuiteSandboxLogPath := WorkspaceDir + 'script-steps_' + RequestId + '.txt';
    ResultText := '{"sandbox": "no result set"}';
    Succeeded := True;
    Try
        SandboxLog('sandbox start');
        Try
''' + '\n'.join('            ' + line for line in script.splitlines()) + '''
        Except
            Succeeded := False;
            SandboxLog('EXCEPTION escaped the script body');
            ResultText := '{"error": "exception escaped script - see log for last step"}';
        End;
        SandboxLog('sandbox end');
        StepJson := '';
        For StepIndex := 0 To SuiteSandboxSteps.Count - 1 Do Begin
            If StepIndex > 0 Then StepJson := StepJson + ',';
            StepJson := StepJson + '"' + EscapeJsonString(SuiteSandboxSteps[StepIndex]) + '"';
        End;
        Result := BuildSuccessResponse(RequestId, JsonObj(
            JsonBool('success', Succeeded) + ',' + JsonStr('result', ResultText) + ',' +
            JsonRaw('steps', '[' + StepJson + ']') + ',' + JsonInt('dialogs_dismissed', 0)));
    Finally
        SuiteSandboxSteps.Free;
    End;
End;
'''


def execute_overlay(bridge, execute, params, timeout):
    """Caller holds the shared engine gate for stop/recompile/execute/restore.

    A timeout or failed startup never authorizes a second launch. The same
    coordinator's session-bound release proof controls every transition.
    """
    import os
    import bridge_coordination as coordination
    from suite_config import prepare_runtime
    script = params.get('script')
    render_script_unit(script)  # Validate before touching the native engine.
    if timeout <= 0:
        raise ValueError('timeout_seconds must be positive')
    base_project = coordination.SCRIPT
    base_environment = os.environ.get('ALTIUM_MCP_EDA_SCRIPT')
    overlay = prepare_runtime(script_body=script)['eda']
    coordination.stop_eda()
    coordination.SCRIPT = overlay
    os.environ['ALTIUM_MCP_EDA_SCRIPT'] = str(overlay)
    completed = False
    try:
        coordination.ensure_eda(bridge, execute)
        result = execute(bridge, 'legacy.run_altium_script', {}, timeout)
        completed = True
        return result
    finally:
        # Do not interrupt an unresolved script or attempt to replay it.
        if completed:
            coordination.stop_eda()
        coordination.SCRIPT = base_project
        if base_environment is None:
            os.environ.pop('ALTIUM_MCP_EDA_SCRIPT', None)
        else:
            os.environ['ALTIUM_MCP_EDA_SCRIPT'] = base_environment
