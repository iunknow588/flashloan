# Candidate Edge Pool Manager

Owns the shadow `manager_dex_candidate_*` tables for the `core4`
candidate-edge scope. The legacy `dex_candidate_*` tables remain untouched
until the M3 comparison and cutover gates pass.

The manager reads:

- admitted tokens from Token Manager's `token_library_catalog`;
- active and isolated pools from Pool Manager's `v4_exchange_pools_core4`.

It writes candidate tokens, candidate pairs, eligible candidate pools,
directional recommendations and abnormal pools. It never writes Y-Z,
closed-loop or broadcast tables.

`compare_candidate_edges` is a read-only cutover gate. It normalizes the
manager shadow tables and the legacy `dex_candidate_*` tables into comparable
anchor, direction, pool and recommendation sets, then reports checksums,
manager-only rows, legacy-only rows, field mismatches and unresolved legacy
anchors. The comparison uses Pool IDs and normalized addresses; it does not
require USD depth, USD TVL or a USDG/USDE relationship.

The HTTP boundary exposes the same operation as
`GET /v1/candidate-edge-pools/compare`. It never writes business tables,
idempotency records or events, and cannot enable candidate apply authority.
