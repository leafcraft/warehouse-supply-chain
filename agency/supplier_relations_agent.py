"""supplier_relations_agent — onboarding + sourcing + quarterly scorecards.

DEEP (self-reliant — reasons over real performance data, not a blob):

@pre_compose  load_supplier_context — pull the supplier's scorecard +
              trailing performance HISTORY (and, for sourcing, the
              category candidate pool; for scorecards, the precomputed
              signal rows across all suppliers) from the source store
              BEFORE the LLM reasons. In dev these come from
              agency/_shared/store.py (seeded ERP/SRM); in prod from your
              ERP/SRM (${ERP_MCP_URL}, tool get_supplier_scorecards). This
              is the self-reliant context pull: the relationship manager
              fetches its own authority — the actual on-time %, defect PPM
              trend, ESG/KYC state — instead of trusting a spoon-fed blob
              the caller pasted into the payload. The grading MATH stays
              upstream (ERP computes composite_score); the agent reasons
              over the TREND it pulled.

Pure-LLM body (no @chain): the onboarding/sourcing/scorecard FLOORS are
deterministic wire conditions in config.yaml (esg_high / kyc_rejected /
procurement-lead gates) — this module exists to make the agent
self-reliant for its inputs, not to add a second floor.
"""
from __future__ import annotations

import json
from typing import Any

from leafmesh import LeafMeshLogger, pre_compose

from agency._shared import store

logger = LeafMeshLogger(__name__)

DEFAULT_ESG_FLOOR = "medium"


def _parse_json(llm_response: Any) -> dict[str, Any]:
    if isinstance(llm_response, dict):
        return llm_response
    if isinstance(llm_response, str):
        try:
            obj = json.loads(llm_response)
            return obj if isinstance(obj, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _up(input_data: dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(input_data, dict) and input_data.get(key) not in (None, ""):
        return input_data[key]
    uy = input_data.get("upstream_yields") if isinstance(input_data, dict) else None
    if isinstance(uy, dict) and uy.get(key) not in (None, ""):
        return uy[key]
    return default


def _ctx(context: Any) -> dict[str, Any]:
    prepared = (context or {}).get("prepared_data", {}) if isinstance(context, dict) else {}
    c = prepared.get("context", {}) if isinstance(prepared, dict) else {}
    return c if isinstance(c, dict) else {}


def _infer_mode(input_data: dict[str, Any]) -> str:
    """Mirror the prompt's mode inference: explicit mode wins; else infer
    from the payload shape; an empty scheduled tick is a scorecard run."""
    mode = str(_up(input_data, "mode", "") or "").strip().lower()
    if mode in ("onboarding", "sourcing", "scorecard"):
        return mode
    if _up(input_data, "supplier_signals") or _up(input_data, "period"):
        return "scorecard"
    if _up(input_data, "sku") or _up(input_data, "category"):
        return "sourcing"
    if _up(input_data, "supplier_id"):
        return "onboarding"
    return "scorecard"   # bare quarterly tick


# ── @pre_compose — pull the scorecard / history / candidate pool ──────
def load_supplier_context(data: Any, ctx: Any) -> dict[str, Any]:
    """Fetch real supplier performance data for the inferred mode."""
    input_data = data if isinstance(data, dict) else {}
    mode = _infer_mode(input_data)
    out: dict[str, Any] = {"mode": mode}

    if mode == "onboarding":
        supplier_id = str(_up(input_data, "supplier_id", "") or "")
        out["supplier_id"] = supplier_id
        if supplier_id:
            rec = store.get_supplier(supplier_id)
            out["supplier"] = rec
            out["scorecard"] = store.supplier_scorecard(supplier_id)
            out["history"] = store.supplier_history(supplier_id)

    elif mode == "sourcing":
        category = str(_up(input_data, "category", "") or "")
        esg_floor = str(_up(input_data, "esg_floor", "") or DEFAULT_ESG_FLOOR)
        out["sku"] = str(_up(input_data, "sku", "") or "")
        out["category"] = category
        out["esg_floor"] = esg_floor
        # Self-reliant candidate pool — the preferred + market suppliers
        # that serve this category at/above the ESG floor, with their real
        # lead times + on-time history attached.
        out["candidates"] = (store.suppliers_for_category(category, esg_floor)
                             if category else [])

    else:  # scorecard
        # The precomputed signal rows across ALL suppliers (the math is
        # done upstream); the agent grades + flags them.
        out["period"] = str(_up(input_data, "period", "") or "")
        signals = _up(input_data, "supplier_signals")
        out["supplier_signals"] = (list(signals) if isinstance(signals, list)
                                   else store.supplier_signals())

    return out


@pre_compose(context_processor=load_supplier_context)
async def supplier_relations_agent(llm_response: Any, input_data: dict[str, Any],
                                   context: dict[str, Any]) -> dict[str, Any]:
    input_data = input_data if isinstance(input_data, dict) else {}
    llm = _parse_json(llm_response)
    pctx = _ctx(context)
    mode = str(llm.get("mode") or pctx.get("mode") or _infer_mode(input_data))

    # The LLM owns the per-mode judgment (grade bands, gates, ranking) over
    # the store-pulled context; the body normalises mode + identity and
    # passes the reasoned object through. Floors live on the wires.
    out = dict(llm) if llm else {}
    out["mode"] = mode
    if mode == "sourcing" and not out.get("sku"):
        out["sku"] = str(pctx.get("sku") or _up(input_data, "sku", "") or "")
    if mode == "onboarding" and not out.get("supplier_id"):
        out["supplier_id"] = str(pctx.get("supplier_id")
                                 or _up(input_data, "supplier_id", "") or "")
    if mode == "scorecard" and not out.get("period"):
        out["period"] = str(pctx.get("period") or _up(input_data, "period", "") or "")

    logger.info(
        f"[supplier-relations] mode={mode} "
        f"candidates={len(pctx.get('candidates') or [])} "
        f"signals={len(pctx.get('supplier_signals') or [])}"
    )
    return out
