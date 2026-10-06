# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Offline tests for the SCH->PCB bridge netlist derivation.

Covers the pure-Python half of ``pcb_build_from_project``: parsing the
tabular netlist CSV that ``proj_export_netlist(net_format="tabular")``
writes, deriving the net-creation / pad-binding work lists, and the
``~~``/``;`` wire encoding the Pascal ``NextBatchOp``/``GetBatchField``
helpers parse. No Altium round-trip anywhere in this file.
"""

from __future__ import annotations

from eda_agent.tools.pcb import (
    NETLIST_CSV_HEADER,
    _encode_bindings_param,
    derive_netlist_build,
    parse_tabular_netlist,
)


CSV_555 = """component,pin,pin_name,net
C1,1,1,N_CTRL
C1,2,2,GND
R1,1,1,VCC
R1,2,2,N_DIS
U1,1,GND,GND
U1,4,RST,VCC
U1,7,DIS,N_DIS
U1,8,VCC,VCC
"""


# ---------------------------------------------------------------------------
# parse_tabular_netlist
# ---------------------------------------------------------------------------

class TestParseTabularNetlist:
    def test_happy_path(self):
        out = parse_tabular_netlist(CSV_555)
        assert out["ok"] is True
        assert out["skipped_rows"] == 0
        assert len(out["nodes"]) == 8
        assert out["nodes"][0] == ("C1", "1", "1", "N_CTRL")
        assert out["nodes"][-1] == ("U1", "8", "VCC", "VCC")

    def test_preserves_file_order(self):
        out = parse_tabular_netlist(CSV_555)
        comps = [n[0] for n in out["nodes"]]
        assert comps == ["C1", "C1", "R1", "R1", "U1", "U1", "U1", "U1"]

    def test_empty_text_rejected(self):
        out = parse_tabular_netlist("")
        assert out["ok"] is False
        assert "empty" in out["reason"]

    def test_whitespace_only_rejected(self):
        out = parse_tabular_netlist("\n\n  \n")
        assert out["ok"] is False

    def test_wrong_header_rejected(self):
        out = parse_tabular_netlist("*PADS-PCB*\n*NET*\n")
        assert out["ok"] is False
        assert "tabular" in out["reason"]
        # The reason points at the fix.
        assert "proj_export_netlist" in out["reason"]

    def test_header_case_insensitive(self):
        text = "Component,Pin,Pin_Name,Net\nU1,1,VCC,VCC\n"
        out = parse_tabular_netlist(text)
        assert out["ok"] is True
        assert out["nodes"] == [("U1", "1", "VCC", "VCC")]

    def test_header_only_is_ok_but_empty(self):
        out = parse_tabular_netlist("component,pin,pin_name,net\n")
        assert out["ok"] is True
        assert out["nodes"] == []

    def test_crlf_line_endings(self):
        out = parse_tabular_netlist(CSV_555.replace("\n", "\r\n"))
        assert out["ok"] is True
        assert len(out["nodes"]) == 8

    def test_blank_lines_ignored(self):
        text = "component,pin,pin_name,net\n\nU1,1,VCC,VCC\n\n"
        out = parse_tabular_netlist(text)
        assert out["nodes"] == [("U1", "1", "VCC", "VCC")]
        assert out["skipped_rows"] == 0

    def test_wrong_field_count_skipped(self):
        text = ("component,pin,pin_name,net\n"
                "U1,1,VCC,VCC\n"
                "U1,2\n"                  # 2 fields
                "U1,3,A,B,EXTRA\n")       # 5 fields
        out = parse_tabular_netlist(text)
        assert out["ok"] is True
        assert len(out["nodes"]) == 1
        assert out["skipped_rows"] == 2

    def test_missing_required_fields_skipped(self):
        text = ("component,pin,pin_name,net\n"
                ",1,VCC,VCC\n"            # no component
                "U1,,VCC,VCC\n"           # no pin
                "U1,1,VCC,\n"             # no net
                "U1,1,,VCC\n")            # empty pin_name is allowed
        out = parse_tabular_netlist(text)
        assert out["nodes"] == [("U1", "1", "", "VCC")]
        assert out["skipped_rows"] == 3

    def test_quoted_fields_with_comma(self):
        # csv-quoted pin_name carrying a comma parses as 4 fields.
        text = ('component,pin,pin_name,net\n'
                'U1,5,"CTRL,V",N_CTRL\n')
        out = parse_tabular_netlist(text)
        assert out["nodes"] == [("U1", "5", "CTRL,V", "N_CTRL")]

    def test_fields_are_stripped(self):
        text = "component,pin,pin_name,net\n U1 , 1 , VCC , VCC \n"
        out = parse_tabular_netlist(text)
        assert out["nodes"] == [("U1", "1", "VCC", "VCC")]

    def test_header_constant_matches_export_format(self):
        # proj_export_netlist writes exactly this header line.
        assert ",".join(NETLIST_CSV_HEADER) == "component,pin,pin_name,net"


# ---------------------------------------------------------------------------
# derive_netlist_build
# ---------------------------------------------------------------------------

class TestDeriveNetlistBuild:
    def test_happy_path(self):
        nodes = parse_tabular_netlist(CSV_555)["nodes"]
        work = derive_netlist_build(nodes)
        assert work["nets"] == ["GND", "N_CTRL", "N_DIS", "VCC"]
        assert work["components"] == ["C1", "R1", "U1"]
        assert len(work["bindings"]) == 8

    def test_bindings_grouped_by_designator(self):
        # Interleaved input still comes out with each designator's rows
        # adjacent (the Pascal component cache relies on this).
        nodes = [
            ("U1", "1", "", "GND"),
            ("C1", "1", "", "VCC"),
            ("U1", "2", "", "VCC"),
            ("C1", "2", "", "GND"),
        ]
        work = derive_netlist_build(nodes)
        desigs = [b["designator"] for b in work["bindings"]]
        assert desigs == ["C1", "C1", "U1", "U1"]

    def test_binding_shape(self):
        work = derive_netlist_build([("U1", "A1", "VDD", "3V3")])
        assert work["bindings"] == [
            {"designator": "U1", "pin": "A1", "net": "3V3"}
        ]

    def test_duplicate_pad_rows_collapse_to_first(self):
        nodes = [
            ("U1", "1", "", "GND"),
            ("U1", "1", "", "VCC"),  # same pad again: first wins
        ]
        work = derive_netlist_build(nodes)
        assert len(work["bindings"]) == 1
        assert work["bindings"][0]["net"] == "GND"
        # Both nets still appear in the net list.
        assert work["nets"] == ["GND", "VCC"]

    def test_empty_nodes(self):
        work = derive_netlist_build([])
        assert work == {"nets": [], "bindings": [], "components": []}

    def test_deterministic(self):
        nodes = parse_tabular_netlist(CSV_555)["nodes"]
        assert derive_netlist_build(nodes) == derive_netlist_build(nodes)
        assert (derive_netlist_build(list(reversed(nodes)))["nets"]
                == derive_netlist_build(nodes)["nets"])

    def test_shared_net_counted_once(self):
        nodes = [
            ("U1", "8", "", "VCC"),
            ("R1", "1", "", "VCC"),
            ("C1", "1", "", "VCC"),
        ]
        assert derive_netlist_build(nodes)["nets"] == ["VCC"]


# ---------------------------------------------------------------------------
# _encode_bindings_param + round-trip against the Pascal wire grammar
# ---------------------------------------------------------------------------

def _next_batch_op(remaining: str) -> tuple[str, str]:
    """Python mirror of Main.pas NextBatchOp ('~~' op separator)."""
    while remaining:
        sep = remaining.find("~~")
        if sep < 0:
            return remaining, ""
        op, remaining = remaining[:sep], remaining[sep + 2:]
        if op:
            return op, remaining
    return "", ""


def _get_batch_field(op: str, key: str) -> str:
    """Python mirror of Main.pas GetBatchField (';' fields, '=' key/value)."""
    for field in op.split(";"):
        eq = field.find("=")
        if eq > 0 and field[:eq].upper() == key.upper():
            return field[eq + 1:]
    return ""


def _decode_bindings(wire: str) -> list[dict[str, str]]:
    out = []
    remaining = wire
    while remaining:
        op, remaining = _next_batch_op(remaining)
        if not op:
            break
        out.append({
            "designator": _get_batch_field(op, "designator"),
            "pin": _get_batch_field(op, "pin"),
            "net": _get_batch_field(op, "net"),
        })
    return out


class TestEncodeBindingsParam:
    def test_single(self):
        wire = _encode_bindings_param(
            [{"designator": "U1", "pin": "3", "net": "VCC"}]
        )
        assert wire == "designator=U1;pin=3;net=VCC"

    def test_multiple_joined_with_double_tilde(self):
        wire = _encode_bindings_param([
            {"designator": "U1", "pin": "1", "net": "GND"},
            {"designator": "U1", "pin": "8", "net": "VCC"},
        ])
        assert wire == ("designator=U1;pin=1;net=GND"
                        "~~designator=U1;pin=8;net=VCC")

    def test_invalid_entries_dropped(self):
        wire = _encode_bindings_param([
            {"designator": "", "pin": "1", "net": "GND"},
            {"designator": "U1", "pin": "", "net": "GND"},
            {"designator": "U1", "pin": "1", "net": ""},
            {"pin": "1", "net": "GND"},
            {"designator": "U1", "pin": "2", "net": "VCC"},
        ])
        assert wire == "designator=U1;pin=2;net=VCC"

    def test_empty_list(self):
        assert _encode_bindings_param([]) == ""

    def test_values_stringified_and_stripped(self):
        wire = _encode_bindings_param(
            [{"designator": " U1 ", "pin": 3, "net": " VCC "}]
        )
        assert wire == "designator=U1;pin=3;net=VCC"

    def test_round_trip_through_pascal_grammar(self):
        nodes = parse_tabular_netlist(CSV_555)["nodes"]
        bindings = derive_netlist_build(nodes)["bindings"]
        decoded = _decode_bindings(_encode_bindings_param(bindings))
        assert decoded == bindings

    def test_round_trip_alphanumeric_pins(self):
        bindings = [
            {"designator": "U3", "pin": "A12", "net": "DDR_DQ7"},
            {"designator": "J1", "pin": "S1", "net": "GND"},
        ]
        decoded = _decode_bindings(_encode_bindings_param(bindings))
        assert decoded == bindings
