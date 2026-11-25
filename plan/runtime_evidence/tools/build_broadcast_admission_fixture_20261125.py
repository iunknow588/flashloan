#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the ``T-广.01`` broadcast-admission fixture store.

``T-广.01`` asks for the four refusal paths to be **really triggered and
evidenced**:

1. ``profit <= 0``            -> ``BC-1.pnl_positive``
2. profit or amount unknown   -> ``BC-1.pnl_measured`` / ``BC-1.price_limits_complete``
3. the state is stale         -> ``BC-1.review_fresh``
4. the guardrail does not match -> ``BC-2.*``

The live store carries **no** broadcast request rows (no review has ever
reached ``completed`` with a positive, executable profit), so the live
endpoint can only ever answer ``UnknownRequest``.  Triggering the four
paths therefore needs a **fixture** store that carries one request row per
path, crafted so that exactly one rule fails on each.

The fixture is a **separate database file**.  It is created from the real
``manager-db`` migrations, so the schema is the production schema, and it
is driven through the **real** ``manager-api`` binary over HTTP -- the
admission code that refuses is the shipping code.

Usage:
    python build_broadcast_admission_fixture_20261125.py <fixture.sqlite3>
"""

import datetime
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"
SCOPE = "core4"

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
MANAGER_DB_BIN = os.path.join(
    REPO_ROOT, "flashloan", "tools", "target", "release", "manager-db.exe"
)

ANCHOR = "0x1111111111111111111111111111111111111111"
SIGNER = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OTHER_SIGNER = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _stamp(delta_seconds=0.0):
    moment = _now() + datetime.timedelta(seconds=delta_seconds)
    return moment.isoformat().replace("+00:00", "+00:00"), moment


def migrate(path):
    """Create the schema with the **real** manager-db migrations."""
    if not os.path.isfile(MANAGER_DB_BIN):
        raise SystemExit(
            "manager-db binary not found at %s -- run: cargo build --offline -p manager-db --release"
            % MANAGER_DB_BIN
        )
    completed = subprocess.run(
        [MANAGER_DB_BIN, "migrate", "--db", path],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise SystemExit("manager-db migrate failed:\n%s\n%s" % (completed.stdout, completed.stderr))
    return json.loads(completed.stdout or "{}")


def seed(path):
    import sqlite3

    connection = sqlite3.connect(path)
    cursor = connection.cursor()

    # ---- one request + review pair per refusal path --------------------
    #
    # ``cases`` is the contract with the driver: each entry names the rule
    # the admission decision is expected to refuse on, and the fields that
    # make exactly that rule fail.
    cases = []

    fresh_at, _ = _stamp(-5.0)
    stale_at, _ = _stamp(-7200.0)

    cases.append(
        {
            "key": "profit-zero",
            "expectedRule": "BC-1.pnl_positive",
            "reviewStatus": "completed",
            "runArbitrage": {"ok": True, "executable": True},
            "reviewedAt": fresh_at,
            "requestPnlAmount": 0.0,
            "reviewPnlAmount": 0.0,
            "priceLimits": ["100", "200", "300"],
            "signer": SIGNER,
            "amount": 1000,
            "note": "a measured zero profit is a business outcome, not a measurement gap",
        }
    )
    cases.append(
        {
            "key": "profit-negative",
            "expectedRule": "BC-1.pnl_positive",
            "reviewStatus": "completed",
            "runArbitrage": {"ok": True, "executable": True},
            "reviewedAt": fresh_at,
            "requestPnlAmount": -1.25,
            "reviewPnlAmount": -1.25,
            "priceLimits": ["100", "200", "300"],
            "signer": SIGNER,
            "amount": 1000,
            "note": "a measured negative profit",
        }
    )
    cases.append(
        {
            "key": "profit-unmeasured",
            "expectedRule": "BC-1.pnl_measured",
            "reviewStatus": "completed",
            "runArbitrage": {"ok": True, "executable": True},
            "reviewedAt": fresh_at,
            # A non-finite number is what a producer writes when it had no
            # figure to report; SQLite keeps it as a REAL.
            "requestPnlAmount": float("inf"),
            "reviewPnlAmount": float("inf"),
            "priceLimits": ["100", "200", "300"],
            "signer": SIGNER,
            "amount": 1000,
            "note": "the profit was never measured -- it must not read as zero",
        }
    )
    cases.append(
        {
            "key": "price-limits-missing",
            "expectedRule": "BC-1.price_limits_complete",
            "reviewStatus": "completed",
            "runArbitrage": {"ok": True, "executable": True},
            "reviewedAt": fresh_at,
            "requestPnlAmount": 12.5,
            "reviewPnlAmount": 12.5,
            "priceLimits": [],
            "signer": SIGNER,
            "amount": 1000,
            "note": "the per-hop price limits are missing",
        }
    )
    cases.append(
        {
            "key": "review-stale",
            "expectedRule": "BC-1.review_fresh",
            "reviewStatus": "completed",
            "runArbitrage": {"ok": True, "executable": True},
            "reviewedAt": stale_at,
            "requestPnlAmount": 12.5,
            "reviewPnlAmount": 12.5,
            "priceLimits": ["100", "200", "300"],
            "signer": SIGNER,
            "amount": 1000,
            "note": "the review is two hours old -- past the 120 second gate",
        }
    )
    cases.append(
        {
            "key": "guard-account-mismatch",
            "expectedRule": "BC-2.account_matches",
            "reviewStatus": "completed",
            "runArbitrage": {"ok": True, "executable": True},
            "reviewedAt": fresh_at,
            "requestPnlAmount": 12.5,
            "reviewPnlAmount": 12.5,
            "priceLimits": ["100", "200", "300"],
            "signer": OTHER_SIGNER,
            "amount": 1000,
            "note": "the attempt names a signer the guardrail does not allow",
        }
    )
    cases.append(
        {
            "key": "guard-amount-over-limit",
            "expectedRule": "BC-2.amount_within_limit",
            "reviewStatus": "completed",
            "runArbitrage": {"ok": True, "executable": True},
            "reviewedAt": fresh_at,
            "requestPnlAmount": 12.5,
            "reviewPnlAmount": 12.5,
            "priceLimits": ["100", "200", "300"],
            "signer": SIGNER,
            "amount": 1000000000000,
            "note": "the amount is far above the configured ceiling",
        }
    )
    cases.append(
        {
            "key": "positive-control",
            "expectedRule": None,
            "reviewStatus": "completed",
            "runArbitrage": {"ok": True, "executable": True},
            "reviewedAt": fresh_at,
            "requestPnlAmount": 12.5,
            "reviewPnlAmount": 12.5,
            "priceLimits": ["100", "200", "300"],
            "signer": SIGNER,
            "amount": 1000,
            "note": "every rule holds -- the positive control",
        }
    )

    for case in cases:
        loop_key = "fixture:%s" % case["key"]
        cursor.execute(
            """INSERT INTO manager_v4_review_broadcast_requests (
                network, chain_id, protocol, scope_key, loop_key,
                closed_loop_version, closed_loop_checksum,
                anchor_token_key_id, path_text, path_addresses_json,
                path_symbols_json, pool_path_key, pool_ids_json,
                signed_pnl_raw, signed_pnl_amount, signed_pnl_currency,
                signed_pnl_address, signed_pnl_unit, signed_pnl_source,
                request_status, request_reason,
                hop_sqrt_price_limits_json, created_at, updated_at
             ) VALUES (?, ?, ?, ?, ?, 7, 'sha256:closed-loop', ?, ?,
                       '[]', '[]', 'pool-path:1', '["pool-a","pool-yz","pool-d"]',
                       '12.5 USDG', ?, 'USDG', ?, 'token', 'runArbitrage',
                       'generated', 'fixture', ?, 'fixture', 'fixture')""",
            (
                NETWORK,
                CHAIN_ID,
                PROTOCOL,
                SCOPE,
                loop_key,
                "robinhood:4663:%s" % ANCHOR,
                "USDG -> AMZN -> TA -> USDG",
                case["requestPnlAmount"],
                ANCHOR,
                json.dumps(case["priceLimits"]),
            ),
        )
        cursor.execute(
            """INSERT INTO manager_v4_reviews (
                network, chain_id, protocol, scope_key, loop_key,
                closed_loop_version, closed_loop_checksum,
                candidate_edge_version, candidate_edge_checksum,
                observation_version, observation_checksum,
                pool_scope_checksum, anchor_token_key_id, anchor_address,
                anchor_symbol, path_text, path_addresses_json,
                path_symbols_json, pool_path_key, pool_ids_json,
                review_status, review_reason, static_preview_json,
                run_arbitrage_json, signed_pnl_raw, signed_pnl_amount,
                signed_pnl_currency, signed_pnl_address, signed_pnl_unit,
                signed_pnl_source, reviewed_at, created_at, updated_at
             ) VALUES (?, ?, ?, ?, ?, 7, 'sha256:closed-loop', 5,
                       'sha256:candidate-edge', 3, 'sha256:observation',
                       'sha256:pool-scope', ?, ?, 'USDG', ?,
                       '[]', '[]', 'pool-path:1', '[]', ?, 'fixture', '{}', ?,
                       '12.5 USDG', ?, 'USDG', ?, 'token', 'runArbitrage',
                       ?, 'fixture', 'fixture')""",
            (
                NETWORK,
                CHAIN_ID,
                PROTOCOL,
                SCOPE,
                loop_key,
                "robinhood:4663:%s" % ANCHOR,
                ANCHOR,
                "USDG -> AMZN -> TA -> USDG",
                case["reviewStatus"],
                json.dumps(case["runArbitrage"]),
                case["reviewPnlAmount"],
                ANCHOR,
                case["reviewedAt"],
            ),
        )

    cursor.execute(
        """INSERT INTO manager_review_library_state (
            network, chain_id, protocol, scope_key, review_version,
            materialized_checksum, closed_loop_version,
            closed_loop_checksum, candidate_edge_version,
            candidate_edge_checksum, observation_version,
            observation_checksum, pool_scope_checksum,
            library_versions_json, library_checksums_json, loop_count,
            reviewed_count, completed_count, data_incomplete_count,
            not_executable_count, failed_count, broadcast_request_count,
            generated_at, updated_at
         ) VALUES (?, ?, 'uniswap_v4', 'core4', 9, 'sha256:review',
                   7, 'sha256:closed-loop', 5, 'sha256:candidate-edge',
                   3, 'sha256:observation', 'sha256:pool-scope',
                   '{"core":3}', '{"core":"sha256:core"}',
                    ?, ?, ?, 0, 0, 0, ?, 'fixture', 'fixture')""",
        (
            NETWORK,
            CHAIN_ID,
            len(cases),
            len(cases),
            len(cases),
                   len(cases),
        ),
    )
    connection.commit()
    connection.close()
    return cases


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = os.path.abspath(sys.argv[1])
    if os.path.exists(path):
        os.remove(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    report = migrate(path)
    cases = seed(path)
    print("fixture:", path)
    applied = report.get("applied")
    print("migrations applied:", len(applied) if isinstance(applied, list) else applied)
    for case in cases:
        print("  %-28s expected=%s" % (case["key"], case["expectedRule"] or "(admitted)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
