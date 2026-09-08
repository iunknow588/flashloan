"""Shared constants for pool candidate normalization and filtering."""

from __future__ import annotations

import re


STABLE_SYMBOLS = {
    "AUSD", "BUSD", "DAI", "DAI.E", "EURC", "FDUSD", "FRAX", "FRAX.E",
    "GHO", "MIM", "SUSDE", "TUSD", "USDC", "USDC.E", "USDD", "USDE",
    "USDG", "USDT", "USDT.E",
}
FLASH_LOAN_NATIVE_PROTOCOLS = {"aave_v3", "venus_core", "morpho_blue"}
FLASH_SWAP_PROTOCOLS = {
    "uniswap_v2", "uniswap_v3", "uniswap_v4", "pancakeswap_v2", "pancakeswap_v3",
    "quickswap_v2", "traderjoe_v2", "balancer_v2", "curve",
}
DEX_POOL_PROTOCOLS = set(FLASH_SWAP_PROTOCOLS)
UNISWAPX_DUTCH_V3_CHAIN_IDS = {42161, 43114, 8453, 56, 4663, 4217, 130}
READABLE_SYMBOL_RE = re.compile(r"^[A-Z0-9.$_\-\u4e00-\u9fff()]+$")
MAX_READABLE_SYMBOL_LENGTH = 24
