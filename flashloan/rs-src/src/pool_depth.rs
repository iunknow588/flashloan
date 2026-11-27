//! Runtime entry into the pool manager's depth refresh.
//!
//! `v4_exchange_pool_liquidity_snapshots` has exactly **one** writer: the
//! Rust pool manager.  It writes the price columns and the single-tick
//! bounds in one statement, so a row always describes exactly one on-chain
//! moment.  The evidence channel deliberately does **not** write that table
//! (TASK-172 / AC-221), so the runtime has to reach the pool manager
//! itself: measuring the chain in Python and handing the numbers over
//! would fuse two on-chain moments into one row again.
//!
//! Before this entry existed the runtime had **no** way to write the table:
//! the pool manager's `refresh_depth` was only reachable through the
//! `pool-manager` command line.  The maintenance worker measured the chain
//! every tick, handed the measurement to the evidence channel, and the
//! evidence channel dropped it -- so the snapshot table only moved when an
//! operator ran the command by hand.
//!
//! The entry takes the pool ids the caller wants refreshed, asks the pool
//! manager to measure and materialise them, and returns the manager's own
//! receipt.  The caller stores the receipt; it never recomputes a capacity.
//!
//! The endpoints the entry tries are **the chain's own endpoint list**, not
//! one endpoint the caller happened to pick.  A refresh that owns a single
//! endpoint turns one momentary network fault into a failed maintenance
//! stage, and a failed stage stops the pipeline that publishes the next
//! review generation -- the whole chain stops for one dead node.  The
//! endpoint the caller names therefore stays **first** and the chain's own
//! endpoints follow it as the alternatives.

use serde_json::{json, Value};

/// The ordered endpoints the liquidity refresh tries.
///
/// The endpoint the caller named comes first and the chain's own endpoints
/// follow it.  Duplicates are listed once and the order is kept, so the
/// caller's choice stays the preferred one.  `None` means the caller named
/// nothing and the chain published nothing, which is a failure the caller
/// has to report rather than a refresh that quietly measures nothing.
pub fn liquidity_rpc_urls(named: Option<&str>, chain_urls: &[String]) -> Option<Vec<String>> {
    let mut urls: Vec<String> = Vec::new();
    if let Some(url) = named {
        push_endpoint(&mut urls, url);
    }
    for url in chain_urls {
        push_endpoint(&mut urls, url);
    }
    if urls.is_empty() {
        None
    } else {
        Some(urls)
    }
}

/// Adds one endpoint to the list, ignoring blanks and repeats.
fn push_endpoint(urls: &mut Vec<String>, value: &str) {
    let trimmed = value.trim();
    if !trimmed.is_empty() && !urls.iter().any(|existing| existing == trimmed) {
        urls.push(trimmed.to_owned());
    }
}

/// Refresh the liquidity snapshot rows of the given pools.
///
/// The pool manager owns the measurement: it reads the live pool state,
/// derives the per-side capacity and writes the snapshot row in one
/// statement.  A pool whose read was only half successful is reported as
/// an incomplete read and its row is left untouched, so a failed read can
/// never replace a good measurement with a fabricated empty pool.
///
/// The network is checked against the chain id before anything is written:
/// the pool manager derives the network from the chain id, and a mismatch
/// would materialise one chain's rows under another chain's scope.
pub fn refresh_pool_liquidity(
    database_path: &str,
    network: &str,
    chain_id: i64,
    pool_ids: &[String],
    rpc_url: Option<String>,
    batch_size: usize,
    timeout_seconds: u64,
    apply: bool,
) -> Result<Value, String> {
    let resolved_network = pool_manager::chain::network_for_chain_id(chain_id);
    if !network.trim().is_empty() && network.trim() != resolved_network {
        return Err(format!(
            "network {network} does not match chain id {chain_id} (the pool manager writes {resolved_network})"
        ));
    }

    let requested: Vec<String> = pool_ids
        .iter()
        .map(|pool_id| pool_id.trim().to_lowercase())
        .filter(|pool_id| !pool_id.is_empty())
        .collect();

    // The caller names the endpoint it prefers; the chain's own endpoints
    // follow it as the alternatives.  The chain is selected by the chain
    // id, so a testnet run never measures the mainnet's pools.
    let chain_urls = pool_manager::chain::rpc_urls_for_chain(chain_id);
    let rpc_urls = liquidity_rpc_urls(rpc_url.as_deref(), &chain_urls);

    let options = pool_manager::DepthOptions {
        pool_ids: requested.clone(),
        // An explicit pool list wins over the scope, so the scope only has
        // to name the population the row belongs to.  `All` is the honest
        // choice here: the caller picked the pools, not the tables.
        scope: pool_manager::DepthScope::All,
        // Resume would skip every pool whose row already carries a
        // timestamp, and the whole point of the tick is to move that
        // timestamp forward.
        resume: false,
        max_pools: None,
        progress_every: 0,
        rpc_url,
        rpc_urls,
        batch_size: batch_size.max(1),
        timeout_seconds: timeout_seconds.max(1),
        apply,
        // The USDG depth rows are a second, per-side measurement.  They
        // are not part of this entry: the snapshot row already carries the
        // binding capacity every consumer reads, and the valuation report
        // is a transient input the pool manager reads on its own.
        usdg_valuation_report: None,
        usdg_valuation_db: None,
    };

    let mut receipt = pool_manager::refresh_depth(database_path, chain_id, &options)?;
    if let Some(object) = receipt.as_object_mut() {
        object.insert("ok".to_owned(), Value::Bool(true));
        object.insert("manager".to_owned(), Value::String("pool_manager".to_owned()));
        object.insert("requestedNetwork".to_owned(), json!(resolved_network));
        object.insert("requestedPoolCount".to_owned(), json!(requested.len()));
        object.insert(
            "depthSnapshotWriter".to_owned(),
            json!("pool_manager"),
        );
    }
    Ok(receipt)
}

#[cfg(test)]
mod tests {
    use super::{liquidity_rpc_urls, refresh_pool_liquidity};

    #[test]
    fn the_named_endpoint_comes_first_and_the_chain_endpoints_follow() {
        let chain = vec!["https://chain.example/a".to_string()];
        let urls = liquidity_rpc_urls(Some("https://named.example"), &chain)
            .expect("a named endpoint must produce a list");
        assert_eq!(urls[0], "https://named.example");
        assert!(urls.contains(&"https://chain.example/a".to_string()));
        assert_eq!(urls.len(), 2);
    }

    #[test]
    fn a_repeated_endpoint_is_listed_once() {
        let chain = vec![
            "https://same.example".to_string(),
            "https://same.example".to_string(),
        ];
        let urls = liquidity_rpc_urls(Some("https://same.example"), &chain)
            .expect("a named endpoint must produce a list");
        assert_eq!(urls, vec!["https://same.example".to_string()]);
    }

    #[test]
    fn no_endpoint_at_all_yields_nothing() {
        assert!(liquidity_rpc_urls(None, &[]).is_none());
        assert!(liquidity_rpc_urls(Some("   "), &[]).is_none());
    }

    #[test]
    fn a_network_that_does_not_match_the_chain_id_is_refused() {
        let error = refresh_pool_liquidity(
            ":memory:",
            "robinhood-testnet",
            4663,
            &["0xabc".to_string()],
            None,
            10,
            20,
            false,
        )
        .expect_err("a mismatched network must not write another chain's rows");
        assert!(error.contains("does not match"), "{error}");
    }
}
