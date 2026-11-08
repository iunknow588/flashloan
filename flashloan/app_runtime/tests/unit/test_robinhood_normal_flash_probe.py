from dex.intent_trade.chains.robinhood_testnet.normal_flash_probe import (
    _sqlite_token_liquidity_evidence,
    resolve_robinhood_testnet_normal_flash_probe,
)


def _liquidity_payload(*, balance_raw: str = "2000000000000000000000"):
    return {
        "poolManager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
        "payload": {
            "flashAccountingSources": [
                {
                    "address": "0x5d523e2Fd857bD4E2039C2bbe5636A1b920E06b3",
                    "symbol": "USDT",
                    "decimals": 18,
                    "poolManagerBalanceRaw": balance_raw,
                    "poolManagerBalance": "2000",
                    "poolIds": ["0x" + "1" * 64],
                    "source": "unit_test_flash_accounting_sources",
                }
            ],
            "nativePoolManagerBalanceWei": "0",
        },
        "pools": [],
        "pool_keys": [],
    }


def test_robinhood_testnet_normal_flash_probe_resolves_token_and_amount_from_plan():
    plan = {
        "network": "robinhood-testnet",
        "chainId": 46630,
        "route": {
            "pathText": "USDT -> ETH -> USDT",
            "closed": True,
        },
        "preflightSpec": {
            "routeHops": [
                {
                    "tokenIn": "0x5d523e2Fd857bD4E2039C2bbe5636A1b920E06b3",
                    "fromSymbol": "USDT",
                    "amountInRaw": "1000000000000000000000",
                    "inputDecimals": 18,
                }
            ],
            "repaymentConstraint": {
                "borrowToken": "0x5d523e2Fd857bD4E2039C2bbe5636A1b920E06b3",
                "borrowSymbol": "USDT",
                "borrowAmountRaw": "1000000000000000000000",
            },
        },
    }

    report = resolve_robinhood_testnet_normal_flash_probe(
        chain_metadata={},
        contracts_metadata={
            "normalTestnetProbe": {
                "address": "0x1111111111111111111111111111111111111111",
                "deploymentFile": "contract/robinhood-testnet/DEX/deployments/robinhood-v4-flash-normal-probe-robinhoodTestnet.json",
            }
        },
        chain_test_plan=plan,
        v4_pool_liquidity=_liquidity_payload(),
    )

    assert report["measurementReady"] is True
    assert report["readyForNormalBroadcast"] is True
    assert report["borrowableLiquidity"]["ready"] is True
    assert report["token"]["address"] == "0x5d523e2fd857bd4e2039c2bbe5636a1b920e06b3"
    assert report["token"]["symbol"] == "USDT"
    assert report["amount"]["amount"] == "1000000000000000000000"
    assert report["amount"]["human"] == "1000"
    assert report["blockingReasons"] == []


def test_robinhood_testnet_normal_flash_probe_accepts_internal_probe_registration():
    plan = {
        "network": "robinhood-testnet",
        "chainId": 46630,
        "route": {"pathText": "USDT -> ETH -> USDT", "closed": True},
        "preflightSpec": {
            "routeHops": [
                {
                    "tokenIn": "0x5d523e2Fd857bD4E2039C2bbe5636A1b920E06b3",
                    "fromSymbol": "USDT",
                    "amountInRaw": "1000000000000000000000",
                    "inputDecimals": 18,
                }
            ],
            "repaymentConstraint": {
                "borrowToken": "0x5d523e2Fd857bD4E2039C2bbe5636A1b920E06b3",
                "borrowSymbol": "USDT",
                "borrowAmountRaw": "1000000000000000000000",
            },
        },
    }

    report = resolve_robinhood_testnet_normal_flash_probe(
        chain_metadata={},
        contracts_metadata={
            "normalTestnetProbe": {
                "address": "0x1111111111111111111111111111111111111111",
                "deploymentFile": "contract/robinhood-testnet/DEX/deployments/robinhood-v4-flash-normal-probe-robinhoodTestnet.json",
            }
        },
        chain_test_plan=plan,
        v4_pool_liquidity=_liquidity_payload(),
    )

    assert report["probe"]["address"] == "0x1111111111111111111111111111111111111111"
    assert report["readyForNormalBroadcast"] is True
    assert report["blockingReasons"] == []


def test_robinhood_testnet_normal_flash_probe_blocks_when_poolmanager_balance_is_insufficient():
    plan = {
        "network": "robinhood-testnet",
        "chainId": 46630,
        "route": {"pathText": "USDT -> ETH -> USDT", "closed": True},
        "preflightSpec": {
            "routeHops": [
                {
                    "tokenIn": "0x5d523e2Fd857bD4E2039C2bbe5636A1b920E06b3",
                    "fromSymbol": "USDT",
                    "amountInRaw": "1000000000000000000000",
                    "inputDecimals": 18,
                }
            ],
            "repaymentConstraint": {
                "borrowToken": "0x5d523e2Fd857bD4E2039C2bbe5636A1b920E06b3",
                "borrowSymbol": "USDT",
                "borrowAmountRaw": "1000000000000000000000",
            },
        },
    }

    report = resolve_robinhood_testnet_normal_flash_probe(
        chain_metadata={},
        contracts_metadata={},
        chain_test_plan=plan,
        v4_pool_liquidity=_liquidity_payload(balance_raw="999999999999999999"),
    )

    assert report["borrowableLiquidity"]["ready"] is False
    assert report["readyForNormalBroadcast"] is False
    assert "borrow_amount_exceeds_poolmanager_balance" in report["blockingReasons"]


def test_robinhood_testnet_normal_flash_probe_uses_sqlite_evidence_when_balance_is_not_verified():
    plan = {
        "network": "robinhood-testnet",
        "chainId": 46630,
        "route": {"pathText": "USDT -> ETH -> USDT", "closed": True},
        "preflightSpec": {
            "routeHops": [
                {
                    "tokenIn": "0x5d523e2Fd857bD4E2039C2bbe5636A1b920E06b3",
                    "fromSymbol": "USDT",
                    "amountInRaw": "1000000000000000000000",
                    "inputDecimals": 18,
                }
            ],
            "repaymentConstraint": {
                "borrowToken": "0x5d523e2Fd857bD4E2039C2bbe5636A1b920E06b3",
                "borrowSymbol": "USDT",
                "borrowAmountRaw": "1000000000000000000000",
            },
        },
    }

    report = resolve_robinhood_testnet_normal_flash_probe(
        chain_metadata={},
        contracts_metadata={},
        chain_test_plan=plan,
        v4_pool_liquidity={},
        sqlite_pool_evidence={
            "ready": False,
            "poolCount": 0,
            "reason": "no_verified_sqlite_liquidity_pool",
            "source": "sqlite_db",
        },
    )

    assert report["borrowableLiquidity"]["ready"] is False
    assert report["readyForNormalBroadcast"] is False
    assert "borrowable_liquidity_not_verified" in report["blockingReasons"]
    assert "sqlite_verified_liquidity_pool_not_available" in report["blockingReasons"]
    assert report["sqliteLiquidityEvidence"]["checked"] is True


def test_robinhood_testnet_normal_flash_probe_verified_balance_is_not_reversed_by_missing_sqlite_pool():
    plan = {
        "network": "robinhood-testnet",
        "chainId": 46630,
        "route": {"pathText": "USDT -> ETH -> USDT", "closed": True},
        "preflightSpec": {
            "routeHops": [
                {
                    "tokenIn": "0x5d523e2Fd857bD4E2039C2bbe5636A1b920E06b3",
                    "fromSymbol": "USDT",
                    "amountInRaw": "1000000000000000000000",
                    "inputDecimals": 18,
                }
            ],
            "repaymentConstraint": {
                "borrowToken": "0x5d523e2Fd857bD4E2039C2bbe5636A1b920E06b3",
                "borrowSymbol": "USDT",
                "borrowAmountRaw": "1000000000000000000000",
            },
        },
    }

    report = resolve_robinhood_testnet_normal_flash_probe(
        chain_metadata={},
        contracts_metadata={
            "normalTestnetProbe": {
                "address": "0x1111111111111111111111111111111111111111",
                "deploymentFile": "contract/robinhood-testnet/DEX/deployments/robinhood-v4-flash-normal-probe-robinhoodTestnet.json",
            }
        },
        chain_test_plan=plan,
        v4_pool_liquidity=_liquidity_payload(),
        sqlite_pool_evidence={
            "ready": False,
            "poolCount": 0,
            "reason": "no_verified_sqlite_liquidity_pool",
            "source": "sqlite_db",
        },
    )

    assert report["borrowableLiquidity"]["ready"] is True
    assert report["readyForNormalBroadcast"] is True
    assert report["blockingReasons"] == []
    assert report["sqliteLiquidityEvidence"]["checked"] is True


def test_robinhood_testnet_normal_flash_probe_sqlite_evidence_fails_closed_for_missing_token():
    evidence = _sqlite_token_liquidity_evidence(
        database=None,
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        token_address="",
    )

    assert evidence["ready"] is False
    assert evidence["poolCount"] == 0
    assert evidence["reason"] == "token_address_missing"
