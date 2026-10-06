{ AltiumMaskCache.pas

  Reports what Altium makes of the mask-expansion cache state on every pad of a
  library, and re-saves the library so the on-disk result can be compared with what
  was handed in. Pads only: the cache record is reached through IPCB_Pad, and the
  tri-state it carries is the same one a via stores.

  The mask-expansion mode is Altium's TCacheState = (eCacheInvalid, eCacheValid,
  eCacheManual) — ordinals 0/1/2, taken from the Advpcb.dll RTTI. It says whether a
  *cached* expansion is authoritative, so a primitive claiming eCacheValid asserts
  that its stored number is a rule result Altium should honour verbatim. This script
  exists to establish whether Altium acts on that claim or overwrites it, which
  decides whether writing the wrong state is merely untidy or actually changes the
  mask a fabricator receives.

  Reads one absolute .PcbLib path per line from the bridge request file and writes a
  JSON array to the response file: one entry per library, each carrying every pad's
  cache state as Altium reports it after the load, plus the path of the Altium
  re-saved copy.

  The cache state is reported through Ord() rather than compared against the enum
  identifiers. eCacheManual is proven to resolve in DelphiScript; eCacheInvalid and
  eCacheValid are known only from the RTTI, and an identifier that does not resolve
  aborts the whole script at COMPILE time, where a try/except cannot help. Ord()
  keeps the reading numeric and the script compilable.

  The RunScript launch mechanism and the file-based request/response bridge are
  adapted from coffeenmusic/altium-mcp (MIT) — https://github.com/coffeenmusic/altium-mcp

  Run via:
    X2.EXE -RScriptingSystem:RunScript(ProjectName="...\AltiumMaskCache.PrjScr"^|ProcName="AltiumMaskCache>Run") }

const
    BRIDGE_DIR = 'C:\Users\Public\altium_designer_mcp\';

// Escapes a string for embedding in JSON. Non-ASCII goes out as \uXXXX from Ord():
// concatenating a non-ANSI character into the response flattens it to '?', which
// would corrupt the very paths this reports.
function JsonEscape(const S : String) : String;
var
    i, C : Integer;
begin
    Result := '';
    for i := 1 to Length(S) do
    begin
        C := Ord(S[i]);
        if (C > 126) or (C < 32) then Result := Result + '\u' + IntToHex(C, 4)
        else if S[i] = '\' then Result := Result + '\\'
        else if S[i] = '"' then Result := Result + '\"'
        else Result := Result + S[i];
    end;
end;


{ Every pad and via in the open PcbLib, as a JSON array of cache-state readings.
  Coordinates are reported in Altium's internal units so the caller can compare
  them exactly against the bytes it wrote, without a mils round-trip. }
function CacheStates : String;
var
    PcbLib   : IPCB_Library;
    PcbIter  : IPCB_LibraryIterator;
    PcbComp  : IPCB_LibComponent;
    GIter    : IPCB_GroupIterator;
    Prim     : IPCB_Primitive;
    Pad      : IPCB_Pad;
    Cache    : TPadCache;
    First    : Boolean;
    CompName : String;
begin
    Result := '[';
    First  := True;
    try
        PcbLib := PCBServer.GetCurrentPCBLibrary;
        if PcbLib = nil then
        begin
            Result := Result + ']';
            Exit;
        end;

        PcbIter := PcbLib.LibraryIterator_Create;
        PcbIter.SetState_FilterAll;
        PcbComp := PcbIter.FirstPCBObject;
        while PcbComp <> nil do
        begin
            CompName := PcbComp.Name;

            GIter := PcbComp.GroupIterator_Create;
            GIter.AddFilter_ObjectSet(MkSet(ePadObject));
            Prim := GIter.FirstPCBObject;
            while Prim <> nil do
            begin
                Pad   := Prim;
                Cache := Pad.GetState_Cache;

                if not First then Result := Result + ',';
                First := False;
                Result := Result +
                    '{"component":"' + JsonEscape(CompName) + '"' +
                    ',"kind":"pad"' +
                    ',"designator":"' + JsonEscape(Pad.Name) + '"' +
                    ',"solder_valid":'  + IntToStr(Ord(Cache.SolderMaskExpansionValid)) +
                    ',"solder_coord":'  + IntToStr(Cache.SolderMaskExpansion) +
                    ',"paste_valid":'   + IntToStr(Ord(Cache.PasteMaskExpansionValid)) +
                    ',"paste_coord":'   + IntToStr(Cache.PasteMaskExpansion) + '}';

                Prim := GIter.NextPCBObject;
            end;
            PcbComp.GroupIterator_Destroy(GIter);

            PcbComp := PcbIter.NextPCBObject;
        end;
        PcbLib.LibraryIterator_Destroy(PcbIter);
    except
    end;
    Result := Result + ']';
end;


procedure Run;
var
    RequestFile, ResponseFile : String;
    Requests, Response : TStringList;
    Json, Path, Detail, States, SavedAs : String;
    i, Emitted : Integer;
    Doc : IServerDocument;
    Opened, Saved : Boolean;
begin
    RequestFile  := BRIDGE_DIR + 'maskcache_request.txt';
    ResponseFile := BRIDGE_DIR + 'maskcache_response.json';
    if not DirectoryExists(BRIDGE_DIR) then ForceDirectories(BRIDGE_DIR);

    Requests := TStringList.Create;
    Response := TStringList.Create;
    try
        if not FileExists(RequestFile) then
        begin
            Response.Text := '{"error":"no request file at ' + JsonEscape(RequestFile) + '"}';
            Response.SaveToFile(ResponseFile);
            Exit;
        end;
        Requests.LoadFromFile(RequestFile);

        Json := '[';
        Emitted := 0;
        for i := 0 to Requests.Count - 1 do
        begin
            Path := Trim(Requests[i]);
            if Path = '' then Continue;

            Opened  := False;
            Saved   := False;
            States  := '[]';
            SavedAs := '';
            Detail  := '';

            if not FileExists(Path) then
                Detail := 'file not found'
            else
            begin
                try
                    Doc := Client.OpenDocument('PCBLIB', Path);
                    if Doc <> nil then
                    begin
                        Client.ShowDocument(Doc);
                        Opened := True;
                        Detail := 'opened';
                        States := CacheStates;

                        // Re-save under a sibling name. Comparing those bytes with
                        // the handed-in file shows whether Altium accepts the cache
                        // states as written or replaces them with its own.
                        // IServerDocument has no DoFileSaveAs; DoSafeChangeFileNameAndSave
                        // is the "Save As to a path" call (second arg is the kind).
                        SavedAs := ChangeFileExt(Path, '') + '_Altium.PcbLib';
                        Doc.DoSafeChangeFileNameAndSave(SavedAs, 'PCBLIB');
                        Saved  := FileExists(SavedAs);
                        if Saved then Detail := 'opened and re-saved'
                        else Detail := 'opened; re-save produced no file';
                    end
                    else
                        Detail := 'Altium OpenDocument returned nil (could not parse)';
                except
                    Detail := 'exception while opening or saving';
                end;
            end;

            if Emitted > 0 then Json := Json + ',';
            Json := Json + '{"file":"' + JsonEscape(Path) + '","opened":';
            if Opened then Json := Json + 'true' else Json := Json + 'false';
            Json := Json + ',"saved":';
            if Saved then Json := Json + 'true' else Json := Json + 'false';
            Json := Json + ',"saved_as":"' + JsonEscape(SavedAs) + '"';
            Json := Json + ',"detail":"' + JsonEscape(Detail) + '"';
            Json := Json + ',"primitives":' + States + '}';
            Inc(Emitted);
        end;
        Json := Json + ']';

        Response.Text := Json;
        Response.SaveToFile(ResponseFile);
    finally
        Requests.Free;
        Response.Free;
    end;
end;
