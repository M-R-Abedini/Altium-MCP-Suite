# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Design agent primitives, exposed to Claude Code via MCP tools.

The MCP server provides the building blocks:

* ``design.get_discipline`` returns the design rules + schema reference.
* ``design.snapshot_inventory`` opens SchLibs and reports what parts exist.
* ``design.validate_plan`` runs schema + cross-check on a candidate plan.
* ``design.execute_plan`` instantiates a plan in Altium (Slice B).
* ``design.validate`` produces an ERC + connectivity sanity report (Slice C).

Claude Code is the planner. It reads the discipline, reads the inventory,
emits a DesignPlan JSON, hands it to the executor, reads the validator's
report, iterates. No Anthropic API calls happen from within this package.

NDA scope: the planner's corpus is constrained to (1) public material
(datasheets, manufacturer reference designs, app notes); (2) the user's
neutral library / template artefacts; (3) the current project only.
Cross-project reads are forbidden.
"""

from eda_agent.design.plan import (
    BomLine,
    DesignPlan,
    DesignRuleDelta,
    Net,
    Part,
    PartStatus,
    PinRef,
    Sheet,
    Zone,
)

__all__ = [
    "BomLine",
    "DesignPlan",
    "DesignRuleDelta",
    "Net",
    "Part",
    "PartStatus",
    "PinRef",
    "Sheet",
    "Zone",
]
