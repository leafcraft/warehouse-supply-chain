"""Dev-mode store for the warehouse / supply-chain agency.

Stands in for the systems of record so the FULL chain runs end-to-end
before any connector is wired — order → allocate → pick (humanoid) →
inspect → pack → ship, plus the damage-exception path:

  * WMS        → on-hand / reserved counts, bin locations, reservations
                 (inventory_agent, returns_handler_agent)
  * Fleet API  → the humanoid fleet roster + telemetry
                 (humanoid_status_agent, the *_humanoid actors)
  * TMS        → carrier rate tables (carrier_selector_agent)
  * ERP        → SKU master / reorder points (replenishment_planner_agent)

Each function below is the CONTRACT the matching connector must honour.
In prod, replace the body with the MCP call (point ${WMS_MCP_URL} /
${TMS_MCP_URL} / ${ERP_MCP_URL} / ${FLEET_API_MCP_URL} at your gateway);
the return shape is identical. Setting the env var alone does nothing
until you wire the call here.

Day-0 demo: the seed below carries one realistic order (ORD-4471) whose
two SKUs sit in bins A-014 / C-103, one fully-stocked SKU and one
short-stocked SKU (to exercise the out-of-stock allocation gate), a
three-unit humanoid fleet (one on low battery), an open outbound
shipment, and a carrier rate table.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
_DIR = Path("./data")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(k: str) -> Path:
    _DIR.mkdir(parents=True, exist_ok=True)
    return _DIR / f"{k}.json"


def _load(k: str) -> dict[str, Any]:
    p = _path(k)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (ValueError, OSError):
        return {}


def _save(k: str, d: dict[str, Any]) -> None:
    p = _path(k)
    t = p.with_suffix(".json.tmp")
    t.write_text(json.dumps(d, indent=2, default=str))
    t.replace(p)


# ── Seeds ────────────────────────────────────────────────────────────
# Inventory keyed by SKU. on_hand − reserved = available. SKU-9001 is
# fully stocked; SKU-7700 is short-stocked (available 1 < demand) so the
# availability gate fires on a 2-unit order line.
_SEED_INVENTORY = {
    "SKU-9001": {"sku": "SKU-9001", "description": "Trail Runner 42",
                 "zone": "A", "bin": "A-014", "on_hand": 40, "reserved": 6,
                 "reorder_point": 12, "target_qty": 60, "unit_weight_kg": 0.6,
                 "lead_time_days": 7, "supplier_id": "SUP-1001",
                 "unit_value_cents": 11900},
    "SKU-7700": {"sku": "SKU-7700", "description": "Summit Daypack 28L",
                 "zone": "C", "bin": "C-103", "on_hand": 3, "reserved": 2,
                 "reorder_point": 8, "target_qty": 40, "unit_weight_kg": 0.9,
                 "lead_time_days": 14, "supplier_id": "SUP-1002",
                 "unit_value_cents": 8900},
    # High-value SKU (and out of stock) — exercises BOTH the
    # critical-stockout floor (replenishment) and the exception
    # severity/value floor: a damage/loss on this line can't be
    # down-rated to a front-line resolve.
    "SKU-3300": {"sku": "SKU-3300", "description": "Alpine Pro Optic 1L",
                 "zone": "A", "bin": "A-051", "on_hand": 0, "reserved": 0,
                 "reorder_point": 20, "target_qty": 120, "unit_weight_kg": 0.3,
                 "lead_time_days": 5, "supplier_id": "SUP-1001",
                 "unit_value_cents": 64900},
}

# The day-0 order. ORD-4471 picks one fully-stocked and one short line so
# the demo runs clean OR fails the availability gate depending on qty.
_SEED_ORDERS = {
    "ORD-4471": {
        "order_id": "ORD-4471", "customer_id": "CUST-Maria-Denver",
        "ship_method": "standard", "received_at_utc": "2026-06-23T14:07:00Z",
        "ship_to": {"name": "Maria R.", "city": "Denver", "state": "CO",
                    "postal_code": "80202", "country": "US"},
        "line_items": [
            {"sku": "SKU-9001", "quantity": 1},
            {"sku": "SKU-7700", "quantity": 1},
        ],
    },
    # ORD-4490 over-demands the short SKU — exercises the allocation gate.
    "ORD-4490": {
        "order_id": "ORD-4490", "customer_id": "CUST-Lee-Austin",
        "ship_method": "express_overnight", "received_at_utc": "2026-06-23T15:30:00Z",
        "ship_to": {"name": "Lee K.", "city": "Austin", "state": "TX",
                    "postal_code": "78701", "country": "US"},
        "line_items": [
            {"sku": "SKU-7700", "quantity": 5},   # available 1 < 5 → gate
        ],
    },
}

# Humanoid fleet roster (Fleet API). One unit on low battery → the
# dispatcher routes around it.
_SEED_FLEET = {
    "hpkr-01": {"unit_id": "hpkr-01", "role": "picker", "status": "online",
                "battery_pct": 0.82, "zone": "A", "fault_code": ""},
    "hpkr-02": {"unit_id": "hpkr-02", "role": "picker", "status": "online",
                "battery_pct": 0.17, "zone": "C", "fault_code": ""},
    "hins-01": {"unit_id": "hins-01", "role": "inspector", "status": "online",
                "battery_pct": 0.91, "zone": "QA", "fault_code": ""},
    "hpck-01": {"unit_id": "hpck-01", "role": "packer", "status": "online",
                "battery_pct": 0.74, "zone": "PACK", "fault_code": ""},
    "hldr-01": {"unit_id": "hldr-01", "role": "loader", "status": "online",
                "battery_pct": 0.66, "zone": "DOCK", "fault_code": ""},
}

# Carrier rate table (TMS). cost_cents per kg-band + transit days.
_SEED_CARRIERS = [
    {"carrier": "usps", "service_level": "ground", "base_cents": 650,
     "per_kg_cents": 120, "transit_days": 4, "max_kg": 9.0},
    {"carrier": "ups", "service_level": "ground", "base_cents": 820,
     "per_kg_cents": 95, "transit_days": 3, "max_kg": 30.0},
    {"carrier": "fedex", "service_level": "2day", "base_cents": 1450,
     "per_kg_cents": 110, "transit_days": 2, "max_kg": 30.0},
    {"carrier": "ups", "service_level": "overnight", "base_cents": 3200,
     "per_kg_cents": 140, "transit_days": 1, "max_kg": 30.0},
]
DEFAULT_CARRIER_CENTS = 1800   # the un-shopped default this agency beats

# ── Supplier scorecards + performance history (ERP/SRM) ──────────────
# The supplier_relations_agent reasons over THIS — real performance
# history per supplier, not a spoon-fed blob in the inbound payload. The
# scorecard MATH (on_time_pct, defect_ppm, composite_score) is computed
# upstream in your ERP/WMS; the agent grades + flags it. SUP-1001 is a
# solid A; SUP-1002 has a quality slide (rising defect PPM, slipping
# on-time) so the scorecard fires requires_action; SUP-2003 is a market
# alternate carried for the sourcing responsibility. In PROD swap the
# bodies below for the SRM/ERP read (point ${ERP_MCP_URL} at your
# gateway, tool get_supplier_scorecards) — the return shape is identical.
_SEED_SUPPLIERS = {
    "SUP-1001": {
        "supplier_id": "SUP-1001", "legal_name": "Summit Outfitters LLC",
        "categories": ["footwear", "apparel"], "country": "US",
        "esg_risk": "low", "kyc_status": "approved", "preferred": True,
        "lead_time_days": 7,
        "scorecard": {"on_time_pct": 0.97, "defect_ppm": 220,
                      "response_hours_median": 6, "pricing_trend_pct": -0.01,
                      "esg_compliance": "current", "composite_score": 0.91},
        "history": [
            {"period": "2025-Q3", "on_time_pct": 0.96, "defect_ppm": 260,
             "composite_score": 0.89},
            {"period": "2025-Q4", "on_time_pct": 0.97, "defect_ppm": 240,
             "composite_score": 0.90},
            {"period": "2026-Q1", "on_time_pct": 0.97, "defect_ppm": 220,
             "composite_score": 0.91},
        ],
    },
    "SUP-1002": {
        "supplier_id": "SUP-1002", "legal_name": "Daypack Industries Inc",
        "categories": ["bags", "apparel"], "country": "VN",
        "esg_risk": "medium", "kyc_status": "approved", "preferred": True,
        "lead_time_days": 14,
        # Quality slide: defect PPM climbing, on-time slipping below 0.85.
        "scorecard": {"on_time_pct": 0.82, "defect_ppm": 1450,
                      "response_hours_median": 31, "pricing_trend_pct": 0.04,
                      "esg_compliance": "current", "composite_score": 0.61},
        "history": [
            {"period": "2025-Q3", "on_time_pct": 0.91, "defect_ppm": 540,
             "composite_score": 0.78},
            {"period": "2025-Q4", "on_time_pct": 0.87, "defect_ppm": 910,
             "composite_score": 0.71},
            {"period": "2026-Q1", "on_time_pct": 0.82, "defect_ppm": 1450,
             "composite_score": 0.61},
        ],
    },
    # Market alternate (not yet preferred) for the sourcing responsibility.
    "SUP-2003": {
        "supplier_id": "SUP-2003", "legal_name": "Highland Gear Co",
        "categories": ["bags", "footwear"], "country": "US",
        "esg_risk": "low", "kyc_status": "pending", "preferred": False,
        "lead_time_days": 10,
        "scorecard": {"on_time_pct": 0.93, "defect_ppm": 380,
                      "response_hours_median": 9, "pricing_trend_pct": 0.0,
                      "esg_compliance": "current", "composite_score": 0.84},
        "history": [],
    },
}


def _inventory() -> dict[str, Any]:
    s = _load("inventory")
    if not s:
        s = {k: dict(v) for k, v in _SEED_INVENTORY.items()}
        _save("inventory", s)
    return s


def _orders() -> dict[str, Any]:
    s = _load("orders")
    if not s:
        s = {k: dict(v) for k, v in _SEED_ORDERS.items()}
        _save("orders", s)
    return s


def _fleet() -> dict[str, Any]:
    s = _load("fleet")
    if not s:
        s = {k: dict(v) for k, v in _SEED_FLEET.items()}
        _save("fleet", s)
    return s


# ── Orders (OMS) ─────────────────────────────────────────────────────
def get_order(order_id: str) -> dict[str, Any]:
    """The order header + lines. PROD: OMS read."""
    return dict(_orders().get(str(order_id or ""), {}))


# ── Inventory / bins (WMS) ───────────────────────────────────────────
def get_sku(sku: str) -> dict[str, Any]:
    return dict(_inventory().get(str(sku or ""), {}))


def available(sku: str) -> int:
    """on_hand − reserved for one SKU. PROD: WMS availability read."""
    rec = _inventory().get(str(sku or ""))
    if not rec:
        return 0
    return max(0, int(rec.get("on_hand", 0)) - int(rec.get("reserved", 0)))


def bin_for(sku: str) -> dict[str, str]:
    """The pick face (zone + bin) for a SKU. PROD: WMS slotting read."""
    rec = _inventory().get(str(sku or ""))
    if not rec:
        return {"zone": "", "bin": ""}
    return {"zone": str(rec.get("zone", "")), "bin": str(rec.get("bin", ""))}


def reserve(sku: str, qty: int) -> bool:
    """Reserve qty units if available. PROD: WMS reservation write.
    Returns True on success (reservation held), False on shortage."""
    with _LOCK:
        inv = _inventory()
        rec = inv.get(str(sku or ""))
        if not rec:
            return False
        if int(rec.get("on_hand", 0)) - int(rec.get("reserved", 0)) < int(qty):
            return False
        rec["reserved"] = int(rec.get("reserved", 0)) + int(qty)
        _save("inventory", inv)
        return True


def restock(sku: str, qty: int) -> int:
    """Add qty to on_hand (inbound / returns). Returns new on_hand."""
    with _LOCK:
        inv = _inventory()
        rec = inv.setdefault(str(sku), {"sku": sku, "on_hand": 0, "reserved": 0,
                                        "zone": "", "bin": ""})
        rec["on_hand"] = int(rec.get("on_hand", 0)) + int(qty)
        _save("inventory", inv)
        return int(rec["on_hand"])


def all_skus() -> list[dict[str, Any]]:
    return [dict(v) for v in _inventory().values()]


# ── Humanoid fleet (Fleet API) ───────────────────────────────────────
def fleet_status() -> dict[str, Any]:
    """Aggregate telemetry for humanoid_status_agent's contract."""
    units = list(_fleet().values())
    faults = [{"unit_id": u["unit_id"], "fault_code": u["fault_code"],
               "severity": "high"} for u in units if u.get("fault_code")]
    return {
        "total_units": len(units),
        "offline_units": sum(1 for u in units if u.get("status") != "online"),
        "low_battery_units": sum(1 for u in units
                                 if float(u.get("battery_pct", 1)) < 0.20),
        "fault_alerts": faults,
    }


def available_unit(role: str, min_battery: float = 0.20) -> dict[str, Any]:
    """Pick an online unit of the role with enough charge. PROD: fleet
    dispatch reservation. Returns {} when the fleet has no ready unit."""
    candidates = [u for u in _fleet().values()
                  if u.get("role") == role and u.get("status") == "online"
                  and not u.get("fault_code")
                  and float(u.get("battery_pct", 0)) >= min_battery]
    candidates.sort(key=lambda u: float(u.get("battery_pct", 0)), reverse=True)
    return dict(candidates[0]) if candidates else {}


# ── Carriers (TMS) ───────────────────────────────────────────────────
def carrier_rates() -> list[dict[str, Any]]:
    return [dict(r) for r in _SEED_CARRIERS]


# ── Shipments (audit/handoff scratch) ────────────────────────────────
def record_shipment(order_id: str, carton_id: str, payload: dict[str, Any]) -> None:
    with _LOCK:
        s = _load("shipments")
        s[str(carton_id or order_id)] = {"order_id": order_id,
                                         "carton_id": carton_id,
                                         "at": _now(), **(payload or {})}
        _save("shipments", s)


def get_shipment(carton_id: str) -> dict[str, Any]:
    return dict(_load("shipments").get(str(carton_id or ""), {}))


# ── Suppliers (ERP / SRM) ────────────────────────────────────────────
def get_supplier(supplier_id: str) -> dict[str, Any]:
    """One supplier's master + current scorecard + history. PROD: SRM read."""
    return dict(_SEED_SUPPLIERS.get(str(supplier_id or ""), {}))


def supplier_scorecard(supplier_id: str) -> dict[str, Any]:
    """The current-period scorecard signals for one supplier (the math is
    computed upstream in your ERP/WMS). Returns {} if unknown."""
    return dict((_SEED_SUPPLIERS.get(str(supplier_id or "")) or {})
                .get("scorecard", {}))


def supplier_history(supplier_id: str) -> list[dict[str, Any]]:
    """Trailing-quarter performance history for one supplier — lets the
    relationship manager reason over the TREND, not just this quarter."""
    return [dict(h) for h in (_SEED_SUPPLIERS.get(str(supplier_id or "")) or {})
            .get("history", [])]


def all_suppliers() -> list[dict[str, Any]]:
    """Every supplier on file (with scorecard + history). PROD: SRM list."""
    return [dict(v) for v in _SEED_SUPPLIERS.values()]


def supplier_signals() -> list[dict[str, Any]]:
    """The scorecard signal rows across ALL suppliers — the precomputed
    input the quarterly scorecard responsibility grades. Each row is the
    current scorecard plus identity. PROD: ERP/WMS aggregation read."""
    rows = []
    for s in _SEED_SUPPLIERS.values():
        sc = s.get("scorecard") or {}
        rows.append({"supplier_id": s.get("supplier_id"),
                     "legal_name": s.get("legal_name"),
                     "categories": list(s.get("categories") or []),
                     "esg_risk": s.get("esg_risk"), **sc})
    return rows


def suppliers_for_category(category: str, esg_floor: str = "medium"
                           ) -> list[dict[str, Any]]:
    """Candidate suppliers serving a category at/above an ESG floor — the
    sourcing responsibility's candidate pool. PROD: SRM catalog query."""
    rank = {"low": 0, "medium": 1, "high": 2}
    cap = rank.get(str(esg_floor or "medium"), 1)
    out = []
    for s in _SEED_SUPPLIERS.values():
        if str(category or "") not in (s.get("categories") or []):
            continue
        if rank.get(str(s.get("esg_risk") or "high"), 2) > cap:
            continue
        sc = s.get("scorecard") or {}
        out.append({"supplier_id": s.get("supplier_id"),
                    "legal_name": s.get("legal_name"),
                    "categories": list(s.get("categories") or []),
                    "country": s.get("country"), "esg_risk": s.get("esg_risk"),
                    "preferred": bool(s.get("preferred")),
                    "lead_time_days": s.get("lead_time_days"),
                    "recent_on_time_pct": sc.get("on_time_pct", 0)})
    return out


# ── Exception context (the order/inventory/fleet behind an exception) ──
def exception_context(order_id: str = "", sku: str = "", unit_id: str = ""
                      ) -> dict[str, Any]:
    """Pull the order header, the affected SKU's inventory record (value,
    bin, availability) and the fleet unit behind an exception so the
    handler reasons over real context — not whatever the upstream payload
    happened to echo. PROD: OMS + WMS + fleet reads."""
    order = get_order(order_id) if order_id else {}
    sku = sku or _first_sku(order)
    rec = get_sku(sku) if sku else {}
    unit = {}
    if unit_id:
        unit = dict(_fleet().get(str(unit_id), {}))
    return {
        "order_id": str(order_id or order.get("order_id") or ""),
        "order": order,
        "sku": sku,
        "inventory": rec,
        "unit_value_cents": int(rec.get("unit_value_cents") or 0),
        "available": available(sku) if sku else 0,
        "unit": unit,
    }


def _first_sku(order: dict[str, Any]) -> str:
    items = (order or {}).get("line_items") or []
    for li in items:
        if isinstance(li, dict) and li.get("sku"):
            return str(li["sku"])
    return ""
