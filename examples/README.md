# warehouse-supply-chain — example scenarios

Four realistic scenarios that exercise the major paths through this mesh.

## How to run

```bash
create-leafmesh --load .   # once: loads the team into the Redis in .env (or use create-leafmesh --start .)
python examples/run_examples.py                 # all scenarios
python examples/run_examples.py 01_             # one scenario (substring match)
```

This pod runs **day-0 with no connectors**: inventory, returns, carrier
selection, replenishment, cycle-count and fleet telemetry are
store-grounded against the seeded dev store (`agency/_shared/store.py`)
and each announces `available: false` when there is no real source. To
run against YOUR systems, create the vault connections `wms_mcp` /
`tms_mcp` / `erp_mcp` (Infrastructure → Integrations → MCP — the URL + token
live there, never in `.env`), adapt the agent body to the connector's
response, and uncomment the matching connector block (in Studio's raw YAML editor — see README Tier 2).

## Scenario map

- **01_routine_order** — `order_placed` — `ORD-4471`, two in-stock lines ($208) — allocation finds bins AND builds the pick sequence, inventory reserves, humanoids pick and pack, the carton self-releases (under the $500 line).
- **02_short_pick_exception** — `order_placed` — `ORD-4490` asks for 5 daypacks with 1 free — the availability gate blocks the picker and the exception handler classifies the short line (a short on an $89 line is front-line — backorder or substitute; damage, safety or a $500+ line goes to the Ops Supervisor).
- **03_returns_inspection** — `return_received` — one unopened pair returned from `ORD-4471` — the returns handler restocks it (opened → refurbish, damaged → scrap).
- **04_carrier_outage_failover** — `carrier_outage_failover` — UPS is out while carton `ctn-4471-01` waits — the rate-shop excludes UPS and picks FedEx 2-day, the cheapest carrier still meeting the 3-day SLA.

To see the dispatch release gate, place `ORD-4512` (5 × trail runners, $595):
its carton waits for the Warehouse Manager's release in Studio → Inbox.

## Human gates

If nobody answers a request in time, its Inbox card closes and the ticket
records that no reply came — nothing is released on silence.

## Audit trail

Every scenario appends one hash-chained entry to the template's
`logs/<audit>.jsonl` file. Tail it during a run to see the
ledger build up across scenarios.
