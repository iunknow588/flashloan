from market.binance_market.dex_session_cache import (
    cache_session_payload,
    cached_session_payload,
    clear_dex_session_cache,
    session_cached_call,
    session_fingerprint,
)


def test_session_fingerprint_invalidates_on_block_and_sqrt_change():
    base = session_fingerprint(block_number=100, sqrt_price_x96="1234")
    new_block = session_fingerprint(block_number=101, sqrt_price_x96="1234")
    new_sqrt = session_fingerprint(block_number=100, sqrt_price_x96="1235")
    new_pool = session_fingerprint(
        block_number=100,
        sqrt_price_x96="1234",
        pool_ids=["0xpool1", "0xpool2"],
    )
    other_pool = session_fingerprint(
        block_number=100,
        sqrt_price_x96="1234",
        pool_ids=["0xpool1", "0xpool3"],
    )

    assert base == session_fingerprint(block_number=100, sqrt_price_x96="1234")
    assert base != new_block
    assert base != new_sqrt
    assert new_pool != other_pool


def test_session_cache_returns_deepcopy_and_survives_mutation():
    key_parts = ("robinhood", 4663, "0xpool")
    cache_session_payload(
        kind="quote",
        key_parts=key_parts,
        payload={"amountOutRaw": "1", "nested": {"ok": True}},
    )

    first = cached_session_payload(kind="quote", key_parts=key_parts)
    first["amountOutRaw"] = "999"
    first["nested"]["ok"] = False

    second = cached_session_payload(kind="quote", key_parts=key_parts)
    assert second is not None
    assert second["amountOutRaw"] == "1"
    assert second["nested"]["ok"] is True

    clear_dex_session_cache()


def test_session_cache_expires_when_ttl_is_zero():
    key_parts = ("robinhood-testnet", 46630, "0xpool")
    cache_session_payload(
        kind="route",
        key_parts=key_parts,
        payload={"keysets": [[1]], "directionsets": [[True]]},
        ttl_seconds=0.0,
    )

    assert cached_session_payload(kind="route", key_parts=key_parts) is None


def test_session_cached_call_skips_builder_and_reruns_on_fingerprint_change():
    calls = []

    def builder(value):
        def _build():
            calls.append(value)
            return {"value": value}

        return _build

    key = ("robinhood-testnet", 46630, session_fingerprint(block_number=1, sqrt_price_x96="1"))
    first = session_cached_call(kind="live_quote", key_parts=key, builder=builder("a"))
    second = session_cached_call(kind="live_quote", key_parts=key, builder=builder("b"))

    assert calls == ["a"]
    assert first == {"value": "a"}
    assert second == {"value": "a"}

    changed = ("robinhood-testnet", 46630, session_fingerprint(block_number=2, sqrt_price_x96="1"))
    third = session_cached_call(kind="live_quote", key_parts=changed, builder=builder("b"))
    assert calls == ["a", "b"]
    assert third == {"value": "b"}

    clear_dex_session_cache()


def test_session_cache_is_process_local_and_never_writes_files(tmp_path):
    before = {path.name for path in tmp_path.iterdir()}

    cache_session_payload(
        kind="analysis",
        key_parts=("local",),
        payload={"route": "snapshot"},
    )
    assert cached_session_payload(kind="analysis", key_parts=("local",)) is not None

    after = {path.name for path in tmp_path.iterdir()}
    assert before == after
    assert clear_dex_session_cache() >= 1
