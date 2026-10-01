from dex.intent_trade.chains.bnb import config


def test_bnb_rpc_config_separates_read_and_log_rpc_env(monkeypatch):
    monkeypatch.setenv("BNB_RPCS", "https://read-a.example,https://read-b.example")
    monkeypatch.setenv("BNB_LOG_RPCS", "https://logs-a.example,https://logs-b.example")

    read_urls = config.rpc_urls_from_env()
    log_urls = config.log_rpc_urls_from_env()

    assert read_urls[:2] == ("https://read-a.example", "https://read-b.example")
    assert "https://bsc-dataseed.bnbchain.org" in read_urls
    assert log_urls[:2] == ("https://logs-a.example", "https://logs-b.example")
    assert "https://bsc-dataseed.bnbchain.org" in log_urls


def test_bnb_log_rpc_config_uses_chain_json_defaults(monkeypatch):
    for name in (
        "BNB_LOG_RPCS",
        "BNB_LOG_RPC_URLS",
        "BSC_LOG_RPCS",
        "BSC_LOG_RPC_URLS",
        "BNB_EVENT_RPCS",
        "BNB_EVENT_RPC_URLS",
    ):
        monkeypatch.delenv(name, raising=False)

    urls = config.log_rpc_urls_from_env()

    assert urls[0] == "https://bsc-dataseed.bnbchain.org"
    assert "https://bsc.publicnode.com" in urls
