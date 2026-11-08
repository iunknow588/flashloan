#!/usr/bin/env python3
"""Measure Robinhood Web/API health, latency, and process resource stability.

The tool is intentionally read-only with respect to the business database. It
uses HTTP probes for the service and OS process counters for RSS/thread
observation. The optional submission write probe calls the production clear
endpoint with an explicit dry run and idempotency headers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "logs" / "plan" / "runtime_evidence" / "reports"
DEFAULT_PROCESS_MARKER = "flashloan/app_runtime/run.py"
SCHEMA = "robinhood-api-status-measurement-v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(float(values[0]), 3)
    ordered = sorted(float(value) for value in values)
    position = max(0.0, min(1.0, float(fraction))) * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    weight = position - lower
    value = ordered[lower] * (1.0 - weight) + ordered[upper] * weight
    return round(value, 3)


def compact_json(value: Any, limit: int = 500) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = str(value)
    return text[:limit]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--network", default="robinhood")
    parser.add_argument("--chain-id", type=int, default=4663)
    parser.add_argument("--protocol", default="uniswap_v4")
    parser.add_argument(
        "--market-id",
        default="dex-arbitrage-4663-uniswap-v4",
    )
    parser.add_argument("--status-samples", type=int, default=40)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--warmup-seconds", type=float, default=0.0)
    parser.add_argument("--resource-duration-seconds", type=float, default=0.0)
    parser.add_argument("--resource-interval-seconds", type=float, default=10.0)
    parser.add_argument("--required-resource-seconds", type=float, default=1800.0)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--process-marker", default=DEFAULT_PROCESS_MARKER)
    parser.add_argument("--max-rss-growth-mb", type=float, default=64.0)
    parser.add_argument("--max-thread-growth", type=int, default=2)
    parser.add_argument(
        "--include-submission-dry-run",
        action="store_true",
        help="Probe the clear endpoint with dryRun=true and idempotency headers.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 2 when any P0-1 gate fails.",
    )
    args = parser.parse_args(argv)
    if not str(args.base_url).strip():
        parser.error("--base-url must not be empty")
    if args.status_samples < 1:
        parser.error("--status-samples must be at least 1")
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.resource_duration_seconds < 0:
        parser.error("--resource-duration-seconds must not be negative")
    if args.resource_interval_seconds <= 0:
        parser.error("--resource-interval-seconds must be positive")
    if args.required_resource_seconds < 0:
        parser.error("--required-resource-seconds must not be negative")
    if args.max_rss_growth_mb < 0:
        parser.error("--max-rss-growth-mb must not be negative")
    if args.max_thread_growth < 0:
        parser.error("--max-thread-growth must not be negative")
    if args.pid is not None and args.pid <= 0:
        parser.error("--pid must be positive")
    return args


def scoped_url(base_url: str, path: str, args: argparse.Namespace) -> str:
    query = urllib.parse.urlencode(
        {
            "network": str(args.network).strip().lower(),
            "chain_id": int(args.chain_id),
            "protocol": str(args.protocol).strip().lower(),
            "market_id": str(args.market_id).strip().lower(),
        }
    )
    separator = "&" if "?" in path else "?"
    return f"{base_url.rstrip('/')}{path}{separator}{query}"


def payload_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"type": type(payload).__name__}
    summary: dict[str, Any] = {
        "keys": sorted(str(key) for key in payload.keys())[:40],
    }
    for key in (
        "status",
        "ok",
        "running",
        "starting",
        "pid",
        "mode",
        "dataSource",
        "error",
        "code",
        "chain_id",
        "network",
        "market_id",
    ):
        if key in payload and isinstance(payload.get(key), (str, int, float, bool, type(None))):
            summary[key] = payload.get(key)
    chain_runtime = payload.get("chain_runtime")
    if isinstance(chain_runtime, dict):
        summary["chain_runtime"] = {
            key: chain_runtime.get(key)
            for key in ("chain_id", "network", "dataSource", "runtime_core")
            if key in chain_runtime
        }
    control_status = payload.get("control_status")
    if isinstance(control_status, dict):
        summary["control_status"] = {
            key: control_status.get(key)
            for key in ("state", "stage", "message", "percent")
            if key in control_status
        }
    return summary


def extract_authorities(payload: Any, *, max_depth: int = 6) -> dict[str, Any]:
    found: dict[str, Any] = {}

    def walk(value: Any, depth: int) -> None:
        if depth > max_depth or len(found) >= 40:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                name = str(key)
                lower = name.lower()
                if (
                    ("authority" in lower or lower == "datasource")
                    and isinstance(item, (str, int, float, bool, type(None)))
                    and name not in found
                ):
                    found[name] = item
                walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value[:40]:
                walk(item, depth + 1)

    walk(payload, 0)
    return dict(sorted(found.items()))


def request_once(
    url: str,
    *,
    method: str = "GET",
    timeout: float,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "flashloan-p0-1-status-probe/1",
        **(headers or {}),
    }
    encoded_body: bytes | None = None
    if body is not None:
        encoded_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=encoded_body,
        headers=request_headers,
        method=method,
    )
    started_at = utc_now()
    started = time.perf_counter()
    status: int | None = None
    response_headers: dict[str, str] = {}
    response_bytes = b""
    error: str | None = None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            response_headers = {
                key.lower(): value
                for key, value in response.headers.items()
                if key.lower() in {"content-type", "content-length", "x-request-id"}
            }
            response_bytes = response.read()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        response_headers = {
            key.lower(): value
            for key, value in exc.headers.items()
            if key.lower() in {"content-type", "content-length", "x-request-id"}
        }
        try:
            response_bytes = exc.read()
        except Exception:
            response_bytes = b""
        error = f"HTTP {exc.code}: {exc.reason}"
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:500]}"
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    parsed: Any = None
    parse_error: str | None = None
    if response_bytes:
        try:
            parsed = json.loads(response_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            parse_error = f"{type(exc).__name__}: {str(exc)[:300]}"
    return {
        "startedAt": started_at,
        "method": method,
        "url": url,
        "status": status,
        "elapsedMs": elapsed_ms,
        "bodyBytes": len(response_bytes),
        "bodySha256": hashlib.sha256(response_bytes).hexdigest() if response_bytes else None,
        "contentType": response_headers.get("content-type"),
        "requestId": response_headers.get("x-request-id"),
        "error": error,
        "parseError": parse_error,
        "payload": payload_summary(parsed),
        "_payloadForEvaluation": parsed,
    }


def public_sample(sample: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in sample.items() if not key.startswith("_")}


def endpoint_summary(
    samples: list[dict[str, Any]],
    *,
    expected_statuses: set[int],
) -> dict[str, Any]:
    latencies = [
        float(sample["elapsedMs"])
        for sample in samples
        if isinstance(sample.get("elapsedMs"), (int, float))
    ]
    body_sizes = [
        int(sample["bodyBytes"])
        for sample in samples
        if isinstance(sample.get("bodyBytes"), int)
    ]
    statuses = [
        int(sample["status"])
        for sample in samples
        if isinstance(sample.get("status"), int)
    ]
    failed = [
        sample
        for sample in samples
        if (
            sample.get("status") not in expected_statuses
            or sample.get("error")
            or sample.get("parseError")
        )
    ]
    unexpected_5xx = [
        sample
        for sample in samples
        if isinstance(sample.get("status"), int)
        and sample["status"] >= 500
        and sample["status"] not in expected_statuses
    ]
    return {
        "sampleCount": len(samples),
        "expectedStatuses": sorted(expected_statuses),
        "statusCodes": sorted(set(statuses)),
        "successCount": len(samples) - len(failed),
        "failureCount": len(failed),
        "errorRate": round(len(failed) / len(samples), 6) if samples else 1.0,
        "unexpected5xxCount": len(unexpected_5xx),
        "latencyMs": {
            "min": round(min(latencies), 3) if latencies else None,
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "max": round(max(latencies), 3) if latencies else None,
            "mean": round(statistics.fmean(latencies), 3) if latencies else None,
        },
        "bodyBytes": {
            "min": min(body_sizes) if body_sizes else None,
            "p50": percentile([float(value) for value in body_sizes], 0.50),
            "p95": percentile([float(value) for value in body_sizes], 0.95),
            "max": max(body_sizes) if body_sizes else None,
        },
    }


def run_series(
    url: str,
    *,
    count: int,
    concurrency: int,
    timeout: float,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    indexed: list[tuple[int, dict[str, Any]]] = []
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(
                request_once,
                url,
                method=method,
                timeout=timeout,
                headers=headers,
                body=body,
            ): index
            for index in range(count)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                sample = future.result()
            except Exception as exc:
                sample = {
                    "startedAt": utc_now(),
                    "method": method,
                    "url": url,
                    "status": None,
                    "elapsedMs": 0.0,
                    "bodyBytes": 0,
                    "bodySha256": None,
                    "contentType": None,
                    "requestId": None,
                    "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                    "parseError": None,
                    "payload": {},
                }
                failures.append(f"sample {index}: {sample['error']}")
            sample["sequence"] = index
            indexed.append((index, sample))
    indexed.sort(key=lambda item: item[0])
    return [sample for _, sample in indexed], failures


def resolve_process_identity(pid: int | None) -> dict[str, Any]:
    if pid is None:
        return {"status": "unresolved", "pid": None}
    if os.name == "nt":
        script = (
            "$ErrorActionPreference='Stop';"
            f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\";"
            "if($null -eq $p){exit 3};"
            "[pscustomobject]@{pid=[int]$p.ProcessId;name=$p.Name;"
            "commandLine=$p.CommandLine}|ConvertTo-Json -Compress"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                timeout=15,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                return {
                    "status": "not_found",
                    "pid": int(pid),
                    "error": result.stderr.decode(errors="replace").strip()[:500],
                }
            payload = json.loads(result.stdout.decode("utf-8", errors="replace"))
            return {"status": "resolved", **payload}
        except Exception as exc:
            return {
                "status": "error",
                "pid": int(pid),
                "error": f"{type(exc).__name__}: {str(exc)[:500]}",
            }
    proc = Path("/proc") / str(int(pid))
    try:
        command = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(
            errors="replace"
        )
        comm = (proc / "comm").read_text(encoding="utf-8", errors="replace").strip()
        return {
            "status": "resolved",
            "pid": int(pid),
            "name": comm,
            "commandLine": command.strip(),
        }
    except Exception as exc:
        return {
            "status": "error",
            "pid": int(pid),
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
        }


def resolve_port_owner(base_url: str) -> dict[str, Any]:
    parsed = urllib.parse.urlsplit(base_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if os.name != "nt":
        return {"status": "unsupported", "port": port}
    host = str(parsed.hostname or "").lower()
    script = (
        "$ErrorActionPreference='Stop';"
        f"$connections=Get-NetTCPConnection -State Listen -LocalPort {int(port)} "
        "-ErrorAction SilentlyContinue;"
        "if(-not $connections){[pscustomobject]@{status='not_listening';"
        f"port={int(port)}}}|ConvertTo-Json -Compress;exit 0;}};"
        f"$hostName='{host}';"
        "$connection=$connections|Sort-Object "
        "@{Expression={if($_.LocalAddress -eq $hostName){0}elseif($_.LocalAddress -in "
        "@('0.0.0.0','::')){1}else{2}}}|Select-Object -First 1;"
        "$procId=[int]$connection.OwningProcess;"
        "$process=Get-CimInstance Win32_Process -Filter \"ProcessId=$procId\";"
        "[pscustomobject]@{status='resolved';port=@($connections|ForEach-Object "
        "{[int]$_.LocalPort});localAddresses=@($connections|ForEach-Object "
        "{$_.LocalAddress});pid=$procId;name=$process.Name;"
        "commandLine=$process.CommandLine}|ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=20,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            return {
                "status": "error",
                "port": port,
                "error": result.stderr.decode(errors="replace").strip()[:500],
            }
        text = result.stdout.decode("utf-8", errors="replace").strip()
        return json.loads(text) if text else {"status": "not_listening", "port": port}
    except Exception as exc:
        return {
            "status": "error",
            "port": port,
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
        }


def process_marker_matches(identity: dict[str, Any], marker: str) -> bool:
    if (identity.get("status") != "resolved") or not str(marker).strip():
        return False
    command = str(identity.get("commandLine") or "").replace("\\", "/").lower()
    expected = str(marker).replace("\\", "/").lower()
    return expected in command


def read_process_metrics(pid: int) -> dict[str, Any]:
    sampled_at = utc_now()
    if os.name == "nt":
        script = (
            "$ErrorActionPreference='Stop';"
            f"$p=Get-Process -Id {int(pid)};"
            "[pscustomobject]@{rssBytes=[int64]$p.WorkingSet64;"
            "threadCount=[int]@($p.Threads).Count;"
            "handleCount=[int]$p.HandleCount;"
            "cpuSeconds=if($null -ne $p.CPU){[double]$p.CPU}else{$null};"
            "responding=[bool]$p.Responding}|ConvertTo-Json -Compress"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                timeout=15,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                raise RuntimeError(
                    result.stderr.decode(errors="replace").strip()[:500]
                    or f"Get-Process exited {result.returncode}"
                )
            payload = json.loads(result.stdout.decode("utf-8", errors="replace"))
            rss_bytes = int(payload["rssBytes"])
            return {
                "sampledAt": sampled_at,
                "pid": int(pid),
                "rssBytes": rss_bytes,
                "rssMb": round(rss_bytes / (1024 * 1024), 3),
                "threadCount": int(payload["threadCount"]),
                "handleCount": int(payload.get("handleCount") or 0),
                "cpuSeconds": payload.get("cpuSeconds"),
                "responding": bool(payload.get("responding")),
            }
        except Exception as exc:
            return {
                "sampledAt": sampled_at,
                "pid": int(pid),
                "error": f"{type(exc).__name__}: {str(exc)[:500]}",
            }
    status_path = Path("/proc") / str(int(pid)) / "status"
    try:
        values: dict[str, str] = {}
        for line in status_path.read_text(encoding="utf-8").splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                values[key.strip()] = value.strip()
        rss_kb = int(values.get("VmRSS", "0 kB").split()[0])
        return {
            "sampledAt": sampled_at,
            "pid": int(pid),
            "rssBytes": rss_kb * 1024,
            "rssMb": round(rss_kb / 1024.0, 3),
            "threadCount": int(values.get("Threads") or 0),
            "handleCount": None,
            "cpuSeconds": None,
            "responding": True,
        }
    except Exception as exc:
        return {
            "sampledAt": sampled_at,
            "pid": int(pid),
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
        }


def sample_resources(
    pid: int,
    *,
    duration_seconds: float,
    interval_seconds: float,
) -> dict[str, Any]:
    started_at = utc_now()
    started = time.monotonic()
    samples: list[dict[str, Any]] = []
    while True:
        samples.append(read_process_metrics(pid))
        elapsed = time.monotonic() - started
        if elapsed >= duration_seconds:
            break
        time.sleep(min(interval_seconds, max(0.0, duration_seconds - elapsed)))
    ended_at = utc_now()
    valid = [
        sample
        for sample in samples
        if isinstance(sample.get("rssBytes"), int)
        and isinstance(sample.get("threadCount"), int)
    ]
    errors = [sample for sample in samples if sample.get("error")]
    result: dict[str, Any] = {
        "startedAt": started_at,
        "endedAt": ended_at,
        "requestedDurationSeconds": round(duration_seconds, 3),
        "observedDurationSeconds": round(time.monotonic() - started, 3),
        "intervalSeconds": round(interval_seconds, 3),
        "samples": samples,
        "sampleCount": len(samples),
        "validSampleCount": len(valid),
        "errorCount": len(errors),
    }
    if valid:
        rss_values = [float(sample["rssBytes"]) / (1024 * 1024) for sample in valid]
        thread_values = [int(sample["threadCount"]) for sample in valid]
        split = max(1, math.ceil(len(valid) / 4))
        first_rss = statistics.median(rss_values[:split])
        last_rss = statistics.median(rss_values[-split:])
        first_threads = statistics.median(thread_values[:split])
        last_threads = statistics.median(thread_values[-split:])
        times = [float(index) for index in range(len(rss_values))]
        mean_time = statistics.fmean(times)
        mean_rss = statistics.fmean(rss_values)
        denominator = sum((value - mean_time) ** 2 for value in times)
        slope = (
            sum(
                (times[index] - mean_time) * (rss_values[index] - mean_rss)
                for index in range(len(rss_values))
            )
            / denominator
            if denominator
            else 0.0
        )
        result["summary"] = {
            "rssMb": {
                "min": round(min(rss_values), 3),
                "p50": percentile(rss_values, 0.50),
                "max": round(max(rss_values), 3),
                "firstQuartileMedian": round(first_rss, 3),
                "lastQuartileMedian": round(last_rss, 3),
                "deltaMb": round(last_rss - first_rss, 3),
                "slopeMbPerSample": round(slope, 6),
            },
            "threads": {
                "min": min(thread_values),
                "p50": percentile([float(value) for value in thread_values], 0.50),
                "max": max(thread_values),
                "firstQuartileMedian": round(first_threads, 3),
                "lastQuartileMedian": round(last_threads, 3),
                "delta": round(last_threads - first_threads, 3),
            },
        }
    else:
        result["summary"] = None
    return result


def resource_stability_gate(
    *,
    applicable: bool,
    duration_passed: bool,
    growth_passed: bool,
    required_duration_seconds: float,
    observed_duration_seconds: float | None,
    rss_delta_mb: float | None,
    max_rss_growth_mb: float,
    thread_delta: float | None,
    max_thread_growth: int,
) -> dict[str, Any]:
    passed = not applicable or (duration_passed and growth_passed)
    return {
        "passed": passed,
        "applicable": applicable,
        "durationPassed": duration_passed,
        "growthPassed": growth_passed,
        "requiredDurationSeconds": required_duration_seconds,
        "observedDurationSeconds": observed_duration_seconds,
        "rssDeltaMb": rss_delta_mb,
        "maxRssGrowthMb": max_rss_growth_mb,
        "threadDelta": thread_delta,
        "maxThreadGrowth": max_thread_growth,
    }


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 3
    generated_at = utc_now()
    output_path = args.output or (
        DEFAULT_OUTPUT_DIR / f"robinhood_api_status_{utc_stamp()}.json"
    )
    blockers: list[str] = []
    warnings: list[str] = []

    health_url = f"{args.base_url.rstrip('/')}/healthz"
    status_url = f"{args.base_url.rstrip('/')}/api/status"
    submission_url = scoped_url(
        args.base_url,
        "/api/dex/submissions",
        args,
    )

    health_samples, health_exec_errors = run_series(
        health_url,
        count=1,
        concurrency=1,
        timeout=args.timeout,
    )
    submission_samples, submission_exec_errors = run_series(
        submission_url,
        count=1,
        concurrency=1,
        timeout=args.timeout,
    )
    status_warmup, status_warmup_errors = run_series(
        status_url,
        count=1,
        concurrency=1,
        timeout=args.timeout,
    )
    status_samples, status_exec_errors = run_series(
        status_url,
        count=args.status_samples,
        concurrency=args.concurrency,
        timeout=args.timeout,
    )

    submission_dry_run_samples: list[dict[str, Any]] = []
    submission_dry_run_errors: list[str] = []
    if args.include_submission_dry_run:
        run_id = f"p0-1-{utc_stamp()}"
        dry_run_url = scoped_url(
            args.base_url,
            "/api/dex/submissions/clear?record_kind=submission",
            args,
        )
        submission_dry_run_samples, submission_dry_run_errors = run_series(
            dry_run_url,
            count=1,
            concurrency=1,
            timeout=args.timeout,
            method="POST",
            headers={
                "X-Request-Id": run_id,
                "Idempotency-Key": f"{run_id}-clear",
            },
            body={"record_kind": "submission", "dryRun": True},
        )

    summaries = {
        "health": endpoint_summary(
            health_samples,
            expected_statuses={200},
        ),
        "status": endpoint_summary(
            status_warmup + status_samples,
            expected_statuses={200},
        ),
        "statusConcurrent": endpoint_summary(
            status_samples,
            expected_statuses={200},
        ),
        "submissionRead": endpoint_summary(
            submission_samples,
            expected_statuses={200},
        ),
    }
    if args.include_submission_dry_run:
        summaries["submissionDryRun"] = endpoint_summary(
            submission_dry_run_samples,
            expected_statuses={200},
        )

    status_payloads = [
        sample.get("_payloadForEvaluation")
        for sample in status_warmup + status_samples
        if isinstance(sample.get("_payloadForEvaluation"), dict)
    ]
    authorities: dict[str, Any] = {}
    for payload in status_payloads:
        for key, value in extract_authorities(payload).items():
            authorities.setdefault(key, value)

    parsed_base = urllib.parse.urlsplit(args.base_url)
    port_owner = resolve_port_owner(args.base_url)
    process_identity = (
        resolve_process_identity(args.pid)
        if args.pid is not None
        else (
            resolve_process_identity(int(port_owner["pid"]))
            if isinstance(port_owner.get("pid"), int)
            else {"status": "unresolved", "pid": None, **port_owner}
        )
    )
    marker_matches = process_marker_matches(process_identity, args.process_marker)
    process_identity["expectedMarker"] = args.process_marker
    process_identity["matchesExpectedMarker"] = marker_matches

    if args.warmup_seconds > 0:
        time.sleep(args.warmup_seconds)

    resource_observation: dict[str, Any] | None = None
    if args.resource_duration_seconds > 0:
        process_pid = process_identity.get("pid")
        if not isinstance(process_pid, int) or process_pid <= 0:
            blockers.append("resource_observation_missing_process_pid")
        elif not marker_matches:
            blockers.append("resource_observation_process_identity_unverified")
        else:
            resource_observation = sample_resources(
                process_pid,
                duration_seconds=args.resource_duration_seconds,
                interval_seconds=args.resource_interval_seconds,
            )

    health_ok = (
        summaries["health"]["successCount"] == 1
        and summaries["health"]["unexpected5xxCount"] == 0
    )
    health_payloads = [
        sample.get("_payloadForEvaluation") for sample in health_samples
    ]
    health_contract_ok = any(
        isinstance(payload, dict) and payload.get("status") == "ok"
        for payload in health_payloads
    )
    status_ok = (
        summaries["status"]["failureCount"] == 0
        and summaries["status"]["unexpected5xxCount"] == 0
    )
    status_contract_ok = bool(status_payloads) and all(
        isinstance(payload, dict)
        and "running" in payload
        and isinstance(payload.get("control_status"), dict)
        for payload in status_payloads
    )
    submission_ok = (
        summaries["submissionRead"]["failureCount"] == 0
        and summaries["submissionRead"]["unexpected5xxCount"] == 0
    )
    submission_payloads = [
        sample.get("_payloadForEvaluation") for sample in submission_samples
    ]
    submission_contract_ok = bool(submission_payloads) and all(
        isinstance(payload, dict) for payload in submission_payloads
    )
    dry_run_ok = (
        not args.include_submission_dry_run
        or (
            summaries["submissionDryRun"]["failureCount"] == 0
            and summaries["submissionDryRun"]["unexpected5xxCount"] == 0
        )
    )
    dry_run_payloads = [
        sample.get("_payloadForEvaluation")
        for sample in submission_dry_run_samples
    ]
    dry_run_contract_ok = (
        not args.include_submission_dry_run
        or (
            bool(dry_run_payloads)
            and all(
                isinstance(payload, dict)
                and payload.get("ok") is True
                and payload.get("dryRun") is True
                for payload in dry_run_payloads
            )
        )
    )
    status_p95 = summaries["statusConcurrent"]["latencyMs"]["p95"]
    latency_ok = (
        isinstance(status_p95, (int, float))
        and float(status_p95) <= 2000.0
        and summaries["statusConcurrent"]["errorRate"] == 0.0
    )
    resource_applicable = args.resource_duration_seconds > 0
    resource_duration_ok = (
        resource_applicable
        and isinstance(resource_observation, dict)
        and float(resource_observation.get("observedDurationSeconds") or 0.0)
        >= float(args.required_resource_seconds)
    )
    resource_summary = (
        resource_observation.get("summary")
        if isinstance(resource_observation, dict)
        else None
    )
    rss_delta = (
        resource_summary.get("rssMb", {}).get("deltaMb")
        if isinstance(resource_summary, dict)
        else None
    )
    thread_delta = (
        resource_summary.get("threads", {}).get("delta")
        if isinstance(resource_summary, dict)
        else None
    )
    resource_growth_ok = (
        resource_duration_ok
        and isinstance(resource_observation, dict)
        and resource_observation.get("errorCount") == 0
        and isinstance(rss_delta, (int, float))
        and isinstance(thread_delta, (int, float))
        and float(rss_delta) <= float(args.max_rss_growth_mb)
        and float(thread_delta) <= float(args.max_thread_growth)
    )

    if not health_ok:
        blockers.append("health_probe_failed")
    if not health_contract_ok:
        blockers.append("health_response_contract_failed")
    if not status_ok:
        blockers.append("status_probe_failed")
    if not status_contract_ok:
        blockers.append("status_response_contract_failed")
    if not submission_ok:
        blockers.append("submission_read_probe_failed")
    if not submission_contract_ok:
        blockers.append("submission_read_contract_failed")
    if not dry_run_ok:
        blockers.append("submission_dry_run_probe_failed")
    if not dry_run_contract_ok:
        blockers.append("submission_dry_run_contract_failed")
    if not latency_ok:
        blockers.append("status_concurrent_p95_or_error_gate_failed")
    if resource_applicable and not resource_duration_ok:
        blockers.append("resource_observation_duration_insufficient")
    if resource_applicable and not resource_growth_ok:
        blockers.append("resource_growth_gate_failed")
    if not resource_applicable:
        warnings.append("resource_observation_not_requested")

    for name, error_list in (
        ("health", health_exec_errors),
        ("statusWarmup", status_warmup_errors),
        ("statusConcurrent", status_exec_errors),
        ("submissionRead", submission_exec_errors),
        ("submissionDryRun", submission_dry_run_errors),
    ):
        for message in error_list:
            warnings.append(f"{name}: {message}")

    gates = {
        "healthAndCoreApis": {
            "passed": (
                health_ok
                and health_contract_ok
                and status_ok
                and status_contract_ok
                and submission_ok
                and submission_contract_ok
                and dry_run_ok
                and dry_run_contract_ok
            ),
            "health": health_ok,
            "healthContract": health_contract_ok,
            "status": status_ok,
            "statusContract": status_contract_ok,
            "submissionRead": submission_ok,
            "submissionReadContract": submission_contract_ok,
            "submissionDryRun": dry_run_ok,
        },
        "statusConcurrentLatency": {
            "passed": latency_ok,
            "concurrency": args.concurrency,
            "sampleCount": args.status_samples,
            "p95Ms": status_p95,
            "thresholdMs": 2000.0,
            "errorRate": summaries["statusConcurrent"]["errorRate"],
        },
        "resourceStability": resource_stability_gate(
            applicable=resource_applicable,
            duration_passed=resource_duration_ok,
            growth_passed=resource_growth_ok,
            required_duration_seconds=args.required_resource_seconds,
            observed_duration_seconds=(
                resource_observation.get("observedDurationSeconds")
                if isinstance(resource_observation, dict)
                else None
            ),
            rss_delta_mb=rss_delta,
            max_rss_growth_mb=args.max_rss_growth_mb,
            thread_delta=thread_delta,
            max_thread_growth=args.max_thread_growth,
        ),
    }
    gates["allPassed"] = all(
        bool(value.get("passed"))
        for value in (
            gates["healthAndCoreApis"],
            gates["statusConcurrentLatency"],
            gates["resourceStability"],
        )
    )
    if gates["allPassed"] and blockers:
        gates["allPassed"] = False

    output = {
        "schema": SCHEMA,
        "taskId": "P0-1",
        "generatedAt": generated_at,
        "mode": "http-and-process-read-only",
        "scope": {
            "network": str(args.network).strip().lower(),
            "chainId": int(args.chain_id),
            "protocol": str(args.protocol).strip().lower(),
            "marketId": str(args.market_id).strip().lower(),
            "primary": (
                int(args.chain_id) == 4663
                and str(args.network).strip().lower() == "robinhood"
            ),
        },
        "authorities": authorities,
        "server": {
            "baseUrl": args.base_url,
            "scheme": parsed_base.scheme,
            "host": parsed_base.hostname,
            "port": parsed_base.port or (443 if parsed_base.scheme == "https" else 80),
            "portOwner": port_owner,
            "processIdentity": process_identity,
        },
        "configuration": {
            "statusSamples": args.status_samples,
            "concurrency": args.concurrency,
            "timeoutSeconds": args.timeout,
            "warmupSeconds": args.warmup_seconds,
            "resourceDurationSeconds": args.resource_duration_seconds,
            "resourceIntervalSeconds": args.resource_interval_seconds,
            "requiredResourceSeconds": args.required_resource_seconds,
            "includeSubmissionDryRun": args.include_submission_dry_run,
            "maxRssGrowthMb": args.max_rss_growth_mb,
            "maxThreadGrowth": args.max_thread_growth,
        },
        "endpoints": {
            "health": {
                "method": "GET",
                "url": health_url,
                "expectedStatuses": [200],
            },
            "status": {
                "method": "GET",
                "url": status_url,
                "expectedStatuses": [200],
            },
            "submissionRead": {
                "method": "GET",
                "url": submission_url,
                "expectedStatuses": [200],
            },
            "submissionDryRun": (
                {
                    "method": "POST",
                    "path": "/api/dex/submissions/clear",
                    "body": {"record_kind": "submission", "dryRun": True},
                    "expectedStatuses": [200],
                }
                if args.include_submission_dry_run
                else None
            ),
        },
        "samples": {
            "health": [public_sample(sample) for sample in health_samples],
            "statusWarmup": [public_sample(sample) for sample in status_warmup],
            "statusConcurrent": [public_sample(sample) for sample in status_samples],
            "submissionRead": [public_sample(sample) for sample in submission_samples],
            "submissionDryRun": [
                public_sample(sample) for sample in submission_dry_run_samples
            ],
        },
        "summaries": summaries,
        "resourceObservation": resource_observation,
        "gates": gates,
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings)),
        "conclusion": (
            "passed"
            if gates["allPassed"]
            else "blocked"
            if not health_ok
            else "failed"
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {output_path}")
    print(
        "health={health} status={status} submission={submission} "
        "p95={p95}ms allPassed={passed}".format(
            health="OK" if health_ok else "FAIL",
            status="OK" if status_ok else "FAIL",
            submission="OK" if submission_ok else "FAIL",
            p95=status_p95,
            passed=gates["allPassed"],
        )
    )
    if blockers:
        print("blockers: " + "; ".join(output["blockers"]))
    if args.strict and not gates["allPassed"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
