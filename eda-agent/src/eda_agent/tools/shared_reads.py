# Modified by M-R-Abedini; see root MODIFICATIONS.md.
# SPDX-License-Identifier: Apache-2.0
"""Suite-only snapshot orchestration; standalone upstream fails explicitly."""
import asyncio
from typing import Any, Optional
from ..bridge import get_bridge


def register_shared_read_tools(mcp):
    @mcp.tool()
    async def pcb_capture_snapshot(board_path: str) -> dict[str, Any]:
        """Capture components, pad assignments, nets and physical counts in one native handler.

        Requires an absolute path to a loaded PcbDoc and the suite's eda_stdio
        wrapper. Returns an immutable snapshot ID and counts; use
        design_read_snapshot or design_analyze_snapshot concurrently afterward.
        Every capture reads Altium anew. A snapshot is historical evidence,
        never proof of live state, copper connectivity or permission to edit.
        """
        from pathlib import Path
        if not Path(board_path).is_absolute() or not board_path.lower().endswith('.pcbdoc'):
            return {'ok': False, 'reason': 'An absolute loaded PcbDoc path is required'}
        bridge = get_bridge()
        if not getattr(bridge, 'suite_shared_reads', False):
            return {'ok': False, 'reason': 'This operation requires the coordinated suite eda_stdio entry point'}
        return await bridge.send_command_async('suite.capture_snapshot', {'board_path': board_path}, timeout=60)

    @mcp.tool()
    async def design_read_snapshot(snapshot_id: str, sections: Optional[list[str]] = None) -> dict[str, Any]:
        """Read an existing immutable snapshot without accessing Altium or its queue.

        sections: counts (default), components, pads, nets. Several requested
        sections are returned together. Data is historical even if captured
        moments ago; GUI edits may occur without a reliable Modified flag.
        """
        try:
            import suite_reads
            envelope = await asyncio.to_thread(suite_reads.read, snapshot_id)
            return dict(snapshot_id=snapshot_id, **suite_reads.select_sections(envelope, sections if sections is not None else ['counts']))
        except (ImportError, OSError, ValueError) as error:
            return {'ok': False, 'reason': str(error)}

    @mcp.tool()
    async def design_analyze_snapshot(snapshot_id: str, checks: Optional[list[str]] = None) -> dict[str, Any]:
        """Analyze physical inventory, pad assignments and grouped BOM outside Altium.

        checks: inventory, pad_assignments, bom (all by default). Work is
        offloaded from the MCP event loop and independent clients can analyze
        the same snapshot simultaneously. Python threads do not guarantee CPU
        speedup. This reports recorded pad assignments, not routing/DRC or
        schematic BOM/variant fitted state. Never uses a snapshot to edit.
        """
        try:
            import suite_reads
            selected = checks if checks is not None else list(suite_reads.CHECKS)
            if not selected or len(selected) != len(set(selected)) or any(c not in suite_reads.CHECKS for c in selected):
                raise ValueError('checks must be unique names from: ' + ', '.join(suite_reads.CHECKS))
            envelope = await asyncio.to_thread(suite_reads.read, snapshot_id)
            results = await asyncio.gather(*(asyncio.to_thread(suite_reads.analyze, envelope, c) for c in selected))
            return dict(ok=True, snapshot_id=snapshot_id, live=False,
                        captured_at=envelope['captured_at'],
                        suitable_as_write_precondition=False,
                        analysis=dict(zip(selected, results)))
        except (ImportError, OSError, ValueError) as error:
            return {'ok': False, 'reason': str(error)}

    @mcp.tool()
    async def app_queue_status() -> dict[str, Any]:
        """Read local shared engine queue depth without launching Altium or a service.

        queued counts foreground admission tickets, active means a granted
        client connection; it does not assert that a native script is running.
        """
        try:
            import engine_queue
            return dict(ok=True, **await asyncio.to_thread(engine_queue.status))
        except (ImportError, OSError, ValueError, RuntimeError) as error:
            return {'ok': False, 'reason': str(error)}
