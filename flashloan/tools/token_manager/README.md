# Token Manager

Rust token-library owner for the Robinhood Mainnet `4663` manager refactor.
The crate currently provides the M0 schema-backed four-library row model, the
D1.1.2 transactional storage service, D1.1.3 read-only metadata
normalization, D1.1.4 library classification, D1.1.6 admission-diff and
primary-conflict audit, D1.1.7 temporary-database bootstrap simulation,
D1.2.1 token reads, D1.2.2 admission writes, and D1.2.3 manager audit.
D1.2.4 defines the cross-language authority switch and rollback drill.
Production bootstrap apply and the production authority switch are still
pending a maintenance window.

## Libraries

- `core` -> `token_core`
- `stocks` -> `token_stocks`
- `extension` -> `token_extension`
- `test` -> `token_test`

Each `token_key_id` may have at most one primary membership in `core`, `stocks`,
or `test`; those primary libraries are mutually exclusive. `extension` is a
derived membership library and may overlap any primary library. `test` contains
only the explicit TZ address per network; TA and TB can enter `extension` when
they have a direct pool with TZ. Symbols are display and configuration values
only; they are never persistent foreign keys. Native ETH uses the V4
zero-address currency key and remains distinct from WETH.

## Extension Admission

`extension` membership is a derived candidate membership. A place enters
`extension` only when both conditions hold:

- its normalized metadata is complete and accepted by the Token Manager; and
- it has at least one direct V4 pool to `core`, `stocks`, or `test/TZ` whose
  liquidity evidence passes the current Pool Manager quality gate.

The extension relationship is defined by direct pool connectivity, not by
USD valuation. A token may enter `extension` when it can exchange directly
with any core token, a stock token, or test/TZ. This means extension may
contain core tokens, stock tokens, TA, and TB. TZ itself remains a `test`
primary token and does not receive an extension membership.

The liquidity gate requires:

- fixed fee in `1..=3000` with no dynamic or special fee;
- zero hooks;
- `has_liquidity=1` with positive `liquidity_index`.

`depth_status`, `estimated_tvl_usd`, and legacy `deep` / `thin` / `unpriced`
labels are diagnostic fields only. They must not reject a pool that has
confirmed on-chain liquidity, and candidate pools and YZ exchange pools do not
need a USD relationship.

`TA` and `TB` can enter `extension` through a direct TZ pool. `TZ` itself stays
in `test` and is never assigned an `extension` membership. The mainnet database
is read-only for this classification audit; no extension rows are written yet.

## Storage Contract

`store::write_library` and `store::write_library_dry_run` run in an `IMMEDIATE`
SQLite transaction. The dry-run variant rolls back after returning the same
insert/update/checksum/version result. The service:

- rejects inconsistent addresses and conflicting primary-library ownership
  before writes, while allowing legal `extension` overlap;
- creates admission events only for inserted or semantically changed tokens;
- increments `library_version` at most once per changed library write;
- stores an ordered canonical-JSON SHA-256 checksum and source checkpoint;
- treats an identical re-import as unchanged without a version increment.

The service writes temporary test databases in unit and integration tests.
HTTP admission writes are enabled only when `MANAGER_DB_PATH` points at a
manager schema; the mainnet database is not migrated or written by these tests.

## Metadata Normalization

The read-only source adapters cover `chain_tokens`, the Robinhood stock
registry, the fixed mainnet core4 address allowlist, and V4 pool currency
references. Normalization:

- lowercases and validates addresses and computes `token_key_id`;
- trims and uppercases symbols without deriving symbols from addresses;
- rejects empty, `UNRESOLVED:`, and `0X` hex placeholder symbols;
- requires decimals in `0..=36`, an explicit metadata and admission source,
  valid JSON objects, and consistent native/WETH/stable flags;
- computes a deterministic per-token SHA-256 metadata checksum.

Run the actual database audit through a read-only SQLite connection:

```text
cargo run --manifest-path flashloan/tools/token_manager/Cargo.toml --bin token-manager -- audit-metadata --db flashloan/db/local_store_4663.sqlite3 --registry flashloan/src/chains/robinhood/robinhood_stock_tokens.json
```

Run the D1.1.6 admission-diff audit against a read-only target manager schema:

```text
cargo run --manifest-path flashloan/tools/token_manager/Cargo.toml --bin token-manager -- audit-library --source-db flashloan/db/local_store_4663.sqlite3 --target-db <manager-schema.sqlite3> --registry flashloan/src/chains/robinhood/robinhood_stock_tokens.json
```

When the target database has no M0 token tables yet, the audit reports
`targetSchemaState=absent` and does not migrate or apply. Use a temporary or
copy database with the manager schema for D1.1.7 dry-run before production.

Run the completed D1.1.7 temporary-database bootstrap simulation:

```text
cargo run --manifest-path flashloan/tools/token_manager/Cargo.toml --bin token-manager -- bootstrap-simulate --source-db flashloan/db/local_store_4663.sqlite3 --registry flashloan/src/chains/robinhood/robinhood_stock_tokens.json
```

This creates a disposable SQLite database, applies the M0 migration twice,
runs all four libraries in dry-run mode against the empty schema, then applies
each library and applies it again to prove idempotence and checksum stability.
The mainnet source database is opened read-only; production apply remains
disabled.

## HTTP API

`manager_api` exposes the Token Manager routes through:

```text
GET  /v1/tokens?chainId=4663&library=core&status=admitted
GET  /v1/tokens/audit?chainId=4663&recentEventLimit=20
POST /v1/tokens/resolve
POST /v1/tokens/admit
POST /v1/tokens/block
```

Set `MANAGER_DB_PATH` to the manager SQLite database before starting the API.
Queries use `SQLITE_OPEN_READ_ONLY`; symbol resolution deduplicates legal
primary/extension overlap by `token_key_id` and returns
`TOKEN_SYMBOL_AMBIGUOUS` only when distinct addresses share a symbol.

Admission writes require `X-Request-Id`, `Idempotency-Key`, and an explicit
Boolean `dryRun` in the JSON body. `dryRun=true` performs the same read and
checksum preview as apply, then rolls back without changing status, version,
or events. Apply uses an `IMMEDIATE` transaction, writes the admission event,
version, checksum, and idempotency record atomically. Reusing an idempotency
key for different request content returns `IDEMPOTENCY_KEY_REUSED`.

## Authority Switch

Rust and Python share `TOKEN_MANAGER_AUTHORITY`:

- `legacy` is the default and preserves the current Python writer. Manager API
  admission apply returns `TOKEN_AUTHORITY_WRITE_DENIED`; dry-run remains
  available for comparison.
- `manager` enables manager API admission apply and disables Python token
  authority writes. Python may only call
  `project_chain_tokens_from_manager` to publish a non-destructive
  compatibility projection.
- `rollback` restores legacy Python writes only when
  `TOKEN_MANAGER_ROLLBACK_REASON` is non-empty. Manager tables, admission
  events, library state, and idempotency records remain intact.

The projection publishes only `admitted` and `candidate_only`, deduplicates an
extension overlap in favor of `core -> stocks -> test`, and never deletes other
`chain_tokens` rows. Its explicit dry-run and apply modes must return the same
canonical checksum. If a blocked or pending manager token still exists in the
legacy projection, dry-run reports `readyForProjection=false` and apply fails
with `TOKEN_PROJECTION_EXCLUDED_ROWS_PRESENT`. An invalid authority value fails
closed.

## Verification

```text
cargo fmt --manifest-path flashloan/tools/Cargo.toml --all -- --check
cargo check --manifest-path flashloan/tools/Cargo.toml --workspace
cargo test --manifest-path flashloan/tools/Cargo.toml --workspace
cargo clippy --manifest-path flashloan/tools/Cargo.toml -p token-manager -p manager-api -p pool-manager --all-targets --no-deps -- -D warnings
```

Run the status entry point:

```text
cargo run --manifest-path flashloan/tools/token_manager/Cargo.toml
```
