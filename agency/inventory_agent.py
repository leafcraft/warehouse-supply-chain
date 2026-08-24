"""inventory_agent — confirms stock + reserves units (store-grounded day-0).

auto_discover matches `inventory_agent`. Reserves the pick plan's lines against
the WMS and holds a reservation; a shortage surfaces as a short-pick exception.
Also absorbs an inbound restock (restock_event / a restocked return).

Store-grounded so the chain runs day-0 with NO connector wired: reads/writes the
seeded inventory in agency/_shared/store.py and **announces itself** — an unknown
SKU returns available:false, never a synthetic reservation. In PROD swap the body
for the WMS call (uncomment the mcp block in config.yaml, tool
check_and_reserve_stock); the yields are the contract.
"""
from __future__ import annotations

import uuid
from typing import Any

from leafmesh import LeafMeshLogger

from agency._shared import store

logger = LeafMeshLogger(__name__)


def _plan(input_data: dict[str, Any]) -> list[dict[str, Any]]:
    plan = input_data.get("pick_plan") or (input_data.get("upstream_yields") or {}).get("pick_plan")
    return list(plan or [])


def _qty(line: dict[str, Any]) -> int:
    return int(line.get("qty") or line.get("quantity") or 1)


async def inventory_agent(llm_response: Any, input_data: dict[str, Any],
                          context: dict[str, Any]) -> dict[str, Any]:
    input_data = input_data if isinstance(input_data, dict) else {}
    payload = input_data.get("payload") if isinstance(input_data.get("payload"), dict) else {}

    # ── Inbound restock path (restock_event / restocked return) ──────────
    restock_sku = str(input_data.get("sku") or payload.get("sku") or "").strip()
    restock_qty = input_data.get("delta") or input_data.get("quantity") or payload.get("quantity")
    if restock_sku and restock_qty and not _plan(input_data):
        new_on_hand = store.restock(restock_sku, int(restock_qty))
        logger.info(f"[inventory] restock {restock_sku} +{restock_qty} -> {new_on_hand}")
        return {"all_in_stock": True, "short_skus": [],
                "reservation_id": "", "available": True}

    # ── Reserve path (from allocation) ───────────────────────────────────
    plan = _plan(input_data)
    if not plan:
        return {"all_in_stock": False, "short_skus": [],
                "reservation_id": "", "available": False}

    short: list[dict[str, Any]] = []
    for line in plan:
        if not isinstance(line, dict):
            continue
        sku = str(line.get("sku") or "")
        want = _qty(line)
        if not store.reserve(sku, want):
            short.append({"sku": sku, "requested": want, "available": store.available(sku)})

    all_in_stock = not short
    reservation_id = f"resv-{uuid.uuid4().hex[:10]}" if all_in_stock else ""
    logger.info(f"[inventory] reserve lines={len(plan)} all_in_stock={all_in_stock} short={short}")
    return {"all_in_stock": all_in_stock, "short_skus": short,
            "reservation_id": reservation_id, "available": True}
