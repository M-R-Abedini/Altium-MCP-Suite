# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Library inventory snapshot, what the planner sees as 'available parts'.

The planner reads this to bias its part choices toward libs the user
already has. NDA scope: the snapshot only includes paths the caller
explicitly hands in (the user's own neutral standard libraries). It does
not crawl the current project's local libs, those may be client-specific
and re-using their content across engagements would breach NDA.

Two modes:

* ``snapshot_live(...)``, opens each SchLib in Altium and queries the
  bridge. Use during a real session.
* ``LibraryInventory.from_json_file(...)``, loads a previously saved
  snapshot. Lets the planner iterate offline without an Altium round-trip.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("eda_agent.design.inventory")


class ComponentSummary(BaseModel):
    """One component row the planner sees.

    Carries the atomic-parts contract fields (mpn, manufacturer,
    footprint, datasheet) so the planner can populate ``Part`` directly
    from the inventory without a second round-trip. Pascal fills these
    from the live ISch_Component's parameters (``Manufacturer Part
    Number``, ``Manufacturer``, ``Datasheet``) and its current
    implementation (``ModelName`` -> footprint) when the snapshot is
    requested with parameters.
    """

    model_config = ConfigDict(extra="ignore")

    lib_ref: str = Field(min_length=1)
    designator_prefix: Optional[str] = None
    description: Optional[str] = None
    pin_count: Optional[int] = None
    footprint: Optional[str] = None
    mpn: Optional[str] = None
    manufacturer: Optional[str] = None
    datasheet: Optional[str] = None
    parameters: dict[str, str] = Field(default_factory=dict)


class LibrarySummary(BaseModel):
    """One library row, path + components."""

    model_config = ConfigDict(extra="ignore")

    path: str = Field(min_length=1)
    components: list[ComponentSummary] = Field(default_factory=list)


class LibraryInventory(BaseModel):
    """The planner-visible inventory: a flat list of libraries."""

    model_config = ConfigDict(extra="forbid")

    libraries: list[LibrarySummary] = Field(default_factory=list)

    def total_components(self) -> int:
        return sum(len(lib.components) for lib in self.libraries)

    def find(self, lib_ref: str) -> Optional[tuple[str, ComponentSummary]]:
        """Look up a component by lib_ref. Returns (lib_path, summary) or None."""
        for lib in self.libraries:
            for comp in lib.components:
                if comp.lib_ref == lib_ref:
                    return lib.path, comp
        return None

    @classmethod
    def from_json_file(cls, path: Path) -> "LibraryInventory":
        with open(path, "r", encoding="utf-8") as f:
            return cls.model_validate(json.load(f))

    def to_json_file(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.model_dump_json(indent=2))


def _component_summary_from_raw(row: dict) -> ComponentSummary:
    """Map a raw library.get_components row into a ComponentSummary.

    The bridge response shape is permissive, different Altium builds /
    component types use different field names. We try the common ones.

    Atomic-parts fields (mpn, manufacturer, datasheet, footprint) are
    read from the top-level row first (Pascal exposes them explicitly
    when with_parameters=true) and fall back to the ``parameters`` dict
    using the canonical Altium parameter names.
    """

    def _get(*keys: str) -> Optional[str]:
        for k in keys:
            v = row.get(k)
            if v not in (None, ""):
                return v if isinstance(v, str) else str(v)
        return None

    pin_count: Optional[int] = None
    raw_pin_count = row.get("pin_count") or row.get("PinCount") or row.get("pins")
    if isinstance(raw_pin_count, int):
        pin_count = raw_pin_count
    elif isinstance(raw_pin_count, str) and raw_pin_count.isdigit():
        pin_count = int(raw_pin_count)
    elif isinstance(raw_pin_count, list):
        pin_count = len(raw_pin_count)

    raw_params = row.get("parameters")
    parameters: dict[str, str] = {}
    if isinstance(raw_params, dict):
        for k, v in raw_params.items():
            if v in (None, ""):
                continue
            parameters[str(k)] = v if isinstance(v, str) else str(v)

    def _param(*keys: str) -> Optional[str]:
        """Case-insensitive lookup into the parameters dict."""
        if not parameters:
            return None
        lowered = {k.lower(): v for k, v in parameters.items()}
        for k in keys:
            v = lowered.get(k.lower())
            if v not in (None, ""):
                return v
        return None

    mpn = _get("mpn", "MPN") or _param(
        "Manufacturer Part Number",
        "ManufacturerPartNumber",
        "Manufacturer_Part_Number",
        "MPN",
        "Part Number",
        "PartNumber",
    )
    manufacturer = _get("manufacturer", "Manufacturer") or _param(
        "Manufacturer", "Mfr", "Mfg"
    )
    datasheet = _get("datasheet", "Datasheet", "datasheet_url") or _param(
        "Datasheet", "DatasheetURL", "Datasheet URL", "ComponentLink1URL"
    )
    footprint = _get("footprint", "Footprint", "DefaultFootprint") or _param(
        "Footprint"
    )

    return ComponentSummary(
        lib_ref=_get("name", "lib_ref", "Name", "ComponentName") or "",
        designator_prefix=_get("designator_prefix", "Designator", "DesignatorPrefix"),
        description=_get("description", "Description"),
        pin_count=pin_count,
        footprint=footprint,
        mpn=mpn,
        manufacturer=manufacturer,
        datasheet=datasheet,
        parameters=parameters,
    )


def snapshot_live(library_paths: list[Path]) -> LibraryInventory:
    """Pull a component summary for each SchLib path.

    The Pascal handler reads the SchLib from disk via
    ``SchServer.CreateLibCompInfoReader(library_path)``, so the lib does
    not need to be open in Altium first.

    The caller controls scope by handing in an explicit path list. We never
    crawl. Project-local libs stay out of the snapshot unless the caller
    asks for them by hand, which is the right boundary for NDA isolation.
    """
    from eda_agent.bridge import get_bridge  # late import, bridge needs Altium running

    bridge = get_bridge()
    libs: list[LibrarySummary] = []

    for raw_path in library_paths:
        lib_path = Path(raw_path).expanduser().resolve()
        if not lib_path.exists():
            logger.warning("library path missing: %s", lib_path)
            libs.append(LibrarySummary(path=str(lib_path)))
            continue

        try:
            # with_parameters=true makes Pascal walk each component's live
            # parameter set + current footprint implementation, populating
            # the atomic-parts fields (mpn, manufacturer, datasheet,
            # footprint) on every row. Slower than the metadata-only path,
            # but the planner needs these fields to populate Part for an
            # atomic-parts-clean BOM, so the snapshot path opts in.
            raw = bridge.send_command(
                "library.get_components",
                {"library_path": str(lib_path), "with_parameters": "true"},
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("library.get_components failed for %s: %s", lib_path, exc)
            libs.append(LibrarySummary(path=str(lib_path)))
            continue

        rows = []
        if isinstance(raw, dict):
            rows = raw.get("components") or raw.get("results") or []
        elif isinstance(raw, list):
            rows = raw

        components = [_component_summary_from_raw(r) for r in rows if isinstance(r, dict)]
        components = [c for c in components if c.lib_ref]
        libs.append(LibrarySummary(path=str(lib_path), components=components))

    return LibraryInventory(libraries=libs)
