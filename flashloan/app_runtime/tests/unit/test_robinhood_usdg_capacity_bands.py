"""Tests for the USDG carrying-capacity banding of the candidate pools.

VAL-4-9
--------

The banding tool classifies every candidate pool into exactly one of three
bands: ``carryable``, ``dust`` and ``unmeasured``. The properties that
matter are:

* every candidate pool lands in exactly one band;
* a pool that was never measured is ``unmeasured``, never ``dust``;
* a pool that was measured as empty is ``dust`` with an explicit reason,
  because "empty" is a measurement while "unknown" is not;
* the binding side is the smaller of the two decimals-normalised sides;
* the USDG conversion is exact integer arithmetic.
"""

from __future__ import annotations

from dex.tools.band_robinhood_candidate_usdg_capacity import (
    BAND_CARRYABLE,
    BAND_DUST,
    BAND_UNMEASURED,
    CANONICAL_USDG_ADDRESS,
    NATIVE_TOKEN_ADDRESS,
    USDG_REFERENCE_VALUE_RAW,
    _band_pool,
    _read_depth_rows_from_db,
    _read_valuations_from_db,
)

# The valued token is worth two USDG per whole token.
VALUATION_USDG_RAW = 2_000_000

# The minimum economic position is ten USDG.
MINIMUM_USDG_RAW = 10_000_000


def _depth_row(
    pool_id: str = "0xpool",
    capacity0: int = 10**18,
    capacity1: int = 10**18,
    decimals0: int = 18,
    decimals1: int = 18,
    has_liquidity: bool = True,
) -> dict:
    return {
        "poolId": pool_id,
        "hasLiquidity": has_liquidity,
        "token0Decimals": decimals0,
        "token1Decimals": decimals1,
        "capacity": {
            "capacityToken0Raw": str(capacity0),
            "capacityToken1Raw": str(capacity1),
            "priceImpactBps": 9.9,
        },
    }


def _pair(token0: str = NATIVE_TOKEN_ADDRESS, token1: str = "0xtoken") -> dict:
    return {"token0": token0, "token1": token1, "sources": ["dex_candidate_pools"]}


# Distinguishes "the caller did not supply a depth row" from "the caller
# explicitly said there is no depth row", which are different statements.
_DEFAULT = object()


def _band(
    depth_row: dict | None | object = _DEFAULT,
    pair: dict | None = None,
    decimals: dict | None = None,
    valuations: dict | None = None,
    minimum_usdg_raw: int = MINIMUM_USDG_RAW,
) -> dict:
    """Band one pool whose BINDING side is the valued token.

    The native asset carries eighteen decimals and one whole token is worth
    one normalised unit. The valued token has to be the smaller side for
    its valuation to be the binding one, so its capacity is set to a tenth
    of the native side.

    Passing ``depth_row=None`` explicitly means "this pool was never
    measured"; omitting the argument means "use the default dust row".
    """
    if depth_row is _DEFAULT:
        depth_row = _depth_row(capacity0=10**18, capacity1=10**17)
    if pair is None:
        pair = _pair()
    if decimals is None:
        decimals = {NATIVE_TOKEN_ADDRESS: 18, "0xtoken": 18}
    if valuations is None:
        valuations = {"0xtoken": VALUATION_USDG_RAW}
    return _band_pool(
        "0xpool",
        depth_row,
        pair,
        decimals,
        valuations,
        minimum_usdg_raw,
    )


def test_a_measured_and_valued_pool_is_banded():
    # 0.1 whole token at two USDG is 0.2 USDG of depth, which is below the
    # ten-USDG floor, so this pool is dust.
    record = _band()
    assert record["band"] == BAND_DUST
    assert record["depthUsdgRaw"] == 200_000
    assert record["depthUsdg"] == "0.200000"


def test_a_pool_that_carries_the_floor_is_carryable():
    # Five whole tokens of the valued side at two USDG is ten USDG of
    # depth, which reaches the floor exactly. The valued side has to be
    # the binding one, so the native side carries more whole tokens.
    record = _band(
        depth_row=_depth_row(capacity0=10**19, capacity1=5 * 10**18),
        valuations={"0xtoken": VALUATION_USDG_RAW},
    )
    assert record["bindingToken"] == "0xtoken"
    assert record["band"] == BAND_CARRYABLE
    assert record["depthUsdgRaw"] == 10_000_000


def test_every_pool_lands_in_exactly_one_band():
    for record in (
        _band(),
        _band(depth_row=None),
        _band(
            depth_row=_depth_row(
                has_liquidity=False,
                capacity0=0,
                capacity1=0,
            )
        ),
    ):
        assert record["band"] in {BAND_CARRYABLE, BAND_DUST, BAND_UNMEASURED}


def test_an_unmeasured_pool_is_unmeasured_and_never_dust():
    # No depth row at all: the pool was never looked at.
    record = _band(depth_row=None)
    assert record["band"] == BAND_UNMEASURED
    assert record["depthUsdgRaw"] is None
    assert record["depthUsdg"] is None
    assert "no measured depth row" in record["reason"]


def test_a_pool_without_a_valuation_is_unmeasured():
    record = _band(valuations={})
    assert record["band"] == BAND_UNMEASURED
    assert "no USDG valuation" in record["reason"]


def test_a_pool_with_no_live_liquidity_is_dust_not_unmeasured():
    # The pool WAS measured: the measurement says it is empty. That is a
    # different statement from "nobody measured it".
    record = _band(
        depth_row=_depth_row(
            has_liquidity=False,
            capacity0=0,
            capacity1=0,
        )
    )
    assert record["band"] == BAND_DUST
    assert record["reason"] == "pool has no live liquidity"
    assert record["depthUsdgRaw"] == 0


def test_a_shallow_pool_is_dust():
    # One thousandth of a USDG cannot carry a ten-USDG position.
    record = _band(valuations={"0xtoken": 1_000})
    assert record["band"] == BAND_DUST
    assert record["depthUsdgRaw"] < MINIMUM_USDG_RAW


def test_the_binding_side_is_the_smaller_decimals_normalised_side():
    # token0 carries one whole token, token1 carries one billion raw units.
    # After normalisation token1 is the smaller side, so it binds.
    record = _band(
        depth_row=_depth_row(capacity0=10**18, capacity1=1_000_000_000),
        pair=_pair(token0=NATIVE_TOKEN_ADDRESS, token1="0xtoken"),
        decimals={NATIVE_TOKEN_ADDRESS: 18, "0xtoken": 18},
        valuations={"0xtoken": VALUATION_USDG_RAW},
    )
    assert record["bindingSide"] == "token1"
    assert record["bindingToken"] == "0xtoken"


def test_the_usdg_conversion_is_exact_integer_arithmetic():
    # One whole token worth two USDG is two USDG of depth.
    record = _band(
        depth_row=_depth_row(capacity0=10**18, capacity1=10**18),
        pair=_pair(token0="0xtoken", token1=NATIVE_TOKEN_ADDRESS),
        decimals={"0xtoken": 18, NATIVE_TOKEN_ADDRESS: 18},
        valuations={"0xtoken": VALUATION_USDG_RAW},
    )
    assert record["depthUsdgRaw"] == 2_000_000
    assert record["depthUsdg"] == "2.000000"


def test_the_usdg_reference_valuation_is_one_to_one():
    # The quote asset has no route to itself, so its value is a definition.
    assert USDG_REFERENCE_VALUE_RAW == 1_000_000
    # USDG has six decimals, so one million raw units is one whole USDG.
    # The valued side has to be the binding one for its reference
    # valuation to be the one that applies.
    record = _band(
        depth_row=_depth_row(
            capacity0=10**6,
            capacity1=10**18,
            decimals0=6,
            decimals1=18,
        ),
        pair=_pair(token0=CANONICAL_USDG_ADDRESS, token1="0xtoken"),
        decimals={CANONICAL_USDG_ADDRESS: 6, "0xtoken": 18},
        valuations={CANONICAL_USDG_ADDRESS: USDG_REFERENCE_VALUE_RAW},
    )
    assert record["bindingToken"] == CANONICAL_USDG_ADDRESS
    assert record["valuationSource"] == "reference_definition_usdg_quote_asset"


def test_a_pool_without_known_decimals_is_unmeasured():
    # The depth row itself does not carry the decimals either.
    depth_row = _depth_row(capacity0=10**18, capacity1=10**17)
    depth_row["token0Decimals"] = None
    depth_row["token1Decimals"] = None
    record = _band(depth_row=depth_row, decimals={})
    assert record["band"] == BAND_UNMEASURED
    assert "known decimals" in record["reason"]


def test_a_pool_without_a_token_pair_is_unmeasured():
    record = _band(pair={"token0": "", "token1": "", "sources": []})
    assert record["band"] == BAND_UNMEASURED
    assert "no token pair" in record["reason"]

# ---------------------------------------------------------------------------
# The durable-store readers.
#
# The banding used to read two transient report files.  It now reads the
# database directly, so that the bands cannot drift away from the
# valuations and the depth measurements the admission gate actually used.
# ---------------------------------------------------------------------------


def _store_schema(connection) -> None:
    connection.executescript(
        """
        CREATE TABLE token_usdg_valuations (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            token_address TEXT NOT NULL,
            verdict TEXT NOT NULL,
            has_value INTEGER NOT NULL,
            usdg_amount_raw TEXT,
            PRIMARY KEY (network, chain_id, token_address)
        );
        CREATE TABLE v4_exchange_pool_liquidity_snapshots (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            pool_id TEXT NOT NULL,
            has_liquidity INTEGER NOT NULL,
            capacity_token0_raw TEXT,
            capacity_token1_raw TEXT,
            capacity_raw TEXT,
            price_impact_bps REAL
        );
        """
    )


def test_the_store_reads_only_valued_routes(tmp_path):
    import sqlite3

    path = tmp_path / "store.sqlite3"
    connection = sqlite3.connect(path)
    _store_schema(connection)
    connection.executemany(
        "INSERT INTO token_usdg_valuations "
        "(network, chain_id, token_address, verdict, has_value, usdg_amount_raw) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("robinhood", 4663, "0xVALUED", "route_found", 1, "1500000"),
            ("robinhood", 4663, "0xNOROUTE", "no_route", 0, None),
            ("robinhood", 4663, "0xERROR", "api_error", 0, None),
            ("othernet", 1, "0xOTHERNET", "route_found", 1, "999"),
            ("robinhood", 1, "0xOTHERCHAIN", "route_found", 1, "999"),
        ],
    )
    connection.commit()

    valuations = _read_valuations_from_db(connection)
    connection.close()

    # A negative and an unknown both carry no value.  Neither becomes a
    # zero, and neither leaks in from another network or chain.
    assert valuations == {"0xvalued": 1_500_000}


def test_the_store_reads_the_measured_pool_capacity(tmp_path):
    import sqlite3

    path = tmp_path / "store.sqlite3"
    connection = sqlite3.connect(path)
    _store_schema(connection)
    connection.executemany(
        "INSERT INTO v4_exchange_pool_liquidity_snapshots "
        "(network, chain_id, pool_id, has_liquidity, capacity_token0_raw, "
        " capacity_token1_raw, capacity_raw, price_impact_bps) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("robinhood", 4663, "0xPOOL", 1, "100", "200", "200", 9.9),
            ("robinhood", 4663, "0xUNMEASURED", 1, None, None, None, None),
            ("othernet", 1, "0xOTHERNET", 1, "1", "1", "1", 1.0),
        ],
    )
    connection.commit()

    measured = _read_depth_rows_from_db(connection)
    connection.close()

    # Only a pool that was actually measured has a capacity.  A pool with
    # no capacity row is "not measured", which is not the same as "empty".
    assert set(measured) == {"0xpool"}
    row = measured["0xpool"]
    assert row["hasLiquidity"] is True
    assert row["capacity"]["capacityToken0Raw"] == "100"
    assert row["capacity"]["capacityToken1Raw"] == "200"
    assert row["priceImpactBps"] == 9.9


def test_the_store_readers_survive_a_database_without_the_tables(tmp_path):
    import sqlite3

    path = tmp_path / "empty.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE unrelated (id INTEGER)")
    connection.commit()

    # A database that predates the stores reads as "no observation", never
    # as an error and never as a fabricated zero.
    assert _read_valuations_from_db(connection) == {}
    assert _read_depth_rows_from_db(connection) == {}
    connection.close()
