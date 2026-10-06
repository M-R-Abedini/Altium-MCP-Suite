// READ-ONLY electrical check of the focused schematic.
//
// KNOWN LIMITATION - MIRRORED COMPONENTS: this checker computes pin hot ends
// from Pin.Location + PinLength along Pin.Orientation, which reports WRONG
// positions for pins of a component transformed with Mirror(). Verified on a
// mirrored USB-C connector: the checker placed its pins where neither the
// builder pin map nor dump_sheet.pas (whose netlist matched a hand-drawn
// reference sheet exactly) put them, and flagged phantom unconnected pins.
// Trust netlist.py compare for mirrored parts; fixing the hot-end math for
// mirrored parents needs its own experiment.
//
// Catches the failure mode where a wire LOOKS connected but is not: routing to
// ISch_Pin.Location (the body end) instead of the pin's electrical end runs
// the wire straight through the pin and out the far side. The pin then reads
// as unconnected, and any neighbouring pin sharing that connection column gets
// silently shorted to it.
//
// For every pin's true connection point it reports:
//   ENDS  - wire vertices landing exactly on it   (a proper connection)
//   THRU  - wire segments crossing it with NO vertex there (the bug)
// A pin with THRU > 0, or with ENDS = 0, is broken.
// Junctions are scored by BRANCH count - a wire ending contributes one branch,
// a wire passing through contributes two, and a pin landing on it one more.
// Fewer than 3 branches means the dot is decoration, which usually marks an
// overshoot rather than a real node.

Obj1 := SchServer.GetCurrentSchDocument;
if (Obj1 = nil) or (Obj1.ObjectID <> 32) then
begin
    ResultText := '{"error": "focused document is not a schematic"}';
    SandboxLog('ABORT: not a schematic');
end
else
begin
    SandboxLog('checking ' + Obj1.DocumentName);
    List1 := TStringList.Create;   // wire segments: x1|y1|x2|y2
    List2 := TStringList.Create;   // report

    // ---- collect every wire segment --------------------------------------
    Obj2 := Obj1.SchIterator_Create;
    Obj2.AddFilter_ObjectSet(MkSet(eWire));
    Obj3 := Obj2.FirstSchObject;
    I4 := 0;
    while (Obj3 <> nil) do
    begin
        I4 := I4 + 1;
        for I1 := 1 to Obj3.VerticesCount - 1 do
            List1.Add(IntToStr(CoordToMils(Obj3.Vertex[I1].X)) + '|' +
                      IntToStr(CoordToMils(Obj3.Vertex[I1].Y)) + '|' +
                      IntToStr(CoordToMils(Obj3.Vertex[I1 + 1].X)) + '|' +
                      IntToStr(CoordToMils(Obj3.Vertex[I1 + 1].Y)));
        Obj3 := Obj2.NextSchObject;
    end;
    Obj1.SchIterator_Destroy(Obj2);
    SandboxLog('wires = ' + IntToStr(I4) + '  segments = ' + IntToStr(List1.Count));

    // Junction coordinates go in the same list, tagged 'J' so they cannot be
    // read as a segment. A pin sitting mid-span on a wire IS connected when a
    // junction marks the tee - that is a deliberate shunt tap, not a short.
    Obj2 := Obj1.SchIterator_Create;
    Obj2.AddFilter_ObjectSet(MkSet(eJunction));
    Obj3 := Obj2.FirstSchObject;
    while (Obj3 <> nil) do
    begin
        List1.Add('J|' + IntToStr(CoordToMils(Obj3.Location.X)) + '|' +
                  IntToStr(CoordToMils(Obj3.Location.Y)));
        Obj3 := Obj2.NextSchObject;
    end;
    Obj1.SchIterator_Destroy(Obj2);

    // ---- test every pin's true connection point --------------------------
    I4 := 0;   // pins with a wire passing through (bad)
    I5 := 0;   // pins with no wire ending on them
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
            I3 := CoordToMils(Obj7.PinLength);
            if (Obj7.Orientation = 0) then I2 := I2 + I3
            else if (Obj7.Orientation = 1) then B1 := B1 + I3
            else if (Obj7.Orientation = 2) then I2 := I2 - I3
            else if (Obj7.Orientation = 3) then B1 := B1 - I3;

            S4 := IntToStr(I2);
            S5 := IntToStr(B1);

            I1 := 0;   // vertices landing on the pin
            S1 := '';  // set to 'X' if a segment passes through
            for I3 := 0 to List1.Count - 1 do
            begin
                S2 := List1[I3];
                // endpoint match?
                if ((SbxField(S2, 0) = S4) and (SbxField(S2, 1) = S5)) then I1 := I1 + 1;
                if ((SbxField(S2, 2) = S4) and (SbxField(S2, 3) = S5)) then I1 := I1 + 1;
                // interior match on an axis-aligned segment?
                if ((SbxField(S2, 0) = S4) and (SbxField(S2, 2) = S4)) then
                    if (((StrToInt(SbxField(S2, 1)) < B1) and (StrToInt(SbxField(S2, 3)) > B1)) or
                        ((StrToInt(SbxField(S2, 3)) < B1) and (StrToInt(SbxField(S2, 1)) > B1))) then
                        S1 := 'X';
                if ((SbxField(S2, 1) = S5) and (SbxField(S2, 3) = S5)) then
                    if (((StrToInt(SbxField(S2, 0)) < I2) and (StrToInt(SbxField(S2, 2)) > I2)) or
                        ((StrToInt(SbxField(S2, 2)) < I2) and (StrToInt(SbxField(S2, 0)) > I2))) then
                        S1 := 'X';
            end;

            // a junction here turns "wire passes through" into a valid tap
            if (S1 = 'X') then
                if (List1.IndexOf('J|' + S4 + '|' + S5) >= 0) then S1 := 'TAP';

            S3 := Obj6.Designator.Text + '.' + Obj7.Name + ' (' + S4 + ',' + S5 + ')' +
                  '  ENDS=' + IntToStr(I1);
            if (S1 = 'TAP') then
                S3 := S3 + '  TAP (pin on a wire, junction present)';
            if (S1 = 'X') then
            begin
                S3 := S3 + '  THRU=YES  <== wire passes through this pin';
                I4 := I4 + 1;
            end;
            if (I1 = 0) and (S1 = '') then
            begin
                S3 := S3 + '  <== nothing connects here';
                I5 := I5 + 1;
            end;
            SandboxLog('  ' + S3);
            List2.Add(S3);

            Obj7 := Obj4.NextSchObject;
        end;
        Obj6.SchIterator_Destroy(Obj4);
        Obj6 := Obj2.NextSchObject;
    end;
    Obj1.SchIterator_Destroy(Obj2);

    // ---- junctions: how many wire ends actually meet there ----------------
    SandboxLog('--- junctions ---');
    I3 := 0;
    Obj2 := Obj1.SchIterator_Create;
    Obj2.AddFilter_ObjectSet(MkSet(eJunction));
    Obj3 := Obj2.FirstSchObject;
    while (Obj3 <> nil) do
    begin
        S4 := IntToStr(CoordToMils(Obj3.Location.X));
        S5 := IntToStr(CoordToMils(Obj3.Location.Y));
        // Branch count, not endpoint count: a wire ENDING here contributes one
        // branch, but a wire PASSING THROUGH contributes two (it continues out
        // the other side). A T-tap is one stub end plus a rail passing through
        // = 3 branches, and counting only endpoints would wrongly flag it.
        I1 := 0;
        I2 := 0;
        while (I2 < List1.Count) do
        begin
            S2 := List1[I2];
            if ((SbxField(S2, 0) = S4) and (SbxField(S2, 1) = S5)) then I1 := I1 + 1;
            if ((SbxField(S2, 2) = S4) and (SbxField(S2, 3) = S5)) then I1 := I1 + 1;
            if ((SbxField(S2, 0) = S4) and (SbxField(S2, 2) = S4)) then
                if (((StrToInt(SbxField(S2, 1)) < StrToInt(S5)) and (StrToInt(SbxField(S2, 3)) > StrToInt(S5))) or
                    ((StrToInt(SbxField(S2, 3)) < StrToInt(S5)) and (StrToInt(SbxField(S2, 1)) > StrToInt(S5)))) then
                    I1 := I1 + 2;
            if ((SbxField(S2, 1) = S5) and (SbxField(S2, 3) = S5)) then
                if (((StrToInt(SbxField(S2, 0)) < StrToInt(S4)) and (StrToInt(SbxField(S2, 2)) > StrToInt(S4))) or
                    ((StrToInt(SbxField(S2, 2)) < StrToInt(S4)) and (StrToInt(SbxField(S2, 0)) > StrToInt(S4)))) then
                    I1 := I1 + 2;
            I2 := I2 + 1;
        end;
        // a pin landing on the junction is a branch too
        Obj4 := Obj1.SchIterator_Create;
        Obj4.AddFilter_ObjectSet(MkSet(eSchComponent));
        Obj6 := Obj4.FirstSchObject;
        while (Obj6 <> nil) do
        begin
            Obj5 := Obj6.SchIterator_Create;
            Obj5.AddFilter_ObjectSet(MkSet(ePin));
            Obj7 := Obj5.FirstSchObject;
            while (Obj7 <> nil) do
            begin
                I2 := CoordToMils(Obj7.Location.X);
                B1 := CoordToMils(Obj7.Location.Y);
                if (Obj7.Orientation = 0) then I2 := I2 + CoordToMils(Obj7.PinLength)
                else if (Obj7.Orientation = 1) then B1 := B1 + CoordToMils(Obj7.PinLength)
                else if (Obj7.Orientation = 2) then I2 := I2 - CoordToMils(Obj7.PinLength)
                else if (Obj7.Orientation = 3) then B1 := B1 - CoordToMils(Obj7.PinLength);
                if ((IntToStr(I2) = S4) and (IntToStr(B1) = S5)) then I1 := I1 + 1;
                Obj7 := Obj5.NextSchObject;
            end;
            Obj6.SchIterator_Destroy(Obj5);
            Obj6 := Obj4.NextSchObject;
        end;
        Obj1.SchIterator_Destroy(Obj4);

        S3 := 'junction (' + S4 + ',' + S5 + ') branches=' + IntToStr(I1);
        if (I1 < 3) then
        begin
            S3 := S3 + '  <== fewer than 3 branches meet here';
            I3 := I3 + 1;
        end;
        SandboxLog('  ' + S3);
        List2.Add(S3);
        Obj3 := Obj2.NextSchObject;
    end;
    Obj1.SchIterator_Destroy(Obj2);

    // Stash the pin counters: the body-crossing scan below reuses I4/I5 for
    // bounding-box edges. Reusing a variable that still holds a result is the
    // single most repeated bug in this codebase - see SCHEMATIC_CONVENTIONS.md.
    SavedCounts := IntToStr(I4) + '|' + IntToStr(I5);

    // ---- wires must not cross a component BODY ---------------------------
    // A wire routed through a symbol looks connected to it and is not. The
    // body here is the DRAWN outline only - graphics, no pins - because wires
    // legitimately end on pins, which stick out past the body.
    SandboxLog('--- wires through component bodies ---');
    BodyCross := 0;
    Obj2 := Obj1.SchIterator_Create;
    Obj2.AddFilter_ObjectSet(MkSet(eSchComponent));
    Obj6 := Obj2.FirstSchObject;
    while (Obj6 <> nil) do
    begin
        I4 := 999999;   // left
        I5 := -999999;  // right
        B1 := 999999;   // bottom
        I2 := -999999;  // top
        Obj4 := Obj6.SchIterator_Create;
        Obj4.AddFilter_ObjectSet(MkSet(eLine, eRectangle, eArc, ePolyline, eEllipse));
        Obj7 := Obj4.FirstSchObject;
        while (Obj7 <> nil) do
        begin
            Obj5 := Obj7.BoundingRectangle;
            if (CoordToMils(Obj5.Left) < I4) then I4 := CoordToMils(Obj5.Left);
            if (CoordToMils(Obj5.Right) > I5) then I5 := CoordToMils(Obj5.Right);
            if (CoordToMils(Obj5.Bottom) < B1) then B1 := CoordToMils(Obj5.Bottom);
            if (CoordToMils(Obj5.Top) > I2) then I2 := CoordToMils(Obj5.Top);
            Obj7 := Obj4.NextSchObject;
        end;
        Obj6.SchIterator_Destroy(Obj4);

        if (I4 < 999999) then
            for I1 := 0 to List1.Count - 1 do
            begin
                S2 := List1[I1];
                if (SbxField(S2, 0) <> 'J') then
                begin
                    // horizontal segment through the body?
                    if (SbxField(S2, 1) = SbxField(S2, 3)) then
                        if (StrToInt(SbxField(S2, 1)) > B1) and (StrToInt(SbxField(S2, 1)) < I2) then
                            if (StrToInt(SbxField(S2, 0)) < I5) and (StrToInt(SbxField(S2, 2)) > I4) then
                            begin
                                SandboxLog('  wire crosses ' + Obj6.Designator.Text + ' body at y=' + SbxField(S2, 1));
                                List2.Add('BODYCROSS|' + Obj6.Designator.Text + '|' + S2);
                                BodyCross := BodyCross + 1;
                            end;
                    // vertical segment through the body?
                    if (SbxField(S2, 0) = SbxField(S2, 2)) then
                        if (StrToInt(SbxField(S2, 0)) > I4) and (StrToInt(SbxField(S2, 0)) < I5) then
                            if (StrToInt(SbxField(S2, 1)) < I2) and (StrToInt(SbxField(S2, 3)) > B1) then
                            begin
                                SandboxLog('  wire crosses ' + Obj6.Designator.Text + ' body at x=' + SbxField(S2, 0));
                                List2.Add('BODYCROSS|' + Obj6.Designator.Text + '|' + S2);
                                BodyCross := BodyCross + 1;
                            end;
                end;
            end;
        Obj6 := Obj2.NextSchObject;
    end;
    Obj1.SchIterator_Destroy(Obj2);
    SandboxLog('wires crossing a component body = ' + IntToStr(BodyCross));

    // ---- net labels: a label only names a wire if it TOUCHES it ----------
    // A label sitting near a wire looks right and names nothing, so check that
    // each one lands on a segment (endpoint or interior).
    SandboxLog('--- net labels ---');
    I2 := 0;
    Obj2 := Obj1.SchIterator_Create;
    Obj2.AddFilter_ObjectSet(MkSet(eNetLabel));
    Obj3 := Obj2.FirstSchObject;
    while (Obj3 <> nil) do
    begin
        S4 := IntToStr(CoordToMils(Obj3.Location.X));
        S5 := IntToStr(CoordToMils(Obj3.Location.Y));
        S1 := '';
        for I1 := 0 to List1.Count - 1 do
        begin
            S2 := List1[I1];
            if (SbxField(S2, 0) <> 'J') then
            begin
                if ((SbxField(S2, 0) = S4) and (SbxField(S2, 1) = S5)) then S1 := 'ON';
                if ((SbxField(S2, 2) = S4) and (SbxField(S2, 3) = S5)) then S1 := 'ON';
                if ((SbxField(S2, 0) = S4) and (SbxField(S2, 2) = S4)) then
                    if (((StrToInt(SbxField(S2, 1)) <= StrToInt(S5)) and (StrToInt(SbxField(S2, 3)) >= StrToInt(S5))) or
                        ((StrToInt(SbxField(S2, 3)) <= StrToInt(S5)) and (StrToInt(SbxField(S2, 1)) >= StrToInt(S5)))) then
                        S1 := 'ON';
                if ((SbxField(S2, 1) = S5) and (SbxField(S2, 3) = S5)) then
                    if (((StrToInt(SbxField(S2, 0)) <= StrToInt(S4)) and (StrToInt(SbxField(S2, 2)) >= StrToInt(S4))) or
                        ((StrToInt(SbxField(S2, 2)) <= StrToInt(S4)) and (StrToInt(SbxField(S2, 0)) >= StrToInt(S4)))) then
                        S1 := 'ON';
            end;
        end;
        S3 := 'netlabel "' + Obj3.Text + '" (' + S4 + ',' + S5 + ')';
        if (S1 = 'ON') then
            S3 := S3 + '  on a wire'
        else
        begin
            S3 := S3 + '  <== NOT touching any wire - names nothing';
            I2 := I2 + 1;
        end;
        SandboxLog('  ' + S3);
        List2.Add(S3);
        Obj3 := Obj2.NextSchObject;
    end;
    Obj1.SchIterator_Destroy(Obj2);
    SandboxLog('net labels off-wire = ' + IntToStr(I2));

    List2.SaveToFile('C:\Users\Public\altium_mcp\connectivity.txt');
    SandboxLog('pins with wire passing through = ' + IntToStr(I4));
    SandboxLog('pins with nothing connected    = ' + IntToStr(I5));
    SandboxLog('suspect junctions              = ' + IntToStr(I3));
    ResultText := '{"doc": "' + Obj1.DocumentName + '", "pins_shorted_through": ' + SbxField(SavedCounts, 0) +
                  ', "pins_unconnected": ' + SbxField(SavedCounts, 1) +
                  ', "suspect_junctions": ' + IntToStr(I3) +
                  ', "netlabels_off_wire": ' + IntToStr(I2) +
                  ', "wires_through_bodies": ' + IntToStr(BodyCross) + '}';
    List1.Free;
    List2.Free;
end;
