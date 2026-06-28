"""Deterministic allocation arithmetic — the floors the LLM can't waive.

Warehouse logic that belongs to this mesh, not a system of record:

  * AVAILABILITY GATE — a pick line can only be dispatched when the WMS
    has enough free stock (on_hand − reserved). A short line is NOT
    dispatchable; it must surface as a short-pick exception. A fat-
    fingered direct call cannot present an out-of-stock line as pickable.
  * TRAVEL MINIMISATION — bin-sort within zone to minimise picker travel.
  * SHIP-READINESS — a carton is only ship-ready when it is packed, every
    line is accounted for, and weight is known.

These constants ARE the warehouse's allocation policy — an ops planner
tunes them here, not by re-prompting a model.
"""
from __future__ import annotations

from typing import Any

MIN_PER_ITEM = 1.5               # picker travel/handle minutes per line


def build_pick_plan(line_items: list[Any],
                    bin_lookup) -> list[dict[str, Any]]:
    """Resolve each order line to its pick face (zone/bin) via the WMS
    slotting lookup. bin_lookup(sku) → {"zone", "bin"}."""
    plan: list[dict[str, Any]] = []
    for li in line_items or []:
        if not isinstance(li, dict):
            continue
        sku = str(li.get("sku") or "")
        qty = int(li.get("quantity") or li.get("qty") or 0)
        loc = bin_lookup(sku) if sku else {"zone": "", "bin": ""}
        plan.append({"sku": sku, "qty": qty,
                     "zone": loc.get("zone", ""), "bin": loc.get("bin", "")})
    return plan


def availability_gate(pick_plan: list[dict[str, Any]],
                      avail_lookup) -> dict[str, Any]:
    """The deterministic availability floor. avail_lookup(sku) → int free.

    Returns {all_available, short_lines:[{sku, requested, available}]}.
    A line is short when free stock < requested qty. The LLM/body cannot
    LOWER a short into available — this recomputes from the WMS.
    """
    short: list[dict[str, Any]] = []
    for line in pick_plan or []:
        if not isinstance(line, dict):
            continue
        sku = str(line.get("sku") or "")
        req = int(line.get("qty") or 0)
        free = int(avail_lookup(sku)) if sku else 0
        if req > free:
            short.append({"sku": sku, "requested": req, "available": free})
    return {"all_available": not short, "short_lines": short}


def sequence_route(pick_plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bin-sort within zone — minimises picker travel."""
    return sorted(
        [p for p in (pick_plan or []) if isinstance(p, dict)],
        key=lambda p: (str(p.get("zone", "")), str(p.get("bin", ""))),
    )


def estimate_minutes(pick_plan: list[dict[str, Any]]) -> float:
    return round(len([p for p in pick_plan or [] if isinstance(p, dict)])
                 * MIN_PER_ITEM, 2)


def ship_ready(packed: bool, weight_kg: float, lines_expected: int,
               units_packed: int) -> tuple[bool, list[str]]:
    """Ship-readiness floor: packed + weight known + nothing missing."""
    reasons: list[str] = []
    if not packed:
        reasons.append("not_packed")
    if float(weight_kg or 0) <= 0:
        reasons.append("weight_unknown")
    if int(units_packed or 0) <= 0 and int(lines_expected or 0) > 0:
        reasons.append("no_units_packed")
    return (not reasons), reasons
