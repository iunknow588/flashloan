"""Tests for the mainnet V4 index multi-provider log refresh path.

The refresh pipeline must be able to scan the full chain through HyperRPC and
fall back to standard RPCs while shrinking block segments on response limits.
"""

from __future__ import annotations

from web3 import Web3

from dex.intent_trade.chains.robinhood.config import CHAIN_ID as ROBINHOOD_CHAIN_ID
from dex.intent_trade.chains.robinhood.v4_pool_index import _contract_address
from dex.intent_trade.chains.robinhood_common import v4_pool_index_core as core
from dex.intent_trade.chains.robinhood_common.v4_pool_index_core import (
    V4_INITIALIZE_TOPIC,
    _fetch_logs_sharded,
)
from dex.tools.refresh_robinhood_v4_live import _direction_backfill_plan
from dex.tools.refresh_robinhood_mainnet_v4_evidence import _normalize_database_arg


def _fake_w3(chain_id: int = ROBINHOOD_CHAIN_ID, block_number: int = 84_000_000):
    class _FakeEth:
        def __init__(self):
            self.chain_id = chain_id
            self.block_number = block_number

    class _FakeWeb3:
        def __init__(self):
            self.eth = _FakeEth()
            self.codec = Web3().codec

        def is_connected(self):
            return True

    return _FakeWeb3()


def _string_initialize_log(pool_id: str, block_number: int) -> dict:
    w3 = Web3()
    fee, tick_spacing, hooks, sqrt_price_x96, tick = (
        3000,
        60,
        "0x0000000000000000000000000000000000000000",
        2**96,
        10,
    )
    data = w3.codec.encode(
        ["uint24", "int24", "address", "uint160", "int24"],
        [fee, tick_spacing, hooks, sqrt_price_x96, tick],
    )
    zero_topic = "0x" + "00" * 32
    one_topic = "0x" + "00" * 12 + "11" * 20
    return {
        "address": _contract_address("v4_pool_manager"),
        "topics": [
            V4_INITIALIZE_TOPIC,
            pool_id,
            zero_topic,
            one_topic,
        ],
        "data": "0x" + data.hex(),
        "blockNumber": hex(block_number),
        "transactionIndex": "0x0",
        "logIndex": "0x1",
        "transactionHash": "0x" + "ab" * 32,
    }


def test_jsonrpc_logs_normalize_string_block_numbers(monkeypatch):
    log = _string_initialize_log("0x" + "11" * 32, 12_345_678)

    def fake_call(url, method, params, *, timeout_seconds, user_agent):
        return [log]

    monkeypatch.setattr(core, "_jsonrpc_call", fake_call)
    normalized = core._get_logs_jsonrpc(
        "https://hyper.example",
        pool_manager=_contract_address("v4_pool_manager"),
        topic=V4_INITIALIZE_TOPIC,
        from_block=0,
        to_block=12_345_678,
        timeout_seconds=1,
        user_agent="test",
    )
    assert normalized[0]["blockNumber"] == 12_345_678
    assert normalized[0]["transactionIndex"] == 0
    assert normalized[0]["logIndex"] == 1


def test_fetch_logs_sharded_falls_back_and_halves_chunk_on_limit(monkeypatch):
    destinations: dict[str, list[dict]] = {}
    calls: list[tuple[str, int, int]] = []

    def fake_get_logs(
        url,
        *,
        pool_manager,
        topic,
        from_block,
        to_block,
        timeout_seconds,
        user_agent,
    ):
        label = url.split("//", 1)[1].split(".", 1)[0]
        calls.append((label, int(from_block), int(to_block)))
        key = f"{label}:{int(from_block)}-{int(to_block)}"
        if key == "hyper:0-1499999":
            raise RuntimeError("eth_getLogs query exceeds max block range")
        if key == "std:0-1499999":
            raise RuntimeError("eth_getLogs response size limit exceeded")
        return destinations.get(key, [])

    monkeypatch.setattr(core, "_get_logs_jsonrpc", fake_get_logs)
    logs = _fetch_logs_sharded(
        providers=["https://hyper.example", "https://std.example"],
        pool_manager="0x" + "00" * 20,
        topic=V4_INITIALIZE_TOPIC,
        from_block=0,
        to_block=1_499_999,
        initial_chunk_size=5_000_000,
        min_chunk_size=1_000,
        timeout_seconds=1,
        user_agent="test",
        attempts_per_segment=3,
    )

    assert logs == []
    used_providers = {label for label, _from, _to in calls}
    assert used_providers == {"hyper", "std"}
    assert any(0 <= to - frm <= 750_000 for _label, frm, to in calls)


def test_build_index_decodes_string_logs_and_deduplicates_pool_ids(monkeypatch):
    pool_id = "0x" + "22" * 32
    log_one = _string_initialize_log(pool_id, 84_000_000)
    log_two = _string_initialize_log(pool_id, 84_000_100)
    logs_by_segment: dict[str, list[dict]] = {
        "0-9999999": [log_one, log_two],
    }

    def fake_get_logs(
        url,
        *,
        pool_manager,
        topic,
        from_block,
        to_block,
        timeout_seconds,
        user_agent,
    ):
        return logs_by_segment.get(f"{int(from_block)}-{int(to_block)}", [])

    def fake_connect(providers, *, user_agent, timeout_seconds, attempts):
        return _fake_w3()

    monkeypatch.setattr(core, "_get_logs_jsonrpc", fake_get_logs)
    monkeypatch.setattr(core, "_connect_rpc_from_urls", fake_connect)

    payload = core.build_v4_pool_index_from_initialize_logs(
        network="robinhood",
        chain_id=ROBINHOOD_CHAIN_ID,
        pool_manager=_contract_address("v4_pool_manager"),
        rpc_url="https://hyper.example",
        schema_version="robinhood-v4-pool-index-v1",
        include_currency_metadata=False,
        from_block=0,
        to_block=9_999_999,
        chunk_size=10_000_000,
        min_chunk_size=1_000,
        include_metadata=False,
        mark_recent_swaps=False,
    )

    assert payload["initializeLogCount"] == 2
    assert payload["initializedPoolCount"] == 1
    assert payload["selectedPoolCount"] == 1
    assert payload["pools"][0]["poolId"].lower() == pool_id
    assert payload["pools"][0]["initializeBlockNumber"] == 84_000_000


def test_directional_backfill_scans_newest_first_and_stops_at_targets(monkeypatch):
    pool_id = "0x" + "33" * 32
    calls = []

    def fake_get_logs(url, *, pool_manager, topic, from_block, to_block, timeout_seconds, user_agent):
        calls.append((int(from_block), int(to_block)))
        if int(from_block) == 84_000_001 and int(to_block) == 84_900_000:
            return [_string_initialize_log(pool_id, 84_500_000)]
        return []

    def fake_connect(providers, *, user_agent, timeout_seconds, attempts):
        return _fake_w3(block_number=84_900_000)

    monkeypatch.setattr(core, "_get_logs_jsonrpc", fake_get_logs)
    monkeypatch.setattr(core, "_connect_rpc_from_urls", fake_connect)
    payload = core.build_v4_directional_pool_backfill(
        network="robinhood",
        chain_id=ROBINHOOD_CHAIN_ID,
        pool_manager=_contract_address("v4_pool_manager"),
        rpc_url="https://hyper.example",
        schema_version="robinhood-v4-pool-index-v1",
        direction_targets={
            "0x0000000000000000000000000000000000000000:0x1111111111111111111111111111111111111111": 1,
        },
        direction_floor_block=0,
        chunk_size=900_000,
        min_chunk_size=1_000,
    )

    assert payload["scanDirection"] == "newest_to_oldest"
    assert payload["stoppedReason"] == "direction_targets_satisfied"
    assert payload["satisfiedDirectionCount"] == 1
    assert payload["selectedPoolCount"] == 1
    assert calls == [(84_000_001, 84_900_000)]


def test_directional_backfill_stops_at_pool_creation_floor_when_target_not_reached(monkeypatch):
    pool_id = "0x" + "44" * 32
    calls = []

    def fake_get_logs(url, *, pool_manager, topic, from_block, to_block, timeout_seconds, user_agent):
        calls.append((int(from_block), int(to_block)))
        if int(from_block) == 5_000_000 and int(to_block) == 5_999_999:
            return [_string_initialize_log(pool_id, 5_500_000)]
        return []

    def fake_connect(providers, *, user_agent, timeout_seconds, attempts):
        return _fake_w3(block_number=5_999_999)

    monkeypatch.setattr(core, "_get_logs_jsonrpc", fake_get_logs)
    monkeypatch.setattr(core, "_connect_rpc_from_urls", fake_connect)
    payload = core.build_v4_directional_pool_backfill(
        network="robinhood",
        chain_id=ROBINHOOD_CHAIN_ID,
        pool_manager=_contract_address("v4_pool_manager"),
        rpc_url="https://hyper.example",
        schema_version="robinhood-v4-pool-index-v1",
        direction_targets={
            "0x0000000000000000000000000000000000000000:0x1111111111111111111111111111111111111111": 2,
        },
        direction_floor_block=5_000_000,
        chunk_size=1_000_000,
        min_chunk_size=1_000,
    )

    assert payload["stoppedReason"] == "floor_reached_or_range_exhausted"
    assert payload["satisfiedDirectionCount"] == 0
    assert calls == [(5_000_000, 5_999_999)]


def test_directional_backfill_counts_pool_for_both_directions_when_liquid(monkeypatch):
    pool_id = "0x" + "55" * 32
    z = "0x" + "00" * 20
    one = "0x" + "11" * 20
    calls = []

    def fake_get_logs(url, *, pool_manager, topic, from_block, to_block, timeout_seconds, user_agent):
        calls.append((int(from_block), int(to_block)))
        if int(from_block) == 84_000_001 and int(to_block) == 84_900_000:
            return [_string_initialize_log(pool_id, 84_500_000)]
        return []

    def fake_connect(providers, *, user_agent, timeout_seconds, attempts):
        return _fake_w3(block_number=84_900_000)

    def fake_confirm(pools):
        return {str(pool.get("poolId") or "").lower() for pool in pools}

    monkeypatch.setattr(core, "_get_logs_jsonrpc", fake_get_logs)
    monkeypatch.setattr(core, "_connect_rpc_from_urls", fake_connect)
    payload = core.build_v4_directional_pool_backfill(
        network="robinhood",
        chain_id=ROBINHOOD_CHAIN_ID,
        pool_manager=_contract_address("v4_pool_manager"),
        rpc_url="https://hyper.example",
        schema_version="robinhood-v4-pool-index-v1",
        direction_targets={
            f"{z}:{one}": 1,
            f"{one}:{z}": 1,
        },
        direction_floor_block=0,
        chunk_size=900_000,
        min_chunk_size=1_000,
        confirm_pool_liquidity=fake_confirm,
    )

    assert payload["stoppedReason"] == "direction_targets_satisfied"
    assert payload["satisfiedDirectionCount"] == 2
    assert payload["directionCounts"] == {
        f"{z}:{one}": 1,
        f"{one}:{z}": 1,
    }
    assert calls == [(84_000_001, 84_900_000)]


def test_directional_backfill_uses_sorted_address_direction_keys(monkeypatch):
    pool_id = "0x" + "66" * 32
    z = "0x" + "00" * 20
    one = "0x" + "11" * 20
    calls = []

    def fake_get_logs(url, *, pool_manager, topic, from_block, to_block, timeout_seconds, user_agent):
        calls.append((int(from_block), int(to_block)))
        if int(from_block) == 84_000_001 and int(to_block) == 84_900_000:
            log = _string_initialize_log(pool_id, 84_500_000)
            log["topics"][2], log["topics"][3] = log["topics"][3], log["topics"][2]
            return [log]
        return []

    def fake_connect(providers, *, user_agent, timeout_seconds, attempts):
        return _fake_w3(block_number=84_900_000)

    monkeypatch.setattr(core, "_get_logs_jsonrpc", fake_get_logs)
    monkeypatch.setattr(core, "_connect_rpc_from_urls", fake_connect)
    payload = core.build_v4_directional_pool_backfill(
        network="robinhood",
        chain_id=ROBINHOOD_CHAIN_ID,
        pool_manager=_contract_address("v4_pool_manager"),
        rpc_url="https://hyper.example",
        schema_version="robinhood-v4-pool-index-v1",
        direction_targets={
            f"{z}:{one}": 1,
            f"{one}:{z}": 1,
        },
        direction_floor_block=0,
        chunk_size=900_000,
        min_chunk_size=1_000,
    )

    assert payload["stoppedReason"] == "direction_targets_satisfied"
    assert payload["satisfiedDirectionCount"] == 2
    assert payload["directionCounts"] == {
        f"{z}:{one}": 1,
        f"{one}:{z}": 1,
    }
    assert payload["selectedPoolCount"] == 1
    assert calls == [(84_000_001, 84_900_000)]


def test_directional_backfill_caps_count_within_one_segment(monkeypatch):
    z = "0x" + "00" * 20
    one = "0x" + "11" * 20
    pool_ids = [f"0x{token_byte:02x}" * 32 for token_byte in (0xaa, 0xbb, 0xcc)]
    calls = []

    def fake_get_logs(url, *, pool_manager, topic, from_block, to_block, timeout_seconds, user_agent):
        calls.append((int(from_block), int(to_block)))
        if int(from_block) == 84_000_001 and int(to_block) == 84_900_000:
            return [
                _string_initialize_log(pool_id, 84_500_000 + index)
                for index, pool_id in enumerate(pool_ids)
            ]
        return []

    def fake_connect(providers, *, user_agent, timeout_seconds, attempts):
        return _fake_w3(block_number=84_900_000)

    monkeypatch.setattr(core, "_get_logs_jsonrpc", fake_get_logs)
    monkeypatch.setattr(core, "_connect_rpc_from_urls", fake_connect)
    payload = core.build_v4_directional_pool_backfill(
        network="robinhood",
        chain_id=ROBINHOOD_CHAIN_ID,
        pool_manager=_contract_address("v4_pool_manager"),
        rpc_url="https://hyper.example",
        schema_version="robinhood-v4-pool-index-v1",
        direction_targets={
            f"{z}:{one}": 2,
        },
        direction_floor_block=0,
        chunk_size=900_000,
        min_chunk_size=1_000,
    )

    assert payload["stoppedReason"] == "direction_targets_satisfied"
    assert payload["directionCounts"] == {f"{z}:{one}": 2}
    assert payload["selectedPoolCount"] == 2
    assert calls == [(84_000_001, 84_900_000)]


def test_direction_backfill_plan_counts_each_pool_direction_separately(tmp_path):
    from core.db.storage_business_schema import ensure_business_schema
    from core.db.storage_business_io import executemany

    db_path = str((tmp_path / "plan.sqlite3").resolve())
    ensure_business_schema(db_path)
    now = "2026-10-17T00:00:00Z"
    anchor = "0x" + "cc" * 20
    party = "0x" + "aa" * 20
    second = "0x" + "bb" * 20
    executemany(
        db_path,
        """
        INSERT INTO v4_exchange_pools (
            network, chain_id, protocol, pool_id, pool_identity_mode,
            pool_manager, pool_key_json, token0, token1, fee, fee_label,
            dynamic_fee, special_fee, tick_spacing, hooks, address_pair,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "robinhood", ROBINHOOD_CHAIN_ID, "uniswap_v4", "0x" + "11" * 32,
                "v4_pool_id", _contract_address("v4_pool_manager"),
                '{"initializeBlockNumber": 50000000}', anchor, party, 3000, 3000,
                0, 0, 60, "0x0000000000000000000000000000000000000000",
                f"{anchor}:{party}", now, now,
            ),
        ],
    )
    executemany(
        db_path,
        """
        INSERT INTO dex_candidate_pool_pairs (
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            anchor_address, counter_address, address_pair, direction_key,
            pool_count, fixed_fee_liquid_pool_count, priced_pool_count,
            yc_optimal_pool_id, zc_optimal_pool_id, yc_backup_pool_ids_json,
            zc_backup_pool_ids_json, price_reference_json, price_outlier_filter,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "robinhood", ROBINHOOD_CHAIN_ID, "uniswap_v4", "stock", "TEST",
                anchor, party, f"{anchor}:{party}", f"{anchor}:{party}",
                1, 1, 1, "", "", "[]", "[]", "{}", "fixed_fee",
                now, now,
            ),
            (
                "robinhood", ROBINHOOD_CHAIN_ID, "uniswap_v4", "stock", "TEST",
                anchor, second, f"{anchor}:{second}", f"{anchor}:{second}",
                1, 1, 1, "0x" + "22" * 32, "0x" + "33" * 32,
                '["0x' + "44" * 32 + '","0x' + "55" * 32 + '"]',
                '["0x' + "66" * 32 + '"]', "{}", "fixed_fee",
                now, now,
            ),
        ],
    )
    plan = _direction_backfill_plan(
        database=db_path,
        network="robinhood",
        chain_id=ROBINHOOD_CHAIN_ID,
        direction_limit=5,
    )
    assert plan["neededDirectionCount"] == 4
    assert plan["targets"] == {
        f"{party}:{anchor}": 5,
        f"{anchor}:{party}": 5,
        f"{second}:{anchor}": 5,
        f"{anchor}:{second}": 5,
    }
    assert plan["floor_block"] == 50_000_000


def test_refresh_database_arg_resolves_relative_paths_from_repo_root(tmp_path):
    relative = "src/runtime/local_store.sqlite3"
    assert _normalize_database_arg(relative, repo_root=tmp_path) == str(
        (tmp_path / relative).resolve()
    )
    absolute = (tmp_path / "another.sqlite3").resolve()
    assert _normalize_database_arg(str(absolute), repo_root=tmp_path) == str(absolute)
    assert _normalize_database_arg("", repo_root=tmp_path) == ""
