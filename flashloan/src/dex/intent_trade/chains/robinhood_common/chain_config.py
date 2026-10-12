"""Robinhood mainnet/testnet anchor configuration.

The two networks share the same candidate/observation/loop semantics.  Only
chain identity, market id, wrapped native and the exact five anchor addresses
differ here; page builders and chain modules should import this module instead
of re-declaring the anchor set.
"""

from __future__ import annotations


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

ROBINHOOD_NETWORK = "robinhood"
ROBINHOOD_CHAIN_ID = 4663
ROBINHOOD_MARKET_ID = "dex-arbitrage-4663-uniswap-v4"

ROBINHOOD_TESTNET_NETWORK = "robinhood-testnet"
ROBINHOOD_TESTNET_CHAIN_ID = 46630
ROBINHOOD_TESTNET_MARKET_ID = "dex-arbitrage-46630-uniswap-v4"

MAINNET_ANCHOR_SYMBOLS = ("ETH", "USDG", "USDE", "TZ", "WETH")
MAINNET_ANCHOR_ADDRESS_BY_SYMBOL = {
    "ETH": ZERO_ADDRESS,
    "WETH": "0x0bd7d308f8e1639fab988df18a8011f41eacad73",
    "USDG": "0x5fc5360d0400a0fd4f2af552add042d716f1d168",
    "USDE": "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34",
    "TZ": "0xa8cf7c51eee671646ba440ffa180f970902fd2fb",
}

TESTNET_ANCHOR_SYMBOLS = ("ETH", "WETH", "TZ", "USDE", "USDG")
TESTNET_ANCHOR_ADDRESS_BY_SYMBOL = {
    "ETH": ZERO_ADDRESS,
    "WETH": "0x7943e237c7f95da44e0301572d358911207852fa",
    "TZ": "0x05a31b34a1bf8edb08cbfa2e9d860e6d0939313e",
    "USDE": "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34",
    "USDG": "0x5fc5360d0400a0fd4f2af552add042d716f1d168",
}

def configured_anchor_address_by_symbol(network: str) -> dict[str, str]:
    """Return the exact configured anchor address map for a Robinhood network."""
    if str(network or "").strip().lower() == ROBINHOOD_TESTNET_NETWORK:
        return dict(TESTNET_ANCHOR_ADDRESS_BY_SYMBOL)
    return dict(MAINNET_ANCHOR_ADDRESS_BY_SYMBOL)


__all__ = [
    "MAINNET_ANCHOR_ADDRESS_BY_SYMBOL",
    "MAINNET_ANCHOR_SYMBOLS",
    "ROBINHOOD_CHAIN_ID",
    "ROBINHOOD_MARKET_ID",
    "ROBINHOOD_NETWORK",
    "ROBINHOOD_TESTNET_CHAIN_ID",
    "ROBINHOOD_TESTNET_MARKET_ID",
    "ROBINHOOD_TESTNET_NETWORK",
    "TESTNET_ANCHOR_ADDRESS_BY_SYMBOL",
    "TESTNET_ANCHOR_SYMBOLS",
    "ZERO_ADDRESS",
    "configured_anchor_address_by_symbol",
]
