{ SPDX-License-Identifier: MIT }
{ Generated snippet handler placeholder. The coordinator replaces only this
  unit in a content-addressed copy of the same EDA project. }
Function LegacySandboxExecute(Params, RequestId : String) : String;
Begin
    Result := BuildErrorResponse(RequestId, 'NO_SCRIPT_OVERLAY', 'No script body was compiled into this project');
End;
