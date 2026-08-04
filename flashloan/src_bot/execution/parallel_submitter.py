from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable

from core.sensitive_data import redact_sensitive_text


SubmitCallable = Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class SubmissionAttempt:
    name: str
    submit: SubmitCallable


def run_parallel_submissions(
    attempts: list[SubmissionAttempt],
    *,
    max_workers: int = 3,
) -> dict[str, Any]:
    if not attempts:
        raise ValueError("at least one submission attempt is required")
    workers = max(1, min(int(max_workers), len(attempts)))
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(attempt.submit): attempt for attempt in attempts}
        for future in as_completed(future_map):
            attempt = future_map[future]
            try:
                result = dict(future.result())
                result.setdefault("parallel_attempt_name", attempt.name)
                successes.append(result)
            except Exception as exc:
                failures.append({"name": attempt.name, "error": redact_sensitive_text(exc)})

    if successes:
        winner = successes[0]
        winner["parallel_submission"] = {
            "enabled": True,
            "winner": winner.get("parallel_attempt_name"),
            "success_count": len(successes),
            "failure_count": len(failures),
            "attempts": [
                {
                    "name": item.get("parallel_attempt_name"),
                    "tx_hash": item.get("tx_hash"),
                    "sender": item.get("sender"),
                    "status": "success",
                }
                for item in successes
            ] + [{"name": item["name"], "status": "error", "error": item["error"]} for item in failures],
        }
        return winner

    details = "; ".join(f"{item['name']}: {item['error']}" for item in failures)
    raise RuntimeError(f"all parallel submissions failed: {details}")
