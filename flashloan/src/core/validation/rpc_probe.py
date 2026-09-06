from __future__ import annotations

import json
import time
from typing import Any, Callable
from urllib.request import Request, urlopen


def probe_rpc_urls(
    urls: list[str],
    *,
    timeout_seconds: float = 5.0,
    transport: Callable[[str, dict[str, Any], float], Any] | None = None,
) -> dict[str, Any]:
    attempts = []
    for index, url in enumerate(dict.fromkeys(str(item).strip() for item in urls if str(item).strip())):
        started = time.perf_counter()
        try:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}
            result = transport(url, payload, timeout_seconds) if transport else _http_json_rpc(url, payload, timeout_seconds)
            elapsed = (time.perf_counter() - started) * 1000.0
            chain_id = result.get("result") if isinstance(result, dict) else None
            attempts.append({"index": index, "url": url, "ok": bool(chain_id), "latency_ms": elapsed, "chain_id": chain_id, "error": None})
            if chain_id:
                return {"status": "connected", "selected_index": index, "selected_url": url, "attempts": attempts, "fallback_count": index}
        except Exception as exc:
            attempts.append({"index": index, "url": url, "ok": False, "latency_ms": (time.perf_counter() - started) * 1000.0, "chain_id": None, "error": str(exc)[:240]})
    return {"status": "all_failed", "selected_index": None, "selected_url": None, "attempts": attempts, "fallback_count": max(0, len(attempts) - 1)}


def _http_json_rpc(url: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=max(0.5, float(timeout_seconds))) as response:
        return json.loads(response.read().decode("utf-8"))

