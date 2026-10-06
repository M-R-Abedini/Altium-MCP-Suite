# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Schematic / PCB rendering tools.

Independent of any third-party Altium parser. Geometry is pulled from
the running Altium session via the existing DelphiScript bridge, then
rendered to SVG in pure Python. Output is interactive-ready (``data-*``
attributes on every group) so a dashboard or LLM client can hook
hover / click / cross-probe events without re-parsing.

Shipped surface:
  - ``sch_render_svg`` : active SchDoc -> SVG file.
  - ``pcb_render_svg`` : per-layer PCB rendering.
  - ``design_visual_review``: render + rasterize to PNG + a layout
    critique rubric (the visual self-check loop).

Renders are GEOMETRY ONLY -- a visual artifact for layout/placement
review or when an image is genuinely needed. They are NEVER the basis
for judging connectivity or part values; that comes from the netlist
(see each tool's docstring and the design discipline).

Still deferred: full symbol-internal fidelity (the schematic renderer
draws labelled bounding-box bodies with pin stubs, not the symbol art).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..bridge import get_bridge
from ..config import get_config
from ..render import (
    render_sch_svg, SchRenderOptions,
    render_pcb_svg, PcbRenderOptions,
    rasterize_svg, size_svg_for_raster, visual_review_guidance,
)
from ..render.bom_html import render_bom_html


def _renders_dir() -> Path:
    """Transient render-output directory, kept OUT of the workspace root so
    renders don't pile up in (any) user's folder. Created on demand."""
    d = get_config().workspace_dir / "renders"
    d.mkdir(parents=True, exist_ok=True)
    return d


def register_render_tools(mcp):
    """Register rendering tools with the MCP server."""

    @mcp.tool()
    async def sch_render_svg(
        output_path: Optional[str] = None,
        margin_mils: int = 200,
        flip_y: bool = True,
    ) -> dict[str, Any]:
        """Render the active SchDoc to an SVG file (in-house renderer).

        SCOPE: this is a VISUAL artifact for when an image is genuinely
        needed (layout/readability review, sharing a picture, a CI
        thumbnail). It is NOT an input to a design review. NEVER judge
        connectivity, part values, ratings, or pin functions from this
        render -- a wire that looks joined may not share a net, and a
        net can be correct while the drawing looks messy. Connectivity
        and part facts come from the compiled netlist + part data
        (`proj_get_nets`, `proj_get_connectivity_many`, `obj_crossref_net`,
        `proj_get_unconnected_pins`, `proj_get_erc_violations`,
        `design_review_snapshot`), never from a picture.

        Pulls every component, pin, wire, junction, net label, port and
        power port from the active schematic via the bridge, then emits
        a self-contained SVG with ``data-designator``, ``data-net``,
        ``data-pin`` attributes on every meaningful group so a downstream
        consumer (the web dashboard, an LLM tool, a CI check) can hook
        interaction without re-parsing.

        v1 draws components as labelled bounding-box bodies with pin
        stubs from the body to the electrical end. Full symbol-internal
        primitives (rectangles / lines / arcs inside each symbol) are
        deferred to v2 -- the v1 output is recognisable and ready for
        interactive overlay but does not yet draw the actual symbol art.

        Args:
            output_path: Where to write the SVG. Defaults to the
                transient ``<workspace>/renders/`` dir (kept out of the
                workspace root so renders don't pile up in your folder).
            margin_mils: Padding around the geometry bbox in mils
                (default 200).
            flip_y: When True (default) flip the Y axis so the output
                reads in Altium's conventional bottom-left-origin space
                rather than SVG's top-left-origin.

        Returns:
            Dict with ``ok``, ``svg_path``, ``doc``, ``counts``
            (one entry per primitive type).
        """
        bridge = get_bridge()
        geometry = await bridge.send_command_async(
            "generic.get_sch_geometry", {}, timeout=60.0,
        )
        if not isinstance(geometry, dict):
            return {"ok": False, "reason": "no geometry returned"}
        opts = SchRenderOptions(margin=margin_mils, flip_y=flip_y)
        svg = render_sch_svg(geometry, opts)

        doc = geometry.get("doc") or "schematic"
        # Strip directory components if Altium handed back a full path;
        # we write under the workspace by default.
        doc_name = Path(doc).name or "schematic"
        if not doc_name.lower().endswith(".svg"):
            doc_name = (
                Path(doc_name).with_suffix(".svg").name
                if doc_name else "schematic.svg"
            )

        if output_path:
            target = Path(output_path)
        else:
            target = _renders_dir() / doc_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(svg, encoding="utf-8")

        return {
            "ok": True,
            "svg_path": str(target),
            "doc": geometry.get("doc"),
            "counts": geometry.get("counts") or {},
            "bytes": len(svg),
        }

    @mcp.tool()
    async def pcb_render_svg(
        output_path: Optional[str] = None,
        layers: Optional[list[str]] = None,
        margin_mils: int = 250,
        flip_y: bool = True,
        show_drills: bool = True,
        show_texts: bool = True,
        show_designators: bool = True,
        interactive_legend: bool = True,
        background: str = "#1f2937",
    ) -> dict[str, Any]:
        """Render the active PcbDoc to an SVG file (in-house renderer).

        SCOPE: a VISUAL artifact for when an image is genuinely needed
        (placement / silkscreen / spacing review, sharing a picture).
        It is NOT an input to a design review's electrical findings.
        NEVER judge connectivity, net assignment, or clearance from this
        render -- read those from the board data (`pcb_get_nets`,
        `pcb_get_unrouted_nets`, `pcb_get_clearance_violations`,
        `pcb_run_drc`, `proj_compare_sch_pcb`). The render shows
        geometry; the netlist shows connection.

        Pulls the board outline, every track, arc, pad, via, and text
        primitive from the active PCB via the bridge, then emits a
        per-layer SVG. Layers are grouped in render order (KeepOut at
        bottom, then bottom copper, top copper, overlays, outline on
        top, drills last) and every track / arc / pad / via group
        carries ``data-net``, ``data-layer``, ``data-shape`` so a
        downstream dashboard or LLM tool can cross-probe or net-
        highlight without re-parsing.

        v1 surface: board outline + tracks + arcs + pads (round / rect /
        octagonal / rounded-rect) + vias + texts on overlays. Regions
        (polygon fills / copper pours) and component-body silkscreen
        outlines are deferred to v1.1.

        Args:
            output_path: Where to write the SVG. Defaults to the
                transient ``<workspace>/renders/`` dir (kept out of the
                workspace root so renders don't pile up in your folder).
            layers: Restrict to these Altium layer names (e.g.
                ``["TopLayer", "TopOverlay", "KeepOutLayer"]``). When
                ``None`` (default) every known layer is rendered.
                ``MultiLayer`` (vias) and the outline are always drawn.
            margin_mils: Padding around the board bbox in mils.
            flip_y: When True (default) flip Y so the SVG reads in
                Altium's bottom-left-origin space.
            show_drills: Punch hole circles through pads / vias.
            show_texts: Render text primitives on overlay layers.
            show_designators: Draw component designators on the overlay.
            interactive_legend: Emit a clickable per-layer legend group
                in the SVG (for the dashboard / cross-probe consumers).
            background: Board-background fill (defaults to a slate
                review colour; pass ``"none"`` for transparent).

        Returns:
            Dict with ``ok``, ``svg_path``, ``counts`` (per primitive
            kind), ``bbox`` (in mils), ``bytes``.
        """
        bridge = get_bridge()
        geometry = await bridge.send_command_async(
            "generic.get_pcb_geometry", {}, timeout=120.0,
        )
        if not isinstance(geometry, dict):
            return {"ok": False, "reason": "no geometry returned"}

        opts = PcbRenderOptions(
            margin_mils=margin_mils, flip_y=flip_y, layers=layers,
            background=background, show_drills=show_drills,
            show_texts=show_texts, show_designators=show_designators,
            interactive_legend=interactive_legend,
        )
        svg = render_pcb_svg(geometry, opts)

        if output_path:
            target = Path(output_path)
        else:
            target = _renders_dir() / "pcb.svg"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(svg, encoding="utf-8")

        return {
            "ok": True,
            "svg_path": str(target),
            "counts": geometry.get("counts") or {},
            "bbox": geometry.get("bbox") or {},
            "bytes": len(svg),
        }

    @mcp.tool()
    async def design_visual_review(
        target: str = "auto",
        output_path: Optional[str] = None,
        rasterize: bool = True,
        width: int = 1600,
        margin_mils: Optional[int] = None,
        layers: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Render the active design and return it as a viewable image plus
        a critique rubric -- the first step of a visual self-check loop.

        SCOPE: layout and placement ONLY -- schematic readability /
        spread / flow, PCB part placement, silkscreen, spacing, overlaps.
        This is NOT a connectivity or part-correctness review. Do NOT use
        it to decide what connects to what, whether a value/rating is
        right, or whether a pin is wired -- those come from the netlist +
        part data (`design_review_snapshot`, `proj_get_nets`,
        `proj_get_connectivity_many`, `proj_get_erc_violations`), never
        from the image. The rubric below is deliberately about geometry;
        treat every electrical question as out of scope here.

        Renderers are exports; this turns them into *feedback*. It draws
        the active SchDoc or PcbDoc, rasterizes it to PNG (headless Edge)
        so you can actually look at it with the Read tool, and returns a
        document-specific ``rubric`` (what to look for) and
        ``loop_protocol`` (render -> look -> critique -> cross-check ->
        fix -> re-render). Each rubric item names the existing audit
        tool(s) that measure it exactly, so vision finds candidates and
        the audits confirm and locate them.

        Use it as a loop: call this, ``Read`` the returned ``png_path``,
        critique against ``rubric``, run the named audits for anything
        suspicious, fix with the normal tools (for PCB placement,
        ``pcb_plan_placement``), then call this again to confirm. A couple
        of iterations is usually enough.

        Args:
            target: ``"auto"`` (detect the active document), ``"schematic"``,
                or ``"pcb"``.
            output_path: Where to write the SVG (``.png`` sits beside it).
                Defaults to the transient ``<workspace>/renders/`` dir; the
                scratch ``.raster.svg`` used for the screenshot is deleted
                automatically (kept out of your folder).
            rasterize: Also produce a PNG so the image is directly
                viewable (default True). If no browser is found the SVG is
                still returned with a ``rasterize_note``.
            width: Raster width in pixels (height follows the aspect).
            margin_mils: Override the render margin.
            layers: PCB only -- restrict to these layer names.

        Returns:
            Dict with ``ok``, ``target``, ``doc``, ``svg_path``,
            ``png_path`` (None if rasterization was skipped/failed),
            ``counts``, ``bbox``, ``rubric``, ``loop_protocol``,
            ``next_step``.
        """
        bridge = get_bridge()
        t = (target or "auto").strip().lower()
        if t in ("auto", ""):
            info = await bridge.send_command_async(
                "application.get_active_document")
            kind = ""
            if isinstance(info, dict):
                kind = str(info.get("document_kind")
                           or info.get("file_name") or "").lower()
            # Libraries FIRST. DM_DocumentKind spells them 'PCBLIB' and
            # 'SCHLIB', so a plain substring test reads "pcb" out of
            # "pcblib" and sends a PcbLib down the PcbDoc geometry path,
            # where the user gets an obscure geometry error instead of
            # being told the active document is a library. This is not a
            # rare state: every library-authoring call leaves a SchLib or
            # PcbLib active, which is exactly when someone renders to
            # check their work.
            if "lib" in kind:
                return {
                    "ok": False,
                    "reason": f"the active document is a LIBRARY "
                    f"({kind or 'unknown'}), not a schematic or board. "
                    f"Open the .SchDoc / .PcbDoc you want to review, or "
                    f"pass target='schematic'/'pcb' explicitly to try "
                    f"anyway.",
                    "active_document": info,
                }
            if "pcb" in kind:
                t = "pcb"
            elif "sch" in kind:
                t = "schematic"
            else:
                return {
                    "ok": False,
                    "reason": "could not detect the active document kind; "
                    "pass target='schematic' or target='pcb'",
                    "active_document": info,
                }
        if t in ("sch", "schdoc"):
            t = "schematic"
        if t in ("pcbdoc", "board"):
            t = "pcb"
        if t not in ("schematic", "pcb"):
            return {"ok": False,
                    "reason": f"target must be schematic or pcb (got {target!r})"}

        if t == "schematic":
            geometry = await bridge.send_command_async(
                "generic.get_sch_geometry", {}, timeout=60.0)
            if not isinstance(geometry, dict):
                return {"ok": False, "reason": "no schematic geometry returned"}
            opts = SchRenderOptions(
                margin=margin_mils if margin_mils is not None else 200)
            svg = render_sch_svg(geometry, opts)
            doc = geometry.get("doc") or "schematic"
            default_name = (Path(str(doc)).with_suffix(".review.svg").name
                            or "schematic.review.svg")
        else:
            geometry = await bridge.send_command_async(
                "generic.get_pcb_geometry", {}, timeout=120.0)
            if not isinstance(geometry, dict):
                return {"ok": False, "reason": "no PCB geometry returned"}
            # No interactive legend: its foreignObject HTML is not
            # well-formed XML, which breaks standalone-SVG rasterization.
            # A static critique image does not need the checkboxes anyway.
            opts = PcbRenderOptions(
                margin_mils=margin_mils if margin_mils is not None else 250,
                layers=layers, interactive_legend=False)
            svg = render_pcb_svg(geometry, opts)
            doc = geometry.get("doc") or "pcb"
            default_name = "pcb.review.svg"

        if output_path:
            svg_target = Path(output_path)
            if svg_target.suffix.lower() != ".svg":
                svg_target = svg_target.with_suffix(".svg")
        else:
            svg_target = _renders_dir() / default_name
        svg_target.parent.mkdir(parents=True, exist_ok=True)
        svg_target.write_text(svg, encoding="utf-8")

        result: dict[str, Any] = {
            "ok": True,
            "target": t,
            "doc": geometry.get("doc"),
            "svg_path": str(svg_target),
            "png_path": None,
            "counts": geometry.get("counts") or {},
            "bbox": geometry.get("bbox") or {},
        }

        if rasterize:
            # Give the responsive SVG explicit pixel dims, write a sized
            # copy, and screenshot it. Best-effort: a failure leaves the
            # SVG as the fallback artifact.
            sized, w_px, h_px = size_svg_for_raster(svg, target_width=int(width))
            raster_svg = svg_target.with_suffix(".raster.svg")
            raster_svg.write_text(sized, encoding="utf-8")
            png_target = str(svg_target.with_suffix(".png"))
            rr = rasterize_svg(str(raster_svg), png_target, width=w_px, height=h_px)
            # The sized copy is pure scratch for the screenshot -- never leave
            # it cluttering the user's folder.
            try:
                raster_svg.unlink()
            except OSError:
                pass
            if rr.get("ok"):
                result["png_path"] = rr["png_path"]
            else:
                result["rasterize_note"] = rr.get("reason")

        result.update(visual_review_guidance(t))
        viewable = result["png_path"] or result["svg_path"]
        result["next_step"] = (
            f"Read {viewable} and critique it against `rubric`, then work "
            "through `loop_protocol`."
        )
        return result

    @mcp.tool()
    async def proj_export_bom_html(
        output_path: Optional[str] = None,
        title: str = "Bill of Materials",
        limit: int = 5000,
    ) -> dict[str, Any]:
        """Export the project BOM as a self-contained interactive HTML file.

        Standalone HTML page, no external CSS / JS / Altium runtime
        dependency. Open it in any browser; sortable columns, free-text
        filter, toggle between grouped (one row per value+footprint) and
        per-component layouts. A self-contained one-shot static export.

        Useful for emailing the BOM to a manufacturer, archiving with a
        board release, or sharing with a non-Altium reviewer.

        Args:
            output_path: Where to write the HTML. Defaults to
                ``<workspace>/bom.html``.
            title: Heading + ``<title>`` for the page.
            limit: Max components to include (default 5000; the BOM call
                supports up to ~10000 cleanly).

        Returns:
            Dict with ``ok``, ``html_path``, ``components`` (count
            written), ``bytes``.
        """
        bridge = get_bridge()
        bom = await bridge.send_command_async(
            "project.get_bom",
            {"limit": str(limit)},
        )
        if not isinstance(bom, dict):
            return {"ok": False, "reason": "no BOM returned"}

        # Resolve project name for the sub-heading -- best-effort, the
        # focused-project lookup isn't critical for the export to work.
        project_name = ""
        try:
            focused = await bridge.send_command_async(
                "project.get_focused", {})
            if isinstance(focused, dict):
                project_name = (focused.get("project_name")
                                or focused.get("name") or "")
        except Exception:
            pass

        html_str = render_bom_html(bom, title=title, project=project_name)

        if output_path:
            target = Path(output_path)
        else:
            target = get_config().workspace_dir / "bom.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html_str, encoding="utf-8")

        return {
            "ok": True,
            "html_path": str(target),
            "components": len(bom.get("components") or []),
            "bytes": len(html_str),
        }
