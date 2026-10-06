# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Routing-stage analysis, repair planning, and the Manhattan router
(offline, no Altium IPC).

The router half (:mod:`~eda_agent.route.model` +
:mod:`~eda_agent.route.router`) is grid A* over the board geometry dict
the bridge returns (the ``Gen_GetPcbGeometry`` shape that
:mod:`eda_agent.render.pcb_svg` also consumes). It emits track / via
dicts whose keys match the ``pcb_place_tracks`` / ``pcb_place_via`` MCP
tool parameters, so the result can be pushed to Altium verbatim. All
coordinates are MILS, integers on the wire.
"""

from eda_agent.route.model import (
    DEFAULT_GRID_PITCH_MILS,
    RouteRules,
    RoutingProblem,
    Terminal,
    rules_from_dict,
)
from eda_agent.route.router import (
    RouterOptions,
    route_geometry,
    route_problem,
    validate_solution,
)

__all__ = [
    "DEFAULT_GRID_PITCH_MILS",
    "RouteRules",
    "RouterOptions",
    "RoutingProblem",
    "Terminal",
    "route_geometry",
    "route_problem",
    "rules_from_dict",
    "validate_solution",
]
