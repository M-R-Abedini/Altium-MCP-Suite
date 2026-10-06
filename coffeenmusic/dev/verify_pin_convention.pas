// READ-ONLY. Which end of a pin actually takes a wire? Use a HAND-WIRED sheet
// as the oracle instead of my own formula.
//   A = Pin.Location
//   B = Pin.Location + PinLength along Pin.Orientation
// Count wire endpoints landing on each.
//
// The previous attempt wedged here: GetSchDocumentByPath returns NIL for a
// document that belongs to the project but is not open in the editor, and the
// next call dereferenced it. Open it first, then guard.
S1 := '';
for I2 := 0 to GetWorkspace.DM_ProjectCount - 1 do
begin
    Obj1 := GetWorkspace.DM_Projects(I2);
    for I1 := 0 to Obj1.DM_LogicalDocumentCount - 1 do
    begin
        Obj2 := Obj1.DM_LogicalDocuments(I1);
        if (Pos('Sheet6.SchDoc', Obj2.DM_FullPath) > 0) then
            S1 := Obj2.DM_FullPath;
    end;
end;
SandboxLog('reference sheet: ' + S1);

Obj1 := nil;
if (S1 <> '') then
begin
    SandboxLog('opening it');
    Obj2 := Client.OpenDocument('SCH', S1);
    if (Obj2 <> nil) then Client.ShowDocument(Obj2);
    Sleep(1500);
    Obj1 := SchServer.GetSchDocumentByPath(S1);
end;

if (Obj1 = nil) then
begin
    ResultText := '{"error": "could not open a reference schematic"}';
    SandboxLog('ABORT: reference doc nil');
end
else
begin
    SandboxLog('got document: ' + Obj1.DocumentName);
    List1 := TStringList.Create;
    Obj2 := Obj1.SchIterator_Create;
    Obj2.AddFilter_ObjectSet(MkSet(eWire));
    Obj3 := Obj2.FirstSchObject;
    while (Obj3 <> nil) do
    begin
        for I1 := 1 to Obj3.VerticesCount do
            List1.Add(IntToStr(CoordToMils(Obj3.Vertex[I1].X)) + ',' +
                      IntToStr(CoordToMils(Obj3.Vertex[I1].Y)));
        Obj3 := Obj2.NextSchObject;
    end;
    Obj1.SchIterator_Destroy(Obj2);
    SandboxLog('wire vertices = ' + IntToStr(List1.Count));

    I4 := 0;
    I5 := 0;
    Obj2 := Obj1.SchIterator_Create;
    Obj2.AddFilter_ObjectSet(MkSet(eSchComponent));
    Obj6 := Obj2.FirstSchObject;
    while (Obj6 <> nil) do
    begin
        Obj4 := Obj6.SchIterator_Create;
        Obj4.AddFilter_ObjectSet(MkSet(ePin));
        Obj7 := Obj4.FirstSchObject;
        while (Obj7 <> nil) do
        begin
            I2 := CoordToMils(Obj7.Location.X);
            B1 := CoordToMils(Obj7.Location.Y);
            S4 := IntToStr(I2) + ',' + IntToStr(B1);
            I3 := CoordToMils(Obj7.PinLength);
            if (Obj7.Orientation = 0) then I2 := I2 + I3
            else if (Obj7.Orientation = 1) then B1 := B1 + I3
            else if (Obj7.Orientation = 2) then I2 := I2 - I3
            else if (Obj7.Orientation = 3) then B1 := B1 - I3;
            S5 := IntToStr(I2) + ',' + IntToStr(B1);
            if (List1.IndexOf(S4) >= 0) then I4 := I4 + 1;
            if (List1.IndexOf(S5) >= 0) then I5 := I5 + 1;
            Obj7 := Obj4.NextSchObject;
        end;
        Obj6.SchIterator_Destroy(Obj4);
        Obj6 := Obj2.NextSchObject;
    end;
    Obj1.SchIterator_Destroy(Obj2);
    SandboxLog('wire ends on Pin.Location        (A) = ' + IntToStr(I4));
    SandboxLog('wire ends on Location+PinLength  (B) = ' + IntToStr(I5));
    ResultText := '{"hits_on_Location": ' + IntToStr(I4) +
                  ', "hits_on_Location_plus_len": ' + IntToStr(I5) + '}';
    List1.Free;
end;
