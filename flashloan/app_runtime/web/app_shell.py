from __future__ import annotations

from html import escape
import os
import re
from urllib.parse import urlencode

from flask import request

from core.chain_data_registry import chain_default_protocol_preferences
from core.chain_route_selector import chain_scope_options, feature_from_page_path
from core.chain_selection import available_chain_selections, resolve_chain_selection
from core.flash_loan_pool_discovery import protocol_options_for_chain
from market.binance_market.chain_selector_component import (
    render_chain_selector_bootstrap,
    render_chain_selector_component,
)
from market.binance_market.dex_strategy_selector_component import render_dex_strategy_bootstrap
from market.binance_market.dex_strategy_registry import dex_strategy_key_for_path, dex_strategy_keys, dex_strategy_path_for_key
from core.dex_provenance import render_dex_provenance_bootstrap


_BOT_LINKS = (
    ("BOT 总览", "/bot"),
    ("债务池", "/bot/borrow-pool"),
    ("账户扫描", "/bot/account-scan"),
    ("清算执行", "/bot/execution"),
    ("执行审计", "/bot/audit"),
)
_DEX_LINKS = (
    ("报价与路由", "/dex"),
    ("DEX 总览", "/dex/overview"),
    ("市场观察", "/dex/market-observation"),
    ("候选池", "/dex/candidates"),
    ("提交记录", "/dex/submissions"),
)
_FLASH_LOAN_LINKS = (
    ("资金池", "/flash-loan/pools"),
)
_FLASH_LOAN_NAV_PROTOCOLS = {
    "aave_v3",
    "venus_core",
    "morpho_blue",
    "uniswap_v2",
    "uniswap_v3",
    "uniswap_v4",
    "pancakeswap_v2",
    "pancakeswap_v3",
    "biswap_v2",
    "quickswap_v2",
    "traderjoe_v2",
    "balancer_v2",
    "curve",
}
_SYSTEM_LINKS = (
    ("运行监控", "/ops"),
    ("系统配置", "/settings"),
    ("兑换矩阵", "/analytics/exchange-matrix"),
    ("机会健康诊断", "/analytics/opportunity-health"),
)


def _inject_after_body_open(html: str, markup: str) -> str:
    return re.sub(r"<body([^>]*)>", lambda match: f"<body{match.group(1)}>{markup}", html, count=1, flags=re.IGNORECASE)


def _active_path(path: str, target: str) -> bool:
    normalized = str(path or "/").split("?", 1)[0].rstrip("/") or "/"
    if normalized == target:
        return True
    aliases = {
        "/liquidation": "/bot/borrow-pool",
        "/account-scan": "/bot/account-scan",
        "/execution": "/bot/execution",
        "/audit": "/bot/audit",
        "/dex-arbitrage": "/dex",
        "/binance-market": "/dex",
        "/dex/quotes": "/dex",
        "/dex-pools": "/dex/candidates",
        "/candidate-pools": "/dex/candidates",
        "/flash-loan": "/flash-loan/pools",
        "/market-observation": "/dex/market-observation",
        "/config": "/settings",
        "/exchange-matrix": "/analytics/exchange-matrix",
        "/opportunity-health": "/analytics/opportunity-health",
    }
    return aliases.get(normalized) == target


def _active_path_mutually_exclusive(path: str, target: str) -> bool:
    """资金池与候选池是同一描述面的两种视图，同一时间只能有一个高亮。"""
    if not _active_path(path, target):
        return False
    if target in {"/flash-loan/pools", "/dex/candidates"}:
        counterpart = "/dex/candidates" if target == "/flash-loan/pools" else "/flash-loan/pools"
        return not _active_path(path, counterpart)
    return True


def _links(
    items: tuple[tuple[str, str], ...],
    current_path: str,
    *,
    preserve_scope: bool = False,
    current_feature: str | None = None,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> str:
    rendered = []
    scope_query = {}
    if preserve_scope:
        source_feature = str(current_feature or feature_from_page_path(current_path) or "").strip().lower()
        resolved_market_id = str(market_id or request.args.get("market_id", "")).strip()
        resolved_chain_id = chain_id
        if resolved_chain_id is None:
            raw_chain_id = str(request.args.get("chain_id", "")).strip()
            if raw_chain_id:
                try:
                    resolved_chain_id = int(raw_chain_id)
                except ValueError:
                    resolved_chain_id = raw_chain_id
        if resolved_chain_id is not None:
            scope_query["chain_id"] = str(resolved_chain_id)
    for label, href in items:
        active = " active" if _active_path_mutually_exclusive(current_path, href) else ""
        target = href
        target_query = dict(scope_query)
        target_feature = str(feature_from_page_path(href) or "").strip().lower()
        if preserve_scope and source_feature and target_feature == source_feature:
            if resolved_market_id:
                target_query["market_id"] = resolved_market_id
            protocols = str(request.args.get("protocols", "")).strip()
            if protocols:
                target_query["protocols"] = protocols
        elif preserve_scope and source_feature and not target_feature:
            target_query["feature"] = source_feature
        if preserve_scope and target_feature == "dex_arbitrage":
            dex_strategy = str(request.args.get("dex_strategy", "")).strip()
            if dex_strategy:
                target_query["dex_strategy"] = dex_strategy
        if target_query and "robinhood-dex" not in href and href != "/dex/robinhood-shadow":
            target = f"{href}?{urlencode(target_query)}"
        rendered.append(
            f'<a class="app-shell-link{active}" href="{target}"'
            f' data-shell-link="{href}">{escape(label)}</a>'
        )
    return "".join(rendered)


def _flash_loan_protocol_links(current_path: str, *, default_chain_id: int | None = None) -> str:
    normalized_path = str(current_path or "/").split("?", 1)[0].rstrip("/") or "/"
    default_protocols = chain_default_protocol_preferences().get(int(default_chain_id), []) if default_chain_id else []
    fallback_protocol = default_protocols[0] if default_protocols else "aave_v3"
    selected = (
        str(request.args.get("protocols", "")).replace("|", ",").split(",", 1)[0].strip().lower()
        or fallback_protocol
    )
    base_query = {}
    chain_id = str(request.args.get("chain_id", "")).strip()
    resolved_chain_id = None
    if chain_id:
        base_query["chain_id"] = chain_id
        try:
            resolved_chain_id = int(chain_id)
        except ValueError:
            resolved_chain_id = None
    elif default_chain_id is not None:
        resolved_chain_id = int(default_chain_id)
        base_query["chain_id"] = str(resolved_chain_id)
    protocol_options = [
        option
        for option in protocol_options_for_chain(chain_id=resolved_chain_id)
        if str(option.get("protocol") or "").strip().lower() in _FLASH_LOAN_NAV_PROTOCOLS
    ]
    rendered = ['<div class="app-shell-subnav" data-flash-loan-protocol-nav="true">']
    if not protocol_options:
        rendered.append('<span class="app-shell-subnote">暂无可用闪电贷池</span>')
    for option in protocol_options:
        label = str(option.get("display_name") or option.get("protocol") or "")
        protocol = str(option.get("protocol") or "").strip().lower()
        if not protocol:
            continue
        query = {**base_query, "protocols": protocol}
        active = " active" if normalized_path in {"/flash-loan", "/flash-loan/pools"} and selected == protocol else ""
        rendered.append(
            f'<a class="app-shell-sublink{active}" href="/flash-loan/pools?{urlencode(query)}"'
            f' data-flash-loan-protocol-link="{escape(protocol, quote=True)}">{escape(label)}</a>'
        )
    rendered.append("</div>")
    return "".join(rendered)


def _shell_sidebar_markup(
    *,
    feature_value: str,
    current_path: str,
    scope_market_id: str | None,
    scope_chain_id: int | None,
    default_chain_id: int | None,
) -> str:
    link_kwargs = {
        "current_path": current_path,
        "preserve_scope": True,
        "current_feature": feature_value,
        "market_id": scope_market_id,
        "chain_id": scope_chain_id,
    }
    sections = []
    if feature_value == "dex_arbitrage":
        sections.append(
            f'<section class="app-shell-group"><h2 class="app-shell-group-title">DEX 套利</h2>{_links(_DEX_LINKS, **link_kwargs)}</section>'
        )
        sections.append(
            f'<section class="app-shell-group"><h2 class="app-shell-group-title">闪电贷</h2>{_links(_FLASH_LOAN_LINKS, **link_kwargs)}{_flash_loan_protocol_links(current_path, default_chain_id=default_chain_id)}</section>'
        )
    else:
        sections.append(
            f'<section class="app-shell-group"><h2 class="app-shell-group-title">BOT 清算</h2>{_links(_BOT_LINKS, **link_kwargs)}</section>'
        )
    sections.append(
        f'<section class="app-shell-group"><h2 class="app-shell-group-title">系统与分析</h2>{_links(_SYSTEM_LINKS, **link_kwargs)}</section>'
    )
    return "".join(sections)


def _shell_layout_css(*, include_strategy_control: bool) -> str:
    topbar_columns = (
        "minmax(0, 20fr) minmax(0, 25fr) minmax(0, 25fr) minmax(0, 30fr)"
        if include_strategy_control
        else "minmax(0, 20fr) minmax(0, 25fr) minmax(0, 55fr)"
    )
    selector_rows = "auto auto auto auto" if include_strategy_control else "auto auto auto"
    chain_column = 3 if include_strategy_control else 2
    protocol_column = 4 if include_strategy_control else 3
    desktop_topbar_height = 96
    mobile_top = 280 if include_strategy_control else 216
    mobile_frame_top = mobile_top + 52
    strategy_css = ""
    strategy_mobile_css = ""
    if include_strategy_control:
        strategy_css = (
            "  .app-shell-dex-strategy-control { grid-column: 2; grid-row: 1; align-self: start; display: flex; flex-direction: column; align-items: flex-start; justify-content: flex-start; gap: 6px; width: 100%; min-width: 0; }\n"
            "  .app-shell-dex-strategy-control select { min-height: 38px; flex: 1 1 auto; width: auto; min-width: 0; box-sizing: border-box; border: 1px solid #667085; border-radius: 5px; padding: 0 10px; background: #fff; color: #101828; font-weight: 700; }\n"
            "  .app-shell-dex-strategy-meta { width: 100%; min-width: 0; min-height: 18px; display: flex; align-items: center; gap: 6px; color: #f2f4f7; font-size: 12px; line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }\n"
            "  .app-shell-dex-strategy-name { flex: 0 0 auto; min-width: 0; display: block; color: #f2f4f7; font-weight: 800; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }\n"
            "  .app-shell-dex-strategy-desc { flex: 1 1 auto; min-width: 0; display: block; color: #d0d5dd; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }\n"
        )
        strategy_mobile_css = (
            "    .app-shell-dex-strategy-control { grid-column: 1; grid-row: 2; justify-content: flex-start; }\n"
            "    .app-shell-dex-strategy-control select { width: auto; min-width: 0; }\n"
        )
    return f'''
<style id="flashloan-app-shell-style">
  body:not(.embedded) {{ padding-top: {desktop_topbar_height}px !important; padding-left: 246px !important; }}
  body:not(.embedded) > header {{ display: none !important; }}
  body.embedded {{ padding: 0 !important; }}
  body.app-shell-framed > :not([data-app-shell="true"]):not(.app-shell-content-frame):not(script):not(style) {{ display: none !important; }}
  [data-app-shell="true"] {{ position: fixed; inset: 0 0 auto 0; z-index: 1000; font-family: Arial, "Microsoft YaHei", sans-serif; }}
  .app-shell-content-frame {{ position: fixed; top: {desktop_topbar_height}px; left: 246px; right: 0; bottom: 0; z-index: 1; width: calc(100% - 246px); height: calc(100vh - {desktop_topbar_height}px); border: 0; background: #f6f7f9; }}
  .app-shell-topbar {{ min-height: {desktop_topbar_height}px; display: grid; grid-template-columns: {topbar_columns}; grid-template-rows: auto; align-items: start; gap: 8px 18px; padding: 10px 36px 12px; background: #101828; color: #fff; box-shadow: 0 1px 5px rgba(16,24,40,.2); }}
  .app-shell-selector {{ display: contents; }}
  .app-shell-feature-control {{ grid-column: 1; grid-row: 1; align-self: start; display: flex; flex-direction: column; align-items: flex-start; justify-content: flex-start; gap: 6px; width: 100%; min-width: 0; }}
{strategy_css}  .app-shell-chain-control {{ grid-column: {chain_column}; grid-row: 1; align-self: start; display: flex; flex-direction: column; align-items: flex-start; justify-content: flex-start; gap: 6px; width: 100%; min-width: 0; }}
  .app-shell-protocol-control {{ grid-column: {protocol_column}; grid-row: 1; align-self: start; display: flex; flex-direction: column; align-items: flex-start; justify-content: flex-start; gap: 6px; width: 100%; min-width: 0; overflow: hidden; }}
  .app-shell-protocol-line {{ display: flex; align-items: center; gap: 8px; width: 100%; min-width: 0; min-height: 38px; }}
  .app-shell-protocol-label {{ flex: 0 0 auto; color: #98a2b3; font-size: 11px; line-height: 1; white-space: nowrap; }}
  .app-shell-control-line {{ display: flex; align-items: center; gap: 8px; width: 100%; min-width: 0; min-height: 38px; }}
  .app-shell-label {{ flex: 0 0 auto; color: #98a2b3; font-size: 11px; white-space: nowrap; }}
  .app-shell-feature-control select {{ min-height: 38px; flex: 1 1 auto; width: auto; min-width: 0; box-sizing: border-box; border: 1px solid #667085; border-radius: 5px; padding: 0 10px; background: #fff; color: #101828; font-weight: 700; }}
  .app-shell-feature-meta {{ width: 100%; min-width: 0; min-height: 18px; color: #d0d5dd; font-size: 11px; line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .app-shell-chain-control select {{ min-height: 38px; flex: 1 1 auto; width: auto; min-width: 0; box-sizing: border-box; border: 1px solid #667085; border-radius: 5px; padding: 0 10px; background: #fff; color: #101828; font-weight: 700; }}
  .app-shell-selector-meta {{ width: 100%; min-width: 0; min-height: 18px; display: flex; justify-content: flex-start; align-items: center; gap: 8px; color: #d0d5dd; font-size: 11px; line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .app-shell-selector-meta [data-chain-selection-meta] {{ flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .app-shell-gate {{ color: #fdb022; font-size: 11px; white-space: nowrap; }}
  .app-shell-protocols {{ display: flex; flex-wrap: nowrap; justify-content: flex-start; align-items: center; align-content: flex-start; gap: 6px; width: 100%; min-width: 0; height: 42px; min-height: 42px; max-height: 42px; overflow-x: auto; overflow-y: hidden; scrollbar-width: none; }}
  .app-shell-protocols::-webkit-scrollbar {{ display: none; }}
  .app-shell-protocols-meta {{ width: 100%; min-width: 0; min-height: 18px; color: #d0d5dd; font-size: 11px; line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .app-shell-protocols .protocol-chip {{ display: inline-flex; flex: 0 0 auto; align-items: center; gap: 5px; height: 24px; min-height: 24px; box-sizing: border-box; line-height: 1; padding: 0 7px; border: 1px solid #5b6678; border-radius: 5px; background: #263244; color: #eef4ff; font-size: 10px; font-weight: 700; white-space: nowrap; }}
  .app-shell-protocols .protocol-chip input {{ flex: 0 0 auto; width: 13px; height: 13px; margin: 0; }}
  .app-shell-protocols .cow-network-chip {{ gap: 3px; height: 20px; min-height: 20px; padding: 0 5px; font-size: 9px; line-height: 1; }}
  .app-shell-protocols .cow-network-chip input {{ width: 10px; height: 10px; }}
  .app-shell-protocols .protocol-chip.active {{ border-color: #7dd3a8; background: #2d6f66; color: #f4fffb; }}
  .app-shell-sidebar {{ position: fixed; top: {desktop_topbar_height}px; bottom: 0; left: 0; width: 246px; overflow-y: auto; padding: 16px 12px 24px; background: #fff; border-right: 1px solid #d9dee7; }}
  .app-shell-group {{ margin-bottom: 18px; }}
  .app-shell-group-title {{ margin: 0 8px 6px; color: #667085; font-size: 11px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; }}
  .app-shell-link {{ display: block; padding: 9px 10px; border-radius: 5px; color: #344054; text-decoration: none; font-size: 13px; font-weight: 700; }}
  .app-shell-link:hover {{ background: #f2f4f7; color: #0f766e; }}
  .app-shell-link.active {{ background: #e6f4f1; color: #0f766e; }}
  .app-shell-subnav {{ margin: 3px 0 0 12px; padding-left: 9px; border-left: 1px solid #d9dee7; }}
  .app-shell-sublink {{ display: block; padding: 6px 8px; border-radius: 5px; color: #475467; text-decoration: none; font-size: 12px; font-weight: 700; }}
  .app-shell-sublink:hover {{ background: #f2f4f7; color: #0f766e; }}
  .app-shell-sublink.active {{ background: #e6f4f1; color: #0f766e; }}
  .app-shell-subnote {{ display: block; padding: 6px 8px; color: #98a2b3; font-size: 12px; }}
  @media (max-width: 900px) {{
    body:not(.embedded) {{ padding-left: 0 !important; padding-top: {mobile_top}px !important; }}
    .app-shell-topbar {{ min-height: {mobile_top}px; grid-template-columns: 1fr; grid-template-rows: auto auto auto; gap: 6px; padding-left: 18px; padding-right: 18px; }}
    .app-shell-selector {{ display: grid; grid-column: 1; grid-row: 1 / 4; grid-template-columns: 1fr; grid-template-rows: {selector_rows}; width: 100%; }}
    .app-shell-feature-control {{ grid-column: 1; grid-row: 1; justify-content: flex-start; }}
{strategy_mobile_css}    .app-shell-chain-control {{ grid-column: 1; grid-row: {3 if include_strategy_control else 2}; justify-content: flex-start; }}
    .app-shell-protocol-control {{ grid-column: 1; grid-row: {4 if include_strategy_control else 3}; justify-content: flex-start; }}
    .app-shell-protocol-line {{ min-height: 32px; }}
    .app-shell-selector-meta {{ justify-content: flex-start; }}
    .app-shell-feature-control select {{ width: auto; min-width: 0; }}
    .app-shell-chain-control select {{ width: auto; min-width: 0; }}
    .app-shell-sidebar {{ top: {mobile_top}px; width: 100%; height: auto; max-height: 52px; padding: 5px 8px; overflow-x: auto; overflow-y: hidden; white-space: nowrap; }}
    .app-shell-content-frame {{ top: {mobile_frame_top}px; left: 0; width: 100%; height: calc(100vh - {mobile_frame_top}px); }}
    .app-shell-group {{ display: inline-flex; align-items: center; gap: 4px; margin: 0 12px 0 0; }}
    .app-shell-group-title {{ margin: 0 4px; }}
    .app-shell-link {{ display: inline-block; padding: 6px 8px; }}
    .app-shell-subnav {{ display: inline-flex; align-items: center; gap: 4px; margin: 0; padding-left: 0; border-left: 0; }}
    .app-shell-sublink {{ display: inline-block; padding: 6px 8px; }}
  }}
</style>
'''


def inject_app_shell_markup(
    html: str,
    *,
    current_path: str,
    feature: str | None = None,
    default_market_id: str | None = None,
    default_chain_id: int | None = None,
    selected_market_id: str | None = None,
    selected_chain_id: int | None = None,
    include_chain_selector: bool = True,
) -> str:
    if 'data-app-shell="true"' in html:
        return html

    feature_value = str(feature or "").strip().lower()
    default_feature = str(os.getenv("CONTROL_PANEL_DEFAULT_FEATURE", "dex_arbitrage")).strip().lower()
    if current_path == "/" and not feature_value and not str(request.args.get("feature", "")).strip():
        selector_feature_value = default_feature
    else:
        selector_feature_value = feature_value or feature_from_page_path(current_path) or str(request.args.get("feature", "")).strip().lower() or default_feature
    if selector_feature_value not in {"liquidation", "dex_arbitrage"}:
        selector_feature_value = default_feature if default_feature in {"liquidation", "dex_arbitrage"} else "dex_arbitrage"
    selected_market_value = str(selected_market_id or default_market_id or "").strip()
    selected_chain_value = selected_chain_id if selected_chain_id is not None else default_chain_id
    current_strategy_key = dex_strategy_key_for_path(current_path)
    strategy_key_set = set(dex_strategy_keys())
    requested_strategy_raw = str(request.args.get("dex_strategy", "")).strip().lower()
    requested_strategy_key = requested_strategy_raw if requested_strategy_raw in strategy_key_set else ""
    selected_strategy_key = requested_strategy_key or current_strategy_key
    selected_strategy_path = dex_strategy_path_for_key(selected_strategy_key)
    selector = ""
    selected_chain = None
    include_strategy_control = include_chain_selector and selector_feature_value == "dex_arbitrage"
    if include_chain_selector:
        resolved_feature = selector_feature_value
        chain_selections = available_chain_selections(database_url=None, feature=resolved_feature)
        if chain_selections:
            try:
                selected_chain = resolve_chain_selection(
                    market_id=selected_market_value or None,
                    chain_id=selected_chain_value,
                    feature=resolved_feature,
                    default_market_id=default_market_id,
                    selections=chain_selections,
                )
            except Exception:
                selected_chain = next((item for item in chain_selections if item.is_default), chain_selections[0])
        chain_options = chain_scope_options(
            chain_selections,
            resolved_feature,
            include_chain_config=False,
        ) if chain_selections else []
        selector = render_chain_selector_component(
            variant="app_shell",
            selected_feature=selector_feature_value,
            include_strategy_control=include_strategy_control,
            selected_strategy_key=selected_strategy_key,
            chain_options=chain_options,
            selected_chain_key=selected_chain.global_chain_id if selected_chain else None,
        )
    scope_market_id = selected_chain.market_id if selected_chain else selected_market_value
    scope_chain_id = selected_chain.chain_id if selected_chain else selected_chain_value
    sidebar = _shell_sidebar_markup(
        feature_value=selector_feature_value,
        current_path=current_path,
        scope_market_id=scope_market_id,
        scope_chain_id=scope_chain_id,
        default_chain_id=default_chain_id,
    )
    shell = _shell_layout_css(include_strategy_control=include_strategy_control) + f'''
<div data-app-shell="true" data-shell-path="{escape(current_path, quote=True)}" data-shell-feature="{escape(selector_feature_value, quote=True)}">
  <div class="app-shell-topbar">
    {selector}
  </div>
  <aside class="app-shell-sidebar" aria-label="业务流程导航">
    {sidebar}
  </aside>
</div>
'''
    if include_chain_selector:
        shell += render_chain_selector_bootstrap(
            feature=selector_feature_value,
            default_market_id=default_market_id,
            default_chain_id=default_chain_id,
            include_chain_config=False,
        )
    if include_strategy_control:
        shell += render_dex_strategy_bootstrap(
            feature=selector_feature_value,
            selected_strategy_key=selected_strategy_key,
            selected_strategy_path=selected_strategy_path,
            current_strategy_key=current_strategy_key,
        )
    if feature_value == "dex_arbitrage":
        shell += render_dex_provenance_bootstrap()
    shell += (
        '<script src="/static/app-shell.js"></script>'
        '<script src="/static/workflow-context.js"></script>'
        '<script>(function(){window.addEventListener("workflowcontextready",function(event){var c=event.detail&&event.detail.context||{};var e=document.querySelector("[data-shell-gate]");if(e)e.textContent=(c.execution_gate&&c.execution_gate.broadcast_enabled)?"链上执行开关已开启":"链上执行开关未授权";});window.addEventListener("chainselectionready",function(event){var p=event.detail||{};var e=document.querySelector("[data-shell-gate]");if(e&&p.selected)e.title=(p.selected.market_id||"")+" / "+(p.selected.chain_id||"");});})();</script>'
    )
    return _inject_after_body_open(html, shell)
