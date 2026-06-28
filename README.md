# warehouse-supply-chain — Warehouse / Supply Chain (with humanoid integration)

Realises the **supply × goods** ROI cell, and introduces the FOURTH
actor kind — **humanoids** ($3-8/hr projected) doing the physical share
of the work alongside the agents that reason about it.

## Story

It's 14:07 on a Tuesday. **Maria**, a customer in Denver, places order
`ORD-4471` for two SKUs through the storefront; the OMS fires its
webhook into the mesh at `order_placed`. `allocation_agent` (warehouse
domain math) builds the pick plan, batches it, tags it `standard`
priority, bin-sorts the two lines into a travel-minimised pick sequence,
estimates three minutes, and assigns `picker_humanoid` — while
`inventory_agent` reserves the units in the WMS in parallel. The
humanoid picker walks the route, picks clean, and hands off to
`packer_humanoid`, which boxes carton `CTN-…` and calls
`loader_humanoid`. Loading writes the WORM audit line and the order is
on the dock. Maria's parcel ships that afternoon, untouched by a human.

Now order `ORD-4472`: the picker reports a crushed corner —
`damage_flag` true. The mesh forks: `inspection_humanoid` verifies the
item is not safe to ship, and `exception_agent` classifies it. Severity
lands at 0.8, so the wire condition (`severity >= 0.7`) routes it
straight to **Dwayne**, the on-shift **ops supervisor**, with a
2-sentence floor briefing on his tablet — and the manager's narration
hints that, since this lot came from inbound supplier goods, the
supplier relationship manager should start inspecting that supplier's
inbound lots. Dwayne reads it in 90 seconds, approves a substitution,
and the trail is hash-chained into the WORM audit. Nobody watched a
dashboard; the routine ran itself and the one judgment call reached the
right human fast.

## Depth — self-reliant agents, deterministic floors

This is not a thin YAML wrapper. The agents pull their own context and
enforce their own floors:

- **Self-reliant context (`@pre_compose`)** — `allocation_agent` pulls the
  order, bin/slotting locations and free-stock availability;
  `picker_humanoid` reserves a ready fleet unit; `inventory_control_agent`
  pulls the SKU master; `carrier_selector_agent` pulls the carton weight +
  rate table. All from the seeded dev store (`agency/_shared/store.py`) day-0,
  the live system in prod.
- **Deterministic floors (`@chain`) the LLM can't waive** — the
  **availability gate** (a short order can't dispatch a picker), the
  **shrink / cycle-count threshold** (2% variance + systematic-shrink
  manager floor), the **carrier rate-shop rule** (cheapest-meeting-SLA,
  `sla_met` re-derived), and **ship-readiness** (no empty/weightless carton
  ships). Plus the exception severity/safety floor on the wires.
- **Real actuators + idempotency** — the humanoid task-dispatch side
  effects run once per `(session, task)` via `agency/_shared/session_stash.py`;
  a re-entered chain never double-dispatches a pick.
- **Day-0, no connectors** — the seeded store carries a realistic order
  (`ORD-4471`), inventory with one short SKU (`SKU-7700`, to exercise the
  gate + damage/exception path), a 5-unit humanoid fleet (one on low
  battery) and a carrier rate table, so the FULL chain runs out of the box.

## Why it works in an organization

Replenishment, picking, packing, cycle-counting and carrier selection
are ~75% routine and historically run on people **watching dashboards**
and reacting. This agency absorbs the routine and *acts*: agents pull
on-hand counts, reserve stock, sequence pick routes, rate-shop carriers,
sweep cycle counts and score suppliers — and the humanoids do the
physical pick / inspect / pack / load. The ROI is direct: COGS and
labour are the dominant cost line in goods, per-parcel carrier selection
typically recovers 8-12% of shipping spend that leaks without it, and
cycle-count + shrink-zone detection catches inventory loss before it
compounds. Humans are pulled in **only** at genuine judgment or
irreversible points (damage/safety exceptions, ESG/KYC supplier gates,
critical stockouts) — default-deny on timeout — so supervisory headcount
scales with exceptions, not with volume. Every pick, pack, ship,
exception and humanoid action lands in a hash-chained WORM audit, which
is what makes the autonomy defensible.

## Configuration (three tiers)

### Tier 1 — Boot (required to start)

| Var | Required | Purpose |
|---|---|---|
| `LEAFMESH_LICENSE_KEY` | yes | SDK license |
| `OPENAI_API_KEY` | yes | the two `llm` agents (`gpt-4o-mini`) + manager |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` | host-provided | session store / auto-storage |
| `LEAFMESH_ENV_TOKEN` | yes | environment token (`development`) |
| `ANTHROPIC_API_KEY` | optional | only if you switch a model to Claude |

### Tier 2 — Host prereqs + system-of-record adapters

**The mesh runs end-to-end day-0 with NONE of these set** — the deepened
agents read the seeded dev store (`agency/_shared/store.py`). Set a URL
only when you swap that agent for the live system of record.

Pure MCP connectors (`integration: mcp` + `connector_config`; `yields:` is
the contract your gateway must return; unset → empty contract until wired):

| Env var | System of record | Agents (tool_name) |
|---|---|---|
| `WMS_MCP_URL` | WMS (Manhattan, Blue Yonder, NetSuite WMS, …) | `inventory_agent` (check_and_reserve_stock), `returns_handler_agent` (process_return) |
| `ERP_MCP_URL` | ERP (SAP, Oracle, NetSuite, Odoo, …) | `replenishment_planner_agent` (get_reorder_recommendations) |

Store-grounded deepened agents (read the seeded store until you swap the
module body for the live read — the PROD swap targets):

| Env var | System of record | Agent | PROD swap |
|---|---|---|---|
| `WMS_MCP_URL` | WMS count ledger | `inventory_control_agent` | `get_cycle_count_results` |
| `TMS_MCP_URL` | TMS / rate API (Shippo, EasyPost, …) | `carrier_selector_agent` | `select_carrier` |
| `FLEET_API_MCP_URL` | robot fleet API | `humanoid_status_agent` | `get_fleet_status` |

Redis (`docker run -d -p 6379:6379 redis:7-alpine`) is the only hard
host prereq.

### Tier 3 — Connectors / escalation targets (optional)

| Env var | Used by | Notes |
|---|---|---|
| `PAGER_WEBHOOK` | manager escalation `webhook` target | PagerDuty Events v2 default |
| `SLACK_WAREHOUSE_CHANNEL` | manager escalation `channel` target | the target's `channel_id` (the `[warehouse]` alert) |
| `SLACK_BOT_TOKEN` / `SLACK_SIGNING_SECRET` | — | fields of a `channels: {slack: {...}}` block (`ChannelConfig`). This template has **no `channels:` block**, so they are **not auto-consumed** — the escalation `channel` target only reads `channel_id`. Add a `channels:` block to use them. |

## Agent table

Role = the human job title each agent stands in for.

| Agent | Role | Responsibilities | Can perform |
|---|---|---|---|
| `allocation_agent` | Warehouse allocation & routing planner | Pull order/bins/availability (`@pre_compose`), build pick plan, batch, set priority, bin-sort the travel-min route, assign the picking resource; enforce the availability gate (`@chain`); passthrough `order_id` | programmatic + domain math; calls `inventory_agent` (reserve), `exception_agent` (short order), `picker_humanoid` (available pick) or `warehouse_manager_human` (hazmat → human picker) |
| `inventory_agent` | Inventory clerk (WMS) | Confirm stock and reserve units in the WMS | MCP connector; calls `exception_agent` on shortage |
| `exception_agent` | Floor exceptions analyst | Classify short-pick / damage / wrong-item / safety, set severity, recommend action, write the supervisor briefing | LLM; wire floors → `ops_supervisor_human` (safety / severity≥0.7) else `warehouse_audit_logger_agent`; narration → manager / supplier-relations |
| `picker_humanoid` | Picker (humanoid) | Reserve a ready fleet unit (`@pre_compose`), dispatch the physical pick (idempotent actuator); report status / units / damage | programmatic actor; calls `inspection_humanoid` (damage), `exception_agent` (not clean), `packer_humanoid` (clean) |
| `inspection_humanoid` | Quality inspector (humanoid) | Verify condition of flagged items; set safe-to-ship | programmatic actor; calls `packer_humanoid` (pass) or `exception_agent` (fail) |
| `packer_humanoid` | Packer (humanoid) | Pack picked items into a carton; enforce ship-readiness (`@chain`); record the shipment | programmatic actor; calls `loader_humanoid` |
| `loader_humanoid` | Dock loader (humanoid) | Load cartons onto outbound trucks; stamp the shipment loaded | programmatic actor; calls `warehouse_audit_logger_agent` |
| `returns_handler_agent` | Returns processor (WMS) | Disposition returns — restock / scrap / refurbish | MCP connector; calls `inventory_agent` (restock), `warehouse_audit_logger_agent` |
| `inventory_control_agent` | Cycle-count & shrink controller | Pull the SKU master (`@pre_compose`), reconcile counts, enforce the 2% shrink threshold + manager floor (`@chain`) | programmatic + shrink math (WMS swap); wire floors → `exception_agent` (variance>2%), `warehouse_manager_human` (systematic shrink / >20 over tolerance) |
| `carrier_selector_agent` | Transportation / rate-shopping planner | Pull weight + rate table (`@pre_compose`), rate-shop cheapest-meeting-SLA, re-derive `sla_met` (`@chain`); quarterly re-cost; outage failover | programmatic + carrier math (TMS swap); calls `loader_humanoid` |
| `replenishment_planner_agent` | Replenishment / demand planner | Read SKU on-hand, reorder points, target qtys from ERP; surface reorder recommendations | MCP connector (ERP); wire floor → `ops_supervisor_human` (critical stockout) |
| `supplier_relations_agent` | Supplier relationship manager | Onboarding gates (KYC/ESG/banking/insurance), alternate sourcing, quarterly scorecards | LLM + quarterly cron; floors → `warehouse_manager_human` (approval / compliance / ESG-high / KYC-rejected / lead review), `exception_agent` (no alternates) |
| `humanoid_status_agent` | Robotics fleet operations monitor | Ingest fleet telemetry — battery / fault / location (store-grounded; fleet-API swap) | programmatic; calls `ops_supervisor_human` on fault alerts |
| `ops_supervisor_human` | Operations supervisor (HITL) | Floor decisions, exceptions, safety sign-off (30-min timeout) | human; escalates to `warehouse_manager_human` or `warehouse_audit_logger_agent` |
| `warehouse_manager_human` | Warehouse manager (HITL) | Operational tier-2, humanoid governance, supplier sign-off (4-hr timeout, default-deny halt) | human; calls `warehouse_audit_logger_agent` |
| `warehouse_audit_logger_agent` | Compliance / audit recorder | Hash-chained WORM record of every pick, pack, ship, exception, humanoid action | external `custom` (registered in `main.py`); terminal |

## Trust mechanisms

Deterministic floors (`@chain` in Python — the LLM/body can't waive them):

- **Availability gate** — a short order line is never dispatched to a picker; it surfaces as a short-pick exception (`allocation_math`)
- **Shrink / cycle-count threshold** — any SKU over 2% variance, or a systematic-short zone, escalates (`shrink_math`)
- **Carrier rate-shop rule** — cheapest carrier meeting the SLA; `sla_met` re-derived from transit days (`carrier_math`)
- **Ship-readiness** — no empty/weightless carton is marked packed (`allocation_math`)
- **Idempotency** — humanoid actuators dispatch once per `(session, task)` (`session_stash`)

Policy on the wires:

- **Safety event** → supervisor, always (prompt hard rule + wire condition on `exception_type == 'safety'`)
- **Severity ≥ 0.7** → supervisor required (wire condition)
- **Cycle-count variance > 2%** on any SKU → `exception_agent`
- **Systematic shrink** (any zone avg < −2% across ≥3 counts, or >20 SKUs over tolerance) → warehouse manager
- **Critical stockout** (any SKU at zero on-hand) → ops supervisor
- **ESG high / KYC rejected** supplier → warehouse manager, even if the model forgets the compliance flag
- **Inspection humanoid** verifies any flagged item before pack
- **HITL chain** with default-deny on warehouse-manager timeout
- **Hash-chained WORM audit** on every pick / pack / ship / exception / humanoid action

## Crons

- `supplier_relations_agent` — quarterly scorecard tick (`0 9 1 1,4,7,10 *`)
- `inventory_control_agent` — nightly count sweep ships commented out;
  enable `# wake_up: "0 3 * * *"` after wiring your WMS connector

## Entry points

`order_placed`, `return_received`, `restock_event`,
`cycle_count_scheduled`, `humanoid_status`, `manual_action`,
`sku_master_update`, `reorder_sweep`, `cycle_count_ingest`,
`inventory_recon`, `carrier_select`, `bulk_rate_renegotiation`,
`carrier_outage_failover`, the supplier-relations entries
(`supplier_relations_onboarding` / `_sourcing` / `_quarterly_scorecard`),
plus per-agent `direct_call_*` entries for programmatic / debug ingest.

## Quick start

```bash
cp env .env && pip install -r requirements.txt
docker run -d -p 6379:6379 redis:7-alpine
python main.py
```

### Day-0 entry call (runs against the seeded store, no connectors)

```python
import asyncio
from leafmesh import LeafMesh
from agency._shared import audit_logger

async def main():
    mesh = LeafMesh.from_yaml("configs/config.yaml")
    audit_logger.register(mesh, "warehouse_audit_logger_agent")
    await mesh.start()

    # Clean order — allocation gate passes, humanoid picks → packs → ships
    result = await mesh.mesh_call("order_placed", {"order": {
        "order_id": "ORD-4471", "ship_method": "standard",
        "line_items": [{"sku": "SKU-9001", "quantity": 1},
                       {"sku": "SKU-7700", "quantity": 1}]}},
        session_id="demo-1")
    print(result)   # all_available=True, pick_sequence bin-sorted, dispatched

    await mesh.stop()

asyncio.run(main())
```

Or run the bundled scenarios: `python examples/run_examples.py` (01 =
clean order, 02 = the availability-gate short path, 03 = returns
inspection, 04 = carrier failover).

Set `WMS_MCP_URL` / `TMS_MCP_URL` / `ERP_MCP_URL` / `FLEET_API_MCP_URL`
in `.env` as you swap each agent for the live system; until then the
store-grounded agents read the seeded dev store (`agency/_shared/store.py`)
and the pure MCP connectors return empty contracts.
