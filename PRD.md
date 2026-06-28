# warehouse-supply-chain — Warehouse / Supply Chain — Product Requirements / Build Doc

> A warehouse-operations department where **agents reason and humanoids
> act**. An order webhook enters; an allocation planner builds and
> travel-minimises the pick route while an inventory clerk reserves
> stock; a humanoid picker / inspector / packer / loader carry out the
> physical work; a floor-exceptions analyst classifies anything that
> goes wrong and the wires route safety / high-severity events to a
> human supervisor; replenishment, cycle-counting, carrier rate-shopping
> and supplier scorecards run on connectors against your WMS / ERP / TMS
> / fleet API. Every terminal action lands in a hash-chained WORM audit.
>
> Built on LeafMesh as an **operation of self-reliant agents**. The
> config casts the roles and carries the wire policy; the depth lives in
> Python where the domain demands it: agents pull their own context
> (`@pre_compose`) from a seeded dev store, enforce deterministic floors
> (`@chain`) the LLM can't waive, run real humanoid actuators with
> idempotency, and shape connector results. Systems of record are
> connectors (or store-grounded with a documented PROD swap); the two
> judgment roles are pure-YAML LLM with policy on the `can_call` wires.

---

## 1. The operation at a glance

```
 OMS order webhook            return arrives        restock / cycle count / telemetry
   (order_placed)            (return_received)        (restock_event / cycle_* / humanoid_status)
        │                          │                            │
   ┌────▼─────────────────┐   ┌────▼──────────────┐      (connector agents — WMS/ERP/TMS/fleet)
   │ allocation_agent     │   │ returns_handler   │
   │ (pick plan + batch + │   │ (WMS disposition) │
   │  travel-min route)   │   └────┬──────────────┘
   └──┬──────────────┬────┘        │ restocked
      │ reserve      │ dispatch     ▼
 ┌────▼──────┐  ┌────▼───────────┐  inventory_agent (reserve)
 │ inventory │  │ picker_humanoid│
 │  _agent   │  └──┬─────────┬───┘
 └────┬──────┘     │ clean   │ damage / short
   shortage        ▼         ▼
      │      packer_humanoid  inspection_humanoid ──pass──▶ packer_humanoid
      ▼            │          └──fail──▶ exception_agent
 exception_agent   ▼
      │      loader_humanoid ──▶ warehouse_audit_logger_agent (WORM)
      ▼            ▲
 (severity ≥0.7 │  └── carrier_selector_agent (rate-shop) ──▶ loader_humanoid
  or safety)    │
      ▼         │
 ops_supervisor_human ──escalate──▶ warehouse_manager_human ──▶ WORM
      │ resolve
      ▼
 warehouse_audit_logger_agent (WORM)
```

Bounded by construction: forward-flow DAG, every branch terminates in
the WORM audit, no conductor hub. Humans (`ops_supervisor_human`,
`warehouse_manager_human`) are default-deny on timeout.

## 2. Actor inventory (job titles)

| Agent | Job title | Type |
|---|---|---|
| `allocation_agent` | Warehouse allocation & routing planner | programmatic (domain math) |
| `inventory_agent` | Inventory clerk (WMS) | programmatic / MCP |
| `exception_agent` | Floor exceptions analyst | llm |
| `picker_humanoid` | Picker (humanoid) | programmatic actor |
| `inspection_humanoid` | Quality inspector (humanoid) | programmatic actor |
| `packer_humanoid` | Packer (humanoid) | programmatic actor |
| `loader_humanoid` | Dock loader (humanoid) | programmatic actor |
| `returns_handler_agent` | Returns processor (WMS) | programmatic / MCP |
| `inventory_control_agent` | Cycle-count & shrink controller | programmatic / MCP |
| `carrier_selector_agent` | Transportation / rate-shopping planner | programmatic / MCP (TMS) |
| `replenishment_planner_agent` | Replenishment / demand planner | programmatic / MCP (ERP) |
| `supplier_relations_agent` | Supplier relationship manager | llm + quarterly cron |
| `humanoid_status_agent` | Robotics fleet operations monitor | programmatic / MCP (fleet) |
| `ops_supervisor_human` | Operations supervisor | human (HITL) |
| `warehouse_manager_human` | Warehouse manager | human (HITL) |
| `warehouse_audit_logger_agent` | Compliance / audit recorder | external `custom` (WORM) |

## 3. Data artifacts

- **`OrderEvent`** (declared `data_structure`) — `order_id`,
  `customer_id`, `line_items` `[{sku, quantity}]`, `ship_to`,
  `ship_method`, `received_at_utc`.
- **Pick plan / sequence** — `allocation_agent` yields: `pick_plan`,
  `batch_id`, `priority`, `pick_sequence`, `estimated_minutes`,
  `assigned_resource`, `order_id`.
- **Connector contracts** — each MCP agent's `yields:` block is the
  shape the gateway must return (e.g. `inventory_agent`: `all_in_stock`,
  `short_skus`, `reservation_id`).
- **WORM audit entry** — `audit_event_id`, `written_at_utc`,
  `hash_chain_prev`, `hash_chain_current` (SHA-256 chain over the prior
  entry's body).

## 4. Per-agent spec (highlights)

- **`allocation_agent`** (Python, deep): `@pre_compose load_fulfillment_context`
  pulls the order (hydrating from `order_id` alone), bin/slotting
  locations and free-stock availability from the store; the body does
  bin-sort travel minimisation, batching and resource assignment;
  `@chain enforce_allocation_floor` recomputes free stock per line and
  blanks the picker dispatch for a fully-short order (the **availability
  gate** — `agency/_shared/allocation_math`). Direct-call path routes an
  existing `pick_plan`. Identity passthrough of `order_id`.
- **`picker_humanoid`** (Python, deep): `@pre_compose load_pick_context`
  reserves a ready fleet unit (online, charged, not faulted); the body is
  the real humanoid task-dispatch **actuator**, guarded for **idempotency**
  via `session_stash` so a re-entered chain never double-dispatches.
- **`inventory_control_agent`** (Python, deep): `@pre_compose` pulls the
  SKU master; the body reconciles counts; `@chain enforce_shrink_floor`
  re-asserts the 2% variance tolerance + manager-attention floor
  (`agency/_shared/shrink_math`). PROD swaps the body for the WMS read.
- **`carrier_selector_agent`** (Python, deep): `@pre_compose` pulls the
  carton weight + rate table; the body rate-shops; `@chain
  enforce_rate_shop_floor` re-derives `sla_met` and the cheapest-meeting-
  SLA rule (`agency/_shared/carrier_math`). PROD swaps the body for the TMS.
- **`packer_humanoid`** (Python, deep): `@chain enforce_ship_ready` — the
  **ship-readiness floor** blocks an empty/weightless carton; records the
  shipment for the loader/carrier handoff.
- **`exception_agent`** (pure-YAML LLM): OUTPUT CONTRACT over
  `exception_type / severity / recommended_action / requires_supervisor
  / supervisor_briefing / order_id`. The deterministic floors (safety →
  supervisor; severity ≥ 0.7 → supervisor) are prompt hard rules AND
  mirrored as mutually-exclusive `can_call` conditions, so they hold even
  if the model under-scores. Narration adds discretionary manager /
  supplier-relations routes (additive via the Manager+Summarizer).
- **`supplier_relations_agent`** (pure-YAML LLM, 3 responsibilities in
  one role): onboarding gates, alternate sourcing, quarterly scorecards.
  Scorecard math arrives precomputed in `supplier_signals`; ESG-high /
  KYC-rejected floors duplicated on the wires.
- **`inventory_control_agent`** (MCP): mode-dispatched count / ingest /
  report; 2% variance tolerance and systematic-shrink floor live on the
  wires.
- **humanoid actors** (`picker / inspection / packer / loader`):
  Python actors standing in for the robot fleet dispatch API — the return
  shape is the contract; `packer` carries the ship-readiness `@chain`.
- **`warehouse_audit_logger_agent`** (external `custom`): hash-chained
  WORM sink registered in `main.py` via `mesh.intelligence(...)`.

## 5. Entry points

`order_placed`, `return_received`, `restock_event`,
`cycle_count_scheduled`, `humanoid_status`, `manual_action`,
`sku_master_update`, `reorder_sweep`, `cycle_count_ingest`,
`inventory_recon`, `carrier_select`, `bulk_rate_renegotiation`,
`carrier_outage_failover`, `supplier_relations_onboarding` /
`_sourcing` / `_quarterly_scorecard`, plus per-agent `direct_call_*`.

## 6. Controls / trust

- Human gates default-deny on timeout (`ops_supervisor_human` 30 min →
  escalate; `warehouse_manager_human` 4 hr → halt for review).
- Safety + severity floors enforced on the wires, not only in the prompt.
- Variance / shrink / stockout / ESG / KYC floors on the wires.
- Inspection-humanoid gate before any flagged item is packed.
- Hash-chained WORM audit terminus on every branch.
- Manager escalation targets: human_agent → webhook (PagerDuty) → Slack
  channel.

## 7. Config pointer

The department lives in `configs/config.yaml`; the depth lives in
`agency/`. Pure MCP connectors (`inventory_agent`, `returns_handler_agent`,
`replenishment_planner_agent`) name the system of record; their `yields:`
blocks are the contracts. Policy is enforced two ways: deterministic
floors in Python `@chain` (availability, shrink, rate-shop, ship-
readiness) AND readable wire conditions (`can_call`). The Python surface:

- `agency/allocation_agent.py`, `picker_humanoid.py`, `packer_humanoid.py`,
  `loader_humanoid.py`, `inspection_humanoid.py` — the fulfilment chain
- `agency/inventory_control_agent.py`, `carrier_selector_agent.py`,
  `humanoid_status_agent.py` — store-grounded with documented PROD swaps
- `agency/_shared/store.py` — the seeded dev store (the day-0 demo data)
- `agency/_shared/allocation_math.py`, `shrink_math.py`, `carrier_math.py`
  — the deterministic floor arithmetic
- `agency/_shared/session_stash.py` — idempotency + bounded loops
- `agency/_shared/audit_logger.py` — the hash-chained WORM sink

## 8. Reflection / decisions

- **Deep where the domain demands it.** Three agents stay pure MCP
  connectors; two judgment roles are pure-YAML LLM. The fulfilment chain
  and the three rule-bearing agents carry real Python: self-reliant
  `@pre_compose` context pulls, deterministic `@chain` floors, humanoid
  actuators with idempotency. Floors hold on direct calls too — the
  bodies fall back to `input_data` when `@pre_compose` hasn't run, so the
  gate/threshold/rule can't be bypassed by invocation path.
- **Day-0 usable.** A seeded dev store (`agency/_shared/store.py`) carries
  a realistic order, inventory with a short SKU, a humanoid fleet and a
  carrier table, so the full chain (incl. the availability-gate /
  exception path) runs with no connectors set.
- **2.3.x role consolidation (19 → 16).** Five responsibility-shaped
  agents were merged into the role that owns them: routing folded into
  `allocation_agent`; onboarding + sourcing + scorecards into one
  `supplier_relations_agent`; count + ingest + reconciliation into one
  `inventory_control_agent`. Each survivor reads as a real job title.
- **Floors on the wires, not in prompts.** When the old exception /
  inventory Python modules were deleted, their deterministic policy moved
  onto `can_call` conditions so the LLM cannot waive it.
- **Narration vs conditions.** `exception_agent` narration names
  `warehouse_manager_human` / `supplier_relations_agent` without a
  `can_call` edge — this is the intended LeafMesh primitive (the Manager
  + Summarizer dispatch narration targets additively; narration may
  reference any agent), not unenforced narration.
- **Slack env.** `SLACK_BOT_TOKEN` / `SLACK_SIGNING_SECRET` are
  `ChannelConfig` fields; with no `channels:` block they are not
  auto-consumed — the escalation `channel` target only reads
  `channel_id` (`SLACK_WAREHOUSE_CHANNEL`).
