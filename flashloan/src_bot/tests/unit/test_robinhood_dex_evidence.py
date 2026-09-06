from pathlib import Path

from dex.intent_trade.chains.robinhood.dex_evidence import (
    build_current_robinhood_candidates,
    build_robinhood_pool_records,
    write_robinhood_evidence_package,
)


def _report():
    return {
        "network": "robinhood",
        "chainId": 4663,
        "factory": "0x" + "f" * 40,
        "factoryCodeVerified": True,
        "sourceUrl": "https://docs.example/robinhood-factory",
        "pools": [
            {
                "pool": "0x" + "1" * 40,
                "token0": "0x" + "a" * 40,
                "token1": "0x" + "b" * 40,
                "fee": 500,
                "tickSpacing": 10,
                "poolCodeVerified": True,
                "factoryQueryVerified": True,
                "poolStateVerified": True,
                "blockNumber": 10,
            },
            {
                "pool": "0x" + "2" * 40,
                "token0": "0x" + "b" * 40,
                "token1": "0x" + "c" * 40,
                "fee": 500,
                "tickSpacing": 10,
                "poolCodeVerified": True,
                "factoryQueryVerified": True,
                "poolStateVerified": True,
                "blockNumber": 11,
            },
            {
                "pool": "0x" + "3" * 40,
                "token0": "0x" + "a" * 40,
                "token1": "0x" + "c" * 40,
                "fee": 500,
                "tickSpacing": 10,
                "poolCodeVerified": True,
                "factoryQueryVerified": True,
                "poolStateVerified": True,
                "blockNumber": 12,
            },
        ],
    }


def test_evidence_builder_filters_blacklisted_tokens_and_builds_observation_candidate():
    report = _report()
    registry = {
        "0x" + "a" * 40: {"symbol": "WETH"},
        "0x" + "b" * 40: {"symbol": "USDC"},
        "0x" + "c" * 40: {"symbol": "TOKEN"},
    }

    rows = build_robinhood_pool_records(
        report,
        token_registry=registry,
        token_allowlist={"0x" + "a" * 40, "0x" + "b" * 40},
        token_blacklist={"0x" + "c" * 40},
    )
    candidates = build_current_robinhood_candidates(rows)

    assert sum(row["status"] == "observable" for row in rows) == 1
    assert candidates == []
    assert all(row["executionEnabled"] is False for row in [candidate["evidence"] for candidate in candidates])


def test_evidence_package_contains_required_files(tmp_path: Path):
    report = _report()
    registry = {
        "0x" + "a" * 40: {"symbol": "WETH"},
        "0x" + "b" * 40: {"symbol": "USDC"},
        "0x" + "c" * 40: {"symbol": "TOKEN"},
    }
    rows = build_robinhood_pool_records(report, token_registry=registry, token_allowlist=set(registry))
    candidates = build_current_robinhood_candidates(rows)
    assert len(candidates) == 1
    output = write_robinhood_evidence_package(
        tmp_path / "run",
        discovery_report=report,
        pool_rows=rows,
        candidates=candidates,
        token_registry=registry,
        token_allowlist=set(registry),
        token_blacklist=set(),
    )

    assert (output / "source_urls.json").exists()
    assert (output / "contracts.json").exists()
    assert (output / "code_hashes.json").exists()
    assert (output / "factory_events.json").exists()
    assert (output / "token_allowlist.json").exists()
    assert (output / "token_risk_report.json").exists()
    assert (output / "pool_static_table.json").exists()
    assert (output / "quote_smoke_report.json").exists()
    assert (output / "manifest.json").exists()
    assert '"schemaVersion": "robinhood-dex-evidence-v1"' in (output / "manifest.json").read_text(encoding="utf-8")
    assert "Execution: disabled" in (output / "summary.md").read_text(encoding="utf-8")


def test_candidate_builder_keeps_multiple_fee_tier_pools_for_pair_arbitrage():
    report = _report()
    report["pools"] = [
        {
            "pool": "0x" + "1" * 40,
            "token0": "0x" + "a" * 40,
            "token1": "0x" + "b" * 40,
            "fee": 100,
            "tickSpacing": 1,
            "poolCodeVerified": True,
            "factoryQueryVerified": True,
            "poolStateVerified": True,
            "blockNumber": 10,
        },
        {
            "pool": "0x" + "2" * 40,
            "token0": "0x" + "a" * 40,
            "token1": "0x" + "b" * 40,
            "fee": 500,
            "tickSpacing": 10,
            "poolCodeVerified": True,
            "factoryQueryVerified": True,
            "poolStateVerified": True,
            "blockNumber": 11,
        },
    ]
    registry = {
        "0x" + "a" * 40: {"symbol": "WETH"},
        "0x" + "b" * 40: {"symbol": "USDG"},
    }

    rows = build_robinhood_pool_records(report, token_registry=registry, token_allowlist=set(registry))
    candidates = build_current_robinhood_candidates(rows)

    assert len(candidates) == 1
    assert candidates[0]["route_kind"] == "two_pool_same_pair"
    assert candidates[0]["pool_addresses"] == ["0x" + "1" * 40, "0x" + "2" * 40]
    assert candidates[0]["evidence"]["executionEnabled"] is False
