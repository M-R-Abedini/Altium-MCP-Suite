// Isolated DelphiScript sandbox used by the run_altium_script MCP tool.
//
// Each invocation uses a separate generated project and exchange directory.
// The suite retains engine ownership until the final completion acknowledgement.
//
// SandboxLog() flushes to disk on every call, so when a script dies silently
// (Altium leaves it paused in the debugger with no dialog) the log still shows
// the last step that completed - the statement after it is the culprit.

const
    REPLACEALL = 1;

var
    LogLines : TStringList;
    LogPath  : String;
    OutPath  : String;
    // Scratch variables: DelphiScript has no inline declarations, so scripts
    // passed to the tool reuse these rather than declaring their own.
    S1, S2, S3 : String;
    I1, I2, I3 : Integer;
    B1         : Integer;
    Obj1, Obj2, Obj3, Obj4, Obj5 : IDispatch;
    List1      : TStringList;
    IntMan     : IIntegratedLibraryManager;
    DbDoc      : IDatabaseLibDocument;

procedure SandboxLog(Msg: String);
begin
    LogLines.Add(Msg);
    LogLines.SaveToFile(LogPath);
end;

procedure Run;
var
    ResultText : String;
    OutLines   : TStringList;
begin
    LogPath := '__ALTIUM_MCP_SANDBOX_LOG__';
    OutPath := '__ALTIUM_MCP_SANDBOX_RESULT__';
    LogLines := TStringList.Create;
    try
    ResultText := '{"sandbox": "no result set"}';
    SandboxLog('sandbox start');

    try
        // === BEGIN EXPERIMENT (rewritten by the run_altium_script tool) ===
        ResultText := '{"sandbox":"empty experiment"}';
        // === END EXPERIMENT ===
    except
        SandboxLog('EXCEPTION escaped the script body');
        ResultText := '{"error": "exception escaped script - see log for last step"}';
    end;

    SandboxLog('sandbox end');

    OutLines := TStringList.Create;
    try
        OutLines.Text := ResultText;
        OutLines.SaveToFile(OutPath);
    finally
        OutLines.Free;
    end;
    finally
        LogLines.Free;
    end;
    // Only a fully completed invocation can authorize another script launch.
    OutLines := TStringList.Create;
    try
        OutLines.Text := '{"request_id":"__ALTIUM_MCP_SANDBOX_REQUEST_ID__"}';
        OutLines.SaveToFile('__ALTIUM_MCP_SANDBOX_COMPLETION__');
    finally
        OutLines.Free;
    end;
end;
