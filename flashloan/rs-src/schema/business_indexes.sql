CREATE INDEX IF NOT EXISTS idx_v4_pools_token_edge ON v4_exchange_pools(network, chain_id, protocol, token0, token1)
;
CREATE INDEX IF NOT EXISTS idx_v4_liquidity_pool ON v4_exchange_pool_liquidity_snapshots(network, chain_id, protocol, pool_id)
;
CREATE INDEX IF NOT EXISTS idx_candidate_pairs_counter ON dex_candidate_pool_pairs(network, chain_id, protocol, anchor_symbol, counter_address)
;
CREATE INDEX IF NOT EXISTS idx_candidate_pairs_direction ON dex_candidate_pool_pairs(network, chain_id, protocol, anchor_symbol, direction_key)
;
CREATE INDEX IF NOT EXISTS idx_candidate_pools_edge ON dex_candidate_pools(network, chain_id, protocol, anchor_symbol, address_pair)
;
CREATE INDEX IF NOT EXISTS idx_candidate_pools_direction ON dex_candidate_pools(network, chain_id, protocol, anchor_symbol, direction_key)
;
CREATE INDEX IF NOT EXISTS idx_candidate_recommendations_direction ON dex_candidate_pool_recommendations(network, chain_id, protocol, anchor_symbol, direction_key)
;
CREATE INDEX IF NOT EXISTS idx_observation_yz ON market_observation_yz_pairs(network, chain_id, protocol, anchor_symbol, token_x_address, token_y_address)
;
CREATE INDEX IF NOT EXISTS idx_observation_yz_direction ON market_observation_yz_pairs(network, chain_id, protocol, anchor_symbol, direction_key)
;
CREATE INDEX IF NOT EXISTS idx_observation_pools ON market_observation_intermediate_pools(network, chain_id, protocol, anchor_symbol, pool_id)
;
CREATE INDEX IF NOT EXISTS idx_closed_loop_anchor ON v4_closed_loops(network, chain_id, protocol, market_id, anchor_symbol)
;
CREATE INDEX IF NOT EXISTS idx_closed_loop_hops_pool ON v4_closed_loop_hops(network, chain_id, protocol, market_id, pool_id);
