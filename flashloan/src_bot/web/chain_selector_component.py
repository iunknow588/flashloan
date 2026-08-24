from __future__ import annotations

from html import escape


CHAIN_SELECTOR_SCRIPT_PATH = "/static/chain-selector.js"


def _chain_value(chain_id: int | None) -> str:
    return "" if chain_id is None else str(int(chain_id))


def render_chain_selector_component(*, variant: str = "app_shell") -> str:
    normalized = str(variant or "app_shell").strip().lower()
    if normalized == "app_shell":
        return (
            '<div class="app-shell-selector" data-chain-selector="true" '
            'data-global-chain-selector="true" data-chain-selector-component="true">'
            '<div class="app-shell-protocols" data-chain-protocols="true" aria-live="polite"></div>'
            '<div class="app-shell-chain-control">'
            '<span class="app-shell-label">Chain</span>'
            '<select aria-label="Select chain"></select>'
            "</div>"
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
) -> str:
    feature_value = escape(str(feature or "").strip().lower(), quote=True)
    market_value = escape(str(default_market_id or ""), quote=True)
    return (
        f'<script>window.ChainSelector && window.ChainSelector.init({{feature: "{feature_value}", '
        f'defaultMarketId: "{market_value}"'
        '}).catch(function(error){console.error("chain selector:", error);});</script>'
    )


def render_chain_selector_bootstrap(
    *,
    feature: str | None = None,
    default_market_id: str | None = None,
    default_chain_id: int | None = None,
    include_script_tag: bool = True,
    include_init: bool = True,
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
            )
        )
    return "".join(parts)

