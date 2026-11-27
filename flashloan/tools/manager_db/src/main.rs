use manager_db::{migrate, preflight_read_only, restamp_ledger_checksums};
use std::env;
use std::path::PathBuf;

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut arguments = env::args().skip(1);
    let command = arguments.next().ok_or_else(usage)?;
    let (database, dry_run) = parse_database_argument(arguments)?;
    let output = match command.as_str() {
        "preflight" => serde_json::to_value(preflight_read_only(&database)?)?,
        "migrate" => serde_json::to_value(migrate(&database)?)?,
        "restamp" => serde_json::to_value(restamp_ledger_checksums(&database, dry_run)?)?,
        _ => return Err(usage().into()),
    };
    println!("{}", serde_json::to_string_pretty(&output)?);
    Ok(())
}

fn parse_database_argument(
    mut arguments: impl Iterator<Item = String>,
) -> Result<(PathBuf, bool), Box<dyn std::error::Error>> {
    let mut path: Option<String> = None;
    let mut dry_run = false;
    while let Some(argument) = arguments.next() {
        match argument.as_str() {
            "--db" => path = arguments.next(),
            "--dry-run" => dry_run = true,
            _ => return Err(usage().into()),
        }
    }
    match path {
        Some(path) => Ok((PathBuf::from(path), dry_run)),
        _ => Err(usage().into()),
    }
}

fn usage() -> String {
    "usage: manager-db <preflight|migrate|restamp> --db <sqlite-path> [--dry-run]".to_owned()
}
