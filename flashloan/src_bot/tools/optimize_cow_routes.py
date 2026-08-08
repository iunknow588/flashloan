from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files  # noqa: E402
from cow_flashloan.routes import (  # noqa: E402
    build_token_registry,
    cow_network_config,
    default_cow_owner,
    evaluate_cow_route,
    rank_cow_routes,
    read_route_specs,
)

load_env_files(__file__, override=False)


def default_aave_cache_path() -> Path:
    return SRC_ROOT / "runtime" / "cache" / "aave_reserve_assets.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Rank fixed CoW Protocol route candidates with CoW Protocol quotes.")
    parser.add_argument("--routes", required=True, help="JSON file containing route candidates.")
    parser.add_argument("--amount", default="1000", help="Default human input amount for routes without amount.")
    parser.add_argument("--owner", default="", help="Address used for quote fee estimation.")
    parser.add_argument("--cow-network", default="avalanche", help="CoW API network for this run, e.g. avalanche.")
    parser.add_argument("--price-quality", default="fast", choices=["fast", "optimal", "verified"])
    parser.add_argument("--valid-for", type=int, default=180)
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--out", default="", help="Optional output JSON path.")
    args = parser.parse_args()

    routes, defaults = read_route_specs(Path(args.routes))
    network_config = cow_network_config(network=args.cow_network)
    default_owner = default_cow_owner(network_config.network)
    registry = build_token_registry(
        aave_cache_path=default_aave_cache_path(),
        include_cow_token_list=True,
        cow_network=network_config.network,
    )
    owner = str(defaults.get("owner") or args.owner or default_owner)
    amount = defaults.get("amount", args.amount)
    price_quality = str(defaults.get("price_quality") or args.price_quality)
    valid_for = int(defaults.get("valid_for") or args.valid_for)

    results = [
        evaluate_cow_route(
            route,
            registry=registry,
            default_amount=amount,
            owner=owner,
            cow_network=network_config.network,
            price_quality=price_quality,
            valid_for=valid_for,
        )
        for route in routes
    ]
    ranked = rank_cow_routes(results)
    payload = {
        "route_count": len(routes),
        "viable_count": sum(1 for item in ranked if item.get("viable")),
        "cow_network": network_config.network,
        "cow_chain_id": network_config.chain_id,
        "cow_testnet": network_config.testnet,
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
