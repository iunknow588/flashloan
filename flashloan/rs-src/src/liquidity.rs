use serde_json::{json, Value};

pub fn snapshot_row(
    pool_id: &str,
    has_liquidity: Option<bool>,
    liquidity: Option<&str>,
    liquidity_index: Option<f64>,
    estimated_tvl_usd: Option<f64>,
    sqrt_price_x96: Option<&str>,
    price: Option<f64>,
    tick: Option<i32>,
    liquidity_read_at: Option<&str>,
    liquidity_block_number: Option<u64>,
    recent_swap_active: Option<bool>,
    observed_at: &str,
    refreshed_at: &str,
) -> Value {
    json!({
        "poolId": pool_id.to_lowercase(),
        "hasLiquidity": has_liquidity,
        "liquidity": liquidity,
        "liquidityIndex": liquidity_index,
        "estimatedTvlUsd": estimated_tvl_usd,
        "sqrtPriceX96": sqrt_price_x96,
        "price": price,
        "tick": tick,
        "liquidityReadAt": liquidity_read_at,
        "liquidityBlockNumber": liquidity_block_number,
        "recentSwapActive": recent_swap_active,
        "observedAt": observed_at,
        "refreshedAt": refreshed_at,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snapshot_row_keeps_pool_id_lowercased() {
        let row = snapshot_row(
            "0xABC", None, None, None, None, None, None, None, None, None, None,
            "2026-10-09", "2026-10-09",
        );
        assert_eq!(row["poolId"], "0xabc");
    }
}
