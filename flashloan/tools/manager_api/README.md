# Manager API

The single HTTP/JSON boundary for Rust manager modules. The current M8 surface
contains health, capability, Token Manager, Pool Manager, Candidate Edge,
Candidate Exchange, Closed-loop, Review, Broadcast, and Composite pipeline
routes:

```text
GET  /healthz
GET  /v1/capabilities
GET  /v1/tokens?chainId=4663&library=core&status=admitted
GET  /v1/tokens/audit?chainId=4663&recentEventLimit=20
POST /v1/tokens/resolve
POST /v1/tokens/admit
POST /v1/tokens/block
GET  /v1/pools/scopes?chainId=4663&scopeKey=core4
POST /v1/pools/scopes/rebuild
POST /v1/pools/scopes/repair
GET  /v1/candidate-edge-pools?chainId=4663
GET  /v1/candidate-edge-pools/compare?chainId=4663
POST /v1/candidate-edge-pools/rebuild
GET  /v1/candidate-exchange-pools?chainId=4663
GET  /v1/candidate-exchange-pools/compare?chainId=4663
POST /v1/candidate-exchange-pools/rebuild
GET  /v1/closed-loops?chainId=4663
GET  /v1/closed-loops/compare?chainId=4663
POST /v1/closed-loops/rebuild
GET  /v1/reviews?chainId=4663
POST /v1/reviews/run
GET  /v1/overview?chainId=4663&limit=1000
GET  /v1/broadcasts?chainId=4663
POST /v1/broadcasts/submit
POST /v1/broadcasts/finalize
POST /v1/broadcasts/clear
GET  /v1/pipeline/status?chainId=4663
GET  /v1/pipeline/runs?chainId=4663&limit=50
GET  /v1/pipeline/runs/{run_id}?chainId=4663
POST /v1/pipeline/refresh
POST /v1/pipeline/steps/begin
POST /v1/pipeline/steps/complete
```

Start the service with a manager database:

```powershell
$env:MANAGER_DB_PATH = "<manager-schema.sqlite3>"
$env:MANAGER_API_BIND = "127.0.0.1:8091"
$env:TOKEN_MANAGER_AUTHORITY = "legacy"
$env:CANDIDATE_MANAGER_AUTHORITY = "legacy"
$env:CANDIDATE_EXCHANGE_MANAGER_AUTHORITY = "legacy"
$env:CLOSED_LOOP_MANAGER_AUTHORITY = "legacy"
$env:REVIEW_MANAGER_AUTHORITY = "legacy"
$env:BROADCAST_MANAGER_AUTHORITY = "legacy"
$env:COMPOSITE_MANAGER_AUTHORITY = "legacy"
cargo run --manifest-path flashloan/tools/Cargo.toml -p manager-api
```

`MANAGER_DB_PATH` is optional for `/healthz` and `/v1/capabilities`. Token
routes fail closed with `UPSTREAM_UNAVAILABLE` when it is absent or the library
schema is not available. `businessWritesEnabled` and `tokenReadsEnabled` reflect
whether the business database is configured. `/healthz` never opens the
business database.

The seven authority variables are independent. An unset value defaults to
`legacy`. `legacy` and `rollback` still permit read, compare-only, and
`dryRun=true`, but every `dryRun=false` request fails closed. Enabling one
module with `manager` does not enable any other module.

Resolve request:

```json
{"chainId":4663,"symbol":"USDG","requiredStatus":"admitted"}
```

The request is rejected with `TOKEN_SYMBOL_AMBIGUOUS` when distinct
`token_key_id` values share the normalized symbol. A primary/extension overlap
for the same `token_key_id` is one candidate with multiple memberships.

Admission request:

```http
POST /v1/tokens/admit
Content-Type: application/json
X-Request-Id: request-20261104-001
Idempotency-Key: admit-robinhood-4663-token-001

{
  "chainId": 4663,
  "libraryKey": "extension",
  "tokenKeyId": "robinhood:4663:0x...",
  "reason": "operator approved after liquidity review",
  "evidence": {"ticket": "OP-001"},
  "dryRun": true
}
```

`POST /v1/tokens/block` accepts the same body shape. Every admission write
requires both headers and an explicit Boolean `dryRun`; a missing value is not
treated as apply.

- `dryRun=true` returns the version and checksum that apply would produce, but
  rolls back all rows, state, events, and idempotency records.
- `dryRun=false` commits the status, library version, checksum, admission event,
  and idempotency record in one `IMMEDIATE` transaction.
- Repeating the same idempotency key with the same request content returns the
  original result with `idempotentReplay=true`.
- Reusing the key for different content returns
  `IDEMPOTENCY_KEY_REUSED` / HTTP 409.
- The persisted admission source is `manager_api`; the request id is retained
  in the audit event evidence.

Audit response includes four-library status counts, library state/checksum,
duplicate symbols, primary-library conflicts, admission sources, recent events,
and state-to-row consistency. `recentEventLimit` defaults to `20` and must not
exceed `100`.

`GET /v1/candidate-edge-pools` returns the manager-owned Candidate Edge
snapshot. In addition to `status`, `anchorTokens`, `poolPairs`, `pools`,
`recommendations`, and abnormal rows, it includes a bounded `tokens` array with
`tokenKeyId`, `tokenAddress`, `tokenSymbol`, decimals, library key, and
admission status for only the anchors/candidates referenced by that snapshot.
The token metadata is read from `token_library_catalog`; symbols are display
metadata and are not used as persistence keys. The Flask candidate page maps
this response directly to its existing `anchorGroups`, shared/YC/ZC pools,
pairs, abnormal sections, and pagination DTO, and fails closed with HTTP 503
when the manager response is unavailable or malformed. USD depth,
USD-denominated TVL, and USDG/USDE linkage are not admission conditions for
candidate or YZ pools.

Candidate Exchange rebuild request:

```http
POST /v1/candidate-exchange-pools/rebuild
Content-Type: application/json
X-Request-Id: request-20261104-m4-001
Idempotency-Key: candidate-exchange-4663-20261104-001

{
  "chainId": 4663,
  "expectedUpstream": {
    "candidateEdgeVersion": 7,
    "candidateEdgeChecksum": "sha256:...",
    "poolScopeChecksum": "sha256:...",
    "libraryVersions": {"core": 3, "stocks": 5, "test": 1, "extension": 9},
    "libraryChecksums": {
      "core": "sha256:...",
      "stocks": "sha256:...",
      "test": "sha256:...",
      "extension": "sha256:..."
    }
  },
  "dryRun": true
}
```

The M4 rebuild writes only `manager_market_observation_*` shadow tables during
migration. It does not write legacy `market_observation_*`, candidate edge,
closed-loop, review, or broadcast tables. The compare route is read-only and
attributes manager-only pools caused solely by removal of the old USD-depth
gate as `legacyUsdDepthGateRemoved`; field mismatches and legacy-only rows still
block cutover.

`GET /v1/candidate-exchange-pools` also returns a bounded `tokens` array for
the anchors, Y/Z endpoints, and intermediate pools referenced by the current
observation snapshot. The Flask market-observation page maps `yzPairs`, `pools`,
abnormal rows, and token metadata directly to its existing candidate groups,
bridge-pool groups, associated pairs/pools/tokens, and pagination DTO. The GET
route is manager-only and fails closed with HTTP 503 when the manager response
is unavailable or malformed. It never evaluates USD depth, USD TVL, or a
USDG/USDE relation for observation-pool admission. The Flask page refresh route
uses RuntimeCore `rebuild_candidate_exchange_pools` rather than this HTTP
rebuild endpoint, so production UI traffic does not call the legacy builder or
fall back to SQLite. This HTTP rebuild endpoint remains the manager
compatibility/diagnostic ingress and still requires the independent
`CANDIDATE_EXCHANGE_MANAGER_AUTHORITY=manager` gate for apply. UI command-path
migration does not switch the mainnet M4 production authority.

M5 Closed-loop rebuild request:

```http
POST /v1/closed-loops/rebuild
Content-Type: application/json
X-Request-Id: request-20261104-m5-001
Idempotency-Key: closed-loops-4663-20261104-001

{
  "chainId": 4663,
  "expectedUpstream": {
    "candidateEdgeVersion": 7,
    "candidateEdgeChecksum": "sha256:...",
    "observationVersion": 4,
    "observationChecksum": "sha256:...",
    "poolScopeChecksum": "sha256:...",
    "libraryVersions": {"core": 3, "stocks": 5, "test": 1, "extension": 9},
    "libraryChecksums": {
      "core": "sha256:...",
      "stocks": "sha256:...",
      "test": "sha256:...",
      "extension": "sha256:..."
    }
  },
  "dryRun": true
}
```

M5 writes only `manager_v4_closed_loop_*` shadow tables. It consumes one M3
candidate-edge version/checksum plus one M4 observation version/checksum, and
rejects the rebuild when those upstream versions or their stored checksums do
not form a single consistent snapshot. Legacy `v4_closed_loops` and
`v4_closed_loop_hops` are read-only compare inputs. M5 does not calculate PnL,
does not create broadcast requests, and has no USD-depth or stable-token
predicate.

`GET /v1/closed-loops` is the manager-owned closed-loop page projection used by
`/api/dex/closed-loop-groups`. Flask maps `loops`, `hops` and `status` to the
existing compact group DTO (`flashLoanToken`, `routeCount`,
`poolDependencyCount`) and, only when `include_routes=true`, to a display-only
route/routePool projection. The group borrowable flag uses
`borrowableBasis=closed_loop_manager_anchor`; it means the manager approved the
loop anchor and does not claim on-chain flash source balance. The Flask route
fails closed with HTTP 503 when the manager response is unavailable or
malformed. `/v1/closed-loops/compare` remains an M5 dual-read diagnostic and is
not a page GET data path.

`GET /v1/overview` is a read-only, manager-owned page projection. Composite
Manager combines the latest candidate-edge, YZ, closed-loop, Review and
Broadcast snapshots with pipeline/upstream version data. It resolves symbols
through admitted Token Manager rows and returns path pools, YZ watch pairs,
closed-loop groups, version/checksum consistency and data-quality markers.
`selectedProtocols`, `routeFrameworkSource`, `marketObservationSource`,
`marketObservationSourceStage` and `decisionBasis` are emitted by the manager
projection rather than inferred by Flask. YC/ZC hop candidates use Candidate
Edge pools and Y-Z hop candidates use Candidate Exchange pools.

PnL is projected only from Review Manager signed PnL and is complete only when
the corresponding review status is `completed`; missing PnL remains
incomplete. USD depth and USD-denominated TVL are not admission requirements.
This route does not read legacy overview caches or legacy business tables and
is the upstream used by both the manager-only DEX overview and quote-routes
pages. The quote-routes adapter maps `arbitragePaths`, `arbitragePathGroups`,
execution pools, closed-loop groups, versions, data quality and pipeline status
into the existing page DTO, including complete V4 PoolKey identity and
`borrowableBasis=manager_closed_loop_anchor` on closed-loop groups. Flask
validates the manager chain scope and required DTO shape, returns
`dataSource=manager` error DTOs on failure, and does not invent empty business
fields or expose legacy shadow diagnostics. The quote-routes GET does not run
the legacy bootstrap, observation loader, opportunity scanner, scan-now path or
recent-attempt merge. Anchor filtering is presentation-only and does not add a
USD-depth or USDG/USDE relation gate.

The quote page reads the manager-only `/api/dex/quote-routes` projection for
its initial load, refresh and retry flows. `/api/dex/quote-routes/static` is
retained only for legacy regression fixtures and diagnostics; it is not a page
data source and must not be reintroduced as a UI fallback.

The quote page also retires all `opportunity-scanner` calls:
`snapshot`, `start`, `scan-now`, `auto-execute` and `stats-reset` remain server
maintenance/diagnostic routes but are not page data sources. The page renders
Manager versions, Review/Broadcast counts and consistency fields from the
`/v1/overview` projection instead of Python scanner session state. Automatic
execution and statistics-reset controls stay disabled on the page until the M8
manager pipeline control surface replaces them.

`GET /api/dex/quote-request` uses the same manager-owned `/v1/overview`
projection. Flask selects one `arbitragePath` by pair, maps its complete V4
PoolKey, pool IDs, route inputs and Review signed PnL into the existing quote
request DTO, and wraps the manager QuoteInputs as
`quoteMode=manager_overview_projection`. The route does not run the legacy
surface bootstrap, quote-request builder, quote-route loader or observation
loader and never triggers a live quote. A missing pair returns 404
`manager_request_not_found`; an unavailable or malformed manager response
returns 503 `manager_read_failed`. Both error DTOs keep `dataSource=manager`
and `managerDetail`, with no legacy fallback.

M6 Review request:

```http
POST /v1/reviews/run
Content-Type: application/json
X-Request-Id: request-20261104-m6-001
Idempotency-Key: reviews-4663-20261104-001

{
  "chainId": 4663,
  "expectedUpstream": {
    "closedLoopVersion": 9,
    "closedLoopChecksum": "sha256:...",
    "candidateEdgeVersion": 7,
    "candidateEdgeChecksum": "sha256:...",
    "observationVersion": 4,
    "observationChecksum": "sha256:...",
    "poolScopeChecksum": "sha256:...",
    "libraryVersions": {"core": 3},
    "libraryChecksums": {"core": "sha256:..."}
  },
  "evidence": [
    {
      "loopKey": "robinhood:4663:0x...:0x...:0x...",
      "staticPreview": {},
      "reviewedAt": "2026-11-04T00:00:00Z",
      "runArbitrage": {
        "ok": true,
        "executable": true,
        "blockNumber": 100,
        "signedPnl": {
          "raw": "12.5 USDG",
          "amount": 12.5,
          "currency": "USDG",
          "unit": "token",
          "source": "runArbitrage"
        },
        "gasEstimate": "120000"
      }
    }
  ],
  "dryRun": true
}
```

M6 writes only `manager_review_library_state`, `manager_v4_reviews`,
`manager_v4_review_diagnostics`, and `manager_v4_review_broadcast_requests`
shadow tables. It locks one M5 closed-loop version/checksum plus its recorded
upstream versions and checksums. M6 does not call RPC or calculate PnL; missing
signed PnL is `data_incomplete`, diagnostics never replace PnL, and only a
completed review with a strictly positive executable signed PnL creates a
`generated` broadcast request. M6 never sends a transaction.

M7 Broadcast submit request:

```http
POST /v1/broadcasts/submit
Content-Type: application/json
X-Request-Id: request-20261104-m7-001
Idempotency-Key: broadcasts-4663-20261104-001

{
  "chainId": 4663,
  "attempts": [
    {
      "attemptId": "attempt-1",
      "loopKey": "robinhood:4663:0x...:0x...:0x...",
      "signerAddress": "0x...",
      "nonce": 42,
      "gasLimit": 500000,
      "gasPriceWei": "1000000000",
      "rpcUrl": "https://rpc.example.invalid",
      "rpcName": "primary",
      "evidence": {"signerNonce": 42}
    }
  ],
  "dryRun": true
}
```

M7 finalize request:

```http
POST /v1/broadcasts/finalize
Content-Type: application/json
X-Request-Id: request-20261104-m7-002
Idempotency-Key: broadcasts-finalize-4663-20261104-001

{
  "chainId": 4663,
  "finalizations": [
    {
      "attemptId": "attempt-1",
      "status": "failed",
      "txHash": "0x...",
      "receipt": {"status": "0x0"},
      "statusReason": "rpc rejected transaction",
      "diagnostics": [
        {
          "diagnosticType": "rpc_unavailable",
          "diagnosticCode": "RPC_TIMEOUT",
          "message": "primary RPC timed out",
          "rpcUrl": "https://rpc.example.invalid",
          "txHash": "0x...",
          "payload": {"attempt": 1},
          "observedAt": "2026-11-04T01:00:00Z"
        }
      ],
      "evidence": {"receipt": "timeout"},
      "finalizedAt": "2026-11-04T01:00:01Z"
    }
  ],
  "dryRun": true
}
```

M7 only accepts Review broadcast requests with a complete positive signed PnL.
`submit` persists a pending attempt with signer/nonce/gas/RPC path/evidence but
does not touch the network; `finalize` moves pending to `success` or `failed`
and preserves tx hash, nonce, RPC, receipt, status reason, and diagnostics.
M7 `broadcast_manager::ExecutionQueue` owns the only execution queue, and
`broadcast_manager::BroadcastSupervisor` owns the bounded
`rust-broadcast-manager-exec-*` workers. `rs-src/src/worker_pool.rs` is the PyO3
host and execution adapter; it must not define a second queue or worker owner.
Python `BroadcastSupervisor` is test/legacy-only and is not part of native
production assembly.

M8 pipeline refresh request:

```http
POST /v1/pipeline/refresh
Content-Type: application/json
X-Request-Id: request-20261104-m8-001
Idempotency-Key: pipeline-4663-20261104-001

{
  "chainId": 4663,
  "requestedSteps": [
    "candidate_edge",
    "candidate_exchange",
    "closed_loop",
    "review",
    "broadcast"
  ],
  "dryRun": true
}
```

If `requestedSteps` is omitted, the API uses the same five-step list. The full
internal order also permits `pool_scope` as the first step. Steps must be
unique and follow the fixed order
`pool_scope -> candidate_edge -> candidate_exchange -> closed_loop -> review ->
broadcast`.

M8 step begin request:

```http
POST /v1/pipeline/steps/begin
Content-Type: application/json
X-Request-Id: request-20261104-m8-002
Idempotency-Key: pipeline-begin-4663-20261104-001

{
  "chainId": 4663,
  "runId": "pipeline-run-...",
  "stepId": "pipeline-step-...",
  "dryRun": true
}
```

M8 step complete request:

```http
POST /v1/pipeline/steps/complete
Content-Type: application/json
X-Request-Id: request-20261104-m8-003
Idempotency-Key: pipeline-complete-4663-20261104-001

{
  "chainId": 4663,
  "runId": "pipeline-run-...",
  "stepId": "pipeline-step-...",
  "status": "completed",
  "result": {"source": "manager"},
  "error": null,
  "dryRun": true
}
```

Completion status is one of `completed`, `failed`, or `idle`. A failed step can
be retried from the latest successful checkpoint. Completed and idle steps
cannot be begun again. A pipeline refresh is rejected when the token libraries,
pool scope, candidate edge, candidate exchange, closed loop, review, or
broadcast snapshots do not form one consistent upstream version set. The
broadcast step cannot complete while reviewed broadcast attempts remain
pending.

Manager M8 does not execute business rebuilds, call RPC, start workers, or own
runtime queues. It writes only `manager_pipeline_runs`, `manager_pipeline_steps`,
`manager_pipeline_events`, `manager_events`, and the request idempotency ledger.
Dry-run writes none of those rows. Review and Broadcast managers are runtime
management tools, not persistence-only facades:
`review_manager::ReviewSupervisor` owns the review main loop and bounded review
workers, while `broadcast_manager::BroadcastSupervisor` owns the broadcast main
loop, execution queue, and bounded broadcast workers. `rs-src/src/worker_pool.rs`
is a PyO3 host and business execution adapter. `manager_api` is the sole
external HTTP command/read gateway; it does not own worker threads or queues.
UI consumes the Rust `RuntimeCore` through PyO3 rather than calling this HTTP
boundary directly.

RuntimeCore and native-pool ownership:

- `flashloan-rs` keeps one `RuntimeCore` per chain. Its snapshot reads Manager
  public read APIs only and atomically replaces one generation after Manager
  versions/checksums and the Composite fingerprint agree.
- The PyO3 surface is `runtime_core_register`, `runtime_core_status`,
  `runtime_core_state`, `runtime_core_manager_snapshot`, `runtime_core_command`,
  `runtime_core_manager_view` and `runtime_core_close`. Commands require
  `requestId`, `idempotencyKey` and explicit `dryRun`; the command set currently
  contains read-only `refresh_snapshot`, authoritative `clear_broadcasts`, and
  authoritative `rebuild_candidate_edges` and
  `rebuild_candidate_exchange_pools`.
- `runtime_core_register` retains its original six-argument call contract and
  appends optional `candidate_exchange_authority="legacy"` as the seventh
  argument. Candidate edge, candidate exchange, and broadcast authority values
  are part of RuntimeCore's per-chain registration identity.
- Migrated page GETs use RuntimeCore views `overview`, `candidate_edge`,
  `candidate_exchange`, `closed_loops`, `reviews` and `broadcasts`. View options
  are allow-listed in Rust; review/broadcast filters and pagination are
  preserved. Submission-record clear now calls `clear_broadcasts` through
  RuntimeCore; candidate-pool refresh calls `rebuild_candidate_edges` through
  RuntimeCore and uses `CANDIDATE_MANAGER_AUTHORITY` for apply gating;
  market-observation refresh calls `rebuild_candidate_exchange_pools` through
  RuntimeCore and uses `CANDIDATE_EXCHANGE_MANAGER_AUTHORITY`. This HTTP API
  remains the compatibility/diagnostic gateway and the ingress for commands not
  yet migrated.
- `native_pool_registry.rs` owns `ClosedLoopWorkerPool` lifecycle. Its
  `native_pool_get_or_create`, `native_pool_registry_manage` and
  `native_pool_registry_status` APIs key pools by normalized database path and
  support stopping one chain without affecting other chains.
- Python `ChainRuntimeContext` only proxies PyO3 and maps DTOs. It stores no
  authoritative Manager snapshot and no native pool cache.
- Build distributable Windows wheels with `maturin build --release
  --auditwheel repair`; the repair step packages the external `zlib.dll` under
  `flashloan_rs.libs`.

UI refresh cadence is also outside manager ownership. The quote page keeps its
own timer and periodically reads `/api/dex/quote-routes`. Its independent
`DEX_QUOTES_UI_REFRESH_INTERVAL_SECONDS` default does not read or alter worker
cadence, and it never starts, stops, or reschedules review/broadcast workers.
Group-level and route-level worker lifecycle controls are manager runtime
commands, not page controls. UI refresh must never start, stop, or reschedule
manager workers.
Quote-page polling is read-only and does not automatically call route estimates,
RPC quotes, review submission, or broadcast. Explicit user commands remain
separate runtime command-ingress surfaces pending manager command APIs.
