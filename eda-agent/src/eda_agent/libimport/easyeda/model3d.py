# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Convert EasyEDA's 3D model payload into KiCad VRML.

EasyEDA serves something that LOOKS like Wavefront OBJ but is not: the
material library is INLINED in the same file rather than living in a
separate ``.mtl`` referenced by ``mtllib``. A stock OBJ reader either
rejects the ``newmtl``/``Ka``/``endmtl`` lines or silently drops every
colour, which is why this parser is hand written.

Observed shape of a real payload (verified against a live model):

    v -2.325 1.95 0.8         <- vertices, millimetres, Z up
    newmtl 1                  <- material block, interleaved with them
    Ka 0.223 0.223 0.223
    Kd 0.223 0.223 0.223
    Ks 0.113 0.113 0.113
    d 0.0
    endmtl
    ...
    usemtl 1                  <- binds the faces that follow
    f 1// 2// 3//             <- triangles, 1-based, empty uv/normal slots

Two conventions worth stating because they are easy to get backwards:

* ``d`` is TRANSPARENCY here, not OBJ's "dissolve". Standard MTL treats
  ``d 1.0`` as opaque; EasyEDA emits ``d 0.0`` for a solid black plastic
  body, so the value maps straight onto VRML ``transparency``.
* KiCad's VRML models are in units of 0.1 inch, not millimetres, which
  is what lets a footprint reference them with ``(scale (xyz 1 1 1))``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

__all__ = ["Material", "Model3D", "obj_to_wrl", "parse_easyeda_obj"]

#: KiCad VRML models are expressed in 0.1 inch units.
_MM_PER_VRML_UNIT = 2.54

_DEFAULT_DIFFUSE = (0.7, 0.7, 0.7)


@dataclass
class Material:
    """One inlined ``newmtl`` block."""

    name: str
    ambient: tuple[float, float, float] = _DEFAULT_DIFFUSE
    diffuse: tuple[float, float, float] = _DEFAULT_DIFFUSE
    specular: tuple[float, float, float] = (0.0, 0.0, 0.0)
    transparency: float = 0.0


@dataclass
class Model3D:
    """Vertices plus per-material triangle groups."""

    vertices: list[tuple[float, float, float]] = field(default_factory=list)
    materials: dict[str, Material] = field(default_factory=dict)
    #: (material name, [(i, j, k), ...]) with 0-based vertex indices.
    groups: list[tuple[str, list[tuple[int, int, int]]]] = field(
        default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def triangle_count(self) -> int:
        return sum(len(tris) for _, tris in self.groups)


def _floats(parts: list[str], n: int) -> tuple[float, ...]:
    out: list[float] = []
    for tok in parts[:n]:
        try:
            out.append(float(tok))
        except ValueError:
            out.append(0.0)
    while len(out) < n:
        out.append(0.0)
    return tuple(out)


def _face_index(token: str) -> Optional[int]:
    """First field of an OBJ face token: ``5``, ``5/1``, ``5//`` all give 5."""
    head = token.split("/", 1)[0].strip()
    if not head:
        return None
    try:
        return int(head)
    except ValueError:
        return None


def parse_easyeda_obj(text: str) -> Model3D:
    """Parse the OBJ/MTL hybrid EasyEDA serves for a footprint model."""
    model = Model3D()
    current_mtl: Optional[Material] = None
    dropped_faces = [0]
    active = ""
    # Faces are appended to the group opened by the most recent usemtl.
    group_index: dict[str, int] = {}

    def group_for(name: str) -> list[tuple[int, int, int]]:
        if name not in group_index:
            group_index[name] = len(model.groups)
            model.groups.append((name, []))
        return model.groups[group_index[name]][1]

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        tag = parts[0]

        if tag == "v":
            model.vertices.append(_floats(parts[1:], 3))  # type: ignore[arg-type]
        elif tag == "newmtl":
            current_mtl = Material(name=parts[1] if len(parts) > 1 else "")
        elif tag == "endmtl":
            if current_mtl is not None:
                model.materials[current_mtl.name] = current_mtl
                current_mtl = None
        elif current_mtl is not None and tag in ("Ka", "Kd", "Ks", "d", "Tr"):
            if tag == "Ka":
                current_mtl.ambient = _floats(parts[1:], 3)  # type: ignore
            elif tag == "Kd":
                current_mtl.diffuse = _floats(parts[1:], 3)  # type: ignore
            elif tag == "Ks":
                current_mtl.specular = _floats(parts[1:], 3)  # type: ignore
            else:
                current_mtl.transparency = _floats(parts[1:], 1)[0]
        elif tag == "usemtl":
            active = parts[1] if len(parts) > 1 else ""
            group_for(active)
        elif tag == "f":
            idx = [_face_index(t) for t in parts[1:]]
            verts = [i for i in idx if i is not None]
            if len(verts) < 3:
                continue
            # OBJ indices are 1-based and may be negative (relative to the
            # end of the vertex list so far).
            resolved: list[int] = []
            for i in verts:
                resolved.append(len(model.vertices) + i if i < 0 else i - 1)
            if any(i < 0 or i >= len(model.vertices) for i in resolved):
                # Face references a vertex that does not exist (or not
                # yet). Dropping it silently loses surface with nothing
                # to notice, so count them and report below.
                dropped_faces[0] += 1
                continue
            tris = group_for(active)
            # Fan-triangulate anything with more than three corners.
            for k in range(1, len(resolved) - 1):
                tris.append((resolved[0], resolved[k], resolved[k + 1]))

    if not model.vertices:
        model.warnings.append("model payload carries no vertices")
    elif not model.triangle_count:
        # Vertices but no usable faces still writes a syntactically fine
        # VRML file with nothing in it, which looks like success.
        model.warnings.append(
            f"{len(model.vertices)} vertices but no usable faces; the "
            f"model would render as nothing")
    if dropped_faces[0]:
        model.warnings.append(
            f"{dropped_faces[0]} face(s) referenced vertices that do not "
            f"exist and were dropped; the model is missing surface")
    missing = [n for n, _ in model.groups if n and n not in model.materials]
    if missing:
        model.warnings.append(
            f"{len(missing)} face group(s) reference an undefined material "
            f"({', '.join(sorted(set(missing)))}); a default grey is used")
    return model


def _fmt(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def obj_to_wrl(model: Model3D, *, name: str = "model") -> str:
    """VRML97 text for KiCad, in 0.1 inch units with materials preserved."""
    if not model.vertices:
        raise ValueError("model has no vertices")

    k = 1.0 / _MM_PER_VRML_UNIT
    out: list[str] = ["#VRML V2.0 utf8",
                      f"# {name}",
                      "# Converted from an EasyEDA 3D model by eda-agent.",
                      ""]

    points = ",\n          ".join(
        f"{_fmt(x * k)} {_fmt(y * k)} {_fmt(z * k)}"
        for (x, y, z) in model.vertices)

    first = True
    for mtl_name, tris in model.groups:
        if not tris:
            continue
        mtl = model.materials.get(mtl_name) or Material(name=mtl_name)
        out.append("Shape {")
        out.append("  appearance Appearance {")
        out.append("    material Material {")
        out.append(f"      diffuseColor {_fmt(mtl.diffuse[0])} "
                   f"{_fmt(mtl.diffuse[1])} {_fmt(mtl.diffuse[2])}")
        out.append(f"      specularColor {_fmt(mtl.specular[0])} "
                   f"{_fmt(mtl.specular[1])} {_fmt(mtl.specular[2])}")
        out.append(f"      ambientIntensity {_fmt(_intensity(mtl.ambient))}")
        out.append(f"      transparency {_fmt(_clamp01(mtl.transparency))}")
        out.append("    }")
        out.append("  }")
        out.append("  geometry IndexedFaceSet {")
        if first:
            # One shared vertex array: DEF it on the first shape and USE
            # it afterwards, rather than repeating ~1000 points per
            # material group.
            out.append("    coord DEF EEVERTS Coordinate {")
            out.append(f"      point [ {points} ]")
            out.append("    }")
            first = False
        else:
            out.append("    coord USE EEVERTS")
        idx = ",\n      ".join(f"{a},{b},{c},-1" for (a, b, c) in tris)
        out.append(f"    coordIndex [ {idx} ]")
        out.append("    solid FALSE")
        out.append("  }")
        out.append("}")
        out.append("")

    return "\n".join(out)


def _clamp01(v: float) -> float:
    return 0.0 if v < 0 else (1.0 if v > 1 else v)


def _intensity(rgb: tuple[float, float, float]) -> float:
    return _clamp01(sum(rgb) / 3.0)
