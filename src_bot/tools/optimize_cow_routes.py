from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from execution.cow_routes import (  # noqa: E402
    DEFAULT_OWNER,
    build_token_registry,
    evaluate_cow_route,
    rank_cow_routes,
    read_route_specs,
)


def default_aave_cache_path() -> Path:
    return SRC_ROOT / "runtime" / "cache" / "aave_reserve_assets.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Rank fixed CoW Protocol route candidates on Avalanche.")
    parser.add_argument("--routes", required=True, help="JSON file containing route candidates.")
    parser.add_argument("--amount", default="1000", help="Default human input amount for routes without amount.")
    parser.add_argument("--owner", default=DEFAULT_OWNER, help="Address used for quote fee estimation.")
    parser.add_argument("--price-quality", default="fast", choices=["fast", "optimal", "verified"])
    parser.add_argument("--valid-for", type=int, default=180)
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--out", default="", help="Optional output JSON path.")
    args = parser.parse_args()

    routes, defaults = read_route_specs(Path(args.routes))
    registry = build_token_registry(aave_cache_path=default_aave_cache_path(), include_cow_token_list=True)
    owner = str(defaults.get("owner") or args.owner)
    amount = defaults.get("amount", args.amount)
    price_quality = str(defaults.get("price_quality") or args.price_quality)
    valid_for = int(defaults.get("valid_for") or args.valid_for)

    results = [
        evaluate_cow_route(
            route,
            registry=registry,
            default_amount=amount,
            owner=owner,
            price_quality=price_quality,
            valid_for=valid_for,
        )
        for route in routes
    ]
    ranked = rank_cow_routes(results)
    payload = {
        "route_count": len(routes),
        "viable_count": sum(1 for item in ranked if item.get("viable")),
        "best": ranked[0] if ranked else None,
        "ranking": ranked[: max(1, int(args.top))],
    }

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
