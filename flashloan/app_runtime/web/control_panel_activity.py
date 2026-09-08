"""Control panel activity formatting helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Optional, Any

from core.sensitive_data import redact_sensitive_text


LIQUIDATION_STAGE_LABELS = {
    "idle": "空闲",
    "window": "计算扫描窗口",
    "resolving-blocks": "解析区块范围",
    "borrowers": "发现借款账户",
    "saving": "保存账户",
    "debt_pool": "扫描债务池",
    "health": "扫描健康度",
    "starting": "启动中",
    "running": "运行中",
}


def _iso_elapsed_seconds(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        started_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - started_at).total_seconds())


def _liquidation_activity_percent(stage: str, progress: dict) -> int:
    if {"from_block", "to_block", "current_to_block"}.issubset(progress):
        from_block = int(progress.get("from_block") or 0)
        to_block = int(progress.get("to_block") or 0)
        current_to = int(progress.get("current_to_block") or from_block)
        total = max(1, to_block - from_block + 1)
        scanned = max(0, min(total, current_to - from_block + 1))
        return max(5, min(99, int(scanned / total * 100)))
    if "scanned_count" in progress or "account_count" in progress:
        scanned = int(progress.get("scanned_count") or 0)
        total = int(progress.get("account_count") or 0)
        if total:
            return max(5, min(99, int(scanned / total * 100)))
        return 15 if stage in {"debt_pool", "health"} else 10
    return {
        "window": 8,
        "resolving-blocks": 12,
        "borrowers": 20,
        "saving": 90,
        "debt_pool": 15,
        "health": 15,
    }.get(stage, 5)


def _liquidation_activity_payload(label: str, cache: dict, lock: threading.Lock) -> dict:
    running = bool(cache.get("running"))
    if running and hasattr(lock, "locked") and not lock.locked():
        cache["running"] = False
        cache["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cache["stage"] = "idle"
        running = False
    stage = str(cache.get("stage") or "idle")
    stage_label = LIQUIDATION_STAGE_LABELS.get(stage, stage)
    progress = dict(cache.get("progress") or {})
    last_result = dict(cache.get("last_result") or {})
    detail = _liquidation_activity_detail(stage, progress, last_result)
    elapsed_seconds = _iso_elapsed_seconds(cache.get("started_at")) if running else None
    percent = _liquidation_activity_percent(stage, progress) if running else 0
    text = f"{label}：{stage_label}"
    if detail:
        text = f"{text}（{detail}）"
    feedback = text if running else f"{label}：空闲"
    if running and elapsed_seconds is not None:
        feedback = f"{feedback}，已用 {elapsed_seconds:.1f}s"
    return {
        "label": label,
        "running": running,
        "stage": stage,
        "stage_label": stage_label,
        "progress": progress,
        "last_result": last_result,
        "detail": detail,
        "feedback": feedback,
        "percent": percent,
        "elapsed_seconds": elapsed_seconds,
        "started_at": cache.get("started_at"),
        "finished_at": cache.get("finished_at"),
        "text": text if running else f"{label}：空闲",
    }


def _liquidation_activity_detail(stage: str, progress: dict, last_result: dict) -> str:
    if progress:
        if {"from_block", "to_block", "current_to_block"}.issubset(progress):
            from_block = int(progress.get("from_block") or 0)
            to_block = int(progress.get("to_block") or 0)
            current_to = int(progress.get("current_to_block") or from_block)
            total = max(1, to_block - from_block + 1)
            scanned = max(0, min(total, current_to - from_block + 1))
            percent = scanned / total * 100.0
            found = int(progress.get("discovered_count") or 0)
            return f"区块 {current_to}/{to_block}，{percent:.1f}%，发现 {found} 个账户"
        if "scanned_count" in progress or "account_count" in progress:
            scanned = int(progress.get("scanned_count") or 0)
            total = int(progress.get("account_count") or 0)
            if total:
                return f"账户 {scanned}/{total}"
            return f"已扫 {scanned} 个账户"
    error = redact_sensitive_text(last_result.get("error") or "")
    if stage == "borrowers":
        return "正在建立账户池，等待首次发现结果"
    if error == "database liquidation account table is empty":
        return "账户库为空，等待发现扫描写入账户"
    if error == "liquidation account registry is empty":
        return "账户池为空，等待发现扫描写入账户"
    if error:
        return error
    count = last_result.get("count")
    if count is not None and stage != "idle":
        return f"已发现 {int(count or 0)} 个账户"
    return ""


