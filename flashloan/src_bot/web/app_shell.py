from __future__ import annotations

from html import escape
import re
from urllib.parse import urlencode

from flask import request

from core.chain_data_registry import chain_default_protocol_preferences
from core.chain_route_selector import chain_scope_options, feature_from_page_path
from core.chain_selection import available_chain_selections, resolve_chain_selection
from core.flash_loan_pool_discovery import protocol_options_for_chain
from web.chain_selector_component import (
    render_chain_selector_bootstrap,
    render_chain_selector_component,
)
from web.dex_strategy_selector_component import render_dex_strategy_bootstrap
from market.binance_market.dex_strategy_registry import dex_strategy_key_for_path, dex_strategy_keys, dex_strategy_path_for_key
from web.dex_provenance import render_dex_provenance_bootstrap


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


def _links(
    items: tuple[tuple[str, str], ...],
    current_path: str,
    *,
    preserve_scope: bool = False,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> str:
    rendered = []
    scope_query = {}
    if preserve_scope and market_id:
        scope_query["market_id"] = str(market_id)
        if chain_id is not None:
            scope_query["chain_id"] = str(chain_id)
        protocols = str(request.args.get("protocols", "")).strip()
        if protocols:
            scope_query["protocols"] = protocols
        dex_strategy = str(request.args.get("dex_strategy", "")).strip()
        if dex_strategy:
            scope_query["dex_strategy"] = dex_strategy
    for label, href in items:
        active = " active" if _active_path(current_path, href) else ""
        target = href
        if scope_query and "robinhood-dex" not in href and href != "/dex/robinhood-shadow":
            target = f"{href}?{urlencode(scope_query)}"
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


def _page_title(current_path: str, feature_value: str) -> str:
    normalized_path = str(current_path or "/").split("?", 1)[0].rstrip("/") or "/"
    is_dex_path = normalized_path.startswith("/dex") or normalized_path in {
        "/binance-market",
        "/dex-arbitrage",
        "/market-observation",
    }
    is_bot_path = normalized_path.startswith("/bot") or normalized_path in {
        "/",
        "/liquidation",
        "/account-scan",
        "/execution",
        "/audit",
    }
    is_system_path = normalized_path in {
        "/ops",
        "/config",
        "/settings",
        "/exchange-matrix",
        "/opportunity-health",
    } or normalized_path.startswith("/analytics/")
    is_flash_loan_path = normalized_path.startswith("/flash-loan")
    if is_flash_loan_path:
        return "闪电贷"
    if feature_value == "liquidation" or is_bot_path:
        return "BOT"
    if feature_value == "dex_arbitrage" or is_dex_path:
        return "DEX"
    if is_system_path:
        return "系统"
    return "工作台"


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
    selected_market_value = str(selected_market_id or default_market_id or "").strip()
    selected_chain_value = selected_chain_id if selected_chain_id is not None else default_chain_id
    current_strategy_key = dex_strategy_key_for_path(current_path)
    strategy_key_set = set(dex_strategy_keys())
    requested_strategy_raw = str(request.args.get("dex_strategy", "")).strip().lower()
    requested_strategy_key = requested_strategy_raw if requested_strategy_raw in strategy_key_set else ""
    selected_strategy_key = requested_strategy_key or current_strategy_key
    selected_strategy_path = dex_strategy_path_for_key(selected_strategy_key)
    selector = ""
    if include_chain_selector:
        resolved_feature = feature_value or feature_from_page_path(current_path) or None
        chain_selections = available_chain_selections(database_url=None, feature=resolved_feature)
        selected_chain = None
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
            include_strategy_control=True,
            selected_strategy_key=selected_strategy_key,
            chain_options=chain_options,
            selected_chain_key=selected_chain.global_chain_id if selected_chain else None,
        )
    title = _page_title(current_path, feature_value)
    shell = f'''
<style id="flashloan-app-shell-style">
  body:not(.embedded) {{ padding-top: 116px !important; padding-left: 246px !important; }}
  body:not(.embedded) > header {{ display: none !important; }}
  body.embedded {{ padding: 0 !important; }}
  [data-app-shell="true"] {{ position: fixed; inset: 0 0 auto 0; z-index: 1000; font-family: Arial, "Microsoft YaHei", sans-serif; }}
  .app-shell-topbar {{ min-height: 116px; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); grid-template-rows: auto auto; align-items: center; gap: 8px 18px; padding: 10px 36px 12px; background: #101828; color: #fff; box-shadow: 0 1px 5px rgba(16,24,40,.2); }}
  .app-shell-brand {{ grid-column: 1; grid-row: 1; color: #fff; text-decoration: none; font-size: 18px; font-weight: 800; white-space: nowrap; }}
  .app-shell-title {{ grid-column: 1; grid-row: 2; color: #d0d5dd; font-size: 11px; white-space: nowrap; }}
  .app-shell-selector {{ display: contents; }}
  .app-shell-dex-strategy-control {{ grid-column: 2; grid-row: 1; align-self: start; display: flex; flex-direction: column; align-items: flex-start; justify-content: center; gap: 6px; width: 100%; min-width: 0; }}
  .app-shell-protocols {{ grid-column: 4; grid-row: 1; display: flex; flex-wrap: wrap; justify-content: flex-start; align-items: center; align-content: flex-start; gap: 6px; width: 100%; min-width: 0; height: 42px; min-height: 42px; max-height: 42px; overflow: hidden; }}
  .app-shell-chain-control {{ grid-column: 3; grid-row: 1; align-self: start; display: flex; flex-direction: column; align-items: flex-start; justify-content: center; gap: 6px; width: 100%; min-width: 0; }}
  .app-shell-control-line {{ display: flex; align-items: center; gap: 8px; width: 100%; min-width: 0; }}
  .app-shell-label {{ flex: 0 0 auto; color: #98a2b3; font-size: 11px; white-space: nowrap; }}
  .app-shell-dex-strategy-control select {{ min-height: 38px; flex: 1 1 auto; width: auto; min-width: 0; box-sizing: border-box; border: 1px solid #667085; border-radius: 5px; padding: 0 10px; background: #fff; color: #101828; font-weight: 700; }}
  .app-shell-dex-strategy-meta {{ width: 100%; min-width: 0; color: #f2f4f7; font-size: 12px; line-height: 1.4; }}
  .app-shell-dex-strategy-name {{ display: block; color: #f2f4f7; font-weight: 800; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .app-shell-dex-strategy-desc {{ display: -webkit-box; margin-top: 2px; color: #d0d5dd; white-space: normal; overflow: hidden; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }}
  .app-shell-chain-control select {{ min-height: 38px; flex: 1 1 auto; width: auto; min-width: 0; box-sizing: border-box; border: 1px solid #667085; border-radius: 5px; padding: 0 10px; background: #fff; color: #101828; font-weight: 700; }}
  .app-shell-selector-meta {{ grid-column: 2 / 5; grid-row: 2; display: flex; justify-content: flex-start; align-items: center; gap: 14px; min-width: 0; color: #d0d5dd; font-size: 11px; }}
  .app-shell-selector-meta [data-chain-selection-meta] {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .app-shell-gate {{ color: #fdb022; font-size: 11px; white-space: nowrap; }}
  .app-shell-protocols .protocol-chip {{ display: inline-flex; flex: 0 0 auto; align-items: center; gap: 5px; min-height: 22px; box-sizing: border-box; line-height: 1.2; padding: 2px 7px; border: 1px solid #5b6678; border-radius: 5px; background: #263244; color: #eef4ff; font-size: 10px; font-weight: 700; }}
  .app-shell-protocols .protocol-chip input {{ flex: 0 0 auto; width: 13px; height: 13px; margin: 0; }}
  .app-shell-protocols .cow-network-chip {{ gap: 3px; min-height: 17px; padding: 1px 5px; font-size: 9px; line-height: 1.1; }}
  .app-shell-protocols .cow-network-chip input {{ width: 10px; height: 10px; }}
  .app-shell-protocols .protocol-chip.active {{ border-color: #7dd3a8; background: #2d6f66; color: #f4fffb; }}
  .app-shell-sidebar {{ position: fixed; top: 116px; bottom: 0; left: 0; width: 246px; overflow-y: auto; padding: 16px 12px 24px; background: #fff; border-right: 1px solid #d9dee7; }}
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
    body:not(.embedded) {{ padding-left: 0 !important; padding-top: 184px !important; }}
    .app-shell-topbar {{ min-height: 184px; grid-template-columns: 1fr; grid-template-rows: auto auto auto; gap: 6px; padding-left: 18px; padding-right: 18px; }}
    .app-shell-brand {{ grid-column: 1; grid-row: 1; }}
    .app-shell-title {{ grid-column: 1; grid-row: 1; justify-self: end; }}
    .app-shell-selector {{ display: grid; grid-column: 1; grid-row: 2 / 4; grid-template-columns: 1fr; grid-template-rows: auto auto auto auto; width: 100%; }}
    .app-shell-dex-strategy-control {{ grid-column: 1; grid-row: 1; justify-content: flex-start; }}
    .app-shell-chain-control {{ grid-column: 1; grid-row: 2; justify-content: flex-start; }}
    .app-shell-protocols {{ grid-column: 1; grid-row: 3; justify-content: flex-start; }}
    .app-shell-selector-meta {{ grid-column: 1; grid-row: 4; justify-content: flex-start; flex-wrap: wrap; }}
    .app-shell-dex-strategy-control select {{ width: auto; min-width: 0; }}
    .app-shell-chain-control select {{ width: auto; min-width: 0; }}
    .app-shell-sidebar {{ top: 184px; width: 100%; height: auto; max-height: 52px; padding: 5px 8px; overflow-x: auto; overflow-y: hidden; white-space: nowrap; }}
    .app-shell-group {{ display: inline-flex; align-items: center; gap: 4px; margin: 0 12px 0 0; }}
    .app-shell-group-title {{ margin: 0 4px; }}
    .app-shell-link {{ display: inline-block; padding: 6px 8px; }}
    .app-shell-subnav {{ display: inline-flex; align-items: center; gap: 4px; margin: 0; padding-left: 0; border-left: 0; }}
    .app-shell-sublink {{ display: inline-block; padding: 6px 8px; }}
  }}
</style>
<div data-app-shell="true" data-shell-path="{escape(current_path, quote=True)}">
  <div class="app-shell-topbar">
    <a class="app-shell-brand" href="/bot">FLASH LOAN</a>
    <span class="app-shell-title">{escape(title)}</span>
    {selector}
  </div>
  <aside class="app-shell-sidebar" aria-label="业务流程导航">
    <section class="app-shell-group"><h2 class="app-shell-group-title">BOT 清算</h2>{_links(_BOT_LINKS, current_path, preserve_scope=feature_value == "liquidation", market_id=selected_market_value, chain_id=selected_chain_value)}</section>
    <section class="app-shell-group"><h2 class="app-shell-group-title">DEX 套利</h2>{_links(_DEX_LINKS, current_path, preserve_scope=feature_value == "dex_arbitrage", market_id=selected_market_value, chain_id=selected_chain_value)}</section>
    <section class="app-shell-group"><h2 class="app-shell-group-title">闪电贷</h2>{_links(_FLASH_LOAN_LINKS, current_path)}{_flash_loan_protocol_links(current_path, default_chain_id=default_chain_id)}</section>
    <section class="app-shell-group"><h2 class="app-shell-group-title">系统与分析</h2>{_links(_SYSTEM_LINKS, current_path)}</section>
  </aside>
</div>
'''
    if include_chain_selector:
        shell += render_chain_selector_bootstrap(
            feature=feature_value,
            default_market_id=default_market_id,
            default_chain_id=default_chain_id,
            include_chain_config=False,
        )
        shell += render_dex_strategy_bootstrap(
            feature=feature_value,
            selected_strategy_key=selected_strategy_key,
            selected_strategy_path=selected_strategy_path,
            current_strategy_key=current_strategy_key,
        )
    if feature_value == "dex_arbitrage":
        shell += render_dex_provenance_bootstrap()
    shell += (
        '<script src="/static/workflow-context.js"></script>'
        '<script>(function(){window.addEventListener("workflowcontextready",function(event){var c=event.detail&&event.detail.context||{};var e=document.querySelector("[data-shell-gate]");if(e)e.textContent=(c.execution_gate&&c.execution_gate.broadcast_enabled)?"Broadcast enabled":"Observation mode";});window.addEventListener("chainselectionready",function(event){var p=event.detail||{};var e=document.querySelector("[data-shell-gate]");if(e&&p.selected)e.title=(p.selected.market_id||"")+" / "+(p.selected.chain_id||"");});})();</script>'
    )
    return _inject_after_body_open(html, shell)
