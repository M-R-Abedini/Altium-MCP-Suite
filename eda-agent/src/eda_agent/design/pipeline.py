# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Plan -> SchematicCanvas, pure Python.

The pipeline takes a validated DesignPlan plus an extracted symbol library
and produces a fully populated SchematicCanvas: components placed, wires
routed, junctions detected, net labels positioned, power ports placed.
Zero Altium round-trips during layout. The only Altium contact this
module makes is via the SymbolExtractor on a cache miss; everything
downstream is pure data.

The downstream AltiumEmitter (design.emitter) takes the populated canvas
and writes it to a project + sheet in one batched IPC pass.

Pipeline stages, in order:

1. Symbol extraction (once per unique (lib_path, lib_ref)).
2. Placement: compute_layout(plan) -> list[PlacedPart].
3. Canvas construction: PlacedParts + SymbolModels -> SymbolInstances.
4. Wiring per net (block-local vs cross-block):
   a. Compute world stub endpoints from canvas.pin_world().
   b. Two-pass: collect every net's stub-ends, then route each net
      treating other-net stub-ends as point obstacles.
   c. Power/ground nets get port clusters; block-local signal nets get
      wires; cross-block signal nets get labels.
5. Junction detection on the assembled wire list.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any, Optional

from eda_agent.design.canvas import (
    POWER_RAIL_CLUSTER_RADIUS_MILS,
    Junction,
    NetLabel,
    PowerPort,
    SchematicCanvas,
    Sheet,
    SymbolInstance,
    WireSegment,
)
from eda_agent.design._wiring import (
    _bom_lookup,
    _cross_net_meeting_counts,
    _detect_junctions,
    _ground_style,
    _is_ground_net,
    _is_power_net,
    _apply_no_stranded_parts_rule,
    _label_justification,
    _net_representation,
    _part_parameters,
    _power_port_orientation,
)
from eda_agent.design.composer import compose_layout
from eda_agent.design.force_directed import _hard_shove_pass
from eda_agent.design.layout import compute_layout, order_rail_columns
from eda_agent.design.plan import DesignPlan, Net, PartStatus
from eda_agent.design.priors import (
    apply_placement_priors,
    load_priors,
    resnap_crystal_clusters,
    resnap_motif_clusters,
)
from eda_agent.design.quality import LayoutScore, score_canvas
from eda_agent.design.router import (
    _adaptive_stub_length,
    _pin_direction_vector,
    _route_l_path,
    _route_signal_pins,
    _stub_endpoints,
)
from eda_agent.design.symbols import SymbolExtractor, SymbolModel

logger = logging.getLogger("eda_agent.design.pipeline")

# Pin-aware force-directed attractor-stiffness sweep. build_best builds + scores
# the FD layout at each value and keeps the lowest, because the score-vs-K
# landscape is chaotic and the best value varies per board. Dense by default
# (offline, ~50 ms/eval); the test conftest patches it down for speed.
_FD_K_SWEEP: tuple = tuple(round(0.02 + i * (0.28 / 99), 4) for i in range(100))

# Wire-vs-label span threshold, mils. A signal net whose stub-end
# half-perimeter exceeds this draws as per-pin labels instead of a
# sheet-crossing wire trunk. Calibrated on the fixtures: a divider's
# local VMID (span ~1400) must stay wired; a feedback tap crossing the
# sheet (span ~3100) must become labels.
_LABEL_SPAN_MILS = 2400

# Net roles exempt from the span demotion: current-carrying loops that
# must stay visually traceable as drawn copper at any span.
_WIRED_ROLES = frozenset({"switch", "high_current", "power_path"})


def _placement_pass(plan, result):  # type: ignore[no-untyped-def]
    """Run motif composer + Sugiyama-fallback placement.

    Three-layer placement strategy:

    1. ``compose_layout(plan)`` runs Sugiyama first as a baseline,
       then overlays motif-driven positions for parts matched by the
       motif catalogue (bypass cap, voltage divider, fb_divider, ...).
       Parts unmatched by any motif keep their Sugiyama position.
    2. The pipeline's downstream ``apply_placement_priors`` pass adds
       per-role-pair nudges on top (single-part canonical offsets like
       "vcc_decoup goes 400 mils above its IC").
    3. Wiring + port-routing then sees a sensible placement and
       produces clean schematic output.
    """
    composer_result = compose_layout(plan)
    if composer_result.motif_matches:
        result.notes.append(PipelineNote(
            severity="info",
            text=(
                f"composer: {len(composer_result.motif_matches)} motif "
                f"matches ({', '.join(m.motif_name for m in composer_result.motif_matches)}); "
                f"{len(composer_result.motif_parts)} parts placed by motif, "
                f"{len(composer_result.fallback_parts)} parts via Sugiyama fallback"
            ),
        ))
    return composer_result.placements


@dataclass
class PipelineNote:
    """One human-readable note from the pipeline run.

    ``refdes`` carries the affected part as structured data when the note
    is about a specific part (e.g. a needs_creation skip), so callers read
    it directly instead of re-parsing ``text`` -- formatted text is for
    humans, not a data channel.
    """

    severity: str  # "info" | "warning" | "error"
    text: str
    refdes: Optional[str] = None


@dataclass
class PipelineResult:
    """Output of `build_canvas_from_plan`.

    The canvas is what the emitter consumes. Notes surface
    soft-failures (e.g. a plan part with status=needs_creation that the
    pipeline skipped) so the caller can decide whether to proceed to
    emit or stop. Failures are hard problems that must be addressed
    before emit (missing symbol, pin id mismatch, etc.).

    ``parameter_stamps`` is the {refdes: {param_name: value}} dict the
    emitter consumes to bind Value / Manufacturer / MPN / Footprint /
    Datasheet on each placed instance. Derived from the plan's Part
    fields (Part fields win over BomLine fallback).
    """

    canvas: SchematicCanvas = field(default_factory=SchematicCanvas)
    notes: list[PipelineNote] = field(default_factory=list)
    failures: list[PipelineNote] = field(default_factory=list)
    parameter_stamps: dict[str, dict[str, str]] = field(default_factory=dict)
    placement_count: int = 0
    wire_count: int = 0
    label_count: int = 0
    power_port_count: int = 0
    junction_count: int = 0
    # Nets that WOULD have been wired but were demoted to net-labels because
    # wiring them at this placement would short on another net. A pure
    # placement-quality signal (connectivity is preserved either way): a layout
    # that needs FEWER of these is more readable, so best-of prefers it over a
    # lower-wirelength variant that resorts to more labels.
    forced_label_count: int = 0
    # Total stub-end half-perimeter (mils) of nets deliberately drawn as
    # labels because their span exceeded _LABEL_SPAN_MILS. Selection
    # ranking charges this as if the net had been wired -- otherwise a
    # SPRAWLED placement profits from the demotion (its long nets turn
    # into free labels and its wirelength score drops).
    span_labelled_mils: int = 0

    @property
    def ok(self) -> bool:
        return not self.failures


def build_canvas_from_plan(
    plan: DesignPlan,
    extractor: SymbolExtractor,
    *,
    layout_overrides: Optional[dict[str, Any]] = None,
    placement_hints: Optional[dict[str, dict[str, int]]] = None,
    port_hints: Optional[dict[str, dict[str, int]]] = None,
    strict_shorts: bool = True,
    polish: bool = False,
) -> PipelineResult:
    """Run the full plan -> canvas pipeline.

    ``polish``: enable convention polish (pin-polarity repair) that
    must NOT run while best-of candidates are being scored: polishing
    every candidate compensates bad placements more than good ones
    (a wrong-side passive's wire shrinks when flipped toward its IC),
    flattening the score landscape until a side-wrong layout wins.
    ``build_best_canvas_from_plan`` scores candidates unpolished, then
    rebuilds only the winner with ``polish=True``.

    All inputs and outputs are pure data; this function does not touch
    Altium except indirectly via the extractor (which caches anyway).

    ``layout_overrides``: optional ``{refdes: PlacedPart}`` mapping that
    fully replaces compute_layout's output for the listed refdes. Lets
    the multi-try iterator ``build_best_canvas_from_plan`` reuse this
    entry point with alternative placements.

    ``placement_hints``: optional ``{refdes: {"x": int, "y": int,
    "rotation": int}}`` partial overrides applied AFTER compute_layout
    runs. Used by the agent-in-loop refinement workflow: the agent
    reads the SVG, decides a few specific refdes should be anchored
    somewhere different, and passes the deltas without having to
    construct full PlacedPart objects. Non-hinted refdes still get the
    algorithmic placement.

    ``strict_shorts``: when True (default), routing shorts produce
    hard failures (``result.ok = False``) so the canvas never reaches
    Altium emit. When False, shorts are reported as WARNINGS instead
    of failures -- used by pairwise vote generation so bad layouts
    can be shown to the user (so they can vote them down) rather than
    hidden. Emit-to-Altium paths always use strict=True.
    """
    result = PipelineResult()

    # 1. Extract symbols. Skip needs_creation parts up front -- they
    # can't be placed and the planner is expected to resolve them.
    refs: list[tuple[str, str]] = []
    placeable_refdes: set[str] = set()
    for part in plan.parts:
        if part.status == PartStatus.NEEDS_CREATION:
            result.notes.append(PipelineNote(
                severity="warning",
                text=(
                    f"skipping {part.refdes}: status=needs_creation "
                    f"(lib_ref={part.lib_ref!r})"
                ),
                refdes=part.refdes,
            ))
            continue
        if not part.lib_path:
            result.failures.append(PipelineNote(
                severity="error",
                text=(
                    f"part {part.refdes} has status=existing but no "
                    f"lib_path; cannot extract symbol"
                ),
            ))
            continue
        refs.append((part.lib_path, part.lib_ref))
        placeable_refdes.add(part.refdes)
    symbols = extractor.extract_many(refs)
    # Surface symbol-extraction failures up front so the user sees them
    # without scanning the canvas.
    missing: set[tuple[str, str]] = set(refs) - set(symbols.keys())
    for lib_path, lib_ref in missing:
        result.failures.append(PipelineNote(
            severity="error",
            text=(
                f"symbol extraction failed: lib_ref={lib_ref!r} "
                f"lib_path={lib_path!r}. The .SchLib must be openable "
                f"in Altium and contain a component with that name."
            ),
        ))
    if result.failures:
        return result

    # 2. Placement. Two-way pick:
    #    a) layout_overrides supplied -> use those (multi-try iterator path).
    #    b) Otherwise run _placement_pass (composer + Sugiyama fallback).
    if layout_overrides:
        placements = list(layout_overrides.values())
    else:
        placements = _placement_pass(plan, result)
    if placement_hints:
        # First application: give the post-passes (priors / shove /
        # resnap / ordering / recenter) the hinted geometry to work
        # around. They may move hinted parts; hints are RE-ASSERTED
        # after 2d because a hint is explicit caller intent and must be
        # final (measured: priors re-anchored hinted decaps to their
        # role-pair offsets, scattering a hand-specified column layout).
        placements = _apply_placement_hints(placements, placement_hints, result)

    # 2b-2d run only on fresh placements. A polish rebuild feeds back the
    # WINNER's final instance positions -- priors / shove / resnap /
    # ordering / recenter already shaped them once, and running the stack
    # again on its own output cascades (double-applied nudges, re-shoved
    # clusters) into a layout that no longer matches what was scored.
    if polish:
        priors = None
    else:
        # 2b. Apply learned placement priors as a post-Sugiyama bias.
        # Priors live in placement_priors.json (shipped with the package
        # or supplied via EDA_AGENT_PRIORS). When no priors exist (fresh
        # install with no edits yet), this is a no-op.
        priors = load_priors()
    if priors:
        placements = apply_placement_priors(placements, plan, priors)
        result.notes.append(PipelineNote(
            severity="info",
            text=f"applied placement priors ({len(priors)} role pairs)",
        ))

    if not polish:
        # 2c. Final overlap repair. Both the motif composer and the priors
        # layer overlay positions without consulting each other or the
        # initial shove pass that compute_layout ran. The result can have
        # bbox overlaps (e.g., two passives that priors moved to the same
        # x column with rotation that puts their bodies on the same line).
        # Run the audit-aware shove one more time so the wiring stage sees
        # a clean placement.
        placements, residual_overlaps = _hard_shove_pass(plan, placements)
        if residual_overlaps:
            result.notes.append(PipelineNote(
                severity="warning",
                text=(
                    f"post-priors shove left {residual_overlaps} residual "
                    f"overlap(s); wires may pass through component bodies"
                ),
            ))

        # 2c'. Re-tighten crystal oscillator clusters. The shove sizes
        # parts by pin count, so it reads a crystal's two small load caps
        # (400 mils off the crystal) as overlapping and scatters them --
        # undoing the symmetric prior. Re-snap them to the crystal's
        # post-shove position so XIN/XOUT stay short.
        placements = resnap_crystal_clusters(plan, placements)

        # 2c''. Same repair for every OTHER self-contained motif (pi
        # filter, diode bridge, voltage divider, RC filters): the shove
        # scatters those tight clusters too, but only crystals had a
        # resnap. Restore each motif's canonical shape around its
        # post-shove centroid.
        placements = resnap_motif_clusters(plan, placements)

        # 2c'''. Column potential-ordering. Within each vertical stack of
        # 2-pin rail parts, potential must descend top to bottom (power at
        # the top, ground at the bottom); an inverted pair forces the
        # router into a wrap-around loop because the connected pins face
        # away from each other. Pure permutation of existing y-slots (see
        # order_rail_columns), so it cannot create overlaps or grow the
        # sheet.
        placements = order_rail_columns(plan, placements)

        # 2d. Sheet-edge keep-out. Recenter the placement so every part
        # plus a port-glyph margin fits within the sheet rectangle.
        # Without this, parts placed near the sheet edge by Sugiyama push
        # the downstream power-port glyph past the page boundary (see
        # clamp in _emit_port_cluster, which is the LAST defence -- this
        # is the primary one).
        placements = _recenter_within_sheet(placements, plan)

        # 2e. Re-assert placement hints. The post-passes above may have
        # moved hinted parts (priors re-anchor by role pair, shove
        # displaces, recenter translates everything); a hint is explicit
        # caller intent, so it wins over every automatic adjustment. The
        # shorts detector downstream still guards against a hint that
        # would produce a silent short.
        if placement_hints:
            placements = _apply_placement_hints(
                placements, placement_hints, result)

    # 3. Canvas construction. Sheets come from the plan; instances are
    # PlacedPart + SymbolModel pairs.
    canvas = result.canvas
    for sheet in plan.sheets:
        canvas.add_sheet(Sheet(
            name=sheet.name,
            title=sheet.title or "",
            size=sheet.size,
        ))
    part_by_refdes = {p.refdes: p for p in plan.parts}
    for placement in placements:
        part = part_by_refdes.get(placement.refdes)
        if part is None:
            continue  # layout produced a refdes that isn't in the plan?
        if part.status == PartStatus.NEEDS_CREATION:
            continue
        symbol = symbols.get((part.lib_path or "", part.lib_ref))
        if symbol is None:
            # This shouldn't happen if we already short-circuited on
            # missing extraction above, but stay defensive.
            result.failures.append(PipelineNote(
                severity="error",
                text=(
                    f"symbol unavailable at canvas-build time for "
                    f"{placement.refdes}; lib_ref={part.lib_ref!r}"
                ),
            ))
            continue
        # PlacedPart(x, y) is the body CENTRE (the frame every placement
        # pass reasons in); the canvas instance stores the symbol ORIGIN.
        # Convert here, then grid-align by the PINS, not the origin: a
        # symbol whose local pin coordinates sit off the 100-mil grid
        # (odd 50s happen in real libraries) would get off-grid pins from
        # an origin snap, and off-grid pins do not bond to wires in
        # Altium. Aligning the first pin aligns every pin that shares
        # the symbol's own 100-mil pin grid, which is the best any
        # placement can do.
        _flip = bool(getattr(placement, "flipped", False))
        _offx, _offy = _center_offset(symbol, placement.rotation, _flip)
        _inst = SymbolInstance(
            refdes=placement.refdes,
            symbol=symbol,
            x=int(round(placement.x_mils - _offx)),
            y=int(round(placement.y_mils - _offy)),
            rotation=placement.rotation,
            sheet=placement.sheet,
            flipped=_flip,
            value=(part.value or ""),
        )
        _snap_instance_pins_to_grid(_inst)
        canvas.add_instance(_inst)
    result.placement_count = len(canvas.instances)

    # 3a'. Pin-polarity repair with REAL symbol geometry. The upstream
    # rotation pass (_apply_rotations) only picks horizontal-vs-vertical;
    # it never decides which pin ends up on top, so about half of all
    # vertical rail passives land with the ground pin pointing UP. Now
    # that instances exist we know each pin's true world position, so
    # flip any 2-pin part whose polarity breaks the convention every
    # professional sheet follows: ground pin down, power pin up, signal
    # pin toward its partner. Must run BEFORE wiring (flips move pins)
    # and ONLY on the selected winner (see the ``polish`` docstring).
    if polish:
        _repair_pin_polarity(canvas, plan)

    # 3b. Parameter stamps. Bind Value / Manufacturer / MPN / Footprint
    # (and Datasheet when the part carries datasheet_url) so the emitter
    # can stamp them on each placed instance in one bulk call. Same
    # resolution order as the legacy executor: Part fields > BomLine fallback.
    bom_lookup = _bom_lookup(plan)
    for part in plan.parts:
        if part.status == PartStatus.NEEDS_CREATION:
            continue
        stamps = dict(_part_parameters(part, bom_lookup))
        if part.datasheet_url:
            stamps["Datasheet"] = part.datasheet_url
        if stamps:
            result.parameter_stamps[part.refdes] = stamps

    # 4. Wiring per sheet.
    refdes_to_sheet = {p.refdes: p.sheet for p in plan.parts}
    refdes_to_zone = {p.refdes: p.zone for p in plan.parts}

    nets_by_sheet: dict[str, list[Net]] = {}
    for net in plan.nets:
        sheets_touched: set[str] = set()
        for pin_ref in net.pins:
            s = refdes_to_sheet.get(pin_ref.refdes)
            if s:
                sheets_touched.add(s)
        for s in sheets_touched:
            nets_by_sheet.setdefault(s, []).append(net)

    for sheet_obj in canvas.sheets:
        nets = nets_by_sheet.get(sheet_obj.name, [])
        if not nets:
            continue
        _wire_sheet(
            canvas=canvas,
            sheet_name=sheet_obj.name,
            nets=nets,
            placeable_refdes=placeable_refdes,
            refdes_to_sheet=refdes_to_sheet,
            refdes_to_zone=refdes_to_zone,
            result=result,
            plan=plan,
            port_hints=port_hints or {},
        )

    # 4b. Bus drawing. Redraw any detected wide inter-IC bus (>= 4 nets) as a
    # bus glyph -- a thick bus line + 45-degree entries + per-pin labels --
    # instead of N per-pin labels. Gated to NEVER add crossings and to fall
    # back to the per-pin form when a clean bus can't be drawn, so it is a
    # no-op on designs without a bus and never a regression.
    from eda_agent.design.buses import apply_bus_drawing
    apply_bus_drawing(canvas, plan)

    # 4c. Collision-free designator/value text. Libraries stamp text at a
    # fixed offset from the symbol origin, so defaults collide with wires
    # / neighbours on any dense sheet. Pure geometry over the finished
    # canvas (never moves symbols or wires, invisible to the scorer), so
    # it runs on every build, not just the polish rebuild.
    from eda_agent.design.text_placement import place_instance_text
    place_instance_text(canvas)

    result.wire_count = len(canvas.wires)
    result.label_count = len(canvas.labels)
    result.power_port_count = len(canvas.power_ports)
    result.junction_count = len(canvas.junctions)

    # 5. Canvas validation. Catch the class of bug where a plan net
    # silently failed to produce any wire/label/port on the canvas
    # (would emit to Altium as a no-op, then surface much later as an
    # unconnected-pin ERC violation). Cheap last-mile check.
    _validate_canvas_against_plan(plan, canvas, result)
    # 6. Routing-shorts detector. Strict: if a wire on net N passes
    # through or terminates on a pin that's NOT on net N, Altium will
    # auto-merge them into a single net at compile time. ERC won't
    # catch this (the merged net has both pins, which often looks
    # "fully connected") -- the design just silently does the wrong
    # thing. This pass turns those into hard failures BEFORE the emit
    # so the bad layout never reaches Altium.
    #
    # In strict_shorts=False mode (pairwise voting), demote shorts
    # from failures to warning notes: the caller WANTS to see bad
    # layouts so the user can vote against them. The downstream emit
    # path always re-checks with strict=True.
    if strict_shorts:
        _detect_routing_shorts(plan, canvas, result)
    else:
        _detect_routing_shorts_nonfatal(plan, canvas, result)
    return result


def _selection_rank_cost(res: "PipelineResult", plan: DesignPlan) -> float:
    """Convention terms the wirelength scorer cannot see, added to a
    candidate's score for best-of ranking.

    - forced label demotions (shorts fallback): 150 each -- floating
      labels where a wire belongs are the strongest amateur signal;
    - rail polarity inversions: 100 each -- ground pin up / power pin
      down forces a wrap or a post-hoc flip the polish guard may reject;
    - span-labelled mils at the scorer's wire rate (0.01/mil): a net
      deliberately drawn as labels because of its span still costs what
      wiring it would have -- otherwise sprawl PROFITS from the
      demotion (long nets become free labels and the wirelength term
      drops), and the sweep starts preferring scattered placements.

    Without these terms selection is convention roulette: whichever
    variant shaves wire-mils wins, however unprofessionally it draws.
    """
    return (
        150.0 * res.forced_label_count
        + 100.0 * _count_polarity_inversions(res.canvas, plan)
        + 120.0 * _count_pin_side_violations(res.canvas, plan)
        + 0.01 * res.span_labelled_mils
    )


def _count_pin_side_violations(
    canvas: SchematicCanvas, plan: DesignPlan
) -> int:
    """Satellites placed on the wrong side of the IC they wire to.

    A discrete that wires to an IC's LEFT-side pin belongs left of that
    IC; one that wires to a RIGHT-side pin belongs right. Getting this
    wrong sends every one of its wires around the IC body, which reads
    as a tangle no matter how short the total wirelength is, and the
    wirelength scorer is nearly indifferent between the two (measured on
    a 555 board: the side-wrong layout scored 2% BETTER than the best
    side-correct one). Counting the violations lets selection ranking
    see the convention the score cannot.
    """
    pin_counts: dict[str, int] = {}
    for net in plan.nets:
        for pr in net.pins:
            pin_counts[pr.refdes] = pin_counts.get(pr.refdes, 0) + 1
    inst_by_refdes = {i.refdes: i for i in canvas.instances}

    def _center_x(refdes: str):
        inst = inst_by_refdes.get(refdes)
        if inst is None:
            return None
        bb = inst.world_bbox()
        return (bb.x_min + bb.x_max) / 2.0

    violations = 0
    for net in plan.nets:
        if _is_power_net(net) or _is_ground_net(net):
            continue  # rails reach everything; side carries no meaning
        ics = [pr for pr in net.pins if pin_counts.get(pr.refdes, 0) >= 4]
        sats = [pr for pr in net.pins if pin_counts.get(pr.refdes, 0) <= 3]
        if len(ics) != 1 or not sats:
            continue
        ic_ref = ics[0]
        ic_cx = _center_x(ic_ref.refdes)
        ep = canvas.pin_world(ic_ref.refdes, ic_ref.pin)
        if ic_cx is None or ep is None:
            continue
        pin_side = (ep.x > ic_cx) - (ep.x < ic_cx)
        if pin_side == 0:
            continue
        for sat in sats:
            sat_cx = _center_x(sat.refdes)
            if sat_cx is None:
                continue
            sat_side = (sat_cx > ic_cx) - (sat_cx < ic_cx)
            if sat_side != 0 and sat_side != pin_side:
                violations += 1
    return violations


def _count_polarity_inversions(
    canvas: SchematicCanvas, plan: DesignPlan
) -> int:
    """Count 2-pin parts whose rail polarity breaks convention.

    Same detection as _repair_pin_polarity's rail rules (ground pin not
    at the bottom / power pin not at the top of a vertical part) but
    read-only. Used by best-of candidate ranking: the wirelength scorer
    cannot see polarity, so without this term the selection is
    convention-roulette across variants.
    """
    conn_roles = _INPUT_CONN_ROLES_PIPE | _OUTPUT_CONN_ROLES_PIPE
    role_by_refdes = {
        p.refdes: (p.role or "").strip().lower() for p in plan.parts
    }
    net_of_pin: dict[tuple[str, str], Net] = {}
    pins_of: dict[str, list[str]] = {}
    for net in plan.nets:
        for pr in net.pins:
            net_of_pin[(pr.refdes, pr.pin)] = net
            pins_of.setdefault(pr.refdes, []).append(pr.pin)

    inversions = 0
    for inst in canvas.instances:
        pin_ids = pins_of.get(inst.refdes, [])
        if len(pin_ids) != 2:
            continue
        if role_by_refdes.get(inst.refdes, "") in conn_roles:
            continue
        ep_a = canvas.pin_world(inst.refdes, pin_ids[0])
        ep_b = canvas.pin_world(inst.refdes, pin_ids[1])
        if ep_a is None or ep_b is None:
            continue
        net_a = net_of_pin[(inst.refdes, pin_ids[0])]
        net_b = net_of_pin[(inst.refdes, pin_ids[1])]
        if net_a.name == net_b.name:
            continue
        if abs(ep_a.y - ep_b.y) <= abs(ep_a.x - ep_b.x):
            continue  # horizontal part: rail polarity rules don't apply
        a_gnd, b_gnd = _is_ground_net(net_a), _is_ground_net(net_b)
        a_pwr, b_pwr = _is_power_net(net_a), _is_power_net(net_b)
        if a_gnd != b_gnd:
            gnd_ep, other_ep = (ep_a, ep_b) if a_gnd else (ep_b, ep_a)
            if gnd_ep.y > other_ep.y:
                inversions += 1
        elif a_pwr != b_pwr:
            pwr_ep, other_ep = (ep_a, ep_b) if a_pwr else (ep_b, ep_a)
            if pwr_ep.y < other_ep.y:
                inversions += 1
    return inversions


def _repair_pin_polarity(canvas: SchematicCanvas, plan: DesignPlan) -> int:
    """Flip 2-pin parts whose pin polarity breaks drawing convention.

    Conventions enforced, in priority order, using REAL world pin
    positions (so library pin-numbering quirks cannot fool it):

      * vertical part with exactly one pin on a ground net: that pin
        must be the BOTTOM pin (ground symbols hang below);
      * vertical part with exactly one pin on a power net: that pin
        must be the TOP pin (rail bars sit above);
      * otherwise (signal-signal, either axis): each pin should point
        toward its own net's partner pins -- flip when the top (or
        right) pin's partners sit lower (or lefter) than the bottom
        (left) pin's partners by a clear margin.

    A flip is rotation+180: for a 2-pin symbol this swaps the two pin
    positions in place while keeping the body footprint, so it cannot
    create overlaps and runs safely between placement and wiring.
    Connector-role parts keep their deliberate orientation. Returns the
    number of instances flipped (for tests / notes).
    """
    conn_roles = _INPUT_CONN_ROLES_PIPE | _OUTPUT_CONN_ROLES_PIPE
    role_by_refdes = {
        p.refdes: (p.role or "").strip().lower() for p in plan.parts
    }

    # (refdes, pin_id) -> Net, and per-part pin list.
    net_of_pin: dict[tuple[str, str], Net] = {}
    pins_of: dict[str, list[str]] = {}
    for net in plan.nets:
        for pr in net.pins:
            net_of_pin[(pr.refdes, pr.pin)] = net
            pins_of.setdefault(pr.refdes, []).append(pr.pin)

    def partner_coord(net: Net, refdes: str, axis: int) -> Optional[float]:
        vals = []
        for pr in net.pins:
            if pr.refdes == refdes:
                continue
            ep = canvas.pin_world(pr.refdes, pr.pin)
            if ep is not None:
                vals.append(ep.x if axis == 0 else ep.y)
        if not vals:
            return None
        return sum(vals) / len(vals)

    flipped = 0
    for inst in sorted(canvas.instances, key=lambda i: i.refdes):
        pin_ids = pins_of.get(inst.refdes, [])
        if len(pin_ids) != 2:
            continue
        if role_by_refdes.get(inst.refdes, "") in conn_roles:
            continue
        ep_a = canvas.pin_world(inst.refdes, pin_ids[0])
        ep_b = canvas.pin_world(inst.refdes, pin_ids[1])
        if ep_a is None or ep_b is None:
            continue
        net_a = net_of_pin[(inst.refdes, pin_ids[0])]
        net_b = net_of_pin[(inst.refdes, pin_ids[1])]
        if net_a.name == net_b.name:
            continue
        vertical = abs(ep_a.y - ep_b.y) > abs(ep_a.x - ep_b.x)

        do_flip = False
        a_gnd = _is_ground_net(net_a)
        b_gnd = _is_ground_net(net_b)
        a_pwr = _is_power_net(net_a)
        b_pwr = _is_power_net(net_b)
        if vertical and (a_gnd != b_gnd):
            gnd_ep, other_ep = (ep_a, ep_b) if a_gnd else (ep_b, ep_a)
            do_flip = gnd_ep.y > other_ep.y
        elif vertical and (a_pwr != b_pwr):
            pwr_ep, other_ep = (ep_a, ep_b) if a_pwr else (ep_b, ep_a)
            do_flip = pwr_ep.y < other_ep.y
        else:
            axis = 1 if vertical else 0
            ca = partner_coord(net_a, inst.refdes, axis)
            cb = partner_coord(net_b, inst.refdes, axis)
            if ca is not None and cb is not None:
                my_a = ep_a.y if vertical else ep_a.x
                my_b = ep_b.y if vertical else ep_b.x
                # Clear-margin guard: don't flip on near-ties.
                if my_a > my_b and ca < cb - 50:
                    do_flip = True
                elif my_b > my_a and cb < ca - 50:
                    do_flip = True

        if do_flip:
            # Rotate 180 about the BODY CENTRE, not the symbol origin: a
            # corner-anchored symbol rotated about its origin swings the
            # whole body to the other side (origin-centred synthetic
            # symbols hid this). Keep the centre fixed by moving the
            # origin to compensate, re-snapped to the grid.
            old_offx, old_offy = _center_offset(
                inst.symbol, inst.rotation, inst.flipped)
            new_rot = (inst.rotation + 180) % 360
            new_offx, new_offy = _center_offset(
                inst.symbol, new_rot, inst.flipped)
            cx = inst.x + old_offx
            cy = inst.y + old_offy
            inst.rotation = new_rot
            inst.x = int(round(cx - new_offx))
            inst.y = int(round(cy - new_offy))
            # Re-align by pins, not origin (see _snap_instance_pins_to_grid).
            _snap_instance_pins_to_grid(inst)
            flipped += 1
    return flipped


_INPUT_CONN_ROLES_PIPE = frozenset(
    {"input_conn", "vin_conn", "power_in", "input"})
_OUTPUT_CONN_ROLES_PIPE = frozenset(
    {"output_conn", "vout_conn", "power_out", "output"})


def _recenter_within_sheet(
    placements: list,
    plan: DesignPlan,
) -> list:
    """Shift placements so the part bbox sits inside the sheet rect
    with margin for power-port glyphs.

    Each sheet in the plan carries its own size (A4, A3, B, ...) -- the
    Sheet object computes width_mils / height_mils from that string. We
    use those dimensions for the clamp; never assume a default page.

    Behaviour: if the part bbox already fits with margin, no change.
    If a side breaches the margin, shift the whole group toward sheet
    centre by exactly the breach amount (no zoom, no per-part nudging).
    If the bbox is wider/taller than the sheet's interior, leave the
    placements alone -- bigger problem than a recenter can fix.
    """
    if not placements:
        return placements

    # Build a lookup: refdes -> sheet name from the plan.
    sheet_by_refdes = {p.refdes: p.sheet for p in plan.parts}
    # Sheet dimensions by name. Plan.sheets is a list of Sheet dicts;
    # construct a temporary canvas Sheet to get width_mils / height_mils.
    from eda_agent.design.canvas import Sheet as CanvasSheet
    sheet_dims: dict[str, tuple[int, int]] = {}
    for s in plan.sheets:
        cs = CanvasSheet(name=s.name, title=s.title or "", size=s.size)
        sheet_dims[s.name] = (cs.width_mils, cs.height_mils)

    # Margin: enough headroom for a power-port glyph + label (~400)
    # plus a 200-mil buffer so the glyph isn't crammed against the edge.
    margin = 600

    # Group placements by sheet and shift each group independently.
    by_sheet: dict[str, list] = {}
    for p in placements:
        by_sheet.setdefault(sheet_by_refdes.get(p.refdes, p.sheet), []).append(p)

    from eda_agent.design.layout import PlacedPart
    shifted_by_refdes: dict[str, PlacedPart] = {}
    for sheet_name, group in by_sheet.items():
        if not group or sheet_name not in sheet_dims:
            for p in group:
                shifted_by_refdes[p.refdes] = p
            continue
        sw, sh = sheet_dims[sheet_name]
        xs = [p.x_mils for p in group]
        ys = [p.y_mils for p in group]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        # If the group already fits inside (margin, sheet - margin), no shift.
        dx = 0
        dy = 0
        if x_min < margin:
            dx = margin - x_min
        elif x_max > sw - margin:
            dx = (sw - margin) - x_max
        if y_min < margin:
            dy = margin - y_min
        elif y_max > sh - margin:
            dy = (sh - margin) - y_max
        # Snap shift to 100-mil grid so placements stay grid-aligned.
        dx = (dx // 100) * 100
        dy = (dy // 100) * 100
        for p in group:
            if dx == 0 and dy == 0:
                shifted_by_refdes[p.refdes] = p
            else:
                shifted_by_refdes[p.refdes] = PlacedPart(
                    refdes=p.refdes, sheet=p.sheet,
                    x_mils=p.x_mils + dx, y_mils=p.y_mils + dy,
                    rotation=p.rotation,
                )

    # Preserve input order.
    return [shifted_by_refdes[p.refdes] for p in placements]


def _apply_placement_hints(
    placements: list,
    hints: dict[str, dict[str, int]],
    result: PipelineResult,
) -> list:
    """Override (x, y, rotation) on hinted refdes, leave others alone.

    Returns a new list; non-hinted PlacedParts pass through unchanged.
    Unknown hint keys (refdes not in placements) get a warning note --
    the agent probably typo'd a refdes. Partial hints (e.g. rotation
    omitted) preserve the placement's existing value for that field.
    """
    from eda_agent.design.layout import PlacedPart
    placement_by_refdes = {p.refdes: p for p in placements}
    known_refdes = set(placement_by_refdes.keys())
    out: list = []
    for placement in placements:
        hint = hints.get(placement.refdes)
        if hint is None:
            out.append(placement)
            continue
        new_x = int(hint.get("x", placement.x_mils))
        new_y = int(hint.get("y", placement.y_mils))
        new_rot = int(hint.get("rotation", placement.rotation)) % 360
        # Flip is carried separately as `flipped: bool`. PlacedPart
        # doesn't have a field yet; we encode it by extending PlacedPart
        # below if the hint asks for a flip. Default unchanged.
        flipped = bool(hint.get("flipped", getattr(placement, "flipped", False)))
        # Snap to 100-mil grid so anchored positions don't accidentally
        # land off-grid (which Altium would auto-snap anyway, but worse:
        # the wires routed by the pipeline expect grid alignment).
        new_x = (new_x // 100) * 100
        new_y = (new_y // 100) * 100
        kwargs = dict(
            refdes=placement.refdes, sheet=placement.sheet,
            x_mils=new_x, y_mils=new_y, rotation=new_rot,
        )
        # PlacedPart may or may not yet have a `flipped` field. Pass it
        # only when supported so we don't break older PlacedPart shape.
        try:
            new_placement = PlacedPart(**kwargs, flipped=flipped)
        except TypeError:
            new_placement = PlacedPart(**kwargs)
            new_placement.__dict__["flipped"] = flipped
        out.append(new_placement)
    # Flag unknown refdes hints so the agent sees them.
    for refdes in hints.keys():
        if refdes not in known_refdes:
            result.notes.append(PipelineNote(
                severity="warning",
                text=(
                    f"placement_hint references unknown refdes {refdes!r} "
                    f"(known: {sorted(known_refdes)}). Hint ignored."
                ),
            ))
    if hints:
        result.notes.append(PipelineNote(
            severity="info",
            text=f"applied {len(hints)} placement hint(s)",
        ))
    return out


def build_best_canvas_from_plan(
    plan: DesignPlan,
    extractor: SymbolExtractor,
    *,
    n_tries: int = 5,
    placement_hints: Optional[dict[str, dict[str, int]]] = None,
    port_hints: Optional[dict[str, dict[str, int]]] = None,
    strict_shorts: bool = True,
) -> PipelineResult:
    """Multi-try plan -> canvas; return the lowest-scoring variant.

    Closes the SVG-iteration loop the renderer was always meant to
    serve. Strategy:

    1. Run ``build_canvas_from_plan`` once to get the base placement.
    2. Generate ``n_tries`` placement variants by rescaling the base
       placements to different bbox aspect ratios. Each rescale
       preserves relative ordering but reshapes the layout's overall
       footprint (e.g. tall-thin -> square -> wide-short).
    3. Re-run ``build_canvas_from_plan`` with each variant as
       ``layout_overrides``, score every resulting canvas, return
       the lowest-score one.

    The variants are intentionally narrow for now (aspect rescaling
    only); the real win is exercising the score-pick-best plumbing.
    Future expansion: vary force-directed seeds, swap Sugiyama
    parameters, or accept agent-supplied placement anchors as a
    second-tier escalation when no variant lands below a quality
    threshold.

    On failure (base canvas couldn't be built, all variants failed),
    returns the base result with its failures intact.
    """
    base = build_canvas_from_plan(
        plan, extractor, placement_hints=placement_hints,
        port_hints=port_hints, strict_shorts=strict_shorts,
    )
    base_label = "base"

    # Force-directed alternative placement. The base used Sugiyama (the plan
    # has an anchor role), which excludes power/ground from layering -- so on a
    # board whose signal graph is split by a power-only bridge (a regulator
    # that connects ONLY through rails) it leaves parts floating and sprawls.
    # FD's spring graph uses ALL nets, so it places the whole power tree; it
    # wins on those boards and loses on clean signal chains. It is an
    # INDEPENDENT placement (does not reuse base positions), so it can even
    # rescue a base that failed to route -- evaluate it before the early-out.
    def _convention_cost(res: PipelineResult) -> float:
        return _selection_rank_cost(res, plan)

    def _cand_key(res: PipelineResult) -> tuple[int, float]:
        if not res.canvas.instances:
            return (2, float("inf"))
        total = score_canvas(res.canvas, plan).total
        return (0 if res.ok else 1, total + _convention_cost(res))

    try:
        from eda_agent.design.layout import compute_layout as _compute_layout
        # Pin-aware force-directed candidate. FD's spring graph uses ALL nets
        # (so it places power-bridged boards Sugiyama leaves floating), and with
        # the IC pin offsets each discrete is pulled toward the specific pin it
        # wires to -- so the output stage settles by OUT, the timing network by
        # DISCH/THRES, etc. Run for every board (not just anchored ones) and
        # score it; it wins where pin-side placement or power-tree handling
        # helps, and loses to Sugiyama on clean signal chains. ic_pin_offsets
        # empty (no ICs) reproduces the old centroid FD exactly.
        ic_off = _ic_pin_offsets(plan, extractor)
        # FD is chaotic in the pin-attractor stiffness -- one value lands a
        # clean side-grouped layout while a neighbouring value sprawls. Rather
        # than trust a single tuned constant (overfitting to one board), SWEEP a
        # few strengths, build + score each, and keep the lowest. With no ICs
        # the sweep collapses to one centroid-only FD run (old behaviour).
        # FD's pin-attractor stiffness has a chaotic, multi-modal score
        # landscape (good minima sit next to sprawled ones, and the best K
        # varies per board), so a few hand-picked values miss the optimum.
        # Sweep it DENSELY (module constant ``_FD_K_SWEEP``) and let the scorer
        # pick per board -- offline, ~50 ms per eval. No ICs -> one centroid FD
        # run (old behaviour). The test conftest patches the sweep down for
        # speed; the pin-side regression test restores the full density.
        ks: tuple = (None,) if not ic_off else _FD_K_SWEEP
        for k in ks:
            fd_placed = _compute_layout(
                plan, engine="force_directed", ic_pin_offsets=ic_off,
                pin_attract_k=k)
            fd_cand = build_canvas_from_plan(
                plan, extractor,
                layout_overrides={p.refdes: p for p in fd_placed},
                placement_hints=placement_hints, port_hints=port_hints,
                strict_shorts=strict_shorts,
            )
            # Prefer an OK candidate, then the lower score.
            if _cand_key(fd_cand) < _cand_key(base):
                base, base_label = fd_cand, (
                    f"pin_aware_fd(k={k})" if ic_off else "force_directed")
    except Exception as fd_exc:
        # FD alternative is best-effort; never block the base result -- but
        # say so, or a persistently-broken FD path silently degrades every
        # layout to the base engine.
        base.notes.append(PipelineNote(
            severity="warning",
            text=(
                "force-directed layout alternative failed and was skipped: "
                f"{type(fd_exc).__name__}: {fd_exc}"),
        ))
        ic_off = {}

    # Pin-side-aware Sugiyama candidate. The base Sugiyama layers by hop
    # distance only, so a timing network can land in the column RIGHT of
    # its IC while wiring to LEFT-side pins (every wire loops around the
    # body). With the symbol pin offsets, small parts move to the side of
    # the IC their pins sit on; scored like every other variant, so it
    # only wins when the geometry actually improves.
    try:
        if ic_off:
            from eda_agent.design.layout import compute_layout as _cl2
            ps_placed = _cl2(plan, engine="sugiyama", ic_pin_offsets=ic_off)
            ps_cand = build_canvas_from_plan(
                plan, extractor,
                layout_overrides={p.refdes: p for p in ps_placed},
                placement_hints=placement_hints, port_hints=port_hints,
                strict_shorts=strict_shorts,
            )
            if _cand_key(ps_cand) < _cand_key(base):
                base, base_label = ps_cand, "pin_side_sugiyama"
    except Exception as ps_exc:
        base.notes.append(PipelineNote(
            severity="warning",
            text=(
                "pin-side sugiyama alternative failed and was skipped: "
                f"{type(ps_exc).__name__}: {ps_exc}"),
        ))

    # Only an empty canvas is unrecoverable. A base that FAILED strict
    # checks must still go through the aspect variants below: rescaling
    # moves every part, so a variant can be clean where the base was not.
    # Returning here made the engine give up on a board whose very next
    # variant emitted cleanly.
    if not base.canvas.instances:
        return base

    base_score = score_canvas(base.canvas, plan)
    best_score: LayoutScore = base_score
    best_result: PipelineResult = base
    best_label = base_label
    # Rank as (failed?, cost) so any clean variant beats a failing one
    # regardless of score.
    best_rank = (
        0 if base.ok else 1,
        base_score.total + _convention_cost(base),
    )
    base.notes.append(PipelineNote(
        severity="info",
        text=(
            f"layout score baseline: total={base_score.total:.1f} "
            f"(crossings={base_score.wire_crossings}, "
            f"through_body={base_score.wires_through_bodies}, "
            f"overlaps={base_score.body_overlaps}, "
            f"aspect={base_score.aspect_ratio_penalty:.2f}, "
            f"length={base_score.total_wire_length}, "
            f"ports={base_score.port_count})"
        ),
    ))

    # Aspect-rescaling variants. Tall layouts (aspect >> 1) often
    # compress better at square 1.0 or modest 1.33; wide layouts at
    # 0.75 or 0.66. Each variant is scored independently and the
    # lowest-score one wins.
    target_aspects = [1.0, 1.33, 0.75, 1.5, 0.66][: max(0, n_tries - 1)]
    for aspect in target_aspects:
        variant_placements = _rescale_placements(
            [_canvas_instance_to_placement(i) for i in base.canvas.instances],
            target_aspect=aspect,
        )
        overrides = {p.refdes: p for p in variant_placements}
        variant = build_canvas_from_plan(
            plan, extractor,
            layout_overrides=overrides,
            placement_hints=placement_hints,
            port_hints=port_hints,
            strict_shorts=strict_shorts,
        )
        if not variant.canvas.instances:
            continue
        variant_score = score_canvas(variant.canvas, plan)
        variant.notes.append(PipelineNote(
            severity="info",
            text=(
                f"variant aspect={aspect}: score={variant_score.total:.1f}"
                f"{'' if variant.ok else ' (failed strict checks)'}"
            ),
        ))
        variant_rank = (
            0 if variant.ok else 1,
            variant_score.total + _convention_cost(variant),
        )
        if variant_rank < best_rank:
            best_score = variant_score
            best_result = variant
            best_label = f"aspect={aspect}"
            best_rank = variant_rank

    # NOTE: the deterministic neat-layout engine (schematic_layout.py) was
    # trialled here as an extra positions-only variant, but it consistently
    # lost to the Sugiyama-based placer that the canvas already uses: feeding
    # only its centres into the canvas discards its crossing-minimal routing,
    # and the canvas re-routes them with more crossings. It stays available as
    # a standalone preview (design_layout_schematic) and via
    # `_neat_engine_overrides`; it is not run in this hot path.
    n_variants = 1 + len(target_aspects)
    best_result.notes.append(PipelineNote(
        severity="info",
        text=(
            f"selected layout: {best_label} score={best_score.total:.1f} "
            f"out of {n_variants} variants"
        ),
    ))

    # Convention polish on the WINNER only: rebuild with the exact same
    # placement (layout_overrides) and polish=True so the pin-polarity
    # repair runs, then rewire. Candidates were scored unpolished so the
    # selection stayed honest; the winner now gets the professional
    # conventions (ground pin down, power pin up, signal pin toward its
    # partner). If the polished rebuild fails (e.g. a flip provoked a
    # routing short under strict mode), keep the unpolished winner.
    try:
        polished = build_canvas_from_plan(
            plan, extractor,
            layout_overrides={
                p.refdes: p
                for p in (
                    _canvas_instance_to_placement(i)
                    for i in best_result.canvas.instances
                )
            },
            placement_hints=placement_hints,
            port_hints=port_hints,
            strict_shorts=strict_shorts,
            polish=True,
        )
        if polished.ok and polished.canvas.instances:
            # Acceptance guard: polish exists to add conventions WITHOUT
            # regressing the drawing. A flip can perturb downstream gated
            # stages (e.g. the bus glyph's never-add-crossings gate
            # declining and reverting to per-pin labels), so verify the
            # polished canvas kept what the winner had: no new crossings,
            # no lost bus glyphs, and no runaway score (small increases
            # are fine -- a conventional rail drop may cost a few mils).
            pol_score = score_canvas(polished.canvas, plan)
            if (
                pol_score.wire_crossings <= best_score.wire_crossings
                and len(polished.canvas.buses)
                    >= len(best_result.canvas.buses)
                and pol_score.total <= best_score.total * 1.10 + 50
            ):
                polished.notes.extend(best_result.notes)
                polished.notes.append(PipelineNote(
                    severity="info",
                    text="convention polish applied to selected layout",
                ))
                return polished
            best_result.notes.append(PipelineNote(
                severity="info",
                text=(
                    "convention polish rejected (would regress: "
                    f"crossings {best_score.wire_crossings}->"
                    f"{pol_score.wire_crossings}, buses "
                    f"{len(best_result.canvas.buses)}->"
                    f"{len(polished.canvas.buses)}, score "
                    f"{best_score.total:.0f}->{pol_score.total:.0f})"
                ),
            ))
    except Exception as pol_exc:
        best_result.notes.append(PipelineNote(
            severity="warning",
            text=(
                "convention polish failed and was skipped: "
                f"{type(pol_exc).__name__}: {pol_exc}"),
        ))

    # LAST, on the winner only. Moving a repair glyph off its pin onto a
    # short stub is how the sheet is drawn by hand, but it adds wire, and
    # wire drawn before this point would enter the scored objective and
    # steer placement. Run here and the selection above is bit-identical
    # to what it was without the feature.
    try:
        moved = upgrade_repair_ports_to_stubs(best_result.canvas, plan)
        if moved:
            # Designator/value text was placed against the OLD glyph
            # positions, inside the per-candidate build. Moving a glyph
            # afterwards can drop it on text that was routed around where
            # it used to be, so re-run the placer over the finished
            # canvas. It only moves text (never symbols or wires) and is
            # documented as invisible to the scorer, so this is safe
            # after selection.
            from eda_agent.design.text_placement import place_instance_text
            place_instance_text(best_result.canvas)
            best_result.notes.append(PipelineNote(
                severity="info",
                text=(
                    f"{moved} repair power port(s) moved onto a short stub "
                    f"off their pin; the rest stayed coincident because a "
                    f"stub there would have touched another net"),
            ))
    except Exception as stub_exc:
        best_result.notes.append(PipelineNote(
            severity="warning",
            text=(
                "repair-port stub upgrade failed and was skipped: "
                f"{type(stub_exc).__name__}: {stub_exc}"),
        ))
    return best_result


def _neat_engine_overrides(plan):  # type: ignore[no-untyped-def]
    """Placement overrides from the deterministic neat-layout engine.

    Best-effort: runs ``compute_schematic_layout`` and maps its placed
    symbols to canvas :class:`PlacedPart` overrides. Returns ``None`` on any
    failure so the pipeline degrades to the rescale variants alone.
    """
    try:
        from eda_agent.design.layout import PlacedPart
        from eda_agent.design.schematic_layout import compute_schematic_layout
        layout = compute_schematic_layout(plan)
        if not layout.placed:
            return None
        return {
            r: PlacedPart(refdes=s.refdes, sheet=s.sheet,
                          x_mils=s.x_mils, y_mils=s.y_mils, rotation=s.rotation)
            for r, s in layout.placed.items()
        }
    except Exception:
        return None


def _snap_instance_pins_to_grid(inst, grid: int = 100) -> None:
    """Shift an instance so its PINS sit on the wiring grid.

    Snapping the origin is not enough: a symbol whose local pin
    coordinates are off the 100-mil grid would end up with off-grid
    pins, and an off-grid pin never bonds to a wire in Altium. Use the
    first pin's world position as the alignment reference -- every pin
    that shares the symbol's own pin grid (the sane-library case) lands
    on grid with it. Symbols with internally mixed pin grids cannot be
    fully aligned by translation; the first pin still is.
    """
    eps = list(inst.all_pin_endpoints())
    if not eps:
        inst.x = int(round(inst.x / grid) * grid)
        inst.y = int(round(inst.y / grid) * grid)
        return
    rx = eps[0].x % grid
    ry = eps[0].y % grid
    inst.x += -rx if rx <= grid // 2 else grid - rx
    inst.y += -ry if ry <= grid // 2 else grid - ry


def _center_offset(
    symbol, rotation: int, flipped: bool = False,
) -> tuple[int, int]:
    """World-frame delta from a symbol's ORIGIN to its body CENTER.

    Placement passes (Sugiyama, motif splat, priors, shove, column
    ordering) all reason about a part's position as its body centre.
    That held on the synthetic fixtures because their symbols are
    origin-centred -- but real library symbols anchor at a CORNER
    (measured: a QFN28 bridge IC whose body spans local x 300..1500,
    y 0..-1900), so treating the anchor as the centre put every
    IC-anchored satellite inside the pin field. This helper is the
    single source of the conversion: placements stay centre-frame,
    the canvas instance origin is centre - offset, and instance ->
    placement round-trips add it back.
    """
    bb = symbol.body_bbox
    cx = (bb.x_min + bb.x_max) / 2.0
    cy = (bb.y_min + bb.y_max) / 2.0
    if flipped:
        cx = -cx
    r = rotation % 360
    if r == 90:
        cx, cy = -cy, cx
    elif r == 180:
        cx, cy = -cx, -cy
    elif r == 270:
        cx, cy = cy, -cx
    return (int(round(cx)), int(round(cy)))


def _canvas_instance_to_placement(inst):  # type: ignore[no-untyped-def]
    """Pull a PlacedPart out of a canvas SymbolInstance for variant generation.

    Inverse of the origin conversion at canvas build: PlacedPart carries
    the body CENTRE, the instance carries the symbol origin.
    """
    from eda_agent.design.layout import PlacedPart
    offx, offy = _center_offset(inst.symbol, inst.rotation, inst.flipped)
    return PlacedPart(
        refdes=inst.refdes, sheet=inst.sheet,
        x_mils=inst.x + offx, y_mils=inst.y + offy, rotation=inst.rotation,
    )


def _rescale_placements(placements, target_aspect: float):
    """Rescale placements so the bbox approximates target_aspect = w/h.

    Preserves relative ordering and shape but compresses/stretches one
    axis. Output is snapped to the 100-mil grid. Only shrinks (never
    grows) along each axis so the resulting bbox stays within the sheet.
    """
    if not placements:
        return list(placements)
    xs = [p.x_mils for p in placements]
    ys = [p.y_mils for p in placements]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    w = max(1, x_max - x_min)
    h = max(1, y_max - y_min)
    # Target bbox preserves area; compute new w, h with target aspect.
    import math
    area = w * h
    target_h = max(1, int(math.sqrt(area / target_aspect)))
    target_w = max(1, int(target_h * target_aspect))
    # Cap each dimension so we never grow beyond original (keeps everything
    # on-sheet; if growth is desired, raise the cap).
    target_w = min(target_w, max(w, 1500))
    target_h = min(target_h, max(h, 1500))
    sx = target_w / w
    sy = target_h / h
    out = []
    for p in placements:
        new_x = x_min + int((p.x_mils - x_min) * sx)
        new_y = y_min + int((p.y_mils - y_min) * sy)
        # Snap to 100-mil grid.
        new_x = (new_x // 100) * 100
        new_y = (new_y // 100) * 100
        # Re-import PlacedPart here so the function stays importable
        # in isolation (the module-level layout import is also fine).
        from eda_agent.design.layout import PlacedPart
        out.append(PlacedPart(
            refdes=p.refdes, sheet=p.sheet,
            x_mils=new_x, y_mils=new_y, rotation=p.rotation,
        ))
    return out


def _detect_routing_shorts_nonfatal(
    plan: DesignPlan,
    canvas: SchematicCanvas,
    result: PipelineResult,
) -> None:
    """Same as _detect_routing_shorts but downgrades failures to warnings.

    Used by pairwise vote generation: we want to SHOW bad layouts to
    the user (so they can vote against them) rather than reject them.
    The downstream emit path re-runs the strict version.
    """
    sink = PipelineResult(canvas=result.canvas)
    _detect_routing_shorts(plan, canvas, sink)
    # Move what would be failures into warning notes on the real result.
    for f in sink.failures:
        result.notes.append(PipelineNote(severity="warning", text=f.text))


def _detect_routing_shorts(
    plan: DesignPlan,
    canvas: SchematicCanvas,
    result: PipelineResult,
) -> None:
    """Flag wires that bridge unrelated nets via coincident pins.

    For every wire ``w`` on a named net ``N``, every pin endpoint that
    is NOT on ``N`` is a potential short:

    1. Pin endpoint coincides with one of the wire's two endpoints, OR
    2. Pin endpoint lies strictly between the wire's endpoints on an
       axis-aligned segment.

    When either happens, Altium will auto-merge the wire's net with
    whatever the offending pin's plan-net is. ERC sees one fully-
    connected net and stays silent. Catching the geometric coincidence
    pre-emit is the only reliable signal.

    Per-pin net membership is computed from plan.nets; if a pin isn't
    in any plan net it's treated as un-netted (no shorting possible).
    """
    # Build refdes/pin_id -> plan_net_name lookup.
    pin_to_net: dict[tuple[str, str], str] = {}
    for net in plan.nets:
        for pin_ref in net.pins:
            pin_to_net[(pin_ref.refdes, pin_ref.pin)] = net.name

    # Build (x, y) -> {(refdes, pin_id, net_name)} for every placed
    # instance's pin world coords. Aliased pin IDs (designator vs name)
    # both map to the same coordinate, so we deduplicate by storing the
    # pin's canonical designator only.
    points: dict[tuple[int, int], list[tuple[str, str, str]]] = {}
    for inst in canvas.instances:
        for endpoint in inst.all_pin_endpoints():
            key = (endpoint.x, endpoint.y)
            net_name = pin_to_net.get((inst.refdes, endpoint.pin_id), "")
            points.setdefault(key, []).append(
                (inst.refdes, endpoint.pin_id, net_name)
            )

    shorts: list[tuple[str, str, str, str, int, int]] = []
    # (wire_net, offending_refdes, offending_pin, offending_net, x, y)

    for wire in canvas.wires:
        if not wire.net:
            continue
        # Set of (refdes, pin_id) pairs that ARE on this wire's net per
        # the plan -- those are the legitimate touch points and must
        # not be flagged.
        own_pins = {
            (pr.refdes, pr.pin)
            for n in plan.nets
            if n.name == wire.net
            for pr in n.pins
        }
        for (px, py), pin_entries in points.items():
            if not _point_on_segment(px, py, wire.x1, wire.y1,
                                     wire.x2, wire.y2):
                continue
            for refdes, pin_id, pin_net in pin_entries:
                if (refdes, pin_id) in own_pins:
                    continue
                # Pin is NOT on the wire's net. Two bad cases:
                # (a) Pin is on a DIFFERENT plan net -> cross-net short.
                # (b) Pin has no plan-net assignment -> stray connection
                #     (the wire would bridge the plan's net into this
                #     part's auto-named net, an unplanned connection).
                shorts.append((
                    wire.net, refdes, pin_id, pin_net or "_unnetted_",
                    px, py,
                ))

    # Deduplicate -- same short often surfaces via many wire segments.
    seen: set[tuple[str, str, str, str]] = set()
    for wire_net, refdes, pin_id, pin_net, x, y in shorts:
        key = (wire_net, refdes, pin_id, pin_net)
        if key in seen:
            continue
        seen.add(key)
        result.failures.append(PipelineNote(
            severity="error",
            text=(
                f"routing short: wire on net {wire_net!r} passes through "
                f"pin {refdes}.{pin_id} (plan net {pin_net!r}) at "
                f"({x}, {y}). Altium would auto-merge the two nets; "
                f"emit blocked."
            ),
        ))

    # Net-label coincidences. A net label attaches its net to whatever pin or
    # wire sits at its point, so a label of net A landing on a FOREIGN pin or
    # on a FOREIGN net's wire merges A into that net -- a short Altium realises
    # on compile that neither the wire-vs-pin check above nor ERC reports.
    label_shorts: list[tuple[str, str, int, int]] = []
    for lb in canvas.labels:
        for refdes, pin_id, pin_net in points.get((lb.x, lb.y), []):
            if pin_net and pin_net != lb.text:
                label_shorts.append(
                    (lb.text, f"pin {refdes}.{pin_id} (net {pin_net!r})",
                     lb.x, lb.y))
        for wire in canvas.wires:
            if not wire.net or wire.net == lb.text:
                continue
            if _point_on_segment(lb.x, lb.y, wire.x1, wire.y1,
                                  wire.x2, wire.y2):
                label_shorts.append(
                    (lb.text, f"wire on net {wire.net!r}", lb.x, lb.y))
                break
    seen_labels: set[tuple[str, str]] = set()
    for text, what, x, y in label_shorts:
        key = (text, what)
        if key in seen_labels:
            continue
        seen_labels.add(key)
        result.failures.append(PipelineNote(
            severity="error",
            text=(
                f"routing short: net label {text!r} at ({x}, {y}) sits on "
                f"{what}; Altium would merge the nets; emit blocked."
            ),
        ))


def _point_on_segment(
    px: int, py: int, x1: int, y1: int, x2: int, y2: int,
) -> bool:
    """True iff (px, py) lies on the axis-aligned segment (x1,y1)-(x2,y2).

    Only axis-aligned segments are emitted by the router, so we only
    handle horizontal / vertical. Endpoints count as 'on' the segment
    (the wire physically terminates there); coincident endpoint is the
    most common shorting mode.
    """
    if x1 == x2:
        if px != x1:
            return False
        lo, hi = (y1, y2) if y1 <= y2 else (y2, y1)
        return lo <= py <= hi
    if y1 == y2:
        if py != y1:
            return False
        lo, hi = (x1, x2) if x1 <= x2 else (x2, x1)
        return lo <= px <= hi
    return False  # diagonal segments not used by the router


def _validate_canvas_against_plan(
    plan: DesignPlan,
    canvas: SchematicCanvas,
    result: PipelineResult,
) -> None:
    """Sanity-check every plan net got SOME representation on the canvas.

    A wire whose ``net`` attribute matches, a label whose ``text``
    matches, or a power port whose ``text`` matches all count. We don't
    distinguish wire-vs-label-vs-port -- if a net has zero of all three,
    something dropped it. That's not necessarily a hard failure (cross-
    sheet nets that span the canvas to a sheet we didn't render would
    legitimately have nothing here), but it's worth flagging as a
    warning so the caller can investigate.

    Pins-on-placed-instances are also checked: if a plan pin references
    a refdes that DID get placed but the pin id isn't on the symbol,
    that's a hard failure (the emit would silently drop the connection).
    The pipeline already catches this during wiring, but a redundant
    check here means a future refactor that bypasses the wiring loop
    still gets caught.
    """
    wire_nets = {w.net for w in canvas.wires if w.net}
    label_texts = {l.text for l in canvas.labels}
    port_texts = {p.text for p in canvas.power_ports}
    represented = wire_nets | label_texts | port_texts

    placed = {i.refdes: i for i in canvas.instances}
    for net in plan.nets:
        if net.name in represented:
            continue
        # Check whether ANY of this net's pins are on placed instances.
        # If not, the net is genuinely off this canvas (multi-sheet
        # design where this net lives elsewhere) -- silent skip.
        any_pin_on_canvas = any(
            pin_ref.refdes in placed for pin_ref in net.pins
        )
        if not any_pin_on_canvas:
            continue
        result.notes.append(PipelineNote(
            severity="warning",
            text=(
                f"plan net {net.name!r} has placed pins on the canvas "
                f"but no wire/label/port references it. Will emit as "
                f"electrically disconnected."
            ),
        ))


def _wire_sheet(
    *,
    canvas: SchematicCanvas,
    sheet_name: str,
    nets: list[Net],
    placeable_refdes: set[str],
    refdes_to_sheet: dict[str, str],
    refdes_to_zone: dict[str, Optional[str]],
    result: PipelineResult,
    plan: DesignPlan,
    port_hints: Optional[dict[str, dict[str, int]]] = None,
) -> None:
    """Compute wires + labels + ports for one sheet.

    Two-pass (matches executor logic):
      - Pass 1: collect every (net, pin) stub-end, emit the stub wire.
      - Pass 2: route each net's wires/labels/ports treating other
        nets' stub-ends as point obstacles (cluster radius 50 mils).
    """
    instances = canvas.instances_on(sheet_name)
    if not instances:
        return

    body_obstacles: list[tuple[int, int, int, int]] = []
    for inst in instances:
        bb = inst.world_bbox()
        body_obstacles.append((bb.x_min, bb.y_min, bb.x_max, bb.y_max))

    # Stagger counter per (refdes, direction) so two adjacent same-
    # direction stubs don't share an L-bend column.
    stagger_counter: dict[tuple[str, int, int], int] = {}

    # Per-net action list: (pin_ref, (end_x, end_y), pin_orient)
    sheet_net_actions: dict[
        str, list[tuple[Any, tuple[int, int], int]]
    ] = {}
    sheet_wire_segments: list[tuple[int, int, int, int, str]] = []
    # Per-pin stub segments (pin hotspot -> stub end), tracked apart from
    # routed segments: a net label bonds only when a wire runs under it,
    # so the cross-net cull below must be able to keep a net's stubs
    # while dropping its routing.
    stub_segments: set[tuple[int, int, int, int, str]] = set()

    # Pass 1: stub wires + endpoint collection.
    for net in nets:
        net_actions: list[tuple[Any, tuple[int, int], int]] = []
        for pin_ref in net.pins:
            if pin_ref.refdes not in placeable_refdes:
                result.failures.append(PipelineNote(
                    severity="error",
                    text=(
                        f"net {net.name!r} references unplaceable "
                        f"refdes {pin_ref.refdes!r}"
                    ),
                ))
                continue
            if refdes_to_sheet.get(pin_ref.refdes) != sheet_name:
                continue  # cross-sheet, handled on its home sheet
            endpoint = canvas.pin_world(pin_ref.refdes, pin_ref.pin)
            if endpoint is None:
                result.failures.append(PipelineNote(
                    severity="error",
                    text=(
                        f"pin {pin_ref.pin!r} not found on "
                        f"{pin_ref.refdes} (symbol mismatch)"
                    ),
                ))
                continue
            dx_dir, dy_dir = _pin_direction_vector(endpoint.orientation)
            stagger_key = (pin_ref.refdes, dx_dir, dy_dir)
            extra = stagger_counter.get(stagger_key, 0) * 100
            stagger_counter[stagger_key] = (
                stagger_counter.get(stagger_key, 0) + 1
            )
            (hot_x, hot_y), (end_x, end_y) = _stub_endpoints(
                endpoint.x, endpoint.y, endpoint.orientation,
                endpoint.length,
                obstacles=body_obstacles,
                extra_length_mils=extra,
            )
            _stub = (hot_x, hot_y, end_x, end_y, net.name)
            sheet_wire_segments.append(_stub)
            stub_segments.add(_stub)
            net_actions.append((pin_ref, (end_x, end_y), endpoint.orientation))
        if net_actions:
            sheet_net_actions[net.name] = net_actions

    # Pass 2 setup: every-other-net's stub-end becomes a point obstacle.
    all_stub_end_points: set[tuple[int, int]] = set()
    for actions in sheet_net_actions.values():
        for _, (ex, ey), _ in actions:
            all_stub_end_points.add((ex, ey))

    # Pin world coords per net, used by port routing so power-spokes
    # don't bridge through unrelated pins. Built once per sheet.
    # (refdes, pin_id) -> net_name lookup for fast filtering.
    plan_pin_to_net: dict[tuple[str, str], str] = {}
    for n in plan.nets:
        for pr in n.pins:
            plan_pin_to_net[(pr.refdes, pr.pin)] = n.name
    # All placed-pin world coords on this sheet.
    pin_world_coords: list[tuple[int, int, str]] = []  # (x, y, net_name)
    for inst in canvas.instances_on(sheet_name):
        for endpoint in inst.all_pin_endpoints():
            pn = plan_pin_to_net.get((inst.refdes, endpoint.pin_id), "")
            pin_world_coords.append((endpoint.x, endpoint.y, pn))

    shorted_to_label = 0

    # Decide every net's representation up front so the part-level HARD
    # RULE below can see the whole sheet: no component may end up with
    # every terminal on a floating net label (no wire, no port glyph).
    # _apply_no_stranded_parts_rule promotes the cheapest incident net
    # of each fully-labelled part to "wire".
    representations: dict[str, str] = {
        net.name: _net_representation(net, refdes_to_zone) for net in nets
    }

    # Wire-vs-label by SPAN + TANGLE: the professional mix is wires
    # inside a functional cluster and labels between clusters. Span
    # alone is NOT the criterion -- on a large sheet healthy nets span
    # 3000 mils, and demoting them wholesale reshuffles the whole
    # best-of landscape (measured: a sprawled 555 layout won because
    # its long nets all turned into cheap labels). A net is demoted
    # only when it is BOTH long (> _LABEL_SPAN_MILS) AND its tentative
    # route would cross other wired nets' routes -- i.e. the drawn
    # trunk would actually damage readability. Longest nets decide
    # first; each demotion removes its segments so shorter nets are
    # judged against the surviving copper. Deliberate choice, NOT
    # counted in forced_label_count (the shorts-fallback signal), but
    # charged to the selection rank at wire rate so sprawl cannot
    # profit. force_wires and current-carrying roles always stay wired.
    tentative: dict[str, list] = {}
    spans: dict[str, int] = {}
    for net in nets:
        if representations.get(net.name) != "wire":
            continue
        pts = [a[1] for a in sheet_net_actions.get(net.name, [])]
        if len(pts) < 2:
            continue
        spans[net.name] = (
            (max(p[0] for p in pts) - min(p[0] for p in pts))
            + (max(p[1] for p in pts) - min(p[1] for p in pts))
        )
        tentative[net.name] = _route_signal_pins(pts, body_obstacles)

    def _seg_crossings(a_segs, b_segs) -> int:
        n = 0
        for sa in a_segs:
            ax1, ay1, ax2, ay2 = sa[0], sa[1], sa[2], sa[3]
            a_vert = ax1 == ax2
            for sb in b_segs:
                bx1, by1, bx2, by2 = sb[0], sb[1], sb[2], sb[3]
                if a_vert == (bx1 == bx2):
                    continue
                if a_vert:
                    vx, vylo, vyhi = ax1, min(ay1, ay2), max(ay1, ay2)
                    hxlo, hxhi, hy = min(bx1, bx2), max(bx1, bx2), by1
                else:
                    vx, vylo, vyhi = bx1, min(by1, by2), max(by1, by2)
                    hxlo, hxhi, hy = min(ax1, ax2), max(ax1, ax2), ay1
                if hxlo < vx < hxhi and vylo < hy < vyhi:
                    n += 1
        return n

    net_by_name = {net.name: net for net in nets}
    for name in sorted(tentative, key=lambda n: -spans[n]):
        if spans[name] <= _LABEL_SPAN_MILS:
            continue
        net = net_by_name[name]
        if getattr(net, "force_wires", False):
            continue
        if (net.role or "").strip().lower() in _WIRED_ROLES:
            continue
        others = [
            s for other, segs in tentative.items()
            if other != name for s in segs
        ]
        if _seg_crossings(tentative[name], others) < 2:
            continue
        representations[name] = "label_per_pin"
        result.span_labelled_mils += spans[name]
        del tentative[name]
        result.notes.append(PipelineNote(
            severity="info",
            text=(
                f"net {name!r} drawn as labels: span {spans[name]} mils "
                f"and its wire route would cross other nets; labels at "
                f"each pin read better than a tangled trunk"
            ),
        ))

    sheet_refdes = {
        pr.refdes for net in nets for pr in net.pins
        if refdes_to_sheet.get(pr.refdes) == sheet_name
    }
    promoted = _apply_no_stranded_parts_rule(
        nets, representations, sheet_refdes,
    )
    for net_name in promoted:
        result.notes.append(PipelineNote(
            severity="info",
            text=(
                f"net {net_name!r} promoted from label to wire: a part "
                f"would otherwise have every terminal on labels only"
            ),
        ))

    for net in nets:
        net_actions = sheet_net_actions.get(net.name)
        if not net_actions:
            continue
        own_stub_ends: set[tuple[int, int]] = {a[1] for a in net_actions}
        other_stub_end_bboxes = [
            (x - 50, y - 50, x + 50, y + 50)
            for (x, y) in all_stub_end_points
            if (x, y) not in own_stub_ends
        ]
        routing_obstacles = list(body_obstacles) + other_stub_end_bboxes
        stub_ends = [a[1] for a in net_actions]
        representation = representations[net.name]

        if representation == "port":
            # Pins on OTHER nets become point obstacles so the port
            # centroid + spoke routing can't run a wire through them
            # (Altium auto-merges coincident endpoints; ERC wouldn't
            # catch the resulting silent short).
            other_net_pin_points = [
                (x, y) for (x, y, pn) in pin_world_coords
                if pn and pn != net.name
            ]
            # Look up the actual sheet bounds for clamping. The Sheet
            # object always carries width_mils / height_mils set from
            # the plan's size string (A4, A3, B, ...) by Sheet.__init__,
            # so there's no need for a hardcoded fallback.
            sheet_obj_match = next(
                s for s in canvas.sheets if s.name == sheet_name
            )
            sw = sheet_obj_match.width_mils
            sh = sheet_obj_match.height_mils
            _emit_port_cluster(
                canvas=canvas,
                sheet_name=sheet_name,
                net=net,
                net_actions=net_actions,
                sheet_wire_segments=sheet_wire_segments,
                routing_obstacles=routing_obstacles,
                other_net_pin_points=other_net_pin_points,
                port_hint=(port_hints or {}).get(net.name),
                sheet_width_mils=sw,
                sheet_height_mils=sh,
            )
        elif representation == "wire":
            segs = _route_signal_pins(stub_ends, routing_obstacles)
            # A routed segment passing through a pin NOT on this net is a
            # short Altium would auto-merge. At density the obstacle-aware
            # router can't always avoid this; rather than block the whole
            # emit, fall back to per-pin net labels for THIS net (labels
            # never short). A labelled connection beats no output.
            foreign_pins = [(x, y) for (x, y, pn) in pin_world_coords
                            if pn != net.name]
            would_short = any(
                _point_on_segment(px, py, s[0], s[1], s[2], s[3])
                for s in segs for (px, py) in foreign_pins
            )
            if would_short:
                shorted_to_label += 1
                for (_pr, (end_x, end_y), p_orient) in net_actions:
                    canvas.add_labels([NetLabel(
                        text=net.name, x=end_x, y=end_y,
                        orientation=0, sheet=sheet_name,
                        justification=_label_justification(p_orient))])
            else:
                for seg in segs:
                    sheet_wire_segments.append(
                        (seg[0], seg[1], seg[2], seg[3], net.name))
        else:  # "label_per_pin"
            for (_pr, (end_x, end_y), p_orient) in net_actions:
                canvas.add_labels([NetLabel(
                    text=net.name, x=end_x, y=end_y,
                    orientation=0, sheet=sheet_name,
                    justification=_label_justification(p_orient),
                )])

    # Cross-net WIRE meetings: two nets whose wires share an endpoint, or one
    # ending on the other's segment, are auto-junctioned by Altium on compile
    # -- a silent short the pin-based guard above does not see (it is wire-on-
    # wire, not at a pin). Fall the worst offender back to labels and repeat
    # until none remain.
    #
    # Drop the offender's ROUTED segments, then keep back each per-pin stub
    # that is still meeting-free. A net label bonds a pin only when a wire
    # runs under it, so dropping every stub too leaves the labels this cull
    # just placed floating and the net silently disconnected (the netlist
    # solver caught exactly that: a five-pin switch node reconstructed as
    # five isolated pins). Each stub is re-admitted only if it introduces no
    # meeting, tested with the same _cross_net_meeting_counts the routing
    # safety check uses, so the two cannot disagree. A stub that cannot come
    # back leaves its pin on a floating label, which is a hard failure: the
    # netlist would be wrong, so best-of must move to another variant rather
    # than emit it.
    while True:
        offenders = _cross_net_meeting_counts(sheet_wire_segments)
        if not offenders:
            break
        worst = max(offenders, key=lambda n: (offenders[n], n))
        worst_net = next((n for n in nets if n.name == worst), None)
        is_rail = worst_net is not None and (
            _is_power_net(worst_net) or _is_ground_net(worst_net))
        others = [s for s in sheet_wire_segments if s[4] != worst]
        stranded = 0
        label_points: list[tuple[tuple[int, int], int]] = []
        # Rails keep the original all-segments drop: they are drawn with
        # glyphs, and _repair_floating_power_pins below bonds every pin
        # the cull bared with a coincident port. Holding stubs back for
        # them only orphans the cluster glyph the spoke used to reach.
        for stub in ([] if is_rail else [
                s for s in sheet_wire_segments
                if s[4] == worst and s in stub_segments]):
            hot_x, hot_y, end_x, end_y, _ = stub
            dx = (end_x > hot_x) - (end_x < hot_x)
            dy = (end_y > hot_y) - (end_y < hot_y)
            span = max(abs(end_x - hot_x), abs(end_y - hot_y))
            placed = False
            # Longest stub first, shortening toward the pin. A shorter
            # stub reaches past less foreign copper, so a pin whose full
            # stub is blocked usually still gets a short one, which is
            # all the label needs to bond.
            for length in range(span, 0, -100):
                cand = (hot_x, hot_y,
                        hot_x + dx * length, hot_y + dy * length, worst)
                if worst not in _cross_net_meeting_counts(others + [cand]):
                    others.append(cand)
                    label_points.append(((cand[2], cand[3]), 0))
                    placed = True
                    break
            if not placed:
                stranded += 1
        sheet_wire_segments[:] = others

        # Label each pin at its (possibly shortened) stub end so every
        # label sits on this net's own copper. Rails get no labels here;
        # their pins are bonded by the port repair below.
        orients = [a[2] for a in sheet_net_actions.get(worst, [])]
        if is_rail:
            for (_pin_ref, (end_x, end_y), p_orient) in \
                    sheet_net_actions.get(worst, []):
                canvas.add_labels([NetLabel(
                    text=worst, x=end_x, y=end_y, orientation=0,
                    sheet=sheet_name,
                    justification=_label_justification(p_orient))])
        else:
            for i, ((lx, ly), _) in enumerate(label_points):
                canvas.add_labels([NetLabel(
                    text=worst, x=lx, y=ly, orientation=0, sheet=sheet_name,
                    justification=_label_justification(
                        orients[i] if i < len(orients) else 0))])
        if stranded:
            result.failures.append(PipelineNote(
                severity="error",
                text=(
                    f"net {worst!r} cannot be drawn at this placement: "
                    f"{stranded} pin(s) have no stub clear of other nets' "
                    f"copper, so they would emit on floating labels and "
                    f"the net would be disconnected."
                ),
            ))
        shorted_to_label += 1

    # Guarantee power/ground connectivity. A power spoke that the cull loop
    # above dropped leaves its pins on bare net labels -- but a net label sat
    # on a pin with no wire under it does NOT connect the pin in Altium (it
    # reads as a floating label + floating power object in ERC). Repair every
    # such pin with a power port placed COINCIDENT with the pin: a power port
    # on a pin connects directly, with no wire to route, cull, or short.
    _repair_floating_power_pins(canvas, sheet_name, plan, sheet_wire_segments)

    if shorted_to_label > 0:
        result.forced_label_count += shorted_to_label
        result.notes.append(PipelineNote(
            severity="warning",
            text=(
                f"sheet {sheet_name!r}: {shorted_to_label} signal net(s) "
                f"labelled instead of wired because wiring them would short on "
                f"another net's pin or wire at this placement density. "
                f"Connectivity is preserved via the labels (a net label is "
                f"electrically identical to a wire); this is a local placement-"
                f"density artifact, not an error."
            ),
        ))

    # Flush wires to canvas + detect junctions on the assembled list.
    wire_objects = [
        WireSegment(x1=x1, y1=y1, x2=x2, y2=y2, sheet=sheet_name, net=net_name)
        for (x1, y1, x2, y2, net_name) in sheet_wire_segments
    ]
    canvas.add_wires(wire_objects)
    # Pass the net tag so a junction dot is only placed where SAME-net wires
    # meet -- a dot at a cross-net wire crossing would short the two nets.
    for jx, jy in _detect_junctions(sheet_wire_segments):
        canvas.add_junctions([Junction(x=jx, y=jy, sheet=sheet_name)])


def _cluster_radius_for_net(
    net_actions: list[tuple[Any, tuple[int, int], int]],
    is_ground: bool = False,
) -> int:
    """Pick a Manhattan clustering radius for a power/ground net's pins.

    Pins within one radius share a single rail glyph wired with short
    spokes; pins farther apart each get their OWN symbol. The radius is
    ASYMMETRIC by kind, matching the drawn geometry of each glyph:

    * GROUND: tight (``POWER_RAIL_CLUSTER_RADIUS_MILS``, 300). A ground
      symbol is narrow and hangs below its pin, so per-pin drops are the
      convention -- a decoupling row on a 400-mil column pitch reads as
      clean parallel columns. The old shared 1000-mil radius merged the
      row into one port with a wandering spoke trunk beneath it.
    * POWER: wide (1000). A rail bar carries its NET NAME, which is
      typically wider than a decap column pitch; per-pin bars overlap
      their names ("USB_3VUSB_3V3"), so nearby rail pins share one bar.
    """
    if is_ground:
        return POWER_RAIL_CLUSTER_RADIUS_MILS
    return 1000


def _shift_centroid_clear_of_pins(
    centroid_x: int,
    forbidden_xs: set[int],
    grid_mils: int = 100,
    max_shift_mils: int = 1000,
) -> int:
    """Return a centroid x that does not coincide with any forbidden pin x.

    The port glyph sits at (centroid_x, port_y) and every spoke's vertical
    segment runs along x = centroid_x. If any pin from another net has
    the same world x, the spoke physically passes through that pin's
    location and Altium auto-merges the two nets. Shift the centroid
    along the grid until clear. Searches both directions, prefers the
    smaller absolute shift.
    """
    if centroid_x not in forbidden_xs:
        return centroid_x
    for delta in range(grid_mils, max_shift_mils + grid_mils, grid_mils):
        for sign in (+1, -1):
            candidate = centroid_x + sign * delta
            if candidate not in forbidden_xs:
                return candidate
    # All near-by columns are taken (unlikely); fall back to the original
    # so we at least emit something. The shorts detector will catch it.
    return centroid_x


def _emit_port_cluster(
    *,
    canvas: SchematicCanvas,
    sheet_name: str,
    net: Net,
    net_actions: list[tuple[Any, tuple[int, int], int]],
    sheet_wire_segments: list[tuple[int, int, int, int, str]],
    routing_obstacles: list[tuple[int, int, int, int]],
    sheet_width_mils: int,
    sheet_height_mils: int,
    other_net_pin_points: Optional[list[tuple[int, int]]] = None,
    port_hint: Optional[dict[str, int]] = None,
) -> None:
    """Cluster a power/ground net's pins and emit ONE port per cluster.

    Mirrors executor logic: greedy clustering by Manhattan radius, port
    above (VCC) or below (GND) the cluster centroid, wires from every
    pin to the port via _route_l_path through the existing obstacle set.
    The radius is now adaptive (``_cluster_radius_for_net``) so small
    boards don't get fragmented power-port glyphs.

    ``other_net_pin_points`` is the list of world-frame pin locations
    that belong to OTHER nets on the same sheet. The port centroid is
    nudged off any column shared with such a pin, and each spoke's
    L-path treats them as small bbox obstacles so the spoke never runs
    through one. Together these eliminate the silent-short failure mode
    where the spoke wire bridges unrelated nets via coincident endpoints.
    """
    is_gnd = _is_ground_net(net)
    style = _ground_style(net.name) if is_gnd else "bar"
    cluster_radius = _cluster_radius_for_net(net_actions, is_ground=is_gnd)
    clusters: list[list[int]] = []
    for i, (_, (ex, ey), _) in enumerate(net_actions):
        joined = False
        for cl in clusters:
            for j in cl:
                mx, my = net_actions[j][1]
                if abs(ex - mx) + abs(ey - my) <= cluster_radius:
                    cl.append(i)
                    joined = True
                    break
            if joined:
                break
        if not joined:
            clusters.append([i])

    other_pins = other_net_pin_points or []
    forbidden_xs = {x for (x, _) in other_pins}
    # Pin-point obstacles for L-path routing: small bbox around each
    # other-net pin so the path geometry avoids them.
    pin_point_bboxes = [(x - 25, y - 25, x + 25, y + 25) for (x, y) in other_pins]
    spoke_obstacles = list(routing_obstacles) + pin_point_bboxes

    for cl in clusters:
        cluster_pts = [net_actions[i][1] for i in cl]
        cluster_orients = [net_actions[i][2] for i in cl]
        # User-supplied port_hint wins over centroid calculation. The
        # drag-edit UI feeds these in: the user moves a port glyph,
        # the server records its new (x, y), the pipeline pins the
        # port there and routes spokes accordingly.
        if port_hint is not None:
            centroid_x = (int(port_hint.get("x", 0)) // 100) * 100
            port_y = (int(port_hint.get("y", 0)) // 100) * 100
        else:
            centroid_x = sum(pt[0] for pt in cluster_pts) // len(cluster_pts)
            centroid_x = (centroid_x // 100) * 100
            if len(cluster_pts) == 1:
                # Single-pin cluster: the professional form is a dead-
                # straight drop from the pin to its glyph. Only the actual
                # segment between pin and port must be clear -- shifting
                # off ANY shared column (the multi-pin rule below) creates
                # the 100-mil S-jog on every solo rail pin, the most
                # common wire blemish on small sheets. "Clear" must be
                # checked against the FULL obstacle set (other nets' stub
                # ends and pins, part bodies), not just pin points: parts
                # stacked in the same column put their neighbours' stub
                # SEGMENTS in the drop's path, and a collinear overlap is
                # a cross-net meeting the cull loop later resolves by
                # demoting a whole signal net to labels (measured on the
                # divider fixture: VMID fell to floating labels).
                from eda_agent.design.router import _segment_crosses_rect
                px, py = cluster_pts[0]
                drop_y = py - 400 if is_gnd else py + 400
                blocked = any(
                    _segment_crosses_rect(
                        px, py, px, drop_y, bx1, by1, bx2, by2)
                    for (bx1, by1, bx2, by2) in spoke_obstacles
                )
                if not blocked:
                    centroid_x = px
                else:
                    centroid_x = _shift_centroid_clear_of_pins(
                        centroid_x, forbidden_xs)
            else:
                # Nudge centroid off any other-net pin column so the
                # spoke's vertical segment doesn't run straight through a
                # different pin.
                centroid_x = _shift_centroid_clear_of_pins(
                    centroid_x, forbidden_xs)
            if is_gnd:
                port_y = min(pt[1] for pt in cluster_pts) - 400
            else:
                port_y = max(pt[1] for pt in cluster_pts) + 400
            port_y = (port_y // 100) * 100
            # Clamp port placement to within the sheet rectangle. Without
            # this, a cluster pin near the sheet edge pushes the port
            # glyph off-sheet (glyph_y = cluster_max_y + 400, which can
            # exceed the sheet's top edge). Margin keeps the bar fully
            # inside the page.
            _SHEET_MARGIN = 200
            if port_y < _SHEET_MARGIN:
                port_y = _SHEET_MARGIN
            if port_y > sheet_height_mils - _SHEET_MARGIN:
                port_y = sheet_height_mils - _SHEET_MARGIN
            if centroid_x < _SHEET_MARGIN:
                centroid_x = _SHEET_MARGIN
            if centroid_x > sheet_width_mils - _SHEET_MARGIN:
                centroid_x = sheet_width_mils - _SHEET_MARGIN
            # Re-snap after clamping.
            port_y = (port_y // 100) * 100
            centroid_x = (centroid_x // 100) * 100

            # Never park the glyph inside a component body. The centroid
            # of a multi-pin cluster (or a clamped drop) can land within
            # a part's bbox -- measured on the 555 fixture, where the
            # VCC bar sat inside the input connector's body. March the
            # glyph further along its natural direction (down for
            # ground, up for power) until BOTH the glyph point is clear
            # of every body obstacle AND the lengthened drop segment
            # does not pass through a foreign pin (marching past a body
            # must not buy the glyph a new short). If no position within
            # the march budget satisfies both, keep the original spot --
            # a glyph brushing a body is ugly; a silent short is worse.
            def _inside_a_body(px: int, py: int) -> bool:
                for (ox1, oy1, ox2, oy2) in routing_obstacles:
                    if ox1 - 50 <= px <= ox2 + 50 and \
                            oy1 - 50 <= py <= oy2 + 50:
                        return True
                return False

            def _drop_hits_foreign_pin(px: int, py: int) -> bool:
                ys = [pt[1] for pt in cluster_pts] + [py]
                lo, hi = min(ys), max(ys)
                return any(
                    x == px and lo <= y <= hi for (x, y) in other_pins
                )

            if _inside_a_body(centroid_x, port_y):
                step = -100 if is_gnd else 100
                cand_y = port_y
                for _ in range(20):
                    cand_y += step
                    if _inside_a_body(centroid_x, cand_y):
                        continue
                    if _drop_hits_foreign_pin(centroid_x, cand_y):
                        continue
                    port_y = cand_y
                    break
        # port orientation tracked for the emitter so it knows whether
        # the glyph mounts above or below the connection; the canvas
        # stores it in the PowerPort.style + emitter will translate.
        port_orient_unused = _power_port_orientation(
            cluster_orients[0], is_ground=is_gnd
        )
        del port_orient_unused
        canvas.add_power_ports([PowerPort(
            text=net.name,
            x=centroid_x,
            y=port_y,
            style=style,
            sheet=sheet_name,
        )])
        for pt in cluster_pts:
            if pt == (centroid_x, port_y):
                continue
            for seg in _route_l_path(
                pt[0], pt[1], centroid_x, port_y, spoke_obstacles
            ):
                sheet_wire_segments.append((seg[0], seg[1], seg[2], seg[3], net.name))


#: How far a repair stub reaches before the glyph sits on it. Short on
#: purpose: this is the "pin, tick, glyph" the hand-drawn convention
#: uses, not a route. Clipped further by _adaptive_stub_length when a
#: neighbouring body is closer than this.
_REPAIR_STUB_LEN_MILS = 200


def _repair_stub_is_safe(
    sx: int, sy: int, ex: int, ey: int,
    foreign_points: set[tuple[int, int]],
    foreign_wires: list[tuple[int, int, int, int]],
) -> bool:
    """True iff a stub (sx,sy)->(ex,ey) cannot bond to another net.

    Encodes Altium's connection rules rather than plain geometry, and
    the distinction matters in both directions.

    Two wires CROSSING mid-span do not connect without a junction dot,
    so a crossing must not veto the stub. Vetoing on crossings would
    reject almost every stub on a dense sheet, which is the same as not
    having the feature.

    What does connect, and is therefore checked:
      * a foreign pin or power port anywhere along the stub
      * our endpoint landing on a foreign wire (a T-intersection)
      * a foreign wire's endpoint landing on our stub (the same, mirrored)
    """
    for (px, py) in foreign_points:
        # The source end is this net's own pin, so it is not foreign
        # traffic even if some other net's geometry also passes there.
        if (px, py) == (sx, sy):
            continue
        if _point_on_segment(px, py, sx, sy, ex, ey):
            return False
    for (x1, y1, x2, y2) in foreign_wires:
        if _point_on_segment(ex, ey, x1, y1, x2, y2):
            return False
        if _point_on_segment(x1, y1, sx, sy, ex, ey):
            return False
        if _point_on_segment(x2, y2, sx, sy, ex, ey):
            return False
    return True


def _clearance_to_bodies(
    x: int, y: int, bboxes: list[tuple[int, int, int, int]],
) -> float:
    """Distance from a point to the nearest symbol body, 0 if inside.

    Used to keep the stub upgrade from crowding a glyph it was meant to
    give room to.
    """
    best = float("inf")
    for (x1, y1, x2, y2) in bboxes:
        dx = max(x1 - x, 0, x - x2)
        dy = max(y1 - y, 0, y - y2)
        best = min(best, (dx * dx + dy * dy) ** 0.5)
    return best


def _glyph_would_hit_text(
    x: int, y: int, text: str, canvas: SchematicCanvas, sheet: str,
    skip_index: int,
) -> bool:
    """True if a glyph at (x, y) would collide with text already placed.

    Only NET LABELS and other glyphs are checked here. Designator and
    value text is deliberately not: ``place_instance_text`` re-runs after
    this pass and moves that text out of the way, so rejecting a move on
    its account would forfeit the improvement for a collision that is
    about to be resolved anyway. Net labels are never moved, so a glyph
    dropped on one stays there.

    Extents use the text placer's own character metrics rather than a
    second set, so the two agree on what overlaps.
    """
    from eda_agent.design.text_placement import CHAR_W, LINE_H

    def _text_box(tx: int, ty: int, s: str, just: int = 0):
        w = CHAR_W * max(1, len(s))
        x1 = tx - w if just == 2 else tx
        return (x1, ty, x1 + w, ty + LINE_H)

    # The glyph's own footprint: the bar/symbol plus its name underneath.
    half = max(100, (CHAR_W * max(1, len(text))) // 2)
    mine = (x - half, y - LINE_H, x + half, y + LINE_H)

    def _hits(box) -> bool:
        return not (mine[2] <= box[0] or box[2] <= mine[0]
                    or mine[3] <= box[1] or box[3] <= mine[1])

    for lab in canvas.labels:
        if lab.sheet != sheet:
            continue
        if _hits(_text_box(lab.x, lab.y, lab.text,
                           getattr(lab, "justification", 0))):
            return True
    for i, other in enumerate(canvas.power_ports):
        # Skip by INDEX, not by coordinate. The glyph being moved is
        # still recorded at its old position while its new one is being
        # tested, so a coordinate check does not exclude it -- and with
        # LINE_H at 110 its own 220-tall box overlaps itself across a
        # 200-mil stub, which silently rejected every vertical move.
        if i == skip_index or other.sheet != sheet:
            continue
        o_half = max(100, (CHAR_W * max(1, len(other.text))) // 2)
        if _hits((other.x - o_half, other.y - LINE_H,
                  other.x + o_half, other.y + LINE_H)):
            return True
    return False


def upgrade_repair_ports_to_stubs(
    canvas: SchematicCanvas,
    plan: DesignPlan,
) -> int:
    """Move repair glyphs off their pins onto a short stub, where safe.

    WHAT THIS BUYS, measured rather than assumed. A repair glyph sits on
    the pin's ELECTRICAL end, which is already outside the body, so it
    never overlaps the symbol -- on the benchmark boards not one glyph of
    60 was inside a body bbox either before or after. What it does is
    relieve CROWDING: a glyph can sit 150 mils off a neighbouring body
    with its own bar and text in that gap, and the stub pushes it clear
    (measured +200 mils on every moved glyph of one board, mean +189 on
    another).

    That is a modest gain, so the pass is deliberately conservative: a
    glyph is moved only when the stub is electrically safe AND the glyph
    ends no closer to any body than it started. The second condition
    currently rejects nothing on any benchmark board, and is kept anyway
    because it is what makes this pass safe to run unattended:
    ``_adaptive_stub_length`` only steers around the obstacles in the
    pin's own path, so nothing else stops a stub carrying a glyph toward
    a DIFFERENT part, and a cosmetic pass that crowds a glyph is worse
    than one that does nothing. Enforced structurally rather than trusted
    to keep holding on boards nobody has run yet.

    A move is also refused when the glyph would land on a NET LABEL or
    another glyph. That one is not theoretical: it rejects 13 of 49
    candidate moves on the mcu board as the test suite builds it.
    (Every count in this docstring is from the benchmark boards under
    the suite's reduced force-directed sweep; production uses the full
    sweep, places differently, and will not reproduce them exactly.) Net labels are never repositioned
    by the text placer, so a glyph dropped on one stays there, whereas
    designator/value text is re-placed afterwards and is therefore not a
    reason to refuse a move.

    Do NOT justify this by ``bends_per_power_net``. That number does drop
    (2.5 -> 1.25 on one board) but only because straight 0-bend stubs
    dilute an average over the rail's wires; no existing wire got
    straighter. Clearance is the honest measure.

    COSMETIC ONLY, and that is why it is a separate pass run once on the
    chosen canvas instead of inside ``_repair_floating_power_pins``.
    That repair executes for every best-of candidate, so any wire it drew
    would enter the scored objective and steer placement: an earlier
    version of this did exactly that and moved a part to the wrong side
    of its IC. Connectivity is already guaranteed before this runs, so
    nothing here can change it, only how it reads.

    A repair glyph is identified by geometry rather than a tag: a power
    port sitting exactly on a pin of its own net with no wire of that net
    ending there. That is precisely what the repair leaves behind, and
    re-deriving it keeps the two passes independent.

    Returns how many glyphs were moved.
    """
    moved = 0
    for sheet in {inst.sheet for inst in canvas.instances}:
        pin_xy: dict[tuple[str, str], tuple[int, int]] = {}
        pin_dir: dict[tuple[str, str], int] = {}
        bboxes: list[tuple[int, int, int, int]] = []
        for inst in canvas.instances_on(sheet):
            for ep in inst.all_pin_endpoints():
                pin_xy[(inst.refdes, ep.pin_id)] = (ep.x, ep.y)
                # Outward direction, so the stub leaves the body rather
                # than running back across it.
                pin_dir[(inst.refdes, ep.pin_id)] = ep.orientation
            bb = inst.world_bbox()
            bboxes.append((int(bb.x_min), int(bb.y_min),
                           int(bb.x_max), int(bb.y_max)))

        # Which net owns each pin. A pin on no net at all still counts as
        # foreign: bonding it into a power rail would be a short this
        # pass invented.
        pin_net: dict[tuple[int, int], str] = {}
        for a_net in plan.nets:
            for pr in a_net.pins:
                pt = pin_xy.get((pr.refdes, pr.pin))
                if pt is not None:
                    pin_net[pt] = a_net.name

        for net in plan.nets:
            if not (_is_power_net(net) or _is_ground_net(net)):
                continue
            own_keys = [(pr.refdes, pr.pin) for pr in net.pins
                        if (pr.refdes, pr.pin) in pin_xy]
            own_pins = {pin_xy[k]: k for k in own_keys}
            wire_ends = {
                pt for w in canvas.wires if w.sheet == sheet
                and w.net == net.name
                for pt in ((w.x1, w.y1), (w.x2, w.y2))
            }
            for idx, port in enumerate(canvas.power_ports):
                if port.sheet != sheet or port.text != net.name:
                    continue
                here = (port.x, port.y)
                key = own_pins.get(here)
                if key is None or here in wire_ends:
                    continue  # not a repair glyph
                dx, dy = _pin_direction_vector(pin_dir.get(key, 0))
                if (dx, dy) == (0, 0):
                    continue
                length = _adaptive_stub_length(
                    port.x, port.y, dx, dy, bboxes,
                    base_length=_REPAIR_STUB_LEN_MILS)
                ex = port.x + dx * length
                ey = port.y + dy * length
                foreign_points = {
                    pt for pt, owner in pin_net.items()
                    if owner != net.name
                }
                foreign_points |= {
                    (p.x, p.y) for p in canvas.power_ports
                    if p.sheet == sheet and p.text != net.name
                }
                foreign_wires = [
                    (w.x1, w.y1, w.x2, w.y2) for w in canvas.wires
                    if w.sheet == sheet and w.net != net.name
                ]
                if not _repair_stub_is_safe(port.x, port.y, ex, ey,
                                            foreign_points, foreign_wires):
                    continue
                # Net labels are NOT moved by the text placer, so a glyph
                # landing on one stays landed on it. Checked here because
                # nothing downstream will clean it up.
                if _glyph_would_hit_text(ex, ey, port.text, canvas, sheet,
                                         skip_index=idx):
                    continue
                # And it must not make the drawing worse. Rejects nothing
                # on the current benchmark boards; kept because the stub
                # only steers around obstacles in the pin's own path, so
                # nothing else prevents it carrying the glyph toward a
                # different body.
                if (_clearance_to_bodies(ex, ey, bboxes)
                        < _clearance_to_bodies(port.x, port.y, bboxes)):
                    continue
                canvas.add_wires([WireSegment(
                    x1=port.x, y1=port.y, x2=ex, y2=ey,
                    sheet=sheet, net=net.name)])
                # PowerPort is frozen, so the glyph is replaced in place
                # rather than moved.
                canvas.power_ports[idx] = replace(port, x=ex, y=ey)
                # The pin is now a wire end, so a second pass would not
                # mistake it for another unrepaired glyph.
                wire_ends |= {here, (ex, ey)}
                moved += 1
    return moved


def _repair_floating_power_pins(
    canvas: SchematicCanvas,
    sheet_name: str,
    plan: DesignPlan,
    sheet_wire_segments: list[tuple[int, int, int, int, str]],
) -> None:
    """Ensure every power/ground pin actually connects in Altium.

    A power pin connects when a wire ends on it, or a power port sits exactly
    on it. The clustered port + spoke path (``_emit_port_cluster``) wires the
    pins to a shared glyph, but a spoke can be dropped by the cross-net cull
    above; the dropped net then falls to bare labels that float (a net label
    with no wire under it does not bond the pin). This pass finds any power /
    ground pin that ends up neither wire-connected nor under a port and drops a
    power port COINCIDENT with it -- the one Altium primitive that bonds a pin
    with no wire, so it cannot be culled or short. It also clears that net's
    now-redundant floating labels and any orphaned cluster glyph (a port left
    sitting on neither a pin nor a surviving spoke end), which would otherwise
    read as floating power objects in ERC. Fully-wired nets are left untouched.

    Deliberately adds NO wire. This runs inside every best-of candidate, so
    anything it draws lands in the scored objective and steers placement.
    Moving the glyph off the pin onto a short stub is the nicer drawing, but it
    is cosmetic, and it is applied once to the winning canvas by
    ``upgrade_repair_ports_to_stubs`` rather than here.
    """
    pin_xy: dict[tuple[str, str], tuple[int, int]] = {}
    for inst in canvas.instances_on(sheet_name):
        for ep in inst.all_pin_endpoints():
            pin_xy[(inst.refdes, ep.pin_id)] = (ep.x, ep.y)

    for net in plan.nets:
        if not (_is_power_net(net) or _is_ground_net(net)):
            continue
        net_pin_keys = [
            (pr.refdes, pr.pin) for pr in net.pins
            if (pr.refdes, pr.pin) in pin_xy
        ]
        net_pins = [pin_xy[k] for k in net_pin_keys]
        if not net_pins:
            continue
        wire_ends: set[tuple[int, int]] = set()
        for (x1, y1, x2, y2, nm) in sheet_wire_segments:
            if nm == net.name:
                wire_ends.add((x1, y1))
                wire_ends.add((x2, y2))
        port_pts = {
            (p.x, p.y)
            for p in canvas.power_ports
            if p.sheet == sheet_name and p.text == net.name
        }
        floating = [
            key for key in net_pin_keys
            if pin_xy[key] not in wire_ends and pin_xy[key] not in port_pts
        ]
        if not floating:
            continue  # net is fully connected -- leave the working path alone

        style = _ground_style(net.name) if _is_ground_net(net) else "bar"
        canvas.add_power_ports([
            PowerPort(text=net.name, x=pin_xy[key][0], y=pin_xy[key][1],
                      style=style, sheet=sheet_name)
            for key in floating
        ])
        # This net's labels never bonded (power nets carry ports, not labels);
        # drop them so they do not linger as floating net labels.
        canvas.labels[:] = [
            l for l in canvas.labels
            if not (l.sheet == sheet_name and l.text == net.name)
        ]
        # Drop orphaned glyphs: a port of this net sitting on neither a pin nor
        # a surviving spoke end is electrically floating.
        keep = set(net_pins) | wire_ends
        canvas.power_ports[:] = [
            p for p in canvas.power_ports
            if not (
                p.sheet == sheet_name
                and p.text == net.name
                and (p.x, p.y) not in keep
            )
        ]


def _ic_pin_offsets(
    plan: DesignPlan,
    extractor: SymbolExtractor,
    *,
    ic_pin_threshold: int = 4,
) -> dict[str, dict[str, tuple[int, int]]]:
    """Each IC pin's WIRE-end offset from the IC centre, native (rot-0) frame.

    ``{ic_refdes: {pin_id: (dx, dy)}}`` for every part with at least
    ``ic_pin_threshold`` pins. Feeds the pin-aware force-directed placer so a
    discrete is pulled toward the specific pin it wires to. Built from the
    extracted symbol geometry (the same wire end the router and the canvas use
    -- never the label/body end). Best-effort: returns what it can resolve.
    """
    pin_count: dict[str, int] = {}
    for net in plan.nets:
        for pr in net.pins:
            pin_count[pr.refdes] = pin_count.get(pr.refdes, 0) + 1
    ics = {p.refdes for p in plan.parts
           if pin_count.get(p.refdes, 0) >= ic_pin_threshold}
    if not ics:
        return {}
    refs = list({(p.lib_path, p.lib_ref) for p in plan.parts
                 if p.refdes in ics and p.lib_path})
    if not refs:
        return {}
    try:
        symbols = extractor.extract_many(refs)
    except Exception:
        return {}
    out: dict[str, dict[str, tuple[int, int]]] = {}
    for part in plan.parts:
        if part.refdes not in ics:
            continue
        s = symbols.get((part.lib_path, part.lib_ref))
        if s is None:
            continue
        inst = SymbolInstance(refdes=part.refdes, symbol=s, x=0, y=0, rotation=0)
        out[part.refdes] = {
            ep.pin_id: (ep.x, ep.y) for ep in inst.all_pin_endpoints()
        }
    return out
