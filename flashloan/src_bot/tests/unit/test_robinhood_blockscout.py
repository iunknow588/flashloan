from intent_trade.chains.robinhood.blockscout import (
    probe_robinhood_blockscout_contract,
    probe_robinhood_official_contracts,
)


def test_blockscout_contract_probe_reports_verified_contract():
    requested = []

    def transport(url, timeout):
        requested.append((url, timeout))
        return 200, {"is_verified": True, "name": "WETH", "compiler_version": "v0.8.0"}

    report = probe_robinhood_blockscout_contract(
        "0x0000000000000000000000000000000000000001",
        transport=transport,
    )

    assert report["ok"] is True
    assert report["verified"] is True
    assert report["contractName"] == "WETH"
    assert requested[0][0].endswith("/api/v2/smart-contracts/0x0000000000000000000000000000000000000001")


def test_blockscout_contract_probe_rejects_invalid_address_without_http_call():
    report = probe_robinhood_blockscout_contract("not-an-address", transport=lambda *_args: (_ for _ in ()).throw(AssertionError()))

    assert report["ok"] is False
    assert report["error"] == "invalid_contract_address"


def test_blockscout_official_contract_probe_aggregates_contract_results():
    def transport(_url, _timeout):
        return 200, {"isVerified": True}

    report = probe_robinhood_official_contracts(transport=transport)

    assert report["ok"] is True
    assert set(report["contracts"]) == {"weth", "multicall"}
