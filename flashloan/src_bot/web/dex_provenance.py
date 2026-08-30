from __future__ import annotations

import json
import re
from typing import Any


STATIC_FRAMEWORK_SOURCES = (
    "market_observation",
    "cached_snapshot",
    "generated_live_snapshot",
    "refreshed_live_snapshot",
)
STATIC_FRAMEWORK_LABEL = "静态框架"
CANDIDATE_FRAMEWORK_SOURCE = "candidate_pool_fallback"
CANDIDATE_FRAMEWORK_LABEL = "候选框架"
COMPUTED_FRAMEWORK_SOURCE = "computed"
COMPUTED_FRAMEWORK_LABEL = "链上重算"
MARKET_OBSERVATION_SOURCE_STAGE_LABELS = {
    "cached_snapshot": "缓存静态快照",
    "generated_live_snapshot": "实时生成快照",
    "refreshed_live_snapshot": "实时刷新快照",
    "computed": "链上重算",
    "dex_market_observation_payload": "观察载荷",
    "market_observation": "市场观察框架",
}
PRICE_EVIDENCE_SOURCE_LABELS = {
    "chain_live_quote": "链上实时报价",
    "cached_framework_live_confirmation": "静态框架 + 实时复核",
    "chain_live_quote_required": "链上实时复核",
    "framework_reused_live_confirmation": "静态框架 + 实时复核",
    "live_quoter_required": "需要链上 Quoter",
    "reference_price_requires_live_quoter": "参考价需要链上 Quoter",
    "static_reference_probe": "静态探针",
}
PRICE_EVIDENCE_MODE_LABELS = {
    "framework_reused_live_confirmation": "静态框架复核",
    "live_required": "需要链上复核",
    "live_verified": "实时价格已验证",
    "reference_only": "参考价",
}


def framework_label(source: object) -> str:
    value = str(source or "").strip().lower()
    if value in STATIC_FRAMEWORK_SOURCES:
        return STATIC_FRAMEWORK_LABEL
    if value == CANDIDATE_FRAMEWORK_SOURCE:
        return CANDIDATE_FRAMEWORK_LABEL
    if value == COMPUTED_FRAMEWORK_SOURCE:
        return COMPUTED_FRAMEWORK_LABEL
    return str(source or "-")


def price_evidence_label(source: object) -> str:
    value = str(source or "").strip().lower()
    return PRICE_EVIDENCE_SOURCE_LABELS.get(value, str(source or "-"))


def price_evidence_mode_label(mode: object) -> str:
    value = str(mode or "").strip().lower()
    return PRICE_EVIDENCE_MODE_LABELS.get(value, str(mode or "-"))


def market_observation_source_stage_label(source: object) -> str:
    value = str(source or "").strip().lower()
    return MARKET_OBSERVATION_SOURCE_STAGE_LABELS.get(value, str(source or "-"))


def build_price_evidence_payload(*, source: object = None, mode: object = None) -> dict[str, Any]:
    return {
        "priceEvidenceSource": source,
        "priceEvidenceSourceLabel": price_evidence_label(source),
        "priceEvidenceMode": mode,
        "priceEvidenceModeLabel": price_evidence_mode_label(mode),
    }


def provenance_label(source: object) -> str:
    return framework_label(source)


def build_route_framework_payload(
    *,
    route_framework_source: object,
    market_observation_source: object,
    market_observation_source_stage: object = None,
    decision_basis: object = None,
    route_count: int = 0,
    observation_sequence_count: int = 0,
    observation_anchor_count: int = 0,
    candidate_route_count: int = 0,
    chain_submission_candidate_count: int | None = None,
    top_route_text: object = "-",
    top_route_status: object = "-",
    framework_label_fn=framework_label,
    provenance_label_fn=provenance_label,
    source_stage_label_fn=market_observation_source_stage_label,
) -> dict[str, Any]:
    candidate_count = candidate_route_count if chain_submission_candidate_count is None else chain_submission_candidate_count
    source_stage = market_observation_source_stage if market_observation_source_stage is not None else market_observation_source
    return {
        "routeFrameworkSource": str(route_framework_source or "market_observation"),
        "routeFrameworkLabel": framework_label_fn(route_framework_source),
        "routeFrameworkProvenanceLabel": provenance_label_fn(route_framework_source),
        "marketObservationSource": str(market_observation_source or "computed"),
        "marketObservationLabel": framework_label_fn(market_observation_source),
        "marketObservationProvenanceLabel": provenance_label_fn(market_observation_source),
        "marketObservationSourceStage": str(source_stage or market_observation_source or "computed"),
        "marketObservationSourceStageLabel": source_stage_label_fn(source_stage),
        "decisionBasis": str(decision_basis or "-"),
        "routeCount": int(route_count or 0),
        "observationSequenceCount": int(observation_sequence_count or 0),
        "observationAnchorCount": int(observation_anchor_count or 0),
        "candidateRouteCount": int(candidate_route_count or 0),
        "chainSubmissionCandidateCount": int(candidate_count or 0),
        "bestObservedNetProfitUsd": None,
        "bestExecutableNetProfitUsd": None,
        "topRouteText": str(top_route_text or "-"),
        "topRouteStatus": str(top_route_status or "-"),
    }


def dex_provenance_payload() -> dict[str, Any]:
    return {
        "staticFrameworkSources": list(STATIC_FRAMEWORK_SOURCES),
        "staticFrameworkLabel": STATIC_FRAMEWORK_LABEL,
        "candidateFrameworkSource": CANDIDATE_FRAMEWORK_SOURCE,
        "candidateFrameworkLabel": CANDIDATE_FRAMEWORK_LABEL,
        "computedFrameworkSource": COMPUTED_FRAMEWORK_SOURCE,
        "computedFrameworkLabel": COMPUTED_FRAMEWORK_LABEL,
        "marketObservationSourceStageLabels": dict(MARKET_OBSERVATION_SOURCE_STAGE_LABELS),
        "priceEvidenceSourceLabels": dict(PRICE_EVIDENCE_SOURCE_LABELS),
        "priceEvidenceModeLabels": dict(PRICE_EVIDENCE_MODE_LABELS),
    }


def render_dex_provenance_bootstrap() -> str:
    payload = json.dumps(dex_provenance_payload(), ensure_ascii=False, separators=(",", ":"))
    return (
        "<script>window.__DEX_PROVENANCE__="
        f"{payload};</script>"
        '<script src="/static/dex-provenance.js"></script>'
    )


def inject_dex_provenance_markup(html: str) -> str:
    if 'window.__DEX_PROVENANCE__' in html or 'data-dex-provenance="true"' in html:
        return html
    markup = '<div data-dex-provenance="true" hidden></div>' + render_dex_provenance_bootstrap()
    return re.sub(r"<body([^>]*)>", lambda match: f"<body{match.group(1)}>{markup}", html, count=1, flags=re.IGNORECASE)


__all__ = [
    "CANDIDATE_FRAMEWORK_LABEL",
    "CANDIDATE_FRAMEWORK_SOURCE",
    "COMPUTED_FRAMEWORK_LABEL",
    "COMPUTED_FRAMEWORK_SOURCE",
    "MARKET_OBSERVATION_SOURCE_STAGE_LABELS",
    "build_route_framework_payload",
    "PRICE_EVIDENCE_MODE_LABELS",
    "PRICE_EVIDENCE_SOURCE_LABELS",
    "STATIC_FRAMEWORK_LABEL",
    "STATIC_FRAMEWORK_SOURCES",
    "dex_provenance_payload",
    "framework_label",
    "inject_dex_provenance_markup",
    "market_observation_source_stage_label",
    "price_evidence_label",
    "price_evidence_mode_label",
    "provenance_label",
    "build_price_evidence_payload",
    "render_dex_provenance_bootstrap",
]
