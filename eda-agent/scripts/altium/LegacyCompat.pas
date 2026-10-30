{ SPDX-License-Identifier: MIT }
{ Adapted business handlers from coffeenmusic; transport removed.
MIT License

Copyright (c) 2025 Siddharth Ahuja

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.}

function Legacy_GetBoardSafe(Dummy: Integer): IPCB_Board;
begin
    Result := nil;
    if (PCBServer <> nil) then
        Result := GetPCBBoardAnywhere(0);
end;

// Nil-safe access to the current PCB library (same hazard as GetBoardSafe)








function Legacy_GetPcbLibSafe(Dummy: Integer): IPCB_Library;
begin
    Result := nil;
    if (PCBServer <> nil) then
        Result := PCBServer.GetCurrentPCBLibrary;
end;

// Create many footprints in a single script Run from a spec file (plain
// text, pipe-delimited; coords in mils, layers as names, shapes/hole types
// as raw enum ints - symmetric with get_footprint_primitives):
//   FPLIB|<path to .PcbLib>              (optional first line - focus/open)
//   FOOTPRINT|<name>|<description>
//   PAD|name|x|y|rot|layer|plated|hole_size|hole_type|hole_width|hole_rot|top_x|top_y|top_shape[|corner_pct[|mode|mid_x|mid_y|mid_shape|bot_x|bot_y|bot_shape]]
//   TRACK|x1|y1|x2|y2|width|layer
//   ARC|cx|cy|radius|start_angle|end_angle|width|layer
//   FILL|x1|y1|x2|y2|rotation|layer
//   TEXT|x|y|size|width|rotation|layer|mirror|ttf|text








function Legacy_GetOpenOutputJob(): String;
var
    Project     : IProject;
    ProjectIdx, I  : Integer;
    Doc: IDocument;
    DocKind: String;
begin
    result := '';
    for ProjectIdx := 0 to GetWorkspace.DM_ProjectCount - 1 do
    begin
        Project := GetWorkspace.DM_Projects(ProjectIdx);
        if Project = Nil then Exit;

        // Iterate all documents in the project
        for I := 0 to Project.DM_LogicalDocumentCount - 1 do
        begin
            Doc := Project.DM_LogicalDocuments(I);
            DocKind := Doc.DM_DocumentKind;
            if DocKind = 'OUTPUTJOB' then
            begin
                result := Doc.DM_FullPath;
                Exit;
            end;
        end;
    end;
end;

// Return the Index-th (0-based) field of a pipe-delimited string,
// e.g. GetFieldFromPipeString('R1|100|200', 1) = '100'.
// Returns '' if the field does not exist.








function Legacy_EnsureDocumentFocused(CommandName: String; ViewTypeHint: String): Boolean;
var
    I           : Integer;
    Project     : IProject;
    Doc         : IDocument;
    DocFound    : Boolean;
    CurrentDoc  : IServerDocument;
    DocumentKind: String;
    LogMessage  : String;
    OutJobPath: String;
begin
    Result := False;
    DocFound := False;
    DocumentKind := 'PCB'; // 'Blank'

    // Commands that handle their own document management - skip focusing
    if (CommandName = 'search_library_symbol') or
       (CommandName = 'build_circuit') or
       (CommandName = 'get_symbol_primitives') or
       (CommandName = 'create_symbols_batch') or
       (CommandName = 'get_footprint_primitives') or
       (CommandName = 'create_footprints_batch') or
       (CommandName = 'create_pcb_footprint') then
    begin
        Result := True;
        Exit;
    end;

    // For PCB-related commands, ensure PCB is available first
    if (CommandName = 'create_net_class')                    or
       (CommandName = 'get_all_component_data')              or
       (CommandName = 'get_all_components')                  or
       (CommandName = 'get_all_nets')                        or
       (CommandName = 'get_component_pins')                  or
       (CommandName = 'get_pcb_layers')                      or
       (CommandName = 'get_pcb_rules')                       or
       (CommandName = 'get_selected_components_coordinates') or
       (CommandName = 'layout_duplicator')                   or
       (CommandName = 'layout_duplicator_apply')             or
       (CommandName = 'move_components')                     or
       (CommandName = 'place_components')                    or
       (CommandName = 'check_placement')                     or
       (CommandName = 'get_net_connections')                 or
       (CommandName = 'set_component_position')              or
       (CommandName = 'set_pcb_layer_visibility')            or
       (CommandName = 'get_pcb_layer_stackup')               then
    begin
        DocumentKind := 'PCB';
    end
    // Screenshots can target either domain - follow the caller's view_type
    // instead of always demanding a PCB.
    else if (CommandName = 'take_view_screenshot') then
    begin
        if LowerCase(ViewTypeHint) = 'sch' then
            DocumentKind := 'SCH'
        else
            DocumentKind := 'PCB';
    end
    else if (CommandName = 'create_schematic_symbol')        or
            (CommandName = 'get_library_symbol_reference')   then
    begin
        DocumentKind := 'SCHLIB';
    end
    else if (CommandName = 'create_pcb_footprint') then
    begin
        DocumentKind := 'PCBLIB';
    end
    else if (CommandName = 'get_schematic_data')             then
    begin
        DocumentKind := 'SCH';
    end
    else if (CommandName = 'get_output_job_containers')       or
            (CommandName = 'run_output_jobs')                then
    begin
        DocumentKind := 'OUTJOB';
    end;
    // Default to user argument if command not recognized

    LogMessage := 'Attempting to focus ' + DocumentKind + ' document';

    // Log the current focused document first
    if DocumentKind = 'PCB' then
    begin
        if PCBServer <> nil then
            LogMessage := LogMessage + '. Current PCB: ' + BoolToStr(Legacy_GetBoardSafe(0) <> nil, True);
    end
    else if DocumentKind = 'SCH' then
    begin
        if SchServer <> nil then
            LogMessage := LogMessage + '. Current SCH: ' + BoolToStr(SchServer.GetCurrentSchDocument <> nil, True);
    end
    else if DocumentKind = 'SCHLIB' then
    begin
        if SchServer <> nil then
        begin
            CurrentDoc := SchServer.GetCurrentSchDocument;
            LogMessage := LogMessage + '. Current SCHLIB: ' + BoolToStr((CurrentDoc <> nil) and (CurrentDoc.ObjectID = eSchLib), True);
        end;
    end;

    // AppendLog(LogMessage); // For debugging

    // Retrieve the current project
    Project := GetWorkspace.DM_FocusedProject;
    If Project = Nil Then
    begin
        // No project is open
        Exit;
    end;

    // Check if the correct document type is already focused
    if (DocumentKind = 'PCB') and (PCBServer <> Nil) then
    begin
        if Legacy_GetBoardSafe(0) <> Nil then
        begin
            Result := True;
            Exit;
        end;
    end
    else if (DocumentKind = 'SCH') and (SchServer <> Nil) then
    begin
        CurrentDoc := SchServer.GetCurrentSchDocument;
        if CurrentDoc <> Nil then
        begin
            Result := True;
            Exit;
        end;
    end
    else if (DocumentKind = 'SCHLIB') and (SchServer <> Nil) then
    begin
        CurrentDoc := SchServer.GetCurrentSchDocument;
        if (CurrentDoc <> Nil) and (CurrentDoc.ObjectId = eSchLib) then
        begin
            Result := True;
            Exit;
        end;
    end
    else if (DocumentKind = 'PCBLIB') and (PCBServer <> Nil) then
    begin
        if Legacy_GetPcbLibSafe(0) <> Nil then
        begin
            Result := True;
            Exit;
        end;
    end
    else if (DocumentKind = 'OUTJOB') then
    begin
        OutJobPath := Legacy_GetOpenOutputJob();
        if OutJobPath <> '' then
        begin
            Result := True;
            Exit;
        end;
    end;

    // Try to find and focus the required document type
    For I := 0 to Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(I);
        If Doc.DM_DocumentKind = DocumentKind Then
        Begin
            DocFound := True;
            // Try to open and focus the document
            Doc.DM_OpenAndFocusDocument;
            // Give it a moment to focus
            Sleep(500);

            // Verify that the document is now focused
            if DocumentKind = 'PCB' then
            begin
                if Legacy_GetBoardSafe(0) <> Nil then
                begin
                    Result := True;
                    // AppendLog('Successfully focused PCB document');
                    Exit;
                end;
            end
            else if DocumentKind = 'SCH' then
            begin
                CurrentDoc := SchServer.GetCurrentSchDocument;
                if (CurrentDoc <> Nil) then
                begin
                    Result := True;
                    // AppendLog('Successfully focused SCH document');
                    Exit;
                end;
            end
            else if DocumentKind = 'SCHLIB' then
            begin
                CurrentDoc := SchServer.GetCurrentSchDocument;
                if (CurrentDoc <> Nil) and (CurrentDoc.ObjectID = eSchLib) then
                begin
                    Result := True;
                    // AppendLog('Successfully focused SCHLIB document');
                    Exit;
                end;
            end
            else if DocumentKind = 'PCBLIB' then
            begin
                if Legacy_GetPcbLibSafe(0) <> Nil then
                begin
                    Result := True;
                    Exit;
                end;
            end
            else if DocumentKind = 'OUTJOB' then
            begin
                CurrentDoc := SchServer.GetCurrentSchDocument;
                if (CurrentDoc <> Nil) then
                begin
                    Result := True;
                    Exit;
                end;
            end;
        End;
    End;

    // TODO: Do I want to iterate through all workspace projects to find valid document if it is not current document?
    // Could use IWorkspace.DM_ProjectCount and for loop

    // No matching document found or couldn't be focused
    if not DocFound then
    begin
        AppendLog('Error: No ' + DocumentKind + ' document found in the project.');
    end
    else
    begin
        AppendLog('Error: Found ' + DocumentKind + ' document but could not focus it.');
    end;

    Result := False;
end;

// Zoom the current PCB view to the union bounding box of the given
// components (plus a margin) so a following screenshot shows them clearly.
// Returns the number of components found.






Const LegacyReplaceAll = 1; LegacyDefault = 'Blank';



Function Legacy_TrimJSON(InputStr : String) : String;
Var Value : String;
Begin
    Value := Trim(InputStr);
    If Copy(Value, Length(Value), 1) = ',' Then Value := Trim(Copy(Value, 1, Length(Value)-1));
    Result := ExtractJsonValue('{"value":' + Value + '}', 'value');
End;






Function Legacy_JSONPairStr(Name, Value : String; IsString : Boolean) : String;
Begin
    If IsString Then Result := StringReplace(JsonStr(Name, Value), '":', '": ', 1) Else Result := JsonRaw(Name, Value);
End;






Procedure Legacy_AddJSONProperty(List : TStringList; Name, Value : String; IsString : Boolean = True);
Begin List.Add(Legacy_JSONPairStr(Name, Value, IsString)); End;






Procedure Legacy_AddJSONNumber(List : TStringList; Name : String; Value : Double);
Begin List.Add(JsonRaw(Name, FloatToJsonStr(Value))); End;






Function Legacy_BuildJSONObject(Pairs : TStringList; IndentLevel : Integer = 0) : String;
Var I : Integer; Fields : String;
Begin
    Fields := '';
    For I := 0 To Pairs.Count - 1 Do Begin
        If I > 0 Then Fields := Fields + ',';
        Fields := Fields + Pairs[I];
    End;
    Result := JsonObj(Fields);
End;






Function Legacy_BuildJSONArray(Items : TStringList; ArrayName : String = ''; IndentLevel : Integer = 0) : String;
Var I : Integer; Fields : String;
Begin
    Fields := '';
    For I := 0 To Items.Count - 1 Do Begin
        If I > 0 Then Fields := Fields + ',';
        Fields := Fields + Items[I];
    End;
    Result := '[' + Fields + ']';
    If ArrayName <> '' Then Result := JsonRaw(ArrayName, Result);
End;






Function Legacy_WriteJSONToFile(JSON : TStringList; FileName : String = '') : String;
Begin
    { Business handlers return in memory. Only the EDA dispatcher writes replies. }
    Result := JSON.Text;
End;






function Legacy_GetComponentPinsFromList(LegacyBusinessDirectory: String; DesignatorsList: TStringList): String;
var
    Board           : IPCB_Board;
    Component       : IPCB_Component;
    ComponentsArray : TStringList;
    CompProps       : TStringList;
    PinsArray       : TStringList;
    GrpIter         : IPCB_GroupIterator;
    Pad             : IPCB_Pad;
    NetName         : String;
    xorigin, yorigin : Integer;
    PinProps        : TStringList;
    PinCount, PinsProcessed : Integer;
    Designator      : String;
    i               : Integer;
    OutputLines     : TStringList;
    CompX, CompY    : Double;
    CompRad         : Double;
    AbsDX, AbsDY    : Double;
    RelDX, RelDY    : Double;
begin
    // Retrieve the current board
    Board := Legacy_GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := '[]';
        Exit;
    end;

    // Get board origin coordinates
    xorigin := Board.XOrigin;
    yorigin := Board.YOrigin;

    // Create array for components
    ComponentsArray := TStringList.Create;

    try
        // Process each designator
        for i := 0 to DesignatorsList.Count - 1 do
        begin
            Designator := Trim(DesignatorsList[i]);

            // Use direct function to get component by designator
            Component := Board.GetPcbComponentByRefDes(Designator);

            if (Component <> Nil) then
            begin
                // Create component properties
                CompProps := TStringList.Create;
                PinsArray := TStringList.Create;

                try
                    // Add designator to component
                    Legacy_AddJSONProperty(CompProps, 'designator', Component.Name.Text);

                    // Component placement info so pin data is self-contained
                    CompX := CoordToMils(Component.x - xorigin);
                    CompY := CoordToMils(Component.y - yorigin);
                    CompRad := Component.Rotation * Pi / 180;
                    Legacy_AddJSONNumber(CompProps, 'x', CompX);
                    Legacy_AddJSONNumber(CompProps, 'y', CompY);
                    Legacy_AddJSONNumber(CompProps, 'rotation', Component.Rotation);
                    Legacy_AddJSONProperty(CompProps, 'layer', Layer2String(Component.Layer));

                    // Create pad iterator
                    GrpIter := Component.GroupIterator_Create;
                    try
                        GrpIter.SetState_FilterAll;
                        GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));

                        // Count pins
                        PinCount := 0;
                        Pad := GrpIter.FirstPCBObject;
                        while (Pad <> Nil) do
                        begin
                            if Pad.InComponent then
                                PinCount := PinCount + 1;
                            Pad := GrpIter.NextPCBObject;
                        end;

                        // Reset iterator
                    finally
                        Component.GroupIterator_Destroy(GrpIter);
                    end;
                    GrpIter := Component.GroupIterator_Create;
                    try
                        GrpIter.SetState_FilterAll;
                        GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));

                        // Process each pad
                        PinsProcessed := 0;
                        Pad := GrpIter.FirstPCBObject;
                        while (Pad <> Nil) do
                        begin
                            if Pad.InComponent then
                            begin
                                // Get net name if connected
                                if (Pad.Net <> Nil) then
                                    NetName := Pad.Net.Name
                                else
                                    NetName := '';

                                // Create pin properties
                                PinProps := TStringList.Create;
                                try
                                    Legacy_AddJSONProperty(PinProps, 'name', Pad.Name);
                                    Legacy_AddJSONProperty(PinProps, 'net', NetName);
                                    Legacy_AddJSONNumber(PinProps, 'x', CoordToMils(Pad.x - xorigin));
                                    Legacy_AddJSONNumber(PinProps, 'y', CoordToMils(Pad.y - yorigin));

                                    // Pad offset from the component origin in the
                                    // footprint's rotation-0 frame: un-rotate the
                                    // current offset, and un-mirror X for parts on
                                    // the bottom side. Predicted pad position after
                                    // placement = origin + (mirror-x if bottom,
                                    // then rotate CCW by rotation) applied to dx/dy.
                                    AbsDX := CoordToMils(Pad.x - xorigin) - CompX;
                                    AbsDY := CoordToMils(Pad.y - yorigin) - CompY;
                                    RelDX := AbsDX * Cos(CompRad) + AbsDY * Sin(CompRad);
                                    RelDY := -AbsDX * Sin(CompRad) + AbsDY * Cos(CompRad);
                                    if (Component.Layer = eBottomLayer) then
                                    begin
                                        RelDX := -(AbsDX * Cos(CompRad) - AbsDY * Sin(CompRad));
                                        RelDY := AbsDX * Sin(CompRad) + AbsDY * Cos(CompRad);
                                    end;
                                    // Round away trig noise (0.0001 mil resolution)
                                    RelDX := Round(RelDX * 10000) / 10000;
                                    RelDY := Round(RelDY * 10000) / 10000;
                                    Legacy_AddJSONNumber(PinProps, 'dx', RelDX);
                                    Legacy_AddJSONNumber(PinProps, 'dy', RelDY);

                                    Legacy_AddJSONNumber(PinProps, 'rotation', Pad.Rotation);
                                    Legacy_AddJSONProperty(PinProps, 'layer', Layer2String(Pad.Layer));
                                    Legacy_AddJSONNumber(PinProps, 'width', CoordToMils(Pad.XSizeOnLayer[Pad.Layer]));
                                    Legacy_AddJSONNumber(PinProps, 'height', CoordToMils(Pad.YSizeOnLayer[Pad.Layer]));
                                    Legacy_AddJSONProperty(PinProps, 'shape', ShapeToString(Pad.ShapeOnLayer[Pad.Layer]));

                                    // Add to pins array
                                    PinsArray.Add(Legacy_BuildJSONObject(PinProps, 3));

                                    // Increment counter
                                    PinsProcessed := PinsProcessed + 1;
                                finally
                                    PinProps.Free;
                                end;
                            end;

                            Pad := GrpIter.NextPCBObject;
                        end;

                        // Clean up iterator
                    finally
                        Component.GroupIterator_Destroy(GrpIter);
                    end;

                    // Add pins array to component
                    CompProps.Add(Legacy_BuildJSONArray(PinsArray, 'pins', 1));

                    // Add to components array
                    ComponentsArray.Add(Legacy_BuildJSONObject(CompProps, 1));
                finally
                    CompProps.Free;
                    PinsArray.Free;
                end;
            end
            else
            begin
                // Component not found, add empty component
                CompProps := TStringList.Create;
                try
                    Legacy_AddJSONProperty(CompProps, 'designator', Designator);
                    CompProps.Add('"pins": []');

                    // Add to components array
                    ComponentsArray.Add(Legacy_BuildJSONObject(CompProps, 1));
                finally
                    CompProps.Free;
                end;
            end;
        end;

        // Build the final JSON array
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := Legacy_BuildJSONArray(ComponentsArray);
            Result := Legacy_WriteJSONToFile(OutputLines, LegacyBusinessDirectory+'\temp_pins_data.json');
        finally
            OutputLines.Free;
        end;
    finally
        ComponentsArray.Free;
    end;
end;

// Set absolute position of a single component








function Legacy_ExecuteGetComponentPins(RequestData: TStringList): String;
var
    ParamValue: String;
    i: Integer;
    DesignatorsList: TStringList;
begin
    DesignatorsList := TStringList.Create;
    try
        // Look through all the RequestData lines to find designators
        for i := 0 to RequestData.Count - 1 do
        begin
            if (Pos('"designators"', RequestData[i]) > 0) then
            begin
                // Found the designators parameter
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')

                while (i < RequestData.Count) and (Copy(Trim(RequestData[i]), 1, 1) <> ']') do
                begin
                    // This is an array element
                    // Extract the designator value
                    ParamValue := RequestData[i];
                    ParamValue := Legacy_TrimJSON(ParamValue);

                    if (ParamValue <> '') and (ParamValue <> '[') then
                        DesignatorsList.Add(ParamValue);

                    i := i + 1;
                end;

                break;
            end;
        end;

        if DesignatorsList.Count > 0 then
        begin
            Result := Legacy_GetComponentPinsFromList(WorkspaceDir, DesignatorsList);
        end
        else
        begin
            AppendLog('Error: No designators found for get_component_pins');
            Result := '';
        end;
    finally
        DesignatorsList.Free;
    end;
end;

// Extract the create net class logic








Procedure Legacy_AddJSONInteger(List : TStringList; Name : String; Value : Integer);
Begin List.Add(JsonInt(Name, Value)); End;






function Legacy_GetLibrarySymbolReference(LegacyBusinessDirectory: String): String;
var
    CurrentLib       : ISch_Lib;
    SchComponent     : ISch_Component;
    PinIterator      : ISch_Iterator;
    Pin              : ISch_Pin;
    ComponentProps   : TStringList;
    PinsArray        : TStringList;
    PinProps         : TStringList;
    OutputLines      : TStringList;
    PinName, PinNum  : String;
    PinType          : String;
    PinOrient        : String;
    PinX, PinY       : Integer;
begin
    Result := '';

    // Check if we have a schematic library document
    CurrentLib := SchServer.GetCurrentSchDocument;
    if (CurrentLib.ObjectID <> eSchLib) Then
    begin
        Result := 'ERROR: Please open a schematic library document';
        Exit;
    end;

    // Get the currently focused component from the library
    SchComponent := CurrentLib.CurrentSchComponent;
    if SchComponent = Nil Then
    begin
        Result := 'ERROR: No component is currently selected in the library';
        Exit;
    end;

    // Create component properties
    ComponentProps := TStringList.Create;

    try
        // Add basic component properties
        Legacy_AddJSONProperty(ComponentProps, 'library_name', ExtractFileName(CurrentLib.DocumentName));
        Legacy_AddJSONProperty(ComponentProps, 'component_name', SchComponent.LibReference);
        Legacy_AddJSONProperty(ComponentProps, 'description', SchComponent.ComponentDescription);
        Legacy_AddJSONProperty(ComponentProps, 'designator', SchComponent.Designator.Text);
        Legacy_AddJSONInteger(ComponentProps, 'part_count', SchComponent.PartCount);

        // Create an array for pins
        PinsArray := TStringList.Create;

        try
            // Create pin iterator
            PinIterator := SchComponent.SchIterator_Create;
            try
                PinIterator.AddFilter_ObjectSet(MkSet(ePin));

                Pin := PinIterator.FirstSchObject;

                // Process all pins
                while (Pin <> nil) do
                begin
                    // Create pin properties
                    PinProps := TStringList.Create;

                    try
                        // Get pin properties
                        PinNum := Pin.Designator;
                        PinName := Pin.Name;

                        // Convert electrical type to string
                        If (Pin.Electrical = eElectricHiZ) Then
                        Begin
PinType := 'eElectricHiZ';
                        End
                        Else If (Pin.Electrical = eElectricInput) Then
                        Begin
PinType := 'eElectricInput';
                        End
                        Else If (Pin.Electrical = eElectricIO) Then
                        Begin
PinType := 'eElectricIO';
                        End
                        Else If (Pin.Electrical = eElectricOpenCollector) Then
                        Begin
PinType := 'eElectricOpenCollector';
                        End
                        Else If (Pin.Electrical = eElectricOpenEmitter) Then
                        Begin
PinType := 'eElectricOpenEmitter';
                        End
                        Else If (Pin.Electrical = eElectricOutput) Then
                        Begin
PinType := 'eElectricOutput';
                        End
                        Else If (Pin.Electrical = eElectricPassive) Then
                        Begin
PinType := 'eElectricPassive';
                        End
                        Else If (Pin.Electrical = eElectricPower) Then
                        Begin
PinType := 'eElectricPower';
                        End
                        Else
                        Begin
PinType := 'eElectricPassive';
                        End;
                        // Convert orientation to string
                        If (Pin.Orientation = eRotate0) Then
                        Begin
PinOrient := 'eRotate0';
                        End
                        Else If (Pin.Orientation = eRotate90) Then
                        Begin
PinOrient := 'eRotate90';
                        End
                        Else If (Pin.Orientation = eRotate180) Then
                        Begin
PinOrient := 'eRotate180';
                        End
                        Else If (Pin.Orientation = eRotate270) Then
                        Begin
PinOrient := 'eRotate270';
                        End
                        Else
                        Begin
PinOrient := 'eRotate0';
                        End;
                        // Get coordinates
                        PinX := CoordToMils(Pin.Location.X);
                        PinY := CoordToMils(Pin.Location.Y);

                        // Add pin properties
                        Legacy_AddJSONProperty(PinProps, 'pin_number', PinNum);
                        Legacy_AddJSONProperty(PinProps, 'pin_name', PinName);
                        Legacy_AddJSONProperty(PinProps, 'pin_type', PinType);
                        Legacy_AddJSONProperty(PinProps, 'pin_orientation', PinOrient);
                        Legacy_AddJSONNumber(PinProps, 'x', PinX);
                        Legacy_AddJSONNumber(PinProps, 'y', PinY);
                        Legacy_AddJSONInteger(PinProps, 'owner_part_id', Pin.OwnerPartId);

                        // Add this pin to the pins array
                        PinsArray.Add(Legacy_BuildJSONObject(PinProps, 1));

                        // Move to next pin
                        Pin := PinIterator.NextSchObject;
                    finally
                        PinProps.Free;
                    end;
                end;
            finally
                SchComponent.SchIterator_Destroy(PinIterator);
            end;

            // Add pins array to component - pass empty string as the array name
            // because we're adding it directly to the ComponentProps
            ComponentProps.Add('"pins": ' + Legacy_BuildJSONArray(PinsArray));

            // Build final JSON
            OutputLines := TStringList.Create;

            try
                OutputLines.Text := Legacy_BuildJSONObject(ComponentProps);
                Result := Legacy_WriteJSONToFile(OutputLines, LegacyBusinessDirectory+'temp_symbol_reference.json');
            finally
                OutputLines.Free;
            end;
        finally
            PinsArray.Free;
        end;
    finally
        ComponentProps.Free;
    end;
end;









function Legacy_GetFieldFromPipeString(S: String; Index: Integer): String;
var
    i, FieldStart, FieldIndex: Integer;
begin
    Result := '';
    FieldIndex := 0;
    FieldStart := 1;
    for i := 1 to Length(S) do
    begin
        if S[i] = '|' then
        begin
            if FieldIndex = Index then
            begin
                Result := Copy(S, FieldStart, i - FieldStart);
                Exit;
            end;
            FieldIndex := FieldIndex + 1;
            FieldStart := i + 1;
        end;
    end;
    if FieldIndex = Index then
        Result := Copy(S, FieldStart, Length(S) - FieldStart + 1);
end;

// Modify the EnsureDocumentFocused function to handle all document types
// and return more detailed information.
// ViewTypeHint is only consulted by commands that can target more than one
// document kind (currently take_view_screenshot); pass '' otherwise.








Function Legacy_SafeStrToFloat(S : String) : Double;
Begin Result := StrToFloat(StringReplace(S, '.', DecimalSeparator, 1)); End;






function Legacy_StrToPinElectricalType(ElecType: String): TPinElectrical;
begin
    if ElecType = 'eElectricHiZ' then
        Result := eElectricHiZ
    else if ElecType = 'eElectricInput' then
        Result := eElectricInput
    else if ElecType = 'eElectricIO' then
        Result := eElectricIO
    else if ElecType = 'eElectricOpenCollector' then
        Result := eElectricOpenCollector
    else if ElecType = 'eElectricOpenEmitter' then
        Result := eElectricOpenEmitter
    else if ElecType = 'eElectricOutput' then
        Result := eElectricOutput
    else if ElecType = 'eElectricPassive' then
        Result := eElectricPassive
    else if ElecType = 'eElectricPower' then
        Result := eElectricPower
    else
        Result := eElectricPassive; // 'Blank'
end;

// Helper function to convert string to pin orientation








function Legacy_StrToPinOrientation(Orient: String): TRotationBy90;
begin
    if Orient = 'eRotate0' then
        Result := eRotate0
    else if Orient = 'eRotate90' then
        Result := eRotate90
    else if Orient = 'eRotate180' then
        Result := eRotate180
    else if Orient = 'eRotate270' then
        Result := eRotate270
    else
        Result := eRotate0; // 'Blank'
end;

// Function to get current schematic library component data








Procedure Legacy_AddJSONBoolean(List : TStringList; Name : String; Value : Boolean);
Begin List.Add(JsonBool(Name, Value)); End;






function Legacy_CreateSchematicSymbol(SymbolName: String; PinsList: TStringList; GraphicsList: TStringList; PartCount: Integer = 1): String;
var
    CurrentLib       : ISch_Lib;
    SchComponent     : ISch_Component;
    SchPin           : ISch_Pin;
    R                : ISch_Rectangle;
    SchLine          : ISch_Line;
    SchPolyline      : ISch_Polyline;
    SchPolygon       : ISch_Polygon;
    SchArc           : ISch_Arc;
    SchEllipse       : ISch_Ellipse;
    SchLabel         : ISch_Label;
    I, J, PinCount   : Integer;
    GraphicsCount    : Integer;
    Entry            : String;
    FieldValue       : String;
    GType            : String;
    GPart            : Integer;
    GWidth           : Integer;
    FIdx, VCount, V  : Integer;
    PinName, PinNum  : String;
    PinType          : String;
    PinOrient        : String;
    PinX, PinY       : Integer;
    PinOwnerPartId   : Integer;
    PinElec          : TPinElectrical;
    PinOrientation   : TRotationBy90;
    MinX, MaxX, MinY, MaxY : Integer;
    HasPins          : Boolean;
    ResultProps      : TStringList;
    Description      : String;
    OutputLines      : TStringList;
begin
    // Check if we have a schematic library document
    CurrentLib := SchServer.GetCurrentSchDocument;
    if (CurrentLib.ObjectID <> eSchLib) Then
    begin
        Result := 'ERROR: Please open a schematic library document';
        Exit;
    end;

    Description := 'New Component';  // 'Blank' description

    // Parse the pins list for description and auto-detect PartCount from max owner_part_id
    for I := 0 to PinsList.Count - 1 do
    begin
        if (Pos('Description=', PinsList[I]) = 1) then
        begin
            Description := Copy(PinsList[I], 13, Length(PinsList[I]) - 12);
        end
        else
        begin
            FieldValue := Trim(Legacy_GetFieldFromPipeString(PinsList[I], 6));
            if (FieldValue <> '') then
            begin
                PinOwnerPartId := StrToInt(FieldValue);
                if (PinOwnerPartId > PartCount) then
                    PartCount := PinOwnerPartId;
            end;
        end;
    end;

    // Also auto-detect PartCount from graphics owner part ids
    if (GraphicsList <> nil) then
        for I := 0 to GraphicsList.Count - 1 do
        begin
            FieldValue := Trim(Legacy_GetFieldFromPipeString(GraphicsList[I], 1));
            if (FieldValue <> '') then
            begin
                GPart := StrToInt(FieldValue);
                if (GPart > PartCount) then
                    PartCount := GPart;
            end;
        end;

    // Create a library component (a page of the library is created)
    SchComponent := SchServer.SchObjectFactory(eSchComponent, eCreate_Default);
    if (SchComponent = Nil) Then
    begin
        Result := 'ERROR: Failed to create component';
        Exit;
    end;

    // Set up parameters for the library component
    SchComponent.CurrentPartID := 1;
    SchComponent.DisplayMode := 0;
    SchComponent.PartCount := PartCount;

    // Define the LibReference and component description
    SchComponent.LibReference := SymbolName;
    SchComponent.ComponentDescription := Description;
    SchComponent.Designator.Text := 'U?';

    // Create a body for each part: an auto-sized rectangle when no explicit
    // graphics are given (legacy behavior), otherwise the caller's graphics
    // define the body and only the designator position is derived from pins
    PinCount := 0;
    if (GraphicsList <> nil) then
        GraphicsCount := GraphicsList.Count
    else
        GraphicsCount := 0;

    for J := 1 to PartCount do
    begin
        // Compute bounding box for this part's pins (including shared pins with OwnerPartId=0)
        MinX := 9999; MaxX := -9999; MinY := 9999; MaxY := -9999;
        HasPins := False;

        for I := 0 to PinsList.Count - 1 do
        begin
            if (Pos('Description=', PinsList[I]) = 1) then Continue;

            FieldValue := Trim(Legacy_GetFieldFromPipeString(PinsList[I], 4));
            if (FieldValue = '') then Continue;
            PinX := StrToInt(FieldValue);
            PinY := StrToInt(Trim(Legacy_GetFieldFromPipeString(PinsList[I], 5)));

            // Determine owner part id ('Blank' 1 for backward compatibility)
            FieldValue := Trim(Legacy_GetFieldFromPipeString(PinsList[I], 6));
            if (FieldValue <> '') then
                PinOwnerPartId := StrToInt(FieldValue)
            else
                PinOwnerPartId := 1;

            // Include pin in this part's bounding box if it belongs to this part or is shared (0)
            if (PinOwnerPartId = J) or (PinOwnerPartId = 0) then
            begin
                MinX := Min(MinX, PinX);
                MaxX := Max(MaxX, PinX);
                MinY := Min(MinY, PinY);
                MaxY := Max(MaxY, PinY);
                HasPins := True;
            end;
        end;

        // Default rectangle if no pins for this part
        if not HasPins then
        begin
            MinX := 300; MinY := 0; MaxX := 1000; MaxY := 1000;
        end;

        // Create an auto-sized body rectangle only when no graphics are
        // given AND the part actually has content - a symbol with neither
        // pins nor graphics stays empty
        if (GraphicsCount = 0) and HasPins then
        begin
            R := SchServer.SchObjectFactory(eRectangle, eCreate_Default);
            if (R <> Nil) Then
            begin
                R.LineWidth := eSmall;
                R.Location := Point(MilsToCoord(MinX), MilsToCoord(MinY - 100));
                R.Corner := Point(MilsToCoord(MaxX), MilsToCoord(MaxY + 100));
                R.AreaColor := $00B0FFFF; // Yellow (BGR format)
                R.Color := $00FF0000;     // Blue (BGR format)
                R.IsSolid := True;
                R.OwnerPartId := J;
                R.OwnerPartDisplayMode := 0;
                SchComponent.AddSchObject(R);
            end;
        end;

        // Position designator using Part 1's bounding box
        if (J = 1) then
            SchComponent.Designator.Location := Point(MilsToCoord(MinX), MilsToCoord(MaxY + 100));
    end;

    // Create explicit graphic primitives. Entry formats (coords in mils,
    // width 0..3 = zero/small/medium/large, solid 0/1):
    //   line|part|width|x1|y1|x2|y2
    //   polyline|part|width|x1|y1|x2|y2|...
    //   polygon|part|width|solid|x1|y1|x2|y2|...
    //   rectangle|part|width|solid|x1|y1|x2|y2
    //   arc|part|width|cx|cy|radius|start_angle|end_angle
    //   ellipse|part|width|solid|cx|cy|radius|secondary_radius
    //   label|part|x|y|text
    for I := 0 to GraphicsCount - 1 do
    begin
        Entry := Trim(GraphicsList[I]);
        if (Entry = '') then Continue;

        GType := LowerCase(Trim(Legacy_GetFieldFromPipeString(Entry, 0)));

        FieldValue := Trim(Legacy_GetFieldFromPipeString(Entry, 1));
        if (FieldValue <> '') then
            GPart := StrToInt(FieldValue)
        else
            GPart := 1;

        FieldValue := Trim(Legacy_GetFieldFromPipeString(Entry, 2));
        if (FieldValue <> '') then
            GWidth := StrToInt(FieldValue)
        else
            GWidth := 1;

        if (GType = 'line') then
        begin
            SchLine := SchServer.SchObjectFactory(eLine, eCreate_Default);
            if (SchLine <> Nil) then
            begin
                SchLine.LineWidth := GWidth;
                SchLine.Location := Point(MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 3))),
                                          MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 4))));
                SchLine.Corner := Point(MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 5))),
                                        MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 6))));
                SchLine.OwnerPartId := GPart;
                SchLine.OwnerPartDisplayMode := 0;
                SchComponent.AddSchObject(SchLine);
            end;
        end
        else if (GType = 'polyline') then
        begin
            SchPolyline := SchServer.SchObjectFactory(ePolyline, eCreate_Default);
            if (SchPolyline <> Nil) then
            begin
                SchPolyline.LineWidth := GWidth;
                // Count coordinate fields from index 3 up
                VCount := 0;
                while (Trim(Legacy_GetFieldFromPipeString(Entry, 3 + VCount)) <> '') do
                    VCount := VCount + 1;
                VCount := VCount div 2;
                SchPolyline.VerticesCount := VCount;
                for V := 1 to VCount do
                    SchPolyline.Vertex[V] := Point(MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 3 + (V-1)*2))),
                                                   MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 4 + (V-1)*2))));
                SchPolyline.OwnerPartId := GPart;
                SchPolyline.OwnerPartDisplayMode := 0;
                SchComponent.AddSchObject(SchPolyline);
            end;
        end
        else if (GType = 'polygon') then
        begin
            SchPolygon := SchServer.SchObjectFactory(ePolygon, eCreate_Default);
            if (SchPolygon <> Nil) then
            begin
                SchPolygon.LineWidth := GWidth;
                SchPolygon.IsSolid := (Trim(Legacy_GetFieldFromPipeString(Entry, 3)) = '1');
                SchPolygon.AreaColor := $00B0FFFF; // Standard body yellow (BGR)
                SchPolygon.Color := $00FF0000;     // Standard body blue (BGR)
                VCount := 0;
                while (Trim(Legacy_GetFieldFromPipeString(Entry, 4 + VCount)) <> '') do
                    VCount := VCount + 1;
                VCount := VCount div 2;
                SchPolygon.VerticesCount := VCount;
                for V := 1 to VCount do
                    SchPolygon.Vertex[V] := Point(MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 4 + (V-1)*2))),
                                                  MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 5 + (V-1)*2))));
                SchPolygon.OwnerPartId := GPart;
                SchPolygon.OwnerPartDisplayMode := 0;
                SchComponent.AddSchObject(SchPolygon);
            end;
        end
        else if (GType = 'rectangle') then
        begin
            R := SchServer.SchObjectFactory(eRectangle, eCreate_Default);
            if (R <> Nil) then
            begin
                R.LineWidth := GWidth;
                R.IsSolid := (Trim(Legacy_GetFieldFromPipeString(Entry, 3)) = '1');
                R.AreaColor := $00B0FFFF;
                R.Color := $00FF0000;
                R.Location := Point(MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 4))),
                                    MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 5))));
                R.Corner := Point(MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 6))),
                                  MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 7))));
                R.OwnerPartId := GPart;
                R.OwnerPartDisplayMode := 0;
                SchComponent.AddSchObject(R);
            end;
        end
        else if (GType = 'arc') then
        begin
            SchArc := SchServer.SchObjectFactory(eArc, eCreate_Default);
            if (SchArc <> Nil) then
            begin
                SchArc.LineWidth := GWidth;
                SchArc.Location := Point(MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 3))),
                                         MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 4))));
                SchArc.Radius := MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 5)));
                SchArc.StartAngle := Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 6));
                SchArc.EndAngle := Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 7));
                SchArc.OwnerPartId := GPart;
                SchArc.OwnerPartDisplayMode := 0;
                SchComponent.AddSchObject(SchArc);
            end;
        end
        else if (GType = 'elliptical_arc') then
        begin
            SchArc := SchServer.SchObjectFactory(eEllipticalArc, eCreate_Default);
            if (SchArc <> Nil) then
            begin
                SchArc.LineWidth := GWidth;
                SchArc.Location := Point(MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 3))),
                                         MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 4))));
                SchArc.Radius := MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 5)));
                SchArc.SecondaryRadius := MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 6)));
                SchArc.StartAngle := Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 7));
                SchArc.EndAngle := Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 8));
                SchArc.OwnerPartId := GPart;
                SchArc.OwnerPartDisplayMode := 0;
                SchComponent.AddSchObject(SchArc);
            end;
        end
        else if (GType = 'ellipse') then
        begin
            SchEllipse := SchServer.SchObjectFactory(eEllipse, eCreate_Default);
            if (SchEllipse <> Nil) then
            begin
                SchEllipse.LineWidth := GWidth;
                SchEllipse.IsSolid := (Trim(Legacy_GetFieldFromPipeString(Entry, 3)) = '1');
                SchEllipse.Location := Point(MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 4))),
                                             MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 5))));
                SchEllipse.Radius := MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 6)));
                SchEllipse.SecondaryRadius := MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 7)));
                SchEllipse.OwnerPartId := GPart;
                SchEllipse.OwnerPartDisplayMode := 0;
                SchComponent.AddSchObject(SchEllipse);
            end;
        end
        else if (GType = 'label') then
        begin
            SchLabel := SchServer.SchObjectFactory(eLabel, eCreate_Default);
            if (SchLabel <> Nil) then
            begin
                // label|part|x|y|text (no width field)
                SchLabel.Location := Point(MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 2))),
                                           MilsToCoord(Legacy_SafeStrToFloat(Legacy_GetFieldFromPipeString(Entry, 3))));
                SchLabel.Text := Legacy_GetFieldFromPipeString(Entry, 4);
                SchLabel.OwnerPartId := GPart;
                SchLabel.OwnerPartDisplayMode := 0;
                SchComponent.AddSchObject(SchLabel);
            end;
        end;
    end;

    // Add pins to the component. Format:
    //   number|name|electrical|orientation|x|y[|owner_part_id[|length[|show_name[|show_designator]]]]
    for I := 0 to PinsList.Count - 1 do
    begin
        if (Pos('Description=', PinsList[I]) = 1) then Continue;

        Entry := PinsList[I];
        FieldValue := Trim(Legacy_GetFieldFromPipeString(Entry, 5));
        if (FieldValue = '') then Continue;

        PinNum := Trim(Legacy_GetFieldFromPipeString(Entry, 0));
        // Pin name is NOT trimmed: some libraries carry trailing spaces in
        // pin names and round-trip fidelity preserves them exactly
        PinName := Legacy_GetFieldFromPipeString(Entry, 1);
        PinType := Trim(Legacy_GetFieldFromPipeString(Entry, 2));
        PinOrient := Trim(Legacy_GetFieldFromPipeString(Entry, 3));
        PinX := StrToInt(Trim(Legacy_GetFieldFromPipeString(Entry, 4)));
        PinY := StrToInt(FieldValue);

        // Determine owner part id ('Blank' 1 for backward compatibility)
        FieldValue := Trim(Legacy_GetFieldFromPipeString(Entry, 6));
        if (FieldValue <> '') then
            PinOwnerPartId := StrToInt(FieldValue)
        else
            PinOwnerPartId := 1;

        // Create a pin
        SchPin := SchServer.SchObjectFactory(ePin, eCreate_Default);
        if (SchPin = Nil) Then
            Continue;

        // Set pin properties
        PinElec := Legacy_StrToPinElectricalType(PinType);
        PinOrientation := Legacy_StrToPinOrientation(PinOrient);

        SchPin.Designator := PinNum;
        SchPin.Name := PinName;
        SchPin.Electrical := PinElec;
        SchPin.Orientation := PinOrientation;
        SchPin.Location := Point(MilsToCoord(PinX), MilsToCoord(PinY));

        // Optional pin length in mils
        FieldValue := Trim(Legacy_GetFieldFromPipeString(Entry, 7));
        if (FieldValue <> '') then
            SchPin.PinLength := MilsToCoord(Legacy_SafeStrToFloat(FieldValue));

        // Optional name/designator visibility (1 = shown, 0 = hidden)
        FieldValue := Trim(Legacy_GetFieldFromPipeString(Entry, 8));
        if (FieldValue <> '') then
            SchPin.ShowName := (FieldValue = '1');
        FieldValue := Trim(Legacy_GetFieldFromPipeString(Entry, 9));
        if (FieldValue <> '') then
            SchPin.ShowDesignator := (FieldValue = '1');

        // Set ownership to the specified part (0 = shared across all parts)
        SchPin.OwnerPartId := PinOwnerPartId;
        SchPin.OwnerPartDisplayMode := 0;

        SchComponent.AddSchObject(SchPin);
        PinCount := PinCount + 1;
    end;

    // Add the component to the library
    CurrentLib.AddSchComponent(SchComponent);

    // Send a system notification that a new component has been added to the library
    SchServer.RobotManager.SendMessage(nil, c_BroadCast, SCHM_PrimitiveRegistration, SchComponent.I_ObjectAddress);
    CurrentLib.CurrentSchComponent := SchComponent;

    // Refresh library
    CurrentLib.GraphicallyInvalidate;

    // Create result JSON
    ResultProps := TStringList.Create;
    try
        Legacy_AddJSONBoolean(ResultProps, 'success', True);
        Legacy_AddJSONProperty(ResultProps, 'component_name', SymbolName);
        Legacy_AddJSONInteger(ResultProps, 'pins_count', PinCount);
        Legacy_AddJSONInteger(ResultProps, 'part_count', PartCount);

        // Build final JSON
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := Legacy_BuildJSONObject(ResultProps);
            Result := OutputLines.Text;
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
    end;
end;

// Function to search for a symbol in a schematic library and navigate to it








function Legacy_ExecuteCreateSchematicSymbol(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    ComponentName: String;
    PartCount: Integer;
    PinsList: TStringList;
    GraphicsList: TStringList;
begin
    // Look for component name
    ComponentName := '';
    PartCount := 1;  // 'Blank' to single-part symbol
    PinsList := TStringList.Create;
    GraphicsList := TStringList.Create;

    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for component name
            if (Pos('"symbol_name"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ComponentName := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ComponentName := Legacy_TrimJSON(ComponentName);
            end
            // Look for part_count
            else if (Pos('"part_count"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := Legacy_TrimJSON(ParamValue);
                PartCount := StrToInt(ParamValue);
            end
            // Look for pins array
            else if (Pos('"pins"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')

                while (i < RequestData.Count) and (Copy(Trim(RequestData[i]), 1, 1) <> ']') do
                begin
                    // Extract the pin data
                    ParamValue := RequestData[i];
                    ParamValue := Legacy_TrimJSON(ParamValue);

                    if (ParamValue <> '') and (ParamValue <> '[') then
                        PinsList.Add(ParamValue);

                    i := i + 1;
                end;
            end
            // Look for graphics array
            else if (Pos('"graphics"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')

                while (i < RequestData.Count) and (Copy(Trim(RequestData[i]), 1, 1) <> ']') do
                begin
                    ParamValue := RequestData[i];
                    ParamValue := Legacy_TrimJSON(ParamValue);

                    if (ParamValue <> '') and (ParamValue <> '[') then
                        GraphicsList.Add(ParamValue);

                    i := i + 1;
                end;
            end
            // Look for description
            else if (Pos('"description"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := Legacy_TrimJSON(ParamValue);
                PinsList.Add('Description=' + ParamValue);
            end;
        end;

        if ComponentName <> '' then
        begin
            Result := Legacy_CreateSchematicSymbol(ComponentName, PinsList, GraphicsList, PartCount);
        end
        else
        begin
            AppendLog('Error: No component name provided');
            Result := '';
        end;
    finally
        PinsList.Free;
        GraphicsList.Free;
    end;
end;

// Extract the set PCB layer visibility logic







function Legacy_GetSchematicData(LegacyBusinessDirectory: String): String;
var
    Project     : IProject;
    Doc         : IDocument;
    CurrentSch  : ISch_Document;
    Iterator    : ISch_Iterator;
    PIterator   : ISch_Iterator;
    Component   : ISch_Component;
    Parameter, NextParameter : ISch_Parameter;
    Rect        : TCoordRect;
    ComponentsArray : TStringList;
    CompProps   : TStringList;
    ParamsProps : TStringList;
    OutputLines : TStringList;
    Designator, Sheet, ParameterName, ParameterValue : String;
    x, y, width, height, rotation : String;
    left, right, top, bottom : String;
    i : Integer;
    SchematicCount, ComponentCount : Integer;
begin
    Result := '';

    // Retrieve the current project
    Project := GetWorkspace.DM_FocusedProject;
    If (Project = Nil) Then
    begin
        AppendLog('Error: No project is currently open');
        Exit;
    end;

    // Create array for components
    ComponentsArray := TStringList.Create;

    try
        // Count the number of schematic documents
        SchematicCount := 0;
        For i := 0 to Project.DM_LogicalDocumentCount - 1 Do
        Begin
            Doc := Project.DM_LogicalDocuments(i);
            If Doc.DM_DocumentKind = 'SCH' Then
                SchematicCount := SchematicCount + 1;
        End;

        // Process each schematic document
        ComponentCount := 0;
        For i := 0 to Project.DM_LogicalDocumentCount - 1 Do
        Begin
            Doc := Project.DM_LogicalDocuments(i);
            If Doc.DM_DocumentKind = 'SCH' Then
            Begin
                // Open the schematic document
                Client.OpenDocument('SCH', Doc.DM_FullPath);
                CurrentSch := SchServer.GetSchDocumentByPath(Doc.DM_FullPath);

                If (CurrentSch <> Nil) Then
                Begin
                    // Get schematic components
                    Iterator := CurrentSch.SchIterator_Create;
                    try
                        Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));

                        Component := Iterator.FirstSchObject;
                        While (Component <> Nil) Do
                        Begin
                            // Create component properties
                            CompProps := TStringList.Create;

                            try
                                // Get basic component properties
                                Designator := Component.Designator.Text;
                                Sheet := Doc.DM_FullPath;

                                // Get position, dimensions and rotation
                                x := FloatToJsonStr(CoordToMils(Component.Location.X));
                                y := FloatToJsonStr(CoordToMils(Component.Location.Y));

                                Rect := Component.BoundingRectangle;
                                left := FloatToJsonStr(CoordToMils(Rect.Left));
                                right := FloatToJsonStr(CoordToMils(Rect.Right));
                                top := FloatToJsonStr(CoordToMils(Rect.Top));
                                bottom := FloatToJsonStr(CoordToMils(Rect.Bottom));

                                width := FloatToJsonStr(CoordToMils(Rect.Right - Rect.Left));
                                height := FloatToJsonStr(CoordToMils(Rect.Bottom - Rect.Top));

                                If Component.Orientation = eRotate0 Then
                                    rotation := '0'
                                Else If Component.Orientation = eRotate90 Then
                                    rotation := '90'
                                Else If Component.Orientation = eRotate180 Then
                                    rotation := '180'
                                Else If Component.Orientation = eRotate270 Then
                                    rotation := '270'
                                Else
                                    rotation := '0';

                                // Add component properties
                                Legacy_AddJSONProperty(CompProps, 'designator', Designator);
                                Legacy_AddJSONProperty(CompProps, 'sheet', Sheet);
                                Legacy_AddJSONNumber(CompProps, 'schematic_x', StrToFloat(x));
                                Legacy_AddJSONNumber(CompProps, 'schematic_y', StrToFloat(y));
                                Legacy_AddJSONNumber(CompProps, 'schematic_width', StrToFloat(width));
                                Legacy_AddJSONNumber(CompProps, 'schematic_height', StrToFloat(height));
                                Legacy_AddJSONNumber(CompProps, 'schematic_rotation', StrToFloat(rotation));

                                // Get parameters
                                ParamsProps := TStringList.Create;
                                try
                                    // Create parameter iterator
                                    PIterator := Component.SchIterator_Create;
                                    PIterator.AddFilter_ObjectSet(MkSet(eParameter));

                                    Parameter := PIterator.FirstSchObject;

                                    // Process all parameters
                                    while (Parameter <> nil) do
                                    begin
                                        // Get this parameter's info
                                        ParameterName := Parameter.Name;
                                        ParameterValue := Parameter.Text;

                                        // Add parameter to the list
                                        Legacy_AddJSONProperty(ParamsProps, ParameterName, ParameterValue);

                                        // Move to next parameter
                                        Parameter := PIterator.NextSchObject;
                                    end;

                                    Component.SchIterator_Destroy(PIterator);

                                    // Add parameters to component
                                    CompProps.Add('"parameters": ' + Legacy_BuildJSONObject(ParamsProps, 2));

                                    // Add to components array
                                    ComponentsArray.Add(Legacy_BuildJSONObject(CompProps, 1));
                                    ComponentCount := ComponentCount + 1;
                                finally
                                    ParamsProps.Free;
                                end;
                            finally
                                CompProps.Free;
                            end;

                            // Move to next component
                            Component := Iterator.NextSchObject;
                        End;
                    finally
                        CurrentSch.SchIterator_Destroy(Iterator);
                    end;
                End;
            End;
        End;

        // Build the final JSON array
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := Legacy_BuildJSONArray(ComponentsArray);
            Result := Legacy_WriteJSONToFile(OutputLines, LegacyBusinessDirectory+'temp_schematic_data.json');
        finally
            OutputLines.Free;
        end;
    finally
        ComponentsArray.Free;
    end;
end;


{..............................................................................}
{ Circuit builder                                                              }
{                                                                              }
{ Builds a wired schematic from a spec file. dev/SCHEMATIC_CONVENTIONS.md holds }
{ the drafting rules the spec is expected to already satisfy - this code places }
{ what it is told and reports what it did; it does not lay out.                 }
{                                                                              }
{ Spec records (pipe-delimited, one per line, coordinates in mils):             }
{   PART|desig|symlib|symbol|designitemid|x|y|orient|mirror                     }
{   COMMENT|t   DESCRIPTION|t   FOOTPRINT|n   PARAM|name|value                  }
{   WIRE|x1|y1|x2|y2[|...]      JUNCTION|x|y                                    }
{   NETLABEL|x|y|orient|text    POWER|x|y|orient|style|text|shownetname         }
{   NOTE|x|y|text                                                               }
{                                                                              }
{ Uses LOCAL variables throughout, deliberately. The dev sandbox shares scratch }
{ variables between blocks and that silently corrupted results three separate   }
{ times - a loop counter overwriting a stored coordinate yields plausible wrong }
{ output rather than an error.                                                  }
{..............................................................................}

// Absolute electrical connection point of a pin. Pin.Location is the end
// attached to the BODY; the hot end is PinLength further along the pin's
// orientation. Verified 119:0 against a hand-wired production sheet.








function Legacy_GetPCBLayers(LegacyBusinessDirectory: String): String;
var
    Board           : IPCB_Board;
    TheLayerStack   : IPCB_LayerStack_V7;
    LayerObj        : IPCB_LayerObject;
    MechLayer       : IPCB_MechanicalLayer;
    AllLayersArray  : TStringList;
    CopperArray     : TStringList;
    MechArray       : TStringList;
    OtherArray      : TStringList;
    LayerProps      : TStringList;
    i               : Integer;
    OutputLines     : TStringList;
begin
    Result := '';

    // Retrieve the current board
    Board := Legacy_GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := '[]';
        Exit;
    end;

    // Get the layer stack
    TheLayerStack := Board.LayerStack_V7;
    if (TheLayerStack = nil) then
    begin
        Result := '[]';
        Exit;
    end;

    // Create arrays for different layer categories
    AllLayersArray := TStringList.Create;
    CopperArray := TStringList.Create;
    MechArray := TStringList.Create;
    OtherArray := TStringList.Create;

    try
        // Process copper (electrical) layers
        LayerObj := TheLayerStack.FirstLayer;
        while (LayerObj <> nil) do
        begin
            // Create layer properties
            LayerProps := TStringList.Create;
            try
                // Add properties
                Legacy_AddJSONProperty(LayerProps, 'name', LayerObj.Name);
                Legacy_AddJSONProperty(LayerProps, 'layer_id', IntToStr(LayerObj.V6_LayerID));
                Legacy_AddJSONProperty(LayerProps, 'layer_type', 'copper');

                if LayerSet.SignalLayers.Contains(LayerObj.V6_LayerID) then
                    Legacy_AddJSONProperty(LayerProps, 'is_signal', 'true', False)
                else
                    Legacy_AddJSONProperty(LayerProps, 'is_signal', 'false', False);

                if not LayerSet.SignalLayers.Contains(LayerObj.V6_LayerID) then
                    Legacy_AddJSONProperty(LayerProps, 'is_plane', 'true', False)
                else
                    Legacy_AddJSONProperty(LayerProps, 'is_plane', 'false', False);

                Legacy_AddJSONBoolean(LayerProps, 'is_displayed', LayerObj.IsDisplayed[Board]);
                Legacy_AddJSONBoolean(LayerProps, 'is_enabled', True);
                Legacy_AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[LayerObj.LayerID]));

                // Add to copper array
                CopperArray.Add(Legacy_BuildJSONObject(LayerProps, 1));
            finally
                LayerProps.Free;
            end;

            LayerObj := TheLayerStack.NextLayer(LayerObj);
        end;

        // Process mechanical layers
        for i := 1 to 32 do
        begin
            MechLayer := TheLayerStack.LayerObject_V7[ILayer.MechanicalLayer(i)];

            if MechLayer.MechanicalLayerEnabled then
            begin
                // Create layer properties
                LayerProps := TStringList.Create;
                try
                    // Add properties
                    Legacy_AddJSONProperty(LayerProps, 'name', MechLayer.Name);
                    Legacy_AddJSONProperty(LayerProps, 'layer_id', IntToStr(MechLayer.V6_LayerID));
                    Legacy_AddJSONProperty(LayerProps, 'layer_type', 'mechanical');
                    Legacy_AddJSONProperty(LayerProps, 'mechanical_number', IntToStr(i));
                    Legacy_AddJSONBoolean(LayerProps, 'is_displayed', MechLayer.IsDisplayed[Board]);
                    Legacy_AddJSONBoolean(LayerProps, 'is_enabled', MechLayer.MechanicalLayerEnabled);
                    Legacy_AddJSONBoolean(LayerProps, 'link_to_sheet', MechLayer.LinkToSheet);
                    Legacy_AddJSONBoolean(LayerProps, 'is_paired', Board.MechanicalPairs.LayerUsed(ILayer.MechanicalLayer(i)));
                    Legacy_AddJSONProperty(LayerProps, 'color', ColorToString(PCBServer.SystemOptions.LayerColors[MechLayer.V6_LayerID]));

                    // If layer is paired, add the pair information
                    if Board.MechanicalPairs.LayerUsed(ILayer.MechanicalLayer(i)) then
                    begin
                        // Could add pair info here if Altium API provides it
                    end;

                    // Add to mechanical array
                    MechArray.Add(Legacy_BuildJSONObject(LayerProps, 1));
                finally
                    LayerProps.Free;
                end;
            end;
        end;

        // Process other special layers
        // Top Overlay
        LayerProps := TStringList.Create;
        try
            Legacy_AddJSONProperty(LayerProps, 'name', 'Top Overlay');
            Legacy_AddJSONProperty(LayerProps, 'layer_id', IntToStr(String2Layer('Top Overlay')));
            Legacy_AddJSONProperty(LayerProps, 'layer_type', 'special');
            Legacy_AddJSONProperty(LayerProps, 'special_type', 'overlay');
            Legacy_AddJSONBoolean(LayerProps, 'is_displayed', Board.LayerIsDisplayed[String2Layer('Top Overlay')]);
            Legacy_AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[String2Layer('Top Overlay')]));
            OtherArray.Add(Legacy_BuildJSONObject(LayerProps, 1));
        finally
            LayerProps.Free;
        end;

        // Bottom Overlay
        LayerProps := TStringList.Create;
        try
            Legacy_AddJSONProperty(LayerProps, 'name', 'Bottom Overlay');
            Legacy_AddJSONProperty(LayerProps, 'layer_id', IntToStr(String2Layer('Bottom Overlay')));
            Legacy_AddJSONProperty(LayerProps, 'layer_type', 'special');
            Legacy_AddJSONProperty(LayerProps, 'special_type', 'overlay');
            Legacy_AddJSONBoolean(LayerProps, 'is_displayed', Board.LayerIsDisplayed[String2Layer('Bottom Overlay')]);
            Legacy_AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[String2Layer('Bottom Overlay')]));
            OtherArray.Add(Legacy_BuildJSONObject(LayerProps, 1));
        finally
            LayerProps.Free;
        end;

        // Top Solder Mask
        LayerProps := TStringList.Create;
        try
            Legacy_AddJSONProperty(LayerProps, 'name', 'Top Solder Mask');
            Legacy_AddJSONProperty(LayerProps, 'layer_id', IntToStr(String2Layer('Top Solder Mask')));
            Legacy_AddJSONProperty(LayerProps, 'layer_type', 'special');
            Legacy_AddJSONProperty(LayerProps, 'special_type', 'solder_mask');
            Legacy_AddJSONBoolean(LayerProps, 'is_displayed', Board.LayerIsDisplayed[String2Layer('Top Solder Mask')]);
            Legacy_AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[String2Layer('Top Solder Mask')]));
            OtherArray.Add(Legacy_BuildJSONObject(LayerProps, 1));
        finally
            LayerProps.Free;
        end;

        // Bottom Solder Mask
        LayerProps := TStringList.Create;
        try
            Legacy_AddJSONProperty(LayerProps, 'name', 'Bottom Solder Mask');
            Legacy_AddJSONProperty(LayerProps, 'layer_id', IntToStr(String2Layer('Bottom Solder Mask')));
            Legacy_AddJSONProperty(LayerProps, 'layer_type', 'special');
            Legacy_AddJSONProperty(LayerProps, 'special_type', 'solder_mask');
            Legacy_AddJSONBoolean(LayerProps, 'is_displayed', Board.LayerIsDisplayed[String2Layer('Bottom Solder Mask')]);
            Legacy_AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[String2Layer('Bottom Solder Mask')]));
            OtherArray.Add(Legacy_BuildJSONObject(LayerProps, 1));
        finally
            LayerProps.Free;
        end;

        // Top Paste
        LayerProps := TStringList.Create;
        try
            Legacy_AddJSONProperty(LayerProps, 'name', 'Top Paste');
            Legacy_AddJSONProperty(LayerProps, 'layer_id', IntToStr(String2Layer('Top Paste')));
            Legacy_AddJSONProperty(LayerProps, 'layer_type', 'special');
            Legacy_AddJSONProperty(LayerProps, 'special_type', 'paste');
            Legacy_AddJSONBoolean(LayerProps, 'is_displayed', Board.LayerIsDisplayed[String2Layer('Top Paste')]);
            Legacy_AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[String2Layer('Top Paste')]));
            OtherArray.Add(Legacy_BuildJSONObject(LayerProps, 1));
        finally
            LayerProps.Free;
        end;

        // Bottom Paste
        LayerProps := TStringList.Create;
        try
            Legacy_AddJSONProperty(LayerProps, 'name', 'Bottom Paste');
            Legacy_AddJSONProperty(LayerProps, 'layer_id', IntToStr(String2Layer('Bottom Paste')));
            Legacy_AddJSONProperty(LayerProps, 'layer_type', 'special');
            Legacy_AddJSONProperty(LayerProps, 'special_type', 'paste');
            Legacy_AddJSONBoolean(LayerProps, 'is_displayed', Board.LayerIsDisplayed[String2Layer('Bottom Paste')]);
            Legacy_AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[String2Layer('Bottom Paste')]));
            OtherArray.Add(Legacy_BuildJSONObject(LayerProps, 1));
        finally
            LayerProps.Free;
        end;

        // Drill Guide
        LayerProps := TStringList.Create;
        try
            Legacy_AddJSONProperty(LayerProps, 'name', 'Drill Guide');
            Legacy_AddJSONProperty(LayerProps, 'layer_id', IntToStr(String2Layer('Drill Guide')));
            Legacy_AddJSONProperty(LayerProps, 'layer_type', 'special');
            Legacy_AddJSONProperty(LayerProps, 'special_type', 'drill');
            Legacy_AddJSONBoolean(LayerProps, 'is_displayed', Board.LayerIsDisplayed[String2Layer('Drill Guide')]);
            Legacy_AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[String2Layer('Drill Guide')]));
            OtherArray.Add(Legacy_BuildJSONObject(LayerProps, 1));
        finally
            LayerProps.Free;
        end;

        // Drill Drawing
        LayerProps := TStringList.Create;
        try
            Legacy_AddJSONProperty(LayerProps, 'name', 'Drill Drawing');
            Legacy_AddJSONProperty(LayerProps, 'layer_id', IntToStr(String2Layer('Drill Drawing')));
            Legacy_AddJSONProperty(LayerProps, 'layer_type', 'special');
            Legacy_AddJSONProperty(LayerProps, 'special_type', 'drill');
            Legacy_AddJSONBoolean(LayerProps, 'is_displayed', Board.LayerIsDisplayed[String2Layer('Drill Drawing')]);
            Legacy_AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[String2Layer('Drill Drawing')]));
            OtherArray.Add(Legacy_BuildJSONObject(LayerProps, 1));
        finally
            LayerProps.Free;
        end;

        // Multi Layer
        LayerProps := TStringList.Create;
        try
            Legacy_AddJSONProperty(LayerProps, 'name', 'Multi Layer');
            Legacy_AddJSONProperty(LayerProps, 'layer_id', IntToStr(String2Layer('Multi Layer')));
            Legacy_AddJSONProperty(LayerProps, 'layer_type', 'special');
            Legacy_AddJSONProperty(LayerProps, 'special_type', 'multi');
            Legacy_AddJSONBoolean(LayerProps, 'is_displayed', Board.LayerIsDisplayed[String2Layer('Multi Layer')]);
            Legacy_AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[String2Layer('Multi Layer')]));
            OtherArray.Add(Legacy_BuildJSONObject(LayerProps, 1));
        finally
            LayerProps.Free;
        end;

        // Keep Out Layer
        LayerProps := TStringList.Create;
        try
            Legacy_AddJSONProperty(LayerProps, 'name', 'Keep Out Layer');
            Legacy_AddJSONProperty(LayerProps, 'layer_id', IntToStr(String2Layer('Keep Out Layer')));
            Legacy_AddJSONProperty(LayerProps, 'layer_type', 'special');
            Legacy_AddJSONProperty(LayerProps, 'special_type', 'keepout');
            Legacy_AddJSONBoolean(LayerProps, 'is_displayed', Board.LayerIsDisplayed[String2Layer('Keep Out Layer')]);
            Legacy_AddJSONProperty(LayerProps, 'color', ColorToString(Board.LayerColor[String2Layer('Keep Out Layer')]));
            OtherArray.Add(Legacy_BuildJSONObject(LayerProps, 1));
        finally
            LayerProps.Free;
        end;

        // Add additional info for the complete layer response
        LayerProps := TStringList.Create;
        try
            // Add summary information
            Legacy_AddJSONInteger(LayerProps, 'copper_layers_count', TheLayerStack.LayersInStackCount);
            Legacy_AddJSONInteger(LayerProps, 'signal_layers_count', TheLayerStack.SignalLayerCount);
            Legacy_AddJSONInteger(LayerProps, 'internal_planes_count', TheLayerStack.LayersInStackCount - TheLayerStack.SignalLayerCount);

            // Get the number of enabled mechanical layers
            i := 0;
            for i := 1 to 32 do
                if TheLayerStack.LayerObject_V7[ILayer.MechanicalLayer(i)].MechanicalLayerEnabled then
                    i := i + 1;
            Legacy_AddJSONInteger(LayerProps, 'mechanical_layers_count', i);

            // Add the layer arrays
            LayerProps.Add(Legacy_BuildJSONArray(CopperArray, 'copper_layers'));
            LayerProps.Add(Legacy_BuildJSONArray(MechArray, 'mechanical_layers'));
            LayerProps.Add(Legacy_BuildJSONArray(OtherArray, 'special_layers'));

            // Build the final JSON
            OutputLines := TStringList.Create;
            try
                OutputLines.Text := Legacy_BuildJSONObject(LayerProps);
                Result := Legacy_WriteJSONToFile(OutputLines, LegacyBusinessDirectory+'\temp_layers_data.json');
            finally
                OutputLines.Free;
            end;
        finally
            LayerProps.Free;
        end;
    finally
        AllLayersArray.Free;
        CopperArray.Free;
        MechArray.Free;
        OtherArray.Free;
    end;
end;

// Function to set layer visibility (only specified layers visible)
// Function to set layer visibility with two modes:
// - visible=true: Show only specified layers, hide all others
// - visible=false: Hide specified layers, leave others unchanged








function Legacy_SetPCBLayerVisibility(LayerNamesList: TStringList; Visible: Boolean): String;
var
    Board          : IPCB_Board;
    TheLayerStack  : IPCB_LayerStack_V7;
    LayerObj       : IPCB_LayerObject;
    MechLayer      : IPCB_MechanicalLayer;
    ResultProps    : TStringList;
    OutputLines    : TStringList;
    i, j           : Integer;
    LayerName      : String;
    LayerID        : TLayer;
    FoundCount     : Integer;
    NotFoundList   : TStringList;
    FoundLayers    : TStringList;
begin
    // Retrieve the current board
    Board := Legacy_GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := '{"success": false, "error": "No PCB document is currently active"}';
        Exit;
    end;

    // Get the layer stack
    TheLayerStack := Board.LayerStack_V7;
    if (TheLayerStack = nil) then
    begin
        Result := '{"success": false, "error": "Failed to retrieve layer stack"}';
        Exit;
    end;

    // Create lists for tracking results
    ResultProps := TStringList.Create;
    NotFoundList := TStringList.Create;
    FoundLayers := TStringList.Create;
    FoundCount := 0;

    try
        // First phase: identify all specified layers
        for i := 0 to LayerNamesList.Count - 1 do
        begin
            LayerName := LayerNamesList[i];

            // Try to find the layer by name
            // First check special layers (since they have specific names)
            if (LayerName = 'Top Overlay') or
               (LayerName = 'Bottom Overlay') or
               (LayerName = 'Top Solder Mask') or
               (LayerName = 'Bottom Solder Mask') or
               (LayerName = 'Top Paste') or
               (LayerName = 'Bottom Paste') or
               (LayerName = 'Drill Guide') or
               (LayerName = 'Drill Drawing') or
               (LayerName = 'Multi Layer') or
               (LayerName = 'Keep Out Layer') then
            begin
                // Get layer ID from name
                LayerID := String2Layer(LayerName);
                if (LayerID <> eNoLayer) then
                begin
                    FoundLayers.Add(IntToStr(LayerID));
                    FoundCount := FoundCount + 1;
                end
                else
                    NotFoundList.Add('"' + EscapeJsonString(LayerName) + '"');

                continue;
            end;

            // Check copper layers
            LayerObj := TheLayerStack.FirstLayer;
            j := 1;

            while (LayerObj <> nil) do
            begin
                if (LayerObj.Name = LayerName) then
                begin
                    FoundLayers.Add(IntToStr(LayerObj.V6_LayerID));
                    FoundCount := FoundCount + 1;
                    break;
                end;

                Inc(j);
                LayerObj := TheLayerStack.NextLayer(LayerObj);
            end;

            // If we found the layer in copper layers, continue to next layer name
            if (LayerObj <> nil) then
                continue;

            // Check mechanical layers (they can have custom names)
            for j := 1 to 32 do
            begin
                MechLayer := TheLayerStack.LayerObject_V7[ILayer.MechanicalLayer(j)];

                if MechLayer.MechanicalLayerEnabled and (MechLayer.Name = LayerName) then
                begin
                    FoundLayers.Add(IntToStr(MechLayer.V6_LayerID));
                    FoundCount := FoundCount + 1;
                    break;
                end;
            end;

            // If we've checked all layer types and didn't find a match, add to not found list
            if j > 32 then
                NotFoundList.Add('"' + EscapeJsonString(LayerName) + '"');
        end;

        // Second phase: set visibility for all layers based on mode
        if Visible then
        begin
            // Visibility mode: show only specified layers, hide all others

            // For copper layers
            LayerObj := TheLayerStack.FirstLayer;
            while (LayerObj <> nil) do
            begin
                // Check if this layer is in our found list
                if (FoundLayers.IndexOf(IntToStr(LayerObj.V6_LayerID)) >= 0) then
                    LayerObj.IsDisplayed[Board] := True
                else
                    LayerObj.IsDisplayed[Board] := False;

                LayerObj := TheLayerStack.NextLayer(LayerObj);
            end;

            // For mechanical layers
            for j := 1 to 32 do
            begin
                MechLayer := TheLayerStack.LayerObject_V7[ILayer.MechanicalLayer(j)];

                if MechLayer.MechanicalLayerEnabled then
                begin
                    if (FoundLayers.IndexOf(IntToStr(MechLayer.V6_LayerID)) >= 0) then
                        MechLayer.IsDisplayed[Board] := True
                    else
                        MechLayer.IsDisplayed[Board] := False;
                end;
            end;

            // For special layers
            for j := 1 to 10 do
            begin
                case j of
                    1: LayerID := String2Layer('Top Overlay');
                    2: LayerID := String2Layer('Bottom Overlay');
                    3: LayerID := String2Layer('Top Solder Mask');
                    4: LayerID := String2Layer('Bottom Solder Mask');
                    5: LayerID := String2Layer('Top Paste');
                    6: LayerID := String2Layer('Bottom Paste');
                    7: LayerID := String2Layer('Drill Guide');
                    8: LayerID := String2Layer('Drill Drawing');
                    9: LayerID := String2Layer('Multi Layer');
                    10: LayerID := String2Layer('Keep Out Layer');
                end;

                if (FoundLayers.IndexOf(IntToStr(LayerID)) >= 0) then
                    Board.LayerIsDisplayed[LayerID] := True
                else
                    Board.LayerIsDisplayed[LayerID] := False;
            end;
        end
        else
        begin
            // Hide mode: only hide specified layers, leave others unchanged

            // For copper layers
            LayerObj := TheLayerStack.FirstLayer;
            while (LayerObj <> nil) do
            begin
                // Check if this layer is in our found list
                if (FoundLayers.IndexOf(IntToStr(LayerObj.V6_LayerID)) >= 0) then
                    LayerObj.IsDisplayed[Board] := False;

                LayerObj := TheLayerStack.NextLayer(LayerObj);
            end;

            // For mechanical layers
            for j := 1 to 32 do
            begin
                MechLayer := TheLayerStack.LayerObject_V7[ILayer.MechanicalLayer(j)];

                if MechLayer.MechanicalLayerEnabled then
                begin
                    if (FoundLayers.IndexOf(IntToStr(MechLayer.V6_LayerID)) >= 0) then
                        MechLayer.IsDisplayed[Board] := False;
                end;
            end;

            // For special layers
            for j := 1 to 10 do
            begin
                case j of
                    1: LayerID := String2Layer('Top Overlay');
                    2: LayerID := String2Layer('Bottom Overlay');
                    3: LayerID := String2Layer('Top Solder Mask');
                    4: LayerID := String2Layer('Bottom Solder Mask');
                    5: LayerID := String2Layer('Top Paste');
                    6: LayerID := String2Layer('Bottom Paste');
                    7: LayerID := String2Layer('Drill Guide');
                    8: LayerID := String2Layer('Drill Drawing');
                    9: LayerID := String2Layer('Multi Layer');
                    10: LayerID := String2Layer('Keep Out Layer');
                end;

                if (FoundLayers.IndexOf(IntToStr(LayerID)) >= 0) then
                    Board.LayerIsDisplayed[LayerID] := False;
            end;
        end;

        // Update the display
        Board.ViewManager_FullUpdate;
        Board.ViewManager_UpdateLayerTabs;

        // Create result JSON
        Legacy_AddJSONBoolean(ResultProps, 'success', True);
        Legacy_AddJSONInteger(ResultProps, 'updated_count', FoundCount);

        // Add missing layers array
        if (NotFoundList.Count > 0) then
            ResultProps.Add(Legacy_BuildJSONArray(NotFoundList, 'not_found_layers'))
        else
            ResultProps.Add('"not_found_layers": []');

        // Build final JSON
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := Legacy_BuildJSONObject(ResultProps);
            Result := OutputLines.Text;
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
        NotFoundList.Free;
        FoundLayers.Free;
    end;
end;

// Function to get all PCB rules








function Legacy_ExecuteSetPCBLayerVisibility(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    SourceList: TStringList;
    Visible: Boolean;
begin
    // Create a stringlist for layer names and extract the visible parameter
    SourceList := TStringList.Create;
    Visible := False;

    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for layer_names array
            if (Pos('"layer_names"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')

                while (i < RequestData.Count) and (Copy(Trim(RequestData[i]), 1, 1) <> ']') do
                begin
                    // Extract the layer name
                    ParamValue := RequestData[i];
                    ParamValue := Legacy_TrimJSON(ParamValue);

                    if (ParamValue <> '') and (ParamValue <> '[') then
                        SourceList.Add(ParamValue);

                    i := i + 1;
                end;
            end
            // Look for visible parameter
            else if (Pos('"visible"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := Legacy_TrimJSON(ParamValue);
                Visible := (ParamValue = 'true');
            end;
        end;

        if SourceList.Count > 0 then
        begin
            Result := Legacy_SetPCBLayerVisibility(SourceList, Visible);
        end
        else
        begin
            Result := '{"success": false, "error": "No layer names provided"}';
        end;
    finally
        SourceList.Free;
    end;
end;

// Extract the set component position logic








function Legacy_GetPCBLayerStackup(LegacyBusinessDirectory): String;
var
    Board           : IPCB_Board;
    LayerIterator   : IPCB_LayerObjectIterator;
    LayerObject     : IPCB_LayerObject;
    StackupArray    : TStringList;
    LayerProps      : TStringList;
    OutputLines     : TStringList;
    TotalThickness  : Double;
    LayerCount      : Integer;
begin
    Result := '';

    // Retrieve the current board
    Board := Legacy_GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := '{"error": "No PCB document is currently active"}';
        Exit;
    end;

    // Create arrays for stackup data
    StackupArray := TStringList.Create;
    TotalThickness := 0;
    LayerCount := 0;

    try
        // Get the electrical layer iterator
        LayerIterator := Board.ElectricalLayerIterator;

        // Process each electrical layer
        while LayerIterator.Next do
        begin
            LayerObject := LayerIterator.LayerObject;

            // Create layer properties
            LayerProps := TStringList.Create;
            try
                // Basic layer information
                Legacy_AddJSONProperty(LayerProps, 'layer_name', LayerObject.Name);
                Legacy_AddJSONProperty(LayerProps, 'layer_id', Layer2String(LayerObject.LayerID));
                Legacy_AddJSONProperty(LayerProps, 'material_type', 'Copper');
                Legacy_AddJSONNumber(LayerProps, 'copper_thickness_mils', LayerObject.CopperThickness / 10000);
                Legacy_AddJSONNumber(LayerProps, 'copper_thickness_um', LayerObject.CopperThickness / 254);

                // Add copper thickness to total
                TotalThickness := TotalThickness + (LayerObject.CopperThickness / 10000);

                // Dielectric information (if present)
                if LayerObject.Dielectric.DielectricType <> eNoDielectric then
                begin
                    If (LayerObject.Dielectric.DielectricType = eCore) Then
                    Begin
Legacy_AddJSONProperty(LayerProps, 'dielectric_type', 'Core');
                    End
                    Else If (LayerObject.Dielectric.DielectricType = ePrePreg) Then
                    Begin
Legacy_AddJSONProperty(LayerProps, 'dielectric_type', 'PrePreg');
                    End
                    Else If (LayerObject.Dielectric.DielectricType = eSurfaceMaterial) Then
                    Begin
Legacy_AddJSONProperty(LayerProps, 'dielectric_type', 'Surface Material');
                    End
                    Else
                    Begin
Legacy_AddJSONProperty(LayerProps, 'dielectric_type', 'Unknown');
                    End;
                    Legacy_AddJSONProperty(LayerProps, 'dielectric_material', LayerObject.Dielectric.DielectricMaterial);
                    Legacy_AddJSONNumber(LayerProps, 'dielectric_height_mils', LayerObject.Dielectric.DielectricHeight / 10000);
                    Legacy_AddJSONNumber(LayerProps, 'dielectric_height_um', LayerObject.Dielectric.DielectricHeight / 254);
                    Legacy_AddJSONNumber(LayerProps, 'dielectric_constant', LayerObject.Dielectric.DielectricConstant);

                    // Add dielectric thickness to total
                    TotalThickness := TotalThickness + (LayerObject.Dielectric.DielectricHeight / 10000);
                end
                else
                begin
                    Legacy_AddJSONProperty(LayerProps, 'dielectric_type', 'No Dielectric');
                    Legacy_AddJSONProperty(LayerProps, 'dielectric_material', '');
                    Legacy_AddJSONNumber(LayerProps, 'dielectric_height_mils', 0);
                    Legacy_AddJSONNumber(LayerProps, 'dielectric_height_um', 0);
                    Legacy_AddJSONNumber(LayerProps, 'dielectric_constant', 0);
                end;

                // Add layer order
                Legacy_AddJSONInteger(LayerProps, 'layer_order', LayerCount + 1);

                // Add to stackup array
                StackupArray.Add(Legacy_BuildJSONObject(LayerProps, 1));
                LayerCount := LayerCount + 1;
            finally
                LayerProps.Free;
            end;
        end;

        // Create final stackup object with summary
        LayerProps := TStringList.Create;
        try
            Legacy_AddJSONInteger(LayerProps, 'total_layers', LayerCount);
            Legacy_AddJSONNumber(LayerProps, 'total_thickness_mils', TotalThickness);
            Legacy_AddJSONNumber(LayerProps, 'total_thickness_mm', TotalThickness * 0.0254);
            Legacy_AddJSONProperty(LayerProps, 'board_name', ExtractFileName(Board.FileName));

            // Add the layers array
            LayerProps.Add(Legacy_BuildJSONArray(StackupArray, 'layers'));

            // Build the final JSON
            OutputLines := TStringList.Create;
            try
                OutputLines.Text := Legacy_BuildJSONObject(LayerProps);
                Result := Legacy_WriteJSONToFile(OutputLines, LegacyBusinessDirectory+'\temp_stackup_data.json');
            finally
                OutputLines.Free;
            end;
        finally
            LayerProps.Free;
        end;
    finally
        StackupArray.Free;
    end;
end;

// Function to get all layer information from the PCB








function Legacy_ComponentMinDistance(Board: IPCB_Board; CompA, CompB: IPCB_Component): Double;
var
    ItA, ItB : IPCB_GroupIterator;
    PA, PB   : IPCB_Primitive;
    D, MinD  : Integer;
begin
    MinD := MAX_INT;

    ItA := CompA.GroupIterator_Create;
    try
        ItA.SetState_FilterAll;
        PA := ItA.FirstPCBObject;
        while (PA <> nil) do
        begin
            if (PA.ObjectId <> eTextObject) then
            begin
                ItB := CompB.GroupIterator_Create;
                ItB.SetState_FilterAll;
                PB := ItB.FirstPCBObject;
                while (PB <> nil) do
                begin
                    if (PB.ObjectId <> eTextObject) then
                    begin
                        D := Board.PrimPrimDistance(PA, PB);
                        if (D < MinD) then MinD := D;
                    end;

                    // Early exit once touching - it cannot get closer
                    if (MinD = 0) then
                        PB := nil
                    else
                        PB := ItB.NextPCBObject;
                end;
                CompB.GroupIterator_Destroy(ItB);
            end;

            if (MinD = 0) then
                PA := nil
            else
                PA := ItA.NextPCBObject;
        end;
    finally
        CompA.GroupIterator_Destroy(ItA);
    end;

    Result := CoordToMils(MinD);
end;

// Check component placement for overlaps and clearance violations.
// Targets are the given designators (or the current selection when the list
// is empty); each target is checked against every other component on the same
// side of the board. Bounding boxes act as a fast prefilter; close pairs are
// measured precisely with Board.PrimPrimDistance (minimum distance between
// any two primitives of the components, 0 = touching/overlapping).








function Legacy_CheckPlacement(DesignatorsList: TStringList; ClearanceMils: Double): String;
var
    Board           : IPCB_Board;
    Iterator        : IPCB_BoardIterator;
    Target, Other   : IPCB_Component;
    Targets         : TStringList;
    Processed       : TStringList;
    MissingArray    : TStringList;
    ViolationsArray : TStringList;
    VProps          : TStringList;
    ResultProps     : TStringList;
    RectA, RectB    : TCoordRect;
    Designator      : String;
    i               : Integer;
    OverlapX, OverlapY : Double;
    Separation      : Double;
    DistMils        : Double;
    PairsChecked    : Integer;
    IsOverlap       : Boolean;
begin
    Board := Legacy_GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := 'ERROR: No PCB document is currently active';
        Exit;
    end;

    if (ClearanceMils <= 0) then
        ClearanceMils := 6;

    Targets := TStringList.Create;
    Processed := TStringList.Create;
    MissingArray := TStringList.Create;
    ViolationsArray := TStringList.Create;
    ResultProps := TStringList.Create;
    PairsChecked := 0;

    try
        // Build the target list: explicit designators, or current selection
        if (DesignatorsList.Count > 0) then
        begin
            for i := 0 to DesignatorsList.Count - 1 do
            begin
                Designator := Trim(DesignatorsList[i]);
                if (Board.GetPcbComponentByRefDes(Designator) <> nil) then
                    Targets.Add(Designator)
                else
                    MissingArray.Add('"' + EscapeJsonString(Designator) + '"');
            end;
        end
        else
        begin
            for i := 0 to Board.SelectecObjectCount - 1 do
                if (Board.SelectecObject[i].ObjectId = eComponentObject) then
                    Targets.Add(Board.SelectecObject[i].Name.Text);
        end;

        if (Targets.Count = 0) then
        begin
            Result := 'ERROR: No components to check (no designators given and no components selected)';
            Exit;
        end;

        // Check each target against every other component on the same side
        for i := 0 to Targets.Count - 1 do
        begin
            Target := Board.GetPcbComponentByRefDes(Targets[i]);
            RectA := Target.BoundingRectangleNoNameComment;

            Iterator := Board.BoardIterator_Create;
            try
                Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
                Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
                Iterator.AddFilter_Method(eProcessAll);

                Other := Iterator.FirstPCBObject;
                while (Other <> nil) do
                begin
                    if (Other.Name.Text <> Target.Name.Text) and
                       (Other.Layer = Target.Layer) and
                       (Processed.IndexOf(Other.Name.Text) < 0) then
                    begin
                        RectB := Other.BoundingRectangleNoNameComment;

                        // Bounding-box overlap/separation in mils (negative = gap)
                        OverlapX := CoordToMils(Min(RectA.Right, RectB.Right) - Max(RectA.Left, RectB.Left));
                        OverlapY := CoordToMils(Min(RectA.Top, RectB.Top) - Max(RectA.Bottom, RectB.Bottom));

                        if (OverlapX > 0) and (OverlapY > 0) then
                            Separation := 0
                        else
                            Separation := Max(-OverlapX, -OverlapY);

                        // Only measure precisely when the prefilter says "close"
                        if (Separation < ClearanceMils + 25) then
                        begin
                            PairsChecked := PairsChecked + 1;
                            DistMils := Legacy_ComponentMinDistance(Board, Target, Other);
                            IsOverlap := (OverlapX > 0) and (OverlapY > 0);

                            if (IsOverlap) or (DistMils < ClearanceMils) then
                            begin
                                VProps := TStringList.Create;
                                try
                                    Legacy_AddJSONProperty(VProps, 'a', Target.Name.Text);
                                    Legacy_AddJSONProperty(VProps, 'b', Other.Name.Text);
                                    Legacy_AddJSONProperty(VProps, 'layer', Layer2String(Target.Layer));
                                    if IsOverlap then
                                        Legacy_AddJSONProperty(VProps, 'type', 'bounding_box_overlap')
                                    else
                                        Legacy_AddJSONProperty(VProps, 'type', 'clearance');
                                    Legacy_AddJSONNumber(VProps, 'distance_mils', Round(DistMils * 100) / 100);
                                    if IsOverlap then
                                    begin
                                        Legacy_AddJSONNumber(VProps, 'overlap_x_mils', Round(OverlapX * 100) / 100);
                                        Legacy_AddJSONNumber(VProps, 'overlap_y_mils', Round(OverlapY * 100) / 100);
                                    end;
                                    Legacy_AddJSONNumber(VProps, 'b_x', CoordToMils(Other.x - Board.XOrigin));
                                    Legacy_AddJSONNumber(VProps, 'b_y', CoordToMils(Other.y - Board.YOrigin));
                                    ViolationsArray.Add(Legacy_BuildJSONObject(VProps, 2));
                                finally
                                    VProps.Free;
                                end;
                            end;
                        end;
                    end;

                    Other := Iterator.NextPCBObject;
                end;
            finally
                Board.BoardIterator_Destroy(Iterator);
            end;
            Processed.Add(Target.Name.Text);
        end;

        Legacy_AddJSONInteger(ResultProps, 'checked_count', Targets.Count);
        Legacy_AddJSONNumber(ResultProps, 'clearance_mils', ClearanceMils);
        Legacy_AddJSONInteger(ResultProps, 'close_pairs_measured', PairsChecked);
        Legacy_AddJSONInteger(ResultProps, 'violation_count', ViolationsArray.Count);

        if (MissingArray.Count > 0) then
            ResultProps.Add(Legacy_BuildJSONArray(MissingArray, 'missing_designators'))
        else
            ResultProps.Add('"missing_designators": []');

        if (ViolationsArray.Count > 0) then
            ResultProps.Add(Legacy_BuildJSONArray(ViolationsArray, 'violations', 1))
        else
            ResultProps.Add('"violations": []');

        Result := Legacy_BuildJSONObject(ResultProps);
    finally
        Targets.Free;
        Processed.Free;
        MissingArray.Free;
        ViolationsArray.Free;
        ResultProps.Free;
    end;
end;

// Get all pads on every net touched by the given components (or the current
// selection when the list is empty). Returns a flat board-wide pad list -
// including pads of components outside the target set - so the caller can
// group by net and compute airline lengths. Coordinates in mils relative to
// the board origin.








function Legacy_ExecuteCheckPlacement(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    DesignatorsList: TStringList;
    ClearanceMils: Double;
begin
    DesignatorsList := TStringList.Create;
    ClearanceMils := 6;

    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for designators array (optional - empty means use selection)
            if (Pos('"designators"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')

                while (i < RequestData.Count) and (Copy(Trim(RequestData[i]), 1, 1) <> ']') do
                begin
                    ParamValue := RequestData[i];
                    ParamValue := Legacy_TrimJSON(ParamValue);

                    if (ParamValue <> '') and (ParamValue <> '[') then
                        DesignatorsList.Add(ParamValue);

                    i := i + 1;
                end;
            end
            // Look for clearance_mils
            else if (Pos('"clearance_mils"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := Legacy_TrimJSON(ParamValue);
                ClearanceMils := Legacy_SafeStrToFloat(ParamValue);
            end;
        end;

        Result := Legacy_CheckPlacement(DesignatorsList, ClearanceMils);
    finally
        DesignatorsList.Free;
    end;
end;

// Extract the place components logic








function Legacy_GetNetConnections(LegacyBusinessDirectory: String; DesignatorsList: TStringList): String;
var
    Board       : IPCB_Board;
    Component   : IPCB_Component;
    Iterator    : IPCB_BoardIterator;
    GrpIter     : IPCB_GroupIterator;
    Pad         : IPCB_Pad;
    NetNames    : TStringList;
    TargetNames : TStringList;
    PadsArray   : TStringList;
    PadProps    : TStringList;
    ResultProps : TStringList;
    NamesArray  : TStringList;
    OutputLines : TStringList;
    Designator  : String;
    xorigin, yorigin : Integer;
    i           : Integer;
begin
    Board := Legacy_GetBoardSafe(0);
    if (Board = nil) then
    begin
        Result := 'ERROR: No PCB document is currently active';
        Exit;
    end;

    xorigin := Board.XOrigin;
    yorigin := Board.YOrigin;

    NetNames := TStringList.Create;
    TargetNames := TStringList.Create;
    PadsArray := TStringList.Create;
    ResultProps := TStringList.Create;
    NamesArray := TStringList.Create;

    try
        // Build the target component list: explicit designators, or selection
        if (DesignatorsList.Count > 0) then
        begin
            for i := 0 to DesignatorsList.Count - 1 do
            begin
                Designator := Trim(DesignatorsList[i]);
                if (Board.GetPcbComponentByRefDes(Designator) <> nil) then
                    TargetNames.Add(Designator);
            end;
        end
        else
        begin
            for i := 0 to Board.SelectecObjectCount - 1 do
                if (Board.SelectecObject[i].ObjectId = eComponentObject) then
                    TargetNames.Add(Board.SelectecObject[i].Name.Text);
        end;

        if (TargetNames.Count = 0) then
        begin
            Result := 'ERROR: No components found (no designators given and no components selected)';
            Exit;
        end;

        // Collect the set of nets touched by the target components
        for i := 0 to TargetNames.Count - 1 do
        begin
            Component := Board.GetPcbComponentByRefDes(TargetNames[i]);
            GrpIter := Component.GroupIterator_Create;
            try
                GrpIter.SetState_FilterAll;
                GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));

                Pad := GrpIter.FirstPCBObject;
                while (Pad <> nil) do
                begin
                    if (Pad.Net <> nil) then
                        if (NetNames.IndexOf(Pad.Net.Name) < 0) then
                            NetNames.Add(Pad.Net.Name);
                    Pad := GrpIter.NextPCBObject;
                end;
            finally
                Component.GroupIterator_Destroy(GrpIter);
            end;
        end;

        // One board-wide pass: emit every pad on any of those nets
        Iterator := Board.BoardIterator_Create;
        try
            Iterator.AddFilter_ObjectSet(MkSet(ePadObject));
            Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
            Iterator.AddFilter_Method(eProcessAll);

            Pad := Iterator.FirstPCBObject;
            while (Pad <> nil) do
            begin
                if (Pad.Net <> nil) then
                begin
                    if (NetNames.IndexOf(Pad.Net.Name) >= 0) then
                    begin
                        PadProps := TStringList.Create;
                        try
                            Legacy_AddJSONProperty(PadProps, 'net', Pad.Net.Name);
                            if (Pad.Component <> nil) then
                                Legacy_AddJSONProperty(PadProps, 'designator', Pad.Component.Name.Text)
                            else
                                Legacy_AddJSONProperty(PadProps, 'designator', '');
                            Legacy_AddJSONProperty(PadProps, 'pin', Pad.Name);
                            Legacy_AddJSONNumber(PadProps, 'x', CoordToMils(Pad.x - xorigin));
                            Legacy_AddJSONNumber(PadProps, 'y', CoordToMils(Pad.y - yorigin));
                            PadsArray.Add(Legacy_BuildJSONObject(PadProps, 2));
                        finally
                            PadProps.Free;
                        end;
                    end;
                end;
                Pad := Iterator.NextPCBObject;
            end;
        finally
            Board.BoardIterator_Destroy(Iterator);
        end;

        // Build the result - include the resolved targets so the caller
        // knows which components were analyzed when using the selection
        for i := 0 to TargetNames.Count - 1 do
            NamesArray.Add('"' + EscapeJsonString(TargetNames[i]) + '"');
        ResultProps.Add(Legacy_BuildJSONArray(NamesArray, 'targets'));
        NamesArray.Text := '';

        for i := 0 to NetNames.Count - 1 do
            NamesArray.Add('"' + EscapeJsonString(NetNames[i]) + '"');

        if (NamesArray.Count > 0) then
            ResultProps.Add(Legacy_BuildJSONArray(NamesArray, 'net_names'))
        else
            ResultProps.Add('"net_names": []');

        if (PadsArray.Count > 0) then
            ResultProps.Add(Legacy_BuildJSONArray(PadsArray, 'pads', 1))
        else
            ResultProps.Add('"pads": []');

        // Potentially large (plane nets) - go through the temp-file path
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := Legacy_BuildJSONObject(ResultProps);
            Result := Legacy_WriteJSONToFile(OutputLines, LegacyBusinessDirectory+'\temp_net_connections.json');
        finally
            OutputLines.Free;
        end;
    finally
        NetNames.Free;
        TargetNames.Free;
        PadsArray.Free;
        ResultProps.Free;
        NamesArray.Free;
    end;
end;

// Place multiple components at absolute positions in a single transaction.
// Each entry in PlacementsList is 'Designator|X|Y|Rotation|Layer' where X/Y
// are mils relative to the board origin, Rotation is degrees CCW (-1 = keep
// current) and Layer is 'top', 'bottom' or '' (keep current side).








function Legacy_ExecuteGetNetConnections(RequestData: TStringList): String;
var
    ParamValue: String;
    i: Integer;
    DesignatorsList: TStringList;
begin
    DesignatorsList := TStringList.Create;

    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for designators array (optional - empty means use selection)
            if (Pos('"designators"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')

                while (i < RequestData.Count) and (Copy(Trim(RequestData[i]), 1, 1) <> ']') do
                begin
                    ParamValue := RequestData[i];
                    ParamValue := Legacy_TrimJSON(ParamValue);

                    if (ParamValue <> '') and (ParamValue <> '[') then
                        DesignatorsList.Add(ParamValue);

                    i := i + 1;
                end;
            end;
        end;

        Result := Legacy_GetNetConnections(WorkspaceDir, DesignatorsList);
    finally
        DesignatorsList.Free;
    end;
end;

// Extract the check placement logic








procedure Legacy_AddVerticesProperty(Props: TStringList; Poly: ISch_Polyline);
var
    VertsArray : TStringList;
    VProps     : TStringList;
    V          : Integer;
begin
    VertsArray := TStringList.Create;
    try
        for V := 1 to Poly.VerticesCount do
        begin
            VProps := TStringList.Create;
            try
                Legacy_AddJSONNumber(VProps, 'x', CoordToMils(Poly.Vertex[V].X));
                Legacy_AddJSONNumber(VProps, 'y', CoordToMils(Poly.Vertex[V].Y));
                VertsArray.Add(Legacy_BuildJSONObject(VProps, 3));
            finally
                VProps.Free;
            end;
        end;
        Props.Add(Legacy_BuildJSONArray(VertsArray, 'vertices', 2));
    finally
        VertsArray.Free;
    end;
end;

// Get the graphic primitives of symbols in a schematic library.
// SymbolName = '' -> inventory mode: every symbol with per-type primitive
// counts. SymbolName given -> full geometry dump of that symbol (mils).








function Legacy_GetSymbolPrimitives(LegacyBusinessDirectory: String; LibraryPath: String; SymbolName: String): String;
var
    CurrentLib   : ISch_Lib;
    LibIterator  : ISch_Iterator;
    LibComp      : ISch_Component;
    PrimIterator : ISch_Iterator;
    Prim         : ISch_GraphicalObject;
    ServerDoc    : IServerDocument;
    ResultProps  : TStringList;
    SymbolsArray : TStringList;
    SymProps     : TStringList;
    PrimsArray   : TStringList;
    PrimProps    : TStringList;
    OutputLines  : TStringList;
    Counts       : TStringList;
    TypeName     : String;
    i            : Integer;
    Found        : Boolean;
    PinObj       : ISch_Pin;
begin
    Result := '';

    // Open the requested library, or use the currently focused SchLib.
    // IMPORTANT: if the document is already open, only focus it -
    // re-opening an open document reloads it from disk and silently
    // discards any unsaved changes (e.g. symbols created this session).
    if (LibraryPath <> '') then
    begin
        if not FileExists(LibraryPath) then
        begin
            Result := 'ERROR: Library file not found: ' + LibraryPath;
            Exit;
        end;
        if Client.IsDocumentOpen(LibraryPath) then
            ServerDoc := Client.GetDocumentByPath(LibraryPath)
        else
            ServerDoc := Client.OpenDocument('SchLib', LibraryPath);
        if ServerDoc = Nil then
        begin
            Result := 'ERROR: Failed to open library: ' + LibraryPath;
            Exit;
        end;
        Client.ShowDocument(ServerDoc);
        Sleep(500);
    end;

    CurrentLib := SchServer.GetCurrentSchDocument;
    if (CurrentLib = Nil) or (CurrentLib.ObjectID <> eSchLib) then
    begin
        Result := 'ERROR: No schematic library is open (provide library_path or open a .SchLib)';
        Exit;
    end;

    ResultProps := TStringList.Create;
    SymbolsArray := TStringList.Create;
    Found := False;

    try
        Legacy_AddJSONProperty(ResultProps, 'library_name', ExtractFileName(CurrentLib.DocumentName));

        LibIterator := CurrentLib.SchLibIterator_Create;
        try
            LibIterator.AddFilter_ObjectSet(MkSet(eSchComponent));

            LibComp := LibIterator.FirstSchObject;
            while (LibComp <> Nil) do
            begin
                if (SymbolName = '') then
                begin
                    // Inventory mode: count primitives by type
                    Counts := TStringList.Create;
                    SymProps := TStringList.Create;
                    try
                        PrimIterator := LibComp.SchIterator_Create;
                        Prim := PrimIterator.FirstSchObject;
                        while (Prim <> Nil) do
                        begin
                            If (Prim.ObjectId = ePin) Then
                            Begin
TypeName := 'pins';
                            End
                            Else If (Prim.ObjectId = eRectangle) Then
                            Begin
TypeName := 'rectangles';
                            End
                            Else If (Prim.ObjectId = eLine) Then
                            Begin
TypeName := 'lines';
                            End
                            Else If (Prim.ObjectId = ePolyline) Then
                            Begin
TypeName := 'polylines';
                            End
                            Else If (Prim.ObjectId = ePolygon) Then
                            Begin
TypeName := 'polygons';
                            End
                            Else If (Prim.ObjectId = eArc) Then
                            Begin
TypeName := 'arcs';
                            End
                            Else If (Prim.ObjectId = eEllipticalArc) Then
                            Begin
TypeName := 'elliptical_arcs';
                            End
                            Else If (Prim.ObjectId = eEllipse) Then
                            Begin
TypeName := 'ellipses';
                            End
                            Else If (Prim.ObjectId = eBezier) Then
                            Begin
TypeName := 'beziers';
                            End
                            Else If (Prim.ObjectId = ePie) Then
                            Begin
TypeName := 'pies';
                            End
                            Else If (Prim.ObjectId = eRoundRectangle) Then
                            Begin
TypeName := 'round_rectangles';
                            End
                            Else If (Prim.ObjectId = eLabel) Then
                            Begin
TypeName := 'labels';
                            End
                            Else If (Prim.ObjectId = eParameter) Then
                            Begin
TypeName := '';
                            End
                            Else If (Prim.ObjectId = eDesignator) Then
                            Begin
TypeName := '';
                            End
                            Else
                            Begin
TypeName := 'other';
                            End;
                            if (TypeName <> '') then
                            begin
                                i := Counts.IndexOfName(TypeName);
                                if (i < 0) then
                                    Counts.Add(TypeName + '=1')
                                else
                                    Counts[i] := TypeName + '=' + IntToStr(StrToInt(Counts.ValueFromIndex[i]) + 1);
                            end;

                            Prim := PrimIterator.NextSchObject;
                        end;
                        LibComp.SchIterator_Destroy(PrimIterator);

                        Legacy_AddJSONProperty(SymProps, 'name', LibComp.LibReference);
                        Legacy_AddJSONProperty(SymProps, 'description', LibComp.ComponentDescription);
                        Legacy_AddJSONInteger(SymProps, 'part_count', LibComp.PartCount);
                        for i := 0 to Counts.Count - 1 do
                            Legacy_AddJSONInteger(SymProps, Counts.Names[i], StrToInt(Counts.ValueFromIndex[i]));

                        SymbolsArray.Add(Legacy_BuildJSONObject(SymProps, 1));
                    finally
                        Counts.Free;
                        SymProps.Free;
                    end;
                end
                else if (SymbolName = '*') or (UpperCase(LibComp.LibReference) = UpperCase(SymbolName)) then
                begin
                    // Dump mode: full geometry of every primitive.
                    // SymbolName '*' dumps every symbol into the symbols array.
                    Found := True;
                    SymProps := TStringList.Create;
                    PrimsArray := TStringList.Create;
                    try
                        Legacy_AddJSONProperty(SymProps, 'symbol_name', LibComp.LibReference);
                        Legacy_AddJSONProperty(SymProps, 'description', LibComp.ComponentDescription);
                        Legacy_AddJSONInteger(SymProps, 'part_count', LibComp.PartCount);

                        PrimIterator := LibComp.SchIterator_Create;
                        Prim := PrimIterator.FirstSchObject;
                        while (Prim <> Nil) do
                        begin
                            PrimProps := TStringList.Create;
                            try
                              // One unreadable primitive must not crash the dump:
                              // a property access that throws would otherwise leave
                              // the script paused in the debugger, wedging all
                              // later script runs
                              try
                                If (Prim.ObjectId = ePin) Then
                                Begin
PinObj := Prim;
                                        Legacy_AddJSONProperty(PrimProps, 'type', 'pin');
                                        Legacy_AddJSONProperty(PrimProps, 'pin_number', PinObj.Designator);
                                        Legacy_AddJSONProperty(PrimProps, 'pin_name', PinObj.Name);
                                        Legacy_AddJSONInteger(PrimProps, 'electrical', PinObj.Electrical);
                                        Legacy_AddJSONInteger(PrimProps, 'orientation', PinObj.Orientation);
                                        Legacy_AddJSONNumber(PrimProps, 'x', CoordToMils(PinObj.Location.X));
                                        Legacy_AddJSONNumber(PrimProps, 'y', CoordToMils(PinObj.Location.Y));
                                        Legacy_AddJSONNumber(PrimProps, 'length', CoordToMils(PinObj.PinLength));
                                        Legacy_AddJSONBoolean(PrimProps, 'show_name', PinObj.ShowName);
                                        Legacy_AddJSONBoolean(PrimProps, 'show_designator', PinObj.ShowDesignator);
                                End
                                Else If (Prim.ObjectId = eRectangle) Then
                                Begin
Legacy_AddJSONProperty(PrimProps, 'type', 'rectangle');
                                        Legacy_AddJSONNumber(PrimProps, 'x1', CoordToMils(Prim.Location.X));
                                        Legacy_AddJSONNumber(PrimProps, 'y1', CoordToMils(Prim.Location.Y));
                                        Legacy_AddJSONNumber(PrimProps, 'x2', CoordToMils(Prim.Corner.X));
                                        Legacy_AddJSONNumber(PrimProps, 'y2', CoordToMils(Prim.Corner.Y));
                                        Legacy_AddJSONInteger(PrimProps, 'line_width', Prim.LineWidth);
                                        Legacy_AddJSONBoolean(PrimProps, 'is_solid', Prim.IsSolid);
                                End
                                Else If (Prim.ObjectId = eLine) Then
                                Begin
Legacy_AddJSONProperty(PrimProps, 'type', 'line');
                                        Legacy_AddJSONNumber(PrimProps, 'x1', CoordToMils(Prim.Location.X));
                                        Legacy_AddJSONNumber(PrimProps, 'y1', CoordToMils(Prim.Location.Y));
                                        Legacy_AddJSONNumber(PrimProps, 'x2', CoordToMils(Prim.Corner.X));
                                        Legacy_AddJSONNumber(PrimProps, 'y2', CoordToMils(Prim.Corner.Y));
                                        Legacy_AddJSONInteger(PrimProps, 'line_width', Prim.LineWidth);
                                End
                                Else If (Prim.ObjectId = ePolyline) Then
                                Begin
Legacy_AddJSONProperty(PrimProps, 'type', 'polyline');
                                        Legacy_AddJSONInteger(PrimProps, 'line_width', Prim.LineWidth);
                                        Legacy_AddVerticesProperty(PrimProps, Prim);
                                End
                                Else If (Prim.ObjectId = ePolygon) Then
                                Begin
Legacy_AddJSONProperty(PrimProps, 'type', 'polygon');
                                        Legacy_AddJSONInteger(PrimProps, 'line_width', Prim.LineWidth);
                                        Legacy_AddJSONBoolean(PrimProps, 'is_solid', Prim.IsSolid);
                                        Legacy_AddVerticesProperty(PrimProps, Prim);
                                End
                                Else If (Prim.ObjectId = eArc) Then
                                Begin
Legacy_AddJSONProperty(PrimProps, 'type', 'arc');
                                        Legacy_AddJSONNumber(PrimProps, 'cx', CoordToMils(Prim.Location.X));
                                        Legacy_AddJSONNumber(PrimProps, 'cy', CoordToMils(Prim.Location.Y));
                                        Legacy_AddJSONNumber(PrimProps, 'radius', CoordToMils(Prim.Radius));
                                        Legacy_AddJSONNumber(PrimProps, 'start_angle', Prim.StartAngle);
                                        Legacy_AddJSONNumber(PrimProps, 'end_angle', Prim.EndAngle);
                                        Legacy_AddJSONInteger(PrimProps, 'line_width', Prim.LineWidth);
                                End
                                Else If (Prim.ObjectId = eEllipticalArc) Then
                                Begin
Legacy_AddJSONProperty(PrimProps, 'type', 'elliptical_arc');
                                        Legacy_AddJSONNumber(PrimProps, 'cx', CoordToMils(Prim.Location.X));
                                        Legacy_AddJSONNumber(PrimProps, 'cy', CoordToMils(Prim.Location.Y));
                                        Legacy_AddJSONNumber(PrimProps, 'radius', CoordToMils(Prim.Radius));
                                        Legacy_AddJSONNumber(PrimProps, 'secondary_radius', CoordToMils(Prim.SecondaryRadius));
                                        Legacy_AddJSONNumber(PrimProps, 'start_angle', Prim.StartAngle);
                                        Legacy_AddJSONNumber(PrimProps, 'end_angle', Prim.EndAngle);
                                        Legacy_AddJSONInteger(PrimProps, 'line_width', Prim.LineWidth);
                                End
                                Else If (Prim.ObjectId = eEllipse) Then
                                Begin
Legacy_AddJSONProperty(PrimProps, 'type', 'ellipse');
                                        Legacy_AddJSONNumber(PrimProps, 'cx', CoordToMils(Prim.Location.X));
                                        Legacy_AddJSONNumber(PrimProps, 'cy', CoordToMils(Prim.Location.Y));
                                        Legacy_AddJSONNumber(PrimProps, 'radius', CoordToMils(Prim.Radius));
                                        Legacy_AddJSONNumber(PrimProps, 'secondary_radius', CoordToMils(Prim.SecondaryRadius));
                                        Legacy_AddJSONBoolean(PrimProps, 'is_solid', Prim.IsSolid);
                                        Legacy_AddJSONInteger(PrimProps, 'line_width', Prim.LineWidth);
                                End
                                Else If (Prim.ObjectId = eBezier) Then
                                Begin
Legacy_AddJSONProperty(PrimProps, 'type', 'bezier');
                                        Legacy_AddJSONInteger(PrimProps, 'line_width', Prim.LineWidth);
                                        Legacy_AddVerticesProperty(PrimProps, Prim);
                                End
                                Else If (Prim.ObjectId = ePie) Then
                                Begin
Legacy_AddJSONProperty(PrimProps, 'type', 'pie');
                                        Legacy_AddJSONNumber(PrimProps, 'cx', CoordToMils(Prim.Location.X));
                                        Legacy_AddJSONNumber(PrimProps, 'cy', CoordToMils(Prim.Location.Y));
                                        Legacy_AddJSONNumber(PrimProps, 'radius', CoordToMils(Prim.Radius));
                                        Legacy_AddJSONNumber(PrimProps, 'start_angle', Prim.StartAngle);
                                        Legacy_AddJSONNumber(PrimProps, 'end_angle', Prim.EndAngle);
                                        Legacy_AddJSONBoolean(PrimProps, 'is_solid', Prim.IsSolid);
                                        Legacy_AddJSONInteger(PrimProps, 'line_width', Prim.LineWidth);
                                End
                                Else If (Prim.ObjectId = eRoundRectangle) Then
                                Begin
Legacy_AddJSONProperty(PrimProps, 'type', 'round_rectangle');
                                        Legacy_AddJSONNumber(PrimProps, 'x1', CoordToMils(Prim.Location.X));
                                        Legacy_AddJSONNumber(PrimProps, 'y1', CoordToMils(Prim.Location.Y));
                                        Legacy_AddJSONNumber(PrimProps, 'x2', CoordToMils(Prim.Corner.X));
                                        Legacy_AddJSONNumber(PrimProps, 'y2', CoordToMils(Prim.Corner.Y));
                                        Legacy_AddJSONNumber(PrimProps, 'corner_x_radius', CoordToMils(Prim.CornerXRadius));
                                        Legacy_AddJSONNumber(PrimProps, 'corner_y_radius', CoordToMils(Prim.CornerYRadius));
                                        Legacy_AddJSONInteger(PrimProps, 'line_width', Prim.LineWidth);
                                        Legacy_AddJSONBoolean(PrimProps, 'is_solid', Prim.IsSolid);
                                End
                                Else If (Prim.ObjectId = eLabel) Then
                                Begin
Legacy_AddJSONProperty(PrimProps, 'type', 'label');
                                        Legacy_AddJSONProperty(PrimProps, 'text', Prim.Text);
                                        Legacy_AddJSONNumber(PrimProps, 'x', CoordToMils(Prim.Location.X));
                                        Legacy_AddJSONNumber(PrimProps, 'y', CoordToMils(Prim.Location.Y));
                                End
                                Else If (Prim.ObjectId = eParameter) Then
                                Begin
Legacy_AddJSONProperty(PrimProps, 'type', '');
                                End
                                Else If (Prim.ObjectId = eDesignator) Then
                                Begin
Legacy_AddJSONProperty(PrimProps, 'type', '');
                                // Footprint/model links are metadata, not drawn
                                // graphics - excluded like parameters;
                                End
                                Else If (Prim.ObjectId = eImplementation) Then
                                Begin
Legacy_AddJSONProperty(PrimProps, 'type', '');
                                End
                                Else If (Prim.ObjectId = eImplementationMap) Then
                                Begin
Legacy_AddJSONProperty(PrimProps, 'type', '');
                                End
                                Else
                                Begin
// Surface unknown graphic types instead of
                                    // hiding them - the object_id identifies them
                                    Legacy_AddJSONProperty(PrimProps, 'type', 'unknown');
                                    Legacy_AddJSONInteger(PrimProps, 'object_id', Prim.ObjectId);
                                End;

                                if (PrimProps.Count > 0) then
                                begin
                                    // Skip parameters/designator
                                    if (Pos('"type": ""', PrimProps[0]) = 0) then
                                    begin
                                        // Unknown object kinds may not expose
                                        // OwnerPartId (they are not standard
                                        // graphical objects) - do not touch it
                                        if (Pos('"type": "unknown"', PrimProps[0]) = 0) then
                                            Legacy_AddJSONInteger(PrimProps, 'owner_part_id', Prim.OwnerPartId);
                                        PrimsArray.Add(Legacy_BuildJSONObject(PrimProps, 1));
                                    end;
                                end;
                              except
                                PrimProps.Text := '';
                                Legacy_AddJSONProperty(PrimProps, 'type', 'unreadable');
                                PrimsArray.Add(Legacy_BuildJSONObject(PrimProps, 1));
                              end;
                            finally
                                PrimProps.Free;
                            end;

                            Prim := PrimIterator.NextSchObject;
                        end;
                        LibComp.SchIterator_Destroy(PrimIterator);

                        SymProps.Add(Legacy_BuildJSONArray(PrimsArray, 'primitives', 1));

                        if (SymbolName = '*') then
                            SymbolsArray.Add(Legacy_BuildJSONObject(SymProps, 1))
                        else
                            for i := 0 to SymProps.Count - 1 do
                                ResultProps.Add(SymProps[i]);
                    finally
                        SymProps.Free;
                        PrimsArray.Free;
                    end;
                end;

                LibComp := LibIterator.NextSchObject;
            end;
        finally
            CurrentLib.SchIterator_Destroy(LibIterator);
        end;

        if (SymbolName = '') or (SymbolName = '*') then
        begin
            Legacy_AddJSONInteger(ResultProps, 'symbol_count', SymbolsArray.Count);
            ResultProps.Add(Legacy_BuildJSONArray(SymbolsArray, 'symbols', 1));
        end
        else if not Found then
        begin
            Result := 'ERROR: Symbol not found in library: ' + SymbolName;
            Exit;
        end;

        OutputLines := TStringList.Create;
        try
            OutputLines.Text := Legacy_BuildJSONObject(ResultProps);
            Result := Legacy_WriteJSONToFile(OutputLines, LegacyBusinessDirectory + '\temp_symbol_primitives.json');
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
        SymbolsArray.Free;
    end;
end;

// Function to get all schematic component data








function Legacy_ExecuteGetSymbolPrimitives(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    LibraryPath: String;
    SymbolName: String;
begin
    LibraryPath := '';
    SymbolName := '';

    // Parse parameters from the request
    for i := 0 to RequestData.Count - 1 do
    begin
        // Look for library_path
        if (Pos('"library_path"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            ParamValue := Legacy_TrimJSON(ParamValue);
            LibraryPath := ParamValue;
        end
        // Look for symbol_name
        else if (Pos('"symbol_name"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            ParamValue := Legacy_TrimJSON(ParamValue);
            SymbolName := ParamValue;
        end;
    end;

    Result := Legacy_GetSymbolPrimitives(WorkspaceDir, LibraryPath, SymbolName);
end;

// Extract the get net connections logic








function Legacy_BatchCreateOne(SymName, SymDesc: String; PartCount: Integer; PinsList, GraphicsList: TStringList): Boolean;
var
    R: String;
begin
    Result := False;
    if (SymName = '') then Exit;
    if (SymDesc <> '') then
        PinsList.Add('Description=' + SymDesc);
    R := Legacy_CreateSchematicSymbol(SymName, PinsList, GraphicsList, PartCount);
    Result := (Pos('"success": true', R) > 0);
end;

// Create many symbols in a single script Run from a spec file. The file is
// plain text, one record per line, pipe-delimited (no JSON, so field text
// is preserved exactly):
//   LIBRARY|<path to .SchLib>            (optional first line - focus/open)
//   SYMBOL|<name>|<description>|<part_count>
//   PIN|<same fields as create_schematic_symbol pins>
//   GRAPHIC|<same entry format as create_schematic_symbol graphics>
// Each SYMBOL line flushes the previous symbol. Far fewer Altium script
// launches than one create call per symbol - use for bulk imports.








function Legacy_CreateSymbolsBatch(SpecFilePath: String): String;
var
    Lines        : TStringList;
    PinsList     : TStringList;
    GraphicsList : TStringList;
    FailedArray  : TStringList;
    ResultProps  : TStringList;
    ServerDoc    : IServerDocument;
    Line, Kind   : String;
    LibPath      : String;
    CurrentName  : String;
    CurrentDesc  : String;
    FieldValue   : String;
    PartCount    : Integer;
    CreatedCount : Integer;
    i            : Integer;
begin
    if not FileExists(SpecFilePath) then
    begin
        Result := 'ERROR: Spec file not found: ' + SpecFilePath;
        Exit;
    end;

    Lines := TStringList.Create;
    PinsList := TStringList.Create;
    GraphicsList := TStringList.Create;
    FailedArray := TStringList.Create;
    ResultProps := TStringList.Create;
    CurrentName := '';
    CurrentDesc := '';
    PartCount := 1;
    CreatedCount := 0;

    try
        Lines.LoadFromFile(SpecFilePath);

        for i := 0 to Lines.Count - 1 do
        begin
            Line := Lines[i];
            Kind := UpperCase(Trim(Legacy_GetFieldFromPipeString(Line, 0)));

            if (Kind = 'LIBRARY') then
            begin
                LibPath := Trim(Legacy_GetFieldFromPipeString(Line, 1));
                if (LibPath <> '') and FileExists(LibPath) then
                begin
                    // Focus if already open; never re-open (reload discards
                    // unsaved symbols)
                    if Client.IsDocumentOpen(LibPath) then
                        ServerDoc := Client.GetDocumentByPath(LibPath)
                    else
                        ServerDoc := Client.OpenDocument('SchLib', LibPath);
                    if (ServerDoc <> Nil) then
                    begin
                        Client.ShowDocument(ServerDoc);
                        Sleep(500);
                    end;
                end;
            end
            else if (Kind = 'SYMBOL') then
            begin
                // Flush the previous symbol
                if (CurrentName <> '') then
                begin
                    if Legacy_BatchCreateOne(CurrentName, CurrentDesc, PartCount, PinsList, GraphicsList) then
                        CreatedCount := CreatedCount + 1
                    else
                        FailedArray.Add('"' + EscapeJsonString(CurrentName) + '"');
                end;
                PinsList.Text := '';
                GraphicsList.Text := '';
                CurrentName := Trim(Legacy_GetFieldFromPipeString(Line, 1));
                CurrentDesc := Legacy_GetFieldFromPipeString(Line, 2);
                FieldValue := Trim(Legacy_GetFieldFromPipeString(Line, 3));
                if (FieldValue <> '') then
                    PartCount := StrToInt(FieldValue)
                else
                    PartCount := 1;
            end
            else if (Kind = 'PIN') then
            begin
                PinsList.Add(Copy(Line, 5, Length(Line)));
            end
            else if (Kind = 'GRAPHIC') then
            begin
                GraphicsList.Add(Copy(Line, 9, Length(Line)));
            end;
        end;

        // Flush the last symbol
        if (CurrentName <> '') then
        begin
            if Legacy_BatchCreateOne(CurrentName, CurrentDesc, PartCount, PinsList, GraphicsList) then
                CreatedCount := CreatedCount + 1
            else
                FailedArray.Add('"' + EscapeJsonString(CurrentName) + '"');
        end;

        Legacy_AddJSONInteger(ResultProps, 'created', CreatedCount);
        if (FailedArray.Count > 0) then
            ResultProps.Add(Legacy_BuildJSONArray(FailedArray, 'failed'))
        else
            ResultProps.Add('"failed": []');

        Result := Legacy_BuildJSONObject(ResultProps);
    finally
        Lines.Free;
        PinsList.Free;
        GraphicsList.Free;
        FailedArray.Free;
        ResultProps.Free;
    end;
end;

// Dump the vertices of a polyline-like object as a JSON array property








function Legacy_ExecuteCreateSymbolsBatch(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    SpecFile: String;
begin
    SpecFile := '';

    // Parse parameters from the request
    for i := 0 to RequestData.Count - 1 do
    begin
        if (Pos('"spec_file"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            ParamValue := Legacy_TrimJSON(ParamValue);
            SpecFile := ParamValue;
        end;
    end;

    if (SpecFile <> '') then
        Result := Legacy_CreateSymbolsBatch(SpecFile)
    else
        Result := 'ERROR: No spec_file provided for create_symbols_batch';
end;

// Extract the get symbol primitives logic








procedure Legacy_GetComponentBody(Comp: ISch_Component; var BodyL: Integer; var BodyR: Integer; var BodyT: Integer);
var
    Iter : ISch_Iterator;
    Prim : ISch_GraphicalObject;
    Rect : IDispatch;
begin
    BodyL := 999999;
    BodyR := -999999;
    BodyT := -999999;
    Iter := Comp.SchIterator_Create;
    try
        Iter.AddFilter_ObjectSet(MkSet(ePin, eLine, eRectangle, eArc, ePolyline, eEllipse));
        Prim := Iter.FirstSchObject;
        while (Prim <> nil) do
        begin
            Rect := Prim.BoundingRectangle;
            if (CoordToMils(Rect.Left)  < BodyL) then BodyL := CoordToMils(Rect.Left);
            if (CoordToMils(Rect.Right) > BodyR) then BodyR := CoordToMils(Rect.Right);
            if (CoordToMils(Rect.Top)   > BodyT) then BodyT := CoordToMils(Rect.Top);
            Prim := Iter.NextSchObject;
        end;
    finally
        Comp.SchIterator_Destroy(Iter);
    end;
end;

// Apply harvested parameter placement to one component. Harvested offsets are
// relative SPACING from a reference sheet; the block is translated to clear the
// body and sit below the topmost pin, so it stays together on one side of any
// rail instead of straddling it. Justification is what aligns the column.





procedure Legacy_StyleComponentText(Comp: ISch_Component; Placement: TStringList);
var
    LibRef, Rec, PName : String;
    i, MaxDy, AnchorX, BlockTop, Just, TextY : Integer;
    BodyL, BodyR, BodyT : Integer;
    Found, ExactPose : Boolean;
    Iter : ISch_Iterator;
    Param : ISch_Parameter;
begin
    LibRef := Comp.LibReference;

    // Prefer a record harvested from a part in the SAME pose. Its offsets are
    // then literal - the house style puts a horizontal resistor's four fields
    // at the four corners, which is not a stack and must not be re-flowed.
    ExactPose := False;
    Found := False;
    MaxDy := -999999;
    for i := 0 to Placement.Count - 1 do
        if (Legacy_GetFieldFromPipeString(Placement[i], 1) = LibRef) then
        begin
            Found := True;
            if (StrToInt(Legacy_GetFieldFromPipeString(Placement[i], 2)) = Comp.Orientation) then
                ExactPose := True;
            if (StrToInt(Legacy_GetFieldFromPipeString(Placement[i], 5)) > MaxDy) then
                MaxDy := StrToInt(Legacy_GetFieldFromPipeString(Placement[i], 5));
        end;
    if not Found then Exit;

    Legacy_GetComponentBody(Comp, BodyL, BodyR, BodyT);
    BlockTop := BodyT - 100;

    Iter := Comp.SchIterator_Create;
    try
        Iter.AddFilter_ObjectSet(MkSet(eParameter));
        Param := Iter.FirstSchObject;
        while (Param <> nil) do
        begin
            Param.IsHidden := True;
            Param := Iter.NextSchObject;
        end;
    finally
        Comp.SchIterator_Destroy(Iter);
    end;

    for i := 0 to Placement.Count - 1 do
    begin
        Rec := Placement[i];
        if (Legacy_GetFieldFromPipeString(Rec, 1) = LibRef) and
           ((not ExactPose) or (StrToInt(Legacy_GetFieldFromPipeString(Rec, 2)) = Comp.Orientation)) then
        begin
            PName := Legacy_GetFieldFromPipeString(Rec, 3);
            Just  := StrToInt(Legacy_GetFieldFromPipeString(Rec, 6));
            // Placement is harvested per LibReference, but the reference part
            // may have been ROTATED while this one is not. Beside-the-body only
            // works for a vertical part; on a horizontal one it lands exactly
            // where the wire leaves the end pin. So stack the block ABOVE a
            // horizontal part and beside a vertical one.
            if ExactPose then
            begin
                // Same pose as the reference: offsets are literal.
                AnchorX := CoordToMils(Comp.Location.X) + StrToInt(Legacy_GetFieldFromPipeString(Rec, 4));
                TextY   := CoordToMils(Comp.Location.Y) + StrToInt(Legacy_GetFieldFromPipeString(Rec, 5));

                // ...unless the anchor lands ON the body. RES-DISCRETE offsets
                // (-100 / +600) sit clear of its body and give the four-corner
                // house layout; CAP-NP uses dx=0, which for a rotated cap is
                // the middle of the plates. Push those clear, keeping dy.
                if (AnchorX >= BodyL) and (AnchorX <= BodyR) then
                    if (Just = 2) or (Just = 5) or (Just = 8) then
                        AnchorX := BodyL - 50
                    else
                        AnchorX := BodyR + 50;
            end
            else
            begin
                // No reference in this pose - fall back to a column beside the
                // body, keeping the harvested spacing and justification.
                if (Just = 2) or (Just = 5) or (Just = 8) then
                    AnchorX := BodyL - 50
                else
                    AnchorX := BodyR + 50;
                TextY := BlockTop - (MaxDy - StrToInt(Legacy_GetFieldFromPipeString(Rec, 5)));
            end;

            if (PName = 'DESIGNATOR') then
            begin
                Comp.Designator.Autoposition := False;
                Comp.Designator.Orientation := StrToInt(Legacy_GetFieldFromPipeString(Rec, 7));
                Comp.Designator.Justification := Just;
                Comp.Designator.MoveToXY(MilsToCoord(AnchorX), MilsToCoord(TextY));
            end
            else
            begin
                Iter := Comp.SchIterator_Create;
                try
                    Iter.AddFilter_ObjectSet(MkSet(eParameter));
                    Param := Iter.FirstSchObject;
                    while (Param <> nil) do
                    begin
                        if (UpperCase(Param.Name) = UpperCase(PName)) then
                        begin
                            Param.IsHidden := False;
                            Param.Autoposition := False;
                            Param.Orientation := StrToInt(Legacy_GetFieldFromPipeString(Rec, 7));
                            Param.Justification := Just;
                            Param.MoveToXY(MilsToCoord(AnchorX), MilsToCoord(TextY));
                        end;
                        Param := Iter.NextSchObject;
                    end;
                finally
                    Comp.SchIterator_Destroy(Iter);
                end;
            end;
        end;
    end;
end;






procedure Legacy_GetPinHotEnd(Pin: ISch_Pin; var HotX: Integer; var HotY: Integer);
var
    PinLen : Integer;
begin
    HotX := CoordToMils(Pin.Location.X);
    HotY := CoordToMils(Pin.Location.Y);
    PinLen := CoordToMils(Pin.PinLength);
    if      (Pin.Orientation = 0) then HotX := HotX + PinLen
    else if (Pin.Orientation = 1) then HotY := HotY + PinLen
    else if (Pin.Orientation = 2) then HotX := HotX - PinLen
    else if (Pin.Orientation = 3) then HotY := HotY - PinLen;
end;

// Body extent EXCLUDING parameter text. Component.BoundingRectangle includes
// the text, so anchoring text to it feeds back on itself and walks the block
// off the sheet.





function Legacy_BuildCircuitFromSpec(SpecPath: String; PlacementPath: String; PinMapPath: String): String;
var
    Spec, Placement, PinMap : TStringList;
    TargetDoc, LibDoc : ISch_Document;
    Comp, Found, Replica : ISch_Component;
    Iter, ChildIter : ISch_Iterator;
    Prim, Param, Obj : ISch_GraphicalObject;
    Impl : ISch_Implementation;
    Rec, Kind, CurLib, PName, PVal, Adopted : String;
    Proj : IProject;
    i, fld, vtx, PartCount, GfxCount, PinCount : Integer;
    HotX, HotY : Integer;
    Exists : Boolean;
begin
    Result := '{"success": false, "error": "build did not Run"}';

    if not FileExists(SpecPath) then
    begin
        Result := '{"success": false, "error": "spec file not found"}';
        Exit;
    end;

    Spec := TStringList.Create;
    Spec.LoadFromFile(SpecPath);
    Placement := TStringList.Create;
    if FileExists(PlacementPath) then Placement.LoadFromFile(PlacementPath);

    // SAFETY: opening a symbol library makes THAT document current. Stray
    // components were once registered into shared libraries on a read-only
    // share exactly that way. Create the target up front, hold it by
    // reference, and verify it is a schematic (32) not a library (33) once.
    GetWorkSpace.DM_CreateNewDocument('SCH');
    TargetDoc := SchServer.GetCurrentSchDocument;
    if (TargetDoc = nil) then
    begin
        Spec.Free;
        Placement.Free;
        Result := '{"success": false, "error": "could not create a target schematic"}';
        Exit;
    end;
    if (TargetDoc.ObjectID <> 32) then
    begin
        Spec.Free;
        Placement.Free;
        Result := '{"success": false, "error": "target is not a schematic - refusing to build"}';
        Exit;
    end;

    // DM_CreateNewDocument attaches the new sheet to the FOCUSED PROJECT, so
    // generated sheets were being silently adopted into the user's design -
    // they showed up as [13]/[14] in Base.PrjPcb instead of under Free
    // Documents. A generated sheet must not modify an existing project
    // unless the caller asks for that, so detach it again.
    Adopted := '';
    Proj := GetWorkspace.DM_FocusedProject;
    if (Proj <> nil) then
        if (Pos('Free Documents', Proj.DM_ProjectFileName) = 0) then
        begin
            Adopted := Proj.DM_ProjectFileName;
            Proj.DM_RemoveSourceDocument(TargetDoc.DocumentName);
        end;

    SchServer.ProcessControl.PreProcess(TargetDoc, '');
    try
        Comp := nil;
        CurLib := '';
        PartCount := 0;
        GfxCount := 0;

        for i := 0 to Spec.Count - 1 do
        begin
            Rec := Spec[i];
            Kind := Legacy_GetFieldFromPipeString(Rec, 0);

            if (Kind = 'PART') then
            begin
                if (Legacy_GetFieldFromPipeString(Rec, 2) <> CurLib) then
                begin
                    CurLib := Legacy_GetFieldFromPipeString(Rec, 2);
                    Client.ShowDocument(Client.OpenDocument('SchLib', CurLib));
                    Sleep(1200);
                end;
                LibDoc := SchServer.GetCurrentSchDocument;

                Found := nil;
                Iter := LibDoc.SchLibIterator_Create;
                try
                    Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
                    Prim := Iter.FirstSchObject;
                    while (Prim <> nil) do
                    begin
                        if (Prim.LibReference = Legacy_GetFieldFromPipeString(Rec, 3)) then Found := Prim;
                        Prim := Iter.NextSchObject;
                    end;
                finally
                    LibDoc.SchIterator_Destroy(Iter);
                end;

                if (Found = nil) then
                    Comp := nil
                else
                begin
                    Replica := Found.Replicate;
                    Replica.Designator.Text := Legacy_GetFieldFromPipeString(Rec, 1);
                    Replica.DesignItemID    := Legacy_GetFieldFromPipeString(Rec, 4);
                    Replica.Orientation     := StrToInt(Legacy_GetFieldFromPipeString(Rec, 7));

                    TargetDoc.RegisterSchObjectInContainer(Replica);
                    SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
                        SCHM_PrimitiveRegistration, Replica.I_ObjectAddress);

                    // Mirror is the editor's X key. IsMirrored is a display flag
                    // only - it sets True and leaves the pin coordinates alone.
                    if (Legacy_GetFieldFromPipeString(Rec, 8) = '1') then
                        Replica.Mirror(Replica.Location);

                    // MoveByXY, not Location, so child text travels with the part
                    Replica.MoveByXY(
                        MilsToCoord(StrToInt(Legacy_GetFieldFromPipeString(Rec, 5)) - CoordToMils(Replica.Location.X)),
                        MilsToCoord(StrToInt(Legacy_GetFieldFromPipeString(Rec, 6)) - CoordToMils(Replica.Location.Y)));

                    Comp := Replica;
                    PartCount := PartCount + 1;
                end;
            end

            else if (Kind = 'COMMENT') then
            begin
                if (Comp <> nil) then Comp.Comment.Text := Legacy_GetFieldFromPipeString(Rec, 1);
            end

            else if (Kind = 'DESCRIPTION') then
            begin
                if (Comp <> nil) then Comp.ComponentDescription := Legacy_GetFieldFromPipeString(Rec, 1);
            end

            else if (Kind = 'FOOTPRINT') then
            begin
                if (Comp <> nil) then
                begin
                    Impl := Comp.AddSchImplementation;
                    Impl.ModelName := Legacy_GetFieldFromPipeString(Rec, 1);
                    Impl.ModelType := 'PCBLIB';
                    Impl.IsCurrent := True;
                    Impl.UseComponentLibrary := True;
                end;
            end

            else if (Kind = 'PARAM') then
            begin
                if (Comp <> nil) then
                begin
                    PName := Legacy_GetFieldFromPipeString(Rec, 1);
                    PVal  := Legacy_GetFieldFromPipeString(Rec, 2);
                    Exists := False;
                    ChildIter := Comp.SchIterator_Create;
                    try
                        ChildIter.AddFilter_ObjectSet(MkSet(eParameter));
                        Param := ChildIter.FirstSchObject;
                        while (Param <> nil) do
                        begin
                            if (UpperCase(Param.Name) = UpperCase(PName)) then
                            begin
                                Param.Text := PVal;
                                Exists := True;
                            end;
                            Param := ChildIter.NextSchObject;
                        end;
                    finally
                        Comp.SchIterator_Destroy(ChildIter);
                    end;

                    if not Exists then
                    begin
                        Param := SchServer.SchObjectFactory(eParameter, eCreate_Default);
                        Param.Name := PName;
                        Param.Text := PVal;
                        Param.ParamType := eParameterType_String;
                        Param.ReadOnlyState := eReadOnly_None;
                        Param.IsHidden := True;
                        Comp.AddSchObject(Param);
                        SchServer.RobotManager.SendMessage(Comp.I_ObjectAddress, c_BroadCast,
                            SCHM_PrimitiveRegistration, Param.I_ObjectAddress);
                    end;
                end;
            end

            else if (Kind = 'WIRE') then
            begin
                Obj := SchServer.SchObjectFactory(eWire, eCreate_GlobalCopy);
                Obj.Location := Point(MilsToCoord(StrToInt(Legacy_GetFieldFromPipeString(Rec, 1))),
                                      MilsToCoord(StrToInt(Legacy_GetFieldFromPipeString(Rec, 2))));
                fld := 1;
                vtx := 0;
                while (Legacy_GetFieldFromPipeString(Rec, fld) <> '') do
                begin
                    vtx := vtx + 1;
                    Obj.InsertVertex := vtx;
                    Obj.SetState_Vertex(vtx,
                        Point(MilsToCoord(StrToInt(Legacy_GetFieldFromPipeString(Rec, fld))),
                              MilsToCoord(StrToInt(Legacy_GetFieldFromPipeString(Rec, fld + 1)))));
                    fld := fld + 2;
                end;
                TargetDoc.RegisterSchObjectInContainer(Obj);
                SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
                    SCHM_PrimitiveRegistration, Obj.I_ObjectAddress);
                GfxCount := GfxCount + 1;
            end

            else if (Kind = 'JUNCTION') then
            begin
                Obj := SchServer.SchObjectFactory(eJunction, eCreate_GlobalCopy);
                Obj.Location := Point(MilsToCoord(StrToInt(Legacy_GetFieldFromPipeString(Rec, 1))),
                                      MilsToCoord(StrToInt(Legacy_GetFieldFromPipeString(Rec, 2))));
                TargetDoc.RegisterSchObjectInContainer(Obj);
                SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
                    SCHM_PrimitiveRegistration, Obj.I_ObjectAddress);
                GfxCount := GfxCount + 1;
            end

            else if (Kind = 'NETLABEL') then
            begin
                Obj := SchServer.SchObjectFactory(eNetLabel, eCreate_GlobalCopy);
                Obj.Location := Point(MilsToCoord(StrToInt(Legacy_GetFieldFromPipeString(Rec, 1))),
                                      MilsToCoord(StrToInt(Legacy_GetFieldFromPipeString(Rec, 2))));
                Obj.Orientation := StrToInt(Legacy_GetFieldFromPipeString(Rec, 3));
                Obj.Text := Legacy_GetFieldFromPipeString(Rec, 4);
                TargetDoc.RegisterSchObjectInContainer(Obj);
                SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
                    SCHM_PrimitiveRegistration, Obj.I_ObjectAddress);
                GfxCount := GfxCount + 1;
            end

            else if (Kind = 'POWER') then
            begin
                Obj := SchServer.SchObjectFactory(ePowerObject, eCreate_GlobalCopy);
                Obj.Location := Point(MilsToCoord(StrToInt(Legacy_GetFieldFromPipeString(Rec, 1))),
                                      MilsToCoord(StrToInt(Legacy_GetFieldFromPipeString(Rec, 2))));
                Obj.Orientation := StrToInt(Legacy_GetFieldFromPipeString(Rec, 3));
                Obj.Style := StrToInt(Legacy_GetFieldFromPipeString(Rec, 4));
                // NOTE: ShowNetName := False does NOT hide the label - Altium draws
                // it regardless. Keep wires out of the ~300 mil band below a port.
                Obj.ShowNetName := (Legacy_GetFieldFromPipeString(Rec, 6) = '1');
                Obj.Text := Legacy_GetFieldFromPipeString(Rec, 5);
                TargetDoc.RegisterSchObjectInContainer(Obj);
                SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
                    SCHM_PrimitiveRegistration, Obj.I_ObjectAddress);
                GfxCount := GfxCount + 1;
            end

            else if (Kind = 'SPORT') then
            begin
                // Sheet port: SPORT|x|y|name|iotype|style|width
                // IOType: 0=unspecified 1=output 2=input 3=bidirectional.
                // Location is the port's connection-side end; wire to it.
                Obj := SchServer.SchObjectFactory(ePort, eCreate_GlobalCopy);
                Obj.Location := Point(MilsToCoord(StrToInt(Legacy_GetFieldFromPipeString(Rec, 1))),
                                      MilsToCoord(StrToInt(Legacy_GetFieldFromPipeString(Rec, 2))));
                Obj.Name := Legacy_GetFieldFromPipeString(Rec, 3);
                Obj.IOType := StrToInt(Legacy_GetFieldFromPipeString(Rec, 4));
                Obj.Style := StrToInt(Legacy_GetFieldFromPipeString(Rec, 5));
                Obj.Width := MilsToCoord(StrToInt(Legacy_GetFieldFromPipeString(Rec, 6)));
                TargetDoc.RegisterSchObjectInContainer(Obj);
                SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
                    SCHM_PrimitiveRegistration, Obj.I_ObjectAddress);
                GfxCount := GfxCount + 1;
            end

            else if (Kind = 'NOTE') then
            begin
                Obj := SchServer.SchObjectFactory(eLabel, eCreate_GlobalCopy);
                Obj.Location := Point(MilsToCoord(StrToInt(Legacy_GetFieldFromPipeString(Rec, 1))),
                                      MilsToCoord(StrToInt(Legacy_GetFieldFromPipeString(Rec, 2))));
                Obj.Text := Legacy_GetFieldFromPipeString(Rec, 3);
                TargetDoc.RegisterSchObjectInContainer(Obj);
                SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
                    SCHM_PrimitiveRegistration, Obj.I_ObjectAddress);
                GfxCount := GfxCount + 1;
            end;
        end;

        // Parameter text last, once every part is placed.
        if (Placement.Count > 0) then
        begin
            Iter := TargetDoc.SchIterator_Create;
            try
                Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
                Prim := Iter.FirstSchObject;
                while (Prim <> nil) do
                begin
                    Legacy_StyleComponentText(Prim, Placement);
                    Prim := Iter.NextSchObject;
                end;
            finally
                TargetDoc.SchIterator_Destroy(Iter);
            end;
        end;
    finally
        SchServer.ProcessControl.PostProcess(TargetDoc, '');
    end;
    TargetDoc.GraphicallyInvalidate;

    // Pin map: absolute connection point of every placed pin, so a caller can
    // route from measured coordinates instead of predicting rotated offsets.
    PinMap := TStringList.Create;
    PinCount := 0;
    Iter := TargetDoc.SchIterator_Create;
    try
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Prim := Iter.FirstSchObject;
        while (Prim <> nil) do
        begin
            ChildIter := Prim.SchIterator_Create;
            ChildIter.AddFilter_ObjectSet(MkSet(ePin));
            Param := ChildIter.FirstSchObject;
            while (Param <> nil) do
            begin
                Legacy_GetPinHotEnd(Param, HotX, HotY);
                PinMap.Add('PIN|' + Prim.Designator.Text + '|' + Param.Name + '|' +
                           IntToStr(HotX) + '|' + IntToStr(HotY));
                PinCount := PinCount + 1;
                Param := ChildIter.NextSchObject;
            end;
            Prim.SchIterator_Destroy(ChildIter);
            Prim := Iter.NextSchObject;
        end;
    finally
        TargetDoc.SchIterator_Destroy(Iter);
    end;
    PinMap.SaveToFile(PinMapPath);
    PinMap.Free;

    Result := '{"success": true, "detached_from": "' + Adopted + '", "sheet": "' + TargetDoc.DocumentName +
              '", "parts": ' + IntToStr(PartCount) +
              ', "graphics": ' + IntToStr(GfxCount) +
              ', "pins": ' + IntToStr(PinCount) + '}';
    Spec.Free;
    Placement.Free;
end;








function Legacy_GetFootprintPrimitives(LegacyBusinessDirectory: String; LibraryPath: String; FootprintName: String): String;
var
    PcbLib      : IPCB_Library;
    LibComp     : IPCB_LibComponent;
    GrpIter     : IPCB_GroupIterator;
    Prim        : IDispatch;
    ServerDoc   : IServerDocument;
    ResultProps : TStringList;
    FPArray     : TStringList;
    FPProps     : TStringList;
    PrimsArray  : TStringList;
    PrimProps   : TStringList;
    PointsArray : TStringList;
    PProps      : TStringList;
    Counts      : TStringList;
    OutputLines : TStringList;
    TypeName    : String;
    i, C, V     : Integer;
    Found       : Boolean;
    Pad : IPCB_Pad;
    Track : IPCB_Track;
    Arc : IPCB_Arc;
    Fill : IPCB_Fill;
    TextObj : IPCB_Text;
    Region : IPCB_Region;
    Via : IPCB_Via;
begin
    Result := '';

    if (LibraryPath <> '') then
    begin
        if not FileExists(LibraryPath) then
        begin
            Result := 'ERROR: Library file not found: ' + LibraryPath;
            Exit;
        end;
        // Never re-open an open document (reload discards unsaved changes)
        if Client.IsDocumentOpen(LibraryPath) then
            ServerDoc := Client.GetDocumentByPath(LibraryPath)
        else
            ServerDoc := Client.OpenDocument('PcbLib', LibraryPath);
        if ServerDoc = Nil then
        begin
            Result := 'ERROR: Failed to open library: ' + LibraryPath;
            Exit;
        end;
        Client.ShowDocument(ServerDoc);
        Sleep(500);
    end;

    PcbLib := Legacy_GetPcbLibSafe(0);
    if (PcbLib = nil) then
    begin
        Result := 'ERROR: No PCB library is open (provide library_path or open a .PcbLib)';
        Exit;
    end;

    ResultProps := TStringList.Create;
    FPArray := TStringList.Create;
    Found := False;

    try
        Legacy_AddJSONProperty(ResultProps, 'library_name', ExtractFileName(PcbLib.Board.FileName));

        for C := 0 to PcbLib.ComponentCount - 1 do
        begin
            LibComp := PcbLib.GetComponent(C);

            if (FootprintName = '') then
            begin
                // Inventory mode
                Counts := TStringList.Create;
                FPProps := TStringList.Create;
                try
                    GrpIter := LibComp.GroupIterator_Create;
                    try
                        Prim := GrpIter.FirstPCBObject;
                        while (Prim <> nil) do
                        begin
                            If (Prim.ObjectId = ePadObject) Then
                            Begin
TypeName := 'pads';
                            End
                            Else If (Prim.ObjectId = eTrackObject) Then
                            Begin
TypeName := 'tracks';
                            End
                            Else If (Prim.ObjectId = eArcObject) Then
                            Begin
TypeName := 'arcs';
                            End
                            Else If (Prim.ObjectId = eFillObject) Then
                            Begin
TypeName := 'fills';
                            End
                            Else If (Prim.ObjectId = eTextObject) Then
                            Begin
TypeName := 'texts';
                            End
                            Else If (Prim.ObjectId = eRegionObject) Then
                            Begin
TypeName := 'regions';
                            End
                            Else If (Prim.ObjectId = eViaObject) Then
                            Begin
TypeName := 'vias';
                            End
                            Else If (Prim.ObjectId = eComponentBodyObject) Then
                            Begin
TypeName := 'component_bodies';
                            End
                            Else
                            Begin
TypeName := 'other';
                            End;
                            i := Counts.IndexOfName(TypeName);
                            if (i < 0) then
                                Counts.Add(TypeName + '=1')
                            else
                                Counts[i] := TypeName + '=' + IntToStr(StrToInt(Counts.ValueFromIndex[i]) + 1);
                            Prim := GrpIter.NextPCBObject;
                        end;
                    finally
                        LibComp.GroupIterator_Destroy(GrpIter);
                    end;

                    Legacy_AddJSONProperty(FPProps, 'name', LibComp.Name);
                    Legacy_AddJSONProperty(FPProps, 'description', LibComp.Description);
                    for i := 0 to Counts.Count - 1 do
                        Legacy_AddJSONInteger(FPProps, Counts.Names[i], StrToInt(Counts.ValueFromIndex[i]));
                    FPArray.Add(Legacy_BuildJSONObject(FPProps, 1));
                finally
                    Counts.Free;
                    FPProps.Free;
                end;
            end
            else if (FootprintName = '*') or (UpperCase(LibComp.Name) = UpperCase(FootprintName)) then
            begin
                // Dump mode
                Found := True;
                FPProps := TStringList.Create;
                PrimsArray := TStringList.Create;
                try
                    Legacy_AddJSONProperty(FPProps, 'footprint_name', LibComp.Name);
                    Legacy_AddJSONProperty(FPProps, 'description', LibComp.Description);

                    GrpIter := LibComp.GroupIterator_Create;
                    Try
                        GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));
                        Pad := GrpIter.FirstPCBObject;
                        While Pad <> Nil Do Begin
                            PrimProps := TStringList.Create;
                            Try
                                Try
Legacy_AddJSONProperty(PrimProps, 'type', 'pad');
                                        Legacy_AddJSONProperty(PrimProps, 'name', Pad.Name);
                                        Legacy_AddJSONNumber(PrimProps, 'x', CoordToMils(Pad.x));
                                        Legacy_AddJSONNumber(PrimProps, 'y', CoordToMils(Pad.y));
                                        Legacy_AddJSONNumber(PrimProps, 'rotation', Pad.Rotation);
                                        Legacy_AddJSONProperty(PrimProps, 'layer', Layer2String(Pad.Layer));
                                        Legacy_AddJSONBoolean(PrimProps, 'plated', Pad.Plated);
                                        Legacy_AddJSONInteger(PrimProps, 'mode', Pad.Mode);
                                        Legacy_AddJSONNumber(PrimProps, 'top_x_size', CoordToMils(Pad.TopXSize));
                                        Legacy_AddJSONNumber(PrimProps, 'top_y_size', CoordToMils(Pad.TopYSize));
                                        Legacy_AddJSONInteger(PrimProps, 'top_shape', Pad.TopShape);
                                        Legacy_AddJSONNumber(PrimProps, 'hole_size', CoordToMils(Pad.HoleSize));
                                        Legacy_AddJSONInteger(PrimProps, 'hole_type', Pad.HoleType);
                                        Legacy_AddJSONNumber(PrimProps, 'hole_width', CoordToMils(Pad.HoleWidth));
                                        Legacy_AddJSONNumber(PrimProps, 'hole_rotation', Pad.HoleRotation);
                                        if (Pad.Mode <> ePadMode_Simple) then
                                        begin
                                            Legacy_AddJSONNumber(PrimProps, 'mid_x_size', CoordToMils(Pad.MidXSize));
                                            Legacy_AddJSONNumber(PrimProps, 'mid_y_size', CoordToMils(Pad.MidYSize));
                                            Legacy_AddJSONInteger(PrimProps, 'mid_shape', Pad.MidShape);
                                            Legacy_AddJSONNumber(PrimProps, 'bot_x_size', CoordToMils(Pad.BotXSize));
                                            Legacy_AddJSONNumber(PrimProps, 'bot_y_size', CoordToMils(Pad.BotYSize));
                                            Legacy_AddJSONInteger(PrimProps, 'bot_shape', Pad.BotShape);
                                        end;
                                        if (Pad.TopShape = eRoundedRectangular) then
                                            Legacy_AddJSONInteger(PrimProps, 'corner_pct', Pad.StackCRPctOnLayer[eTopLayer]);

                                Except
                                    PrimProps.Free;
                                    PrimProps := TStringList.Create;
                                    Legacy_AddJSONProperty(PrimProps, 'type', 'unreadable');
                                End;
                                PrimsArray.Add(Legacy_BuildJSONObject(PrimProps, 1));
                            Finally PrimProps.Free; End;
                            Pad := GrpIter.NextPCBObject;
                        End;
                    Finally LibComp.GroupIterator_Destroy(GrpIter); End;
                    GrpIter := LibComp.GroupIterator_Create;
                    Try
                        GrpIter.AddFilter_ObjectSet(MkSet(eTrackObject));
                        Track := GrpIter.FirstPCBObject;
                        While Track <> Nil Do Begin
                            PrimProps := TStringList.Create;
                            Try
                                Try
Legacy_AddJSONProperty(PrimProps, 'type', 'track');
                                        Legacy_AddJSONNumber(PrimProps, 'x1', CoordToMils(Track.x1));
                                        Legacy_AddJSONNumber(PrimProps, 'y1', CoordToMils(Track.y1));
                                        Legacy_AddJSONNumber(PrimProps, 'x2', CoordToMils(Track.x2));
                                        Legacy_AddJSONNumber(PrimProps, 'y2', CoordToMils(Track.y2));
                                        Legacy_AddJSONNumber(PrimProps, 'width', CoordToMils(Track.Width));
                                        Legacy_AddJSONProperty(PrimProps, 'layer', Layer2String(Track.Layer));

                                Except
                                    PrimProps.Free;
                                    PrimProps := TStringList.Create;
                                    Legacy_AddJSONProperty(PrimProps, 'type', 'unreadable');
                                End;
                                PrimsArray.Add(Legacy_BuildJSONObject(PrimProps, 1));
                            Finally PrimProps.Free; End;
                            Track := GrpIter.NextPCBObject;
                        End;
                    Finally LibComp.GroupIterator_Destroy(GrpIter); End;
                    GrpIter := LibComp.GroupIterator_Create;
                    Try
                        GrpIter.AddFilter_ObjectSet(MkSet(eArcObject));
                        Arc := GrpIter.FirstPCBObject;
                        While Arc <> Nil Do Begin
                            PrimProps := TStringList.Create;
                            Try
                                Try
Legacy_AddJSONProperty(PrimProps, 'type', 'arc');
                                        Legacy_AddJSONNumber(PrimProps, 'cx', CoordToMils(Arc.XCenter));
                                        Legacy_AddJSONNumber(PrimProps, 'cy', CoordToMils(Arc.YCenter));
                                        Legacy_AddJSONNumber(PrimProps, 'radius', CoordToMils(Arc.Radius));
                                        Legacy_AddJSONNumber(PrimProps, 'start_angle', Arc.StartAngle);
                                        Legacy_AddJSONNumber(PrimProps, 'end_angle', Arc.EndAngle);
                                        Legacy_AddJSONNumber(PrimProps, 'width', CoordToMils(Arc.LineWidth));
                                        Legacy_AddJSONProperty(PrimProps, 'layer', Layer2String(Arc.Layer));

                                Except
                                    PrimProps.Free;
                                    PrimProps := TStringList.Create;
                                    Legacy_AddJSONProperty(PrimProps, 'type', 'unreadable');
                                End;
                                PrimsArray.Add(Legacy_BuildJSONObject(PrimProps, 1));
                            Finally PrimProps.Free; End;
                            Arc := GrpIter.NextPCBObject;
                        End;
                    Finally LibComp.GroupIterator_Destroy(GrpIter); End;
                    GrpIter := LibComp.GroupIterator_Create;
                    Try
                        GrpIter.AddFilter_ObjectSet(MkSet(eFillObject));
                        Fill := GrpIter.FirstPCBObject;
                        While Fill <> Nil Do Begin
                            PrimProps := TStringList.Create;
                            Try
                                Try
Legacy_AddJSONProperty(PrimProps, 'type', 'fill');
                                        Legacy_AddJSONNumber(PrimProps, 'x1', CoordToMils(Fill.x1Location));
                                        Legacy_AddJSONNumber(PrimProps, 'y1', CoordToMils(Fill.y1Location));
                                        Legacy_AddJSONNumber(PrimProps, 'x2', CoordToMils(Fill.x2Location));
                                        Legacy_AddJSONNumber(PrimProps, 'y2', CoordToMils(Fill.y2Location));
                                        Legacy_AddJSONNumber(PrimProps, 'rotation', Fill.Rotation);
                                        Legacy_AddJSONProperty(PrimProps, 'layer', Layer2String(Fill.Layer));

                                Except
                                    PrimProps.Free;
                                    PrimProps := TStringList.Create;
                                    Legacy_AddJSONProperty(PrimProps, 'type', 'unreadable');
                                End;
                                PrimsArray.Add(Legacy_BuildJSONObject(PrimProps, 1));
                            Finally PrimProps.Free; End;
                            Fill := GrpIter.NextPCBObject;
                        End;
                    Finally LibComp.GroupIterator_Destroy(GrpIter); End;
                    GrpIter := LibComp.GroupIterator_Create;
                    Try
                        GrpIter.AddFilter_ObjectSet(MkSet(eTextObject));
                        TextObj := GrpIter.FirstPCBObject;
                        While TextObj <> Nil Do Begin
                            PrimProps := TStringList.Create;
                            Try
                                Try
Legacy_AddJSONProperty(PrimProps, 'type', 'text');
                                        Legacy_AddJSONProperty(PrimProps, 'text', TextObj.Text);
                                        Legacy_AddJSONNumber(PrimProps, 'x', CoordToMils(TextObj.XLocation));
                                        Legacy_AddJSONNumber(PrimProps, 'y', CoordToMils(TextObj.YLocation));
                                        Legacy_AddJSONNumber(PrimProps, 'size', CoordToMils(TextObj.Size));
                                        Legacy_AddJSONNumber(PrimProps, 'width', CoordToMils(TextObj.Width));
                                        Legacy_AddJSONNumber(PrimProps, 'rotation', TextObj.Rotation);
                                        Legacy_AddJSONProperty(PrimProps, 'layer', Layer2String(TextObj.Layer));
                                        Legacy_AddJSONBoolean(PrimProps, 'mirror', TextObj.MirrorFlag);
                                        Legacy_AddJSONBoolean(PrimProps, 'ttf', TextObj.UseTTFonts);

                                Except
                                    PrimProps.Free;
                                    PrimProps := TStringList.Create;
                                    Legacy_AddJSONProperty(PrimProps, 'type', 'unreadable');
                                End;
                                PrimsArray.Add(Legacy_BuildJSONObject(PrimProps, 1));
                            Finally PrimProps.Free; End;
                            TextObj := GrpIter.NextPCBObject;
                        End;
                    Finally LibComp.GroupIterator_Destroy(GrpIter); End;
                    GrpIter := LibComp.GroupIterator_Create;
                    Try
                        GrpIter.AddFilter_ObjectSet(MkSet(eRegionObject));
                        Region := GrpIter.FirstPCBObject;
                        While Region <> Nil Do Begin
                            PrimProps := TStringList.Create;
                            Try
                                Try
Legacy_AddJSONProperty(PrimProps, 'type', 'region');
                                        Legacy_AddJSONProperty(PrimProps, 'layer', Layer2String(Region.Layer));
                                        Legacy_AddJSONInteger(PrimProps, 'kind', Region.Kind);
                                        PointsArray := TStringList.Create;
                                        try
                                            for V := 1 to Region.MainContour.Count do
                                            begin
                                                PProps := TStringList.Create;
                                                try
                                                    Legacy_AddJSONNumber(PProps, 'x', CoordToMils(Region.MainContour.x[V]));
                                                    Legacy_AddJSONNumber(PProps, 'y', CoordToMils(Region.MainContour.y[V]));
                                                    PointsArray.Add(Legacy_BuildJSONObject(PProps, 3));
                                                finally
                                                    PProps.Free;
                                                end;
                                            end;
                                            PrimProps.Add(Legacy_BuildJSONArray(PointsArray, 'vertices', 2));
                                        finally
                                            PointsArray.Free;
                                        end;
                                        Legacy_AddJSONInteger(PrimProps, 'hole_count', Region.HoleCount);

                                Except
                                    PrimProps.Free;
                                    PrimProps := TStringList.Create;
                                    Legacy_AddJSONProperty(PrimProps, 'type', 'unreadable');
                                End;
                                PrimsArray.Add(Legacy_BuildJSONObject(PrimProps, 1));
                            Finally PrimProps.Free; End;
                            Region := GrpIter.NextPCBObject;
                        End;
                    Finally LibComp.GroupIterator_Destroy(GrpIter); End;
                    GrpIter := LibComp.GroupIterator_Create;
                    Try
                        GrpIter.AddFilter_ObjectSet(MkSet(eViaObject));
                        Via := GrpIter.FirstPCBObject;
                        While Via <> Nil Do Begin
                            PrimProps := TStringList.Create;
                            Try
                                Try
Legacy_AddJSONProperty(PrimProps, 'type', 'via');
                                        Legacy_AddJSONNumber(PrimProps, 'x', CoordToMils(Via.x));
                                        Legacy_AddJSONNumber(PrimProps, 'y', CoordToMils(Via.y));
                                        Legacy_AddJSONNumber(PrimProps, 'size', CoordToMils(Via.Size));
                                        Legacy_AddJSONNumber(PrimProps, 'hole_size', CoordToMils(Via.HoleSize));
                                        Legacy_AddJSONProperty(PrimProps, 'low_layer', Layer2String(Via.LowLayer));
                                        Legacy_AddJSONProperty(PrimProps, 'high_layer', Layer2String(Via.HighLayer));

                                Except
                                    PrimProps.Free;
                                    PrimProps := TStringList.Create;
                                    Legacy_AddJSONProperty(PrimProps, 'type', 'unreadable');
                                End;
                                PrimsArray.Add(Legacy_BuildJSONObject(PrimProps, 1));
                            Finally PrimProps.Free; End;
                            Via := GrpIter.NextPCBObject;
                        End;
                    Finally LibComp.GroupIterator_Destroy(GrpIter); End;

                    FPProps.Add(Legacy_BuildJSONArray(PrimsArray, 'primitives', 1));

                    if (FootprintName = '*') then
                        FPArray.Add(Legacy_BuildJSONObject(FPProps, 1))
                    else
                        for i := 0 to FPProps.Count - 1 do
                            ResultProps.Add(FPProps[i]);
                finally
                    FPProps.Free;
                    PrimsArray.Free;
                end;
            end;
        end;

        if (FootprintName = '') or (FootprintName = '*') then
        begin
            Legacy_AddJSONInteger(ResultProps, 'footprint_count', FPArray.Count);
            ResultProps.Add(Legacy_BuildJSONArray(FPArray, 'footprints', 1));
        end
        else if not Found then
        begin
            Result := 'ERROR: Footprint not found in library: ' + FootprintName;
            Exit;
        end;

        OutputLines := TStringList.Create;
        try
            OutputLines.Text := Legacy_BuildJSONObject(ResultProps);
            Result := Legacy_WriteJSONToFile(OutputLines, LegacyBusinessDirectory + '\temp_footprint_primitives.json');
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
        FPArray.Free;
    end;
end;


// Function to get all unique net names from the current PCB document








function Legacy_ExecuteGetFootprintPrimitives(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    LibraryPath: String;
    FootprintName: String;
begin
    LibraryPath := '';
    FootprintName := '';

    for i := 0 to RequestData.Count - 1 do
    begin
        if (Pos('"library_path"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            LibraryPath := Legacy_TrimJSON(ParamValue);
        end
        else if (Pos('"footprint_name"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            FootprintName := Legacy_TrimJSON(ParamValue);
        end;
    end;

    Result := Legacy_GetFootprintPrimitives(WorkspaceDir, LibraryPath, FootprintName);
end;

// Extract the create footprints batch logic








function Legacy_CreateFootprintsBatch(SpecFilePath: String): String;
var
    PcbLib      : IPCB_Library;
    LibComp     : IPCB_LibComponent;
    ServerDoc   : IServerDocument;
    Lines       : TStringList;
    FailedArray : TStringList;
    ResultProps : TStringList;
    Pad         : IPCB_Pad;
    Track       : IPCB_Track;
    Arc         : IPCB_Arc;
    Fill        : IPCB_Fill;
    Text        : IPCB_Text;
    Region      : IPCB_Region;
    Contour     : IPCB_Contour;
    Via         : IPCB_Via;
    Line, Kind  : String;
    LibPath     : String;
    FieldValue  : String;
    CreatedCount: Integer;
    PrimErrors  : Integer;
    i, V        : Integer;


begin
    if not FileExists(SpecFilePath) then
    begin
        Result := 'ERROR: Spec file not found: ' + SpecFilePath;
        Exit;
    end;

    Lines := TStringList.Create;
    FailedArray := TStringList.Create;
    ResultProps := TStringList.Create;
    LibComp := nil;
    CreatedCount := 0;
    PrimErrors := 0;

    try
        Lines.LoadFromFile(SpecFilePath);

        for i := 0 to Lines.Count - 1 do
        begin
            Line := Lines[i];
            Kind := UpperCase(Trim(Legacy_GetFieldFromPipeString(Line, 0)));

            try
                if (Kind = 'FPLIB') then
                begin
                    LibPath := Legacy_GetFieldFromPipeString(Line, 1);
                    if (LibPath <> '') and FileExists(LibPath) then
                    begin
                        if Client.IsDocumentOpen(LibPath) then
                            ServerDoc := Client.GetDocumentByPath(LibPath)
                        else
                            ServerDoc := Client.OpenDocument('PcbLib', LibPath);
                        if (ServerDoc <> Nil) then
                        begin
                            Client.ShowDocument(ServerDoc);
                            Sleep(500);
                        end;
                    end;
                end
                else if (Kind = 'FOOTPRINT') then
                begin
                    PcbLib := Legacy_GetPcbLibSafe(0);
                    // Focus can drift between chunks - retry via the opened
                    // document before giving up
                    if (PcbLib = nil) and (ServerDoc <> nil) then
                    begin
                        Client.ShowDocument(ServerDoc);
                        Sleep(1000);
                        PcbLib := Legacy_GetPcbLibSafe(0);
                    end;
                    if (PcbLib = nil) then
                    begin
                        Result := 'ERROR: No PCB library document is active';
                        Exit;
                    end;
                    LibComp := PCBServer.CreatePCBLibComp;
                    LibComp.Name := Trim(Legacy_GetFieldFromPipeString(Line, 1));
                    LibComp.Description := Legacy_GetFieldFromPipeString(Line, 2);
                    PcbLib.RegisterComponent(LibComp);
                    CreatedCount := CreatedCount + 1;
                end
                else if (LibComp <> nil) and (Kind = 'PAD') then
                begin
                    Pad := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
                    // Pad name is NOT trimmed - round-trip fidelity preserves
                    // whitespace and even control characters found in source data
                    Pad.Name := Legacy_GetFieldFromPipeString(Line, 1);
                    // Mode first so per-stack sizes land correctly
                    FieldValue := Trim(Legacy_GetFieldFromPipeString(Line, 15));
                    if (FieldValue <> '') then
                        Pad.Mode := StrToInt(FieldValue)
                    else
                        Pad.Mode := ePadMode_Simple;
                    Pad.x := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 2))));
                    Pad.y := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 3))));
                    Pad.Layer := String2Layer(Trim(Legacy_GetFieldFromPipeString(Line, 5)));
                    Pad.Plated := (Trim(Legacy_GetFieldFromPipeString(Line, 6)) = '1');
                    Pad.HoleSize := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 7))));
                    Pad.HoleType := StrToInt(Trim(Legacy_GetFieldFromPipeString(Line, 8)));
                    Pad.HoleWidth := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 9))));
                    Pad.HoleRotation := Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 10)));
                    Pad.TopXSize := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 11))));
                    Pad.TopYSize := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 12))));
                    Pad.TopShape := StrToInt(Trim(Legacy_GetFieldFromPipeString(Line, 13)));
                    FieldValue := Trim(Legacy_GetFieldFromPipeString(Line, 14));
                    if (FieldValue <> '') then
                        Pad.StackCRPctOnLayer[eTopLayer] := StrToInt(FieldValue);
                    if (Pad.Mode <> ePadMode_Simple) then
                    begin
                        Pad.MidXSize := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 16))));
                        Pad.MidYSize := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 17))));
                        Pad.MidShape := StrToInt(Trim(Legacy_GetFieldFromPipeString(Line, 18)));
                        Pad.BotXSize := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 19))));
                        Pad.BotYSize := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 20))));
                        Pad.BotShape := StrToInt(Trim(Legacy_GetFieldFromPipeString(Line, 21)));
                    end;
                    // Rotation last: it rotates the pad about its location
                    Pad.Rotation := Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 4)));
                    LibComp.AddPCBObject(Pad);
                    PCBServer.SendMessageToRobots(Pad.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);
                end
                else if (LibComp <> nil) and (Kind = 'TRACK') then
                begin
                    Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
                    Track.x1 := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 1))));
                    Track.y1 := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 2))));
                    Track.x2 := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 3))));
                    Track.y2 := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 4))));
                    Track.Width := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 5))));
                    Track.Layer := String2Layer(Trim(Legacy_GetFieldFromPipeString(Line, 6)));
                    LibComp.AddPCBObject(Track);
                    PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);
                end
                else if (LibComp <> nil) and (Kind = 'ARC') then
                begin
                    Arc := PCBServer.PCBObjectFactory(eArcObject, eNoDimension, eCreate_Default);
                    Arc.XCenter := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 1))));
                    Arc.YCenter := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 2))));
                    Arc.Radius := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 3))));
                    Arc.StartAngle := Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 4)));
                    Arc.EndAngle := Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 5)));
                    Arc.LineWidth := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 6))));
                    Arc.Layer := String2Layer(Trim(Legacy_GetFieldFromPipeString(Line, 7)));
                    LibComp.AddPCBObject(Arc);
                    PCBServer.SendMessageToRobots(Arc.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);
                end
                else if (LibComp <> nil) and (Kind = 'FILL') then
                begin
                    Fill := PCBServer.PCBObjectFactory(eFillObject, eNoDimension, eCreate_Default);
                    Fill.x1Location := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 1))));
                    Fill.y1Location := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 2))));
                    Fill.x2Location := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 3))));
                    Fill.y2Location := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 4))));
                    Fill.Rotation := Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 5)));
                    Fill.Layer := String2Layer(Trim(Legacy_GetFieldFromPipeString(Line, 6)));
                    LibComp.AddPCBObject(Fill);
                    PCBServer.SendMessageToRobots(Fill.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);
                end
                else if (LibComp <> nil) and (Kind = 'VIA') then
                begin
                    Via := PCBServer.PCBObjectFactory(eViaObject, eNoDimension, eCreate_Default);
                    Via.x := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 1))));
                    Via.y := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 2))));
                    Via.Size := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 3))));
                    Via.HoleSize := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 4))));
                    Via.LowLayer := String2Layer(Trim(Legacy_GetFieldFromPipeString(Line, 5)));
                    Via.HighLayer := String2Layer(Trim(Legacy_GetFieldFromPipeString(Line, 6)));
                    LibComp.AddPCBObject(Via);
                    PCBServer.SendMessageToRobots(Via.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);
                end
                else if (LibComp <> nil) and (Kind = 'REGION') then
                begin
                    Region := PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default);
                    Contour := PCBServer.PCBContourFactory;
                    V := 3;
                    while (Trim(Legacy_GetFieldFromPipeString(Line, V)) <> '') do
                    begin
                        Contour.AddPoint(MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, V)))),
                                         MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, V + 1)))));
                        V := V + 2;
                    end;
                    Region.SetOutlineContour(Contour);
                    Region.Layer := String2Layer(Trim(Legacy_GetFieldFromPipeString(Line, 1)));
                    Region.Kind := StrToInt(Trim(Legacy_GetFieldFromPipeString(Line, 2)));
                    LibComp.AddPCBObject(Region);
                    PCBServer.SendMessageToRobots(Region.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);
                end
                else if (LibComp <> nil) and (Kind = 'TEXT') then
                begin
                    Text := PCBServer.PCBObjectFactory(eTextObject, eNoDimension, eCreate_Default);
                    Text.XLocation := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 1))));
                    Text.YLocation := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 2))));
                    Text.Size := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 3))));
                    Text.Width := MilsToCoord(Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 4))));
                    Text.Layer := String2Layer(Trim(Legacy_GetFieldFromPipeString(Line, 6)));
                    Text.MirrorFlag := (Trim(Legacy_GetFieldFromPipeString(Line, 7)) = '1');
                    Text.UseTTFonts := (Trim(Legacy_GetFieldFromPipeString(Line, 8)) = '1');
                    // <NL> placeholder carries embedded newlines through the
                    // line-based spec file
                    Text.Text := StringReplace(Legacy_GetFieldFromPipeString(Line, 9), '<NL>', #13#10, LegacyReplaceAll);
                    Text.Rotation := Legacy_SafeStrToFloat(Trim(Legacy_GetFieldFromPipeString(Line, 5)));
                    LibComp.AddPCBObject(Text);
                    PCBServer.SendMessageToRobots(Text.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);
                end;
            except
                PrimErrors := PrimErrors + 1;
                if (Kind = 'FOOTPRINT') then
                    FailedArray.Add('"' + EscapeJsonString(Trim(Legacy_GetFieldFromPipeString(Line, 1))) + '"');
            end;
        end;

        Legacy_AddJSONInteger(ResultProps, 'created', CreatedCount);
        Legacy_AddJSONInteger(ResultProps, 'primitive_errors', PrimErrors);
        if (FailedArray.Count > 0) then
            ResultProps.Add(Legacy_BuildJSONArray(FailedArray, 'failed'))
        else
            ResultProps.Add('"failed": []');

        Result := Legacy_BuildJSONObject(ResultProps);
    finally
        Lines.Free;
        FailedArray.Free;
        ResultProps.Free;
    end;
end;

// Get the graphic/copper primitives of footprints in a PCB library.
// FootprintName = '' -> inventory (per-footprint primitive counts);
// '*' -> full dump of every footprint; name -> dump that footprint.
// Coordinates in mils, layers as names, shapes/hole types as raw enum ints
// (pass-through symmetric with create_footprints_batch).








function Legacy_ExecuteCreateFootprintsBatch(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    SpecFile: String;
begin
    SpecFile := '';

    for i := 0 to RequestData.Count - 1 do
    begin
        if (Pos('"spec_file"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            SpecFile := Legacy_TrimJSON(ParamValue);
        end;
    end;

    if (SpecFile <> '') then
        Result := Legacy_CreateFootprintsBatch(SpecFile)
    else
        Result := 'ERROR: No spec_file provided for create_footprints_batch';
end;

// Extract the create symbols batch logic








function Legacy_DuplicateSelectedObjects(Board: IPCB_Board; ObjectSet: TSet): TObjectList;
var
    Iterator       : IPCB_BoardIterator;
    OrigObj, NewObj : IPCB_Primitive;
    DuplicatedObjects : TObjectList;
    temp: String;
begin
    // Create object list to store duplicated objects
    DuplicatedObjects := CreateObject(TObjectList);
    DuplicatedObjects.OwnsObjects := False; // Don't destroy objects when list is freed

    // Create iterator for the specified object type
    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(ObjectSet);
    Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
    Iterator.AddFilter_Method(eProcessAll);

    PCBServer.PreProcess;

    OrigObj := Iterator.FirstPCBObject;
    while (OrigObj <> Nil) do
    begin
        if OrigObj.Selected then
        begin
            // Replicate the object
            NewObj := PCBServer.PCBObjectFactory(OrigObj.ObjectId, eNoDimension, eCreate_Default);
            NewObj := OrigObj.Replicate;

            // Add to board
            PCBServer.SendMessageToRobots(NewObj.I_ObjectAddress, c_Broadcast, PCBM_BeginModify, c_NoEventData);
            Board.AddPCBObject(NewObj);
            PCBServer.SendMessageToRobots(NewObj.I_ObjectAddress, c_Broadcast, PCBM_EndModify, c_NoEventData);

            // Send board registration message
            //PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, NewObj.I_ObjectAddress);

            // Add to our list of duplicated objects
            DuplicatedObjects.Add(NewObj);
        end;

        OrigObj := Iterator.NextPCBObject;
    end;

    Board.BoardIterator_Destroy(Iterator);

    PCBServer.PostProcess;

    Board.ViewManager_FullUpdate();

    Result := DuplicatedObjects;
end;

// Function to get source and destination component lists with pin data








function Legacy_GetLayoutDuplicatorComponents(SelectedOnly: Boolean = True): String;
var
    Board          : IPCB_Board;
    Iterator       : IPCB_BoardIterator;
    SourceCmps     : TStringList;
    ResultProps    : TStringList;
    SourceArray    : TStringList;
    DestArray      : TStringList;
    Component      : IPCB_Component;
    CompProps      : TStringList;
    PinsArray      : TStringList;
    GrpIter        : IPCB_GroupIterator;
    Pad            : IPCB_Pad;
    i, j           : Integer;
    PinCount       : Integer;
    NetName        : String;
    xorigin, yorigin : Integer;
    PinProps       : TStringList;
    OutputLines    : TStringList;

    // For duplicated objects
    DuplicatedObjects : TObjectList;
    Obj               : IPCB_Primitive;
begin
    // Retrieve the current board
    Board := Legacy_GetBoardSafe(0);
    if (Board = Nil) then
    begin
        Result := '{"success": false, "message": "No PCB document is currently active"}';
        Exit;
    end;

    // Get board origin coordinates
    xorigin := Board.XOrigin;
    yorigin := Board.YOrigin;

    // Create result properties
    ResultProps := TStringList.Create;
    SourceCmps := TStringList.Create;
    SourceArray := TStringList.Create;

    try
        // Get selected components as source
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
        Iterator.AddFilter_LayerSet(MkSet(eTopLayer, eBottomLayer));
        Iterator.AddFilter_Method(eProcessAll);

        Component := Iterator.FirstPCBObject;
        while (Component <> Nil) do
        begin
            if (Component.Selected = True) then
                SourceCmps.Add(Component.Name.Text);

            Component := Iterator.NextPCBObject;
        end;
        Board.BoardIterator_Destroy(Iterator);

        // Check if any source components were selected
        if (SourceCmps.Count = 0) then
        begin
            Legacy_AddJSONBoolean(ResultProps, 'success', False);
            Legacy_AddJSONProperty(ResultProps, 'message', 'No source components selected. Please select source components first.');

            OutputLines := TStringList.Create;
            try
                OutputLines.Text := Legacy_BuildJSONObject(ResultProps);
                Result := OutputLines.Text;
            finally
                OutputLines.Free;
            end;

            Exit;
        end;

        // Duplicate all object types in one call
        DuplicatedObjects := Legacy_DuplicateSelectedObjects(Board, MkSet(eTrackObject, eArcObject, eViaObject, ePolyObject, eRegionObject, eFillObject));

        // Deselect all original objects to avoid duplicating them again
        Iterator := Board.BoardIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, eViaObject, ePolyObject, eRegionObject, eFillObject));
        Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
        Iterator.AddFilter_Method(eProcessAll);

        PCBServer.PreProcess;
        Obj := Iterator.FirstPCBObject;
        while (Obj <> Nil) do
        begin
            // Only deselect objects that are not in our duplicated list
            if Obj.Selected and (DuplicatedObjects.IndexOf(Obj) < 0) then
                Obj.Selected := False;

            Obj := Iterator.NextPCBObject;
        end;
        PCBServer.PostProcess;
        Board.BoardIterator_Destroy(Iterator);

        // Select only the duplicated objects
        for i := 0 to DuplicatedObjects.Count - 1 do
        begin
            Obj := DuplicatedObjects[i];
            if (Obj <> nil) then
                Obj.Selected := True;
        end;

        // Add source components to JSON
        for i := 0 to SourceCmps.Count - 1 do
        begin
            Component := Board.GetPcbComponentByRefDes(SourceCmps[i]);
            if (Component <> nil) then
            begin
                // Create component properties
                CompProps := TStringList.Create;
                PinsArray := TStringList.Create;

                try
                    // Add component properties
                    Legacy_AddJSONProperty(CompProps, 'designator', Component.Name.Text);
                    Legacy_AddJSONProperty(CompProps, 'description', Component.SourceDescription);
                    Legacy_AddJSONProperty(CompProps, 'footprint', Component.Pattern);
                    Legacy_AddJSONNumber(CompProps, 'rotation', Component.Rotation);
                    Legacy_AddJSONProperty(CompProps, 'layer', Layer2String(Component.Layer));

                    // Add pin data
                    // Create pad iterator
                    GrpIter := Component.GroupIterator_Create;
                    GrpIter.SetState_FilterAll;
                    GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));

                    // Process each pad
                    Pad := GrpIter.FirstPCBObject;
                    while (Pad <> Nil) do
                    begin
                        if Pad.InComponent then
                        begin
                            // Get net name if connected
                            if (Pad.Net <> Nil) then
                                NetName := EscapeJsonString(Pad.Net.Name)
                            else
                                NetName := '';

                            // Create pin properties
                            PinProps := TStringList.Create;
                            try
                                Legacy_AddJSONProperty(PinProps, 'name', Pad.Name);
                                Legacy_AddJSONProperty(PinProps, 'net', NetName);
                                Legacy_AddJSONNumber(PinProps, 'x', CoordToMils(Pad.x - xorigin));
                                Legacy_AddJSONNumber(PinProps, 'y', CoordToMils(Pad.y - yorigin));
                                Legacy_AddJSONProperty(PinProps, 'layer', Layer2String(Pad.Layer));

                                // Add to pins array
                                PinsArray.Add(Legacy_BuildJSONObject(PinProps, 3));
                            finally
                                PinProps.Free;
                            end;
                        end;

                        Pad := GrpIter.NextPCBObject;
                    end;

                    // Clean up iterator
                    Component.GroupIterator_Destroy(GrpIter);

                    // Add pins array to component
                    CompProps.Add(Legacy_BuildJSONArray(PinsArray, 'pins', 1));

                    // Add to source array
                    SourceArray.Add(Legacy_BuildJSONObject(CompProps, 2));
                finally
                    CompProps.Free;
                    PinsArray.Free;
                end;
            end;
        end;

        // Reset selection for destination components
        Client.SendMessage('PCB:DeSelect', 'Scope=All', 255, Client.CurrentView);

        // Have the user select destination components
        Client.SendMessage('PCB:Select', 'Scope=InsideArea | ObjectKind=Component', 255, Client.CurrentView);

        // Get the newly selected components (destination)
        SourceCmps.Text := '';
        DestArray := TStringList.Create();

        try
            // Get newly selected components
            Iterator := Board.BoardIterator_Create;
            Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
            Iterator.AddFilter_LayerSet(MkSet(eTopLayer, eBottomLayer));
            Iterator.AddFilter_Method(eProcessAll);

            Component := Iterator.FirstPCBObject;
            while (Component <> Nil) do
            begin
                if (Component.Selected = True) then
                    SourceCmps.Add(Component.Name.Text);

                Component := Iterator.NextPCBObject;
            end;
            Board.BoardIterator_Destroy(Iterator);

            // Add destination components to JSON
            for i := 0 to SourceCmps.Count - 1 do
            begin
                Component := Board.GetPcbComponentByRefDes(SourceCmps[i]);
                if (Component <> nil) then
                begin
                    // Create component properties
                    CompProps := TStringList.Create;
                    PinsArray := TStringList.Create;

                    try
                        // Add component properties
                        Legacy_AddJSONProperty(CompProps, 'designator', Component.Name.Text);
                        Legacy_AddJSONProperty(CompProps, 'description', Component.SourceDescription);
                        Legacy_AddJSONProperty(CompProps, 'footprint', Component.Pattern);
                        Legacy_AddJSONNumber(CompProps, 'rotation', Component.Rotation);
                        Legacy_AddJSONProperty(CompProps, 'layer', Layer2String(Component.Layer));

                        // Add pin data
                        // Create pad iterator
                        GrpIter := Component.GroupIterator_Create;
                        GrpIter.SetState_FilterAll;
                        GrpIter.AddFilter_ObjectSet(MkSet(ePadObject));

                        // Process each pad
                        Pad := GrpIter.FirstPCBObject;
                        while (Pad <> Nil) do
                        begin
                            if Pad.InComponent then
                            begin
                                // Get net name if connected
                                if (Pad.Net <> Nil) then
                                    NetName := EscapeJsonString(Pad.Net.Name)
                                else
                                    NetName := '';

                                // Create pin properties
                                PinProps := TStringList.Create;
                                try
                                    Legacy_AddJSONProperty(PinProps, 'name', Pad.Name);
                                    Legacy_AddJSONProperty(PinProps, 'net', NetName);
                                    Legacy_AddJSONNumber(PinProps, 'x', CoordToMils(Pad.x - xorigin));
                                    Legacy_AddJSONNumber(PinProps, 'y', CoordToMils(Pad.y - yorigin));
                                    Legacy_AddJSONProperty(PinProps, 'layer', Layer2String(Pad.Layer));

                                    // Add to pins array
                                    PinsArray.Add(Legacy_BuildJSONObject(PinProps, 3));
                                finally
                                    PinProps.Free;
                                end;
                            end;

                            Pad := GrpIter.NextPCBObject;
                        end;

                        // Clean up iterator
                        Component.GroupIterator_Destroy(GrpIter);

                        // Add pins array to component
                        CompProps.Add(Legacy_BuildJSONArray(PinsArray, 'pins', 1));

                        // Add to destination array
                        DestArray.Add(Legacy_BuildJSONObject(CompProps, 2));
                    finally
                        CompProps.Free;
                        PinsArray.Free;
                    end;
                end;
            end;

            // Now select all duplicated objects
            for i := 0 to DuplicatedObjects.Count - 1 do
            begin
                Obj := DuplicatedObjects[i];
                if (Obj <> nil) then
                    Obj.Selected := True;
            end;

            // Add all arrays to result
            Legacy_AddJSONBoolean(ResultProps, 'success', True);
            ResultProps.Add(Legacy_BuildJSONArray(SourceArray, 'source_components'));
            ResultProps.Add(Legacy_BuildJSONArray(DestArray, 'destination_components'));
            Legacy_AddJSONProperty(ResultProps, 'message', 'Successfully duplicated objects. Match each source and destination designator using the part descriptions, pin data, and other information. Then call layout_duplicator_apply and pass the source and destination lists in matching order.');

            // Build final JSON
            OutputLines := TStringList.Create;
            try
                OutputLines.Text := Legacy_BuildJSONObject(ResultProps);
                Result := OutputLines.Text;
            finally
                OutputLines.Free;
            end;
        finally
            DestArray.Free;
        end;
    finally
        ResultProps.Free;
        SourceCmps.Free;
        SourceArray.Free;
    end;
end;

// Function to check if two points are within tolerance








function Legacy_CheckWithTolerance(X1, Y1, X2, Y2 : TCoord) : Boolean;
begin
    if (Abs(X1 - X2) <= Tolerance) and (Abs(Y1 - Y2) <= Tolerance) then
        Result := True
    else
        Result := False;
end;

// Function to apply layout duplication with provided source and destination lists





function Legacy_ApplyLayoutDuplicator(SourceList: TStringList; DestList: TStringList): String;
var
    Board          : IPCB_Board;
    CmpSrc, CmpDst : IPCB_Component;
    NameSrc, NameDst : TPCB_String;
    i, j           : Integer;
    ResultProps    : TStringList;
    MovedCount     : Integer;
    OutputLines    : TStringList;
    PadIterator    : IPCB_GroupIterator;
    Pad            : IPCB_Pad;
    ProcessedPoints: TStringList;
    Tolerance      : TCoord;

    // For faster tracking of processed primitives
    ProcessedObjects : TStringList;

    // For net tracing
    SelectedObjects : TObjectList;
    ConnectedPrim   : IPCB_Primitive;
    TraceStack      : TStringList;
    X, Y, NextX, NextY : TCoord;
    StackSize       : Integer;
    PointInfo       : String;
    Net             : IPCB_Net;
    ObjectAddress   : Integer;

    // For polygon processing
    Polygon        : IPCB_Primitive;
    PadRect        : TCoordRect;
    PolyRect       : TCoordRect;
    Overlapping    : Boolean;
    PolygonCount   : Integer;

    // For net invalidation
    NetsToInvalidate: TStringList;
begin
    // Retrieve the current board
    Board := Legacy_GetBoardSafe(0);
    if (Board = Nil) then
    begin
        Result := '{"success": false, "error": "No PCB document is currently active"}';
        Exit;
    end;

    // Create result properties
    ResultProps := TStringList.Create;
    MovedCount := 0;
    PolygonCount := 0;

    // Create list to track processed points
    ProcessedPoints := TStringList.Create;
    ProcessedPoints.Duplicates := dupIgnore;  // Ignore duplicate entries

    // Create list to track processed primitives (using object address as string)
    ProcessedObjects := TStringList.Create;
    ProcessedObjects.Duplicates := dupIgnore;  // Ignore duplicate entries

    // Create list for net invalidation (using net names)
    NetsToInvalidate := TStringList.Create;
    NetsToInvalidate.Duplicates := dupIgnore;

    // Create stack for tracking points to process
    TraceStack := TStringList.Create;

    // Set a small tolerance for connection checking (1 mil)
    Tolerance := MilsToCoord(1);

    // Create collection for all selected objects
    SelectedObjects := TObjectList.Create; // Don't own objects
    SelectedObjects.OwnsObjects := False; // Don't destroy objects when list is freed

    try
        // Begin board modification
        PCBServer.PreProcess;

        // Collect all selected objects first - OPTIMIZATION #1
        // This is our biggest optimization - collecting all selected objects once
        for i := 0 to Board.SelectecObjectCount - 1 do
        begin
            ConnectedPrim := Board.SelectecObject[i];

            // Only collect relevant object types
            if (ConnectedPrim.ObjectId = eTrackObject) or
               (ConnectedPrim.ObjectId = eArcObject) or
               (ConnectedPrim.ObjectId = eViaObject) or
               (ConnectedPrim.ObjectId = ePolyObject) or
               (ConnectedPrim.ObjectId = eRegionObject) or
               (ConnectedPrim.ObjectId = eFillObject) then
            begin
                SelectedObjects.Add(ConnectedPrim);
            end;
        end;

        // Process component pairs
        for i := 0 to SourceList.Count - 1 do
        begin
            if (i < DestList.Count) then
            begin
                NameSrc := SourceList.Get(i);
                CmpSrc := Board.GetPcbComponentByRefDes(NameSrc);

                NameDst := DestList.Get(i);
                CmpDst := Board.GetPcbComponentByRefDes(NameDst);

                if ((CmpSrc <> nil) and (CmpDst <> nil)) then
                begin
                    // Begin modify component
                    CmpDst.BeginModify;

                    // Move Destination Components to Match Source Components
                    CmpDst.Rotation := CmpSrc.Rotation;
                    CmpDst.Layer_V6 := CmpSrc.Layer_V6;
                    CmpDst.x := CmpSrc.x;
                    CmpDst.y := CmpSrc.y;
                    CmpDst.Selected := True;

                    // End modify component
                    CmpDst.EndModify;

                    // Register component with the board
                    Board.DispatchMessage(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, CmpDst.I_ObjectAddress);

                    // Clear the processed points list for this component
                    ProcessedPoints.Text := '';

                    // Clear processed objects list
                    ProcessedObjects.Text := '';

                    // Clear nets to invalidate
                    NetsToInvalidate.Text := '';

                    // Process all pads in the destination component
                    PadIterator := CmpDst.GroupIterator_Create;
                    PadIterator.AddFilter_ObjectSet(MkSet(ePadObject));

                    Pad := PadIterator.FirstPCBObject;
                    while Pad <> nil do
                    begin
                        // Skip if pad has no net
                        if (Pad.Net <> nil) then
                        begin
                            Net := Pad.Net;

                            // Track this net for final invalidation
                            if NetsToInvalidate.IndexOf(Net.Name) < 0 then
                                NetsToInvalidate.Add(Net.Name);

                            // Clear the stack
                            TraceStack.Text := '';

                            // Add initial pad position to stack
                            TraceStack.Add(IntToStr(Pad.x) + ',' + IntToStr(Pad.y));

                            // Process until stack is empty
                            while TraceStack.Count > 0 do
                            begin
                                // Pop a point from the stack
                                StackSize := TraceStack.Count;
                                PointInfo := TraceStack[StackSize - 1];
                                TraceStack.Delete(StackSize - 1);

                                // Skip if we've already processed this point
                                if ProcessedPoints.IndexOf(PointInfo) >= 0 then
                                    Continue;

                                // Mark this point as processed
                                ProcessedPoints.Add(PointInfo);

                                // Extract X,Y from the point info
                                X := StrToInt(Copy(PointInfo, 1, Pos(',', PointInfo) - 1));
                                Y := StrToInt(Copy(PointInfo, Pos(',', PointInfo) + 1, Length(PointInfo)));

                                // Process all selected objects - OPTIMIZATION
                                for j := SelectedObjects.Count - 1 downto 0 do
                                begin
                                    ConnectedPrim := SelectedObjects[j];

                                    // Skip if already processed
                                    ObjectAddress := ConnectedPrim.I_ObjectAddress;
                                    if ProcessedObjects.IndexOf(IntToStr(ObjectAddress)) >= 0 then
                                        Continue;

                                    // Skip if already processed (using object address as identifier)
                                    ObjectAddress := ConnectedPrim.I_ObjectAddress;
                                    if ProcessedObjects.IndexOf(IntToStr(ObjectAddress)) >= 0 then
                                        Continue;

                                    // Check if primitive is at this point
                                    if ConnectedPrim.ObjectId = eTrackObject then
                                    begin
                                        // Check both endpoints
                                        if Legacy_CheckWithTolerance(ConnectedPrim.x1, ConnectedPrim.y1, X, Y) or
                                           Legacy_CheckWithTolerance(ConnectedPrim.x2, ConnectedPrim.y2, X, Y) then
                                        begin
                                            // Apply the net
                                            ConnectedPrim.BeginModify;
                                            ConnectedPrim.Net := Net;
                                            ConnectedPrim.EndModify;

                                            // Register primitive with the board
                                            Board.DispatchMessage(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, ConnectedPrim.I_ObjectAddress);

                                            // Mark as processed
                                            ProcessedObjects.Add(IntToStr(ObjectAddress));

                                            // Add other endpoint to stack
                                            if Legacy_CheckWithTolerance(ConnectedPrim.x1, ConnectedPrim.y1, X, Y) then
                                                TraceStack.Add(IntToStr(ConnectedPrim.x2) + ',' + IntToStr(ConnectedPrim.y2))
                                            else
                                                TraceStack.Add(IntToStr(ConnectedPrim.x1) + ',' + IntToStr(ConnectedPrim.y1));

                                            // Remove from selected objects to speed up future searches
                                            SelectedObjects.Delete(j);
                                        end;
                                    end
                                    else if ConnectedPrim.ObjectId = eArcObject then
                                    begin
                                        // Check both endpoints
                                        if Legacy_CheckWithTolerance(ConnectedPrim.StartX, ConnectedPrim.StartY, X, Y) or
                                           Legacy_CheckWithTolerance(ConnectedPrim.EndX, ConnectedPrim.EndY, X, Y) then
                                        begin
                                            // Apply the net
                                            ConnectedPrim.BeginModify;
                                            ConnectedPrim.Net := Net;
                                            ConnectedPrim.EndModify;

                                            // Register primitive with the board
                                            Board.DispatchMessage(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, ConnectedPrim.I_ObjectAddress);

                                            // Mark as processed
                                            ProcessedObjects.Add(IntToStr(ObjectAddress));

                                            // Add other endpoint to stack
                                            if Legacy_CheckWithTolerance(ConnectedPrim.StartX, ConnectedPrim.StartY, X, Y) then
                                                TraceStack.Add(IntToStr(ConnectedPrim.EndX) + ',' + IntToStr(ConnectedPrim.EndY))
                                            else
                                                TraceStack.Add(IntToStr(ConnectedPrim.StartX) + ',' + IntToStr(ConnectedPrim.StartY));

                                            // Remove from selected objects
                                            SelectedObjects.Delete(j);
                                        end;
                                    end
                                    else if ConnectedPrim.ObjectId = eViaObject then
                                    begin
                                        // Check single point
                                        if Legacy_CheckWithTolerance(ConnectedPrim.x, ConnectedPrim.y, X, Y) then
                                        begin
                                            ConnectedPrim.BeginModify;
                                            ConnectedPrim.Net := Net;
                                            ConnectedPrim.EndModify;

                                            // Register primitive with the board
                                            Board.DispatchMessage(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, ConnectedPrim.I_ObjectAddress);

                                            // Mark as processed
                                            ProcessedObjects.Add(IntToStr(ObjectAddress));

                                            // Remove from selected objects
                                            SelectedObjects.Delete(j);

                                            // Mark as processed rather than removing
                                            //ProcessedObjects.Add(IntToStr(ObjectAddress));
                                        end;
                                    end;
                                end;
                            end;

                            // Process polygons for this pad - using filtered list
                            PadRect := Pad.BoundingRectangle;

                            // Process each selected polygon from our collected objects
                            for j := SelectedObjects.Count - 1 downto 0 do
                            begin
                                Polygon := SelectedObjects[j];

                                // Skip if already processed
                                ObjectAddress := Polygon.I_ObjectAddress;
                                if ProcessedObjects.IndexOf(IntToStr(ObjectAddress)) >= 0 then
                                    Continue;

                                // Check if it's a polygon type on the same layer
                                if ((Polygon.ObjectId = ePolyObject) or
                                    (Polygon.ObjectId = eRegionObject) or
                                    (Polygon.ObjectId = eFillObject)) and
                                    (Polygon.Layer = Pad.Layer) then
                                begin
                                    // Get polygon's bounding rectangle
                                    PolyRect := Polygon.BoundingRectangle;

                                    // Faster bounding box check
                                    if (PadRect.Left <= PolyRect.Right + Tolerance) and
                                       (PadRect.Right >= PolyRect.Left - Tolerance) and
                                       (PadRect.Bottom <= PolyRect.Top + Tolerance) and
                                       (PadRect.Top >= PolyRect.Bottom - Tolerance) then
                                    begin
                                        Overlapping := False;

                                        // For polygon, use PointInPolygon
                                        if Polygon.ObjectId = ePolyObject then
                                        begin
                                            // Check pad center and corners
                                            X := (PadRect.Left + PadRect.Right) div 2;
                                            Y := (PadRect.Bottom + PadRect.Top) div 2;

                                            if Polygon.PointInPolygon(X, Y) or
                                               Polygon.PointInPolygon(PadRect.Left, PadRect.Bottom) or
                                               Polygon.PointInPolygon(PadRect.Left, PadRect.Top) or
                                               Polygon.PointInPolygon(PadRect.Right, PadRect.Bottom) or
                                               Polygon.PointInPolygon(PadRect.Right, PadRect.Top) then
                                            begin
                                                Overlapping := True;
                                            end;
                                        end
                                        // For regions and fills, use distance checking
                                        else if Board.PrimPrimDistance(Pad, Polygon) <= Tolerance then
                                        begin
                                            Overlapping := True;
                                        end;

                                        if Overlapping then
                                        begin
                                            // Assign pad's net to the polygon
                                            Polygon.BeginModify;
                                            Polygon.Net := Net;
                                            Polygon.EndModify;

                                            // Register with board
                                            Board.DispatchMessage(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Polygon.I_ObjectAddress);

                                            // Mark as processed
                                            ProcessedObjects.Add(IntToStr(ObjectAddress));

                                            // Remove from the selected objects
                                            SelectedObjects.Delete(j);

                                            Inc(PolygonCount);
                                        end;
                                    end;
                                end;
                            end;
                        end;

                        Pad := PadIterator.NextPCBObject;
                    end;

                    CmpDst.GroupIterator_Destroy(PadIterator);

                    MovedCount := MovedCount + 1;
                end;
            end;
        end;

        // End board modification
        PCBServer.PostProcess;

        // Force redraw of the view - once at the end
        Client.SendMessage('PCB:Zoom', 'Action=Redraw', 255, Client.CurrentView);

        // Update connectivity
        ResetParameters;
        AddStringParameter('Action', 'RebuildConnectivity');
        RunProcess('PCB:UpdateConnectivity');

        // Rebuild net connectivity so ratsnest and net highlighting work correctly
        Board.ConnectivelyValidateNets;

        // Run full update
        Board.ViewManager_FullUpdate;

        // Create result JSON
        Legacy_AddJSONBoolean(ResultProps, 'success', True);
        Legacy_AddJSONInteger(ResultProps, 'moved_count', MovedCount);
        Legacy_AddJSONInteger(ResultProps, 'polygon_count', PolygonCount);
        Legacy_AddJSONProperty(ResultProps, 'message', 'Successfully duplicated layout and applied nets for ' + IntToStr(MovedCount) +
                        ' components and ' + IntToStr(PolygonCount) + ' polygons/regions/fills.');

        // Build final JSON
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := Legacy_BuildJSONObject(ResultProps);
            Result := OutputLines.Text;
        finally
            OutputLines.Free;
        end;
    finally
        NetsToInvalidate.Free;
        ProcessedObjects.Free;
        ProcessedPoints.Free;
        TraceStack.Free;
        ResultProps.Free;
    end;
end;








function Legacy_ExecuteLayoutDuplicatorApply(RequestData: TStringList): String;
var
    ParamValue: String;
    i: Integer;
    SourceList, DestList: TStringList;
begin
    // For this command, we need to extract the source and destination lists
    SourceList := TStringList.Create;
    DestList := TStringList.Create;

    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for source designators array
            if (Pos('"source_designators"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')

                while (i < RequestData.Count) and (Copy(Trim(RequestData[i]), 1, 1) <> ']') do
                begin
                    // Extract the designator value
                    ParamValue := RequestData[i];
                    ParamValue := Legacy_TrimJSON(ParamValue);

                    if (ParamValue <> '') and (ParamValue <> '[') then
                        SourceList.Add(ParamValue);

                    i := i + 1;
                end;
            end
            // Look for destination designators array
            else if (Pos('"destination_designators"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')

                while (i < RequestData.Count) and (Copy(Trim(RequestData[i]), 1, 1) <> ']') do
                begin
                    // Extract the designator value
                    ParamValue := RequestData[i];
                    ParamValue := Legacy_TrimJSON(ParamValue);

                    if (ParamValue <> '') and (ParamValue <> '[') then
                        DestList.Add(ParamValue);

                    i := i + 1;
                end;
            end
        end;

        if (SourceList.Count > 0) and (DestList.Count > 0) then
        begin
            Result := Legacy_ApplyLayoutDuplicator(SourceList, DestList);
        end
        else
        begin
            AppendLog('Error: Source or destination lists are empty');
            Result := '{"success": false, "error": "Source or destination lists are empty"}';
        end;
    finally
        SourceList.Free;
        DestList.Free;
    end;
end;

// Function to execute get output job containers








function Legacy_GetPCBRules(LegacyBusinessDirectory: String): String;
Var
    Board         : IPCB_Board;
    Rule          : IPCB_Rule;
    BoardIterator : IPCB_BoardIterator;
    RulesArray    : TStringList;
    RuleProps     : TStringList;
    OutputLines   : TStringList;
begin
    // Retrieve the current board
    Board := Legacy_GetBoardSafe(0);
    if (Board = Nil) then
    begin
        Result := '[]';
        Exit;
    end;

    // Create array for rules
    RulesArray := TStringList.Create;

    try
        // Retrieve the iterator
        BoardIterator := Board.BoardIterator_Create;
        try
            BoardIterator.AddFilter_ObjectSet(MkSet(eRuleObject));
            BoardIterator.AddFilter_LayerSet(AllLayers);
            BoardIterator.AddFilter_Method(eProcessAll);

            // Process each rule
            Rule := BoardIterator.FirstPCBObject;
            while (Rule <> Nil) do
            begin
                // Create rule properties
                RuleProps := TStringList.Create;
                try
                    // Add rule descriptor
                    Legacy_AddJSONProperty(RuleProps, 'descriptor', Rule.Descriptor);
                    Legacy_AddJSONProperty(RuleProps, 'rule_kind', Rule.GetState_ShortDescriptorString);
                    Legacy_AddJSONProperty(RuleProps, 'filter1', Rule.Scope1Expression);
                    Legacy_AddJSONProperty(RuleProps, 'filter2', Rule.Scope2Expression);

                    // Add to rules array
                    RulesArray.Add(Legacy_BuildJSONObject(RuleProps, 1));
                finally
                    RuleProps.Free;
                end;

                // Move to next rule
                Rule := BoardIterator.NextPCBObject;
            end;

            // Clean up the iterator
        finally
            Board.BoardIterator_Destroy(BoardIterator);
        end;

        // Build the final JSON array
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := Legacy_BuildJSONArray(RulesArray);
            Result := Legacy_WriteJSONToFile(OutputLines, LegacyBusinessDirectory+'\temp_rules_data.json');
        finally
            OutputLines.Free;
        end;
    finally
        RulesArray.Free;
    end;
end;

// Function to get all component data from the PCB








function Legacy_GetOutputJobContainers(LegacyBusinessDirectory: String): String;
var
    OutJobPath: String;
    IniFile: TIniFile;
    ContainerName, ContainerAction: String;
    G, J: Integer;
    S: String;
    ResultProps: TStringList;
    ContainersArray: TStringList;
    ContainerProps: TStringList;
    OutputLines: TStringList;
begin
    // Get the path of the first open OutJob
    OutJobPath := Legacy_GetOpenOutputJob();

    // Exit if no open OutJob was found
    if OutJobPath = '' then
    begin
        ResultProps := TStringList.Create;
        try
            Legacy_AddJSONBoolean(ResultProps, 'success', False);
            Legacy_AddJSONProperty(ResultProps, 'error', 'No open OutJob document found');
            Result := Legacy_BuildJSONObject(ResultProps);
        finally
            ResultProps.Free;
        end;
        Exit;
    end;

    // Create output containers array
    ResultProps := TStringList.Create;
    ContainersArray := TStringList.Create;

    try
        // Add the OutJob path to the result
        Legacy_AddJSONProperty(ResultProps, 'outjob_path', OutJobPath);

        // Open the OutJob file (it's just an INI file)
        IniFile := TIniFile.Create(OutJobPath);
        try
            G := 1; // Group Number
            J := 1; // Job/Container Number
            ContainerName := '';

            // Iterate each Output Group
            While (G = 1) Or (ContainerName <> LegacyDefault) Do
            Begin
                S := 'OutputGroup'+IntToStr(G); // Section (aka OutputGroup)

                // Reset J for each group
                J := 1;
                ContainerName := '';
                ContainerAction := '';

                // Iterate each Output Container
                While (J = 1) Or (ContainerName <> LegacyDefault) Do
                Begin
                    ContainerName := IniFile.ReadString(S, 'OutputMedium' + IntToStr(J), LegacyDefault);
                    ContainerAction := IniFile.ReadString(S, 'OutputMedium' + IntToStr(J) + '_Type', LegacyDefault);

                    // Add valid containers to the list
                    if (ContainerName <> LegacyDefault) then
                    begin
                        ContainerProps := TStringList.Create;
                        try
                            // Add container properties
                            Legacy_AddJSONProperty(ContainerProps, 'container_name', ContainerName);
                            Legacy_AddJSONProperty(ContainerProps, 'container_type', ContainerAction);
                            //AddJSONProperty(ContainerProps, 'group', IntToStr(G));
                            //AddJSONProperty(ContainerProps, 'container_id', IntToStr(J));

                            // Add to containers array
                            ContainersArray.Add(Legacy_BuildJSONObject(ContainerProps, 1));
                        finally
                            ContainerProps.Free;
                        end;
                    end;

                    Inc(J);
                    // Exit if we've reached the 'Blank' value
                    if ContainerName = 'Blank' then
                        break;
                End;

                Inc(G);
                // Exit if we've reached the default value after first group
                if (G > 1) and (ContainerName = LegacyDefault) then
                    break;
            End;
        finally
            IniFile.Free;
        end;

        // Add success status and containers array to result
        Legacy_AddJSONBoolean(ResultProps, 'success', True);
        ResultProps.Add(Legacy_BuildJSONArray(ContainersArray, 'containers'));

        // Build final JSON
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := Legacy_BuildJSONObject(ResultProps);
            Result := Legacy_WriteJSONToFile(OutputLines, LegacyBusinessDirectory);
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
        ContainersArray.Free;
    end;
end;

// Run selected output job containers with simplified logic








function Legacy_ExecuteGetOutputJobContainers(RequestData: TStringList): String;
var
    ParamValue: String;
    i: Integer;
    OutJobPath: String;
begin
    OutJobPath := '';

    // Parse parameters from the request
    for i := 0 to RequestData.Count - 1 do
    begin
        if (Pos('"outjob_path"', RequestData[i]) > 0) then
        begin
            // Found the outjob_path parameter
            ParamValue := Copy(RequestData[i], Pos(':', RequestData[i]) + 1, Length(RequestData[i]));
            ParamValue := Legacy_TrimJSON(ParamValue);
            OutJobPath := ParamValue;
            break;
        end;
    end;

    // Call the appropriate function
    Result := Legacy_GetOutputJobContainers(WorkspaceDir);
end;

// Function to execute Run output jobs








function Legacy_RunOutputJobs(ContainerNames: TStringList, WorkspaceDir: String): String;
var
    OutJobPath: String;
    IniFile: TIniFile;
    ContainerName, ContainerAction, RelativePath: String;
    G, J: Integer;
    S: String;
    ResultProps: TStringList;
    ContainerResults: TStringList;
    ContainerResultProps: TStringList;
    I: Integer;
    ContainerFound: Boolean;
    SuccessCount: Integer;
    OutJobDoc: IServerDocument;
    OutputLines: TStringList;
begin
    // Get the path of the first open OutJob
    OutJobPath := Legacy_GetOpenOutputJob();

    // Exit if no open OutJob was found
    if OutJobPath = '' then
    begin
        ResultProps := TStringList.Create;
        try
            Legacy_AddJSONBoolean(ResultProps, 'success', False);
            Legacy_AddJSONProperty(ResultProps, 'error', 'No open OutJob document found');
            Result := Legacy_BuildJSONObject(ResultProps);
        finally
            ResultProps.Free;
        end;
        Exit;
    end;

    // Create results
    ResultProps := TStringList.Create;
    ContainerResults := TStringList.Create;
    SuccessCount := 0;

    try
        // Add the OutJob path to the result
        Legacy_AddJSONProperty(ResultProps, 'outjob_path', OutJobPath);

        // Open the OutJob document
        if not(Client.IsDocumentOpen(OutJobPath)) then
        begin
            OutJobDoc := Client.OpenDocument('OUTPUTJOB', OutJobPath);
            OutJobDoc.Focus();
        end
        else
        begin
            OutJobDoc := Client.GetDocumentByPath(OutJobPath);
            OutJobDoc.Focus();
        end;

        // Exit if we can't open the OutJob document
        if OutJobDoc = Nil then
        begin
            Legacy_AddJSONBoolean(ResultProps, 'success', False);
            Legacy_AddJSONProperty(ResultProps, 'error', 'Could not open OutJob document: ' + OutJobPath);
            Result := Legacy_BuildJSONObject(ResultProps);
            Exit;
        end;

        // Open the OutJob file (it's just an INI file)
        IniFile := TIniFile.Create(OutJobPath);
        try
            // Process each requested container
            for I := 0 to ContainerNames.Count - 1 do
            begin
                ContainerFound := False;
                ContainerResultProps := TStringList.Create;

                try
                    // Add container name to results
                    Legacy_AddJSONProperty(ContainerResultProps, 'container_name', ContainerNames[I]);

                    // Iterate through groups and containers to find the matching one
                    G := 1;
                    while True do
                    begin
                        S := 'OutputGroup'+IntToStr(G);

                        J := 1;
                        while True do
                        begin
                            ContainerName := IniFile.ReadString(S, 'OutputMedium' + IntToStr(J), 'Blank');

                            // Exit inner loop if we've reached the default value
                            if ContainerName = LegacyDefault then
                                break;

                            // Check if this is one of the containers we want to Run
                            if ContainerName = ContainerNames[I] then
                            begin
                                ContainerFound := True;
                                ContainerAction := IniFile.ReadString(S, 'OutputMedium' + IntToStr(J) + '_Type', LegacyDefault);
                                RelativePath := IniFile.ReadString('PublishSettings', 'OutputBasePath' + IntToStr(J), '');

                                // Ensure document is focused
                                OutJobDoc.Focus();

                                // Run the container based on its type
                                if ContainerAction = 'GeneratedFiles' then
                                begin
                                    // Run GenerateFiles
                                    ResetParameters;
                                    AddStringParameter('Action', 'Run');
                                    AddStringParameter('OutputMedium', ContainerName);
                                    AddStringParameter('ObjectKind', 'OutputBatch');
                                    AddStringParameter('OutputBasePath', RelativePath);
                                    RunProcess('WorkspaceManager:GenerateReport');

                                    // Assume success
                                    Legacy_AddJSONBoolean(ContainerResultProps, 'success', True);
                                    SuccessCount := SuccessCount + 1;
                                end
                                else if ContainerAction = 'Publish' then
                                begin
                                    // Run PublishToPDF with simpler parameters
                                    ResetParameters;
                                    AddStringParameter('Action', 'PublishToPDF');
                                    AddStringParameter('OutputMedium', ContainerName);
                                    AddStringParameter('ObjectKind', 'OutputBatch');
                                    AddStringParameter('OutputBasePath', RelativePath);
                                    AddStringParameter('DisableDialog', 'True');
                                    RunProcess('WorkspaceManager:Print');

                                    // Assume success
                                    Legacy_AddJSONBoolean(ContainerResultProps, 'success', True);
                                    SuccessCount := SuccessCount + 1;
                                end
                                else
                                begin
                                    // Unknown action type
                                    Legacy_AddJSONBoolean(ContainerResultProps, 'success', False);
                                    Legacy_AddJSONProperty(ContainerResultProps, 'error', 'Unknown container action type: ' + ContainerAction);
                                end;

                                // Add output path info
                                Legacy_AddJSONProperty(ContainerResultProps, 'relative_path', RelativePath);

                                // Break out after processing the container
                                break;
                            end;

                            Inc(J);
                        end;

                        // If we already found and processed the container, break out
                        if ContainerFound then
                            break;

                        // Exit outer loop if we've processed all groups
                        if ContainerName = 'Blank' then
                            break;

                        Inc(G);
                    end;

                    // Handle container not found
                    if not ContainerFound then
                    begin
                        Legacy_AddJSONBoolean(ContainerResultProps, 'success', False);
                        Legacy_AddJSONProperty(ContainerResultProps, 'error', 'Container not found: ' + ContainerNames[I]);
                    end;

                    // Add this container result to the results array
                    ContainerResults.Add(Legacy_BuildJSONObject(ContainerResultProps, 1));
                finally
                    ContainerResultProps.Free;
                end;
            end;
        finally
            IniFile.Free;
        end;

        // Add summary results
        Legacy_AddJSONBoolean(ResultProps, 'success', SuccessCount > 0);
        Legacy_AddJSONInteger(ResultProps, 'total_containers', ContainerNames.Count);
        Legacy_AddJSONInteger(ResultProps, 'successful_containers', SuccessCount);
        ResultProps.Add(Legacy_BuildJSONArray(ContainerResults, 'container_results'));

        // Build and return the final JSON result
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := Legacy_BuildJSONObject(ResultProps);
            Result := Legacy_WriteJSONToFile(OutputLines, WorkspaceDir);
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
        ContainerResults.Free;
    end;
end;

// Helper function to check if a document is open








function Legacy_ExecuteRunOutputJobs(RequestData: TStringList): String;
var
    ParamValue: String;
    i: Integer;
    ContainersList: TStringList;
begin
    ContainersList := TStringList.Create;

    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            if (Pos('"container_names"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')

                while (i < RequestData.Count) and (Copy(Trim(RequestData[i]), 1, 1) <> ']') do
                begin
                    // Extract the container name
                    ParamValue := RequestData[i];
                    ParamValue := Legacy_TrimJSON(ParamValue);

                    if (ParamValue <> '') and (ParamValue <> '[') then
                        ContainersList.Add(ParamValue);

                    i := i + 1;
                end;
            end;
        end;

        if ContainersList.Count > 0 then
        begin
            Result := Legacy_RunOutputJobs(ContainersList, WorkspaceDir);
        end
        else
        begin
            AppendLog('Error: No container names specified');
            Result := '{"success": false, "error": "No container names specified"}';
        end;
    finally
        ContainersList.Free;
    end;
end;

// Extract the create PCB footprint logic








function Legacy_SearchLibrarySymbol(LegacyBusinessDirectory: String; LibraryPath: String; SymbolName: String): String;
var
    CurrentLib       : ISch_Lib;
    LibIterator      : ISch_Iterator;
    LibComp          : ISch_Component;
    MatchedComp      : ISch_Component;
    ResultProps      : TStringList;
    MatchesArray     : TStringList;
    AllSymbolsArray  : TStringList;
    MatchProps       : TStringList;
    OutputLines      : TStringList;
    SearchUpper      : String;
    LibRefUpper      : String;
    MatchCount       : Integer;
    ServerDoc        : IServerDocument;
    OpenDlg          : TOpenDialog;
    NeedToOpen       : Boolean;
begin
    Result := '';
    MatchedComp := Nil;
    MatchCount := 0;
    SearchUpper := UpperCase(SymbolName);
    NeedToOpen := False;

    // If a library path is provided, open it
    if (LibraryPath <> '') then
    begin
        NeedToOpen := True;
    end
    else
    begin
        // No path provided - check if a SchLib is already open
        if (SchServer <> Nil) then
        begin
            CurrentLib := SchServer.GetCurrentSchDocument;
            if (CurrentLib <> Nil) and (CurrentLib.ObjectID = eSchLib) then
                NeedToOpen := False  // Already have a SchLib open
            else
                NeedToOpen := True;  // No SchLib open, need to browse
        end
        else
            NeedToOpen := True;
    end;

    // If we need to open a library and no path was given, prompt the user
    if NeedToOpen and (LibraryPath = '') then
    begin
        OpenDlg := TOpenDialog.Create(nil);
        try
            OpenDlg.Title := 'Select Schematic Library (.SchLib)';
            OpenDlg.Filter := 'Schematic Library (*.SchLib)|*.SchLib|All Files (*.*)|*.*';
            OpenDlg.FilterIndex := 1;
            if OpenDlg.Execute then
                LibraryPath := OpenDlg.FileName
            else
            begin
                Result := 'ERROR: No library selected. User cancelled the file browser.';
                Exit;
            end;
        finally
            OpenDlg.Free;
        end;
    end;

    // Open the library if we have a path
    if (LibraryPath <> '') then
    begin
        // Check if the file exists
        if not FileExists(LibraryPath) then
        begin
            Result := 'ERROR: Library file not found: ' + LibraryPath;
            Exit;
        end;

        // Open the library document. If it is already open, only focus it -
        // re-opening reloads from disk and silently discards unsaved changes.
        if Client.IsDocumentOpen(LibraryPath) then
            ServerDoc := Client.GetDocumentByPath(LibraryPath)
        else
            ServerDoc := Client.OpenDocument('SchLib', LibraryPath);
        if ServerDoc = Nil then
        begin
            Result := 'ERROR: Failed to open library: ' + LibraryPath;
            Exit;
        end;
        Client.ShowDocument(ServerDoc);
        Sleep(500); // Give Altium time to focus the document
    end;

    // Get the current schematic library document
    CurrentLib := SchServer.GetCurrentSchDocument;
    if CurrentLib = Nil then
    begin
        Result := 'ERROR: No schematic library document is currently open';
        Exit;
    end;

    if (CurrentLib.ObjectID <> eSchLib) then
    begin
        Result := 'ERROR: Current document is not a schematic library. Please open a .SchLib file';
        Exit;
    end;

    // Create arrays for results
    MatchesArray := TStringList.Create;
    AllSymbolsArray := TStringList.Create;
    ResultProps := TStringList.Create;

    try
        // Create library iterator to enumerate all symbols
        // NOTE: Must use SchLibIterator_Create (not SchIterator_Create) for SchLib documents
        LibIterator := CurrentLib.SchLibIterator_Create;
        try
            LibIterator.AddFilter_ObjectSet(MkSet(eSchComponent));

            LibComp := LibIterator.FirstSchObject;
            while (LibComp <> Nil) do
            begin
                LibRefUpper := UpperCase(LibComp.LibReference);

                // Add to all symbols list
                AllSymbolsArray.Add('"' + LibComp.LibReference + '"');

                // Check for partial match
                if (Pos(SearchUpper, LibRefUpper) > 0) then
                begin
                    MatchCount := MatchCount + 1;

                    // Record this match
                    MatchProps := TStringList.Create;
                    try
                        Legacy_AddJSONProperty(MatchProps, 'name', LibComp.LibReference);
                        Legacy_AddJSONProperty(MatchProps, 'description', LibComp.ComponentDescription);

                        // Check for exact match
                        if (LibRefUpper = SearchUpper) then
                            Legacy_AddJSONBoolean(MatchProps, 'exact_match', True)
                        else
                            Legacy_AddJSONBoolean(MatchProps, 'exact_match', False);

                        MatchesArray.Add(Legacy_BuildJSONObject(MatchProps, 1));
                    finally
                        MatchProps.Free;
                    end;

                    // Prefer exact match, otherwise use first partial match
                    if (LibRefUpper = SearchUpper) then
                        MatchedComp := LibComp
                    else if (MatchedComp = Nil) then
                        MatchedComp := LibComp;
                end;

                LibComp := LibIterator.NextSchObject;
            end;
        finally
            CurrentLib.SchIterator_Destroy(LibIterator);
        end;

        // Navigate to the matched component if found
        if (MatchedComp <> Nil) then
        begin
            CurrentLib.CurrentSchComponent := MatchedComp;
            CurrentLib.GraphicallyInvalidate;

            Legacy_AddJSONBoolean(ResultProps, 'found', True);
            Legacy_AddJSONProperty(ResultProps, 'navigated_to', MatchedComp.LibReference);
            Legacy_AddJSONProperty(ResultProps, 'description', MatchedComp.ComponentDescription);
        end
        else
        begin
            Legacy_AddJSONBoolean(ResultProps, 'found', False);
            Legacy_AddJSONProperty(ResultProps, 'message', 'No symbol matching "' + SymbolName + '" was found');
        end;

        Legacy_AddJSONInteger(ResultProps, 'match_count', MatchCount);
        Legacy_AddJSONProperty(ResultProps, 'library_name', ExtractFileName(CurrentLib.DocumentName));
        Legacy_AddJSONInteger(ResultProps, 'total_symbols', AllSymbolsArray.Count);
        ResultProps.Add('"matches": ' + Legacy_BuildJSONArray(MatchesArray));

        // Build final JSON
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := Legacy_BuildJSONObject(ResultProps);
            Result := Legacy_WriteJSONToFile(OutputLines, LegacyBusinessDirectory + 'temp_search_symbol.json');
        finally
            OutputLines.Free;
        end;
    finally
        MatchesArray.Free;
        AllSymbolsArray.Free;
        ResultProps.Free;
    end;
end;

// Create one symbol for CreateSymbolsBatch. Returns True when created.








function Legacy_ExecuteSearchLibrarySymbol(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    LibraryPath: String;
    SymbolName: String;
begin
    LibraryPath := '';
    SymbolName := '';

    // Parse parameters from the request
    for i := 0 to RequestData.Count - 1 do
    begin
        // Look for library_path
        if (Pos('"library_path"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            ParamValue := Legacy_TrimJSON(ParamValue);
            LibraryPath := ParamValue;
        end
        // Look for symbol_name
        else if (Pos('"symbol_name"', RequestData[i]) > 0) then
        begin
            ValueStart := Pos(':', RequestData[i]) + 1;
            ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
            ParamValue := Legacy_TrimJSON(ParamValue);
            SymbolName := ParamValue;
        end;
    end;

    if SymbolName <> '' then
    begin
        Result := Legacy_SearchLibrarySymbol(WorkspaceDir, LibraryPath, SymbolName);
    end
    else
    begin
        Result := 'ERROR: No symbol name provided for search_library_symbol';
    end;
end;

// Function to execute a command with parameters








function Legacy_CreatePCBFootprint(FootprintName: String; Description: String; PadsList: TStringList; CourtyardXMM: Double; CourtyardYMM: Double): String;
var
    PcbLib      : IPCB_Library;
    LibComp     : IPCB_Component;
    Pad         : IPCB_Pad;
    Track       : IPCB_Track;
    ResultProps : TStringList;
    OutputLines : TStringList;
    i, j        : Integer;
    PadData     : String;
    PadNum      : String;
    XMM, YMM    : Double;
    WMM, HMM    : Double;
    ShapeStr    : String;
    PadShape    : TShape;
    PadCount    : Integer;
    MaxX, MaxY  : Double;
    MinX, MinY  : Double;
    CrtX1, CrtY1, CrtX2, CrtY2 : Double;
    TrackWidth  : TCoord;
    FieldStart  : Integer;
    Fields      : TStringList;
    SilkLayer   : TLayer;
begin
    PcbLib := Legacy_GetPcbLibSafe(0);
    if PcbLib = nil then
    begin
        Result := '{"success": false, "error": "No PCB library document is currently active. Open a .PcbLib file first."}';
        Exit;
    end;

    ResultProps := TStringList.Create;
    Fields := TStringList.Create;
    PadCount := 0;
    MaxX := -1e9; MaxY := -1e9;
    MinX :=  1e9; MinY :=  1e9;
    SilkLayer := String2Layer('Top Overlay');

    try
        LibComp := PCBServer.CreatePCBLibComp;
        LibComp.Name := FootprintName;

        PcbLib.RegisterComponent(LibComp);

        for i := 0 to PadsList.Count - 1 do
        begin
            PadData := Trim(PadsList[i]);
            if (PadData = '') then continue;

            // Parse pipe-delimited fields manually
            Fields.Text := '';
            FieldStart := 1;
            for j := 1 to Length(PadData) + 1 do
            begin
                if (j > Length(PadData)) or (PadData[j] = '|') then
                begin
                    Fields.Add(Trim(Copy(PadData, FieldStart, j - FieldStart)));
                    FieldStart := j + 1;
                end;
            end;

            if Fields.Count < 5 then continue;

            PadNum := Fields[0];
            XMM := Legacy_SafeStrToFloat(Fields[1]);
            YMM := Legacy_SafeStrToFloat(Fields[2]);
            WMM := Legacy_SafeStrToFloat(Fields[3]);
            HMM := Legacy_SafeStrToFloat(Fields[4]);

            if Fields.Count >= 6 then
                ShapeStr := Fields[5]
            else
                ShapeStr := 'Rect';

            if ShapeStr = 'Round' then
                PadShape := eRounded
            else if ShapeStr = 'Oval' then
                PadShape := eRoundedRectangle
            else
                PadShape := eRectangular;

            Pad := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
            Pad.Name := PadNum;
            Pad.Mode := ePadMode_Simple;
            Pad.HoleSize := 0;
            Pad.x := MMsToCoord(XMM);
            Pad.y := MMsToCoord(YMM);
            Pad.Layer := eTopLayer;
            Pad.TopXSize := MMsToCoord(WMM);
            Pad.TopYSize := MMsToCoord(HMM);
            Pad.TopShape := PadShape;

            LibComp.AddPCBObject(Pad);
            PCBServer.SendMessageToRobots(Pad.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

            if (XMM - WMM/2) < MinX then MinX := XMM - WMM/2;
            if (YMM - HMM/2) < MinY then MinY := YMM - HMM/2;
            if (XMM + WMM/2) > MaxX then MaxX := XMM + WMM/2;
            if (YMM + HMM/2) > MaxY then MaxY := YMM + HMM/2;

            PadCount := PadCount + 1;
        end;

        // Compute courtyard extents
        if (CourtyardXMM > 0) and (CourtyardYMM > 0) then
        begin
            CrtX1 := -CourtyardXMM; CrtX2 :=  CourtyardXMM;
            CrtY1 := -CourtyardYMM; CrtY2 :=  CourtyardYMM;
        end
        else
        begin
            CrtX1 := MinX - 0.25; CrtX2 := MaxX + 0.25;
            CrtY1 := MinY - 0.25; CrtY2 := MaxY + 0.25;
        end;

        TrackWidth := MMsToCoord(0.1);

        // Courtyard on Mechanical 15
        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := ILayer.MechanicalLayer(15);
        Track.x1 := MMsToCoord(CrtX1); Track.y1 := MMsToCoord(CrtY1);
        Track.x2 := MMsToCoord(CrtX2); Track.y2 := MMsToCoord(CrtY1);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := ILayer.MechanicalLayer(15);
        Track.x1 := MMsToCoord(CrtX1); Track.y1 := MMsToCoord(CrtY2);
        Track.x2 := MMsToCoord(CrtX2); Track.y2 := MMsToCoord(CrtY2);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := ILayer.MechanicalLayer(15);
        Track.x1 := MMsToCoord(CrtX1); Track.y1 := MMsToCoord(CrtY1);
        Track.x2 := MMsToCoord(CrtX1); Track.y2 := MMsToCoord(CrtY2);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := ILayer.MechanicalLayer(15);
        Track.x1 := MMsToCoord(CrtX2); Track.y1 := MMsToCoord(CrtY1);
        Track.x2 := MMsToCoord(CrtX2); Track.y2 := MMsToCoord(CrtY2);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        // Silkscreen on TopOverlay (inset 0.1mm from courtyard)
        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := SilkLayer;
        Track.x1 := MMsToCoord(CrtX1+0.1); Track.y1 := MMsToCoord(CrtY1+0.1);
        Track.x2 := MMsToCoord(CrtX2-0.1); Track.y2 := MMsToCoord(CrtY1+0.1);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := SilkLayer;
        Track.x1 := MMsToCoord(CrtX1+0.1); Track.y1 := MMsToCoord(CrtY2-0.1);
        Track.x2 := MMsToCoord(CrtX2-0.1); Track.y2 := MMsToCoord(CrtY2-0.1);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        // Left silk — split to mark pin 1 (gap at top-left corner for pin 1 indicator)
        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := SilkLayer;
        Track.x1 := MMsToCoord(CrtX1+0.1); Track.y1 := MMsToCoord(CrtY1+0.1);
        Track.x2 := MMsToCoord(CrtX1+0.1); Track.y2 := MMsToCoord(CrtY2-0.6);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        // Right silk
        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.Layer := SilkLayer;
        Track.x1 := MMsToCoord(CrtX2-0.1); Track.y1 := MMsToCoord(CrtY1+0.1);
        Track.x2 := MMsToCoord(CrtX2-0.1); Track.y2 := MMsToCoord(CrtY2-0.1);
        Track.Width := TrackWidth;
        LibComp.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Track.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, c_NoEventData);

        // Register with library board, navigate, and refresh
        PCBServer.SendMessageToRobots(PcbLib.Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, LibComp.I_ObjectAddress);
        PcbLib.CurrentComponent := LibComp;
        PcbLib.Board.ViewManager_FullUpdate;

        Legacy_AddJSONBoolean(ResultProps, 'success', True);
        Legacy_AddJSONProperty(ResultProps, 'footprint_name', FootprintName);
        Legacy_AddJSONInteger(ResultProps, 'pad_count', PadCount);
        Legacy_AddJSONNumber(ResultProps, 'courtyard_width_mm', CrtX2 - CrtX1);
        Legacy_AddJSONNumber(ResultProps, 'courtyard_height_mm', CrtY2 - CrtY1);

        OutputLines := TStringList.Create;
        try
            OutputLines.Text := Legacy_BuildJSONObject(ResultProps);
            Result := OutputLines.Text;
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
        Fields.Free;
    end;
end;

// Function to move components by X and Y offsets and set rotation








function Legacy_ExecuteCreatePCBFootprint(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    FootprintName: String;
    Description: String;
    PadsList: TStringList;
    CourtyardXMM: Double;
    CourtyardYMM: Double;
begin
    FootprintName := '';
    Description := '';
    CourtyardXMM := 0;
    CourtyardYMM := 0;
    PadsList := TStringList.Create;

    try
        for i := 0 to RequestData.Count - 1 do
        begin
            if (Pos('"footprint_name"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                FootprintName := Legacy_TrimJSON(Copy(RequestData[i], ValueStart, Length(RequestData[i])));
            end
            else if (Pos('"description"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                Description := Legacy_TrimJSON(Copy(RequestData[i], ValueStart, Length(RequestData[i])));
            end
            else if (Pos('"courtyard_x_mm"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Legacy_TrimJSON(Copy(RequestData[i], ValueStart, Length(RequestData[i])));
                CourtyardXMM := Legacy_SafeStrToFloat(ParamValue);
            end
            else if (Pos('"courtyard_y_mm"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Legacy_TrimJSON(Copy(RequestData[i], ValueStart, Length(RequestData[i])));
                CourtyardYMM := Legacy_SafeStrToFloat(ParamValue);
            end
            else if (Pos('"pads"', RequestData[i]) > 0) then
            begin
                i := i + 1;
                while (i < RequestData.Count) and (Copy(Trim(RequestData[i]), 1, 1) <> ']') do
                begin
                    ParamValue := RequestData[i];
                    ParamValue := Legacy_TrimJSON(ParamValue);
                    if (ParamValue <> '') and (ParamValue <> '[') then
                        PadsList.Add(ParamValue);
                    i := i + 1;
                end;
            end;
        end;

        if FootprintName <> '' then
            Result := Legacy_CreatePCBFootprint(FootprintName, Description, PadsList, CourtyardXMM, CourtyardYMM)
        else
            Result := '{"success": false, "error": "No footprint name provided"}';
    finally
        PadsList.Free;
    end;
end;

// Extract the search library symbol logic








function Legacy_ZoomToComponents(DesignatorsList: TStringList): Integer;
var
    Board      : IPCB_Board;
    Component  : IPCB_Component;
    Rect       : TCoordRect;
    i          : Integer;
    HaveBounds : Boolean;
    MinX, MinY, MaxX, MaxY : TCoord;
    Margin     : TCoord;
begin
    Result := 0;
    HaveBounds := False;

    Board := Legacy_GetBoardSafe(0);
    if (Board = nil) then Exit;

    for i := 0 to DesignatorsList.Count - 1 do
    begin
        Component := Board.GetPcbComponentByRefDes(Trim(DesignatorsList[i]));
        if (Component <> nil) then
        begin
            Rect := Component.BoundingRectangleNoNameComment;
            if not HaveBounds then
            begin
                MinX := Rect.Left;
                MaxX := Rect.Right;
                MinY := Rect.Bottom;
                MaxY := Rect.Top;
                HaveBounds := True;
            end
            else
            begin
                if Rect.Left < MinX then MinX := Rect.Left;
                if Rect.Right > MaxX then MaxX := Rect.Right;
                if Rect.Bottom < MinY then MinY := Rect.Bottom;
                if Rect.Top > MaxY then MaxY := Rect.Top;
            end;
            Result := Result + 1;
        end;
    end;

    if HaveBounds then
    begin
        // 10% margin on the larger span, at least 100 mils
        Margin := (MaxX - MinX);
        if (MaxY - MinY) > Margin then Margin := (MaxY - MinY);
        Margin := Margin div 10;
        if Margin < MilsToCoord(100) then Margin := MilsToCoord(100);

        Board.GraphicalView_ZoomOnRect(MinX - Margin, MinY - Margin, MaxX + Margin, MaxY + Margin);
        Board.GraphicalView_ZoomRedraw;
    end;
end;

// Add a screenshot function that supports both PCB and SCH views.
// If DesignatorsList is non-empty (PCB view only), the view is zoomed to
// those components before the server captures the window.








function Legacy_TakeViewScreenshot(ViewType: String; DesignatorsList: TStringList): String;
var
    Board          : IPCB_Board;
    SchDoc         : ISch_Document;
    ResultProps    : TStringList;
    OutputLines    : TStringList;
    ClassName      : String;
    DocType        : String;
    WindowFound    : Boolean;
    ZoomedCount    : Integer;

    // For screenshot thread
    ThreadStarted  : Boolean;
    ScreenshotResult : String;
begin
    // 'Blank' result
    Result := '{"success": false, "error": "Failed to initialize screenshot capture"}';

    // Determine what type of document we need to focus
    if LowerCase(ViewType) = 'pcb' then
    begin
        DocType := 'PCB';
        ClassName := 'View_Graphical';
    end
    else if LowerCase(ViewType) = 'sch' then
    begin
        DocType := 'SCH';
        ClassName := 'SchView';
    end
    else
    begin
        Result := '{"success": false, "error": "Invalid view type: ' + ViewType + '. Must be ''pcb'' or ''sch''"}';
        Exit;
    end;

    // Optionally zoom to the requested components before capture
    ZoomedCount := 0;
    if (DocType = 'PCB') and (DesignatorsList <> nil) and (DesignatorsList.Count > 0) then
        ZoomedCount := Legacy_ZoomToComponents(DesignatorsList);

    // Build the command to call the external screenshot utility
    // This part depends on how your C# server calls Altium for screenshots

    // Create result JSON
    ResultProps := TStringList.Create;
    try
        // Add successful result properties
        Legacy_AddJSONBoolean(ResultProps, 'success', True);
        Legacy_AddJSONProperty(ResultProps, 'view_type', ViewType);
        Legacy_AddJSONProperty(ResultProps, 'class_filter', ClassName);
        Legacy_AddJSONBoolean(ResultProps, 'window_found', WindowFound);
        Legacy_AddJSONInteger(ResultProps, 'zoomed_component_count', ZoomedCount);

        // Add signal to the server that it can now capture the screenshot
        Legacy_AddJSONBoolean(ResultProps, 'ready_for_capture', True);

        // Build final JSON
        OutputLines := TStringList.Create;
        try
            OutputLines.Text := Legacy_BuildJSONObject(ResultProps);
            Result := OutputLines.Text;
        finally
            OutputLines.Free;
        end;
    finally
        ResultProps.Free;
    end;
end;

// Get all available output job containers from the first open OutJob








function Legacy_ExecuteTakeViewScreenshot(RequestData: TStringList): String;
var
    ParamValue: String;
    i, ValueStart: Integer;
    ViewType: String;
    DesignatorsList: TStringList;
begin
    // Extract the view type parameter
    ViewType := 'pcb';  // 'Blank' to PCB
    DesignatorsList := TStringList.Create;

    try
        // Parse parameters from the request
        for i := 0 to RequestData.Count - 1 do
        begin
            // Look for view_type parameter
            if (Pos('"view_type"', RequestData[i]) > 0) then
            begin
                ValueStart := Pos(':', RequestData[i]) + 1;
                ParamValue := Copy(RequestData[i], ValueStart, Length(RequestData[i]) - ValueStart + 1);
                ParamValue := Legacy_TrimJSON(ParamValue);
                ViewType := ParamValue;
            end
            // Look for optional designators array to zoom to before capture
            else if (Pos('"designators"', RequestData[i]) > 0) then
            begin
                // Parse the array in the next lines
                i := i + 1; // Move to the next line (should be '[')

                while (i < RequestData.Count) and (Copy(Trim(RequestData[i]), 1, 1) <> ']') do
                begin
                    ParamValue := RequestData[i];
                    ParamValue := Legacy_TrimJSON(ParamValue);

                    if (ParamValue <> '') and (ParamValue <> '[') then
                        DesignatorsList.Add(ParamValue);

                    i := i + 1;
                end;
            end;
        end;

        Result := Legacy_TakeViewScreenshot(ViewType, DesignatorsList);
    finally
        DesignatorsList.Free;
    end;
end;

// Extract the create schematic symbol logic








Function HandleLegacyCommand(Action, ParamsText, RequestId : String) : String;
Var RequestData : TStringList; Data, ViewHint : String;
Begin
    RequestData := TStringList.Create;
    Try
        RequestData.Text := ExtractJsonValue(ParamsText, 'pretty_params');
        ParamsText := ExtractJsonValue(ParamsText, 'arguments');
        ViewHint := ExtractJsonValue(ParamsText, 'view_type');
        Legacy_EnsureDocumentFocused(Action, ViewHint);
        Data := '';
        Case Action Of
            'get_component_pins': Data := Legacy_ExecuteGetComponentPins(RequestData);
            'get_library_symbol_reference': Data := Legacy_GetLibrarySymbolReference(WorkspaceDir);
            'create_schematic_symbol': Data := Legacy_ExecuteCreateSchematicSymbol(RequestData);
            'get_schematic_data': Data := Legacy_GetSchematicData(WorkspaceDir);
            'get_pcb_layers': Data := Legacy_GetPCBLayers(WorkspaceDir);
            'set_pcb_layer_visibility': Data := Legacy_ExecuteSetPCBLayerVisibility(RequestData);
            'get_pcb_layer_stackup': Data := Legacy_GetPCBLayerStackup(WorkspaceDir);
            'check_placement': Data := Legacy_ExecuteCheckPlacement(RequestData);
            'get_net_connections': Data := Legacy_ExecuteGetNetConnections(RequestData);
            'get_symbol_primitives': Data := Legacy_ExecuteGetSymbolPrimitives(RequestData);
            'create_symbols_batch': Data := Legacy_ExecuteCreateSymbolsBatch(RequestData);
            'build_circuit': Data := Legacy_BuildCircuitFromSpec(ExtractJsonValue(ParamsText, 'spec_file'), WorkspaceDir + 'param_placement.txt', ExtractJsonValue(ParamsText, 'pin_map_file'));
            'get_footprint_primitives': Data := Legacy_ExecuteGetFootprintPrimitives(RequestData);
            'create_footprints_batch': Data := Legacy_ExecuteCreateFootprintsBatch(RequestData);
            'layout_duplicator': Data := Legacy_GetLayoutDuplicatorComponents(True);
            'layout_duplicator_apply': Data := Legacy_ExecuteLayoutDuplicatorApply(RequestData);
            'get_pcb_rules': Data := Legacy_GetPCBRules(WorkspaceDir);
            'get_output_job_containers': Data := Legacy_ExecuteGetOutputJobContainers(RequestData);
            'run_output_jobs': Data := Legacy_ExecuteRunOutputJobs(RequestData);
            'search_library_symbol': Data := Legacy_ExecuteSearchLibrarySymbol(RequestData);
            'create_pcb_footprint': Data := Legacy_ExecuteCreatePCBFootprint(RequestData);
            'take_view_screenshot': Data := Legacy_ExecuteTakeViewScreenshot(RequestData);
        Else
            Begin
                Result := BuildErrorResponse(RequestId, 'UNKNOWN_LEGACY_COMMAND', Action);
                Exit;
            End;
        End;
        If Copy(Data, 1, 6) = 'ERROR:' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'LEGACY_NATIVE_ERROR', Copy(Data, 8, Length(Data)));
            Exit;
        End;
        If Data = '' Then
            Result := BuildErrorResponse(RequestId, 'LEGACY_EMPTY_RESULT', 'Native handler returned no result')
        Else
            Result := BuildSuccessResponse(RequestId, Data);
    Finally RequestData.Free; End;
End;
