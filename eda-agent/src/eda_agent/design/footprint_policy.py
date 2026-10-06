# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Footprint-library policy / consistency auditor (offline engine).

A PcbLib should apply the same conventions to every footprint: silkscreen on
one layer, assembly on one mechanical layer, a courtyard on every part, a
pin-1 marker, a 3D body, a consistent designator height, and so on. Drift
creeps in as a library grows across authors and years, and it is exactly the
kind of thing a machine should catch.

This module is the pure-Python analysis core. It takes a list of parsed
footprints (geometry already extracted from Altium: see the schema below)
and, for each **policy dimension**, does one of two things:

- **Inferred mode (default):** learns the library's OWN dominant convention
  by majority vote across footprints, then flags every footprint that
  deviates. This is deliberately library-agnostic: it never hard-codes
  "silk must be on Top Overlay" or "assembly is Mechanical 13", because those
  differ per house style. It reports *inconsistency*, which is the real
  defect in a mature library.
- **Explicit mode:** when a ``policy`` dict pins a dimension to a required
  value, footprints are checked against that instead of the inferred norm,
  for enforcing a documented standard.

The live bridge / tool layer supplies the footprint geometry; this module is
Altium-free and fully unit-testable. Findings are structured so a caller can
both *report* inconsistencies and drive *fixes* (each finding names the
footprint, the dimension, the expected value, and the actual value).

Footprint schema (all keys optional; richer data → more checks):

    {
      "name": str,
      "pads": [{"name", "shape", "layer", "size_x", "size_y", "hole",
                "rotation"}],                        # from lib_get_footprint_pads
      "texts": [{"text", "layer", "kind", "height", "x", "y"}],
                                            # kind: designator|comment|free
      "primitives": [{"kind", "layer", "width"}],      # track|arc|region|fill|...
      "pad_center": {"x", "y"},             # average pad centre, in mils
      "bodies": int,                                   # count of 3D body objects
    }
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Optional

ERROR = "error"
WARNING = "warning"
INFO = "info"

# Layer-name substrings that classify a primitive's role, case-folded. These
# only *classify* a primitive by the layer it sits on; they never assert which
# layer is "correct": that is inferred per library.
_SILK_HINTS = ("overlay", "silk")
_ASSEMBLY_HINTS = ("assembly", "assy", "fab")
_COURTYARD_HINTS = ("courtyard", "court", "place bound", "placement")


def _finding(footprint: str, dimension: str, severity: str, message: str,
             expected: Any = None, actual: Any = None,
             target: Any = None) -> dict:
    return {"footprint": footprint, "dimension": dimension,
            "severity": severity, "message": message,
            "expected": expected, "actual": actual,
            "target": target}  # the specific object (e.g. pad name), if any


def _dominant(values) -> Optional[Any]:
    """Most common non-None value, or None if there are none."""
    counts = Counter(v for v in values if v is not None)
    if not counts:
        return None
    return counts.most_common(1)[0][0]


# Altium's fixed layers. Everything else a footprint draws on is a mechanical
# layer, whatever it has been renamed to. Compared with spaces stripped and
# case folded, so "Top Overlay" and "TopOverlay" are the same layer.
_STANDARD_LAYERS = frozenset({
    # Altium's names.
    "toplayer", "bottomlayer", "multilayer",
    "topoverlay", "bottomoverlay",
    "toppaste", "bottompaste",
    "topsolder", "bottomsolder",
    "keepoutlayer", "drillguide", "drilldrawing",
    # EasyEDA's names, MEASURED from pcb.layers on a live
    # 92-layer board rather than guessed. Only the two copper layers
    # happen to normalise to the same string as Altium's; every other
    # name differs, so this audit saw a standard EasyEDA stackup as a
    # pile of non-standard layers.
    #
    # The inner layers cannot be listed. EasyEDA reports them with
    # type SIGNAL and whatever name the user gave them, measured as
    # "Int1 (GND)" and "Inner7" on that board, so a name alone cannot
    # say they are standard. _STANDARD_PREFIXES below catches Altium's
    # midlayer convention and nothing catches EasyEDA's, which is a
    # known limit rather than an oversight: the type field says it and
    # this function only receives a name.
    "topsilkscreenlayer", "bottomsilkscreenlayer",
    "topsoldermasklayer", "bottomsoldermasklayer",
    "toppastemasklayer", "bottompastemasklayer",
    "topassemblylayer", "bottomassemblylayer",
    "boardoutlinelayer", "multi-layer", "documentlayer",
    "mechanicallayer",
    # The rest of the measured stackup. These are standard EasyEDA
    # layers a footprint can legitimately draw on, and leaving them out
    # made the audit report a perfectly ordinary part as using thirteen
    # non-standard layers.
    "holelayer", "componentshapelayer", "componentmarkinglayer",
    "pinsolderinglayer", "pinfloatinglayer", "componentmodellayer",
    "3dshelloutlinelayer", "3dshelltoplayer", "3dshellbottomlayer",
    "drilldrawinglayer", "ratlinelayer",
    "topstiffenerlayer", "bottomstiffenerlayer",
})
_STANDARD_PREFIXES = ("midlayer", "internalplane")


def _norm_layer(name: str) -> str:
    return "".join(str(name).split()).lower()


def _is_standard_layer(name: str) -> bool:
    n = _norm_layer(name)
    return n in _STANDARD_LAYERS or n.startswith(_STANDARD_PREFIXES)


def _layer_of(item: dict) -> str:
    """The layer identity of a primitive or text.

    Altium builds carry more mechanical layers than the bridge's name table
    maps, so a layer can come back as ``'Unknown'`` (or blank). Fall back to the
    raw ``layer_id`` ordinal: it is not human-friendly, but it is a STABLE and
    DISTINCT identity. Without it every unnamed layer collapses into one
    ``'Unknown'`` bucket and the layer checks silently compare unrelated layers.
    """
    name = (item.get("layer") or "").strip()
    if name and name.lower() != "unknown":
        return name
    lid = item.get("layer_id")
    if lid is not None and lid != -1:
        return f"Layer{lid}"
    return name


# --- per-footprint feature extraction ---------------------------------------
def _texts(fp: dict) -> list[dict]:
    return fp.get("texts") or []


def _designator_text(fp: dict) -> Optional[dict]:
    for t in _texts(fp):
        if (t.get("kind") or "").lower() == "designator":
            return t
    return None


def _designator_count(fp: dict) -> int:
    """How many .Designator strings the footprint carries.

    A footprint must have exactly one. More than one renders the designator
    twice on the board and is always a defect -- typically a tool that failed
    to recognise the existing string and added another.
    """
    n = fp.get("designator_count")
    if isinstance(n, int):
        return n
    return sum(1 for t in _texts(fp)
               if (t.get("kind") or "").lower() == "designator")


def _check_duplicate_designators(footprints, policy):
    findings = []
    for fp in footprints:
        n = _designator_count(fp)
        if n > 1:
            findings.append(_finding(
                fp.get("name", "?"), "duplicate_designator", ERROR,
                f"{n} .Designator strings: a footprint must have exactly one; "
                f"the extras render on top of each other",
                expected=1, actual=n))
    return None, findings


def _role_layers(fp: dict, role_name: str, hints) -> set:
    """Layers of the silkscreen/assembly/courtyard GRAPHICS of a footprint.

    Considers primitives and FREE texts only (the designator/comment labels
    have their own dimension). An item is attributed to the role if it carries
    an explicit ``role`` (from Altium's mechanical-layer kind: the reliable
    signal, since a layer named "Mechanical 13" gives no hint), else by a
    name-substring fallback.
    """
    out: set = set()
    free_texts = [t for t in _texts(fp) if (t.get("kind") or "").lower() == "free"]
    for item in (fp.get("primitives") or []) + free_texts:
        role = (item.get("role") or "").lower()
        name = (item.get("layer") or "")
        if role == role_name or (not role and any(h in name.lower() for h in hints)):
            out.add(_layer_of(item))
    return out


def _has_courtyard(fp: dict) -> bool:
    return bool(_role_layers(fp, "courtyard", _COURTYARD_HINTS))


def _body_count(fp: dict) -> int:
    b = fp.get("bodies")
    return b if isinstance(b, int) else 0


def _pin1_pad(fp: dict) -> Optional[dict]:
    for pad in fp.get("pads") or []:
        if str(pad.get("name")).strip() in ("1", "A1", "A"):
            return pad
    return None


def _has_pin1_marker(fp: dict) -> bool:
    """A pin-1 indicator: pad 1 shaped differently from the rest, OR a
    silkscreen/assembly marker primitive tagged pin1 by layer/text."""
    pads = fp.get("pads") or []
    p1 = _pin1_pad(fp)
    if p1 is not None and len(pads) > 1:
        others = [p for p in pads if p is not p1]
        other_shapes = {(p.get("shape") or "").lower() for p in others}
        if len(other_shapes) == 1 and (p1.get("shape") or "").lower() not in other_shapes:
            return True  # pad 1 is the odd shape → a pin-1 marker
    # A free text like "1" or a dot on silk near pad 1 is harder to detect
    # without geometry; a text whose content is exactly "1" counts.
    for t in _texts(fp):
        if (t.get("kind") or "").lower() == "free" and str(t.get("text")).strip() == "1":
            return True
    return False


# --- policy dimensions ------------------------------------------------------
def _check_layer_role(footprints, role_name, hints, policy):
    """Generic: the layer used for a role (silk/assembly/courtyard) should be
    the library's dominant one; flag footprints using a different layer.

    Two distinct defects share this dimension, and they need different fixes:

    * MISPLACED: the role's graphics are only on the wrong layer. Moving them
      to the convention layer is a safe, mechanical correction.
    * STRAY: the footprint already has graphics on the convention layer AND
      extra graphics elsewhere. Moving the strays would stack duplicate
      geometry on top of the good graphics, so this needs a human. It is
      tagged ``stray=True`` and the fix planner keeps it manual.
    """
    per_fp = {fp.get("name", "?"): _role_layers(fp, role_name, hints)
              for fp in footprints}
    inferred = policy.get(f"{role_name}_layer") if policy else None
    if inferred is None:
        inferred = _dominant(
            layer for layers in per_fp.values() for layer in layers)
    findings = []
    if inferred is None:
        return inferred, findings  # role not used anywhere → nothing to check
    for name, layers in per_fp.items():
        outliers = layers - {inferred}
        if not outliers:
            continue
        stray = inferred in layers
        if stray:
            message = (f"{role_name} is on {inferred!r} but ALSO on "
                       f"{sorted(outliers)}: stray graphics, review before "
                       f"moving (a move would duplicate the existing "
                       f"{role_name})")
        else:
            message = (f"{role_name} on {sorted(outliers)} but the library "
                       f"convention is {inferred!r}")
        f = _finding(name, f"{role_name}_layer", WARNING, message,
                     expected=inferred, actual=sorted(layers),
                     target=sorted(outliers))
        f["stray"] = stray
        findings.append(f)
    return inferred, findings


def _check_presence(footprints, dimension, predicate, policy):
    """A feature present on the MAJORITY of footprints is the convention; flag
    footprints that lack it. If most lack it, it isn't the convention → no
    findings (unless policy forces it)."""
    present = {fp.get("name", "?"): predicate(fp) for fp in footprints}
    forced = policy.get(dimension) if policy else None
    require = forced if forced is not None else (
        sum(present.values()) * 2 > len(present))  # strict majority
    findings = []
    if require:
        for name, ok in present.items():
            if not ok:
                findings.append(_finding(
                    name, dimension, WARNING,
                    f"missing {dimension.replace('_', ' ')}: the library "
                    f"applies it to most footprints",
                    expected=True, actual=False))
    return require, findings


def _pad_scheme(name: str) -> str:
    """Classify a pad name by SHAPE alone: 'numeric' (1, 2), 'grid-like'
    (A1, B2), or 'named' (GND, SH, MP).

    Shape is not enough to call a pad a BGA ball: ``M1``/``M2`` (mounting),
    ``S1``/``S2`` (shield/standoff) and ``D3`` (mech) are all grid-shaped but
    are mounting hardware. Use :func:`_pad_schemes_of` for the real verdict.
    """
    s = str(name).strip()
    if s.isdigit():
        return "numeric"
    if len(s) >= 2 and s[0].isalpha() and s[1:].isdigit():
        return "grid"
    return "named"


# A ball array dominates its footprint: a BGA's pads are essentially all grid
# cells, spread over several row letters. Below these thresholds, grid-SHAPED
# pads among numeric ones are mounting/shield/test hardware (M1, S2, D3), not a
# numbering scheme: treating them as one produced false "mixed scheme" reports
# on ordinary module and connector footprints.
_GRID_MIN_ROWS = 2


def _pad_schemes_of(fp: dict) -> set:
    """The pad-numbering schemes genuinely in use in one footprint."""
    names = [str(p.get("name")).strip() for p in fp.get("pads") or []]
    if not names:
        return set()
    raw = [_pad_scheme(n) for n in names]
    grid_like = [n for n, s in zip(names, raw) if s == "grid"]
    rows = {n[0].upper() for n in grid_like}
    is_array = (len(grid_like) * 2 > len(names)  # grid cells dominate the part
                and len(rows) >= _GRID_MIN_ROWS)  # spread over several rows
    schemes = set()
    for n, s in zip(names, raw):
        if s == "grid" and not is_array:
            schemes.add("named")   # mounting / shield / test pad
        else:
            schemes.add(s)
    return schemes


def _check_pad_naming(footprints, policy):
    """A footprint's electrical pads should use ONE numbering scheme; a mix of
    numeric (1,2) and grid (A1,B2) inside one part is almost always an error.
    'named' pads (mounting holes, shields, standoffs) are exempt: they
    legitimately mix, and a grid-SHAPED name like ``M2`` on an otherwise
    numeric part is mounting hardware, not a ball array.
    """
    findings = []
    for fp in footprints:
        electrical = _pad_schemes_of(fp) - {"named"}
        if len(electrical) > 1:
            findings.append(_finding(
                fp.get("name", "?"), "pad_naming", WARNING,
                f"pads mix numbering schemes {sorted(electrical)}: a single "
                f"footprint should use one",
                expected="one scheme", actual=sorted(electrical)))
    return None, findings


def _check_pad_drill(footprints, policy):
    """Per-pad geometry integrity: a pad with a drill hole must be
    through-hole (multi-layer); a drilled pad stuck on a single copper layer
    is a defect."""
    findings = []
    for fp in footprints:
        for p in fp.get("pads") or []:
            hole = p.get("hole") or 0
            layer = (p.get("layer") or "").lower()
            if hole and hole > 0 and layer not in ("multi", "multilayer", ""):
                findings.append(_finding(
                    fp.get("name", "?"), "pad_drill", WARNING,
                    f"pad {p.get('name')!r} has a {hole} drill but sits on "
                    f"layer {p.get('layer')!r}: a drilled pad must be "
                    f"multi-layer (through-hole)",
                    expected="multi", actual=p.get("layer"),
                    target=p.get("name")))
    return None, findings


def _mech_layers(fp: dict) -> set:
    """Mechanical layers a footprint draws on (assembly, courtyard, fab, etc.).

    Identified by ELIMINATION, not by name: any layer that is not one of
    Altium's standard electrical/overlay/mask/paste/keepout/drill layers is a
    mechanical layer. Matching on the substring "mechanical" would miss a layer
    the user renamed to "Top Assembly" or "Fab", which is precisely the layer
    worth auditing. Layers nothing can name fall back to ``Layer<id>`` and are
    likewise non-standard, so they are included.

    The layer identity (real name, else the ordinal) is what gets compared, so
    consistency is checked whatever the house naming happens to be.
    """
    out: set = set()
    for item in fp.get("primitives") or []:
        ident = _layer_of(item)
        if ident and not _is_standard_layer(ident):
            out.add(ident)
    return out


def _check_mechanical_consistency(footprints, policy):
    """The library should use the SAME set of mechanical layers on every
    footprint. Without knowing which layer means 'assembly' vs 'courtyard',
    inconsistency is still catchable: a footprint MISSING a layer the majority
    use, or drawing on a mechanical layer NO other footprint uses, is almost
    always a misplaced assembly/courtyard/fab graphic."""
    n = len(footprints)
    per_fp = {fp.get("name", "?"): _mech_layers(fp) for fp in footprints}
    usage = Counter(layer for layers in per_fp.values() for layer in layers)
    expected = {layer for layer, c in usage.items() if c * 2 > n}  # majority
    findings = []
    for name, layers in per_fp.items():
        for miss in sorted(expected - layers):
            findings.append(_finding(
                name, "mechanical_layer", WARNING,
                f"missing mechanical layer {miss!r}: {usage[miss]}/{n} "
                f"footprints use it (likely the assembly / fab layer)",
                expected=miss, actual=None, target=miss))
        for lyr in sorted(layers):
            if n >= 4 and usage[lyr] == 1:
                findings.append(_finding(
                    name, "mechanical_layer", WARNING,
                    f"uses mechanical layer {lyr!r} that no other footprint "
                    f"uses: likely graphics on the wrong layer",
                    expected=None, actual=lyr, target=lyr))
    return sorted(expected), findings


def _median(values: list) -> float:
    s = sorted(values)
    n = len(s)
    if not n:
        return 0.0
    mid = n // 2
    return float(s[mid]) if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _designator_offsets(footprints) -> list:
    """(footprint, offset-from-pad-centre) for every footprint that exposes
    both a designator location and a pad centre. Offset is the Chebyshev
    distance in mils, so a designator off in x OR y counts equally."""
    out = []
    for fp in footprints:
        d = _designator_text(fp)
        center = fp.get("pad_center")
        if not d or not isinstance(center, dict):
            continue
        dx, dy = d.get("x"), d.get("y")
        cx, cy = center.get("x"), center.get("y")
        if None in (dx, dy, cx, cy):
            continue
        out.append((fp, max(abs(dx - cx), abs(dy - cy)), (dx, dy), (cx, cy)))
    return out


def _check_designator_centered(footprints, policy):
    """Designators should sit where THIS library habitually puts them relative
    to the footprint's average PAD CENTRE, not at the library origin, which is
    arbitrary and often far from the body.

    The tolerance is not a hard-coded number. It is inferred from the library's
    own distribution of designator offsets, via a robust upper fence:

        tolerance = median(offsets) + 3 * MAD(offsets)

    A library that centres everything exactly has median = MAD = 0, so the
    tolerance is 0 and any offset at all is an outlier. A library that habitually
    parks designators above the body has a large median, so that habit becomes
    the convention and only genuine strays are flagged. Both are correct
    behaviour for "what does the majority of this library do".

    ``policy["designator_center_tol"]`` (mils) overrides the inference: user
    input shapes the policy, it does not have to accept it.

    Footprints that expose no designator location or no pad centre are skipped,
    never guessed at.
    """
    samples = _designator_offsets(footprints)
    if not samples:
        return None, []

    tol = (policy or {}).get("designator_center_tol")
    if tol is None:
        offsets = [off for _, off, _, _ in samples]
        med = _median(offsets)
        mad = _median([abs(o - med) for o in offsets])
        tol = med + 3 * mad

    findings = []
    for fp, off, (dx, dy), (cx, cy) in samples:
        if off <= tol:
            continue
        findings.append(_finding(
            fp.get("name", "?"), "designator_centered", INFO,
            f"designator sits {off} mils from the average pad centre; this "
            f"library's own tolerance is {tol:g}",
            expected=f"within {tol:g} mils of ({cx},{cy})",
            actual=f"({dx},{dy})", target={"x": cx, "y": cy}))
    return tol, findings


def _center_write_coords(center) -> tuple:
    """The pad centre in the units the WRITER wants: native TCoord when the
    script reports them, else mils. Mils lose up to a mil per re-centre."""
    if not isinstance(center, dict):
        return None, None
    if center.get("coord_x") is not None:
        return center["coord_x"], center["coord_y"]
    return center.get("x"), center.get("y")


def _anchor_for_center(designator: dict, target: tuple) -> tuple:
    """The ANCHOR (XLocation) that puts the designator's bounding-box centre on
    ``target``.

    ``XLocation`` is a corner of the text, not its middle, so assigning the pad
    centre to it leaves the string hanging half its width off the part: right
    outside the body on a small passive. The script reports both the anchor and
    the bbox centre; the difference between them is the constant offset:

        new_anchor = anchor + (target - bbox_centre)

    Falls back to the target itself when the script is too old to report an
    anchor, which reproduces the old (wrong) behaviour rather than crashing.
    """
    tx, ty = target
    ax, ay = designator.get("anchor_x"), designator.get("anchor_y")
    cx, cy = designator.get("coord_x"), designator.get("coord_y")
    if None in (ax, ay, cx, cy):
        return tx, ty
    return ax + (tx - cx), ay + (ty - cy)


def designator_conventions(footprints, policy=None) -> dict:
    """What THIS library does with designators: which layer (by name and by raw
    ordinal), what height, and how far off-centre it tolerates.

    The ordinal matters because a house layer such as 'Assembly Designator' is
    not in the bridge's name table, and the ordinal is the only handle that can
    address it when writing.
    """
    policy = policy or {}
    desigs = [d for d in (_designator_text(fp) for fp in footprints) if d]
    if not desigs:
        return {"layer": None, "layer_id": None, "height": None,
                "center_tol": None, "count": 0}

    layer = policy.get("designator_layer") or _dominant(
        _layer_of(d) for d in desigs)
    # The ordinal of that same layer, taken from the designators that sit on it,
    # never from a different layer that merely shares a name.
    layer_id = _dominant(d.get("layer_id") for d in desigs
                         if _layer_of(d) == layer)
    height = policy.get("designator_height") or _dominant(
        d.get("height") for d in desigs)

    tol = policy.get("designator_center_tol")
    if tol is None:
        offsets = [off for _, off, _, _ in _designator_offsets(footprints)]
        if offsets:
            med = _median(offsets)
            tol = med + 3 * _median([abs(o - med) for o in offsets])
    return {"layer": layer, "layer_id": layer_id, "height": height,
            "center_tol": tol, "count": len(desigs)}


def plan_designator_repairs(
    footprints: list[dict[str, Any]],
    *,
    fix_layer: bool = True,
    fix_center: bool = True,
    fix_height: bool = False,
    add_missing: bool = False,
    policy: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Concrete per-footprint repairs bringing designators onto the library's
    own convention. Returns ``{conventions, actions, skipped}``.

    Each action carries exactly the arguments ``library.set_designator`` needs.
    A footprint is only touched when it actually deviates, and a footprint whose
    geometry cannot support a decision (no pad centre, unknown target layer) is
    reported in ``skipped`` rather than repaired on a guess.
    """
    conv = designator_conventions(footprints, policy)
    actions: list[dict] = []
    skipped: list[dict] = []
    layer, layer_id = conv["layer"], conv["layer_id"]
    height, tol = conv["height"], conv["center_tol"]

    for fp in footprints:
        name = fp.get("name", "?")
        d = _designator_text(fp)
        center = fp.get("pad_center")
        cx = center.get("x") if isinstance(center, dict) else None
        cy = center.get("y") if isinstance(center, dict) else None
        # The writer positions in native TCoord; fall back to mils only when the
        # script is too old to report coords.
        wx, wy = _center_write_coords(center)

        if _designator_count(fp) > 1:
            skipped.append({"footprint": name,
                            "reason": "has duplicate .Designator strings: "
                                      "remove the extras before repairing"})
            continue

        if d is None:
            if not add_missing:
                continue
            if cx is None or layer_id is None or not height:
                skipped.append({"footprint": name,
                                "reason": "no pad centre, target layer or height"})
                continue
            actions.append({"footprint": name, "create": True,
                            "layer_id": layer_id, "x": wx, "y": wy,
                            "height": height, "reasons": ["missing"]})
            continue

        reasons: list[str] = []
        act: dict[str, Any] = {"footprint": name, "create": False}

        if fix_layer and layer is not None and _layer_of(d) != layer:
            if layer_id is None:
                skipped.append({"footprint": name,
                                "reason": "target layer has no ordinal"})
            else:
                act["layer_id"] = layer_id
                reasons.append("layer")

        if fix_center and tol is not None and cx is not None:
            dx, dy = d.get("x"), d.get("y")
            if dx is not None and dy is not None:
                if max(abs(dx - cx), abs(dy - cy)) > tol:
                    # The writer sets the anchor, so send the anchor that lands
                    # the bbox centre on the pad centre.
                    act["x"], act["y"] = _anchor_for_center(d, (wx, wy))
                    reasons.append("off-centre")

        if fix_height and height and d.get("height") != height:
            act["height"] = height
            reasons.append("height")

        if reasons:
            act["reasons"] = reasons
            actions.append(act)

    return {"conventions": conv, "actions": actions, "skipped": skipped}


def _check_designator_height(footprints, policy):
    heights = {}
    for fp in footprints:
        d = _designator_text(fp)
        heights[fp.get("name", "?")] = d.get("height") if d else None
    inferred = policy.get("designator_height") if policy else None
    if inferred is None:
        inferred = _dominant(heights.values())
    findings = []
    if inferred is None:
        return inferred, findings
    for name, h in heights.items():
        if h is not None and h != inferred:
            findings.append(_finding(
                name, "designator_height", INFO,
                f"designator height {h} != library norm {inferred}",
                expected=inferred, actual=h))
    return inferred, findings


def audit_footprint_library(
    footprints: list[dict[str, Any]],
    policy: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Audit a footprint library for inconsistent policy application.

    Args:
        footprints: parsed footprints (see the module docstring schema).
        policy: optional explicit conventions to enforce, e.g.
            ``{"silk_layer": "Top Overlay", "courtyard": True,
            "three_d_model": True}``. Any dimension omitted is inferred from
            the library itself.

    Returns ``{footprint_count, conventions, findings, summary}`` where
    ``conventions`` is the inferred/enforced value per dimension and
    ``findings`` lists every deviation (each names footprint + expected +
    actual, enough to drive a fix).
    """
    policy = policy or {}
    conventions: dict[str, Any] = {}
    findings: list[dict] = []

    for role, hints in (("silk", _SILK_HINTS),
                        ("assembly", _ASSEMBLY_HINTS)):
        conv, fs = _check_layer_role(footprints, role, hints, policy)
        conventions[f"{role}_layer"] = conv
        findings += fs

    conventions["courtyard"], fs = _check_presence(
        footprints, "courtyard", _has_courtyard, policy)
    findings += fs

    conventions["three_d_model"], fs = _check_presence(
        footprints, "three_d_model", lambda fp: _body_count(fp) > 0, policy)
    findings += fs

    conventions["mechanical_layers"], fs = _check_mechanical_consistency(
        footprints, policy)
    findings += fs

    conventions["pin1_marker"], fs = _check_presence(
        footprints, "pin1_marker", _has_pin1_marker, policy)
    findings += fs

    # Designator layer comes from the designator text specifically (not any
    # silk primitive), so it has its own precise check.
    conventions["designator_layer"], fs = _check_designator_layer(
        footprints, policy)
    findings += fs

    # Pad rules (per-footprint / per-pad, not library-inferred).
    for check in (_check_pad_naming, _check_pad_drill,
                  _check_duplicate_designators):
        _, fs = check(footprints, policy)
        findings += fs

    conventions["designator_height"], fs = _check_designator_height(
        footprints, policy)
    findings += fs

    # Every footprint should carry a designator string at all. Only meaningful
    # once text extraction is in play: if NO footprint exposes texts, the dump
    # didn't include them and flagging all 1000 would be noise.
    if any(_texts(fp) for fp in footprints):
        conventions["designator_present"], fs = _check_presence(
            footprints, "designator_present",
            lambda fp: _designator_text(fp) is not None, policy)
        findings += fs

    conventions["designator_centered"], fs = _check_designator_centered(
        footprints, policy)
    findings += fs

    summary = {ERROR: 0, WARNING: 0, INFO: 0}
    for f in findings:
        summary[f["severity"]] = summary.get(f["severity"], 0) + 1

    # Per-footprint rollup so a caller can scan the whole library at a glance
    # (which footprints are clean vs. how many issues each has), most-flagged
    # first: the "go easily through every footprint" view.
    counts: dict[str, int] = {}
    for f in findings:
        fp_name = f.get("footprint") or "?"
        counts[fp_name] = counts.get(fp_name, 0) + 1
    per_footprint = [
        {"name": fp.get("name", "?"),
         "issues": counts.get(fp.get("name", "?"), 0),
         "ok": counts.get(fp.get("name", "?"), 0) == 0}
        for fp in footprints
    ]
    per_footprint.sort(key=lambda r: (-r["issues"], r["name"]))

    return {
        "footprint_count": len(footprints),
        "clean_count": sum(1 for r in per_footprint if r["ok"]),
        "conventions": conventions,
        "per_footprint": per_footprint,
        "findings": findings,
        "summary": summary,
    }


# --- fix planning ----------------------------------------------------------
# Which dimensions have a mechanical, unambiguous correction (auto) vs. ones
# that need new geometry or human judgment (manual). ``auto`` fixes just
# move/set an existing property to the library convention.
_AUTO_FIX = {
    "silk_layer": "move_graphics_to_layer",
    "assembly_layer": "move_graphics_to_layer",
    "designator_layer": "move_designator_to_layer",
    "designator_height": "set_designator_height",
    "designator_centered": "move_designator_to_center",
    "pad_drill": "set_pad_layer",
}
_MANUAL_FIX = {
    "courtyard": "add_courtyard",           # needs a generated outline
    "three_d_model": "attach_3d_model",     # needs a model to attach
    "pin1_marker": "add_pin1_marker",       # needs a marker + placement
    "pad_naming": "renumber_pads",          # ambiguous: which scheme is right
    "mechanical_layer": "review_mechanical_layer",  # role unknown → human picks
    "designator_present": "add_designator",  # needs a text object placed
}


def plan_footprint_fixes(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn an audit report's findings into concrete, ordered fix actions.

    Each action is ``{footprint, dimension, action, auto, params,
    description}``. ``auto=True`` actions are mechanical: move the graphics
    to the convention layer, set the designator height, make a drilled pad
    multi-layer, and can be applied without judgment. ``auto=False`` actions
    need new geometry or a decision (add a courtyard/3D body/pin-1 marker,
    pick a pad-numbering scheme) and are surfaced for a human / the LLM to
    resolve. Auto fixes are ordered first so a caller can apply the safe batch
    and then review the rest.
    """
    actions: list[dict[str, Any]] = []
    for f in report.get("findings", []):
        dim = f.get("dimension")
        fp = f.get("footprint")
        if f.get("stray"):
            # Graphics exist on BOTH the convention layer and elsewhere. A
            # layer move would duplicate them onto the good geometry, so this
            # is never an auto fix regardless of dimension.
            actions.append({
                "footprint": fp, "dimension": dim,
                "action": "review_stray_graphics", "auto": False,
                "params": {"layers": f.get("target"), "keep": f.get("expected"),
                           "role": (dim or "").replace("_layer", "")},
                "description": f"{fp}: {f.get('message', dim)} (needs review)"})
        elif dim in _AUTO_FIX:
            params: dict[str, Any] = {"to": f.get("expected")}
            if dim == "pad_drill":
                params = {"pad": f.get("target"), "layer": "multi"}
            elif dim == "designator_height":
                params = {"height": f.get("expected")}
            elif dim == "designator_centered":
                params = {"to": f.get("target")}  # {x, y} average pad centre
            elif dim in ("silk_layer", "assembly_layer"):
                params = {"from": f.get("target"), "to": f.get("expected"),
                          "role": dim.replace("_layer", "")}
            else:  # designator_layer
                params = {"to": f.get("expected")}
            actions.append({
                "footprint": fp, "dimension": dim, "action": _AUTO_FIX[dim],
                "auto": True, "params": params,
                "description": f"{fp}: {f.get('message', dim)}"})
        elif dim in _MANUAL_FIX:
            actions.append({
                "footprint": fp, "dimension": dim, "action": _MANUAL_FIX[dim],
                "auto": False, "params": {"expected": f.get("expected")},
                "description": f"{fp}: {f.get('message', dim)} (needs review)"})
    actions.sort(key=lambda a: (not a["auto"], a["footprint"] or "",
                                a["dimension"]))
    return actions


def _check_designator_layer(footprints, policy):
    layers = {}
    for fp in footprints:
        d = _designator_text(fp)
        layers[fp.get("name", "?")] = _layer_of(d) if d else None
    inferred = policy.get("designator_layer") if policy else None
    if inferred is None:
        inferred = _dominant(layers.values())
    findings = []
    if inferred is None:
        return inferred, findings
    for name, layer in layers.items():
        if layer is not None and layer != inferred:
            findings.append(_finding(
                name, "designator_layer", WARNING,
                f"designator on {layer!r} but the library convention is "
                f"{inferred!r}", expected=inferred, actual=layer))
    return inferred, findings
