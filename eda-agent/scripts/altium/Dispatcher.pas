{ Modified 2026-10-06 by M-R-Abedini; see root MODIFICATIONS.md. }
{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>                                      }
{..............................................................................}
{ Dispatcher.pas - Polling loop and per-request dispatcher.                     }
{ Compiles last so all Handle*Command functions are visible.                   }
{..............................................................................}

{ Dashboard counters fed to StatusForm.pas helpers each tick. }
Var
    StatusStartTick      : Cardinal;
    StatusRequestCount   : Integer;
    StatusLastCommand    : String;
    StatusTotalAltiumMs  : Cardinal;

Function ProcessCommand(Command : String; Params : String; RequestId : String) : String;
Var
    Category, Action : String;
    DotPos : Integer;
Begin
    DotPos := Pos('.', Command);
    If DotPos > 0 Then
    Begin
        Category := Copy(Command, 1, DotPos - 1);
        Action := Copy(Command, DotPos + 1, Length(Command));
    End
    Else
    Begin
        Category := Command;
        Action := '';
    End;

    Case Category Of
        'application': Result := HandleApplicationCommand(Action, Params, RequestId);
        'project':     Result := HandleProjectCommand(Action, Params, RequestId);
        'library':     Result := HandleLibraryCommand(Action, Params, RequestId);
        'generic':     Result := HandleGenericCommand(Action, Params, RequestId);
        'pcb':         Result := HandlePCBCommand(Action, Params, RequestId);
        'audit':       Result := HandleAuditCommand(Action, Params, RequestId);
    Else
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_COMMAND',
            'Unknown command category: ' + Category +
            '. Use generic.* for object operations, pcb.* for PCB-specific ' +
            'commands, or audit.* for design-lint checks.');
    End;
End;

{..............................................................................}
{ Process a single request if one exists. Returns True iff a request was found.}
{                                                                                }
{ The dispatcher scans for any request_*.json file in the workspace, extracts  }
{ the ID from the filename, reads and deletes the request, dispatches, and    }
{ writes response_<id>.json. The handler returns the JSON envelope as a       }
{ String; the dispatcher writes the file. Handlers that previously bypassed    }
{ the dispatcher's write via the ResponseAlreadyWritten flag have all been     }
{ migrated to the standard pattern.                                            }
{..............................................................................}

{..............................................................................}
{ CommandIsReadOnly - whether a command leaves the design untouched.           }
{                                                                              }
{ Used for ONE thing: deciding whether the compiled-netlist cache survives a   }
{ command. SmartCompile skips DM_Compile for COMPILE_CACHE_TTL_MS when the     }
{ project reports no dirty documents, and InvalidateCompileCache existed but   }
{ was never called from anywhere, so a write followed within that window by a  }
{ connectivity read handed back the netlist from BEFORE the write. Whether it  }
{ did depended on whether that particular handler happened to dirty the        }
{ document, which is not uniform: many go through ProcessControl, which marks  }
{ the document modified, and others assign through SetState_ and do not.       }
{                                                                              }
{ THE UNKNOWN CASE COUNTS AS A WRITE. Only the prefixes below are treated as   }
{ leaving the design alone, so a command this list has never heard of, and     }
{ every command added later, invalidates. An unnecessary invalidation costs    }
{ one recompile; a missed one returns connectivity that predates the edit.     }
{..............................................................................}

Function ActionHasPrefix(Verb : String; Prefix : String) : Boolean;
Begin
    Result := Copy(Verb, 1, Length(Prefix)) = Prefix;
End;

Function CommandIsReadOnly(Command : String) : Boolean;
Var
    Verb : String;
    DotPos : Integer;
Begin
    Verb := LowerCase(Trim(Command));
    DotPos := Pos('.', Verb);
    If DotPos > 0 Then Verb := Copy(Verb, DotPos + 1, Length(Verb) - DotPos);

    Result := ActionHasPrefix(Verb, 'get_')
           Or ActionHasPrefix(Verb, 'list_')
           Or ActionHasPrefix(Verb, 'query')
           Or ActionHasPrefix(Verb, 'read_')
           Or ActionHasPrefix(Verb, 'find_')
           Or ActionHasPrefix(Verb, 'count')
           Or ActionHasPrefix(Verb, 'audit_')
           Or ActionHasPrefix(Verb, 'check_')
           Or ActionHasPrefix(Verb, 'calc_')
           Or ActionHasPrefix(Verb, 'export_')
           Or ActionHasPrefix(Verb, 'render_')
           Or ActionHasPrefix(Verb, 'probe_')
           Or ActionHasPrefix(Verb, 'inspect_')
           Or ActionHasPrefix(Verb, 'diff_')
           Or ActionHasPrefix(Verb, 'compare_')
           Or (Verb = 'ping');
End;

Function ProcessSingleRequest(Dummy : Integer): Boolean;
Var
    RequestPath, RequestId : String;
    RequestContent, ResponseContent : String;
    Command, Params, ProtoVer, EnvelopeError : String;
    ExceptionMsg : String;
    FocusBefore, FocusAfter : String;
    StartMs, DurationMs : Cardinal;
    ResultTag : String;
    DashIsError : Boolean;
    DashDetail, DashErrPayload, DashCode : String;
Begin
    Result := False;
    EnsureWorkspaceDir(0);

    If Not ScanForRequestFile(RequestPath, RequestId) Then Exit;

    // Read the request file
    RequestContent := ReadFileContent(RequestPath);
    // Remove the request file regardless of read outcome so we never reprocess
    DeleteFile(RequestPath);

    If RequestContent = '' Then
    Begin
        { ReadFileContent already retried 12 times over ~180ms for a       }
        { transient sharing violation, so an empty result here means the   }
        { file was genuinely empty or still locked. Deleting it and        }
        { exiting SILENTLY left the caller to wait out its entire deadline }
        { and report a plain timeout, which reads exactly like a wedged    }
        { polling loop and sends the user hunting the wrong fault.          }
        {                                                                   }
        { The id came from the FILENAME via ScanForRequestFile and has not  }
        { been overwritten by the body's id yet, so the call can still be   }
        { answered with the actual reason.                                  }
        If IsValidRequestId(RequestId) Then
            WriteResponseFile(RequestId,
                BuildErrorResponse(RequestId, 'REQUEST_UNREADABLE',
                    'Request file was empty or unreadable after 12 retries '
                    + 'and has been discarded. The polling loop is healthy; '
                    + 'retry the call.'));
        Exit;
    End;

    // ID arrives in the JSON body. Per-request response files use it for
    // the filename so concurrent callers each get an isolated response file.
    RequestId := ExtractJsonValue(RequestContent, 'id');
    Command := ExtractJsonValue(RequestContent, 'command');
    Params := ExtractJsonValue(RequestContent, 'params');
    ProtoVer := ExtractJsonValue(RequestContent, 'protocol_version');

    EnvelopeError := ValidateRequestEnvelope(RequestId, Command);
    If EnvelopeError <> '' Then
    Begin
        // Without a valid id we can't write a per-request response file;
        // fall back to writing response.json so Python can still pick it up.
        If IsValidRequestId(RequestId) Then
            WriteResponseFile(RequestId,
                BuildErrorResponse(RequestId, 'MALFORMED_REQUEST', EnvelopeError))
        Else
            WriteFileContent(WorkspaceDir + 'response.json',
                BuildErrorResponse('', 'MALFORMED_REQUEST', EnvelopeError));
        Result := True;
        Exit;
    End;

    If (ProtoVer <> '') And (ProtoVer <> IntToStr(PROTOCOL_VERSION)) Then
    Begin
        WriteResponseFile(RequestId,
            BuildErrorResponseDetailed(RequestId, 'PROTOCOL_VERSION_MISMATCH',
                'Client protocol_version=' + ProtoVer +
                ' does not match server PROTOCOL_VERSION=' + IntToStr(PROTOCOL_VERSION) +
                '. Update the eda-agent client or restart the Altium script.',
                '{"client_version":' + ProtoVer +
                ',"server_version":' + IntToStr(PROTOCOL_VERSION) + '}'));
        Result := True;
        Exit;
    End;

    StatusLastCommand := Command;
    Inc(StatusRequestCount);
    StartMs := GetTickCount;
    ResultTag := 'OK';

    { MCP liveness: any inbound command (typically application.ping every }
    { 30 s) keeps the Open Dashboard button enabled.                       }
    LastPingMs := StartMs;

    { Spinner + in-flight readout on the dashboard. Reset on exit so the }
    { status pill drops back to idle/paused/green when we're done.       }
    SetInFlight(Command);

    { WHERE THE CALLER WAS LOOKING, BEFORE THE HANDLER RAN.
      Nearly every tool acts on the focused document, and several change
      it as a side effect of doing their job. Nothing announced that.
      Measured: lib_probe_footprint focused a PcbLib to read it, the
      obj_switch_view that followed switched the LIBRARY into 3D, and the
      session spent a long time looking for a placement bug that was not
      there.
      Captured here rather than per handler because there are hundreds of
      them and this is the one place every command passes through. }
    FocusBefore := CurrentFocusedDocPath(0);
    ResetNextStep(0);

    ExceptionMsg := '';
    { Heartbeat: write progress_<id>.json so Python can distinguish "still      }
    { working" from "polling loop dead" when the 10 s default deadline runs   }
    { out on a legitimately-slow handler. Delete only AFTER writing the       }
    { response, so at no point are both files missing.                        }
    StartProgress(RequestId);
    Try
        Try
            ResponseContent := ProcessCommand(Command, Params, RequestId);
        Except
            ExceptionMsg := 'Unhandled exception processing: ' + Command;
            ResponseContent := BuildErrorResponse(RequestId, 'INTERNAL_ERROR', ExceptionMsg);
            ResultTag := 'EXCEPTION';
        End;

        { The compiled netlist is stale the moment anything is written, and
          this is the one place every command passes through, so it is done
          here rather than in each of the hundreds of handlers.

          On the exception path too, deliberately: a handler that threw part
          way through may well have written something first, and that is
          exactly when a cached netlist is worth least. }
        If Not CommandIsReadOnly(Command) Then InvalidateCompileCache(0);

        If ResponseContent = '' Then
        Begin
            // Handler returned nothing, degenerate but recoverable. Synthesise
            // an INTERNAL_ERROR rather than leaving the caller polling forever.
            ResponseContent := BuildErrorResponse(RequestId, 'INTERNAL_ERROR',
                'Handler returned empty response for: ' + Command);
            ResultTag := 'EMPTY';
        End;

        { Say so if the active document moved. Appended as a sibling of
          data rather than merged into it, because data is whatever the
          handler chose to return and this must not depend on its shape.
          Silent when nothing moved, which is the overwhelming majority. }
        { The follow-up this reply owes, if the handler named one. }
        If PendingNextStep(0) <> '' Then
            ResponseContent := AppendEnvelopeField(ResponseContent,
                JsonStr('next_step', PendingNextStep(0)));

        FocusAfter := CurrentFocusedDocPath(0);
        If FocusAfter <> FocusBefore Then
            ResponseContent := AppendEnvelopeField(ResponseContent,
                '"active_document_changed":' + JsonObj(
                    JsonStr('from', FocusBefore) + ',' +
                    JsonStr('to', FocusAfter) + ',' +
                    JsonStr('note', 'this command moved the focused '
                        + 'document. Tools that act on the focused '
                        + 'document will now act on the new one.')));

        WriteResponseFile(RequestId, ResponseContent);
    Finally
        EndProgress(RequestId);
    End;

    DurationMs := GetTickCount - StartMs;
    StatusTotalAltiumMs := StatusTotalAltiumMs + DurationMs;

    AppendLog(FormatLogStamp(0) + ',' + IntToStr(DurationMs) + ',' + Command + ',' + ResultTag
              + ',' + IntToStr(Length(ResponseContent)) + ',' + Copy(ResponseContent, 1, 200));

    { Surface the error message to the dashboard (inline detail row + last- }
    { error banner) when the response is success=false. ExtractJsonValue   }
    { handles the nested error/code/message path via two successive calls. }
    DashIsError := (ResultTag = 'EXCEPTION');
    DashErrPayload := ExtractJsonValue(ResponseContent, 'error');
    DashDetail := '';
    DashCode := '';
    If (DashErrPayload <> '') And (DashErrPayload <> 'null') Then
    Begin
        DashIsError := True;
        DashCode    := ExtractJsonValue(DashErrPayload, 'code');
        DashDetail  := ExtractJsonValue(DashErrPayload, 'message');
        { Explicit Begin/End around each branch, DelphiScript parser   }
        { trips on `Else If` without them.                               }
        If (DashCode <> '') And (DashDetail <> '') Then
        Begin
            DashDetail := DashCode + ': ' + DashDetail;
        End
        Else
        Begin
            If (DashCode <> '') Then DashDetail := DashCode;
        End;
    End;
    AppendLogLine(Command, DurationMs, DashIsError, RequestId, DashDetail);

    ResetInFlight(0);

    Result := True;
End;

{..............................................................................}
{ Clean up state left by the MCP server before exiting. Deletes any leftover   }
{ per-request IPC files and flushes the UI.                                    }
{..............................................................................}

Procedure CleanupMCPServer(Dummy : Integer);
Begin
    CleanupOrphanRequests(0);
    CleanupOrphanProgress(0);
    Application.ProcessMessages;
End;

{..............................................................................}
{ Start MCP server, adaptive polling loop.                                  }
{                                                                            }
{ Uses ADAPTIVE POLLING to avoid blocking Altium:                             }
{   - Active (just processed a request): polls fast (PollIntervalActiveMs)   }
{   - Idle: polls slow (PollIntervalIdleMs) with extra ProcessMessages calls }
{   - Auto-shuts down after AutoShutdownMs of inactivity                      }
{                                                                            }
{ All tunables come from mcp_config.json via LoadMCPConfig at startup.       }
{ Stop methods: send application.stop_server, drop a 'stop' file in the      }
{ workspace, or wait for auto-shutdown.                                      }
{..............................................................................}

Procedure StartMCPServer;
Var
    StopPath       : String;
    IdleCount      : Integer;
    CurrentSleep   : Integer;
    LastActivityMs : Cardinal;
    NowMs          : Cardinal;
    HadRequest     : Boolean;
    I              : Integer;
    ActiveTickCount : Integer;
Begin
    If Running Then Exit;

    InitDefaultConfig(0);
    EnsureWorkspaceDir(0);
    LoadMCPConfig(0);
    { Startup purge: nothing on disk can belong to a live exchange, because no
      loop was running to serve it. Responses are purged here but NOT in
      CleanupMCPServer(0) -- on shutdown a client may still be reading one. }
    CleanupOrphanRequests(0);
    CleanupOrphanResponses(0);
    CleanupOrphanProgress(0);
    Running := True;
    StopReason := '';
    StopPath := WorkspaceDir + 'stop';
    If FileExists(StopPath) Then DeleteFile(StopPath);

    IdleCount := 0;
    CurrentSleep := PollIntervalActiveMs;
    LastActivityMs := GetTickCount;
    ActiveTickCount := 0;

    StatusStartTick := GetTickCount;
    StatusRequestCount := 0;
    StatusLastCommand := '';
    StatusTotalAltiumMs := 0;
    ShowStatusForm(0);
    UpdateStatusHeader('MCP: idle');
    UpdateStatsLine(0, 0, 0, AutoShutdownMs Div 1000);
    AppendLog(FormatLogStamp(0) + ',0,_session_start,version=' + SCRIPT_VERSION
              + ',protocol=' + IntToStr(PROTOCOL_VERSION));

    Try
        While Running Do
        Begin
            // Shutdown detection: Altium quitting
            Try
                If Client.IsQuitting Then
                Begin
                    StopReason := 'altium_quitting';
                    Running := False;
                    Break;
                End;
            Except
                StopReason := 'client_state_error';
                Running := False;
                Break;
            End;

            // Stop file
            If FileExists(StopPath) Then
            Begin
                DeleteFile(StopPath);
                StopReason := 'coordinator_stop_file';
                Running := False;
                Break;
            End;

            // Renew button: reset the real idle deadline once per click.
            If RenewRequested Then
            Begin
                LastActivityMs := GetTickCount;
                RenewRequested := False;
                UpdateStatsLine(
                    (GetTickCount - StatusStartTick) Div 1000,
                    StatusRequestCount,
                    StatusTotalAltiumMs,
                    AutoShutdownMs Div 1000);
            End;

            // Auto-shutdown after prolonged inactivity. Paused sessions
            // never auto-shutdown so the user can step away indefinitely.
            If PausedFlag Then
                LastActivityMs := GetTickCount;
            If AutoShutdownMs > 0 Then
            Begin
                NowMs := GetTickCount;
                If NowMs >= LastActivityMs Then
                Begin
                    If (NowMs - LastActivityMs) > AutoShutdownMs Then
                    Begin
                        StopReason := 'idle_timeout';
                        Running := False;
                        Break;
                    End;
                End;
            End;

            If PausedFlag Then
            Begin
                { Skip dispatch entirely while paused, but still yield and    }
                { refresh stats so the dashboard countdown stays alive.       }
                UpdateStatsLine(
                    (GetTickCount - StatusStartTick) Div 1000,
                    StatusRequestCount,
                    StatusTotalAltiumMs,
                    AutoShutdownMs Div 1000);
                Application.ProcessMessages;
                Sleep(PollIntervalIdleMs);
                Continue;
            End;

            HadRequest := ProcessSingleRequest(0);

            If HadRequest Then
            Begin
                IdleCount := 0;
                CurrentSleep := PollIntervalActiveMs;
                LastActivityMs := GetTickCount;
                UpdateStatusHeader('MCP: idle');
                UpdateStatsLine(
                    (GetTickCount - StatusStartTick) Div 1000,
                    StatusRequestCount,
                    StatusTotalAltiumMs,
                    (AutoShutdownMs - (GetTickCount - LastActivityMs)) Div 1000);
                { Perf row already updated in-place by TrackPerf (called }
                { from AppendLogLine inside ProcessSingleRequest). Skip  }
                { the full RefreshPerfPanel rebuild that used to flash  }
                { the visible memo on every command.                     }
            End
            Else
            Begin
                Inc(IdleCount);
                If IdleCount > IdleThreshold Then
                    CurrentSleep := PollIntervalIdleMs;
                If (IdleCount Mod 10) = 0 Then
                    UpdateStatsLine(
                        (GetTickCount - StatusStartTick) Div 1000,
                        StatusRequestCount,
                        StatusTotalAltiumMs,
                        (AutoShutdownMs - (GetTickCount - LastActivityMs)) Div 1000);
            End;

            If CurrentSleep >= PollIntervalIdleMs Then
            Begin
                For I := 1 To YieldIterations Do
                Begin
                    Application.ProcessMessages;
                    Sleep(CurrentSleep Div YieldIterations);
                    If Not Running Then Break;
                End;
                ActiveTickCount := 0;
            End
            Else
            Begin
                Inc(ActiveTickCount);
                If ActiveTickCount >= YieldEveryNActive Then
                Begin
                    Application.ProcessMessages;
                    ActiveTickCount := 0;
                End;
                Sleep(CurrentSleep);
            End;
        End;
    Except
        // Altium shutting down or fatal error, exit gracefully
        StopReason := 'dispatcher_exception';
    End;

    Running := False;
    If StopReason = '' Then StopReason := 'command_or_external_stop';
    AppendLog(FormatLogStamp(0) + ',0,_session_end,requests=' + IntToStr(StatusRequestCount) + ',reason=' + StopReason);
    HideStatusForm(0);
    CleanupMCPServer(0);
End;

{..............................................................................}
{ Write the 'stop' file so a running StartMCPServer exits on its next poll.   }
{                                                                              }
{ HIDDEN FROM THE RUN SCRIPT DIALOG, because it cannot be useful there.       }
{ The scripting engine runs one script at a time, so while the polling loop   }
{ holds it there is no way to pick this out of the dialog and run it, and     }
{ when the loop is NOT running there is nothing to stop: the sentinel would   }
{ just sit there, and StartMCPServer deletes a stale one at startup anyway.   }
{                                                                              }
{ Nothing calls it. Python stops the loop with the application.stop_server    }
{ COMMAND, and the dashboard's Detach button sets Running := False directly,  }
{ which is the same result by a shorter route. It is kept rather than deleted }
{ because the sentinel it writes is the documented out-of-band stop and a     }
{ future caller may want it; the argument keeps it out of a list of four      }
{ things a human is choosing between.                                          }
{..............................................................................}

Procedure StopMCPServer(Dummy : Integer);
Var
    StopPath : String;
    F : TextFile;
Begin
    EnsureWorkspaceDir(0);
    StopPath := WorkspaceDir + 'stop';
    Try
        AssignFile(F, StopPath);
        Rewrite(F);
        Writeln(F, '1');
        CloseFile(F);
        ShowMessage('MCP server stop signal sent. The server will exit within 500ms.');
    Except
        ShowMessage('Failed to write stop file: ' + StopPath);
    End;
End;
