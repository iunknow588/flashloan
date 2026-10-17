from web3 import Web3

from dex.tools.build_robinhood_v4_address_evidence import build_v4_address_provenance


class _FakeEth:
    block_number = 12345

    def get_code(self, address, block_identifier="latest"):
        return b"\x01\x02\x03\x04"


class _FakeW3:
    eth = _FakeEth()


def _fake_explorer_probe(
    address,
    *,
    explorer_url,
    network,
    chain_id,
    timeout_seconds=8,
    transport=None,
):
    return {
        "ok": True,
        "network": network,
        "chainId": chain_id,
        "address": address,
        "explorerUrl": explorer_url,
        "verified": True,
        "contractName": "Contract",
    }


def test_v4_address_provenance_reads_code_and_stays_read_only():
    report = build_v4_address_provenance(
        network="robinhood-testnet",
        w3=_FakeW3(),
        explorer_probe=_fake_explorer_probe,
    )
    assert report["network"] == "robinhood-testnet"
    assert report["chainId"] == 46630
    assert report["contractCount"] == 5
    assert report["allContractsHaveCode"] is True
    assert report["executionEnabled"] is False
    assert report["broadcastEnabled"] is False
    expected_hash = Web3.keccak(b"\x01\x02\x03\x04").hex()
    for record in report["contracts"].values():
        assert record["hasCode"] is True
        assert record["codeHash"] == expected_hash
        assert record["explorer"]["ok"] is True
        assert record["explorer"]["verified"] is True


def test_v4_address_provenance_reports_missing_code():
    class _EmptyEth(_FakeEth):
        def get_code(self, address, block_identifier="latest"):
            return b""

    report = build_v4_address_provenance(
        network="robinhood",
        w3=type("FakeW3", (), {"eth": _EmptyEth()})(),
        explorer_probe=_fake_explorer_probe,
    )
    assert report["allContractsHaveCode"] is False
    assert len(report["missingCodeContracts"]) == 5
