"""Deterministic carrier rate-shopping — the floor the LLM can't waive.

The cheapest-meeting-SLA rule, computed from the TMS rate table:

  * RATE-SHOP — cost a carton against every rate row that can carry its
    weight and meets the requested transit SLA; pick the cheapest.
  * SLA FLOOR — a carrier that misses the SLA is never selected when a
    compliant option exists; sla_met is computed, not asserted.
  * SAVINGS — measured against the un-shopped default (what the carton
    would have cost shipped on the house default carrier).

These constants ARE the transportation policy. Production reads live
rates from the TMS; the arithmetic is identical.
"""
from __future__ import annotations

from typing import Any


def cost_cents(rate: dict[str, Any], weight_kg: float) -> int:
    base = int(rate.get("base_cents") or 0)
    per_kg = int(rate.get("per_kg_cents") or 0)
    return base + int(round(per_kg * max(0.0, float(weight_kg or 0))))


def rate_shop(rates: list[dict[str, Any]], weight_kg: float,
              sla_days: int, default_cents: int) -> dict[str, Any]:
    """Pick the cheapest carrier that can carry the weight AND meets SLA.

    Returns the carrier_selector_agent contract. When nothing meets the
    SLA, falls back to the cheapest carrier that can carry the weight and
    reports sla_met=False (the wire/manager decides what to do).
    """
    weight_kg = max(0.0, float(weight_kg or 0))
    sla_days = int(sla_days or 99)

    carriable = [r for r in rates or []
                 if float(r.get("max_kg") or 0) >= weight_kg]
    compliant = [r for r in carriable
                 if int(r.get("transit_days") or 99) <= sla_days]

    pool = compliant or carriable
    if not pool:
        return {"selected_carrier": "", "service_level": "",
                "estimated_cost_cents": 0, "sla_met": False,
                "savings_vs_default_cents": 0,
                "rationale": "no_carrier_can_carry_weight"}

    best = min(pool, key=lambda r: cost_cents(r, weight_kg))
    best_cost = cost_cents(best, weight_kg)
    sla_met = int(best.get("transit_days") or 99) <= sla_days
    savings = max(0, int(default_cents or 0) - best_cost)
    return {
        "selected_carrier": str(best.get("carrier") or ""),
        "service_level": str(best.get("service_level") or ""),
        "estimated_cost_cents": best_cost,
        "sla_met": sla_met,
        "savings_vs_default_cents": savings,
        "rationale": (f"cheapest of {len(pool)} option(s) "
                      f"{'meeting' if sla_met else 'MISSING'} "
                      f"{sla_days}d SLA at {weight_kg:.2f}kg"),
    }
