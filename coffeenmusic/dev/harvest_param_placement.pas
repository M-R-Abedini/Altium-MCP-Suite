// READ-ONLY. Harvest parameter placement from a hand-drawn schematic, keyed by
// LibReference, so generated sheets match house style instead of using offsets
// I invented.
//
// Method follows CopyParamPlacement.pas: a parameter's placement is its offset
// from the OWNING COMPONENT'S Location, plus Justification and Orientation.
// Justification is what makes a column of values line up - setting position
// alone still leaves ragged text.
//
// Output (C:\Users\Public\altium_mcp\param_placement.txt):
//   PLACE|<LibReference>|<compOrientation>|<paramName>|<dx>|<dy>|<just>|<ori>
// where paramName is DESIGNATOR for the designator. Only VISIBLE parameters
// are recorded; everything else stays hidden on the generated part.

S1 := 'Sheet6.SchDoc';
S2 := '';
for I2 := 0 to GetWorkspace.DM_ProjectCount - 1 do
begin
    Obj1 := GetWorkspace.DM_Projects(I2);
    for I1 := 0 to Obj1.DM_LogicalDocumentCount - 1 do
    begin
        Obj2 := Obj1.DM_LogicalDocuments(I1);
        if (Pos(S1, Obj2.DM_FullPath) > 0) then S2 := Obj2.DM_FullPath;
    end;
end;
SandboxLog('source sheet: ' + S2);

Obj1 := nil;
if (S2 <> '') then
begin
    Obj3 := Client.OpenDocument('SCH', S2);
    if (Obj3 <> nil) then Client.ShowDocument(Obj3);
    Sleep(1500);
    Obj1 := SchServer.GetSchDocumentByPath(S2);
end;

if (Obj1 = nil) then
begin
    ResultText := '{"error": "reference sheet not available"}';
    SandboxLog('ABORT: reference sheet nil');
end
else
begin
    List1 := TStringList.Create;
    List2 := TStringList.Create;      // LibReferences already captured
    I4 := 0;

    Obj2 := Obj1.SchIterator_Create;
    Obj2.AddFilter_ObjectSet(MkSet(eSchComponent));
    Obj6 := Obj2.FirstSchObject;
    while (Obj6 <> nil) do
    begin
        // Key on symbol AND orientation. A style harvested from a rotated part
        // does not transfer to an unrotated one: beside-the-body is right for a
        // vertical resistor and lands on the wire for a horizontal one.
        S3 := Obj6.LibReference;
        S1 := S3 + '#' + IntToStr(Obj6.Orientation);
        if (List2.IndexOf(S1) < 0) then
        begin
            List2.Add(S1);
            I4 := I4 + 1;
            I1 := CoordToMils(Obj6.Location.X);
            I2 := CoordToMils(Obj6.Location.Y);

            List1.Add('PLACE|' + S3 + '|' + IntToStr(Obj6.Orientation) + '|DESIGNATOR|' +
                      IntToStr(CoordToMils(Obj6.Designator.Location.X) - I1) + '|' +
                      IntToStr(CoordToMils(Obj6.Designator.Location.Y) - I2) + '|' +
                      IntToStr(Obj6.Designator.Justification) + '|' +
                      IntToStr(Obj6.Designator.Orientation));

            Obj4 := Obj6.SchIterator_Create;
            Obj4.AddFilter_ObjectSet(MkSet(eParameter));
            Obj7 := Obj4.FirstSchObject;
            while (Obj7 <> nil) do
            begin
                if (Obj7.IsHidden = False) then
                    List1.Add('PLACE|' + S3 + '|' + IntToStr(Obj6.Orientation) + '|' +
                              Obj7.Name + '|' +
                              IntToStr(CoordToMils(Obj7.Location.X) - I1) + '|' +
                              IntToStr(CoordToMils(Obj7.Location.Y) - I2) + '|' +
                              IntToStr(Obj7.Justification) + '|' +
                              IntToStr(Obj7.Orientation));
                Obj7 := Obj4.NextSchObject;
            end;
            Obj6.SchIterator_Destroy(Obj4);
            SandboxLog('  captured ' + S3 + ' (compOri=' + IntToStr(Obj6.Orientation) + ')');
        end;
        Obj6 := Obj2.NextSchObject;
    end;
    Obj1.SchIterator_Destroy(Obj2);

    List1.SaveToFile('C:\Users\Public\altium_mcp\param_placement.txt');
    SandboxLog('symbols captured = ' + IntToStr(I4) + '  records = ' + IntToStr(List1.Count));
    ResultText := '{"symbols": ' + IntToStr(I4) + ', "records": ' + IntToStr(List1.Count) + '}';
    List1.Free;
    List2.Free;
end;
