import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Optional

from flask import Flask, Response, jsonify, request

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from execution.dex_costs import estimate_symbol_cost, parse_trade_usd_amounts
from core.env_loader import load_env_files
from execution.execution_payload import PayloadConfig, build_execution_payload
from market.observer import ASSETS
from execution.plan_quotes import quote_execution_plan
from web.control_panel_config import strategy_config as read_strategy_config, write_strategy_config as save_strategy_config
from web.control_panel_data import (
    aave_reserve_cache as read_aave_reserve_cache,
    latest_arbitrage_simulation as read_latest_arbitrage_simulation,
    latest_binance_extremes as read_latest_binance_extremes,
    latest_executable_signal as read_latest_executable_signal,
    observation_count as read_observation_count,
    recent_observations as read_recent_observations,
)
from web.control_panel_stats import testnet_trade_stats as read_testnet_trade_stats, trade_stats as read_trade_stats
from db.storage import ensure_database_schema, require_psycopg


WEB_DIR = Path(__file__).resolve().parent
APP_DIR = SRC_ROOT
RUNTIME_DIR = Path(os.getenv("FLASHLOAN_RUNTIME_DIR", str(APP_DIR / "runtime")))
STATE_DIR = RUNTIME_DIR / "state"
CONFIG_DIR = RUNTIME_DIR / "config"
CACHE_DIR = RUNTIME_DIR / "cache"
OBSERVER_PATH = APP_DIR / "market" / "observer.py"
TEMPLATE_PATH = WEB_DIR / "templates" / "control_panel.html"
LATEST_ARBITRAGE_PATH = STATE_DIR / "latest_arbitrage.json"
LATEST_EXECUTABLE_SIGNAL_PATH = STATE_DIR / "latest_executable_signal.json"
LATEST_EXTREMES_PATH = STATE_DIR / "latest_extremes.json"
AAVE_RESERVE_CACHE_PATH = CACHE_DIR / "aave_reserve_assets.json"
OBSERVER_PID_PATH = RUNTIME_DIR / "observer.pid"
STRATEGY_CONFIG_PATH = CONFIG_DIR / "strategy_config.json"
REPO_ROOT = APP_DIR.parents[1]

load_env_files(__file__)

app = Flask(__name__)
observer_process: Optional[subprocess.Popen] = None
selected_symbols: list[str] = []

def configured_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required. Attach Replit SQL first.")
    return database_url


def is_observer_running() -> bool:
    if observer_process is not None and observer_process.poll() is None:
        return True
    pid = read_observer_pid()
    return pid is not None and process_exists(pid)


def observer_pid() -> Optional[int]:
    if observer_process is not None and observer_process.poll() is None:
        return observer_process.pid
    return read_observer_pid()


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_observer_pid() -> Optional[int]:
    try:
        if not OBSERVER_PID_PATH.exists():
            return None
        pid = int(OBSERVER_PID_PATH.read_text(encoding="utf-8").strip())
        if process_exists(pid):
            return pid
        OBSERVER_PID_PATH.unlink(missing_ok=True)
    except (OSError, ValueError):
        return None
    return None


def write_observer_pid(pid: int) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    OBSERVER_PID_PATH.write_text(str(pid), encoding="utf-8")


def validate_symbols(raw_symbols: object) -> list[str]:
    if not isinstance(raw_symbols, list):
        raise ValueError("symbols must be a list")
    symbols = list(dict.fromkeys(str(value).strip().upper() for value in raw_symbols))
    unsupported = [symbol for symbol in symbols if symbol not in ASSETS]
    if unsupported:
        raise ValueError(f"unsupported symbol: {unsupported[0]}")
    if not symbols:
        raise ValueError("select at least one symbol")
    return symbols


def strategy_config() -> dict:
    return read_strategy_config(STRATEGY_CONFIG_PATH)


def write_strategy_config(payload: dict) -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return save_strategy_config(STRATEGY_CONFIG_PATH, payload)


def safe_latest(fetcher) -> Optional[dict]:
    try:
        return fetcher()
    except Exception:
        return None


def latest_binance_extremes() -> Optional[dict]:
    return read_latest_binance_extremes(configured_database_url(), LATEST_EXTREMES_PATH)


def latest_arbitrage_simulation() -> Optional[dict]:
    return read_latest_arbitrage_simulation(configured_database_url(), LATEST_ARBITRAGE_PATH)


def latest_executable_signal() -> Optional[dict]:
    return read_latest_executable_signal(LATEST_EXECUTABLE_SIGNAL_PATH)


def aave_reserve_cache() -> Optional[dict]:
    return read_aave_reserve_cache(AAVE_RESERVE_CACHE_PATH)


def observation_count() -> Optional[int]:
    return read_observation_count(configured_database_url())


def recent_observations(symbol: str, limit: int) -> list[dict]:
    return read_recent_observations(configured_database_url(), symbol, limit)


def configured_fee_slippage_percent() -> float:
    try:
        return max(0.0, float(os.getenv("FEE_SLIPPAGE_PERCENT", os.getenv("ALERT_DIFF_PERCENT", "0.30"))))
    except ValueError:
        return 0.30


def latest_reference_price(symbol: str) -> float:
    rows = recent_observations(symbol, 1)
    if not rows:
        raise RuntimeError(f"No observations found for {symbol}")
    return float(rows[-1]["aave_price"])


def read_slippage_bps() -> int:
    return int(strategy_config()["EXECUTION_SLIPPAGE_BPS"])


def read_execution_plan_max_age_seconds() -> float:
    return float(strategy_config()["EXECUTION_PLAN_MAX_AGE_SECONDS"])


def read_require_binance_ws_for_execution() -> bool:
    return os.getenv("ARBITRAGE_REQUIRE_BINANCE_WS", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def assert_fresh_execution_plan(simulation: dict) -> None:
    if not simulation.get("signal"):
        reasons = ", ".join(simulation.get("blocked_reasons") or ["signal is false"])
        raise RuntimeError(f"execution plan is blocked: {reasons}")

    if read_require_binance_ws_for_execution() and simulation.get("price_source") != "ws":
        raise RuntimeError("execution plan is blocked: Binance WebSocket price source is required")

    observed_at = str(simulation.get("observed_at", "")).replace("Z", "+00:00")
    try:
        observed = datetime.fromisoformat(observed_at)
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise RuntimeError("execution plan is blocked: invalid observed_at") from exc

    age_seconds = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
    max_age = read_execution_plan_max_age_seconds()
    if age_seconds > max_age:
        raise RuntimeError(
            f"execution plan is blocked: stale plan age={age_seconds:.1f}s max={max_age:.1f}s"
        )


@app.get("/")
def index():
    asset_items = "\n".join(
        f'<label class="asset"><input type="checkbox" value="{escape(symbol)}" checked>'
        f'<span><strong>{escape(symbol)}</strong><small>{escape(asset.symbol)}</small></span></label>'
        for symbol, asset in ASSETS.items()
    )
    chart_options = "\n".join(f'<option value="{escape(symbol)}">{escape(symbol)}</option>' for symbol in ASSETS)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace("__ASSET_ITEMS__", asset_items).replace("__CHART_OPTIONS__", chart_options)


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.get("/favicon.ico")
def favicon():
    return Response(status=204)


@app.get("/api/status")
def status():
    running = is_observer_running()
    return jsonify(
        {
            "running": running,
            "pid": observer_pid() if running else None,
            "symbols": selected_symbols if running else [],
            "rows": observation_count(),
            "binance_extremes": safe_latest(latest_binance_extremes),
            "arbitrage_simulation": safe_latest(latest_arbitrage_simulation),
            "executable_signal": safe_latest(latest_executable_signal),
            "aave_reserve_cache": safe_latest(aave_reserve_cache),
            "strategy_config": strategy_config(),
            "trade_stats": safe_latest(lambda: read_trade_stats(configured_database_url())),
            "testnet_trade_stats": safe_latest(lambda: read_testnet_trade_stats(REPO_ROOT)),
        }
    )


@app.get("/api/strategy-config")
def get_strategy_config():
    return jsonify({"config": strategy_config(), "running": is_observer_running()})


@app.post("/api/strategy-config")
def post_strategy_config():
    try:
        config = write_strategy_config(request.get_json(silent=True) or {})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"config": config, "restart_required": is_observer_running()})


@app.get("/api/trade-stats")
def get_trade_stats():
    return jsonify({"stats": safe_latest(lambda: read_trade_stats(configured_database_url()))})


@app.get("/api/testnet-trade-stats")
def get_testnet_trade_stats():
    return jsonify({"stats": safe_latest(lambda: read_testnet_trade_stats(REPO_ROOT))})


@app.get("/api/observations")
def observations():
    symbol = request.args.get("symbol", "AVAXUSDT").strip().upper()
    if symbol not in ASSETS:
        return jsonify({"error": f"unsupported symbol: {symbol}"}), 400
    try:
        limit = max(2, min(int(request.args.get("limit", "120")), 1000))
        rows = recent_observations(symbol, limit)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"symbol": symbol, "limit": limit, "fee_slippage_percent": configured_fee_slippage_percent(), "rows": rows})


@app.get("/api/binance-extremes/latest")
def binance_extremes_latest():
    return jsonify({"extremes": safe_latest(latest_binance_extremes)})


@app.get("/api/arbitrage/latest")
def arbitrage_latest():
    return jsonify({"simulation": safe_latest(latest_arbitrage_simulation)})


@app.get("/api/trigger/latest")
def trigger_latest():
    return jsonify({"trigger": safe_latest(latest_arbitrage_simulation)})


@app.get("/api/executable-signal/latest")
def executable_signal_latest():
    return jsonify({"executable_signal": safe_latest(latest_executable_signal)})


@app.get("/api/execution-plan/quote")
def execution_plan_quote():
    try:
        simulation = latest_arbitrage_simulation()
        if not simulation or not simulation.get("execution_plan"):
            return jsonify({"error": "latest arbitrage result has no execution_plan"}), 404
        assert_fresh_execution_plan(simulation)
        rpc_url = os.getenv("AVALANCHE_RPC", "https://api.avax.network/ext/bc/C/rpc").strip()
        router = os.getenv("DEX_ROUTER_ADDRESS", "0x60aE616a2155Ee3d9A68541Ba4544862310933d4").strip()
        quote = quote_execution_plan(
            simulation["execution_plan"],
            rpc_url=rpc_url,
            router_address=router,
            slippage_bps=read_slippage_bps(),
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"quote": quote})


@app.get("/api/execution-plan/payload")
def execution_plan_payload():
    try:
        simulation = latest_arbitrage_simulation()
        if not simulation or not simulation.get("execution_plan"):
            return jsonify({"error": "latest arbitrage result has no execution_plan"}), 404
        assert_fresh_execution_plan(simulation)
        rpc_url = os.getenv("AVALANCHE_RPC", "https://api.avax.network/ext/bc/C/rpc").strip()
        router = os.getenv("DEX_ROUTER_ADDRESS", "0x60aE616a2155Ee3d9A68541Ba4544862310933d4").strip()
        quote = quote_execution_plan(
            simulation["execution_plan"],
            rpc_url=rpc_url,
            router_address=router,
            slippage_bps=read_slippage_bps(),
        )
        payload = build_execution_payload(
            simulation["execution_plan"],
            quote,
            PayloadConfig(
                min_profit_usdc=float(request.args.get("min_profit_usdc", "0")),
                deadline_seconds=int(request.args.get("deadline_seconds", "600")),
            ),
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"payload": payload})


@app.get("/api/dex-costs")
def dex_costs():
    symbol = request.args.get("symbol", "AVAXUSDT").strip().upper()
    if symbol not in ASSETS:
        return jsonify({"error": f"unsupported symbol: {symbol}"}), 400
    try:
        amounts = parse_trade_usd_amounts(os.getenv("DEX_COST_USD_AMOUNTS"))
        reference_price = latest_reference_price(symbol)
        rpc_url = os.getenv("AVALANCHE_RPC", "https://api.avax.network/ext/bc/C/rpc").strip()
        router = os.getenv("DEX_ROUTER_ADDRESS", "0x60aE616a2155Ee3d9A68541Ba4544862310933d4").strip()
        costs = [estimate_symbol_cost(rpc_url, symbol, amount, reference_price, router) for amount in amounts]
        payload = [
            {
                "amount_usd": quote.amount_usd,
                "buy_cost_percent": quote.buy_cost_percent,
                "sell_cost_percent": quote.sell_cost_percent,
                "roundtrip_cost_percent": quote.roundtrip_cost_percent,
                "buy_price_usd": quote.buy_price_usd,
                "sell_price_usd": quote.sell_price_usd,
                "token_amount": quote.token_amount,
            }
            for quote in costs
            if quote is not None
        ]
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"symbol": symbol, "dex_name": "Trader Joe V2", "reference_price_usd": reference_price, "costs": payload})


@app.post("/api/start")
def start():
    global observer_process, selected_symbols
    if is_observer_running():
        return jsonify({"error": "observer is already running"}), 409
    try:
        symbols = validate_symbols((request.get_json(silent=True) or {}).get("symbols"))
        ensure_database_schema(configured_database_url())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    env = os.environ.copy()
    env["SYMBOLS"] = ",".join(symbols)
    for key, value in strategy_config().items():
        env[key] = str(value)
    observer_process = subprocess.Popen([sys.executable, str(OBSERVER_PATH)], cwd=str(APP_DIR), env=env)
    write_observer_pid(observer_process.pid)
    selected_symbols = symbols
    return jsonify({"running": True, "pid": observer_process.pid, "symbols": symbols})


@app.post("/api/init")
def init_database():
    try:
        ensure_database_schema(configured_database_url())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"initialized": True, "rows": observation_count()})


@app.post("/api/stop")
def stop():
    global observer_process, selected_symbols
    if is_observer_running():
        if observer_process is not None and observer_process.poll() is None:
            observer_process.terminate()
            try:
                observer_process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                observer_process.kill()
                observer_process.wait(timeout=3)
        else:
            pid = read_observer_pid()
            if pid is not None:
                os.kill(pid, signal.SIGTERM)
    observer_process = None
    selected_symbols = []
    OBSERVER_PID_PATH.unlink(missing_ok=True)
    return jsonify({"running": False})


@app.post("/api/clear")
def clear():
    try:
        ensure_database_schema(configured_database_url())
        psycopg = require_psycopg()
        with psycopg.connect(configured_database_url(), connect_timeout=8) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "TRUNCATE TABLE observations, binance_price_history, "
                    "binance_window_extremes, arbitrage_simulations RESTART IDENTITY"
                )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"cleared": True, "rows": 0})


@app.post("/api/clear-files")
def clear_files():
    deleted, errors = [], []
    for path in [APP_DIR / "observations.csv", LATEST_ARBITRAGE_PATH, LATEST_EXTREMES_PATH, LATEST_EXECUTABLE_SIGNAL_PATH]:
        if path.exists():
            try:
                path.unlink()
                deleted.append(str(path))
            except OSError as exc:
                errors.append(f"{path}: {exc}")
    return jsonify({"deleted": deleted, "errors": errors}), 400 if errors else 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
