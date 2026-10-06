# 1. Servers: `SchServer`, `PCBServer`, `Client`

The global servers are the entry points to the object model. They are predefined
identifiers, available in any DelphiScript unit without construction. From a
server you fetch the active document, create objects with its factory, and reach
the sub-managers (`ProcessControl`, `RobotManager`) that bracket edits so the
editor commits and repaints.

---

## 1.1 `SchServer`: the schematic server

`SchServer` is the schematic-editor server (`ISch_ServerInterface`). It owns the
active schematic document, the object factory for creating `ISch_*` primitives,
and the notification/transaction managers.

```pascal
// Canonical open: get the active sheet, bail if there is no schematic focused.
Var SchDoc : ISch_Document;
Begin
    If SchServer = Nil Then Exit;
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then Exit;
End;
```

### Documents

**`GetCurrentSchDocument : ISch_Document`**
Returns the schematic document that currently has editor focus. The result is
either an ordinary sheet (`.SchDoc`) or a schematic library (`.SchLib`): tell
them apart with `ObjectId = eSchLib`. Returns `Nil` when the focused document is
not a schematic (a PCB is active, or nothing is open), so every caller guards for
`Nil` first.

**`GetSchDocumentByPath(FullPath : String) : ISch_Document`**
Returns the already-open schematic whose file path matches `FullPath`, or `Nil`
if that file is not open as a schematic. Use it to operate on a specific sheet by
path rather than relying on which one has focus.

### Object creation and destruction

**`SchObjectFactory(ObjectId : TObjectId, CreationMode) : ISch_GraphicalObject`**
Creates a new, unparented schematic object of kind `ObjectId`, e.g.
`eSchComponent`, `ePin`, `eRectangle`, `eLine`, `eArc`, `eWire`, `eNetLabel`,
`ePowerObject`, `eParameter`, `eImplementation`. `CreationMode` is normally
`eCreate_Default`. The returned object exists only in memory until you add it to
a container with the container's `AddSchObject` / `AddSchComponent`; if you never
add it, free it with `DestroySchObject`.

```pascal
Var Pin : ISch_Pin;
Begin
    Pin := SchServer.SchObjectFactory(ePin, eCreate_Default);
    Pin.Designator := '1';
    Pin.Location.X := MilsToCoord(0);
    Pin.Location.Y := MilsToCoord(100);
    Component.AddSchObject(Pin);          // bind it to a component
End;
```

**`DestroySchObject(Obj : ISch_GraphicalObject)`**
Frees an object obtained from `SchObjectFactory` that was *not* added to a
document. Do not call it on an object that is already owned by a sheet/component
(remove it with the container's `RemoveSchObject` instead).

### Library metadata (the fast path)

**`CreateLibCompInfoReader(LibFullPath : String) : ISch_LibCompInfoReader`**
Opens a metadata reader over a `.SchLib` that enumerates its symbol entries
(name, alias, part-count, description) without loading each symbol: much faster
than instantiating every component, and the correct way to *list* a library
(`SchIterator` walks placed objects on a sheet, not library entries). Call
`ReadAllComponentInfo`, read `NumComponentInfos` / `ComponentInfos[I]`, then free
the reader.

```pascal
Var Reader : ISch_LibCompInfoReader; I : Integer;
Begin
    Reader := SchServer.CreateLibCompInfoReader(LibPath);
    Reader.ReadAllComponentInfo;
    For I := 0 To Reader.NumComponentInfos - 1 Do
        ShowMessage(Reader.ComponentInfos[I].CompName);
    SchServer.DestroyCompInfoReader(Reader);
End;
```

**`DestroyCompInfoReader(Reader : ISch_LibCompInfoReader)`**
Frees a reader created by `CreateLibCompInfoReader`. Always pair the two.

### Placement

**`LoadComponentFromLibrary(LibReference : String, LibFullPath : String, Doc : ISch_Document) : ISch_Component`**
Loads symbol `LibReference` from the library at `LibFullPath`, ready to place
onto `Doc`, and returns the component. Follow with `Doc.AddSchObject`, then
`MoveToXY` and `SetState_Orientation` to position it. Prefer this over
`Doc.PlaceSchComponent`, which raises modal pickers and leaves the position
inconsistent.

### Sub-managers

**`ProcessControl : IProcessControl`**  *(property)*
The edit-transaction manager. Bracket any mutation between
`ProcessControl.PreProcess(Doc, '')` and `ProcessControl.PostProcess(Doc, '')` so
the document is marked dirty and the view re-renders. The second argument is a
**string** (often `''`), never an object.

```pascal
SchServer.ProcessControl.PreProcess(SchDoc, '');
Try
    Obj.SetState_...;                     // your edits
Finally
    SchServer.ProcessControl.PostProcess(SchDoc, '');
End;
```

**`RobotManager : ISch_RobotManager`**  *(property)*
The notification bus. Commit a per-object change or register a new object by
broadcasting a message:
`RobotManager.SendMessage(Obj.I_ObjectAddress, c_BroadCast, SCHM_BeginModify, c_NoEventData)`
… mutate … `SCHM_EndModify`. A whole new component is registered with
`SendMessage(Nil, Nil, SCHM_PrimitiveRegistration, Component.I_ObjectAddress)`.

**`FontManager : ISch_FontManager`**  *(property)*
Resolves font ids and specifications for labels and text objects (look up a font
id, read its size/style).

---

## 1.2 `PCBServer`: the board server

`PCBServer` is the PCB-editor server (`IPCB_ServerInterface`). It owns the active
board / library, the primitive and rule factories, and the board transaction
(`PreProcess`/`PostProcess`).

```pascal
Var Board : IPCB_Board; Pad : IPCB_Pad;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then Exit;
    PCBServer.PreProcess;
    Pad := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
    Pad.X := MilsToCoord(1000);  Pad.Y := MilsToCoord(1000);
    Board.AddPCBObject(Pad);
    PCBServer.PostProcess;
End;
```

### Documents and libraries

**`GetCurrentPCBBoard : IPCB_Board`**
Returns the focused `.PcbDoc`, or `Nil` if a board is not the active document.

**`GetPCBBoardByPath(FullPath : String) : IPCB_Board`**
Returns the open board whose file path matches `FullPath`, or `Nil`.

**`GetCurrentPCBLibrary : IPCB_Library`**
Returns the focused `.PcbLib`, or `Nil`. Its active footprint is
`.CurrentComponent` and the document behind it is `.Board` (used for
`AddPCBObject` and `FileName`).

### Object, rule and class factories

**`PCBObjectFactory(ObjectId : TObjectId, Dimension, CreationMode) : IPCB_Primitive`**
Creates a board primitive of kind `ObjectId`: `ePadObject`, `eTrackObject`,
`eViaObject`, `eArcObject`, `eTextObject`, `ePolyObject`, `eRegionObject`,
`eFillObject`. `Dimension` is normally `eNoDimension` and `CreationMode`
`eCreate_Default`. Add the result with the owner's `AddPCBObject`.

**`CreatePCBLibComp : IPCB_LibComponent`**
Creates a new empty footprint. Name it, then register it with
`PcbLib.RegisterComponent` and make it current with `PcbLib.CurrentComponent`.

**`PCBRuleFactory(RuleKind) : IPCB_Rule`**
Creates a design rule of `RuleKind` (e.g. `eRule_Clearance`, `eRule_MaxMinWidth`)
ready to configure (scopes, constraint values) and add to the board.

**`PCBClassFactoryByClassMember(MemberKind) : IPCB_ObjectClass`**
Creates a net / component class (e.g. `eClassMemberKind_Net`).

### Transactions and notifications

**`PreProcess`** / **`PostProcess`**
Open and close a board edit transaction. Unlike `SchServer.ProcessControl`,
these take **no document argument**: call them bare around board mutations.

**`SendMessageToRobots(Address, BroadcastKind, MessageId, EventData)`**
Broadcasts a board message. After adding a primitive that does not render until
reload, register it with
`PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast, PCBM_BoardRegisteration, Obj.I_ObjectAddress)`;
bracket a change with `PCBM_BeginModify` / `PCBM_EndModify`.

**`SystemOptions : IPCB_SystemOptions`**  *(property)*
Global PCB editor options: default units and primitive sizes.

---

## 1.3 `Client`: the application

`Client` (`IClient`) is the application shell: open and show documents, query
application state, dispatch processes. A "document" here is an `IServerDocument`
(the raw open file), distinct from the model `IDocument` of
[page 4](04-workspace-project-documents.md).

```pascal
Var SvrDoc : IServerDocument;
Begin
    SvrDoc := Client.OpenDocument('PCB', BoardPath);
    If SvrDoc <> Nil Then Client.ShowDocument(SvrDoc);
End;
```

### Documents

**`OpenDocument(Kind : String, FullPath : String) : IServerDocument`**
Opens the existing file at `FullPath` in the editor for document `Kind`
(`'SCH'`, `'PCB'`, `'PCBLIB'`, `'SCHLIB'`, …) and returns its server document.

**`OpenNewDocument(Kind, Name, KernelFileName, …) : IServerDocument`** *(as called)*
Creates a new blank document of `Kind` with display name `Name`. Used to spin up
a `.PcbDoc` before an ECO, a fresh `.SchDoc`, etc.

**`GetDocumentByPath(FullPath : String) : IServerDocument`**
Returns the open server document at `FullPath`, or `Nil` if it is not open.

**`ShowDocument(Doc : IServerDocument)`** / **`ShowDocumentDontFocus(Doc)`**
Brings `Doc` to the front, with or without taking keyboard focus.

**`IsDocumentOpen(FullPath : String) : Boolean`**
Whether the file at `FullPath` is currently open.

There is no `GetDocumentCount` and no `GetDocument(I)` on `Client`, despite
them reading like the obvious pair. Both raise "Undeclared identifier" at
runtime, which `Try/Except` cannot catch and which stops the polling loop.
`scripts/altium/lint.py` rejects them by name (`KNOWN_WRONG_METHOD_NAMES`).

To enumerate open documents, walk the project instead:
`Project.DM_LogicalDocumentCount` with `Project.DM_LogicalDocuments(I)`, then
`Client.GetDocumentByPath` for the `IServerDocument`.

**`GetCurrentView : IServerDocumentView`**
The currently focused editor view.

### Application state and dispatch

**`GetProductVersion : WideString`**
The Altium Designer product-version string.

**`IsQuitting : Boolean`**
True once a shutdown is in progress. Poll it from a long-running or background
loop so the script stops cleanly when Altium is closing.

**`SendMessage(…)`**
Dispatches a client message / process invocation.

---

## 1.4 `IntegratedLibraryManager`

The installed-libraries manager (a predefined global).

**`InstallLibrary(FullPath : String)`**: adds the library at `FullPath` to the
installed/available set.
**`UnInstallLibrary(FullPath : String)`**: removes it.
