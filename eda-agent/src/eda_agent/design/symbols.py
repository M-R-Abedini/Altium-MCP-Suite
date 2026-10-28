# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Symbol geometry models cached from SchLib.

A SymbolModel captures everything the layout / router / renderer need to
reason about a placed component without asking Altium for pin positions
during every iteration. Pins are stored in the symbol-local frame; the
canvas applies the placed instance's (x, y, rotation) to produce world
coordinates.

Caching: each SchLib is extracted once (via `lib_get_component_details`)
and cached on disk by full library path, reference, and content identity.
Library batches check identity at both boundaries. To force a
re-extract, delete the .symbol_cache/ directory or call
`SymbolCache.invalidate(lib_path)`.

Coordinate convention (matches Altium):
- All distances in mils.
- Pin (x, y) is the OUTSIDE/electrical endpoint where wires connect.
- Pin orientation: 0=right, 1=up, 2=left, 3=down (TRotationBy90 / 90 deg).
- Length is the visible pin stub length in mils; the body-attach end is
  at (x - length*dx, y - length*dy) where (dx, dy) is the direction the
  pin points outward.
"""

from __future__ import annotations

import json
import hashlib
import os
import uuid
from contextlib import contextmanager
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("eda_agent.design.symbols")


@dataclass(frozen=True)
class SymbolPin:
    """One pin in symbol-local coordinates."""

    designator: str  # pin number, e.g. "1", "8"
    name: str  # pin name, e.g. "VCC", "GND", "OUT"
    x: int  # symbol-local mils, OUTSIDE endpoint
    y: int
    orientation: int  # 0=right, 1=up, 2=left, 3=down
    length: int  # mils
    electrical_type: str  # input/output/passive/power/io/open_collector/...
    hidden: bool = False


@dataclass(frozen=True)
class SymbolBBox:
    """Axis-aligned bbox in symbol-local mils.

    Derived from pin positions plus a small padding so the bbox encloses
    the visible body. NOT the same as the symbol's rectangle primitive
    (which we'd need a separate Pascal call to read); for layout-collision
    purposes the pin-derived bbox is conservative enough.
    """

    x_min: int
    y_min: int
    x_max: int
    y_max: int

    @property
    def width(self) -> int:
        return self.x_max - self.x_min

    @property
    def height(self) -> int:
        return self.y_max - self.y_min


@dataclass(frozen=True)
class SymbolModel:
    """One library component, extracted once and cached.

    Keyed by (lib_path, lib_ref). Equality is by content so two equal
    SymbolModels from different cache lookups deduplicate cleanly.
    """

    lib_path: str
    lib_ref: str
    pins: tuple[SymbolPin, ...]
    body_bbox: SymbolBBox
    designator_prefix: str = "U"
    description: str = ""

    def pin_by_id(self, pin_id: str) -> Optional[SymbolPin]:
        """Look up a pin by its designator or name (designator first).

        DesignPlan PinRefs may reference either; the executor used to call a
        Pascal helper that did the same flexible lookup. We replicate that
        here so plan authors can keep using either form.
        """
        for pin in self.pins:
            if pin.designator == pin_id:
                return pin
        for pin in self.pins:
            if pin.name == pin_id:
                return pin
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "lib_path": self.lib_path,
            "lib_ref": self.lib_ref,
            "pins": [asdict(p) for p in self.pins],
            "body_bbox": asdict(self.body_bbox),
            "designator_prefix": self.designator_prefix,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SymbolModel":
        return cls(
            lib_path=d["lib_path"],
            lib_ref=d["lib_ref"],
            pins=tuple(SymbolPin(**p) for p in d["pins"]),
            body_bbox=SymbolBBox(**d["body_bbox"]),
            designator_prefix=d.get("designator_prefix", "U"),
            description=d.get("description", ""),
        )


# Pin orientation -> outward unit vector. Matches Altium's TRotationBy90
# (0=right=+X, 1=up=+Y, 2=left=-X, 3=down=-Y).
_PIN_DIRECTION: dict[int, tuple[int, int]] = {
    0: (1, 0),
    1: (0, 1),
    2: (-1, 0),
    3: (0, -1),
}


def pin_direction(orientation: int) -> tuple[int, int]:
    """Outward (dx, dy) unit vector for a pin at the given orientation."""
    return _PIN_DIRECTION[orientation % 4]


def _bbox_from_pins(pins: tuple[SymbolPin, ...]) -> SymbolBBox:
    """Compute a tight symbol bbox from pin endpoints.

    Altium API asymmetry, learned empirically:

    - For a pin on a SchLib component (what ``lib_get_component_details``
      returns and what this function consumes), ``Pin.Location`` is the
      BODY-ATTACH end -- the inside of the symbol where the pin meets
      the body rectangle. The pin extends from Location OUTWARD by
      ``length`` to the electrical end (where wires snap).
    - For a placed pin on a SchDoc (what world-coord routing consumes),
      ``Pin.Location`` is the ELECTRICAL end -- the wire-connection
      point in world coordinates.

    So for the body bounding box (used by overlap detection and the
    placement engine), we want the body-attach ends -- and those are
    already what ``pin.x`` / ``pin.y`` carry on a SymbolModel. No
    offset needed; previous +/- length * direction formulas were both
    wrong.
    """
    if not pins:
        return SymbolBBox(x_min=-100, y_min=-100, x_max=100, y_max=100)
    body_xs: list[int] = []
    body_ys: list[int] = []
    for pin in pins:
        body_xs.append(pin.x)
        body_ys.append(pin.y)
    # Pad the body by a tiny amount so a body that touches a pin (1-pin
    # special parts) still has non-zero area.
    pad = 50
    return SymbolBBox(
        x_min=min(body_xs) - pad,
        y_min=min(body_ys) - pad,
        x_max=max(body_xs) + pad,
        y_max=max(body_ys) + pad,
    )


def _designator_prefix_from_refdes_or_default(lib_ref: str) -> str:
    """Best-effort guess at a designator prefix from the symbol name.

    Only used as a last-resort fallback; the authoritative prefix is
    the symbol's own ``designator`` field (e.g. ``"U?"`` strips to
    ``"U"``) which the caller should prefer. We only sniff the lib_ref
    when its prefix follows a well-known passive convention like
    ``RES ``, ``CAP ``, ``LED ``, ``DIODE ``, ``IND ``. IC names do
    NOT follow a predictable lib_ref prefix so we default to ``U``;
    the caller's own designator field is the only reliable source for
    those.
    """
    name = lib_ref.upper().strip()
    if name.startswith("RES "):
        return "R"
    if name.startswith("CAP "):
        return "C"
    if name.startswith("LED "):
        return "D"
    if name.startswith("DIODE "):
        return "D"
    if name.startswith("IND ") or name.startswith("INDUCTOR"):
        return "L"
    return "U"


def parse_symbol_from_details(
    details: dict[str, Any], lib_path: str
) -> SymbolModel:
    """Parse a SymbolModel from a `lib_get_component_details` response.

    Pascal returns: {name, library_path, designator{}, comment{},
    description, alias_name, part_count, pin_count, pins[], parameters{},
    parameter_styles[]}.

    We only need name, description, pins. designator_prefix is heuristic
    when not available; the placed-instance refdes overrides it.
    """
    pins_raw = details.get("pins", []) or []
    pins: list[SymbolPin] = []
    for p in pins_raw:
        # `length` was added in script v2026.05.15.8; older Pascal builds
        # don't include it. Fall back to 200 (Altium default) so the
        # parser is forward-compatible during the transition.
        pin_length = int(p.get("length", 200))
        pins.append(SymbolPin(
            designator=str(p.get("designator", "")),
            name=str(p.get("name", "")),
            x=int(p.get("x", 0)),
            y=int(p.get("y", 0)),
            orientation=int(p.get("orientation", 0)) % 4,
            length=pin_length,
            electrical_type=str(p.get("electrical_type", "passive")),
            hidden=bool(p.get("hidden", False)),
        ))
    pins_tuple = tuple(pins)
    bbox = _bbox_from_pins(pins_tuple)
    lib_ref = str(details.get("name", ""))
    return SymbolModel(
        lib_path=lib_path,
        lib_ref=lib_ref,
        pins=pins_tuple,
        body_bbox=bbox,
        designator_prefix=_designator_prefix_from_refdes_or_default(lib_ref),
        description=str(details.get("description", "")),
    )


class SymbolCache:
    """Disk-backed cache bound to a full library path and on-disk content.

    Layout: one JSON file per resolved SchLib path. Each file
    holds {"identity": ..., "components": {lib_ref: SymbolModel}}. We
    write per-SchLib (not per-component) so a single SchLib edit
    invalidates the whole lib in one shot.
    """

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # In-memory mirror so repeated lookups within one run don't re-read disk.
        self._memory: dict[str, dict[str, Any]] = {}
        self._batch_identities: dict[str, Optional[dict[str, Any]]] = {}

    def _lib_cache_path(self, lib_path: str) -> Path:
        stem = Path(lib_path).stem
        key = hashlib.sha256(self._path_key(lib_path).encode("utf8")).hexdigest()[:24]
        return self.cache_dir / f"{stem}-{key}.json"

    @staticmethod
    def _path_key(lib_path: str) -> str:
        return os.path.normcase(str(Path(lib_path).resolve()))

    def _identity(self, lib_path: str) -> Optional[dict[str, Any]]:
        key = self._path_key(lib_path)
        if key in self._batch_identities:
            return self._batch_identities[key]
        return self._read_identity(lib_path)

    def _read_identity(self, lib_path: str) -> Optional[dict[str, Any]]:
        path = Path(lib_path)
        try:
            before = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            after = path.stat()
        except OSError:
            return None
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            return None
        return {"path": self._path_key(lib_path), "mtime_ns": after.st_mtime_ns,
                "size": after.st_size, "sha256": digest}

    @contextmanager
    def library_batch(self, lib_path: str):
        """Hash once at each batch boundary, not once per symbol.

        If a library changes while symbols are being collected, discard all
        results for that library; the caller must not emit mixed revisions.
        """
        key = self._path_key(lib_path)
        before = self._read_identity(lib_path)
        state = {"unchanged": False}
        self._batch_identities[key] = before
        try:
            yield state
        finally:
            self._batch_identities.pop(key, None)
            state["unchanged"] = before is not None and before == self._read_identity(lib_path)
            if not state["unchanged"]:
                self.invalidate(lib_path)

    def _load_lib(self, lib_path: str) -> Optional[dict[str, Any]]:
        if lib_path in self._memory:
            return self._memory[lib_path]
        cache_file = self._lib_cache_path(lib_path)
        if not cache_file.exists():
            return None
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("symbol cache read failed for %s: %s", cache_file, exc)
            return None
        self._memory[lib_path] = data
        return data

    def is_fresh(self, lib_path: str) -> bool:
        """True iff the saved on-disk library content still matches.

        This cannot establish freshness of unsaved edits in an open library.
        Call invalidate when the caller knows that a native library was edited.
        """
        data = self._load_lib(lib_path)
        if data is None:
            return False
        identity = self._identity(lib_path)
        return identity is not None and data.get("identity") == identity

    def get(self, lib_path: str, lib_ref: str) -> Optional[SymbolModel]:
        if not self.is_fresh(lib_path):
            return None
        data = self._load_lib(lib_path)
        if data is None:
            return None
        comps = data.get("components", {})
        raw = comps.get(lib_ref)
        if raw is None:
            return None
        try:
            model = SymbolModel.from_dict(raw)
            if self._path_key(model.lib_path) != self._path_key(lib_path) or model.lib_ref != lib_ref:
                return None
            return model
        except (KeyError, TypeError) as exc:
            logger.warning(
                "symbol cache corrupt entry %s/%s: %s", lib_path, lib_ref, exc
            )
            return None

    def put(self, model: SymbolModel) -> None:
        """Insert a model and persist the lib's cache file atomically."""
        identity = self._identity(model.lib_path)
        if identity is None:
            return
        data = self._load_lib(model.lib_path)
        # Never refresh a stale library's timestamp while retaining its old
        # components. One newly extracted symbol must not revive stale peers.
        if data is None or data.get("identity") != identity:
            data = {"identity": identity, "components": {}}
        data["components"][model.lib_ref] = model.to_dict()
        self._memory[model.lib_path] = data
        cache_file = self._lib_cache_path(model.lib_path)
        tmp = cache_file.with_suffix("." + uuid.uuid4().hex + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(cache_file)

    def invalidate(self, lib_path: str) -> None:
        self._memory.pop(lib_path, None)
        cache_file = self._lib_cache_path(lib_path)
        if cache_file.exists():
            cache_file.unlink()


class SymbolExtractor:
    """Pull SymbolModels from Altium via the bridge, route through cache.

    Single entrypoint: `extract_many([(lib_path, lib_ref), ...]) -> dict`.
    The dict is keyed by (lib_path, lib_ref) so the caller can look up a
    Part's symbol by the same key the plan carries.

    Failure mode: if a lib_get_component_details call errors (lib doesn't
    load, component name doesn't resolve), the failed (lib_path, lib_ref)
    is absent from the returned dict. Callers should check membership and
    treat absence as a hard plan-level error before continuing.
    """

    def __init__(self, bridge: Any, cache: SymbolCache) -> None:
        self.bridge = bridge
        self.cache = cache

    def extract_one(
        self, lib_path: str, lib_ref: str
    ) -> Optional[SymbolModel]:
        cached = self.cache.get(lib_path, lib_ref)
        if cached is not None:
            return cached
        # Cache miss: ask Altium. lib_get_component_details requires the
        # target SchLib to be loaded in the editor. The handler reopens
        # the lib via WorkspaceManager:OpenObject if it isn't already
        # focused, so we don't need a separate load step here.
        try:
            response = self.bridge.send_command(
                "library.get_component_details",
                {"component_name": lib_ref, "library_path": lib_path},
            )
        except Exception as exc:
            logger.warning(
                "lib_get_component_details failed for %s/%s: %s",
                lib_path, lib_ref, exc,
            )
            return None
        if not isinstance(response, dict):
            logger.warning(
                "lib_get_component_details unexpected response shape for %s/%s: %r",
                lib_path, lib_ref, response,
            )
            return None
        model = parse_symbol_from_details(response, lib_path)
        self.cache.put(model)
        return model

    def extract_many(
        self, refs: list[tuple[str, str]]
    ) -> dict[tuple[str, str], SymbolModel]:
        out: dict[tuple[str, str], SymbolModel] = {}
        # Group by lib_path so we hit each SchLib once (lib_get_component_details
        # reopens the lib per call; grouping cuts the cost when several
        # parts share a lib).
        by_lib: dict[str, list[str]] = {}
        for lib_path, lib_ref in refs:
            by_lib.setdefault(lib_path, []).append(lib_ref)
        for lib_path, lib_refs in by_lib.items():
            with self.cache.library_batch(lib_path) as snapshot:
                for lib_ref in dict.fromkeys(lib_refs):
                    model = self.extract_one(lib_path, lib_ref)
                    if model is not None:
                        out[(lib_path, lib_ref)] = model
            if not snapshot["unchanged"]:
                logger.warning("library changed during symbol batch: %s", lib_path)
                for lib_ref in lib_refs:
                    out.pop((lib_path, lib_ref), None)
        return out
