import pytest

from dex.cow_flashloan.network_scope import (
    CowNetworkScopeError,
    normalize_cow_network,
    normalize_cow_networks,
    supported_cow_networks,
)


def test_cow_network_scope_normalizes_aliases_and_deduplicates():
    assert normalize_cow_network("bsc") == "bnb"
    assert normalize_cow_networks(["bnb", "avalanche", "43114", "bnb"]) == (
        "bnb",
        "avalanche",
    )


def test_cow_network_scope_excludes_testnets_by_default():
    assert "sepolia" not in supported_cow_networks()
    assert "sepolia" in supported_cow_networks(include_testnet=True)
    with pytest.raises(CowNetworkScopeError, match="testnet"):
        normalize_cow_network("sepolia")


def test_cow_network_scope_rejects_non_cow_markets():
    with pytest.raises(CowNetworkScopeError, match="unsupported CoW network"):
        normalize_cow_network("robinhood")
