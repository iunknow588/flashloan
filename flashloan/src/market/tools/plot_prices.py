import argparse
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import matplotlib

# Server environments usually have no GUI; Agg can write PNG files.
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from core.db.storage_common import (
    configured_database_url,
    database_url_or_none,
)
from core.kv_rows import load_rows


load_env_files(__file__)

LOG = logging.getLogger("plot_prices")

DEFAULT_OUTPUT_DIR = "charts"


@dataclass(frozen=True)
class Observation:
    """One row of price observation data."""

    observed_at: datetime
    symbol: str
    asset: str
    binance_price: float
    aave_price: float
    diff_percent: float


def parse_time(value: str) -> datetime:
    """Parse UTC ISO timestamps written by observer.py."""

    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_observations(
    database_url: str | None, symbols: Optional[set[str]]
) -> dict[str, list[Observation]]:
    """Read observations from the local KV store and group them by trading pair."""

    if not database_url:
        return {}
    grouped: dict[str, list[Observation]] = defaultdict(list)
    wanted = {str(symbol).strip().upper() for symbol in symbols or [] if str(symbol).strip()}
    rows = load_rows(database_url, "observations")
    rows.sort(key=lambda row: (str(row.get("symbol") or ""), str(row.get("observed_at") or "")))
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol or (wanted and symbol not in wanted):
            continue
        grouped[symbol].append(
            Observation(
                observed_at=parse_time(str(row.get("observed_at"))),
                symbol=symbol,
                asset=str(row.get("asset") or symbol).strip() or symbol,
                binance_price=float(row.get("binance_price") or 0),
                aave_price=float(row.get("aave_price") or 0),
                diff_percent=float(row.get("diff_percent") or 0),
            )
        )

    return dict(grouped)


def keep_last(observations: list[Observation], limit: int) -> list[Observation]:
    if limit <= 0 or len(observations) <= limit:
        return observations
    return observations[-limit:]


def plot_symbol(observations: list[Observation], output_dir: Path) -> Path:
    """Generate price and diff charts for one trading pair."""

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

    # Keep long-running charts readable by compacting the time axis.
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
    """Generate a short summary for quick price-diff inspection."""

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
        default=database_url_or_none(),
        help="Local KV store path",
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

    database_url = configured_database_url(args.database_url)

    LOG.info("reading database")
    grouped = load_observations(database_url, symbols)

    if not grouped:
        raise RuntimeError(
            "No observations found. Run observer.py first and check FLASHLOAN_KV_PATH."
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
