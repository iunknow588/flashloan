import json
from pathlib import Path

from copy import deepcopy
import dex.intent_trade.chains.robinhood.chain_test_pack as chain_test_pack
from dex.intent_trade.chains.robinhood.chain_test_audit import audit_robinhood_chain_test_plan
from dex.intent_trade.chains.robinhood.chain_test_pack import build_robinhood_chain_test_plan, write_robinhood_chain_test_package
from dex.intent_trade.chains.robinhood.chain_test_policy import robinhood_chain_test_scope
from dex.tools.build_robinhood_chain_test_package import _assert_attempt_scope


def _pool_id(char: str) -> str:
    return "0x" + char * 64


def _sample_attempt():
    return {
        "observed_at": "2026-08-25T06:29:42.838755+00:00",
        "created_at": "2026-08-25T06:29:58.427228+00:00",
        "network": "robinhood",
        "chain_id": 4663,
        "pair": "USDG -> WETH -> VLAD -> WETH -> USDG",
        "route_path": ["USDG", "WETH", "VLAD", "WETH", "USDG"],
        "state": "ready_for_transaction_development",
        "execution_phase": "market_candidate",
        "trigger_block_number": 45513946,
        "quote": {
            "live_onchain_evidence": {
                "status": "live_verified",
                "executionTrust": "live_quoter_verified_readonly",
                "trustedForExecution": False,
                "readyForTransactionPreflight": True,
                "blockNumber": 45513946,
                "triggerBlockNumber": 45513946,
                "routeSymbols": ["USDG", "WETH", "VLAD", "WETH", "USDG"],
                "validation": {
                    "allHopsQuoted": True,
                    "routeClosed": True,
                    "finalOutputSymbol": "USDG",
                    "profitableAfterCosts": True,
                },
                "costModel": {
                    "notionalUsd": 1000.0,
                    "finalOutputUsd": 7611.915561,
                    "grossProfitUsd": 6611.915561,
                    "flashLoanFeeBps": 5.0,
                    "netProfitUsd": 6610.415561,
                    "netProfitBps": 66104.15561,
                },
                "blockers": [
                    "Read-only on-chain quoter validation is positive; calldata, slippage guard, flash-loan repayment constraint, and fork/static-call are still required before submission.",
                ],
                "hops": [
                    {
                        "hop": 1,
                        "protocol": "uniswap_v2",
                        "quoteMethod": "v2_pair_reserves_constant_product_at_block",
                        "pool": "0x8803c117ccae7B5146297876c2A25DF135141C4d",
                        "fromSymbol": "USDG",
                        "toSymbol": "WETH",
                        "tokenIn": "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168",
                        "tokenOut": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
                        "amountInRaw": "1000000000",
                        "amountOutRaw": "393304782954818430",
                        "amountIn": "1000",
                        "amountOut": "0.39330478295481843",
                        "inputDecimals": 6,
                        "outputDecimals": 18,
                        "feeBps": 30,
                    },
                    {
                        "hop": 2,
                        "protocol": "uniswap_v3",
                        "quoteMethod": "v3_quoter_quoteExactInputSingle_eth_call",
                        "pool": "0xE7E1337B0505eEe349e3345bDf829073Fde0E2c3",
                        "fromSymbol": "WETH",
                        "toSymbol": "VLAD",
                        "tokenIn": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
                        "tokenOut": "0x91e2ce85c223CD55b0Cf76Ca668a0e61ed696C6b",
                        "amountInRaw": "393304782954818430",
                        "amountOutRaw": "101185239581426471901833926",
                        "amountIn": "0.39330478295481843",
                        "amountOut": "101185239.581426471901833926",
                        "inputDecimals": 18,
                        "outputDecimals": 18,
                        "fee": 10000,
                    },
                    {
                        "hop": 3,
                        "protocol": "uniswap_v3",
                        "quoteMethod": "v3_quoter_quoteExactInputSingle_eth_call",
                        "pool": "0x8a3aE9184141C085BAD6aBfEFcC626dE288be3d3",
                        "fromSymbol": "VLAD",
                        "toSymbol": "WETH",
                        "tokenIn": "0x91e2ce85c223CD55b0Cf76Ca668a0e61ed696C6b",
                        "tokenOut": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
                        "amountInRaw": "101185239581426471901833926",
                        "amountOutRaw": "3314675503758191895",
                        "amountIn": "101185239.581426471901833926",
                        "amountOut": "3.314675503758191895",
                        "inputDecimals": 18,
                        "outputDecimals": 18,
                        "fee": 10000,
                    },
                    {
                        "hop": 4,
                        "protocol": "uniswap_v2",
                        "quoteMethod": "v2_pair_reserves_constant_product_at_block",
                        "pool": "0x8803c117ccae7B5146297876c2A25DF135141C4d",
                        "fromSymbol": "WETH",
                        "toSymbol": "USDG",
                        "tokenIn": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
                        "tokenOut": "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168",
                        "amountInRaw": "3314675503758191895",
                        "amountOutRaw": "7611915561",
                        "amountIn": "3.314675503758191895",
                        "amountOut": "7611.915561",
                        "inputDecimals": 18,
                        "outputDecimals": 6,
                        "feeBps": 30,
                    },
                ],
            }
        },
        "precheck": {
            "status": "transaction_development_required",
            "ready_for_transaction_preflight": True,
            "trustedForExecution": False,
            "live_profit_percent": 661.0415561,
            "reference_profit_amount": 15807.767101509085,
            "requires_live_quoter": False,
            "reasons": [
                "Read-only on-chain quoter validation is positive; calldata, slippage guard, flash-loan repayment constraint, and fork/static-call are still required before submission.",
            ],
        },
        "blocked_reasons": [
            "Read-only on-chain quoter validation is positive; calldata, slippage guard, flash-loan repayment constraint, and fork/static-call are still required before submission.",
        ],
        "can_submit_order": False,
        "market_id": "robinhood-dex-shadow",
    }




def _testnet_size_cap_attempt():
    return {
        "observed_at": "2026-08-31T04:38:21.763336+00:00",
        "created_at": "2026-08-31T04:38:21.763336+00:00",
        "network": "robinhood-testnet",
        "chain_id": 46630,
        "pair": "ETH -> 0X71178B -> 0XC9F9C8 -> ETH",
        "route_path": ["ETH", "0X71178B", "0XC9F9C8", "ETH"],
        "state": "live_verified",
        "execution_phase": "market_candidate",
        "trigger_block_number": 109454378,
        "quote": {
            "live_onchain_evidence": {
                "status": "live_verified",
                "executionTrust": "live_quoter_verified_readonly",
                "trustedForExecution": False,
                "readyForTransactionPreflight": True,
                "blockNumber": 109454378,
                "triggerBlockNumber": 109454378,
                "requestedBorrowInputAmountRaw": "1000000000000000000",
                "routeSymbols": ["ETH", "0X71178B", "0XC9F9C8", "ETH"],
                "validation": {
                    "allHopsQuoted": True,
                    "routeClosed": True,
                    "finalOutputSymbol": "ETH",
                    "profitableAfterCosts": True,
                    "meetsSubmissionProfitThreshold": True,
                },
                "blockers": [],
                "hops": [
                    {
                        "hop": 1,
                        "protocol": "uniswap_v4",
                        "quoteMethod": "v4_sdk_exact_input_single",
                        "pool": _pool_id("b"),
                        "fromSymbol": "ETH",
                        "toSymbol": "0X71178B",
                        "tokenIn": "0x0000000000000000000000000000000000000000",
                        "tokenOut": "0x71178bac73cbeb415514eb542a8995b82669778d",
                        "amountInRaw": "1000000000000000000",
                        "amountOutRaw": "4950000000000000000000",
                        "amountIn": "1",
                        "amountOut": "4950",
                        "inputDecimals": 18,
                        "outputDecimals": 18,
                        "fee": 10000,
                    },
                    {
                        "hop": 2,
                        "protocol": "uniswap_v4",
                        "quoteMethod": "v4_sdk_exact_input_single",
                        "pool": _pool_id("7"),
                        "fromSymbol": "0X71178B",
                        "toSymbol": "0XC9F9C8",
                        "tokenIn": "0x71178bac73cbeb415514eb542a8995b82669778d",
                        "tokenOut": "0xc9f9c86933092bbbfff3ccb4b105a4a94bf3bd4e",
                        "amountInRaw": "4950000000000000000000",
                        "amountOutRaw": "9870300000000000000000",
                        "amountIn": "4950",
                        "amountOut": "9870.3",
                        "inputDecimals": 18,
                        "outputDecimals": 18,
                        "fee": 30,
                    },
                    {
                        "hop": 3,
                        "protocol": "uniswap_v4",
                        "quoteMethod": "v4_sdk_exact_input_single",
                        "pool": _pool_id("c"),
                        "fromSymbol": "0XC9F9C8",
                        "toSymbol": "ETH",
                        "tokenIn": "0xc9f9c86933092bbbfff3ccb4b105a4a94bf3bd4e",
                        "tokenOut": "0x0000000000000000000000000000000000000000",
                        "amountInRaw": "9870300000000000000000",
                        "amountOutRaw": "1002000000000000000",
                        "amountIn": "9870.3",
                        "amountOut": "1.002",
                        "inputDecimals": 18,
                        "outputDecimals": 18,
                        "fee": 5,
                    },
                ],
            }
        },
        "precheck": {
            "status": "transaction_development_required",
            "ready_for_transaction_preflight": True,
            "trustedForExecution": False,
            "reasons": [],
        },
    }

def test_build_robinhood_chain_test_plan_marks_preflight_boundary():
    plan = build_robinhood_chain_test_plan(_sample_attempt())

    assert plan["chainTestScope"]["mode"] == "mainnet_fork_staticcall_only"
    assert plan["chainTestScope"]["canDeployTestnet"] is False
    assert plan["route"]["pathText"] == "USDG -> WETH -> VLAD -> WETH -> USDG"
    assert plan["route"]["hopCount"] == 4
    assert plan["evidence"]["status"] == "live_verified"
    assert plan["evidence"]["triggerBlockNumber"] == 45513946
    assert plan["evidence"]["readyForTransactionPreflight"] is True
    assert plan["decision"]["trustedForExecution"] is False
    assert plan["decision"]["canBroadcast"] is False
    assert plan["decision"]["canRunForkPreflight"] is True
    assert plan["preflightSpec"]["status"] == "ready_for_fork_staticcall"
    assert plan["preflightSpec"]["executionModel"]["routeExecutionMode"] == "mixed_protocol_executor"
    assert plan["preflightSpec"]["repaymentConstraint"]["repayAmountRaw"] == "1000500000"
    assert plan["preflightSpec"]["routeHops"][0]["minAmountOutRaw"] == "391338259040044337"
    assert any(item["id"] == "fork_staticcall" for item in plan["checklist"])
    assert plan["checklist"][0]["done"] is True
    assert "45513946" in plan["checklist"][0]["detail"]
    assert plan["checklist"][-1]["title"] == "归档实施方案与测试包"


def test_build_robinhood_chain_test_plan_falls_back_to_quote_hops_when_live_evidence_is_empty():
    attempt = deepcopy(_sample_attempt())
    attempt["quote"]["hops"] = deepcopy(attempt["quote"]["live_onchain_evidence"]["hops"])
    attempt["quote"]["live_onchain_evidence"]["hops"] = []
    attempt["quote"]["live_onchain_evidence"]["validation"]["allHopsQuoted"] = False
    attempt["quote"]["live_onchain_evidence"]["costModel"] = {}

    plan = build_robinhood_chain_test_plan(attempt)
    audit = audit_robinhood_chain_test_plan(plan)

    assert plan["route"]["hopCount"] == 4
    assert len(plan["preflightSpec"]["routeHops"]) == 4
    assert plan["preflightSpec"]["routeHops"][0]["amountInRaw"] == "1000000000"
    assert plan["preflightSpec"]["routeHops"][-1]["amountOutRaw"] == "7611915561"
    assert plan["preflightSpec"]["repaymentConstraint"]["borrowAmountRaw"] == "1000000000"
    assert plan["preflightSpec"]["repaymentConstraint"]["repayAmountRaw"] == "1000500000"
    assert plan["evidence"]["allHopsQuoted"] is True
    assert plan["evidence"]["finalOutputSymbol"] == "USDG"
    assert audit["ok"] is True

def test_robinhood_chain_test_plan_records_latest_snapshot_size_gate_block(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "updated_at": "2026-08-31T04:50:30.225613+00:00",
                "snapshots": {
                    "46630": {
                        "payload": {
                            "routes": [
                                {
                                    "pathText": "ETH -> 0X71178B -> 0XC9F9C8 -> ETH",
                                    "sizeAnalysis": {
                                        "maxExecutableAmountRaw": "498365714991916341",
                                        "executionSizeProbe": {
                                            "status": "liquidity_probe_complete",
                                        },
                                    },
                                    "quoteAnalysis": {
                                        "executableCandidate": False,
                                        "chainSubmissionCandidate": False,
                                    },
                                }
                            ]
                        }
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(chain_test_pack, "ROBINHOOD_TESTNET_OPPORTUNITY_SNAPSHOT", snapshot_path)

    plan = build_robinhood_chain_test_plan(_testnet_size_cap_attempt())

    assert plan["evidence"]["routeOriginalRequestedBorrowAmountRaw"] == 1000000000000000000
    assert plan["evidence"]["routeRequestedBorrowAmountRaw"] == 498365714991916341
    assert plan["evidence"]["routeMaxExecutableAmountRaw"] == 498365714991916341
    assert plan["evidence"]["routeExecutionSizeProbeExceeded"] is False
    assert plan["evidence"]["routeOriginalExecutionSizeProbeExceeded"] is True
    assert plan["evidence"]["routeOriginalExecutionSizeShortfallRaw"] == 501634285008083659
    assert plan["decision"]["readyForTransactionPreflight"] is True
    assert plan["preflightSpec"]["status"] == "ready_for_fork_staticcall"
    assert plan["decision"]["blockedReasons"] == []


def test_robinhood_chain_test_plan_surfaces_latest_snapshot_recommended_borrow_amount(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "updated_at": "2026-09-03T04:50:30.225613+00:00",
                "snapshots": {
                    "46630": {
                        "payload": {
                            "routes": [
                                {
                                    "pathText": "ETH -> 0X71178B -> 0XC9F9C8 -> ETH",
                                    "sizeAnalysis": {
                                        "recommendedBorrowInputAmountRaw": "250000000000000000",
                                        "maxExecutableAmountRaw": "498365714991916341",
                                        "executionSizeProbe": {
                                            "status": "liquidity_probe_complete",
                                        },
                                    },
                                    "quoteAnalysis": {
                                        "executableCandidate": True,
                                        "chainSubmissionCandidate": True,
                                    },
                                }
                            ]
                        }
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(chain_test_pack, "ROBINHOOD_TESTNET_OPPORTUNITY_SNAPSHOT", snapshot_path)
    attempt = _testnet_size_cap_attempt()
    attempt["quote"]["live_onchain_evidence"].pop("selectedBorrowInputAmountRaw", None)

    plan = build_robinhood_chain_test_plan(attempt)

    assert plan["evidence"]["routeRecommendedBorrowAmountRaw"] == 250000000000000000
    assert plan["evidence"]["routeSelectedBorrowAmountRaw"] == 250000000000000000
    assert plan["evidence"]["routeRequestedBorrowAmountRaw"] == 250000000000000000


def test_robinhood_chain_test_audit_rejects_size_gate_inconsistency(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "updated_at": "2026-08-31T04:50:30.225613+00:00",
                "snapshots": {
                    "46630": {
                        "payload": {
                            "routes": [
                                {
                                    "pathText": "ETH -> 0X71178B -> 0XC9F9C8 -> ETH",
                                    "sizeAnalysis": {
                                        "maxExecutableAmountRaw": "498365714991916341",
                                        "executionSizeProbe": {
                                            "status": "liquidity_probe_complete",
                                        },
                                    },
                                    "quoteAnalysis": {
                                        "executableCandidate": False,
                                        "chainSubmissionCandidate": False,
                                    },
                                }
                            ]
                        }
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(chain_test_pack, "ROBINHOOD_TESTNET_OPPORTUNITY_SNAPSHOT", snapshot_path)

    plan = build_robinhood_chain_test_plan(_testnet_size_cap_attempt())
    plan["evidence"]["routeRequestedBorrowAmountRaw"] = 1000000000000000000
    plan["evidence"]["routeExecutionSizeProbeExceeded"] = True
    plan["evidence"]["routeExecutionSizeShortfallRaw"] = 501634285008083659
    plan["decision"]["readyForTransactionPreflight"] = True
    plan["preflightSpec"]["status"] = "ready_for_fork_staticcall"

    audit = audit_robinhood_chain_test_plan(plan)
    codes = {issue["code"] for issue in audit["issues"]}

    assert audit["ok"] is False
    assert "route_execution_size_gate_exceeded_but_preflight_ready" in codes
    assert "route_execution_size_gate_exceeded_but_preflight_ready_status" in codes


def test_robinhood_chain_test_scope_only_enables_robinhood_family():
    mainnet = robinhood_chain_test_scope("robinhood", 4663)
    testnet = robinhood_chain_test_scope("robinhood-testnet", 46630)
    bnb = robinhood_chain_test_scope("bnb", 56)

    assert mainnet["enabled"] is True
    assert mainnet["canRunForkStaticcall"] is True
    assert mainnet["canDeployTestnet"] is False
    assert mainnet["canBroadcast"] is False
    assert testnet["enabled"] is True
    assert testnet["canRunForkStaticcall"] is True
    assert testnet["canDeployTestnet"] is True
    assert testnet["canBroadcast"] is False
    assert bnb["enabled"] is False
    assert bnb["disabledReason"] == "disabled_non_robinhood_chain_test_scope"


def test_plan_keeps_non_robinhood_attempt_out_of_chain_test_scope():
    attempt = _sample_attempt()
    attempt["network"] = "bnb"
    attempt["chain_id"] = 56

    plan = build_robinhood_chain_test_plan(attempt)
    audit = audit_robinhood_chain_test_plan(plan)

    assert plan["chainTestScope"]["enabled"] is False
    assert plan["chainTestScope"]["disabledReason"] == "disabled_non_robinhood_chain_test_scope"
    assert plan["decision"]["canRunForkPreflight"] is False
    assert plan["decision"]["canDeployTestnet"] is False
    assert audit["ok"] is True


def test_plan_infers_legacy_net_positive_below_min_profit():
    attempt = _sample_attempt()
    evidence = attempt["quote"]["live_onchain_evidence"]
    evidence["readyForTransactionPreflight"] = False
    evidence["validation"]["profitableAfterCosts"] = False
    evidence["validation"]["meetsSubmissionProfitThreshold"] = False
    evidence["validation"]["meetsOffchainProfitThreshold"] = False
    evidence["costModel"]["netProfitUsd"] = 0.612327
    evidence["costModel"]["minProfitUsd"] = 2.0

    plan = build_robinhood_chain_test_plan(attempt)

    assert plan["evidence"]["netPositiveAfterCosts"] is True
    assert plan["evidence"]["meetsSubmissionProfitThreshold"] is False
    assert plan["evidence"]["meetsOffchainProfitThreshold"] is False
    assert plan["decision"]["readyForTransactionPreflight"] is False
    assert plan["decision"]["canRunForkPreflight"] is False


def test_non_stable_chain_test_plan_uses_positive_x_profit_floor():
    attempt = _sample_attempt()
    attempt["network"] = "robinhood-testnet"
    attempt["chain_id"] = 46630
    attempt["pair"] = "ETH -> Y -> ETH"
    attempt["route_path"] = ["ETH", "Y", "ETH"]
    evidence = attempt["quote"]["live_onchain_evidence"]
    evidence["network"] = "robinhood-testnet"
    evidence["chainId"] = 46630
    evidence["routeSymbols"] = ["ETH", "Y", "ETH"]
    evidence["validation"] = {
        "allHopsQuoted": True,
        "routeClosed": True,
        "finalOutputSymbol": "ETH",
        "borrowAssetIsStable": False,
        "profitableAfterCosts": True,
        "netPositiveAfterCosts": True,
        "meetsSubmissionProfitThreshold": True,
        "meetsOffchainProfitThreshold": False,
    }
    evidence["costModel"] = {
        "profitBasis": "borrow_token",
        "borrowInputAmount": 1.0,
        "borrowInputSymbol": "ETH",
        "finalOutputAmount": 1.01,
        "finalOutputSymbol": "ETH",
        "flashLoanFeeBps": 5.0,
        "flashLoanFeeAmount": 0.0005,
        "submissionNetProfitAmount": 0.0095,
        "netProfitAmount": 0.0095,
        "netProfitAmountSymbol": "ETH",
        "netProfitAmountBps": 95.0,
        "finalOutputUsd": None,
        "netProfitUsd": None,
    }
    evidence["hops"] = [
        {
            "hop": 1,
            "protocol": "uniswap_v4",
            "quoteMethod": "uniswap_v4_sdk_exact_input_single",
            "pool": "0x" + "1" * 40,
            "fromSymbol": "ETH",
            "toSymbol": "Y",
            "tokenIn": "0x0000000000000000000000000000000000000000",
            "tokenOut": "0x" + "b" * 40,
            "amountInRaw": str(10**18),
            "amountOutRaw": str(10 * 10**18),
            "amountIn": "1",
            "amountOut": "10",
            "inputDecimals": 18,
            "outputDecimals": 18,
            "fee": 3000,
        },
        {
            "hop": 2,
            "protocol": "uniswap_v4",
            "quoteMethod": "uniswap_v4_sdk_exact_input_single",
            "pool": "0x" + "2" * 40,
            "fromSymbol": "Y",
            "toSymbol": "ETH",
            "tokenIn": "0x" + "b" * 40,
            "tokenOut": "0x0000000000000000000000000000000000000000",
            "amountInRaw": str(10 * 10**18),
            "amountOutRaw": str(101 * 10**16),
            "amountIn": "10",
            "amountOut": "1.01",
            "inputDecimals": 18,
            "outputDecimals": 18,
            "fee": 3000,
        },
    ]
    attempt["precheck"]["ready_for_transaction_preflight"] = True
    attempt["precheck"]["live_profit_percent"] = 0.95
    attempt["precheck"]["reference_profit_amount"] = None

    plan = build_robinhood_chain_test_plan(attempt)

    assert plan["decision"]["readyForTransactionPreflight"] is True
    assert plan["chainTestScope"]["mode"] == "testnet_deploy_readback_only"
    assert plan["decision"]["canRunForkPreflight"] is True
    assert plan["decision"]["canDeployTestnet"] is True
    assert plan["evidence"]["profitBasis"] == "borrow_token"
    assert plan["evidence"]["netProfitAmount"] == 0.0095
    assert plan["preflightSpec"]["repaymentConstraint"]["profitBasis"] == "borrow_token"
    assert plan["preflightSpec"]["repaymentConstraint"]["borrowAmountRaw"] == str(10**18)
    assert plan["preflightSpec"]["repaymentConstraint"]["repayAmountRaw"] == str(10**18 + 5 * 10**14)
    assert plan["preflightSpec"]["repaymentConstraint"]["minProfitRaw"] == "1"
    assert plan["preflightSpec"]["repaymentConstraint"]["gasReserveRaw"] == "0"
    assert plan["preflightSpec"]["repaymentConstraint"]["minProfitRule"] == "positive_x_profit_min_raw_unit"
    assert plan["preflightSpec"]["executionRequest"]["function"] == "requestV4FlashLoan"
    assert plan["preflightSpec"]["executionRequest"]["struct"] == "V4FlashAccountingRequest"
    assert plan["preflightSpec"]["executionRequest"]["borrowTokenIsNative"] is True
    assert plan["preflightSpec"]["executionRequest"]["requiresNativeValueForwarding"] is False
    assert plan["preflightSpec"]["executionRequest"]["transactionValueRaw"] == "0"
    assert plan["preflightSpec"]["executionModel"]["routeExecutionMode"] == "pool_manager_direct_swap_settle_take"
    assert plan["preflightSpec"]["executionModel"]["requiresDirectPoolManagerFlow"] is True
    assert plan["preflightSpec"]["executionModel"]["requiresUniversalRouter"] is False
    assert plan["preflightSpec"]["executionModel"]["requiresNativeValueForwarding"] is False
    assert plan["preflightSpec"]["executionRequest"]["swapValues"] == ["0", "0"]


def test_non_stable_three_hop_v4_chain_test_plan_uses_direct_pool_manager_flow():
    attempt = deepcopy(_sample_attempt())
    attempt["network"] = "robinhood-testnet"
    attempt["chain_id"] = 46630
    attempt["pair"] = "ETH -> A -> B -> ETH"
    attempt["route_path"] = ["ETH", "A", "B", "ETH"]
    evidence = attempt["quote"]["live_onchain_evidence"]
    evidence["network"] = "robinhood-testnet"
    evidence["chainId"] = 46630
    evidence["routeSymbols"] = ["ETH", "A", "B", "ETH"]
    evidence["validation"] = {
        "allHopsQuoted": True,
        "routeClosed": True,
        "finalOutputSymbol": "ETH",
        "borrowAssetIsStable": False,
        "profitableAfterCosts": True,
        "netPositiveAfterCosts": True,
        "meetsSubmissionProfitThreshold": True,
        "meetsOffchainProfitThreshold": False,
    }
    evidence["costModel"] = {
        "profitBasis": "borrow_token",
        "borrowInputAmount": 1.0,
        "borrowInputSymbol": "ETH",
        "finalOutputAmount": 1.01,
        "finalOutputSymbol": "ETH",
        "flashLoanFeeBps": 5.0,
        "flashLoanFeeAmount": 0.0005,
        "submissionNetProfitAmount": 0.0095,
        "netProfitAmount": 0.0095,
        "netProfitAmountSymbol": "ETH",
        "netProfitAmountBps": 95.0,
        "finalOutputUsd": None,
        "netProfitUsd": None,
    }
    evidence["hops"] = [
        {
            "hop": 1,
            "protocol": "uniswap_v4",
            "quoteMethod": "uniswap_v4_sdk_exact_input_single",
            "pool": "0x" + "1" * 40,
            "fromSymbol": "ETH",
            "toSymbol": "A",
            "tokenIn": "0x0000000000000000000000000000000000000000",
            "tokenOut": "0x" + "a" * 40,
            "amountInRaw": str(10**18),
            "amountOutRaw": str(2 * 10**18),
            "amountIn": "1",
            "amountOut": "2",
            "inputDecimals": 18,
            "outputDecimals": 18,
            "fee": 3000,
        },
        {
            "hop": 2,
            "protocol": "uniswap_v4",
            "quoteMethod": "uniswap_v4_sdk_exact_input_single",
            "pool": "0x" + "2" * 40,
            "fromSymbol": "A",
            "toSymbol": "B",
            "tokenIn": "0x" + "a" * 40,
            "tokenOut": "0x" + "b" * 40,
            "amountInRaw": str(2 * 10**18),
            "amountOutRaw": str(3 * 10**18),
            "amountIn": "2",
            "amountOut": "3",
            "inputDecimals": 18,
            "outputDecimals": 18,
            "fee": 3000,
        },
        {
            "hop": 3,
            "protocol": "uniswap_v4",
            "quoteMethod": "uniswap_v4_sdk_exact_input_single",
            "pool": "0x" + "3" * 40,
            "fromSymbol": "B",
            "toSymbol": "ETH",
            "tokenIn": "0x" + "b" * 40,
            "tokenOut": "0x0000000000000000000000000000000000000000",
            "amountInRaw": str(3 * 10**18),
            "amountOutRaw": str(101 * 10**16),
            "amountIn": "3",
            "amountOut": "1.01",
            "inputDecimals": 18,
            "outputDecimals": 18,
            "fee": 3000,
        },
    ]
    attempt["precheck"]["ready_for_transaction_preflight"] = True
    attempt["precheck"]["requires_live_quoter"] = False
    attempt["precheck"]["reference_profit_amount"] = None

    plan = build_robinhood_chain_test_plan(attempt)

    assert plan["route"]["hopCount"] == 3
    assert plan["preflightSpec"]["executionModel"]["routeExecutionMode"] == "pool_manager_direct_swap_settle_take"
    assert plan["preflightSpec"]["executionModel"]["allV4Hops"] is True
    assert plan["preflightSpec"]["executionModel"]["requiresDirectPoolManagerFlow"] is True
    assert plan["preflightSpec"]["executionRequest"]["swapValues"] == ["0", "0", "0"]
    assert len(plan["preflightSpec"]["routeHops"]) == 3


def test_write_robinhood_chain_test_package_creates_contract_notes(tmp_path):
    output_dir = tmp_path / "package"
    contract_dir = tmp_path / "contract" / "robinhood"

    plan = write_robinhood_chain_test_package(output_dir, _sample_attempt(), contract_dir=contract_dir)

    assert (output_dir / "manifest.json").exists()
    assert (output_dir / "summary.md").exists()
    assert (output_dir / "plan.json").exists()
    assert (output_dir / "checklist.json").exists()
    assert (contract_dir / "README.md").exists()
    assert (contract_dir / "contracts.json").exists()
    assert plan["contractHandoff"]["contractDir"] == str(contract_dir)


def test_write_robinhood_testnet_chain_test_package_uses_testnet_scope(tmp_path):
    attempt = _sample_attempt()
    attempt["network"] = "robinhood-testnet"
    attempt["chain_id"] = 46630
    attempt.pop("market_id", None)
    attempt["quote"]["live_onchain_evidence"]["network"] = "robinhood-testnet"
    attempt["quote"]["live_onchain_evidence"]["chainId"] = 46630
    output_dir = tmp_path / "runtime" / "evidence" / "robinhood-testnet" / "chain_test" / "latest"
    contract_dir = tmp_path / "contract" / "robinhood-testnet" / "DEX" / "config" / "chain-test-package"

    plan = write_robinhood_chain_test_package(output_dir, attempt, contract_dir=contract_dir)
    contracts = (contract_dir / "contracts.json").read_text(encoding="utf-8")
    notes = (contract_dir / "README.md").read_text(encoding="utf-8")

    assert plan["network"] == "robinhood-testnet"
    assert plan["chainId"] == 46630
    assert plan["marketId"] == "dex-arbitrage-46630-uniswap-v4"
    assert plan["legacyMarketId"] == "robinhood-testnet-dex-paused"
    assert plan["chainTestScope"]["mode"] == "testnet_deploy_readback_only"
    assert '"scope": "robinhood_testnet_46630_only"' in contracts
    assert '"defaultV4Quoter": "0x8dc178efb8111bb0973dd9d722ebeff267c98f94"' in contracts
    assert '"repaymentConstraint": {' in contracts
    assert '"executionRequest": {' in contracts
    assert '"executionModel": {' in contracts
    assert '"candidatePoolSupplyEvidence": null' in contracts
    assert "not_publicly_verified_on_testnet_46630" in contracts
    assert "- Chain: `robinhood-testnet`" in notes
    assert "Route execution mode" in notes


def test_audit_accepts_complete_preflight_plan():
    plan = build_robinhood_chain_test_plan(_sample_attempt())

    audit = audit_robinhood_chain_test_plan(plan)

    assert audit["ok"] is True
    assert audit["errorCount"] == 0
    assert audit["expectedHopCount"] == 4
    assert audit["actualHopCount"] == 4


def test_audit_rejects_broadcast_and_incomplete_hop_data():
    plan = build_robinhood_chain_test_plan(_sample_attempt())
    plan["decision"]["canBroadcast"] = True
    plan["preflightSpec"]["broadcastEnabled"] = True
    plan["preflightSpec"]["routeHops"][1]["amountOutRaw"] = ""

    audit = audit_robinhood_chain_test_plan(plan)

    codes = {issue["code"] for issue in audit["issues"]}
    assert audit["ok"] is False
    assert "broadcast_without_execution_trust" in codes
    assert "preflight_broadcast_enabled_without_trust" in codes
    assert "hop_required_field_missing" in codes


def test_plan_blocks_same_symbol_different_token_route():
    attempt = _sample_attempt()
    attempt["quote"]["live_onchain_evidence"]["hops"][2]["tokenIn"] = "0xfDBb27E971E72E38C41b1C772FB409C94dd92789"

    plan = build_robinhood_chain_test_plan(attempt)
    audit = audit_robinhood_chain_test_plan(plan)

    assert plan["route"]["tokenContinuous"] is False
    assert plan["decision"]["readyForTransactionPreflight"] is False
    assert plan["decision"]["canRunForkPreflight"] is False
    assert plan["preflightSpec"]["status"] == "blocked_token_discontinuity"
    assert audit["ok"] is False
    assert {issue["code"] for issue in audit["issues"]} == {"hop_token_chain_broken"}


def test_chain_test_package_rejects_mainnet_attempt_for_testnet_scope():
    attempt = _sample_attempt()

    try:
        _assert_attempt_scope(attempt, network="robinhood-testnet", chain_id=46630)
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected Robinhood mainnet attempt to be rejected for testnet scope")

    assert "network_scope_mismatch" in message
    assert "chain_id_scope_mismatch" in message


def test_chain_test_package_rejects_testnet_attempt_for_mainnet_scope():
    attempt = _sample_attempt()
    attempt["network"] = "robinhood-testnet"
    attempt["chain_id"] = 46630
    attempt["quote"]["live_onchain_evidence"]["network"] = "robinhood-testnet"
    attempt["quote"]["live_onchain_evidence"]["chainId"] = 46630

    try:
        _assert_attempt_scope(attempt, network="robinhood", chain_id=4663)
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected Robinhood testnet attempt to be rejected for mainnet scope")

    assert "network_scope_mismatch" in message
    assert "chain_id_scope_mismatch" in message




