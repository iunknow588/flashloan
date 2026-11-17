use candidate_edge_pool_manager::materialize_candidate_edges;
use candidate_exchange_pool_manager::materialize_candidate_exchanges;
use closed_loop_manager::materialize_closed_loops;
use rusqlite::{params, Connection, OpenFlags};
use serde_json::json;
use std::env;
use std::path::PathBuf;
use std::process::ExitCode;

fn main() -> ExitCode {
    let arguments = env::args().skip(1).collect::<Vec<_>>();
    if arguments.first().map(String::as_str) != Some("materialize-arbitrage") {
        eprintln!(
            "usage: composite-manager materialize-arbitrage --db PATH --chain-id <4663|46630> [--apply]"
        );
        return ExitCode::from(2);
    }
    match materialize_arbitrage(&arguments[1..]) {
        Ok(report) => {
            println!("{report}");
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("{error}");
            ExitCode::FAILURE
        }
    }
}

fn materialize_arbitrage(arguments: &[String]) -> Result<String, Box<dyn std::error::Error>> {
    let database = flag(arguments, "--db")
        .map(PathBuf::from)
        .ok_or("materialize-arbitrage requires --db <manager sqlite path>")?;
    let chain_id = flag(arguments, "--chain-id")
        .ok_or("materialize-arbitrage requires --chain-id <4663|46630>")?
        .parse::<i64>()?;
    let network = network_for_chain_id(chain_id)
        .ok_or_else(|| format!("unsupported chain-id: {chain_id}"))?
        .to_owned();
    let apply = arguments.iter().any(|argument| argument == "--apply");
    let canonical_database = database.canonicalize()?;
    let preflight = Connection::open_with_flags(
        &canonical_database,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI,
    )?;
    let pool_scope_count: i64 = preflight.query_row(
        "SELECT COUNT(*) FROM pool_scope_config
         WHERE network = ?1 AND chain_id = ?2 AND scope_key = 'core4' AND enabled = 1",
        params![network, chain_id],
        |row| row.get(0),
    )?;
    if pool_scope_count != 1 {
        return Err(format!(
            "core4 pool scope preflight failed for {}: expected 1 enabled row, found {pool_scope_count}",
            canonical_database.display()
        )
        .into());
    }

    let candidate_edges =
        materialize_candidate_edges(&canonical_database, &network, chain_id, apply)?;
    let candidate_exchanges =
        materialize_candidate_exchanges(&canonical_database, &network, chain_id, apply)?;
    let closed_loops = materialize_closed_loops(&canonical_database, &network, chain_id, apply)?;

    Ok(serde_json::to_string_pretty(&json!({
        "mode": if apply { "apply" } else { "dry_run" },
        "network": network,
        "chainId": chain_id,
        "database": canonical_database.display().to_string(),
        "poolScopePreflightCount": pool_scope_count,
        "candidateEdges": candidate_edges,
        "candidateExchanges": candidate_exchanges,
        "closedLoops": closed_loops,
        "applied": apply
    }))?)
}

/// Maps a chain id to the network scope that owns its data.
///
/// The composite manager does not own the network table: it reads and
/// writes the same `(network, chain_id)` scope as every other manager, so
/// the network has to be derived from the chain id instead of being fixed
/// to mainnet. A chain id that this deployment does not know is rejected
/// rather than silently mapped onto a `chain-<id>` scope, because such a
/// scope has no producer and every write to it would be unreachable.
fn network_for_chain_id(chain_id: i64) -> Option<&'static str> {
    match chain_id {
        4663 => Some("robinhood"),
        46630 => Some("robinhood-testnet"),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::{flag, materialize_arbitrage, network_for_chain_id};

    fn arguments(items: &[&str]) -> Vec<String> {
        items.iter().map(|item| (*item).to_owned()).collect()
    }

    #[test]
    fn network_is_derived_from_the_chain_id() {
        assert_eq!(network_for_chain_id(4663), Some("robinhood"));
        assert_eq!(network_for_chain_id(46630), Some("robinhood-testnet"));
    }

    #[test]
    fn an_unknown_chain_id_is_rejected_instead_of_guessed() {
        assert_eq!(network_for_chain_id(1), None);
        assert_eq!(network_for_chain_id(0), None);
        assert_eq!(network_for_chain_id(46631), None);
    }

    #[test]
    fn the_testnet_chain_id_is_not_rejected_as_an_unsupported_chain() {
        let error = materialize_arbitrage(&arguments(&[
            "materialize-arbitrage",
            "--db",
            "missing.sqlite3",
            "--chain-id",
            "46630",
        ]))
        .expect_err("the testnet chain id must pass the chain gate");
        let message = error.to_string();
        assert!(
            !message.contains("unsupported chain-id"),
            "the testnet chain id was rejected by the chain gate: {message}"
        );
    }

    #[test]
    fn an_unknown_chain_id_is_rejected_by_the_command() {
        let error = materialize_arbitrage(&arguments(&[
            "materialize-arbitrage",
            "--db",
            "missing.sqlite3",
            "--chain-id",
            "1",
        ]))
        .expect_err("an unknown chain id must be rejected");
        assert!(
            error.to_string().contains("unsupported chain-id: 1"),
            "unexpected error: {error}"
        );
    }

    #[test]
    fn the_chain_id_flag_is_required() {
        let error = materialize_arbitrage(&arguments(&[
            "materialize-arbitrage",
            "--db",
            "missing.sqlite3",
        ]))
        .expect_err("the chain id flag is required");
        assert!(
            error.to_string().contains("--chain-id"),
            "unexpected error: {error}"
        );
    }

    #[test]
    fn flag_reads_the_value_after_the_name() {
        let arguments = arguments(&["--db", "store.sqlite3", "--chain-id", "46630"]);
        assert_eq!(flag(&arguments, "--db"), Some("store.sqlite3"));
        assert_eq!(flag(&arguments, "--chain-id"), Some("46630"));
        assert_eq!(flag(&arguments, "--apply"), None);
    }
}

fn flag<'a>(arguments: &'a [String], name: &str) -> Option<&'a str> {
    arguments
        .windows(2)
        .find(|pair| pair[0] == name)
        .map(|pair| pair[1].as_str())
}
