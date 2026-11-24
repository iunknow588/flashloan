//! Materialize the directional pools of one scope against a store.
//!
//! The example is the smallest possible driver of the direction layer: it
//! takes the store path, the network, the chain id, the scope key and
//! whether the materialization is applied, and it prints the result. It
//! exists so the direction layer can be driven from the shell, which is
//! what the rank comparison between two builds needs.

//! ```text
//! cargo run --offline --release -p directional-pool-manager --example materialize_directional_pools -- <store> <network> <chain_id> <scope> <apply|dry-run>
//! ```

use directional_pool_manager::materialize_directional_pools;

fn main() {
    let arguments = std::env::args().collect::<Vec<_>>();
    if arguments.len() < 6 {
        eprintln!(
            "usage: materialize_directional_pools <store> <network> <chain_id> <scope> <apply|dry-run>"
        );
        std::process::exit(2);
    }
    let store = &arguments[1];
    let network = &arguments[2];
    let chain_id = arguments[3].parse::<i64>().expect("chain id must be a number");
    let scope_key = &arguments[4];
    let apply = arguments[5] == "apply";
    let result = materialize_directional_pools(store, network, chain_id, scope_key, apply)
        .expect("the directional pool materialization must succeed");
    println!("{}", serde_json::to_string(&result).expect("the result is serializable"));
}
