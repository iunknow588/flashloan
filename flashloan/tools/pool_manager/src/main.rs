use pool_manager::{
    add_pair, apply_cleanup, audit, materialize_scope, read_scope_status, refresh_key, refresh_pair,
};
use serde_json::json;
use std::env;

fn env_value(name: &str) -> Option<String> {
    env::var(name).ok().filter(|value| !value.trim().is_empty())
}

fn next_arg(args: &[String], index: &mut usize, label: &str) -> Result<String, String> {
    if *index + 1 >= args.len() {
        return Err(format!("missing {label}"));
    }
    *index += 1;
    Ok(args[*index].clone())
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let store = env_value("POOL_MANAGER_STORE")
        .unwrap_or_else(|| "flashloan/db/local_store_4663.sqlite3".to_string());
    if args.len() < 2 {
        eprintln!(
            "usage: pool-manager <audit|cleanup|add-pair|refresh-pair|materialize-scope|scope-status> \
             --store PATH --chain-id N [options]"
        );
        std::process::exit(2);
    }
    let mut index = 1;
    let command = &args[index];
    index += 1;
    let mut chain_id = 4663i64;
    let mut apply = false;
    let mut base = String::new();
    let mut quote = String::new();
    let mut key_id = String::new();
    let mut pool_group = "PAIR".to_string();
    let mut limit = 5usize;
    let mut minimal = false;
    let mut scope = "core4".to_string();
    let mut dry_run = false;
    let mut apply_flag = false;
    let mut store = store;
    while index < args.len() {
        match args[index].as_str() {
            "--store" => {
                store =
                    next_arg(&args, &mut index, "--store").unwrap_or_else(|error| panic!("{error}"))
            }
            "--chain-id" => {
                let value = next_arg(&args, &mut index, "--chain-id")
                    .unwrap_or_else(|error| panic!("{error}"));
                chain_id = value
                    .parse()
                    .unwrap_or_else(|_| panic!("invalid chain-id: {value}"));
            }
            "--apply" => {
                apply = true;
                apply_flag = true;
            }
            "--dry-run" => dry_run = true,
            "--base" => {
                base =
                    next_arg(&args, &mut index, "--base").unwrap_or_else(|error| panic!("{error}"))
            }
            "--quote" => {
                quote =
                    next_arg(&args, &mut index, "--quote").unwrap_or_else(|error| panic!("{error}"))
            }
            "--key-id" => {
                key_id = next_arg(&args, &mut index, "--key-id")
                    .unwrap_or_else(|error| panic!("{error}"))
            }
            "--minimal" => minimal = true,
            "--pool-group" | "--anchor-symbol" => {
                pool_group = next_arg(&args, &mut index, "--pool-group")
                    .unwrap_or_else(|error| panic!("{error}"));
            }
            "--limit" => {
                let value = next_arg(&args, &mut index, "--limit")
                    .unwrap_or_else(|error| panic!("{error}"));
                limit = value
                    .parse()
                    .unwrap_or_else(|_| panic!("invalid limit: {value}"));
            }
            "--scope" => {
                scope =
                    next_arg(&args, &mut index, "--scope").unwrap_or_else(|error| panic!("{error}"))
            }
            other => panic!("unknown argument: {other}"),
        }
        index += 1;
    }
    if dry_run && apply_flag {
        panic!("--dry-run and --apply are mutually exclusive");
    }
    if dry_run {
        apply = false;
    }

    let network = match chain_id {
        4663 => "robinhood",
        46630 => "robinhood-testnet",
        other => {
            eprintln!("unsupported chain-id: {other}");
            std::process::exit(2);
        }
    };

    let result = match command.as_str() {
        "audit" => audit(&store, chain_id),
        "cleanup" => {
            if !apply {
                audit(&store, chain_id)
            } else {
                apply_cleanup(&store, chain_id)
            }
        }
        "add-pair" => {
            if base.is_empty() || quote.is_empty() {
                Err("add-pair requires --base and --quote".to_string())
            } else {
                add_pair(&store, chain_id, &base, &quote, &pool_group, limit, apply)
            }
        }
        "refresh-pair" | "update-pair" => {
            if base.is_empty() || quote.is_empty() {
                Err("refresh-pair requires --base and --quote".to_string())
            } else {
                refresh_pair(&store, chain_id, &base, &quote, &pool_group, limit, apply)
            }
        }
        "refresh-key" => {
            if key_id.is_empty() {
                Err("refresh-key requires --key-id <tokenX:tokenY>".to_string())
            } else {
                if minimal {
                    refresh_key(&store, chain_id, &key_id, &pool_group, limit, apply)
                        .map(|_| json!({"refreshKey": true}))
                } else {
                    refresh_key(&store, chain_id, &key_id, &pool_group, limit, apply)
                }
            }
        }
        "materialize-scope" | "rebuild-scope" => {
            materialize_scope(&store, network, chain_id, &scope, apply)
                .map(|value| json!(value))
                .map_err(|error| error.to_string())
        }
        "scope-status" | "scope" => read_scope_status(&store, network, chain_id, &scope)
            .map(|value| json!(value))
            .map_err(|error| error.to_string()),
        other => Err(format!("unknown command: {other}")),
    };

    match result {
        Ok(value) => {
            println!("{value}");
        }
        Err(error) => {
            eprintln!("pool-manager error: {error}");
            std::process::exit(1);
        }
    }
}
