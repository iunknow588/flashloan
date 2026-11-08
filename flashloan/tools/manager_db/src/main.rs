use manager_db::{migrate, preflight_read_only};
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
    let database = parse_database_argument(arguments)?;
    let output = match command.as_str() {
        "preflight" => serde_json::to_value(preflight_read_only(&database)?)?,
        "migrate" => serde_json::to_value(migrate(&database)?)?,
        _ => return Err(usage().into()),
    };
    println!("{}", serde_json::to_string_pretty(&output)?);
    Ok(())
}

fn parse_database_argument(
    mut arguments: impl Iterator<Item = String>,
) -> Result<PathBuf, Box<dyn std::error::Error>> {
    match (
        arguments.next().as_deref(),
        arguments.next(),
        arguments.next(),
    ) {
        (Some("--db"), Some(path), None) => Ok(PathBuf::from(path)),
        _ => Err(usage().into()),
    }
}

fn usage() -> String {
    "usage: manager-db <preflight|migrate> --db <sqlite-path>".to_owned()
}
