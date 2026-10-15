from __future__ import annotations

from dex.intent_trade.chains.robinhood_common.eth_pair_tokens_core import (
    MAINNET_CHAIN_ID,
    NETWORK,
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
