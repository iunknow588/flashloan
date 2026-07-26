import argparse
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import matplotlib

# Replit/服务器通常没有图形桌面，Agg 后端可以直接生成 PNG 文件。
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

LOG = logging.getLogger("plot_prices")

DEFAULT_OUTPUT_DIR = "charts"


@dataclass(frozen=True)
class Observation:
    """一行价格观测数据。"""

    observed_at: datetime
    symbol: str
    asset: str
    binance_price: float
    aave_price: float
    diff_percent: float


def parse_time(value: str) -> datetime:
    """解析 observer.py 写入的 UTC ISO 时间。"""

    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def require_psycopg():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: install psycopg[binary] or run pip install -r requirements.txt"
        ) from exc
    return psycopg


def load_observations(
    database_url: str, symbols: Optional[set[str]]
) -> dict[str, list[Observation]]:
    """从 Replit SQL/Postgres 读取观测数据，并按交易对分组。"""

    grouped: dict[str, list[Observation]] = defaultdict(list)
    params: list[object] = []
    where = ""
    if symbols:
        placeholders = ",".join("%s" for _ in symbols)
        where = f"WHERE UPPER(symbol) IN ({placeholders})"
        params = sorted(symbols)

    query = f"""
        SELECT observed_at, symbol, asset, binance_price, aave_price, diff_percent
        FROM observations
        {where}
        ORDER BY symbol, observed_at
    """
    psycopg = require_psycopg()
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        for (
            observed_at,
            symbol_raw,
            asset_raw,
            binance_price,
            aave_price,
            diff_percent,
        ) in rows:
            symbol = str(symbol_raw).strip().upper()
            if not symbol:
                continue
            grouped[symbol].append(
                Observation(
                    observed_at=parse_time(str(observed_at)),
                    symbol=symbol,
                    asset=str(asset_raw or symbol).strip() or symbol,
                    binance_price=float(binance_price),
                    aave_price=float(aave_price),
                    diff_percent=float(diff_percent),
                )
            )

    return dict(grouped)


def keep_last(observations: list[Observation], limit: int) -> list[Observation]:
    if limit <= 0 or len(observations) <= limit:
        return observations
    return observations[-limit:]


def plot_symbol(observations: list[Observation], output_dir: Path) -> Path:
    """为单个交易对生成价格曲线和价差曲线。"""

    symbol = observations[0].symbol
    asset = observations[0].asset
    times = [item.observed_at for item in observations]
    binance_prices = [item.binance_price for item in observations]
    aave_prices = [item.aave_price for item in observations]
    diffs = [item.diff_percent for item in observations]

    fig, (price_ax, diff_ax) = plt.subplots(
        2,
        1,
        figsize=(13, 7),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )

    price_ax.plot(
        times, binance_prices, label="Binance aggTrade", color="#2563eb", linewidth=1.5
    )
    price_ax.plot(
        times,
        aave_prices,
        label="Aave Oracle",
        color="#dc2626",
        linewidth=1.5,
        drawstyle="steps-post",
    )
    price_ax.set_title(f"{symbol} / {asset}: Binance vs Aave Oracle")
    price_ax.set_ylabel("Price / USD")
    price_ax.grid(True, alpha=0.25)
    price_ax.legend(loc="best")

    diff_ax.axhline(0, color="#111827", linewidth=0.8, alpha=0.6)
    diff_ax.plot(times, diffs, label="Diff %", color="#16a34a", linewidth=1.2)
    diff_ax.fill_between(times, diffs, 0, color="#16a34a", alpha=0.12)
    diff_ax.set_ylabel("Diff %")
    diff_ax.set_xlabel("Time (UTC)")
    diff_ax.grid(True, alpha=0.25)

    # 时间刻度自动压缩，长时间运行后的图不会挤成一团。
    locator = mdates.AutoDateLocator(minticks=5, maxticks=10)
    formatter = mdates.ConciseDateFormatter(locator)
    diff_ax.xaxis.set_major_locator(locator)
    diff_ax.xaxis.set_major_formatter(formatter)

    fig.tight_layout()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{symbol.lower()}_price_diff.png"
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def summarize_symbol(observations: list[Observation]) -> str:
    """生成一个简单摘要，用来快速判断价差是否值得继续研究。"""

    diffs = [item.diff_percent for item in observations]
    abs_diffs = [abs(value) for value in diffs]
    max_abs = max(abs_diffs)
    avg_abs = sum(abs_diffs) / len(abs_diffs)
    positive_count = sum(1 for value in diffs if value > 0)
    negative_count = sum(1 for value in diffs if value < 0)
    first_time = observations[0].observed_at.isoformat(timespec="seconds")
    last_time = observations[-1].observed_at.isoformat(timespec="seconds")
    return (
        f"rows={len(observations)} window={first_time}..{last_time} "
        f"avg_abs_diff={avg_abs:.5f}% max_abs_diff={max_abs:.5f}% "
        f"positive={positive_count} negative={negative_count}"
    )


def parse_symbols(raw: Optional[str]) -> Optional[set[str]]:
    if not raw:
        return None
    symbols = {part.strip().upper() for part in raw.split(",") if part.strip()}
    return symbols or None


def positive_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("value must be >= 0")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plot Binance and Aave price curves from observer data."
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="Replit SQL/Postgres DATABASE_URL",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for generated PNG charts",
    )
    parser.add_argument(
        "--symbols", help="Comma-separated symbols, for example AVAXUSDT,ETHUSDT"
    )
    parser.add_argument(
        "--last",
        type=positive_int,
        default=0,
        help="Only plot the last N rows per symbol; 0 means all rows",
    )
    return parser


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)sZ %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    args = build_parser().parse_args()
    output_dir = Path(args.output_dir)
    symbols = parse_symbols(args.symbols)

    if not args.database_url:
        raise RuntimeError(
            "DATABASE_URL is required. Run inside Replit with SQL attached or pass --database-url."
        )

    LOG.info("reading database")
    grouped = load_observations(args.database_url, symbols)

    if not grouped:
        raise RuntimeError(
            "No observations found. Run observer.py first and check DATABASE_URL."
        )

    for symbol, observations in grouped.items():
        if len(observations) < 2:
            LOG.warning(
                "skip %s: need at least 2 rows, got %s", symbol, len(observations)
            )
            continue
        plotted_observations = keep_last(observations, args.last)
        output_path = plot_symbol(plotted_observations, output_dir)
        LOG.info("chart written: %s", output_path)
        LOG.info("summary %s: %s", symbol, summarize_symbol(plotted_observations))


if __name__ == "__main__":
    main()
