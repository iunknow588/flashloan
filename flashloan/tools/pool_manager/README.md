# Pool Manager

The Rust pool manager owns V4 scope materialization and the legacy candidate
pool maintenance commands.

## M2 scope

```powershell
cargo run -p pool-manager -- materialize-scope `
  --store <sqlite-path> --chain-id 4663 --scope core4 --dry-run

cargo run -p pool-manager -- materialize-scope `
  --store <sqlite-path> --chain-id 4663 --scope core4 --apply

cargo run -p pool-manager -- scope-status `
  --store <sqlite-path> --chain-id 4663 --scope core4
```

`core4` is fixed to:

```text
["core"] -> v4_exchange_pools_core4 -> edge_any
```

One pool endpoint must resolve to an admitted `token_core` anchor. Both
endpoints must resolve to admitted `token_key_id` values from the Token Manager
catalog. Pool identity is rebuilt from normalized V4 currency keys with the
shared `manager_contracts` ABI encoder.

Eligibility requires a fixed fee from `1` through `3000`, a zero hook,
`has_liquidity=1`, and a positive `liquidity_index`. USD depth, USD TVL, and a
direct or indirect USDG/USDE relationship are not eligibility requirements.

Rows with a complete PoolKey are persisted as `active`, `invalid`, `blocked`, or
`identity_mismatch`. Rows too incomplete to form a PoolKey are skipped and
reported under `skipReasonCounts`.

Dry-run and apply use the same canonical checksum. Repeating apply after no
semantic source change returns `changedRows=0`; timestamps are excluded from the
checksum.

The HTTP write paths are:

```text
GET  /v1/pools/scopes
POST /v1/pools/scopes/rebuild
POST /v1/pools/scopes/repair
```

Both POST routes require `X-Request-Id`, `Idempotency-Key`, and an explicit
`dryRun`. Apply is disabled outside manager authority. An apply commits pool
rows, `pool_scope_config`, and `manager_write_requests` in one immediate
transaction. Replaying the same key and request digest returns the original
result with `idempotentReplay=true`; a changed digest returns
`IDEMPOTENCY_KEY_REUSED`. Dry-run never writes the idempotency record.

## Legacy commands

- `audit`: audits YC, Y-Z, and ZC candidate rows without writing.
- `cleanup`: removes ineligible candidate rows only with `--apply`.
- `add-pair`: adds liquidity-qualified pools for one explicit token pair.
- `refresh-pair` / `update-pair`: refreshes one candidate pool group.
- `refresh-key`: refreshes one canonical token-pair key.

Raw `v4_exchange_pools` and liquidity snapshot rows are never deleted by these
commands. Candidate and closed-loop tables have separate ownership boundaries.
