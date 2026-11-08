from __future__ import annotations

from dex.intent_trade.chains.robinhood_common.eth_pair_tokens_core import (
    MAINNET_CHAIN_ID,
    NETWORK,
    _TOKEN_METADATA_CACHE,
    _load_v4_token_metadata,
    eth_tokens_from_index,
)


def _eth_index(tokens: list[tuple[str, str]]) -> dict:
    zero = "0x0000000000000000000000000000000000000000"
    pools = []
    for index, (address, symbol) in enumerate(tokens, start=1):
        pools.append(
            {
                "poolId": f"0x{index:064x}",
                "poolManager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
                "token0": zero,
                "token1": address,
                "token0Symbol": "ETH",
                "token1Symbol": symbol,
                "token0Decimals": 18,
                "token1Decimals": 18,
                "fee": 3000,
                "tickSpacing": 60,
                "hooks": zero,
            }
        )
    return {"pools": pools}


def test_eth_tokens_from_index_uses_chain_id_for_metadata_lookup() -> None:
    usde = "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34"
    token = "0x1111111111111111111111111111111111111111"
    index = _eth_index([(usde, "USDE"), (token, "TOKEN")])

    rows = eth_tokens_from_index(index, network=NETWORK, chain_id=MAINNET_CHAIN_ID)

    addresses = {row["address"] for row in rows}
    assert usde in addresses
    assert token in addresses
    assert {row["symbol"] for row in rows} >= {"USDE", "TOKEN"}


def test_metadata_lookup_is_sqlite_first_and_chain_scoped(monkeypatch, tmp_path) -> None:
    _TOKEN_METADATA_CACHE.clear()
    mainnet_db_rows = {
        "0x1111111111111111111111111111111111111111": {
            "address": "0x1111111111111111111111111111111111111111",
            "symbol": "MAINNET_ONLY",
            "decimals": 18,
            "metadataOk": True,
            "source": "sqlite_db",
        }
    }

    def _fake_sqlite_metadata(*, network: str, chain_id: int) -> dict:
        if network == NETWORK and int(chain_id) == MAINNET_CHAIN_ID:
            return mainnet_db_rows
        return {}

    monkeypatch.setattr(
        "dex.intent_trade.chains.robinhood_common.eth_pair_tokens_core._load_sqlite_v4_token_metadata",
        _fake_sqlite_metadata,
    )

    mainnet = _load_v4_token_metadata(network=NETWORK, chain_id=MAINNET_CHAIN_ID)
    testnet = _load_v4_token_metadata(network="robinhood-testnet", chain_id=46630)

    assert mainnet["0x1111111111111111111111111111111111111111"]["symbol"] == "MAINNET_ONLY"
    assert "0x1111111111111111111111111111111111111111" not in testnet
    assert _TOKEN_METADATA_CACHE[(NETWORK, MAINNET_CHAIN_ID)] is mainnet_db_rows
    _TOKEN_METADATA_CACHE.clear()


def test_metadata_lookup_falls_back_to_json_only_when_sqlite_is_empty(
    monkeypatch,
    tmp_path,
) -> None:
    _TOKEN_METADATA_CACHE.clear()
    token = "0x2222222222222222222222222222222222222222"
    json_path = tmp_path / "v4_token_metadata.json"
    json_path.write_text(
        '{"tokens":{"%s":{"symbol":"JSON_FALLBACK","decimals":6}}}' % token,
        encoding="utf-8",
    )
    import dex.intent_trade.chains.robinhood_common.eth_pair_tokens_core as core

    monkeypatch.setattr(core, "_load_sqlite_v4_token_metadata", lambda **kwargs: {})
    monkeypatch.setattr(core, "default_v4_token_metadata_path", lambda **kwargs: json_path)

    rows = _load_v4_token_metadata(network=NETWORK, chain_id=MAINNET_CHAIN_ID)
    assert rows[token]["symbol"] == "JSON_FALLBACK"
    _TOKEN_METADATA_CACHE.clear()
