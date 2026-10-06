# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Turn a validated DesignPlan into an ordered list of EasyEDA calls.

WHY THIS IS NOT AN EXECUTOR. ``design/executor.py`` drives Altium
directly, and it names Altium bridge commands throughout, so it has no
seam an EasyEDA backend could be dropped into. Rather than abstract a
1100-line module that works, this follows the pattern
``lib_easyeda_import`` already uses: produce the ordered sequence of
this server's own tool calls and hand it back.

The consequence is the useful part. The sequence is data, so it can be
read, diffed and validated before anything touches a design, and the
Altium path is untouched by anything here.

WHAT IT DELIBERATELY DOES NOT DO. It never picks a library part. Altium
resolves a symbol by name; EasyEDA needs the ``{libraryUuid, uuid}``
pair a search returns, and a search for an MPN can come back with
several. Choosing one silently is how a board ends up with the wrong
footprint under a BOM line that reads correctly, so an unresolved part
becomes a search step plus an explicit hole in the plan, and emitting
stops short of pretending the design is placeable.

UNITS. Every coordinate here is in mils, matching the layout engine and
the rest of this project. The conversion to EasyEDA's schematic units
belongs to the tool layer, in ``MILS_PER_SCHEMATIC_UNIT``, and must not
be repeated here: applying it twice is a hundredfold error that still
draws a plausible-looking schematic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ._wiring import _is_ground_net, _net_representation
from .plan import DesignPlan, PartStatus

__all__ = [
    "EmittedCall",
    "EasyEdaPlan",
    "emit_easyeda_plan",
    "emit_easyeda_connections",
]


@dataclass
class EmittedCall:
    """One tool call, with why it is here."""

    tool: str
    arguments: dict[str, Any]
    #: What this step is for, in one line, for a human reading the plan.
    purpose: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "arguments": self.arguments,
            "purpose": self.purpose,
        }


@dataclass
class EasyEdaPlan:
    """The emitted sequence, plus what stopped it being complete."""

    calls: list[EmittedCall] = field(default_factory=list)
    #: Parts with no library uuid and uuid pair yet. Each one is a
    #: search step in ``calls``; this list is what the caller must
    #: resolve before the sequence can run.
    #:
    #: EITHER SPELLING IS ACCEPTED. ``lib.search_devices`` answers with
    #: ``libraryUuid`` and this emitter was written against
    #: ``library_uuid``, so feeding a search result straight back left
    #: every part unresolved even though the documented flow chains
    #: exactly those two steps. See ``_library_uuid``.
    unresolved_parts: list[dict[str, Any]] = field(default_factory=list)
    #: Reasons the plan cannot be run as emitted. Non-empty means do not
    #: run it.
    blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: Nets this sequence does NOT connect, and what connects them.
    #:
    #: Placement can be emitted from a plan alone; a wire is drawn at a
    #: pin, and pin coordinates only exist once the symbols are placed.
    #: So a full schematic takes two passes, and the first reports
    #: `runnable` true with every net still missing. Carried as data so
    #: a caller can see the sequence is a stage rather than the whole
    #: job without having to read the note.
    nets_pending: int = 0
    next_step: Optional[str] = None

    @property
    def runnable(self) -> bool:
        return not self.blockers and not self.unresolved_parts

    @property
    def complete(self) -> bool:
        """Runnable AND nothing deferred to a later pass."""
        return self.runnable and not self.nets_pending

    def to_dict(self) -> dict[str, Any]:
        return {
            "runnable": self.runnable,
            "complete": self.complete,
            "calls": [c.to_dict() for c in self.calls],
            "unresolved_parts": self.unresolved_parts,
            "blockers": self.blockers,
            "nets_pending": self.nets_pending,
            "next_step": self.next_step,
            "notes": self.notes,
        }


def _library_uuid(ref: dict) -> Optional[str]:
    """The library uuid from a resolution entry, either spelling.

    ``lib.search_devices`` answers with ``libraryUuid``, because that is
    what the editor's own API returns. This emitter asked for
    ``library_uuid``. The documented flow chains the two directly, emit
    a search step, run it, feed the result back as a resolution, and it
    did not join: a caller who passed the search result through
    unchanged had every part reported as unresolved.

    Both spellings are accepted, snake_case first since it matches the
    argument the place call takes. Converting at the boundary beats
    making every caller rename a field the editor chose.
    """
    return ref.get("library_uuid") or ref.get("libraryUuid")


def _search_terms(part: Any) -> Optional[str]:
    """What to search the EasyEDA library for, for one part.

    MPN first: it identifies one physical part, and it is what the
    atomic-parts contract requires every existing part to carry. A
    library reference is a name in somebody's library and can match
    several things, so it is a fallback rather than a choice.
    """
    if getattr(part, "mpn", None) and part.mpn.strip():
        return part.mpn.strip()
    if getattr(part, "lib_ref", None) and part.lib_ref.strip():
        return part.lib_ref.strip()
    return None


def emit_easyeda_plan(
    plan: DesignPlan,
    *,
    placements: Optional[dict[str, tuple[float, float, float]]] = None,
    resolved_parts: Optional[dict[str, dict[str, str]]] = None,
) -> EasyEdaPlan:
    """Emit the EasyEDA call sequence for ``plan``.

    Args:
        plan: a validated DesignPlan.
        placements: refdes -> (x_mils, y_mils, rotation). Computed by
            ``compute_layout`` when the caller has run it. Parts with no
            placement are reported rather than dropped at the origin,
            where they would stack invisibly on top of each other.
        resolved_parts: refdes -> {"library_uuid": ..., "uuid": ...} for
            parts already looked up. Anything absent gets a search step
            and is listed as unresolved.

    Returns:
        An EasyEdaPlan. ``runnable`` is false whenever anything was left
        undecided, and the sequence should not be run in that state.
    """
    out = EasyEdaPlan()
    placements = placements or {}
    resolved_parts = resolved_parts or {}

    cross = plan.cross_check()
    if cross:
        out.blockers.extend(cross)
        return out

    needs_creation = [p.refdes for p in plan.parts
                      if p.status == PartStatus.NEEDS_CREATION]
    if needs_creation:
        # Same refusal the Altium executor makes, for the same reason: a
        # partially placed design reads as a finished one.
        out.blockers.append(
            "plan contains parts that need creating "
            f"({', '.join(sorted(needs_creation))}); emitting a sequence "
            "that places the rest would produce a design that looks "
            "complete and is not")
        return out

    out.calls.append(EmittedCall(
        tool="easyeda_ping",
        arguments={},
        purpose="confirm an editor is connected before changing anything",
    ))

    for part in plan.parts:
        ref = resolved_parts.get(part.refdes)

        # A RESOLUTION THAT DID NOT PARSE IS NOT AN UNRESOLVED PART.
        #
        # An entry present under the wrong keys used to fall through to
        # the search branch, so a caller who HAD looked the part up was
        # told to look it up again, with nothing saying why. The keys
        # here are snake_case, and this file described them both ways:
        # the field comment above said {libraryUuid, uuid} and the
        # docstring said {library_uuid, uuid}. Following the wrong one
        # cost a silent downgrade.
        if ref and not (_library_uuid(ref) and ref.get("uuid")):
            out.unresolved_parts.append({
                "refdes": part.refdes,
                "reason": (
                    f"resolved_parts has an entry for {part.refdes} but it "
                    f"carries {sorted(ref)} instead of a library uuid "
                    f"(library_uuid or libraryUuid) and uuid, so it "
                    f"could not be used"),
            })
            continue

        if ref and _library_uuid(ref) and ref.get("uuid"):
            placement = placements.get(part.refdes)
            if placement is None:
                out.unresolved_parts.append({
                    "refdes": part.refdes,
                    "reason": "no placement was computed for this part",
                })
                continue
            x, y, rotation = placement
            out.calls.append(EmittedCall(
                tool="easyeda_place_schematic_component",
                arguments={
                    "library_uuid": _library_uuid(ref),
                    "uuid": ref["uuid"],
                    "x": x,
                    "y": y,
                    "rotation": rotation,
                },
                purpose=f"place {part.refdes} ({part.value or part.lib_ref})",
            ))
            continue

        terms = _search_terms(part)
        if terms is None:
            out.unresolved_parts.append({
                "refdes": part.refdes,
                "reason": "no mpn and no lib_ref to search for",
            })
            continue

        out.calls.append(EmittedCall(
            tool="easyeda_search_devices",
            arguments={"query": terms},
            purpose=f"find a library part for {part.refdes} ({terms})",
        ))
        out.unresolved_parts.append({
            "refdes": part.refdes,
            "search": terms,
            "reason": "no library uuid pair yet; run the search and choose",
        })

    if out.unresolved_parts:
        out.notes.append(
            f"{len(out.unresolved_parts)} of {len(plan.parts)} parts are "
            "not placeable yet. Resolve each one to a "
            "{library_uuid, uuid} pair and emit again; nothing here picks "
            "among search results, because a wrong pick produces a board "
            "that is wrong in a way the BOM does not show.")

    # Connectivity cannot be emitted alongside placement, and saying so
    # is better than emitting coordinates that would be guesses. A wire
    # or a net label is drawn AT a pin, and a pin's position is not
    # known until its symbol is placed and the editor is asked where its
    # pins landed. Nothing in the plan carries that: the layout engine
    # positions parts, not pins.
    count = len(plan.nets)
    out.notes.append(
        f"{count} net{'' if count == 1 else 's'} "
        f"{'is' if count == 1 else 'are'} not in this sequence. Connecting "
        "them is a second pass: place the parts, read the pin coordinates "
        "back with easyeda_get_schematic_pins, then emit wires and labels "
        "against real positions. Emitting them now would mean inventing "
        "coordinates, and a schematic wired to the wrong points still "
        "looks like a schematic.")

    # SAY IT IN DATA, not only in prose.
    #
    # `runnable` is the flag a caller branches on, and it is true here
    # while every net is still missing. That is correct for what this
    # pass is, and it reads as "the sequence is complete" to anything
    # that does not also parse the note. Running it and stopping leaves
    # parts placed and nothing wired, which looks like a finished
    # schematic and is not one.
    out.nets_pending = count
    out.next_step = (
        "easyeda_emit_connections" if count else None)

    return out


def emit_easyeda_connections(
    plan: DesignPlan,
    pin_positions: dict[tuple[str, str], tuple[float, float]],
) -> EasyEdaPlan:
    """Emit the calls that connect a plan's nets, given real pin points.

    The second pass. Placement can be emitted from the plan alone, but a
    wire or a label is drawn AT a pin, and a pin's position only exists
    once its symbol is on the sheet. So this takes the positions read
    back from the editor rather than deriving them, and a net with a
    missing pin is reported instead of connected to a guess.

    How each net is drawn comes from ``_net_representation``, the same
    rule the Altium path uses, imported rather than restated. Two
    backends that decide this separately would drift, and the drift
    would show up as one tool drawing labels where the other draws
    wires, on the same plan.

    Args:
        plan: the validated DesignPlan.
        pin_positions: (refdes, pin) -> (x_mils, y_mils), in MILS. What
            the editor reports is in schematic units of ten mils, so a
            caller passing those through unconverted puts every wire a
            tenth of the way to where it belongs.

    Returns:
        An EasyEdaPlan whose calls connect the nets. ``blockers`` names
        any net that could not be drawn.
    """
    out = EasyEdaPlan()

    refdes_to_zone = {p.refdes: p.zone for p in plan.parts}

    for net in plan.nets:
        points: list[tuple[str, float, float]] = []
        missing: list[str] = []
        for pin in net.pins:
            position = pin_positions.get((pin.refdes, pin.pin))
            if position is None:
                missing.append(f"{pin.refdes}.{pin.pin}")
                continue
            points.append((pin.refdes, position[0], position[1]))

        if missing:
            # Drawing the pins that ARE known would produce a net that
            # looks connected and is not, which survives review.
            out.blockers.append(
                f"net {net.name}: no position for "
                f"{', '.join(missing)}; nothing drawn for this net")
            continue

        representation = _net_representation(net, refdes_to_zone)

        if representation == "port":
            kind = "Ground" if _is_ground_net(net) else "Power"
            for _refdes, x, y in points:
                out.calls.append(EmittedCall(
                    tool="easyeda_create_net_flag",
                    arguments={"name": net.name, "x": x, "y": y,
                               "kind": kind},
                    purpose=f"{kind.lower()} rail glyph on {net.name}",
                ))
            continue

        if representation == "label_per_pin":
            for _refdes, x, y in points:
                out.calls.append(EmittedCall(
                    tool="easyeda_create_net_label",
                    arguments={"name": net.name, "x": x, "y": y},
                    purpose=f"label {net.name} at a pin it crosses to",
                ))
            continue

        # A wire. Drawn pin to pin in the order the net lists them,
        # which is the plan's order rather than a shortest path: routing
        # is not this function's job, and a net named by the planner in
        # signal order reads correctly drawn that way.
        previous: Optional[list[list[float]]] = None
        for (from_ref, x1, y1), (to_ref, x2, y2) in zip(points, points[1:]):
            # A ZERO LENGTH WIRE IS NOT A CONNECTION, IT IS A SYMPTOM.
            #
            # Two pins at one coordinate means the symbols are on top of
            # each other, which is a placement fault. Drawing a wire
            # from a point to itself adds an invisible primitive that
            # cannot be selected and hides the real problem, so the
            # overlap is reported instead.
            if x1 == x2 and y1 == y2:
                out.notes.append(
                    f"net {net.name}: {from_ref} and {to_ref} are at the "
                    f"same point ({x1}, {y1}), so no wire was drawn. Two "
                    f"pins in one place means the parts are placed on top "
                    f"of one another; fix the placement rather than the "
                    f"wiring.")
                previous = None
                continue

            route = _orthogonal(x1, y1, x2, y2, previous=previous)
            previous = route
            out.calls.append(EmittedCall(
                tool="easyeda_add_wire",
                arguments={"points": route, "net": net.name},
                purpose=f"wire {net.name} from {from_ref} to {to_ref}",
            ))

    return out


def _orthogonal(x1: float, y1: float, x2: float, y2: float, *,
                previous: "Optional[list[list[float]]]" = None,
                ) -> list[list[float]]:
    """Pin to pin as horizontal and vertical runs, never a diagonal.

    A two-point segment between pins that share neither coordinate is a
    DIAGONAL wire. Schematics are drawn on the square: every reader
    expects horizontal and vertical runs, a diagonal reads as a mistake
    even where the editor accepts it, and this project holds its
    schematics to what a person would draw by hand.

    Aligned pins keep their single straight segment. Everything else
    gets one elbow, horizontal first by default. That default is
    arbitrary between the two L shapes and is fixed rather than chosen
    per net, so two runs of one plan draw the same schematic.

    IT FLIPS TO VERTICAL FIRST WHEN HORIZONTAL WOULD RETRACE. A net of
    three or more pins is wired as a chain, so each wire starts where
    the last one ended. On a three pin net whose first two pins share a
    row, the horizontal first elbow sent the branch back along the wire
    just drawn, laying a second line on top of it before turning off.
    Doubled copper is not what it looks like on a schematic; it looks
    like one wire, and the drawing quietly stops matching what was
    emitted.
    """
    if x1 == x2 or y1 == y2:
        return [[x1, y1], [x2, y2]]

    horizontal_first = [[x1, y1], [x2, y1], [x2, y2]]
    if previous and len(previous) >= 2:
        last = previous[-2:]
        (px1, py1), (px2, py2) = last[0], last[1]
        # The previous run ends horizontally at our starting height, and
        # our first leg would travel back along it.
        retraces = (py1 == py2 == y1
                    and min(px1, px2) <= x2 <= max(px1, px2))
        if retraces:
            return [[x1, y1], [x1, y2], [x2, y2]]
    return horizontal_first
