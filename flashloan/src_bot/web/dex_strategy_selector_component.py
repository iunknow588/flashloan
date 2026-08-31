from __future__ import annotations

from html import escape

from market.binance_market.dex_strategy_registry import (
    dex_strategy_definition,
    dex_strategy_definitions,
    resolve_dex_strategy_key,
)


DEX_STRATEGY_SELECTOR_SCRIPT_PATH = "/static/dex-strategy-selector.js"


def _render_strategy_options(selected_strategy_key: str | None) -> str:
    resolved_selected = resolve_dex_strategy_key(selected_strategy_key)
    options: list[str] = []
    for definition in dex_strategy_definitions():
        key = str(definition.get("key") or "").strip().lower()
        if not key:
            continue
        display_name = str(definition.get("display_name") or key).strip()
        description = str(definition.get("description") or "").strip()
        default_path = str(definition.get("default_path") or "").strip()
        title = " | ".join([value for value in (description, default_path) if value])
        selected = " selected" if key == resolved_selected else ""
        options.append(
            f'<option value="{escape(key, quote=True)}"{selected} '
            f'title="{escape(title, quote=True)}">{escape(display_name)}</option>'
        )
    return "".join(options)


def _render_strategy_meta(
    *,
    strategy_name: str | None = None,
    strategy_description: str | None = None,
    strategy_key: str | None = None,
) -> str:
    definition = dex_strategy_definition(strategy_key) if strategy_key else None
    name = str(strategy_name or (definition or {}).get("display_name") or "策略信息加载中").strip()
    description = str(strategy_description or (definition or {}).get("description") or "").strip()
    key = str((definition or {}).get("key") or strategy_key or "").strip()
    default_path = str((definition or {}).get("default_path") or "").strip()
    summary_title = " | ".join([value for value in (key, default_path) if value])
    return (
        '<div class="app-shell-dex-strategy-meta" id="app-shell-dex-strategy-summary" data-dex-strategy-meta '
        f'aria-live="polite" aria-atomic="true" title="{escape(summary_title, quote=True)}">'
        f'<span class="app-shell-dex-strategy-name" data-dex-strategy-name>{escape(name)}</span>'
        f'<span class="app-shell-dex-strategy-desc" data-dex-strategy-description>{escape(description)}</span>'
        "</div>"
    )


def render_dex_strategy_selector_component(
    *,
    variant: str = "app_shell",
    selected_strategy_key: str | None = None,
    strategy_name: str | None = None,
    strategy_description: str | None = None,
) -> str:
    normalized = str(variant or "app_shell").strip().lower()
    if normalized == "app_shell":
        options = _render_strategy_options(selected_strategy_key)
        return (
            '<div class="app-shell-dex-strategy-control" data-dex-strategy-selector="true" '
            'data-dex-strategy-component="true">'
            '<div class="app-shell-control-line">'
            '<span class="app-shell-label">策略</span>'
            f'<select data-dex-strategy-select="true" aria-label="Select DEX strategy" aria-describedby="app-shell-dex-strategy-summary">{options}</select>'
            '</div>'
            f'{_render_strategy_meta(strategy_name=strategy_name, strategy_description=strategy_description, strategy_key=selected_strategy_key)}'
            "</div>"
        )
    return (
        '<div class="dex-strategy-selector-shell" data-dex-strategy-selector="true" '
        'data-dex-strategy-component="true">'
        "<label>策略</label>"
        '<select aria-label="Select DEX strategy"></select>'
        "</div>"
    )


def render_dex_strategy_default_script(
    *,
    selected_strategy_key: str | None = None,
    selected_strategy_path: str | None = None,
    current_strategy_key: str | None = None,
) -> str:
    strategy_key = escape(str(selected_strategy_key or ""), quote=True)
    strategy_path = escape(str(selected_strategy_path or ""), quote=True)
    current_key = escape(str(current_strategy_key or ""), quote=True)
    return (
        "<script>"
        f'window.__DEX_STRATEGY_DEFAULT__={{strategyKey:"{strategy_key}",strategyPath:"{strategy_path}",currentStrategyKey:"{current_key}"}};'
        "</script>"
    )


def render_dex_strategy_script_tag() -> str:
    return f'<script src="{DEX_STRATEGY_SELECTOR_SCRIPT_PATH}"></script>'


def render_dex_strategy_init_script(
    *,
    feature: str | None = None,
    selected_strategy_key: str | None = None,
    selected_strategy_path: str | None = None,
    current_strategy_key: str | None = None,
) -> str:
    feature_value = escape(str(feature or "").strip().lower(), quote=True)
    strategy_key_value = escape(str(selected_strategy_key or "").strip().lower(), quote=True)
    strategy_path_value = escape(str(selected_strategy_path or "").strip(), quote=True)
    current_key_value = escape(str(current_strategy_key or "").strip().lower(), quote=True)
    return (
        "<script>"
        "window.DexStrategySelector && window.DexStrategySelector.init({"
        f'feature:"{feature_value}",'
        f'selectedStrategyKey:"{strategy_key_value}",'
        f'selectedStrategyPath:"{strategy_path_value}",'
        f'currentStrategyKey:"{current_key_value}"'
        "}).catch(function(error){console.error(\"dex strategy selector:\", error);});"
        "</script>"
    )


def render_dex_strategy_bootstrap(
    *,
    feature: str | None = None,
    selected_strategy_key: str | None = None,
    selected_strategy_path: str | None = None,
    current_strategy_key: str | None = None,
    include_script_tag: bool = True,
    include_init: bool = True,
) -> str:
    parts = [
        render_dex_strategy_default_script(
            selected_strategy_key=selected_strategy_key,
            selected_strategy_path=selected_strategy_path,
            current_strategy_key=current_strategy_key,
        )
    ]
    if include_script_tag:
        parts.append(render_dex_strategy_script_tag())
    if include_init:
        parts.append(
            render_dex_strategy_init_script(
                feature=feature,
                selected_strategy_key=selected_strategy_key,
                selected_strategy_path=selected_strategy_path,
                current_strategy_key=current_strategy_key,
            )
        )
    return "".join(parts)
