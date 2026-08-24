from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _function_block(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    end = source.index("\n  function ", start + 1)
    return source[start:end]


def test_chain_selector_navigation_refreshes_after_chain_change():
    script = _read("web/static/chain_selector.js")

    assert "history.replaceState" in script
    assert "applySelectionToCurrentUrl" in script
    assert "window.location.assign(target);" in script
    assert "window.location.reload" not in script
    assert 'getElementById("liquidationMarketSelect")' not in script


def test_chain_selector_renders_protocol_checkboxes_without_fetching_pool_data():
    script = _read("web/static/chain_selector.js")

    assert "function renderProtocolBadgeList" in script
    assert 'checkbox.type = "checkbox"' in script
    assert 'mount.setAttribute("data-chain-protocols-empty", "true");' in script
    assert 'mount.removeAttribute("data-chain-protocols-empty");' in script
    assert "mount.hidden = true" not in script
    assert 'window.dispatchEvent(new CustomEvent(PROTOCOL_EVENT' in script
    assert 'window.dispatchEvent(new CustomEvent("chainselectionchanged"' in script
    assert 'url.searchParams.set("protocols", protocolSignature(normalized));' in script
    assert 'return unique(protocols).sort().join(",");' in script
    assert 'fetch("/api/liquidation/borrow-pool' not in script


def test_chain_selector_renders_cow_network_checkboxes_for_cow_rail():
    script = _read("web/static/chain_selector.js")

    assert "function renderCowNetworkBadgeList" in script
    assert 'fetch("/api/binance-market/cow-config"' in script
    assert 'checkbox.setAttribute("data-cow-network-checkbox", "true");' in script
    assert "const labelText = item.label || item.network;" in script
    assert '"chain " + String(item.chain_id)' in script
    assert 'url.searchParams.set("cow_networks", normalized.join(","));' in script
    assert 'url.searchParams.delete("protocols");' in script
    assert 'window.dispatchEvent(new CustomEvent(COW_NETWORK_EVENT' in script
    assert 'protocolList = renderCowNetworkBadgeList(protocolMount, cowConfig, cowNetworks);' in script


def test_chain_selector_uses_chain_key_for_chain_options():
    script = _read("web/static/chain_selector.js")

    assert "if (selection.chain_key) return String(selection.chain_key);" in script
    assert 'return "chain:" + String(selection.chain_id);' in script
    assert "const value = selectionKey(item);" in script
    assert "return selectionKey(candidate) === option.value;" in script
    assert 'next.searchParams.set("market_id", String(selection && selection.market_id || ""));' not in script
    assert 'next.searchParams.delete("market_id");' in script
    assert '["chain_id", "workflow", "execution_mode", "scope", "protocols"]' in script
    assert 'candidate.market_id + ":"' not in script
    assert 'item.market_id + ":"' not in script


def test_chain_selector_dropdown_labels_are_chain_only():
    script = _read("web/static/chain_selector.js")
    render_select_options = _function_block(script, "renderSelectOptions")

    assert "item.display_name || item.market_id" in render_select_options
    assert '"ID " + item.chain_id' in render_select_options
    assert "item.protocol_summary" not in render_select_options
    assert "item.protocol" not in render_select_options
    assert "item.market_count" not in render_select_options


def test_dex_pages_refresh_after_chain_selection_change():
    for relative_path in [
        "web/templates/binance_market.html",
        "web/templates/dex_single_chain.html",
        "web/templates/robinhood_dex_candidates.html",
        "web/templates/liquidation_panel.html",
    ]:
        body = _read(relative_path)
        assert "chainselectionchanged" in body


def test_dex_overview_refreshes_on_any_chain_selection_change():
    body = _read("web/templates/binance_market.html")

    assert "chainselectionchanged" in body
    assert 'selected.feature || "").toLowerCase() !== "dex_arbitrage"' not in body
    assert "refresh(true).then(() => refreshCowSupport())" in body
