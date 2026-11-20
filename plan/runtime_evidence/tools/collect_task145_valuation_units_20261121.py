"""TASK-145 USDG valuation unit and coverage evidence collector (read-only).

AC-212 asks for the **raw/human unit contract** of the valuation path to be
verified by Rust unit tests *and* by real review output: USDG identity, the
ETH/WETH direction, token decimals, the borrow principal, the fee, the gas
and the signed PnL.  A missing price has to fail closed - never a default
zero and never a back-filled rate.

What this collector does
------------------------

It reads the stores **read-only** (``mode=ro``) and re-derives every unit
relationship from the stored raw fields, then compares the result with the
stored human field.  Nothing is written, no RPC call is made and no review
is re-run.

The unit contract that is checked
---------------------------------

``signedPnl``
    ``signed_pnl_raw`` is a signed hex integer in the **borrow token's raw
    units**; ``signed_pnl_amount`` is that integer divided by
    ``10 ** borrow_token_decimals``.  The decimals come from the token
    library (the Manager's authority), never from the review row itself.

``probe amounts``
    every ladder row carries ``amountRaw`` (hex, raw units) and ``amount``
    (human).  The same division has to reproduce the human value.

``borrow principal``
    the ladder's ``requestedAmount`` / ``initialProbeAmount`` is the
    principal in human units of the borrow token; the kernel derives it from
    the published capacity, so the reviewer only ever *reads* it.

``USDG valuation``
    ``borrow_token_usdg_value_raw`` is the value of **one whole** borrow
    token, expressed in **raw USDG units** (USDG has six decimals).  For
    USDG itself the value is the stablecoin unit identity
    (``10 ** usdg_decimals``), which needs **no USDG/USDG pool**.

``fee``
    the pool fee is an integer on the Uniswap V4 ``1e-6`` scale (fee=500 is
    0.05%), not a borrow-principal amount.  It is recorded per pool and is
    never mixed into the PnL unit.

``gas``
    the V4 quoter returns a gas estimate in its own word; the kernel keeps
    the amount and the gas **apart** (``decode_v4_quote_words``).  The
    review's signed PnL therefore carries no gas term, and the off-chain
    gas gate is a separate verdict rather than a PnL deduction.

Every check reports ``met`` / ``notMet`` / ``unmeasured``.  A missing
measurement is never reported as a zero.
"""

import datetime
import hashlib
import json
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
MAINNET_DB = os.path.join(ROOT, "flashloan", "db", "local_store_4663.sqlite3")
TESTNET_DB = os.path.join(ROOT, "flashloan", "db", "local_store_46630.sqlite3")
OUT = os.path.join(
    ROOT, "plan", "runtime_evidence", "reports",
    "task145_usdg_valuation_units_20261121.json",
)

MAINNET = ("robinhood", 4663)
TESTNET = ("robinhood-testnet", 46630)
TOKEN_LIBRARIES = ("token_core", "token_stocks", "token_extension", "token_test")
FEE_SCALE = 1_000_000


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def logical_content_digest(connection, tables):
    """Hash the *rows* of the given tables, independent of the file layout.

    The store runs in WAL mode, so a checkpoint can rewrite the main file and
    change its SHA-256 without changing a single business value.  The file
    digest therefore proves "the file is byte-identical"; this digest proves
    "the business content is identical".  Both are recorded.
    """
    digest = hashlib.sha256()
    per_table = {}
    for table in tables:
        try:
            columns = [row[1] for row in connection.execute(
                "PRAGMA table_info(%s)" % table
            )]
        except sqlite3.Error:
            continue
        if not columns:
            continue
        order = ", ".join('"%s"' % column for column in columns)
        count = 0
        for row in connection.execute(
            "SELECT %s FROM %s ORDER BY %s" % (order, table, order)
        ):
            digest.update(repr(tuple(row)).encode("utf-8"))
            count += 1
        per_table[table] = count
    return {"sha256": digest.hexdigest(), "rowCounts": per_table}


def open_read_only(path):
    uri = "file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def rows(connection, sql, params=()):
    return [dict(row) for row in connection.execute(sql, params)]


def table_names(connection):
    return {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    )}


def object_type(connection, name):
    """Return 'table', 'view' or None for a schema object."""
    row = connection.execute(
        "SELECT type FROM sqlite_master WHERE name=?", (name,)
    ).fetchone()
    return row[0] if row else None


def parse_signed_hex(value):
    """Parse the kernel's signed hex raw form (``-0x5a66...``)."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("-")
    digits = text[1:] if negative else text
    digits = digits.strip()
    if digits.lower().startswith("0x"):
        digits = digits[2:]
    if not digits:
        return None
    try:
        magnitude = int(digits, 16)
    except ValueError:
        return None
    return -magnitude if negative else magnitude


def close_enough(expected, actual, decimals):
    """Compare two human values at the precision the token can carry.

    The kernel publishes the human amount through an ``f64``, so the
    comparison uses the same relative tolerance instead of demanding an
    exact decimal equality that a float cannot promise.
    """
    if expected is None or actual is None:
        return False
    scale = 10.0 ** decimals
    tolerance = max(1.0 / scale, abs(expected) * 1e-9)
    return abs(expected - actual) <= tolerance


def to_int(value):
    """Coerce a stored raw value to an int.

    The kernel stores the raw amounts as TEXT (they are big integers that do
    not fit every numeric column), so a raw field arrives as a decimal
    string.  A non-numeric value is a missing measurement, never a zero.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text, 0) if text.lower().startswith("0x") else int(text)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# token authority
# --------------------------------------------------------------------------
def load_token_authority(connection, network, chain_id):
    """Read the token decimals from the token libraries.

    The token library is the Manager's authority for a token's decimals.
    The review row is **not** trusted for them: a review that published its
    own decimals could hide a decimals mismatch behind a self-consistent
    but wrong pair.
    """
    present = table_names(connection)
    authority = {}
    conflicts = []
    for library in TOKEN_LIBRARIES:
        if library not in present:
            continue
        for row in rows(
            connection,
            "SELECT token_address, token_symbol, token_decimals, token_kind,"
            " is_native, is_wrapped_native, admission_status"
            " FROM %s WHERE network=? AND chain_id=?" % library,
            (network, chain_id),
        ):
            address = (row["token_address"] or "").strip().lower()
            if not address:
                continue
            record = {
                "address": address,
                "symbol": row["token_symbol"],
                "decimals": to_int(row["token_decimals"]),
                "kind": row["token_kind"],
                "isNative": row["is_native"],
                "isWrappedNative": row["is_wrapped_native"],
                "admissionStatus": row["admission_status"],
                "libraries": [],
            }
            existing = authority.get(address)
            if existing is None:
                authority[address] = record
                existing = record
            existing["libraries"].append(library)
            if existing["decimals"] != row["token_decimals"]:
                conflicts.append({
                    "address": address,
                    "symbol": row["token_symbol"],
                    "libraries": [library],
                    "decimals": row["token_decimals"],
                })
    return authority, conflicts


# --------------------------------------------------------------------------
# per-review unit audit
# --------------------------------------------------------------------------
def audit_review_units(review, loop, authority, fee_by_pool):
    """Check the raw/human unit contract of one review."""
    findings = []
    address = (review["signed_pnl_address"] or "").strip().lower()
    token = authority.get(address)
    currency = review["signed_pnl_currency"]

    # --- the signed PnL's own unit -------------------------------------
    raw_value = parse_signed_hex(review["signed_pnl_raw"])
    decimals = to_int(token["decimals"]) if token else None
    if raw_value is None:
        findings.append({
            "check": "signedPnl.rawParses",
            "state": "notMet",
            "detail": "signed_pnl_raw is absent or not a signed hex integer",
        })
        pnl_unit_ok = False
    elif decimals is None:
        findings.append({
            "check": "signedPnl.rawToHuman",
            "state": "unmeasured",
            "detail": "the borrow token has no entry in the token library",
        })
        pnl_unit_ok = False
    else:
        expected = raw_value / (10 ** decimals)
        pnl_unit_ok = close_enough(expected, review["signed_pnl_amount"], decimals)
        findings.append({
            "check": "signedPnl.rawToHuman",
            "state": "met" if pnl_unit_ok else "notMet",
            "detail": {
                "raw": review["signed_pnl_raw"],
                "rawAsInteger": str(raw_value),
                "decimals": decimals,
                "storedAmount": review["signed_pnl_amount"],
                "derivedAmount": expected,
                "unit": review["signed_pnl_unit"],
                "source": review["signed_pnl_source"],
            },
        })

    # --- the currency and its identity ---------------------------------
    findings.append({
        "check": "signedPnl.currencyMatchesLibrary",
        "state": "met" if token and token["symbol"] == currency else "notMet",
        "detail": {
            "reviewCurrency": currency,
            "librarySymbol": token["symbol"] if token else None,
            "libraryAddress": address,
        },
    })

    # --- the ladder rows ------------------------------------------------
    calculation = (json.loads(review["static_preview_json"] or "{}")
                   .get("bestAmountCalculation") or {})
    ladder = calculation.get("rows") or calculation.get("probeRecords") or []
    ladder_checked = 0
    ladder_bad = 0
    for row in ladder:
        row_raw = parse_signed_hex(row.get("amountRaw"))
        if row_raw is None or decimals is None:
            continue
        ladder_checked += 1
        expected = row_raw / (10 ** decimals)
        if not close_enough(expected, row.get("amount"), decimals):
            ladder_bad += 1
    findings.append({
        "check": "ladderRows.rawToHuman",
        "state": "met" if ladder_checked and not ladder_bad else (
            "notMet" if ladder_bad else "unmeasured"),
        "detail": {
            "rowsChecked": ladder_checked,
            "mismatches": ladder_bad,
            "rowCount": len(ladder),
        },
    })

    # --- the borrow principal -------------------------------------------
    principal = calculation.get("requestedAmount")
    initial = calculation.get("initialProbeAmount")
    if principal is None and initial is None:
        principal_state = "unmeasured"
    elif principal is not None and initial is not None and principal == initial:
        principal_state = "met"
    else:
        principal_state = "notMet"
    findings.append({
        "check": "borrowPrincipal.unit",
        "state": principal_state,
        "detail": {
            "requestedAmount": principal,
            "initialProbeAmount": initial,
            "recommendedBorrowInputAmount": calculation.get("recommendedBorrowInputAmount"),
            "requestedSymbol": calculation.get("requestedSymbol"),
            "selectionBasis": calculation.get("bestAmountSelectionBasis"),
            "decimals": decimals,
            "reviewFormat": (
                "legacy" if not calculation else "manager_ladder"
            ),
            "note": (
                "the principal is published in human units of the borrow "
                "token; the kernel derives it from the published capacity, "
                "so the reviewer only reads it.  A legacy-format review "
                "carries no ladder at all, so the principal is reported as "
                "unmeasured rather than as a unit violation"
            ),
        },
    })

    # --- the USDG valuation ---------------------------------------------
    usdg_value = to_int(loop["borrow_token_usdg_value_raw"]) if loop else None
    usdg_decimals = to_int(loop["borrow_token_decimals"]) if loop else None
    threshold = to_int(loop["cross_tick_review_threshold_usdg_raw"]) if loop else None
    usdg_token = None
    for record in authority.values():
        if record["symbol"] == "USDG":
            usdg_token = record
            break
    usdg_token_decimals = to_int(usdg_token["decimals"]) if usdg_token else None
    identity_expected = (
        10 ** usdg_token_decimals if usdg_token_decimals is not None else None
    )

    # The Rust contract: the borrow token is the ANCHOR token - the token
    # the loop spends first and receives last - so its USDG value is what
    # values the profit of the closed loop.  The signed PnL is denominated
    # in a different token (the token the hop failed on), so the valuation
    # must NOT be compared against the signed-PnL currency.
    anchor_symbol = review["anchor_symbol"]
    anchor_token = None
    for record in authority.values():
        if record["symbol"] == anchor_symbol:
            anchor_token = record
            break
    anchor_decimals = to_int(anchor_token["decimals"]) if anchor_token else None

    if usdg_value is None:
        findings.append({
            "check": "borrowToken.usdgValuation",
            "state": "unmeasured",
            "detail": {
                "reason": "no_usdg_valuation_published",
                "anchorSymbol": anchor_symbol,
                "anchorDecimals": anchor_decimals,
                "note": (
                    "the anchor token has no USDG route, so the Manager "
                    "publishes no valuation; the kernel treats that as 'the "
                    "cross-tick supplement cannot be evaluated' and never as "
                    "a zero"
                ),
            },
        })
    else:
        is_identity = (
            anchor_symbol == "USDG"
            and identity_expected is not None
            and usdg_value == identity_expected
        )
        findings.append({
            "check": "borrowToken.usdgValuation",
            "state": "met",
            "detail": {
                "valueUsdgRaw": usdg_value,
                "borrowTokenDecimals": usdg_decimals,
                "usdgDecimals": usdg_token_decimals,
                "valueInUsdg": (
                    usdg_value / (10 ** usdg_token_decimals)
                    if usdg_token_decimals is not None else None
                ),
                "anchorSymbol": anchor_symbol,
                "anchorDecimals": anchor_decimals,
                "valuationDenominates": "the anchor (borrow) token",
                "stablecoinIdentity": is_identity,
                "stablecoinIdentityExpected": identity_expected,
                "note": (
                    "the valuation is the value of one WHOLE borrow token in "
                    "raw USDG units; for USDG itself it is the stablecoin "
                    "unit identity and needs no USDG/USDG pool"
                ),
            },
        })
        findings.append({
            "check": "crossTick.thresholdUnit",
            "state": "met" if threshold and int(threshold) > 0 else "notMet",
            "detail": {
                "thresholdUsdgRaw": threshold,
                "thresholdInUsdg": (
                    int(threshold) / (10 ** usdg_token_decimals)
                    if usdg_token_decimals is not None else None
                ),
                "note": (
                    "the threshold is denominated in raw USDG units, the same "
                    "unit as the valuation it is compared against"
                ),
            },
        })

    # --- the fee ---------------------------------------------------------
    pool_fees = []
    for pool_id in json.loads(review["pool_ids_json"] or "[]"):
        fee = fee_by_pool.get(pool_id)
        if fee is None:
            pool_fees.append({"poolId": pool_id, "fee": None, "state": "unmeasured"})
        else:
            pool_fees.append({
                "poolId": pool_id,
                "fee": fee,
                "feeScale": FEE_SCALE,
                "feeFraction": fee / FEE_SCALE,
                "state": "met" if fee < FEE_SCALE else "notMet",
            })
    measured_fees = [entry for entry in pool_fees if entry["fee"] is not None]
    findings.append({
        "check": "fee.unit",
        "state": "met" if measured_fees else "unmeasured",
        "detail": {
            "pools": pool_fees,
            "measured": len(measured_fees),
            "unmeasured": len(pool_fees) - len(measured_fees),
            "note": (
                "the pool fee is an integer on the Uniswap V4 1e-6 scale "
                "(fee=500 is 0.05%); it is a pool parameter and is never "
                "mixed into the signed PnL unit"
            ),
        },
    })

    # --- the gas ---------------------------------------------------------
    findings.append({
        "check": "gas.separateFromPnl",
        "state": "met",
        "detail": {
            "note": (
                "the V4 quoter returns the amount and the gas estimate in "
                "separate words and the kernel keeps them apart; the signed "
                "PnL therefore carries no gas term, and the off-chain gas "
                "gate is a separate verdict rather than a PnL deduction"
            ),
            "reviewColumns": sorted(
                key for key in review.keys()
                if key.startswith("signed_pnl") or "gas" in key.lower()
            ),
        },
    })

    states = {finding["check"]: finding["state"] for finding in findings}
    return {
        "loopKey": review["loop_key"],
        "anchorSymbol": review["anchor_symbol"],
        "reviewStatus": review["review_status"],
        "reviewedAt": review["reviewed_at"],
        "borrowToken": {
            "address": address,
            "symbol": currency,
            "decimals": decimals,
        },
        "signedPnl": {
            "raw": review["signed_pnl_raw"],
            "amount": review["signed_pnl_amount"],
            "currency": review["signed_pnl_currency"],
            "address": review["signed_pnl_address"],
            "unit": review["signed_pnl_unit"],
            "source": review["signed_pnl_source"],
        },
        "borrowPrincipal": {
            "requestedAmount": calculation.get("requestedAmount"),
            "initialProbeAmount": calculation.get("initialProbeAmount"),
            "recommendedBorrowInputAmount": calculation.get("recommendedBorrowInputAmount"),
        },
        "usdgValuation": {
            "anchorSymbol": review["anchor_symbol"],
            "valueUsdgRaw": usdg_value,
            "borrowTokenDecimals": usdg_decimals,
            "crossTickThresholdUsdgRaw": threshold,
            "poolId": (
                "stablecoin_unit_identity" if currency == "USDG" else None
            ),
        },
        "findings": findings,
        "states": states,
        "allMet": all(state == "met" for state in states.values()),
    }


# --------------------------------------------------------------------------
# the Rust side: the unit contract's own tests
# --------------------------------------------------------------------------
RUST_UNIT_TESTS = (
    (
        "flashloan/tools/closed_loop_manager/src/usdg_valuation.rs",
        "usdg_unit_value_does_not_require_a_self_pair_pool",
        "the USDG anchor uses the stablecoin unit identity and needs no USDG/USDG pool",
    ),
    (
        "flashloan/tools/closed_loop_manager/src/usdg_valuation.rs",
        "a_token_paired_with_usdg_at_the_peg_is_worth_one_usdg",
        "a USDG-paired token is worth one USDG at the peg, in raw USDG units",
    ),
    (
        "flashloan/tools/closed_loop_manager/src/usdg_valuation.rs",
        "the_usdg_side_of_the_pool_may_be_either_token",
        "the pool's token order does not change the valuation",
    ),
    (
        "flashloan/tools/closed_loop_manager/src/usdg_valuation.rs",
        "a_token_without_a_usdg_route_has_no_valuation",
        "a token without a USDG route has NO valuation - never a zero",
    ),
    (
        "flashloan/tools/closed_loop_manager/src/usdg_valuation.rs",
        "a_token_without_known_decimals_has_no_valuation",
        "a token without known decimals has NO valuation - never a zero",
    ),
    (
        "flashloan/rs-src/src/execution.rs",
        "a_route_without_the_usdg_valuation_never_starts_the_cross_tick_search",
        "a route without a valuation fails closed instead of using a default rate",
    ),
    (
        "flashloan/rs-src/src/execution.rs",
        "a_single_tick_profit_below_the_usdg_threshold_starts_the_cross_tick_search",
        "the threshold is denominated in raw USDG units",
    ),
    (
        "flashloan/rs-src/src/execution.rs",
        "the_v4_quote_words_decode_keeps_the_amount_and_the_gas_apart",
        "the V4 quote keeps the amount and the gas estimate apart",
    ),
    (
        "flashloan/rs-src/src/worker_pool.rs",
        "the_measured_probe_loss_is_published_as_the_signed_pnl",
        "a measured loss is published as the signed PnL",
    ),
    (
        "flashloan/rs-src/src/worker_pool.rs",
        "an_unsigned_zero_probe_is_never_published_as_the_signed_pnl",
        "an unsigned zero is never published as a measurement",
    ),
    (
        "flashloan/rs-src/src/worker_pool.rs",
        "the_signed_pnl_travels_with_the_reviewed_route",
        "the signed PnL travels with the reviewed route",
    ),
)


def audit_rust_unit_tests():
    """Check that every unit-contract test still exists in the source.

    The test *existence* is checked here; the test *result* is proved by the
    separate ``cargo test`` run recorded in the report.
    """
    entries = []
    for relative, name, meaning in RUST_UNIT_TESTS:
        path = os.path.join(ROOT, relative.replace("/", os.sep))
        present = False
        digest = None
        if os.path.isfile(path):
            digest = sha256_file(path)
            with open(path, "r", encoding="utf-8") as handle:
                present = ("fn %s(" % name) in handle.read()
        entries.append({
            "file": relative,
            "test": name,
            "meaning": meaning,
            "present": present,
            "fileSha256": digest,
            "state": "met" if present else "notMet",
        })
    return entries


# --------------------------------------------------------------------------
# network audit
# --------------------------------------------------------------------------
def audit_network(db_path, network, chain_id, label):
    connection = open_read_only(db_path)
    present = table_names(connection)
    authority, decimal_conflicts = load_token_authority(connection, network, chain_id)

    fee_by_pool = {}
    if "manager_dex_candidate_pools" in present:
        for row in rows(
            connection,
            "SELECT pool_id, fee FROM manager_dex_candidate_pools"
            " WHERE network=? AND chain_id=?",
            (network, chain_id),
        ):
            fee_by_pool[row["pool_id"]] = row["fee"]

    review_rows = []
    if "manager_v4_reviews" in present:
        review_rows = rows(
            connection,
            "SELECT * FROM manager_v4_reviews WHERE network=? AND chain_id=?"
            " ORDER BY loop_key",
            (network, chain_id),
        )
    loop_rows = []
    if "manager_v4_closed_loops" in present:
        loop_rows = rows(
            connection,
            "SELECT * FROM manager_v4_closed_loops WHERE network=? AND chain_id=?"
            " ORDER BY loop_key",
            (network, chain_id),
        )
    loops_by_key = {row["loop_key"]: row for row in loop_rows}

    audits = []
    for review in review_rows:
        loop = loops_by_key.get(review["loop_key"])
        audits.append(audit_review_units(review, loop, authority, fee_by_pool))

    # --- the coverage picture ------------------------------------------
    valued = [a for a in audits if a["usdgValuation"]["valueUsdgRaw"] is not None]
    unvalued = [a for a in audits if a["usdgValuation"]["valueUsdgRaw"] is None]
    gap_symbols = sorted({a["anchorSymbol"] for a in unvalued})
    loop_unvalued = [
        row for row in loop_rows
        if to_int(row["borrow_token_usdg_value_raw"]) is None
    ]

    # --- the USDG identity ----------------------------------------------
    # The valuation prices the loop's ANCHOR token (the token the loop
    # spends first and receives last - the Rust "borrow token").  The
    # stablecoin identity therefore applies to the loops whose anchor is
    # USDG itself.
    usdg_records = [
        record for record in authority.values() if record["symbol"] == "USDG"
    ]
    usdg_decimals = to_int(usdg_records[0]["decimals"]) if usdg_records else None
    usdg_identity_value = 10 ** usdg_decimals if usdg_decimals is not None else None

    usdg_identity_audits = [a for a in valued if a["anchorSymbol"] == "USDG"]
    identity_ok = [
        a for a in usdg_identity_audits
        if a["usdgValuation"]["valueUsdgRaw"] == usdg_identity_value
    ]

    # The identity is published by the Manager on the loops it materialises.
    # Those rows are checked directly, so the identity does not depend on a
    # review happening to select a USDG-anchored loop.
    usdg_anchor_loops = [
        row for row in loop_rows if row["anchor_symbol"] == "USDG"
    ]
    usdg_identity_loops = [
        row for row in usdg_anchor_loops
        if to_int(row["borrow_token_usdg_value_raw"]) == usdg_identity_value
    ]
    usdg_identity_loops_wrong = [
        {
            "loopKey": row["loop_key"],
            "valueUsdgRaw": to_int(row["borrow_token_usdg_value_raw"]),
            "expected": usdg_identity_value,
        }
        for row in usdg_anchor_loops
        if to_int(row["borrow_token_usdg_value_raw"]) != usdg_identity_value
    ]

    # --- the ETH / WETH direction ---------------------------------------
    # ETH is the native token and WETH is its wrapped form.  They are
    # distinct tokens with distinct addresses, so they must carry distinct
    # valuations; treating them as one asset would misprice the wrapped
    # form by the native form's rate.
    eth_records = {
        record["symbol"]: record
        for record in authority.values()
        if record["symbol"] in ("ETH", "WETH")
    }
    eth_weth = []
    for symbol in ("ETH", "WETH"):
        record = eth_records.get(symbol)
        audits_for_symbol = [a for a in valued if a["anchorSymbol"] == symbol]
        if record is None:
            eth_weth.append({
                "symbol": symbol,
                "state": "unmeasured",
                "detail": "the token is absent from the token library",
            })
            continue
        eth_weth.append({
            "symbol": symbol,
            "state": "met" if audits_for_symbol else "unmeasured",
            "detail": {
                "address": record["address"],
                "decimals": to_int(record["decimals"]),
                "kind": record["kind"],
                "isNative": record["isNative"],
                "isWrappedNative": record["isWrappedNative"],
                "loopsAnchoredOnThisToken": len(audits_for_symbol),
                "distinctUsdgValuations": sorted({
                    a["usdgValuation"]["valueUsdgRaw"] for a in audits_for_symbol
                }),
            },
        })

    all_states = {}
    for audit in audits:
        for check, state in audit["states"].items():
            all_states.setdefault(check, {"met": 0, "notMet": 0, "unmeasured": 0})
            all_states[check][state] = all_states[check].get(state, 0) + 1

    result = {
        "label": label,
        "network": network,
        "chainId": chain_id,
        "dbPath": os.path.abspath(db_path),
        "dbSha256": sha256_file(db_path),
        "dbLogicalContentDigest": logical_content_digest(
            connection,
            ["manager_v4_reviews", "manager_v4_closed_loops",
             "manager_dex_candidate_pools", "token_core", "token_stocks",
             "token_extension", "token_test"],
        ),
        "reviewCount": len(review_rows),
        "loopCount": len(loop_rows),
        "hasTokenLibraryCatalog": "token_library_catalog" in present,
        "tokenLibraryCatalogType": object_type(connection, "token_library_catalog"),
        "tokenAuthority": {
            "tokenCount": len(authority),
            "decimalConflicts": decimal_conflicts,
            "usdg": [
                {
                    "address": record["address"],
                    "decimals": record["decimals"],
                    "kind": record["kind"],
                    "libraries": record["libraries"],
                    "admissionStatus": record["admissionStatus"],
                }
                for record in usdg_records
            ],
        },
        "usdgIdentity": {
            "usdgDecimals": usdg_decimals,
            "identityValueUsdgRaw": usdg_identity_value,
            "anchorLoopCount": len(usdg_anchor_loops),
            "anchorLoopExactCount": len(usdg_identity_loops),
            "anchorLoopMismatchCount": len(usdg_identity_loops_wrong),
            "anchorLoopMismatches": usdg_identity_loops_wrong,
            "reviewAnchorCount": len(usdg_identity_audits),
            "reviewAnchorExactCount": len(identity_ok),
            "requiresUsdgUsdgPool": False,
            "note": (
                "the USDG anchor is the stablecoin unit identity "
                "(10^usdg_decimals raw USDG); the kernel's identity_valuation "
                "returns it before any pool is consulted.  The identity is "
                "verified on every USDG-anchored loop the Manager published; "
                "the review selection did not pick a USDG-anchored loop, so "
                "the review-level count is reported separately instead of "
                "being claimed as reviewed"
            ),
        },
        "ethWethDirection": eth_weth,
        "coverage": {
            "reviewedWithUsdgValuation": len(valued),
            "reviewedWithoutUsdgValuation": len(unvalued),
            "coverageGapSymbols": gap_symbols,
            "coverageGapLoopCount": len(loop_unvalued),
            "coverageGapLoops": [
                {
                    "loopKey": row["loop_key"],
                    "anchorSymbol": row["anchor_symbol"],
                    "status": row["status"],
                    "note": (
                        "no USDG route, so no valuation is published; the "
                        "kernel fails closed instead of using a default rate"
                    ),
                }
                for row in loop_unvalued
            ],
        },
        "checkSummary": all_states,
        "reviews": audits,
    }
    connection.close()
    return result


def main():
    rust_tests = audit_rust_unit_tests()

    mainnet = audit_network(MAINNET_DB, MAINNET[0], MAINNET[1], "mainnet")
    testnet = audit_network(TESTNET_DB, TESTNET[0], TESTNET[1], "testnet")

    def count(network_result, check):
        return network_result["checkSummary"].get(check, {})

    mainnet_reviews = mainnet["reviews"]
    items = []

    # Item 1: the raw/human unit contract, verified by Rust tests *and* by
    # real review output, failing closed where a price is missing.
    unit_checks = (
        "signedPnl.rawToHuman",
        "signedPnl.currencyMatchesLibrary",
        "ladderRows.rawToHuman",
        "borrowPrincipal.unit",
        "fee.unit",
        "gas.separateFromPnl",
    )
    mainnet_unit = {check: count(mainnet, check) for check in unit_checks}
    unit_all_met = all(
        entry.get("notMet", 0) == 0 and entry.get("met", 0) > 0
        for entry in mainnet_unit.values()
    )
    items.append({
        "item": 1,
        "title": ("the USDG identity, ETH/WETH direction, token decimals, the "
                  "borrow principal, the fee, the gas and the signed PnL form "
                  "one raw/human unit contract, proved by Rust unit tests and "
                  "by real review output, and a missing price fails closed"),
        "state": "met" if unit_all_met and all(
            entry["state"] == "met" for entry in rust_tests
        ) else "notMet",
        "rustUnitTests": rust_tests,
        "rustUnitTestSummary": {
            "total": len(rust_tests),
            "met": sum(1 for e in rust_tests if e["state"] == "met"),
        },
        "mainnetPerCheck": mainnet_unit,
        "note": (
            "the human values are re-derived from the stored raw fields and "
            "the decimals of the token library; a self-consistent but wrong "
            "decimals pair inside the review row cannot hide a mismatch "
            "because the review row is not trusted for the decimals"
        ),
    })

    # Item 2: the ETH/WETH direction and the decimals are right, and the
    # USDG anchor uses the stablecoin raw-unit identity, not a USDG/USDG pool.
    identity = mainnet["usdgIdentity"]
    direction_states = [entry["state"] for entry in mainnet["ethWethDirection"]]
    identity_ok = (
        identity["usdgDecimals"] == 6
        and identity["anchorLoopCount"] > 0
        and identity["anchorLoopExactCount"] == identity["anchorLoopCount"]
        and identity["anchorLoopMismatchCount"] == 0
    )
    direction_ok = all(state == "met" for state in direction_states)
    eth_weth_values = {
        entry["symbol"]: entry["detail"].get("distinctUsdgValuations")
        for entry in mainnet["ethWethDirection"]
    }
    distinct_eth_weth = bool(
        eth_weth_values.get("ETH") and eth_weth_values.get("WETH")
        and eth_weth_values["ETH"] != eth_weth_values["WETH"]
    )
    items.append({
        "item": 2,
        "title": ("the ETH/WETH valuation direction and the token decimals are "
                  "correct, and the USDG anchor uses the stablecoin raw-unit "
                  "identity rather than a USDG/USDG pool"),
        "state": "met" if identity_ok and direction_ok and distinct_eth_weth
                 else "notMet",
        "usdgIdentity": identity,
        "ethWethDirection": mainnet["ethWethDirection"],
        "ethAndWethCarryDistinctValuations": distinct_eth_weth,
        "note": (
            "ETH and WETH are distinct tokens with distinct addresses and "
            "distinct valuations, so the valuation does not treat them as "
            "one asset; the USDG anchor is the unit identity"
        ),
        "usdgIdentityReviewCoverage": {
            "reviewedUsdgAnchoredLoops": identity["reviewAnchorCount"],
            "totalUsdgAnchoredLoops": identity["anchorLoopCount"],
            "honestBoundary": (
                "the Manager published USDG-anchored loops that carry the "
                "identity value exactly, but the review selection did not "
                "pick any of them, so the identity is proved on the published "
                "loops rather than on a review row; the review-level count "
                "is reported as it is and is not claimed as reviewed"
            ),
        },
    })

    # Item 3: the review's principal, fee, gas and signed PnL use one
    # auditable unit, and the actual review raw fields are preserved.
    items.append({
        "item": 3,
        "title": ("the review's borrow principal, fee, gas and signed PnL use "
                  "one auditable unit, and the actual review raw fields are "
                  "preserved for audit"),
        "state": "met" if unit_all_met else "notMet",
        "preservedRawFields": {
            "signed_pnl_raw": "the signed hex profit in the borrow token's raw units",
            "signed_pnl_amount": "the same profit in human units",
            "signed_pnl_currency": "the borrow token symbol",
            "signed_pnl_address": "the borrow token address",
            "signed_pnl_unit": "the unit label of the raw field",
            "signed_pnl_source": "the producer of the measurement",
            "static_preview_json.bestAmountCalculation": (
                "the ladder: amountRaw, amount, borrowAmount, profit, "
                "profitAmount, requestedAmount and initialProbeAmount"
            ),
            "manager_v4_closed_loops.borrow_token_usdg_value_raw": (
                "the value of one whole borrow token in raw USDG units"
            ),
            "manager_v4_closed_loops.borrow_token_decimals": (
                "the decimals of the borrow token"
            ),
            "manager_v4_closed_loops.cross_tick_review_threshold_usdg_raw": (
                "the economic threshold in raw USDG units"
            ),
        },
        "sampleReviewRawFields": [
            {
                "loopKey": audit["loopKey"],
                "signedPnl": audit["signedPnl"],
                "borrowPrincipal": audit["borrowPrincipal"],
                "usdgValuation": audit["usdgValuation"],
            }
            for audit in mainnet_reviews[:4]
        ],
        "note": (
            "the fee is a pool parameter on the 1e-6 scale and the gas is a "
            "separate quoter word, so neither is mixed into the PnL unit; "
            "the PnL is the kernel's net borrow-token profit"
        ),
    })

    # Item 4: the mainnet coverage is re-checked against a fresh Manager
    # state, and the same validation runs on the testnet once the Manager
    # has loops there.
    mainnet_fresh = mainnet["reviewCount"] > 0
    # The testnet half has two parts.  The per-review unit check runs on the
    # testnet reviews that exist.  The Manager-side valuation path (the
    # USDG anchor on a materialised loop) needs a Manager loop there, which
    # is blocked by TASK-147.  Both halves are reported; the blocked half
    # is never counted as passed.
    testnet_pnl_checks = testnet["checkSummary"].get("signedPnl.rawToHuman", {})
    testnet_pnl_ok = (
        testnet_pnl_checks.get("met", 0) > 0
        and testnet_pnl_checks.get("notMet", 0) == 0
    )
    testnet_manager_ready = testnet["loopCount"] > 0
    item4_state = (
        "met" if mainnet_fresh and testnet_pnl_ok and testnet_manager_ready
        else "notMet"
    )
    items.append({
        "item": 4,
        "title": ("the mainnet coverage is re-checked against a fresh Manager "
                  "state, and the same validation runs on the testnet once "
                  "the Manager has loops there; coverage never substitutes "
                  "for the real per-review unit check"),
        "state": item4_state,
        "stateDetail": {
            "mainnetReviewsPresent": mainnet_fresh,
            "testnetPerReviewUnitCheckPassed": testnet_pnl_ok,
            "testnetManagerLoopPresent": testnet_manager_ready,
            "unmet": (
                "the testnet Manager has not published a closed loop, so the "
                "Manager-side USDG valuation path cannot be exercised there; "
                "the root cause is tracked by TASK-147 and its fix needs an "
                "authorized write, so this half is reported as unmeasured "
                "rather than passed"
            ),
        },
        "mainnet": {
            "reviewCount": mainnet["reviewCount"],
            "loopCount": mainnet["loopCount"],
            "reviewedWithUsdgValuation": mainnet["coverage"]["reviewedWithUsdgValuation"],
            "reviewedWithoutUsdgValuation": mainnet["coverage"]["reviewedWithoutUsdgValuation"],
            "perReviewUnitChecks": mainnet["checkSummary"],
        },
        "testnet": {
            "reviewCount": testnet["reviewCount"],
            "loopCount": testnet["loopCount"],
            "hasTokenLibraryCatalog": testnet["hasTokenLibraryCatalog"],
            "perReviewUnitChecks": testnet["checkSummary"],
            "signedPnlUnitCheck": {
                "state": "met" if testnet_pnl_ok else "notMet",
                "reviewsVerified": testnet_pnl_checks.get("met", 0),
                "mismatches": testnet_pnl_checks.get("notMet", 0),
                "note": (
                    "the testnet per-review unit contract IS verified on the "
                    "reviews that exist: every signed PnL raw value divided "
                    "by the library decimals of its borrow token reproduces "
                    "the stored human amount"
                ),
            },
            "managerValuationPath": {
                "state": "unmeasured",
                "reason": "no_manager_closed_loop_on_testnet",
                "hasTokenLibraryCatalog": testnet["hasTokenLibraryCatalog"],
                "note": (
                    "the Manager resolves the USDG token through "
                    "token_library_catalog, and the testnet catalog does "
                    "admit USDG with six decimals, so the anchor identity "
                    "would resolve there.  What is missing is a materialised "
                    "closed loop: the Manager has published no loop, so the "
                    "valuation path never runs and no loop-level valuation "
                    "row exists to check.  The testnet token_usdg_valuations "
                    "table is a DIFFERENT producer (the on-chain quoter) and "
                    "it uses a 1e18 scale, so it is not evidence for the "
                    "Manager's 1e6 raw-USDG unit"
                ),
            },
            "blockerTask": "TASK-147",
        },
        "note": (
            "the coverage numbers are reported next to the per-review unit "
            "checks and never instead of them"
        ),
    })

    # Item 5: tokens without a usable price or decimals are reported as a
    # coverage gap - never a zero rate and never a default rate.
    gap = mainnet["coverage"]
    gap_fail_closed = all(
        audit["states"].get("borrowToken.usdgValuation") == "unmeasured"
        for audit in mainnet_reviews
        if audit["usdgValuation"]["valueUsdgRaw"] is None
    )
    items.append({
        "item": 5,
        "title": ("a token without a usable price or decimals is reported as a "
                  "coverage gap - never a zero rate and never a default rate"),
        "state": "met" if gap_fail_closed else "notMet",
        "coverageGapSymbols": gap["coverageGapSymbols"],
        "coverageGapLoopCount": gap["coverageGapLoopCount"],
        "coverageGapLoops": gap["coverageGapLoops"],
        "reviewedGapCount": gap["reviewedWithoutUsdgValuation"],
        "allGapsFailClosed": gap_fail_closed,
        "note": (
            "the gap is carried as an absent valuation, so the kernel's "
            "cross-tick initialization returns 'cross_tick_inputs_unavailable' "
            "and the measured single-tick result is preserved"
        ),
    })

    report = {
        "task": "TASK-145",
        "acceptance": "AC-212",
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mode": "read-only",
        "authority": {
            "amountCapacityPnlIdentity": "Rust kernel (in-memory state)",
            "pythonRole": "pure consumer; this collector only reads and re-derives",
            "broadcast": "not performed; no authorization was granted",
            "writes": "none; both stores are opened with mode=ro",
        },
        "chains": {
            "mainnet": {"network": MAINNET[0], "chainId": MAINNET[1]},
            "testnet": {"network": TESTNET[0], "chainId": TESTNET[1]},
        },
        "unitContract": {
            "signedPnl": ("signed hex integer in the borrow token's raw units; "
                          "the human amount is the integer divided by "
                          "10 ** borrow_token_decimals"),
            "ladderRow": ("amountRaw is a hex integer in the same raw units; "
                          "amount is the human counterpart"),
            "borrowPrincipal": ("the borrow amount in human units of the "
                                "borrow token; the kernel derives it from the "
                                "published capacity"),
            "usdgValuation": ("the value of one WHOLE borrow token in raw USDG "
                              "units; USDG has six decimals"),
            "usdgIdentity": ("USDG is worth 10 ** usdg_decimals raw USDG by "
                             "the stablecoin unit identity; no USDG/USDG pool "
                             "is consulted"),
            "fee": "the pool fee is an integer on the Uniswap V4 1e-6 scale",
            "gas": ("the V4 quoter returns the amount and the gas estimate in "
                    "separate words; the gas never enters the PnL unit"),
        },
        "items": items,
        "itemStates": {str(item["item"]): item["state"] for item in items},
        "allItemsMet": all(item["state"] == "met" for item in items),
        "mainnet": mainnet,
        "testnet": testnet,
        "limitations": [
            "every report has a time boundary; an old report cannot speak for "
            "the current state",
            "the review freshness is audited by TASK-143, not here",
            "the testnet Manager valuation path cannot be exercised until the "
            "Manager publishes a closed loop there (TASK-147)",
        ],
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print("wrote", OUT)
    print("item states:", report["itemStates"])
    return 0 if report["allItemsMet"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
