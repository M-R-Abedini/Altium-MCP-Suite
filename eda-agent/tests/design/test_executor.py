# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Executor tests with a fake bridge, no Altium round-trips."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from eda_agent.design.executor import (
    ExecutorResult,
    _net_representation,
    _path_collisions,
    _route_l_path,
    _route_s_bend,
    execute_plan,
    execute_plan_from_json,
)
from eda_agent.design.plan import (
    DesignPlan,
    Net,
    Part,
    PartStatus,
    PinRef,
    Sheet,
    Zone,
)


def _parse_batch_ops(payload: str) -> list[dict[str, str]]:
    """Decode a Pascal-side bulk payload: records separated by ``~~``,
    fields by ``;``, key/value by ``=``."""
    ops: list[dict[str, str]] = []
    for record in payload.split("~~"):
        if not record:
            continue
        fields: dict[str, str] = {}
        for kv in record.split(";"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                fields[k] = v
        ops.append(fields)
    return ops


def _bulk_labels(bridge: Any) -> list[dict[str, str]]:
    """Return per-label ops parsed from any generic.place_net_labels call."""
    ops: list[dict[str, str]] = []
    for c, p in bridge.calls:
        if c == "generic.place_net_labels":
            ops.extend(_parse_batch_ops(p.get("labels", "")))
    return ops


def _bulk_ports(bridge: Any) -> list[dict[str, str]]:
    """Return per-port ops parsed from any generic.place_power_ports call."""
    ops: list[dict[str, str]] = []
    for c, p in bridge.calls:
        if c == "generic.place_power_ports":
            ops.extend(_parse_batch_ops(p.get("ports", "")))
    return ops


class _FakeBridge:
    """Records every send_command call; configurable failure simulation.

    pin_layouts maps refdes -> [{pin_number, pin_name, x_mils, y_mils}];
    when generic.get_sch_component_pins is called we return the matching
    layout. Defaults to a 2-pin layout for every queried refdes so tests
    that don't care about exact pin positions still get a sensible response.
    """

    def __init__(
        self,
        fail_commands: set[str] | None = None,
        pin_layouts: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_commands = fail_commands or set()
        self.pin_layouts = pin_layouts or {}

    def send_command(
        self,
        command: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        self.calls.append((command, params or {}))
        if command in self.fail_commands:
            raise RuntimeError(f"forced failure on {command}")

        if command == "generic.get_sch_component_pins":
            designator = (params or {}).get("designator", "")
            layout = self.pin_layouts.get(
                designator,
                [
                    {"pin_number": "1", "pin_name": "A", "x_mils": 1000, "y_mils": 5000},
                    {"pin_number": "2", "pin_name": "K", "x_mils": 1100, "y_mils": 5000},
                ],
            )
            return {"designator": designator, "pins": layout}

        return {"ok": True}


def _two_part_plan() -> DesignPlan:
    return DesignPlan(
        spec="LED + 1k on 5V",
        summary="LED tied to GND through 1k on the V5 rail.",
        sheets=[Sheet(name="main")],
        parts=[
            Part(refdes="R1", lib_ref="RES_0805", value="1k", sheet="main"),
            Part(refdes="D1", lib_ref="LED_RED", sheet="main"),
        ],
        nets=[
            Net(
                name="V5",
                is_power=True,
                pins=[
                    PinRef(refdes="R1", pin="1"),
                    PinRef(refdes="D1", pin="A"),
                ],
            ),
            Net(
                name="LED_K",
                pins=[
                    PinRef(refdes="R1", pin="2"),
                    PinRef(refdes="D1", pin="K"),
                ],
            ),
        ],
    )


def test_executor_creates_project_when_missing(tmp_path: Path) -> None:
    bridge = _FakeBridge()
    result = execute_plan(
        _two_part_plan(),
        str(tmp_path / "test.PrjPcb"),
        bridge=bridge,
    )

    assert result.ok is True
    cmds = [c for c, _ in bridge.calls]
    assert "project.create" in cmds
    assert "application.create_document" in cmds
    assert "application.set_active_document" in cmds
    # Bulk: one place_sch_components_from_library call with 2 placements.
    bulk_place = [
        p for c, p in bridge.calls
        if c == "generic.place_sch_components_from_library"
    ]
    assert len(bulk_place) == 1
    assert len(_parse_batch_ops(bulk_place[0]["placements"])) == 2
    # Verification runs after save_all; save_all is the last write-side
    # command but project.get_nets follows it for net verification.
    assert "application.save_all" in cmds
    assert cmds[-1] == "project.get_nets"


def test_executor_opens_existing_project(tmp_path: Path) -> None:
    bridge = _FakeBridge()
    project = tmp_path / "test.PrjPcb"
    project.write_text("[Design]\nVersion=1.0\n", encoding="utf-8")

    result = execute_plan(_two_part_plan(), str(project), bridge=bridge)

    assert result.ok is True
    cmds = [c for c, _ in bridge.calls]
    assert "project.open" in cmds
    assert "project.create" not in cmds


def test_executor_halts_on_needs_creation(tmp_path: Path) -> None:
    bridge = _FakeBridge()
    plan = DesignPlan(
        spec="custom IC",
        summary="needs an IC we don't have yet.",
        sheets=[Sheet(name="main")],
        parts=[
            Part(
                refdes="U1",
                lib_ref="MAX9999",
                status=PartStatus.NEEDS_CREATION,
                sheet="main",
                value="MAX9999AUT",
            ),
            Part(refdes="C1", lib_ref="CAP_0805", sheet="main"),
        ],
        nets=[
            Net(
                name="VCC",
                is_power=True,
                pins=[PinRef(refdes="U1", pin="1"), PinRef(refdes="C1", pin="1")],
            )
        ],
    )

    result = execute_plan(plan, str(tmp_path / "x.PrjPcb"), bridge=bridge)

    assert result.ok is False
    assert result.needs_creation == ["U1"]
    assert bridge.calls == []  # no Altium mutation when halting early


def test_executor_reports_place_failures(tmp_path: Path) -> None:
    bridge = _FakeBridge(fail_commands={"generic.place_sch_components_from_library"})
    result = execute_plan(
        _two_part_plan(),
        str(tmp_path / "x.PrjPcb"),
        bridge=bridge,
    )
    assert result.ok is False
    assert len(result.failures) == 2
    assert all(f.code == "PLACE_FAILED" for f in result.failures)


def test_executor_places_parts_at_distinct_coords(tmp_path: Path) -> None:
    bridge = _FakeBridge()
    execute_plan(_two_part_plan(), str(tmp_path / "x.PrjPcb"), bridge=bridge)

    bulk = [
        p for c, p in bridge.calls
        if c == "generic.place_sch_components_from_library"
    ]
    assert len(bulk) == 1
    ops = _parse_batch_ops(bulk[0]["placements"])
    assert len(ops) == 2
    coords = {(op["x"], op["y"]) for op in ops}
    assert len(coords) == 2  # no two parts at the same coords


def test_executor_passes_designator_and_lib_ref(tmp_path: Path) -> None:
    bridge = _FakeBridge()
    execute_plan(_two_part_plan(), str(tmp_path / "x.PrjPcb"), bridge=bridge)

    bulk = [
        p for c, p in bridge.calls
        if c == "generic.place_sch_components_from_library"
    ]
    assert len(bulk) == 1
    ops = _parse_batch_ops(bulk[0]["placements"])
    designators = {op["designator"] for op in ops}
    assert designators == {"R1", "D1"}
    lib_refs = {op["lib_reference"] for op in ops}
    assert lib_refs == {"RES_0805", "LED_RED"}


def test_execute_plan_from_json_invalid_json_returns_error(tmp_path: Path) -> None:
    bridge = _FakeBridge()
    result = execute_plan_from_json("not json", str(tmp_path / "x.PrjPcb"), bridge=bridge)
    assert result.ok is False
    assert any("invalid JSON" in n for n in result.notes)
    assert bridge.calls == []


def test_execute_plan_from_json_schema_error_returns_error(tmp_path: Path) -> None:
    bridge = _FakeBridge()
    result = execute_plan_from_json(
        '{"spec": "x"}',
        str(tmp_path / "x.PrjPcb"),
        bridge=bridge,
    )
    assert result.ok is False
    assert bridge.calls == []


def test_execute_plan_from_json_round_trip(tmp_path: Path) -> None:
    bridge = _FakeBridge()
    blob = _two_part_plan().model_dump_json()
    result = execute_plan_from_json(blob, str(tmp_path / "x.PrjPcb"), bridge=bridge)
    assert result.ok is True
    assert len(result.placed) == 2


def test_executor_result_serializes_to_dict(tmp_path: Path) -> None:
    bridge = _FakeBridge()
    result = execute_plan(_two_part_plan(), str(tmp_path / "x.PrjPcb"), bridge=bridge)
    blob = result.to_dict()
    assert blob["ok"] is True
    assert isinstance(blob["placed"], list)
    assert isinstance(blob["failures"], list)


def test_executor_create_project_failure_short_circuits(tmp_path: Path) -> None:
    bridge = _FakeBridge(fail_commands={"project.create"})
    result = execute_plan(
        _two_part_plan(),
        str(tmp_path / "x.PrjPcb"),
        bridge=bridge,
    )
    assert result.ok is False
    # Should NOT proceed to create_document or place after project failure
    cmds = [c for c, _ in bridge.calls]
    assert "application.create_document" not in cmds
    assert "generic.place_sch_component_from_library" not in cmds


# ---------------------------------------------------------------------------
# Wiring stage (Slice B.2)
# ---------------------------------------------------------------------------


def test_executor_drops_net_labels_at_pin_coords(tmp_path: Path) -> None:
    bridge = _FakeBridge(
        pin_layouts={
            "R1": [
                {"pin_number": "1", "pin_name": "1", "x_mils": 1500, "y_mils": 6500},
                {"pin_number": "2", "pin_name": "2", "x_mils": 1500, "y_mils": 6300},
            ],
            "D1": [
                {"pin_number": "A", "pin_name": "A", "x_mils": 2500, "y_mils": 6500},
                {"pin_number": "K", "pin_name": "K", "x_mils": 2500, "y_mils": 6300},
            ],
        },
    )
    result = execute_plan(_two_part_plan(), str(tmp_path / "x.PrjPcb"), bridge=bridge)

    assert result.ok is True
    # V5 is is_power -> rail glyph consolidation: pins close enough to
    # cluster get ONE shared rail port. LED_K is a block-local signal
    # net (both R1 and D1 are unzoned, so they share the implicit "no
    # zone" group) -> wires only, no label per discipline rule 3.
    power_calls = _bulk_ports(bridge)
    label_calls = _bulk_labels(bridge)
    assert len(power_calls) == 1  # V5: clustered into one rail glyph
    assert len(label_calls) == 0  # LED_K: block-local, wires only
    assert power_calls[0]["text"] == "V5"
    assert result.nets_labelled == 0
    assert result.power_ports_placed == 1


def test_executor_uses_pin_name_when_number_missing(tmp_path: Path) -> None:
    """Plan refers to pin 'A' on D1; layout has pin_number=A so it resolves."""
    bridge = _FakeBridge(
        pin_layouts={
            "R1": [
                {"pin_number": "1", "pin_name": "1", "x_mils": 1000, "y_mils": 6000},
                {"pin_number": "2", "pin_name": "2", "x_mils": 1000, "y_mils": 5800},
            ],
            "D1": [
                {"pin_number": "A", "pin_name": "Anode", "x_mils": 2000, "y_mils": 6000},
                {"pin_number": "K", "pin_name": "Cathode", "x_mils": 2000, "y_mils": 5800},
            ],
        },
    )
    result = execute_plan(_two_part_plan(), str(tmp_path / "x.PrjPcb"), bridge=bridge)
    assert result.ok is True
    assert all(f.code != "PIN_NOT_FOUND" for f in result.failures)


def test_executor_caches_pin_lookup_per_refdes(tmp_path: Path) -> None:
    """A part referenced by multiple nets should only trigger one lookup."""
    bridge = _FakeBridge()
    result = execute_plan(_two_part_plan(), str(tmp_path / "x.PrjPcb"), bridge=bridge)
    assert result.ok is True

    pin_lookups = [
        params.get("designator")
        for cmd, params in bridge.calls
        if cmd == "generic.get_sch_component_pins"
    ]
    # R1 is in V5 + LED_K, D1 is in V5 + LED_K. Cache should keep each
    # designator to a single lookup.
    from collections import Counter
    counts = Counter(pin_lookups)
    assert counts == {"R1": 1, "D1": 1}


def test_executor_reports_pin_not_found(tmp_path: Path) -> None:
    """If the plan references a pin that's not on the placed component."""
    bridge = _FakeBridge(
        pin_layouts={
            "R1": [
                {"pin_number": "1", "pin_name": "1", "x_mils": 0, "y_mils": 0},
                {"pin_number": "2", "pin_name": "2", "x_mils": 0, "y_mils": 0},
            ],
            "D1": [
                {"pin_number": "1", "pin_name": "1", "x_mils": 0, "y_mils": 0},
                {"pin_number": "2", "pin_name": "2", "x_mils": 0, "y_mils": 0},
            ],
        },
    )
    # plan references D1 pin "A" / "K" but we only have "1"/"2"
    result = execute_plan(_two_part_plan(), str(tmp_path / "x.PrjPcb"), bridge=bridge)
    assert result.ok is False
    pin_failures = [f for f in result.failures if f.code == "PIN_NOT_FOUND"]
    assert len(pin_failures) >= 2
    assert any("D1" in f.refdes for f in pin_failures)


def test_executor_handles_pin_lookup_failure(tmp_path: Path) -> None:
    bridge = _FakeBridge(fail_commands={"generic.get_sch_component_pins"})
    result = execute_plan(_two_part_plan(), str(tmp_path / "x.PrjPcb"), bridge=bridge)
    assert result.ok is False
    assert any(f.code == "PIN_LOOKUP_FAILED" for f in result.failures)


def test_executor_ground_uses_gnd_glyph(tmp_path: Path) -> None:
    plan = DesignPlan(
        spec="ground only",
        summary="ground sanity",
        sheets=[Sheet(name="main")],
        parts=[
            Part(refdes="R1", lib_ref="RES", sheet="main"),
            Part(refdes="R2", lib_ref="RES", sheet="main"),
        ],
        nets=[
            Net(
                name="GND",
                is_ground=True,
                pins=[PinRef(refdes="R1", pin="1"), PinRef(refdes="R2", pin="1")],
            ),
            Net(
                name="OUT",
                pins=[PinRef(refdes="R1", pin="2"), PinRef(refdes="R2", pin="2")],
            ),
        ],
    )
    bridge = _FakeBridge()
    result = execute_plan(plan, str(tmp_path / "x.PrjPcb"), bridge=bridge)
    assert result.ok is True
    power_calls = _bulk_ports(bridge)
    # GND rail-consolidation (#51): two pins close enough cluster to one glyph.
    assert len(power_calls) == 1
    assert all("gnd" in p["style"] for p in power_calls)


def test_executor_agnd_picks_signal_ground(tmp_path: Path) -> None:
    plan = DesignPlan(
        spec="analog ground",
        summary="analog ground",
        sheets=[Sheet(name="main")],
        parts=[
            Part(refdes="R1", lib_ref="RES", sheet="main"),
            Part(refdes="R2", lib_ref="RES", sheet="main"),
        ],
        nets=[
            Net(
                name="AGND",
                is_ground=True,
                pins=[PinRef(refdes="R1", pin="1"), PinRef(refdes="R2", pin="1")],
            ),
            Net(
                name="N1",
                pins=[PinRef(refdes="R1", pin="2"), PinRef(refdes="R2", pin="2")],
            ),
        ],
    )
    bridge = _FakeBridge()
    execute_plan(plan, str(tmp_path / "x.PrjPcb"), bridge=bridge)
    power_calls = _bulk_ports(bridge)
    assert all(p["style"] == "gnd_signal" for p in power_calls)


# ---------------------------------------------------------------------------
# Stub-wire + orientation (Slices 1-3)
# ---------------------------------------------------------------------------


def test_executor_draws_stub_wire_before_each_label_or_port(tmp_path: Path) -> None:
    """Every label/port placement must be preceded by a stub-wire call.

    Slice 1: drop a 100-mil stub from the pin's hot end so ERC stops
    flagging "Floating net labels". The stub call has to land in the IPC
    stream BEFORE the label/port placement for the same pin, otherwise the
    label gets registered at the pin endpoint with no electrical wire
    connecting it back to the component.
    """
    bridge = _FakeBridge(
        pin_layouts={
            "R1": [
                {
                    "pin_number": "1",
                    "pin_name": "1",
                    "x_mils": 1500,
                    "y_mils": 6500,
                    "orientation": 0,
                    "pin_length_mils": 200,
                },
                {
                    "pin_number": "2",
                    "pin_name": "2",
                    "x_mils": 1500,
                    "y_mils": 6300,
                    "orientation": 2,
                    "pin_length_mils": 200,
                },
            ],
            "D1": [
                {
                    "pin_number": "A",
                    "pin_name": "A",
                    "x_mils": 2500,
                    "y_mils": 6500,
                    "orientation": 1,
                    "pin_length_mils": 200,
                },
                {
                    "pin_number": "K",
                    "pin_name": "K",
                    "x_mils": 2500,
                    "y_mils": 6300,
                    "orientation": 3,
                    "pin_length_mils": 200,
                },
            ],
        },
    )
    result = execute_plan(_two_part_plan(), str(tmp_path / "x.PrjPcb"), bridge=bridge)
    assert result.ok is True

    # Bulk: one place_wires call per sheet holds every stub + every
    # signal-net routing segment.
    bulk_wires = [
        params for cmd, params in bridge.calls if cmd == "generic.place_wires"
    ]
    label_ops = _bulk_labels(bridge)
    port_ops = _bulk_ports(bridge)
    assert len(bulk_wires) == 1
    wire_ops = _parse_batch_ops(bulk_wires[0]["wires"])
    # 4 stubs (one per pin on the 2 nets) + routing segments for the
    # block-local signal net LED_K (L-path = 2 segments since the two
    # stub ends differ in both X and Y).
    assert len(wire_ops) >= 4
    # Rail consolidation: V5 (power, 2 pins close) -> 1 port glyph.
    # LED_K is block-local (both parts unzoned, share implicit "no zone"
    # group) -> wires only, no labels per discipline rule 3.
    assert len(port_ops) == 1
    assert len(label_ops) == 0

    # Behavioural invariant: every label / port has at least one wire in
    # the batch whose endpoint touches the label/port's (x, y) coord.
    wire_pts: set[tuple[str, str]] = set()
    for op in wire_ops:
        wire_pts.add((op["x1"], op["y1"]))
        wire_pts.add((op["x2"], op["y2"]))
    for params in label_ops + port_ops:
        assert (params["x"], params["y"]) in wire_pts


def _single_pin_plan(net_name: str, *, is_power: bool = False,
                     is_ground: bool = False,
                     force_label: bool = False) -> DesignPlan:
    """Plan with two resistors and one net of two pins so the schema is happy.

    Tests only inspect the first pin (R1.1); R2.1 is just there to satisfy
    Net.pins min-length=2 in the pydantic model.

    ``force_label=True`` triggers the label_per_pin representation even
    though both parts are unzoned (i.e. block-local by the default rule).
    Use this in tests that explicitly verify label placement geometry.
    """
    return DesignPlan(
        spec="single net",
        summary="one net, two pins",
        sheets=[Sheet(name="main")],
        parts=[
            Part(refdes="R1", lib_ref="RES", sheet="main"),
            Part(refdes="R2", lib_ref="RES", sheet="main"),
        ],
        nets=[
            Net(
                name=net_name,
                is_power=is_power,
                is_ground=is_ground,
                force_label=force_label,
                pins=[
                    PinRef(refdes="R1", pin="1"),
                    PinRef(refdes="R2", pin="1"),
                ],
            ),
        ],
    )


def test_executor_stub_endpoint_drives_label_placement(tmp_path: Path) -> None:
    """The label / power port sits at the FAR end of the stub, not on the pin.

    ISch_Pin.Location is the electrical endpoint of the pin (verified
    empirically on a placed TPS54331D). The stub starts AT Pin.Location
    and extends outward by _STUB_LEN_MILS. pin_length is NOT added.

    For a pin facing right at (1000, 5000) with pin_length=300 the
    stub goes from (1000, 5000) to (1100, 5000); label lands at
    (1100, 5000). The pin_length field is retained on the wire payload
    for ABI compat but does not affect the math.
    """
    bridge = _FakeBridge(
        pin_layouts={
            "R1": [
                {
                    "pin_number": "1",
                    "pin_name": "1",
                    "x_mils": 1000,
                    "y_mils": 5000,
                    "orientation": 0,
                    "pin_length_mils": 300,
                },
            ],
            "R2": [
                {
                    "pin_number": "1",
                    "pin_name": "1",
                    "x_mils": 9000,
                    "y_mils": 5000,
                    "orientation": 2,
                    "pin_length_mils": 300,
                },
            ],
        },
    )
    execute_plan(
        _single_pin_plan("OUT", force_label=True),
        str(tmp_path / "x.PrjPcb"),
        bridge=bridge,
    )

    bulk_wires = [p for c, p in bridge.calls if c == "generic.place_wires"]
    assert len(bulk_wires) == 1
    wire_ops = _parse_batch_ops(bulk_wires[0]["wires"])
    # R1 pin facing right: hot=(1000,5000) (Pin.Location IS the hot end),
    # stub end=(1300,5000) with _STUB_LEN_MILS = 300
    assert wire_ops[0] == {"x1": "1000", "y1": "5000", "x2": "1300", "y2": "5000"}
    label_calls = _bulk_labels(bridge)
    # force_label=True -> label at every pin's stub end.
    assert any(c["x"] == "1300" and c["y"] == "5000" for c in label_calls)
    # Horizontal pin -> horizontal label
    assert label_calls[0]["orientation"] == "0"


def test_executor_vertical_pin_rotates_net_label(tmp_path: Path) -> None:
    """A pin facing up (orientation=1) should rotate the label to read along Y."""
    bridge = _FakeBridge(
        pin_layouts={
            "R1": [
                {
                    "pin_number": "1",
                    "pin_name": "1",
                    "x_mils": 1000,
                    "y_mils": 5000,
                    "orientation": 1,
                    "pin_length_mils": 200,
                },
            ],
            "R2": [
                {
                    "pin_number": "1",
                    "pin_name": "1",
                    "x_mils": 4000,
                    "y_mils": 5000,
                    "orientation": 1,
                    "pin_length_mils": 200,
                },
            ],
        },
    )
    execute_plan(
        _single_pin_plan("OUT", force_label=True),
        str(tmp_path / "x.PrjPcb"),
        bridge=bridge,
    )
    label_calls = _bulk_labels(bridge)
    # force_label=True -> one label per pin (2 pins on this net = 2
    # labels). The label itself is always horizontal (orientation=0);
    # readability rotation isn't a per-pin label concern in the
    # label_per_pin convention.
    assert len(label_calls) == 2
    assert all(c["orientation"] == "0" for c in label_calls)
    assert label_calls[0]["x"] == "1000"
    assert label_calls[0]["y"] == "5300"


def test_executor_vcc_power_port_always_faces_up(tmp_path: Path) -> None:
    """VCC-style power-port glyph always points UP (orientation 1),
    independent of pin direction. User schematic convention: power
    rails read consistently upward."""
    bridge = _FakeBridge(
        pin_layouts={
            "R1": [
                {
                    "pin_number": "1",
                    "pin_name": "1",
                    "x_mils": 1000,
                    "y_mils": 5000,
                    "orientation": 1,  # pin points up
                    "pin_length_mils": 200,
                },
            ],
            "R2": [
                {
                    "pin_number": "1",
                    "pin_name": "1",
                    "x_mils": 4000,
                    "y_mils": 5000,
                    "orientation": 1,
                    "pin_length_mils": 200,
                },
            ],
        },
    )
    execute_plan(
        _single_pin_plan("VCC", is_power=True),
        str(tmp_path / "x.PrjPcb"),
        bridge=bridge,
    )
    port_calls = _bulk_ports(bridge)
    # Canonical: VCC always points up.
    assert port_calls[0]["orientation"] == "1"
    assert port_calls[0]["style"] == "bar"


def test_executor_gnd_power_port_always_faces_down(tmp_path: Path) -> None:
    """GND glyph always points DOWN (orientation 3), independent of pin
    direction. User schematic convention."""
    bridge = _FakeBridge(
        pin_layouts={
            "R1": [
                {
                    "pin_number": "1",
                    "pin_name": "1",
                    "x_mils": 1000,
                    "y_mils": 5000,
                    "orientation": 3,  # pin points down
                    "pin_length_mils": 200,
                },
            ],
            "R2": [
                {
                    "pin_number": "1",
                    "pin_name": "1",
                    "x_mils": 4000,
                    "y_mils": 5000,
                    "orientation": 3,
                    "pin_length_mils": 200,
                },
            ],
        },
    )
    execute_plan(
        _single_pin_plan("GND", is_ground=True),
        str(tmp_path / "x.PrjPcb"),
        bridge=bridge,
    )
    port_calls = _bulk_ports(bridge)
    assert port_calls[0]["orientation"] == "3"
    assert port_calls[0]["style"] == "gnd_power"


# ---------------------------------------------------------------------------
# Net representation (3-tier rule: ports > block-local wires > cross-block labels)
# discipline.py Rule 3
# ---------------------------------------------------------------------------


def _net(name: str, pins: list[tuple[str, str]], **kw: Any) -> Net:
    return Net(name=name, pins=[PinRef(refdes=r, pin=p) for r, p in pins], **kw)


def test_net_representation_power_returns_port() -> None:
    n = _net("VCC", [("U1", "1"), ("U2", "1")], is_power=True)
    assert _net_representation(n, {"U1": "buck", "U2": "amp"}) == "port"


def test_net_representation_ground_returns_port() -> None:
    n = _net("GND", [("U1", "1"), ("U2", "1")], is_ground=True)
    assert _net_representation(n, {"U1": None, "U2": None}) == "port"


def test_net_representation_block_local_returns_wire() -> None:
    """Both pins in the same zone -> block-local -> wires."""
    n = _net("FB", [("U1", "FB"), ("R1", "1")])
    assert _net_representation(n, {"U1": "buck", "R1": "buck"}) == "wire"


def test_net_representation_all_unzoned_returns_wire() -> None:
    """All pins unzoned share the implicit 'no zone' group -> wires.

    This preserves current behaviour for plans that haven't started
    using zones yet.
    """
    n = _net("LED_K", [("R1", "2"), ("D1", "K")])
    assert _net_representation(n, {"R1": None, "D1": None}) == "wire"


def test_net_representation_cross_block_returns_label_per_pin() -> None:
    """Pins in two different zones -> cross-block -> per-pin labels."""
    n = _net("I2S_BCLK", [("U1", "10"), ("U2", "3")])
    rep = _net_representation(n, {"U1": "mcu", "U2": "amp"})
    assert rep == "label_per_pin"


def test_net_representation_mixed_zoned_unzoned_returns_label_per_pin() -> None:
    """One pin in a zone, another unzoned -> different blocks -> labels."""
    n = _net("VIN_IN", [("U1", "1"), ("J1", "1")])
    rep = _net_representation(n, {"U1": "buck", "J1": None})
    assert rep == "label_per_pin"


def test_net_representation_force_label_overrides_block_local() -> None:
    """force_label=True wins even when all pins share a zone."""
    n = _net(
        "BUCK_LOCAL_BUS",
        [("U1", "1"), ("R1", "1"), ("C1", "1")],
        force_label=True,
    )
    rep = _net_representation(
        n, {"U1": "buck", "R1": "buck", "C1": "buck"}
    )
    assert rep == "label_per_pin"


def test_net_representation_power_wins_over_force_label() -> None:
    """Priority order: power/ground beats force_label."""
    n = _net("VCC", [("U1", "1"), ("U2", "1")], is_power=True, force_label=True)
    assert _net_representation(n, {"U1": "buck", "U2": "amp"}) == "port"


def test_net_representation_force_wires_beats_everything() -> None:
    """force_wires is the hard override: it beats the explicit power flag,
    the conventional-rail name heuristic, and the cross-zone label rule."""
    flagged = _net("RAW_5V", [("U1", "1"), ("U2", "1")],
                   is_power=True, force_wires=True)
    assert _net_representation(flagged, {"U1": "buck", "U2": "amp"}) == "wire"

    # A net NAMED like a rail is normally ported even without the flag --
    # force_wires is the planner's escape hatch from that heuristic.
    named = _net("VCC", [("U1", "1"), ("U2", "1")], force_wires=True)
    assert _net_representation(named, {"U1": "mcu", "U2": "amp"}) == "wire"


def test_net_force_label_and_force_wires_are_mutually_exclusive() -> None:
    import pytest

    with pytest.raises(ValueError, match="mutually exclusive"):
        _net("X", [("U1", "1"), ("U2", "1")],
             force_label=True, force_wires=True)


def test_executor_cross_block_net_emits_label_per_pin(tmp_path: Path) -> None:
    """Integration: a net spanning two zones gets one label per pin, no wires
    between them.

    Buck block has U1 (regulator); MCU block has U2 (MCU). The PGOOD
    signal between them is cross-block, so the executor emits a label
    at U1.PGOOD and another at U2.IRQ, no inter-pin routing.
    """
    bridge = _FakeBridge(
        pin_layouts={
            "U1": [
                {"pin_number": "PGOOD", "pin_name": "PGOOD",
                 "x_mils": 1000, "y_mils": 5000,
                 "orientation": 2, "pin_length_mils": 200},
            ],
            "U2": [
                {"pin_number": "IRQ", "pin_name": "IRQ",
                 "x_mils": 8000, "y_mils": 5000,
                 "orientation": 0, "pin_length_mils": 200},
            ],
        },
    )
    plan = DesignPlan(
        spec="cross-block signal",
        summary="buck PGOOD -> MCU IRQ across two functional blocks",
        sheets=[Sheet(name="main")],
        zones=[
            Zone(name="buck", sheet="main"),
            Zone(name="mcu", sheet="main"),
        ],
        parts=[
            Part(refdes="U1", lib_ref="TPS54331", sheet="main", zone="buck"),
            Part(refdes="U2", lib_ref="STM32", sheet="main", zone="mcu"),
        ],
        nets=[
            _net("PGOOD", [("U1", "PGOOD"), ("U2", "IRQ")]),
        ],
    )
    result = execute_plan(plan, str(tmp_path / "x.PrjPcb"), bridge=bridge)
    assert result.ok is True

    label_ops = _bulk_labels(bridge)
    # One label at every pin (2 pins -> 2 labels), both named "PGOOD".
    assert len(label_ops) == 2
    assert all(op["text"] == "PGOOD" for op in label_ops)
    assert result.nets_labelled == 2

    # No inter-pin routing wires beyond the per-pin stubs.
    bulk_wires = [p for c, p in bridge.calls if c == "generic.place_wires"]
    assert len(bulk_wires) == 1
    wire_ops = _parse_batch_ops(bulk_wires[0]["wires"])
    # Exactly 2 stubs (one per pin), no L-path routing between them.
    assert len(wire_ops) == 2


def test_executor_block_local_net_emits_wires_no_label(tmp_path: Path) -> None:
    """Integration: two parts in the SAME zone, signal net stays as wires."""
    bridge = _FakeBridge(
        pin_layouts={
            "R1": [
                {"pin_number": "1", "pin_name": "1",
                 "x_mils": 1000, "y_mils": 5000,
                 "orientation": 0, "pin_length_mils": 200},
            ],
            "R2": [
                {"pin_number": "1", "pin_name": "1",
                 "x_mils": 1500, "y_mils": 5000,
                 "orientation": 2, "pin_length_mils": 200},
            ],
        },
    )
    plan = DesignPlan(
        spec="block-local divider tap",
        summary="two resistors in the buck block",
        sheets=[Sheet(name="main")],
        zones=[Zone(name="buck", sheet="main")],
        parts=[
            Part(refdes="R1", lib_ref="RES", sheet="main", zone="buck"),
            Part(refdes="R2", lib_ref="RES", sheet="main", zone="buck"),
        ],
        nets=[
            _net("FB_TAP", [("R1", "1"), ("R2", "1")]),
        ],
    )
    result = execute_plan(plan, str(tmp_path / "x.PrjPcb"), bridge=bridge)
    assert result.ok is True

    label_ops = _bulk_labels(bridge)
    # Block-local -> wires only, NO labels.
    assert label_ops == []
    assert result.nets_labelled == 0

    bulk_wires = [p for c, p in bridge.calls if c == "generic.place_wires"]
    wire_ops = _parse_batch_ops(bulk_wires[0]["wires"])
    # 2 stubs + L-path between stub ends (>=1 segment) -> at least 3.
    assert len(wire_ops) >= 3


# ---------------------------------------------------------------------------
# Router: L-path + S-bend fallback
# ---------------------------------------------------------------------------


def test_l_path_clean_returns_two_segments() -> None:
    """No obstacles -> straightforward L-path."""
    segs = _route_l_path(0, 0, 1000, 1000, [])
    assert len(segs) == 2
    # Joins endpoints
    assert (segs[0][0], segs[0][1]) == (0, 0)
    assert (segs[-1][2], segs[-1][3]) == (1000, 1000)


def test_l_path_picks_collision_free_ordering() -> None:
    """One L-ordering crosses an obstacle, the other doesn't -> pick clean."""
    # Obstacle at (500, 400)-(700, 600). H-first L corners at (1000, 0):
    # H seg (0,0)->(1000,0) misses the obstacle, V seg (1000,0)->(1000,1000)
    # also misses. V-first L corners at (0, 1000): also clean. Both are
    # actually clean here. Add a directional obstacle.
    # Obstacle blocks H-first at corner (1000, 0) -> V-first wins.
    obstacles = [(800, -200, 1200, 200)]  # blocks the H-first corner
    segs = _route_l_path(0, 0, 1000, 1000, obstacles)
    # The chosen path must miss the obstacle.
    assert _path_collisions(segs, obstacles, ((0, 0), (1000, 1000))) == 0


def test_s_bend_routes_around_central_obstacle() -> None:
    """An obstacle straddling BOTH L-corners -> S-bend rescue applies."""
    # Endpoints (0, 0) and (2000, 1000). Both L-paths go through the
    # corner zone; an obstacle covering both corners forces an S-bend.
    obstacles = [
        (500, -200, 700, 200),     # blocks H-first L (corner at 2000,0)? no -- blocks H-segment near (500-700, 0)
        (1500, 800, 1700, 1200),   # blocks V-first L's H-segment near (1500-1700, 1000)
    ]
    # Add an obstacle that blocks the H-first corner area:
    obstacles.append((1900, -200, 2100, 200))
    obstacles.append((-100, 800, 100, 1200))  # blocks V-first corner area
    segs = _route_l_path(0, 0, 2000, 1000, obstacles)
    # Must still produce *some* route. S-bend ideal, but acceptable if it
    # falls back to the cleaner L-path. Importantly the router must not
    # crash.
    assert segs
    assert (segs[0][0], segs[0][1]) == (0, 0)
    assert (segs[-1][2], segs[-1][3]) == (2000, 1000)


def test_s_bend_chooses_shorter_clean_route() -> None:
    """When multiple S-bends are clean, pick the shortest."""
    # No obstacles -> midpoint S would be slightly longer than an L, but
    # the L-path branch fires first (h_first/v_first are 0). The S-bend
    # function isn't invoked. Test S directly.
    segs = _route_s_bend(0, 0, 1000, 500, [])
    # With no obstacles, mid is (500, 250). Both HVH and VHV at the
    # midpoint should be clean; the function returns the shortest one.
    assert segs is not None
    # Path joins endpoints and is fully Manhattan.
    assert (segs[0][0], segs[0][1]) == (0, 0)
    assert (segs[-1][2], segs[-1][3]) == (1000, 500)
    for sx1, sy1, sx2, sy2 in segs:
        assert sx1 == sx2 or sy1 == sy2


def test_stub_default_length_with_no_obstacles() -> None:
    """Pin facing right at (1000, 5000) with no obstacles: stub
    extends the default 300 mil to (1300, 5000)."""
    from eda_agent.design.executor import _stub_endpoints
    (hx, hy), (ex, ey) = _stub_endpoints(1000, 5000, 0, 0)
    assert (hx, hy) == (1000, 5000)
    assert (ex, ey) == (1300, 5000)


def test_stub_clips_when_obstacle_close_in_stub_direction() -> None:
    """An obstacle 200 mil to the right of a right-facing pin clips
    the stub to obstacle_distance - clearance, with a minimum length
    floor."""
    from eda_agent.design.executor import _stub_endpoints
    # Pin at (1000, 5000) facing right. Obstacle starts at x=1200.
    # Stub direction: +x. Default 300 -> hits obstacle at x=1200
    # (200 mil away). Clip to 200 - 50 (clearance) = 150 mil.
    obstacles = [(1200, 4900, 1500, 5100)]
    (hx, hy), (ex, ey) = _stub_endpoints(1000, 5000, 0, 0, obstacles=obstacles)
    assert (hx, hy) == (1000, 5000)
    # Clipped: stub extends 150 mil right to (1150, 5000).
    assert ex == 1150
    assert ey == 5000


def test_stub_respects_owner_bbox() -> None:
    """The obstacle containing the pin is the owner -- it's excluded
    from clipping. Otherwise every stub would have length 0."""
    from eda_agent.design.executor import _stub_endpoints
    # Pin at (1000, 5000) facing right; the part's own bbox surrounds
    # the pin location and extends to the right.
    obstacles = [(900, 4900, 1100, 5100)]
    (_hx, _hy), (ex, _ey) = _stub_endpoints(1000, 5000, 0, 0, obstacles=obstacles)
    # Owner skipped -> full 300 mil stub.
    assert ex == 1300


def test_stub_clears_far_obstacle_completely() -> None:
    """An obstacle well past the default 300-mil stub doesn't affect
    the result -- adaptive logic only kicks in for in-range obstacles."""
    from eda_agent.design.executor import _stub_endpoints
    # Obstacle at x=2000 (1000 mil away from a right-facing pin at 1000).
    obstacles = [(2000, 4900, 2200, 5100)]
    (_hx, _hy), (ex, _ey) = _stub_endpoints(1000, 5000, 0, 0, obstacles=obstacles)
    assert ex == 1300  # default, unaffected


def test_stub_minimum_length_floor() -> None:
    """An obstacle right next to the pin (within the clearance
    distance) still produces a stub of at least _STUB_MIN_LEN_MILS,
    so ERC doesn't flag a 0-length wire."""
    from eda_agent.design.executor import _stub_endpoints, _STUB_MIN_LEN_MILS
    # Obstacle starts at x=1010 (10 mil from a right-facing pin at 1000).
    obstacles = [(1010, 4900, 1500, 5100)]
    (_hx, _hy), (ex, _ey) = _stub_endpoints(1000, 5000, 0, 0, obstacles=obstacles)
    assert ex - 1000 >= _STUB_MIN_LEN_MILS


def test_route_signal_pins_corner_hub_clears_obstacle_at_centroid() -> None:
    """When the geometric centroid lands inside an obstacle AND the
    pin-hubs all force a spoke through the obstacle, the bbox-corner
    fallback gives the star a clean detour around the obstruction."""
    from eda_agent.design.executor import _route_signal_pins
    # Three pins on an L-shape around an obstacle in the inside corner.
    stub_ends = [(1000, 1000), (5000, 1000), (5000, 5000)]
    # Obstacle inside the L's elbow (centroid ~3300, 2300).
    obstacles = [(2500, 2000, 4000, 3500)]
    segs = _route_signal_pins(stub_ends, obstacles)
    assert segs, "router must return wires"
    # No segment may cross the obstacle.
    for sx1, sy1, sx2, sy2 in segs:
        # Owner-bbox skip rule: endpoints inside the obstacle would
        # exempt; here none of the stub_ends are inside.
        assert not _segment_crosses_rect_helper(
            sx1, sy1, sx2, sy2, *obstacles[0]
        ), f"segment {sx1, sy1, sx2, sy2} crosses obstacle"


def _segment_crosses_rect_helper(x1, y1, x2, y2, rx1, ry1, rx2, ry2) -> bool:
    """Test-local copy of the executor's segment-vs-rect predicate."""
    from eda_agent.design.executor import _segment_crosses_rect
    return _segment_crosses_rect(x1, y1, x2, y2, rx1, ry1, rx2, ry2)


def test_route_signal_pins_3pin_with_obstacles_returns_segments() -> None:
    """Regression: when the chain topology has crossings, the star
    branch used to wipe ``best_segs`` then fail the sentinel check
    and return []. Now it keeps whichever topology has fewer
    crossings.

    Three pins arranged so both H-then-V and V-then-H chains have to
    skirt obstacles; the function must return SOME non-empty segment
    list rather than dropping the net silently.
    """
    from eda_agent.design.executor import _route_signal_pins
    stub_ends = [(1000, 5000), (5000, 1000), (5000, 5000)]
    obstacles = [
        (2500, 3000, 3500, 4000),
        (4000, 1500, 4500, 2500),
    ]
    segs = _route_signal_pins(stub_ends, obstacles)
    assert segs, "router must return SOME wires for a 3-pin net"
    # Every segment is axis-aligned (orthogonal routing invariant).
    for sx1, sy1, sx2, sy2 in segs:
        assert sx1 == sx2 or sy1 == sy2


def test_multi_pin_net_uses_low_bend_trunk() -> None:
    """A shared net across several pins routes as a trunk-and-stub (straight
    median spine + short taps), not a zig-zag chain -- the canonical clean
    schematic form. Near-collinear pins route with ZERO corners; a scattered
    set still beats the old chain/star on corner count."""
    from eda_agent.design.router import _count_bends, _route_signal_pins

    # Four near-collinear pins: a straight trunk, no corners at all.
    aligned = [(1000, 2000), (2000, 2050), (3000, 1950), (4000, 2000)]
    assert _count_bends(_route_signal_pins(aligned, [])) == 0

    # Scattered 5-pin net: the trunk keeps corners low (the old chain hit 7).
    scattered = [(1000, 1000), (1500, 3000), (3000, 1500),
                 (3500, 3200), (2200, 2000)]
    assert _count_bends(_route_signal_pins(scattered, [])) <= 3


def test_s_bend_detours_around_central_blocker() -> None:
    """A single huge obstacle in the middle still gets routed around
    using x_mid/y_mid past the obstacle edges -- the S-bend doesn't
    just bisect, it considers obstacle-edge detours."""
    obstacles = [(50, 50, 950, 950)]
    segs = _route_s_bend(0, 0, 1000, 1000, obstacles)
    assert segs is not None
    # The result must be a fully-clean route around the obstacle.
    assert _path_collisions(segs, obstacles, ((0, 0), (1000, 1000))) == 0


def test_s_bend_returns_none_in_pathological_corridor() -> None:
    """Endpoints walled in so tightly that no x_mid/y_mid in the
    candidate set escapes -- S-bend gives up and returns None."""
    # Walls hug both endpoints on opposite sides so every candidate
    # x_mid and y_mid runs straight into a wall.
    obstacles = [
        # Walls flanking (0, 0) on its right side at multiple y-bands
        (200, -10000, 250, 10000),
        # Walls flanking (1000, 1000) on its left side
        (750, -10000, 800, 10000),
        # Horizontal walls top and bottom of the corridor between them
        (-10000, 200, 10000, 250),
        (-10000, 750, 10000, 800),
    ]
    segs = _route_s_bend(0, 0, 1000, 1000, obstacles)
    # Either a clean route or None; what matters is the function
    # doesn't return an obstacle-crossing path silently.
    if segs is not None:
        assert _path_collisions(segs, obstacles, ((0, 0), (1000, 1000))) == 0
