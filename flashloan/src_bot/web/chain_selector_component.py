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


def render_chain_selector_component(
    *,
    variant: str = "app_shell",
    include_strategy_control: bool = False,
    selected_strategy_key: str | None = None,
    strategy_name: str | None = None,
    strategy_description: str | None = None,
    chain_options: list[dict[str, Any]] | None = None,
    selected_chain_key: str | None = None,
) -> str:
    normalized = str(variant or "app_shell").strip().lower()
    if normalized == "app_shell":
        options, signature = _render_chain_options(chain_options, selected_chain_key)
        signature_attr = (
            f' data-chain-selector-signature="{escape(signature, quote=True)}"'
            if signature
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
        return (
            '<div class="app-shell-selector" data-chain-selector="true" '
            'data-global-chain-selector="true" data-chain-selector-component="true">'
            f"{strategy_control}"
            '<div class="app-shell-protocols" data-chain-protocols="true" aria-live="polite"></div>'
            '<div class="app-shell-chain-control">'
            '<div class="app-shell-control-line">'
            '<span class="app-shell-label">Chain</span>'
            f'<select data-chain-selector-select="true"{signature_attr} aria-label="Select chain">{options}</select>'
            '</div>'
            '<div class="app-shell-selector-meta">'
            '<span data-chain-selection-meta>Loading</span>'
            '<span class="app-shell-gate" data-shell-gate>Reading execution gate</span>'
            "</div>"
            "</div>"
        )
    return (
        '<div class="chain-selector-shell" data-chain-selector '
        'data-chain-selector-component="true">'
        "<label>Chain</label>"
        "<span data-chain-selection-meta>Loading</span>"
        '<div data-chain-protocols="true"></div>'
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
