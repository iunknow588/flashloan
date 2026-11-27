//! Materialize the directional pools of every enabled scope of one chain.
//!
//! The chain level is the level the runtime works at: a chain runs
//! several scopes and every one of them has to be materialized, or the
//! closed loop that walks one scope would read a stale direction layer.
//!
//! ```text
//! cargo run --offline --release -p directional-pool-manager --example materialize_directional_pools_for_chain -- <store> <network> <chain_id> <apply|dry-run>
//! ```

use directional_pool_manager::materialize_directional_pools_for_chain;

fn main() {
    let arguments = std::env::args().collect::<Vec<_>>();
    if arguments.len() < 5 {
        eprintln!(
            "usage: materialize_directional_pools_for_chain <store> <network> <chain_id> <apply|dry-run>"
        );
        std::process::exit(2);
    }
    let store = &arguments[1];
    let network = &arguments[2];
    let chain_id = arguments[3].parse::<i64>().expect("chain id must be a number");
    let apply = arguments[4] == "apply";
    let result = materialize_directional_pools_for_chain(store, network, chain_id, apply)
        .expect("the directional pool chain materialization must succeed");
    println!(
        "{}",
        serde_json::to_string(&result).expect("the result is serializable")
    );
}
