from __future__ import annotations

from html import escape
from urllib.parse import urlencode


_BOT_LINKS = (
    ("BOT 总览", "/bot"),
    ("债务池", "/bot/borrow-pool"),
    ("账户扫描", "/bot/account-scan"),
    ("清算执行", "/bot/execution"),
    ("执行审计", "/bot/audit"),
)
_DEX_LINKS = (
    ("DEX 总览", "/dex"),
    ("市场观察", "/dex/market-observation"),
    ("候选池", "/dex/candidates"),
    ("报价与路由", "/dex/quotes"),
    ("提交记录", "/dex/submissions"),
)
_SYSTEM_LINKS = (
    ("运行监控", "/ops"),
    ("系统配置", "/settings"),
    ("兑换矩阵", "/analytics/exchange-matrix"),
    ("机会健康诊断", "/analytics/opportunity-health"),
)


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
        "/dex-pools": "/dex/candidates",
        "/candidate-pools": "/dex/candidates",
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
    market_value = escape(str(default_market_id or ""), quote=True)
    chain_value = "" if default_chain_id is None else str(int(default_chain_id))
    selected_market_value = str(selected_market_id or default_market_id or "").strip()
    selected_chain_value = selected_chain_id if selected_chain_id is not None else default_chain_id
    selector = ""
    if include_chain_selector:
        selector_label = (
            "当前 DEX 链路"
            if feature_value == "dex_arbitrage"
            else "当前 BOT 市场"
            if feature_value == "liquidation"
            else "当前作用域"
        )
        selector = (
            '<div class="app-shell-chain" data-chain-selector="true" '
            f'data-chain-feature="{escape(feature_value, quote=True)}" '
            'data-global-chain-selector="true">'
            f'<span class="app-shell-label">{escape(selector_label)}</span>'
            '<span data-chain-selection-meta>Loading</span>'
            '</div>'
        )

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
    title = (
        "BOT 清算"
        if feature_value == "liquidation" or is_bot_path
        else "DEX 套利"
        if feature_value == "dex_arbitrage" or is_dex_path
        else "系统与分析"
        if is_system_path
        else "FlashLoan 工作台"
    )
    shell = f'''
<style id="flashloan-app-shell-style">
  body:not(.embedded) {{ padding-top: 58px !important; padding-left: 246px !important; }}
  body:not(.embedded) > header {{ display: none !important; }}
  body.embedded {{ padding: 0 !important; }}
  [data-app-shell="true"] {{ position: fixed; inset: 0 0 auto 0; z-index: 1000; font-family: Arial, "Microsoft YaHei", sans-serif; }}
  .app-shell-topbar {{ min-height: 58px; display: flex; align-items: center; gap: 18px; padding: 8px 18px; background: #101828; color: #fff; box-shadow: 0 1px 5px rgba(16,24,40,.2); }}
  .app-shell-brand {{ color: #fff; text-decoration: none; font-weight: 800; letter-spacing: .02em; white-space: nowrap; }}
  .app-shell-title {{ color: #d0d5dd; font-size: 13px; white-space: nowrap; }}
  .app-shell-context {{ display: flex; align-items: center; gap: 10px; margin-left: auto; min-width: 0; }}
  .app-shell-chain {{ display: flex; align-items: center; gap: 8px; min-width: 190px; }}
  .app-shell-chain select {{ min-height: 32px; max-width: 290px; border: 1px solid #667085; border-radius: 5px; padding: 0 8px; background: #fff; color: #101828; font-weight: 700; }}
  .app-shell-chain [data-chain-selection-meta] {{ color: #d0d5dd; font-size: 11px; white-space: nowrap; }}
  .app-shell-label {{ color: #98a2b3; font-size: 11px; white-space: nowrap; }}
  .app-shell-gate {{ color: #fdb022; font-size: 12px; white-space: nowrap; }}
  .app-shell-sidebar {{ position: fixed; top: 58px; bottom: 0; left: 0; width: 246px; overflow-y: auto; padding: 16px 12px 24px; background: #fff; border-right: 1px solid #d9dee7; }}
  .app-shell-group {{ margin-bottom: 18px; }}
  .app-shell-group-title {{ margin: 0 8px 6px; color: #667085; font-size: 11px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; }}
  .app-shell-link {{ display: block; padding: 9px 10px; border-radius: 5px; color: #344054; text-decoration: none; font-size: 13px; font-weight: 700; }}
  .app-shell-link:hover {{ background: #f2f4f7; color: #0f766e; }}
  .app-shell-link.active {{ background: #e6f4f1; color: #0f766e; }}
  @media (max-width: 900px) {{
    body:not(.embedded) {{ padding-left: 0 !important; padding-top: 104px !important; }}
    .app-shell-topbar {{ flex-wrap: wrap; gap: 7px 12px; }}
    .app-shell-context {{ width: 100%; margin-left: 0; }}
    .app-shell-sidebar {{ top: 104px; width: 100%; height: auto; max-height: 52px; padding: 5px 8px; overflow-x: auto; overflow-y: hidden; white-space: nowrap; }}
    .app-shell-group {{ display: inline-flex; align-items: center; gap: 4px; margin: 0 12px 0 0; }}
    .app-shell-group-title {{ margin: 0 4px; }}
    .app-shell-link {{ display: inline-block; padding: 6px 8px; }}
  }}
</style>
<div data-app-shell="true" data-shell-path="{escape(current_path, quote=True)}">
  <div class="app-shell-topbar">
    <a class="app-shell-brand" href="/bot">FlashLoan</a>
    <span class="app-shell-title">{escape(title)}</span>
    <div class="app-shell-context">
      {selector}
      <span class="app-shell-gate" data-shell-gate>执行门禁读取中</span>
    </div>
  </div>
  <aside class="app-shell-sidebar" aria-label="业务流程导航">
    <section class="app-shell-group"><h2 class="app-shell-group-title">BOT 清算</h2>{_links(_BOT_LINKS, current_path, preserve_scope=feature_value == "liquidation", market_id=selected_market_value, chain_id=selected_chain_value)}</section>
    <section class="app-shell-group"><h2 class="app-shell-group-title">DEX 套利</h2>{_links(_DEX_LINKS, current_path, preserve_scope=feature_value == "dex_arbitrage", market_id=selected_market_value, chain_id=selected_chain_value)}</section>
    <section class="app-shell-group"><h2 class="app-shell-group-title">系统与分析</h2>{_links(_SYSTEM_LINKS, current_path)}</section>
  </aside>
</div>
<script>
  window.__CHAIN_SELECTOR_DEFAULT__={{market_id:"{market_value}",chain_id:"{chain_value}"}};
</script>
'''
    if include_chain_selector:
        shell += '<script src="/static/chain-selector.js"></script>'
        shell += (
            f'<script>window.ChainSelector && window.ChainSelector.init({{feature: "{escape(feature_value)}", '
            f'defaultMarketId: "{market_value}"}}).catch(function(error){{console.error("chain selector:", error);}});</script>'
        )
    shell += (
        '<script src="/static/workflow-context.js"></script>'
        '<script>(function(){window.addEventListener("workflowcontextready",function(event){var c=event.detail&&event.detail.context||{};var e=document.querySelector("[data-shell-gate]");if(e)e.textContent=(c.execution_gate&&c.execution_gate.broadcast_enabled)?"广播已启用":"观察/门禁模式";});window.addEventListener("chainselectionready",function(event){var p=event.detail||{};var e=document.querySelector("[data-shell-gate]");if(e&&p.selected)e.title=(p.selected.market_id||"")+" / "+(p.selected.chain_id||"");});})();</script>'
    )
    return html.replace("<body>", "<body>" + shell, 1)
