from tools.import_bnb_venus_borrowers_from_dune import (
    build_dune_borrow_logs_sql,
    build_dune_borrower_accounts_sql,
    dune_account_rows_to_registry,
    dune_rows_to_venus_events,
)


def test_build_dune_borrow_logs_sql_targets_bnb_logs_and_borrow_topic():
    sql = build_dune_borrow_logs_sql(
        ["0xC4eF4229FEc74Ccfe17B2bdeF7715fAC740BA0ba"],
        lookback_days=60,
        row_limit=100,
    )

    assert "FROM bnb.logs" in sql
    assert "contract_address IN (0xc4ef4229fec74ccfe17b2bdef7715fac740ba0ba)" in sql
    assert "topic0 = 0x13ed6866d4e1ee6da46f845c46d7e54120883d75c5ea9a2dacc1c4ca8984ab80" in sql
    assert "INTERVAL '60' DAY" in sql
    assert "LIMIT 100" in sql


def test_build_dune_borrower_accounts_sql_groups_distinct_borrowers():
    sql = build_dune_borrower_accounts_sql(
        ["0xC4eF4229FEc74Ccfe17B2bdeF7715fAC740BA0ba"],
        lookback_days=60,
        row_limit=100,
    )

    assert "varbinary_substring(data, 13, 20) AS account" in sql
    assert "COUNT(*) AS borrow_count" in sql
    assert "GROUP BY 1" in sql
    assert "tx_hash" not in sql
    assert "data" not in sql.split("FROM bnb.logs", 1)[0].replace("varbinary_substring(data, 13, 20)", "")


def test_dune_account_rows_to_registry_builds_account_only_registry():
    registry = dune_account_rows_to_registry(
        [
            {
                "account": "0x9f47800b40f2663e25e820e491b0106e904b06aa",
                "borrow_count": 2,
                "first_block": 112304123,
                "last_block": 116342737,
                "borrow_market_count": 1,
            }
        ]
    )

    assert registry["event_storage_mode"] == "account_only"
    assert registry["account_count"] == 1
    assert registry["accounts"][0]["event_count"] == 2


def test_dune_rows_to_venus_events_decodes_borrower_from_data():
    rows = [
        {
            "contract_address": "0xc4ef4229fec74ccfe17b2bdef7715fac740ba0ba",
            "block_number": 116342737,
            "tx_hash": "0x1dc4",
            "log_index": 125,
            "block_time": "2026-08-16 21:15:56.000 UTC",
            "data": "0x0000000000000000000000009f47800b40f2663e25e820e491b0106e904b06aa"
            "0000000000000000000000000000000000000000000000000de0b6b3a7640000",
        }
    ]

    events = dune_rows_to_venus_events(rows)

    assert len(events) == 1
    assert events[0]["account"].lower() == "0x9f47800b40f2663e25e820e491b0106e904b06aa"
    assert events[0]["event"] == "Borrow"
    assert events[0]["source"] == "dune_bnb_logs"
