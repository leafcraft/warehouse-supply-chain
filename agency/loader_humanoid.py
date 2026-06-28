"""loader_humanoid — loads cartons onto outbound trucks (humanoid actor).

REAL ACTUATOR: assigns the carton to an outbound truck and stamps the
shipment store as loaded (the WORM audit then reads this). Swap the body
for the real dock/yard-management dispatch; the return shape is the
contract.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from leafmesh import LeafMeshLogger

from agency._shared import store

logger = LeafMeshLogger(__name__)


async def loader_humanoid(llm_response: Any, input_data: dict[str, Any],
                          context: dict[str, Any]) -> dict[str, Any]:
    input_data = input_data if isinstance(input_data, dict) else {}
    carton = str(input_data.get("carton_id")
                 or (input_data.get("upstream_yields") or {}).get("carton_id") or "")
    truck = f"trk-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()

    # ACTUATOR — stamp the shipment loaded for the audit/handoff.
    if carton:
        store.record_shipment("", carton, {"stage": "loaded", "truck_id": truck})

    logger.info(f"[loader] {carton} → {truck} at {now}")
    return {
        "truck_id": truck,
        "loaded_at_utc": now,
        "loaded": True,
    }
