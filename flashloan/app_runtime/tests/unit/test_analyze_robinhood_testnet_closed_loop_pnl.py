from __future__ import annotations

import json
import importlib.util
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = REPO_ROOT / "scripts" / "analyze_robinhood_testnet_closed_loop_pnl.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("closed_loop_pnl_analyzer", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_t21_field_counts_walk_nested_rows() -> None:
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


def test_analyze_report_includes_t21_field_counts() -> None:
    mod = _load_module()
    rows = [
        {
            "pair": "A -> B -> C -> A",
            "observed_at": "2026-10-20T04:13:56Z",
            "created_at": "2026-10-20T04:14:01Z",
            "state": "quote_required",
            "quote": None,
        },
        {
            "pair": "B -> C -> A -> B",
            "observed_at": "2026-10-20T04:15:56Z",
            "created_at": "2026-10-20T04:16:01Z",
            "state": "quote_required",
            "quote": {
                "live_onchain_evidence": {
                    "costModel": {"netProfitAmountMissingReason": "missing"},
                }
            },
        },
    ]
    report = mod.analyze(rows)
    assert report["t21FieldCounts"] == {
        "netProductDeltaRaw": 0,
        "ArbitrageNotProfitable": 0,
        "netProfitAmountMissingReason": 1,
    }


def test_success_gas_assessment_handles_eth_and_token_symbols() -> None:
    mod = _load_module()
    rows = [
        {
            "pair": "ETH -> A -> B -> ETH",
            "observed_at": "2026-10-21T09:24:43Z",
            "created_at": "2026-10-21T09:25:12Z",
            "state": "execution_success",
            "tx_hash": "0xabc",
            "broadcast": True,
            "auto_execute": {
                "result": {
                    "receipt": {
                        "receipt": {
                            "gasUsed": "0x186a0",
                            "effectiveGasPrice": "0x2540be400",
                        }
                    },
                    "referenceNetProfitAmount": "0.000001",
                    "referenceNetProfitSymbol": "ETH",
                }
            },
        },
        {
            "pair": "TSLA -> A -> B -> TSLA",
            "observed_at": "2026-10-21T21:59:30Z",
            "created_at": "2026-10-21T22:00:02Z",
            "state": "execution_success",
            "tx_hash": "0xdef",
            "broadcast": True,
            "auto_execute": {
                "result": {
                    "receipt": {
                        "receipt": {
                            "gasUsed": "0x186a0",
                            "effectiveGasPrice": "0x2540be400",
                        }
                    },
                    "referenceNetProfitAmount": "0.000002",
                    "referenceNetProfitSymbol": "TSLA",
                }
            },
        },
    ]
    report = mod.analyze(rows)
    assessment = report["success"]["gasAssessment"]
    assert assessment["evaluableEthCount"] == 1
    assert assessment["belowGasCostCount"] == 1
    assert assessment["netPositiveAfterGasCount"] == 0
    assert assessment["needsTokenPriceQuoteCount"] == 1
    assert assessment["missingGasCount"] == 0
    assert assessment["items"][0]["gasCostEth"] == "0.001"


def test_load_binance_usd_prices_reads_basket_current_prices(tmp_path) -> None:
    mod = _load_module()
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "observed_at": "2026-10-22T16:00:00Z",
                "price_source": "rest_interval",
                "basket": [
                    {"symbol": "TSLABUSDT", "current_price": "383.16"},
                    {"symbol": "ETHUSDT", "current_price": "2398.28"},
                ],
            }
        ),
        encoding="utf-8",
    )
    prices, meta = mod.load_binance_usd_prices(snapshot)
    assert prices == {
        "TSLABUSDT": Decimal("383.16"),
        "ETHUSDT": Decimal("2398.28"),
    }
    assert meta["available"] is True
    assert meta["priceCount"] == 2


def test_precision_uses_fresh_snapshot_usd_reference() -> None:
    mod = _load_module()
    row = {
        "pair": "TSLA -> A -> B -> TSLA",
        "observed_at": "2026-10-22T16:05:00Z",
        "created_at": "2026-10-22T16:05:05Z",
        "state": "execution_success",
        "tx_hash": "0x1234",
        "broadcast": True,
        "quote": {
            "live_onchain_evidence": {
                "costModel": {
                    "netProfitAmount": "0.000002",
                    "netProfitAmountSymbol": "TSLA",
                    "netProfitBps": "0.5",
                }
            }
        },
    }
    report = mod.analyze(
        [row],
        {"TSLABUSDT": Decimal("383.16")},
        {"observedAt": "2026-10-22T16:00:00Z", "available": True},
        now_utc=datetime(2026, 10, 22, 16, 10, tzinfo=timezone.utc),
    )
    precision = report["success"]["precisionAssessment"]
    assert precision["usdNetProfitAvailableCount"] == 1
    assert "usd_reference_unavailable" not in precision["assessmentCounts"]
    assert precision["items"][0]["netProfitUsd"] == "0.00076632"
    assert precision["items"][0]["usdReferenceSource"] == "binance_market_snapshot"
    assert precision["items"][0]["usdReference"]["fresh"] is True


def test_usd_reference_fails_closed_for_unknown_symbol_or_stale_snapshot() -> None:
    mod = _load_module()
    tz_row = {
        "pair": "TZ -> A -> B -> TZ",
        "observed_at": "2026-10-22T16:05:00Z",
        "created_at": "2026-10-22T16:05:05Z",
        "state": "execution_success",
        "tx_hash": "0x5678",
        "broadcast": True,
        "quote": {
            "live_onchain_evidence": {
                "costModel": {
                    "netProfitAmount": "0.000001",
                    "netProfitAmountSymbol": "TZ",
                }
            }
        },
    }
    stale_row = {
        **tz_row,
        "tx_hash": "0x5679",
        "observed_at": "2026-10-20T00:00:00Z",
        "created_at": "2026-10-20T00:00:05Z",
        "quote": {
            "live_onchain_evidence": {
                "costModel": {
                    "netProfitAmount": "0.000002",
                    "netProfitAmountSymbol": "TSLA",
                }
            }
        },
    }
    report = mod.analyze(
        [tz_row, stale_row],
        {"TSLABUSDT": Decimal("383.16")},
        {"observedAt": "2026-10-22T16:00:00Z", "available": True},
        now_utc=datetime(2026, 10, 22, 16, 10, tzinfo=timezone.utc),
    )
    precision = report["success"]["precisionAssessment"]
    assert precision["usdNetProfitAvailableCount"] == 0
    assert precision["assessmentCounts"]["usd_reference_unavailable"] == 2


def test_gas_assessment_converts_token_profit_with_usd_prices() -> None:
    mod = _load_module()
    row = {
        "pair": "TSLA -> A -> B -> TSLA",
        "observed_at": "2026-10-22T16:05:00Z",
        "created_at": "2026-10-22T16:05:05Z",
        "state": "execution_success",
        "tx_hash": "0xabcd",
        "broadcast": True,
        "auto_execute": {
            "result": {
                "receipt": {
                    "receipt": {
                        "gasUsed": "0x186a0",
                        "effectiveGasPrice": "0x2540be400",
                    }
                },
                "referenceNetProfitAmount": "0.000002",
                "referenceNetProfitSymbol": "TSLA",
            }
        },
    }
    report = mod.analyze(
        [row],
        {
            "TSLABUSDT": Decimal("383.16"),
            "ETHUSDT": Decimal("2398.28"),
        },
        {"observedAt": "2026-10-22T16:00:00Z", "available": True},
        now_utc=datetime(2026, 10, 22, 16, 10, tzinfo=timezone.utc),
    )
    assessment = report["success"]["gasAssessment"]
    assert assessment["needsTokenPriceQuoteCount"] == 0
    assert assessment["evaluableEthCount"] == 1
    assert assessment["belowGasCostCount"] == 1
    item = assessment["items"][0]
    assert item["assessment"] == "below_gas_cost"
    assert item["gasCostEth"] == "0.001"
    assert item["netAfterGasEth"] is not None
    assert item["netAfterGasUsd"] is not None
    assert item["usdReference"]["eth"]["snapshotSymbol"] == "ETHUSDT"


def test_precision_uses_chain_token_decimals_metadata() -> None:
    mod = _load_module()
    row = {
        "pair": "TSLA -> A -> B -> TSLA",
        "observed_at": "2026-10-22T16:05:00Z",
        "created_at": "2026-10-22T16:05:05Z",
        "state": "execution_success",
        "tx_hash": "0x9999",
        "broadcast": True,
        "quote": {
            "live_onchain_evidence": {
                "routeAddresses": [
                    "0xc9f9c86933092bbbfff3ccb4b105a4a94bf3bd4e",
                    "0x0000000000000000000000000000000000000000",
                    "0x5884ad2f920c162cfbbacc88c9c51aa75ec09e02",
                ],
                "costModel": {
                    "netProfitAmount": "0.000002",
                    "netProfitAmountSymbol": "TSLA",
                },
            }
        },
    }
    decimals = {
        "0xc9f9c86933092bbbfff3ccb4b105a4a94bf3bd4e": 18,
        "0x0000000000000000000000000000000000000000": 18,
        "0x5884ad2f920c162cfbbacc88c9c51aa75ec09e02": 18,
    }
    report = mod.analyze(
        [row],
        token_decimals=decimals,
        token_decimals_meta={"available": True, "rowCount": 3, "trustedDecimalsCount": 3},
    )
    precision = report["success"]["precisionAssessment"]["items"][0]
    assert precision["tokenDecimalsTrusted"] is True
    assert precision["tokenDecimalsSource"] == "sqlite_chain_tokens"
    assert "token_decimals_unverified" not in precision["assessment"]


def test_precision_backfills_source_when_quote_trusted_and_sqlite_has_decimals() -> None:
    mod = _load_module()
    row = {
        "pair": "TSLA -> ETH -> AMZN -> TSLA",
        "observed_at": "2026-10-22T16:05:00Z",
        "created_at": "2026-10-22T16:05:05Z",
        "state": "execution_success",
        "tx_hash": "0x7777",
        "broadcast": True,
        "quote": {
            "live_onchain_evidence": {
                "routeAddresses": [
                    "0xc9f9c86933092bbbfff3ccb4b105a4a94bf3bd4e",
                    "0x0000000000000000000000000000000000000000",
                    "0x5884ad2f920c162cfbbacc88c9c51aa75ec09e02",
                ],
                "costModel": {
                    "netProfitAmount": "0.000002",
                    "netProfitAmountSymbol": "TSLA",
                },
            },
            "quote_analysis": {"tokenDecimalsTrusted": True},
        },
    }
    decimals = {
        "0xc9f9c86933092bbbfff3ccb4b105a4a94bf3bd4e": 18,
        "0x0000000000000000000000000000000000000000": 18,
        "0x5884ad2f920c162cfbbacc88c9c51aa75ec09e02": 18,
    }
    report = mod.analyze(
        [row],
        token_decimals=decimals,
        token_decimals_meta={"available": True, "rowCount": 3, "trustedDecimalsCount": 3},
    )
    precision = report["success"]["precisionAssessment"]["items"][0]
    assert precision["tokenDecimalsTrusted"] is True
    assert precision["tokenDecimalsSource"] == "sqlite_chain_tokens"


def test_precision_fails_closed_when_chain_decimals_missing() -> None:
    mod = _load_module()
    row = {
        "pair": "TZ -> A -> B -> TZ",
        "observed_at": "2026-10-22T16:05:00Z",
        "created_at": "2026-10-22T16:05:05Z",
        "state": "execution_success",
        "tx_hash": "0x8888",
        "broadcast": True,
        "quote": {
            "live_onchain_evidence": {
                "routeAddresses": ["0x05a31b34a1bf8edb08cbfa2e9d860e6d0939313e"],
                "costModel": {
                    "netProfitAmount": "0.000001",
                    "netProfitAmountSymbol": "TZ",
                },
            }
        },
    }
    report = mod.analyze(
        [row],
        token_decimals={},
        token_decimals_meta={"available": True, "rowCount": 0, "trustedDecimalsCount": 0},
    )
    precision = report["success"]["precisionAssessment"]["items"][0]
    assert precision["tokenDecimalsTrusted"] is False
    assert "token_decimals_unverified" in precision["assessment"]


def test_load_chain_token_decimals_reads_sqlite(tmp_path) -> None:
    import sqlite3

    mod = _load_module()
    store = tmp_path / "store.sqlite3"
    connection = sqlite3.connect(str(store))
    connection.execute(
        """
        CREATE TABLE chain_tokens (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            token_address TEXT NOT NULL,
            token_decimals INTEGER,
            token_metadata_json TEXT,
            PRIMARY KEY (network, chain_id, token_address)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO chain_tokens (network, chain_id, token_address, token_decimals, token_metadata_json)
        VALUES
          ('robinhood-testnet', 46630, '0xc9f9c86933092bbbfff3ccb4b105a4a94bf3bd4e', 18, '{}'),
          ('robinhood-testnet', 46630, '0x102154e70d8485ff466bab229be90a763bf33264', 6, '{}'),
          ('robinhood-testnet', 46630, '0x05a31b34a1bf8edb08cbfa2e9d860e6d0939313e', null, null)
        """
    )
    connection.commit()
    connection.close()
    decimals, meta = mod.load_chain_token_decimals(store)
    assert decimals["0xc9f9c86933092bbbfff3ccb4b105a4a94bf3bd4e"] == 18
    assert decimals["0x102154e70d8485ff466bab229be90a763bf33264"] == 6
    assert decimals["0x05a31b34a1bf8edb08cbfa2e9d860e6d0939313e"] is None
    assert meta["available"] is True
    assert meta["trustedDecimalsCount"] == 2


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
    report = mod.analyze(rows)
    funnel = report["funnel"]
    assert funnel["reviewedCount"] == 6
    assert funnel["withExpectedProfitCount"] == 4
    assert funnel["broadcastCount"] == 4
    assert funnel["successTxCount"] == 3
    assert funnel["uniqueSuccessCount"] == 2
    assert funnel["uniqueSuccessWithExpectedProfitCount"] == 1
    assert report["success"]["uniqueSuccessCount"] == 2


def test_group_pnl_counts_both_directions_with_signed_values() -> None:
    mod = _load_module()
    rows = [
        {
            "pair": "A -> B -> C -> A",
            "route_path": ["A", "B", "C", "A"],
            "observed_at": "2026-10-20T04:13:56Z",
            "created_at": "2026-10-20T04:14:01Z",
            "state": "quote_required",
            "quote": {
                "live_onchain_evidence": {
                    "costModel": {"netProfitAmount": "-0.5", "netProfitAmountSymbol": "A"}
                }
            },
        },
        {
            "pair": "A -> C -> B -> A",
            "route_path": ["A", "C", "B", "A"],
            "observed_at": "2026-10-20T04:15:56Z",
            "created_at": "2026-10-20T04:16:01Z",
            "state": "quote_required",
            "quote": {
                "live_onchain_evidence": {
                    "costModel": {"netProfitAmount": "0.3", "netProfitAmountSymbol": "A"}
                }
            },
        },
        {
            "pair": "A -> B -> C -> A",
            "route_path": ["A", "B", "C", "A"],
            "observed_at": "2026-10-20T04:17:56Z",
            "created_at": "2026-10-20T04:18:01Z",
            "state": "quote_required",
            "quote": {
                "live_onchain_evidence": {
                    "costModel": {"netProfitAmount": None}
                }
            },
        },
    ]
    groups = mod.group_pnl_by_closed_loop(rows)
    assert len(groups) == 1
    group = groups[0]
    assert group["groupKey"] == "A|B|C"
    assert group["hasPositivePnl"] is True
    assert group["profitableDirectionCount"] == 1
    assert group["unprofitableDirectionCount"] == 1
    assert group["missingPnlDirectionCount"] == 0
    assert group["missingPnlRowCount"] == 0
    directions = {item["direction"]: item for item in group["directions"]}
    assert directions["A|B|C|A"]["rowCount"] == 2
    assert directions["A|B|C|A"]["sum"] == "-0.5"
    assert directions["A|C|B|A"]["sum"] == "0.3"
    assert directions["A|B|C|A"]["missingReason"] is None


def test_group_pnl_signed_value_clears_missing_reason_for_same_direction() -> None:
    mod = _load_module()
    rows = [
        {
            "pair": "A -> B -> C -> A",
            "route_path": ["A", "B", "C", "A"],
            "observed_at": "2026-10-20T04:13:56Z",
            "created_at": "2026-10-20T04:14:01Z",
            "state": "quote_required",
            "quote": None,
        },
        {
            "pair": "A -> B -> C -> A",
            "route_path": ["A", "B", "C", "A"],
            "observed_at": "2026-10-20T04:15:56Z",
            "created_at": "2026-10-20T04:16:01Z",
            "state": "quote_required",
            "quote": {
                "live_onchain_evidence": {
                    "costModel": {"netProfitAmount": "1.25", "netProfitAmountSymbol": "A"}
                }
            },
        },
    ]
    groups = mod.group_pnl_by_closed_loop(rows)
    assert len(groups) == 1
    group = groups[0]
    assert group["missingPnlDirectionCount"] == 0
    assert group["missingPnlRowCount"] == 0
    direction = group["directions"][0]
    assert direction["direction"] == "A|B|C|A"
    assert direction["sum"] == "1.25"
    assert direction["expectedSignedProfit"] == "1.25"
    assert direction["missingReason"] is None


def test_group_pnl_missing_value_never_becomes_zero() -> None:
    mod = _load_module()
    rows = [
        {
            "pair": "TZ -> A -> B -> TZ",
            "route_path": ["TZ", "A", "B", "TZ"],
            "observed_at": "2026-10-20T04:13:56Z",
            "state": "quote_required",
            "quote": None,
        },
        {
            "pair": "TZ -> B -> A -> TZ",
            "route_path": ["TZ", "B", "A", "TZ"],
            "observed_at": "2026-10-20T04:14:56Z",
            "state": "quote_required",
            "quote": {
                "live_onchain_evidence": {
                    "costModel": {"netProfitAmount": "1.25", "netProfitAmountSymbol": "TZ"}
                }
            },
        },
    ]
    groups = mod.group_pnl_by_closed_loop(rows)
    assert len(groups) == 1
    group = groups[0]
    assert group["groupKey"] == "TZ|A|B"
    assert group["missingPnlDirectionCount"] == 1
    missing = [item for item in group["directions"] if item["direction"] == "TZ|A|B|TZ"][0]
    assert missing["sum"] is None
    assert missing["expectedSignedProfit"] is None
    assert missing["missingReason"] is not None
    assert group["hasPositivePnl"] is True


def test_eth_call_rpc_request_failed_classified_as_transport_error() -> None:
    mod = _load_module()
    row = {
        "pair": "AMD -> AMZN -> TSLA -> AMD",
        "observed_at": "2026-10-23T16:06:00Z",
        "created_at": "2026-10-23T16:06:05Z",
        "state": "quote_required",
        "blocked_reasons": [
            "official_v4:previewArbitrageBestAmount:eth_call:attempt=3: "
            "eth_call RPC request failed: error sending request for url (https://example.invalid/)"
        ],
        "quote": {
            "live_onchain_evidence": {
                "blockers": [
                    "official_v4:previewArbitrageBestAmount:eth_call:attempt=3: "
                    "eth_call RPC request failed: error sending request for url (https://example.invalid/)"
                ],
                "costModel": {
                    "netProfitAmount": None,
                    "netProfitAmountMissingReason": (
                        "native result has no signed borrow-token profit value; "
                        "zero placeholder does not prove a zero or negative loss"
                    ),
                },
            }
        },
    }
    pnl = mod.row_pnl(row, row["pair"])
    assert pnl["expectedSignedProfit"] is None
    assert pnl["missingEvidence"]["code"] == "rpc_transport_error"
    assert pnl["missingEvidence"]["reliability"] == "quote_failed"
    report = mod.analyze([row])
    assert report["blocked"]["blockedRpcTransportErrorCount"] == 1
    assert report["blocked"]["blockedNotVerifiedCount"] == 0
    assert "native_missing_signed_value" not in report["blocked"]["blockedMissingEvidenceBreakdown"]


def test_row_review_consistent_requires_live_verified_preflight_no_blockers_positive_profit() -> None:
    mod = _load_module()
    base = {
        "pair": "A -> B -> C -> A",
        "observed_at": "2026-10-26T05:00:00Z",
        "created_at": "2026-10-26T05:00:05Z",
        "state": "quote_required",
    }
    consistent_row = {
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
    inconsistent_row = {
        **base,
        "quote": {
            "live_onchain_evidence": {
                "status": "route_unprofitable",
                "readyForTransactionPreflight": False,
                "blockers": ["NoProfitableRoute()"],
                "costModel": {"netProfitAmount": "1.25"},
            }
        },
    }
    assert mod._row_review_consistent(consistent_row) is True
    assert mod._row_review_consistent(inconsistent_row) is False
    assert mod.row_pnl(consistent_row, consistent_row["pair"])["reviewConsistent"] is True
    assert mod.row_pnl(inconsistent_row, inconsistent_row["pair"])["reviewConsistent"] is False


def test_analyze_report_counts_review_consistent_and_inconsistent_positive_estimates() -> None:
    mod = _load_module()
    base = {
        "observed_at": "2026-10-26T05:00:00Z",
        "created_at": "2026-10-26T05:00:05Z",
        "state": "quote_required",
    }
    rows = [
        {
            **base,
            "pair": "A -> B -> C -> A",
            "quote": {
                "live_onchain_evidence": {
                    "status": "live_verified",
                    "readyForTransactionPreflight": True,
                    "blockers": [],
                    "costModel": {"netProfitAmount": "1.25"},
                }
            },
        },
        {
            **base,
            "pair": "A -> C -> B -> A",
            "quote": {
                "live_onchain_evidence": {
                    "status": "route_unprofitable",
                    "readyForTransactionPreflight": False,
                    "blockers": ["NoProfitableRoute()"],
                    "costModel": {"netProfitAmount": "0.75"},
                }
            },
        },
        {
            **base,
            "pair": "B -> A -> C -> B",
            "quote": None,
        },
    ]
    report = mod.analyze(rows)
    consistency = report["reviewConsistency"]
    assert consistency["reviewConsistentCount"] == 1
    assert consistency["reviewInconsistentPositiveEstimateCount"] == 1
    assert consistency["totalReviewedCount"] == 3
    assert report["funnel"]["withExpectedProfitCount"] == 2


def test_analyze_report_counts_submission_failed_as_failed_broadcast() -> None:
    mod = _load_module()
    row = {
        "pair": "AMZN -> ETH -> AMD -> AMZN",
        "observed_at": "2026-10-26T07:02:00Z",
        "created_at": "2026-10-26T07:02:06Z",
        "state": "submission_failed",
        "category": "execution_failed",
        "broadcast": False,
        "quote": {
            "live_onchain_evidence": {
                "costModel": {
                    "netProfitAmount": "-0.0001847584554753671",
                    "netProfitAmountSymbol": "AMZN",
                }
            }
        },
    }
    report = mod.analyze([row])
    assert report["failed"]["failedCount"] == 1
    item = report["failed"]["failedClosedLoops"][0]
    assert item["state"] == "submission_failed"
    assert mod.as_decimal(item["expectedSignedProfit"]) == Decimal("-0.0001847584554753671")
    assert report["failed"]["expectedPnlBySymbol"] == [
        {"symbol": "AMZN", "count": 1, "total": "-0.0001847584554753671"}
    ]


def test_infer_chain_scope_from_store_filename() -> None:
    mod = _load_module()
    testnet_store = REPO_ROOT / "flashloan" / "db" / "local_store_46630.sqlite3"
    mainnet_store = REPO_ROOT / "flashloan" / "db" / "local_store_4663.sqlite3"
    assert mod.infer_chain_scope(testnet_store) == ("robinhood-testnet", 46630)
    assert mod.infer_chain_scope(mainnet_store) == ("robinhood", 4663)
    assert mod.infer_chain_scope(
        mainnet_store,
        network="robinhood-testnet",
        chain_id=46630,
    ) == ("robinhood-testnet", 46630)
    assert mod.schema_version_for("robinhood") == "robinhood-mainnet-closed-loop-pnl-v1"
    assert mod.schema_version_for("robinhood-testnet") == mod.SCHEMA_VERSION
