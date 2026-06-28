"""exception_agent — short-pick / damage / wrong-item / safety handler.

DEEP (self-reliant + deterministic severity/safety floor):

@pre_compose  load_exception_context — pull the ORDER, the affected SKU's
              inventory record (unit value, bin, free stock) and the
              fleet unit behind the event from the source store BEFORE the
              LLM classifies. In dev these come from
              agency/_shared/store.py (seeded); in prod from the OMS / WMS
              / fleet API. Self-reliant: the handler fetches the real
              context (e.g. that the damaged SKU is a $649 item) instead of
              trusting whatever the upstream payload echoed — so the LLM
              reasons over facts, not a one-line "damaged" note.

@chain        enforce_severity_floor — the deterministic SEVERITY / SAFETY
              FLOOR the LLM cannot waive. A safety event, ANY damage, OR a
              high-value line (unit value ≥ HIGH_VALUE_CENTS) forces
              recommended_action = escalate, requires_supervisor = true and
              floors severity. The model can under-score a "minor" damage
              or fail to know the SKU is high-value; the floor re-asserts
              it from the store-pulled context. Fail-closed: the picker /
              floor can never present a damage/safety/high-value exception
              as front-line-resolvable.

KEPT AS PYTHON (the floor): warehouse loss/safety policy is a deterministic
control that belongs to this mesh, not to a prompt a model can talk itself
out of. The four exception classes and the wire conditions in config.yaml
mirror these same floors.
"""
from __future__ import annotations

import json
from typing import Any

from leafmesh import LeafMeshLogger, chain, pre_compose

from agency._shared import store

logger = LeafMeshLogger(__name__)

# A line at/above this unit value can't be front-line-resolved on a
# damage/short — it goes to the supervisor regardless of the LLM's score.
HIGH_VALUE_CENTS = 50_000          # $500
SAFETY_FLOOR_SEVERITY = 1.0
HIGH_IMPACT_SEVERITY = 0.7         # the supervisor threshold the wires gate on

EXCEPTION_TYPES = ("short_pick", "damage", "wrong_item", "safety")
ACTIONS = ("substitute_sku", "backorder", "refund", "escalate")

_DEFAULTS: dict[str, Any] = {
    "exception_type": "short_pick", "severity": 0.0,
    "recommended_action": "backorder", "requires_supervisor": False,
    "supervisor_briefing": "", "order_id": "", "floor_reason": "",
}


def _clamp01(v: Any) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _up(input_data: dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(input_data, dict) and input_data.get(key) not in (None, ""):
        return input_data[key]
    uy = input_data.get("upstream_yields") if isinstance(input_data, dict) else None
    if isinstance(uy, dict) and uy.get(key) not in (None, ""):
        return uy[key]
    return default


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


def _ctx(context: Any) -> dict[str, Any]:
    prepared = (context or {}).get("prepared_data", {}) if isinstance(context, dict) else {}
    c = prepared.get("context", {}) if isinstance(prepared, dict) else {}
    return c if isinstance(c, dict) else {}


# ── @pre_compose — pull the exception's order / inventory / fleet ─────
def load_exception_context(data: Any, ctx: Any) -> dict[str, Any]:
    """Fetch the real context behind the exception. PROD: OMS+WMS+fleet."""
    input_data = data if isinstance(data, dict) else {}
    order = input_data.get("order") if isinstance(input_data.get("order"), dict) else {}
    order_id = str(_up(input_data, "order_id", "")
                   or (order or {}).get("order_id") or "")
    sku = str(_up(input_data, "sku", "") or "")
    unit_id = str(_up(input_data, "unit_id", "")
                  or _up(input_data, "picker_id", "") or "")
    return store.exception_context(order_id=order_id, sku=sku, unit_id=unit_id)


# ── @chain — the deterministic SEVERITY / SAFETY FLOOR (LLM can't waive)
async def enforce_severity_floor(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """Re-assert escalation for safety / damage / high-value exceptions.

    The LLM may under-score; this floor recomputes from the store-pulled
    context (unit value) and the exception class. It can only RAISE
    severity / escalation, never lower it. Fail-closed.
    """
    if not isinstance(result, dict):
        result = {}
    for k, v in _DEFAULTS.items():
        result.setdefault(k, v)

    etype = str(result.get("exception_type") or "").strip().lower()
    if etype not in EXCEPTION_TYPES:
        etype = "short_pick"
    result["exception_type"] = etype
    result["severity"] = _clamp01(result.get("severity"))

    # The unit value drives the high-value floor. Prefer the @pre_compose
    # context; but @chain may not receive prepared_data in every runtime
    # path, so fall back to recomputing it from the store via the order
    # the body stamped on the result. Either way the floor reads the WMS,
    # never a model-asserted value.
    pctx = _ctx(ctx)
    value_cents = int(pctx.get("unit_value_cents") or 0)
    if not value_cents:
        order_id = str(result.get("order_id") or "")
        if order_id:
            value_cents = int(store.exception_context(order_id=order_id)
                              .get("unit_value_cents") or 0)

    reasons: list[str] = []
    # 1. Safety — the hardest floor: max severity, always escalate.
    if etype == "safety":
        result["severity"] = SAFETY_FLOOR_SEVERITY
        reasons.append("safety_event")
    # 2. Any damage — never front-line-resolvable; floor to the supervisor.
    if etype == "damage":
        result["severity"] = max(result["severity"], HIGH_IMPACT_SEVERITY)
        reasons.append("damage")
    # 3. High-value line — a loss/short/damage on a $500+ item escalates.
    if value_cents >= HIGH_VALUE_CENTS:
        result["severity"] = max(result["severity"], HIGH_IMPACT_SEVERITY)
        reasons.append(f"high_value_{value_cents}c")

    floored = bool(reasons)
    # Re-assert the supervisor + escalate floor (the wires gate on the same
    # severity ≥ 0.7 / safety / requires_supervisor conditions).
    if floored or result["severity"] >= HIGH_IMPACT_SEVERITY:
        result["requires_supervisor"] = True
        result["recommended_action"] = "escalate"
    if str(result.get("recommended_action") or "") not in ACTIONS:
        result["recommended_action"] = "escalate" if result["requires_supervisor"] else "backorder"

    result["floor_reason"] = ",".join(reasons)

    if floored:
        prefix = f"[FLOOR: {result['floor_reason']}] "
        brief = str(result.get("supervisor_briefing") or "")
        if not brief.startswith(prefix):
            result["supervisor_briefing"] = (prefix + brief).strip()
        logger.warning(
            f"[exception] SEVERITY FLOOR fired ({result['floor_reason']}) — "
            f"type={etype} severity={result['severity']} → supervisor escalate"
        )
    return result


@pre_compose(context_processor=load_exception_context)
@chain(enforce_severity_floor)
async def exception_agent(llm_response: Any, input_data: dict[str, Any],
                          context: dict[str, Any]) -> dict[str, Any]:
    input_data = input_data if isinstance(input_data, dict) else {}
    llm = _parse_json(llm_response)
    pctx = _ctx(context)

    out = {**_DEFAULTS}
    out["exception_type"] = str(llm.get("exception_type") or "short_pick")
    out["severity"] = _clamp01(llm.get("severity"))
    out["recommended_action"] = str(llm.get("recommended_action") or "backorder")
    out["requires_supervisor"] = bool(llm.get("requires_supervisor"))
    out["supervisor_briefing"] = str(llm.get("supervisor_briefing") or "")
    out["order_id"] = str(llm.get("order_id")
                          or pctx.get("order_id")
                          or _up(input_data, "order_id", "") or "")
    # floor_reason is owned by the @chain floor — never the model.
    out["floor_reason"] = ""
    return out
