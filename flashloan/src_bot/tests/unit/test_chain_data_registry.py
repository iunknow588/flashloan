from core.chain_data_registry import (
    CHAIN_DATA_ROOT,
    chain_config_summary,
    chain_contracts,
    chain_data_audit,
    chain_data_by_chain_id,
    chain_data_by_network,
    chain_data_file_path,
    chain_default_protocol_preferences,
    default_chain_scope,
    chain_protocol_inventory,
    chain_supported_protocol_count,
    chain_supported_protocols,
    default_flash_loan_pool_scope,
    default_market_id_for_feature,
    global_chain_id,
    load_chain_flow_specs,
    load_chain_data,
    processing_flow_for_chain,
    processing_handler_for_chain,
)


def test_chain_data_registry_loads_unique_chain_files():
    rows = load_chain_data()
    flows = load_chain_flow_specs()
    chain_ids = [row["chain_id"] for row in rows]
    networks = [row["network"] for row in rows]
    flow_chain_ids = [row["chain_id"] for row in flows]
    flow_networks = [row["network"] for row in flows]

    assert len(rows) >= 7
    assert len(chain_ids) == len(set(chain_ids))
    assert len(networks) == len(set(networks))
    assert len(flows) == len(rows)
    assert set(flow_chain_ids) == set(chain_ids)
    assert set(flow_networks) == set(networks)
    assert {"robinhood", "robinhood-testnet", "avalanche", "avalanche-testnet"}.issubset(networks)


def test_each_chain_directory_has_data_config_and_flow_spec():
    directories = [path for path in CHAIN_DATA_ROOT.iterdir() if path.is_dir()]

    assert directories
    for directory in directories:
        assert (directory / "chain.json").exists(), directory
        assert (directory / "flows.json").exists(), directory


def test_core_directory_does_not_own_chain_named_modules():
    core_dir = CHAIN_DATA_ROOT.parent / "core"
    chain_names = ("robinhood", "bnb", "avalanche", "polygon", "ethereum")
    chain_named_files = [
        path.name
        for path in core_dir.glob("*.py")
        if any(chain_name in path.stem for chain_name in chain_names)
    ]

    assert chain_named_files == []


def test_robinhood_mainnet_and_testnet_have_separate_data_and_handlers():
    mainnet = chain_data_by_network("robinhood")
    testnet = chain_data_by_network("robinhood-testnet")
    mainnet_flash_flow = processing_flow_for_chain(4663, "flash_loan_pools")

    assert mainnet["chain_id"] == 4663
    assert testnet["chain_id"] == 46630
    assert mainnet["data_file"].endswith("chains\\robinhood\\chain.json") or mainnet["data_file"].endswith("chains/robinhood/chain.json")
    assert testnet["data_file"].endswith("chains\\robinhood-testnet\\chain.json") or testnet["data_file"].endswith("chains/robinhood-testnet/chain.json")
    assert "uniswap_v4" in mainnet["flash_loan"]["candidate_protocols"]
    assert "uniswap_v4" in mainnet_flash_flow["allowed_protocols"]
    assert processing_handler_for_chain(4663, "flash_loan_pools") != processing_handler_for_chain(
        46630,
        "flash_loan_pools",
    )
    assert processing_handler_for_chain(46630, "flash_loan_pools") == (
        "intent_trade.chains.robinhood_testnet.flash_loan_pools:flash_loan_pool_protocol_view"
    )


def test_flash_loan_pool_default_scope_is_robinhood_testnet_v4():
    scope = default_flash_loan_pool_scope()

    assert scope == {
        "network": "robinhood-testnet",
        "chain_id": 46630,
        "market_id": "robinhood-testnet-dex-paused",
        "protocol": "uniswap_v4",
    }
    assert chain_default_protocol_preferences()[46630] == ["uniswap_v4"]
    assert chain_contracts("robinhood-testnet")["v4_pool_manager"] == "0x8366a39CC670B4001A1121B8F6A443A643e40951"


def test_feature_default_scope_comes_from_chain_registry():
    scope = default_chain_scope("dex_arbitrage")

    assert scope["network"] == "robinhood-testnet"
    assert scope["chain_id"] == 46630
    assert scope["market_id"] == "robinhood-testnet-dex-paused"
    assert default_market_id_for_feature("dex_arbitrage") == "robinhood-testnet-dex-paused"
    assert default_market_id_for_feature("liquidation") == "robinhood-testnet-liquidation-shadow"


def test_global_chain_id_and_config_summary_are_chain_owned():
    summary = chain_config_summary(chain_id=46630)
    data_independence = summary["data_independence"]
    processing_independence = summary["processing_independence"]

    assert global_chain_id(chain_id=46630, network="robinhood-testnet") == "chain:46630"
    assert global_chain_id(network="hyperliquid") == "network:hyperliquid"
    assert summary["global_chain_id"] == "chain:46630"
    assert summary["chain_key"] == "chain:46630"
    assert summary["network"] == "robinhood-testnet"
    assert summary["chainId"] == 46630
    assert summary["features"]["dex_arbitrage"]["default_market_id"] == "robinhood-testnet-dex-paused"
    assert summary["flash_loan"]["candidate_protocols"] == ["uniswap_v4"]
    assert summary["contracts"]["v4_pool_manager"] == "0x8366a39CC670B4001A1121B8F6A443A643e40951"
    assert "flash_loan_pools" in summary["flow_names"]
    assert summary["processing_flows"]["flash_loan_pools"]["allowed_protocols"] == ["uniswap_v4"]
    assert data_independence["all_flow_data_files_declared"] is True
    assert data_independence["data_file_keys"] == [
        "chain_test",
        "lending_liquidity",
        "market_observation",
        "normal_testnet_probe",
        "predeploy_readiness",
        "v4_pool_index",
        "v4_pool_liquidity",
    ]
    assert data_independence["flow_data_file_keys"] == [
        "chain_test",
        "lending_liquidity",
        "market_observation",
        "predeploy_readiness",
        "v4_pool_index",
        "v4_pool_liquidity",
    ]
    assert data_independence["isolation"]["forbidden_source_networks"] == [
        "avalanche",
        "avalanche-testnet",
        "bnb",
        "ethereum",
        "polygon",
        "robinhood",
    ]
    assert data_independence["isolation"]["forbidden_source_chain_ids"] == [1, 56, 137, 4663, 43113, 43114]
    assert processing_independence["all_flows_have_handlers"] is True
    assert processing_independence["all_flows_require_scope_match"] is True
    assert processing_independence["flow_names"] == [
        "dex_overview",
        "dex_pool_candidates",
        "dex_quote_request",
        "dex_quote_routes",
        "flash_loan_pools",
        "lending_liquidity",
        "market_observation",
        "predeploy_readiness",
    ]


def test_dex_pool_candidate_handlers_are_declared_per_chain():
    expected = {
        1: "intent_trade.chains.ethereum.dex_pool_candidates:dex_pool_candidates_payload",
        56: "intent_trade.chains.bnb.dex_pool_candidates:dex_pool_candidates_payload",
        137: "intent_trade.chains.polygon.dex_pool_candidates:dex_pool_candidates_payload",
        43113: "intent_trade.chains.avalanche_testnet.dex_pool_candidates:dex_pool_candidates_payload",
        43114: "intent_trade.chains.avalanche.dex_pool_candidates:dex_pool_candidates_payload",
        4663: "intent_trade.chains.robinhood.dex_pool_candidates:dex_pool_candidates_payload",
        46630: "intent_trade.chains.robinhood_testnet.dex_pool_candidates:dex_pool_candidates_payload",
    }

    for chain_id, handler in expected.items():
        assert processing_handler_for_chain(chain_id, "dex_pool_candidates") == handler
        flow = processing_flow_for_chain(chain_id, "dex_pool_candidates")
        assert flow["handler"] == handler
        assert flow["requires_scope_match"] is True


def test_supported_protocol_counts_are_owned_by_chain_config():
    inventory = {item["network"]: item for item in chain_protocol_inventory()}

    assert inventory["robinhood-testnet"]["supportedProtocolCount"] == 1
    assert inventory["robinhood-testnet"]["candidateProtocols"] == ["uniswap_v4"]
    assert chain_supported_protocol_count("robinhood-testnet") == 1
    assert chain_supported_protocols("robinhood-testnet") == ["uniswap_v4"]
    assert chain_supported_protocols("robinhood-testnet", protocol_set="default") == ["uniswap_v4"]
    assert inventory["robinhood"]["supportedProtocolCount"] == 5
    assert "uniswap_v4" in inventory["robinhood"]["candidateProtocols"]


def test_flow_allowed_protocols_are_subsets_of_chain_supported_protocols():
    for row in load_chain_data():
        supported = set(row["flash_loan"]["candidate_protocols"])
        spec = next(item for item in load_chain_flow_specs() if item["network"] == row["network"])
        for flow in spec["flows"].values():
            allowed = set(flow.get("allowed_protocols") or [])
            assert allowed <= supported


def test_chain_data_file_path_resolves_from_chain_owner_file():
    path = chain_data_file_path("robinhood-testnet", "lending_liquidity")
    v4_path = chain_data_file_path("robinhood-testnet", "v4_pool_liquidity")

    assert path is not None
    assert path.as_posix().endswith("flashloan/src_bot/runtime/evidence/robinhood-testnet/lending_liquidity/latest.json")
    assert v4_path is not None
    assert v4_path.as_posix().endswith("flashloan/src_bot/runtime/evidence/robinhood-testnet/v4_pool_liquidity/latest.json")


def test_chain_data_audit_enforces_robinhood_testnet_isolation():
    audit = chain_data_audit()
    testnet = chain_data_by_chain_id(46630)
    flash_flow = processing_flow_for_chain(46630, "flash_loan_pools")

    assert audit["ok"] is True
    assert audit["independenceOk"] is True
    assert audit["issues"] == []
    assert audit["flowSpecCount"] == audit["chainCount"]
    assert audit["protocolInventory"]
    assert len(audit["independence"]) == audit["chainCount"]
    assert all(row["data"]["isolation"]["forbidden_networks_complete"] for row in audit["independence"])
    assert all(row["data"]["isolation"]["forbidden_chain_ids_complete"] for row in audit["independence"])
    assert all(row["processing"]["all_flows_require_scope_match"] for row in audit["independence"])
    assert all(row["processing"]["all_flow_handlers_are_chain_owned"] for row in audit["independence"])
    assert testnet["flash_loan"]["candidate_protocols"] == ["uniswap_v4"]
    assert testnet["flash_loan"]["supports_aave_or_morpho_flash_loans"] is False
    assert flash_flow["allowed_protocols"] == ["uniswap_v4"]
    assert flash_flow["requires_lending_pool"] is False
    assert flash_flow["mainnet_evidence_allowed"] is False
