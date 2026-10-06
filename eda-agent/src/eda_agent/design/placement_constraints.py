# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""One entry point for the role-driven PCB placement constraints.

Several modules each derive a different placement constraint from the
planner-asserted :class:`~eda_agent.design.plan.Net` roles:

* :func:`~eda_agent.design.diffpairs.diff_pair_match_groups` -- a differential
  pair's matched series elements share a ``match_group`` (kept together / on a
  common axis).
* :func:`~eda_agent.design.mixed_signal.infer_keepout_groups` -- analog and
  digital parts get opposing ``keepout_group`` tags (pushed apart).

This module bundles them so a caller derives BOTH from one plan in one call,
and provides the merge rule for combining inferred tags with any the planner
supplied explicitly (explicit always wins). ``match_group`` and
``keepout_group`` are independent attributes on a part, so a diff pair's series
resistor can be both kept-with-its-mate AND separated-from-analog at once --
the two dicts never conflict with each other, only an explicit override of the
same kind replaces an inferred tag.

Pure Python, no Altium. NDA scope: only the current plan's topology.
"""

from __future__ import annotations

from dataclasses import dataclass

from eda_agent.design.diffpairs import diff_pair_match_groups
from eda_agent.design.mixed_signal import infer_keepout_groups
from eda_agent.design.plan import DesignPlan


@dataclass(frozen=True)
class PlacementConstraints:
    """The role-derived constraints for one plan, each ``{refdes: group}``."""

    match_groups: dict[str, str]
    keepout_groups: dict[str, str]

    def is_empty(self) -> bool:
        return not self.match_groups and not self.keepout_groups


def infer_placement_constraints(plan: DesignPlan) -> PlacementConstraints:
    """Derive every role-driven placement constraint from ``plan``.

    Returns matched-pair ``match_groups`` (differential pairs) and mixed-signal
    ``keepout_groups`` (analog vs digital). Either may be empty when the plan
    has no differential nets / no mixed-signal split, so a plain single-domain
    design yields empty constraints and is unaffected.
    """
    return PlacementConstraints(
        match_groups=diff_pair_match_groups(plan),
        keepout_groups=infer_keepout_groups(plan),
    )


def merge_groups(
    inferred: dict[str, str],
    explicit: dict[str, str] | None,
) -> dict[str, str]:
    """Combine inferred tags with planner-supplied ones; explicit wins on a
    per-refdes conflict (the planner's intent overrides the inference)."""
    out = dict(inferred)
    out.update(explicit or {})
    return out


__all__ = [
    "PlacementConstraints",
    "infer_placement_constraints",
    "merge_groups",
]
