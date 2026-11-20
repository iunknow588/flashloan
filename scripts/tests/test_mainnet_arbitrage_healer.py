import datetime as dt
import importlib.util
import json
import socket
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "mainnet_arbitrage_healer.py"
SPEC = importlib.util.spec_from_file_location("mainnet_arbitrage_healer", MODULE_PATH)
HEALER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(HEALER)


class MainnetArbitrageHealerTests(unittest.TestCase):
    def test_token_audit_requires_exact_funding_addresses_and_stock_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tokens.sqlite3"
            connection = sqlite3.connect(database)
            for table in ("token_core", "token_stocks", "token_test"):
                connection.execute(
                    f"CREATE TABLE {table} (network TEXT,chain_id INTEGER,token_symbol TEXT,"
                    "token_address TEXT,token_decimals INTEGER,admission_status TEXT,"
                    "metadata_source TEXT,admission_source TEXT,source_json TEXT)"
                )
            connection.execute(
                "CREATE TABLE chain_tokens (network TEXT,chain_id INTEGER,token_symbol TEXT,"
                "token_address TEXT,token_decimals INTEGER)"
            )
            # The audit reads the funding addresses from the chain's own
            # token allowlist, so the fixture has to use the same addresses.
            official = HEALER.official_funding_token_addresses(4663)
            for symbol in HEALER.FUNDING_TOKEN_SYMBOLS:
                address = official[symbol]
                table = "token_test" if symbol == "TZ" else "token_core"
                connection.execute(
                    f"INSERT INTO {table} VALUES (?,?,?,?,?,'admitted','','','')",
                    ("robinhood", 4663, symbol, address, 18),
                )
            # The stock provenance is checked against the chain's own stock
            # registry, so the fixture uses a symbol the registry declares.
            registry = HEALER.stock_registry_provenance(4663)
            stock_symbol = sorted(registry)[0]
            stock_entry = registry[stock_symbol]
            connection.execute(
                "INSERT INTO token_stocks VALUES (?,?,?,?,?,'admitted',?,?,?)",
                ("robinhood", 4663, stock_symbol, stock_entry["address"], 18,
                 stock_entry["metadataSource"], "bootstrap:stock_registry",
                 '{"seed":"robinhood_stock_token_registry"}'),
            )
            connection.commit()
            connection.close()

            report = HEALER.audit_tokens(database)
            self.assertTrue(report["valid"], report)
            self.assertEqual(report["fundingAddressMismatches"], [])
            self.assertEqual(report["counterfeitFundingAdmissions"], [])
            self.assertEqual(report["stockProvenanceInvalid"], [])

            connection = sqlite3.connect(database)
            connection.execute(
                "UPDATE token_core SET token_address=? WHERE token_symbol='USDG'",
                ("0x03f5c4ff794618a20add6cab0383d559782fe1d8",),
            )
            connection.commit()
            connection.close()
            report = HEALER.audit_tokens(database)
            self.assertFalse(report["valid"])
            self.assertEqual(report["fundingAddressMismatches"][0]["symbol"], "USDG")
            self.assertEqual(len(report["counterfeitFundingAdmissions"]), 1)

    def test_stock_provenance_is_checked_against_the_chain_own_registry(self):
        """The provenance rule has to hold on every chain.

        The metadata source of a stock token is chain specific: mainnet
        stocks are valued from the assets API while testnet stocks carry
        on-chain V4 evidence. An audit that hardcoded one chain's metadata
        source would report the other chain's valid stocks as broken, so the
        audit has to read the expected value from the chain's own registry.
        """
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tokens.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE token_stocks (network TEXT,chain_id INTEGER,token_symbol TEXT,"
                "token_address TEXT,token_decimals INTEGER,admission_status TEXT,"
                "metadata_source TEXT,admission_source TEXT,source_json TEXT)"
            )
            connection.execute(
                "CREATE TABLE token_core (network TEXT,chain_id INTEGER,token_symbol TEXT,"
                "token_address TEXT,token_decimals INTEGER,admission_status TEXT,"
                "metadata_source TEXT,admission_source TEXT,source_json TEXT)"
            )
            registry = HEALER.stock_registry_provenance(46630)
            self.assertTrue(registry, "the testnet stock registry has to be readable")
            for symbol, entry in registry.items():
                connection.execute(
                    "INSERT INTO token_stocks VALUES (?,?,?,?,?,'admitted',?,?,?)",
                    ("robinhood-testnet", 46630, symbol, entry["address"], 18,
                     entry["metadataSource"], "bootstrap:stock_registry",
                     '{"seed":"robinhood_stock_token_registry"}'),
                )
            connection.commit()
            connection.close()

            with mock.patch.object(HEALER, "CHAIN_ID", 46630), \
                    mock.patch.object(HEALER, "NETWORK", "robinhood-testnet"):
                invalid = HEALER.audit_stock_provenance(database)
            self.assertEqual(invalid, [], invalid)

            # The mainnet metadata source is not valid on the testnet, so a
            # row that claims it has to be reported as broken.
            connection = sqlite3.connect(database)
            connection.execute(
                "UPDATE token_stocks SET metadata_source='robinhood_assets_api'")
            connection.commit()
            connection.close()
            with mock.patch.object(HEALER, "CHAIN_ID", 46630), \
                    mock.patch.object(HEALER, "NETWORK", "robinhood-testnet"):
                invalid = HEALER.audit_stock_provenance(database)
            self.assertEqual(len(invalid), len(registry))
            for item in invalid:
                self.assertIn("registry_metadata_source_mismatch", item["reasons"])

    def test_api_post_treats_connection_reset_as_transport_failure(self):
        with mock.patch.object(
            HEALER.urllib.request,
            "urlopen",
            side_effect=ConnectionResetError(10054, "connection reset"),
        ):
            result = HEALER.api_post("http://127.0.0.1:5000", "/review", {})
        self.assertFalse(result["ok"])
        self.assertIsNone(result["status"])
        self.assertIn("connection reset", result["body"])

    def review_row(self, amount=1.25, category="executable", loop_key="0xa:0xb:0xc"):
        # ``loop_key`` is the identity the closed-loop Manager published and
        # the kernel wrote into the review payload. It is consumed, never
        # derived from the route addresses (MIO-7 / TASK-149 / AC-214).
        payload = {"route_addresses": ["0xA", "0xB", "0xC", "0xA"]}
        if loop_key is not None:
            payload["loopKey"] = loop_key
        return {
            "id": 10,
            "attempt_key": "attempt-10",
            "category": category,
            "pair": "SPY/ETH",
            "created_at": "2026-11-10T00:00:00Z",
            "selected_profit_raw": "125",
            "selected_profit_amount": amount,
            "selected_profit_amount_symbol": "SPY",
            "selected_profit_source": "native_rust_contract_preview",
            "selected_profit_missing_reason": None,
            "payload_json": json.dumps(payload),
        }

    def test_review_evidence_preserves_direction_and_positive_pnl(self):
        evidence = HEALER.review_evidence(self.review_row())
        self.assertEqual(evidence["loopKey"], "0xa:0xb:0xc")
        self.assertEqual(evidence["runArbitrage"]["signedPnl"]["amount"], 1.25)
        self.assertFalse(evidence["runArbitrage"]["executable"])
        self.assertTrue(evidence["staticPreview"]["historicalEvidence"])
        self.assertFalse(evidence["staticPreview"]["automaticBroadcastEligible"])
        self.assertEqual(HEALER.review_loop_key(self.review_row()), "0xa:0xb:0xc")

    def test_a_review_without_a_published_identity_is_not_associated(self):
        """TASK-149: the route addresses never become the identity.

        A review that carries no published ``loop_key`` has no closed-loop
        identity. The healer used to assemble one out of the route
        addresses, which could name a loop the Manager never published and
        therefore borrow its capacity and pool set.
        """
        row = self.review_row(loop_key=None)

        self.assertIsNone(HEALER.review_loop_key(row))

        evidence = HEALER.review_evidence(row)
        self.assertEqual(evidence["loopKey"], "missing-route:10")
        # The measurement itself is preserved; only the identity is absent.
        self.assertEqual(evidence["runArbitrage"]["signedPnl"]["amount"], 1.25)

    def test_missing_pnl_is_not_replaced_with_zero(self):
        row = self.review_row(amount=None, category="not_executable")
        row["selected_profit_raw"] = None
        row["selected_profit_missing_reason"] = "eth_call RPC request failed"
        evidence = HEALER.review_evidence(row)
        self.assertIsNone(evidence["runArbitrage"]["signedPnl"])
        self.assertFalse(evidence["runArbitrage"]["ok"])
        self.assertEqual(
            evidence["runArbitrage"]["diagnostics"][0]["diagnosticType"],
            "rpc_unavailable",
        )
        self.assertEqual(evidence["diagnostics"], [])

    def test_pnl_stats_count_missing_separately(self):
        valued = self.review_row(amount=2.0)
        missing = self.review_row(amount=None)
        missing["selected_profit_raw"] = None
        stats = HEALER.pnl_stats([valued, missing])
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["withValue"], 1)
        self.assertEqual(stats["missingValue"], 1)
        self.assertEqual(stats["sum"], 2.0)

    def test_positive_estimate_is_not_counted_as_executable_profit(self):
        estimated = self.review_row(amount=2.0, category="not_executable")
        stats = HEALER.pnl_stats([estimated])
        self.assertEqual(stats["positiveAmount"], 1)
        self.assertEqual(stats["positive"], 0)

    def test_missing_pnl_classification_marks_rpc_retryable(self):
        self.assertEqual(
            HEALER.missing_pnl_classification("eth_call RPC request failed"),
            ("rpc_unavailable", True),
        )
        self.assertEqual(
            HEALER.missing_pnl_classification("NoProfitableRoute"),
            ("quote_failed", False),
        )
        self.assertEqual(
            HEALER.diag_type("pool_key_currencies_unavailable"),
            "quote_failed",
        )

    def test_runtime_environment_forces_robinhood_mainnet_v4_profile(self):
        environment = HEALER.runtime_environment(5007)
        self.assertEqual(environment["FLASHLOAN_RUNTIME_PROFILE"], "v4_dex")
        self.assertEqual(environment["DEX_ACTIVE_CHAIN_ID"], "4663")
        self.assertEqual(environment["DEX_ACTIVE_NETWORK"], "robinhood")
        self.assertEqual(environment["PORT"], "5007")

    def test_healing_actions_stop_at_first_invalid_upstream_layer(self):
        report = {
            "layers": {
                "tokens": {"valid": True},
                "pools": {"valid": False},
                "closedLoops": {"valid": False},
                "reviews": {"valid": False, "legacy": {"stats": {"missingValue": 4}}},
            }
        }
        self.assertEqual(HEALER.healing_actions(report), ["repair_pools"])

    def test_healing_actions_never_request_direct_broadcast(self):
        report = {
            "layers": {
                "tokens": {"valid": True},
                "pools": {"valid": True},
                "closedLoops": {"valid": True},
                "reviews": {"valid": True, "legacy": {"stats": {"missingValue": 0}}},
            }
        }
        actions = HEALER.healing_actions(report)
        self.assertIn("fill_current_loop_reviews", actions)
        self.assertIn("rereview_historical_positive_candidates", actions)
        self.assertIn("observe_automatic_broadcast", actions)
        self.assertNotIn("broadcast", actions)

    def test_dry_run_reports_worker_refresh_without_mutating_runtime(self):
        candidate = {
            "routeSymbols": ["ETH", "WETH", "SPCX", "ETH"],
            "routeAddresses": ["0x0", "0x1", "0x2", "0x0"],
        }
        missing = {"ok": False, "status": 503, "body": "closed_loop_group_worker_not_running:eth|weth|spcx"}
        with mock.patch.object(HEALER, "api_post", return_value=missing) as api_post:
            result = HEALER.request_live_review(
                Path("unused.sqlite3"), "http://127.0.0.1:5000", candidate, False
            )
        self.assertTrue(result["workerRefreshRequired"])
        self.assertFalse(result["workerRefreshApplied"])
        self.assertEqual(api_post.call_count, 1)

    def test_apply_refreshes_missing_worker_then_retries_live_review(self):
        candidate = {
            "routeSymbols": ["ETH", "WETH", "SPCX", "ETH"],
            "routeAddresses": ["0x0", "0x1", "0x2", "0x0"],
        }
        missing = {"ok": False, "status": 503, "body": "closed_loop_group_worker_not_running:eth|weth|spcx"}
        refreshed = {"ok": True, "status": 200, "body": '{"accepted":true}'}
        with mock.patch.object(HEALER, "api_post", side_effect=[missing, refreshed]) as api_post:
            result = HEALER.request_live_review(
                Path("unused.sqlite3"), "http://127.0.0.1:5000", candidate, True
            )
        self.assertTrue(result["workerRefreshApplied"])
        self.assertTrue(result["reviewTriggeredByRefresh"])
        self.assertTrue(result["finalReview"]["ok"])
        self.assertIn("eth%7Cweth%7Cspcx", api_post.call_args_list[1].args[1])
        self.assertEqual(api_post.call_count, 2)

    def test_http_200_with_application_error_is_not_success(self):
        response = {"ok": True, "status": 200, "body": '{"ok":false,"error":"pool_manager_refresh_required"}'}
        self.assertFalse(HEALER.response_succeeded(response))

    def test_pool_repair_keys_extract_missing_candidate_edge(self):
        key_id = "0xaaa:0xbbb"
        report = {
            "results": [{
                "liveReviewRequest": {
                    "workerRefresh": {
                        "ok": True,
                        "body": '{"ok":false,"detail":{"missingEdges":[{"keyId":"' + key_id + '"}]}}',
                    }
                }
            }]
        }
        self.assertEqual(HEALER.pool_repair_keys(report), [key_id])

    def test_fill_reviews_forwards_session_exclusions(self):
        excluded = {"loop-a"}
        with mock.patch.object(HEALER, "missing_review_candidates", return_value=[]) as candidates:
            result = HEALER.fill_missing_reviews(
                HEALER.DEFAULT_DB,
                "http://127.0.0.1:5000",
                True,
                1,
                excluded,
            )
        candidates.assert_called_once_with(HEALER.DEFAULT_DB, 1, excluded)
        self.assertEqual(result["candidateCount"], 0)

    def test_missing_pnl_candidates_only_include_latest_missing_review(self):
        missing = self.review_row(amount=None, category="not_executable")
        missing["selected_profit_raw"] = None
        missing["selected_profit_missing_reason"] = "route invalid"
        valued = self.review_row(
            amount=-1.0, category="not_executable", loop_key="0xd:0xe:0xf")
        loops = [
            {"loop_key": "0xa:0xb:0xc", "path_text": "A -> B -> C -> A",
             "path_symbols_json": '["A","B","C","A"]',
             "path_addresses_json": '["0xA","0xB","0xC","0xA"]'},
            {"loop_key": "0xd:0xe:0xf", "path_text": "D -> E -> F -> D",
             "path_symbols_json": '["D","E","F","D"]',
             "path_addresses_json": '["0xD","0xE","0xF","0xD"]'},
        ]
        with mock.patch.object(HEALER, "review_rows", return_value=[missing, valued]), \
             mock.patch.object(HEALER, "all_rows", return_value=loops):
            candidates = HEALER.missing_pnl_review_candidates(HEALER.DEFAULT_DB, 24, 5)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["loopKey"], "0xa:0xb:0xc")
        self.assertEqual(candidates[0]["historicalReviewId"], 10)

    def test_rereview_missing_pnl_never_requests_manual_broadcast(self):
        candidate = {"loopKey": "0xa:0xb:0xc", "path": "A -> B -> C -> A",
                     "routeSymbols": ["A", "B", "C", "A"],
                     "routeAddresses": ["0xA", "0xB", "0xC", "0xA"]}
        with mock.patch.object(HEALER, "missing_pnl_review_candidates", return_value=[candidate]), \
             mock.patch.object(HEALER, "request_live_review", return_value={"ok": True}) as request:
            result = HEALER.rereview_missing_pnl(
                HEALER.DEFAULT_DB, "http://127.0.0.1:5000", True, 24, 1)
        self.assertEqual(result["candidateCount"], 1)
        self.assertFalse(result["results"][0]["broadcastRequestedByHealer"])
        request.assert_called_once()

    def test_production_status_distinguishes_market_idle_from_review_stall(self):
        layers = {
            "tokens": {"valid": True},
            "pools": {"valid": True},
            "closedLoops": {"valid": True},
            "reviews": {"legacy": {"latestReviewAt": "2026-11-10T11:59:30Z", "stats": {"positive": 0}}},
            "broadcast": {"legacy": {"total": 0, "latestAttemptAt": None}},
        }
        status = HEALER.transaction_production_status(layers, "2026-11-10T12:00:00Z")
        self.assertEqual(status["state"], "market_no_executable_profit")
        self.assertTrue(status["healthy"])

        layers["reviews"]["legacy"]["latestReviewAt"] = "2026-11-10T11:00:00Z"
        status = HEALER.transaction_production_status(layers, "2026-11-10T12:00:00Z")
        self.assertEqual(status["state"], "review_stalled")
        self.assertFalse(status["healthy"])

    def test_production_status_flags_positive_review_without_transaction_record(self):
        layers = {
            "tokens": {"valid": True},
            "pools": {"valid": True},
            "closedLoops": {"valid": True},
            "reviews": {"legacy": {"latestReviewAt": "2026-11-10T11:59:30Z", "stats": {"positive": 1}}},
            "broadcast": {"legacy": {"total": 0, "latestAttemptAt": None}},
        }
        status = HEALER.transaction_production_status(layers, "2026-11-10T12:00:00Z")
        self.assertEqual(status["state"], "profitable_review_waiting_for_broadcast")
        self.assertFalse(status["healthy"])

    def test_receipt_realized_token_delta_uses_executor_transfer_net(self):
        token = "0x" + "1" * 40
        executor = "0x" + "2" * 40
        pool = "0x" + "3" * 40
        summary = {
            "route_addresses": [token, pool, token, token],
            "auto_execute": {
                "result": {
                    "receipt": {
                        "receipt": {
                            "to": executor,
                            "logs": [
                                {
                                    "address": token,
                                    "topics": [
                                        HEALER.ERC20_TRANSFER_TOPIC,
                                        "0x" + "0" * 24 + pool[2:],
                                        "0x" + "0" * 24 + executor[2:],
                                    ],
                                    "data": hex(500),
                                },
                                {
                                    "address": token,
                                    "topics": [
                                        HEALER.ERC20_TRANSFER_TOPIC,
                                        "0x" + "0" * 24 + executor[2:],
                                        "0x" + "0" * 24 + pool[2:],
                                    ],
                                    "data": hex(400),
                                },
                            ],
                        }
                    }
                }
            },
        }
        with mock.patch.object(HEALER, "token_decimals", return_value=2):
            amount, reason = HEALER.receipt_realized_token_delta(
                HEALER.DEFAULT_DB,
                summary,
                "TEST",
            )
        self.assertEqual(amount, 1.0)
        self.assertIsNone(reason)

    def test_historical_positive_rereview_includes_not_executable_estimate(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reviews.sqlite3"
            con = sqlite3.connect(database)
            try:
                con.execute(
                    "CREATE TABLE manager_v4_closed_loops (network TEXT, chain_id INTEGER, "
                    "loop_key TEXT, path_text TEXT, path_symbols_json TEXT, path_addresses_json TEXT)"
                )
                con.execute(
                    "CREATE TABLE cow_execution_reviews (id INTEGER, network TEXT, chain_id INTEGER, "
                    "attempt_key TEXT, pair TEXT, tx_hash TEXT, created_at TEXT, category TEXT, "
                    "payload_json TEXT, selected_profit_raw TEXT, selected_profit_amount REAL, "
                    "selected_profit_amount_symbol TEXT, selected_profit_source TEXT, "
                    "selected_profit_missing_reason TEXT)"
                )
                con.execute(
                    "INSERT INTO manager_v4_closed_loops VALUES (?,?,?,?,?,?)",
                    (
                        HEALER.NETWORK,
                        HEALER.CHAIN_ID,
                        "0xa:0xb:0xc",
                        "SPY -> NVDA -> USDG -> SPY",
                        json.dumps(["SPY", "NVDA", "USDG", "SPY"]),
                        json.dumps(["0xa", "0xb", "0xc", "0xa"]),
                    ),
                )
                con.execute(
                    "INSERT INTO cow_execution_reviews "
                    "(id, network, chain_id, created_at, category, selected_profit_amount, "
                    "selected_profit_amount_symbol, payload_json) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        1,
                        HEALER.NETWORK,
                        HEALER.CHAIN_ID,
                        "2026-11-10T11:00:00Z",
                        "not_executable",
                        0.12,
                        "SPY",
                        # The legacy review table carries the published
                        # identity in the review payload, not in a column.
                        json.dumps({
                            "route_addresses": ["0xa", "0xb", "0xc", "0xa"],
                            "loopKey": "0xa:0xb:0xc",
                        }),
                    ),
                )
                con.commit()
            finally:
                con.close()
            with mock.patch.object(HEALER, "observation_started_at", return_value="2026-11-10T00:00:00Z"):
                candidates = HEALER.historical_positive_candidates(database, 5)
                excluded = HEALER.historical_positive_candidates(database, 5, {"0xa:0xb:0xc"})
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["historicalCategory"], "not_executable")
        self.assertEqual(excluded, [])


    def pool_depth_database(self, directory, depth_status="deep", tvl=500000.0,
                            in_candidates=0, fee=3000):
        database = Path(directory) / "pools.sqlite3"
        con = sqlite3.connect(database)
        try:
            con.execute(
                "CREATE TABLE v4_exchange_pools (network TEXT, chain_id INTEGER, "
                "protocol TEXT, pool_id TEXT, token0 TEXT, token1 TEXT, fee INTEGER, "
                "dynamic_fee INTEGER, special_fee INTEGER, hooks TEXT)"
            )
            con.execute(
                "CREATE TABLE v4_exchange_pool_liquidity_snapshots (network TEXT, "
                "chain_id INTEGER, protocol TEXT, pool_id TEXT, has_liquidity INTEGER, "
                "liquidity_index INTEGER, depth_status TEXT, estimated_tvl_usd REAL, "
                "capacity_binding_raw TEXT)"
            )
            # The production table carries the anchor scope and the depth
            # status; the fixture has to match it, otherwise the audit is
            # exercised against a schema that does not exist.
            con.execute(
                "CREATE TABLE dex_candidate_pools (network TEXT, chain_id INTEGER, "
                "protocol TEXT, pool_id TEXT, anchor_scope TEXT, depth_status TEXT)"
            )
            con.execute(
                "INSERT INTO v4_exchange_pools VALUES (?,?,?,?,?,?,?,?,?,?)",
                (HEALER.NETWORK, HEALER.CHAIN_ID, "uniswap_v4", "pool-1",
                 "0x" + "a" * 40, "0x" + "b" * 40, fee, 0, 0, ""),
            )
            con.execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots VALUES (?,?,?,?,?,?,?,?,?)",
                (HEALER.NETWORK, HEALER.CHAIN_ID, "uniswap_v4", "pool-1", 1, 5,
                 depth_status, tvl, "1000000000000000000"),
            )
            if in_candidates:
                con.execute(
                    "INSERT INTO dex_candidate_pools VALUES (?,?,?,?,?,?)",
                    (HEALER.NETWORK, HEALER.CHAIN_ID, "uniswap_v4", "pool-1",
                     "PAIR", depth_status),
                )
            con.commit()
        finally:
            con.close()
        return database

    # ------------------------------------------------------------------
    # The deployed pool-manager binary must support every command the
    # healer drives. A binary that predates a command rejects it with
    # "unknown command"; the healer used to carry on as if the step had
    # succeeded, which turned a deployment gap into a silent no-op.
    # ------------------------------------------------------------------
    def test_a_pool_manager_binary_missing_a_command_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = Path(directory)
            binary = tools / "pool-manager.exe"
            binary.write_bytes(b"MZ")
            completed = mock.Mock(returncode=0, stdout="usage: pool-manager <audit>",
                                  stderr="")
            with mock.patch.object(HEALER.subprocess, "run", return_value=completed):
                support = HEALER.pool_manager_command_support(tools)
            self.assertFalse(support["complete"])
            self.assertIn("depth", support["missingCommands"])
            self.assertIsNotNone(support["reason"])

    def test_a_pool_manager_binary_with_every_command_is_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = Path(directory)
            binary = tools / "pool-manager.exe"
            binary.write_bytes(b"MZ")
            usage = "usage: pool-manager <" + "|".join(
                HEALER.POOL_MANAGER_REQUIRED_COMMANDS) + ">"
            completed = mock.Mock(returncode=0, stdout=usage, stderr="")
            with mock.patch.object(HEALER.subprocess, "run", return_value=completed):
                support = HEALER.pool_manager_command_support(tools)
            self.assertTrue(support["available"])
            self.assertTrue(support["complete"])
            self.assertEqual(support["missingCommands"], [])

    def test_a_pool_manager_binary_that_cannot_be_executed_is_reported(self):
        """A truncated or foreign binary must not take the healer down."""
        with tempfile.TemporaryDirectory() as directory:
            tools = Path(directory)
            binary = tools / "pool-manager.exe"
            binary.write_bytes(b"MZ")
            with mock.patch.object(HEALER.subprocess, "run",
                                   side_effect=OSError(193, "not a valid Win32 application")):
                support = HEALER.pool_manager_command_support(tools)
            self.assertFalse(support["available"])
            self.assertFalse(support["complete"])
            self.assertEqual(support["reason"], "pool_manager_probe_failed")

    def test_a_missing_pool_manager_binary_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            support = HEALER.pool_manager_command_support(Path(directory))
            self.assertFalse(support["available"])
            self.assertFalse(support["complete"])
            self.assertEqual(support["reason"], "pool_manager_binary_missing")

    def test_replacement_report_lists_pool_outside_candidate_set(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.pool_depth_database(
                directory, in_candidates=0, fee=HEALER.MAX_CANDIDATE_FIXED_FEE)
            report = HEALER.pool_depth_replacement_report(database)
            self.assertEqual(report["status"], "replacement_available")
            self.assertEqual(report["replacementKeyCount"], 1)
            self.assertIsNone(report["code"])

    def test_replacement_report_excludes_pool_above_shared_fee_ceiling(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.pool_depth_database(
                directory, in_candidates=0, fee=HEALER.MAX_CANDIDATE_FIXED_FEE + 1)
            report = HEALER.pool_depth_replacement_report(database)
            self.assertEqual(report["status"], "no_capacity_qualified_pool_exists")
            self.assertEqual(report["replacementKeyCount"], 0)
            self.assertEqual(report["code"], "pool_depth_insufficient")

    def test_replacement_report_states_market_depth_exhausted(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.pool_depth_database(directory, in_candidates=1)
            report = HEALER.pool_depth_replacement_report(database)
            self.assertEqual(report["status"], "market_depth_exhausted")
            self.assertEqual(report["replacementKeyCount"], 0)
            self.assertEqual(report["code"], "pool_depth_insufficient")

    def test_replacement_report_states_no_depth_qualified_pool_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.pool_depth_database(directory, depth_status="thin", tvl=7955.0)
            report = HEALER.pool_depth_replacement_report(database)
            self.assertEqual(report["status"], "no_depth_qualified_pool_exists")
            self.assertEqual(report["depthQualifiedPairCount"], 0)
            self.assertEqual(report["code"], "pool_depth_insufficient")

    def test_replacement_report_accepts_a_depth_qualified_pool_below_the_usd_band(self):
        """A capacity-qualified pool is a replacement even when its USD
        valuation sits below the reference band.

        The USD valuation is denominated in the chain's own quote asset, so on
        a chain whose quote is not the USD every pool reads far below a fixed
        USD band. Gating the replacement search on that band made the healer
        report an exhausted market while hundreds of depth-qualified pools
        were waiting in the exchange table.
        """
        with tempfile.TemporaryDirectory() as directory:
            database = self.pool_depth_database(
                directory, in_candidates=0, depth_status="medium", tvl=0.42)
            report = HEALER.pool_depth_replacement_report(database)
            self.assertEqual(report["status"], "replacement_available")
            self.assertEqual(report["replacementKeyCount"], 1)
            self.assertEqual(report["depthCriterion"], "pool_manager_capacity_verdict")

    def test_depth_audit_accepts_a_capacity_qualified_pool_below_the_usd_band(self):
        """The depth audit must use the same capacity verdict as the pool
        manager, not a USD band that no chain-agnostic threshold can express."""
        with tempfile.TemporaryDirectory() as directory:
            database = self.pool_depth_database(
                directory, in_candidates=1, depth_status="medium", tvl=0.42)
            audit = HEALER.pool_depth_audit(database)
            self.assertTrue(audit["valid"])
            self.assertEqual(audit["adequateDepthPoolCount"], 1)
            self.assertEqual(audit["insufficientDepthPoolCount"], 0)
            self.assertEqual(audit["depthCriterion"], "pool_manager_capacity_verdict")

    def test_depth_audit_rejects_a_pool_whose_verdict_is_thin(self):
        """A pool the pool manager called thin is not adequate, whatever its
        USD valuation says."""
        with tempfile.TemporaryDirectory() as directory:
            database = self.pool_depth_database(
                directory, in_candidates=1, depth_status="thin", tvl=500000.0)
            audit = HEALER.pool_depth_audit(database)
            self.assertFalse(audit["valid"])
            self.assertEqual(audit["adequateDepthPoolCount"], 0)
            self.assertEqual(audit["thinPoolIdCount"], 1)

    # ------------------------------------------------------------------
    # TASK-128 (GAP-C): one broken symbol is repaired on its own.
    # ------------------------------------------------------------------
    def token_library_database(self, directory, rows):
        """Build a store with one admitted token library."""
        database = Path(directory) / "tokens.sqlite3"
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "CREATE TABLE token_core (network TEXT, chain_id INTEGER, "
                "token_symbol TEXT, token_address TEXT, token_decimals INTEGER, "
                "admission_status TEXT, metadata_source TEXT, admission_source TEXT, "
                "source_json TEXT)"
            )
            connection.execute(
                "CREATE TABLE chain_tokens (network TEXT, chain_id INTEGER, "
                "token_symbol TEXT, token_address TEXT, token_decimals INTEGER)"
            )
            for row in rows:
                connection.execute(
                    "INSERT INTO token_core VALUES (?,?,?,?,?,?,?,?,?)",
                    (HEALER.NETWORK, HEALER.CHAIN_ID, *row),
                )
            connection.commit()
        finally:
            connection.close()
        return database

    def test_a_missing_decimals_token_is_detected_by_symbol(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.token_library_database(directory, [
                ("USDG", HEALER.OFFICIAL_FUNDING_TOKEN_ADDRESS_BY_SYMBOL["USDG"],
                 None, "admitted", "", "", ""),
            ])
            defects = HEALER.token_defects(database)
            kinds = {item["kind"] for item in defects}
            self.assertIn("missing_decimals", kinds)
            self.assertEqual(
                [item["symbol"] for item in defects if item["kind"] == "missing_decimals"],
                ["USDG"])

    def test_a_funding_token_at_the_wrong_address_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.token_library_database(directory, [
                ("USDG", "0x" + "11" * 20, 18, "admitted", "", "", ""),
            ])
            defects = HEALER.token_defects(database)
            mismatch = [item for item in defects
                        if item["kind"] == "funding_address_mismatch"]
            self.assertEqual(len(mismatch), 1)
            self.assertEqual(mismatch[0]["symbol"], "USDG")
            self.assertEqual(mismatch[0]["expectedAddress"],
                             HEALER.OFFICIAL_FUNDING_TOKEN_ADDRESS_BY_SYMBOL["USDG"])

    def test_a_counterfeit_token_is_not_sent_to_the_symbol_resolver(self):
        """A counterfeit cannot be repaired by resolving its symbol."""
        counterfeit = sorted(HEALER.KNOWN_COUNTERFEIT_FUNDING_TOKEN_ADDRESSES)[0]
        with tempfile.TemporaryDirectory() as directory:
            database = self.token_library_database(directory, [
                ("USDG", counterfeit, 18, "admitted", "", "", ""),
            ])
            defects = HEALER.token_defects(database)
            kinds = {item["kind"] for item in defects}
            self.assertIn("counterfeit_admission", kinds)
            targets = HEALER.resolve_token_targets(defects)
            self.assertEqual(targets, [])

    def test_a_broken_symbol_names_the_resolve_token_action(self):
        report = {
            "layers": {
                "tokens": {"valid": False, "defects": [
                    {"kind": "missing_decimals", "symbol": "USDG",
                     "address": "0x" + "22" * 20}]},
                "pools": {"valid": True},
                "closedLoops": {"valid": True},
                "reviews": {"valid": True, "legacy": {"stats": {"missingValue": 0}}},
                "broadcast": {"outcomes": {"failed": 0}},
            }
        }
        self.assertIn("resolve_token", HEALER.healing_actions(report))

    def test_a_counterfeit_only_token_layer_falls_back_to_the_batch_path(self):
        report = {
            "layers": {
                "tokens": {"valid": False, "defects": [
                    {"kind": "counterfeit_admission", "symbol": "USDG",
                     "address": "0x" + "33" * 20}]},
                "pools": {"valid": True},
                "closedLoops": {"valid": True},
                "reviews": {"valid": True, "legacy": {"stats": {"missingValue": 0}}},
                "broadcast": {"outcomes": {"failed": 0}},
            }
        }
        self.assertEqual(HEALER.healing_actions(report), ["repair_tokens"])

    def test_resolve_token_calls_the_manager_api_once_per_symbol(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.token_library_database(directory, [
                ("USDG", HEALER.OFFICIAL_FUNDING_TOKEN_ADDRESS_BY_SYMBOL["USDG"],
                 None, "admitted", "", "", ""),
                ("WETH", HEALER.OFFICIAL_FUNDING_TOKEN_ADDRESS_BY_SYMBOL["WETH"],
                 None, "admitted", "", "", ""),
            ])
            defects = HEALER.token_defects(database)
            calls = []

            def fake_post(base, path, body):
                calls.append((base, path, body))
                return {"ok": True, "status": 200, "body": "{}"}

            with mock.patch.object(HEALER, "api_post", side_effect=fake_post):
                result = HEALER.repair_resolve_token(database, "http://manager", False,
                                                     defects)
            self.assertEqual(len(calls), 2)
            self.assertEqual({call[1] for call in calls}, {"/v1/tokens/resolve"})
            self.assertEqual({call[2]["symbol"] for call in calls}, {"USDG", "WETH"})
            for call in calls:
                self.assertEqual(call[2]["chainId"], HEALER.CHAIN_ID)
                self.assertTrue(call[2]["dryRun"])
            self.assertEqual(result["resolvedCount"], 2)
            self.assertEqual(result["failedCount"], 0)

    # ------------------------------------------------------------------
    # TASK-131: the orchestrator is chain-agnostic.
    #
    # The orchestrator is a **mechanism**, not a chain-specific script.
    # The proof has two parts:
    #
    # 1. the same code path runs for both chains -- the chain id only
    #    selects the scope, the database and the registry; it never
    #    selects a different algorithm;
    # 2. every query is filtered by the active chain, so a report built
    #    for one chain can never contain the other chain's rows.
    # ------------------------------------------------------------------
    def test_the_orchestrator_supports_both_chains_through_one_path(self):
        self.assertIn(4663, HEALER.KNOWN_SCOPES)
        self.assertIn(46630, HEALER.KNOWN_SCOPES)
        for chain_id in (4663, 46630):
            network, active_chain, scope = HEALER.activate_scope(chain_id)
            self.assertEqual(active_chain, chain_id)
            self.assertEqual(scope, "core4")
            self.assertTrue(network)
        # Restore the default scope so the rest of the suite is unaffected.
        HEALER.activate_scope(4663)

    def test_each_chain_resolves_to_its_own_database_file(self):
        mainnet = HEALER.database_for_chain(4663)
        testnet = HEALER.database_for_chain(46630)
        self.assertNotEqual(mainnet, testnet)
        self.assertEqual(mainnet.name, "local_store_4663.sqlite3")
        self.assertEqual(testnet.name, "local_store_46630.sqlite3")
        self.assertEqual(HEALER.CHAIN_DATABASES[4663], "local_store_4663.sqlite3")
        self.assertEqual(HEALER.CHAIN_DATABASES[46630], "local_store_46630.sqlite3")

    def test_the_chain_database_guard_refuses_the_other_chains_file(self):
        mainnet = HEALER.database_for_chain(4663)
        testnet = HEALER.database_for_chain(46630)
        # The guard is what makes the per-chain premise enforceable: a
        # report built from the wrong file would describe the wrong chain.
        with self.assertRaises(ValueError):
            HEALER.assert_chain_database(testnet, 4663)
        with self.assertRaises(ValueError):
            HEALER.assert_chain_database(mainnet, 46630)
        # The matching file is accepted.
        self.assertEqual(HEALER.assert_chain_database(mainnet, 4663), mainnet)
        self.assertEqual(HEALER.assert_chain_database(testnet, 46630), testnet)

    def test_a_report_never_contains_another_chains_rows(self):
        """The same code path, applied to two chains, keeps them separate."""
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "two_chains.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE cow_execution_reviews (id INTEGER, network TEXT, "
                "chain_id INTEGER, attempt_key TEXT, category TEXT, pair TEXT, "
                "tx_hash TEXT, created_at TEXT, payload_json TEXT, "
                "selected_profit_raw TEXT, selected_profit_amount TEXT, "
                "selected_profit_amount_symbol TEXT, selected_profit_source TEXT, "
                "selected_profit_missing_reason TEXT)"
            )
            # The Manager library has to exist too, otherwise the healer
            # reads it first and the legacy table is never reached.
            connection.execute(
                "CREATE TABLE manager_v4_reviews (network TEXT, chain_id INTEGER, "
                "loop_key TEXT, closed_loop_version INTEGER, closed_loop_checksum TEXT, "
                "candidate_edge_version INTEGER, candidate_edge_checksum TEXT, "
                "observation_version INTEGER, observation_checksum TEXT, "
                "pool_scope_checksum TEXT, anchor_token_key_id TEXT, anchor_symbol TEXT, "
                "path_text TEXT, path_addresses_json TEXT, path_symbols_json TEXT, "
                "pool_path_key TEXT, pool_ids_json TEXT, review_status TEXT, "
                "review_reason TEXT, static_preview_json TEXT, run_arbitrage_json TEXT, "
                "signed_pnl_raw TEXT, signed_pnl_amount TEXT, signed_pnl_currency TEXT, "
                "signed_pnl_address TEXT, signed_pnl_unit TEXT, signed_pnl_source TEXT, "
                "reviewed_at TEXT, created_at TEXT, updated_at TEXT)"
            )
            # The legacy reader filters on a **relative** window, so the
            # fixture has to carry a current timestamp.
            created_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            for network, chain_id, amount in (("robinhood", 4663, "1.5"),
                                             ("robinhood-testnet", 46630, "2.5")):
                connection.execute(
                    "INSERT INTO cow_execution_reviews VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (1 if chain_id == 4663 else 2, network, chain_id,
                     "attempt", "executable", "ETH/ETH", None,
                     created_at, "{}", "1", amount, "ETH",
                     "native_rust_contract_preview", None),
                )
            connection.commit()
            connection.close()
            for chain_id, network, expected in ((4663, "robinhood", 1.5),
                                                (46630, "robinhood-testnet", 2.5)):
                HEALER.activate_scope(chain_id)
                try:
                    with mock.patch.object(HEALER, "observation_started_at",
                                           return_value=created_at):
                        rows = HEALER.review_rows(database, 24)
                    # Exactly one row: the row of the **active** chain.
                    # The other chain's row is filtered out by the
                    # ``network=? AND chain_id=?`` predicate, so the report
                    # never describes the wrong chain.
                    self.assertEqual(len(rows), 1, network)
                    self.assertEqual(HEALER.pnl(rows[0])["amount"], expected)
                finally:
                    HEALER.activate_scope(4663)

    def test_the_cascade_matrix_is_chain_scoped(self):
        """The matrix counts the active chain only."""
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "matrix.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE dex_candidate_pools (network TEXT, chain_id INTEGER, "
                "pool_id TEXT, depth_status TEXT)")
            connection.execute(
                "CREATE TABLE manager_v4_closed_loops (network TEXT, chain_id INTEGER, "
                "loop_key TEXT, pool_ids_json TEXT, path_symbols_json TEXT, "
                "anchor_symbol TEXT, status TEXT)")
            connection.execute(
                "INSERT INTO dex_candidate_pools VALUES (?,?,?,?)",
                ("robinhood", 4663, "pool-mainnet", "deep"))
            connection.execute(
                "INSERT INTO dex_candidate_pools VALUES (?,?,?,?)",
                ("robinhood-testnet", 46630, "pool-testnet", "deep"))
            connection.commit()
            connection.close()
            for chain_id, expected in ((4663, 1), (46630, 1)):
                HEALER.activate_scope(chain_id)
                try:
                    matrix = HEALER.cascade_matrix(database)
                    self.assertEqual(matrix["candidatePools"]["distinctPoolCount"],
                                     expected)
                finally:
                    HEALER.activate_scope(4663)

    # ------------------------------------------------------------------
    # TASK-129 (GAP-D): a broken reading and a thin pool are different
    # defects with different repairs.
    # ------------------------------------------------------------------
    def test_a_pool_above_the_tvl_ceiling_is_a_bad_reading_not_a_thin_pool(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.pool_depth_database(
                directory, in_candidates=1, tvl=1.0e20)
            audit = HEALER.pool_depth_audit(database)
            self.assertIsNone(audit["classification"])
            self.assertIsNone(audit["repairPath"])
            self.assertEqual(audit["staleTvlReadingPoolCount"], 1)
            self.assertEqual(audit["thinPoolIdCount"], 0)
            self.assertEqual(audit["adequatePoolIdCount"], 1)

    def test_a_pool_below_the_tvl_floor_is_genuinely_thin(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.pool_depth_database(
                directory, in_candidates=1, depth_status="thin", tvl=1000.0)
            audit = HEALER.pool_depth_audit(database)
            self.assertEqual(audit["classification"], "pool_depth_insufficient")
            self.assertEqual(audit["repairPath"], "replace_shallow_pool")
            self.assertEqual(audit["badReadingPoolCount"], 0)
            self.assertEqual(audit["thinPoolIdCount"], 1)

    def test_a_broken_reading_names_the_remeasure_action(self):
        report = {
            "layers": {
                "tokens": {"valid": True},
                "pools": {"valid": False, "depth": {
                    "classification": "pool_reading_invalid"}},
                "closedLoops": {"valid": True},
                "reviews": {"valid": True, "legacy": {"stats": {"missingValue": 0}}},
                "broadcast": {"outcomes": {"failed": 0}},
            }
        }
        self.assertEqual(HEALER.healing_actions(report), ["remeasure_pool_depth"])

    def test_a_genuinely_thin_pool_names_the_replace_action(self):
        report = {
            "layers": {
                "tokens": {"valid": True},
                "pools": {"valid": False, "depth": {
                    "classification": "pool_depth_insufficient"}},
                "closedLoops": {"valid": True},
                "reviews": {"valid": True, "legacy": {"stats": {"missingValue": 0}}},
                "broadcast": {"outcomes": {"failed": 0}},
            }
        }
        self.assertEqual(HEALER.healing_actions(report), ["repair_pools"])

    # ------------------------------------------------------------------
    # TASK-129: the full-range matrix counts every link explicitly.
    # ------------------------------------------------------------------
    def test_the_cascade_matrix_reports_an_empty_link_as_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "empty.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE dex_candidate_pools (network TEXT, chain_id INTEGER, "
                "pool_id TEXT, depth_status TEXT)")
            connection.execute(
                "CREATE TABLE manager_v4_closed_loops (network TEXT, chain_id INTEGER, "
                "loop_key TEXT, pool_ids_json TEXT, path_symbols_json TEXT, "
                "anchor_symbol TEXT, status TEXT)")
            connection.commit()
            connection.close()
            matrix = HEALER.cascade_matrix(database)
            self.assertEqual(matrix["fundingTokens"]["valid"], 0)
            self.assertEqual(matrix["fundingTokens"]["expected"], 5)
            self.assertEqual(matrix["candidateTokens"]["admitted"], 0)
            self.assertEqual(matrix["candidatePools"]["distinctPoolCount"], 0)
            self.assertEqual(matrix["closedLoops"]["total"], 0)
            self.assertEqual(matrix["closedLoops"]["exactThreeHops"], 0)

    # ------------------------------------------------------------------
    # TASK-130: the stable-achievement criterion.
    # ------------------------------------------------------------------
    def healthy_report(self, timestamp="2026-11-17T00:00:00Z"):
        return {
            "generatedAt": timestamp,
            "blockers": [],
            "layers": {
                "reviews": {"legacy": {"stats": {
                    "positive": 3, "missingValue": 0, "sum": 1.0}}},
                "broadcast": {"outcomes": {"success": 2, "failed": 0}},
            },
            "transactionProduction": {"state": "healthy"},
        }

    def test_one_healthy_window_is_not_stability(self):
        tracker = HEALER.StabilityTracker()
        verdict = tracker.observe(self.healthy_report())
        self.assertFalse(verdict["stable"])
        self.assertEqual(verdict["consecutiveWindows"], 1)

    def test_three_consecutive_healthy_windows_are_stable(self):
        tracker = HEALER.StabilityTracker()
        for minute in (0, 31, 62):
            verdict = tracker.observe(self.healthy_report(
                f"2026-11-17T00:{minute:02d}:00Z"))
        self.assertTrue(verdict["stable"])
        self.assertEqual(verdict["consecutiveWindows"], 3)

    def test_a_broken_window_resets_the_stability_run(self):
        tracker = HEALER.StabilityTracker()
        tracker.observe(self.healthy_report("2026-11-17T00:00:00Z"))
        tracker.observe(self.healthy_report("2026-11-17T00:31:00Z"))
        broken = self.healthy_report("2026-11-17T01:02:00Z")
        broken["blockers"] = ["pools"]
        verdict = tracker.observe(broken)
        self.assertFalse(verdict["stable"])
        self.assertEqual(verdict["consecutiveWindows"], 0)
        self.assertIn("noBlockers", verdict["failedConditions"])

    def test_a_missing_review_value_breaks_the_stability_window(self):
        report = self.healthy_report()
        report["layers"]["reviews"]["legacy"]["stats"]["missingValue"] = 2
        verdict = HEALER.StabilityTracker().observe(report)
        self.assertFalse(verdict["satisfied"])
        self.assertIn("noMissingReviewValue", verdict["failedConditions"])

    def test_a_broadcast_without_success_breaks_the_stability_window(self):
        report = self.healthy_report()
        report["layers"]["broadcast"]["outcomes"]["success"] = 0
        verdict = HEALER.StabilityTracker().observe(report)
        self.assertFalse(verdict["satisfied"])
        self.assertIn("broadcastSuccess", verdict["failedConditions"])

    def test_an_unhealthy_production_state_breaks_the_stability_window(self):
        report = self.healthy_report()
        report["transactionProduction"]["state"] = "upstream_invalid"
        verdict = HEALER.StabilityTracker().observe(report)
        self.assertFalse(verdict["satisfied"])
        self.assertIn("productionHealthy", verdict["failedConditions"])

    # ------------------------------------------------------------------
    # AUD-P0-1 / AUD-P1-4 / AUD-P2-1: pool depth accounting
    # ------------------------------------------------------------------
    def multi_row_pool_depth_database(self, directory, candidate_rows, tvl,
                                      depth_status="deep", tvl_affinity="REAL"):
        """Build a candidate table where one pool may span several rows.

        ``candidate_rows`` is the number of candidate rows to insert for the
        same pool id, which simulates the per-anchor-scope duplication that
        used to inflate the depth counters.
        """
        database = Path(directory) / "pools_multi.sqlite3"
        con = sqlite3.connect(database)
        try:
            con.execute(
                "CREATE TABLE v4_exchange_pools (network TEXT, chain_id INTEGER, "
                "protocol TEXT, pool_id TEXT, token0 TEXT, token1 TEXT, fee INTEGER, "
                "dynamic_fee INTEGER, special_fee INTEGER, hooks TEXT)"
            )
            con.execute(
                "CREATE TABLE v4_exchange_pool_liquidity_snapshots (network TEXT, "
                "chain_id INTEGER, protocol TEXT, pool_id TEXT, has_liquidity INTEGER, "
                "liquidity_index INTEGER, depth_status TEXT, "
                f"estimated_tvl_usd {tvl_affinity}, capacity_binding_raw TEXT)"
            )
            con.execute(
                "CREATE TABLE dex_candidate_pools (network TEXT, chain_id INTEGER, "
                "protocol TEXT, pool_id TEXT, anchor_scope TEXT, depth_status TEXT)"
            )
            con.execute(
                "INSERT INTO v4_exchange_pools VALUES (?,?,?,?,?,?,?,?,?,?)",
                (HEALER.NETWORK, HEALER.CHAIN_ID, "uniswap_v4", "pool-1",
                 "0x" + "a" * 40, "0x" + "b" * 40, 3000, 0, 0, ""),
            )
            con.execute(
                "INSERT INTO v4_exchange_pool_liquidity_snapshots VALUES (?,?,?,?,?,?,?,?,?)",
                (HEALER.NETWORK, HEALER.CHAIN_ID, "uniswap_v4", "pool-1", 1, 5,
                 depth_status, tvl, "1000000000000000000"),
            )
            for index in range(candidate_rows):
                con.execute(
                    "INSERT INTO dex_candidate_pools VALUES (?,?,?,?,?,?)",
                    (HEALER.NETWORK, HEALER.CHAIN_ID, "uniswap_v4", "pool-1",
                     f"scope-{index}", depth_status),
                )
            con.commit()
        finally:
            con.close()
        return database

    def test_pool_depth_audit_counts_distinct_pools_and_candidate_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.multi_row_pool_depth_database(directory, 3, 500000.0)
            report = HEALER.pool_depth_audit(database)
            self.assertEqual(report["uniquePoolCount"], 1)
            self.assertEqual(report["candidatePoolRows"], 3)
            self.assertEqual(report["adequateDepthPoolCount"], 1)

    def test_pool_depth_audit_excludes_bad_readings_above_upper_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.multi_row_pool_depth_database(directory, 1, 1e43)
            report = HEALER.pool_depth_audit(database)
            self.assertEqual(report["badReadingCount"], 1)
            self.assertEqual(report["adequateDepthPoolCount"], 1)
            self.assertTrue(report["valid"])

    def test_pool_depth_audit_casts_text_tvl_to_real(self):
        """A TEXT-affinity TVL column must still satisfy the numeric floor."""
        with tempfile.TemporaryDirectory() as directory:
            database = self.multi_row_pool_depth_database(
                directory, 1, "100000", tvl_affinity="TEXT")
            report = HEALER.pool_depth_audit(database)
            self.assertEqual(report["adequateDepthPoolCount"], 1)
            self.assertEqual(report["badReadingCount"], 0)

    # ------------------------------------------------------------------
    # AUD-P0-4: profit accounting
    # ------------------------------------------------------------------
    def test_pnl_stats_separates_estimate_only_profit(self):
        rows = [
            {"selected_profit_amount": "1.5", "selected_profit_amount_symbol": "ETH",
             "selected_profit_source": "native_contract_rate_delta_estimate",
             "category": "not_executable", "payload_json": "{}"},
            {"selected_profit_amount": "2.0", "selected_profit_amount_symbol": "ETH",
             "selected_profit_source": "native_rust_contract_preview",
             "category": "executable", "payload_json": "{}"},
            {"selected_profit_amount": "-1.0", "selected_profit_amount_symbol": "ETH",
             "selected_profit_source": "native_rust_contract_preview",
             "category": "not_executable", "payload_json": "{}"},
        ]
        stats = HEALER.pnl_stats(rows)
        self.assertEqual(stats["positiveAmount"], 2)
        self.assertEqual(stats["positive"], 1)
        self.assertEqual(stats["positiveEstimateOnly"], 1)
        self.assertEqual(stats["estimatedProfitOnlyCount"], 1)
        self.assertEqual(stats["confirmedSum"], 2.0)
        # nonPositive counts every non-positive value, it must not subtract
        # the estimate-only positives from the positive amount.
        self.assertEqual(stats["nonPositive"], 1)

    # ------------------------------------------------------------------
    # AUD-P1-3: best amount accounting
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # AUD-P1-3: best-trade-amount fixtures
    # ------------------------------------------------------------------
    def best_amount_payload(self, best_amount="1.0", confirmed=True, missing=False,
                            probe_rows=None, requested_symbol="ETH",
                            offchain_gas_gate=None, status="reviewed"):
        """Build a review payload that carries concrete best-amount evidence.

        The payload mirrors the shape the Review Manager materialises: the
        numeric selection lives under ``bestAmountCalculation`` and the
        on-chain probe ladder lives under
        ``quote.live_onchain_evidence.probeRecords``.
        """
        if probe_rows is None:
            probe_rows = []
        evidence = {
            "probeRecords": list(probe_rows),
            "costModel": {},
        }
        if offchain_gas_gate is not None:
            evidence["offchainGasGate"] = dict(offchain_gas_gate)
        return {
            "status": status,
            "bestAmountCalculation": {
                "bestAmount": best_amount,
                "bestAmountConfirmed": bool(confirmed),
                "bestAmountMissing": bool(missing),
                "initialProbeAmount": best_amount,
                "requestedSymbol": requested_symbol,
                "method": "execution_size_ladder",
            },
            "quote": {
                "live_onchain_evidence": evidence,
            },
        }

    def completed_ladder(self):
        """Return a probe ladder that actually expanded past the first probe."""
        return [
            {"amount": "1.0", "ok": True},
            {"amount": "2.0", "ok": True},
        ]

    def best_amount_database(self, directory, payload, selected_profit_amount="1.25"):
        """Build a store that carries one Manager-authoritative review.

        ``manager_v4_reviews`` is the authoritative source the healer reads
        first, so the fixture materialises that table together with the
        closed-loop library it is matched against.
        """
        database = Path(directory) / "reviews.sqlite3"
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "CREATE TABLE manager_v4_reviews (network TEXT, chain_id INTEGER, "
                "loop_key TEXT, closed_loop_version INTEGER, closed_loop_checksum TEXT, "
                "candidate_edge_version INTEGER, candidate_edge_checksum TEXT, "
                "observation_version INTEGER, observation_checksum TEXT, "
                "pool_scope_checksum TEXT, anchor_token_key_id TEXT, anchor_symbol TEXT, "
                "path_text TEXT, path_addresses_json TEXT, path_symbols_json TEXT, "
                "pool_path_key TEXT, pool_ids_json TEXT, review_status TEXT, "
                "review_reason TEXT, static_preview_json TEXT, run_arbitrage_json TEXT, "
                "signed_pnl_raw TEXT, signed_pnl_amount TEXT, signed_pnl_currency TEXT, "
                "signed_pnl_address TEXT, signed_pnl_unit TEXT, signed_pnl_source TEXT, "
                "reviewed_at TEXT, created_at TEXT, updated_at TEXT)"
            )
            connection.execute(
                "CREATE TABLE manager_v4_closed_loops (network TEXT, chain_id INTEGER, "
                "loop_key TEXT)"
            )
            connection.execute(
                "CREATE TABLE manager_review_library_state (network TEXT, chain_id INTEGER, "
                "state_key TEXT)"
            )
            connection.execute(
                "CREATE TABLE cow_execution_reviews (id INTEGER, network TEXT, "
                "chain_id INTEGER, attempt_key TEXT, category TEXT, pair TEXT, "
                "tx_hash TEXT, created_at TEXT, payload_json TEXT, "
                "selected_profit_raw TEXT, selected_profit_amount TEXT, "
                "selected_profit_amount_symbol TEXT, selected_profit_source TEXT, "
                "selected_profit_missing_reason TEXT)"
            )
            route = ["0x" + "a" * 40, "0x" + "b" * 40, "0x" + "c" * 40]
            loop_key = ":".join(route)
            created_at = "2026-11-10T00:00:00Z"
            payload_json = json.dumps(payload)
            connection.execute(
                "INSERT INTO manager_v4_closed_loops VALUES (?,?,?)",
                (HEALER.NETWORK, HEALER.CHAIN_ID, loop_key),
            )
            connection.execute(
                "INSERT INTO manager_review_library_state VALUES (?,?,?)",
                (HEALER.NETWORK, HEALER.CHAIN_ID, "reviews"),
            )
            connection.execute(
                "INSERT INTO manager_v4_reviews (network, chain_id, loop_key, "
                "path_addresses_json, review_status, static_preview_json, "
                "signed_pnl_raw, signed_pnl_amount, signed_pnl_currency, "
                "signed_pnl_source, reviewed_at, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (HEALER.NETWORK, HEALER.CHAIN_ID, loop_key,
                 json.dumps(route), "reviewed", payload_json,
                 str(selected_profit_amount),
                 str(selected_profit_amount), "ETH", "native_rust_contract_preview",
                 created_at, created_at, created_at),
            )
            connection.execute(
                "INSERT INTO cow_execution_reviews VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (1, HEALER.NETWORK, HEALER.CHAIN_ID, "attempt-1", "executable",
                 "ETH/ETH", None, created_at, payload_json, str(selected_profit_amount),
                 str(selected_profit_amount), "ETH", "native_rust_contract_preview", None),
            )
            connection.commit()
        finally:
            connection.close()
        return database

    def test_the_manager_review_library_is_read_for_best_amount_evidence(self):
        """The Manager library has no ``payload_json`` column.

        ``manager_v4_reviews`` keeps the best-trade-amount evidence in
        ``static_preview_json`` / ``run_arbitrage_json``. A healer that only
        reads ``payload_json`` therefore sees every Manager review as a
        missing best amount, even when the Manager materialised a concrete
        amount, and the review layer can never converge.
        """
        row = {
            "static_preview_json": json.dumps(
                self.best_amount_payload(best_amount="1.5", confirmed=True,
                                         missing=False,
                                         probe_rows=self.completed_ladder())),
            "run_arbitrage_json": "{}",
        }
        state = HEALER.best_amount_state(row)
        self.assertEqual(state["bestAmount"], "1.5")
        self.assertTrue(state["confirmed"])
        self.assertFalse(state["missing"])
        self.assertFalse(state["ladderIncomplete"])
        self.assertEqual(state["probeCount"], 2)

    def test_a_manager_review_with_a_confirmed_amount_is_not_reported_as_missing(self):
        """The whole point of the fix: a confirmed amount must not be lost."""
        with tempfile.TemporaryDirectory() as directory:
            database = self.best_amount_database(
                directory,
                self.best_amount_payload(best_amount="1.5", confirmed=True,
                                         missing=False,
                                         probe_rows=self.completed_ladder()),
            )
            with mock.patch.object(HEALER, "observation_started_at",
                                   return_value="2026-11-10T00:00:00Z"):
                report = HEALER.audit_reviews(database, 24)
            self.assertEqual(report["bestAmount"]["missing"], 0)
            self.assertEqual(report["bestAmount"]["confirmed"], 1)

    def test_a_manager_review_keeps_its_real_missing_reason(self):
        """The Manager library stores the reason in review_status/review_reason.

        Reading only the legacy ``selected_profit_missing_reason`` column made
        every Manager review look like a generic "evidence_missing", which hid
        the actual upstream failure from the operator.
        """
        row = {"review_status": "review_failed",
               "review_reason": "native_task_config_unavailable"}
        self.assertEqual(HEALER.review_missing_reason(row),
                         "review_failed:native_task_config_unavailable")

    def test_a_manager_review_without_a_value_reports_its_real_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.best_amount_database(
                directory,
                self.best_amount_payload(best_amount=None, confirmed=False,
                                         missing=True, probe_rows=[]),
                selected_profit_amount=None,
            )
            with mock.patch.object(HEALER, "observation_started_at",
                                   return_value="2026-11-10T00:00:00Z"):
                report = HEALER.audit_reviews(database, 24)
            reasons = [item["reason"] for item in report["missingReasons"]]
            self.assertTrue(any("evidence_missing" != reason for reason in reasons),
                            f"the manager reason was lost: {reasons}")

    def test_review_evidence_resolves_the_route_of_a_manager_review(self):
        """A Manager row stores the route in ``path_addresses_json``."""
        row = {"loop_key": "loop-1",
               "path_addresses_json": json.dumps(["0x" + "aa" * 20, "0x" + "bb" * 20]),
               "review_status": "review_failed",
               "review_reason": "rpc_unavailable"}
        evidence = HEALER.review_evidence(row)
        self.assertEqual(evidence["loopKey"], "loop-1")
        self.assertEqual(evidence["staticPreview"]["routeAddresses"],
                         ["0x" + "aa" * 20, "0x" + "bb" * 20])
        self.assertTrue(evidence["runArbitrage"]["diagnostics"])
        self.assertEqual(evidence["runArbitrage"]["diagnostics"][0]["diagnosticType"],
                         "rpc_unavailable")

    def test_the_legacy_review_library_is_read_for_best_amount_evidence(self):
        """The legacy library still keeps the evidence in ``payload_json``."""
        row = {
            "payload_json": json.dumps(
                self.best_amount_payload(best_amount="2.0", confirmed=True,
                                         missing=False,
                                         probe_rows=self.completed_ladder())),
        }
        state = HEALER.best_amount_state(row)
        self.assertEqual(state["bestAmount"], "2.0")
        self.assertTrue(state["confirmed"])
        self.assertFalse(state["missing"])

    def test_best_amount_audit_counts_too_small_amounts_as_insufficient(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.best_amount_database(
                directory,
                self.best_amount_payload(best_amount="0.0", confirmed=True, missing=False,
                                         probe_rows=self.completed_ladder()),
            )
            with mock.patch.object(HEALER, "observation_started_at",
                                   return_value="2026-11-10T00:00:00Z"):
                report = HEALER.best_amount_audit(database, 24)
            self.assertEqual(report["tooSmallCount"], 1)
            self.assertEqual(report["belowGasCount"], 0)
            self.assertEqual(report["insufficientCount"], 1)
            self.assertFalse(report["valid"])

    # ------------------------------------------------------------------
    # AUD-P1-3 / ladder fail-closed
    # ------------------------------------------------------------------
    def test_best_amount_state_flags_incomplete_ladder(self):
        row = {"payload_json": json.dumps(
            self.best_amount_payload(best_amount="1.0", confirmed=True, missing=False,
                                     probe_rows=[{"amount": "1.0", "ok": True}]))}
        state = HEALER.best_amount_state(row)
        self.assertTrue(state["ladderIncomplete"])
        self.assertEqual(state["probeCount"], 1)
        self.assertFalse(state["confirmed"])
        self.assertTrue(state["missing"])
        self.assertEqual(state["reviewFailureKind"],
                         HEALER.BEST_AMOUNT_LADDER_INCOMPLETE_CODE)

    def test_audit_reviews_counts_ladder_incomplete_as_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.best_amount_database(
                directory,
                self.best_amount_payload(best_amount="1.0", confirmed=True, missing=False,
                                         probe_rows=[{"amount": "1.0", "ok": True}]),
            )
            with mock.patch.object(HEALER, "observation_started_at",
                                   return_value="2026-11-10T00:00:00Z"):
                report = HEALER.audit_reviews(database, 24)
            self.assertEqual(report["bestAmount"]["missing"], 1)
            self.assertEqual(report["bestAmount"]["ladderIncomplete"], 1)
            self.assertEqual(report["legacy"]["bestAmountLadderIncomplete"], 1)
            self.assertFalse(report["valid"])

    # ------------------------------------------------------------------
    # AUD-P2-5: review scan limit
    # ------------------------------------------------------------------
    def test_review_rows_applies_default_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.best_amount_database(
                directory,
                self.best_amount_payload(best_amount="1.0", confirmed=True, missing=False,
                                         probe_rows=self.completed_ladder()),
            )
            with mock.patch.object(HEALER, "observation_started_at",
                                   return_value="2026-11-10T00:00:00Z"):
                rows = HEALER.review_rows(database, 24, limit=0)
            self.assertEqual(rows, [])



    # ------------------------------------------------------------------
    # W4: AUD-P2-8 the observation epoch must never be silently substituted
    # ------------------------------------------------------------------

    def test_observation_epoch_marks_a_missing_file_as_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing_epoch.json"
            with mock.patch.object(HEALER, "EPOCH_FILE", missing):
                epoch = HEALER.observation_epoch()
            self.assertTrue(epoch["epochFallback"])
            self.assertTrue(epoch["epochUnknown"])
            self.assertIsNotNone(epoch["epochFallbackReason"])
            # The substituted window must be bounded, never the unix epoch.
            self.assertNotEqual(epoch["startedAt"], "1970-01-01T00:00:00Z")
            self.assertEqual(epoch["startedAt"], HEALER.OBSERVATION_EPOCH_FALLBACK)

    def test_observation_epoch_marks_an_unparsable_file_as_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / "broken_epoch.json"
            broken.write_text("{not json", encoding="utf-8")
            with mock.patch.object(HEALER, "EPOCH_FILE", broken):
                epoch = HEALER.observation_epoch()
            self.assertTrue(epoch["epochUnknown"])
            self.assertEqual(epoch["epochFallbackReason"], "JSONDecodeError")

    def test_observation_epoch_marks_a_non_object_document_as_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / "list_epoch.json"
            broken.write_text("[1, 2, 3]", encoding="utf-8")
            with mock.patch.object(HEALER, "EPOCH_FILE", broken):
                epoch = HEALER.observation_epoch()
            self.assertTrue(epoch["epochUnknown"])
            self.assertEqual(epoch["epochFallbackReason"], "epoch_not_an_object")

    def test_observation_epoch_marks_a_missing_started_at_as_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory) / "empty_epoch.json"
            empty.write_text(json.dumps({"other": "value"}), encoding="utf-8")
            with mock.patch.object(HEALER, "EPOCH_FILE", empty):
                epoch = HEALER.observation_epoch()
            self.assertTrue(epoch["epochUnknown"])
            self.assertEqual(epoch["epochFallbackReason"], "startedAt_missing")

    def test_observation_epoch_marks_an_invalid_timestamp_as_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / "bad_epoch.json"
            bad.write_text(json.dumps({"startedAt": "not-a-timestamp"}), encoding="utf-8")
            with mock.patch.object(HEALER, "EPOCH_FILE", bad):
                epoch = HEALER.observation_epoch()
            self.assertTrue(epoch["epochUnknown"])
            self.assertEqual(epoch["epochFallbackReason"], "startedAt_not_a_timestamp")

    def test_observation_epoch_reads_a_valid_epoch(self):
        with tempfile.TemporaryDirectory() as directory:
            good = Path(directory) / "good_epoch.json"
            good.write_text(
                json.dumps({"startedAt": "2026-11-11T08:46:25Z"}), encoding="utf-8"
            )
            with mock.patch.object(HEALER, "EPOCH_FILE", good):
                epoch = HEALER.observation_epoch()
                started_at = HEALER.observation_started_at()
            self.assertFalse(epoch["epochFallback"])
            self.assertFalse(epoch["epochUnknown"])
            self.assertEqual(epoch["startedAt"], "2026-11-11T08:46:25Z")
            self.assertEqual(started_at, "2026-11-11T08:46:25Z")


if __name__ == "__main__":
    unittest.main()
