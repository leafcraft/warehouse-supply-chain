"""replenishment_planner_agent — reorder recommendations (store-grounded day-0).

auto_discover matches `replenishment_planner_agent`. Sweeps the SKU master
(on-hand vs reorder point vs target) and surfaces reorder recommendations; any
SKU at zero on-hand is a critical stockout routed to the Ops Supervisor. Also
absorbs a single-SKU master update (sku_master_update).

Store-grounded day-0 (agency/_shared/store.py); **announces itself** when the SKU
master is empty. In PROD swap the body for the ERP call (mcp block, tool
get_reorder_recommendations); the yields are the contract.
"""
from __future__ import annotations

from typing import Any

from leafmesh import LeafMeshLogger

from agency._shared import store

logger = LeafMeshLogger(__name__)


def _urgency(on_hand: int, reorder_point: int) -> str:
    if on_hand <= 0:
        return "critical"
    if on_hand <= reorder_point:
        return "reorder"
    return "ok"


async def replenishment_planner_agent(llm_response: Any, input_data: dict[str, Any],
                                      context: dict[str, Any]) -> dict[str, Any]:
    input_data = input_data if isinstance(input_data, dict) else {}
    mode = str(input_data.get("mode") or "").strip()

    # ── Single-SKU master update (sku_master_update) ─────────────────────
    if mode == "sku_master_update" or (input_data.get("sku") and input_data.get("delta")):
        sku = str(input_data.get("sku") or "")
        delta = int(input_data.get("delta") or 0)
        if sku and delta:
            store.restock(sku, delta)
        return {"mode": "sku_master_update", "total_skus": 1,
                "reorder_recommendations": [], "critical_stockout_count": 0,
                "replenishment_briefing": f"Updated {sku} by {delta}.",
                "available": bool(sku)}

    # ── Reorder sweep across the SKU master ──────────────────────────────
    skus = store.all_skus()
    if not skus:
        return {"mode": "reorder_sweep", "total_skus": 0,
                "reorder_recommendations": [], "critical_stockout_count": 0,
                "replenishment_briefing": "SKU master empty — wire ERP_MCP_URL.",
                "available": False}

    recs: list[dict[str, Any]] = []
    critical = 0
    for rec in skus:
        on_hand = int(rec.get("on_hand", 0))
        rop = int(rec.get("reorder_point", 0))
        urgency = _urgency(on_hand, rop)
        if urgency == "ok":
            continue
        if urgency == "critical":
            critical += 1
        target = int(rec.get("target_qty", 0))
        recs.append({
            "sku": rec.get("sku"), "qty_on_hand": on_hand, "reorder_point": rop,
            "order_qty_recommended": max(0, target - on_hand),
            "supplier_id": rec.get("supplier_id", ""),
            "lead_time_days": rec.get("lead_time_days", 0), "urgency": urgency,
        })

    briefing = (f"{len(skus)} SKUs swept, {len(recs)} at/below reorder point, "
                f"{critical} critical stockout(s).")
    logger.info(f"[replenishment] {briefing}")
    return {"mode": "reorder_sweep", "total_skus": len(skus),
            "reorder_recommendations": recs, "critical_stockout_count": critical,
            "replenishment_briefing": briefing, "available": True}
