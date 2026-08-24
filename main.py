"""warehouse-supply-chain — Warehouse / Supply Chain (with humanoid integration).

Config-as-department: configs/config.yaml casts the roles, the wires
carry the deterministic policy floors, and the WMS / ERP connectors are
the systems of record. The depth lives in agency/*_agent.py and
agency/*_humanoid.py (auto-discovered): self-reliant @pre_compose context
pulls from agency/_shared/store.py, deterministic @chain floors
(availability gate, shrink threshold, rate-shop rule, ship-readiness),
the humanoid task-dispatch actuators, idempotency + bounded loops via
agency/_shared/session_stash.py. The only thing registered here besides
the SDK boot is the hash-chained WORM audit sink
(agency/_shared/audit_logger.py).
"""
import asyncio
import signal
import sys

from dotenv import load_dotenv
from leafmesh import EventType, LeafMesh, LeafMeshLogger

import agency.tools  # noqa: F401 — registers @global_tool tools BEFORE from_yaml/start
from agency._shared import audit_logger as _audit_logger

load_dotenv()
logger = LeafMeshLogger(__name__)
leafmesh = LeafMesh.from_yaml("configs/config.yaml")

# Register the WORM audit sink as the intelligence function for the
# custom external warehouse_audit_logger_agent (SDK 2.4+ API).
_audit_logger.register(leafmesh, "warehouse_audit_logger_agent")


async def _on_agent_error(event):
    logger.warning(f"[wh-audit] AGENT_ERROR session={event.session_id}")

async def _on_manager_intervention(event):
    logger.warning(f"[wh-ops] MANAGER_INTERVENTION session={event.session_id}")

async def _on_intervention_needed(event):
    logger.error(f"[wh-ops] INTERVENTION_NEEDED session={event.session_id}")

async def _on_human_input_timeout(event):
    logger.warning(f"[wh-audit] HITL TIMEOUT session={event.session_id}")


async def main():
    await leafmesh.start()
    leafmesh.event_bus.subscribe(EventType.AGENT_ERROR,           _on_agent_error)
    leafmesh.event_bus.subscribe(EventType.MANAGER_INTERVENTION,  _on_manager_intervention)
    leafmesh.event_bus.subscribe(EventType.INTERVENTION_NEEDED,   _on_intervention_needed)
    leafmesh.event_bus.subscribe(EventType.HUMAN_INPUT_TIMEOUT,   _on_human_input_timeout)
    try:
        await leafmesh.enable_self_healing()
    except AttributeError:
        pass

    logger.info("warehouse-supply-chain — Warehouse / Supply Chain mesh running")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
    else:
        try: await stop.wait()
        except KeyboardInterrupt: pass

    await leafmesh.stop()


if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
