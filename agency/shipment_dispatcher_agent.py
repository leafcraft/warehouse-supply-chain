"""shipment_dispatcher_agent — the FINISHER / actuator behind the release gate.

auto_discover matches `shipment_dispatcher_agent`. The dispatcher sits BEHIND
the dispatch RELEASE gate. It never releases a carton on its own authority — it
re-checks that the carton is genuinely authorised, renders the shipping manifest,
records an append-only ledger entry, then hands the carton to the loader. It is
the concrete "@chain fails closed":

  a) release RE-CHECK — reaching this agent does not mean released. Routine
     cartons self-authorise on deterministic invariants; a HELD / high-value
     carton routes to the Warehouse Manager (phase 1) and issues (phase 2) ONLY
     on an explicit release token (allowlist) — an UNKNOWN verdict fails closed.
  b) deterministic invariants a human can't waive (carrier selected, SLA met, a
     real carton with a known weight).
  c) render the real manifest to ./out; on a tripped floor, DELETE the partial
     and record status: blocked with reasons — never a half-issued shipment.
  d) dev-store recovery — after the HITL gate the upstream payload is dropped, so
     the dispatcher recovers the carton from the session stash / dev store, and
     ANNOUNCES available:false when there is genuinely no shipment record.

Two phases: first call (from carrier_selector) -> maybe route for release; second
call (from the Warehouse Manager, approved) -> issue the manifest + hand to load.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from leafmesh import LeafMeshLogger, chain

try:
    from ._shared import store
    from ._shared.session_stash import resolve_session_id, stash_get, stash_put
except ImportError:  # pragma: no cover
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _shared import store  # type: ignore
    from _shared.session_stash import resolve_session_id, stash_get, stash_put  # type: ignore

logger = LeafMeshLogger(__name__)

_OUT_DIR = Path("./out")
_RELEASE_TOKENS = frozenset({"approved", "approve", "release", "released",
                             "authorised", "authorized", "ship", "yes", "ok"})
_BLOCK_TOKENS = frozenset({"rejected", "reject", "no", "deny", "denied",
                           "held", "held_for_review", "hold"})

# A carton at/above this declared value must be released by a human, never
# self-authorised on the routine path.
HIGH_VALUE_CENTS = 50_000          # $500


def _signal(input_data: dict) -> str:
    """The human verdict, from wherever the SDK delivered it (shape-tolerant)."""
    sources = [input_data]
    up = input_data.get("upstream_yields")
    if isinstance(up, dict):
        sources.extend(v for v in up.values() if isinstance(v, dict))
    for src in sources:
        for key in ("human_message", "human_decision", "decision", "message"):
            val = src.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip().lower()
    return ""


def _is_released(input_data: dict) -> bool:
    """Fail closed: only an explicit release token counts; unknown => not released."""
    sig = _signal(input_data)
    if sig in _BLOCK_TOKENS:
        return False
    return sig in _RELEASE_TOKENS


def _is_blocked(input_data: dict) -> bool:
    return _signal(input_data) in _BLOCK_TOKENS


def _needs_human_release(carton: dict) -> bool:
    """A held / high-value carton cannot be self-authorised on the routine path."""
    if str(carton.get("hold") or carton.get("held") or "").lower() in ("1", "true", "yes"):
        return True
    return int(carton.get("value_cents") or 0) >= HIGH_VALUE_CENTS


def _render_manifest(carton: dict) -> str:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    carton_id = str(carton.get("carton_id") or "carton")
    path = _OUT_DIR / f"{carton_id}_shipping_manifest.md"
    lines = [
        f"# Shipping manifest — carton {carton_id}",
        f"Order: {carton.get('order_id') or 'on record'}",
        f"Carrier: {carton.get('selected_carrier') or 'on record'}  "
        f"({carton.get('service_level') or ''})",
        f"Weight: {carton.get('weight_kg') or 0} kg",
        f"Tracking: {carton.get('tracking_number_placeholder') or 'pending'}",
        "",
        "_Released and handed to the loader for outbound dock loading._",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _delete_partial(result: dict) -> None:
    p = result.get("manifest_path")
    if p:
        Path(p).unlink(missing_ok=True)
        result["manifest_path"] = ""


def _recover_carton(input_data: dict, context: dict) -> dict:
    """Recover the FULL carton across the HITL boundary: upstream -> stash -> dev
    store. Match on the substantive field (selected_carrier), never a bare
    carton_id — after the release gate the human reply carries only the verdict +
    id, so trusting a thin payload with an id would skip the real record."""
    up = input_data.get("upstream_yields") or {}
    candidates = [input_data]
    if isinstance(up, dict):
        candidates.append(up.get("carrier_selector_agent"))
        candidates.extend(v for v in up.values() if isinstance(v, dict))
    for src in candidates:
        if isinstance(src, dict) and src.get("selected_carrier"):
            return {k: v for k, v in src.items() if not k.startswith("_")}
    sid = resolve_session_id(input_data, context)
    stashed = stash_get(sid)
    if stashed.get("selected_carrier") or stashed.get("carton_id"):
        return stashed
    carton_id = str(input_data.get("carton_id") or "")
    rec = store.get_shipment(carton_id) if carton_id else {}
    if rec:
        rec.setdefault("carton_id", carton_id)
        return rec
    return {"carton_id": carton_id}


def enforce_dispatch_floor(result: Any, context: dict) -> dict:
    """The finisher floor — issue ONLY on a real release; delete on a block."""
    result = result if isinstance(result, dict) else {}
    result.setdefault("block_reasons", [])

    if result.get("status") == "routed":
        return result  # phase 1: routed to the manager for release, nothing to issue

    carton = result.get("_carton") or {}

    # Dev-store fallback announces itself — a bare id with no real shipment
    # record behind it is available:false, never a synthetic dispatch ack.
    if not carton.get("carton_id") or (
            not carton.get("selected_carrier") and not carton.get("order_id")):
        result.update(status="blocked", dispatched=False, released=False)
        cid = carton.get("carton_id") or "?"
        result["block_reasons"].append(
            f"no shipment record for carton {cid} (available:false)")
        _delete_partial(result)
        logger.warning(f"[dispatch] no carton record for {cid} — announcing unavailable")
        return result

    if not result.get("released"):
        result["status"] = "blocked"
        if _is_blocked(result.get("_input", {})):
            result["block_reasons"].append("release rejected / held")
        else:
            result["block_reasons"].append("no explicit release on record (fail closed)")
        _delete_partial(result)
        logger.info(f"[dispatch] release floor BLOCKED: {result['block_reasons']}")
        return result

    # Deterministic invariants a human can't waive.
    reasons = []
    if not carton.get("selected_carrier"):
        reasons.append("no carrier selected")
    if not carton.get("sla_met", False):
        reasons.append("carrier does not meet SLA")
    if not float(carton.get("weight_kg") or 0) > 0:
        reasons.append("carton weight unknown")
    if reasons:
        result.update(status="blocked", dispatched=False)
        result["block_reasons"].extend(reasons)
        _delete_partial(result)
        logger.info(f"[dispatch] invariant floor BLOCKED: {reasons}")
        return result

    # Passed the floor — render, ledger, hand to the loader.
    result["manifest_path"] = _render_manifest(carton)
    result["carton_id"] = carton.get("carton_id")
    store.record_outbox({"carton_id": carton.get("carton_id"),
                         "order_id": carton.get("order_id"),
                         "action": "dispatch", "manifest_path": result["manifest_path"]})
    store.append_ledger({"carton_id": carton.get("carton_id"),
                         "order_id": carton.get("order_id"),
                         "event": "released_and_dispatched",
                         "carrier": carton.get("selected_carrier")})
    result.update(dispatched=True, status="dispatched")
    logger.info(f"[dispatch] issued carton {carton.get('carton_id')} -> {result['manifest_path']}")
    return result


@chain(enforce_dispatch_floor)
async def shipment_dispatcher_agent(llm_response: Any, input_data: dict,
                                    context: dict) -> dict:
    input_data = input_data if isinstance(input_data, dict) else {}
    carton = _recover_carton(input_data, context)
    sid = resolve_session_id(input_data, context)
    if carton.get("carton_id"):
        stash_put(sid, carton)   # keep it recoverable across the release gate

    out: dict[str, Any] = {
        "routed_for_release": False, "released": False, "dispatched": False,
        "manifest_path": "", "status": "", "block_reasons": [],
        "carton_id": str(carton.get("carton_id") or ""),
        "_input": input_data, "_carton": carton,
    }

    if _is_released(input_data):
        out["released"] = True                     # phase 2 — the floor issues it
    elif _is_blocked(input_data):
        out["status"] = "blocked"
    elif _needs_human_release(carton):
        out["status"] = "routed"                   # phase 1 — held / high-value -> manager
        out["routed_for_release"] = True
    else:
        out["released"] = True                     # routine carton self-authorises
    return out
