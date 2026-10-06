# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""EDA-agnostic offline plan analysis.

Validating and reviewing a ``DesignPlan`` is pure Python over the plan JSON --
no EDA involved. The Altium backend registers these via ``register_design_tools``
(alongside its bridge-backed authoring tools); this module gives the KiCad
backend the same plan-level analysis (schema + ERC-lite validation, and the
one-call review that bundles stats, ERC, circuit behaviour, placement
constraints and net classes), so both backends are plan-analysis-equivalent.
"""

from __future__ import annotations

import json
from typing import Any, Union


def register_plan_tools(mcp) -> None:

    @mcp.tool()
    async def design_validate_plan(plan_json: Union[str, dict]) -> dict[str, Any]:
        """Validate a candidate DesignPlan JSON against the schema, cross-check
        references, and run offline ERC-lite. Returns ``{"ok": True,
        "summary": ...}`` or ``{"ok": False, "errors": [...]}``."""
        from pydantic import ValidationError
        from ..design.plan import DesignPlan
        from ..design.plan_erc import check_plan_erc

        if isinstance(plan_json, dict):
            payload = plan_json
        else:
            try:
                payload = json.loads(plan_json)
            except json.JSONDecodeError as exc:
                return {"ok": False, "errors": [f"invalid JSON: {exc}"]}
        try:
            plan = DesignPlan.model_validate(payload)
        except ValidationError as exc:
            return {"ok": False, "errors": [
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                for e in exc.errors()]}

        cross = plan.cross_check()
        if cross:
            return {"ok": False, "errors": cross}

        erc = check_plan_erc(plan)
        erc_payload = {
            "passed": erc.passed,
            "errors": [{"code": i.code, "message": i.message,
                        "refs": list(i.refs)} for i in erc.errors],
            "warnings": [{"code": i.code, "message": i.message,
                          "refs": list(i.refs)} for i in erc.warnings],
        }
        if not erc.passed:
            return {"ok": False, "errors": [i.message for i in erc.errors],
                    "erc": erc_payload}
        return {"ok": True,
                "summary": (f"Plan valid. {len(plan.parts)} parts, "
                            f"{len(plan.nets)} nets, {len(plan.sheets)} "
                            f"sheet(s). ERC: {len(erc.warnings)} warning(s)."),
                "erc": erc_payload}

    @mcp.tool()
    async def design_review_plan(plan_json: Union[str, dict]) -> dict[str, Any]:
        """One-call offline pre-flight for a DesignPlan: bundles stats, ERC-lite,
        recognised circuit behaviour, auto-derived placement constraints, and
        net classes -- everything the planner would otherwise call one by one."""
        from pydantic import ValidationError
        from ..design.plan import DesignPlan
        from ..design.plan_stats import summarize_plan
        from ..design.plan_erc import check_plan_erc
        from ..design.placement_constraints import infer_placement_constraints
        from ..design.motif_descriptions import describe_motifs
        from ..design.net_classes import classify_nets

        if isinstance(plan_json, dict):
            payload = plan_json
        else:
            try:
                payload = json.loads(plan_json)
            except json.JSONDecodeError as exc:
                return {"ok": False, "error": f"invalid JSON: {exc}"}
        try:
            plan = DesignPlan.model_validate(payload)
        except ValidationError as exc:
            return {"ok": False, "errors": [
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                for e in exc.errors()]}

        stats = summarize_plan(plan)
        erc = check_plan_erc(plan)
        constraints = infer_placement_constraints(plan)
        return {
            "ok": True,
            "passed": erc.passed,
            "stats": {
                "part_count": stats.part_count, "net_count": stats.net_count,
                "parts_by_kind": stats.parts_by_kind,
                "ic_count": stats.ic_count,
                "passive_count": stats.passive_count,
                "power_rails": list(stats.power_rails),
                "ground_nets": list(stats.ground_nets),
                "avg_net_degree": round(stats.avg_net_degree, 2),
                "highest_fanout_signal": list(stats.highest_fanout_signal)
                if stats.highest_fanout_signal else None,
            },
            "erc": {
                "passed": erc.passed,
                "errors": [{"code": i.code, "message": i.message,
                            "refs": list(i.refs)} for i in erc.errors],
                "warnings": [{"code": i.code, "message": i.message,
                              "refs": list(i.refs)} for i in erc.warnings],
            },
            "circuits": [{"motif": d.motif_name, "parts": list(d.parts),
                          "summary": d.summary, "params": d.params}
                         for d in describe_motifs(plan)],
            "placement_constraints": {
                "match_groups": constraints.match_groups,
                "keepout_groups": constraints.keepout_groups,
            },
            "net_classes": {cls: list(nets) for cls, nets
                            in classify_nets(plan).groups.items()},
        }
