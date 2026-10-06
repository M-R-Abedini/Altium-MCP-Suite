# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the footprint-library policy auditor (offline engine)."""

from __future__ import annotations

from eda_agent.design.footprint_policy import (
    INFO,
    WARNING,
    audit_footprint_library,
    plan_footprint_fixes,
)


def _fp(name, *, silk="Top Overlay", assembly="Mechanical 13",
        courtyard=True, bodies=1, desig_layer="Top Overlay", desig_height=25,
        pads=None):
    texts = [{"text": name, "kind": "designator", "layer": desig_layer,
              "height": desig_height}]
    prims = []
    if silk:
        prims.append({"kind": "track", "layer": silk, "role": "silk",
                      "width": 5})
    if assembly:
        prims.append({"kind": "track", "layer": assembly, "role": "assembly",
                      "width": 5})
    if courtyard:
        prims.append({"kind": "track", "layer": "Mechanical 15",
                      "role": "courtyard", "width": 3})
    if pads is None:
        pads = [{"name": "1", "shape": "rectangular"},
                {"name": "2", "shape": "round"}]
    return {"name": name, "texts": texts, "primitives": prims,
            "bodies": bodies, "pads": pads}


def _findings(report, dimension):
    return [f for f in report["findings"] if f["dimension"] == dimension]


def test_consistent_library_has_no_findings():
    lib = [_fp("R0402"), _fp("C0402"), _fp("R0603")]
    report = audit_footprint_library(lib)
    assert report["findings"] == []
    assert report["conventions"]["silk_layer"] == "Top Overlay"
    assert report["conventions"]["assembly_layer"] == "Mechanical 13"


def test_silk_layer_outlier_flagged():
    # One footprint puts silk on the wrong overlay -> inconsistency.
    lib = [_fp("R0402"), _fp("C0402"), _fp("BAD", silk="Bottom Overlay")]
    report = audit_footprint_library(lib)
    fs = _findings(report, "silk_layer")
    assert len(fs) == 1 and fs[0]["footprint"] == "BAD"
    assert fs[0]["expected"] == "Top Overlay"


def _fp_extra_silk(name, extra):
    """A footprint with correct top silk PLUS stray graphics on another layer."""
    fp = _fp(name)
    fp["primitives"].append({"kind": "track", "layer": extra, "role": "silk",
                             "width": 5})
    return fp


def test_silk_on_wrong_layer_only_is_an_auto_move():
    # Silk lives solely on the wrong layer -> moving it is safe and mechanical.
    lib = [_fp("A"), _fp("B"), _fp("WRONG", silk="BottomOverlay")]
    report = audit_footprint_library(lib)
    f = _findings(report, "silk_layer")[0]
    assert f["stray"] is False
    fix = [a for a in plan_footprint_fixes(report) if a["footprint"] == "WRONG"]
    assert fix[0]["auto"] is True
    assert fix[0]["action"] == "move_graphics_to_layer"


def test_stray_silk_alongside_good_silk_is_never_auto_moved():
    # The footprint ALREADY has correct top silk; moving the bottom strays onto
    # it would stack duplicate geometry. Must stay manual.
    lib = [_fp("A"), _fp("B"), _fp_extra_silk("STRAY", "BottomOverlay")]
    report = audit_footprint_library(lib)
    f = [x for x in _findings(report, "silk_layer") if x["footprint"] == "STRAY"][0]
    assert f["stray"] is True
    assert f["actual"] == ["BottomOverlay", "Top Overlay"]
    assert f["target"] == ["BottomOverlay"]
    assert "review before moving" in f["message"]

    fix = [a for a in plan_footprint_fixes(report) if a["footprint"] == "STRAY"]
    assert len(fix) == 1
    assert fix[0]["auto"] is False
    assert fix[0]["action"] == "review_stray_graphics"
    assert fix[0]["action"] != "move_graphics_to_layer"
    assert fix[0]["params"]["layers"] == ["BottomOverlay"]
    assert fix[0]["params"]["keep"] == "Top Overlay"


def test_stray_graphics_sort_after_auto_fixes():
    lib = [_fp("A"), _fp("B"), _fp("WRONG", silk="BottomOverlay"),
           _fp_extra_silk("STRAY", "BottomOverlay")]
    actions = plan_footprint_fixes(audit_footprint_library(lib))
    autos = [i for i, a in enumerate(actions) if a["auto"]]
    manuals = [i for i, a in enumerate(actions) if not a["auto"]]
    assert max(autos) < min(manuals)


def test_assembly_layer_inferred_and_outlier_flagged():
    lib = [_fp("A"), _fp("B"), _fp("C", assembly="Mechanical 5")]
    fs = _findings(audit_footprint_library(lib), "assembly_layer")
    assert len(fs) == 1 and fs[0]["footprint"] == "C"


def test_missing_courtyard_flagged_when_library_uses_them():
    lib = [_fp("A"), _fp("B"), _fp("NOCOURT", courtyard=False)]
    fs = _findings(audit_footprint_library(lib), "courtyard")
    assert len(fs) == 1 and fs[0]["footprint"] == "NOCOURT"


def test_missing_3d_body_flagged_when_majority_have_one():
    lib = [_fp("A"), _fp("B"), _fp("NO3D", bodies=0)]
    fs = _findings(audit_footprint_library(lib), "three_d_model")
    assert len(fs) == 1 and fs[0]["footprint"] == "NO3D"


def test_no_courtyard_convention_when_minority_have_them():
    # Only one of three has a courtyard -> not the convention -> no findings.
    lib = [_fp("A", courtyard=False), _fp("B", courtyard=False), _fp("C")]
    assert _findings(audit_footprint_library(lib), "courtyard") == []


def test_pin1_marker_detected_by_odd_pad_shape():
    # Pad 1 rectangular, rest round -> a pin-1 marker; a fp with all-round
    # pads lacks one and is flagged.
    allround = [{"name": "1", "shape": "round"}, {"name": "2", "shape": "round"}]
    lib = [_fp("A"), _fp("B"), _fp("FLAT", pads=allround)]
    fs = _findings(audit_footprint_library(lib), "pin1_marker")
    assert len(fs) == 1 and fs[0]["footprint"] == "FLAT"


def test_designator_layer_outlier():
    lib = [_fp("A"), _fp("B"), _fp("D", desig_layer="Mechanical 13")]
    fs = _findings(audit_footprint_library(lib), "designator_layer")
    assert len(fs) == 1 and fs[0]["footprint"] == "D"


def test_designator_height_outlier_is_info():
    lib = [_fp("A"), _fp("B"), _fp("TALL", desig_height=50)]
    fs = _findings(audit_footprint_library(lib), "designator_height")
    assert len(fs) == 1 and fs[0]["severity"] != WARNING  # info, not a hard issue


def test_explicit_policy_overrides_inference():
    # The library majority silks on Bottom Overlay, but the policy demands Top;
    # everything on Bottom is flagged against the explicit standard.
    lib = [_fp("A", silk="Bottom Overlay"), _fp("B", silk="Bottom Overlay")]
    report = audit_footprint_library(lib, policy={"silk_layer": "Top Overlay"})
    fs = _findings(report, "silk_layer")
    assert len(fs) == 2
    assert all(f["expected"] == "Top Overlay" for f in fs)


def test_pad_naming_mixed_scheme_flagged():
    # A real ball array (grid pads dominate, several rows) with one pad
    # numbered numerically -- a genuine mixed-scheme error.
    mixed = [{"name": n, "shape": "round"}
             for n in ("A1", "A2", "B1", "B2", "C1", "C2", "1")]
    lib = [_fp("BGA", pads=mixed)]
    fs = _findings(audit_footprint_library(lib), "pad_naming")
    assert len(fs) == 1 and fs[0]["footprint"] == "BGA"


def test_mounting_pad_on_numeric_part_is_not_a_grid():
    # A module connector: many numeric pads plus one mounting pad "M2".
    # Grid-SHAPED, but mounting hardware -- not a mixed numbering scheme.
    pads = [{"name": str(i), "shape": "round"} for i in range(1, 72)]
    pads.append({"name": "M2", "shape": "round"})
    lib = [_fp("MODULE_A", pads=pads)]
    assert _findings(audit_footprint_library(lib), "pad_naming") == []


def test_shield_and_mount_pads_are_not_a_grid():
    # Numeric pads plus S1/S2 (standoff) and M1/M2 (mounting): four
    # grid-shaped names across two row letters, but a tiny minority of pads.
    pads = [{"name": str(i), "shape": "round"} for i in range(1, 68)]
    pads += [{"name": n, "shape": "round"} for n in ("S1", "S2", "M1", "M2")]
    lib = [_fp("MODULE_B", pads=pads)]
    assert _findings(audit_footprint_library(lib), "pad_naming") == []


def test_single_row_letter_is_not_a_grid():
    # D1/D2/D3 mech pads on an otherwise numeric part -- a single row letter
    # is never a ball array.
    pads = [{"name": str(i), "shape": "round"} for i in range(1, 41)]
    pads += [{"name": n, "shape": "round"} for n in ("D1", "D2", "D3")]
    lib = [_fp("MODULE_C", pads=pads)]
    assert _findings(audit_footprint_library(lib), "pad_naming") == []


def test_real_bga_is_still_recognised_as_a_grid():
    # An all-grid BGA is a grid; no finding, and its scheme is not "named".
    from eda_agent.design.footprint_policy import _pad_schemes_of
    pads = [{"name": f"{r}{c}", "shape": "round"}
            for r in "ABCDEFG" for c in range(1, 8)]
    fp = _fp("BGA49", pads=pads)
    assert _pad_schemes_of(fp) == {"grid"}
    assert _findings(audit_footprint_library([fp]), "pad_naming") == []


def test_pad_naming_named_pads_exempt():
    # Numeric pads plus 'named' mounting/shield pads is fine (SH, MP mix ok).
    ok = [{"name": "1", "shape": "round"}, {"name": "2", "shape": "round"},
          {"name": "SH", "shape": "rectangular"}, {"name": "MP", "shape": "round"}]
    lib = [_fp("CONN", pads=ok)]
    assert _findings(audit_footprint_library(lib), "pad_naming") == []


def test_pad_drill_on_single_layer_flagged():
    # A drilled pad must be multi-layer; a hole on a top-only pad is a defect.
    bad = [{"name": "1", "shape": "round", "layer": "top", "hole": 20},
           {"name": "2", "shape": "round", "layer": "multi", "hole": 20}]
    lib = [_fp("THT", pads=bad)]
    fs = _findings(audit_footprint_library(lib), "pad_drill")
    assert len(fs) == 1 and fs[0]["actual"] == "top"


def test_smd_pads_no_drill_finding():
    smd = [{"name": "1", "shape": "rectangular", "layer": "top", "hole": 0},
           {"name": "2", "shape": "round", "layer": "top", "hole": 0}]
    lib = [_fp("SMD", pads=smd)]
    assert _findings(audit_footprint_library(lib), "pad_drill") == []


def _mech(name, layers):
    # a footprint whose only geometry is tracks on the given mechanical layers
    return {"name": name,
            "primitives": [{"kind": "track", "layer": ly} for ly in layers],
            "pads": [{"name": "1", "shape": "round"}]}


def test_mech_consistent_library_has_no_mech_findings():
    lib = [_mech("A", ["Mechanical13", "Mechanical15"]),
           _mech("B", ["Mechanical13", "Mechanical15"]),
           _mech("C", ["Mechanical13", "Mechanical15"])]
    fs = _findings(audit_footprint_library(lib), "mechanical_layer")
    assert fs == []


def test_mech_missing_majority_layer_flagged():
    # A/B/C draw assembly on Mechanical13; D omits it -> missing.
    lib = [_mech("A", ["Mechanical13", "Mechanical15"]),
           _mech("B", ["Mechanical13", "Mechanical15"]),
           _mech("C", ["Mechanical13", "Mechanical15"]),
           _mech("D", ["Mechanical15"])]
    fs = _findings(audit_footprint_library(lib), "mechanical_layer")
    assert any(f["footprint"] == "D" and f["target"] == "Mechanical13"
               for f in fs)


def test_mech_unique_outlier_layer_flagged():
    lib = [_mech("A", ["Mechanical13"]), _mech("B", ["Mechanical13"]),
           _mech("C", ["Mechanical13"]),
           _mech("ODD", ["Mechanical13", "Mechanical5"])]
    fs = _findings(audit_footprint_library(lib), "mechanical_layer")
    assert any(f["footprint"] == "ODD" and f["actual"] == "Mechanical5"
               for f in fs)


def test_renamed_mechanical_layer_is_still_audited():
    # The layer is called "Top Assembly", not "Mechanical N". Detecting mech
    # layers by the substring "mechanical" would miss it entirely -- and this
    # is exactly the layer worth auditing.
    lib = [_mech("A", ["Top Overlay", "Top Assembly"]),
           _mech("B", ["Top Overlay", "Top Assembly"]),
           _mech("C", ["Top Overlay", "Top Assembly"]),
           _mech("NOASSY", ["Top Overlay"])]
    report = audit_footprint_library(lib)
    assert report["conventions"]["mechanical_layers"] == ["Top Assembly"]
    fs = _findings(report, "mechanical_layer")
    assert any(f["footprint"] == "NOASSY" and f["target"] == "Top Assembly"
               for f in fs)


def test_standard_layers_are_never_mechanical():
    from eda_agent.design.footprint_policy import _is_standard_layer
    for std in ("Top Overlay", "TopOverlay", "BottomLayer", "MidLayer3",
                "Top Paste", "KeepOutLayer", "MultiLayer", "InternalPlane1"):
        assert _is_standard_layer(std), std
    for mech in ("Mechanical13", "Top Assembly", "Fab", "Layer67108886"):
        assert not _is_standard_layer(mech), mech


def test_mech_layer_fix_is_manual_review():
    lib = [_mech("A", ["Mechanical13"]), _mech("B", ["Mechanical13"]),
           _mech("C", ["Mechanical13"]), _mech("D", [])]
    actions = plan_footprint_fixes(audit_footprint_library(lib))
    mech = [a for a in actions if a["dimension"] == "mechanical_layer"]
    assert mech and mech[0]["auto"] is False
    assert mech[0]["action"] == "review_mechanical_layer"


def test_summary_counts_by_severity():
    lib = [_fp("A"), _fp("B"), _fp("BAD", silk="Bottom Overlay",
                                   desig_height=99)]
    report = audit_footprint_library(lib)
    assert report["summary"][WARNING] >= 1
    assert report["footprint_count"] == 3


def test_per_footprint_rollup_scans_the_library():
    lib = [_fp("CLEAN1"), _fp("CLEAN2"),
           _fp("BROKEN", silk="Bottom Overlay")]
    report = audit_footprint_library(lib)
    assert report["clean_count"] == 2
    roll = report["per_footprint"]
    assert len(roll) == 3
    # most-flagged first
    assert roll[0]["name"] == "BROKEN" and roll[0]["issues"] >= 1
    assert roll[0]["ok"] is False
    assert all(r["ok"] for r in roll if r["name"].startswith("CLEAN"))


# --- unnamed layers (mechanical > 16) ---------------------------------------
def test_unnamed_layers_stay_distinct_instead_of_collapsing():
    # Two DIFFERENT mechanical layers the bridge cannot name. If both collapse
    # to "Unknown" they look like one layer and real outliers go unreported.
    from eda_agent.design.footprint_policy import _layer_of
    assert _layer_of({"layer": "Unknown", "layer_id": 74}) == "Layer74"
    assert _layer_of({"layer": "Unknown", "layer_id": 75}) == "Layer75"
    assert _layer_of({"layer": "", "layer_id": 74}) == "Layer74"
    # a real name always wins over the ordinal
    assert _layer_of({"layer": "TopOverlay", "layer_id": 3}) == "TopOverlay"


def test_unnamed_layer_outlier_flagged_as_mechanical():
    def _m(name, ids):
        return {"name": name,
                "primitives": [{"kind": "track", "layer": "Unknown",
                                "layer_id": i} for i in ids],
                "pads": [{"name": "1", "shape": "round"}]}

    lib = [_m("A", [74]), _m("B", [74]), _m("C", [74]), _m("ODD", [74, 75])]
    fs = _findings(audit_footprint_library(lib), "mechanical_layer")
    assert any(f["footprint"] == "ODD" and f["actual"] == "Layer75" for f in fs)


def test_designator_layer_uses_the_ordinal_when_unnamed():
    def _d(name, lid):
        return {"name": name, "pad_center": {"x": 0, "y": 0},
                "texts": [{"text": ".Designator", "kind": "designator",
                           "layer": "Unknown", "layer_id": lid,
                           "height": 20, "x": 0, "y": 0}],
                "pads": [{"name": "1", "shape": "round"}]}

    lib = [_d("A", 74), _d("B", 74), _d("ODD", 75)]
    report = audit_footprint_library(lib)
    assert report["conventions"]["designator_layer"] == "Layer74"
    fs = _findings(report, "designator_layer")
    assert [f["footprint"] for f in fs] == ["ODD"]


# --- designator presence + centering ----------------------------------------
def _fp_desig(name, *, dx=0, dy=0, cx=0, cy=0, designator=True):
    """A footprint whose designator sits at (dx,dy) and whose average pad
    centre is (cx,cy)."""
    texts = []
    if designator:
        texts.append({"text": ".Designator", "kind": "designator",
                      "layer": "Top Overlay", "height": 25, "x": dx, "y": dy})
    return {"name": name, "texts": texts,
            "primitives": [{"kind": "track", "layer": "Top Overlay",
                            "role": "silk"}],
            "pad_center": {"x": cx, "y": cy}, "bodies": 1,
            "pads": [{"name": "1", "shape": "rectangular"},
                     {"name": "2", "shape": "round"}]}


def _centered_lib(n=5):
    """A library that habitually centres its designators on the pad centre."""
    return [_fp_desig(f"OK{i}", dx=i * 10, dy=0, cx=i * 10, cy=0)
            for i in range(n)]


def test_designator_centered_on_pad_centre_is_clean():
    assert _findings(audit_footprint_library(_centered_lib()),
                     "designator_centered") == []


def test_designator_far_from_pad_centre_is_flagged():
    # Designator left at the library origin while the body sits far away.
    lib = _centered_lib() + [_fp_desig("OFF", dx=0, dy=0, cx=500, cy=300)]
    fs = _findings(audit_footprint_library(lib), "designator_centered")
    assert len(fs) == 1 and fs[0]["footprint"] == "OFF"
    assert fs[0]["severity"] == INFO
    assert fs[0]["target"] == {"x": 500, "y": 300}


def test_library_that_never_centres_designators_sets_its_own_convention():
    # Another house style: every designator parked 300 mils above the body.
    # That IS this library's convention -- nothing should be flagged.
    lib = [_fp_desig(f"UP{i}", dx=0, dy=300, cx=0, cy=0) for i in range(5)]
    report = audit_footprint_library(lib)
    assert _findings(report, "designator_centered") == []
    assert report["conventions"]["designator_centered"] == 300


def test_stray_designator_flagged_against_an_offset_convention():
    # Same house style, but one footprint is way out -- flagged relative to the
    # library's own habit, not to an absolute "must be centred" rule.
    lib = [_fp_desig(f"UP{i}", dx=0, dy=300, cx=0, cy=0) for i in range(5)]
    lib.append(_fp_desig("STRAY", dx=0, dy=2000, cx=0, cy=0))
    fs = _findings(audit_footprint_library(lib), "designator_centered")
    assert [f["footprint"] for f in fs] == ["STRAY"]


def test_designator_center_tolerance_is_policy_overridable():
    lib = _centered_lib() + [_fp_desig("OFF", dx=0, dy=0, cx=60, cy=0)]
    assert _findings(audit_footprint_library(lib), "designator_centered")
    loose = audit_footprint_library(lib, policy={"designator_center_tol": 100})
    assert _findings(loose, "designator_centered") == []


def test_off_centre_designator_fix_moves_it_to_the_pad_centre():
    lib = _centered_lib() + [_fp_desig("OFF", dx=0, dy=0, cx=500, cy=300)]
    actions = plan_footprint_fixes(audit_footprint_library(lib))
    move = [a for a in actions if a["dimension"] == "designator_centered"]
    assert move and move[0]["auto"] is True
    assert move[0]["action"] == "move_designator_to_center"
    assert move[0]["params"]["to"] == {"x": 500, "y": 300}


def test_missing_designator_flagged_when_others_have_one():
    lib = [_fp_desig("A"), _fp_desig("B"), _fp_desig("NONE", designator=False)]
    fs = _findings(audit_footprint_library(lib), "designator_present")
    assert len(fs) == 1 and fs[0]["footprint"] == "NONE"
    actions = plan_footprint_fixes(audit_footprint_library(lib))
    add = [a for a in actions if a["dimension"] == "designator_present"]
    assert add and add[0]["auto"] is False and add[0]["action"] == "add_designator"


def test_library_without_designators_is_not_flagged():
    # A foreign library whose footprints carry silk text but no designator
    # string. Absence is the majority, so it is that library's convention.
    lib = []
    for i in range(4):
        fp = _fp_desig(f"F{i}", designator=False)
        fp["texts"] = [{"text": "REF", "kind": "free", "layer": "Top Overlay"}]
        lib.append(fp)
    report = audit_footprint_library(lib)
    assert _findings(report, "designator_present") == []
    assert _findings(report, "designator_centered") == []
    assert _findings(report, "designator_layer") == []


def test_designator_layer_convention_is_inferred_not_assumed():
    # A library that puts designators on a mechanical layer is self-consistent;
    # the convention follows the library, and the one outlier is flagged.
    lib = [_fp_desig(f"M{i}") for i in range(4)]
    for fp in lib:
        fp["texts"][0]["layer"] = "Mechanical 13"
    odd = _fp_desig("ODD")
    odd["texts"][0]["layer"] = "Top Overlay"
    lib.append(odd)
    report = audit_footprint_library(lib)
    assert report["conventions"]["designator_layer"] == "Mechanical 13"
    fs = _findings(report, "designator_layer")
    assert [f["footprint"] for f in fs] == ["ODD"]


def test_no_designator_findings_when_texts_are_not_extracted():
    # An older Pascal build emits no texts at all. Flagging every footprint as
    # "missing designator" would be pure noise, so the check stays silent.
    lib = [_fp_no_text("A"), _fp_no_text("B")]
    report = audit_footprint_library(lib)
    assert _findings(report, "designator_present") == []
    assert _findings(report, "designator_centered") == []


def _fp_no_text(name):
    return {"name": name, "pads": [{"name": "1", "shape": "round"}],
            "primitives": [{"kind": "track", "layer": "Top Overlay"}]}


def test_designator_check_skips_footprints_without_a_pad_centre():
    # No pad_center (e.g. a footprint with no pads) -> skipped, not guessed.
    fp = _fp_desig("A", dx=999, dy=999)
    fp["pad_center"] = None
    assert _findings(audit_footprint_library([fp]), "designator_centered") == []


# --- fix planning -----------------------------------------------------------
def test_fix_plan_silk_outlier_is_auto_layer_move():
    lib = [_fp("A"), _fp("B"), _fp("BAD", silk="Bottom Overlay")]
    actions = plan_footprint_fixes(audit_footprint_library(lib))
    silk = [a for a in actions if a["dimension"] == "silk_layer"]
    assert len(silk) == 1
    a = silk[0]
    assert a["auto"] is True
    assert a["action"] == "move_graphics_to_layer"
    assert a["params"]["to"] == "Top Overlay"
    assert a["params"]["from"] == ["Bottom Overlay"]  # the outlier layer(s)
    assert a["params"]["role"] == "silk"


def test_fix_plan_pad_drill_targets_the_pad():
    bad = [{"name": "1", "shape": "round", "layer": "top", "hole": 20},
           {"name": "2", "shape": "round", "layer": "multi", "hole": 20}]
    lib = [_fp("THT", pads=bad)]
    actions = plan_footprint_fixes(audit_footprint_library(lib))
    drill = [a for a in actions if a["dimension"] == "pad_drill"]
    assert drill and drill[0]["auto"] is True
    assert drill[0]["params"] == {"pad": "1", "layer": "multi"}


def test_fix_plan_missing_courtyard_is_manual():
    lib = [_fp("A"), _fp("B"), _fp("NOCOURT", courtyard=False)]
    actions = plan_footprint_fixes(audit_footprint_library(lib))
    court = [a for a in actions if a["dimension"] == "courtyard"]
    assert court and court[0]["auto"] is False
    assert court[0]["action"] == "add_courtyard"


def test_fix_plan_orders_auto_before_manual():
    lib = [_fp("A"), _fp("B"),
           _fp("BAD", silk="Bottom Overlay", courtyard=False)]
    actions = plan_footprint_fixes(audit_footprint_library(lib))
    autos = [i for i, a in enumerate(actions) if a["auto"]]
    manuals = [i for i, a in enumerate(actions) if not a["auto"]]
    assert autos and manuals
    assert max(autos) < min(manuals)  # every auto action precedes every manual


def test_fix_plan_empty_for_clean_library():
    lib = [_fp("A"), _fp("B"), _fp("C")]
    assert plan_footprint_fixes(audit_footprint_library(lib)) == []
