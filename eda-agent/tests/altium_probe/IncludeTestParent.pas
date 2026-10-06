{ SPDX-License-Identifier: Apache-2.0 }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com> }
{ Probe: this is the ONLY .pas listed in IncludeTest.PrjScr.          }
{ It pulls in IncludeTestInner.pas via the {$I ...} directive.        }
{ The expected dialog behaviour after Altium opens IncludeTest.PrjScr }
{ and the user opens File > Run Script:                                }
{                                                                      }
{   PASS: only MCP_RunIncludeProbe appears in the tree.               }
{   FAIL-A: _IncProbeInnerMarker and _IncProbeInnerOnlyProc also      }
{           appear (Altium found the inner file anyway).              }
{   FAIL-B: the project does not compile at all (directive not        }
{           honoured by DelphiScript).                                 }

{$I IncludeTestInner.pas}

Procedure MCP_RunIncludeProbe;
Var marker : String;
Begin
    marker := _IncProbeInnerMarker;
    If marker = 'inner-marker-12345' Then
        ShowMessage('PASS: $I include worked. Inner marker = ' + marker)
    Else
        ShowMessage('FAIL: marker mismatch: ' + marker);
End;
