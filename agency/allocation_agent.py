"""allocation_agent — pick path + batching + travel-minimised route.

DEEP (self-reliant, store-grounded, deterministic floor):

@pre_compose  load_fulfillment_context — pull the ORDER (header + lines),
              the bin/slotting location for each SKU, and free-stock
              availability from the source store BEFORE allocation runs.
              In dev these come from agency/_shared/store.py (seeded); in
              prod from the WMS connector. This is the self-reliant
              context pull: allocation fetches its own authority rather
              than trusting whatever the upstream payload echoed.

@chain        enforce_allocation_floor — the deterministic AVAILABILITY
              GATE (agency/_shared/allocation_math). Free stock is
              recomputed from the WMS per line; any short line is NOT
              dispatchable and is surfaced as a short-pick exception. The
              LLM (or a fat-fingered direct call) cannot present an
              out-of-stock line as pickable, and cannot route a picker
              for an order with no available lines.

KEPT AS PYTHON (YAML-first exception): bin-sort travel minimisation,
batching, priority/resource assignment and the availability gate are
allocation DOMAIN MATH — warehouse logic that belongs to this mesh, not
to a system of record.
"""
from __future__ import annotations

import uuid
from typing import Any

from leafmesh import LeafMeshLogger, chain, pre_compose

from agency._shared import allocation_math, store

logger = LeafMeshLogger(__name__)

PRIORITIES = ("standard", "rush", "hazmat")


def _up(input_data: dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(input_data, dict) and input_data.get(key) not in (None, ""):
        return input_data[key]
    uy = input_data.get("upstream_yields") if isinstance(input_data, dict) else None
    if isinstance(uy, dict) and uy.get(key) not in (None, ""):
        return uy[key]
    return default


# ── @pre_compose — pull the order + bin locations + availability ──────
def load_fulfillment_context(data: Any, ctx: Any) -> dict[str, Any]:
    """Fetch allocation authority from the source store. PROD: WMS read."""
    input_data = data if isinstance(data, dict) else {}
    order = input_data.get("order") if isinstance(input_data.get("order"), dict) else {}
    order_id = str((order or {}).get("order_id")
                   or _up(input_data, "order_id", "") or "")
    # Self-reliant: if only an order_id arrived, hydrate the full order.
    if order_id and not (order or {}).get("line_items"):
        hydrated = store.get_order(order_id)
        if hydrated:
            order = hydrated
    line_items = list((order or {}).get("line_items") or [])
    availability = {str(li.get("sku")): store.available(str(li.get("sku") or ""))
                    for li in line_items if isinstance(li, dict)}
    return {"order": order, "order_id": order_id, "availability": availability}


def _ctx(context: Any) -> dict[str, Any]:
    prepared = (context or {}).get("prepared_data", {}) if isinstance(context, dict) else {}
    c = prepared.get("context", {}) if isinstance(prepared, dict) else {}
    return c if isinstance(c, dict) else {}


# ── @chain — the deterministic AVAILABILITY GATE (LLM can't waive) ────
async def enforce_allocation_floor(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """Recompute free stock per line and gate dispatch on it.

    Any line short on free stock (on_hand − reserved) is flagged; if NO
    line is available the picker is NOT dispatched — assigned_resource is
    blanked and the short list rides the exception edge. Fail-closed.
    """
    if not isinstance(result, dict):
        result = {}
    plan = result.get("pick_plan") or []
    gate = allocation_math.availability_gate(plan, store.available)
    result["all_available"] = gate["all_available"]
    result["short_lines"] = gate["short_lines"]
    if not gate["all_available"]:
        logger.warning(
            f"[allocation] AVAILABILITY GATE fired — short {gate['short_lines']}"
        )
        # No fully-available line → nothing to pick; block the dispatch.
        if len(gate["short_lines"]) >= max(1, len(plan)):
            result["assigned_resource"] = ""
    return result


@pre_compose(context_processor=load_fulfillment_context)
@chain(enforce_allocation_floor)
async def allocation_agent(llm_response: Any, input_data: dict[str, Any],
                           context: dict[str, Any]) -> dict[str, Any]:
    input_data = input_data if isinstance(input_data, dict) else {}
    pctx = _ctx(context)
    order = pctx.get("order") or (input_data.get("order") or {})
    line_items = list((order or {}).get("line_items") or [])
    ship_method = str((order or {}).get("ship_method") or "standard").lower()

    # ── Stage 1 — allocation: pick plan (store-grounded bins), batch ──
    pick_plan = allocation_math.build_pick_plan(line_items, store.bin_for)
    if not pick_plan:
        # Direct-call path — route an existing pick plan
        pick_plan = list(input_data.get("pick_plan") or [])
    batch_id = f"batch-{uuid.uuid4().hex[:10]}"

    if "next_day" in ship_method or "express" in ship_method:
        priority = "rush"
    elif any("hazmat" in str(li.get("flags", "")).lower()
             for li in line_items if isinstance(li, dict)):
        priority = "hazmat"
    else:
        priority = "standard"
    special = priority == "hazmat"

    # ── Stage 2 — routing: travel-minimised sequence + resource ──────
    sorted_items = allocation_math.sequence_route(pick_plan)
    estimated = allocation_math.estimate_minutes(pick_plan)
    assigned = "human_picker" if special else "picker_humanoid"

    order_id = str((order or {}).get("order_id")
                   or pctx.get("order_id")
                   or input_data.get("order_id") or "")

    logger.info(
        f"[allocation] {batch_id} items={len(pick_plan)} priority={priority} "
        f"est_min={estimated:.0f} → {assigned}"
    )
    return {
        "pick_plan": pick_plan,
        "batch_id": batch_id,
        "priority": priority,
        "requires_special_handling": special,
        "pick_sequence": sorted_items,
        "estimated_minutes": estimated,
        "assigned_resource": assigned,
        "order_id": order_id,
    }
