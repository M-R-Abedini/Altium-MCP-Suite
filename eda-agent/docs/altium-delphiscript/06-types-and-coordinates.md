# 6. Value types, coordinates & constants

The records, coordinate system, and broadcast constants the API hands around.

---

## 6.1 Coordinates: the internal unit

PCB and schematic geometry is stored as integer **internal units**, where

```
1 mil  =  10000 internal units      (1 internal unit = 1/10000 mil ≈ 2.54 nm)
```

Convert at every boundary; never pass mils or millimetres straight into a
geometry property:

- **`MilsToCoord(Mils) : TCoord`**: mils → internal units (the workhorse;
  used wherever an `X`/`Y`/`Width`/`Size`/`Radius` is set).
- **`CoordToMils(Coord) : Double`**: internal units → mils (for reading geometry
  back out).
- **`MMToCoord(MM) : TCoord`**: millimetres → internal units.
- **`CoordToMM(Coord) : Double`**: internal units → millimetres (reading
  geometry back out in metric, e.g. a stackup/dielectric height report).

- **`TCoord`**: the integer internal-unit coordinate type.

---

## 6.2 Geometry records

- **`TLocation`**: a point, fields `X` / `Y : TCoord`. Returned by `Location` /
  `Corner` properties. On schematic `ISch_Rectangle` / `ISch_Line` these
  properties return a **copy**: read into a local, mutate, assign back
  (`Loc := R.Location; Loc.X := …; R.Location := Loc;`); a direct
  `R.Location.X := …` is discarded. (`ISch_Pin.Location` is field-writable.)
  Also: writing a field of a `TLocation` local that has **never been assigned**
  (`Var Loc : TLocation; … Loc.X := 0;`) raises a runtime "Undeclared
  identifier: X". Materialize the record first (`Loc := SomeObj.Location;`)
  before writing its fields.
- **`TCoordRect`**: a bounding rectangle (`BoundingRectangle`), corners in
  internal units.
- **`TPolySegment`**, one segment of a polygon / region outline (line or arc).
- **`TPadCache`**: the pad-stack cache record (`Pad.GetState_Cache` /
  `SetState_Cache`).

---

## 6.3 Enum-typed values

These property types are the `eXxx` ordinals catalogued in
[page 5](05-enums.md):

- **`TObjectId`**: object kind (`Obj.ObjectId`, factory argument).
- **`TLayer`**: board / silk / mask layer (`Prim.Layer`).
- **`TPinElectrical`**: pin electrical type (`Pin.Electrical`).
- **`TRotationBy90`**: `eRotate0/90/180/270`.
- **`TPowerObjectStyle`**: power-port glyph style.
- **`TShape`**: pad / hole shape.
- **`TUnit`** / **`TUnitSystem`**: `eMetric` / `eImperial`.
- **`TSize`**: the schematic line-width enum (`eSmall` / `eMedium` / `eLarge`,
  `0..3`).

---

## 6.4 RTL helpers

- **`TStringList`**: the reliable in-memory list and file I/O type. Use
  `LoadFromFile` / `SaveToFile` for text files (the low-level `Reset`/`ReadLn`
  RTL path raises a modal `EInOutError` and stalls the engine). Treat it as a
  function-local; a few list operations (`Clear`, `Insert`) are unreliable
  across the scripting boundary: rebuild the list instead.
- **`TIniFile`**: read / write `.ini`-style config files.
- **`TInterfaceList`**: a list of interface references (for collecting objects
  during iteration before mutating, so the iterator stays valid). Do **not**
  call `.Free` on one that held Altium design-object references: releasing
  each held interface goes through the COM marshaller and faults in
  `oleaut32` (access violation, read of `FFFFFFFF`). Leave the list for the
  script host to clean up at script end.

---

## 6.5 Notification constants

Used with `SchServer.RobotManager.SendMessage` and
`PCBServer.SendMessageToRobots` to commit edits and register new objects
([page 1](01-servers.md)):

- **`c_Broadcast`**: broadcast destination (an edit notifies all listeners).
- **`c_NoEventData`**: the "no payload" event-data sentinel.

**Schematic messages:**
- **`SCHM_PrimitiveRegistration`**: register a newly added object.
- **`SCHM_BeginModify` / `SCHM_EndModify`**: bracket a property change so the
  editor re-renders it.

**PCB messages:**
- **`PCBM_BoardRegisteration`**: register a new primitive with the board editor
  (needed when `AddPCBObject` alone leaves it unrendered until reload).
- **`PCBM_BeginModify` / `PCBM_EndModify`**: bracket a board-object change.
