// READ-ONLY. Dump everything needed to reconstruct a sheet's CIRCUIT - parts
// and connectivity geometry - to C:\Users\Public\altium_mcp\sheet_dump.txt.
//
// The graph analysis (which pins share a net) happens in Python
// (dev/netlist.py): union-find over coordinates is miserable in DelphiScript
// with shared scratch variables, and that pattern has corrupted results four
// times already.
//
// Records:
//   COMP|desig|libref|designitemid|comment|orient
//   CPAR|desig|name|value            (visible parameters only)
//   PIN|desig|pinname|hotx|hoty|electrical
//   WIRE|x1|y1|x2|y2                 (one record per segment)
//   JUNC|x|y
//   NLBL|x|y|text
//   PWR|x|y|text|style
//   PORT|x|y|text|iotype|style|width  (sheet port; x,y = Location)
//
// Set S1 to the target sheet file name before running.

S1 := 'Sheet14.SchDoc';

// Exact match FIRST: an unsaved free document's full path is its bare
// file name, and the project may contain a sheet of the same name
// (Sheet13 was adopted into Base.PrjPcb once). Exact = the free doc.
S2 := '';
for I2 := 0 to GetWorkspace.DM_ProjectCount - 1 do
begin
    Obj1 := GetWorkspace.DM_Projects(I2);
    for I1 := 0 to Obj1.DM_LogicalDocumentCount - 1 do
    begin
        Obj2 := Obj1.DM_LogicalDocuments(I1);
        if (Obj2.DM_FullPath = S1) then S2 := Obj2.DM_FullPath;
    end;
end;
if (S2 = '') then
    for I2 := 0 to GetWorkspace.DM_ProjectCount - 1 do
    begin
        Obj1 := GetWorkspace.DM_Projects(I2);
        for I1 := 0 to Obj1.DM_LogicalDocumentCount - 1 do
        begin
            Obj2 := Obj1.DM_LogicalDocuments(I1);
            if (Pos(S1, Obj2.DM_FullPath) > 0) then S2 := Obj2.DM_FullPath;
        end;
    end;

Obj1 := nil;
if (S2 <> '') then
begin
    Obj3 := Client.OpenDocument('SCH', S2);
    if (Obj3 <> nil) then Client.ShowDocument(Obj3);
    Sleep(1200);
    Obj1 := SchServer.GetSchDocumentByPath(S2);
end;

if (Obj1 = nil) then
begin
    ResultText := '{"error": "sheet not found: ' + S1 + '"}';
    SandboxLog('ABORT: sheet nil');
end
else
begin
    SandboxLog('dumping ' + Obj1.DocumentName);
    List1 := TStringList.Create;

    // ---- components, their visible parameters, and pin hot ends ----------
    Obj2 := Obj1.SchIterator_Create;
    Obj2.AddFilter_ObjectSet(MkSet(eSchComponent));
    Obj6 := Obj2.FirstSchObject;
    while (Obj6 <> nil) do
    begin
        List1.Add('COMP|' + Obj6.Designator.Text + '|' + Obj6.LibReference + '|' +
                  Obj6.DesignItemID + '|' + Obj6.Comment.Text + '|' +
                  IntToStr(Obj6.Orientation));

        Obj4 := Obj6.SchIterator_Create;
        Obj4.AddFilter_ObjectSet(MkSet(eParameter));
        Obj7 := Obj4.FirstSchObject;
        while (Obj7 <> nil) do
        begin
            if (Obj7.IsHidden = False) then
                List1.Add('CPAR|' + Obj6.Designator.Text + '|' + Obj7.Name + '|' + Obj7.Text);
            Obj7 := Obj4.NextSchObject;
        end;
        Obj6.SchIterator_Destroy(Obj4);

        Obj4 := Obj6.SchIterator_Create;
        Obj4.AddFilter_ObjectSet(MkSet(ePin));
        Obj7 := Obj4.FirstSchObject;
        while (Obj7 <> nil) do
        begin
            I3 := CoordToMils(Obj7.Location.X);
            I4 := CoordToMils(Obj7.Location.Y);
            I5 := CoordToMils(Obj7.PinLength);
            if (Obj7.Orientation = 0) then I3 := I3 + I5
            else if (Obj7.Orientation = 1) then I4 := I4 + I5
            else if (Obj7.Orientation = 2) then I3 := I3 - I5
            else if (Obj7.Orientation = 3) then I4 := I4 - I5;
            List1.Add('PIN|' + Obj6.Designator.Text + '|' + Obj7.Designator + '|' +
                      IntToStr(I3) + '|' + IntToStr(I4) + '|' + IntToStr(Obj7.Electrical));
            Obj7 := Obj4.NextSchObject;
        end;
        Obj6.SchIterator_Destroy(Obj4);

        Obj6 := Obj2.NextSchObject;
    end;
    Obj1.SchIterator_Destroy(Obj2);

    // ---- wires (as segments), junctions, labels, power, ports -------------
    Obj2 := Obj1.SchIterator_Create;
    Obj2.AddFilter_ObjectSet(MkSet(eWire));
    Obj6 := Obj2.FirstSchObject;
    while (Obj6 <> nil) do
    begin
        for I1 := 1 to Obj6.VerticesCount - 1 do
            List1.Add('WIRE|' + IntToStr(CoordToMils(Obj6.Vertex[I1].X)) + '|' +
                      IntToStr(CoordToMils(Obj6.Vertex[I1].Y)) + '|' +
                      IntToStr(CoordToMils(Obj6.Vertex[I1 + 1].X)) + '|' +
                      IntToStr(CoordToMils(Obj6.Vertex[I1 + 1].Y)));
        Obj6 := Obj2.NextSchObject;
    end;
    Obj1.SchIterator_Destroy(Obj2);

    Obj2 := Obj1.SchIterator_Create;
    Obj2.AddFilter_ObjectSet(MkSet(eJunction));
    Obj6 := Obj2.FirstSchObject;
    while (Obj6 <> nil) do
    begin
        List1.Add('JUNC|' + IntToStr(CoordToMils(Obj6.Location.X)) + '|' +
                  IntToStr(CoordToMils(Obj6.Location.Y)));
        Obj6 := Obj2.NextSchObject;
    end;
    Obj1.SchIterator_Destroy(Obj2);

    Obj2 := Obj1.SchIterator_Create;
    Obj2.AddFilter_ObjectSet(MkSet(eNetLabel));
    Obj6 := Obj2.FirstSchObject;
    while (Obj6 <> nil) do
    begin
        List1.Add('NLBL|' + IntToStr(CoordToMils(Obj6.Location.X)) + '|' +
                  IntToStr(CoordToMils(Obj6.Location.Y)) + '|' + Obj6.Text);
        Obj6 := Obj2.NextSchObject;
    end;
    Obj1.SchIterator_Destroy(Obj2);

    Obj2 := Obj1.SchIterator_Create;
    Obj2.AddFilter_ObjectSet(MkSet(ePowerObject));
    Obj6 := Obj2.FirstSchObject;
    while (Obj6 <> nil) do
    begin
        List1.Add('PWR|' + IntToStr(CoordToMils(Obj6.Location.X)) + '|' +
                  IntToStr(CoordToMils(Obj6.Location.Y)) + '|' + Obj6.Text + '|' +
                  IntToStr(Obj6.Style));
        Obj6 := Obj2.NextSchObject;
    end;
    Obj1.SchIterator_Destroy(Obj2);

    // Sheet ports: Location is one end; the other end is Width away along the
    // port. Both ends can take a wire, so dump both and let Python try each.
    Obj2 := Obj1.SchIterator_Create;
    Obj2.AddFilter_ObjectSet(MkSet(ePort));
    Obj6 := Obj2.FirstSchObject;
    while (Obj6 <> nil) do
    begin
        List1.Add('PORT|' + IntToStr(CoordToMils(Obj6.Location.X)) + '|' +
                  IntToStr(CoordToMils(Obj6.Location.Y)) + '|' + Obj6.Name + '|' +
                  IntToStr(Obj6.IOType) + '|' + IntToStr(Obj6.Style) + '|' +
                  IntToStr(CoordToMils(Obj6.Width)));
        Obj6 := Obj2.NextSchObject;
    end;
    Obj1.SchIterator_Destroy(Obj2);

    // ---- text and body bounding boxes, for the overlap audit --------------
    // TEXT|kind|owner|text|x1|y1|x2|y2   BODY|desig|x1|y1|x2|y2   (mils)
    Obj2 := Obj1.SchIterator_Create;
    Obj2.AddFilter_ObjectSet(MkSet(eSchComponent));
    Obj6 := Obj2.FirstSchObject;
    while (Obj6 <> nil) do
    begin
        Obj5 := Obj6.Designator.BoundingRectangle;
        List1.Add('TEXT|DESIG|' + Obj6.Designator.Text + '|' + Obj6.Designator.Text + '|' +
                  IntToStr(CoordToMils(Obj5.Left)) + '|' + IntToStr(CoordToMils(Obj5.Bottom)) + '|' +
                  IntToStr(CoordToMils(Obj5.Right)) + '|' + IntToStr(CoordToMils(Obj5.Top)));

        Obj4 := Obj6.SchIterator_Create;
        Obj4.AddFilter_ObjectSet(MkSet(eParameter));
        Obj7 := Obj4.FirstSchObject;
        while (Obj7 <> nil) do
        begin
            if (Obj7.IsHidden = False) then
            begin
                Obj5 := Obj7.BoundingRectangle;
                List1.Add('TEXT|PARAM|' + Obj6.Designator.Text + '|' + Obj7.Text + '|' +
                          IntToStr(CoordToMils(Obj5.Left)) + '|' + IntToStr(CoordToMils(Obj5.Bottom)) + '|' +
                          IntToStr(CoordToMils(Obj5.Right)) + '|' + IntToStr(CoordToMils(Obj5.Top)));
            end;
            Obj7 := Obj4.NextSchObject;
        end;
        Obj6.SchIterator_Destroy(Obj4);

        // drawn body only (graphics, no pins, no text)
        I3 := 999999; I4 := 999999; I5 := -999999; B1 := -999999;
        Obj4 := Obj6.SchIterator_Create;
        Obj4.AddFilter_ObjectSet(MkSet(eLine, eRectangle, eArc, ePolyline, eEllipse));
        Obj7 := Obj4.FirstSchObject;
        while (Obj7 <> nil) do
        begin
            Obj5 := Obj7.BoundingRectangle;
            if (CoordToMils(Obj5.Left)   < I3) then I3 := CoordToMils(Obj5.Left);
            if (CoordToMils(Obj5.Bottom) < I4) then I4 := CoordToMils(Obj5.Bottom);
            if (CoordToMils(Obj5.Right)  > I5) then I5 := CoordToMils(Obj5.Right);
            if (CoordToMils(Obj5.Top)    > B1) then B1 := CoordToMils(Obj5.Top);
            Obj7 := Obj4.NextSchObject;
        end;
        Obj6.SchIterator_Destroy(Obj4);
        if (I3 < 999999) then
            List1.Add('BODY|' + Obj6.Designator.Text + '|' + IntToStr(I3) + '|' + IntToStr(I4) + '|' +
                      IntToStr(I5) + '|' + IntToStr(B1));

        Obj6 := Obj2.NextSchObject;
    end;
    Obj1.SchIterator_Destroy(Obj2);

    Obj2 := Obj1.SchIterator_Create;
    Obj2.AddFilter_ObjectSet(MkSet(eNetLabel));
    Obj6 := Obj2.FirstSchObject;
    while (Obj6 <> nil) do
    begin
        Obj5 := Obj6.BoundingRectangle;
        List1.Add('TEXT|NLBL|-|' + Obj6.Text + '|' +
                  IntToStr(CoordToMils(Obj5.Left)) + '|' + IntToStr(CoordToMils(Obj5.Bottom)) + '|' +
                  IntToStr(CoordToMils(Obj5.Right)) + '|' + IntToStr(CoordToMils(Obj5.Top)));
        Obj6 := Obj2.NextSchObject;
    end;
    Obj1.SchIterator_Destroy(Obj2);

    Obj2 := Obj1.SchIterator_Create;
    Obj2.AddFilter_ObjectSet(MkSet(eLabel));
    Obj6 := Obj2.FirstSchObject;
    while (Obj6 <> nil) do
    begin
        Obj5 := Obj6.BoundingRectangle;
        List1.Add('TEXT|NOTE|-|' + Obj6.Text + '|' +
                  IntToStr(CoordToMils(Obj5.Left)) + '|' + IntToStr(CoordToMils(Obj5.Bottom)) + '|' +
                  IntToStr(CoordToMils(Obj5.Right)) + '|' + IntToStr(CoordToMils(Obj5.Top)));
        Obj6 := Obj2.NextSchObject;
    end;
    Obj1.SchIterator_Destroy(Obj2);

    // Power ports: BoundingRectangle covers symbol + drawn label
    Obj2 := Obj1.SchIterator_Create;
    Obj2.AddFilter_ObjectSet(MkSet(ePowerObject));
    Obj6 := Obj2.FirstSchObject;
    while (Obj6 <> nil) do
    begin
        Obj5 := Obj6.BoundingRectangle;
        List1.Add('TEXT|PWR|-|' + Obj6.Text + '|' +
                  IntToStr(CoordToMils(Obj5.Left)) + '|' + IntToStr(CoordToMils(Obj5.Bottom)) + '|' +
                  IntToStr(CoordToMils(Obj5.Right)) + '|' + IntToStr(CoordToMils(Obj5.Top)));
        Obj6 := Obj2.NextSchObject;
    end;
    Obj1.SchIterator_Destroy(Obj2);

    // Sheet ports likewise
    Obj2 := Obj1.SchIterator_Create;
    Obj2.AddFilter_ObjectSet(MkSet(ePort));
    Obj6 := Obj2.FirstSchObject;
    while (Obj6 <> nil) do
    begin
        Obj5 := Obj6.BoundingRectangle;
        List1.Add('TEXT|SPORT|-|' + Obj6.Name + '|' +
                  IntToStr(CoordToMils(Obj5.Left)) + '|' + IntToStr(CoordToMils(Obj5.Bottom)) + '|' +
                  IntToStr(CoordToMils(Obj5.Right)) + '|' + IntToStr(CoordToMils(Obj5.Top)));
        Obj6 := Obj2.NextSchObject;
    end;
    Obj1.SchIterator_Destroy(Obj2);

    List1.SaveToFile('C:\Users\Public\altium_mcp\sheet_dump.txt');
    SandboxLog('records = ' + IntToStr(List1.Count));
    ResultText := '{"sheet": "' + Obj1.DocumentName + '", "records": ' + IntToStr(List1.Count) + '}';
    List1.Free;
end;
