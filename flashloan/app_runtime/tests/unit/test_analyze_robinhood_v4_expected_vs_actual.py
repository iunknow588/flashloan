from __future__ import annotations

import importlib.util
from pathlib import Path
import json
import sqlite3


REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = REPO_ROOT / "scripts" / "analyze_robinhood_v4_expected_vs_actual.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "robinhood_expected_vs_actual",
        SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _swap_log(
    pool_id: str,
    amount0: int,
    amount1: int,
) -> dict:
    mod = _load_module()
    return {
        "address": "0x8366a39cc670b4001a1121b8f6a443a643e40951",
        "topics": [
            mod.V4_SWAP_TOPIC,
            "0x" + pool_id.lower(),
        ],
        "data": (
            "0x"
            + int(amount0).to_bytes(32, signed=True).hex()
            + int(amount1).to_bytes(32, signed=True).hex()
            + bytes(32).hex()
            + bytes(32).hex()
            + bytes(32).hex()
            + bytes(32).hex()
        ),
    }


def test_parse_swap_log_decodes_signed_deltas() -> None:
    mod = _load_module()
    log = _swap_log(
        "cf6cbc2e370cbe2d78a33232f0b1ecf960b01e7745dddec34f3e838768d9d94b",
        -1508540770064221,
        1953345512694840,
    )
    parsed = mod.parse_swap_log(log)
    assert parsed is not None
    assert parsed["poolId"] == "0xcf6cbc2e370cbe2d78a33232f0b1ecf960b01e7745dddec34f3e838768d9d94b"
    assert parsed["amount0"] == "-1508540770064221"
    assert parsed["amount1"] == "1953345512694840"


def test_actual_swap_amounts_uses_negative_delta_as_input() -> None:
    mod = _load_module()
    swap = {
        "amount0": "-1508540770064221",
        "amount1": "1953345512694840",
    }
    candidate = {
        "currency0": "0x5884ad2f920c162cfbbacc88c9c51aa75ec09e02",
        "currency1": "0xc9f9c86933092bbbfff3ccb4b105a4a94bf3bd4e",
    }
    route = [
        "0xc9f9c86933092bbbfff3ccb4b105a4a94bf3bd4e",
        "0x0000000000000000000000000000000000000000",
        "0x5884ad2f920c162cfbbacc88c9c51aa75ec09e02",
        "0xc9f9c86933092bbbfff3ccb4b105a4a94bf3bd4e",
    ]
    actual = mod.actual_swap_amounts(swap, candidate, route)
    # amount0 negative -> AMZN is input, but AMZN's next route token is TSLA
    # while this candidate outputs TSLA; the adjacency check must accept it.
    assert actual["decodable"] is True
    assert actual["inputRaw"] == "1508540770064221"
    assert actual["outputRaw"] == "1953345512694840"


def test_actual_swap_amounts_rejects_non_adjacent_pool() -> None:
    mod = _load_module()
    swap = {
        "amount0": "115207263117316",
        "amount1": "-1508540770064221",
    }
    candidate = {
        "currency0": "0x0000000000000000000000000000000000000000",
        "currency1": "0x5884ad2f920c162cfbbacc88c9c51aa75ec09e02",
    }
    route = [
        "0xc9f9c86933092bbbfff3ccb4b105a4a94bf3bd4e",
        "0x0000000000000000000000000000000000000000",
        "0x5884ad2f920c162cfbbacc88c9c51aa75ec09e02",
        "0xc9f9c86933092bbbfff3ccb4b105a4a94bf3bd4e",
    ]
    actual = mod.actual_swap_amounts(swap, candidate, route)
    # amount1 negative -> AMZN is input, but AMZN's next route token is TSLA
    # while this candidate outputs ETH; adjacency fails as expected.
    assert actual["decodable"] is False
    assert actual["reason"] == "swap_not_on_route_adjacency"


def test_human_amount_uses_borrow_decimals() -> None:
    mod = _load_module()
    assert mod.human_amount(220512694840, 18) == "2.2051269484E-7"
    assert mod.human_amount(None, 18) is None
    assert mod.human_amount(10, None) is None


def test_row_actual_missing_receipt_is_explicit() -> None:
    mod = _load_module()
    row = {
        "state": "execution_success",
        "tx_hash": "0xdead",
        "pair": "A -> B -> C -> A",
        "auto_execute": {
            "result": {
                "receipt": {"receipt": {}},
            }
        },
        "quote": {
            "live_onchain_evidence": {
                "routeAddresses": [
                    "0x1111111111111111111111111111111111111111",
                    "0x2222222222222222222222222222222222222222",
                    "0x3333333333333333333333333333333333333333",
                    "0x1111111111111111111111111111111111111111",
                ],
                "routeSymbols": ["A", "B", "C", "A"],
            }
        },
    }
    item = mod.row_actual(row)
    assert item["decodable"] is False
    assert item["missingReason"] == "receipt_has_no_pool_manager_swap_logs"
    assert item["actualNetProfit"] is None


def test_row_actual_does_not_report_partial_hops_as_realized_profit() -> None:
    mod = _load_module()
    tsla = "0xc9f9c86933092bbbfff3ccb4b105a4a94bf3bd4e"
    eth = "0x0000000000000000000000000000000000000000"
    amzn = "0x5884ad2f920c162cfbbacc88c9c51aa75ec09e02"
    row = {
        "state": "execution_success",
        "tx_hash": "0xpartial",
        "pair": "TSLA -> ETH -> AMZN -> TSLA",
        "route_addresses": [tsla, eth, amzn, tsla],
        "route_symbols": ["TSLA", "ETH", "AMZN", "TSLA"],
        "auto_execute": {
            "result": {
                "receipt": {
                    "receipt": {
                        "status": 1,
                        "gasUsed": "0x4b143",
                        "effectiveGasPrice": "0x989680",
                        "logs": [
                            _swap_log("missing-first-hop", -100, 200),
                            _swap_log(
                                "1965da48bc986ea0f6197b65521da2f668c22ccc5830f628d0f8f920bef8bc66",
                                -115207263117316,
                                1508540770064221,
                            ),
                            _swap_log(
                                "cf6cbc2e370cbe2d78a33232f0b1ecf960b01e7745dddec34f3e838768d9d94b",
                                -1508540770064221,
                                1953345512694840,
                            ),
                        ],
                    }
                }
            }
        },
        "quote": {
            "candidate_pools": [
                {
                    "poolId": "0x1965da48bc986ea0f6197b65521da2f668c22ccc5830f628d0f8f920bef8bc66",
                    "currency0": eth,
                    "currency1": amzn,
                },
                {
                    "poolId": "0xcf6cbc2e370cbe2d78a33232f0b1ecf960b01e7745dddec34f3e838768d9d94b",
                    "currency0": amzn,
                    "currency1": tsla,
                },
            ],
            "live_onchain_evidence": {
                "routeAddresses": [tsla, eth, amzn, tsla],
                "routeSymbols": ["TSLA", "ETH", "AMZN", "TSLA"],
                "costModel": {
                    "netProfitAmount": "2.2051269484E-7",
                    "netProfitAmountSymbol": "TSLA",
                },
            },
        },
    }

    item = mod.row_actual(
        row,
        chain_decimals={tsla: 18, eth: 18, amzn: 18},
    )

    assert item["decodable"] is False
    assert item["actualNetProfit"] is None
    assert item["profitStatus"] == "unavailable"
    assert item["differenceReasons"] == [
        "full_route_undecodable",
        "actual_net_profit_missing",
    ]


def test_authoritative_candidate_pool_refresh_repairs_stale_attempt_metadata(tmp_path) -> None:
    mod = _load_module()
    store = tmp_path / "local_store_4663.sqlite3"
    connection = sqlite3.connect(store)
    connection.execute(
        """
        CREATE TABLE dex_candidate_pools (
            network TEXT,
            chain_id INTEGER,
            protocol TEXT,
            pool_id TEXT,
            pool_key_json TEXT,
            pool_manager TEXT,
            token0 TEXT,
            token1 TEXT,
            fee INTEGER
        )
        """
    )
    connection.execute(
        "INSERT INTO dex_candidate_pools VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "robinhood",
            4663,
            "uniswap_v4",
            "0xnew",
            json.dumps(
                {
                    "poolId": "0xnew",
                    "currency0": "0x0000000000000000000000000000000000000000",
                    "currency1": "0x117cc2133c37b721f49de2a7a74833232b3b4c0c",
                    "fee": 1000,
                }
            ),
            "0xmanager",
            "0x0000000000000000000000000000000000000000",
            "0x117cc2133c37b721f49de2a7a74833232b3b4c0c",
            1000,
        ),
    )
    connection.commit()
    connection.close()

    pools = mod.load_candidate_pools(
        store,
        network="robinhood",
        chain_id=4663,
    )

    assert pools["0xnew"]["currency0"] == "0x0000000000000000000000000000000000000000"
    assert pools["0xnew"]["currency1"] == "0x117cc2133c37b721f49de2a7a74833232b3b4c0c"
    assert pools["0xnew"]["fee"] == 1000


def test_row_actual_decodes_full_route_and_matches_expected() -> None:
    mod = _load_module()
    tsla = "0xc9f9c86933092bbbfff3ccb4b105a4a94bf3bd4e"
    eth = "0x0000000000000000000000000000000000000000"
    amzn = "0x5884ad2f920c162cfbbacc88c9c51aa75ec09e02"
    row = {
        "state": "execution_success",
        "tx_hash": "0xabc",
        "pair": "TSLA -> ETH -> AMZN -> TSLA",
        "route_symbols": ["TSLA", "ETH", "AMZN", "TSLA"],
        "auto_execute": {
            "result": {
                "receipt": {
                    "receipt": {
                        "status": 1,
                        "gasUsed": "0x4b143",
                        "effectiveGasPrice": "0x989680",
                        "blockNumber": "0x8584993",
                        "logs": [
                            _swap_log("cd5b37560fdaf7cabd475c51d4e5127d7321d6eb45e3d5bacf61f59b91b6a069", 115207263117316, -1953125000000000),
                            _swap_log("1965da48bc986ea0f6197b65521da2f668c22ccc5830f628d0f8f920bef8bc66", -115207263117316, 1508540770064221),
                            _swap_log("cf6cbc2e370cbe2d78a33232f0b1ecf960b01e7745dddec34f3e838768d9d94b", -1508540770064221, 1953345512694840),
                        ],
                    }
                }
            }
        },
        "quote": {
            "candidate_pools": [
                {
                    "poolId": "0xcd5b37560fdaf7cabd475c51d4e5127d7321d6eb45e3d5bacf61f59b91b6a069",
                    "currency0": eth,
                    "currency1": tsla,
                    "token0": eth,
                    "token1": tsla,
                    "token0Decimals": 18,
                    "token1Decimals": 18,
                    "token0Symbol": "ETH",
                    "token1Symbol": "TSLA",
                },
                {
                    "poolId": "0x1965da48bc986ea0f6197b65521da2f668c22ccc5830f628d0f8f920bef8bc66",
                    "currency0": eth,
                    "currency1": amzn,
                    "token0": eth,
                    "token1": amzn,
                    "token0Decimals": 18,
                    "token1Decimals": 18,
                    "token0Symbol": "ETH",
                    "token1Symbol": "AMZN",
                },
                {
                    "poolId": "0xcf6cbc2e370cbe2d78a33232f0b1ecf960b01e7745dddec34f3e838768d9d94b",
                    "currency0": amzn,
                    "currency1": tsla,
                    "token0": amzn,
                    "token1": tsla,
                    "token0Decimals": 18,
                    "token1Decimals": 18,
                    "token0Symbol": "AMZN",
                    "token1Symbol": "TSLA",
                },
            ],
            "live_onchain_evidence": {
                "routeAddresses": [tsla, eth, amzn, tsla],
                "routeSymbols": ["TSLA", "ETH", "AMZN", "TSLA"],
                "costModel": {
                    "netProfitAmount": "2.2051269484E-7",
                    "netProfitAmountSymbol": "TSLA",
                },
            },
        },
    }
    item = mod.row_actual(row)
    assert item["decodable"] is True
    assert item["actualNetProfit"] == "2.2051269484E-7"
    assert item["expectedNetProfit"] == "2.2051269484E-7"
    assert len(item["hops"]) == 3
    assert all(hop["decodable"] for hop in item["hops"])
    assert item["netProfitDifference"] is not None
    assert mod.as_decimal(item["netProfitDifference"]) == 0


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
    assert mod.schema_version_for("robinhood") == "robinhood-mainnet-expected-vs-actual-v2"
    assert mod.schema_version_for("robinhood-testnet") == mod.SCHEMA_VERSION
