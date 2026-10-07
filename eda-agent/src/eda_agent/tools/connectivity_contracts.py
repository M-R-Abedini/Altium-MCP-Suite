# SPDX-License-Identifier: MIT
"""Read-only verification over explicit Altium connectivity payloads."""
from typing import Any, Optional

from ..design import connectivity_contracts as checks


def _run(fn, *args):
    try:
        return fn(*args)
    except ValueError as exc:
        return {'ok': False, 'status': 'invalid_input', 'reason': str(exc)}


def register_connectivity_contract_tools(mcp):
    @mcp.tool()
    async def design_connectivity_snapshot(data: dict[str, Any], complete: bool = False) -> dict[str, Any]:
        """Canonicalize proj_get_nets pins into a hashable snapshot. Set complete only for a full, fresh, unfiltered capture below its limit. Preserves full net names; never compiles or saves documents."""
        return _run(checks.snapshot, data, complete)

    @mcp.tool()
    async def design_check_pin_contracts(actual: dict[str, Any], contracts: list[dict[str, Any]],
                                       require_all_pins: bool = False) -> dict[str, Any]:
        """Check snapshot pins against {component,pin,net} contracts or {component,pin,nc:true,reason}. Optional full pin coverage. Incomplete captures cannot pass. Validates connectivity only, not ERC or freshness."""
        return _run(checks.check_contracts, actual, contracts, require_all_pins)

    @mcp.tool()
    async def design_diff_connectivity(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        """Compare connectivity snapshots; report added/removed pins and exact net changes. Full hierarchical names are retained. A successful comparison does not approve its changes or prove hardware correctness."""
        return _run(checks.diff, before, after)

    @mcp.tool()
    async def design_check_schematic_pcb_parity(schematic: dict[str, Any], board: dict[str, Any],
                                              waivers: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        """Compare schematic snapshot with {components:[pcb_get_component_pads results],complete:true}. Waivers require component,pin,code,reason and expected net (default empty). Shared-pin net mismatches cannot be waived. Incomplete input cannot pass."""
        return _run(checks.parity, schematic, board, waivers)
