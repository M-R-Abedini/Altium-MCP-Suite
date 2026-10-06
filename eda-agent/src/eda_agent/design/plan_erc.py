# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Offline electrical-rule check (ERC-lite) on a DesignPlan.

Altium's ERC catches connectivity mistakes, but only after the plan has been
executed into a schematic -- an expensive round-trip. This module flags the
topology-only errors a planner most often makes, BEFORE emit, from the plan
alone. It does not need pin electrical types (which the plan does not carry),
so it covers what topology can prove and leaves driver/load conflicts to
Altium ERC.

Checks:

* ``shorted_pin`` (error) -- one pin endpoint appears on more than one net,
  which shorts those nets at that pin (the schema only rejects duplicate pins
  WITHIN a net, so a cross-net short otherwise slips through to emit).
* ``contradictory_net_flags`` (error) -- a net flagged BOTH ``is_power`` and
  ``is_ground`` (a net is one or the other).
* ``floating_net`` (error) -- a net whose pins all belong to a single part
  connects nothing to the rest of the design (the classic "both ends landed
  on U1" typo). The schema already forbids a one-pin net, so this catches the
  remaining degenerate case.
* ``shorted_component`` (error) -- a 2-pin passive (R / C / L) with both
  terminals on the SAME net, which makes it electrically inert (a shorted
  resistor, a cap across nothing). The complement of ``shorted_pin``: there
  the one pin is on several nets, here the several pins collapse onto one net.
  Both pins being distinct, the schema's duplicate-pin check does not fire,
  and the part reaches another net so it is not ``floating_net`` either.
* ``unconnected_part`` (warning) -- a part that appears in no net at all.
* ``missing_decoupling`` (warning) -- an IC (a part with >= ``ic_pin_threshold``
  pins) sits on a power rail with no decoupling capacitor (a 2-pin cap from
  that rail to a ground net). The oldest power-integrity rule.
* ``malformed_value`` (warning) -- a passive (R / C / L) carries a value
  string the engineering-value parser cannot read (a BOM typo like ``10kk``).

All checks are naming-agnostic (topology + the plan's power/ground flags and
the refdes-kind letter, not net names). NDA scope: only the current plan.
"""

from __future__ import annotations

from dataclasses import dataclass

from eda_agent.design.plan import DesignPlan

# A part with at least this many distinct pins is treated as an IC for the
# decoupling rule (a 2-pin passive is never the thing that needs a bypass cap).
DEFAULT_IC_PIN_THRESHOLD = 4


@dataclass(frozen=True)
class ErcIssue:
    code: str                 # floating_net | unconnected_part | missing_decoupling
    severity: str             # "error" | "warning"
    message: str
    refs: tuple[str, ...]     # affected refdes / net names


@dataclass(frozen=True)
class PlanErcReport:
    issues: tuple[ErcIssue, ...]

    @property
    def errors(self) -> tuple[ErcIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "error")

    @property
    def warnings(self) -> tuple[ErcIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "warning")

    @property
    def passed(self) -> bool:
        """True when there are no ERROR-severity issues (warnings are fine)."""
        return not self.errors


def _is_ground(net) -> bool:
    return bool(net.is_ground) or (net.role or "").strip().lower() == "ground"


def _is_power(net) -> bool:
    return bool(net.is_power) or (net.role or "").strip().lower() == "power"


def check_plan_erc(
    plan: DesignPlan,
    *,
    ic_pin_threshold: int = DEFAULT_IC_PIN_THRESHOLD,
) -> PlanErcReport:
    """Run the offline ERC-lite checks (see the module docstring)."""
    from eda_agent.design.motifs import _kind_from_refdes

    issues: list[ErcIssue] = []

    # --- pin / part indexes -------------------------------------------------
    pins_of: dict[str, set[str]] = {}        # refdes -> distinct pin ids
    nets_of: dict[str, set[str]] = {}        # refdes -> net names it touches
    for net in plan.nets:
        for pr in net.pins:
            pins_of.setdefault(pr.refdes, set()).add(str(pr.pin))
            nets_of.setdefault(pr.refdes, set()).add(net.name)

    ground_nets = {n.name for n in plan.nets if _is_ground(n)}

    # --- shorted pins (one pin endpoint on more than one net) ---------------
    # A physical pin connects to exactly one net; the same (refdes, pin) on two
    # nets means those nets are SHORTED at that pin. The schema only rejects a
    # duplicate pin WITHIN a net, so a cross-net short slips through to emit
    # (where Altium silently merges the nets). Catch it here as a hard error.
    pin_nets: dict[tuple[str, str], list[str]] = {}
    for net in plan.nets:
        for pr in net.pins:
            pin_nets.setdefault((pr.refdes, str(pr.pin)), []).append(net.name)
    for (refdes, pin), nets in sorted(pin_nets.items()):
        if len(set(nets)) > 1:
            shorted = ", ".join(sorted(set(nets)))
            issues.append(ErcIssue(
                code="shorted_pin", severity="error",
                message=(f"pin {refdes}.{pin} is on {len(set(nets))} nets "
                         f"({shorted}); a pin connects to exactly one net -- "
                         f"these would short"),
                refs=(refdes, *sorted(set(nets)))))

    # --- contradictory net flags (power AND ground) -------------------------
    # A net is either a power rail or ground, never both; the flags drive the
    # power-port glyph and the decoupling / class logic, so a net flagged both
    # is genuinely ambiguous. The schema does not forbid it.
    for net in sorted(plan.nets, key=lambda n: n.name):
        if net.is_power and net.is_ground:
            issues.append(ErcIssue(
                code="contradictory_net_flags", severity="error",
                message=(f"net {net.name!r} is flagged BOTH is_power and "
                         f"is_ground; a net is one or the other"),
                refs=(net.name,)))

    # --- floating nets (reach fewer than two distinct parts) ----------------
    for net in sorted(plan.nets, key=lambda n: n.name):
        parts_on = {pr.refdes for pr in net.pins}
        if len(parts_on) < 2:
            only = next(iter(parts_on), "?")
            issues.append(ErcIssue(
                code="floating_net", severity="error",
                message=(f"net {net.name!r} connects only to part {only}; it "
                         f"reaches nothing else in the design"),
                refs=(net.name,)))

    # --- shorted passives (a 2-pin R/C/L with both pins on one net) ---------
    # A two-terminal passive whose both pins land on the same net is inert: a
    # resistor shorting a net to itself, a cap across nothing. Both pins are
    # distinct so the schema passes it, and it reaches that one net so it is
    # not floating -- this is the only place it is caught. Scoped to R/C/L:
    # a connector or mounting hole with several pins on GND is legitimate.
    for part in sorted(plan.parts, key=lambda p: p.refdes):
        if _kind_from_refdes(part.refdes) not in ("R", "C", "L"):
            continue
        nets = nets_of.get(part.refdes, set())
        if len(pins_of.get(part.refdes, set())) >= 2 and len(nets) == 1:
            net = next(iter(nets))
            issues.append(ErcIssue(
                code="shorted_component", severity="error",
                message=(f"part {part.refdes} has both pins on net {net!r}; a "
                         f"2-terminal passive across a single net is inert"),
                refs=(part.refdes, net)))

    # --- unconnected parts --------------------------------------------------
    for part in sorted(plan.parts, key=lambda p: p.refdes):
        if part.refdes not in nets_of:
            issues.append(ErcIssue(
                code="unconnected_part", severity="warning",
                message=f"part {part.refdes} is not connected to any net",
                refs=(part.refdes,)))

    # --- missing decoupling -------------------------------------------------
    # A decoupling cap for power net P = a 2-pin cap whose two nets are {P, G}
    # with G a ground net. Build, per power net, the set of caps decoupling it.
    decoupled_power: set[str] = set()
    for refdes, nets in nets_of.items():
        if _kind_from_refdes(refdes) != "C":
            continue
        if len(pins_of.get(refdes, set())) != 2 or len(nets) != 2:
            continue
        grounds = nets & ground_nets
        if len(grounds) != 1:
            continue
        rail = next(iter(nets - grounds))
        decoupled_power.add(rail)

    power_net_names = {n.name for n in plan.nets if _is_power(n)}
    for part in sorted(plan.parts, key=lambda p: p.refdes):
        if len(pins_of.get(part.refdes, set())) < ic_pin_threshold:
            continue
        rails = nets_of.get(part.refdes, set()) & power_net_names
        for rail in sorted(rails):
            if rail not in decoupled_power:
                issues.append(ErcIssue(
                    code="missing_decoupling", severity="warning",
                    message=(f"IC {part.refdes} power rail {rail!r} has no "
                             f"decoupling capacitor"),
                    refs=(part.refdes, rail)))

    # --- malformed passive values ------------------------------------------
    from eda_agent.design.value_parser import try_parse_value
    for part in sorted(plan.parts, key=lambda p: p.refdes):
        value = (getattr(part, "value", None) or "").strip()
        if not value or _kind_from_refdes(part.refdes) not in ("R", "C", "L"):
            continue
        if try_parse_value(value) is None:
            issues.append(ErcIssue(
                code="malformed_value", severity="warning",
                message=(f"part {part.refdes} value {value!r} is not a "
                         f"readable engineering value"),
                refs=(part.refdes,)))

    # --- matched-value mismatches (crystal load caps, diff-pair series) -----
    # Lazy import: value_checks imports ErcIssue from this module.
    from eda_agent.design.value_checks import check_matched_values
    issues.extend(check_matched_values(plan))

    return PlanErcReport(issues=tuple(issues))


__all__ = [
    "ErcIssue",
    "PlanErcReport",
    "check_plan_erc",
    "DEFAULT_IC_PIN_THRESHOLD",
]
