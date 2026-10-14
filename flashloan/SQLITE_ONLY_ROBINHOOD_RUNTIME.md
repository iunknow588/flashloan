# Robinhood SQLite-Only Runtime Source

## Rule

All Robinhood DEX page reads use the SQLite business store as the single
source of truth. JSON files under `chains/*/`, `runtime/evidence/`, and
`runtime/cache/` are build inputs and generated seed artifacts only; page
serving, quote routing, market observation, and candidate-pool reads must not
load them.

## Data flow

```text
chain JSON / evidence JSON
        |
        v
import_existing_json / builders / v4 evidence tools
        |
        v
local_store.sqlite3
        |
        v
page_payloads / rs_bridge / storage_* -> DEX pages
```

## Runtime sources

- Official stock anchors: `chain_tokens.token_metadata_json.category ==
  "robinhood_stock"`.
- Base anchors: `chain_config` constants; stock anchors are merged from
  SQLite when `database_url` is supplied.
- V4 static pools: `v4_exchange_pools`.
- V4 dynamic liquidity: `v4_exchange_liquidity` snapshots.
- Candidate pools and recommendation/backup roles: `dex_candidate_*` tables.
- Closed loops and directions: `v4_closed_loops`.
- Flash-loan protocol discovery: SQLite KV snapshots.

## Guards

New runtime code must not import or call the JSON registry helpers
(`stock_token_registry.load_stock_token_registry`,
`stock_token_registry.stock_token_by_address`) for page reads.  The build
tooling may continue to use JSON evidence to seed SQLite.

## Audited JSON readers

The remaining JSON reads in Robinhood code are intentional and do not serve
page data:

- Build/seed tools: `dex/tools/build_robinhood_mainnet_*`,
  `core/db/import_existing_json.py`, `core/db/builders.py`, and
  `stock_token_registry` without `database_url`.
- Test-only route helpers: `market/binance_market/dex_route_helpers.py` and
  `dex_route_test.py`.
- Runtime parameter files, not pool evidence: strategy/liquidation config
  readers (`core/parameter_config.py` consumers) and control-panel report
  files that already fall back to SQLite when the JSON report is absent.
- Non-Robinhood chains (BNB/Avalanche/Unified direct trade) keep their own
  JSON caches; they are outside this contract.

`stock_token_registry.load_stock_token_registry`,
`stock_token_by_symbol`, `stock_token_by_address`,
`stock_token_metadata_for_address`, `apply_stock_token_metadata`, and
`merge_stock_registry_into_metadata` accept `database_url`; when supplied they
read `chain_tokens` from SQLite and never touch the JSON registry file.
