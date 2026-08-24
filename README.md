# warehouse-supply-chain — Warehouse / Supply-Chain Fulfilment Pod

A coordinator + specialists + humanoid actors + a finisher + two human roles that
run an outbound order end to end — **order → allocate + travel-minimised route →
reserve → pick (humanoid) → inspect → pack → rate-shop carrier → DISPATCH
(release gate + manifest) → load → ship** — with the standing supply-chain roles
running alongside (cycle-count / shrink control, returns, replenishment, supplier
relations, humanoid-fleet telemetry). Systems of record (WMS / TMS / ERP / fleet
API) are **tools inside the agents** — commented `mcp` blocks + dev-store
fallbacks that announce themselves — never members.

**Read `PRD.md`** for the full agent-by-agent spec. This is a **pod-standard
reference** (SDK 2.4.131): a team of self-reliant agents, not a pipeline.

## Why it works in an organization

Warehouse replenishment and fulfilment run on people watching dashboards —
someone allocates a pick, someone rekeys a count, someone rate-shops a carton by
hand, someone chases a damaged return. That routine is ~75% of the work and it
leaks money: shipping spend drifts 8–12% without per-parcel carrier selection,
and shrink hides in cycle-count variance nobody sweeps. This pod absorbs the
routine and **acts**, alongside the people who still own the calls. It wins the
honest way: **allocation's availability gate** never dispatches a picker for
stock that isn't free; **the exception handler** can't down-rate a safety,
damage, or high-value event — the deterministic floor re-asserts escalation from
the WMS unit value, not the model's guess; **the shipment dispatcher re-checks a
real release token** before it issues a manifest and **deletes a half-issued
shipment** if its floor trips; **cycle-count fans out across zones** with each
copy claiming a disjoint batch, and the shrink floor re-asserts the 2% tolerance.
**Silence never advances an order** — every human gate default-denies to
`held_for_review` and lands in the audit sink. Every pick, pack, ship, exception,
humanoid action, and held gate is hash-chained in a WORM ledger. Humanoids
(projected $3–8/hr) do the physical share; the agents do the judgment and the
paperwork.

## The team (18 members)

| Agent | Role (human job title) | Responsibilities | Can perform |
|---|---|---|---|
| `fulfillment_coordinator_agent` | Fulfilment Coordinator | Owns the order end to end; opens it, keeps live status, is the single contact | LLM; orchestrates allocation→…→dispatch; **listen_events** on OMS order stream; escalates to the Warehouse Manager; **never allocates, picks, or releases** |
| `allocation_agent` | Slotting & Pick Planner | Pick path, batching, priority + travel-minimised route in one step | Programmatic; `@pre_compose` pulls order+bins; `@chain` **availability gate** (no dispatch on short stock) |
| `inventory_agent` | Stock Controller | Confirms stock + reserves units; absorbs inbound restock | Programmatic; store-grounded day-0; shortage → exception; commented WMS `mcp` |
| `exception_agent` | Exceptions Analyst | Classifies short-pick / damage / wrong-item / safety and briefs the floor | LLM + **playbook**; `@pre_compose` pulls order+value+unit; `@chain` **severity/safety floor** (fail closed) |
| `picker_humanoid` | Picker (Humanoid) | Physical pick from bins | Programmatic actuator; reserves a ready fleet unit; idempotent dispatch |
| `inspection_humanoid` | QA Inspector (Humanoid) | Verifies condition of flagged items | Programmatic actuator |
| `packer_humanoid` | Packer (Humanoid) | Packs picked units into a carton | Programmatic actuator; `@chain` **ship-readiness floor** |
| `carrier_selector_agent` | Transportation Analyst | Picks the cheapest carrier meeting the SLA | Programmatic; `@chain` **rate-shop floor**; commented TMS `mcp` |
| `shipment_dispatcher_agent` | Dispatch / Shipping Lead | Re-checks the release, renders the shipping manifest, hands to load | Programmatic **finisher**; release-token re-check → invariants → render manifest → **delete-on-block** → WORM ledger |
| `loader_humanoid` | Dock Loader (Humanoid) | Loads a released carton onto the outbound truck | Programmatic actuator |
| `returns_handler_agent` | Returns Clerk | Dispositions a return — restock / refurbish / scrap | Programmatic; store-grounded day-0; commented WMS `mcp` |
| `inventory_control_agent` | Cycle-Count Controller | Cycle counts, ingest, physical-vs-system reconciliation, shrink-zone detection | Programmatic; **instances** fan-out (atomic claim per zone) + **wake_up**/**timezone**; `@chain` **shrink floor** |
| `replenishment_planner_agent` | Replenishment Planner | Reorder recommendations off the SKU master; critical-stockout flag | Programmatic; store-grounded day-0; commented ERP `mcp` |
| `supplier_relations_agent` | Supplier Relationship Manager | Onboarding gates, alternate sourcing, quarterly scorecards | LLM + **super_agent** + **playbook** + dev-store lookup tools; `@pre_compose` pulls scorecards+history; **wake_up**/**timezone** |
| `humanoid_status_agent` | Fleet Telemetry | Humanoid battery / fault / location telemetry | Programmatic; fault → Ops Supervisor |
| `ops_supervisor_human` | Ops Supervisor (Human) | Floor decisions, exceptions, safety, humanoid faults | Human gate (webhook); 30-min timeout |
| `warehouse_manager_human` | Warehouse Manager (Human) | Accountable owner: dispatch release, tier-2 governance, supplier gates, shrink | Human gate (webhook); everything that leaves the pod |
| `warehouse_audit_logger_agent` | WORM audit | Hash-chains every pick, pack, ship, exception, humanoid action, and held gate | External (custom sink); terminus |

The **LeafMesh Manager** supervises — it reads every hand-off and escalates
stalls / safety-damage-high-value exceptions / release timeouts / systematic
shrink to the Warehouse Manager automatically. Human gates never call each other
(a `dual`→`dual` edge auto-bounces); escalation goes through the Manager.

## Configuration — what to set, in three tiers

Everything is set in `.env` (`cp env .env`). The **whole chain runs on Tier 1
alone** against the seed store (`./data`): stock reads/reserves, cycle counts,
carrier rates, supplier scorecards, and the humanoid fleet are dev stand-ins that
**announce themselves** (`available: false`) until you wire Tiers 2–3.

### Tier 1 — required to boot (dev mode)
| Setting | For | Notes |
|---|---|---|
| `LEAFMESH_LICENSE_KEY` | LeafMesh runtime | **Required.** |
| `OPENAI_API_KEY` | Coordinator + exception + supplier + Manager (`gpt-4o-mini`) | **Required** unless you change the models. |
| `REDIS_HOST` / `PORT` / `PASSWORD` | State store | Defaults to `localhost:6379`. `docker run -d -p 6379:6379 redis:7-alpine`. |
| `HITL_OUTBOUND_URL` | Where human members are notified | Defaults to a local stub. |

### Tier 2 — real systems of record (set the URL, uncomment the `mcp` block)
| Setting | Enables |
|---|---|
| `WMS_MCP_URL` / `_TOKEN` | Real stock / reservations, returns, cycle-count reads |
| `TMS_MCP_URL` / `_TOKEN` | Real carrier rate tables |
| `ERP_MCP_URL` / `_TOKEN` | Real SKU master + supplier scorecards |
| `FLEET_API_MCP_URL` | Real humanoid fleet telemetry + dispatch |
| `ORDER_EVENTS_REDIS_URL` | The Redis stream the OMS writes `orders.created` to |

> ⚠️ The SDK rejects an `mcp` block with an **empty** URL at load, so every
> connector block ships **commented** in `configs/config.yaml`. Fill the URL,
> then uncomment. Until then the dev-store body answers and announces itself.

### Tier 3 — replace the dev store with your durable system of record
Swap the function bodies in `agency/_shared/store.py` for your WMS/ERP/TMS calls
(or point the `mcp` blocks at a gateway). The signatures are the contract — the
mesh does not change.

## Run it

```bash
pip install -r requirements.txt          # core LeafMesh + apscheduler runs the pod day-0
docker run -d -p 6379:6379 redis:7-alpine
cp env .env                              # fill at least Tier 1
python validate_config.py                # pre-flight: wiring, yields, dead-code
python main.py                           # API at http://127.0.0.1:18820/docs
```

Place an order (day-0, against the seed store — no connector needed):

```bash
curl -X POST http://127.0.0.1:18820/api/mesh/request \
  -H "Content-Type: application/json" \
  -d '{"entry_point":"order_placed",
       "data":{"order":{"order_id":"ORD-4471"}}}'
```

`ORD-4490` over-demands a short SKU on purpose — watch the availability gate
raise a short-pick exception instead of dispatching a picker. On release, the
dispatcher renders the shipping manifest to `./out/` and appends
`./data/fulfillment_ledger.json`. Run the worked scenarios with
`python examples/run_examples.py`.

> Packaging note: `pip install leafmesh` (core) includes all LLM providers and
> runs the pod; `apscheduler` powers the cycle-count / scorecard `wake_up` crons.
> For the real WMS/TMS/ERP connectors add `leafmesh[mcp]`. Avoid `leafmesh[all]`
> on Python 3.12+ (it pulls `cchardet`, which has no wheel) — add only the
> specific extras you need.

---

*Built with `create-leafmesh`. The config is the department; the PRD is the brief.*
*Design doctrine: `claude_skills/leafmesh/building-agents-thoroughly.md`.*
