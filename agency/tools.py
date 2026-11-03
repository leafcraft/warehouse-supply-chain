"""Global tools for the warehouse pod — the on-demand dev-store surface.

These are the supplier relationship manager's day-0 tools — the lookups it calls,
in the turn, when the @pre_compose context did not already carry what it needs.
Each reads the dev store (seeded ERP/SRM) so the pod runs with NO connector
wired, and **each announces itself**: when there is no real source and no seed,
it returns `available: false` and says why — never a synthetic success that looks
like a real read. To go live, create the vault record 'erp_mcp' and wire the
ERP/SRM connector in config.yaml; these dev tools are the fallback contract.
They check that vault record themselves; nothing is read from .env.
"""
from __future__ import annotations

import os

from leafmesh import LeafMeshLogger, global_tool

try:
    from ._shared import store
    from ._shared.wiring import vault_record_exists
except ImportError:  # pragma: no cover — direct-run fallback
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _shared import store  # type: ignore
    from _shared.wiring import vault_record_exists  # type: ignore

logger = LeafMeshLogger(__name__)


async def _erp_wired() -> bool:
    """True once the vault record 'erp_mcp' exists (Infrastructure →
    Integrations → MCP)."""
    return await vault_record_exists("erp_mcp")


@global_tool(category="supplier")
async def lookup_supplier(supplier_id: str) -> dict:
    """Look up one supplier's master record, current scorecard and trailing
    performance history — for onboarding state and scorecard trend reasoning.

    Returns `available: false` (with a reason) when the supplier is unknown and
    no real ERP/SRM connector is wired — never an empty-but-confident record.

    Args:
        supplier_id: The supplier to look up (e.g. "SUP-1001").
    """
    rec = store.get_supplier(str(supplier_id or "").strip())
    if not rec:
        reason = ("ERP record 'erp_mcp' exists, but this dev tool reads only the seed"
                  if await _erp_wired()
                  else f"no seed supplier {supplier_id!r}; create vault record 'erp_mcp' for the live ERP/SRM")
        return {"available": False, "reason": reason}
    logger.info(f"lookup_supplier {supplier_id} available=True")
    return {"available": True, "supplier": rec}


@global_tool(category="supplier")
async def find_alternates(category: str, esg_floor: str = "medium") -> dict:
    """Find qualified alternate suppliers serving a category at/above an ESG
    floor — the sourcing responsibility's candidate pool.

    Returns `available: false` when the category is empty or no candidate exists
    and no ERP is wired — a skip is NEVER reported as "no qualified alternates".

    Args:
        category: The product category to source (e.g. "bags").
        esg_floor: Minimum ESG standing — "low" or "medium" (default "medium").
    """
    category = str(category or "").strip()
    if not category:
        return {"available": False, "reason": "no category given"}
    candidates = store.suppliers_for_category(category, str(esg_floor or "medium"))
    if not candidates:
        # Never report "no qualified alternates" from a seed that simply has none.
        reason = ("ERP record 'erp_mcp' exists, but this dev tool reads only the seed"
                  if await _erp_wired() else
                  f"no seed alternates for {category!r}; create vault record 'erp_mcp' for the live ERP/SRM")
        return {"available": False, "reason": reason}
    return {"available": True, "category": category, "candidates": candidates}
