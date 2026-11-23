//! The record layer: the review audit stream and the monthly trade store.
//!
//! The main store keeps the **current** review generation, the state rows and
//! the idempotency ledger.  Everything that outlives one generation is moved
//! out of it:
//!
//! * the review audit stream becomes **daily JSONL** under
//!   `runtime_records/reviews/<network>/<chain_id>/`;
//! * the finished arbitrage attempts become **monthly SQLite** under
//!   `runtime_records/trades/<network>/<chain_id>/`.
//!
//! Three rules hold everywhere in this module:
//!
//! 1. **The JSONL stream never decides anything.**  It is append-only, it may
//!    lose a line, and no gate may read it.  The main store stays the only
//!    authority for the current generation.
//! 2. **A month store is written before the main store row is slimmed.**  If
//!    the month write fails the main row is left exactly as it was, so a
//!    record can never disappear from both places.
//! 3. **The signed profit is kept verbatim.**  `signed_pnl_raw` is a *signed
//!    hexadecimal* string (`-0x2ee726109c1`), never a JSON number and never a
//!    decimal re-interpretation.
//!
//! Every path and every name in here is **UTC**.  Local time only ever shows
//! up in the presentation layer, because a machine in another time zone would
//! otherwise expire a different set of files.
//!
//! The whole layer lives here on purpose.  `broadcast_manager` already
//! depends on `review-manager`, so it calls into this module instead of
//! carrying a second copy — which also keeps `Cargo.lock` byte-identical
//! (guard `G-4`): no new dependency edge is introduced anywhere.

use std::collections::BTreeMap;
use std::fmt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

use chrono::{DateTime, Datelike, NaiveDate, Utc};
use rusqlite::{types::Value, Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// The subtree that holds the review audit stream, next to the main store.
pub const REVIEW_DIR_NAME: &str = "review_record_jsonl";
/// The subtree that holds the monthly trade stores, next to the main store.
pub const TRADE_DIR_NAME: &str = "broadcast_transaction_db";
/// The subtree that holds the maintenance ledgers.  Never cleaned up.
pub const MAINTENANCE_DIR_NAME: &str = "record_maintenance";
/// The schema tag written into every JSONL line.
pub const JSONL_SCHEMA: &str = "review_record.v1";
/// The capacity of the review audit channel.
pub const REVIEW_CHANNEL_CAPACITY: usize = 10_000;
/// The capacity of the trade archive channel.
pub const TRADE_CHANNEL_CAPACITY: usize = 1_000;
/// How long a daily review file is kept, in days.
pub const REVIEW_RETENTION_DAYS: u32 = 30;
/// How long a monthly trade store is kept.  Zero means *forever*.
pub const TRADE_ARCHIVE_MONTHS: u32 = 0;
/// The name of the SQLite file inside a monthly trade folder.
pub const TRADE_FILE_NAME: &str = "trades.sqlite3";
/// The table that holds a finished broadcast attempt in the main store.
pub const ATTEMPT_TABLE: &str = "manager_cow_execution_attempts";
/// The table that holds a broadcast diagnostic in the main store.
pub const DIAGNOSTIC_TABLE: &str = "manager_broadcast_attempt_diagnostics";
/// The tables a monthly trade store carries.  Cloned, never hand written.
pub const TRADE_ARCHIVE_TABLES: [&str; 2] = [ATTEMPT_TABLE, DIAGNOSTIC_TABLE];

/// Everything that can go wrong in the record layer.
#[derive(Debug)]
pub enum RecordError {
    /// A path component was rejected before it could escape the record root.
    InvalidPath { field: &'static str, value: String },
    /// A timestamp could not be parsed, so its month is unknown.
    InvalidTimestamp { value: String, reason: String },
    /// The filesystem refused the operation.
    Io { path: PathBuf, source: std::io::Error },
    /// SQLite refused the operation.
    Sqlite(rusqlite::Error),
    /// The JSON payload could not be built or read.
    Serde(serde_json::Error),
    /// A stored profit could not be parsed.
    InvalidRaw { value: String, reason: String },
    /// The operation is not allowed in the current state.
    InvalidState(String),
}

impl fmt::Display for RecordError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidPath { field, value } => {
                write!(formatter, "invalid {field}: {value:?}")
            }
            Self::InvalidTimestamp { value, reason } => {
                write!(formatter, "invalid timestamp {value:?}: {reason}")
            }
            Self::Io { path, source } => {
                write!(formatter, "io error at {}: {source}", path.display())
            }
            Self::Sqlite(source) => write!(formatter, "sqlite error: {source}"),
            Self::Serde(source) => write!(formatter, "json error: {source}"),
            Self::InvalidRaw { value, reason } => {
                write!(formatter, "invalid signed raw {value:?}: {reason}")
            }
            Self::InvalidState(message) => write!(formatter, "invalid state: {message}"),
        }
    }
}

impl std::error::Error for RecordError {}

impl From<rusqlite::Error> for RecordError {
    fn from(source: rusqlite::Error) -> Self {
        Self::Sqlite(source)
    }
}

impl From<serde_json::Error> for RecordError {
    fn from(source: serde_json::Error) -> Self {
        Self::Serde(source)
    }
}

/// The directory that holds the main store.
///
/// Pure: it never touches the disk, so a caller can derive the directory
/// before any store is opened and a test can assert the result without a
/// fixture.
///
/// The record trees are siblings of the main store inside the very same
/// directory, so a deployment keeps its whole history in one place:
///
/// ```text
/// db/
///   local_store_4663.sqlite3      the main store
///   review_record_jsonl/          the review audit stream
///   broadcast_transaction_db/     the monthly trade stores
///   record_maintenance/           the cleanup ledgers
/// ```
///
/// A store that has no directory of its own degrades to the working
/// directory.  The derivation never fails, which is why this function returns
/// a path rather than a result.
pub fn store_directory_for(database_path: &Path) -> PathBuf {
    database_path
        .parent()
        .map(Path::new)
        .filter(|parent| !parent.as_os_str().is_empty())
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
}

/// The directory that holds the review audit stream of one chain.
pub fn review_record_dir(
    database_path: &Path,
    network: &str,
    chain_id: i64,
) -> Result<PathBuf, RecordError> {
    Ok(store_directory_for(database_path)
        .join(REVIEW_DIR_NAME)
        .join(validated_scope_component(network, chain_id)?))
}

/// The directory that holds the monthly trade stores of one chain.
pub fn trade_record_dir(
    database_path: &Path,
    network: &str,
    chain_id: i64,
) -> Result<PathBuf, RecordError> {
    Ok(store_directory_for(database_path)
        .join(TRADE_DIR_NAME)
        .join(validated_scope_component(network, chain_id)?))
}

/// The directory that holds the maintenance ledgers.
///
/// The ledgers are audit evidence, so this directory is never cleaned up.
pub fn maintenance_record_dir(database_path: &Path) -> PathBuf {
    store_directory_for(database_path).join(MAINTENANCE_DIR_NAME)
}

/// Validate the `network` / `chain_id` pair that becomes a directory name.
///
/// The pair is derived from the request, so it has to be checked before it
/// becomes a path: a `network` of `..` or a separator would otherwise let a
/// caller write outside the record root.
fn validated_scope_component(network: &str, chain_id: i64) -> Result<String, RecordError> {
    if network.is_empty() || network.trim() != network {
        return Err(RecordError::InvalidPath {
            field: "network",
            value: network.to_owned(),
        });
    }
    if network == "." || network == ".." {
        return Err(RecordError::InvalidPath {
            field: "network",
            value: network.to_owned(),
        });
    }
    if network.chars().any(|character| {
        character.is_control()
            || matches!(
                character,
                '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|'
            )
    }) {
        return Err(RecordError::InvalidPath {
            field: "network",
            value: network.to_owned(),
        });
    }
    if chain_id <= 0 {
        return Err(RecordError::InvalidPath {
            field: "chain_id",
            value: chain_id.to_string(),
        });
    }
    Ok(format!("{network}/{chain_id}"))
}

/// Create a directory and every missing parent.  Idempotent.
pub async fn ensure_dir(path: &Path) -> Result<(), RecordError> {
    tokio::fs::create_dir_all(path).await.map_err(|source| RecordError::Io {
        path: path.to_path_buf(),
        source,
    })
}

/// What happened to one review between two generations.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ReviewRecordEvent {
    /// The review is new or one of its compared fields changed.
    Upsert,
    /// The review was not part of the new generation any more.
    Removed,
}

fn schema_default() -> &'static str {
    JSONL_SCHEMA
}

/// One line of the review audit stream.
///
/// The line is a *delta*, never a snapshot of the whole generation.  A field
/// that has no value is left out instead of being written as `null`, and the
/// signed profit is carried as the verbatim string the producer wrote.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewRecordLine {
    /// The schema tag.  Always `review_record.v1`.
    #[serde(default = "schema_default", skip_deserializing)]
    pub schema: &'static str,
    /// Whether the review was written or removed.
    pub event: ReviewRecordEvent,
    /// When the change was observed, in UTC RFC3339.
    pub observed_at: String,
    /// The chain the review belongs to.
    pub network: String,
    /// The chain id the review belongs to.
    pub chain_id: i64,
    /// The review generation the change belongs to.
    pub review_version: i64,
    /// The closed loop generation the review was measured under.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub closed_loop_version: Option<i64>,
    /// The checksum of the review generation.  Together with the version it
    /// identifies the generation offline.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub review_checksum: Option<String>,
    /// The review identity.
    pub loop_key: String,
    /// The verdict of the review.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub review_status: Option<String>,
    /// Why the review reached that verdict.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub review_reason: Option<String>,
    /// The signed profit, **verbatim**.  Signed hexadecimal, for example
    /// `-0x2ee726109c1`.  Never a JSON number, never re-interpreted.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub signed_pnl_raw: Option<String>,
    /// The currency of the signed profit.  The grouping key of every
    /// aggregate: two currencies are never added together.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub signed_pnl_currency: Option<String>,
    /// The unit of the signed profit.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub signed_pnl_unit: Option<String>,
    /// Where the signed profit came from.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub signed_pnl_source: Option<String>,
    /// The symbols of the route, in route order.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path_symbols: Option<Vec<String>>,
    /// The pools of the route, in route order.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pool_ids: Option<Vec<String>>,
    /// The folded single-tick capacity, as a decimal string.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub capacity_anchor_raw: Option<String>,
    /// The hop whose folded capacity is the smallest.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub capacity_binding_pool_id: Option<String>,
}

/// The identity of the generation a batch of audit lines belongs to.
#[derive(Debug, Clone)]
pub struct ReviewGenerationContext {
    /// The chain the generation belongs to.
    pub network: String,
    /// The chain id the generation belongs to.
    pub chain_id: i64,
    /// The review generation.
    pub review_version: i64,
    /// The checksum of the review generation.
    pub review_checksum: Option<String>,
    /// When the generation was observed, in UTC RFC3339.
    pub observed_at: String,
}

/// The part of a review row that the audit stream compares.
///
/// Only the fields that carry meaning for the audit are kept, so a change in
/// a derived column cannot turn into an audit line on its own.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReviewRowSnapshot {
    /// The review identity.
    pub loop_key: String,
    /// The closed loop generation the review was measured under.
    pub closed_loop_version: Option<i64>,
    /// The verdict.
    pub review_status: Option<String>,
    /// Why the review reached that verdict.
    pub review_reason: Option<String>,
    /// The signed profit, verbatim.
    pub signed_pnl_raw: Option<String>,
    /// The currency of the signed profit.
    pub signed_pnl_currency: Option<String>,
    /// The unit of the signed profit.
    pub signed_pnl_unit: Option<String>,
    /// Where the signed profit came from.
    pub signed_pnl_source: Option<String>,
    /// The symbols of the route.
    pub path_symbols: Option<Vec<String>>,
    /// The pools of the route.
    pub pool_ids: Option<Vec<String>>,
    /// The folded single-tick capacity.
    pub capacity_anchor_raw: Option<String>,
    /// The hop whose folded capacity is the smallest.
    pub capacity_binding_pool_id: Option<String>,
}

impl ReviewRowSnapshot {
    /// Whether two snapshots describe the same audit state.
    ///
    /// The closed loop version is part of the comparison on purpose: a new
    /// closed loop generation changes the version of every review even when
    /// no measured field moved, and an audit stream that stayed silent then
    /// would describe a generation change that never happened.
    fn same_audit_state(&self, other: &Self) -> bool {
        self.closed_loop_version == other.closed_loop_version
            && self.review_status == other.review_status
            && self.review_reason == other.review_reason
            && self.signed_pnl_raw == other.signed_pnl_raw
            && self.signed_pnl_currency == other.signed_pnl_currency
            && self.signed_pnl_unit == other.signed_pnl_unit
            && self.signed_pnl_source == other.signed_pnl_source
            && self.path_symbols == other.path_symbols
            && self.pool_ids == other.pool_ids
            && self.capacity_anchor_raw == other.capacity_anchor_raw
            && self.capacity_binding_pool_id == other.capacity_binding_pool_id
    }
}

impl ReviewRecordLine {
    /// Build the audit line of a review that is part of the new generation.
    pub fn upsert(context: &ReviewGenerationContext, snapshot: &ReviewRowSnapshot) -> Self {
        Self {
            schema: JSONL_SCHEMA,
            event: ReviewRecordEvent::Upsert,
            observed_at: context.observed_at.clone(),
            network: context.network.clone(),
            chain_id: context.chain_id,
            review_version: context.review_version,
            closed_loop_version: snapshot.closed_loop_version,
            review_checksum: context.review_checksum.clone(),
            loop_key: snapshot.loop_key.clone(),
            review_status: snapshot.review_status.clone(),
            review_reason: snapshot.review_reason.clone(),
            signed_pnl_raw: snapshot.signed_pnl_raw.clone(),
            signed_pnl_currency: snapshot.signed_pnl_currency.clone(),
            signed_pnl_unit: snapshot.signed_pnl_unit.clone(),
            signed_pnl_source: snapshot.signed_pnl_source.clone(),
            path_symbols: snapshot.path_symbols.clone(),
            pool_ids: snapshot.pool_ids.clone(),
            capacity_anchor_raw: snapshot.capacity_anchor_raw.clone(),
            capacity_binding_pool_id: snapshot.capacity_binding_pool_id.clone(),
        }
    }

    /// Build the audit line of a review that left the generation.
    ///
    /// A removal carries the identity and nothing else: the fields of a
    /// review that no longer exists would be misleading.
    pub fn removed(context: &ReviewGenerationContext, loop_key: &str) -> Self {
        Self {
            schema: JSONL_SCHEMA,
            event: ReviewRecordEvent::Removed,
            observed_at: context.observed_at.clone(),
            network: context.network.clone(),
            chain_id: context.chain_id,
            review_version: context.review_version,
            closed_loop_version: None,
            review_checksum: None,
            loop_key: loop_key.to_owned(),
            review_status: None,
            review_reason: None,
            signed_pnl_raw: None,
            signed_pnl_currency: None,
            signed_pnl_unit: None,
            signed_pnl_source: None,
            path_symbols: None,
            pool_ids: None,
            capacity_anchor_raw: None,
            capacity_binding_pool_id: None,
        }
    }
}

/// Compare two review generations and produce the audit events between them.
///
/// Only the *difference* is written: a review that is new or that changed
/// produces an `upsert`, a review that disappeared produces a `removed`, and a
/// review that is identical in both generations produces nothing at all.
pub fn diff_review_generations(
    context: &ReviewGenerationContext,
    previous: &[ReviewRowSnapshot],
    next: &[ReviewRowSnapshot],
) -> Vec<ReviewRecordLine> {
    let previous_by_key: BTreeMap<&str, &ReviewRowSnapshot> = previous
        .iter()
        .map(|snapshot| (snapshot.loop_key.as_str(), snapshot))
        .collect();
    let next_keys: BTreeMap<&str, &ReviewRowSnapshot> = next
        .iter()
        .map(|snapshot| (snapshot.loop_key.as_str(), snapshot))
        .collect();

    let mut events = Vec::new();
    for (loop_key, snapshot) in &next_keys {
        match previous_by_key.get(loop_key) {
            None => events.push(ReviewRecordLine::upsert(context, snapshot)),
            Some(previous_snapshot) => {
                if !previous_snapshot.same_audit_state(snapshot) {
                    events.push(ReviewRecordLine::upsert(context, snapshot));
                }
            }
        }
    }
    for (loop_key, _) in &previous_by_key {
        if !next_keys.contains_key(loop_key) {
            events.push(ReviewRecordLine::removed(context, loop_key));
        }
    }
    events
}

/// The writer of the daily review files.
///
/// One writer owns one file handle at a time and rolls over when the UTC day
/// of the line changes.  Every line is written with a single `write_all` that
/// includes the trailing newline, so a torn line can only ever be the last
/// one of the file.
pub struct JsonlWriter {
    dir: PathBuf,
    files: BTreeMap<(NaiveDate, String), tokio::fs::File>,
    paths: BTreeMap<(NaiveDate, String), PathBuf>,
}

impl JsonlWriter {
    /// Build a writer that drops its files into `dir`.
    pub fn new(dir: PathBuf) -> Self {
        Self {
            dir,
            files: BTreeMap::new(),
            paths: BTreeMap::new(),
        }
    }

    /// The file that is currently open for one closed loop, if any.
    pub fn current_path(&self, loop_key: &str) -> Option<&Path> {
        self.paths
            .iter()
            .rev()
            .find(|((_, key), _)| key == loop_key)
            .map(|(_, path)| path.as_path())
    }

    /// Append one line to the file of its closed loop and its UTC day.
    ///
    /// Every closed loop writes into a file of its own, so the audit stream
    /// of one loop can be read without walking the others.
    pub async fn append(&mut self, line: &ReviewRecordLine) -> Result<(), RecordError> {
        let day = line_day(line);
        let key = (day, line.loop_key.clone());
        if !self.files.contains_key(&key) {
            self.rotate(day).await?;
            let path = review_file_path(&self.dir, day, &line.loop_key);
            let parent = path.parent().ok_or_else(|| RecordError::InvalidPath {
                field: "review_file",
                value: path.to_string_lossy().to_string(),
            })?;
            ensure_dir(parent).await?;
            let file = tokio::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&path)
                .await
                .map_err(|source| RecordError::Io {
                    path: path.clone(),
                    source,
                })?;
            self.files.insert(key.clone(), file);
            self.paths.insert(key.clone(), path);
        }
        let mut payload = serde_json::to_vec(line)?;
        payload.push(b'\n');
        let path = self.paths.get(&key).cloned().unwrap_or_default();
        let file = self.files.get_mut(&key).ok_or_else(|| {
            RecordError::InvalidState("the review file is not open".to_owned())
        })?;
        use tokio::io::AsyncWriteExt as _;
        file.write_all(&payload).await.map_err(|source| RecordError::Io {
            path: path.clone(),
            source,
        })?;
        file.flush()
            .await
            .map_err(|source| RecordError::Io { path, source })?;
        Ok(())
    }

    /// Drop the files of the days that are over.
    ///
    /// Only the current day stays open, so a writer that runs for days does
    /// not hold one handle per day it ever saw.
    async fn rotate(&mut self, day: NaiveDate) -> Result<(), RecordError> {
        self.files.retain(|(open_day, _), _| *open_day == day);
        self.paths.retain(|(open_day, _), _| *open_day == day);
        Ok(())
    }
}

/// The name of the folder that holds the reviews of one UTC day.
pub fn review_day_folder(day: NaiveDate) -> String {
    format!("{:04}-{:02}-{:02}", day.year(), day.month(), day.day())
}

/// The day a review folder carries in its name.
pub fn review_day_from_folder(name: &str) -> Option<NaiveDate> {
    if name.len() != 10 {
        return None;
    }
    let bytes = name.as_bytes();
    if bytes[4] != b'-' || bytes[7] != b'-' {
        return None;
    }
    let year = name[0..4].parse::<i32>().ok()?;
    let month = name[5..7].parse::<u32>().ok()?;
    let day = name[8..10].parse::<u32>().ok()?;
    NaiveDate::from_ymd_opt(year, month, day)
}

/// The file name stem of one closed loop.
///
/// A loop key is built from the three token addresses of the loop
/// (`anchor:y:z`), and a colon cannot be part of a file name on Windows.
/// The key is therefore rewritten into a spelling that every file system
/// accepts, and a short digest of the original is appended, so two different
/// loops can never end up in the same file.
pub fn loop_file_stem(loop_key: &str) -> String {
    let mut safe = String::with_capacity(loop_key.len());
    for character in loop_key.chars() {
        if character.is_ascii_alphanumeric() || matches!(character, '.' | '-' | '_') {
            safe.push(character);
        } else {
            safe.push('_');
        }
    }
    let safe = safe.trim_matches('.').to_owned();
    let safe = if safe.is_empty() {
        String::from("loop")
    } else {
        safe
    };
    let safe: String = safe.chars().take(64).collect();
    let digest = Sha256::digest(loop_key.as_bytes());
    format!("{safe}_{}", hex::encode(&digest[..6]))
}

/// The file that holds the audit lines of one closed loop on one day.
pub fn review_file_path(dir: &Path, day: NaiveDate, loop_key: &str) -> PathBuf {
    dir.join(review_day_folder(day))
        .join(format!("{}.jsonl", loop_file_stem(loop_key)))
}

/// The UTC day a line belongs to.
///
/// The line carries its own observation time, so the file it lands in does
/// not depend on when the writer happened to run.
fn line_day(line: &ReviewRecordLine) -> NaiveDate {
    parse_utc(&line.observed_at)
        .map(|observed| observed.date_naive())
        .unwrap_or_else(|_| Utc::now().date_naive())
}

/// Parse an RFC3339 timestamp into UTC.
pub fn parse_utc(value: &str) -> Result<DateTime<Utc>, RecordError> {
    DateTime::parse_from_rfc3339(value)
        .map(|parsed| parsed.with_timezone(&Utc))
        .map_err(|error| RecordError::InvalidTimestamp {
            value: value.to_owned(),
            reason: error.to_string(),
        })
}

/// What the review audit stream is doing right now.
#[derive(Debug, Default)]
pub struct ReviewRecordHealth {
    /// Lines that are queued for the writer.
    pub queued: AtomicUsize,
    /// Lines that reached the disk.
    pub written: AtomicU64,
    /// Lines that were dropped because the channel was full.
    pub dropped: AtomicU64,
    /// Lines that could not be written.
    pub write_errors: AtomicU64,
    /// The last write error.
    pub last_error: Mutex<Option<String>>,
}

/// A readable view of the review audit stream health.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewRecordHealthSnapshot {
    /// Lines that are queued for the writer.
    pub queued: usize,
    /// Lines that reached the disk.
    pub written: u64,
    /// Lines that were dropped because the channel was full.
    pub dropped: u64,
    /// Lines that could not be written.
    pub write_errors: u64,
    /// Batches that could not even be handed to the writer.
    pub publish_failed: u64,
    /// The last write error.
    pub last_error: Option<String>,
}

struct RecordWriter {
    dir: PathBuf,
    sender: tokio::sync::mpsc::Sender<ReviewRecordLine>,
    health: Arc<ReviewRecordHealth>,
}

static REVIEW_WRITERS: OnceLock<Mutex<BTreeMap<PathBuf, Arc<RecordWriter>>>> = OnceLock::new();

/// Counters that belong to the whole process rather than to one writer.
static REVIEW_PUBLISH_FAILURES: AtomicU64 = AtomicU64::new(0);
static REVIEW_PUBLISH_LAST_ERROR: Mutex<Option<String>> = Mutex::new(None);

/// Record a batch that could not be handed to the review writer.
///
/// The audit stream takes part in no decision, so a caller keeps going after
/// a failure like this one.  Counting it keeps the loss visible instead of
/// silent.
pub fn note_review_publish_failure(error: &RecordError) {
    REVIEW_PUBLISH_FAILURES.fetch_add(1, Ordering::Relaxed);
    if let Ok(mut slot) = REVIEW_PUBLISH_LAST_ERROR.lock() {
        *slot = Some(error.to_string());
    }
}

fn review_writer(dir: &Path) -> Result<Arc<RecordWriter>, RecordError> {
    let registry = REVIEW_WRITERS.get_or_init(|| Mutex::new(BTreeMap::new()));
    let mut guard = registry
        .lock()
        .map_err(|_| RecordError::InvalidState("the review writer registry is poisoned".to_owned()))?;
    if let Some(writer) = guard.get(dir) {
        return Ok(writer.clone());
    }
    let (sender, receiver) =
        tokio::sync::mpsc::channel::<ReviewRecordLine>(REVIEW_CHANNEL_CAPACITY);
    let health = Arc::new(ReviewRecordHealth::default());
    let writer = Arc::new(RecordWriter {
        dir: dir.to_path_buf(),
        sender,
        health: Arc::clone(&health),
    });
    spawn_review_writer(Arc::clone(&writer), receiver, health);
    guard.insert(dir.to_path_buf(), Arc::clone(&writer));
    Ok(writer)
}

/// Start the background writer of one review directory.
///
/// The writer runs on its own thread with its own current-thread runtime, so
/// a caller never has to be inside a Tokio runtime and the audit stream can
/// never block the review materialization on a runtime worker.
fn spawn_review_writer(
    writer: Arc<RecordWriter>,
    mut receiver: tokio::sync::mpsc::Receiver<ReviewRecordLine>,
    health: Arc<ReviewRecordHealth>,
) {
    let dir = writer.dir.clone();
    let spawned = std::thread::Builder::new()
        .name("review-record-writer".to_owned())
        .spawn(move || {
            let runtime = match tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
            {
                Ok(runtime) => runtime,
                Err(error) => {
                    health.write_errors.fetch_add(1, Ordering::Relaxed);
                    if let Ok(mut slot) = health.last_error.lock() {
                        *slot = Some(format!("review writer runtime: {error}"));
                    }
                    return;
                }
            };
            runtime.block_on(async move {
                let mut writer = JsonlWriter::new(dir);
                while let Some(line) = receiver.recv().await {
                    match writer.append(&line).await {
                        Ok(()) => {
                            health.written.fetch_add(1, Ordering::Relaxed);
                        }
                        Err(error) => {
                            health.write_errors.fetch_add(1, Ordering::Relaxed);
                            if let Ok(mut slot) = health.last_error.lock() {
                                *slot = Some(error.to_string());
                            }
                        }
                    }
                    // The line is only *not queued* once it reached the disk
                    // (or failed to).  Counting it down earlier would let a
                    // waiter leave while the line is still in flight.
                    health.queued.fetch_sub(1, Ordering::Relaxed);
                }
            });
        });
    if spawned.is_err() {
        writer.health.write_errors.fetch_add(1, Ordering::Relaxed);
        if let Ok(mut slot) = writer.health.last_error.lock() {
            *slot = Some("the review writer thread could not be started".to_owned());
        }
    }
}

/// Hand a batch of audit lines to the background writer.
///
/// The send never blocks: when the channel is full the line is dropped and
/// counted.  The review audit stream is a *lossy* trail — it exists to make
/// the history of a generation readable, and it takes part in no decision — so
/// it must never slow the materialization down.
pub fn publish_review_records(
    dir: &Path,
    lines: Vec<ReviewRecordLine>,
) -> Result<(), RecordError> {
    let writer = review_writer(dir)?;
    for line in lines {
        writer.health.queued.fetch_add(1, Ordering::Relaxed);
        if writer.sender.try_send(line).is_err() {
            writer.health.queued.fetch_sub(1, Ordering::Relaxed);
            writer.health.dropped.fetch_add(1, Ordering::Relaxed);
        }
    }
    Ok(())
}

/// What the review audit stream is doing right now.
///
/// The counters of every writer are added up, so the answer covers the whole
/// process rather than one chain.
pub fn read_review_record_health() -> ReviewRecordHealthSnapshot {
    let registry = REVIEW_WRITERS.get_or_init(|| Mutex::new(BTreeMap::new()));
    let Ok(guard) = registry.lock() else {
        return ReviewRecordHealthSnapshot::default();
    };
    let mut snapshot = ReviewRecordHealthSnapshot::default();
    snapshot.publish_failed = REVIEW_PUBLISH_FAILURES.load(Ordering::Relaxed);
    if snapshot.last_error.is_none() {
        if let Ok(slot) = REVIEW_PUBLISH_LAST_ERROR.lock() {
            snapshot.last_error = slot.clone();
        }
    }
    for writer in guard.values() {
        snapshot.queued += writer.health.queued.load(Ordering::Relaxed);
        snapshot.written += writer.health.written.load(Ordering::Relaxed);
        snapshot.dropped += writer.health.dropped.load(Ordering::Relaxed);
        snapshot.write_errors += writer.health.write_errors.load(Ordering::Relaxed);
        if snapshot.last_error.is_none() {
            if let Ok(slot) = writer.health.last_error.lock() {
                snapshot.last_error = slot.clone();
            }
        }
    }
    snapshot
}

/// Wait until every queued audit line reached the disk.
///
/// Only the tests need this; the production path never waits for the audit
/// stream.
pub async fn drain_review_records(dir: &Path) -> Result<(), RecordError> {
    let writer = review_writer(dir)?;
    loop {
        if writer.health.queued.load(Ordering::Relaxed) == 0 {
            return Ok(());
        }
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
    }
}

/// The month a record belongs to: the UTC month of its own `created_at`.
///
/// A record that was created in October and archived in November belongs to
/// October.  A timestamp that cannot be parsed is an error — the month is
/// never guessed.
pub fn owning_month(created_at: &str) -> Result<(i32, u32), RecordError> {
    let observed = parse_utc(created_at)?;
    Ok((observed.year(), observed.month()))
}

/// The key of a month, for example `2026-11`.
pub fn month_key(year: i32, month: u32) -> String {
    format!("{year:04}-{month:02}")
}

/// The year and the month a month key names.
pub fn month_from_key(key: &str) -> Option<(i32, u32)> {
    if key.len() != 7 {
        return None;
    }
    if key.as_bytes()[4] != b'-' {
        return None;
    }
    let year = key[0..4].parse::<i32>().ok()?;
    let month = key[5..7].parse::<u32>().ok()?;
    if !(1..=12).contains(&month) {
        return None;
    }
    Some((year, month))
}

/// Rewrite a `CREATE` statement so it can be replayed into a store that may
/// already hold the object.
///
/// This is the **only** place a schema statement is touched.  Everything else
/// is copied verbatim, so the month store cannot drift away from the main
/// store by more than the `IF NOT EXISTS` suffix.
fn rewrite_create_statement(sql: &str) -> String {
    let trimmed = sql.trim_start();
    let upper = trimmed.to_ascii_uppercase();
    if upper.starts_with("CREATE TABLE IF NOT EXISTS ") {
        return sql.to_owned();
    }
    if upper.starts_with("CREATE UNIQUE INDEX ") {
        return format!(
            "CREATE UNIQUE INDEX IF NOT EXISTS {}",
            &trimmed["CREATE UNIQUE INDEX ".len()..]
        );
    }
    if upper.starts_with("CREATE INDEX ") {
        return format!("CREATE INDEX IF NOT EXISTS {}", &trimmed["CREATE INDEX ".len()..]);
    }
    if upper.starts_with("CREATE TABLE ") {
        return format!(
            "CREATE TABLE IF NOT EXISTS {}",
            &trimmed["CREATE TABLE ".len()..]
        );
    }
    sql.to_owned()
}

/// Whether two schema statements describe the same object.
///
/// The month store carries the `IF NOT EXISTS` suffix that the main store
/// does not need, so the comparison takes the suffix away again.
pub fn schema_statements_match(main_sql: &str, month_sql: &str) -> bool {
    fn normalize(sql: &str) -> String {
        let trimmed = sql.trim();
        let upper = trimmed.to_ascii_uppercase();
        if upper.starts_with("CREATE TABLE IF NOT EXISTS ") {
            trimmed["CREATE TABLE IF NOT EXISTS ".len()..].trim().to_owned()
        } else if upper.starts_with("CREATE UNIQUE INDEX IF NOT EXISTS ") {
            trimmed["CREATE UNIQUE INDEX IF NOT EXISTS ".len()..].trim().to_owned()
        } else if upper.starts_with("CREATE INDEX IF NOT EXISTS ") {
            trimmed["CREATE INDEX IF NOT EXISTS ".len()..].trim().to_owned()
        } else if upper.starts_with("CREATE TABLE ") {
            trimmed["CREATE TABLE ".len()..].trim().to_owned()
        } else if upper.starts_with("CREATE UNIQUE INDEX ") {
            trimmed["CREATE UNIQUE INDEX ".len()..].trim().to_owned()
        } else if upper.starts_with("CREATE INDEX ") {
            trimmed["CREATE INDEX ".len()..].trim().to_owned()
        } else {
            trimmed.to_owned()
        }
    }
    normalize(main_sql) == normalize(month_sql)
}

/// The column names of a table, in the order the table declares them.
pub fn table_columns(connection: &Connection, table: &str) -> Result<Vec<String>, RecordError> {
    let mut statement = connection.prepare(&format!("PRAGMA table_info({table})"))?;
    let rows = statement.query_map([], |row| row.get::<_, String>(1))?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

/// Copy the live schema of the main store into a month store.
///
/// The schema is read from the main store and replayed, never written by hand:
/// a hand written schema drifts as soon as the main store gains a column, and
/// a month store that drifted makes the cross month numbers incomparable with
/// the main store.  A table the main store does not have is an error — the
/// month store never invents a table of its own.
pub fn clone_schema_into(
    main: &Connection,
    month: &Connection,
    tables: &[&str],
) -> Result<(), RecordError> {
    for table in tables {
        let sql: Option<String> = main
            .query_row(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?1",
                [table],
                |row| row.get(0),
            )
            .ok();
        let Some(sql) = sql else {
            return Err(RecordError::InvalidState(format!(
                "the main store has no table {table} to clone"
            )));
        };
        month.execute_batch(&rewrite_create_statement(&sql))?;
    }
    for table in tables {
        let mut statement = main.prepare(
            "SELECT sql FROM sqlite_master
              WHERE type = 'index' AND tbl_name = ?1 AND sql IS NOT NULL",
        )?;
        let indexes = statement
            .query_map([table], |row| row.get::<_, String>(0))?
            .collect::<Result<Vec<_>, _>>()?;
        for sql in indexes {
            month.execute_batch(&rewrite_create_statement(&sql))?;
        }
    }
    Ok(())
}

/// The monthly trade store of one chain.
///
/// The store owns its connection.  A month rollover checkpoints the old
/// store, releases the handle and only then opens the new one, so two handles
/// never point at the same file.
pub struct MonthStore {
    dir: PathBuf,
    key: Option<String>,
    connection: Option<Connection>,
}

impl MonthStore {
    /// Build a store that has not opened a month yet.
    pub fn empty(dir: PathBuf) -> Self {
        Self {
            dir,
            key: None,
            connection: None,
        }
    }

    /// The month the store currently points at.
    pub fn current_key(&self) -> Option<&str> {
        self.key.as_deref()
    }

    /// The connection of the current month.
    pub fn connection(&mut self) -> Result<&mut Connection, RecordError> {
        self.connection.as_mut().ok_or_else(|| {
            RecordError::InvalidState("the month store has no open month".to_owned())
        })
    }

    /// Point the store at `key`, rolling over when the month changed.
    pub fn rotate_to(&mut self, key: &str, main: &Connection) -> Result<(), RecordError> {
        if self.key.as_deref() == Some(key) {
            return Ok(());
        }
        self.close_current()?;
        // One folder per month, one SQLite file in it.  Every closed loop of
        // the month shares that file, so the monthly numbers of the whole
        // chain stay in one place.
        let month_dir = self.dir.join(key);
        std::fs::create_dir_all(&month_dir).map_err(|source| RecordError::Io {
            path: self.dir.clone(),
            source,
        })?;
        let path = month_dir.join(TRADE_FILE_NAME);
        let existed = path.exists();
        let connection = Connection::open_with_flags(
            &path,
            OpenFlags::SQLITE_OPEN_READ_WRITE
                | OpenFlags::SQLITE_OPEN_CREATE
                | OpenFlags::SQLITE_OPEN_URI,
        )?;
        connection.busy_timeout(std::time::Duration::from_secs(60))?;
        connection.execute_batch(
            "PRAGMA journal_mode = WAL;
             PRAGMA synchronous = NORMAL;
             PRAGMA foreign_keys = ON;",
        )?;
        if !existed {
            clone_schema_into(main, &connection, &TRADE_ARCHIVE_TABLES)?;
        }
        self.key = Some(key.to_owned());
        self.connection = Some(connection);
        Ok(())
    }

    /// Checkpoint the current month and release its handle.
    fn close_current(&mut self) -> Result<(), RecordError> {
        if let Some(connection) = self.connection.take() {
            let _ = connection.execute_batch("PRAGMA wal_checkpoint(TRUNCATE);");
            drop(connection);
        }
        self.key = None;
        Ok(())
    }
}

impl Drop for MonthStore {
    fn drop(&mut self) {
        let _ = self.close_current();
    }
}

/// What the trade archive is doing right now.
#[derive(Debug, Default)]
pub struct TradeArchiveHealth {
    /// Attempts that reached a month store.
    pub archived: AtomicU64,
    /// Attempts whose month write failed.
    pub archive_failed: AtomicU64,
    /// Terminal rows that are still complete in the main store.
    pub pending_reconcile: AtomicUsize,
    /// The last archive error.
    pub last_error: Mutex<Option<String>>,
}

/// A readable view of the trade archive health.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TradeArchiveHealthSnapshot {
    /// Attempts that reached a month store.
    pub archived: u64,
    /// Attempts whose month write failed.
    pub archive_failed: u64,
    /// Terminal rows that are still complete in the main store.
    pub pending_reconcile: usize,
    /// Batches that could not even be handed to the archiver.
    pub publish_failed: u64,
    /// The last archive error.
    pub last_error: Option<String>,
}

/// One attempt that has to reach a month store.
#[derive(Debug, Clone)]
pub struct ArchiveRequest {
    /// The chain the attempt belongs to.
    pub network: String,
    /// The chain id the attempt belongs to.
    pub chain_id: i64,
    /// The protocol of the attempt.
    pub protocol: String,
    /// The scope key of the attempt.
    pub scope_key: String,
    /// The attempt identity.
    pub attempt_id: String,
}

/// The outcome of a reconciliation pass.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ArchiveReconcileReport {
    /// Terminal rows that were looked at.
    pub scanned: usize,
    /// Rows that were archived by this pass.
    pub archived: usize,
    /// Rows whose archive failed.
    pub failed: usize,
    /// Rows that were already slimmed and therefore skipped.
    pub skipped: usize,
}

enum ArchiveJob {
    Attempt(ArchiveRequest),
    Reconcile {
        reply: std::sync::mpsc::Sender<ArchiveReconcileReport>,
    },
}

struct TradeArchiver {
    dir: PathBuf,
    database_path: PathBuf,
    sender: tokio::sync::mpsc::Sender<ArchiveJob>,
    health: Arc<TradeArchiveHealth>,
}

static TRADE_ARCHIVERS: OnceLock<Mutex<BTreeMap<(PathBuf, String, i64), Arc<TradeArchiver>>>> =
    OnceLock::new();

/// Counters that belong to the whole process rather than to one archiver.
static TRADE_PUBLISH_FAILURES: AtomicU64 = AtomicU64::new(0);
static TRADE_PUBLISH_LAST_ERROR: Mutex<Option<String>> = Mutex::new(None);

/// Record a batch that could not be handed to the trade archiver.
///
/// A trade record may not be lost, so the caller counts the failure instead
/// of dropping it silently.  The row is still complete in the main store, so
/// the next reconciliation pass still finds it.
pub fn note_trade_publish_failure(error: &RecordError) {
    TRADE_PUBLISH_FAILURES.fetch_add(1, Ordering::Relaxed);
    if let Ok(mut slot) = TRADE_PUBLISH_LAST_ERROR.lock() {
        *slot = Some(error.to_string());
    }
}

fn trade_archiver(
    database_path: &Path,
    dir: PathBuf,
    network: &str,
    chain_id: i64,
) -> Result<Arc<TradeArchiver>, RecordError> {
    let registry = TRADE_ARCHIVERS.get_or_init(|| Mutex::new(BTreeMap::new()));
    let mut guard = registry.lock().map_err(|_| {
        RecordError::InvalidState("the trade archiver registry is poisoned".to_owned())
    })?;
    let key = (database_path.to_path_buf(), network.to_owned(), chain_id);
    if let Some(archiver) = guard.get(&key) {
        return Ok(archiver.clone());
    }
    let (sender, receiver) = tokio::sync::mpsc::channel::<ArchiveJob>(TRADE_CHANNEL_CAPACITY);
    let health = Arc::new(TradeArchiveHealth::default());
    let archiver = Arc::new(TradeArchiver {
        dir,
        database_path: database_path.to_path_buf(),
        sender,
        health: Arc::clone(&health),
    });
    spawn_trade_archiver(Arc::clone(&archiver), receiver, health);
    guard.insert(key, Arc::clone(&archiver));
    Ok(archiver)
}

fn spawn_trade_archiver(
    archiver: Arc<TradeArchiver>,
    mut receiver: tokio::sync::mpsc::Receiver<ArchiveJob>,
    health: Arc<TradeArchiveHealth>,
) {
    let database_path = archiver.database_path.clone();
    let dir = archiver.dir.clone();
    let spawned = std::thread::Builder::new()
        .name("trade-archive-writer".to_owned())
        .spawn(move || {
            let mut context = match ArchiveContext::open(&database_path, dir) {
                Ok(context) => context,
                Err(error) => {
                    health.archive_failed.fetch_add(1, Ordering::Relaxed);
                    if let Ok(mut slot) = health.last_error.lock() {
                        *slot = Some(error.to_string());
                    }
                    return;
                }
            };
            while let Some(job) = receiver.blocking_recv() {
                match job {
                    ArchiveJob::Attempt(request) => {
                        match context.archive(&request) {
                            Ok(true) => {
                                health.archived.fetch_add(1, Ordering::Relaxed);
                            }
                            Ok(false) => {}
                            Err(error) => {
                                health.archive_failed.fetch_add(1, Ordering::Relaxed);
                                if let Ok(mut slot) = health.last_error.lock() {
                                    *slot = Some(error.to_string());
                                }
                            }
                        }
                    }
                    ArchiveJob::Reconcile { reply } => {
                        let report = context.reconcile();
                        health.pending_reconcile.store(report.scanned, Ordering::Relaxed);
                        let _ = reply.send(report);
                    }
                }
            }
        });
    if spawned.is_err() {
        archiver.health.archive_failed.fetch_add(1, Ordering::Relaxed);
        if let Ok(mut slot) = archiver.health.last_error.lock() {
            *slot = Some("the trade archiver thread could not be started".to_owned());
        }
    }
}

/// The connection pair the archiver works with: the main store it reads and
/// slims, and the month store it writes.
struct ArchiveContext {
    main: Connection,
    store: MonthStore,
}

impl ArchiveContext {
    fn open(database_path: &Path, dir: PathBuf) -> Result<Self, RecordError> {
        let main = Connection::open_with_flags(
            database_path,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_URI,
        )?;
        main.busy_timeout(std::time::Duration::from_secs(60))?;
        Ok(Self {
            main,
            store: MonthStore::empty(dir),
        })
    }

    /// Copy one terminal attempt into its month store and slim the main row.
    ///
    /// The order is the whole point: the month write happens first and the
    /// main row is only touched after it succeeded.  A failure in between
    /// leaves the main row exactly as it was, so the record still exists and
    /// the next reconciliation pass picks it up.
    fn archive(&mut self, request: &ArchiveRequest) -> Result<bool, RecordError> {
        let row = read_attempt_row(&self.main, request)?;
        let Some(row) = row else {
            return Ok(false);
        };
        let columns = table_columns(&self.main, ATTEMPT_TABLE)?;
        let mut values = BTreeMap::new();
        for (column, value) in columns.iter().zip(row.into_iter()) {
            values.insert(column.clone(), value);
        }
        let created_at = values
            .get("created_at")
            .and_then(|value| match value {
                Value::Text(text) => Some(text.as_str()),
                _ => None,
            })
            .ok_or_else(|| {
                RecordError::InvalidState("the attempt row has no created_at".to_owned())
            })?
            .to_owned();
        let (year, month) = owning_month(&created_at)?;
        let key = month_key(year, month);
        self.store.rotate_to(&key, &self.main)?;
        let month_columns = table_columns(self.store.connection()?, ATTEMPT_TABLE)?;
        let missing = month_columns
            .iter()
            .find(|column| !values.contains_key(*column));
        if let Some(missing) = missing {
            return Err(RecordError::InvalidState(format!(
                "the month store has a column the main store does not have: {missing}"
            )));
        }
        let bound = month_columns
            .iter()
            .map(|column| values.get(column).cloned().unwrap_or(Value::Null))
            .collect::<Vec<_>>();
        let column_list = month_columns
            .iter()
            .map(|column| format!("\"{column}\""))
            .collect::<Vec<_>>()
            .join(", ");
        let placeholders = (1..=month_columns.len())
            .map(|index| format!("?{index}"))
            .collect::<Vec<_>>()
            .join(", ");
        let sql = format!(
            "INSERT OR REPLACE INTO {ATTEMPT_TABLE} ({column_list}) VALUES ({placeholders})"
        );
        {
            let connection = self.store.connection()?;
            connection.execute(&sql, rusqlite::params_from_iter(bound.iter()))?;
        }
        // The month store accepted the row: only now may the main row shrink.
        slim_attempt_row(&self.main, request, &Utc::now().to_rfc3339())?;
        Ok(true)
    }

    /// Re-archive the terminal rows that are still complete in the main store.
    fn reconcile(&mut self) -> ArchiveReconcileReport {
        let mut report = ArchiveReconcileReport::default();
        let Ok(pending) = pending_archive_rows(&self.main) else {
            return report;
        };
        report.scanned = pending.len();
        for request in pending {
            match self.archive(&request) {
                Ok(true) => report.archived += 1,
                Ok(false) => report.skipped += 1,
                Err(_) => report.failed += 1,
            }
        }
        report
    }
}

/// Read one attempt row of the main store.
fn read_attempt_row(
    main: &Connection,
    request: &ArchiveRequest,
) -> Result<Option<Vec<Value>>, RecordError> {
    let columns = table_columns(main, ATTEMPT_TABLE)?;
    if columns.is_empty() {
        return Err(RecordError::InvalidState(format!(
            "the main store has no table {ATTEMPT_TABLE}"
        )));
    }
    let column_list = columns
        .iter()
        .map(|column| format!("\"{column}\""))
        .collect::<Vec<_>>()
        .join(", ");
    let sql = format!(
        "SELECT {column_list} FROM {ATTEMPT_TABLE}
          WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3
            AND scope_key = ?4 AND attempt_id = ?5"
    );
    let mut statement = main.prepare(&sql)?;
    let mut rows = statement.query(rusqlite::params![
        request.network,
        request.chain_id,
        request.protocol,
        request.scope_key,
        request.attempt_id
    ])?;
    let Some(row) = rows.next()? else {
        return Ok(None);
    };
    let mut values = Vec::with_capacity(columns.len());
    for index in 0..columns.len() {
        values.push(row.get::<_, Value>(index)?);
    }
    Ok(Some(values))
}

/// Empty the volume columns of a terminal attempt.
///
/// The verdict columns stay: the attempt identity, the status, the receipt
/// hash and the signed profit are what the gates and the idempotency ledger
/// read.  The row itself is never deleted — the attempt identity *is* the
/// idempotency anchor.
fn slim_attempt_row(
    main: &Connection,
    request: &ArchiveRequest,
    archived_at: &str,
) -> Result<(), RecordError> {
    main.execute(
        "UPDATE manager_cow_execution_attempts
            SET path_addresses_json         = '[]',
                path_symbols_json           = '[]',
                pool_ids_json               = '[]',
                hop_sqrt_price_limits_json  = '[]',
                receipt_json                = '{}',
                evidence_json               = '{}',
                updated_at                  = ?1
          WHERE network = ?2 AND chain_id = ?3 AND protocol = ?4
            AND scope_key = ?5 AND attempt_id = ?6",
        rusqlite::params![
            archived_at,
            request.network,
            request.chain_id,
            request.protocol,
            request.scope_key,
            request.attempt_id
        ],
    )?;
    Ok(())
}

/// The terminal rows of the main store that are still complete.
///
/// A row that was already slimmed holds empty arrays and empty objects, so it
/// is left alone.  A row that still carries its path and its evidence has
/// not been archived yet.
fn pending_archive_rows(main: &Connection) -> Result<Vec<ArchiveRequest>, RecordError> {
    let sql = "SELECT network, chain_id, protocol, scope_key, attempt_id
                 FROM manager_cow_execution_attempts
                WHERE status IN ('success', 'failed')
                  AND (path_addresses_json <> '[]'
                    OR path_symbols_json <> '[]'
                    OR pool_ids_json <> '[]'
                    OR IFNULL(receipt_json, '{}') <> '{}'
                    OR IFNULL(evidence_json, '{}') <> '{}')";
    let mut statement = main.prepare(sql)?;
    let rows = statement.query_map([], |row| {
        Ok(ArchiveRequest {
            network: row.get(0)?,
            chain_id: row.get(1)?,
            protocol: row.get(2)?,
            scope_key: row.get(3)?,
            attempt_id: row.get(4)?,
        })
    })?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

/// Hand a finished attempt to the trade archive.
///
/// A trade record may not be lost, so the send applies back pressure instead
/// of dropping: when the channel is full the caller waits for room, and when
/// the caller is inside a Tokio runtime — where waiting would stall the
/// runtime — the archive is done synchronously instead.
pub fn publish_trade_archive(
    database_path: &Path,
    network: &str,
    chain_id: i64,
    requests: Vec<ArchiveRequest>,
) -> Result<(), RecordError> {
    if requests.is_empty() {
        return Ok(());
    }
    let dir = trade_record_dir(database_path, network, chain_id)?;
    let archiver = trade_archiver(database_path, dir.clone(), network, chain_id)?;
    let in_runtime = tokio::runtime::Handle::try_current().is_ok();
    for request in requests {
        if archiver.sender.try_send(ArchiveJob::Attempt(request.clone())).is_ok() {
            continue;
        }
        if !in_runtime {
            archiver
                .sender
                .blocking_send(ArchiveJob::Attempt(request))
                .map_err(|_| {
                    RecordError::InvalidState("the trade archive channel is closed".to_owned())
                })?;
            continue;
        }
        // Inside a runtime the caller must not block, so the archive is done
        // on this thread instead of waiting for the background writer.
        let mut context = ArchiveContext::open(database_path, dir.clone())?;
        match context.archive(&request) {
            Ok(true) => {
                archiver.health.archived.fetch_add(1, Ordering::Relaxed);
            }
            Ok(false) => {}
            Err(error) => {
                archiver.health.archive_failed.fetch_add(1, Ordering::Relaxed);
                if let Ok(mut slot) = archiver.health.last_error.lock() {
                    *slot = Some(error.to_string());
                }
                return Err(error);
            }
        }
    }
    Ok(())
}

/// Re-archive the terminal rows that are still complete in the main store.
///
/// This is what converges a crash between the month write and the slimming:
/// the row is still complete in the main store, the archive runs again, and
/// `INSERT OR REPLACE` keeps the month store free of duplicates.
pub fn reconcile_archive_backlog(
    database_path: &Path,
    network: &str,
    chain_id: i64,
) -> Result<ArchiveReconcileReport, RecordError> {
    let dir = trade_record_dir(database_path, network, chain_id)?;
    let archiver = trade_archiver(database_path, dir, network, chain_id)?;
    let (reply, answer) = std::sync::mpsc::channel();
    archiver
        .sender
        .blocking_send(ArchiveJob::Reconcile { reply })
        .map_err(|_| {
            RecordError::InvalidState("the trade archive channel is closed".to_owned())
        })?;
    answer.recv().map_err(|_| {
        RecordError::InvalidState("the trade archiver did not answer".to_owned())
    })
}

/// What the trade archive is doing right now.
pub fn read_trade_archive_health() -> TradeArchiveHealthSnapshot {
    let registry = TRADE_ARCHIVERS.get_or_init(|| Mutex::new(BTreeMap::new()));
    let Ok(guard) = registry.lock() else {
        return TradeArchiveHealthSnapshot::default();
    };
    let mut snapshot = TradeArchiveHealthSnapshot::default();
    snapshot.publish_failed = TRADE_PUBLISH_FAILURES.load(Ordering::Relaxed);
    if snapshot.last_error.is_none() {
        if let Ok(slot) = TRADE_PUBLISH_LAST_ERROR.lock() {
            snapshot.last_error = slot.clone();
        }
    }
    for archiver in guard.values() {
        snapshot.archived += archiver.health.archived.load(Ordering::Relaxed);
        snapshot.archive_failed += archiver.health.archive_failed.load(Ordering::Relaxed);
        snapshot.pending_reconcile = snapshot
            .pending_reconcile
            .max(archiver.health.pending_reconcile.load(Ordering::Relaxed));
        if snapshot.last_error.is_none() {
            if let Ok(slot) = archiver.health.last_error.lock() {
                snapshot.last_error = slot.clone();
            }
        }
    }
    snapshot
}

/// The exact profit totals of the archived trades, one per currency.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TradeArchiveSummary {
    /// One exact total per currency.  There is never a single cross currency
    /// total: the currencies are not commensurable.
    pub per_currency: BTreeMap<String, String>,
    /// How many archived attempts the totals cover.
    pub attempt_count: usize,
    /// The months the totals cover.
    pub months: Vec<String>,
}

/// Parse a signed hexadecimal raw profit into a sign and a magnitude.
///
/// The stored form is signed hexadecimal (`-0x2ee726109c1`).  A value without
/// the `0x` marker is read as decimal, which is the only other form the
/// producers use.  Anything else is an error: a profit is never guessed.
pub fn parse_signed_raw(raw: &str) -> Result<(bool, alloy_primitives::U256), RecordError> {
    let trimmed = raw.trim();
    let (negative, body) = match trimmed.strip_prefix('-') {
        Some(body) => (true, body),
        None => (false, trimmed.strip_prefix('+').unwrap_or(trimmed)),
    };
    if body.is_empty() {
        return Err(RecordError::InvalidRaw {
            value: raw.to_owned(),
            reason: "the value has no digits".to_owned(),
        });
    }
    let magnitude = if let Some(hex) = body.strip_prefix("0x").or_else(|| body.strip_prefix("0X"))
    {
        alloy_primitives::U256::from_str_radix(hex, 16)
    } else {
        alloy_primitives::U256::from_str_radix(body, 10)
    }
    .map_err(|error| RecordError::InvalidRaw {
        value: raw.to_owned(),
        reason: error.to_string(),
    })?;
    Ok((negative, magnitude))
}

/// Add two signed magnitudes.
///
/// The addition happens on arbitrary precision integers in Rust — never in
/// SQL, where a sum over the raw column would have to go through a floating
/// point value and lose the last digits.
pub fn add_signed_raw(
    a: (bool, alloy_primitives::U256),
    b: (bool, alloy_primitives::U256),
) -> Result<(bool, alloy_primitives::U256), RecordError> {
    match (a.0, b.0) {
        (false, false) => Ok((false, add_magnitudes(a.1, b.1)?)),
        (true, true) => Ok((true, add_magnitudes(a.1, b.1)?)),
        (false, true) => Ok(subtract_magnitudes(a.1, b.1)),
        (true, false) => Ok(subtract_magnitudes(b.1, a.1)),
    }
}

fn add_magnitudes(
    a: alloy_primitives::U256,
    b: alloy_primitives::U256,
) -> Result<alloy_primitives::U256, RecordError> {
    a.checked_add(b).ok_or_else(|| RecordError::InvalidRaw {
        value: format!("{a} + {b}"),
        reason: "the sum does not fit into 256 bits".to_owned(),
    })
}

fn subtract_magnitudes(
    larger: alloy_primitives::U256,
    smaller: alloy_primitives::U256,
) -> (bool, alloy_primitives::U256) {
    match larger.checked_sub(smaller) {
        Some(difference) => (false, difference),
        None => (true, smaller - larger),
    }
}

/// Render a signed magnitude in the stored form.
pub fn render_signed_raw(negative: bool, magnitude: alloy_primitives::U256) -> String {
    if negative && !magnitude.is_zero() {
        format!("-0x{magnitude:x}")
    } else {
        format!("0x{magnitude:x}")
    }
}

/// The months that carry an archived trade store.
pub fn list_trade_archive_months(
    database_path: &Path,
    network: &str,
    chain_id: i64,
) -> Result<Vec<String>, RecordError> {
    let dir = trade_record_dir(database_path, network, chain_id)?;
    let mut months = Vec::new();
    let Ok(entries) = std::fs::read_dir(&dir) else {
        return Ok(months);
    };
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        if !entry.path().is_dir() {
            continue;
        }
        // Only a folder that names a month counts, so an unrelated file in
        // the tree is never read as one.
        if month_from_key(&name).is_some() && dir.join(&name).join(TRADE_FILE_NAME).exists() {
            months.push(name);
        }
    }
    months.sort();
    Ok(months)
}

/// The exact profit totals of every archived month, one per currency.
///
/// The month stores are opened read only, one at a time, and the connection
/// to the main store is never touched — so no query can leave an attached
/// database behind or write into a month store.
pub fn read_trade_archive_summary(
    database_path: &Path,
    network: &str,
    chain_id: i64,
) -> Result<TradeArchiveSummary, RecordError> {
    let months = list_trade_archive_months(database_path, network, chain_id)?;
    let dir = trade_record_dir(database_path, network, chain_id)?;
    let mut summary = TradeArchiveSummary::default();
    // The running total is kept as an exact signed magnitude and only turned
    // into a string at the very end, so no intermediate value can lose a
    // digit on the way.
    let mut totals: BTreeMap<String, (bool, alloy_primitives::U256)> = BTreeMap::new();
    for key in &months {
        let path = dir.join(key).join(TRADE_FILE_NAME);
        if !path.exists() {
            continue;
        }
        let connection = Connection::open_with_flags(
            &path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI,
        )?;
        let mut statement = connection.prepare(
            "SELECT signed_pnl_raw, signed_pnl_currency
               FROM manager_cow_execution_attempts
              WHERE signed_pnl_raw IS NOT NULL AND signed_pnl_raw <> ''",
        )?;
        let rows = statement.query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })?;
        for row in rows {
            let (raw, currency) = row?;
            summary.attempt_count += 1;
            let parsed = parse_signed_raw(&raw)?;
            let entry = totals
                .entry(currency)
                .or_insert_with(|| (false, alloy_primitives::U256::ZERO));
            *entry = add_signed_raw(*entry, parsed)?;
        }
        summary.months.push(key.clone());
    }
    summary.per_currency = totals
        .into_iter()
        .map(|(currency, (negative, magnitude))| (currency, render_signed_raw(negative, magnitude)))
        .collect();
    Ok(summary)
}

/// One file that the retention pass removed.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PurgeEntry {
    /// When the file was removed, in UTC RFC3339.
    pub removed_at: String,
    /// The path of the removed file, relative to the record root.
    pub path: String,
    /// How many bytes the file held.
    pub bytes: u64,
}

/// The outcome of a retention pass.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PurgeReport {
    /// How many files were looked at.
    pub scanned: usize,
    /// The files that were removed.
    pub removed: Vec<PurgeEntry>,
    /// The paths that were refused, and why they were refused.
    pub rejected: Vec<String>,
}

/// Decide whether one day folder may be removed by the retention pass.
///
/// `Ok(Some(day))` means the folder may go, `Ok(None)` means it has to
/// stay, and an error means the path is not acceptable at all: it points
/// outside the review subtree or it is a symbolic link.
pub fn review_day_is_removable(
    reviews_root: &Path,
    candidate: &Path,
    today: NaiveDate,
    retention_days: u32,
) -> Result<Option<NaiveDate>, RecordError> {
    // The containment is proved by the canonical paths below, not by the
    // spelling of the path: the candidates are collected from the review
    // subtree, so they are absolute by the time they get here, and refusing
    // every absolute path would make the retention pass a no-op.
    let canonical_root = reviews_root
        .canonicalize()
        .map_err(|source| RecordError::Io {
            path: reviews_root.to_path_buf(),
            source,
        })?;
    let parent = candidate.parent().ok_or_else(|| RecordError::InvalidPath {
        field: "candidate",
        value: candidate.to_string_lossy().to_string(),
    })?;
    let canonical_parent = parent.canonicalize().map_err(|source| RecordError::Io {
        path: parent.to_path_buf(),
        source,
    })?;
    if !canonical_parent.starts_with(&canonical_root) {
        return Err(RecordError::InvalidPath {
            field: "candidate",
            value: candidate.to_string_lossy().to_string(),
        });
    }
    let metadata = std::fs::symlink_metadata(candidate).map_err(|source| RecordError::Io {
        path: candidate.to_path_buf(),
        source,
    })?;
    if metadata.file_type().is_symlink() {
        return Err(RecordError::InvalidPath {
            field: "candidate",
            value: candidate.to_string_lossy().to_string(),
        });
    }
    if !metadata.is_dir() {
        return Ok(None);
    }
    let Some(name) = candidate.file_name().and_then(|name| name.to_str()) else {
        return Ok(None);
    };
    let Some(day) = review_day_from_folder(name) else {
        return Ok(None);
    };
    let cutoff = today
        .checked_sub_signed(chrono::Duration::days(i64::from(retention_days)))
        .unwrap_or(NaiveDate::MIN);
    if day >= cutoff {
        return Ok(None);
    }
    Ok(Some(day))
}

/// Delete the daily review files that are past their retention period.
///
/// Only the review subtree is walked and only regular files with a review
/// file name are ever removed.  The monthly trade stores are financial
/// records and are never touched, and every removal is written into the
/// maintenance ledger.
pub async fn purge_expired_review_files(
    store_directory: &Path,
    retention_days: u32,
) -> Result<PurgeReport, RecordError> {
    let reviews_root = store_directory.join(REVIEW_DIR_NAME);
    let mut report = PurgeReport::default();
    if !reviews_root.exists() {
        return Ok(report);
    }
    let today = Utc::now().date_naive();
    let mut candidates = Vec::new();
    collect_day_folders(&reviews_root, &reviews_root, 0, &mut candidates)?;
    report.scanned = candidates.len();
    for candidate in candidates {
        let relative = candidate
            .strip_prefix(store_directory)
            .unwrap_or(&candidate)
            .to_string_lossy()
            .to_string();
        match review_day_is_removable(&reviews_root, &candidate, today, retention_days) {
            Ok(Some(_)) => {
                let bytes = folder_size(&candidate);
                tokio::fs::remove_dir_all(&candidate)
                    .await
                    .map_err(|source| RecordError::Io {
                        path: candidate.clone(),
                        source,
                    })?;
                report.removed.push(PurgeEntry {
                    removed_at: Utc::now().to_rfc3339(),
                    path: relative,
                    bytes,
                });
            }
            Ok(None) => {}
            Err(error) => report.rejected.push(format!("{relative}: {error}")),
        }
    }
    if !report.removed.is_empty() {
        write_cleanup_ledger(store_directory, &report.removed).await?;
    }
    Ok(report)
}

/// Collect every day folder below the review subtree.
///
/// The walk follows the layout of the tree: `<network>/<chain id>/<day>`.
/// Only the third level is a candidate, and a symbolic link is never
/// followed, so the pass can not walk out of the tree.
fn collect_day_folders(
    reviews_root: &Path,
    directory: &Path,
    depth: usize,
    found: &mut Vec<PathBuf>,
) -> Result<(), RecordError> {
    let entries = std::fs::read_dir(directory).map_err(|source| RecordError::Io {
        path: directory.to_path_buf(),
        source,
    })?;
    for entry in entries.flatten() {
        let path = entry.path();
        let metadata = match std::fs::symlink_metadata(&path) {
            Ok(metadata) => metadata,
            Err(_) => continue,
        };
        if metadata.file_type().is_symlink() {
            continue;
        }
        if !metadata.is_dir() {
            continue;
        }
        if depth == 2 {
            // <network>/<chain id>/<day> holds the files of one day.
            found.push(path);
        } else {
            collect_day_folders(reviews_root, &path, depth + 1, found)?;
        }
    }
    Ok(())
}

/// How many bytes a folder holds, over every file below it.
fn folder_size(folder: &Path) -> u64 {
    let mut total = 0;
    let Ok(entries) = std::fs::read_dir(folder) else {
        return 0;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let Ok(metadata) = std::fs::symlink_metadata(&path) else {
            continue;
        };
        if metadata.file_type().is_symlink() {
            continue;
        }
        if metadata.is_dir() {
            total += folder_size(&path);
        } else if metadata.is_file() {
            total += metadata.len();
        }
    }
    total
}

/// Append the removals of a retention pass to the maintenance ledger.
async fn write_cleanup_ledger(record_root: &Path, entries: &[PurgeEntry]) -> Result<(), RecordError> {
    let directory = record_root.join(MAINTENANCE_DIR_NAME);
    ensure_dir(&directory).await?;
    let now = Utc::now();
    let path = directory.join(format!("cleanup_{}.jsonl", month_key(now.year(), now.month())));
    let mut payload = Vec::new();
    for entry in entries {
        payload.extend_from_slice(serde_json::to_vec(entry)?.as_slice());
        payload.push(b'\n');
    }
    let mut file = tokio::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .await
        .map_err(|source| RecordError::Io {
            path: path.clone(),
            source,
        })?;
    use tokio::io::AsyncWriteExt as _;
    file.write_all(&payload).await.map_err(|source| RecordError::Io {
        path: path.clone(),
        source,
    })?;
    file.flush().await.map_err(|source| RecordError::Io {
        path: path.clone(),
        source,
    })?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::params;

    const NETWORK: &str = "robinhood";
    const CHAIN_ID: i64 = 4663;
    const PROTOCOL: &str = "uniswap_v4";
    const SCOPE_KEY: &str = "core4";

    /// A main store at `<root>/db/manager.sqlite3`.
    ///
    /// The `db` level is what keeps the record root inside the fixture: the
    /// record root is the sibling of the directory the store lives in.
    fn main_store(root: &Path) -> PathBuf {
        let db_dir = root.join("db");
        std::fs::create_dir_all(&db_dir).unwrap();
        let path = db_dir.join("manager.sqlite3");
        manager_db::migrate(&path).unwrap();
        path
    }

    fn snapshot(loop_key: &str, raw: &str) -> ReviewRowSnapshot {
        ReviewRowSnapshot {
            loop_key: loop_key.to_owned(),
            closed_loop_version: Some(7),
            review_status: Some("completed".to_owned()),
            review_reason: None,
            signed_pnl_raw: Some(raw.to_owned()),
            signed_pnl_currency: Some("WETH".to_owned()),
            signed_pnl_unit: Some("raw".to_owned()),
            signed_pnl_source: Some("native_rust_contract_preview".to_owned()),
            path_symbols: Some(vec!["WETH".to_owned(), "USDC".to_owned()]),
            pool_ids: Some(vec!["pool-1".to_owned()]),
            capacity_anchor_raw: Some("1000".to_owned()),
            capacity_binding_pool_id: Some("pool-1".to_owned()),
        }
    }

    fn context(review_version: i64) -> ReviewGenerationContext {
        ReviewGenerationContext {
            network: NETWORK.to_owned(),
            chain_id: CHAIN_ID,
            review_version,
            review_checksum: Some("checksum".to_owned()),
            observed_at: "2026-11-23T08:00:00Z".to_owned(),
        }
    }

    /// Insert one finished attempt into the main store.
    fn insert_attempt(database: &Path, attempt_id: &str, currency: &str, raw: &str) {
        let connection = Connection::open(database).unwrap();
        connection
            .execute(
                "INSERT INTO manager_cow_execution_attempts (
                     network, chain_id, protocol, scope_key, attempt_id,
                     loop_key, closed_loop_version, closed_loop_checksum,
                     review_version, review_checksum, anchor_token_key_id,
                     path_text, path_addresses_json, path_symbols_json,
                     pool_path_key, pool_ids_json,
                     signed_pnl_raw, signed_pnl_amount, signed_pnl_currency,
                     signed_pnl_address, signed_pnl_unit, signed_pnl_source,
                     signer_address, nonce, gas_limit,
                     rpc_url, status, receipt_json, evidence_json,
                     created_at, updated_at, terminal_at
                 ) VALUES (?1, ?2, ?3, ?4, ?5,
                           'loop-1', 7, 'loop-checksum',
                           9, 'review-checksum', 'token-1',
                           'WETH>USDC', '[\"0xaaa\",\"0xbbb\"]', '[\"WETH\",\"USDC\"]',
                           'pool-path', '[\"pool-1\"]',
                           ?6, 1.5, ?7,
                           NULL, 'raw', 'native_rust_contract_preview',
                           '0xsigner', 1, NULL,
                           'http://127.0.0.1:8545', 'success', '{}', '{}',
                           '2026-11-23T08:00:00Z', '2026-11-23T08:00:01Z',
                           '2026-11-23T08:00:01Z')",
                params![NETWORK, CHAIN_ID, PROTOCOL, SCOPE_KEY, attempt_id, raw, currency],
            )
            .unwrap();
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

    #[test]
    fn the_record_trees_sit_next_to_the_main_store() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("db").join("manager.sqlite3");
        assert_eq!(store_directory_for(&path), root.path().join("db"));
    }

    #[test]
    fn a_scope_that_could_leave_the_record_root_is_refused() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("db").join("manager.sqlite3");
        for network in ["", " ".trim(), ".", "..", "a/b", "a\\b", "a:b", "a*b"] {
            assert!(
                review_record_dir(&path, network, CHAIN_ID).is_err(),
                "the network {network:?} was accepted"
            );
        }
        assert!(review_record_dir(&path, NETWORK, 0).is_err());
        assert_eq!(
            review_record_dir(&path, NETWORK, CHAIN_ID).unwrap(),
            root.path()
                .join("db")
                .join(REVIEW_DIR_NAME)
                .join(format!("{NETWORK}/{CHAIN_ID}"))
        );
        assert_eq!(
            trade_record_dir(&path, NETWORK, CHAIN_ID).unwrap(),
            root.path()
                .join("db")
                .join(TRADE_DIR_NAME)
                .join(format!("{NETWORK}/{CHAIN_ID}"))
        );
    }

    #[tokio::test]
    async fn the_audit_stream_writes_one_json_object_per_line() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("reviews");
        let mut writer = JsonlWriter::new(dir.clone());
        writer
            .append(&ReviewRecordLine::upsert(
                &context(1),
                &snapshot("loop-1", "-0x2ee726109c1"),
            ))
            .await
            .unwrap();
        writer
            .append(&ReviewRecordLine::removed(&context(1), "loop-2"))
            .await
            .unwrap();

        let day = NaiveDate::from_ymd_opt(2026, 11, 23).unwrap();
        // Every closed loop writes into a file of its own.
        let content = std::fs::read_to_string(review_file_path(&dir, day, "loop-1")).unwrap();
        assert!(content.ends_with('\n'), "the last line is not terminated");
        let lines: Vec<&str> = content.lines().filter(|line| !line.is_empty()).collect();
        assert_eq!(lines.len(), 1);
        let first: serde_json::Value = serde_json::from_str(lines[0]).unwrap();
        assert_eq!(first["schema"], JSONL_SCHEMA);
        assert_eq!(first["event"], "upsert");
        assert_eq!(first["loopKey"], "loop-1");
        assert_eq!(first["signedPnlRaw"], "-0x2ee726109c1");
        assert_eq!(first["signedPnlCurrency"], "WETH");

        let removed =
            std::fs::read_to_string(review_file_path(&dir, day, "loop-2")).unwrap();
        let second: serde_json::Value =
            serde_json::from_str(removed.lines().next().unwrap()).unwrap();
        assert_eq!(second["event"], "removed");
        assert_eq!(second["loopKey"], "loop-2");
        assert!(second["signedPnlRaw"].is_null());
    }

    #[tokio::test]
    async fn the_audit_stream_rolls_over_to_the_day_of_the_line() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("reviews");
        let mut writer = JsonlWriter::new(dir.clone());
        let mut first = context(1);
        first.observed_at = "2026-11-22T23:59:59Z".to_owned();
        writer
            .append(&ReviewRecordLine::upsert(
                &first,
                &snapshot("loop-1", "-0x1"),
            ))
            .await
            .unwrap();
        let mut second = context(2);
        second.observed_at = "2026-11-23T00:00:01Z".to_owned();
        writer
            .append(&ReviewRecordLine::upsert(
                &second,
                &snapshot("loop-1", "-0x1"),
            ))
            .await
            .unwrap();
        assert!(dir.join("2026-11-22").join(format!("{}.jsonl", loop_file_stem("loop-1"))).exists());
        assert!(dir.join("2026-11-23").join(format!("{}.jsonl", loop_file_stem("loop-1"))).exists());
    }

    #[tokio::test]
    async fn the_published_lines_reach_the_disk() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("reviews");
        publish_review_records(
            &dir,
            vec![ReviewRecordLine::upsert(
                &context(1),
                &snapshot("loop-1", "-0x1"),
            )],
        )
        .unwrap();
        drain_review_records(&dir).await.unwrap();
        let path = review_file_path(&dir, NaiveDate::from_ymd_opt(2026, 11, 23).unwrap(), "loop-1");
        let content = std::fs::read_to_string(&path).unwrap();
        assert_eq!(content.lines().count(), 1);
    }

    #[test]
    fn only_a_review_that_changed_produces_an_audit_line() {
        let previous = vec![snapshot("loop-1", "-0x1"), snapshot("loop-2", "-0x2")];
        let mut changed = snapshot("loop-1", "-0x1");
        changed.signed_pnl_raw = Some("-0x3".to_owned());
        let next = vec![changed, snapshot("loop-2", "-0x2")];
        let events = diff_review_generations(&context(9), &previous, &next);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].loop_key, "loop-1");
        assert_eq!(events[0].event, ReviewRecordEvent::Upsert);
        assert_eq!(events[0].review_version, 9);
        assert_eq!(events[0].signed_pnl_raw.as_deref(), Some("-0x3"));
    }

    #[test]
    fn a_review_that_left_the_generation_produces_a_removal_line() {
        let previous = vec![snapshot("loop-1", "-0x1"), snapshot("loop-2", "-0x2")];
        let next = vec![snapshot("loop-1", "-0x1")];
        let events = diff_review_generations(&context(9), &previous, &next);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].loop_key, "loop-2");
        assert_eq!(events[0].event, ReviewRecordEvent::Removed);
        assert!(events[0].signed_pnl_raw.is_none());
    }

    #[test]
    fn a_new_closed_loop_generation_produces_an_audit_line() {
        let previous = vec![snapshot("loop-1", "-0x1")];
        let mut next = snapshot("loop-1", "-0x1");
        next.closed_loop_version = Some(8);
        let events = diff_review_generations(&context(9), &previous, &[next]);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].event, ReviewRecordEvent::Upsert);
        assert_eq!(events[0].closed_loop_version, Some(8));
    }

    #[test]
    fn the_signed_profit_survives_the_round_trip_with_every_digit() {
        let (negative, magnitude) = parse_signed_raw("-0x2ee726109c1").unwrap();
        assert!(negative);
        assert_eq!(render_signed_raw(negative, magnitude), "-0x2ee726109c1");
        let (negative, magnitude) = parse_signed_raw("0x2ee726109c1").unwrap();
        assert!(!negative);
        assert_eq!(render_signed_raw(negative, magnitude), "0x2ee726109c1");
        // A magnitude that does not fit into 64 bits keeps every digit.
        let big = alloy_primitives::U256::from_str_radix(
            "ffffffffffffffffffffffffffffffff",
            16,
        )
        .unwrap();
        let (negative, sum) =
            add_signed_raw((true, big), (false, alloy_primitives::U256::from(1_u64))).unwrap();
        assert_eq!(
            render_signed_raw(negative, sum),
            "-0xfffffffffffffffffffffffffffffffe"
        );
        let (negative, sum) =
            add_signed_raw((true, big), (false, big)).unwrap();
        assert!(!negative);
        assert_eq!(render_signed_raw(negative, sum), "0x0");
    }

    #[test]
    fn a_month_store_takes_its_schema_from_the_main_store() {
        let root = tempfile::tempdir().unwrap();
        let database = main_store(root.path());
        let connection = Connection::open(&database).unwrap();
        let mut store = MonthStore::empty(root.path().join("trades"));
        store.rotate_to("2026_11", &connection).unwrap();
        let main_sql: String = connection
            .query_row(
                "SELECT sql FROM sqlite_master
                  WHERE type = 'table' AND name = 'manager_cow_execution_attempts'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        let month_sql: String = store
            .connection()
            .unwrap()
            .query_row(
                "SELECT sql FROM sqlite_master
                  WHERE type = 'table' AND name = 'manager_cow_execution_attempts'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert!(schema_statements_match(&main_sql, &month_sql));
        assert_eq!(
            table_columns(store.connection().unwrap(), ATTEMPT_TABLE).unwrap().len(),
            table_columns(&connection, ATTEMPT_TABLE).unwrap().len()
        );
    }

    #[test]
    fn a_finished_attempt_reaches_the_month_store_and_the_main_row_shrinks() {
        let root = tempfile::tempdir().unwrap();
        let database = main_store(root.path());
        insert_attempt(&database, "attempt-1", "WETH", "-0x2ee726109c1");

        publish_trade_archive(&database, NETWORK, CHAIN_ID, vec![archive_request("attempt-1")]).unwrap();
        // The reconciliation runs on the same queue, so it answers only after
        // the attempt above was archived.
        let report = reconcile_archive_backlog(&database, NETWORK, CHAIN_ID).unwrap();
        assert_eq!(report.failed, 0);

        let dir = trade_record_dir(&database, NETWORK, CHAIN_ID).unwrap();
        let month = dir.join("2026-11").join(TRADE_FILE_NAME);
        assert!(month.exists(), "the month store was not created");
        let month_connection = Connection::open(&month).unwrap();
        let raw: String = month_connection
            .query_row(
                "SELECT signed_pnl_raw FROM manager_cow_execution_attempts
                  WHERE attempt_id = 'attempt-1'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(raw, "-0x2ee726109c1");
        drop(month_connection);

        let connection = Connection::open(&database).unwrap();
        let (path_addresses, receipt): (String, String) = connection
            .query_row(
                "SELECT path_addresses_json, receipt_json
                   FROM manager_cow_execution_attempts
                  WHERE attempt_id = 'attempt-1'",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        assert_eq!(path_addresses, "[]");
        assert_eq!(receipt, "{}");
    }

    #[test]
    fn the_archived_totals_are_grouped_by_currency() {
        let root = tempfile::tempdir().unwrap();
        let database = main_store(root.path());
        insert_attempt(&database, "attempt-1", "WETH", "-0x2ee726109c1");
        insert_attempt(&database, "attempt-2", "WETH", "-0x1");
        insert_attempt(&database, "attempt-3", "SPY", "0x2");
        publish_trade_archive(
            &database,
            NETWORK,
            CHAIN_ID,
            vec![
                archive_request("attempt-1"),
                archive_request("attempt-2"),
                archive_request("attempt-3"),
            ],
        )
        .unwrap();
        reconcile_archive_backlog(&database, NETWORK, CHAIN_ID).unwrap();

        let summary = read_trade_archive_summary(&database, NETWORK, CHAIN_ID).unwrap();
        assert_eq!(summary.attempt_count, 3);
        assert_eq!(summary.months, vec!["2026-11".to_owned()]);
        assert_eq!(summary.per_currency.get("SPY").unwrap(), "0x2");
        // -0x2ee726109c1 + -0x1 keeps every digit of the exact sum.
        assert_eq!(
            summary.per_currency.get("WETH").unwrap(),
            "-0x2ee726109c2"
        );
    }

    #[tokio::test]
    async fn an_expired_day_folder_is_removed_and_a_fresh_one_is_kept() {
        let root = tempfile::tempdir().unwrap();
        let store_directory = root.path().join("db");
        let reviews = store_directory
            .join(REVIEW_DIR_NAME)
            .join(NETWORK)
            .join(CHAIN_ID.to_string());
        std::fs::create_dir_all(&reviews).unwrap();
        let expired_day = Utc::now().date_naive() - chrono::Duration::days(45);
        let expired = reviews.join(review_day_folder(expired_day));
        std::fs::create_dir_all(&expired).unwrap();
        std::fs::write(expired.join(format!("{}.jsonl", loop_file_stem("loop-1"))), "{}\n").unwrap();
        let fresh = reviews.join(review_day_folder(Utc::now().date_naive()));
        std::fs::create_dir_all(&fresh).unwrap();
        std::fs::write(fresh.join(format!("{}.jsonl", loop_file_stem("loop-1"))), "{}\n").unwrap();
        let other = reviews.join("notes");
        std::fs::create_dir_all(&other).unwrap();
        std::fs::write(other.join("keep.txt"), "keep me").unwrap();

        let report = purge_expired_review_files(&store_directory, 30).await.unwrap();
        assert_eq!(report.removed.len(), 1);
        assert!(!expired.exists(), "the expired day folder was not removed");
        assert!(fresh.exists(), "the fresh day folder was removed");
        assert!(other.exists(), "a folder that is not a day folder was removed");
        // Every removal is written into the maintenance ledger.
        let ledger = store_directory.join(MAINTENANCE_DIR_NAME);
        assert!(ledger.exists(), "the maintenance ledger was not written");
    }

    #[test]
    fn a_record_belongs_to_the_month_of_its_own_creation() {
        assert_eq!(owning_month("2026-11-23T08:00:00Z").unwrap(), (2026, 11));
        assert_eq!(owning_month("2026-01-01T00:00:00Z").unwrap(), (2026, 1));
        assert!(owning_month("not a timestamp").is_err());
        assert_eq!(month_key(2026, 11), "2026-11");
        assert_eq!(month_from_key("2026-11"), Some((2026, 11)));
        assert_eq!(month_from_key("2026-13"), None);
        assert_eq!(month_from_key("nonsense"), None);
    }

    #[test]
    fn every_closed_loop_gets_a_file_of_its_own() {
        // A loop key is built from the three token addresses of the loop and
        // holds colons, which no file name may carry.
        let stem = loop_file_stem("0x1111:0x2222:0x3333");
        assert!(!stem.contains(':'));
        assert!(stem.starts_with("0x1111_0x2222_0x3333"));
        // Two different loops never share a file.
        assert_ne!(stem, loop_file_stem("0x1111:0x2222:0x4444"));
        // The same loop always gets the same file.
        assert_eq!(stem, loop_file_stem("0x1111:0x2222:0x3333"));
    }
}

