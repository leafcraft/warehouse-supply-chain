# warehouse-supply-chain — Warehouse / Supply-Chain Fulfilment Pod — PRD / Build Doc

> A warehouse-operations **pod** where a coordinator owns the order, specialists
> and humanoids do the work, a finisher issues the shipment behind a release
> gate, and two humans decide. An order enters (webhook or OMS event stream); the
> **Fulfilment Coordinator** opens it and hands it to allocation; allocation
> builds and travel-minimises the pick route behind an **availability gate**;
> inventory reserves; a humanoid picker / inspector / packer carry out the
> physical work; a carrier is rate-shopped; the **shipment dispatcher** (the
> finisher) re-checks a release token, renders the shipping manifest, and hands
> the carton to the loader. Standing supply-chain roles run alongside:
> cycle-count / shrink control (parallel-copies fan-out), returns,
> replenishment, supplier relations (super-agent), humanoid-fleet telemetry.
> Every terminal action lands in a hash-chained WORM audit.
>
> Built on LeafMesh (SDK 2.4.131) as a **pod of self-reliant agents**, not a
> pipeline. The config casts the roles and carries the wire policy; the depth
> lives in Python where the domain demands it: agents pull their own context
> (`@pre_compose`), enforce deterministic floors (`@chain`) the model can't
> waive, run real humanoid actuators with idempotency, and the finisher fails
> closed. Systems of record (WMS / TMS / ERP / fleet API) are **tools inside
> agents** — commented `mcp` blocks + dev-store fallbacks that announce
> themselves — never members.

---

## 1. The pod at a glance

```
 OMS webhook / order stream        return arrives         restock / cycle-count / telemetry
   (order_placed / listen_events)  (return_received)       (restock_event / cycle_* / humanoid_status)
        │                               │                             │
 ┌──────▼───────────────────┐    ┌──────▼───────────┐         (store-grounded agents; commented mcp)
 │ fulfillment_coordinator  │    │ returns_handler  │
 │ (opens order, status,    │    │ (restock/scrap)  │
 │  single contact)         │    └──────┬───────────┘
 └──────┬───────────────────┘           │ restocked
        │ open_order                     ▼
 ┌──────▼───────────────┐          inventory_agent (reserve)
 │ allocation_agent     │  all_available==true
 │ (plan + route +      │──────────────▶ inventory_agent (reserve) ──shortage──▶ exception_agent
 │  AVAILABILITY GATE)  │──── picker_humanoid (all_available && picker)
 └──────────────────────┘──── warehouse_manager_human (human_picker)
             │ all_available==false
             ▼
        exception_agent
   ┌─────────┴──────────┐
   picker_humanoid ──clean──▶ packer_humanoid ──▶ carrier_selector_agent ──sla_met──▶ shipment_dispatcher_agent
        │ damage/short          (ship-ready floor)   (rate-shop floor)                    (FINISHER)
        ▼                                                    │ !sla_met                     │ routed (held/high-value)
   inspection_humanoid ──pass──▶ packer_humanoid              ▼                              ▼
        └──fail──▶ exception_agent                        exception_agent          warehouse_manager_human
                                                                                          │ approved
 exception_agent ──(safety / sev≥0.7 / requires_sup)──▶ ops_supervisor_human               ▼
        └──(front-line)──▶ WORM                                                    shipment_dispatcher_agent
                                                                                    │ dispatched
 inventory_control_agent (copies fan-out; shrink floor)                             ▼
   ├─ max_variance>2% ──▶ exception_agent                                    loader_humanoid ──▶ WORM
   └─ systematic shrink / >20 over-tol ──▶ warehouse_manager_human

 supplier_relations_agent (super-agent; onboarding/sourcing/scorecard floors ──▶ warehouse_manager_human / exception_agent)
 humanoid_status_agent ──faults──▶ ops_supervisor_human
 every branch ──▶ warehouse_audit_logger_agent (hash-chained WORM terminus)
```

Bounded by construction: forward-flow DAG; every branch terminates in the WORM
audit; the coordinator orchestrates but never does specialist work; human gates
(`ops_supervisor_human` 30 min, `warehouse_manager_human` 2h) never release or
approve on silence — an unanswered request closes its Inbox card and the ticket
records that no reply came — and never call each other (the supervisor's
`escalate` goes through the Coordinator).

## 2. Actor inventory (job titles)

| Agent | Job title | Type |
|---|---|---|
| `fulfillment_coordinator_agent` | Fulfilment coordinator | llm (pure-YAML) + listen_events |
| `allocation_agent` | Slotting & pick planner | programmatic (domain math + gate) |
| `inventory_agent` | Stock controller (WMS) | programmatic (store-grounded) |
| `exception_agent` | Floor exceptions analyst | llm + playbook + `@chain` floor |
| `picker_humanoid` | Picker (humanoid) | programmatic actor |
| `inspection_humanoid` | Quality inspector (humanoid) | programmatic actor |
| `packer_humanoid` | Packer (humanoid) | programmatic actor + ship-ready floor |
| `carrier_selector_agent` | Transportation / rate-shop planner | programmatic (rate-shop floor) |
| `shipment_dispatcher_agent` | Dispatch / shipping lead | programmatic **finisher** |
| `loader_humanoid` | Dock loader (humanoid) | programmatic actor |
| `returns_handler_agent` | Returns processor (WMS) | programmatic (store-grounded) |
| `inventory_control_agent` | Cycle-count & shrink controller | programmatic + scaling.copies + wake_up |
| `replenishment_planner_agent` | Replenishment / demand planner | programmatic (store-grounded) |
| `supplier_relations_agent` | Supplier relationship manager | llm + super_agent + playbook + wake_up |
| `humanoid_status_agent` | Robotics fleet ops monitor | programmatic |
| `ops_supervisor_human` | Operations supervisor | human (Studio Inbox) |
| `warehouse_manager_human` | Warehouse manager | human (Studio Inbox) |
| `warehouse_audit_logger_agent` | Compliance / audit recorder | external `custom` (WORM) |

## 3. Data artifacts

- **`OrderEvent`** (declared `data_structure`) — `order_id`, `customer_id`,
  `line_items` `[{sku, quantity}]`, `ship_to`, `ship_method`, `received_at_utc`.
- **Pick plan / sequence** — `allocation_agent` yields `pick_plan`, `batch_id`,
  `priority`, `pick_sequence`, `estimated_minutes`, `assigned_resource`,
  `all_available`, `short_lines`, `order_id`.
- **Shipping manifest** — `shipment_dispatcher_agent` renders
  `./out/<carton>_shipping_manifest.md` on release and appends
  `./data/fulfillment_ledger.json` + `./data/outbox.json`.
- **WORM audit entry** — `audit_event_id`, `written_at_utc`, `hash_chain_prev`,
  `hash_chain_current` (SHA-256 chain over the prior entry's body).

## 4. Per-agent spec (highlights)

- **`fulfillment_coordinator_agent`** (pure-YAML LLM, coordinator): owns the
  order; OUTPUT CONTRACT over `action / order_id / stage / waiting_on / summary`.
  `action == open_order` → allocation; `escalate` → the Warehouse Manager. Also
  consumes `orders.created` from a Redis stream via `listen_events` (BRD-021),
  parallel to the `order_placed` webhook — shipped commented; create vault
  connection `order_events`, then uncomment the broker and `listen_events` (in `configs/config.yaml` before the first start, in Studio's YAML editor after it). Never allocates, picks, or releases.
- **`allocation_agent`** (Python, deep): `@pre_compose load_fulfillment_context`
  pulls the order (hydrating from `order_id` alone), bins and free-stock
  availability; the body does bin-sort travel minimisation, batching and resource
  assignment; `@chain enforce_allocation_floor` recomputes free stock per line
  and blanks the picker dispatch for a fully-short order — the **availability
  gate** (`agency/_shared/allocation_math`). Every downstream edge is conditional
  on `all_available`.
- **`inventory_agent`** (Python, store-grounded): reserves the pick plan against
  the seeded WMS; a shortage → `exception_agent`; a success is recorded to the
  WORM sink. Also absorbs an inbound restock. Announces `available: false` on an
  unknown SKU. PROD: uncomment the WMS connector block (`integration: mcp`, vault connection `wms_mcp`) — in `configs/config.yaml` before the first start, in Studio's YAML editor after it.
- **`exception_agent`** (LLM, deep): `@pre_compose load_exception_context` pulls
  the order + affected SKU unit value + fleet unit; `@chain
  enforce_severity_floor` re-asserts the floors a prompt can't waive — safety
  (sev 1.0), any damage, or a high-value line (≥ $500) → supervisor + escalate,
  reading the value from the WMS, not the model. **Playbook** = the triage craft
  (only `description` in the prompt; `responsibility` on demand). The `can_call`
  conditions gate on the SAME verdict.
- **humanoid actors** (`picker / inspection / packer / loader`): real
  task-dispatch actuators standing in for the robot fleet API; `picker`
  `@pre_compose` reserves a ready unit and is idempotent via `session_stash`;
  `packer` carries the `@chain enforce_ship_ready` floor (no empty/weightless
  carton).
- **`carrier_selector_agent`** (Python, deep): `@pre_compose` pulls carton weight
  + rate table; `@chain enforce_rate_shop_floor` re-derives `sla_met` and the
  cheapest-meeting-SLA rule (`agency/_shared/carrier_math`). `sla_met` →
  dispatcher; no SLA-meeting carrier → exception. PROD: TMS `mcp`.
- **`shipment_dispatcher_agent`** (Python, **finisher** — the load-bearing
  pattern): sits behind the dispatch RELEASE gate. Recovers the carton across the
  HITL boundary (upstream → session stash → dev store) on the substantive
  `selected_carrier`, never a bare id. Routine cartons self-authorise on
  deterministic invariants; a held carton, or one whose order's declared value
  (Σ quantity × unit value, from the OMS) is $500 or more, routes to the Warehouse
  Manager (phase 1) and issues (phase 2) ONLY on an explicit release token
  (allowlist) — an unknown verdict **fails closed**. `@chain
  enforce_dispatch_floor`: re-check → invariants (carrier / SLA / real carton)
  → render the manifest to `./out` → **delete-on-block** → WORM ledger. A missing
  shipment record announces `available: false` — never a synthetic dispatch ack.
- **`inventory_control_agent`** (Python, deep): mode-dispatched count / ingest /
  report; **parallel copies** (`scaling.copies`) fan the nightly sweep across zones, each copy
  claiming a DISJOINT batch via the atomic `store.claim_count_batch`;
  `scaling.copies.handoff: last` combines the copies (counts SUM correctly). The
  per-copy `@chain enforce_shrink_floor` re-asserts the 2% tolerance +
  manager-attention floor on its own batch; the combined `max_variance_pct` is a
  summed ratio, so it is conservative — it can only over-escalate the `> 0.02`
  wire (fail-safe, never a missed shrink). `wake_up` runs the sweep in
  `America/Denver` (not UTC). PROD: WMS `mcp`.
- **`replenishment_planner_agent`** (Python, store-grounded): reorder recs off
  the SKU master; any zero-on-hand SKU → `ops_supervisor_human`. PROD: ERP `mcp`.
- **`supplier_relations_agent`** (LLM, **super_agent** + one role, three
  responsibilities): `@pre_compose load_supplier_context` pulls the scorecard +
  trailing performance history + candidate pool; the **super-agent** (plan →
  execute → verify → reflect) fits reasoning over the trend; **playbook** holds
  the onboarding/sourcing/scorecard rules; on-demand `lookup_supplier` /
  `find_alternates` dev-store tools announce `available: false` with no ERP.
  ESG-high / KYC-rejected / procurement-lead floors are duplicated on the wires.
  Quarterly `wake_up` in `America/Denver`.
- **`warehouse_audit_logger_agent`** (external `custom`): hash-chained WORM sink
  registered in `main.py` via `mesh.intelligence(...)`.

## 5. Entry points

`order_placed`, `order_status`, `manual_action` (→ coordinator); `return_received`
(→ returns); `restock_event` (→ inventory); `cycle_count_scheduled` /
`cycle_count_ingest` / `inventory_recon` (→ inventory control); `reorder_sweep` /
`sku_master_update` (→ replenishment); `carrier_select` /
`carrier_outage_failover` (→ carrier); `humanoid_status` (→ fleet);
`supplier_relations_onboarding` / `_sourcing` / `supplier_quarterly_scorecard`;
`release_reply` / `supervisor_reply` (HITL resume); and per-agent `direct_call_*`.
Plus the coordinator's `listen_events` on the OMS `orders.created` Redis stream
(commented until vault connection `order_events` exists).

## 6. Controls / trust

- **One human, one agent, one system per stage** — agents produce, humans decide,
  the WORM sink records. No stage advances on an agent's own authority.
- **Every wire conditional** — no bare edges; the audit terminus uses `always`.
- **Nothing released on silence** — an unanswered request closes its Inbox card
  and the ticket records that no reply came; nobody is chased automatically.
  The seats keep `fallback_response` (`held_for_review` → audit) as the declared
  intent; leafmesh 2.4.486 does not route it for dual seats yet. Every answer
  value has a route. `dual` lives on humans only and never `dual`→`dual` (the
  supervisor's `escalate` goes through the Coordinator).
- **Deterministic floors** the LLM can't waive: availability gate, severity /
  safety floor, ship-readiness, rate-shop, shrink, and the finisher's
  release-token re-check + invariants + delete-on-block.
- **No loops to bound** — the outbound chain is a forward DAG: a failed
  inspection goes to the exception handler, never back to the picker.
  `session_stash` keeps the picker's idempotency latch.
- Hash-chained WORM audit terminus on every branch.
- Manager escalation targets: `human_agent` (Warehouse Manager) → webhook
  (PagerDuty; commented until vault connection `escalation_webhook` exists).

## 7. Config pointer

The department lives in `configs/config.yaml` (the first-start seed — after the first start the configuration lives in the store and is changed in Studio); the depth lives in `agency/`.
Policy is enforced two ways: deterministic floors in Python `@chain`
(availability, severity, ship-readiness, rate-shop, shrink, dispatch) AND
readable wire conditions (`can_call`). The Python surface:

- `agency/allocation_agent.py`, `picker_humanoid.py`, `packer_humanoid.py`,
  `loader_humanoid.py`, `inspection_humanoid.py` — the fulfilment chain
- `agency/shipment_dispatcher_agent.py` — the finisher (release re-check +
  manifest render + delete-on-block)
- `agency/inventory_agent.py`, `returns_handler_agent.py`,
  `replenishment_planner_agent.py` — store-grounded WMS/ERP roles (commented mcp)
- `agency/exception_agent.py`, `supplier_relations_agent.py`,
  `inventory_control_agent.py`, `carrier_selector_agent.py`,
  `humanoid_status_agent.py` — store-grounded, deep
- `agency/tools.py` — the `@global_tool` dev-store lookup surface
- `agency/_shared/store.py` — the seeded dev store (day-0 demo data + outbox +
  ledger + the atomic count-claim)
- `agency/_shared/allocation_math.py`, `shrink_math.py`, `carrier_math.py` — the
  deterministic floor arithmetic
- `agency/_shared/session_stash.py` — idempotency + bounded loops + HITL recovery
- `agency/_shared/audit_logger.py` — the hash-chained WORM sink

## 8. Reflection / decisions

- **A pod, not a pipeline.** The rebuild adds the two missing pod primitives: a
  **coordinator** that owns the order and is the single contact, and a
  **finisher** (`shipment_dispatcher_agent`) that produces the real shipping
  artifact behind a human release gate and fails closed. Every other role does
  exactly one job.
- **Modern capabilities, fitted to the domain.** `scaling.copies` fan the cycle-count
  sweep across zones (each copy claims a disjoint batch); `super_agent` gives the
  supplier manager plan/execute/verify over trailing scorecards; `listen_events`
  lets the coordinator consume the OMS order stream; `timezone` pins both
  `wake_up` crons to the warehouse zone (not UTC); `playbook` carries the
  per-intent craft (only `description` resident, `responsibility` on demand).
- **Floors hold on every path.** The deterministic `@chain` floors re-run on
  direct calls too (bodies fall back to `input_data` when `@pre_compose` hasn't
  run), so a gate/threshold/rule can't be bypassed by invocation path.
- **Day-0 usable.** A seeded dev store carries three orders (routine $208,
  short-stock, and a $595 carton that needs a release), inventory with a short
  and a high-value SKU, a humanoid fleet, a carrier table, and supplier
  scorecards, so the FULL chain (incl. the availability-gate / exception /
  dispatch paths) runs with no connectors set — every stub announces itself.
- **Systems are tools, not members.** WMS / TMS / ERP / fleet are commented `mcp`
  blocks + store-grounded bodies with a documented PROD swap; their `yields`
  blocks are the contract the gateway must return.
- **`dual` discipline.** `dual` is on the two humans only; they never call each
  other (a `dual`→`dual` edge auto-bounces, gotcha #17) — the supervisor's
  escalation goes through the Coordinator. Verified: the mesh loads on 2.4.131 with
  **zero dual warnings**.
