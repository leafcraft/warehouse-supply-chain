"""Deterministic cycle-count / shrink arithmetic — the floor the LLM can't waive.

Reconciles physical counts against system on-hand and flags shrink:

  * VARIANCE TOLERANCE — any SKU whose |physical − system| / system
    exceeds VARIANCE_TOLERANCE_PCT is over tolerance and must raise a
    floor follow-up exception.
  * SYSTEMATIC SHRINK — a zone whose average variance runs below
    −SHRINK_ZONE_PCT across at least SHRINK_MIN_SAMPLES counts is
    systematically short (theft / mis-slotting / damage) and goes to the
    warehouse manager.
  * MANAGER-ATTENTION — any systematic-short zone OR more than
    OVER_TOLERANCE_MANAGER_CAP SKUs over tolerance.

These constants ARE the inventory-control policy — a controller tunes
them here, not by re-prompting a model. Production reads the count ledger
from the WMS; the arithmetic is identical.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

VARIANCE_TOLERANCE_PCT = 0.02        # 2% single-SKU tolerance
SHRINK_ZONE_PCT = 0.02               # avg < −2% across a zone is systematic
SHRINK_MIN_SAMPLES = 3               # min counts before calling a zone short
OVER_TOLERANCE_MANAGER_CAP = 20      # >20 over-tolerance SKUs → manager


def variance_pct(system_qty: int, physical_qty: int) -> float:
    """Signed variance: (physical − system) / system. Negative = shrink."""
    system_qty = int(system_qty or 0)
    if system_qty == 0:
        return 0.0 if int(physical_qty or 0) == 0 else 1.0
    return round((int(physical_qty or 0) - system_qty) / system_qty, 4)


def reconcile(counts: list[dict[str, Any]]) -> dict[str, Any]:
    """Reconcile a batch of physical counts vs system on-hand.

    counts: [{sku, zone, bin, system_qty, physical_qty}]. Returns the
    inventory_control_agent connector contract (the shape the WMS gateway
    must also return in prod) with the deterministic floors computed.
    """
    over_tolerance: list[dict[str, Any]] = []
    zone_samples: dict[str, list[float]] = defaultdict(list)
    max_var = 0.0
    total_var_units = 0

    for c in counts or []:
        if not isinstance(c, dict):
            continue
        sku = str(c.get("sku") or "")
        zone = str(c.get("zone") or "")
        sys_q = int(c.get("system_qty") or 0)
        phy_q = int(c.get("physical_qty") or 0)
        vp = variance_pct(sys_q, phy_q)
        zone_samples[zone].append(vp)
        total_var_units += abs(phy_q - sys_q)
        max_var = max(max_var, abs(vp))
        if abs(vp) > VARIANCE_TOLERANCE_PCT:
            over_tolerance.append({
                "sku": sku, "zone": zone, "bin": str(c.get("bin") or ""),
                "system_qty": sys_q, "physical_qty": phy_q,
                "variance_units": phy_q - sys_q, "variance_pct": vp,
            })

    systematic: list[dict[str, Any]] = []
    for zone, samples in zone_samples.items():
        if len(samples) >= SHRINK_MIN_SAMPLES:
            avg = sum(samples) / len(samples)
            if avg < -SHRINK_ZONE_PCT:
                systematic.append({"zone": zone,
                                   "avg_variance_pct": round(avg, 4),
                                   "samples": len(samples)})

    return {
        "skus_counted": len([c for c in counts or [] if isinstance(c, dict)]),
        "total_counts": len([c for c in counts or [] if isinstance(c, dict)]),
        "max_variance_pct": round(max_var, 4),
        "over_tolerance_count": len(over_tolerance),
        "over_tolerance": over_tolerance,
        "variance_skus": [{"sku": o["sku"], "zone": o["zone"],
                           "bin": o["bin"], "variance_pct": o["variance_pct"]}
                          for o in over_tolerance],
        "systematic_short_zones": systematic,
        "systematic_short_zone_count": len(systematic),
        "total_variance_units": total_var_units,
    }


def requires_manager_action(recon: dict[str, Any]) -> bool:
    """The manager-attention floor (mirrors the can_call wire condition)."""
    return (int(recon.get("systematic_short_zone_count") or 0) > 0
            or int(recon.get("over_tolerance_count") or 0)
            > OVER_TOLERANCE_MANAGER_CAP)
