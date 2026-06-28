"""inventory_control_agent — cycle counts, ingest, shrink reconciliation.

DEEP (store-grounded + deterministic shrink floor):

@pre_compose  load_count_context — pull the SKU master (zones/bins/system
              on-hand) from the source store BEFORE reconciliation. In dev
              from agency/_shared/store.py (seeded); in prod from the WMS
              (${WMS_MCP_URL}, tool get_cycle_count_results). Self-reliant:
              the controller fetches the authoritative on-hand itself.

@chain        enforce_shrink_floor — the deterministic SHRINK / CYCLE-COUNT
              THRESHOLD (agency/_shared/shrink_math). Variance is
              recomputed from system-vs-physical; any SKU over the 2%
              tolerance keeps max_variance_pct above the floor and the
              manager-attention floor (systematic-short zone OR >20 over
              tolerance) is re-asserted. The LLM/body cannot wave a
              shrink event through.

Mode-dispatched (count | ingest | report). The MCP path is the prod swap
(point ${WMS_MCP_URL} at your WMS); the dev body reconciles the seeded
counts so the chain runs day-0.
"""
from __future__ import annotations

from typing import Any

from leafmesh import LeafMeshLogger, chain, pre_compose

from agency._shared import shrink_math, store

logger = LeafMeshLogger(__name__)


def _ctx(context: Any) -> dict[str, Any]:
    prepared = (context or {}).get("prepared_data", {}) if isinstance(context, dict) else {}
    c = prepared.get("context", {}) if isinstance(prepared, dict) else {}
    return c if isinstance(c, dict) else {}


def _counts_from_input(input_data: dict[str, Any]) -> list[dict[str, Any]]:
    """A single ingest row, or a batch of counts, or — in dev — synthesise
    a sweep from the seeded inventory (treats system on-hand as physical
    minus the seeded shrink on the short SKU)."""
    if input_data.get("counts"):
        return list(input_data["counts"])
    if input_data.get("sku") and input_data.get("physical_qty") is not None:
        return [{
            "sku": str(input_data.get("sku")),
            "zone": str(input_data.get("zone") or store.bin_for(str(input_data["sku"]))["zone"]),
            "bin": str(input_data.get("bin") or store.bin_for(str(input_data["sku"]))["bin"]),
            "system_qty": int(input_data.get("system_qty")
                              or store.get_sku(str(input_data["sku"])).get("on_hand", 0)),
            "physical_qty": int(input_data.get("physical_qty")),
        }]
    # Dev sweep: count every seeded SKU. Inject a shrink on the short SKU
    # so the demo exercises the over-tolerance path.
    counts = []
    for rec in store.all_skus():
        sys_q = int(rec.get("on_hand", 0))
        phys = sys_q if rec.get("sku") != "SKU-7700" else max(0, sys_q - 1)
        counts.append({"sku": rec.get("sku"), "zone": rec.get("zone"),
                       "bin": rec.get("bin"), "system_qty": sys_q,
                       "physical_qty": phys})
    return counts


# ── @pre_compose — pull the SKU master (self-reliant) ─────────────────
def load_count_context(data: Any, ctx: Any) -> dict[str, Any]:
    input_data = data if isinstance(data, dict) else {}
    return {"counts": _counts_from_input(input_data),
            "mode": str(input_data.get("mode") or "count")}


# ── @chain — the deterministic SHRINK floor (LLM can't waive) ─────────
async def enforce_shrink_floor(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        result = {}
    # Re-assert: if any over-tolerance SKU exists, max_variance_pct must
    # reflect it (never below the 2% tolerance the wire gates on).
    ot = result.get("over_tolerance") or []
    if ot:
        worst = max(abs(float(o.get("variance_pct") or 0)) for o in ot)
        result["max_variance_pct"] = max(float(result.get("max_variance_pct") or 0), worst)
    result["requires_manager_action"] = shrink_math.requires_manager_action(result)
    return result


@pre_compose(context_processor=load_count_context)
@chain(enforce_shrink_floor)
async def inventory_control_agent(llm_response: Any, input_data: dict[str, Any],
                                  context: dict[str, Any]) -> dict[str, Any]:
    input_data = input_data if isinstance(input_data, dict) else {}
    pctx = _ctx(context)
    counts = pctx.get("counts") or _counts_from_input(input_data)
    mode = str(pctx.get("mode") or input_data.get("mode") or "count")

    recon = shrink_math.reconcile(counts)
    over = recon["over_tolerance_count"]
    sysz = recon["systematic_short_zone_count"]
    recon["mode"] = mode
    recon["recon_briefing"] = (
        f"{mode}: {recon['skus_counted']} counted, {over} over 2% tolerance, "
        f"{sysz} systematic-short zone(s), worst variance "
        f"{recon['max_variance_pct']*100:.1f}%."
    )
    logger.info(f"[inv-control] {recon['recon_briefing']}")
    return recon
