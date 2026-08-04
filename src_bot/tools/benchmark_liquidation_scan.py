from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

from web3 import Web3

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from execution import liquidation_scan
from execution.liquidation_scan import LiquidationScanConfig, scan_account_health


def synthetic_accounts(count: int) -> list[str]:
    return [Web3.to_checksum_address(f"0x{index + 1:040x}") for index in range(max(0, int(count)))]


def synthetic_account_data(account: str, index: int) -> dict:
    health_factor = 0.98 if index % 17 == 0 else (1.02 if index % 5 == 0 else 1.35)
    debt_base = 1000.0 if health_factor < 1.05 else 100.0
    return {
        "account": account,
        "total_collateral_base": debt_base * 1.2,
        "total_debt_base": debt_base,
        "available_borrows_base": 0.0,
        "current_liquidation_threshold": 8000,
        "ltv": 7500,
        "health_factor": health_factor,
        "account_data_source": "synthetic_multicall3",
    }


def run_synthetic_benchmark(account_count: int = 5000, batch_size: int = 100) -> dict:
    accounts = synthetic_accounts(account_count)
    index_by_account = {account: index for index, account in enumerate(accounts)}
    batch_calls: list[int] = []

    original_batch = liquidation_scan.fetch_user_account_data_batch
    original_single = liquidation_scan.fetch_user_account_data

    def fake_batch(pool_address, batch_accounts, rpc_url, multicall3_address, batch_size):
        batch_accounts = list(batch_accounts)
        size = max(1, int(batch_size or 100))
        for offset in range(0, len(batch_accounts), size):
            batch_calls.append(len(batch_accounts[offset : offset + size]))
        return {
            account: synthetic_account_data(account, index_by_account[account])
            for account in batch_accounts
        }

    def fail_single(pool_address, account, rpc_url):
        raise AssertionError("single-account RPC fallback was not expected in the synthetic benchmark")

    liquidation_scan.fetch_user_account_data_batch = fake_batch
    liquidation_scan.fetch_user_account_data = fail_single
    started = time.perf_counter()
    try:
        rows = scan_account_health(
            accounts,
            "0x0000000000000000000000000000000000000001",
            "https://rpc.example",
            LiquidationScanConfig(
                max_candidates=account_count,
                parallel_workers=1,
                batch_size=batch_size,
                multicall3_address="0xcA11bde05977b3631167028862bE2a173976CA11",
            ),
        )
    finally:
        liquidation_scan.fetch_user_account_data_batch = original_batch
        liquidation_scan.fetch_user_account_data = original_single
    elapsed = time.perf_counter() - started
    return {
        "account_count": len(rows),
        "batch_count": len(batch_calls),
        "batch_size": batch_size,
        "elapsed_seconds": elapsed,
        "liquidatable_count": sum(1 for row in rows if row.get("status") == "liquidatable"),
        "warning_count": sum(1 for row in rows if row.get("status") == "warning"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a synthetic liquidation scan benchmark.")
    parser.add_argument("--accounts", type=int, default=5000)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--target-seconds", type=float, default=30.0)
    args = parser.parse_args()

    result = run_synthetic_benchmark(args.accounts, args.batch_size)
    print(
        "accounts={account_count} batches={batch_count} batch_size={batch_size} "
        "elapsed={elapsed_seconds:.3f}s liquidatable={liquidatable_count} warning={warning_count}".format(**result)
    )
    if result["elapsed_seconds"] > args.target_seconds:
        raise SystemExit(f"benchmark exceeded target {args.target_seconds:.3f}s")


if __name__ == "__main__":
    main()
