"""carrier_selector_agent — picks the cheapest carrier meeting the SLA.

DEEP (store-grounded + deterministic rate-shop floor):

@pre_compose  load_rate_context — pull the carton weight (from the
              shipment store when only a carton_id arrived) and the
              carrier rate table BEFORE selection. In dev from
              agency/_shared/store.py (seeded rates); in prod from the TMS
              (vault connection tms_mcp, tool select_carrier). Self-reliant: the
              planner fetches the weight + rates itself.

@chain        enforce_rate_shop_floor — the deterministic CARRIER
              RATE-SHOPPING RULE (agency/_shared/carrier_math): recompute
              the cheapest carrier that can carry the weight AND meets the
              SLA; re-assert sla_met from transit days. The LLM/body
              cannot select an SLA-missing or non-cheapest carrier when a
              compliant cheaper one exists.

The MCP path is the prod swap; the dev body rate-shops the seeded table
so the chain runs day-0.
"""
from __future__ import annotations

from typing import Any

from leafmesh import LeafMeshLogger, chain, pre_compose

from agency._shared import carrier_math, store

logger = LeafMeshLogger(__name__)

DEFAULT_SLA_DAYS = 5


def _ctx(context: Any) -> dict[str, Any]:
    prepared = (context or {}).get("prepared_data", {}) if isinstance(context, dict) else {}
    c = prepared.get("context", {}) if isinstance(prepared, dict) else {}
    return c if isinstance(c, dict) else {}


def _weight(input_data: dict[str, Any]) -> float:
    w = input_data.get("weight_kg") or (input_data.get("upstream_yields") or {}).get("weight_kg")
    if w:
        return float(w)
    carton = str(input_data.get("carton_id")
                 or (input_data.get("upstream_yields") or {}).get("carton_id") or "")
    if carton:
        return float(store.get_shipment(carton).get("weight_kg") or 0)
    return 0.0


# ── @pre_compose — pull weight + rate table (self-reliant) ────────────
def load_rate_context(data: Any, ctx: Any) -> dict[str, Any]:
    input_data = data if isinstance(data, dict) else {}
    sla = int(input_data.get("sla_days")
              or (input_data.get("upstream_yields") or {}).get("sla_days")
              or DEFAULT_SLA_DAYS)
    return {"weight_kg": _weight(input_data), "rates": store.carrier_rates(),
            "sla_days": sla}


# ── @chain — the deterministic RATE-SHOP floor (LLM can't waive) ──────
async def enforce_rate_shop_floor(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        result = {}
    # Re-derive sla_met from transit vs SLA — never trust an asserted flag.
    sla_days = int(result.get("_sla_days") or DEFAULT_SLA_DAYS)
    transit = int(result.get("_transit_days") or 99)
    result["sla_met"] = transit <= sla_days
    result.pop("_sla_days", None)
    result.pop("_transit_days", None)
    if not result.get("sla_met"):
        logger.warning(f"[carrier] no carrier meets {sla_days}d SLA — flagged")
    return result


def _carrier_down(input_data: dict[str, Any]) -> str:
    """The carrier that is out (the carrier_outage_failover entry sends it)."""
    up = input_data.get("upstream_yields") or {}
    return str(input_data.get("carrier_down") or input_data.get("primary_carrier")
               or (up.get("carrier_down") if isinstance(up, dict) else "") or "").strip().lower()


@pre_compose(context_processor=load_rate_context)
@chain(enforce_rate_shop_floor)
async def carrier_selector_agent(llm_response: Any, input_data: dict[str, Any],
                                 context: dict[str, Any]) -> dict[str, Any]:
    input_data = input_data if isinstance(input_data, dict) else {}
    pctx = _ctx(context)
    weight = float(pctx.get("weight_kg") or _weight(input_data))
    rates = pctx.get("rates") or store.carrier_rates()
    # @pre_compose does NOT run on a direct call — fall back to input_data
    # so the floor holds regardless of how the agent is invoked.
    sla = int(pctx.get("sla_days")
              or input_data.get("sla_days")
              or (input_data.get("upstream_yields") or {}).get("sla_days")
              or DEFAULT_SLA_DAYS)

    down = _carrier_down(input_data)
    if down:
        # Failover: the carrier that is out is not an option for any service level.
        rates = [r for r in rates if str(r.get("carrier") or "").lower() != down]
    shopped = carrier_math.rate_shop(rates, weight, sla, store.DEFAULT_CARRIER_CENTS)
    if down:
        shopped["rationale"] = f"{shopped['rationale']}; {down} out — excluded"
    # carry the transit days for the floor to re-derive sla_met
    chosen = next((r for r in rates
                   if r.get("carrier") == shopped["selected_carrier"]
                   and r.get("service_level") == shopped["service_level"]), {})
    shopped["_transit_days"] = int(chosen.get("transit_days") or 99)
    shopped["_sla_days"] = sla
    shopped["tracking_number_placeholder"] = ""
    logger.info(
        f"[carrier] {shopped['selected_carrier']}/{shopped['service_level']} "
        f"${shopped['estimated_cost_cents']/100:.2f} saved "
        f"${shopped['savings_vs_default_cents']/100:.2f}"
    )
    return shopped
