from intent_trade.chains.robinhood_testnet.normal_flash_probe import resolve_robinhood_testnet_normal_flash_probe


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
        contracts_metadata={},
        chain_test_plan=plan,
        v4_pool_liquidity={"poolManager": "0x8366a39CC670B4001A1121B8F6A443A643e40951", "pools": [], "pool_keys": []},
    )

    assert report["measurementReady"] is True
    assert report["readyForNormalBroadcast"] is True
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
                "deploymentFile": "contract/contracts-dex/deployments/robinhood-v4-flash-normal-probe-robinhoodTestnet.json",
            }
        },
        chain_test_plan=plan,
        v4_pool_liquidity={"poolManager": "0x8366a39CC670B4001A1121B8F6A443A643e40951", "pools": [], "pool_keys": []},
    )

    assert report["probe"]["address"] == "0x1111111111111111111111111111111111111111"
    assert report["readyForNormalBroadcast"] is True
    assert report["blockingReasons"] == []
