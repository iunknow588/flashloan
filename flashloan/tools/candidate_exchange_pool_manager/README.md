# Candidate Exchange Pool Manager

M4 owns the deterministic `YC -> ZC` exchange observation shadow.

It consumes:

- admitted token catalog rows;
- Candidate Edge Manager anchor-token rows and their version/checksum;
- Pool Manager `candidate_exchange` scope.

It writes only `manager_market_observation_*` tables. It never writes legacy
`market_observation_*`, Candidate Edge, closed-loop, review, or broadcast tables.

Pool usability is based on fixed fee, zero hook, identity consistency, and
positive on-chain liquidity. USD depth, USD TVL, and any USDG/USDE relationship
are intentionally not admission requirements.
