{ SPDX-License-Identifier: Apache-2.0 }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com> }
{ Probe: this file is NOT listed in IncludeTest.PrjScr. It is pulled  }
{ into IncludeTestParent.pas via the {$I ...} directive at compile    }
{ time. If DelphiScript honours $I, the proc below is callable from   }
{ MCP_RunIncludeProbe but should NOT appear in the File > Run Script  }
{ dialog tree.                                                         }

Function _IncProbeInnerMarker : String;
Begin
    Result := 'inner-marker-12345';
End;

Procedure _IncProbeInnerOnlyProc;
Begin
    { This proc exists only in the included file. If it shows up in  }
    { the Run Script dialog when the included file is NOT a Document }
    { in the .PrjScr, then $I does not hide procs from the tree.      }
    ShowMessage('Inner-only proc was called directly');
End;
