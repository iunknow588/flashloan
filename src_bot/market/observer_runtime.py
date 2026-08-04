import asyncio
import json
import logging
import signal
import time
from contextlib import suppress
from pathlib import Path
from typing import Iterable

import websockets
from web3 import Web3

from db.storage_liquidation import try_acquire_observer_lock
from db.storage_observer import (
    append_arbitrage_simulation,
    append_binance_candidate_price_history,
    append_binance_extremes,
    append_binance_pair_price_history,
    append_observations,
)
from db.storage_schema import ensure_database_schema
from market.observer_common import (
    AAVE_ORACLE,
    DEFAULT_BINANCE_WS_CHUNK_SIZE,
    LATEST_ARBITRAGE_PATH,
    LATEST_EXTREMES_PATH,
    LEGACY_ASSETS,
    LOG,
    ORACLE_ABI,
    AssetConfig,
    ObserverConfig,
    age_seconds,
    auto_stop_after,
    binance_stream_url,
    env_bool,
    env_float,
    fetch_binance_rest_price,
    load_config,
    mask_url,
    now_iso,
    pct_diff,
    setup_logging,
    should_compute_conversion_profits,
    sleep_until_next,
    utc_from_ms,
    web3_for_rpc_url,
    write_json_atomic,
)
from market.observer_state import PriceState
from strategy.arbitrage import simulate_basket
async def binance_listener(symbols: Iterable[str], ws_bases: Iterable[str], state: PriceState, stop: asyncio.Event) -> None:
    symbol_list, base_list, base_index, delay = list(symbols), list(ws_bases), 0, 1.0
    while not stop.is_set():
        base = base_list[base_index % len(base_list)]
        try:
            LOG.info("binance connecting base=%s symbols=%s", mask_url(base), len(symbol_list))
            async with websockets.connect(binance_stream_url(base, symbol_list), ping_interval=20, ping_timeout=20, open_timeout=15, max_queue=2048) as ws:
                LOG.info("binance connected base=%s", mask_url(base))
                delay = 1.0
                async for raw in ws:
                    if stop.is_set():
                        break
                    data = json.loads(raw).get("data", {})
                    if data.get("s") and data.get("p"):
                        await state.update_binance(
                            data["s"],
                            float(data["p"]),
                            int(data.get("E", int(time.time() * 1000))),
                            "ws",
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.warning("binance error=%r base=%s reconnect_in=%.1fs", exc, mask_url(base), delay)
            base_index += 1
            await sleep_until_next(stop, delay)
            delay = min(delay * 2, 60)


def chunked_symbols(symbols: Iterable[str], chunk_size: int) -> list[list[str]]:
    unique_symbols = list(dict.fromkeys(symbols))
    size = max(1, chunk_size)
    return [unique_symbols[index : index + size] for index in range(0, len(unique_symbols), size)]


async def binance_rest_poller(config: ObserverConfig, state: PriceState, stop: asyncio.Event) -> None:
    base_index = 0
    while not stop.is_set():
        base = config.binance_rest_bases[base_index % len(config.binance_rest_bases)]
        try:
            for symbol in config.symbols:
                price = await asyncio.to_thread(fetch_binance_rest_price, base, symbol)
                await state.update_binance(symbol, price, int(time.time() * 1000), "rest")
        except Exception as exc:
            LOG.warning("binance_rest error=%r base=%s", exc, mask_url(base))
            base_index += 1
        await sleep_until_next(stop, config.binance_rest_poll_seconds)


async def aave_poller(config: ObserverConfig, state: PriceState, stop: asyncio.Event) -> None:
    tracked = [
        (symbol, config.asset_lookup[symbol])
        for symbol in config.symbols
        if symbol in config.asset_lookup
    ]
    asset_symbols = [symbol for symbol, _ in tracked]
    asset_addresses = [Web3.to_checksum_address(asset.asset_address) for _, asset in tracked]
    rpc_urls = config.rpc_urls or [config.rpc_url]
    rpc_index = 0
    delay = 1.0
    while not stop.is_set():
        started = time.monotonic()
        try:
            base_unit = None
            block = None
            raw_prices = None
            selected_rpc = None
            for offset in range(len(rpc_urls)):
                rpc_url = rpc_urls[(rpc_index + offset) % len(rpc_urls)]
                try:
                    w3 = web3_for_rpc_url(rpc_url, timeout=10)
                    oracle = w3.eth.contract(address=Web3.to_checksum_address(AAVE_ORACLE), abi=ORACLE_ABI)
                    base_unit = await asyncio.to_thread(oracle.functions.BASE_CURRENCY_UNIT().call)
                    block = await asyncio.to_thread(lambda: w3.eth.block_number)
                    try:
                        raw_prices = await asyncio.to_thread(oracle.functions.getAssetsPrices(asset_addresses).call)
                    except Exception:
                        raw_prices = [
                            await asyncio.to_thread(oracle.functions.getAssetPrice(address).call)
                            for address in asset_addresses
                        ]
                    selected_rpc = rpc_url
                    rpc_index = (rpc_index + offset) % len(rpc_urls)
                    break
                except Exception as exc:
                    LOG.warning("aave rpc failed=%r rpc=%s", exc, mask_url(rpc_url))
            if base_unit is None or block is None or raw_prices is None:
                raise RuntimeError(f"all AAVE RPC candidates failed ({len(rpc_urls)})")
            if selected_rpc:
                LOG.info("aave rpc selected=%s", mask_url(selected_rpc))
            for symbol, raw in zip(asset_symbols, raw_prices):
                await state.update_aave(symbol, float(raw) / base_unit, block)
        except Exception as exc:
            LOG.warning("aave_poll error=%r", exc)
            await sleep_until_next(stop, delay)
            delay = min(delay * 2, 60)
        else:
            delay = 1.0
            await sleep_until_next(stop, max(0.1, config.poll_seconds - (time.monotonic() - started)))


async def reporter(config: ObserverConfig, state: PriceState, stop: asyncio.Event) -> None:
    last_report_at = 0.0
    last_observation_write_at = 0.0
    last_db_error_at = 0.0
    while not stop.is_set():
        rows, snapshot = [], await state.snapshot()
        now = time.monotonic()
        report_due = time.monotonic() - last_report_at >= config.report_seconds
        observation_write_due = now - last_observation_write_at >= config.observation_write_seconds
        for symbol in config.symbols:
            b, a = snapshot["binance"].get(symbol), snapshot["aave"].get(symbol)
            if not b or not a:
                continue
            diff = pct_diff(b["price"], a["price"])
            if diff is None:
                continue
            row = {
                "observed_at": now_iso(),
                "symbol": symbol,
                "asset": config.asset_lookup.get(symbol, LEGACY_ASSETS.get(symbol, AssetConfig(symbol, "", symbol))).symbol,
                "binance_price": f"{b['price']:.10f}",
                "binance_event_time": utc_from_ms(b["event_ms"]),
                "aave_price": f"{a['price']:.10f}",
                "aave_block": a["block"],
                "diff_percent": f"{diff:.6f}",
                "binance_age_seconds": f"{age_seconds(b['seen_at']):.3f}",
                "aave_age_seconds": f"{age_seconds(a['seen_at']):.3f}",
            }
            rows.append(row)
            if report_due and not config.report_only_alerts:
                LOG.info("OK %s binance=%.6f aave=%.6f diff=%+.4f%%", symbol, b["price"], a["price"], diff)
        if rows and observation_write_due and config.observation_db_writes:
            try:
                await asyncio.to_thread(append_observations, config.database_url, rows)
                last_observation_write_at = now
            except Exception as exc:
                if now - last_db_error_at >= config.report_seconds:
                    LOG.warning("observation database write failed error=%r", exc)
                    last_db_error_at = now
        if report_due:
            last_report_at = time.monotonic()
        await sleep_until_next(stop, config.sample_seconds)


async def extreme_and_arbitrage_reporter(config: ObserverConfig, state: PriceState, stop: asyncio.Event) -> None:
    last_write_at, last_pair_sample_at, last_pair_flush_at, last_log_at, last_db_error_at = 0.0, 0.0, 0.0, 0.0, 0.0
    candidate_price_buffer: list[dict] = []
    pair_price_buffer: list[dict] = []
    while not stop.is_set():
        extremes = await state.window_extremes(
            config.binance_top_symbols,
            config.binance_change_window_seconds,
            config.binance_velocity_side_limit,
            min_change_percent=config.binance_velocity_min_change_percent,
        )
        simulation_extremes = await state.window_extremes(
            config.binance_top_symbols,
            config.binance_change_window_seconds,
            config.binance_velocity_side_limit,
            source="ws" if config.require_binance_ws_for_arbitrage else None,
            min_change_percent=config.binance_velocity_min_change_percent,
        )
        simulation = (
            simulate_basket(simulation_extremes, config.arbitrage)
            if should_compute_conversion_profits(simulation_extremes, config.market_divergence_trigger_min)
            else None
        )
        if extremes["top"] or extremes["bottom"] or extremes.get("basket"):
            write_json_atomic(LATEST_EXTREMES_PATH, extremes)
        if simulation:
            write_json_atomic(LATEST_ARBITRAGE_PATH, simulation)
        elif Path(LATEST_ARBITRAGE_PATH).exists():
            with suppress(OSError):
                Path(LATEST_ARBITRAGE_PATH).unlink()
        now = time.monotonic()
        if simulation and now - last_log_at >= config.report_seconds:
            LOG.info(
                "trigger signal=%s x=%s %+.4f%% y=%s %+.4f%% window=%.3fs",
                simulation["signal"],
                simulation["a_symbol"],
                simulation["a_change_percent"],
                simulation["b_symbol"],
                simulation["b_change_percent"],
                simulation["window_seconds"],
            )
            last_log_at = now
        if simulation and now - last_write_at >= config.binance_extreme_write_seconds:
            try:
                await asyncio.to_thread(append_binance_extremes, config.database_url, extremes)
                await asyncio.to_thread(append_arbitrage_simulation, config.database_url, simulation)
            except Exception as exc:
                if now - last_db_error_at >= config.report_seconds:
                    LOG.warning("arbitrage database write failed error=%r", exc)
                    last_db_error_at = now
            last_write_at = now
        if (
            config.binance_pair_history_writes
            and now - last_pair_sample_at >= config.binance_pair_price_write_seconds
            and extremes["top"]
            and extremes["bottom"]
        ):
            candidate_rows, pair_rows = await state.candidate_and_pair_price_rows(
                extremes,
                config.binance_candidate_db_side_limit,
            )
            candidate_price_buffer.extend(candidate_rows)
            pair_price_buffer.extend(pair_rows)
            last_pair_sample_at = now
        if (candidate_price_buffer or pair_price_buffer) and now - last_pair_flush_at >= config.binance_pair_price_flush_seconds:
            candidate_rows_to_write = candidate_price_buffer
            pair_rows_to_write = pair_price_buffer
            candidate_price_buffer, pair_price_buffer = [], []
            try:
                await asyncio.to_thread(append_binance_candidate_price_history, config.database_url, candidate_rows_to_write)
                await asyncio.to_thread(append_binance_pair_price_history, config.database_url, pair_rows_to_write)
            except Exception as exc:
                candidate_price_buffer = candidate_rows_to_write + candidate_price_buffer
                pair_price_buffer = pair_rows_to_write + pair_price_buffer
                if now - last_db_error_at >= config.report_seconds:
                    LOG.warning("binance candidate pair price write failed error=%r", exc)
                    last_db_error_at = now
            last_pair_flush_at = now
        await sleep_until_next(stop, config.sample_seconds)



async def main() -> None:
    setup_logging()
    config, state, stop = load_config(), PriceState(), asyncio.Event()
    observer_lock_connection = None
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    if env_bool("SKIP_DATABASE_SCHEMA", False):
        LOG.info("database schema initialization skipped")
    else:
        await asyncio.to_thread(ensure_database_schema, config.database_url)
    if env_bool("OBSERVER_REQUIRE_DB_LOCK", True):
        observer_lock_connection = await asyncio.to_thread(try_acquire_observer_lock, config.database_url)
        if observer_lock_connection is None:
            LOG.error("another observer already holds the database writer lock; exiting")
            return
        LOG.info("database writer lock acquired")
    LOG.info(
        "observer started top_symbols=%s velocity_side_limit=%s sample=%.3fs trigger_window=%.3fs trigger_up=%.2f%% trigger_down=%.2f%%",
        len(config.binance_top_symbols),
        config.binance_velocity_side_limit,
        config.sample_seconds,
        config.binance_change_window_seconds,
        config.trigger.min_up_change_percent,
        config.trigger.min_down_change_percent,
    )
    binance_symbols = list(dict.fromkeys([*config.symbols, *config.binance_top_symbols]))
    ws_chunk_size = max(1, int(env_float("BINANCE_WS_CHUNK_SIZE", DEFAULT_BINANCE_WS_CHUNK_SIZE)))
    binance_chunks = chunked_symbols(binance_symbols, ws_chunk_size)
    LOG.info("binance websocket chunks=%s chunk_size=%s total_symbols=%s symbols=%s", len(binance_chunks), ws_chunk_size, len(binance_symbols), ",".join(binance_symbols))
    tasks = [
        *[
            asyncio.create_task(binance_listener(chunk, config.binance_ws_bases, state, stop))
            for chunk in binance_chunks
        ],
        asyncio.create_task(binance_rest_poller(config, state, stop)),
        asyncio.create_task(extreme_and_arbitrage_reporter(config, state, stop)),
        asyncio.create_task(auto_stop_after(config.run_seconds, stop)),
    ]
    if config.aave_verification_enabled:
        tasks.extend(
            [
                asyncio.create_task(aave_poller(config, state, stop)),
                asyncio.create_task(reporter(config, state, stop)),
            ]
        )
    else:
        LOG.info("aave verification disabled")
    if not config.binance_pair_history_writes:
        LOG.info("candidate and pair history writes disabled")
    try:
        await stop.wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if observer_lock_connection is not None:
            observer_lock_connection.close()


if __name__ == "__main__":
    asyncio.run(main())
