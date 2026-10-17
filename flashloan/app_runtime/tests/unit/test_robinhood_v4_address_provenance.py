from web3 import Web3

import dex.tools.build_robinhood_v4_address_evidence as address_tool
from dex.tools.build_robinhood_v4_address_evidence import (
    OFFICIAL_V4_SOURCE_URLS,
    V4_CONTRACT_KEYS,
    build_v4_address_provenance,
    _probe_source_url,
)


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


def _fake_source_url_probe(url):
    return {
        "url": url,
        "ok": True,
        "statusCode": 200,
        "error": None,
    }


def test_v4_address_provenance_reads_code_and_stays_read_only():
    report = build_v4_address_provenance(
        network="robinhood-testnet",
        w3=_FakeW3(),
        explorer_probe=_fake_explorer_probe,
        source_url_probe=_fake_source_url_probe,
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
        assert record["sourceUrlVerification"]
        assert all(item["ok"] is True for item in record["sourceUrlVerification"])
    assert report["sourceUrlVerification"]
    assert all(item["ok"] is True for item in report["sourceUrlVerification"])
    assert report["sourceUrlProbeMode"] == "probed"


def test_v4_address_provenance_source_urls_are_per_contract():
    report = build_v4_address_provenance(
        network="robinhood-testnet",
        w3=_FakeW3(),
        explorer_probe=_fake_explorer_probe,
        source_url_probe=_fake_source_url_probe,
    )
    contracts = report["contracts"]
    for label in V4_CONTRACT_KEYS:
        record = contracts[label]
        assert OFFICIAL_V4_SOURCE_URLS[label] in record["sourceUrls"]
        explorer_address_urls = [
            url
            for url in record["sourceUrls"]
            if url.startswith("https://explorer.testnet.chain.robinhood.com/address/")
        ]
        assert len(explorer_address_urls) == 1
        assert explorer_address_urls[0].split("/")[-1].lower() == record["address"].lower()
        for other_label in V4_CONTRACT_KEYS:
            if other_label == label:
                continue
            other_explorer_address_urls = [
                url
                for url in record["sourceUrls"]
                if url.startswith("https://explorer.testnet.chain.robinhood.com/address/")
                and url.split("/")[-1].lower()
                == contracts[other_label]["address"].lower()
            ]
            assert not other_explorer_address_urls
    assert report["generatedAt"].endswith("Z")


def test_v4_address_provenance_reports_missing_code():
    class _EmptyEth(_FakeEth):
        def get_code(self, address, block_identifier="latest"):
            return b""

    report = build_v4_address_provenance(
        network="robinhood",
        w3=type("FakeW3", (), {"eth": _EmptyEth()})(),
        explorer_probe=_fake_explorer_probe,
        probe_source_urls=False,
    )
    assert report["allContractsHaveCode"] is False
    assert len(report["missingCodeContracts"]) == 5


def test_probe_source_url_records_ok_and_error():
    ok = _probe_source_url("http://127.0.0.1:1/does-not-exist", timeout_seconds=1)
    assert ok["ok"] is False
    assert ok["url"] == "http://127.0.0.1:1/does-not-exist"
    assert ok["error"]


def test_v4_address_provenance_records_pending_source_url_probe():
    def failing_source_url_probe(url):
        return {
            "url": url,
            "ok": False,
            "statusCode": None,
            "error": "URLError: timeout",
        }

    report = build_v4_address_provenance(
        network="robinhood-testnet",
        w3=_FakeW3(),
        explorer_probe=_fake_explorer_probe,
        source_url_probe=failing_source_url_probe,
    )
    for record in report["contracts"].values():
        assert record["sourceUrlVerification"]
        assert all(item["ok"] is False for item in record["sourceUrlVerification"])
        assert all(item["error"] == "URLError: timeout" for item in record["sourceUrlVerification"])
    assert all(item["ok"] is False for item in report["sourceUrlVerification"])


def test_v4_address_provenance_skips_source_url_probe_by_default():
    report = build_v4_address_provenance(
        network="robinhood-testnet",
        w3=_FakeW3(),
        explorer_probe=_fake_explorer_probe,
        probe_source_urls=False,
    )
    assert report["sourceUrlProbeMode"] == "skipped_pending_network_probe"
    assert report["sourceUrlVerification"]
    assert all(
        item["error"] == "skipped_pending_network_probe"
        for item in report["sourceUrlVerification"]
    )
    for record in report["contracts"].values():
        assert record["sourceUrlVerification"]
        assert all(
            item["error"] == "skipped_pending_network_probe"
            for item in record["sourceUrlVerification"]
        )
