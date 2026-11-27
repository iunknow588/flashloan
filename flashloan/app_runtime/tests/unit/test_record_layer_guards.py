"""Guards for the record layer that was peeled off the main store.

The record layer moves the review audit stream into daily JSONL files and
the finished arbitrage attempts into monthly SQLite stores.  The main store
keeps the current generation, so the layer has to stay strictly on one side
of a few lines:

* it never writes the main review table — the JSONL stream is append only
  and takes part in no decision;
* the signed profit never becomes a float — a profit that is summed in
  `f64` loses its last digits;
* the month write happens *before* the main row is slimmed, so a failure in
  between leaves the record in the main store;
* both streams are published *after* the transaction was committed, so a
  rolled back transaction never leaves a line behind;
* no new dependency edge is added, so `Cargo.lock` stays byte identical.
"""

import re
from pathlib import Path


APP_RUNTIME_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_RUNTIME_ROOT.parents[1]
TOOLS_ROOT = REPO_ROOT / "flashloan" / "tools"
RECORDS_SOURCE = TOOLS_ROOT / "review_manager" / "src" / "records.rs"
REVIEW_SOURCE = TOOLS_ROOT / "review_manager" / "src" / "lib.rs"
BROADCAST_SOURCE = TOOLS_ROOT / "broadcast_manager" / "src" / "lib.rs"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_body(source: str, signature: str) -> str:
    """The body of one Rust function, from its signature to the next one."""
    start = source.find(signature)
    assert start != -1, f"the signature {signature!r} was not found"
    rest = source[start:]
    # A nested function is indented, so the next top level one is the only
    # function that starts a line of its own.
    following = re.search(r"\nfn [a-z_]", rest)
    return rest[: following.start()] if following else rest


def test_the_record_layer_never_writes_the_main_review_table():
    """The main store keeps the current generation; the JSONL stream does not.

    The audit stream is a lossy trail that is written after the fact.  If it
    could write the main review table it would become a second authority, and
    a reader could no longer tell which of the two generations is current.
    """
    source = _source(RECORDS_SOURCE)
    for table in (
        "manager_v4_reviews",
        "manager_v4_review_diagnostics",
        "manager_v4_review_broadcast_requests",
        "manager_review_library_state",
    ):
        assert table not in source, f"the record layer writes {table}"


def test_the_signed_profit_never_becomes_a_float():
    """The exact profit is kept as a signed integer, never as an f64.

    A profit that is summed in a float loses the digits that do not fit into
    the mantissa, and the monthly totals are money.  The record layer
    therefore never names a float type at all.
    """
    source = _source(RECORDS_SOURCE)
    assert "f64" not in source, "the record layer names a floating point type"
    assert "as f64" not in source


def test_the_month_store_is_written_before_the_main_row_is_slimmed():
    """The order of the two steps is what makes the archive safe.

    A record that is slimmed first and written second can vanish from both
    places when the second step fails.  Writing the month store first leaves
    the main row complete, and the next reconciliation pass finds it.
    """
    body = _function_body(_source(RECORDS_SOURCE), "fn archive(&mut self, request: &ArchiveRequest)")
    write_at = body.find("INSERT OR REPLACE INTO")
    slim_at = body.find("slim_attempt_row(&self.main")
    assert write_at != -1, "the archive does not write the month store"
    assert slim_at != -1, "the archive does not slim the main row"
    assert write_at < slim_at, "the main row is slimmed before the month write"


def test_the_review_audit_stream_is_published_after_the_commit():
    """A rolled back transaction must not leave an audit line behind.

    The line is published from the write path, so it has to come after the
    commit — otherwise the stream would describe a generation that the main
    store never accepted.
    """
    source = _source(REVIEW_SOURCE)
    body = _function_body(source, "fn run_impl(")
    commit_at = body.find("transaction.commit()?;")
    publish_at = body.find("publish_review_audit(")
    assert commit_at != -1, "the review write does not commit"
    assert publish_at != -1, "the review write does not publish the audit stream"
    assert commit_at < publish_at, "the audit stream is published before the commit"


def test_the_trade_archive_is_published_after_the_commit():
    """The archive may only see rows that the main store really accepted."""
    source = _source(BROADCAST_SOURCE)
    body = _function_body(source, "fn finalize_impl(")
    commit_at = body.find("transaction.commit()?;")
    publish_at = body.find("publish_trade_archives(")
    assert commit_at != -1, "the broadcast finalization does not commit"
    assert publish_at != -1, "the broadcast finalization does not publish the archive"
    assert commit_at < publish_at, "the archive is published before the commit"


def test_the_record_layer_adds_no_new_dependency_edge():
    """`Cargo.lock` must stay byte identical.

    The whole layer lives in `review-manager`, which `broadcast-manager`
    already depends on, so neither crate gains a dependency edge.  A version
    that is not taken from the workspace would land in the lock file.
    """
    review_manifest = _source(TOOLS_ROOT / "review_manager" / "Cargo.toml")
    broadcast_manifest = _source(TOOLS_ROOT / "broadcast_manager" / "Cargo.toml")

    tokio_line = next(
        line for line in review_manifest.splitlines() if line.strip().startswith("tokio")
    )
    assert "workspace = true" in tokio_line, "the tokio version is not the workspace one"

    for edge in ("tokio", "alloy-primitives", "rusqlite", "serde_json"):
        assert f"{edge} = " not in broadcast_manifest, (
            f"broadcast-manager gained the dependency edge {edge}"
        )


def test_the_record_root_sits_next_to_the_db_directory():
    """The record root is derived from the layout of the running system.

    The main store lives in `<app root>/db/`, so the two record trees are
    its siblings: `review_record_jsonl/` for the review audit stream and
    `broadcast_transaction_db/` for the monthly trade stores.
    """
    source = _source(RECORDS_SOURCE)
    assert 'REVIEW_DIR_NAME: &str = "review_record_jsonl"' in source
    assert 'TRADE_DIR_NAME: &str = "broadcast_transaction_db"' in source
    assert 'MAINTENANCE_DIR_NAME: &str = "record_maintenance"' in source
    assert 'TRADE_FILE_NAME: &str = "trades.sqlite3"' in source

    database_directory = REPO_ROOT / "flashloan" / "db"
    assert database_directory.is_dir(), "the main store directory is gone"
    assert (database_directory / "local_store_4663.sqlite3").exists()


def test_every_closed_loop_writes_into_a_file_of_its_own():
    """The audit stream is split by closed loop, and the month store is not.

    A review file holds the history of exactly one closed loop, so the file
    name has to carry the loop.  A loop key is built from token addresses and
    holds colons, which no file name may carry, so the key is rewritten and
    a digest of the original is appended.

    The monthly trade store is the opposite: every closed loop of the month
    shares one SQLite file, so the monthly numbers stay comparable.
    """
    source = _source(RECORDS_SOURCE)
    assert "pub fn loop_file_stem(loop_key: &str) -> String" in source
    assert "pub fn review_file_path(dir: &Path, day: NaiveDate, hour: u32, loop_key: &str)" in source
    assert "dir.join(review_day_folder(day))" in source
    assert "let month_dir = self.dir.join(key);" in source
    assert "month_dir.join(TRADE_FILE_NAME)" in source


def test_the_review_files_are_split_by_day_and_closed_loop():
    """The day is a folder and the closed loop and the hour are the file.

    One file holds one closed loop in one hour, so a file never grows past
    the lines of that hour.  The **day folder is unchanged**: the retention
    pass removes whole day folders and never has to know about the hour.
    """
    source = _source(RECORDS_SOURCE)
    assert 'pub fn review_day_folder(day: NaiveDate) -> String' in source
    assert 'pub fn review_day_from_folder(name: &str) -> Option<NaiveDate>' in source
    assert 'format!("{:04}-{:02}-{:02}", day.year(), day.month(), day.day())' in source
    assert 'pub fn review_hour_stem(day: NaiveDate, hour: u32) -> String' in source
    assert 'format!("{:04}{:02}{:02}-{:02}", day.year(), day.month(), day.day(), hour)' in source


def test_the_hour_never_enters_the_folder_name():
    """The retention pass reads a folder name, so the hour stays in the file.

    The retention pass removes a folder when its name parses as a day.  A
    folder that carried the hour would not parse, and the pass would then
    remove nothing at all, so the hour lives in the file name and never in
    the folder name.
    """
    source = _source(RECORDS_SOURCE)
    removable = _function_body(source, "fn review_day_is_removable(")
    assert "review_day_from_folder(name)" in removable
    # The folder name is still exactly the day, and the hour is appended to
    # the file name inside it.
    assert 'format!("{:04}-{:02}-{:02}", day.year(), day.month(), day.day())' in source
    assert 'dir.join(review_day_folder(day))' in source


def test_the_retention_pass_removes_whole_day_folders():
    """A day folder is the unit of retention, and it lives under reviews/."""
    source = _source(RECORDS_SOURCE)
    assert "pub fn review_day_is_removable(" in source
    assert "tokio::fs::remove_dir_all(&candidate)" in source
    # The monthly trade stores are financial records and are never touched.
    purge = source.split("pub async fn purge_expired_review_files(", 1)[1]
    purge = purge.split("\nasync fn ", 1)[0]
    assert "TRADE_DIR_NAME" not in purge
    assert "broadcast_transaction_db" not in purge
