"""Reusable pool-edge selection and triangular route discovery."""

from __future__ import annotations

from itertools import combinations
from typing import Any


_QUALITY_RANK = {"recommended": 0, "improvable": 1, "rejected": 2}


def pair_key(left: str, right: str) -> str:
    return ":".join(sorted([str(left), str(right)]))


def all_unordered_pairs(symbols: list[str]) -> list[tuple[str, str]]:
    return list(combinations(symbols, 2))


def best_edges_by_pair(pools: list[dict[str, Any]], allowed: set[str]) -> dict[tuple[str, str], dict[str, Any]]:
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for pool in pools:
        if str(pool.get("status")) not in allowed:
            continue
        pair = str(pool.get("symbolPair") or "")
        tokens = [part for part in pair.split(":") if part]
        if len(tokens) != 2:
            continue
        key = tuple(sorted(tokens))
        score = (_QUALITY_RANK.get(str(pool.get("status")), 9), -float(pool.get("tvlUsd") or 0))
        previous = best.get(key)
        previous_score = (
            _QUALITY_RANK.get(str(previous.get("status")), 9),
            -float(previous.get("tvlUsd") or 0),
        ) if previous else None
        if previous is None or score < previous_score:
            best[key] = pool
    return best


def triangle_routes(best_edges: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    nodes = sorted({node for key in best_edges for node in key})
    routes: list[dict[str, Any]] = []
    for a, b, c in combinations(nodes, 3):
        keys = [tuple(sorted(pair)) for pair in ((a, b), (b, c), (a, c))]
        if not all(key in best_edges for key in keys):
            continue
        edges = [best_edges[key] for key in keys]
        routes.append(
            {
                "path": f"{a} -> {b} -> {c} -> {a}",
                "status": "recommended" if all(edge.get("status") == "recommended" for edge in edges) else "observable",
                "minTvlUsd": min(float(edge.get("tvlUsd") or 0) for edge in edges),
                "edges": edges,
            }
        )
    routes.sort(key=lambda item: (0 if item["status"] == "recommended" else 1, -float(item["minTvlUsd"])))
    return routes
