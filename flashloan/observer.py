import asyncio
import json
import logging
import os
import signal
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, Optional

import websockets
from web3 import Web3


BINANCE_WS_BASE = "wss://stream.binance.com:9443/stream?streams="
DEFAULT_RPC = "https://api.avax.network/ext/bc/C/rpc"
DEFAULT_SYMBOLS = "AVAXUSDT,ETHUSDT,BTCUSDT,AAVEUSDT,USDCUSDT"

# Aave V3 Avalanche Price Oracle address. This script only reads public on-chain data.
AAVE_ORACLE = "0xEBd36016B3eD09D4693Ed4251c67Bd858c3c7C9C"
LOG = logging.getLogger("observer")

# 最小 ABI：只保留本脚本需要调用的两个只读方法。
ORACLE_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "asset", "type": "address"}],
        "name": "getAssetPrice",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "BASE_CURRENCY_UNIT",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


@dataclass(frozen=True)
class AssetConfig:
    """一组可观测资产的 Binance 交易对和 Avalanche token 地址。"""

    symbol: str
    asset_address: str
    binance_symbol: str


@dataclass(frozen=True)
class ObserverConfig:
    """运行配置。所有字段都来自环境变量或默认值，便于部署到 Replit Secrets。"""

    rpc_url: str
    symbols: list[str]
    sample_seconds: float
    poll_seconds: float
    report_seconds: float
    alert_diff_percent: float
    database_url: str
    stale_seconds: float
    run_seconds: float
    report_only_alerts: bool


ASSETS: Dict[str, AssetConfig] = {
    "AVAXUSDT": AssetConfig(
        "WAVAX", "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7", "AVAXUSDT"
    ),
    "ETHUSDT": AssetConfig(
        "WETH.e", "0x49D5c2BdFfac6CE2BFdB6640F4F80f226bc10bAB", "ETHUSDT"
    ),
    "BTCUSDT": AssetConfig(
        "BTC.b", "0x152b9d0FdC40C096757F570A51E494bd4b943E50", "BTCUSDT"
    ),
    "AAVEUSDT": AssetConfig(
        "AAVE.e", "0x63a72806098Bd3D9520cC43356dD78afe5D386D9", "AAVEUSDT"
    ),
    "USDCUSDT": AssetConfig(
        "USDC", "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E", "USDCUSDT"
    ),
}


class PriceState:
    """异步任务之间共享的最新价格快照。

    Binance WebSocket 和 Aave RPC 轮询是两个独立任务，所以这里用 asyncio.Lock
    保护读写，避免 reporter 读取到半更新状态。
    """

    def __init__(self) -> None:
        self.binance: Dict[str, dict] = {}
        self.aave: Dict[str, dict] = {}
        self.lock = asyncio.Lock()

    async def update_binance(self, symbol: str, price: float, event_ms: int) -> None:
        async with self.lock:
            self.binance[symbol] = {
                "price": price,
                "event_ms": event_ms,
                "seen_at": now_iso(),
            }

    async def update_aave(self, symbol: str, price: float, block: int) -> None:
        async with self.lock:
            self.aave[symbol] = {"price": price, "block": block, "seen_at": now_iso()}

    async def snapshot(self) -> dict:
        async with self.lock:
            return {"binance": dict(self.binance), "aave": dict(self.aave)}


class UtcFormatter(logging.Formatter):
    """让日志中的 asctime 使用 UTC，避免 Replit、本地和服务器时区不一致。"""

    converter = time.gmtime


def setup_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(
        UtcFormatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    )
    logging.basicConfig(level=level, handlers=[handler], force=True)


def env_float(name: str, default: float) -> float:
    """读取浮点型环境变量；填错时回退到默认值，避免 Replit 启动即退出。"""

    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        LOG.warning("invalid float env %s=%r; using default=%s", name, value, default)
        return default


def env_bool(name: str, default: bool) -> bool:
    """读取布尔环境变量，支持 Replit Secrets 中常见的 true/false 写法。"""

    value = os.getenv(name)
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    LOG.warning("invalid bool env %s=%r; using default=%s", name, value, default)
    return default


def env_symbols() -> list[str]:
    """读取并过滤交易对，只保留脚本已配置 Avalanche token 地址的资产。"""

    raw = os.getenv("SYMBOLS", DEFAULT_SYMBOLS)
    symbols = [part.strip().upper() for part in raw.split(",") if part.strip()]
    selected = [symbol for symbol in symbols if symbol in ASSETS]
    ignored = [symbol for symbol in symbols if symbol not in ASSETS]
    if ignored:
        LOG.warning("unsupported symbols ignored: %s", ",".join(ignored))
    if not selected:
        raise ValueError("No supported symbols selected. Check SYMBOLS.")
    return list(dict.fromkeys(selected))


def load_config() -> ObserverConfig:
    """集中加载配置，部署到 Replit 时只需要改 Secrets。"""

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise ValueError(
            "DATABASE_URL is required. Create and attach the Replit SQL database first."
        )

    config = ObserverConfig(
        rpc_url=os.getenv("AVALANCHE_RPC", DEFAULT_RPC).strip(),
        symbols=env_symbols(),
        sample_seconds=max(0.05, env_float("SAMPLE_SECONDS", 0.5)),
        poll_seconds=max(0.2, env_float("AAVE_POLL_SECONDS", 5.0)),
        report_seconds=max(0.5, env_float("REPORT_SECONDS", 2.0)),
        alert_diff_percent=max(0.0, env_float("ALERT_DIFF_PERCENT", 0.30)),
        database_url=database_url,
        stale_seconds=max(1.0, env_float("STALE_SECONDS", 30.0)),
        run_seconds=max(0.0, env_float("RUN_SECONDS", 0.0)),
        report_only_alerts=env_bool("REPORT_ONLY_ALERTS", False),
    )
    if not config.rpc_url.startswith(("http://", "https://")):
        raise ValueError("AVALANCHE_RPC must be an HTTP or HTTPS URL.")
    return config


def mask_url(url: str) -> str:
    """日志里隐藏 RPC URL 的 query 部分，避免泄露付费 RPC key。"""

    if "?" not in url:
        return url
    prefix, _ = url.split("?", 1)
    return f"{prefix}?***"


def now_iso() -> str:
    """统一使用 UTC ISO 时间，后续拿到 Colab 里更容易分析。"""

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def utc_from_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    )


def age_seconds(iso_timestamp: str) -> float:
    timestamp = datetime.fromisoformat(iso_timestamp)
    return max(0.0, (datetime.now(timezone.utc) - timestamp).total_seconds())


def pct_diff(binance_price: float, aave_price: float) -> Optional[float]:
    """计算 Binance 相对 Aave Oracle 的百分比价差。"""

    if aave_price <= 0:
        return None
    return (binance_price - aave_price) / aave_price * 100


async def binance_listener(
    symbols: Iterable[str], state: PriceState, stop: asyncio.Event
) -> None:
    """持续监听 Binance 成交流。

    网络断开后会指数退避重连。Replit 免费环境偶发网络抖动时，
    这个任务不会因为一次连接失败就退出。
    """

    symbol_list = list(symbols)
    streams = "/".join(f"{symbol.lower()}@aggTrade" for symbol in symbol_list)
    url = BINANCE_WS_BASE + streams
    reconnect_delay = 1.0

    while not stop.is_set():
        try:
            LOG.info("binance connecting symbols=%s", ",".join(symbol_list))
            async with websockets.connect(
                url, ping_interval=20, ping_timeout=20, close_timeout=5, max_queue=2048
            ) as ws:
                LOG.info("binance connected")
                reconnect_delay = 1.0
                async for raw in ws:
                    if stop.is_set():
                        break
                    try:
                        message = json.loads(raw)
                        data = message.get("data", {})
                        symbol = data.get("s")
                        price_raw = data.get("p")
                        event_ms = int(data.get("E", int(time.time() * 1000)))
                        if symbol and price_raw:
                            await state.update_binance(
                                symbol, float(price_raw), event_ms
                            )
                    except (
                        KeyError,
                        TypeError,
                        ValueError,
                        json.JSONDecodeError,
                    ) as exc:
                        LOG.debug(
                            "ignored malformed binance message error=%r raw=%r",
                            exc,
                            raw[:200],
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.warning("binance error=%r reconnect_in=%.1fs", exc, reconnect_delay)
            await sleep_until_next(stop, reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)


async def wait_for_oracle_base_unit(oracle, stop: asyncio.Event) -> int:
    """等待 Aave Oracle 可用。

    公共 RPC 可能临时失败，启动阶段也做重试，减少 Replit 冷启动失败。
    """

    retry_delay = 1.0
    while not stop.is_set():
        try:
            base_unit = await asyncio.to_thread(
                oracle.functions.BASE_CURRENCY_UNIT().call
            )
            if base_unit <= 0:
                raise RuntimeError("Aave oracle BASE_CURRENCY_UNIT returned zero.")
            LOG.info("aave oracle ready base_unit=%s", base_unit)
            return base_unit
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.warning("aave oracle init failed=%r retry_in=%.1fs", exc, retry_delay)
            await sleep_until_next(stop, retry_delay)
            retry_delay = min(retry_delay * 2, 60)
    raise asyncio.CancelledError


async def aave_poller(
    config: ObserverConfig, state: PriceState, stop: asyncio.Event
) -> None:
    """定时读取 Aave Oracle 价格。

    这里按固定间隔批量读取目标资产，避免每条 Binance WebSocket 消息都打 RPC。
    """

    w3 = Web3(Web3.HTTPProvider(config.rpc_url, request_kwargs={"timeout": 10}))
    oracle = w3.eth.contract(
        address=Web3.to_checksum_address(AAVE_ORACLE), abi=ORACLE_ABI
    )
    base_unit = await wait_for_oracle_base_unit(oracle, stop)

    while not stop.is_set():
        started = time.monotonic()
        try:
            block = await asyncio.to_thread(lambda: w3.eth.block_number)
            for symbol in config.symbols:
                asset = ASSETS[symbol]
                raw_price = await asyncio.to_thread(
                    oracle.functions.getAssetPrice(
                        Web3.to_checksum_address(asset.asset_address)
                    ).call
                )
                await state.update_aave(symbol, raw_price / base_unit, block)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.warning("aave_poll error=%r", exc)

        elapsed = time.monotonic() - started
        await sleep_until_next(stop, max(0.1, config.poll_seconds - elapsed))


async def reporter(
    config: ObserverConfig, state: PriceState, stop: asyncio.Event
) -> None:
    """输出控制台报告，并把完整快照追加写入持久 SQL 数据库。"""

    last_report_at = 0.0
    while not stop.is_set():
        report_due = time.monotonic() - last_report_at >= config.report_seconds
        snapshot = await state.snapshot()
        rows = []

        for symbol in config.symbols:
            b = snapshot["binance"].get(symbol)
            a = snapshot["aave"].get(symbol)
            if not b or not a:
                continue

            diff = pct_diff(b["price"], a["price"])
            if diff is None:
                continue

            binance_age = age_seconds(b["seen_at"])
            aave_age = age_seconds(a["seen_at"])
            row = {
                "observed_at": now_iso(),
                "symbol": symbol,
                "asset": ASSETS[symbol].symbol,
                "binance_price": f"{b['price']:.10f}",
                "binance_event_time": utc_from_ms(b["event_ms"]),
                "aave_price": f"{a['price']:.10f}",
                "aave_block": a["block"],
                "diff_percent": f"{diff:.6f}",
                "binance_age_seconds": f"{binance_age:.3f}",
                "aave_age_seconds": f"{aave_age:.3f}",
            }
            rows.append(row)

            stale = (
                binance_age > config.stale_seconds or aave_age > config.stale_seconds
            )
            tag = (
                "STALE"
                if stale
                else "ALERT"
                if abs(diff) >= config.alert_diff_percent
                else "OK"
            )
            should_log = tag != "OK" or (report_due and not config.report_only_alerts)
            if should_log:
                LOG.info(
                    "%s %s binance=%.6f aave=%.6f diff=%+.4f%% block=%s",
                    tag,
                    symbol,
                    b["price"],
                    a["price"],
                    diff,
                    a["block"],
                )

        if report_due:
            last_report_at = time.monotonic()

        if rows:
            try:
                await asyncio.to_thread(append_observations, config.database_url, rows)
            except Exception as exc:
                LOG.warning("database write failed error=%r", exc)
        elif report_due:
            LOG.info("waiting for first complete price snapshot")

        await sleep_until_next(stop, config.sample_seconds)


async def auto_stop_after(seconds: float, stop: asyncio.Event) -> None:
    """测试辅助：RUN_SECONDS > 0 时自动退出，方便 Replit 首次验证。"""

    if seconds <= 0:
        return
    await sleep_until_next(stop, seconds)
    if not stop.is_set():
        LOG.info("run_seconds reached; stopping")
        stop.set()


async def sleep_until_next(stop: asyncio.Event, seconds: float) -> None:
    """可被 stop 事件提前打断的 sleep。"""

    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        return


def require_psycopg():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: install psycopg[binary] or run pip install -r requirements.txt"
        ) from exc
    return psycopg


def ensure_database_schema(database_url: str) -> None:
    """Create the durable observations table in Replit SQL/Postgres."""

    psycopg = require_psycopg()
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS observations (
                    id BIGSERIAL PRIMARY KEY,
                    observed_at TIMESTAMPTZ NOT NULL,
                    symbol TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    binance_price DOUBLE PRECISION NOT NULL,
                    binance_event_time TIMESTAMPTZ NOT NULL,
                    aave_price DOUBLE PRECISION NOT NULL,
                    aave_block BIGINT NOT NULL,
                    diff_percent DOUBLE PRECISION NOT NULL,
                    binance_age_seconds DOUBLE PRECISION NOT NULL,
                    aave_age_seconds DOUBLE PRECISION NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_observations_symbol_time
                ON observations(symbol, observed_at)
                """
            )


def append_observations(database_url: str, rows: list[dict]) -> None:
    """Append observation rows to Replit SQL/Postgres in one transaction."""

    psycopg = require_psycopg()
    values = [
        (
            row["observed_at"],
            row["symbol"],
            row["asset"],
            float(row["binance_price"]),
            row["binance_event_time"],
            float(row["aave_price"]),
            int(row["aave_block"]),
            float(row["diff_percent"]),
            float(row["binance_age_seconds"]),
            float(row["aave_age_seconds"]),
        )
        for row in rows
    ]
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO observations (
                    observed_at,
                    symbol,
                    asset,
                    binance_price,
                    binance_event_time,
                    aave_price,
                    aave_block,
                    diff_percent,
                    binance_age_seconds,
                    aave_age_seconds
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                values,
            )


async def main() -> None:
    setup_logging()
    config = load_config()
    state = PriceState()
    stop = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    LOG.info("observer started")
    LOG.info("no private key required; observation only")
    await asyncio.to_thread(ensure_database_schema, config.database_url)
    LOG.info("database ready")
    LOG.info(
        "config symbols=%s sample_seconds=%.3f poll_seconds=%.1f report_seconds=%.1f alert_diff_percent=%.4f stale_seconds=%.1f report_only_alerts=%s",
        ",".join(config.symbols),
        config.sample_seconds,
        config.poll_seconds,
        config.report_seconds,
        config.alert_diff_percent,
        config.stale_seconds,
        config.report_only_alerts,
    )
    LOG.info(
        "database_url=%s rpc_url=%s",
        mask_url(config.database_url),
        mask_url(config.rpc_url),
    )

    tasks = [
        asyncio.create_task(binance_listener(config.symbols, state, stop)),
        asyncio.create_task(aave_poller(config, state, stop)),
        asyncio.create_task(reporter(config, state, stop)),
        asyncio.create_task(auto_stop_after(config.run_seconds, stop)),
    ]

    with suppress(asyncio.CancelledError):
        await stop.wait()
    LOG.info("stopping")

    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        LOG.info("keyboard interrupt; stopped")
    except Exception as exc:
        LOG.exception("fatal error=%r", exc)
        raise
