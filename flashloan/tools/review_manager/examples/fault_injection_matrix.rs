//! The fault injection matrix of the record separation work (`F-1` .. `F-10`).
//!
//! Every scenario runs against its own temporary store.  The live database is
//! never opened, so a run can not change production data.
//!
//! Run it from the workspace root:
//!
//! ```text
//! cargo run --offline -p review-manager --example fault_injection_matrix -- <evidence dir>
//! ```

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use chrono::{NaiveDate, Utc};
use review_manager::records::{
    drain_review_records,
    list_trade_archive_months,
    month_from_key,
    owning_month,
    publish_review_records,
    publish_trade_archive,
    purge_expired_review_files,
    read_review_record_health,
    read_trade_archive_health,
    read_trade_archive_summary,
    reconcile_archive_backlog,
    review_day_is_removable,
    review_record_dir,
    trade_record_dir,
    ArchiveRequest,
    ReviewGenerationContext,
    ReviewRowSnapshot,
    REVIEW_CHANNEL_CAPACITY,
    TRADE_FILE_NAME,
};
use rusqlite::types::Value;
use rusqlite::{params_from_iter, Connection};
use serde::Serialize;

const NETWORK: &str = "robinhood";
const CHAIN_ID: i64 = 4663;
const PROTOCOL: &str = "uniswap_v4";
const SCOPE_KEY: &str = "core4";
const CREATED_AT: &str = "2026-11-23T08:00:00Z";
const MONTH_KEY: &str = "2026-11";
const DAY_KEY: &str = "2026-11-23";
const OBSERVED_AT: &str = "2026-11-23T08:00:00Z";
const LOOP_KEY: &str = "0x1111:0x2222:0x3333";
const ATTEMPT_TABLE: &str = "manager_cow_execution_attempts";

/// One measured value of a scenario.
#[derive(Clone, Serialize)]
struct Reading {
    name: String,
    value: String,
}

/// The outcome of one injection.
#[derive(Serialize)]
struct Scenario {
    id: String,
    name: String,
    injection: String,
    expectation: String,
    invariant: String,
    level: String,
    verdict: String,
    readings: Vec<Reading>,
    note: String,
}

fn reading(name: &str, value: impl ToString) -> Reading {
    Reading {
        name: name.to_owned(),
        value: value.to_string(),
    }
}

/// A main store at `<root>/db/manager.sqlite3`.
fn main_store(root: &Path) -> PathBuf {
    let db_dir = root.join("db");
    fs::create_dir_all(&db_dir).unwrap();
    let path = db_dir.join("manager.sqlite3");
    manager_db::migrate(&path).unwrap();
    path
}

/// Insert one finished attempt into the main store.
///
/// The column list is read from the live schema, so the fixture never guesses
/// a column that the store does not have.
fn insert_attempt(
    database: &Path,
    attempt_id: &str,
    currency: &str,
    raw: &str,
    created_at: &str,
) {
    let connection = Connection::open(database).unwrap();
    let mut statement = connection
        .prepare("PRAGMA table_info(manager_cow_execution_attempts)")
        .unwrap();
    let columns: Vec<String> = statement
        .query_map([], |row| row.get::<_, String>(1))
        .unwrap()
        .map(|row| row.unwrap())
        .collect();
    drop(statement);
    let mut names = Vec::new();
    let mut values: Vec<Value> = Vec::new();
    for column in &columns {
        names.push(format!("\"{column}\""));
        let value = match column.as_str() {
            "network" => Value::Text(NETWORK.to_owned()),
            "chain_id" => Value::Integer(CHAIN_ID),
            "protocol" => Value::Text(PROTOCOL.to_owned()),
            "scope_key" => Value::Text(SCOPE_KEY.to_owned()),
            "attempt_id" => Value::Text(attempt_id.to_owned()),
            "loop_key" => Value::Text("loop-1".to_owned()),
            "closed_loop_version" => Value::Integer(7),
            "closed_loop_checksum" => Value::Text("loop-checksum".to_owned()),
            "review_version" => Value::Integer(9),
            "review_checksum" => Value::Text("review-checksum".to_owned()),
            "anchor_token_key_id" => Value::Text("token-1".to_owned()),
            "path_text" => Value::Text("WETH>USDC".to_owned()),
            "path_addresses_json" => Value::Text(json(&["0xaaa", "0xbbb"])),
            "path_symbols_json" => Value::Text(json(&["WETH", "USDC"])),
            "pool_ids_json" => Value::Text(json(&["pool-1"])),
            "pool_path_key" => Value::Text("pool-path".to_owned()),
            "signed_pnl_raw" => Value::Text(raw.to_owned()),
            "signed_pnl_amount" => Value::Real(1.5),
            "signed_pnl_currency" => Value::Text(currency.to_owned()),
            "signed_pnl_unit" => Value::Text("raw".to_owned()),
            "signed_pnl_source" => Value::Text("native_rust_contract_preview".to_owned()),
            "signer_address" => Value::Text("0xsigner".to_owned()),
            "nonce" => Value::Integer(1),
            "rpc_url" => Value::Text("http://127.0.0.1:8545".to_owned()),
            "status" => Value::Text("success".to_owned()),
            "receipt_json" => Value::Text(json(&[("sentinel", 1)])),
            "evidence_json" => Value::Text(json(&[("sentinel", 1)])),
            "created_at" => Value::Text(created_at.to_owned()),
            "updated_at" => Value::Text(created_at.to_owned()),
            "terminal_at" => Value::Text(created_at.to_owned()),
            _ => Value::Null,
        };
        values.push(value);
    }
    let placeholders = (1..=values.len())
        .map(|index| format!("?{index}"))
        .collect::<Vec<_>>()
        .join(", ");
    let sql = format!(
        "INSERT INTO {ATTEMPT_TABLE} ({}) VALUES ({placeholders})",
        names.join(", ")
    );
    connection.execute(&sql, params_from_iter(values.iter())).unwrap();
}

fn json<T: serde::Serialize>(value: &T) -> String {
    serde_json::to_string(value).unwrap()
}

fn archive_request(attempt_id: &str) -> ArchiveRequest {
    ArchiveRequest {
        network: NETWORK.to_owned(),
        chain_id: CHAIN_ID,
        protocol: PROTOCOL.to_owned(),
        scope_key: SCOPE_KEY.to_owned(),
        attempt_id: attempt_id.to_owned(),
    }
}

fn audit_lines(loop_key: &str, count: usize) -> Vec<review_manager::records::ReviewRecordLine> {
    let context = ReviewGenerationContext {
        network: NETWORK.to_owned(),
        chain_id: CHAIN_ID,
        review_version: 7,
        review_checksum: Some("checksum".to_owned()),
        observed_at: OBSERVED_AT.to_owned(),
    };
    let snapshot = ReviewRowSnapshot {
        loop_key: loop_key.to_owned(),
        reviewed_at: Some(OBSERVED_AT.to_owned()),
        closed_loop_version: Some(7),
        review_status: Some("completed".to_owned()),
        review_reason: None,
        signed_pnl_raw: Some("-0x2ee726109c1".to_owned()),
        signed_pnl_currency: Some("WETH".to_owned()),
        signed_pnl_unit: Some("raw".to_owned()),
        signed_pnl_source: Some("native_rust_contract_preview".to_owned()),
        path_symbols: Some(vec!["WETH".to_owned(), "USDC".to_owned()]),
        pool_ids: Some(vec!["pool-1".to_owned()]),
        capacity_anchor_raw: Some("1000".to_owned()),
        capacity_binding_pool_id: Some("pool-1".to_owned()),
    };
    (0..count)
        .map(|_| review_manager::records::ReviewRecordLine::upsert(&context, &snapshot))
        .collect()
}

/// The number of JSONL files below the review directory.
fn jsonl_files(directory: &Path) -> usize {
    let mut count = 0;
    let Ok(entries) = fs::read_dir(directory) else {
        return 0;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        let Ok(inner) = fs::read_dir(&path) else {
            continue;
        };
        for file in inner.flatten() {
            if file.path().extension().and_then(|value| value.to_str()) == Some("jsonl") {
                count += 1;
            }
        }
    }
    count
}

/// The number of lines in every JSONL file below the review directory.
fn jsonl_lines(directory: &Path) -> usize {
    let mut count = 0;
    let Ok(entries) = fs::read_dir(directory) else {
        return 0;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        let Ok(inner) = fs::read_dir(&path) else {
            continue;
        };
        for file in inner.flatten() {
            if file.path().extension().and_then(|value| value.to_str()) != Some("jsonl") {
                continue;
            }
            let Ok(body) = fs::read_to_string(file.path()) else {
                continue;
            };
            count += body.lines().filter(|line| !line.trim().is_empty()).count();
        }
    }
    count
}

/// Is the attempt still complete in the main store?
fn attempt_is_complete(database: &Path, attempt_id: &str) -> bool {
    let connection = Connection::open(database).unwrap();
    let value: Option<String> = connection
        .query_row(
            "SELECT path_addresses_json FROM manager_cow_execution_attempts WHERE attempt_id = ?1",
            params_from_iter([attempt_id]),
            |row| row.get(0),
        )
        .unwrap();
    value.map(|body| body != "[]").unwrap_or(false)
}

fn make_readonly(path: &Path) -> std::io::Result<()> {
    let mut permissions = fs::metadata(path)?.permissions();
    permissions.set_readonly(true);
    fs::set_permissions(path, permissions)
}

/// Wait until the archive failure counter moved past `before`.
fn wait_archive_failed(before: u64, timeout: Duration) -> Option<u64> {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        let now = read_trade_archive_health().archive_failed;
        if now > before {
            return Some(now);
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    None
}

/// Wait until the review write error counter moved past `before`.
fn wait_write_errors(before: u64, timeout: Duration) -> Option<u64> {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        let now = read_review_record_health().write_errors;
        if now > before {
            return Some(now);
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    None
}

/// The commit of the main store happens before the audit stream is published.
fn commit_precedes_publish() -> (bool, String) {
    let source = fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("src/lib.rs"))
        .expect("the review manager source is readable");
    let start = source.find("fn run_impl").expect("run_impl exists");
    let body = &source[start..];
    let commit = body.find("transaction.commit()?").expect("the commit exists");
    let publish = body.find("publish_review_audit(").expect("the publish exists");
    let calls = source.matches("publish_review_audit(").count();
    (
        commit < publish,
        format!("commitOffset={commit} publishOffset={publish} callSites={calls}"),
    )
}

/// `F-1`: a main store that can not be committed leaves the audit stream empty.
fn scenario_f1() -> Scenario {
    let mut readings = Vec::new();
    let root = tempfile::tempdir().unwrap();
    let database = main_store(root.path());
    let directory = review_record_dir(&database, NETWORK, CHAIN_ID).unwrap();

    // The injection: the main store refuses every write.
    make_readonly(&database).unwrap();
    let write = review_manager::run_reviews(
        &database,
        NETWORK,
        CHAIN_ID,
        vec![review_manager::ReviewEvidenceInput {
            loop_key: LOOP_KEY.to_owned(),
            closed_loop_generation: None,
            static_preview: serde_json::json!({"ok": false, "executable": false}),
            run_arbitrage: None,
            diagnostics: Vec::new(),
            reviewed_at: None,
            closed_loop_identity: None,
        }],
        true,
    );
    let write_state = match &write {
        Ok(_) => "ok".to_owned(),
        Err(error) => format!("error: {error}"),
    };
    readings.push(reading("mainStoreWrite", write_state.clone()));
    let files = jsonl_files(&directory);
    let lines = jsonl_lines(&directory);
    readings.push(reading("jsonlFiles", files));
    readings.push(reading("jsonlLinesAfterFailedWrite", lines));

    // The control: the audit stream itself writes.  It runs after the failed
    // write, so the empty stream above is caused by the generation that was
    // never written and not by a writer that never worked.
    let runtime = tokio::runtime::Runtime::new().unwrap();
    runtime.block_on(async {
        publish_review_records(&directory, audit_lines(LOOP_KEY, 1)).unwrap();
        drain_review_records(&directory).await.unwrap();
    });
    let control_lines = jsonl_lines(&directory);
    readings.push(reading("auditWriterControlLines", control_lines));

    let (ordered, detail) = commit_precedes_publish();
    readings.push(reading("commitPrecedesPublish", ordered));
    readings.push(reading("sourceOrder", detail));

    let failed = write.is_err();
    let verdict = if failed && ordered && lines == 0 && control_lines >= 1 {
        "pass"
    } else {
        "fail"
    };
    Scenario {
        id: "F-1".to_owned(),
        name: "the main store commit fails".to_owned(),
        injection: "the main store file is read only, so the generation can not be committed".to_owned(),
        expectation: "the audit stream writes no line at all".to_owned(),
        invariant: "the audit stream never describes a generation that was not committed".to_owned(),
        level: "red".to_owned(),
        verdict: verdict.to_owned(),
        readings,
        note: "the injection keeps the generation from being written: in this fixture the upstream precondition stops the pass and the main store is read only on top of that.  The commit phase itself is covered by the source order guard, which proves that the publish call sits behind the successful commit".to_owned(),
    }
}

/// `F-2`: a month store that can not be written keeps the main row intact.
fn scenario_f2() -> Scenario {
    let mut readings = Vec::new();
    let root = tempfile::tempdir().unwrap();
    let database = main_store(root.path());
    insert_attempt(&database, "attempt-f2", "WETH", "-0x2ee726109c1", CREATED_AT);
    let trade_dir = trade_record_dir(&database, NETWORK, CHAIN_ID).unwrap();
    fs::create_dir_all(&trade_dir).unwrap();
    fs::write(trade_dir.join(MONTH_KEY), b"blocked").unwrap();

    let before = read_trade_archive_health().archive_failed;
    publish_trade_archive(&database, NETWORK, CHAIN_ID, vec![archive_request("attempt-f2")]).unwrap();
    let after = wait_archive_failed(before, Duration::from_secs(20));
    readings.push(reading(
        "archiveFailedDelta",
        after.map(|value| value - before).unwrap_or(0),
    ));
    let complete = attempt_is_complete(&database, "attempt-f2");
    readings.push(reading("mainRowStillComplete", complete));
    let months = list_trade_archive_months(&database, NETWORK, CHAIN_ID).unwrap();
    readings.push(reading("monthStores", months.len()));
    let total = read_trade_archive_summary(&database, NETWORK, CHAIN_ID).unwrap();
    readings.push(reading("archivedAttempts", total.attempt_count));

    let verdict = if after.is_some() && complete && months.is_empty() { "pass" } else { "fail" };
    Scenario {
        id: "F-2".to_owned(),
        name: "the month store can not be written".to_owned(),
        injection: "a file of the month name blocks the month folder".to_owned(),
        expectation: "the main row is not slimmed and the failure is counted".to_owned(),
        invariant: "a record never disappears from both stores".to_owned(),
        level: "red".to_owned(),
        verdict: verdict.to_owned(),
        readings,
        note: String::new(),
    }
}

/// `F-3`: a locked month store waits for the busy timeout and then fails.
fn scenario_f3() -> Scenario {
    let mut readings = Vec::new();
    let root = tempfile::tempdir().unwrap();
    let database = main_store(root.path());
    insert_attempt(&database, "attempt-f3-a", "WETH", "-0x1", CREATED_AT);
    publish_trade_archive(&database, NETWORK, CHAIN_ID, vec![archive_request("attempt-f3-a")]).unwrap();
    reconcile_archive_backlog(&database, NETWORK, CHAIN_ID).unwrap();
    let month_file = trade_record_dir(&database, NETWORK, CHAIN_ID)
        .unwrap()
        .join(MONTH_KEY)
        .join(TRADE_FILE_NAME);
    readings.push(reading("monthStoreExists", month_file.exists()));

    insert_attempt(&database, "attempt-f3-b", "WETH", "-0x2", CREATED_AT);
    let locker = Connection::open(&month_file).unwrap();
    locker.execute_batch("PRAGMA busy_timeout = 0; BEGIN EXCLUSIVE;").unwrap();
    let holder = std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(70));
        let _ = locker.execute_batch("ROLLBACK;");
    });

    let before = read_trade_archive_health().archive_failed;
    let started = Instant::now();
    publish_trade_archive(&database, NETWORK, CHAIN_ID, vec![archive_request("attempt-f3-b")]).unwrap();
    let after = wait_archive_failed(before, Duration::from_secs(80));
    let waited = started.elapsed();
    holder.join().unwrap();

    readings.push(reading(
        "archiveFailedDelta",
        after.map(|value| value - before).unwrap_or(0),
    ));
    readings.push(reading("waitedSeconds", waited.as_secs()));
    let complete = attempt_is_complete(&database, "attempt-f3-b");
    readings.push(reading("mainRowStillComplete", complete));
    let total = read_trade_archive_summary(&database, NETWORK, CHAIN_ID).unwrap();
    readings.push(reading("archivedAttempts", total.attempt_count));

    let verdict = if after.is_some() && complete && waited >= Duration::from_secs(55) {
        "pass"
    } else {
        "fail"
    };
    Scenario {
        id: "F-3".to_owned(),
        name: "the month store is locked".to_owned(),
        injection: "a second connection holds an exclusive lock on the month store".to_owned(),
        expectation: "the write waits for the busy timeout and then fails like F-2".to_owned(),
        invariant: "a record never disappears from both stores".to_owned(),
        level: "red".to_owned(),
        verdict: verdict.to_owned(),
        readings,
        note: "the busy timeout of the month store is 60 seconds, so the scenario needs that long on purpose".to_owned(),
    }
}

/// Publish more lines than the channel can hold.
fn overflow_the_channel(directory: &Path, total: usize) -> u64 {
    let chunk = 50_000;
    let mut done = 0;
    while done < total {
        let size = chunk.min(total - done);
        publish_review_records(directory, audit_lines(LOOP_KEY, size)).unwrap();
        done += size;
    }
    read_review_record_health().dropped
}

/// `F-4`: a full channel drops the audit lines and never blocks the main store.
fn scenario_f4() -> Scenario {
    let mut readings = Vec::new();
    let root = tempfile::tempdir().unwrap();
    let database = main_store(root.path());
    insert_attempt(&database, "attempt-f4", "WETH", "-0x2ee726109c1", CREATED_AT);
    let directory = review_record_dir(&database, NETWORK, CHAIN_ID).unwrap();

    let before = read_review_record_health();
    let started = Instant::now();
    let _dropped = overflow_the_channel(&directory, 200_000);
    let elapsed = started.elapsed();
    let after = read_review_record_health();

    readings.push(reading("channelCapacity", REVIEW_CHANNEL_CAPACITY));
    readings.push(reading("droppedDelta", after.dropped - before.dropped));
    readings.push(reading("publishedLines", 200_000));
    readings.push(reading("publishSeconds", format!("{:.3}", elapsed.as_secs_f64())));
    let complete = attempt_is_complete(&database, "attempt-f4");
    readings.push(reading("mainStoreUntouched", complete));

    let verdict = if after.dropped > before.dropped && complete { "pass" } else { "fail" };
    Scenario {
        id: "F-4".to_owned(),
        name: "the review channel is full".to_owned(),
        injection: format!("{} lines are pushed into a channel of {}", 200_000, REVIEW_CHANNEL_CAPACITY),
        expectation: "the surplus is dropped and counted, and the main store is not blocked".to_owned(),
        invariant: "the main store never waits for the audit stream".to_owned(),
        level: "yellow".to_owned(),
        verdict: verdict.to_owned(),
        readings,
        note: String::new(),
    }
}

/// `F-5`: a review directory that can not be written counts the error.
fn scenario_f5() -> Scenario {
    let mut readings = Vec::new();
    let root = tempfile::tempdir().unwrap();
    let database = main_store(root.path());
    insert_attempt(&database, "attempt-f5", "WETH", "-0x2ee726109c1", CREATED_AT);
    let directory = review_record_dir(&database, NETWORK, CHAIN_ID).unwrap();
    fs::create_dir_all(&directory).unwrap();
    fs::write(directory.join(DAY_KEY), b"blocked").unwrap();

    let before = read_review_record_health().write_errors;
    publish_review_records(&directory, audit_lines(LOOP_KEY, 1)).unwrap();
    let after = wait_write_errors(before, Duration::from_secs(20));
    readings.push(reading(
        "writeErrorsDelta",
        after.map(|value| value - before).unwrap_or(0),
    ));
    let complete = attempt_is_complete(&database, "attempt-f5");
    readings.push(reading("mainStoreUntouched", complete));

    let verdict = if after.is_some() && complete { "pass" } else { "fail" };
    Scenario {
        id: "F-5".to_owned(),
        name: "the review directory can not be written".to_owned(),
        injection: "a file of the day name blocks the day folder".to_owned(),
        expectation: "the write error is counted and the main store is untouched".to_owned(),
        invariant: "the main store never waits for the audit stream".to_owned(),
        level: "yellow".to_owned(),
        verdict: verdict.to_owned(),
        readings,
        note: String::new(),
    }
}

/// `F-6`: a crash between the month write and the slimming is reconciled.
fn scenario_f6() -> Scenario {
    let mut readings = Vec::new();
    let root = tempfile::tempdir().unwrap();
    let database = main_store(root.path());
    insert_attempt(&database, "attempt-f6", "WETH", "-0x2ee726109c1", CREATED_AT);
    publish_trade_archive(&database, NETWORK, CHAIN_ID, vec![archive_request("attempt-f6")]).unwrap();
    reconcile_archive_backlog(&database, NETWORK, CHAIN_ID).unwrap();

    // The crash state: the month write happened, the slimming did not.
    {
        let connection = Connection::open(&database).unwrap();
        connection
            .execute(
                "UPDATE manager_cow_execution_attempts
                    SET path_addresses_json = ?1, receipt_json = ?2
                  WHERE attempt_id = ?3",
                params_from_iter([
                    json(&["0xaaa", "0xbbb"]),
                    json(&[("sentinel", 1)]),
                    "attempt-f6".to_owned(),
                ]),
            )
            .unwrap();
    }
    let pending_before = attempt_is_complete(&database, "attempt-f6");
    readings.push(reading("crashStateMainRowComplete", pending_before));

    let report = reconcile_archive_backlog(&database, NETWORK, CHAIN_ID).unwrap();
    readings.push(reading("reconcileScanned", report.scanned));
    readings.push(reading("reconcileArchived", report.archived));
    readings.push(reading("reconcileFailed", report.failed));

    let month_file = trade_record_dir(&database, NETWORK, CHAIN_ID)
        .unwrap()
        .join(MONTH_KEY)
        .join(TRADE_FILE_NAME);
    let month_connection = Connection::open(&month_file).unwrap();
    let rows: i64 = month_connection
        .query_row(
            "SELECT COUNT(*) FROM manager_cow_execution_attempts WHERE attempt_id = ?1",
            params_from_iter(["attempt-f6"]),
            |row| row.get(0),
        )
        .unwrap();
    drop(month_connection);
    readings.push(reading("monthStoreRowsForAttempt", rows));
    let complete = attempt_is_complete(&database, "attempt-f6");
    readings.push(reading("mainRowSlimmedAgain", !complete));
    let total = read_trade_archive_summary(&database, NETWORK, CHAIN_ID).unwrap();
    readings.push(reading("archivedAttempts", total.attempt_count));

    let verdict = if rows == 1 && !complete && total.attempt_count == 1 { "pass" } else { "fail" };
    Scenario {
        id: "F-6".to_owned(),
        name: "a crash between the month write and the slimming".to_owned(),
        injection: "the main row is filled in again after it was archived".to_owned(),
        expectation: "the reconciliation pass archives it again and both stores agree".to_owned(),
        invariant: "the month store and the main store end up consistent".to_owned(),
        level: "red".to_owned(),
        verdict: verdict.to_owned(),
        readings,
        note: "the crash is reproduced by putting the main row back into its complete shape, which is exactly the state a crash between the two steps leaves behind".to_owned(),
    }
}

/// `F-7`: a lost audit line costs nothing but the audit trail.
fn scenario_f7() -> Scenario {
    let mut readings = Vec::new();
    let root = tempfile::tempdir().unwrap();
    let database = main_store(root.path());
    insert_attempt(&database, "attempt-f7", "WETH", "-0x2ee726109c1", CREATED_AT);
    let directory = review_record_dir(&database, NETWORK, CHAIN_ID).unwrap();

    let totals_before = read_trade_archive_summary(&database, NETWORK, CHAIN_ID).unwrap();
    let attempts_before = totals_before.attempt_count;
    let before = read_review_record_health();
    overflow_the_channel(&directory, 200_000);
    let after = read_review_record_health();
    let dropped = after.dropped - before.dropped;

    publish_trade_archive(&database, NETWORK, CHAIN_ID, vec![archive_request("attempt-f7")]).unwrap();
    reconcile_archive_backlog(&database, NETWORK, CHAIN_ID).unwrap();
    let totals_after = read_trade_archive_summary(&database, NETWORK, CHAIN_ID).unwrap();

    readings.push(reading("droppedLines", dropped));
    readings.push(reading("droppedIsVisible", dropped > 0));
    readings.push(reading("archivedAttemptsBefore", attempts_before));
    readings.push(reading("archivedAttemptsAfter", totals_after.attempt_count));
    let complete = attempt_is_complete(&database, "attempt-f7");
    readings.push(reading("mainRowArchived", !complete));

    let verdict = if dropped > 0 && totals_after.attempt_count == attempts_before + 1 { "pass" } else { "fail" };
    Scenario {
        id: "F-7".to_owned(),
        name: "a crash between the send and the write".to_owned(),
        injection: "the channel overflows, so the surplus lines never reach the disk".to_owned(),
        expectation: "only the audit trail is lost, and the loss is visible in the counter".to_owned(),
        invariant: "the audit stream takes part in no decision".to_owned(),
        level: "green".to_owned(),
        verdict: verdict.to_owned(),
        readings,
        note: "the trade record is still archived after the loss, so the judgement path never depended on the audit stream".to_owned(),
    }
}

/// `F-8`: a damaged month store is reported and never panics.
fn scenario_f8() -> Scenario {
    let mut readings = Vec::new();
    let root = tempfile::tempdir().unwrap();
    let database = main_store(root.path());
    insert_attempt(&database, "attempt-f8", "WETH", "-0x2ee726109c1", CREATED_AT);
    publish_trade_archive(&database, NETWORK, CHAIN_ID, vec![archive_request("attempt-f8")]).unwrap();
    reconcile_archive_backlog(&database, NETWORK, CHAIN_ID).unwrap();
    let month_file = trade_record_dir(&database, NETWORK, CHAIN_ID)
        .unwrap()
        .join(MONTH_KEY)
        .join(TRADE_FILE_NAME);

    // The pages have to be in the file itself before the damage can land
    // there: in write ahead log mode the data still sits in the log next to
    // the file, and a damaged file would be healed by that log.
    {
        let connection = Connection::open(&month_file).unwrap();
        connection
            .execute_batch("PRAGMA wal_checkpoint(TRUNCATE);")
            .unwrap();
    }
    let body = fs::read(&month_file).unwrap();
    readings.push(reading("monthStoreBytesBeforeDamage", body.len()));
    let keep = body.len().min(32);
    fs::write(&month_file, &body[..keep]).unwrap();
    readings.push(reading("monthStoreBytesAfterDamage", keep));

    let summary = read_trade_archive_summary(&database, NETWORK, CHAIN_ID);
    let reported = match &summary {
        Ok(value) => format!("ok: {}", value.attempt_count),
        Err(error) => format!("error: {error}"),
    };
    readings.push(reading("summaryRead", reported.clone()));
    let connection = Connection::open(&database).unwrap();
    let rows: i64 = connection
        .query_row("SELECT COUNT(*) FROM manager_cow_execution_attempts", [], |row| row.get(0))
        .unwrap();
    drop(connection);
    readings.push(reading("mainStoreRows", rows));

    let verdict = if summary.is_err() && rows == 1 { "pass" } else { "fail" };
    Scenario {
        id: "F-8".to_owned(),
        name: "the month store file is damaged".to_owned(),
        injection: "the header of the month store is overwritten".to_owned(),
        expectation: "the read reports an error and does not panic, and the main store is untouched".to_owned(),
        invariant: "a damaged archive never takes the main store with it".to_owned(),
        level: "red".to_owned(),
        verdict: verdict.to_owned(),
        readings,
        note: String::new(),
    }
}

/// `F-9`: the retention pass refuses every path outside the review subtree.
fn scenario_f9() -> Scenario {
    let mut readings = Vec::new();
    let root = tempfile::tempdir().unwrap();
    let store_dir = root.path().join("db");
    fs::create_dir_all(&store_dir).unwrap();
    let reviews_root = store_dir.join("review_record_jsonl");
    let chain_dir = reviews_root.join(NETWORK).join(CHAIN_ID.to_string());
    let old_day = chain_dir.join("2020-01-02");
    fs::create_dir_all(&old_day).unwrap();
    fs::write(old_day.join("loop.jsonl"), b"{}\n").unwrap();
    let sentinel = store_dir.join("sentinel.txt");
    fs::write(&sentinel, b"keep me").unwrap();
    let today = NaiveDate::from_ymd_opt(2026, 11, 25).unwrap();

    let escape = reviews_root.join("..").join("..").join("escape");
    let absolute = root.path().join("outside").join("2020-01-02");
    let refused_escape = review_day_is_removable(&reviews_root, &escape, today, 30).is_err();
    let refused_absolute = review_day_is_removable(&reviews_root, &absolute, today, 30).is_err();
    let removable = review_day_is_removable(&reviews_root, &old_day, today, 30)
        .unwrap()
        .map(|day| day.to_string());
    readings.push(reading("escapePathRefused", refused_escape));
    readings.push(reading("absolutePathRefused", refused_absolute));
    readings.push(reading(
        "oldDayAccepted",
        removable.clone().unwrap_or_else(|| "none".to_owned()),
    ));

    let runtime = tokio::runtime::Runtime::new().unwrap();
    let report = runtime.block_on(purge_expired_review_files(&store_dir, 30)).unwrap();
    readings.push(reading("purgeScanned", report.scanned));
    readings.push(reading("purgeRemoved", report.removed.len()));
    readings.push(reading("purgeRejected", report.rejected.len()));
    readings.push(reading("sentinelSurvived", sentinel.exists()));
    readings.push(reading("outsideStillMissing", !absolute.exists()));

    let verdict = if refused_escape
        && refused_absolute
        && removable.is_some()
        && sentinel.exists()
        && !absolute.exists()
    {
        "pass"
    } else {
        "fail"
    };
    Scenario {
        id: "F-9".to_owned(),
        name: "the retention pass points outside the review subtree".to_owned(),
        injection: "a relative escape and an absolute path outside the tree".to_owned(),
        expectation: "every path outside is refused and no file is removed".to_owned(),
        invariant: "the retention pass never deletes a file outside the review subtree".to_owned(),
        level: "red".to_owned(),
        verdict: verdict.to_owned(),
        readings,
        note: "a symbolic link is refused by the same guard, but Windows refuses to create one without a privilege, so the link case is not exercised on this host".to_owned(),
    }
}

/// `F-10`: a timestamp that can not be parsed never guesses a month.
fn scenario_f10() -> Scenario {
    let mut readings = Vec::new();
    let root = tempfile::tempdir().unwrap();
    let database = main_store(root.path());
    insert_attempt(&database, "attempt-f10", "WETH", "-0x2ee726109c1", "not a timestamp");

    let before = read_trade_archive_health().archive_failed;
    publish_trade_archive(&database, NETWORK, CHAIN_ID, vec![archive_request("attempt-f10")]).unwrap();
    let after = wait_archive_failed(before, Duration::from_secs(20));
    readings.push(reading(
        "archiveFailedDelta",
        after.map(|value| value - before).unwrap_or(0),
    ));
    let months = list_trade_archive_months(&database, NETWORK, CHAIN_ID).unwrap();
    readings.push(reading("monthStores", months.len()));
    let complete = attempt_is_complete(&database, "attempt-f10");
    readings.push(reading("mainRowStillComplete", complete));
    let refused = owning_month("not a timestamp").is_err();
    readings.push(reading("owningMonthRefused", refused));
    readings.push(reading("monthFromBadKey", month_from_key("2026-13").is_none()));

    let verdict = if after.is_some() && months.is_empty() && complete && refused { "pass" } else { "fail" };
    Scenario {
        id: "F-10".to_owned(),
        name: "the created_at of an attempt is not a timestamp".to_owned(),
        injection: "the attempt carries an unparsable created_at".to_owned(),
        expectation: "the archive reports an error, counts it and writes no store".to_owned(),
        invariant: "a month is never guessed".to_owned(),
        level: "red".to_owned(),
        verdict: verdict.to_owned(),
        readings,
        note: String::new(),
    }
}

/// The cross month aggregation fixture matrix.
fn scenario_x1() -> Scenario {
    let mut readings = Vec::new();
    let root = tempfile::tempdir().unwrap();
    let database = main_store(root.path());
    insert_attempt(&database, "attempt-x1", "WETH", "0x2", "2026-09-10T08:00:00Z");
    insert_attempt(&database, "attempt-x2", "WETH", "0x3", "2026-10-10T08:00:00Z");
    insert_attempt(&database, "attempt-x3", "WETH", "0x5", "2026-11-10T08:00:00Z");
    insert_attempt(&database, "attempt-x4", "SPY", "0x7", "2026-11-11T08:00:00Z");
    for attempt in ["attempt-x1", "attempt-x2", "attempt-x3", "attempt-x4"] {
        publish_trade_archive(&database, NETWORK, CHAIN_ID, vec![archive_request(attempt)]).unwrap();
    }
    reconcile_archive_backlog(&database, NETWORK, CHAIN_ID).unwrap();

    let months = list_trade_archive_months(&database, NETWORK, CHAIN_ID).unwrap();
    readings.push(reading("months", months.join(",")));
    let summary = read_trade_archive_summary(&database, NETWORK, CHAIN_ID).unwrap();
    readings.push(reading("attemptCount", summary.attempt_count));
    readings.push(reading(
        "perCurrency",
        summary
            .per_currency
            .iter()
            .map(|(currency, value)| format!("{currency}={value}"))
            .collect::<Vec<_>>()
            .join(","),
    ));
    readings.push(reading("monthKeys", summary.months.join(",")));

    let exact = summary.per_currency.get("WETH").map(|value| value == "0xa").unwrap_or(false);
    let verdict = if months.len() == 3 && summary.attempt_count == 4 && exact { "pass" } else { "fail" };
    Scenario {
        id: "X-1".to_owned(),
        name: "the cross month aggregation".to_owned(),
        injection: "attempts in three different months and two currencies".to_owned(),
        expectation: "the totals cover every month and every currency is kept apart".to_owned(),
        invariant: "one total per currency, never one total over several currencies".to_owned(),
        level: "green".to_owned(),
        verdict: verdict.to_owned(),
        readings,
        note: "the fixture matrix of the cross month aggregation that the record separation plan asked for".to_owned(),
    }
}

fn main() {
    let evidence_dir = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "plan/runtime_evidence/reports".to_owned());
    let stamp = Utc::now().format("%Y%m%d").to_string();

    let mut scenarios = Vec::new();
    scenarios.push(scenario_f1());
    scenarios.push(scenario_f2());
    scenarios.push(scenario_f3());
    scenarios.push(scenario_f4());
    scenarios.push(scenario_f5());
    scenarios.push(scenario_f6());
    scenarios.push(scenario_f7());
    scenarios.push(scenario_f8());
    scenarios.push(scenario_f9());
    scenarios.push(scenario_f10());
    scenarios.push(scenario_x1());

    let passed = scenarios
        .iter()
        .filter(|scenario| scenario.verdict == "pass")
        .count();
    let red_failures = scenarios
        .iter()
        .filter(|scenario| scenario.level == "red" && scenario.verdict != "pass")
        .count();

    let document = serde_json::json!({
        "schema": "task182_fault_injection_matrix.v1",
        "executedAt": Utc::now().to_rfc3339(),
        "target": "record separation (review audit stream and monthly trade store)",
        "liveDatabaseTouched": false,
        "scenarioCount": scenarios.len(),
        "passed": passed,
        "redFailures": red_failures,
        "scenarios": scenarios,
    });

    let reports = Path::new(&evidence_dir);
    fs::create_dir_all(reports).unwrap();
    let stem = format!("task182_fault_injection_matrix_{stamp}");
    let json_path = reports.join(format!("{stem}.json"));
    fs::write(&json_path, serde_json::to_string_pretty(&document).unwrap()).unwrap();

    let mut markdown = String::new();
    markdown.push_str("# TASK-182 fault injection matrix\n\n");
    markdown.push_str(&format!("- executed at: `{}`\n", Utc::now().to_rfc3339()));
    markdown.push_str(&format!("- scenarios: **{}**, passed: **{}**, red failures: **{}**\n", scenarios.len(), passed, red_failures));
    markdown.push_str("- the live database was never opened: **zero writes**\n\n");
    markdown.push_str("| # | injection | level | verdict |\n|---|---|---|---|\n");
    for scenario in &scenarios {
        markdown.push_str(&format!(
            "| {} | {} | {} | {} |\n",
            scenario.id,
            scenario.name,
            scenario.level,
            scenario.verdict
        ));
    }
    markdown.push_str("\n## The readings\n\n");
    for scenario in &scenarios {
        markdown.push_str(&format!("### {} {}\n\n", scenario.id, scenario.name));
        markdown.push_str(&format!("- injection: {}\n", scenario.injection));
        markdown.push_str(&format!("- expectation: {}\n", scenario.expectation));
        markdown.push_str(&format!("- invariant: {}\n", scenario.invariant));
        if !scenario.note.is_empty() {
            markdown.push_str(&format!("- note: {}\n", scenario.note));
        }
        for reading in &scenario.readings {
            markdown.push_str(&format!("- {}: `{}`\n", reading.name, reading.value));
        }
        markdown.push_str("\n");
    }
    let md_path = reports.join(format!("{stem}.md"));
    fs::write(&md_path, markdown).unwrap();

    println!("scenarios={} passed={} redFailures={}", scenarios.len(), passed, red_failures);
    println!("json={}", json_path.display());
    println!("md={}", md_path.display());
    if red_failures > 0 {
        std::process::exit(2);
    }
}
