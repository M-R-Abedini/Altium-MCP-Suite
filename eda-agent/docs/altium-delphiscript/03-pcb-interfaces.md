# 3. PCB interfaces (`IPCB_*`)

The board object model hangs off `PCBServer` ([page 1](01-servers.md)). A board
(`IPCB_Board`) or footprint (`IPCB_LibComponent`) owns primitives; every
primitive descends from `IPCB_Primitive`. Collections are walked with the board,
spatial, or group iterators. Edits are bracketed by `PCBServer.PreProcess` /
`PostProcess`: **no document argument**, unlike the schematic
`ProcessControl`. All geometry is in internal units (`MilsToCoord` /
`CoordToMils`); angles are degrees (`Double`).

```pascal
// Canonical board mutation: open the board, bracket, add a primitive, register.
Var Board : IPCB_Board; Track : IPCB_Track;
Begin
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then Exit;
    PCBServer.PreProcess;
    Try
        Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
        Track.X1 := MilsToCoord(0);    Track.Y1 := MilsToCoord(0);
        Track.X2 := MilsToCoord(500);  Track.Y2 := MilsToCoord(0);
        Track.Width := MilsToCoord(10);
        Track.Layer := eTopLayer;
        Board.AddPCBObject(Track);
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
            PCBM_BoardRegisteration, Track.I_ObjectAddress);
    Finally
        PCBServer.PostProcess;
    End;
End;
```

---

## 3.1 `IPCB_Board`: a `.PcbDoc`

`PCBServer.GetCurrentPCBBoard` returns the focused board; `PcbLib.Board` is the
board document behind a library. It owns every primitive, the layer stack, the
board outline, and the iterators.

### Objects and iteration

**`BoardIterator_Create : IPCB_BoardIterator`**
Creates an iterator over every object on the board (§3.7). Configure it with
`AddFilter_ObjectSet` / `AddFilter_LayerSet`, then walk with
`FirstPCBObject` / `NextPCBObject`. Always pair with `BoardIterator_Destroy` in
a `Finally`.

**`BoardIterator_Destroy(Iter : IPCB_BoardIterator)`**
Frees an iterator from `BoardIterator_Create`. Leaking iterators corrupts later
iteration, so destroy unconditionally.

**`SpatialIterator_Create : IPCB_SpatialIterator`**
Creates an iterator restricted to a rectangular region (set with
`AddFilter_Area`), for proximity queries such as clearance checks: far cheaper
than a full board scan. Pair with `SpatialIterator_Destroy`.

**`SpatialIterator_Destroy(Iter : IPCB_SpatialIterator)`**
Frees a spatial iterator.

**`AddPCBObject(Obj : IPCB_Primitive)`**
Adds a primitive built by `PCBObjectFactory` to the board. For objects that do
not render until reload, follow with the `PCBM_BoardRegisteration` broadcast
(see the example above).

**`RemovePCBObject(Obj : IPCB_Primitive)`**
Removes a primitive from the board. Collect the objects to remove into a
`TInterfaceList` during iteration and delete *after* the iterator is destroyed:
removing mid-walk invalidates the iterator.

**`GetPcbComponentByRefDes(RefDes : String) : IPCB_Component`**
Returns the placed component with designator `RefDes` (e.g. `'U1'`), or `Nil`.
The direct way to reach one component without iterating.

**`FindDominantRuleForObject(Obj, RuleKind) : IPCB_Rule`**
Returns the highest-priority design rule of `RuleKind` that applies to `Obj`
(e.g. the governing clearance or width rule for a given track), or `Nil`. Use it
to read the constraint actually in force at a primitive rather than guessing
which rule wins by priority.

**`PrimPrimDistance(P1, P2 : IPCB_Primitive) : TCoord`**
Returns the minimum copper-to-copper distance between two primitives, in
internal units. The measurement an audit compares against a clearance rule.

### Geometry, origin and units

**`BoardOutline : IPCB_BoardOutline`**
The board shape (§3.10): its vertices/segments, bounding rectangle, and
`Rebuild`/`Validate`.

**`XOrigin : TCoord`** / **`YOrigin : TCoord`**  *(properties)*
The board's relative origin. Subtract from absolute coordinates to report
positions relative to the user-set origin.

**`SnapGridSizeX : TCoord`** / **`SnapGridSizeY : TCoord`**  *(properties)*
The current snap-grid spacing.

**`DisplayUnit : TUnit`**  *(property)*
The board's display unit (`eImperial` / `eMetric`): read it to format reported
coordinates in the unit the user is working in.

### Layers

**`LayerStack_V7 : IPCB_LayerStack_V7`**  *(property)*
The layer stackup (§3.9). The `_V7` form is the current interface; the bare
`LayerStack` exists for backward compatibility but prefer `_V7`.

**`LayerIsDisplayed[Layer : TLayer] : Boolean`**  *(indexed property)*
Whether a layer is currently shown. Read it to honour the user's visibility when
rendering; set it to force a layer visible before a screenshot.

**`LayerIsUsed[Layer : TLayer] : Boolean`**  *(indexed property)*
Whether a layer carries any objects / is enabled in the stack: lets an exporter
skip empty layers.

### Repaint and handles

**`GraphicallyInvalidate`**
Marks the board view dirty so the next refresh repaints it. Call after a batch
of edits.

**`ViewManager_FullUpdate`** / **`GraphicalView_ZoomRedraw`**
Force a full re-render / a zoom-fit redraw of the board view.

**`I_ObjectAddress : Integer`**  *(property)*
The board's handle, passed as the broadcast address in
`PCBServer.SendMessageToRobots(Board.I_ObjectAddress, …)`.

---

## 3.2 `IPCB_Primitive`: base of every board object

Every board object (pad, track, via, arc, text, polygon, region, component)
descends from `IPCB_Primitive` and shares these members. The concrete kind is
read from `ObjectId`; access subtype members by assigning into a typed local
after the check (there are no inline casts).

```pascal
Prim := Iterator.FirstPCBObject;
While Prim <> Nil Do
Begin
    If Prim.ObjectId = ePadObject Then
    Begin
        Pad := Prim;                       // narrow to IPCB_Pad
        ReportPad(Pad.Name, Pad.X, Pad.Y);
    End;
    Prim := Iterator.NextPCBObject;
End;
```

**`ObjectId : TObjectId`**  *(property)*
The kind tag: `ePadObject`, `eTrackObject`, `eViaObject`, `eArcObject`,
`eTextObject`, `eComponentObject`, `ePolyObject`, `eRegionObject`,
`eFillObject` ([enums](05-enums.md)). The discriminator for narrowing.

**`Layer : TLayer`**  *(property)*
The object's layer (§3.9 / [enums](05-enums.md)). For a track/pad it is the
copper layer; for text/silk an overlay layer; `eMultiLayer` for a through pad.

**`Net : IPCB_Net`**  *(property)*
The net the object belongs to (§3.5), or `Nil` if unassigned. Assign by calling
`Net.AddPCBObject(Prim)`, not by writing this property.

**`InNet : Boolean`**  *(property)*
Whether the object is assigned to a net: the cheap guard before reading `Net`.

**`InComponent : Boolean`**  *(property)*
Whether the primitive belongs to a placed component (true for a component's
pads), versus a free board primitive.

**`Component : IPCB_Component`**  *(property)*
The owning component when `InComponent` is true, else `Nil`.

**`BoundingRectangle : TCoordRect`**  *(property)*
The object's extent in internal units, for hit-testing, overlap and extent
reports.

**`Moveable : Boolean`**  *(property)*
Whether the object may be moved (false when locked).

**`Selected : Boolean`**  *(property)*
The selection state: set it to drive a selection-based process, read it to
collect the user's selection.

**`Detail : String`**  *(property)*
A human-readable description of the object (kind + key geometry), useful in
audit output.

**`BeginModify`** / **`EndModify`**
Bracket a property change on an existing primitive so the editor re-renders it,
the PCB analogue of the schematic `SCHM_BeginModify`/`EndModify` broadcast.
`Prim.BeginModify; Prim.Width := …; Prim.EndModify;`

**`GraphicallyInvalidate`**
Marks just this object's region dirty for repaint (lighter than the whole
board).

**`I_ObjectAddress : Integer`**  *(property)*
The primitive's handle, used as the event-data payload when registering it with
`PCBM_BoardRegisteration`.

**Testpoint flags**: **`IsTestpoint_Top` / `IsTestpoint_Bottom`** and
**`IsAssyTestpoint_Top` / `IsAssyTestpoint_Bottom : Boolean`** mark a pad/via as a
fabrication or assembly testpoint on the given side.

---

## 3.3 Copper / routing primitives

> Build each with
> `PCBServer.PCBObjectFactory(eXxxObject, eNoDimension, eCreate_Default)`, set
> its properties, then `Owner.AddPCBObject`. Watch the size-accessor divergence:
> a pad uses `TopXSize`/`TopYSize`, a track uses `Width`, an arc uses
> `LineWidth`, three names for "how wide".

### `IPCB_Pad`

**`Name : String`**  *(property)*
The pad designator/number (`'1'`, `'A1'`), matched against the schematic pin.

**`X : TCoord`** / **`Y : TCoord`**  *(properties)*
The pad centre, in internal units.

**`TopXSize : TCoord`** / **`TopYSize : TCoord`**  *(properties)*
The pad copper size on the top layer: **not** `Width`/`Height`. These are the
top-layer entries of the per-layer pad stack; a simple SMD/through pad reads
them as its size.

**`TopShape : TShape`**  *(property)*
The pad shape: `eRounded`, `eRectangular`, `eOctagonal`, `eRoundRectangle`
([enums](05-enums.md)).

**`HoleSize : TCoord`**  *(property)*
The drill diameter: `0` = SMD pad, `> 0` = through-hole.

**`HoleType : TExtendedHoleType`** / **`HoleWidth : TCoord`** / **`HoleRotation : Double`**  *(properties)*
The hole geometry for slotted/square holes (round, square, slot): width and
rotation apply to non-round holes.

**`Plated : Boolean`**  *(property)*
Whether the hole is plated (PTH) or not (NPTH mounting hole).

**`Rotation : Double`**  *(property)*
Pad rotation in degrees.

**`Mode : TPadMode`**  *(property)*
Simple / top-middle-bottom / full pad-stack mode. `eCacheManual` style stacks
read/write through the cache record below.

**`GetState_Cache : TPadCache`** / **`SetState_Cache(Cache : TPadCache)`**
Read / write the full pad-stack cache record (per-layer sizes, shapes, mask
expansions). Read-modify-write: `C := Pad.GetState_Cache; C.… := …;
Pad.SetState_Cache(C);`.

**`IsSurfaceMount : Boolean`** / **`IsPadRemoved[Layer] : Boolean`**  *(properties)*
Whether the pad is SMD; whether copper is removed on a given layer of the stack.

**`Component : IPCB_Component`** / **`InComponent : Boolean`**  *(properties)*
The owning component when the pad is part of a placed footprint.

**`BoundingRectangleOnLayer(Layer) : TCoordRect`**
The pad's extent on a specific layer (a stack pad differs per layer).

### `IPCB_Track`

**`X1` / `Y1` / `X2` / `Y2 : TCoord`**  *(properties)*
The two endpoints, in internal units.

**`Width : TCoord`**  *(property)*
The track width: a coordinate, unlike the schematic line-width enum.

**`Layer : TLayer`** / **`Net : IPCB_Net`** (base, §3.2): the copper layer and net.

```pascal
Track := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
Track.X1 := MilsToCoord(0);   Track.Y1 := MilsToCoord(0);
Track.X2 := MilsToCoord(0);   Track.Y2 := MilsToCoord(300);
Track.Width := MilsToCoord(8);  Track.Layer := eTopLayer;
Board.AddPCBObject(Track);
```

### `IPCB_Via`

**`X : TCoord`** / **`Y : TCoord`**  *(properties)*
The via centre.

**`Size : TCoord`**  *(property)*
The via pad diameter.

**`HoleSize : TCoord`**  *(property)*
The drill diameter.

**`LowLayer : TLayer`** / **`HighLayer : TLayer`**  *(properties)*
The layer span of the via (also exposed as **`StartLayer` / `StopLayer`**). A
through via spans `eTopLayer`..`eBottomLayer`; a blind/buried via spans inner
layers.

**`SizeOnLayer[Layer] : TCoord`**  *(indexed property)*
The via pad diameter on a specific layer (for tapered stacks).

**`IntersectLayer(Layer) : Boolean`** / **`IsConnectedToPlane(Layer) : Boolean`**
Whether the via passes through / connects to a plane on a given layer.

**`SolderMaskExpansion : TCoord`** / **`SolderMaskExpansionFromHoleEdge : Boolean`**  *(properties)*
The mask opening size and whether it is measured from the hole edge: set both
to tent or open a via.

**`GetState_IsTenting_Top : Boolean`** / **`GetState_IsTenting_Bottom : Boolean`**
Whether the via is tented (mask-covered) on each side.

### `IPCB_Arc`

**`XCenter : TCoord`** / **`YCenter : TCoord`**  *(properties)*
The arc centre.

**`Radius : TCoord`**  *(property)*
The arc radius.

**`StartAngle : Double`** / **`EndAngle : Double`**  *(properties)*
The sweep, in degrees (CCW). A full circle is `0`..`360`.

**`LineWidth : TCoord`**  *(property)*
The arc stroke width: an arc uses `LineWidth`, a track uses `Width` for the same
concept.

**`Layer : TLayer`** / **`Net : IPCB_Net`** (base): the layer and net.

### `IPCB_Text`

**`Text : String`**  *(property)*
The string drawn. For a designator override use the component's name; for free
silk text set this directly. **`UnderlyingString`** is the raw (pre-special-string)
text.

**`XLocation : TCoord`** / **`YLocation : TCoord`**  *(properties)*
The text anchor position.

**`Size : TCoord`**  *(property)*
The text height (character size).

**`Width : TCoord`**  *(property)*
The stroke width of the (vector) glyphs.

**`Rotation : Double`**  *(property)*
Text rotation in degrees.

**`Layer : TLayer`**  *(property)*
Usually `eTopOverlay` / `eBottomOverlay` for silkscreen.

**`MirrorFlag : Boolean`**  *(property)*
Whether the text is mirrored (set automatically for bottom-side text; auditing
it catches readable-from-wrong-side silk).

**`UseTTFonts : Boolean`**  *(property)*
Stroke font (false) vs TrueType (true).

**`IsHidden : Boolean`**  *(property)*
Whether the text is hidden.

> **Registration trap (text especially):** `AddPCBObject` alone may not register
> a new primitive with the placement editor: it can appear only after
> save+reload. After adding, broadcast
> `PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
> PCBM_BoardRegisteration, Obj.I_ObjectAddress)`.

---

## 3.4 Pours

### `IPCB_Polygon`

**`Name : String`**  *(property)*: the polygon name.

**`PolyHatchStyle : TPolygonHatchStyle`**  *(property)*
The fill style: `ePolySolid`, `ePolyHatch90/45`, `ePolyNoHatch`
([enums](05-enums.md)).

**`PourOver : TPolygonPourOver`**  *(property)*
Whether the pour covers same-net objects or pours around them.

**`IsSolid : Boolean`**  *(property)*: solid vs hatched fill.

**`LineWidth : TCoord`**  *(property)*: the track width used to build the pour.

**`Layer : TLayer`** / **`Net : IPCB_Net`**  *(properties)*: the copper layer and net.

**`PointCount : Integer`** / **`GetState_VerticesCount : Integer`** / **`VerticesCount : Integer`**  *(properties)*
The vertex count of the outline.

**`Vertex[I]`** / **`GetState_Vertex(I)`** / **`Segments[I]`**
Access individual outline vertices / segments (a segment is a line or arc edge).

**`Rebuild`**
Re-pours the polygon after the board or its outline changes: call it after
moving copper underneath, or the fill goes stale.

### `IPCB_Region`

**`MainContour : IPCB_Contour`**  *(property)*
The boundary geometry (a contour of points). Read it to inspect a region shape.

**`SetOutlineContour(Contour : IPCB_Contour)`**
Sets the region's outline from a contour you build: the way to author a
free-form copper/keepout region.

**`Layer : TLayer`** / **`Net : IPCB_Net`**  *(properties)*: the layer and net.

**`BoundingRectangle : TCoordRect`**  *(property)*: the region extent.

---

## 3.5 Nets and components

### `IPCB_Net`

**`Name : String`**  *(property)*
The net name (`'GND'`, `'VCC'`).

**`RoutedLength : TCoord`**  *(property)*
The total routed copper length of the net: read for length-matching/tuning
reports.

**`IsHighlighted : Boolean`**  *(property)*
The net's highlight state (set to drive cross-probe highlighting).

**`AddPCBObject(Obj : IPCB_Primitive)`**
Assigns a primitive to this net: the correct way to set a track/via/pad's net
(do not write `Prim.Net`).

**`GroupIterator_Create : IPCB_GroupIterator`** / **`GroupIterator_Destroy(Iter)`**
Iterate the net's member primitives (every pad/track/via on the net). Configure
and walk as in §3.7; always destroy.

```pascal
Iter := Net.GroupIterator_Create;
Iter.AddFilter_ObjectSet(MkSet(eTrackObject));
Track := Iter.FirstPCBObject;
While Track <> Nil Do
Begin
    TotalLen := TotalLen + TrackLength(Track);
    Track := Iter.NextPCBObject;
End;
Net.GroupIterator_Destroy(Iter);
```

### `IPCB_Component`: a placed footprint

**`Name : IPCB_Text` / refdes**  *(property)*
The component designator object/string (`'U1'`); **`NameOn : Boolean`** toggles its
visibility.

**`Comment` / `CommentOn : Boolean`**  *(properties)*
The comment/value and its visibility.

**`Pattern : String`**  *(property)*
The footprint (pattern) name placed for this component.

**`Layer : TLayer`**  *(property)*
`eTopLayer` / `eBottomLayer`, which side the part sits on.

**`Rotation : Double`**  *(property)*: placement angle in degrees.

**`x : TCoord` / `y : TCoord`**  *(properties)*
The component reference position. Move with `MoveToXY`, not by writing these.

**`MoveToXY(X, Y : TCoord)`**
Moves the whole component (body + pads + designator) to an absolute position.

**`Moveable : Boolean`** / **`IsMirrored : Boolean`**  *(properties)*: lock and mirror state.

**`ChangeNameAutoposition(Mode)`**
Repositions the designator text to a standard side automatically: the
silkscreen-tidy operation.

**`SourceDesignator : String`** / **`SourceUniqueId : String`** / **`SourceFootprintLibrary : String`** / **`SourceLibraryName : String`**  *(properties)*
The schematic-linkage fields: the source designator, the unique id tying it to
the schematic part, and where the footprint came from. Auditing these catches
ECO mismatches.

**`LoadFromLibrary(…)`**
Loads/replaces the footprint pattern from a library.

**`GroupIterator_Create` / `GroupIterator_Destroy`**
Iterate the component's own primitives (its pads, silk, courtyard).

**`BoundingRectangle : TCoordRect`**  *(property)*: the placed footprint extent.

**`I_ObjectAddress : Integer`**  *(property)*: the handle for registration broadcasts.

---

## 3.6 Rules and violations

### `IPCB_Rule`

Built with `PCBServer.PCBRuleFactory(RuleKind)`, configured, then added to the
board.

**`Name : String`** / **`Comment : String`**  *(properties)*: identity.

**`RuleKind`** / **`Kind`**  *(properties)*
The rule type (`eRule_Clearance`, `eRule_MaxMinWidth`, …
[enums](05-enums.md)).

**`Enabled : Boolean`** / **`DRCEnabled : Boolean`**  *(properties)*
Whether the rule is active and whether DRC checks it.

**`Priority : Integer`**  *(property)*
The rule priority: when several rules match an object, the highest priority
wins (see `Board.FindDominantRuleForObject`).

**`Scope1Expression : String`** / **`Scope2Expression : String`**  *(properties)*
The query scopes the rule applies to (`'All'`, `'InNet(''GND'')'`, …). A
unary rule uses scope 1; a binary rule (clearance, diff-pair) uses both.

**`Descriptor : String`**  *(property)*: the human-readable rule descriptor.

**`Gap : TCoord`**  *(property)*: the clearance gap (for a clearance rule).

**`PreferedWidth : TCoord`** / **`PreferedHoleWidth : TCoord`**  *(properties)*
Kind-specific constraint values (width rule / hole-size rule). *(Altium spells
these with one "r".)*

**`NetScope`** / **`LayerKind`**  *(properties)*
The net-scope (`eNetScope_AnyNet`) and layer-kind
(`eRuleLayerKind_SameLayer`) qualifiers.

```pascal
Rule := PCBServer.PCBRuleFactory(eRule_Clearance);
Rule.Name := 'Clearance_HV';
Rule.Scope1Expression := 'InNet(''HV'')';
Rule.Scope2Expression := 'All';
Rule.Gap := MilsToCoord(40);
Board.AddPCBObject(Rule);
```

### `IPCB_Violation`

**`Rule : IPCB_Rule`**  *(property)*: the rule that was breached.

**`Name : String`** / **`Description : String`**  *(properties)*: the violation text.

**`DM_ShortDescriptorString` / `DM_LongDescriptorString : String`**  *(properties)*
The short / long descriptor strings (the message shown in the Messages panel).

**`DM_OwnerDocumentName : String`**  *(property)*: the document the violation is on.

**`Primitive1 : IPCB_Primitive`** / **`Primitive2 : IPCB_Primitive`**  *(properties)*
The one or two objects involved (the offending pair for a clearance violation).

**`Layer : TLayer`** / **`BoundingRectangle : TCoordRect`**  *(properties)*, where it is.

---

## 3.7 Iterators (`IPCB_BoardIterator` / `IPCB_SpatialIterator` / `IPCB_GroupIterator`)

The three iterators share one shape. A board iterator comes from
`Board.BoardIterator_Create`, a spatial one from `Board.SpatialIterator_Create`,
a group one from a net's or component's `GroupIterator_Create`. Each is freed by
its owner's matching `*_Destroy`, always in a `Finally`. Configure filters
before the first walk.

**`AddFilter_ObjectSet(MkSet(eXxxObject, …))`**
Restricts the walk to the given object kinds.

**`AddFilter_LayerSet(MkSet(eXxxLayer, …))`** / **`AddFilter_IPCB_LayerSet(LayerSet)`**
Restricts to the given layers. The two overloads are NOT interchangeable:
`AddFilter_LayerSet` takes a `MkSet(...)` value; `AddFilter_IPCB_LayerSet` takes
an `IPCB_LayerSet` *object* such as `LayerSet.SignalLayers` / `LayerSet.AllLayers`.
Passing a `LayerSet.*` object to `AddFilter_LayerSet` raises
`EVariantTypeCastError` ("Could not convert variant of type (Dispatch) into type
(String)").

**`AddFilter_Area(X1, Y1, X2, Y2 : TCoord)`**
Restricts a spatial iterator to a rectangular region.

**`AddFilter_Method(Method)`** / **`SetState_FilterAll`**
Set an alternative filtering method / clear all filters (walk everything).

**`FirstPCBObject : IPCB_Primitive`** / **`NextPCBObject : IPCB_Primitive`**
Walk the matches; `NextPCBObject` returns `Nil` at the end.

```pascal
Iter := Board.BoardIterator_Create;
Try
    Iter.AddFilter_ObjectSet(MkSet(eComponentObject));
    Iter.AddFilter_LayerSet(MkSet(eTopLayer, eBottomLayer));
    Iter.AddFilter_Method(eProcessAll);
    Comp := Iter.FirstPCBObject;
    While Comp <> Nil Do
    Begin
        ReportComponent(Comp);
        Comp := Iter.NextPCBObject;
    End;
Finally
    Board.BoardIterator_Destroy(Iter);
End;
```

---

## 3.8 Libraries and footprints

### `IPCB_Library`: a `.PcbLib`

`PCBServer.GetCurrentPCBLibrary` returns it.

**`CurrentComponent : IPCB_LibComponent`**  *(property)*
The active footprint being edited; **`SetState_CurrentComponent(Fp)`** sets it.

**`RegisterComponent(Fp : IPCB_LibComponent)`**
Adds a new footprint (from `PCBServer.CreatePCBLibComp`) to the library.

**`Board : IPCB_Board`**  *(property)*
The board document behind the library: pass it to `AddPCBObject` when building
a footprint's primitives, and read its `FileName`.

**`LibraryIterator_Create` / `LibraryIterator_Destroy`**
Enumerate the library's footprints.

```pascal
PcbLib := PCBServer.GetCurrentPCBLibrary;
Fp := PCBServer.CreatePCBLibComp;
Fp.Name := 'SOT23-3';
PcbLib.RegisterComponent(Fp);
PcbLib.SetState_CurrentComponent(Fp);
Pad := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
// … set pad geometry …
Fp.AddPCBObject(Pad);
PcbLib.Board.AddPCBObject(Pad);   // register against the underlying board too
```

### `IPCB_LibComponent`: a footprint

**`Name : String`** / **`Description : String`**  *(properties)*: identity.

**`Height : TCoord`**  *(property)*: the 3D body/component height.

**`AddPCBObject(Obj : IPCB_Primitive)`**
Adds a pad / track / arc / text to the footprint. Pair with adding to the
underlying `PcbLib.Board` plus the registration broadcast for it to render.

**`GroupIterator_Create` / `GroupIterator_Destroy`**
Iterate the footprint's primitives (pads, silk outline, courtyard).

---

## 3.9 Layer stack (`IPCB_LayerStack_V7` / `IPCB_LayerObject_V7`)

`Board.LayerStack_V7` returns the stack. Walk it from `FirstLayer` via
`NextLayer`, or index a specific layer with `LayerObject_V7[Layer]`.

**`FirstLayer : IPCB_LayerObject_V7`**
The first layer object in stack order.

**`NextLayer(L : IPCB_LayerObject_V7) : IPCB_LayerObject_V7`**
The next layer after `L`, or `Nil` at the end of the stack.

**`LayerObject_V7[Layer : TLayer] : IPCB_LayerObject_V7`**  *(indexed property)*
The layer object for a specific layer id: the direct accessor when you know the
layer.

**`InsertLayer(…)` / `RemoveFromStack(L)`**
Add / remove a copper or dielectric layer from the stack.

### `IPCB_LayerObject_V7`, one layer

**`Name : String`**  *(property)*: the layer name (`'Top Layer'`, `'GND'`).

**`LayerID : TLayer`**  *(property)*: the layer's enum id.

**`CopperThickness : TCoord`**  *(property)*: the copper weight as a thickness.

**`Dielectric`**  *(sub-record)*
The dielectric beneath the copper layer, with fields **`DielectricType`**
(`eNoDielectric`, `eCore`, `ePrePreg`, `eSurfaceMaterial`),
**`DielectricHeight : TCoord`**, **`DielectricConstant : Double`**, and
**`DielectricMaterial : String`**. Read for an impedance/stackup report; assign
back through the sub-record to edit it.

```pascal
LayerStack := Board.LayerStack_V7;
LayerObj := LayerStack.FirstLayer;
While LayerObj <> Nil Do
Begin
    Report(LayerObj.Name, CoordToMils(LayerObj.CopperThickness));
    If LayerObj.Dielectric.DielectricType <> eNoDielectric Then
        Report(LayerObj.Dielectric.DielectricConstant,
               CoordToMils(LayerObj.Dielectric.DielectricHeight));
    LayerObj := LayerStack.NextLayer(LayerObj);
End;
```

---

## 3.10 Board outline (`IPCB_BoardOutline`)

`Board.BoardOutline` returns the board shape, a closed polygon of segments.

**`PointCount : Integer`**  *(property)*: the vertex count of the outline.

**`Segments[I] : TPolySegment`**  *(indexed property)*, each edge (line or arc).

**`BoundingRectangle : TCoordRect`**  *(property)*: the board extent.

**`PrimitiveInsidePoly(Prim) : Boolean`**
Whether a primitive lies inside the board outline: the test behind a
"components outside the board" audit.

**`Validate` / `Invalidate` / `Rebuild`**
Mark the outline valid / dirty / rebuild it after editing vertices.
