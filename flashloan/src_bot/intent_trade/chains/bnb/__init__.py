"""BNB Chain intent, static-pool, and quote-only modules."""

from intent_trade.chains.bnb.config import CHAIN_ID, NETWORK
from intent_trade.chains.bnb.intent import build_bnb_intent_draft, normalize_network

__all__ = [
    "CHAIN_ID",
    "NETWORK",
    "build_bnb_intent_draft",
    "normalize_network",
]
