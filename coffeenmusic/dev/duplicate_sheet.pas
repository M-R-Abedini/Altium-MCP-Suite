// Duplicate a schematic sheet - v4.
//
// v3 tried to clear the new sheet's 27 default document parameters first, but
// Altium refuses: "System parameters which cannot be removed: CurrentTime".
// So parameters get matched BY NAME and updated in place; only names the
// destination lacks are replicated.  Everything else the sheet owns is copied
// verbatim.
//
// Ownership rule: SchIterator on a document RECURSES, so ObjectId alone is not
// enough - Container.ObjectId = 32 means "owned by the sheet itself".
// Component primitives (container 26) ride along with their parent's
// Replicate; template border/title-block primitives (container 42) ride along
// with the eTemplate object (id 42), which the sheet does own.

// Source sheet to duplicate, matched against the full path of every open
// document. Set this before running.
S2 := 'Sheet1.SchDoc';

// Scan every open project, not just the focused one: a sheet created by
// DM_CreateNewDocument lands in "Free Documents" and takes project focus.
S1 := '';
for I2 := 0 to GetWorkspace.DM_ProjectCount - 1 do
begin
    Obj1 := GetWorkspace.DM_Projects(I2);
    for I1 := 0 to Obj1.DM_LogicalDocumentCount - 1 do
    begin
        Obj2 := Obj1.DM_LogicalDocuments(I1);
        if (Pos(S2, Obj2.DM_FullPath) > 0) then
            S1 := Obj2.DM_FullPath;
    end;
end;

Obj1 := nil;
if (S1 <> '') then Obj1 := SchServer.GetSchDocumentByPath(S1);

if (Obj1 = nil) then
begin
    ResultText := '{"error": "source not found"}';
    SandboxLog('ABORT: source nil');
end
else
begin
    List1 := TStringList.Create;
    SandboxLog('source: ' + Obj1.DocumentName);

    I1 := 0;
    Obj2 := Obj1.SchIterator_Create;
    Obj3 := Obj2.FirstSchObject;
    while (Obj3 <> nil) do
    begin
        Obj5 := Obj3.Container;
        I3 := -1;
        if (Obj5 <> nil) then I3 := Obj5.ObjectId;
        if (I3 = 32) then
        begin
            I1 := I1 + 1;
            List1.Add('SRC|' + IntToStr(Obj3.ObjectId));
        end;
        Obj3 := Obj2.NextSchObject;
    end;
    Obj1.SchIterator_Destroy(Obj2);
    SandboxLog('sheet-owned source objects = ' + IntToStr(I1));

    GetWorkSpace.DM_CreateNewDocument('SCH');
    Obj4 := SchServer.GetCurrentSchDocument;
    SandboxLog('destination: ' + Obj4.DocumentName + ' objectID=' + IntToStr(Obj4.ObjectID));

    if (Obj4.ObjectID <> 32) then
    begin
        ResultText := '{"error": "destination is not a schematic"}';
        SandboxLog('ABORT: destination is not a schematic');
    end
    else
    begin
        SchServer.ProcessControl.PreProcess(Obj4, '');

        // ---- sheet settings BEFORE objects, so coordinates land right ------
        Obj4.SheetStyle           := Obj1.SheetStyle;
        Obj4.UseCustomSheet       := Obj1.UseCustomSheet;
        Obj4.CustomX              := Obj1.CustomX;
        Obj4.CustomY              := Obj1.CustomY;
        Obj4.CustomXZones         := Obj1.CustomXZones;
        Obj4.CustomYZones         := Obj1.CustomYZones;
        Obj4.CustomMarginWidth    := Obj1.CustomMarginWidth;
        Obj4.SheetMarginWidth     := Obj1.SheetMarginWidth;
        Obj4.SheetZonesX          := Obj1.SheetZonesX;
        Obj4.SheetZonesY          := Obj1.SheetZonesY;
        Obj4.WorkspaceOrientation := Obj1.WorkspaceOrientation;
        Obj4.BorderOn             := Obj1.BorderOn;
        Obj4.TitleBlockOn         := Obj1.TitleBlockOn;
        Obj4.ReferenceZonesOn     := Obj1.ReferenceZonesOn;
        Obj4.ShowTemplateGraphics := Obj1.ShowTemplateGraphics;
        Obj4.SnapGridSize         := Obj1.SnapGridSize;
        Obj4.VisibleGridSize      := Obj1.VisibleGridSize;
        Obj4.HotspotGridSize      := Obj1.HotspotGridSize;
        Obj4.TemplateFileName     := Obj1.TemplateFileName;
        Obj4.SetState_xSizeySize;
        SandboxLog('sheet settings copied');

        // ---- everything except parameters, copied verbatim ------------------
        SandboxLog('replicating sheet-owned objects (excluding parameters)');
        I2 := 0;
        Obj2 := Obj1.SchIterator_Create;
        Obj3 := Obj2.FirstSchObject;
        while (Obj3 <> nil) do
        begin
            Obj5 := Obj3.Container;
            B1 := -1;
            if (Obj5 <> nil) then B1 := Obj5.ObjectId;
            if (B1 = 32) and (Obj3.ObjectId <> eParameter) then
            begin
                Obj5 := Obj3.Replicate;
                if (Obj5 <> nil) then
                begin
                    Obj4.RegisterSchObjectInContainer(Obj5);
                    SchServer.RobotManager.SendMessage(Obj4.I_ObjectAddress, c_BroadCast,
                        SCHM_PrimitiveRegistration, Obj5.I_ObjectAddress);
                    I2 := I2 + 1;
                end;
            end;
            Obj3 := Obj2.NextSchObject;
        end;
        Obj1.SchIterator_Destroy(Obj2);
        SandboxLog('replicated = ' + IntToStr(I2));

        // ---- parameters: update by name, add only what is missing -----------
        SandboxLog('merging document parameters');
        I3 := 0;   // updated in place
        B1 := 0;   // added
        Obj2 := Obj1.SchIterator_Create;
        Obj2.AddFilter_ObjectSet(MkSet(eParameter));
        Obj3 := Obj2.FirstSchObject;
        while (Obj3 <> nil) do
        begin
            Obj5 := Obj3.Container;
            if (Obj5 <> nil) then
                if (Obj5.ObjectId = 32) then
                begin
                    Obj5 := Obj4.GetState_SchParameterByName(Obj3.Name);
                    if (Obj5 <> nil) then
                    begin
                        Obj5.Text := Obj3.Text;
                        I3 := I3 + 1;
                        List1.Add('PARAM_UPDATED|' + Obj3.Name + '|' + Obj3.Text);
                    end
                    else
                    begin
                        Obj5 := Obj3.Replicate;
                        if (Obj5 <> nil) then
                        begin
                            Obj4.RegisterSchObjectInContainer(Obj5);
                            SchServer.RobotManager.SendMessage(Obj4.I_ObjectAddress, c_BroadCast,
                                SCHM_PrimitiveRegistration, Obj5.I_ObjectAddress);
                            B1 := B1 + 1;
                            List1.Add('PARAM_ADDED|' + Obj3.Name + '|' + Obj3.Text);
                        end;
                    end;
                end;
            Obj3 := Obj2.NextSchObject;
        end;
        Obj1.SchIterator_Destroy(Obj2);
        SandboxLog('parameters updated = ' + IntToStr(I3) + '  added = ' + IntToStr(B1));

        SchServer.ProcessControl.PostProcess(Obj4, '');
        Obj4.UpdateDocumentProperties;
        Obj4.GraphicallyInvalidate;

        // ---- verify ---------------------------------------------------------
        I1 := 0;
        Obj2 := Obj4.SchIterator_Create;
        Obj3 := Obj2.FirstSchObject;
        while (Obj3 <> nil) do
        begin
            Obj5 := Obj3.Container;
            if (Obj5 <> nil) then
                if (Obj5.ObjectId = 32) then
                begin
                    I1 := I1 + 1;
                    List1.Add('DST|' + IntToStr(Obj3.ObjectId));
                end;
            Obj3 := Obj2.NextSchObject;
        end;
        Obj4.SchIterator_Destroy(Obj2);
        SandboxLog('sheet-owned destination objects = ' + IntToStr(I1));

        List1.SaveToFile('C:\Users\Public\altium_mcp\dup_tally.txt');

        ResultText := '{"source": "' + Obj1.DocumentName + '", "destination": "' + Obj4.DocumentName +
                      '", "replicated": ' + IntToStr(I2) + ', "params_updated": ' + IntToStr(I3) +
                      ', "params_added": ' + IntToStr(B1) +
                      ', "dest_owned": ' + IntToStr(I1) +
                      ', "sheetX": ' + IntToStr(Obj4.SheetSizeX) +
                      ', "sheetY": ' + IntToStr(Obj4.SheetSizeY) + '}';
    end;
    List1.Free;
end;
