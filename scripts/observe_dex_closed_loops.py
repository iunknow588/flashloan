#!/usr/bin/env python3
"""Observe Robinhood mainnet 4663 closed-loop review for 30+ minutes.

Sample the quote-routes API and the cow-execution attempts log periodically,
record status distributions, and flag any new live_verified/profitable route.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path


def log_setup(log_path: Path | None) -> logging.Logger:
    logger = logging.getLogger("observe_dex_closed_loops")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler = logging.StreamHandler()
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    return logger


def http_json(url: str, *, timeout: float = 60.0) -> dict:
    headers = {"Accept": "application/json"}
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def load_attempts(path: Path, chain_id: int) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        try:
            row_chain = int(row.get("chain_id"))
        except (TypeError, ValueError):
            row_chain = None
        if row_chain == chain_id:
            rows.append(row)
    return rows


def attempt_status_summary(rows: list[dict]) -> dict:
    counter: Counter = Counter()
    live_routes = []
    for row in rows:
        quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
        status = (
            row.get("quote_status")
            or row.get("state")
            or quote.get("quote_status")
            or quote.get("status")
            or "unknown"
        )
        counter[str(status)] += 1
        if str(status).lower() in {"live_verified", "quote_verified"}:
            live_routes.append(
                {
                    "pair": row.get("pair")
                    or row.get("direction")
                    or quote.get("pair")
                    or quote.get("direction")
                    or "-",
                    "bestAmount": row.get("best_amount")
                    or quote.get("analysis", {}).get("bestAmount"),
                    "netProfit": row.get("profit_amount")
                    or quote.get("live_onchain_evidence", {})
                    .get("costModel", {})
                    .get("netProfitAmount"),
                    "txHash": row.get("tx_hash")
                    or quote.get("tx_hash")
                    or "",
                }
            )
    return {
        "statuses": dict(counter),
        "live_routes": live_routes,
        "count": len(rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--chain-id", type=int, default=4663)
    parser.add_argument("--protocols", default="uniswap_v4")
    parser.add_argument("--duration", type=float, default=1800.0)
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--attempts", default="")
    parser.add_argument("--log", default="")
    args = parser.parse_args()

    logger = log_setup(Path(args.log) if args.log else None)
    attempts_path = Path(args.attempts) if args.attempts else (
        Path(__file__).resolve().parents[1]
        / "flashloan"
        / "src"
        / "runtime"
        / "logs"
        / "cow_execution_attempts"
        / "robinhood"
        / str(args.chain_id)
        / "attempts.jsonl"
    )
    query = urllib.parse.urlencode(
        {
            "chain_id": args.chain_id,
            "protocols": args.protocols,
            "dex_strategy": "closed_loop_routes",
        }
    )
    quote_url = f"{args.base_url.rstrip('/')}/api/dex/quote-routes?{query}"
    overview_url = f"{args.base_url.rstrip('/')}/api/dex/overview?{query}"
    start = time.time()
    deadline = start + args.duration
    seen_live: set[str] = set()
    while time.time() < deadline:
        sample_no = int((time.time() - start) // args.interval) + 1
        try:
            quote_payload = http_json(quote_url)
            routes = quote_payload.get("routes") or []
            statuses = Counter(
                str(rt.get("quoteStatus") or rt.get("quote_status") or rt.get("status") or "unknown")
                for rt in routes
            )
            live = [
                {
                    "pair": rt.get("symbolPair") or rt.get("pair") or "-",
                    "bestAmount": (rt.get("quoteAnalysis") or {}).get("bestAmount"),
                    "netProfit": (rt.get("quoteAnalysis") or {}).get("referenceNetProfitAmount"),
                }
                for rt in routes
                if str(rt.get("quoteStatus") or rt.get("quote_status") or "").lower()
                == "live_verified"
            ]
            logger.info(
                "sample=%s quote_routes total=%s statuses=%s live=%s",
                sample_no,
                len(routes),
                dict(statuses),
                live,
            )
            for item in live:
                key = item.get("pair") or "-"
                if key not in seen_live:
                    seen_live.add(key)
                    logger.info("NEW_LIVE_ROUTE %s", item)
        except Exception as exc:
            logger.warning("sample=%s quote_routes error=%s", sample_no, exc)

        try:
            overview_payload = http_json(overview_url)
            logger.info(
                "sample=%s overview path_count=%s closed_loop_count=%s",
                sample_no,
                overview_payload.get("arbitragePathCount"),
                overview_payload.get("closedLoopRouteCount"),
            )
        except Exception as exc:
            logger.warning("sample=%s overview error=%s", sample_no, exc)

        try:
            rows = load_attempts(attempts_path, args.chain_id)
            summary = attempt_status_summary(rows)
            logger.info("sample=%s attempts count=%s statuses=%s", sample_no, summary["count"], summary["statuses"])
            for live_route in summary["live_routes"]:
                key = live_route.get("pair") or "-"
                if key not in seen_live:
                    seen_live.add(key)
                    logger.info("NEW_LIVE_ATTEMPT %s", live_route)
        except Exception as exc:
            logger.warning("sample=%s attempts error=%s", sample_no, exc)

        elapsed = time.time() - start
        remaining = deadline - time.time()
        if remaining > 0:
            time.sleep(min(args.interval, remaining))
        logger.info("done total_elapsed=%.1f seen_live=%s", time.time() - start, sorted(seen_live))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
