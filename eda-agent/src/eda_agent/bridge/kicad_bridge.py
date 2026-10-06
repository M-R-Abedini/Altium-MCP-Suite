# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Live connection to a running KiCad via its IPC API (kicad-python / kipy).

Mirrors the role of the Altium bridge: KiCad stays open and in control, and
eda-agent talks to it over KiCad's supported protobuf IPC socket. There is no
polling loop to maintain here; KiCad hosts the server and the ``kipy`` client
connects to it.

Requirements on the KiCad side (KiCad 9+):
  * Preferences > Plugins: enable the KiCad API server (off by default).
  * A board open in the PCB editor for board queries; the API server only
    answers document/board requests when an editor frame owns a document.

Everything here is read-only for now. ``kipy`` is imported lazily so importing
this module never fails when KiCad or the client library is absent; the failure
surfaces as a clear reason at call time instead.
"""

from __future__ import annotations

from typing import Any, Optional

# KiCad internal coordinates are nanometres.
_NM_PER_MM = 1_000_000


def _mm(nm: Optional[int]) -> Optional[float]:
    if nm is None:
        return None
    return round(nm / _NM_PER_MM, 4)


class KiCadNotReachableError(RuntimeError):
    """KiCad is not running, its API server is off, or no board is open."""


class KiCadBridge:
    """Thin, reconnecting wrapper over the kipy client.

    One instance is reused across calls (see :func:`get_kicad_bridge`). The
    underlying client is cached and re-created transparently after a dropped
    connection, since KiCad can be closed and reopened under a long-lived
    server.
    """

    def __init__(self) -> None:
        self._client = None  # kipy.KiCad, created lazily

    # -- connection ---------------------------------------------------------
    def _kicad(self):
        """Return a live kipy.KiCad, (re)connecting as needed.

        Raises KiCadNotReachableError with an actionable message when the
        client library is missing or KiCad's API server is not answering.
        """
        try:
            from kipy import KiCad
            from kipy.errors import ConnectionError as KiConnError
        except Exception as e:  # pragma: no cover - import guard
            raise KiCadNotReachableError(
                "kicad-python (kipy) is not installed: " + str(e)) from None

        if self._client is not None:
            try:
                self._client.ping()
                return self._client
            except Exception:
                self._client = None  # stale, fall through and reconnect

        try:
            client = KiCad()
            client.ping()
        except KiConnError as e:
            raise KiCadNotReachableError(
                "cannot reach KiCad's API server (" + str(e) + "). Open KiCad, "
                "enable Preferences > Plugins > KiCad API server, and restart "
                "KiCad.") from None
        except Exception as e:
            raise KiCadNotReachableError(
                "failed to connect to KiCad: " + str(e)) from None
        self._client = client
        return client

    def ping(self) -> dict[str, Any]:
        """Connection status plus KiCad and API versions."""
        k = self._kicad()
        return {
            "connected": True,
            "kicad_version": str(k.get_version()),
            "api_version": str(k.get_api_version()),
        }

    def _board(self):
        """The open PCB, or a clear error when none is available."""
        k = self._kicad()
        try:
            return k.get_board()
        except Exception as e:
            raise KiCadNotReachableError(
                "no board is available (" + str(e) + "). Open a project and its "
                "PCB in the KiCad PCB editor.") from None

    # -- read helpers -------------------------------------------------------
    @staticmethod
    def _field_text(field) -> str:
        try:
            return field.text.value
        except Exception:
            return ""

    def footprints(self) -> list[dict[str, Any]]:
        """Every footprint on the board, as plain dicts."""
        board = self._board()
        out: list[dict[str, Any]] = []
        for f in board.get_footprints():
            ref = self._field_text(getattr(f, "reference_field", None))
            val = self._field_text(getattr(f, "value_field", None))
            fpid = ""
            try:
                fpid = str(f.definition.id)
            except Exception:
                pass
            pos = getattr(f, "position", None)
            x = _mm(getattr(pos, "x", None)) if pos is not None else None
            y = _mm(getattr(pos, "y", None)) if pos is not None else None
            layer = None
            try:
                layer = int(f.layer)
            except Exception:
                pass
            locked = None
            try:
                locked = bool(f.locked)
            except Exception:
                pass
            out.append({"reference": ref, "value": val, "footprint_id": fpid,
                        "x_mm": x, "y_mm": y, "layer": layer, "locked": locked})
        return out

    def nets(self) -> list[dict[str, Any]]:
        board = self._board()
        out: list[dict[str, Any]] = []
        for n in board.get_nets():
            # Net.code is deprecated in KiCad 10 (identity is by name); read it
            # best-effort so a future removal never breaks the listing.
            code = None
            try:
                code = int(n.code)
            except Exception:
                pass
            out.append({"name": n.name, "code": code})
        return out

    def component_pins(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        """(parts, pins, unconnected-pad-count) for the normalized snapshot.

        Each part: ``{refdes, value, footprint, layer, locked, dnp}``. Each pin:
        ``{refdes, pin, net}`` where net ``""`` means the pad is on no net. A
        placed footprint's ``definition.pads`` carry the live net assignments,
        which is what lets one pass recover the full net<->pin adjacency.
        """
        board = self._board()
        parts: list[dict[str, Any]] = []
        pins: list[dict[str, Any]] = []
        unconnected = 0
        for f in board.get_footprints():
            ref = self._field_text(getattr(f, "reference_field", None))
            val = self._field_text(getattr(f, "value_field", None))
            fpid = ""
            try:
                fpid = str(f.definition.id)
            except Exception:
                pass
            layer = None
            try:
                layer = int(f.layer)
            except Exception:
                pass
            locked = None
            try:
                locked = bool(f.locked)
            except Exception:
                pass
            dnp = False
            try:
                dnp = bool(f.attributes.do_not_populate)
            except Exception:
                pass
            parts.append({"refdes": ref, "value": val, "footprint": fpid,
                          "layer": layer, "locked": locked, "dnp": dnp})
            try:
                fp_pads = list(f.definition.pads)
            except Exception:
                fp_pads = []
            for p in fp_pads:
                num = ""
                try:
                    num = str(p.number)
                except Exception:
                    pass
                net = ""
                try:
                    net = p.net.name or ""
                except Exception:
                    net = ""
                pins.append({"refdes": ref, "pin": num, "net": net})
                if not net:
                    unconnected += 1
        return parts, pins, unconnected

    def pad_net_counts(self) -> tuple[dict[str, int], int]:
        """(pads-per-net-name, count of pads with no net)."""
        board = self._board()
        per_net: dict[str, int] = {}
        no_net = 0
        for p in board.get_pads():
            try:
                name = p.net.name
            except Exception:
                name = ""
            if name:
                per_net[name] = per_net.get(name, 0) + 1
            else:
                no_net += 1
        return per_net, no_net

    def _layer_name(self, board, layer) -> str:
        try:
            return board.get_layer_name(layer)
        except Exception:
            try:
                return str(int(layer))
            except Exception:
                return ""

    def tracks(self) -> list[dict[str, Any]]:
        """Copper tracks and arcs: net, layer, width, endpoints, length."""
        board = self._board()
        out: list[dict[str, Any]] = []
        for t in board.get_tracks():
            row: dict[str, Any] = {"net": "", "layer": "", "width_mm": None,
                                   "length_mm": None, "is_arc": False}
            try:
                row["net"] = t.net.name or ""
            except Exception:
                pass
            try:
                row["layer"] = self._layer_name(board, t.layer)
            except Exception:
                pass
            try:
                row["width_mm"] = _mm(t.width)
            except Exception:
                pass
            try:
                row["length_mm"] = _mm(t.length())
            except Exception:
                pass
            for end in ("start", "end"):
                try:
                    v = getattr(t, end)
                    row[f"{end}_mm"] = [_mm(v.x), _mm(v.y)]
                except Exception:
                    pass
            row["is_arc"] = hasattr(t, "mid")
            out.append(row)
        return out

    def vias(self) -> list[dict[str, Any]]:
        board = self._board()
        out: list[dict[str, Any]] = []
        for v in board.get_vias():
            row: dict[str, Any] = {"net": "", "x_mm": None, "y_mm": None,
                                   "diameter_mm": None, "drill_mm": None}
            try:
                row["net"] = v.net.name or ""
            except Exception:
                pass
            try:
                row["x_mm"], row["y_mm"] = _mm(v.position.x), _mm(v.position.y)
            except Exception:
                pass
            try:
                row["diameter_mm"] = _mm(v.diameter)
            except Exception:
                pass
            try:
                row["drill_mm"] = _mm(v.drill_diameter)
            except Exception:
                pass
            out.append(row)
        return out

    def zones(self) -> list[dict[str, Any]]:
        board = self._board()
        out: list[dict[str, Any]] = []
        for z in board.get_zones():
            row: dict[str, Any] = {"name": "", "net": "", "layers": [],
                                   "filled": None, "priority": None,
                                   "is_rule_area": None}
            try:
                row["name"] = z.name or ""
            except Exception:
                pass
            try:
                row["net"] = z.net.name if z.net is not None else ""
            except Exception:
                pass
            try:
                row["layers"] = [self._layer_name(board, l) for l in z.layers]
            except Exception:
                pass
            for attr, key in (("filled", "filled"), ("priority", "priority")):
                try:
                    row[key] = getattr(z, attr)
                except Exception:
                    pass
            try:
                row["is_rule_area"] = z.is_rule_area()
            except Exception:
                pass
            out.append(row)
        return out

    def stackup(self) -> list[dict[str, Any]]:
        board = self._board()
        out: list[dict[str, Any]] = []
        try:
            layers = board.get_stackup().layers
        except Exception:
            return out
        for l in layers:
            row: dict[str, Any] = {}
            for attr, key in (("user_name", "name"), ("type", "type"),
                              ("material_name", "material")):
                try:
                    row[key] = getattr(l, attr)
                except Exception:
                    pass
            try:
                row["thickness_mm"] = _mm(l.thickness)
            except Exception:
                pass
            try:
                row["layer"] = self._layer_name(board, l.layer)
            except Exception:
                pass
            out.append(row)
        return out

    def layers(self) -> list[str]:
        board = self._board()
        try:
            return [self._layer_name(board, l) for l in board.get_enabled_layers()]
        except Exception:
            return []

    def board_outline(self) -> dict[str, Any]:
        """Bounding box (mm) of the board edge, from Edge.Cuts graphics."""
        board = self._board()
        xs: list[int] = []
        ys: list[int] = []
        try:
            shapes = list(board.get_shapes())
        except Exception:
            shapes = []
        for s in shapes:
            try:
                if self._layer_name(board, s.layer) != "Edge.Cuts":
                    continue
            except Exception:
                continue
            for pt in ("start", "end"):
                try:
                    v = getattr(s, pt)
                    xs.append(v.x)
                    ys.append(v.y)
                except Exception:
                    pass
        if not xs or not ys:
            return {"bbox_mm": None, "edge_shape_count": 0}
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        return {"bbox_mm": {"x": _mm(x0), "y": _mm(y0),
                            "w": _mm(x1 - x0), "h": _mm(y1 - y0)},
                "edge_shape_count": len(xs) // 2}

    def title_block(self) -> dict[str, Any]:
        board = self._board()
        try:
            tb = board.get_title_block_info()
        except Exception:
            return {}
        out: dict[str, Any] = {}
        for attr in ("title", "date", "revision", "company", "comment1",
                     "comment2", "comment3", "comment4"):
            try:
                out[attr] = getattr(tb, attr)
            except Exception:
                pass
        return out

    def selection(self) -> list[dict[str, Any]]:
        """Items currently selected in the PCB editor: type and (for
        footprints) reference."""
        board = self._board()
        out: list[dict[str, Any]] = []
        try:
            items = board.get_selection()
        except Exception:
            return out
        for item in items:
            row: dict[str, Any] = {"type": type(item).__name__}
            try:
                rf = getattr(item, "reference_field", None)
                if rf is not None:
                    ref = self._field_text(rf)
                    if ref:
                        row["reference"] = ref
            except Exception:
                pass
            out.append(row)
        return out

    def shapes(self) -> list[dict[str, Any]]:
        """Graphic shapes (segment/arc/circle/rect/polygon): type, layer, net,
        and the geometry points available for the shape (mm)."""
        board = self._board()
        out: list[dict[str, Any]] = []
        for s in board.get_shapes():
            row: dict[str, Any] = {
                "type": type(s).__name__.replace("Board", "").lower(),
                "layer": "", "net": ""}
            try:
                row["layer"] = self._layer_name(board, s.layer)
            except Exception:
                pass
            try:
                if s.net is not None and s.net.name:
                    row["net"] = s.net.name
            except Exception:
                pass
            for attr in ("start", "end", "center"):
                try:
                    v = getattr(s, attr)
                    row[attr + "_mm"] = [_mm(v.x), _mm(v.y)]
                except Exception:
                    pass
            try:
                row["radius_mm"] = _mm(s.radius)
            except Exception:
                pass
            out.append(row)
        return out

    def texts(self) -> list[dict[str, Any]]:
        """Free board text: value, layer, position (mm)."""
        board = self._board()
        out: list[dict[str, Any]] = []
        for t in board.get_text():
            row: dict[str, Any] = {"value": "", "layer": "", "x_mm": None,
                                   "y_mm": None}
            try:
                row["value"] = t.value or ""
            except Exception:
                pass
            try:
                row["layer"] = self._layer_name(board, t.layer)
            except Exception:
                pass
            try:
                row["x_mm"], row["y_mm"] = _mm(t.position.x), _mm(t.position.y)
            except Exception:
                pass
            out.append(row)
        return out

    def groups(self) -> list[dict[str, Any]]:
        """Item groups: name and member count."""
        board = self._board()
        out: list[dict[str, Any]] = []
        for g in board.get_groups():
            row: dict[str, Any] = {"name": "", "item_count": 0}
            try:
                row["name"] = g.name or ""
            except Exception:
                pass
            try:
                row["item_count"] = len(list(g.items))
            except Exception:
                pass
            out.append(row)
        return out

    def dimensions(self) -> list[dict[str, Any]]:
        """Dimension annotations: layer, override text, height (mm)."""
        board = self._board()
        out: list[dict[str, Any]] = []
        for d in board.get_dimensions():
            row: dict[str, Any] = {"layer": "", "override_text": "",
                                   "height_mm": None}
            try:
                row["layer"] = self._layer_name(board, d.layer)
            except Exception:
                pass
            try:
                if getattr(d, "override_text_enabled", False):
                    row["override_text"] = d.override_text or ""
            except Exception:
                pass
            try:
                row["height_mm"] = _mm(d.height)
            except Exception:
                pass
            out.append(row)
        return out

    def open_documents(self) -> list[dict[str, Any]]:
        """Open documents in KiCad: PCB and schematic, with filename."""
        k = self._kicad()
        out: list[dict[str, Any]] = []
        try:
            from kipy.proto.common.types import DocumentType
        except Exception:
            return out
        for dtype, label in ((DocumentType.DOCTYPE_PCB, "pcb"),
                             (DocumentType.DOCTYPE_SCHEMATIC, "schematic")):
            try:
                docs = k.get_open_documents(dtype)
            except Exception:
                continue
            for d in docs:
                name = ""
                for attr in ("board_filename", "schematic_filename", "name"):
                    try:
                        v = getattr(d, attr, "")
                        if v:
                            name = v
                            break
                    except Exception:
                        pass
                proj = ""
                try:
                    proj = getattr(getattr(d, "project", None), "name", "") or ""
                except Exception:
                    pass
                out.append({"type": label, "filename": name, "project": proj})
        return out

    def project_info(self) -> dict[str, Any]:
        """Open project's name, directory, and net-class names."""
        board = self._board()
        try:
            proj = board.get_project()
        except Exception as e:
            raise KiCadNotReachableError(
                "no project available (" + str(e) + ")") from None
        ncs: list[str] = []
        try:
            ncs = [nc.name for nc in proj.get_net_classes()]
        except Exception:
            pass
        return {"name": getattr(proj, "name", "") or "",
                "path": getattr(proj, "path", "") or "",
                "net_classes": ncs}

    def text_variables(self) -> dict[str, Any]:
        """Project text (substitution) variables as a plain dict."""
        board = self._board()
        try:
            proj = board.get_project()
            tv = proj.get_text_variables()
        except Exception:
            return {}
        try:
            return {str(k): str(v) for k, v in tv.items()}
        except Exception:
            return {}

    def net_classes(self) -> dict[str, Any]:
        """Per-net net-class assignment and each class's routing rules (mm)."""
        board = self._board()
        nets = list(board.get_nets())
        try:
            mapping = board.get_netclass_for_nets(nets)
        except Exception:
            mapping = {}

        def _safe_mm(nc, attr):
            try:
                return _mm(getattr(nc, attr))
            except Exception:
                return None

        by_net: dict[str, str] = {}
        classes: dict[str, Any] = {}
        for net_name, nc in mapping.items():
            cls = ""
            try:
                cls = nc.name or ""
            except Exception:
                pass
            by_net[net_name] = cls
            if cls and cls not in classes:
                classes[cls] = {
                    "clearance_mm": _safe_mm(nc, "clearance"),
                    "diff_pair_gap_mm": _safe_mm(nc, "diff_pair_gap"),
                    "diff_pair_track_width_mm":
                        _safe_mm(nc, "diff_pair_track_width"),
                    "bus_width_mm": _safe_mm(nc, "bus_width"),
                }
        return {"by_net": by_net, "classes": classes}

    def pads(self) -> list[dict[str, Any]]:
        """All pads: number, net, position (mm), pad type."""
        board = self._board()
        out: list[dict[str, Any]] = []
        for p in board.get_pads():
            row: dict[str, Any] = {"number": "", "net": "", "x_mm": None,
                                   "y_mm": None, "pad_type": None}
            try:
                row["number"] = str(p.number)
            except Exception:
                pass
            try:
                row["net"] = p.net.name or ""
            except Exception:
                pass
            try:
                row["x_mm"], row["y_mm"] = _mm(p.position.x), _mm(p.position.y)
            except Exception:
                pass
            try:
                row["pad_type"] = int(p.pad_type)
            except Exception:
                pass
            out.append(row)
        return out

    def sch_file_path(self) -> str:
        """Absolute path of the project's schematic (.kicad_sch) on disk."""
        import os
        board = self.board_file_path()
        root, _ = os.path.splitext(board)
        return root + ".kicad_sch"

    # -- authoring (writes) -------------------------------------------------
    # These mutate the open board. Each groups its change in a commit (one undo
    # step) and applies it; pass save=True to also write the file to disk.
    def _find_footprint(self, board, reference: str):
        for f in board.get_footprints():
            if self._field_text(getattr(f, "reference_field", None)) == reference:
                return f
        raise KiCadNotReachableError(
            "no footprint with reference '" + str(reference) + "'")

    def _apply(self, board, apply_fn, save: bool):
        """Run a mutation inside a commit; push it, or drop it on error."""
        commit = None
        try:
            commit = board.begin_commit()
        except Exception:
            commit = None
        try:
            result = apply_fn()
        except Exception:
            if commit is not None:
                try:
                    board.drop_commit(commit)
                except Exception:
                    pass
            raise
        if commit is not None:
            try:
                board.push_commit(commit, "eda-agent")
            except Exception:
                pass
        if save:
            try:
                board.save()
            except Exception:
                pass
        return result

    def move_component(self, reference: str, x_mm: float, y_mm: float,
                       save: bool = False) -> dict[str, Any]:
        from kipy.geometry import Vector2
        board = self._board()
        f = self._find_footprint(board, reference)

        def apply():
            f.position = Vector2.from_xy_mm(float(x_mm), float(y_mm))
            board.update_items([f])
        self._apply(board, apply, save)
        return {"reference": reference, "x_mm": x_mm, "y_mm": y_mm,
                "saved": save}

    def rotate_component(self, reference: str, degrees: float,
                         save: bool = False) -> dict[str, Any]:
        from kipy.geometry import Angle
        board = self._board()
        f = self._find_footprint(board, reference)

        def apply():
            f.orientation = Angle.from_degrees(float(degrees))
            board.update_items([f])
        self._apply(board, apply, save)
        return {"reference": reference, "orientation_deg": degrees,
                "saved": save}

    def set_component_side(self, reference: str, side: str,
                           save: bool = False) -> dict[str, Any]:
        from kipy.board_types import BoardLayer
        board = self._board()
        f = self._find_footprint(board, reference)
        bottom = str(side).strip().lower() in ("bottom", "b", "back", "b.cu")

        def apply():
            f.layer = BoardLayer.BL_B_Cu if bottom else BoardLayer.BL_F_Cu
            board.update_items([f])
        self._apply(board, apply, save)
        return {"reference": reference, "side": "bottom" if bottom else "top",
                "saved": save}

    def set_component_locked(self, reference: str, locked: bool,
                             save: bool = False) -> dict[str, Any]:
        board = self._board()
        f = self._find_footprint(board, reference)

        def apply():
            f.locked = bool(locked)
            board.update_items([f])
        self._apply(board, apply, save)
        return {"reference": reference, "locked": bool(locked), "saved": save}

    def set_component_value(self, reference: str, value: str,
                            save: bool = False) -> dict[str, Any]:
        board = self._board()
        f = self._find_footprint(board, reference)

        def apply():
            f.value_field.text.value = str(value)
            board.update_items([f])
        self._apply(board, apply, save)
        return {"reference": reference, "value": value, "saved": save}

    def delete_component(self, reference: str,
                         save: bool = False) -> dict[str, Any]:
        board = self._board()
        f = self._find_footprint(board, reference)

        def apply():
            board.remove_items([f])
        self._apply(board, apply, save)
        return {"reference": reference, "deleted": True, "saved": save}

    def _layer_by_name(self, board, layer: str):
        """Friendly layer name ("F.Cu", "F.Silkscreen") -> BoardLayer enum.

        Copper enum names follow "BL_<name>" (F.Cu -> BL_F_Cu), but technical
        layers do not (F.Silkscreen -> BL_F_SilkS), so fall back to matching
        against ``get_layer_name`` for anything the direct form misses.
        """
        from kipy.board_types import BoardLayer
        key = str(layer).strip()
        try:
            return BoardLayer.Value("BL_" + key.replace(".", "_"))
        except Exception:
            pass
        try:
            for val in BoardLayer.values():
                try:
                    if board.get_layer_name(val) == key:
                        return val
                except Exception:
                    continue
        except Exception:
            pass
        return BoardLayer.BL_F_Cu

    def create_track(self, net: str, layer: str, x1_mm: float, y1_mm: float,
                     x2_mm: float, y2_mm: float, width_mm: float,
                     save: bool = False) -> dict[str, Any]:
        from kipy.geometry import Vector2
        from kipy.board_types import Track, Net
        board = self._board()
        t = Track()
        t.start = Vector2.from_xy_mm(float(x1_mm), float(y1_mm))
        t.end = Vector2.from_xy_mm(float(x2_mm), float(y2_mm))
        t.width = int(round(float(width_mm) * _NM_PER_MM))
        t.layer = self._layer_by_name(board, layer)
        if net:
            t.net = Net(name=str(net))

        created: list[Any] = []

        def apply():
            nonlocal created
            created = board.create_items([t])
        self._apply(board, apply, save)
        return {"net": net, "layer": layer, "width_mm": width_mm,
                "start_mm": [x1_mm, y1_mm], "end_mm": [x2_mm, y2_mm],
                "created": len(created), "saved": save}

    def create_via(self, net: str, x_mm: float, y_mm: float,
                   diameter_mm: float, drill_mm: float,
                   save: bool = False) -> dict[str, Any]:
        from kipy.geometry import Vector2
        from kipy.board_types import Via, Net
        board = self._board()
        v = Via()
        v.position = Vector2.from_xy_mm(float(x_mm), float(y_mm))
        v.diameter = int(round(float(diameter_mm) * _NM_PER_MM))
        try:
            v.drill_diameter = int(round(float(drill_mm) * _NM_PER_MM))
        except Exception:
            pass
        if net:
            v.net = Net(name=str(net))

        created: list[Any] = []

        def apply():
            nonlocal created
            created = board.create_items([v])
        self._apply(board, apply, save)
        return {"net": net, "x_mm": x_mm, "y_mm": y_mm,
                "diameter_mm": diameter_mm, "drill_mm": drill_mm,
                "created": len(created), "saved": save}

    def create_text(self, text: str, x_mm: float, y_mm: float,
                    layer: str = "F.Silkscreen",
                    save: bool = False) -> dict[str, Any]:
        from kipy.geometry import Vector2
        from kipy.board_types import BoardText
        board = self._board()
        t = BoardText()
        t.value = str(text)
        t.position = Vector2.from_xy_mm(float(x_mm), float(y_mm))
        t.layer = self._layer_by_name(board, layer)

        created: list[Any] = []

        def apply():
            nonlocal created
            created = board.create_items([t])
        self._apply(board, apply, save)
        return {"text": text, "x_mm": x_mm, "y_mm": y_mm, "layer": layer,
                "created": len(created), "saved": save}

    def create_line(self, x1_mm: float, y1_mm: float, x2_mm: float,
                    y2_mm: float, layer: str = "F.Silkscreen",
                    width_mm: float = 0.15,
                    save: bool = False) -> dict[str, Any]:
        from kipy.geometry import Vector2
        from kipy.board_types import BoardSegment
        board = self._board()
        s = BoardSegment()
        s.start = Vector2.from_xy_mm(float(x1_mm), float(y1_mm))
        s.end = Vector2.from_xy_mm(float(x2_mm), float(y2_mm))
        s.layer = self._layer_by_name(board, layer)
        try:
            s.width = int(round(float(width_mm) * _NM_PER_MM))
        except Exception:
            pass

        created: list[Any] = []

        def apply():
            nonlocal created
            created = board.create_items([s])
        self._apply(board, apply, save)
        return {"layer": layer, "width_mm": width_mm,
                "start_mm": [x1_mm, y1_mm], "end_mm": [x2_mm, y2_mm],
                "created": len(created), "saved": save}

    def create_circle(self, cx_mm: float, cy_mm: float, radius_mm: float,
                      layer: str = "F.Silkscreen", width_mm: float = 0.15,
                      save: bool = False) -> dict[str, Any]:
        from kipy.geometry import Vector2
        from kipy.board_types import BoardCircle
        board = self._board()
        c = BoardCircle()
        c.center = Vector2.from_xy_mm(float(cx_mm), float(cy_mm))
        c.radius = int(round(float(radius_mm) * _NM_PER_MM))
        c.layer = self._layer_by_name(board, layer)
        try:
            c.width = int(round(float(width_mm) * _NM_PER_MM))
        except Exception:
            pass

        created: list[Any] = []

        def apply():
            nonlocal created
            created = board.create_items([c])
        self._apply(board, apply, save)
        return {"layer": layer, "center_mm": [cx_mm, cy_mm],
                "radius_mm": radius_mm, "created": len(created), "saved": save}

    def create_rectangle(self, x1_mm: float, y1_mm: float, x2_mm: float,
                         y2_mm: float, layer: str = "F.Silkscreen",
                         width_mm: float = 0.15,
                         save: bool = False) -> dict[str, Any]:
        from kipy.geometry import Vector2
        from kipy.board_types import BoardRectangle
        board = self._board()
        r = BoardRectangle()
        r.top_left = Vector2.from_xy_mm(float(x1_mm), float(y1_mm))
        r.bottom_right = Vector2.from_xy_mm(float(x2_mm), float(y2_mm))
        r.layer = self._layer_by_name(board, layer)
        try:
            r.width = int(round(float(width_mm) * _NM_PER_MM))
        except Exception:
            pass

        created: list[Any] = []

        def apply():
            nonlocal created
            created = board.create_items([r])
        self._apply(board, apply, save)
        return {"layer": layer, "top_left_mm": [x1_mm, y1_mm],
                "bottom_right_mm": [x2_mm, y2_mm], "created": len(created),
                "saved": save}

    def create_arc(self, x1_mm: float, y1_mm: float, mid_x_mm: float,
                   mid_y_mm: float, x2_mm: float, y2_mm: float,
                   layer: str = "F.Silkscreen", width_mm: float = 0.15,
                   save: bool = False) -> dict[str, Any]:
        from kipy.geometry import Vector2
        from kipy.board_types import BoardArc
        board = self._board()
        a = BoardArc()
        a.start = Vector2.from_xy_mm(float(x1_mm), float(y1_mm))
        a.mid = Vector2.from_xy_mm(float(mid_x_mm), float(mid_y_mm))
        a.end = Vector2.from_xy_mm(float(x2_mm), float(y2_mm))
        a.layer = self._layer_by_name(board, layer)
        try:
            a.width = int(round(float(width_mm) * _NM_PER_MM))
        except Exception:
            pass

        created: list[Any] = []

        def apply():
            nonlocal created
            created = board.create_items([a])
        self._apply(board, apply, save)
        return {"layer": layer, "start_mm": [x1_mm, y1_mm],
                "mid_mm": [mid_x_mm, mid_y_mm], "end_mm": [x2_mm, y2_mm],
                "created": len(created), "saved": save}

    def create_zone(self, net: str, points_mm: list,
                    layer: str = "F.Cu", name: str = "", priority: int = 0,
                    save: bool = False) -> dict[str, Any]:
        from kipy.geometry import (Vector2, PolyLine, PolyLineNode,
                                   PolygonWithHoles)
        from kipy.board_types import Zone, Net
        if not points_mm or len(points_mm) < 3:
            raise KiCadNotReachableError(
                "a zone outline needs at least 3 points")
        board = self._board()
        pl = PolyLine()
        for pt in points_mm:
            pl.append(PolyLineNode.from_point(
                Vector2.from_xy_mm(float(pt[0]), float(pt[1]))))
        poly = PolygonWithHoles()
        poly.outline = pl
        z = Zone()
        z.outline = poly
        z.layers = [self._layer_by_name(board, layer)]
        if name:
            z.name = name
        try:
            z.priority = int(priority)
        except Exception:
            pass
        if net:
            z.net = Net(name=str(net))

        created: list[Any] = []

        def apply():
            nonlocal created
            created = board.create_items([z])
        self._apply(board, apply, save)
        return {"net": net, "layer": layer, "name": name,
                "point_count": len(points_mm), "created": len(created),
                "saved": save}

    def set_text_variable(self, key: str, value: str,
                          save: bool = False) -> dict[str, Any]:
        """Set (merge) one project text variable."""
        board = self._board()
        proj = board.get_project()
        tv = proj.get_text_variables()
        tv[str(key)] = str(value)
        proj.set_text_variables(tv)  # default merge keeps other variables
        if save:
            try:
                board.save()
            except Exception:
                pass
        return {"key": key, "value": value, "saved": save}

    def run_action(self, action: str) -> dict[str, Any]:
        """Run an arbitrary KiCad tool action by name. Unstable KiCad API."""
        k = self._kicad()
        resp = k.run_action(str(action))
        return {"action": action, "status": str(resp)}

    def save_board(self) -> dict[str, Any]:
        board = self._board()
        board.save()
        return {"saved": True}

    def _pcb_document(self):
        """The first open PCB document specifier, or a clear error."""
        k = self._kicad()
        try:
            from kipy.proto.common.types import DocumentType
            docs = list(k.get_open_documents(DocumentType.DOCTYPE_PCB))
        except Exception as e:
            raise KiCadNotReachableError(
                "could not list open PCB documents (" + str(e) + ")") from None
        if not docs:
            raise KiCadNotReachableError(
                "no PCB document is open in the KiCad PCB editor")
        return docs[0]

    def board_file_path(self) -> str:
        """Absolute path of the open board's ``.kicad_pcb`` file on disk."""
        import os
        doc = self._pcb_document()
        fname = getattr(doc, "board_filename", "") or ""
        proj = getattr(doc, "project", None)
        proj_path = (getattr(proj, "path", "") or "") if proj is not None else ""
        if fname and os.path.isabs(fname):
            return fname
        if fname and proj_path:
            base = proj_path
            # project.path may be the directory or the .kicad_pro file.
            if base.lower().endswith(".kicad_pro") or os.path.isfile(base):
                base = os.path.dirname(base)
            return os.path.join(base, fname)
        raise KiCadNotReachableError(
            "could not resolve the board file path; save the board first")

    def kicad_cli_path(self) -> str:
        """Path to KiCad's bundled ``kicad-cli`` executable."""
        k = self._kicad()
        try:
            path = str(k.get_kicad_binary_path("kicad-cli"))
        except Exception as e:
            raise KiCadNotReachableError(
                "kicad-cli path unavailable (" + str(e) + ")") from None
        return path

    def board_stats(self) -> dict[str, Any]:
        """Object counts and layer count for the open board."""
        board = self._board()

        def _n(getter) -> Optional[int]:
            try:
                return len(list(getter()))
            except Exception:
                return None

        layers = None
        try:
            layers = len([l for l in board.get_stackup().layers])
        except Exception:
            pass
        name = None
        try:
            name = board.name
        except Exception:
            pass
        return {
            "name": name,
            "footprints": _n(board.get_footprints),
            "nets": _n(board.get_nets),
            "pads": _n(board.get_pads),
            "tracks": _n(board.get_tracks),
            "vias": _n(board.get_vias),
            "zones": _n(board.get_zones),
            "stackup_layers": layers,
        }


_bridge: Optional[KiCadBridge] = None


def get_kicad_bridge() -> KiCadBridge:
    """Process-wide KiCad bridge singleton."""
    global _bridge
    if _bridge is None:
        _bridge = KiCadBridge()
    return _bridge


def reset_kicad_bridge() -> None:
    global _bridge
    _bridge = None
