"""packer_humanoid — packs into a shipping carton (humanoid actor).

DEEP (actuator + deterministic ship-readiness floor):

@chain        enforce_ship_ready — the deterministic SHIP-READINESS floor
              (agency/_shared/allocation_math): a carton is only marked
              packed/ship-ready when units were actually packed and the
              weight is known. The LLM/body cannot present an empty or
              weightless carton as ship-ready.

REAL ACTUATOR  the pack itself + recording the carton into the shipment
              store (the handoff loader/carrier read).
"""
from __future__ import annotations

import uuid
from typing import Any

from leafmesh import LeafMeshLogger, chain

from agency._shared import allocation_math, store

logger = LeafMeshLogger(__name__)

AVG_UNIT_KG = 0.5   # dev average; prod reads per-SKU weight from the WMS


# ── @chain — the deterministic SHIP-READINESS floor (LLM can't waive) ─
async def enforce_ship_ready(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        result = {}
    ready, reasons = allocation_math.ship_ready(
        packed=bool(result.get("packed")),
        weight_kg=float(result.get("weight_kg") or 0),
        lines_expected=1,
        units_packed=int(result.get("_units") or 0),
    )
    result["packed"] = ready
    if not ready:
        result["ship_block_reasons"] = reasons
        logger.warning(f"[packer] SHIP-READINESS floor blocked carton: {reasons}")
    result.pop("_units", None)
    return result


@chain(enforce_ship_ready)
async def packer_humanoid(llm_response: Any, input_data: dict[str, Any],
                          context: dict[str, Any]) -> dict[str, Any]:
    input_data = input_data if isinstance(input_data, dict) else {}
    units = int(input_data.get("picked_units")
                or (input_data.get("upstream_yields") or {}).get("picked_units")
                or 0)
    carton_id = f"ctn-{uuid.uuid4().hex[:10]}"
    weight = round(units * AVG_UNIT_KG, 2)
    order_id = str(input_data.get("order_id")
                   or (input_data.get("upstream_yields") or {}).get("order_id") or "")

    # ACTUATOR — record the carton so loader/carrier can read it.
    store.record_shipment(order_id, carton_id,
                          {"weight_kg": weight, "units": units, "stage": "packed"})

    logger.info(f"[packer] {carton_id} units={units} weight={weight:.2f}kg")
    return {
        "carton_id": carton_id,
        "weight_kg": weight,
        "packed": True,
        "_units": units,   # consumed by the ship-readiness floor, then dropped
    }
