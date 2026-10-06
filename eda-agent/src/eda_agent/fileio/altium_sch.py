# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Headless .SchDoc reader (roadmap V1: hardware CI).

An Altium ``.SchDoc`` is an OLE compound document. Its ``FileHeader`` stream
is a flat sequence of length-prefixed ASCII records:

    <4-byte little-endian length> | KEY=VALUE | KEY=VALUE | ... <NUL>

Each record has a ``RECORD`` type: 1 = component, 34 = designator, 2 = pin,
41 = parameter, etc. A designator (RECORD=34) links to its component via
``OwnerIndex``, which is the owner record's 0-based index counting from the
first record AFTER the header (verified against the buck fixture: U1 →
TPS54331D, D1 → SS14, R1 → RES 10K, J1 → connector).

This first slice extracts the component list (designator + lib ref +
description): the BOM/connectivity spine a headless review needs. Pure
Python via ``olefile``; no Altium.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

_FILEHEADER = "FileHeader"

RECORD_COMPONENT = "1"
RECORD_DESIGNATOR = "34"
RECORD_PARAMETER = "41"
RECORD_POWER_PORT = "17"
RECORD_NET_LABEL = "25"
RECORD_PIN = "2"
RECORD_WIRE = "27"

# PinConglomerate low 2 bits encode orientation (Altium convention):
# 0 = 0deg (points right), 1 = 90 (up), 2 = 180 (left), 3 = 270 (down).
_PIN_ORIENT_DEG = {0: 0, 1: 90, 2: 180, 3: 270}

# Common parameter names → normalized field. Altium libraries vary, so each
# normalized field lists candidate source names in priority order.
_MPN_NAMES = ("Partnumber", "PartNumber", "Manufacturer Part Number",
              "MPN", "ManufacturerPartNumber", "Manufacturer_Part_Number")
_MFR_NAMES = ("Manufacturer", "Mfr", "Manufacturer Name")
_VALUE_NAMES = ("Value", "Comment")
_DATASHEET_NAMES = ("Datasheet", "DatasheetURL", "HelpURL",
                    "ComponentLink1URL", "Datasheet Link")


def _resolve(value: str, params: dict[str, str]) -> str:
    """Resolve Altium's ``=ParamName`` display-reference convention.

    A Comment/parameter of ``=Partnumber`` means "show the value of the
    Partnumber parameter". Resolve one hop; leave a dangling reference as-is.
    """
    if value.startswith("=") and len(value) > 1:
        return params.get(value[1:], value)
    return value


def _first_present(params: dict[str, str], names) -> str:
    for n in names:
        v = params.get(n)
        if v and v not in ("*", "="):
            return _resolve(v, params)
    return ""


def _parse_fields(payload: bytes) -> dict[str, str]:
    """Parse a ``|KEY=VALUE|...`` record body into a dict (last key wins)."""
    text = payload.rstrip(b"\x00").decode("latin-1", "replace")
    fields: dict[str, str] = {}
    for part in text.split("|"):
        if "=" in part:
            key, value = part.split("=", 1)
            fields[key] = value
    return fields


def read_schdoc_records(path: str | Path) -> list[dict[str, str]]:
    """Return every FileHeader record of a .SchDoc as a field dict.

    Records keep file order; record[0] is the ``HEADER`` record. Raises
    ValueError if the file is not a valid SchDoc OLE container.
    """
    import olefile

    path = str(path)
    if not olefile.isOleFile(path):
        raise ValueError(f"not an OLE compound file (not a .SchDoc?): {path}")
    ole = olefile.OleFileIO(path)
    try:
        if not ole.exists(_FILEHEADER):
            raise ValueError(f"no FileHeader stream in {path}")
        data = ole.openstream(_FILEHEADER).read()
    finally:
        ole.close()

    records: list[dict[str, str]] = []
    i, n = 0, len(data)
    while i + 4 <= n:
        length = struct.unpack("<I", data[i:i + 4])[0]
        i += 4
        if length == 0 or i + length > n:
            break  # truncated / malformed tail: stop cleanly
        records.append(_parse_fields(data[i:i + length]))
        i += length
    return records


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def read_schematic_nets(path: str | Path) -> list[dict[str, Any]]:
    """Extract declared net names from a .SchDoc (labels + power ports).

    Returns one entry per distinct net name:
    ``{name, label_count, power_count, total}``. These are the names
    *declared* on the sheet (RECORD=25 net labels, RECORD=17 power ports),
    not the compiled netlist, which needs a geometric connectivity solver
    (wires touching pins touching labels). Still useful on its own: a
    reviewer can eyeball the rail inventory, and it is the input a future
    net solver will annotate with membership.
    """
    records = read_schdoc_records(path)
    nets: dict[str, dict[str, Any]] = {}
    for rec in records:
        rt = rec.get("RECORD")
        if rt == RECORD_NET_LABEL:
            key = "label_count"
        elif rt == RECORD_POWER_PORT:
            key = "power_count"
        else:
            continue
        name = (rec.get("Text") or "").strip()
        if not name:
            continue
        entry = nets.setdefault(
            name, {"name": name, "label_count": 0, "power_count": 0})
        entry[key] += 1
    for entry in nets.values():
        entry["total"] = entry["label_count"] + entry["power_count"]
    return [nets[n] for n in sorted(nets)]


def read_schematic_wires(path: str | Path) -> list[dict[str, Any]]:
    """Extract wire segments (RECORD=27) as a list of ``{x1,y1,x2,y2}``.

    A SchDoc wire is a polyline (``LocationCount`` vertices, ``X1/Y1..``);
    this flattens each polyline into its individual segments so a future
    connectivity solver can union coincident endpoints. Coordinates are raw
    SchDoc internal units.
    """
    segments: list[dict[str, Any]] = []
    for rec in read_schdoc_records(path):
        if rec.get("RECORD") != RECORD_WIRE:
            continue
        count = _to_int(rec.get("LocationCount")) or 0
        pts = []
        for k in range(1, count + 1):
            x = _to_int(rec.get(f"X{k}"))
            y = _to_int(rec.get(f"Y{k}"))
            if x is not None and y is not None:
                pts.append((x, y))
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            segments.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2})
    return segments


def read_schematic_pins(path: str | Path) -> list[dict[str, Any]]:
    """Extract pins (RECORD=2) with owner, name, location, and orientation.

    Returns ``{owner_index, designator, name, x, y, length, orientation}``
    where ``owner_index`` matches the component owner-index scheme used by
    :func:`read_schematic_components`, so pins can be tied back to their
    component. ``x``/``y`` is the pin's anchor; ``orientation`` (deg) and
    ``length`` describe how it extends: the electrical endpoint the net
    solver needs is derived from these (validated in the solver step).
    """
    pins: list[dict[str, Any]] = []
    for rec in read_schdoc_records(path):
        if rec.get("RECORD") != RECORD_PIN:
            continue
        conglom = _to_int(rec.get("PinConglomerate")) or 0
        pins.append({
            "owner_index": _to_int(rec.get("OwnerIndex")),
            "designator": rec.get("Designator", ""),
            "name": rec.get("Name", ""),
            "x": _to_int(rec.get("Location.X")),
            "y": _to_int(rec.get("Location.Y")),
            "length": _to_int(rec.get("PinLength")) or 0,
            "orientation": _PIN_ORIENT_DEG.get(conglom & 0x3, 0),
        })
    return pins


RECORD_JUNCTION = "29"


def read_schematic_net_label_locations(path: str | Path) -> list[dict[str, Any]]:
    """Extract net labels (RECORD=25) as ``{name, x, y}`` with locations.

    Distinct from :func:`read_schematic_nets` (which only inventories names
    and counts): the geometric net solver needs each label's location to bind
    it to the wire/pin it names.
    """
    labels: list[dict[str, Any]] = []
    for rec in read_schdoc_records(path):
        if rec.get("RECORD") != RECORD_NET_LABEL:
            continue
        labels.append({
            "name": (rec.get("Text") or "").strip(),
            "x": _to_int(rec.get("Location.X")),
            "y": _to_int(rec.get("Location.Y")),
        })
    return labels


def read_schematic_power_ports(path: str | Path) -> list[dict[str, Any]]:
    """Extract power ports (RECORD=17) as ``{name, x, y, orientation}``.

    A power port both connects and NAMES a net (GND, VCC, +3V3, ...). Its
    ``Location`` is the electrical attachment point that binds to a
    coincident pin end or wire. ``name`` is the port's ``Text``.
    """
    ports: list[dict[str, Any]] = []
    for rec in read_schdoc_records(path):
        if rec.get("RECORD") != RECORD_POWER_PORT:
            continue
        ports.append({
            "name": (rec.get("Text") or "").strip(),
            "x": _to_int(rec.get("Location.X")),
            "y": _to_int(rec.get("Location.Y")),
            "orientation": _to_int(rec.get("Orientation")) or 0,
        })
    return ports


def read_schematic_junctions(path: str | Path) -> list[dict[str, Any]]:
    """Extract manual junction dots (RECORD=29) as ``{x, y}``.

    A junction forces a connection where wires cross or meet; the geometric
    net solver treats each junction location as a connection point.
    """
    junctions: list[dict[str, Any]] = []
    for rec in read_schdoc_records(path):
        if rec.get("RECORD") != RECORD_JUNCTION:
            continue
        junctions.append({
            "x": _to_int(rec.get("Location.X")),
            "y": _to_int(rec.get("Location.Y")),
        })
    return junctions


RECORD_SHEET = "31"

# Title-block / document parameters worth surfacing in a review.
_DOC_PARAM_NAMES = (
    "Title", "Revision", "DocumentNumber", "Author", "CheckedBy",
    "ApprovedBy", "CompanyName", "Organization", "Engineer",
)


def read_schematic_document_info(path: str | Path) -> dict[str, Any]:
    """Extract document / title-block metadata from a .SchDoc.

    Returns ``{title, revision, document_number, author, company, ...,
    sheet: {custom_x, custom_y, title_block_on}}`` from the document-level
    parameters (RECORD=41 with no owner) and the sheet record (RECORD=31).
    Altium's ``*`` placeholder (an unfilled special-string field) is
    normalized to an empty string. Fully offline.
    """
    records = read_schdoc_records(path)
    doc_params: dict[str, str] = {}
    for rec in records:
        if rec.get("RECORD") != RECORD_PARAMETER:
            continue
        if _to_int(rec.get("OwnerIndex")) is not None:
            continue  # component parameter, not a document one
        name = rec.get("Name")
        if name:
            doc_params[name] = rec.get("Text", "")

    def _val(name: str) -> str:
        v = doc_params.get(name, "")
        return "" if v in ("*", "=") else v

    info: dict[str, Any] = {}
    info["title"] = _val("Title")
    info["revision"] = _val("Revision")
    info["document_number"] = _val("DocumentNumber")
    info["author"] = _val("Author")
    info["company"] = _val("CompanyName") or _val("Organization")

    sheet = next((r for r in records if r.get("RECORD") == RECORD_SHEET), None)
    info["sheet"] = {
        "custom_x": _to_int(sheet.get("CustomX")) if sheet else None,
        "custom_y": _to_int(sheet.get("CustomY")) if sheet else None,
        "title_block_on": (sheet.get("TitleBlockOn") == "T") if sheet else None,
    }
    return info


def read_schematic_components(path: str | Path) -> list[dict[str, Any]]:
    """Extract placed components (with designators) from a .SchDoc.

    Returns a list of ``{designator, lib_reference, description,
    library_path, unique_id, x, y}``: the fields a headless BOM/review
    needs. Designators are joined to components via the OwnerIndex scheme
    (owner index = record position counting from the first post-header
    record). Coordinates are the raw SchDoc internal units (1/100 mil).
    """
    records = read_schdoc_records(path)

    # Altium OwnerIndex counts records from the first one AFTER the header
    # (record[0]). So the record at file position ``p`` (p >= 1) has owner
    # index ``p - 1``. Build that map for component records only.
    comp_by_owner_index: dict[int, dict[str, str]] = {}
    for pos, rec in enumerate(records):
        if pos == 0:
            continue  # header
        if rec.get("RECORD") == RECORD_COMPONENT:
            comp_by_owner_index[pos - 1] = rec

    # Attach designators (RECORD=34) to their owning component.
    designator_by_owner: dict[int, str] = {}
    for rec in records:
        if rec.get("RECORD") == RECORD_DESIGNATOR:
            oi = _to_int(rec.get("OwnerIndex"))
            text = rec.get("Text")
            if oi is not None and text is not None:
                designator_by_owner[oi] = text

    # Collect component parameters (RECORD=41) by owner. Document-level
    # parameters (title-block fields) have no OwnerIndex and are skipped.
    params_by_owner: dict[int, dict[str, str]] = {}
    for rec in records:
        if rec.get("RECORD") != RECORD_PARAMETER:
            continue
        oi = _to_int(rec.get("OwnerIndex"))
        name = rec.get("Name")
        if oi is None or oi not in comp_by_owner_index or not name:
            continue
        params_by_owner.setdefault(oi, {})[name] = rec.get("Text", "")

    out: list[dict[str, Any]] = []
    for owner_index in sorted(comp_by_owner_index):
        rec = comp_by_owner_index[owner_index]
        params = params_by_owner.get(owner_index, {})
        out.append({
            "designator": designator_by_owner.get(owner_index, ""),
            "lib_reference": rec.get("LibReference", ""),
            "description": rec.get("ComponentDescription", ""),
            "library_path": rec.get("LibraryPath", ""),
            "unique_id": rec.get("UniqueID", ""),
            "x": _to_int(rec.get("Location.X")),
            "y": _to_int(rec.get("Location.Y")),
            "mpn": _first_present(params, _MPN_NAMES),
            "manufacturer": _first_present(params, _MFR_NAMES),
            "value": _first_present(params, _VALUE_NAMES),
            "datasheet": _first_present(params, _DATASHEET_NAMES),
            "parameters": params,
        })
    return out
