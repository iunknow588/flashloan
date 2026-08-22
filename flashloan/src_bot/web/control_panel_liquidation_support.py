"""Helpers for liquidation fork simulation and external command bridging."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from core.sensitive_data import redact_sensitive_text
from execution.dex_costs import USDC
from web.control_panel_liquidation_base import (
    aave_pool_address,
    aave_rpc_urls,
    dex_router_address,
    env_bool,
)


def liquidation_contracts_bot_dir() -> Path:
    configured = os.getenv("LIQUIDATION_CONTRACTS_BOT_DIR", "").strip()
    if configured:
        return Path(configured)
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "contract" / "contracts-bot"


def _set_env_if_blank(env: dict[str, str], name: str, value: str) -> None:
    if value and not str(env.get(name) or "").strip():
        env[name] = value


def _parse_fork_simulation_stdout(stdout: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in str(stdout or "").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in {"network", "mode", "executorSource", "executor", "sender", "user", "debtAsset", "debtToCover", "tx", "status", "gasUsed"}:
            parsed[key] = value.strip()
    return parsed


def run_liquidation_fork_simulation(
    payload: dict,
    *,
    timeout_seconds: int = 180,
    contracts_dir: Path | None = None,
    shutil_module=shutil,
    subprocess_module=subprocess,
    aave_pool_address_fn=aave_pool_address,
    dex_router_address_fn=dex_router_address,
    aave_rpc_urls_fn=aave_rpc_urls,
) -> dict:
    contracts_dir = contracts_dir or liquidation_contracts_bot_dir()
    if not contracts_dir.exists():
        raise RuntimeError(
            "contract/contracts-bot directory not found: "
            f"{contracts_dir}"
        )
    npm = shutil_module.which("npm.cmd") or shutil_module.which("npm")
    if not npm:
        raise RuntimeError("npm is required for liquidation fork simulation")
    use_configured_executor = env_bool("LIQUIDATION_FORK_USE_CONFIGURED_EXECUTOR", True)
    payload_executor = str(payload.get("executor") or "").strip()
    env_executor = str(os.getenv("LIQUIDATION_EXECUTOR_ADDRESS") or "").strip()
    if use_configured_executor and not (payload_executor or env_executor):
        raise RuntimeError("LIQUIDATION_FORK_USE_CONFIGURED_EXECUTOR=true requires LIQUIDATION_EXECUTOR_ADDRESS or payload.executor")
    simulation_payload = {
        "executor": payload_executor or env_executor,
        "request": payload.get("request") or {},
    }
    with tempfile.TemporaryDirectory(prefix="liquidation-fork-") as temp_dir:
        payload_path = Path(temp_dir) / "payload.json"
        payload_path.write_text(json.dumps(simulation_payload, indent=2), encoding="utf-8")
        env = os.environ.copy()
        env["LIQUIDATION_PAYLOAD_PATH"] = str(payload_path)
        _set_env_if_blank(env, "AAVE_POOL_ADDRESS", aave_pool_address_fn())
        _set_env_if_blank(env, "DEX_ROUTER_ADDRESS", dex_router_address_fn())
        _set_env_if_blank(
            env,
            "USDC_ADDRESS",
            os.getenv("LIQUIDATION_USDC_ADDRESS")
            or os.getenv("DYNAMIC_USDC")
            or os.getenv("FUJI_USDC_ADDRESS")
            or os.getenv("FUJI_USDC")
            or USDC,
        )
        rpc_urls = aave_rpc_urls_fn()
        if rpc_urls:
            _set_env_if_blank(env, "AVALANCHE_RPC_URL", rpc_urls[0])
            _set_env_if_blank(env, "AVALANCHE_RPC", rpc_urls[0])
        if use_configured_executor:
            _set_env_if_blank(env, "SIMULATE_USE_CONFIGURED_EXECUTOR", "true")
            _set_env_if_blank(env, "LIQUIDATION_EXECUTOR_ADDRESS", simulation_payload["executor"])
        completed = subprocess_module.run(
            [npm, "run", "simulate:liquidation"],
            cwd=str(contracts_dir),
            env=env,
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout_seconds)),
        )
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if completed.returncode != 0:
        message = (stderr or stdout or f"fork simulation failed with exit code {completed.returncode}").strip()
        raise RuntimeError(redact_sensitive_text(message[-4000:]))
    return {
        "fork_simulation_status": "passed",
        "fork_simulation_passed": True,
        "fork_simulation_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fork_simulation_stdout_tail": stdout.strip()[-2000:],
        "fork_simulation_result": _parse_fork_simulation_stdout(stdout),
    }
