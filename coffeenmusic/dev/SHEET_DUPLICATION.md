# Duplicating a schematic sheet

Working script: [`duplicate_sheet.pas`](duplicate_sheet.pas). Run it through
`dev/sandbox_runner.py`. It copies a source sheet onto a brand-new schematic and
verifies the result. Measured on Altium Designer 26.4.1.

## The one rule that matters: `Container.ObjectId`

`ISch_Document.SchIterator_Create` **recurses into children**. Filtering by
`ObjectId` alone therefore does *not* give you "the objects on this sheet" — it
gives you every object in the document tree.

On a real 52-component sheet the unfiltered iterator returned 448 objects for
the twelve kinds you'd naively copy. Only 251 of those belong to the sheet:

| `Container.ObjectId` | Meaning | What to do |
|---|---|---|
| `32` | owned by the sheet itself | **copy this** |
| `26` | primitive inside a component symbol | skip — travels with the parent's `Replicate` |
| `42` | primitive inside the template | skip — travels with the `eTemplate` object |

All 211 "Lines" on that sheet had `Container.ObjectId = 26` — they were the
graphic strokes inside component symbols, not sheet graphics. Re-registering
them at document level produces **invisible** corruption: the strays land
exactly on top of the originals, so counts pass and the render looks right.

```pascal
Obj5 := Obj3.Container;
B1 := -1;
if (Obj5 <> nil) then B1 := Obj5.ObjectId;
if (B1 = 32) then     // owned by the sheet
```

Do not hardcode a list of ObjectIds either. The same sheet carried 6 `eArc`
(the circled "i" note markers), 5 `eTextFrame` (the design notes) and 1
`eTemplate` — kinds a hand-written list is likely to omit. Take everything the
sheet owns.

## Measured SCH ObjectIds

```
 4 eRectangle    15 eEllipse      26 eSchComponent
 5 eLine         16 eJunction     27 eParameter
 7 eBusEntry     18 ePolyline     32 (schematic document)
 8 eArc          19 eWire         33 (schematic library)
11 eImage        21 eBezier       35 eNoERC
13 eTextFrame    22 eLabel        38 ePort
                 24 eNetLabel     39 ePowerObject
                                  41 eSheetSymbol
                                  42 eTemplate
```

`eSchDoc` is not a defined constant — compare against literal `32`.

## Order of operations

1. **Sheet settings first**, before any object is registered, or content lands
   against the wrong page size. A new document defaults to A4; the source here
   was B (15000 × 9500 mil), so the first attempt overflowed the border and
   pushed the title block outside the frame.

   Copy: `SheetStyle`, `UseCustomSheet`, `CustomX/Y`, `CustomXZones/YZones`,
   `CustomMarginWidth`, `SheetMarginWidth`, `SheetZonesX/Y`,
   `WorkspaceOrientation`, `BorderOn`, `TitleBlockOn`, `ReferenceZonesOn`,
   `ShowTemplateGraphics`, the three grid sizes, and `TemplateFileName`. Then
   call `SetState_xSizeySize`.

2. **Objects**, via `Replicate` → `RegisterSchObjectInContainer` →
   `SCHM_PrimitiveRegistration` broadcast, wrapped in
   `ProcessControl.PreProcess/PostProcess`.

3. **Parameters last, merged by name — never replicated.**

## Document parameters are system parameters

A new schematic ships with ~27 document parameters and Altium **refuses** to
remove them:

> Error: System parameters which cannot be removed: CurrentTime

Replicating the source's parameters on top gives you 55 where the source has
28. Match by name instead:

```pascal
Obj5 := Obj4.GetState_SchParameterByName(Obj3.Name);
if (Obj5 <> nil) then
    Obj5.Text := Obj3.Text          // update the system default in place
else
    ... Replicate + Register ...    // only for names the new sheet lacks
```

On the test sheet: 27 updated in place, 1 added (`Prelim-Note`).

## What a duplicate legitimately cannot carry

Title-block special strings can resolve through a two-hop chain:

```
label "=documentnumber" -> document parameter DocumentNumber, whose value is
                           itself a special string "=<ProjectParam>"
                        -> PROJECT parameter <ProjectParam>
```

`DM_CreateNewDocument` produces a **Free Document**, which has no project, so
the last hop fails and the field renders as the literal special string (or
`#NAME?`). Any title-block field fed by a project parameter behaves this way;
read the project's own set with `IProject.DM_ParameterCount` /
`DM_Parameters(i)` to see which are involved.

This is not a defect in the copy. Save the sheet and add it to the project
(`IProject.DM_AddSourceDocument`) and the fields resolve.

## Verified result

A 52-component sheet → new sheet, all twelve kinds matching exactly:

| kind | src | dst | | kind | src | dst |
|---|---|---|---|---|---|---|
| SchComponent | 52 | 52 | | Label | 11 | 11 |
| Wire | 106 | 106 | | Arc | 6 | 6 |
| PowerObject | 33 | 33 | | TextFrame | 5 | 5 |
| Parameter | 28 | 28 | | Rectangle | 4 | 4 |
| NetLabel | 2 | 2 | | Polyline | 2 | 2 |
| Port | 1 | 1 | | Template | 1 | 1 |
| | | | | **TOTAL** | **251** | **251** |

Window captures of source and copy are identical across the schematic body;
the only differences are the three project-parameter fields above.

## Simpler alternative

To clone a sheet wholesale, copying the `.SchDoc` on disk and adding it to the
project is exact and free — it preserves the template link, the sizing mode
(the script's copy ends up "Standard B" where the source is "Template-driven B"
at the same dimensions), and every UniqueId. Re-annotate afterwards. The script
above is the right tool when you need to copy objects *into* an existing sheet,
or to duplicate selectively.
