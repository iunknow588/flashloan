from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from flask import Response, jsonify, request

from core.html_utils import (
    add_body_attribute as _add_body_attribute,
    prepare_embedded_html as _prepare_embedded_html,
)
from core.chain_selection import ChainSelectionError
from core.flash_loan_pool_discovery import (
    refresh_flash_loan_pool_discovery_cache,
)
from core.chain_data_registry import default_chain_scope
from core.flash_loan_pools import load_flash_loan_pool_static
from core.market_config import (
    supported_market_summaries,
)
from web.chain_selection import inject_chain_selector_markup
from web.app_shell import inject_app_shell_markup
from core.dex_provenance import inject_dex_provenance_markup
from core.market_page_registry import resolve_market_page_variant
from core.workflow_context import inject_workflow_context_markup


ROBINHOOD_CHAIN_ID = 4663
ROBINHOOD_TESTNET_CHAIN_ID = 46630


def _request_scope_defaults(
    default_market_id: str | None,
    default_chain_id: int | None,
) -> tuple[str | None, int | None]:
    market_id = request.args.get("market_id", "").strip() or default_market_id
    raw_chain_id = request.args.get("chain_id", "").strip()
    if not raw_chain_id:
        return market_id, default_chain_id
    try:
        selected_chain_id = int(raw_chain_id)
    except ValueError:
        return market_id, default_chain_id
    return _normalize_page_scope_market_id(market_id, selected_chain_id), selected_chain_id


def _market_id_chain_component(market_id: str | None) -> str | None:
    normalized = str(market_id or "").strip().lower()
    match = re.match(r"^(liquidation|dex-arbitrage)-(\d+)-[a-z0-9_-]+$", normalized)
    return match.group(2) if match else None


def _normalize_page_scope_market_id(market_id: str | None, chain_id: int | None) -> str | None:
    if not market_id or chain_id is None:
        return market_id
    embedded_chain = _market_id_chain_component(market_id)
    if embedded_chain and embedded_chain != str(int(chain_id)):
        return None
    return market_id


def _request_protocol_id() -> str | None:
    raw = request.args.get("protocol_id", "").strip()
    if raw:
        return raw
    for item in request.args.get("protocols", "").replace("|", ",").split(","):
        protocol = item.strip()
        if protocol:
            return protocol
    return None


def _request_shell_feature() -> str:
    feature = request.args.get("feature", "").strip().lower()
    if feature in {"liquidation", "dex_arbitrage"}:
        return feature
    default_feature = str(os.getenv("CONTROL_PANEL_DEFAULT_FEATURE", "dex_arbitrage")).strip().lower()
    return default_feature if default_feature in {"liquidation", "dex_arbitrage"} else "dex_arbitrage"


def _html_response(
    path: Path,
    *,
    feature: str | None = None,
    default_market_id: str | None = None,
    default_chain_id: int | None = None,
    page_surface: str | None = None,
    include_chain_selector: bool = True,
) -> Response:
    html = path.read_text(encoding="utf-8")
    selected_market_id, selected_chain_id = _request_scope_defaults(default_market_id, default_chain_id)
    shell_enabled = request.args.get("embed", "").strip().lower() not in {"1", "true", "yes"}
    if shell_enabled:
        html = inject_app_shell_markup(
            html,
            current_path=request.path,
            feature=feature,
            default_market_id=default_market_id,
            default_chain_id=default_chain_id,
            selected_market_id=selected_market_id,
            selected_chain_id=selected_chain_id,
            include_chain_selector=include_chain_selector,
        )
    else:
        html = _prepare_embedded_html(html)
    if page_surface:
        html = _add_body_attribute(html, "data-page-surface", page_surface)
    if feature == "dex_arbitrage" and not shell_enabled:
        html = inject_dex_provenance_markup(html)
    if include_chain_selector:
        html = inject_chain_selector_markup(
            html,
            feature=feature,
            default_market_id=default_market_id,
            default_chain_id=default_chain_id,
        )
    html = inject_workflow_context_markup(html)
    response = Response(html, mimetype="text/html")
    response.charset = "utf-8"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def _panel_html_response(
    html: str,
    *,
    feature: str | None = None,
    default_market_id: str | None = None,
    default_chain_id: int | None = None,
    include_chain_selector: bool = True,
) -> Response:
    selected_market_id, selected_chain_id = _request_scope_defaults(default_market_id, default_chain_id)
    shell_enabled = request.args.get("embed", "").strip().lower() not in {"1", "true", "yes"}
    if shell_enabled:
        html = inject_app_shell_markup(
            html,
            current_path=request.path,
            feature=feature,
            default_market_id=default_market_id,
            default_chain_id=default_chain_id,
            selected_market_id=selected_market_id,
            selected_chain_id=selected_chain_id,
            include_chain_selector=include_chain_selector,
        )
    else:
        html = _prepare_embedded_html(html)
        if feature == "dex_arbitrage":
            html = inject_dex_provenance_markup(html)
    if include_chain_selector:
        html = inject_chain_selector_markup(
            html,
            feature=feature,
            default_market_id=default_market_id,
            default_chain_id=default_chain_id,
        )
    html = inject_workflow_context_markup(html)
    response = Response(
        html,
        mimetype="text/html",
    )
    response.charset = "utf-8"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def _request_chain_id() -> int | None:
    raw = request.args.get("chain_id", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise ChainSelectionError("invalid_chain_id", f"invalid chain_id: {raw}") from None


def _request_optional_int(name: str) -> int | None:
    raw = request.args.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise ChainSelectionError(f"invalid_{name}", f"invalid {name}: {raw}") from None


def _panel_database_url(panel) -> str | None:
    getter = getattr(panel, "database_url_or_none", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:
        return None


def _flash_loan_discovery_refresh_kwargs(panel) -> dict:
    return {"market_rows": supported_market_summaries(_panel_database_url(panel))}


def _request_protocols() -> list[str]:
    return [
        item.strip().lower()
        for item in request.args.get("protocols", "").replace("|", ",").split(",")
        if item.strip()
    ]


def _request_bool_arg(name: str) -> bool:
    body = request.get_json(silent=True) or {}
    return request.args.get(name, "").strip().lower() in {"1", "true", "yes", "on"} or bool(body.get(name))


def _default_scope_kwargs(feature: str) -> dict[str, object]:
    scope = default_chain_scope(feature)
    return {
        "default_market_id": str(scope["market_id"]),
        "default_chain_id": int(scope["chain_id"]),
    }


def _static_pool_snapshot(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        key: summary.get(key)
        for key in (
            "borrow_asset_count",
            "borrow_target_count",
            "v4_pool_key_count",
            "v4_pool_liquidity_pool_count",
            "v4_indexed_pool_key_count",
            "flash_accounting_asset_count",
        )
    }


def _static_pool_diff(old_summary: dict[str, Any], new_summary: dict[str, Any]) -> dict[str, Any]:
    changed: list[str] = []
    fields: dict[str, dict[str, Any]] = {}
    for key in sorted(set(old_summary) | set(new_summary)):
        old_value = old_summary.get(key)
        new_value = new_summary.get(key)
        if old_value == new_value:
            continue
        changed.append(key)
        fields[key] = {"previous": old_value, "current": new_value}
    return {
        "changed": bool(changed),
        "changedFields": changed,
        "fields": fields,
    }


def _previous_static_pool_store(
    *,
    chain_id: int | None,
    protocols: list[str],
    panel,
) -> dict[str, dict[str, Any]]:
    if chain_id is None or not protocols:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for protocol in protocols:
        stored = load_flash_loan_pool_static(chain_id=chain_id, protocol=protocol)
        if not stored.get("exists"):
            continue
        summary = stored.get("summary") if isinstance(stored.get("summary"), dict) else {}
        result[protocol] = {
            "path": stored.get("path"),
            "network": stored.get("network"),
            "chain_id": stored.get("chain_id"),
            "protocol": stored.get("protocol"),
            "generated_at": stored.get("generated_at"),
            "refresh_mode": stored.get("refresh_mode"),
            "summary": _static_pool_snapshot(summary),
            "section_counts": dict(stored.get("section_counts") or {}),
        }
    return result


def _current_static_pool_store(
    *,
    chain_id: int | None,
    protocols: list[str],
) -> dict[str, dict[str, Any]]:
    if chain_id is None or not protocols:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for protocol in protocols:
        stored = load_flash_loan_pool_static(chain_id=chain_id, protocol=protocol)
        if not stored.get("exists"):
            continue
        summary = stored.get("summary") if isinstance(stored.get("summary"), dict) else {}
        result[protocol] = {
            "path": stored.get("path"),
            "network": stored.get("network"),
            "chain_id": stored.get("chain_id"),
            "protocol": stored.get("protocol"),
            "generated_at": stored.get("generated_at"),
            "refresh_mode": stored.get("refresh_mode"),
            "summary": _static_pool_snapshot(summary),
            "section_counts": dict(stored.get("section_counts") or {}),
        }
    return result


def _static_pool_store_diff(
    previous: dict[str, dict[str, Any]],
    current_static: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    protocols = set(previous) | set(current_static)
    for protocol in sorted(protocols):
        old = previous.get(protocol)
        new = current_static.get(protocol)
        old_summary = _static_pool_snapshot((old or {}).get("summary") or {})
        new_summary = _static_pool_snapshot((new or {}).get("summary") or {})
        diff = _static_pool_diff(old_summary, new_summary)
        diff["previousPath"] = (old or {}).get("path")
        diff["currentPath"] = (new or {}).get("path")
        result[str(protocol)] = diff
    return result


def _refresh_flash_loan_pool_sources(*, chain_id: int | None, protocols: list[str], full: bool, panel) -> dict:
    steps: list[dict] = []
    errors: list[str] = []
    try:
        discovery = refresh_flash_loan_pool_discovery_cache(**_flash_loan_discovery_refresh_kwargs(panel))
        steps.append(
            {
                "source": "flash_loan_pool_discovery",
                "ok": True,
                "chainCount": discovery.get("chain_count"),
                "protocolCount": discovery.get("protocol_count"),
            }
        )
    except Exception as exc:
        errors.append(f"flash_loan_pool_discovery: {exc}")
        steps.append({"source": "flash_loan_pool_discovery", "ok": False, "error": str(exc)})

    protocol_set = set(protocols)
    refresh_v4 = chain_id in (ROBINHOOD_CHAIN_ID, ROBINHOOD_TESTNET_CHAIN_ID) and (not protocol_set or "uniswap_v4" in protocol_set)
    if refresh_v4:
        source = "robinhood_v4_pool_index"
        if full:
            try:
                if chain_id == ROBINHOOD_CHAIN_ID:
                    from dex.intent_trade.chains.robinhood.v4_pool_index import write_v4_pool_index_from_initialize_logs as write_mainnet_v4_index

                    result = write_mainnet_v4_index(
                        from_block=0,
                        to_block=None,
                        include_metadata=True,
                        mark_recent_swaps=True,
                    )
                    source = "robinhood_mainnet_v4_pool_index"
                else:
                    from dex.intent_trade.chains.robinhood_testnet.v4_pool_index import write_v4_pool_index_from_initialize_logs

                    result = write_v4_pool_index_from_initialize_logs()
                    source = "robinhood_testnet_v4_pool_index"
                steps.append(
                    {
                        "source": source,
                        "ok": True,
                        "outputPath": result.get("outputPath"),
                        "poolCount": len(result.get("pools") or []),
                        "poolKeyCount": len(result.get("poolKeys") or result.get("pool_keys") or []),
                    }
                )
            except Exception as exc:
                errors.append(f"{source}: {exc}")
                steps.append({"source": source, "ok": False, "error": str(exc)})
        try:
            if chain_id == ROBINHOOD_TESTNET_CHAIN_ID:
                from dex.intent_trade.chains.robinhood_testnet.v4_pool_liquidity import write_v4_pool_liquidity_from_contract_evidence

                result = write_v4_pool_liquidity_from_contract_evidence(
                    token_balance_scan_scope="active_plus_known",
                    multicall_chunk_size=200,
                    max_token_balance_scan_seconds=None,
                )
                steps.append(
                    {
                        "source": "robinhood_testnet_v4_pool_liquidity",
                        "ok": True,
                        "outputPath": result.get("outputPath"),
                        "poolCount": len(result.get("pools") or []),
                        "poolKeyCount": len(result.get("poolKeys") or result.get("pool_keys") or []),
                        "tokenBalanceScanScope": result.get("tokenBalanceScanScope"),
                        "tokenBalanceScanCount": result.get("tokenBalanceScanCount"),
                        "tokenBalanceSourceCount": result.get("tokenBalanceSourceCount"),
                    }
                )
            else:
                steps.append(
                    {
                        "source": "robinhood_mainnet_v4_pool_liquidity",
                        "ok": True,
                        "poolCount": 0,
                        "poolKeyCount": 0,
                        "message": "主网 V4 只在池索引解码完成后生成流动性证据",
                    }
                )
        except Exception as exc:
            liquidity_source = "robinhood_mainnet_v4_pool_liquidity" if chain_id == ROBINHOOD_CHAIN_ID else "robinhood_testnet_v4_pool_liquidity"
            errors.append(f"{liquidity_source}: {exc}")
            steps.append({"source": liquidity_source, "ok": False, "error": str(exc)})

    return {
        "ok": not errors,
        "requested": "full" if full else "incremental",
        "source": "flash_loan_pool_static_store_refresh",
        "steps": steps,
        "errors": errors,
        "message": "资金池全量刷新完成" if full and not errors else ("资金池增量补全完成" if not errors else "资金池刷新完成但存在错误"),
    }


def _market_page_response(
    panel,
    *,
    template_path: Path | None = None,
    default_market_id: str | None = None,
    default_chain_id: int | None = None,
) -> Response:
    try:
        requested_chain_id = _request_chain_id()
        requested_market_id = _normalize_page_scope_market_id(
            request.args.get("market_id", "").strip() or None,
            requested_chain_id,
        )
        variant = resolve_market_page_variant(
            request.path,
            market_id=requested_market_id,
            chain_id=requested_chain_id,
            protocol_id=_request_protocol_id(),
            database_url=_panel_database_url(panel),
            default_market_id=default_market_id,
            default_chain_id=default_chain_id,
        )
    except ChainSelectionError as exc:
        return jsonify({"ok": False, "error": exc.message, "code": exc.code}), 400
    if variant is None:
        return jsonify({"ok": False, "error": "page market variant is not registered", "code": "page_variant_missing"}), 500
    selected_template = template_path
    if variant.page_key == "robinhood_dex":
        selected_template = panel.ROBINHOOD_DEX_TEMPLATE_PATH
    elif variant.feature == "dex_arbitrage" and variant.surface == "overview":
        selected_template = panel.DEX_OVERVIEW_TEMPLATE_PATH
    elif variant.feature == "dex_arbitrage" and variant.surface == "observation":
        selected_template = panel.MARKET_OBSERVATION_TEMPLATE_PATH
    elif variant.feature == "dex_arbitrage" and variant.surface == "candidates":
        selected_template = panel.DEX_POOL_CANDIDATES_TEMPLATE_PATH
    elif variant.feature == "dex_arbitrage" and variant.surface == "quotes":
        selected_template = panel.DEX_QUOTES_TEMPLATE_PATH
    elif variant.feature == "dex_arbitrage" and variant.surface == "submissions":
        selected_template = panel.DEX_SUBMISSIONS_TEMPLATE_PATH
    elif variant.feature == "dex_arbitrage" and variant.surface == "submissions_failed":
        selected_template = panel.DEX_SUBMISSIONS_FAILED_TEMPLATE_PATH
    elif variant.template_kind == "single_chain":
        selected_template = panel.DEX_SINGLE_CHAIN_TEMPLATE_PATH
    elif selected_template is None and variant.feature == "dex_arbitrage":
        selected_template = panel.BINANCE_MARKET_TEMPLATE_PATH
    elif selected_template is None:
        selected_template = panel.LIQUIDATION_TEMPLATE_PATH
    return _html_response(
        selected_template,
        feature=variant.feature,
        default_market_id=variant.market_id,
        default_chain_id=variant.chain_id,
        page_surface=variant.surface,
    )
