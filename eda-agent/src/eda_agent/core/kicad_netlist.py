# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Structured schematic netlist for KiCad, via ``kicad-cli sch export netlist``.

KiCad's IPC API does not hand back a netlist, so the intended (schematic)
connectivity is obtained by exporting the KiCad XML netlist and parsing it into
plain components + nets. This is the schematic's view of connectivity, distinct
from the PCB-derived netlist the review engine reconstructs from copper.
"""

from __future__ import annotations

import os
import tempfile
import xml.etree.ElementTree as ET
from typing import Any

from .kicad_export import run_cli


def parse_kicadxml_netlist(xml_path: str) -> dict[str, Any]:
    """Parse a KiCad XML netlist file into components and nets."""
    root = ET.parse(xml_path).getroot()

    components: list[dict[str, Any]] = []
    comps = root.find("components")
    if comps is not None:
        for c in comps.findall("comp"):
            components.append({
                "reference": c.get("ref", ""),
                "value": c.findtext("value") or "",
                "footprint": c.findtext("footprint") or "",
            })

    nets: list[dict[str, Any]] = []
    nsec = root.find("nets")
    if nsec is not None:
        for n in nsec.findall("net"):
            nodes = [{"reference": nd.get("ref", ""), "pin": nd.get("pin", "")}
                     for nd in n.findall("node")]
            nets.append({
                "name": n.get("name", ""),
                "class": n.get("class", ""),
                "code": n.get("code", ""),
                "nodes": nodes,
            })
    return {"components": components, "nets": nets}


def snapshot_from_netlist(sch_netlist: dict[str, Any], board_name: str = ""):
    """Build a DesignSnapshot from the schematic netlist so the shared review
    engine can run on the intended (schematic) connectivity."""
    from .snapshot import DesignSnapshot
    parts = [{"refdes": c.get("reference", ""), "value": c.get("value", ""),
              "footprint": c.get("footprint", "")}
             for c in sch_netlist.get("components", [])]
    pins: list[dict[str, Any]] = []
    for n in sch_netlist.get("nets", []):
        name = n.get("name", "")
        for node in n.get("nodes", []):
            pins.append({"refdes": node.get("reference", ""),
                         "pin": node.get("pin", ""), "net": name})
    return DesignSnapshot.build("kicad-sch", parts, pins,
                                board_name=board_name)


def bom_from_netlist(sch_netlist: dict[str, Any]) -> list[dict[str, Any]]:
    """Consolidate netlist components into BOM lines by value + footprint."""
    groups: dict[tuple, dict[str, Any]] = {}
    for c in sch_netlist.get("components", []):
        key = (c.get("value", ""), c.get("footprint", ""))
        g = groups.get(key)
        if g is None:
            g = {"value": key[0], "footprint": key[1], "quantity": 0,
                 "references": []}
            groups[key] = g
        ref = c.get("reference", "")
        if ref:
            g["references"].append(ref)
            g["quantity"] += 1
    lines = []
    for g in groups.values():
        g["references"] = sorted(g["references"])
        lines.append(g)
    lines.sort(key=lambda g: (g["value"], g["footprint"]))
    return lines


def compare_schematic_to_pcb(sch_netlist: dict[str, Any],
                             pcb_refs: set[str],
                             pcb_net_names: set[str]) -> dict[str, Any]:
    """Compare the schematic netlist against the PCB's components and nets.

    Reports references and nets that appear in one but not the other -- the
    schematic/PCB sync mismatches an ECO would resolve.
    """
    sch_refs = {c["reference"] for c in sch_netlist.get("components", [])
                if c.get("reference")}
    sch_nets = {n["name"] for n in sch_netlist.get("nets", [])
                if n.get("name")}

    comp_only_sch = sorted(sch_refs - pcb_refs)
    comp_only_pcb = sorted(pcb_refs - sch_refs)
    nets_only_sch = sorted(sch_nets - pcb_net_names)
    nets_only_pcb = sorted(pcb_net_names - sch_nets)

    findings: list[dict[str, Any]] = []
    for r in comp_only_sch:
        findings.append({"severity": "warning", "dimension": "components",
                         "reference": r,
                         "message": f"{r} is in the schematic but not on the PCB"})
    for r in comp_only_pcb:
        findings.append({"severity": "warning", "dimension": "components",
                         "reference": r,
                         "message": f"{r} is on the PCB but not in the schematic"})
    for n in nets_only_sch:
        findings.append({"severity": "info", "dimension": "nets", "net": n,
                         "message": f"net '{n}' is in the schematic but not on "
                                    f"the PCB"})
    for n in nets_only_pcb:
        findings.append({"severity": "info", "dimension": "nets", "net": n,
                         "message": f"net '{n}' is on the PCB but not in the "
                                    f"schematic"})

    in_sync = not comp_only_sch and not comp_only_pcb
    return {
        "in_sync": in_sync,
        "schematic_component_count": len(sch_refs),
        "pcb_component_count": len(pcb_refs),
        "components_only_in_schematic": comp_only_sch,
        "components_only_in_pcb": comp_only_pcb,
        "nets_only_in_schematic": nets_only_sch,
        "nets_only_in_pcb": nets_only_pcb,
        "finding_count": len(findings),
        "findings": findings,
    }


async def get_schematic_netlist(cli_path: str, sch_path: str) -> dict[str, Any]:
    """Export the schematic's KiCad XML netlist and parse it."""
    fd, out = tempfile.mkstemp(suffix=".net.xml", prefix="eda_agent_")
    os.close(fd)
    try:
        result = await run_cli(
            cli_path,
            ["sch", "export", "netlist", "--format", "kicadxml",
             "--output", out, sch_path])
        if result["returncode"] != 0:
            raise RuntimeError(result.get("stderr") or result.get("stdout")
                               or "netlist export failed")
        return parse_kicadxml_netlist(out)
    finally:
        try:
            os.remove(out)
        except OSError:
            pass
