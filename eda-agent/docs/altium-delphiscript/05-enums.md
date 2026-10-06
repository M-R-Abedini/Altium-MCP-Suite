# 5. Enum vocabulary (`eXxx`)

DelphiScript enums are bare `eXxx` ordinals: there is no enclosing type name at
the call site. They appear as `SchObjectFactory` / `PCBObjectFactory` kind
arguments, iterator filters, layer assignments, `ObjectId` checks, and property
values. Sets of them are built with `MkSet(eA, eB, …)` for iterator filters and
object sets:

```pascal
Iter.AddFilter_ObjectSet(MkSet(ePadObject, eViaObject));
Iter.AddFilter_LayerSet(MkSet(eTopLayer, eBottomLayer));
```

Every constant below appears in the bridge's deployed DelphiScript. Constants
Altium also defines but this project does not use are out of scope.

---

## 5.1 ObjectIds (`TObjectId`)

The kind tag on every object: passed to the factories, used in
`AddFilter_ObjectSet`, and tested as `Obj.ObjectId`.

**Schematic:** `eSchComponent`, `eSchLib`, `eSheet`, `ePin`, `eParameter`,
`eParameterSet`, `eWire`, `eBus`, `eBusEntry`, `eNetLabel`, `eLine`,
`eRectangle`, `ePolygon`, `eArc`, `eEllipse`, `eEllipticalArc`, `eBezier`,
`eJunction`, `ePowerObject`, `ePort`, `eSheetSymbol`, `eSheetEntry`, `eImage`,
`eLabel`, `eTextFrame`, `eImplementation`, `eConnectionObject`, `eNoERC`.

**PCB:** `eTrackObject`, `ePadObject`, `eViaObject`, `eArcObject`,
`eTextObject`, `eComponentObject`, `ePolyObject`, `eRegionObject`,
`eFillObject`, `eRuleObject`, `eNetObject`, `eDimensionObject`,
`eRadialDimension`, `eLinearDimension`, `eAngularDimension`,
`eDifferentialPairObject`, `eViolationObject`, `eClassObject`,
`eEmbeddedBoardObject`.

---

## 5.2 Object-factory and iteration modifiers

- **Creation mode**: `eCreate_Default` (the normal new-object mode).
- **Dimension**: `eNoDimension` (the `PCBObjectFactory` dimension argument for a
  non-dimension primitive).
- **Iteration scope / method**: `eProcessAll` (visit every match),
  `eIterateFirstLevel` (immediate children only).
- **Pad-stack cache mode**: `eCacheManual`.

---

## 5.3 Layers (`TLayer`)

- **Signal copper:** `eTopLayer`, `eBottomLayer`, `eMidLayer1` … `eMidLayer30`,
  `eMultiLayer` (a through pad/via), `eNoLayer`, `eKeepOutLayer`.
- **Internal planes:** `eInternalPlane1` … `eInternalPlane16` (power/ground
  planes, distinct from signal mid-layers).
- **Silkscreen / overlay:** `eTopOverlay`, `eBottomOverlay`.
- **Solder / paste mask:** `eTopSolder`, `eBottomSolder`, `eTopPaste`,
  `eBottomPaste`.
- **Mechanical:** `eMechanical1` … `eMechanical16`.
- **Fabrication drawing:** `eDrillGuide`, `eDrillDrawing`.

---

## 5.4 Pin electrical type (`TPinElectrical`)

`eElectricInput`, `eElectricOutput`, `eElectricPassive`, `eElectricPower`,
`eElectricIO` (bidirectional; also spelled `eElectricBiDir`),
`eElectricOpenCollector`, `eElectricOpenEmitter`, `eElectricHiZ`.

---

## 5.5 Rotation (`TRotationBy90`)

`eRotate0`, `eRotate90`, `eRotate180`, `eRotate270`. (Distinct from a pin's
`Orientation`, which is the ordinal `degrees Div 90`: see
[page 6](06-types-and-coordinates.md).)

---

## 5.6 Pad / hole shapes (`TShape` / hole type)

- **Pad copper shape:** `eRounded`, `eRectangular`, `eOctagonal`,
  `eRoundRectangle` (also spelled `eRoundedRectangular`).
- **Hole shape:** `eRoundHole`, `eSquareHole`, `eSlotHole`.

---

## 5.7 Power-object styles (`TPowerObjectStyle`)

`ePowerBar`, `ePowerArrow`, `ePowerWave`, `ePowerCircle`, `ePowerGndPower`,
`ePowerGndSignal`, `ePowerGndEarth`. (Chosen by net role: a ground net uses one
of the `…Gnd…` glyphs, a rail uses `ePowerBar` / `ePowerArrow`.)

---

## 5.8 Polygon / port / poly-segment

- **Polygon fill:** `ePolySolid`, `ePolyHatch90`, `ePolyHatch45`, `ePolyNoHatch`,
  `ePolygonPourOver_None`.
- **Poly segment / shape:** `ePolySegmentLine`, `ePolyline`, `ePolygon`.
- **Port direction:** `ePortInput`, `ePortOutput`, `ePortBidirectional`,
  `ePortUnspecified`.

---

## 5.9 Units (`TUnit` / measurement)

`eMetric`, `eImperial`; the measurement-unit forms `eMM`, `eMil`, `eCM`,
`eIN`/`eDXP`. Auto forms: `eAutoMetric`, `eAutoImperial`.

---

## 5.10 Stackup dielectrics

`eNoDielectric` (no dielectric below the layer), `eCore`, `ePrePreg`,
`eSurfaceMaterial`. Read as `LayerObj.Dielectric.DielectricType`
([page 3](03-pcb-interfaces.md) §3.9).

---

## 5.11 Rules and scopes

- **Rule kinds:** `eRule_Clearance`, `eRule_MaxMinWidth`, `eRule_MaxMinHoleSize`,
  `eRule_DifferentialPairsRouting`, `eRule_ConfinementConstraint`.
- **Rule scope:** `eRuleLayerKind_SameLayer`, `eNetScope_AnyNet`,
  `eClassMemberKind_Net`, `eConfineIn`.

---

## 5.12 Variants

`eVariation_NotFitted`: the not-fitted variation kind read from
`DM_VariationKind` ([page 4](04-workspace-project-documents.md) §4.3).

---

## 5.13 Sheet styles (`TSheetStyle`)

The schematic sheet-size presets: `eSheetA` … `eSheetE` (ANSI A-E),
`eSheetLetter`, `eSheetLegal`, `eSheetTabloid`, `eSheetCustom` (with explicit
`CustomX` / `CustomY`).

---

## 5.14 Schematic line width (`TSize`)

`eSmall`, `eMedium`, `eLarge` (the schematic line-width enum, `0..3`: a small
fixed set, not a coordinate; contrast the PCB `Width`/`LineWidth` coordinates on
[page 6](06-types-and-coordinates.md)).
