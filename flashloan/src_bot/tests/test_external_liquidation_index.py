from execution.external_liquidation_index import (
    ExternalIndexConfig,
    external_index_config_from_env,
    fetch_external_borrower_accounts,
    merge_candidate_accounts,
    normalize_external_accounts,
)
from web3 import Web3


def test_normalize_external_accounts_extracts_graph_style_addresses():
    payload = {
        "data": {
            "borrows": [
                {
                    "user": {"id": "0x0000000000000000000000000000000000000001"},
                    "borrower": {"id": "0x0000000000000000000000000000000000000001"},
                    "onBehalfOf": {"id": "0x0000000000000000000000000000000000000002"},
                },
                {
                    "user": {"id": "bad"},
                    "borrower": {"id": "0x0000000000000000000000000000000000000003"},
                },
            ]
        }
    }

    assert normalize_external_accounts(payload, limit=10) == [
        Web3.to_checksum_address("0x0000000000000000000000000000000000000001"),
        Web3.to_checksum_address("0x0000000000000000000000000000000000000002"),
        Web3.to_checksum_address("0x0000000000000000000000000000000000000003"),
    ]


def test_fetch_external_borrower_accounts_uses_fetcher_and_marks_onchain_verification():
    payload = {
        "data": {
            "borrows": [
                {
                    "user": {"id": "0x0000000000000000000000000000000000000001"},
                    "borrower": {"id": "0x0000000000000000000000000000000000000002"},
                }
            ]
        }
    }
    captured = {}

    def fake_fetch(url, body, timeout_seconds):
        captured["url"] = url
        captured["body"] = body
        captured["timeout_seconds"] = timeout_seconds
        return payload

    result = fetch_external_borrower_accounts(
        pool_address="0x0000000000000000000000000000000000000009",
        from_block=123,
        to_block=456,
        config=ExternalIndexConfig(enabled=True, url="https://index.example", timeout_seconds=3.5, limit=7),
        fetch_json=fake_fetch,
    )

    assert captured["url"] == "https://index.example"
    assert captured["body"]["variables"]["pool"] == "0x0000000000000000000000000000000000000009"
    assert result["enabled"] is True
    assert result["requires_onchain_verification"] is True
    assert result["count"] == 2
    assert result["accounts"][0] == Web3.to_checksum_address("0x0000000000000000000000000000000000000001")


def test_external_index_config_from_env_normalizes_flags_and_limits():
    config = external_index_config_from_env(
        {
            "LIQUIDATION_EXTERNAL_INDEX_ENABLED": "true",
            "LIQUIDATION_EXTERNAL_INDEX_URL": "https://index.example",
            "LIQUIDATION_EXTERNAL_INDEX_TIMEOUT_SECONDS": "12.5",
            "LIQUIDATION_EXTERNAL_INDEX_LIMIT": "7",
            "LIQUIDATION_EXTERNAL_INDEX_METHOD": "get",
        }
    )

    assert config.enabled is True
    assert config.url == "https://index.example"
    assert config.timeout_seconds == 12.5
    assert config.limit == 7
    assert config.method == "GET"


def test_fetch_external_borrower_accounts_disabled_returns_empty_result():
    result = fetch_external_borrower_accounts(
        config=ExternalIndexConfig(enabled=False, url="https://index.example"),
    )

    assert result["enabled"] is False
    assert result["configured"] is True
    assert result["count"] == 0
    assert result["accounts"] == []
    assert result["requires_onchain_verification"] is True


def test_merge_candidate_accounts_deduplicates_onchain_and_external_sources():
    merged = merge_candidate_accounts(
        [
            "0x0000000000000000000000000000000000000001",
            "0x0000000000000000000000000000000000000002",
        ],
        [
            "0x0000000000000000000000000000000000000002",
            "0x0000000000000000000000000000000000000003",
        ],
        limit=10,
    )

    assert merged == [
        Web3.to_checksum_address("0x0000000000000000000000000000000000000001"),
        Web3.to_checksum_address("0x0000000000000000000000000000000000000002"),
        Web3.to_checksum_address("0x0000000000000000000000000000000000000003"),
    ]
