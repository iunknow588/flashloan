use rusqlite::{params, Connection};
use serde_json::{json, Value};
use std::path::Path;
use std::time::Duration;

const BNB_TICKER_SYMBOL: &str = "BNBUSDT";
const BINANCE_REST_BASE: &str = "https://data-api.binance.vision";

fn now_iso() -> String {
    chrono::Utc::now().to_rfc3339()
}

pub(crate) fn ensure_bnb_market_tables(connection: &Connection) -> Result<(), String> {
    connection
        .execute_batch(
            "CREATE TABLE IF NOT EXISTS bnb_market_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                network TEXT NOT NULL DEFAULT 'bnb',
                chain_id INTEGER NOT NULL DEFAULT 56,
                run_id TEXT,
                round_index INTEGER,
                recorded_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bnb_market_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL DEFAULT 'binance_rest',
                symbol TEXT NOT NULL,
                price_usd TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );",
        )
        .map_err(|error| format!("SQLite BNB market schema batch failed: {error}"))
}

fn ensure_parent_directory(database_path: &str) -> Result<(), String> {
    if let Some(parent) = Path::new(database_path).parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent).map_err(|error| {
                format!(
                    "could not create SQLite directory {}: {error}",
                    parent.display()
                )
            })?;
        }
    }
    Ok(())
}

fn open_rw(database_path: &str) -> Result<Connection, String> {
    ensure_parent_directory(database_path)?;
    let connection = crate::schema::open_rw(database_path)?;
    ensure_bnb_market_tables(&connection)?;
    Ok(connection)
}

pub(crate) fn append_bnb_market_observation(
    database_path: &str,
    source: &str,
    network: &str,
    chain_id: i64,
    payload: &Value,
) -> Result<Value, String> {
    let payload_text = serde_json::to_string(payload)
        .map_err(|error| format!("payload JSON encoding failed: {error}"))?;
    let run_id = payload
        .get("runId")
        .and_then(Value::as_str)
        .map(str::to_string);
    let round_index = payload.get("roundIndex").and_then(Value::as_i64);
    let recorded_at = payload
        .get("generatedAt")
        .and_then(Value::as_str)
        .map(str::to_string)
        .or_else(|| {
            payload
                .get("recordedAt")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .unwrap_or_else(now_iso);
    let connection = open_rw(database_path)?;
    connection
        .execute(
            "INSERT INTO bnb_market_observations
                (source, network, chain_id, run_id, round_index, recorded_at, payload_json)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                source,
                network,
                chain_id,
                run_id,
                round_index,
                recorded_at,
                payload_text
            ],
        )
        .map_err(|error| format!("BNB market observation insert failed: {error}"))?;
    Ok(json!({
        "ok": true,
        "id": connection.last_insert_rowid(),
        "source": source,
        "recordedAt": recorded_at,
    }))
}

pub(crate) fn load_bnb_market_observations(
    database_path: &str,
    source: Option<&str>,
    limit: i64,
) -> Result<Value, String> {
    if !Path::new(database_path).exists() {
        return Ok(json!({"ok": true, "rows": []}));
    }
    let connection = crate::schema::open_rw(database_path)?;
    let table_exists: i64 = connection
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'bnb_market_observations'",
            [],
            |row| row.get(0),
        )
        .map_err(|error| format!("BNB observation table lookup failed: {error}"))?;
    if table_exists == 0 {
        return Ok(json!({"ok": true, "rows": []}));
    }
    let safe_limit = limit.max(1).min(10_000);
    let mut statement = if source.is_some() {
        connection
            .prepare(
                "SELECT id, source, network, chain_id, run_id, round_index, recorded_at, payload_json
                 FROM bnb_market_observations
                 WHERE source = ?1
                 ORDER BY id DESC
                 LIMIT ?2",
            )
            .map_err(|error| format!("BNB observation query prepare failed: {error}"))?
    } else {
        connection
            .prepare(
                "SELECT id, source, network, chain_id, run_id, round_index, recorded_at, payload_json
                 FROM bnb_market_observations
                 ORDER BY id DESC
                 LIMIT ?1",
            )
            .map_err(|error| format!("BNB observation query prepare failed: {error}"))?
    };
    let rows = if let Some(source) = source {
        statement
            .query_map(params![source, safe_limit], |row| {
                let payload_text: String = row.get(7)?;
                let payload: Value = serde_json::from_str(&payload_text).unwrap_or(Value::Null);
                Ok(json!({
                    "id": row.get::<_, i64>(0)?,
                    "source": row.get::<_, String>(1)?,
                    "network": row.get::<_, String>(2)?,
                    "chain_id": row.get::<_, i64>(3)?,
                    "run_id": row.get::<_, Option<String>>(4)?,
                    "round_index": row.get::<_, Option<i64>>(5)?,
                    "recorded_at": row.get::<_, String>(6)?,
                    "payload": payload,
                }))
            })
            .map_err(|error| format!("BNB observation query failed: {error}"))?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("BNB observation row decode failed: {error}"))?
    } else {
        statement
            .query_map(params![safe_limit], |row| {
                let payload_text: String = row.get(7)?;
                let payload: Value = serde_json::from_str(&payload_text).unwrap_or(Value::Null);
                Ok(json!({
                    "id": row.get::<_, i64>(0)?,
                    "source": row.get::<_, String>(1)?,
                    "network": row.get::<_, String>(2)?,
                    "chain_id": row.get::<_, i64>(3)?,
                    "run_id": row.get::<_, Option<String>>(4)?,
                    "round_index": row.get::<_, Option<i64>>(5)?,
                    "recorded_at": row.get::<_, String>(6)?,
                    "payload": payload,
                }))
            })
            .map_err(|error| format!("BNB observation query failed: {error}"))?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("BNB observation row decode failed: {error}"))?
    };
    Ok(json!({"ok": true, "rows": rows}))
}

pub(crate) fn append_bnb_market_snapshot(
    database_path: &str,
    symbol: &str,
    price_usd: &str,
    observed_at: &str,
    payload: &Value,
) -> Result<Value, String> {
    let payload_text = serde_json::to_string(payload)
        .map_err(|error| format!("payload JSON encoding failed: {error}"))?;
    let connection = open_rw(database_path)?;
    connection
        .execute(
            "INSERT INTO bnb_market_snapshots
                (source, symbol, price_usd, observed_at, payload_json)
             VALUES ('binance_rest', ?1, ?2, ?3, ?4)",
            params![symbol, price_usd, observed_at, payload_text],
        )
        .map_err(|error| format!("BNB market snapshot insert failed: {error}"))?;
    Ok(json!({
        "ok": true,
        "id": connection.last_insert_rowid(),
        "symbol": symbol,
        "priceUsd": price_usd,
        "observedAt": observed_at,
    }))
}

pub(crate) fn load_bnb_market_snapshots(
    database_path: &str,
    symbol: Option<&str>,
    limit: i64,
) -> Result<Value, String> {
    if !Path::new(database_path).exists() {
        return Ok(json!({"ok": true, "rows": []}));
    }
    let connection = crate::schema::open_rw(database_path)?;
    let table_exists: i64 = connection
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'bnb_market_snapshots'",
            [],
            |row| row.get(0),
        )
        .map_err(|error| format!("BNB snapshot table lookup failed: {error}"))?;
    if table_exists == 0 {
        return Ok(json!({"ok": true, "rows": []}));
    }
    let safe_limit = limit.max(1).min(10_000);
    let mut statement = if symbol.is_some() {
        connection
            .prepare(
                "SELECT id, source, symbol, price_usd, observed_at, payload_json
                 FROM bnb_market_snapshots
                 WHERE symbol = ?1
                 ORDER BY id DESC
                 LIMIT ?2",
            )
            .map_err(|error| format!("BNB snapshot query prepare failed: {error}"))?
    } else {
        connection
            .prepare(
                "SELECT id, source, symbol, price_usd, observed_at, payload_json
                 FROM bnb_market_snapshots
                 ORDER BY id DESC
                 LIMIT ?1",
            )
            .map_err(|error| format!("BNB snapshot query prepare failed: {error}"))?
    };
    let rows = if let Some(symbol) = symbol {
        statement
            .query_map(params![symbol, safe_limit], |row| {
                let payload_text: String = row.get(5)?;
                let payload: Value = serde_json::from_str(&payload_text).unwrap_or(Value::Null);
                Ok(json!({
                    "id": row.get::<_, i64>(0)?,
                    "source": row.get::<_, String>(1)?,
                    "symbol": row.get::<_, String>(2)?,
                    "price_usd": row.get::<_, String>(3)?,
                    "observed_at": row.get::<_, String>(4)?,
                    "payload": payload,
                }))
            })
            .map_err(|error| format!("BNB snapshot query failed: {error}"))?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("BNB snapshot row decode failed: {error}"))?
    } else {
        statement
            .query_map(params![safe_limit], |row| {
                let payload_text: String = row.get(5)?;
                let payload: Value = serde_json::from_str(&payload_text).unwrap_or(Value::Null);
                Ok(json!({
                    "id": row.get::<_, i64>(0)?,
                    "source": row.get::<_, String>(1)?,
                    "symbol": row.get::<_, String>(2)?,
                    "price_usd": row.get::<_, String>(3)?,
                    "observed_at": row.get::<_, String>(4)?,
                    "payload": payload,
                }))
            })
            .map_err(|error| format!("BNB snapshot query failed: {error}"))?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("BNB snapshot row decode failed: {error}"))?
    };
    Ok(json!({"ok": true, "rows": rows}))
}

pub(crate) fn fetch_bnb_usdt_price(timeout_seconds: u64) -> Result<Value, String> {
    let runtime = tokio::runtime::Runtime::new()
        .map_err(|error| format!("tokio runtime failed for Binance REST: {error}"))?;
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(timeout_seconds.max(1)))
        .build()
        .map_err(|error| format!("Binance REST HTTP client failed: {error}"))?;
    runtime.block_on(async move {
        let url = format!("{BINANCE_REST_BASE}/api/v3/ticker/price?symbol={BNB_TICKER_SYMBOL}");
        let response = client
            .get(&url)
            .send()
            .await
            .map_err(|error| format!("Binance REST request failed: {error}"))?;
        let payload: Value = response
            .json()
            .await
            .map_err(|error| format!("Binance REST response was not JSON: {error}"))?;
        let symbol = payload
            .get("symbol")
            .and_then(Value::as_str)
            .unwrap_or(BNB_TICKER_SYMBOL)
            .to_string();
        let price = payload
            .get("price")
            .and_then(Value::as_str)
            .ok_or_else(|| "Binance REST ticker has no price".to_string())?
            .to_string();
        Ok(json!({
            "ok": true,
            "symbol": symbol,
            "price": price,
            "observedAt": now_iso(),
            "raw": payload,
        }))
    })
}

pub(crate) fn collect_bnb_market_snapshot(database_path: &str) -> Result<Value, String> {
    let fetched = fetch_bnb_usdt_price(10)?;
    let symbol = fetched
        .get("symbol")
        .and_then(Value::as_str)
        .unwrap_or(BNB_TICKER_SYMBOL)
        .to_string();
    let price = fetched
        .get("price")
        .and_then(Value::as_str)
        .ok_or_else(|| "BNB ticker price is missing".to_string())?
        .to_string();
    let observed_at = fetched
        .get("observedAt")
        .and_then(Value::as_str)
        .map(str::to_string)
        .unwrap_or_else(now_iso);
    let inserted =
        append_bnb_market_snapshot(database_path, &symbol, &price, &observed_at, &fetched)?;
    Ok(json!({
        "ok": true,
        "symbol": symbol,
        "priceUsd": price,
        "observedAt": observed_at,
        "stored": inserted,
        // NOTE: This BNB market data is currently collected for future compatibility only.
        // No strategy/execution pipeline consumes it yet. A later fix will decide the exact
        // consumption semantics, schema columns, and cadence.
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bnb_observation_round_trip_is_stored_by_rust() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("bnb.sqlite3");
        let path = path.to_str().unwrap();
        let payload = json!({
            "runId": "run-1",
            "roundIndex": 3,
            "generatedAt": "2026-10-26T00:00:00Z",
            "routeCount": 2,
        });
        let inserted =
            append_bnb_market_observation(path, "unit_test", "bnb", 56, &payload).unwrap();
        assert_eq!(inserted["ok"], true);
        let loaded = load_bnb_market_observations(path, Some("unit_test"), 10).unwrap();
        assert_eq!(loaded["rows"].as_array().unwrap().len(), 1);
        let row = &loaded["rows"][0];
        assert_eq!(row["source"], json!("unit_test"));
        assert_eq!(row["payload"]["roundIndex"], json!(3));
    }

    #[test]
    fn bnb_snapshot_round_trip_is_stored_by_rust() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("bnb.sqlite3");
        let path = path.to_str().unwrap();
        let payload = json!({"ok": true, "symbol": "BNBUSDT", "price": "600.0"});
        let inserted =
            append_bnb_market_snapshot(path, "BNBUSDT", "600.0", "2026-10-26T00:00:00Z", &payload)
                .unwrap();
        assert_eq!(inserted["ok"], true);
        let loaded = load_bnb_market_snapshots(path, Some("BNBUSDT"), 10).unwrap();
        assert_eq!(loaded["rows"].as_array().unwrap().len(), 1);
        assert_eq!(loaded["rows"][0]["price_usd"], json!("600.0"));
    }
}
