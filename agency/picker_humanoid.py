"""picker_humanoid — physical pick from bins (humanoid fleet dispatcher).

DEEP (self-reliant + actuator + idempotency):

@pre_compose  load_pick_context — pull the pick sequence and reserve a
              READY humanoid unit from the fleet (online, charged, not
              faulted) BEFORE the pick runs. In dev this comes from
              agency/_shared/store.py (seeded fleet); in prod from the
              fleet-management API. Self-reliant: the picker chooses its
              own unit rather than trusting an upstream assignment.

REAL ACTUATOR  _dispatch_pick is the humanoid task-dispatch side effect —
              swap the body for the real fleet dispatch call; the return
              shape is the contract. Guarded for IDEMPOTENCY via the
              session stash so a re-entered chain never double-dispatches
              the same pick task.
"""
from __future__ import annotations

import uuid
from typing import Any

from leafmesh import LeafMeshLogger, pre_compose

from agency._shared import session_stash, store

logger = LeafMeshLogger(__name__)


def _ctx(context: Any) -> dict[str, Any]:
    prepared = (context or {}).get("prepared_data", {}) if isinstance(context, dict) else {}
    c = prepared.get("context", {}) if isinstance(prepared, dict) else {}
    return c if isinstance(c, dict) else {}


# ── @pre_compose — reserve a ready fleet unit + pull the sequence ─────
def load_pick_context(data: Any, ctx: Any) -> dict[str, Any]:
    """Reserve an online, charged picker unit. PROD: fleet dispatch hold."""
    input_data = data if isinstance(data, dict) else {}
    sequence = list(input_data.get("pick_sequence")
                    or (input_data.get("upstream_yields") or {}).get("pick_sequence")
                    or [])
    unit = store.available_unit("picker")
    return {"pick_sequence": sequence, "unit": unit,
            "unit_id": unit.get("unit_id", "")}


def _dispatch_pick(unit_id: str, sequence: list[Any]) -> dict[str, Any]:
    """ACTUATOR — dispatch the physical pick to the humanoid fleet.
    PROD: fleet_api.dispatch(unit_id, route=sequence). DEV: simulate a
    clean pick of the sequenced units."""
    units = sum(int(s.get("qty") or 1) for s in sequence if isinstance(s, dict)) \
        or len(sequence)
    return {"picked_units": units, "pick_status": "picked", "damage_flag": False}


async def picker_humanoid(llm_response: Any, input_data: dict[str, Any],
                          context: dict[str, Any]) -> dict[str, Any]:
    input_data = input_data if isinstance(input_data, dict) else {}
    pctx = _ctx(context)
    sequence = pctx.get("pick_sequence") or list(input_data.get("pick_sequence") or [])
    unit = pctx.get("unit") or store.available_unit("picker")
    picker_id = str(unit.get("unit_id") or f"hpkr-{uuid.uuid4().hex[:8]}")

    # ── Test/scenario overrides (drive the damage/short exception path) ─
    up = context.get("upstream") if isinstance(context, dict) else None
    up = up if isinstance(up, dict) else {}
    forced_status = up.get("_test_pick_status")
    forced_damage = up.get("_test_damage")

    # ── Idempotency: dispatch the actuator only once per (session, task) ─
    sid = session_stash.resolve_session_id(input_data, context)
    task_key = f"pick:{picker_id}:{len(sequence)}"
    first = session_stash.mark_once(sid, task_key)

    if forced_status is not None or forced_damage is not None:
        status = str(forced_status or "damaged")
        damage = bool(forced_damage)
        units = int(up.get("_test_picked_units") or len(sequence))
    elif first:
        dispatched = _dispatch_pick(picker_id, sequence)
        status = dispatched["pick_status"]
        damage = dispatched["damage_flag"]
        units = dispatched["picked_units"]
    else:
        # Re-entry — do NOT re-dispatch the fleet; report the prior pick.
        logger.info(f"[picker:{picker_id}] idempotent re-entry — not re-dispatching")
        status, damage, units = "picked", False, len(sequence)

    logger.info(
        f"[picker:{picker_id}] status={status} units={units} damage={damage} "
        f"dispatched={first}"
    )
    return {
        "pick_status": status,
        "picked_units": units,
        "damage_flag": damage,
        "picker_id": picker_id,
    }
