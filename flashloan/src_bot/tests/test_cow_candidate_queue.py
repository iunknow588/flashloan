from execution.cow_routes import CowToken
from runtime.cow_arbitrage_daemon import CowQuoteDaemon, default_quote_candidate
from runtime.cow_candidate_queue import CowCandidateQueue


def _candidate(pair: str = "APEUSDT / PYRUSDT", route=None, rank: int = 1) -> dict:
    return {
        "observed_at": "2026-08-05T10:00:00+00:00",
        "network": "bnb",
        "chain_id": 56,
        "pair": pair,
        "pair_rank": rank,
        "priority_reason": "buy_loser_then_gainer",
        "route_path": route or ["USDC", "APE", "PYR", "USDC"],
        "state": "quote_required",
        "execution_phase": "market_candidate",
        "quote": {
            "input_amount": "1000",
            "input_symbol": "USDC",
            "final_symbol": "USDC",
            "edge_hint_percent": "1.2",
            "x_base_symbol": "APE",
            "y_base_symbol": "PYR",
            "x_start_price": "2.0",
            "x_current_price": "2.2",
            "y_start_price": "1.0",
            "y_current_price": "0.9",
        },
        "precheck": {"status": "quote_required", "reasons": ["requires_cow_or_dex_quote"]},
        "market_state": {"observed_at": "2026-08-05T10:00:00+00:00", "cow_filter": {"network": "bnb"}},
    }


def _spread_candidate(pair: str, spread: str, rank: int) -> dict:
    item = _candidate(pair=pair, rank=rank)
    item["quote"].pop("edge_hint_percent", None)
    item["quote"]["window_spread_percent"] = spread
    item["precheck"]["window_spread_percent"] = spread
    return item


def test_cow_candidate_queue_dedupes_and_claims_fifo():
    queue = CowCandidateQueue(max_size=10)

    first = queue.enqueue_many([_candidate()], source="test")
    second = queue.enqueue_many([_candidate()], source="test")
    third = queue.enqueue_many([_candidate("BNBUSDT / CAKEUSDT", rank=2)], source="test")

    assert first["added"] == 1
    assert second["updated"] == 1
    assert third["added"] == 1
    assert queue.stats()["pending"] == 2

    claimed = queue.claim_next()

    assert claimed["pair"] == "APEUSDT / PYRUSDT"
    assert queue.stats()["processing"] == 1


def test_cow_candidate_queue_cools_down_completed_candidates():
    queue = CowCandidateQueue(max_size=10, requote_cooldown_seconds=60)
    queue.enqueue_many([_candidate()], source="test")
    claimed = queue.claim_next()
    queue.complete(claimed["signature"], status="blocked", result={"reason": "profit_below_threshold"})

    result = queue.enqueue_many([_candidate()], source="test")

    assert result["cooldown"] == 1
    assert queue.stats()["blocked"] == 1
    assert queue.stats()["pending"] == 0


def test_cow_candidate_queue_can_prioritize_window_spread():
    queue = CowCandidateQueue(max_size=10)
    queue.enqueue_many(
        [
            _spread_candidate("LOWUSDT / FLATUSDT", "0.5", 1),
            _spread_candidate("HIGHUSDT / DEEPUSDT", "3.1", 2),
        ],
        source="test",
    )

    claimed = queue.claim_next(sort_key="profit")

    assert claimed["pair"] == "HIGHUSDT / DEEPUSDT"
    assert claimed["priority_score"] == "3.1"


def test_cow_quote_daemon_process_once_records_and_marks_blocked():
    queue = CowCandidateQueue(max_size=10)
    queue.enqueue_many([_candidate()], source="test")
    recorded = {}

    def fake_quote(candidate, database_url):
        assert database_url == "postgres://example"
        return {
            "payload": {"ranking": [{"execution_precheck": {"checks_passed": False}}]},
            "attempts": [{"state": "blocked"}],
        }

    def fake_record(attempts, database_url):
        recorded["attempts"] = attempts
        recorded["database_url"] = database_url
        return {"recorded": len(attempts), "source": "test", "error": None}

    daemon = CowQuoteDaemon(
        queue,
        database_url_provider=lambda: "postgres://example",
        quote_candidate=fake_quote,
        record_attempts=fake_record,
        poll_interval_seconds=0.2,
    )

    assert daemon.process_once() is True
    assert recorded["attempts"] == [{"state": "blocked"}]
    assert queue.stats()["blocked"] == 1
    assert daemon.status()["processed"] == 1


def test_cow_quote_daemon_marks_passed_checks_without_submit_ready_not_submitted():
    queue = CowCandidateQueue(max_size=10)
    queue.enqueue_many([_candidate()], source="test")

    def fake_quote(candidate, database_url):
        return {
            "payload": {
                "ranking": [
                    {
                        "execution_precheck": {
                            "checks_passed": True,
                            "can_submit_order": False,
                        }
                    }
                ]
            },
            "attempts": [{"state": "checks_passed_order_disabled"}],
        }

    daemon = CowQuoteDaemon(queue, quote_candidate=fake_quote, poll_interval_seconds=0.2)

    assert daemon.process_once() is True
    assert queue.stats()["ready_not_submitted"] == 1


def test_cow_quote_daemon_retries_transient_quote_failures():
    queue = CowCandidateQueue(max_size=10)
    queue.enqueue_many([_candidate()], source="test")

    def fail_quote(candidate, database_url):
        raise RuntimeError("temporary quote timeout")

    daemon = CowQuoteDaemon(
        queue,
        quote_candidate=fail_quote,
        poll_interval_seconds=0.2,
        max_attempts=2,
        retry_delay_seconds=30,
    )

    assert daemon.process_once() is True
    assert queue.stats()["retry_wait"] == 1

    item = queue.snapshot(limit=1)[0]
    queue.requeue(item["signature"])

    assert daemon.process_once() is True
    assert queue.stats()["failed"] == 1


def test_cow_quote_daemon_records_failed_attempt_when_quote_candidate_raises():
    queue = CowCandidateQueue(max_size=10)
    queue.enqueue_many([_candidate()], source="test")
    recorded = {}

    def fail_quote(candidate, database_url):
        raise RuntimeError("temporary quote timeout")

    def record_attempts(attempts, database_url):
        recorded["attempts"] = attempts
        recorded["database_url"] = database_url
        return {"recorded": len(attempts), "source": "test", "error": None}

    daemon = CowQuoteDaemon(
        queue,
        database_url_provider=lambda: "postgres://example",
        quote_candidate=fail_quote,
        record_attempts=record_attempts,
        poll_interval_seconds=0.2,
        max_attempts=1,
    )

    assert daemon.process_once() is True
    assert recorded["database_url"] == "postgres://example"
    assert recorded["attempts"][0]["state"] == "quote_failed"
    assert recorded["attempts"][0]["execution_phase"] == "quote_precheck"
    assert recorded["attempts"][0]["quote"]["cow_sdk_result"]["status"] == "quote_failed"
    assert queue.stats()["failed"] == 1


def test_default_quote_candidate_rebuilds_execution_plan_and_support(monkeypatch):
    owner = "0x" + "9" * 40
    monkeypatch.setenv("COW_OWNER_BNB", owner)

    usdc = CowToken("USDC", "0x" + "1" * 40, 6, "test")
    ape = CowToken("APE", "0x" + "2" * 40, 18, "test")
    pyr = CowToken("PYR", "0x" + "3" * 40, 18, "test")
    registry = {token.symbol: token for token in [usdc, ape, pyr]}
    registry.update({token.address: token for token in [usdc, ape, pyr]})

    monkeypatch.setattr(
        "web.binance_market_service.load_cow_supported_token_registry",
        lambda **kwargs: {"network": "bnb", "chain_id": 56, "registry": registry},
    )

    def fake_quote(**kwargs):
        sell = kwargs["sell_token"].symbol
        buy = kwargs["buy_token"].symbol
        if (sell, buy) == ("USDC", "APE"):
            buy_amount = "500000000000000000000"
        elif (sell, buy) == ("APE", "PYR"):
            buy_amount = "1100000000000000000000"
        else:
            buy_amount = "1010000000"
        return {
            "quote": {
                "buyAmount": buy_amount,
                "sellAmount": kwargs["sell_amount_units"],
                "feeAmount": "0",
                "gasAmount": "21000",
            }
        }

    monkeypatch.setattr("execution.cow_routes.post_cow_quote", fake_quote)
    queue = CowCandidateQueue(max_size=10)
    queue.enqueue_many([_candidate()], source="test")
    candidate = queue.claim_next()

    result = default_quote_candidate(candidate, database_url=None)
    quote = result["payload"]["ranking"][0]
    plan = quote["binance_execution_plan"]

    assert quote["cow_support"]["supported"] is True
    assert plan["available"] is True
    assert plan["steps"][0]["query_buy_amount_after_fee"] == "500"
    assert quote["execution_precheck"]["route_supported"] is True
    assert result["attempts"][0]["quote"]["quote_verified"] is True
