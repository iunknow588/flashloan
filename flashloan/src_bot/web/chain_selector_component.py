from __future__ import annotations

from html import escape
from typing import Any

from web.dex_strategy_selector_component import render_dex_strategy_selector_component


CHAIN_SELECTOR_SCRIPT_PATH = "/static/chain-selector.js"


def _chain_value(chain_id: int | None) -> str:
    return "" if chain_id is None else str(int(chain_id))


def _chain_option_value(option: dict[str, Any]) -> str:
    value = str(option.get("chain_key") or option.get("global_chain_id") or "").strip()
    if value:
        return value
    chain_id = option.get("chain_id")
    if chain_id is not None:
        try:
            return f"chain:{int(chain_id)}"
        except (TypeError, ValueError):
            return f"chain:{chain_id}"
    market_id = str(option.get("market_id") or "").strip()
    if market_id:
        return f"market:{market_id}"
    return ""


def _render_chain_options(
    chain_options: list[dict[str, Any]] | None,
    selected_chain_key: str | None,
) -> tuple[str, str]:
    rendered: list[str] = []
    signature_parts: list[str] = []
    selected_value = str(selected_chain_key or "").strip()
    for option in chain_options or []:
        if not isinstance(option, dict):
            continue
        value = _chain_option_value(option)
        if not value:
            continue
        signature_parts.append(value)
        display_name = str(option.get("display_name") or option.get("market_id") or value).strip()
        chain_id = option.get("chain_id")
        if chain_id is None:
            label = display_name
        else:
            try:
                label = f"{display_name} / ID {int(chain_id)}"
            except (TypeError, ValueError):
                label = f"{display_name} / ID {chain_id}"
        title_parts = [
            str(option.get("protocol_summary") or "").strip(),
            str(option.get("default_market_id") or "").strip(),
            str(option.get("representative_market_id") or "").strip(),
        ]
        title = " | ".join([part for part in title_parts if part])
        selected = " selected" if value == selected_value else ""
        title_attr = f' title="{escape(title, quote=True)}"' if title else ""
        rendered.append(
            f'<option value="{escape(value, quote=True)}"{selected}{title_attr}>{escape(label)}</option>'
        )
    if not rendered:
        rendered.append('<option value="" selected>加载中</option>')
    return "".join(rendered), "|".join(signature_parts)


def _normalize_protocol(value: object) -> str:
    return str(value or "").strip().lower()


def _unique_protocols(values: list[str] | None) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for value in values or []:
        protocol = _normalize_protocol(value)
        if not protocol or protocol in seen:
            continue
        seen.add(protocol)
        items.append(protocol)
    return items


def _protocol_signature(protocols: list[str] | None) -> str:
    return ",".join(sorted(_unique_protocols(protocols)))


def _render_protocol_badges(
    protocol_options: list[dict[str, Any]] | None,
    selected_protocols: list[str] | None,
) -> tuple[str, str, str]:
    normalized_options: list[dict[str, str]] = []
    for option in protocol_options or []:
        if not isinstance(option, dict):
            continue
        protocol = _normalize_protocol(option.get("protocol"))
        if not protocol:
            continue
        normalized_options.append(
            {
                "protocol": protocol,
                "display_name": str(option.get("display_name") or protocol).strip(),
            }
        )
    selected = set(_unique_protocols(selected_protocols))
    option_signature = "|".join(
        f'{item["protocol"]}:{item["display_name"]}' for item in normalized_options
    )
    selected_signature = _protocol_signature(selected_protocols)
    rendered: list[str] = []
    for item in normalized_options:
        protocol = item["protocol"]
        display_name = item["display_name"]
        active = " active" if protocol in selected else ""
        checked = " checked" if protocol in selected else ""
        rendered.append(
            f'<label class="protocol-chip{active}">'
            f'<input type="checkbox" value="{escape(protocol, quote=True)}"{checked} '
            f'aria-label="{escape(display_name, quote=True)}">'
            f'{escape(display_name)}'
            "</label>"
        )
    return "".join(rendered), option_signature, selected_signature


def render_chain_selector_component(
    *,
    variant: str = "app_shell",
    selected_feature: str | None = None,
    include_strategy_control: bool = False,
    selected_strategy_key: str | None = None,
    strategy_name: str | None = None,
    strategy_description: str | None = None,
    chain_options: list[dict[str, Any]] | None = None,
    selected_chain_key: str | None = None,
    protocol_options: list[dict[str, Any]] | None = None,
    selected_protocols: list[str] | None = None,
) -> str:
    normalized = str(variant or "app_shell").strip().lower()
    if normalized == "app_shell":
        options, signature = _render_chain_options(chain_options, selected_chain_key)
        protocol_badges, protocol_signature, selected_protocol_signature = _render_protocol_badges(
            protocol_options,
            selected_protocols,
        )
        signature_attr = (
            f' data-chain-selector-signature="{escape(signature, quote=True)}"'
            if signature
            else ""
        )
        protocol_signature_attr = (
            f' data-chain-protocol-signature="{escape(protocol_signature, quote=True)}"'
            if protocol_signature
            else ""
        )
        selected_protocol_attr = (
            f' data-chain-protocol-selection-signature="{escape(selected_protocol_signature, quote=True)}"'
            if selected_protocol_signature
            else ""
        )
        strategy_control = (
            render_dex_strategy_selector_component(
                variant="app_shell",
                selected_strategy_key=selected_strategy_key,
                strategy_name=strategy_name,
                strategy_description=strategy_description,
            )
            if include_strategy_control
            else ""
        )
        feature_value = str(selected_feature or "liquidation").strip().lower()
        bot_selected = " selected" if feature_value == "liquidation" else ""
        dex_selected = " selected" if feature_value == "dex_arbitrage" else ""
        feature_control = (
            '<div class="app-shell-feature-control" data-feature-selector="true">'
            '<div class="app-shell-control-line">'
            '<span class="app-shell-label">功能域</span>'
            '<select data-feature-selector-select="true" aria-label="切换功能域">'
            f'<option value="liquidation"{bot_selected}>BOT</option>'
            f'<option value="dex_arbitrage"{dex_selected}>DEX</option>'
            '</select>'
            '</div>'
            '<div class="app-shell-feature-meta" data-feature-selection-meta>'
            f'当前功能域 {escape("DEX" if feature_value == "dex_arbitrage" else "BOT")}'
            '</div>'
            '</div>'
        )
        return (
            '<div class="app-shell-selector" data-chain-selector="true" '
            'data-global-chain-selector="true" data-chain-selector-component="true">'
            f"{feature_control}"
            f"{strategy_control}"
            '<div class="app-shell-chain-control">'
            '<div class="app-shell-control-line">'
            '<span class="app-shell-label">链路</span>'
            f'<select data-chain-selector-select="true"{signature_attr} aria-label="Select chain">{options}</select>'
            '</div>'
            '<div class="app-shell-selector-meta">'
            '<span data-chain-selection-meta>Loading</span>'
            '<span class="app-shell-gate" data-shell-gate>Reading execution gate</span>'
            "</div>"
            "</div>"
            '<div class="app-shell-protocol-control">'
            '<div class="app-shell-protocol-line">'
            '<div class="app-shell-protocol-label">资金协议</div>'
            '<div class="app-shell-protocols" data-chain-selection-options="true" '
            f'aria-live="polite"{protocol_signature_attr}{selected_protocol_attr}>{protocol_badges}</div>'
            "</div>"
            '<div class="app-shell-protocols-meta" data-chain-selection-options-meta>Loading</div>'
            "</div>"
            "</div>"
        )
    return (
        '<div class="chain-selector-shell" data-chain-selector '
        'data-chain-selector-component="true">'
        "<label>Chain</label>"
        "<span data-chain-selection-meta>Loading</span>"
        '<div data-chain-selection-options="true"></div>'
        "</div>"
    )


def render_chain_selector_default_script(
    *,
    default_market_id: str | None = None,
    default_chain_id: int | None = None,
) -> str:
    market_value = escape(str(default_market_id or ""), quote=True)
    chain_value = escape(_chain_value(default_chain_id), quote=True)
    return (
        "<script>"
        f'window.__CHAIN_SELECTOR_DEFAULT__={{market_id:"{market_value}",chain_id:"{chain_value}"}};'
        "</script>"
    )


def render_chain_selector_script_tag() -> str:
    return f'<script src="{CHAIN_SELECTOR_SCRIPT_PATH}"></script>'


def render_chain_selector_init_script(
    *,
    feature: str | None = None,
    default_market_id: str | None = None,
    include_chain_config: bool = False,
) -> str:
    feature_value = escape(str(feature or "").strip().lower(), quote=True)
    market_value = escape(str(default_market_id or ""), quote=True)
    include_chain_config_value = "true" if include_chain_config else "false"
    return (
        f'<script>window.ChainSelector && window.ChainSelector.init({{feature: "{feature_value}", '
        f'defaultMarketId: "{market_value}", includeChainConfig: {include_chain_config_value}'
        '}).catch(function(error){console.error("chain selector:", error);});</script>'
    )


def render_chain_selector_bootstrap(
    *,
    feature: str | None = None,
    default_market_id: str | None = None,
    default_chain_id: int | None = None,
    include_script_tag: bool = True,
    include_init: bool = True,
    include_chain_config: bool = False,
) -> str:
    parts = [
        render_chain_selector_default_script(
            default_market_id=default_market_id,
            default_chain_id=default_chain_id,
        )
    ]
    if include_script_tag:
        parts.append(render_chain_selector_script_tag())
    if include_init:
        parts.append(
            render_chain_selector_init_script(
                feature=feature,
                default_market_id=default_market_id,
                include_chain_config=include_chain_config,
            )
        )
    return "".join(parts)
