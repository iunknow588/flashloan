# P3-4/P3-5/P4/P5 Acceptance Record

Date: 2026-10-17

## Delivered

- Rust execution primitives in `flashloan/rs-src/src/execution.rs` with Pyo3
  registration and Rust regression vectors for EIP-1559, EIP-155 and V4 quote
  calldata.
- Pyo3 bridge wrappers in `core.rs_bridge` for:
  `resolve_private_key_hex`, `sign_typed_transaction_json`,
  `build_v4_quote_calldata`, `quote_v4_exact_input_single`,
  `submit_v4_route`, `load_v4_receipt`, `get_v4_pending_nonce`.
- Rust-first page receipt lookup, unified direct broadcast, and V4 quote
  eth_call paths, with an explicit Python-only broadcast fallback.
- Architecture guards and fake `flashloan_rs` bridge unit tests.
- Directional candidate backfill fixes: latest-to-oldest scanning no longer
  loses the segment start after adaptive shrinking; range-limited providers
  are rotated without shrinking the whole scan, and retryable RPC failures
  are temporarily quarantined.
- Token-topic filtering for reverse `Initialize` scans, so directional
  backfill only fetches logs involving the requested currencies.
- Read-only testnet evidence tool
  `flashloan/src/dex/tools/collect_robinhood_v4_backfill_evidence.py`; it
  writes only under `runtime/evidence/robinhood-testnet`, never SQLite.

Current protocol scope is intentionally Uniswap V4 only. Other protocols are
deferred to a later release.

## Acceptance Commands

- `cargo test --manifest-path flashloan/rs-src/Cargo.toml`
  - 27 passed, 0 failed
- `python -m compileall -q flashloan/src flashloan/app_runtime`
  - passed
- `python -m pytest flashloan/app_runtime/tests/unit -q`
  - 1653 passed, 1 skipped, 0 failed
- `python -m pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py -q`
  - 36 passed
- `python -m pytest flashloan/app_runtime/tests/functional/test_web_app_assembly_workflows.py::test_dex_surface_payload_apis_are_distinct -q`
  - 1 passed
- `python flashloan/src/dex/tools/build_v4_integrity_report.py --database flashloan/src/runtime/local_store.sqlite3 --network robinhood --chain-id 4663`
  - ok, 0 violations
- `python flashloan/src/dex/tools/build_v4_integrity_report.py --database flashloan/src/runtime/local_store.sqlite3 --network robinhood-testnet --chain-id 46630`
  - ok, 0 violations
- `python -m dex.tools.benchmark_v4_cold_import --cold-import --api-p95` (from `flashloan/src`)
  - cold import 8.27s; API p95 round 1 27.44s, round 2 17.11s

## Known Non-Blocking Items

- Cold import is above the `<2s` target and is archived as an optimization item.
- With `DEX_V4_LIVE_QUOTE_ENABLED=0`, the benchmark still issued RPC calls to
  the testnet endpoint; reproduced during acceptance and tracked separately.
- A live testnet directional backfill evidence run was attempted for
  AMZN/ETH and NFLX/ETH both directions. The official and publicnode RPCs
  repeatedly returned HTTP 429 and TLS handshake/read timeouts, so the
  evidence bundle was not finalized this round. The scan, liquidity
  confirmation, topic-filter and provider-fallback code paths are covered by
  unit tests and ready to rerun against a stable RPC.
