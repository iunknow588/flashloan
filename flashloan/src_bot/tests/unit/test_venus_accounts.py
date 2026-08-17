from web3 import Web3

from execution.venus_accounts import (
    build_venus_borrower_registry_from_events,
    dedupe_venus_events,
    discover_venus_borrower_accounts,
    estimate_start_block_by_lookback_days,
    extract_venus_event_account,
    find_block_by_timestamp,
    load_venus_event_journal,
    scan_venus_account_events,
    validate_venus_borrower_registry,
    write_venus_event_journal,
)
from execution.venus_abis import VENUS_EVENT_SPECS


def test_extract_venus_event_account_decodes_borrow_data():
    w3 = Web3()
    account = "0x00000000000000000000000000000000000000AA"
    data = w3.codec.encode(VENUS_EVENT_SPECS["Borrow"]["types"], [account, 1, 2, 3])
    log = {"topics": [VENUS_EVENT_SPECS["Borrow"]["topic"]], "data": data}

    assert extract_venus_event_account(w3, log, "Borrow") == Web3.to_checksum_address(account)


def test_build_venus_borrower_registry_deduplicates_and_tiers_accounts():
    account = "0x00000000000000000000000000000000000000AA"
    events = [
        {"account": account, "event": "Borrow", "vtoken": "vUSDT", "block_number": 10},
        {"account": account, "event": "RepayBorrow", "vtoken": "vUSDT", "block_number": 20},
    ]

    registry = build_venus_borrower_registry_from_events(events, latest_block=21)

    assert registry["account_count"] == 1
    assert registry["storage_mode"] == "file_only"
    assert registry["accounts"][0]["event_count"] == 2
    assert registry["accounts"][0]["borrow_markets"] == ["vUSDT"]
    assert registry["accounts"][0]["tier"] == "hot"


def test_dedupe_venus_events_prefers_last_copy_and_sorts():
    events = [
        {"account": "0x1", "event": "Borrow", "vtoken": "vUSDT", "block_number": 2, "transaction_hash": "0x02", "log_index": 1},
        {"account": "0x1", "event": "Borrow", "vtoken": "vUSDT", "block_number": 1, "transaction_hash": "0x01", "log_index": 0},
        {"account": "0x1", "event": "Borrow", "vtoken": "vUSDT", "block_number": 1, "transaction_hash": "0x01", "log_index": 0, "extra": True},
    ]

    deduped = dedupe_venus_events(events)

    assert len(deduped) == 2
    assert deduped[0]["block_number"] == 1
    assert deduped[0]["extra"] is True


def test_write_and_load_venus_event_journal(tmp_path):
    path = tmp_path / "events.jsonl"
    rows = [{"account": "0x1", "event": "Borrow", "vtoken": "vUSDT", "block_number": 1, "transaction_hash": "0x01", "log_index": 0}]

    write_venus_event_journal(path, rows)

    loaded = load_venus_event_journal(path)

    assert loaded == rows


class TimestampEth:
    block_number = 10

    def get_block(self, block_number):
        return {"timestamp": int(block_number) * 100}


class TimestampW3:
    eth = TimestampEth()


def test_find_block_by_timestamp_returns_floor_block():
    block = find_block_by_timestamp(TimestampW3(), 350, low_block=0, high_block=10)

    assert block == 3


def test_estimate_start_block_by_lookback_days_uses_block_timestamp():
    class LookbackEth:
        block_number = 20

        def get_block(self, block_number):
            return {"timestamp": 1_728_000_000 + int(block_number) * 3_600}

    class LookbackW3:
        eth = LookbackEth()

    assert estimate_start_block_by_lookback_days(LookbackW3(), 2) == 0


class FakeEth:
    block_number = 10

    def get_logs(self, _params):
        raise RuntimeError("rpc 403")


class FakeW3:
    eth = FakeEth()


def test_scan_venus_account_events_can_continue_on_rpc_errors():
    progress = []

    rows = scan_venus_account_events(
        FakeW3(),
        ["0x0000000000000000000000000000000000000001"],
        from_block=1,
        to_block=1,
        events=["Borrow"],
        continue_on_error=True,
        progress_callback=progress.append,
    )

    assert rows == []
    assert "rpc 403" in progress[0]["error"]


class AdaptiveEth:
    block_number = 10

    def get_logs(self, params):
        if int(params["toBlock"]) - int(params["fromBlock"]) + 1 > 1:
            raise RuntimeError("limit exceeded")
        return [
            {
                "topics": [VENUS_EVENT_SPECS["Borrow"]["topic"]],
                "data": Web3().codec.encode(VENUS_EVENT_SPECS["Borrow"]["types"], ["0x00000000000000000000000000000000000000AA", 1, 2, 3]),
                "blockNumber": int(params["fromBlock"]),
                "transactionHash": "0x01",
                "logIndex": 0,
            }
        ]


class AdaptiveW3:
    eth = AdaptiveEth()
    codec = Web3().codec


def test_scan_venus_account_events_adaptive_chunk_splits_large_ranges():
    rows = scan_venus_account_events(
        AdaptiveW3(),
        ["0x0000000000000000000000000000000000000001"],
        from_block=1,
        to_block=2,
        chunk_size=2,
        events=["Borrow"],
        adaptive_chunk=True,
        min_chunk_size=1,
    )

    assert len(rows) == 2
    assert rows[0]["account"] == Web3.to_checksum_address("0x00000000000000000000000000000000000000AA")


def test_scan_venus_account_events_batches_vtoken_emitters():
    first_vtoken = "0x0000000000000000000000000000000000000001"
    second_vtoken = "0x0000000000000000000000000000000000000002"

    class BatchEth:
        block_number = 10

        def __init__(self):
            self.params = []

        def get_logs(self, params):
            self.params.append(params)
            return [
                {
                    "address": second_vtoken,
                    "topics": [VENUS_EVENT_SPECS["Borrow"]["topic"]],
                    "data": Web3().codec.encode(VENUS_EVENT_SPECS["Borrow"]["types"], ["0x00000000000000000000000000000000000000AA", 1, 2, 3]),
                    "blockNumber": int(params["fromBlock"]),
                    "transactionHash": "0x01",
                    "logIndex": 0,
                }
            ]

    class BatchW3:
        codec = Web3().codec

        def __init__(self):
            self.eth = BatchEth()

    w3 = BatchW3()
    rows = scan_venus_account_events(w3, [first_vtoken, second_vtoken], from_block=1, to_block=1, events=["Borrow"])

    assert w3.eth.params[0]["address"] == [Web3.to_checksum_address(first_vtoken), Web3.to_checksum_address(second_vtoken)]
    assert rows[0]["vtoken"] == Web3.to_checksum_address(second_vtoken)


def test_scan_venus_account_events_adaptive_chunk_splits_address_batches_after_min_range():
    first_vtoken = "0x0000000000000000000000000000000000000001"
    second_vtoken = "0x0000000000000000000000000000000000000002"

    class AddressLimitedEth:
        block_number = 10

        def __init__(self):
            self.address_batches = []

        def get_logs(self, params):
            addresses = list(params["address"])
            self.address_batches.append(addresses)
            if len(addresses) > 1:
                raise RuntimeError("413 request entity too large")
            return []

    class AddressLimitedW3:
        codec = Web3().codec

        def __init__(self):
            self.eth = AddressLimitedEth()

    w3 = AddressLimitedW3()
    rows = scan_venus_account_events(
        w3,
        [first_vtoken, second_vtoken],
        from_block=1,
        to_block=1,
        events=["Borrow"],
        adaptive_chunk=True,
        min_chunk_size=1,
        address_batch_size=2,
    )

    assert rows == []
    assert [len(batch) for batch in w3.eth.address_batches] == [2, 1, 1]


class FailingEth:
    block_number = 10

    def get_logs(self, _params):
        raise RuntimeError("rpc 403")


class WorkingEth:
    block_number = 10

    def get_logs(self, params):
        return [
            {
                "topics": [VENUS_EVENT_SPECS["Borrow"]["topic"]],
                "data": Web3().codec.encode(VENUS_EVENT_SPECS["Borrow"]["types"], ["0x00000000000000000000000000000000000000AA", 1, 2, 3]),
                "blockNumber": int(params["fromBlock"]),
                "transactionHash": "0x01",
                "logIndex": 0,
            }
        ]


class FailingW3:
    eth = FailingEth()
    codec = Web3().codec


class WorkingW3:
    eth = WorkingEth()
    codec = Web3().codec


def test_scan_venus_account_events_rotates_rpc_pool_after_failure():
    rows = scan_venus_account_events(
        FailingW3(),
        ["0x0000000000000000000000000000000000000001"],
        from_block=1,
        to_block=1,
        events=["Borrow"],
        rpc_pool=[FailingW3(), WorkingW3()],
    )

    assert len(rows) == 1
    assert rows[0]["account"] == Web3.to_checksum_address("0x00000000000000000000000000000000000000AA")


def test_scan_venus_account_events_retries_transient_rpc_failures():
    class FlakyEth:
        block_number = 10

        def __init__(self):
            self.calls = 0

        def get_logs(self, params):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("403 rate limited")
            return [
                {
                    "topics": [VENUS_EVENT_SPECS["Borrow"]["topic"]],
                    "data": Web3().codec.encode(VENUS_EVENT_SPECS["Borrow"]["types"], ["0x00000000000000000000000000000000000000AA", 1, 2, 3]),
                    "blockNumber": int(params["fromBlock"]),
                    "transactionHash": "0x01",
                    "logIndex": 0,
                }
            ]

    class FlakyW3:
        codec = Web3().codec

        def __init__(self):
            self.eth = FlakyEth()

    progress = []
    w3 = FlakyW3()

    rows = scan_venus_account_events(
        w3,
        ["0x0000000000000000000000000000000000000001"],
        from_block=1,
        to_block=1,
        events=["Borrow"],
        error_retry_attempts=1,
        error_retry_sleep_seconds=0,
        progress_callback=progress.append,
    )

    assert len(rows) == 1
    assert w3.eth.calls == 2
    assert progress[0]["retry_attempt"] == 1


class RangeCaptureEth:
    block_number = 10

    def __init__(self):
        self.ranges = []

    def get_logs(self, params):
        self.ranges.append((params["fromBlock"], params["toBlock"]))
        return []


def test_scan_venus_account_events_can_scan_newest_first():
    eth = RangeCaptureEth()

    class RangeW3:
        pass

    w3 = RangeW3()
    w3.eth = eth
    w3.codec = Web3().codec

    scan_venus_account_events(
        w3,
        ["0x0000000000000000000000000000000000000001"],
        from_block=1,
        to_block=5,
        chunk_size=2,
        events=["Borrow"],
        newest_first=True,
    )

    assert eth.ranges[:3] == [(4, 5), (2, 3), (1, 1)]


class DiscoveryEth:
    block_number = 10

    def __init__(self):
        self.topics = []

    def get_logs(self, params):
        self.topics.append(params["topics"][0])
        return [
            {
                "topics": [VENUS_EVENT_SPECS["Borrow"]["topic"]],
                "data": Web3().codec.encode(
                    VENUS_EVENT_SPECS["Borrow"]["types"],
                    ["0x00000000000000000000000000000000000000AA", 1, 2, 3],
                ),
                "blockNumber": int(params["fromBlock"]),
                "transactionHash": "0x01",
                "logIndex": 0,
            }
        ]


def test_discover_venus_borrower_accounts_uses_borrow_events_only():
    eth = DiscoveryEth()

    class DiscoveryW3:
        pass

    w3 = DiscoveryW3()
    w3.eth = eth
    w3.codec = Web3().codec

    discovery = discover_venus_borrower_accounts(
        w3,
        ["0x0000000000000000000000000000000000000001"],
        from_block=1,
        to_block=1,
    )

    assert discovery["discovery_mode"] == "borrow_events_only"
    assert discovery["accounts"] == [Web3.to_checksum_address("0x00000000000000000000000000000000000000AA")]
    assert eth.topics == [VENUS_EVENT_SPECS["Borrow"]["topic"]]


def test_validate_venus_borrower_registry_reports_partial_file_state():
    result = validate_venus_borrower_registry(
        {
            "storage_mode": "file_only",
            "discovery_mode": "borrow_events_only",
            "events_jsonl": "events.jsonl",
            "scan_complete": False,
            "account_count": 1,
            "accounts": [{"account": "0x0000000000000000000000000000000000000001"}],
        }
    )

    assert result["ok"] is True
    assert result["scan_complete"] is False
    assert "registry_scan_incomplete" in result["warnings"]
