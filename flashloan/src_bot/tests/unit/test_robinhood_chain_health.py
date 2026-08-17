from core.robinhood_chain_health import probe_robinhood_rpc


def test_probe_robinhood_rpc_accepts_expected_chain_and_contract_code():
    calls = []

    def transport(_url, payload, _timeout):
        calls.append(payload)
        method = payload["method"]
        if method == "eth_chainId":
            return {"result": hex(4663)}
        if method == "eth_blockNumber":
            return {"result": hex(123456)}
        if method == "eth_getCode":
            return {"result": "0x60016001"}
        raise AssertionError(method)

    report = probe_robinhood_rpc("https://rpc.example", transport=transport)

    assert report["ok"] is True
    assert report["network"] == "robinhood"
    assert report["expectedChainId"] == 4663
    assert [call["method"] for call in calls] == [
        "eth_chainId",
        "eth_blockNumber",
        "eth_getCode",
        "eth_getCode",
    ]


def test_probe_robinhood_rpc_reports_wrong_chain_id():
    def transport(_url, payload, _timeout):
        method = payload["method"]
        if method == "eth_chainId":
            return {"result": hex(1)}
        if method == "eth_blockNumber":
            return {"result": hex(123)}
        return {"result": "0x6001"}

    report = probe_robinhood_rpc("https://rpc.example", transport=transport)

    assert report["ok"] is False
    chain_check = next(check for check in report["checks"] if check["name"] == "eth_chainId")
    assert chain_check["value"] == 1


def test_probe_robinhood_rpc_reports_missing_contract_code():
    def transport(_url, payload, _timeout):
        method = payload["method"]
        if method == "eth_chainId":
            return {"result": hex(4663)}
        if method == "eth_blockNumber":
            return {"result": hex(123)}
        return {"result": "0x"}

    report = probe_robinhood_rpc("https://rpc.example", transport=transport)

    assert report["ok"] is False
    assert any(check["name"] == "weth_code" and not check["ok"] for check in report["checks"])
