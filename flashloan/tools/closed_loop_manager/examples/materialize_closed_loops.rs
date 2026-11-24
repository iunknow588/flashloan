//! Materialize the closed loops of one chain against a store.
//!
//! The example is the smallest possible driver of the closed-loop layer:
//! it takes the store path, the network, the chain id and whether the
//! materialization is applied, and it prints the result. It exists so the
//! closed-loop layer can be driven from the shell, which is what the
//! equivalence comparison between two builds needs.

//! ```text
//! cargo run --offline --release -p closed-loop-manager --example materialize_closed_loops -- <store> <network> <chain_id> <apply|dry-run>
//! ```

use closed_loop_manager::materialize_closed_loops;

fn main() {
    let arguments = std::env::args().collect::<Vec<_>>();
    if arguments.len() < 5 {
        eprintln!("usage: materialize_closed_loops <store> <network> <chain_id> <apply|dry-run>");
        std::process::exit(2);
    }
    let store = &arguments[1];
    let network = &arguments[2];
    let chain_id = arguments[3].parse::<i64>().expect("chain id must be a number");
    let apply = arguments[4] == "apply";
    let result = materialize_closed_loops(store, network, chain_id, apply)
        .expect("the closed loop materialization must succeed");
    println!("{}", serde_json::to_string(&result).expect("the result is serializable"));
}
