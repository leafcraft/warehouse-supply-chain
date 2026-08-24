"""returns_handler_agent — disposition a returned item (store-grounded day-0).

auto_discover matches `returns_handler_agent`. Dispositions a returned unit —
restock (good condition), refurbish (repairable), or scrap (damaged) — and, on a
restock, restocks the WMS so the units re-enter available stock.

Store-grounded day-0 (agency/_shared/store.py); **announces itself** — no sku in
the payload returns available:false rather than a silent success. In PROD swap
the body for the WMS returns-module call (mcp block, tool process_return); the
yields are the contract.
"""
from __future__ import annotations

from typing import Any

from leafmesh import LeafMeshLogger

from agency._shared import store

logger = LeafMeshLogger(__name__)

_GOOD = frozenset({"new", "unopened", "good", "sellable", "resalable", "resaleable"})
_REPAIRABLE = frozenset({"opened", "used", "refurbishable", "repairable", "minor_damage"})


async def returns_handler_agent(llm_response: Any, input_data: dict[str, Any],
                                context: dict[str, Any]) -> dict[str, Any]:
    input_data = input_data if isinstance(input_data, dict) else {}
    payload = input_data.get("payload") if isinstance(input_data.get("payload"), dict) else input_data

    sku = str(payload.get("sku") or "").strip()
    qty = int(payload.get("quantity") or payload.get("qty") or 0)
    condition = str(payload.get("condition") or "").strip().lower()

    if not sku:
        return {"return_status": "", "restocked_units": 0, "available": False}

    if condition in _GOOD:
        restocked = store.restock(sku, qty) if qty else 0
        status = "restocked"
    elif condition in _REPAIRABLE:
        status = "refurbish"
        restocked = 0
    else:
        status = "scrapped"
        restocked = 0

    logger.info(f"[returns] {sku} x{qty} condition={condition!r} -> {status}")
    return {"return_status": status,
            "restocked_units": qty if status == "restocked" else 0,
            "available": True}
