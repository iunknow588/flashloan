from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

from core.db.storage_cow_execution import record_cow_execution_attempts


REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_robinhood_testnet_auto_scan_window.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("auto_scan_window_verifier", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_sqlite(path: Path, rows: list[dict]) -> None:
    record_cow_execution_attempts(str(path), rows)


def test_read_attempts_filters_by_window(tmp_path: Path) -> None:
    mod = _load_module()
    path = tmp_path / "attempts.sqlite3"
    _write_sqlite(
        path,
        [
            {"observed_at": "2026-10-25T18:59:59+00:00", "network": "robinhood-testnet", "chain_id": 46630, "state": "quote_required"},
            {"observed_at": "2026-10-25T19:00:00+00:00", "network": "robinhood-testnet", "chain_id": 46630, "state": "quote_required"},
            {"observed_at": "2026-10-25T19:59:59+00:00", "network": "robinhood-testnet", "chain_id": 46630, "state": "quote_required"},
            {"created_at": "2026-10-25T20:00:00+00:00", "network": "robinhood-testnet", "chain_id": 46630, "state": "quote_required"},
            {"network": "robinhood-testnet", "chain_id": 46630, "state": "quote_required"},
        ],
    )
    start = datetime(2026, 10, 25, 19, 0, tzinfo=timezone.utc)
    end = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)
    rows = mod.read_attempts(path, start, end)
    assert [row["state"] for row in rows] == ["quote_required"] * 3


def test_summarize_success_rows_deduplicates_by_tx_hash() -> None:
    mod = _load_module()
    rows = [
        {
            "tx_hash": "0xabc",
            "state": "execution_success",
            "broadcast": True,
            "pair": "A -> B -> A",
            "final_delta_amount": "0.000001",
            "quote": {
                "live_onchain_evidence": {
                    "costModel": {"netProfitAmount": "0.000001", "netProfitAmountBps": "2"}
                }
            },
        },
        {
            "tx_hash": "0xabc",
            "state": "execution_success",
            "broadcast": True,
            "pair": "A -> B -> A",
            "final_delta_amount": "0.000002",
            "auto_execute": {
                "result": {
                    "receipt": {"receipt": {"status": "0x1"}}
                }
            },
            "quote": {
                "live_onchain_evidence": {
                    "costModel": {"netProfitAmount": "0.000002", "netProfitAmountBps": "4"}
                }
            },
        },
        {
            "tx_hash": "0xdef",
            "state": "execution_success",
            "broadcast": True,
            "pair": "B -> C -> B",
        },
        {
            "tx_hash": None,
            "state": "execution_success",
            "broadcast": True,
        },
    ]
    summary = mod.summarize_success_rows(rows)
    assert summary["uniqueSuccessCount"] == 2
    by_hash = {item["txHash"]: item for item in summary["successes"]}
    assert by_hash["0xabc"]["netProfitAmount"] == "0.000002"
    assert by_hash["0xdef"]["netProfitAmount"] is None


def test_summarize_failures_distinguishes_failed_and_blocked() -> None:
    mod = _load_module()
    rows = [
        {
            "state": "execution_failed",
            "broadcast": True,
            "tx_hash": "0xfail",
            "pair": "A -> B -> A",
            "error": "native_auto_execute_failed",
            "blocked_reasons": ["review failed"],
        },
        {
            "state": "quote_required",
            "broadcast": True,
            "tx_hash": None,
            "pair": "B -> C -> B",
        },
        {
            "state": "quote_required",
            "broadcast": False,
            "tx_hash": None,
            "pair": "C -> A -> C",
            "blocked_reasons": ["no liquidity"],
        },
    ]
    summary = mod.summarize_failures(rows)
    assert summary["failedCount"] == 2
    assert summary["blockedCount"] == 1
    assert summary["blockedSample"][0]["blockers"] == ["no liquidity"]


def test_summarize_failures_includes_submission_failed() -> None:
    mod = _load_module()
    rows = [
        {
            "state": "submission_failed",
            "category": "execution_failed",
            "broadcast": False,
            "tx_hash": None,
            "pair": "AMZN -> ETH -> AMD -> AMZN",
            "error": "static_arb_call_reverted",
            "blocked_reasons": ["NoProfitableRoute()"],
        }
    ]
    summary = mod.summarize_failures(rows)
    assert summary["failedCount"] == 1
    assert summary["blockedCount"] == 0
    sample = summary["failedSample"][0]
    assert sample["state"] == "submission_failed"
    assert sample["error"] == "static_arb_call_reverted"


def test_env_broadcast_flags_parses_booleans(tmp_path: Path) -> None:
    mod = _load_module()
    path = tmp_path / ".env"
    path.write_text(
        "ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST=true\n"
        "ROBINHOOD_MAINNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST=false\n",
        encoding="utf-8",
    )
    flags = mod.env_broadcast_flags(path)
    assert flags == {
        "ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST": True,
        "ROBINHOOD_MAINNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST": False,
    }


def test_hex_to_int_handles_null_and_hex() -> None:
    mod = _load_module()
    assert mod.hex_to_int(None) is None
    assert mod.hex_to_int("0x989680") == 10000000
    assert mod.hex_to_int("not-hex") is None


def test_summarize_success_rows_includes_profit_by_symbol() -> None:
    mod = _load_module()
    rows = [
        {
            "tx_hash": "0xa1",
            "state": "execution_success",
            "broadcast": True,
            "pair": "A -> B -> A",
            "quote": {
                "live_onchain_evidence": {
                    "costModel": {
                        "netProfitAmount": "1.5",
                        "netProfitAmountSymbol": "A",
                    }
                }
            },
        },
        {
            "tx_hash": "0xa2",
            "state": "execution_success",
            "broadcast": True,
            "pair": "B -> C -> B",
            "final_symbol": "B",
            "final_delta_amount": "2.5",
            "quote": {
                "live_onchain_evidence": {
                    "costModel": {"netProfitAmount": "2.5"}
                }
            },
        },
        {
            "tx_hash": "0xa3",
            "state": "execution_success",
            "broadcast": True,
            "pair": "C -> A -> C",
        },
    ]
    summary = mod.summarize_success_rows(rows)
    assert summary["uniqueSuccessCount"] == 3
    assert summary["profitBySymbol"] == [
        {"symbol": "A", "count": 1, "total": "1.5"},
        {"symbol": "B", "count": 1, "total": "2.5"},
    ]


def test_count_nested_keys_walks_t21_fields() -> None:
    mod = _load_module()
    rows = [
        {
            "quote": {
                "live_onchain_evidence": {
                    "costModel": {
                        "netProductDeltaRaw": "-0x14",
                        "netProfitAmountMissingReason": "no signed value",
                    }
                }
            }
        },
        {
            "blockers": [
                "ArbitrageNotProfitable(received, required)",
            ]
        },
    ]
    counts = mod.count_nested_keys(rows, mod.FIELD_KEYS)
    assert counts == {
        "netProductDeltaRaw": 1,
        "ArbitrageNotProfitable": 0,
        "netProfitAmountMissingReason": 1,
    }


def test_funnel_counts_live_testing_observation() -> None:
    mod = _load_module()
    rows = [
        {
            "state": "quote_required",
            "pair": "A -> B -> C -> A",
            "precheck": {"reference_token_profit_amount": "-0.5"},
        },
        {
            "state": "quote_required",
            "pair": "B -> C -> A -> B",
            "quote": {
                "live_onchain_evidence": {
                    "costModel": {"netProfitAmount": None}
                }
            },
        },
        {
            "state": "execution_failed",
            "broadcast": True,
            "pair": "C -> A -> B -> C",
            "final_delta_amount": "1.25",
        },
        {
            "state": "execution_success",
            "broadcast": True,
            "tx_hash": "0xaaa",
            "pair": "A -> B -> C -> A",
            "quote": {
                "live_onchain_evidence": {
                    "costModel": {"netProfitAmount": "0.5"}
                }
            },
        },
        {
            "state": "execution_success",
            "broadcast": True,
            "tx_hash": "0xaaa",
            "pair": "A -> B -> C -> A",
            "quote": {
                "live_onchain_evidence": {
                    "costModel": {"netProfitAmount": "0.6"}
                }
            },
        },
        {
            "state": "execution_success",
            "broadcast": True,
            "tx_hash": "0xbbb",
            "pair": "B -> C -> A -> B",
        },
    ]
    summary = mod.summarize_success_rows(rows)
    funnel = mod.funnel_counts(
        rows,
        unique_success_count=summary["uniqueSuccessCount"],
        profit_by_symbol=summary["profitBySymbol"],
    )
    assert funnel == {
        "reviewedCount": 6,
        "withExpectedProfitCount": 4,
        "broadcastCount": 4,
        "successTxCount": 3,
        "uniqueSuccessCount": 2,
        "uniqueSuccessWithExpectedProfitCount": 1,
        "realizedProfitBySymbol": summary["profitBySymbol"],
    }


def test_row_review_consistent_rejects_positive_estimate_without_live_review() -> None:
    mod = _load_module()
    base = {
        "pair": "A -> B -> C -> A",
        "observed_at": "2026-10-26T05:00:00Z",
        "created_at": "2026-10-26T05:00:05Z",
        "state": "quote_required",
    }
    consistent = {
        **base,
        "quote": {
            "live_onchain_evidence": {
                "status": "live_verified",
                "readyForTransactionPreflight": True,
                "blockers": [],
                "costModel": {"netProfitAmount": "1.25"},
            }
        },
    }
    inconsistent = {
        **base,
        "quote": {
            "live_onchain_evidence": {
                "status": "route_unprofitable",
                "readyForTransactionPreflight": False,
                "blockers": ["NoProfitableRoute()"],
                "costModel": {"netProfitAmount": "0.75"},
            }
        },
    }
    assert mod.row_review_consistent(consistent) is True
    assert mod.row_review_consistent(inconsistent) is False
    assert mod.row_review_inconsistent_positive(inconsistent) is True
