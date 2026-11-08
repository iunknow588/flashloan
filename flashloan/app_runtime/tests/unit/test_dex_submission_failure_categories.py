from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from dex.runtime.dex_onchain_quote_evidence import (
    _first_failed_hop_evidence,
    _preview_vs_quote_discrepancy,
)
from web.data_routes.dex_surface_routes_ops import (
    _is_review_deferred_row,
    _is_system_failure_row,
    _failed_submission_category,
    _review_failed_rows,
    _review_row_kind,
    _review_success_rows,
    _submission_tx_unique_stats,
)


def _row(
    *,
    state: str,
    evidence_status: str,
    blockers: list[str] | None = None,
    tx_hash: str = "",
    created_at: str = "2026-10-20T00:00:00+00:00",
) -> dict:
    return {
        "pair": "TZ -> TA -> TB -> TZ",
        "created_at": created_at,
        "state": state,
        "tx_hash": tx_hash,
        "quote": {
            "live_onchain_evidence": {
                "status": evidence_status,
                "blockers": blockers or [],
            }
        },
    }


def test_failed_submission_category_splits_rpc_quote_and_business_blocked() -> None:
    rpc = _row(state="quote_required", evidence_status="rpc_verification_timeout")
    quote = _row(
        state="ready_for_transaction_development",
        evidence_status="live_quote_failed",
        blockers=["hop 1 TZ->TA live_quote_failed: execution reverted"],
    )
    business = _row(
        state="quote_required",
        evidence_status="live_quote_failed",
        blockers=["ArbitrageNotProfitable(received, required)"],
    )
    assert _failed_submission_category(rpc) == "rpc"
    assert _failed_submission_category(quote) == "quote"
    assert _failed_submission_category(business) == "blocked"


def test_review_row_kind_defers_rpc_busy_legacy_failure() -> None:
    row = _row(
        state="quote_required",
        evidence_status="rpc_verification_failed",
        blockers=[
            "official_v4:previewBestPool:eth_call: the network is busy, please try again in a moment"
        ],
    )
    row["review_category"] = "not_executable"
    assert _is_review_deferred_row(row) is True
    assert _review_row_kind(row) == "rpc_busy_deferred"
    assert _is_system_failure_row(row) is False
    assert _failed_submission_category(row) == "deferred"


def test_review_row_kind_defers_dynamic_offline_legacy_row() -> None:
    row = _row(
        state="quote_required",
        evidence_status="rpc_unavailable",
        blockers=[
            "V4 live quote RPC is disabled because the chain link is dynamically offline; automatic review cannot run."
        ],
    )
    row["review_category"] = "not_executable"
    assert _is_review_deferred_row(row) is True
    assert _review_row_kind(row) == "rpc_busy_deferred"


def test_review_failed_and_success_rows_exclude_deferred() -> None:
    busy = _row(
        state="quote_required",
        evidence_status="rpc_verification_failed",
        blockers=["the network is busy, please try again in a moment"],
    )
    busy["review_category"] = "not_executable"
    failed = _row(
        state="quote_required",
        evidence_status="review_failed",
        blockers=["chain review failed; closed loop quote values are unavailable"],
    )
    failed["review_category"] = "not_executable"
    ready = _row(
        state="ready_not_submitted",
        evidence_status="live_verified",
        blockers=[],
    )
    ready["review_category"] = "not_executable"

    assert _review_failed_rows([busy, failed, ready]) == [failed]
    assert _review_success_rows([busy, failed, ready]) == [ready]


def test_failed_submission_category_classifies_hop_1_revert_as_quote() -> None:
    row = _row(
        state="not_executable",
        evidence_status="hop_1_live_quote_failed",
        blockers=["hop 1 TZ->TA live_quote_failed: execution reverted"],
    )
    assert _failed_submission_category(row) == "quote"


def test_failed_submission_category_does_not_merge_broadcast_revert_into_quote() -> None:
    row = _row(
        state="broadcast_failed",
        evidence_status="live_verified",
        blockers=["eth_sendRawTransaction: execution reverted: NoProfitableRoute()"],
    )
    row["broadcast"] = {
        "ok": False,
        "attempted": True,
        "failed": True,
        "status": "broadcast_failed",
        "error": "execution reverted",
    }
    assert _failed_submission_category(row) == "broadcast"


def test_submission_tx_unique_stats_counts_and_aggregates_by_tx_hash() -> None:
    rows = [
        _row(
            state="execution_success",
            evidence_status="live_verified",
            tx_hash="0x" + "a" * 64,
            created_at="2026-10-20T00:00:00+00:00",
        ),
        _row(
            state="submitted_success",
            evidence_status="live_verified",
            tx_hash="0x" + "a" * 64,
            created_at="2026-10-20T00:00:01+00:00",
        ),
        _row(
            state="execution_success",
            evidence_status="live_verified",
            tx_hash="0x" + "b" * 64,
            created_at="2026-10-20T00:00:02+00:00",
        ),
        _row(
            state="quote_required",
            evidence_status="rpc_verification_failed",
            tx_hash="0x" + "c" * 64,
            created_at="2026-10-20T00:00:03+00:00",
        ),
        _row(
            state="quote_required",
            evidence_status="live_quote_failed",
            tx_hash="0x" + "d" * 64,
            created_at="2026-10-20T00:00:04+00:00",
        ),
    ]
    stats = _submission_tx_unique_stats(rows)
    assert stats["total"] == 4
    assert stats["success"] == 2
    assert stats["failed"] == 1
    assert stats["blocked"] == 1


def test_first_failed_hop_evidence_and_preview_discrepancy() -> None:
    hop_rows = [
        {
            "hop": 1,
            "status": "hop_1_live_quote_failed",
            "pool": "0x" + "1" * 64,
            "fromSymbol": "TZ",
            "toSymbol": "TA",
            "amountInRaw": "1000000000000000000",
            "amountIn": "1.0",
            "quoteSource": "chain_hop_1_live_quote_reverted",
            "liveQuoteError": "execution reverted",
            "staticQuote": {"outputAmount": "1.01"},
        },
        {
            "hop": 2,
            "status": "live_verified",
            "fromSymbol": "TA",
            "toSymbol": "TB",
            "amountInRaw": "1010000000000000000",
            "amountIn": "1.01",
        },
    ]
    first = _first_failed_hop_evidence(hop_rows)
    assert first is not None
    assert first["hop"] == 1
    assert first["status"] == "hop_1_live_quote_failed"
    assert first["pool"] == "0x" + "1" * 64
    assert first["amountInRaw"] == "1000000000000000000"
    discrepancy = _preview_vs_quote_discrepancy(hop_rows)
    assert discrepancy is not None
    assert discrepancy["staticPreviewAvailable"] is True
    assert discrepancy["liveStatus"] == "hop_1_live_quote_failed"


REPO_ROOT = Path(__file__).resolve().parents[4]
ARCHIVE_SCRIPT = REPO_ROOT / "scripts" / "archive_cow_execution_attempts_jsonl.py"
PANEL_SCRIPT = REPO_ROOT / "scripts" / "analyze_robinhood_testnet_closed_loop_pnl.py"


def _load_archive_module():
    spec = importlib.util.spec_from_file_location("archive_cow_attempts_jsonl", ARCHIVE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_archive_jsonl_script_dry_run_requires_sqlite_rows(tmp_path) -> None:
    mod = _load_archive_module()
    database = tmp_path / "local_store.sqlite3"
    connection = sqlite3.connect(str(database))
    connection.execute(
        "CREATE TABLE cow_execution_attempts (id INTEGER PRIMARY KEY, payload_json TEXT)"
    )
    connection.commit()
    connection.close()
    with pytest.raises(ValueError, match="refusing to archive JSONL"):
        mod.verify_sqlite_attempts(database)

    connection = sqlite3.connect(str(database))
    connection.execute(
        "INSERT INTO cow_execution_attempts (payload_json) VALUES ('{}')"
    )
    connection.commit()
    connection.close()
    log_root = tmp_path / "cow_execution_attempts" / "robinhood-testnet" / "46630"
    log_root.mkdir(parents=True)
    (log_root / "attempts.jsonl").write_text("{}", encoding="utf-8")
    report = mod.archive_cow_execution_attempts_jsonl(
        log_root=log_root,
        archive_dir=tmp_path / "archive",
        database=database,
        apply=False,
    )
    assert report["dryRun"] is True
    assert report["movedFiles"] == 1
    assert report["archivePath"] is None
    assert (log_root / "attempts.jsonl").is_file()


def _load_pnl_module():
    spec = importlib.util.spec_from_file_location(
        "closed_loop_pnl_analyzer",
        PANEL_SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pnl_missing_profit_evidence_splits_liquidity_and_rpc_failures() -> None:
    mod = _load_pnl_module()
    liquidity = mod.missing_profit_evidence(
        {
            "blocked_reasons": [
                "hop 1 TZ->TA is not executable for requested amount (not profitable / liquidity): NotEnoughLiquidity"
            ]
        }
    )
    assert liquidity["code"] == "quoter_insufficient_liquidity"
    assert liquidity["reliability"] == "quote_failed"

    rpc = mod.missing_profit_evidence(
        {
            "blocked_reasons": [
                "on-chain verification failed at hop 1: rpc_verification_timeout: endpoint timed out"
            ]
        }
    )
    assert rpc["code"] == "rpc_transport_error"
    assert rpc["reliability"] == "quote_failed"

    reverted = mod.missing_profit_evidence(
        {
            "blocked_reasons": [
                "hop 1 TZ->TA live_quote_failed: execution reverted: arithmetic underflow"
            ]
        }
    )
    assert reverted["code"] == "quoter_revert"
    assert reverted["summary"] == "first_hop_live_quote_reverted"
