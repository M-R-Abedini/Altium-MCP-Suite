# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Fab capability profile: the manufacturer's published limits as data.

Design-rule numbers must come from the FAB, never from this codebase: a
:class:`FabProfile` carries the minimum track/gap/drill/annular figures and
the verified stackup geometry, and every value in it must be TRANSCRIBED
from the fab's published capability page (record the page URL and date in
``source``). This module ships ZERO capability numbers of its own -- it only
validates the shape and physical sanity of what the user supplies, and
``rule_synthesis`` refuses to invent anything the profile doesn't carry.

All linear dimensions are MILS (floats at this layer; rule synthesis rounds
to the integer mils the PCB wrappers transmit). Copper weight is oz/ft^2.
Pure offline, no Altium.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class StackupLayer(BaseModel):
    """One ply of a stackup: copper foil or a dielectric (core / prepreg)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    kind: Literal["copper", "core", "prepreg"]
    thickness_mils: float = Field(gt=0)
    er: Optional[float] = Field(
        default=None, gt=0,
        description="Relative permittivity. Dielectric plies only; needed "
        "for impedance sizing.",
    )
    copper_oz: Optional[float] = Field(
        default=None, gt=0,
        description="Copper weight in oz/ft^2. Copper plies only.",
    )

    @model_validator(mode="after")
    def _fields_match_kind(self) -> "StackupLayer":
        if self.kind == "copper" and self.er is not None:
            raise ValueError(f"layer {self.name!r}: er is meaningless on copper")
        if self.kind != "copper" and self.copper_oz is not None:
            raise ValueError(
                f"layer {self.name!r}: copper_oz on a {self.kind} ply")
        return self


class Stackup(BaseModel):
    """A named layer stack, top to bottom, as the fab builds it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    layers: list[StackupLayer] = Field(min_length=1)

    @model_validator(mode="after")
    def _sane_stack(self) -> "Stackup":
        kinds = [layer.kind for layer in self.layers]
        if kinds.count("copper") < 2:
            raise ValueError(
                f"stackup {self.name!r}: needs at least 2 copper layers")
        if kinds[0] != "copper" or kinds[-1] != "copper":
            raise ValueError(
                f"stackup {self.name!r}: outer layers must be copper")
        for a, b in zip(kinds, kinds[1:]):
            if a == "copper" and b == "copper":
                raise ValueError(
                    f"stackup {self.name!r}: adjacent copper layers with no "
                    f"dielectric between them")
        return self


class FabProfile(BaseModel):
    """A fab's published capability limits. All mins are MILS.

    Transcribe every number from the fab's capability page and cite the
    page in ``source`` -- nothing here may be guessed or remembered.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    source: Optional[str] = Field(
        default=None,
        description="Where the numbers came from: capability page URL plus "
        "the date transcribed.",
    )
    copper_layer_counts: list[int] = Field(
        min_length=1,
        description="Layer counts the fab offers, e.g. [2, 4, 6].",
    )
    min_track_mils: float = Field(gt=0)
    min_gap_mils: float = Field(gt=0)
    min_drill_mils: float = Field(gt=0, description="Minimum finished hole")
    min_annular_ring_mils: float = Field(gt=0)
    min_hole_to_hole_mils: float = Field(gt=0)
    min_mask_sliver_mils: float = Field(gt=0)
    min_silk_width_mils: float = Field(gt=0)
    stackups: list[Stackup] = Field(default_factory=list)

    @model_validator(mode="after")
    def _counts_positive_and_stackups_offered(self) -> "FabProfile":
        for n in self.copper_layer_counts:
            if n < 1:
                raise ValueError("copper_layer_counts entries must be >= 1")
        offered = set(self.copper_layer_counts)
        for stk in self.stackups:
            n_cu = sum(1 for layer in stk.layers if layer.kind == "copper")
            if n_cu not in offered:
                raise ValueError(
                    f"stackup {stk.name!r} has {n_cu} copper layers but the "
                    f"profile only offers {sorted(offered)}")
        return self


def load_fab_profile(
    source: Union[FabProfile, dict, str, Path],
) -> dict:
    """Load and validate a fab profile from a dict, a JSON file path, or an
    already-built :class:`FabProfile` (passthrough).

    Returns ``{"ok": True, "profile": FabProfile}`` or
    ``{"ok": False, "reason": str}``.
    """
    if isinstance(source, FabProfile):
        return {"ok": True, "profile": source}
    if isinstance(source, dict):
        data = source
    else:
        path = Path(source)
        if not path.is_file():
            return {"ok": False, "reason": f"profile file not found: {path}"}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "reason": f"cannot parse {path}: {exc}"}
        if not isinstance(data, dict):
            return {"ok": False, "reason": "profile JSON must be an object"}
    try:
        return {"ok": True, "profile": FabProfile.model_validate(data)}
    except ValidationError as exc:
        return {"ok": False, "reason": f"invalid fab profile: {exc}"}


def copper_layers(stackup: Stackup) -> list[StackupLayer]:
    """The copper plies of ``stackup``, top to bottom."""
    return [layer for layer in stackup.layers if layer.kind == "copper"]


@dataclass(frozen=True)
class DielectricSpan:
    """The dielectric between two consecutive copper layers, collapsed to
    the single-dielectric shape Altium's layer model (and the impedance
    closed forms) take. Multi-ply spans sum thicknesses and use the
    thickness-weighted mean er -- consistent with the +/-10 % accuracy of
    the IPC-2141 forms this feeds."""

    height_mils: float        # sum of ply thicknesses
    er: Optional[float]       # thickness-weighted mean; None if any ply lacks er
    kind: str                 # first ply's kind ("core" / "prepreg")
    ply_count: int


def dielectric_spans(stackup: Stackup) -> list[DielectricSpan]:
    """One :class:`DielectricSpan` per gap between consecutive copper layers,
    top to bottom (index 0 sits under the top copper)."""
    spans: list[DielectricSpan] = []
    run: list[StackupLayer] = []
    seen_copper = False
    for layer in stackup.layers:
        if layer.kind == "copper":
            if seen_copper and run:
                spans.append(_make_span(run))
            run = []
            seen_copper = True
        elif seen_copper:
            run.append(layer)
    return spans


def _make_span(plies: list[StackupLayer]) -> DielectricSpan:
    height = sum(p.thickness_mils for p in plies)
    if all(p.er is not None for p in plies):
        er = sum(p.thickness_mils * p.er for p in plies) / height
    else:
        er = None
    return DielectricSpan(
        height_mils=height, er=er, kind=plies[0].kind, ply_count=len(plies))


__all__ = [
    "StackupLayer",
    "Stackup",
    "FabProfile",
    "DielectricSpan",
    "load_fab_profile",
    "copper_layers",
    "dielectric_spans",
]
