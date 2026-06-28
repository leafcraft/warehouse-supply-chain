# warehouse-supply-chain — example scenarios

Four realistic scenarios that exercise the major paths through this mesh.

## How to run

```bash
python examples/run_examples.py                 # all scenarios
python examples/run_examples.py 01_             # one scenario (substring match)
```

This template is YAML-first: inventory, returns, carrier selection,
replenishment and fleet telemetry are connectors into YOUR systems.
Set `WMS_MCP_URL` / `TMS_MCP_URL` / `ERP_MCP_URL` / `FLEET_API_MCP_URL`
in `.env` first — scenarios that touch an unwired connector return an
empty contract instead of live data.

## Scenario map

- **01_routine_order** — `order_placed` — Standard 2-line order — allocation finds bins AND builds the pick sequence, inventory confirms stock, humanoid picks.
- **02_short_pick_exception** — `order_placed` — Stock count is wrong — the WMS reports a short reservation, exception_agent classifies as short_pick, supervisor reviews.
- **03_returns_inspection** — `return_received` — Returned item arrived — inspection_humanoid evaluates condition, returns_handler routes to restock or quarantine.
- **04_carrier_outage_failover** — `carrier_outage_failover` — Primary carrier outage — failover to next-cheapest meeting SLA.

## Audit trail

Every scenario appends one hash-chained entry to the template's
`logs/<audit>.jsonl` file. Tail it during a run to see the
ledger build up across scenarios.
