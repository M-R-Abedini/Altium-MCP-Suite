{ JSON parsing helpers adapted from eda-agent/scripts/altium/Main.pas. }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>. }
{ Apache-2.0; see eda-agent/LICENSE and eda-agent/NOTICE in this suite. }
Function IsWhitespaceOrColon(S : String; Idx : Integer) : Boolean;
Var
    C : String;
Begin
    C := Copy(S, Idx, 1);
    Result := (C = ' ') Or (C = ':') Or (C = #9) Or (C = #10) Or (C = #13);
End;

Function IsDelimiter(S : String; Idx : Integer) : Boolean;
Var
    C : String;
Begin
    C := Copy(S, Idx, 1);
    Result := (C = '') Or (C = ',') Or (C = '}') Or (C = ']') Or (C = ' ') Or (C = #9) Or (C = #10) Or (C = #13);
End;

{ Hex digit to integer (0-15). Returns -1 for invalid input. }
Function HexDigitValue(Ch : String) : Integer;
Var
    O : Integer;
Begin
    Result := -1;
    If Length(Ch) <> 1 Then Exit;
    O := Ord(Ch[1]);
    If (O >= Ord('0')) And (O <= Ord('9')) Then Result := O - Ord('0')
    Else If (O >= Ord('a')) And (O <= Ord('f')) Then Result := O - Ord('a') + 10
    Else If (O >= Ord('A')) And (O <= Ord('F')) Then Result := O - Ord('A') + 10;
End;

Function UnescapeJsonString(S : String) : String;
Var
    I, L : Integer;
    Ch, NextCh, HexStr : String;
    Code, D0, D1, D2, D3 : Integer;
Begin
    // Char-by-char JSON unescape with full \uXXXX support. The naive
    // StringReplace cascade (\t -> tab, \n -> LF, ..., \\ -> \) is broken
    // for sequences like \\temp, handles escapes left-to-right so \\
    // collapses to \ before evaluating the following char.
    Result := '';
    I := 1;
    L := Length(S);
    While I <= L Do
    Begin
        Ch := Copy(S, I, 1);
        If (Ch = '\') And (I < L) Then
        Begin
            NextCh := Copy(S, I + 1, 1);
            If NextCh = '\' Then Begin Result := Result + '\'; Inc(I, 2); End
            Else If NextCh = 'n' Then Begin Result := Result + #10; Inc(I, 2); End
            Else If NextCh = 't' Then Begin Result := Result + #9; Inc(I, 2); End
            Else If NextCh = 'r' Then Begin Result := Result + #13; Inc(I, 2); End
            Else If NextCh = '"' Then Begin Result := Result + '"'; Inc(I, 2); End
            Else If NextCh = '/' Then Begin Result := Result + '/'; Inc(I, 2); End
            Else If NextCh = 'b' Then Begin Result := Result + #8; Inc(I, 2); End
            Else If NextCh = 'f' Then Begin Result := Result + #12; Inc(I, 2); End
            Else If NextCh = 'u' Then
            Begin
                // \uXXXX, 4 hex digits. Codepoints <= 255 are emitted as a
                // single ANSI byte (Pascal native). Higher codepoints can't
                // be represented in single-byte ANSI and must be rejected.
                // Python checks this before publishing the request.
                If I + 5 <= L Then
                Begin
                    HexStr := Copy(S, I + 2, 4);
                    D0 := HexDigitValue(Copy(HexStr, 1, 1));
                    D1 := HexDigitValue(Copy(HexStr, 2, 1));
                    D2 := HexDigitValue(Copy(HexStr, 3, 1));
                    D3 := HexDigitValue(Copy(HexStr, 4, 1));
                    If (D0 >= 0) And (D1 >= 0) And (D2 >= 0) And (D3 >= 0) Then
                    Begin
                        Code := (D0 * 4096) + (D1 * 256) + (D2 * 16) + D3;
                        If Code <= 255 Then
                            Result := Result + Chr(Code)
                        Else
                            Result := Result + '?';
                        Inc(I, 6);
                    End
                    Else
                    Begin
                        // Bad hex, keep literal
                        Result := Result + Ch + NextCh;
                        Inc(I, 2);
                    End;
                End
                Else
                Begin
                    Result := Result + Ch + NextCh;
                    Inc(I, 2);
                End;
            End
            Else
            Begin
                // Unknown escape, keep both chars literally
                Result := Result + Ch + NextCh;
                Inc(I, 2);
            End;
        End
        Else
        Begin
            Result := Result + Ch;
            Inc(I);
        End;
    End;
End;

Function FindJsonMemberValue(Json : String; Key : String) : Integer;
Var
    I, J, K, L, Depth : Integer;
    Ch, Member : String;
    ExpectKey : Boolean;
Begin
    Result := 0;
    I := 1;
    L := Length(Json);
    Depth := 0;
    ExpectKey := False;
    While I <= L Do
    Begin
        Ch := Copy(Json, I, 1);
        If Ch = '"' Then
        Begin
            J := I + 1;
            While J <= L Do
            Begin
                If Copy(Json, J, 1) = '\' Then Inc(J, 2)
                Else If Copy(Json, J, 1) = '"' Then Break
                Else Inc(J);
            End;
            If J > L Then Exit;
            If (Depth = 1) And ExpectKey Then
            Begin
                Member := UnescapeJsonString(Copy(Json, I + 1, J - I - 1));
                K := J + 1;
                While (K <= L) And (Copy(Json, K, 1) <= ' ') Do Inc(K);
                If Copy(Json, K, 1) <> ':' Then Exit;
                Inc(K);
                While (K <= L) And (Copy(Json, K, 1) <= ' ') Do Inc(K);
                If Member = Key Then Begin Result := K; Exit; End;
                ExpectKey := False;
            End;
            I := J + 1;
        End
        Else
        Begin
            If (Ch = '{') Or (Ch = '[') Then
            Begin
                Inc(Depth);
                If Depth = 1 Then
                Begin
                    If Ch <> '{' Then Exit;
                    ExpectKey := True;
                End;
            End
            Else If (Ch = '}') Or (Ch = ']') Then
            Begin
                Dec(Depth);
                If Depth = 0 Then Exit;
            End
            Else If (Ch = ',') And (Depth = 1) Then ExpectKey := True;
            Inc(I);
        End;
    End;
End;

Function LegacyJsonValue(Json : String; Key : String) : String;
Var
    StartPos, EndPos : Integer;
    BraceCount : Integer;
    BackslashCount, TempPos : Integer;
    InStr : Boolean;
    Ch : String;
Begin
    Result := '';
    StartPos := FindJsonMemberValue(Json, Key);
    If StartPos > 0 Then
    Begin
        If StartPos <= Length(Json) Then
        Begin
            If Copy(Json, StartPos, 1) = '"' Then
            Begin
                // String value
                Inc(StartPos);
                EndPos := StartPos;
                While (EndPos <= Length(Json)) Do
                Begin
                    If Copy(Json, EndPos, 1) = '"' Then
                    Begin
                        // Count consecutive backslashes before this quote
                        BackslashCount := 0;
                        TempPos := EndPos - 1;
                        While (TempPos >= StartPos) And (Copy(Json, TempPos, 1) = '\') Do
                        Begin
                            Inc(BackslashCount);
                            Dec(TempPos);
                        End;
                        // Even number of backslashes means quote is real
                        If (BackslashCount Mod 2) = 0 Then Break;
                    End;
                    Inc(EndPos);
                End;
                Result := UnescapeJsonString(Copy(Json, StartPos, EndPos - StartPos));
            End
            Else If (Copy(Json, StartPos, 1) = '{') Or
                    (Copy(Json, StartPos, 1) = '[') Then
            Begin
                // Container value (object or array). Depth-count braces AND
                // brackets together, skipping string literals so a '}' or
                // ']' inside a "string" can't close the container early.
                EndPos := StartPos;
                BraceCount := 0;
                InStr := False;
                While EndPos <= Length(Json) Do
                Begin
                    Ch := Copy(Json, EndPos, 1);
                    If InStr Then
                    Begin
                        If Ch = '"' Then
                        Begin
                            // Real close-quote only after an even run of '\'.
                            BackslashCount := 0;
                            TempPos := EndPos - 1;
                            While (TempPos >= StartPos) And
                                  (Copy(Json, TempPos, 1) = '\') Do
                            Begin
                                Inc(BackslashCount);
                                Dec(TempPos);
                            End;
                            If (BackslashCount Mod 2) = 0 Then InStr := False;
                        End;
                    End
                    Else
                    Begin
                        If Ch = '"' Then InStr := True
                        Else If (Ch = '{') Or (Ch = '[') Then Inc(BraceCount)
                        Else If (Ch = '}') Or (Ch = ']') Then Dec(BraceCount);
                    End;
                    Inc(EndPos);
                    If (Not InStr) And (BraceCount = 0) Then Break;
                End;
                Result := Copy(Json, StartPos, EndPos - StartPos);
            End
            Else
            Begin
                // Number or other value
                EndPos := StartPos;
                While (EndPos <= Length(Json)) And (Not IsDelimiter(Json, EndPos)) Do
                    Inc(EndPos);
                Result := Copy(Json, StartPos, EndPos - StartPos);
            End;
        End;
    End;
End;


// Decode one JSON scalar line. Remove only syntax, never data punctuation.
function TrimJSON(InputStr: String): String;
var
    Value: String;
begin
    Value := Trim(InputStr);
    if (Length(Value) > 0) and (Copy(Value, Length(Value), 1) = ',') then
        Value := Trim(Copy(Value, 1, Length(Value) - 1));
    Result := LegacyJsonValue('{"value":' + Value + '}', 'value');
end;

// Helper function to escape JSON strings
function JSONEscapeString(const S: String): String;
var
    I, Code: Integer;
    Ch: String;
begin
    Result := '';
    for I := 1 to Length(S) do
    begin
        Ch := Copy(S, I, 1);
        Code := Ord(Ch[1]);
        if Ch = '\' then Result := Result + '\\'
        else if Ch = '"' then Result := Result + '\"'
        else if (Code < 32) or (Code > 126) then Result := Result + '\u' + IntToHex(Code, 4)
        else Result := Result + Ch;
    end;
end;

// Function to create a JSON name-value pair
function JSONPairStr(const Name, Value: String; IsString: Boolean): String;
begin
    if IsString then
        Result := '"' + JSONEscapeString(Name) + '": "' + JSONEscapeString(Value) + '"'
    else
        Result := '"' + JSONEscapeString(Name) + '": ' + Value;
end;

// Function to build a JSON object from a list of pairs
function BuildJSONObject(Pairs: TStringList; IndentLevel: Integer = 0): String;
var
    i: Integer;
    Output: TStringList;
    Indent, ChildIndent: String;
begin
    // Create indent strings based on level
    Indent := StringOfChar(' ', IndentLevel * 2);
    ChildIndent := StringOfChar(' ', (IndentLevel + 1) * 2);
    
    Output := TStringList.Create;
    try
        Output.Add(Indent + '{');
        
        for i := 0 to Pairs.Count - 1 do
        begin
            if i < Pairs.Count - 1 then
                Output.Add(ChildIndent + Pairs[i] + ',')
            else
                Output.Add(ChildIndent + Pairs[i]);
        end;
        
        Output.Add(Indent + '}');
        
        Result := Output.Text;
    finally
        Output.Free;
    end;
end;

// Function to build a JSON array from a list of items
function BuildJSONArray(Items: TStringList; ArrayName: String = ''; IndentLevel: Integer = 0): String;
var
    i: Integer;
    Output: TStringList;
    Indent, ChildIndent: String;
begin
    // Create indent strings based on level
    Indent := StringOfChar(' ', IndentLevel * 2);
    ChildIndent := StringOfChar(' ', (IndentLevel + 1) * 2);
    
    Output := TStringList.Create;
    try
        if ArrayName <> '' then
            Output.Add(Indent + '"' + JSONEscapeString(ArrayName) + '": [')
        else
            Output.Add(Indent + '[');
        
        for i := 0 to Items.Count - 1 do
        begin
            if i < Items.Count - 1 then
                Output.Add(ChildIndent + Items[i] + ',')
            else
                Output.Add(ChildIndent + Items[i]);
        end;
        
        Output.Add(Indent + ']');
        
        Result := Output.Text;
    finally
        Output.Free;
    end;
end;

// Function to write JSON to a file and return as string
function WriteJSONToFile(JSON: TStringList; FileName: String = ''): String;
var
    TempFile: String;
begin
    // Use provided filename or generate temp filename
    if Not(AnsiEndsStr('.json', LowerCase(FileName))) then
    begin
        TempFile := Path + 'temp_json_output.json';
    end
    else
    begin
        TempFile := FileName;
    end;
    
    try
        // Save to file
        JSON.SaveToFile(TempFile);
        
        // Load back the complete JSON data
        JSON.Clear;
        JSON.LoadFromFile(TempFile);
        Result := JSON.Text;
        
        // Clean up temporary file if auto-generated
        if (FileName = '') and FileExists(TempFile) then
            DeleteFile(TempFile);
    except
        Result := '{"error": "Failed to write JSON to file"}';
    end;
end;

// Locale-safe StrToFloat: normalizes '.' to the system decimal separator before parsing.
// Mirrors AddJSONNumber which does the reverse on output (fwolter PR #3).
function SafeStrToFloat(S: String): Double;
begin
    S := StringReplace(S, '.', DecimalSeparator, REPLACEALL);
    Result := StrToFloat(S);
end;

// Helper function to add a simple property to a JSON object
procedure AddJSONProperty(List: TStringList; Name: String; Value: String; IsString: Boolean = True);
begin
    List.Add(JSONPairStr(Name, Value, IsString));
end;

// Helper to add a numeric property
procedure AddJSONNumber(List: TStringList; Name: String; Value: Double);
begin
    List.Add(JSONPairStr(Name, StringReplace(FloatToStr(Value), ',', '.', REPLACEALL), False));
end;

// Helper to add an integer property
procedure AddJSONInteger(List: TStringList; Name: String; Value: Integer);
begin
    List.Add(JSONPairStr(Name, IntToStr(Value), False));
end;

// Helper to add a boolean property
procedure AddJSONBoolean(List: TStringList; Name: String; Value: Boolean);
begin
    if Value then
        List.Add(JSONPairStr(Name, 'true', False))
    else
        List.Add(JSONPairStr(Name, 'false', False));
end;
