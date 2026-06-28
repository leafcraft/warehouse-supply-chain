"""humanoid_status_agent — humanoid fleet telemetry (battery / fault / location).

Store-grounded for day-0: reads the seeded fleet roster from
agency/_shared/store.py and returns the aggregate telemetry contract. In
PROD swap the body for the fleet-API read — point ${FLEET_API_MCP_URL} at
your robot fleet-management gateway (tool get_fleet_status) returning the
same yields. The fault-alert edge (→ ops_supervisor_human) fires off the
returned fault_alerts list either way.
"""
from __future__ import annotations

from typing import Any

from leafmesh import LeafMeshLogger

from agency._shared import store

logger = LeafMeshLogger(__name__)


async def humanoid_status_agent(llm_response: Any, input_data: dict[str, Any],
                                context: dict[str, Any]) -> dict[str, Any]:
    status = store.fleet_status()
    logger.info(
        f"[fleet] units={status['total_units']} offline={status['offline_units']} "
        f"low_batt={status['low_battery_units']} faults={len(status['fault_alerts'])}"
    )
    return status
