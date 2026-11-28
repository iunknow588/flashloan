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
        self.assertTrue(evidence["runArbitrage"]["executable"])
        self.assertTrue(evidence["staticPreview"]["historicalEvidence"])
        self.assertFalse(evidence["staticPreview"]["automaticBroadcastEligible"])
        self.assertEqual(HEALER.review_loop_key(self.review_row()), "0xa:0xb:0xc")

    def test_review_evidence_copies_the_recorded_execution_verdict(self):
        """T-复编.01: the caller never declares the execution verdict.

        The verdict belongs to the layer that executed the route.  The
        legacy row records the outcome of the attempt under its
        ``category`` column and the Review Manager row carries the
        kernel's own ``runArbitrage.executable`` flag, so the conversion
        copies the verdict it finds.

        A caller that writes its own verdict decides the two execution
        gates of the Review Manager by itself, and the gates then never
        run - which is how the review library came to hold 164 rows that
        no execution ever saw.
        """
        executed = HEALER.review_evidence(self.review_row(category="executable"))
        self.assertTrue(executed["runArbitrage"]["executable"])

        estimated = HEALER.review_evidence(self.review_row(category="not_executable"))
        self.assertFalse(estimated["runArbitrage"]["executable"])

        published = self.review_row(category="not_executable")
        published["runArbitrage"] = {"ok": True, "executable": True}
        self.assertTrue(HEALER.review_evidence(published)["runArbitrage"]["executable"])

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
        # The unit test must not depend on a live server: the runtime at
        # 127.0.0.1:5000 answers slowly while it measures a review round,
        # and the test would hang on it.  The group key is resolved from
        # the registered workers, and the empty list is the branch the
        # test means: no worker is registered for the key.
        with mock.patch.object(HEALER, "api_post", return_value=missing) as api_post, \
                mock.patch.object(HEALER, "registered_closed_loop_group_keys",
                                  return_value=[]):
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
        with mock.patch.object(HEALER, "api_post", side_effect=[missing, refreshed]) as api_post, \
                mock.patch.object(HEALER, "registered_closed_loop_group_keys",
                                  return_value=[]):
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
        candidates.assert_called_once_with(HEALER.DEFAULT_DB, 1, excluded, None)
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
             mock.patch.object(HEALER, "manager_loop_rows", return_value=loops):
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
        # The verdict no longer counts an **age**: it reads the judgement
        # the review layer publishes, and that judgement is made with the
        # moments of the closed loops -- the moment a loop is scheduled
        # at and the moment its round started.  A loop whose round started
        # after the measurement the store holds is not carrying its
        # current measurement, and no interval is involved anywhere.
        layers = {
            "tokens": {"valid": True},
            "pools": {"valid": True},
            "closedLoops": {"valid": True},
            "reviews": {"legacy": {"latestReviewAt": "2026-11-10T11:59:30Z",
                                     "stats": {"positive": 0},
                                     "currentLoopFreshness": {
                                         "basis": "loop_review_time_point",
                                         "checkedLoops": 2, "currentLoops": 2,
                                         "notCurrentLoops": [], "current": True}}},
            "broadcast": {"legacy": {"total": 0, "latestAttemptAt": None}},
        }
        status = HEALER.transaction_production_status(layers, "2026-11-10T12:00:00Z")
        self.assertEqual(status["state"], "market_no_executable_profit")
        self.assertTrue(status["healthy"])

        layers["reviews"]["legacy"]["currentLoopFreshness"] = {
            "basis": "loop_review_time_point", "checkedLoops": 2,
            "currentLoops": 1, "current": False,
            "notCurrentLoops": [{"loopKey": "loop-1",
                                  "reason": "round_not_measured"}]}
        status = HEALER.transaction_production_status(layers, "2026-11-10T12:00:00Z")
        self.assertEqual(status["state"], "review_stalled")
        self.assertFalse(status["healthy"])
        self.assertEqual(status["reviewFreshnessBasis"], "loop_review_time_point")
        self.assertEqual(status["reviewNotCurrentLoops"][0]["loopKey"], "loop-1")

    def test_production_status_flags_positive_review_without_transaction_record(self):
        layers = {
            "tokens": {"valid": True},
            "pools": {"valid": True},
            "closedLoops": {"valid": True},
            "reviews": {"legacy": {"latestReviewAt": "2026-11-10T11:59:30Z",
                                     "stats": {"positive": 1},
                                     "currentLoopFreshness": {
                                         "basis": "loop_review_time_point",
                                         "checkedLoops": 1, "currentLoops": 1,
                                         "notCurrentLoops": [], "current": True}}},
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
            published_loops = [{
                "loop_key": "0xa:0xb:0xc",
                "path_text": "SPY -> NVDA -> USDG -> SPY",
                "path_symbols_json": json.dumps(["SPY", "NVDA", "USDG", "SPY"]),
                "path_addresses_json": json.dumps(["0xa", "0xb", "0xc", "0xa"]),
            }]
            with mock.patch.object(HEALER, "observation_started_at", return_value="2026-11-10T00:00:00Z"), \
                    mock.patch.object(HEALER, "manager_loop_rows", return_value=published_loops):
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
    # A store that does not carry the candidate pools is a gap of the
    # store, not a failure of the reader. The healer used to raise, and
    # the raise ended the whole healing cycle: one absent table cost a
    # whole round of repairs.
    # ------------------------------------------------------------------
    def test_a_store_without_the_candidate_pools_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "pools.sqlite3"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "CREATE TABLE v4_exchange_pool_liquidity_snapshots (network TEXT, "
                    "chain_id INTEGER, protocol TEXT, pool_id TEXT, has_liquidity INTEGER, "
                    "liquidity_index INTEGER, depth_status TEXT, estimated_tvl_usd REAL, "
                    "capacity_binding_raw TEXT)"
                )
                connection.commit()
            finally:
                connection.close()

            depth = HEALER.pool_depth_audit(database)
            self.assertFalse(depth["valid"], depth)
            self.assertEqual(depth["code"], "candidate_pool_table_missing", depth)
            self.assertEqual(depth["repairPath"], "materialize_candidate_pools", depth)

            # The cascade reads the same table, and it reads it as an empty
            # pool set: the chain has no pool to build a closed loop from.
            matrix = HEALER.cascade_matrix(database, None)
            self.assertEqual(matrix["candidatePools"]["total"], 0, matrix)

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

    def test_a_complete_measurement_is_not_replaced_by_a_summary(self):
        """The runtime's measurement is the complete one.

        The runtime publishes every review with the amount ladder, the
        ceiling the ladder searched inside and the route it priced.  The
        orchestrator's manual review only carries a summary of the same
        measurement, because the record table holds no ladder.  A
        submission that prefers the summary replaces a complete measurement
        with a summary of it, and the review library loses the ceiling that
        says the amount was inside the bound the review enforces.
        """
        loop_key = '0x0000000000000000000000000000000000000000:0x11:0x22'
        complete = {
            'loopKey': loop_key,
            'staticPreview': {
                'source': 'native_rust_review',
                'searchCeilingRaw': '0x1',
                'searchCeilingBasis': 'single_tick_capacity',
                'bestAmountCalculation': {'bestAmountMeasured': True, 'probeCount': 6},
            },
            'runArbitrage': {'ok': False, 'executable': True,
                             'signedPnl': {'raw': '-1 USDG', 'amount': -1.0,
                                           'currency': 'USDG', 'unit': 'token',
                                           'source': 'runArbitrage'}},
        }
        summary = {
            'loopKey': loop_key,
            'staticPreview': {
                'bestAmountCalculation': {'bestAmountMeasured': True, 'probeCount': 6},
            },
            'runArbitrage': {'ok': False, 'executable': True,
                             'signedPnl': {'raw': '-2 USDG', 'amount': -2.0,
                                           'currency': 'USDG', 'unit': 'token',
                                           'source': 'runArbitrage'}},
        }
        with mock.patch.object(HEALER, 'stored_review_evidence', return_value=[complete]):
            with mock.patch.object(HEALER, 'manager_loop_rows', return_value=[]):
                merged, retired, kept = HEALER.merge_with_stored_evidence(
                    Path('unused.sqlite3'), None, [summary])
        self.assertEqual(len(merged), 1, merged)
        self.assertEqual(retired, 0)
        self.assertEqual(kept, [loop_key])
        # The stored measurement is the one that is carried: the summary
        # of it is not submitted.
        self.assertEqual(merged[0]['staticPreview']['searchCeilingRaw'], '0x1')

    def test_a_fresh_measurement_replaces_a_stored_summary(self):
        """A gap is still filled.

        The stored row cannot show the ceiling, so the fresh measurement
        that can is the one that is carried.
        """
        loop_key = '0x0000000000000000000000000000000000000000:0x11:0x22'
        stored = {
            'loopKey': loop_key,
            'staticPreview': {
                'bestAmountCalculation': {'bestAmountMeasured': True, 'probeCount': 6},
            },
            'runArbitrage': {'ok': False, 'executable': True,
                             'signedPnl': {'raw': '-1 USDG', 'amount': -1.0,
                                           'currency': 'USDG', 'unit': 'token',
                                           'source': 'runArbitrage'}},
        }
        fresh = {
            'loopKey': loop_key,
            'staticPreview': {
                'source': 'native_rust_review',
                'searchCeilingRaw': '0x1',
                'searchCeilingBasis': 'single_tick_capacity',
                'bestAmountCalculation': {'bestAmountMeasured': True, 'probeCount': 6},
            },
            'runArbitrage': {'ok': False, 'executable': True,
                             'signedPnl': {'raw': '-2 USDG', 'amount': -2.0,
                                           'currency': 'USDG', 'unit': 'token',
                                           'source': 'runArbitrage'}},
        }
        with mock.patch.object(HEALER, 'stored_review_evidence', return_value=[stored]):
            with mock.patch.object(HEALER, 'manager_loop_rows', return_value=[]):
                merged, retired, kept = HEALER.merge_with_stored_evidence(
                    Path('unused.sqlite3'), None, [fresh])
        self.assertEqual(len(merged), 1, merged)
        self.assertEqual(retired, 0)
        self.assertEqual(kept, [])
        self.assertEqual(merged[0]['staticPreview']['searchCeilingRaw'], '0x1')

    def test_replacement_report_survives_a_store_without_the_candidate_scope(self):
        """The candidate scope is rebuilt while the healer runs.

        The replacement report read ``dex_candidate_pools`` without a guard,
        so the table's absence for a moment ended the whole healing cycle.
        A store that carries no candidate pools is a gap of the store: the
        report says so, and the rest of the cycle still runs.
        """
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "pools.sqlite3"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "CREATE TABLE v4_exchange_pools (network TEXT, chain_id INTEGER, "
                    "protocol TEXT, pool_id TEXT, token0 TEXT, token1 TEXT, fee INTEGER, "
                    "dynamic_fee INTEGER, special_fee INTEGER, hooks TEXT)"
                )
                connection.execute(
                    "CREATE TABLE v4_exchange_pool_liquidity_snapshots (network TEXT, "
                    "chain_id INTEGER, protocol TEXT, pool_id TEXT, has_liquidity INTEGER, "
                    "liquidity_index INTEGER, depth_status TEXT, estimated_tvl_usd REAL, "
                    "capacity_binding_raw TEXT)"
                )
                connection.commit()
            finally:
                connection.close()

            report = HEALER.pool_depth_replacement_report(database)
            self.assertEqual(report["status"], "candidate_pool_table_missing", report)
            self.assertEqual(report["code"], "candidate_pool_table_missing", report)
            self.assertEqual(report["repairPath"], "materialize_candidate_pools", report)
            self.assertEqual(report["replacementKeyCount"], 0, report)
            self.assertEqual(report["keys"], [], report)

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

    def cascade_pool_database(self, directory: str, pools: list[str]) -> Path:
        """A store whose candidate scope publishes the given pool keys."""
        database = Path(directory) / "matrix.sqlite3"
        connection = sqlite3.connect(database)
        connection.execute(
            "CREATE TABLE dex_candidate_pools (network TEXT, chain_id INTEGER, "
            "pool_id TEXT, depth_status TEXT)")
        for pool_id in pools:
            connection.execute("INSERT INTO dex_candidate_pools VALUES (?,?,?,?)",
                               ("robinhood", 4663, pool_id, "deep"))
        connection.commit()
        connection.close()
        return database

    def composite_pool_id(self, scope: str, pool_key: str, rank: int) -> str:
        """The composite pool id the closed-loop Manager publishes."""
        return f"dpool:v1:robinhood:4663:{scope}:{pool_key}:{rank}"

    def test_the_cascade_matrix_measures_the_hops_of_the_closed_loops(self):
        """The closed-loop link is measured from the hop rows of the Manager.

        The Manager publishes the pool of a hop as a composite id and the
        candidate scope publishes the bare pool key.  The matrix has to
        reduce the two to the same thing -- otherwise every hop counts
        as "outside the candidate scope", the link reads "zero hops in
        scope", and a healthy closed loop is reported as broken.
        """
        pool_a = "0x" + "a" * 64
        pool_b = "0x" + "b" * 64
        with tempfile.TemporaryDirectory() as directory:
            database = self.cascade_pool_database(directory, [pool_a, pool_b])
            loops = [{"loopKey": "loop-a", "directionalPoolIds": [
                self.composite_pool_id("core4", pool_a, 1),
                self.composite_pool_id("candidate_exchange", pool_b, 0),
                self.composite_pool_id("core4", pool_a, 0)]}]
            hops = [{"loopKey": "loop-a", "hopIndex": index + 1,
                     "directionalPoolId": pool_id,
                     "directionalScopeKey": scope}
                    for index, (scope, pool_id) in enumerate([
                        ("core4", self.composite_pool_id("core4", pool_a, 1)),
                        ("candidate_exchange",
                         self.composite_pool_id("candidate_exchange", pool_b, 0)),
                        ("core4", self.composite_pool_id("core4", pool_a, 0))])]
            directional = self.directional_pools(
                core4=[self.composite_pool_id("core4", pool_a, 1),
                       self.composite_pool_id("core4", pool_a, 0)],
                candidate_exchange=[self.composite_pool_id("candidate_exchange", pool_b, 0)])
            with mock.patch.object(HEALER, "manager_read_closed_loops",
                                   return_value={"status": {}, "loops": loops,
                                                 "hops": hops, "optimizations": [],
                                                 "runtimeStates": []}), \
                    mock.patch.object(HEALER, "manager_read_directional_pools",
                                      return_value=directional):
                matrix = HEALER.cascade_matrix(database, "http://manager")
            closed = matrix["closedLoops"]
            self.assertEqual(closed["total"], 1, closed)
            self.assertEqual(closed["hopsTotal"], 3, closed)
            self.assertEqual(closed["hopsInCandidateScope"], 3, closed)
            self.assertEqual(closed["hopsMissingFromCandidateScope"], 0, closed)
            self.assertEqual(closed["exactThreeHops"], 1, closed)
            self.assertTrue(closed["allHopsInScope"], closed)
            self.assertTrue(closed["allExactThreeHops"], closed)
            self.assertEqual(closed["loopsWithMissingHop"], 0, closed)
            self.assertEqual(closed["hopScopeCounts"],
                             {"core4": 2, "candidate_exchange": 1}, closed)
            self.assertEqual(closed["hopsInOwnScope"], 3, closed)
            self.assertEqual(closed["hopsMissingFromOwnScope"], 0, closed)
            self.assertEqual(closed["directionalPoolScopeCounts"],
                             {"core4": 2, "candidate_exchange": 1}, closed)

    def directional_pools(self, **scopes: list[str]) -> dict[str, dict[str, object]]:
        """The published directional pools of every scope."""
        return {scope: {"rows": [{"directionalPoolId": pool_id}
                                  for pool_id in pool_ids]}
                for scope, pool_ids in scopes.items()}

    def test_a_closed_loop_without_hop_rows_is_not_reported_as_in_scope(self):
        """An unmeasured link may never read as a healthy one.

        A Manager that published no hop row leaves the link unmeasured.
        An empty hop set compares equal to itself, so the old reading
        reported "all hops in scope" for a link nobody measured.
        """
        with tempfile.TemporaryDirectory() as directory:
            database = self.cascade_pool_database(directory, ["0x" + "a" * 64])
            loops = [{"loopKey": "loop-a"}]
            with mock.patch.object(HEALER, "manager_read_closed_loops",
                                   return_value={"status": {}, "loops": loops,
                                                 "hops": [], "optimizations": [],
                                                 "runtimeStates": []}), \
                    mock.patch.object(HEALER, "manager_read_directional_pools",
                                      return_value={}):
                matrix = HEALER.cascade_matrix(database, "http://manager")
            closed = matrix["closedLoops"]
            self.assertEqual(closed["hopsTotal"], 0, closed)
            self.assertFalse(closed["allHopsInScope"], closed)
            self.assertFalse(closed["allExactThreeHops"], closed)
            self.assertEqual(closed["loopsWithoutHopRows"], 1, closed)
            self.assertEqual(closed["loopsWithMissingHop"], 1, closed)

    def test_a_hop_of_a_pool_outside_the_candidate_scope_is_counted(self):
        """A hop that names a pool the scope does not publish is a defect."""
        pool_a = "0x" + "a" * 64
        outside = "0x" + "c" * 64
        with tempfile.TemporaryDirectory() as directory:
            database = self.cascade_pool_database(directory, [pool_a])
            loops = [{"loopKey": "loop-a"}]
            hops = [{"loopKey": "loop-a", "hopIndex": 1,
                     "directionalPoolId": self.composite_pool_id("core4", pool_a, 1),
                     "directionalScopeKey": "core4"},
                    {"loopKey": "loop-a", "hopIndex": 2,
                     "directionalPoolId": self.composite_pool_id("core4", outside, 0),
                     "directionalScopeKey": "core4"}]
            directional = self.directional_pools(
                core4=[self.composite_pool_id("core4", pool_a, 1),
                       self.composite_pool_id("core4", outside, 0)])
            with mock.patch.object(HEALER, "manager_read_closed_loops",
                                   return_value={"status": {}, "loops": loops,
                                                 "hops": hops, "optimizations": [],
                                                 "runtimeStates": []}), \
                    mock.patch.object(HEALER, "manager_read_directional_pools",
                                      return_value=directional):
                matrix = HEALER.cascade_matrix(database, "http://manager")
            closed = matrix["closedLoops"]
            self.assertEqual(closed["hopsTotal"], 2, closed)
            self.assertEqual(closed["hopsInCandidateScope"], 1, closed)
            self.assertEqual(closed["hopsMissingFromCandidateScope"], 1, closed)
            self.assertEqual(closed["hopsInOwnScope"], 2, closed)
            self.assertEqual(closed["loopsWithMissingHop"], 1, closed)
            self.assertFalse(closed["allHopsInScope"], closed)

    def test_a_hop_of_a_pool_another_scope_publishes_is_not_in_its_own_scope(self):
        """A pool only another scope publishes is not a pool this hop may use.

        The candidate scope publishes the pool, so the union check
        passes -- but the hop declares ``core4`` and ``core4`` does not
        publish that pool.  The hop is outside its own scope, and the
        link may not read as healthy.
        """
        pool_a = "0x" + "a" * 64
        other = "0x" + "b" * 64
        with tempfile.TemporaryDirectory() as directory:
            database = self.cascade_pool_database(directory, [pool_a, other])
            loops = [{"loopKey": "loop-a"}]
            hops = [{"loopKey": "loop-a", "hopIndex": 1,
                     "directionalPoolId": self.composite_pool_id("core4", other, 0),
                     "directionalScopeKey": "core4"}]
            directional = self.directional_pools(
                core4=[self.composite_pool_id("core4", pool_a, 1)],
                candidate_exchange=[self.composite_pool_id("candidate_exchange", other, 0)])
            with mock.patch.object(HEALER, "manager_read_closed_loops",
                                   return_value={"status": {}, "loops": loops,
                                                 "hops": hops, "optimizations": [],
                                                 "runtimeStates": []}), \
                    mock.patch.object(HEALER, "manager_read_directional_pools",
                                      return_value=directional):
                matrix = HEALER.cascade_matrix(database, "http://manager")
            closed = matrix["closedLoops"]
            self.assertEqual(closed["hopsInCandidateScope"], 1, closed)
            self.assertEqual(closed["hopsInOwnScope"], 0, closed)
            self.assertEqual(closed["hopsMissingFromOwnScope"], 1, closed)
            self.assertEqual(closed["loopsWithMissingHop"], 1, closed)
            self.assertFalse(closed["allHopsInScope"], closed)

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
            # The production verdict publishes a **state** and a
            # **boolean**.  The state is one of ``market_no_executable_profit``
            # / ``transaction_records_present`` / ``review_stalled`` / ... and
            # is never the literal string ``healthy``.
            "transactionProduction": {
                "state": "transaction_records_present",
                "healthy": True,
                "reason": "profitable_reviews_and_transaction_records_exist",
            },
        }

    def test_a_production_state_that_never_occurs_cannot_satisfy_the_window(self):
        # The state string ``healthy`` is not a state the production verdict
        # ever publishes.  A window that compared the state to that string
        # could therefore never be satisfied, no matter what the chain did.
        report = self.healthy_report()
        report["transactionProduction"] = {"state": "healthy"}
        verdict = HEALER.StabilityTracker().observe(report)
        self.assertFalse(verdict["satisfied"])
        self.assertIn("productionHealthy", verdict["failedConditions"])

    def test_an_unhealthy_production_verdict_breaks_the_stability_window(self):
        report = self.healthy_report()
        report["transactionProduction"] = {"state": "market_no_executable_profit",
                                            "healthy": False}
        verdict = HEALER.StabilityTracker().observe(report)
        self.assertFalse(verdict["satisfied"])
        self.assertIn("productionHealthy", verdict["failedConditions"])

    def test_the_production_verdict_is_read_from_its_published_boolean(self):
        report = self.healthy_report()
        report["transactionProduction"] = {"state": "market_no_executable_profit",
                                            "healthy": True}
        verdict = HEALER.StabilityTracker().observe(report)
        self.assertTrue(verdict["satisfied"])
        self.assertEqual(verdict["consecutiveWindows"], 1)

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

    def published_manager(self, reviews=(), loops=(), broadcasts=None):
        """Stub the manager API reads of the manager-owned records.

        The **Review Manager** owns the review library and the
        **closed-loop Manager** owns the closed-loop catalog, so the healer
        reads them through the manager API and never selects from their
        tables. The fixture therefore publishes the records the way the
        manager API publishes them: as structured values inside the
        ``data`` member of the answer document.
        """
        review_rows = []
        for row in reviews:
            item = dict(row)
            signed = item.pop("signed_pnl", None) or {}
            item.setdefault("loopKey", row.get("loop_key", ""))
            item.setdefault("reviewedAt", row.get("created_at", ""))
            if signed:
                item["signedPnl"] = signed
            review_rows.append(item)
        loop_rows = []
        for row in loops:
            item = dict(row)
            item.setdefault("loopKey", row.get("loop_key", ""))
            item.setdefault("pathText", row.get("path_text"))
            item.setdefault("pathSymbols", json.loads(row.get("path_symbols_json") or "[]"))
            item.setdefault("pathAddresses", json.loads(row.get("path_addresses_json") or "[]"))
            item.setdefault("poolIds", json.loads(row.get("pool_ids_json") or "[]"))
            loop_rows.append(item)
        broadcast_doc = broadcasts or {"status": {}, "attempts": [], "diagnostics": []}
        return (
            mock.patch.object(HEALER, "manager_read_reviews", return_value=review_rows),
            mock.patch.object(HEALER, "manager_read_closed_loops", return_value={
                "status": {"loopCount": len(loop_rows)},
                "loops": loop_rows,
                "hops": [],
                "optimizations": [],
                "runtimeStates": [],
                "skippedTokens": [],
            }),
            mock.patch.object(HEALER, "manager_read_broadcasts", return_value=broadcast_doc),
        )

    def published_review_rows(self, database):
        """Return the fixture's review rows in the shape the API publishes.

        The fixture materializes the rows the Review Manager owns. The
        manager API publishes the nested blocks as structured values, so
        the fixture rows are converted here: the static preview and the
        arbitrage block become objects, and the signed profit becomes the
        ``signedPnl`` object the API publishes.
        """
        rows = []
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        try:
            for row in connection.execute(
                "SELECT * FROM manager_v4_reviews WHERE network=? AND chain_id=?",
                (HEALER.NETWORK, HEALER.CHAIN_ID),
            ):
                item = dict(row)
                for column, field in (("static_preview_json", "staticPreview"),
                                      ("run_arbitrage_json", "runArbitrage")):
                    try:
                        item[field] = json.loads(item.get(column) or "{}")
                    except (TypeError, ValueError, json.JSONDecodeError):
                        item[field] = {}
                item["signedPnl"] = {
                    "raw": item.get("signed_pnl_raw"),
                    "amount": item.get("signed_pnl_amount"),
                    "currency": item.get("signed_pnl_currency"),
                    "address": item.get("signed_pnl_address"),
                    "unit": item.get("signed_pnl_unit"),
                    "source": item.get("signed_pnl_source"),
                }
                rows.append(item)
        finally:
            connection.close()
        return rows

    def published_loop_rows(self, database):
        """Return the fixture's closed-loop rows in the API-published shape."""
        rows = []
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        try:
            for row in connection.execute(
                "SELECT * FROM manager_v4_closed_loops WHERE network=? AND chain_id=?",
                (HEALER.NETWORK, HEALER.CHAIN_ID),
            ):
                rows.append(dict(row))
        finally:
            connection.close()
        return rows

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
            reviews, loops, broadcasts = self.published_manager(
                reviews=self.published_review_rows(database),
                loops=self.published_loop_rows(database),
            )
            with reviews, loops, broadcasts, \
                    mock.patch.object(HEALER, "observation_started_at",
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
            reviews, loops, broadcasts = self.published_manager(
                reviews=self.published_review_rows(database),
                loops=self.published_loop_rows(database),
            )
            with reviews, loops, broadcasts, \
                    mock.patch.object(HEALER, "observation_started_at",
                                      return_value="2026-11-10T00:00:00Z"):
                report = HEALER.audit_reviews(database, 24)
            reasons = [item["reason"] for item in report["missingReasons"]]
            self.assertTrue(any("evidence_missing" != reason for reason in reasons),
                            f"the manager reason was lost: {reasons}")

    def test_the_review_freshness_is_read_from_the_published_measurement_moment(self):
        """The Review Manager publishes ``reviewedAt`` and no ``createdAt``.

        Reading ``created_at`` alone made every Manager review look
        infinitely old, so the production verdict reported
        ``review_stalled`` while the runtime was writing a fresh review
        every few seconds, and the operator was sent to repair a review
        layer that was already green.
        """
        published = [
            {"loopKey": "loop-1", "reviewedAt": "2026-11-10T12:00:00Z",
             "signedPnl": {"amount": "1.0", "currency": "ETH"}},
            {"loopKey": "loop-2", "reviewedAt": "2026-11-10T06:00:00Z",
             "signedPnl": {"amount": "2.0", "currency": "ETH"}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reviews.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE manager_v4_reviews (loop_key TEXT)")
            connection.execute("CREATE TABLE cow_execution_reviews (id INTEGER, "
                               "network TEXT, chain_id INTEGER, created_at TEXT, "
                               "payload_json TEXT, selected_profit_raw TEXT, "
                               "selected_profit_amount TEXT, "
                               "selected_profit_missing_reason TEXT)")
            connection.commit()
            connection.close()
            with mock.patch.object(HEALER.manager_record_reader, "read_reviews",
                                   return_value=published), \
                    mock.patch.object(HEALER, "manager_read_closed_loops",
                                      return_value={"loops": [{"loopKey": "loop-1"},
                                                               {"loopKey": "loop-2"}]}):
                report = HEALER.audit_reviews(database, 24,
                                             "http://127.0.0.1:8091")
        self.assertEqual(report["legacy"]["latestReviewAt"], "2026-11-10T12:00:00Z")
        self.assertEqual(report["legacy"]["currentLoopLatestReviewAt"],
                         "2026-11-10T12:00:00Z")

    def test_a_review_without_any_published_moment_stays_without_one(self):
        self.assertIsNone(HEALER.newest_review_stamp([{"loop_key": "loop-1"}]))
        self.assertIsNone(HEALER.newest_review_stamp([]))

    def test_the_cascade_judges_the_review_layer_by_the_current_catalog(self):
        """The layer verdict must agree with the review audit.

        The audit judges the live scope (every current loop carries a
        value).  The cascade used to read the whole window, so a route
        that left the catalog kept the layer red forever and the two
        verdicts contradicted each other.
        """
        report = {
            "layers": {
                "tokens": {"valid": True},
                "pools": {"valid": True},
                "closedLoops": {"valid": True},
                "reviews": {"valid": True,
                            "legacy": {"stats": {"missingValue": 19, "positive": 0},
                                       "currentLoopStats": {"missingValue": 0,
                                                            "positive": 0,
                                                            "total": 10}}},
                "broadcast": {"valid": False,
                              "outcomes": {"success": 0, "failed": 0}},
            },
            "blockers": ["broadcast"],
        }
        status = HEALER.cascade_layer_status(report)
        self.assertTrue(status["layers"]["reviews"])
        self.assertEqual(status["missingReviewValue"], 0)
        self.assertFalse(status["layers"]["broadcast"])

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
            reviews, loops, broadcasts = self.published_manager(
                reviews=self.published_review_rows(database),
                loops=self.published_loop_rows(database),
            )
            with reviews, loops, broadcasts, \
                    mock.patch.object(HEALER, "observation_started_at",
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
            reviews, loops, broadcasts = self.published_manager(
                reviews=self.published_review_rows(database),
                loops=self.published_loop_rows(database),
            )
            with reviews, loops, broadcasts, \
                    mock.patch.object(HEALER, "observation_started_at",
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

    # ---- TASK-158: the managers own the record reads ------------------------
    #
    # The **Review Manager** owns the review library, the **Broadcast
    # Manager** owns the broadcast attempts and the **closed-loop Manager**
    # owns the closed-loop catalog.  The healer therefore reads all three
    # through the manager API.  The identity of a review is published by
    # the Review Manager, so the healer only copies it and never rebuilds
    # it from the route addresses.

    def test_the_healer_reads_the_closed_loop_identity_the_kernel_published(self):
        identity = HEALER._closed_loop_identity_of([
            {
                "review": {
                    "closedLoopIdentityStatus": "matched",
                    "closedLoopPoolIdentityStatus": "matched",
                }
            }
        ])
        self.assertIsNotNone(identity)
        self.assertEqual(identity["closedLoopIdentityStatus"], "matched")
        self.assertEqual(identity["closedLoopPoolIdentityStatus"], "matched")

    def test_the_healer_reads_the_identity_diagnosis_of_a_broken_identity(self):
        identity = HEALER._closed_loop_identity_of([
            {
                "runArbitrage": {
                    "closedLoopIdentityStatus": "mismatch",
                    "identityMismatch": {"pool": "0xdeadbeef", "hop": 2},
                    "closedLoopRepairTarget": "manager_closed_loop_pool_identity_mismatch",
                }
            }
        ])
        self.assertIsNotNone(identity)
        self.assertEqual(identity["closedLoopIdentityStatus"], "mismatch")
        # The structured diagnosis is kept as a JSON string so the Review
        # Manager can store it without knowing the shape of the kernel's
        # diagnosis block.
        self.assertEqual(
            json.loads(identity["identityMismatch"]),
            {"pool": "0xdeadbeef", "hop": 2},
        )
        self.assertEqual(
            identity["closedLoopRepairTarget"], "manager_closed_loop_pool_identity_mismatch"
        )

    def test_a_review_without_an_identity_stays_without_one(self):
        # A payload that publishes no identity yields **no** identity.  The
        # absence must not be turned into a negative verdict.
        self.assertIsNone(HEALER._closed_loop_identity_of([{"review": {"ok": True}}]))
        self.assertIsNone(HEALER._closed_loop_identity_of([]))
        self.assertIsNone(
            HEALER._closed_loop_identity_of([{"closedLoopIdentityStatus": "   "}])
        )

    def test_the_manager_review_adapter_publishes_the_closed_loop_identity(self):
        published = [{
            "loopKey": "WETH->NVDA->USDG->WETH",
            "closedLoopIdentity": {
                "closedLoopIdentityStatus": "matched",
                "closedLoopPoolIdentityStatus": "matched",
                "identityMismatch": None,
                "closedLoopRepairTarget": None,
            },
        }]
        with mock.patch.object(
            HEALER.manager_record_reader,
            "read_reviews",
            return_value=published,
        ):
            rows = HEALER.manager_read_reviews("http://127.0.0.1:8091")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["closed_loop_identity_status"], "matched")
        self.assertEqual(row["closed_loop_pool_identity_status"], "matched")
        # A published ``null`` stays missing rather than becoming a default.
        self.assertIsNone(row.get("identity_mismatch"))
        self.assertIsNone(row.get("closed_loop_repair_target"))

    def test_a_manager_review_without_an_identity_has_no_identity_fields(self):
        with mock.patch.object(
            HEALER.manager_record_reader,
            "read_reviews",
            return_value=[{"loopKey": "ETH->AMD->ETH"}],
        ):
            rows = HEALER.manager_read_reviews("http://127.0.0.1:8091")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertNotIn("closed_loop_identity", row)
        self.assertNotIn("closed_loop_identity_status", row)
        self.assertNotIn("closed_loop_pool_identity_status", row)

    def test_the_stored_review_evidence_carries_the_published_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "store.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE cow_execution_reviews (id INTEGER PRIMARY KEY,network TEXT,"
                "chain_id INTEGER,created_at TEXT,payload_json TEXT,summary_json TEXT)"
            )
            connection.commit()
            connection.close()
            published = [{
                "loopKey": "WETH->NVDA->USDG->WETH",
                "reviewedAt": "2026-11-21T00:00:00Z",
                "runArbitrage": {
                    "ok": True,
                    "signedPnl": {
                        "raw": "0.5",
                        "amount": "0.5",
                        "currency": "WETH",
                        "address": "0x0bd7d308f8e1639fab988df18a8011f41eacad73",
                        "unit": "wei",
                        "source": "kernel",
                    },
                },
                "closedLoopIdentity": {
                    "closedLoopIdentityStatus": "matched",
                    "closedLoopPoolIdentityStatus": "matched",
                },
            }]
            with mock.patch.object(
                HEALER.manager_record_reader,
                "read_reviews",
                return_value=published,
            ):
                evidence = HEALER.stored_review_evidence(database, "http://127.0.0.1:8091")
        self.assertEqual(len(evidence), 1)
        self.assertIn("closedLoopIdentity", evidence[0])
        self.assertEqual(
            evidence[0]["closedLoopIdentity"]["closedLoopIdentityStatus"], "matched"
        )

    def test_the_broadcast_audit_counts_the_manager_published_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "store.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE cow_execution_attempts (id INTEGER PRIMARY KEY,network TEXT,"
                "chain_id INTEGER,attempt_key TEXT,category TEXT,tx_hash TEXT,created_at TEXT,"
                "selected_profit_amount TEXT,selected_profit_amount_symbol TEXT,summary_json TEXT)"
            )
            connection.execute(
                "CREATE TABLE cow_execution_reviews (id INTEGER PRIMARY KEY,network TEXT,"
                "chain_id INTEGER,created_at TEXT,category TEXT,selected_profit_amount TEXT)"
            )
            connection.commit()
            connection.close()
            broadcast_doc = {
                "status": {"attemptCount": 1, "successCount": 1, "pendingCount": 0,
                           "failedCount": 0},
                "attempts": [{
                    "attemptId": "attempt-1",
                    "status": "success",
                    "txHash": "0xabc",
                    "createdAt": "2026-11-21T00:00:00Z",
                    "signedPnl": {"amount": "1.5", "currency": "WETH"},
                }],
                "diagnostics": [],
            }
            with mock.patch.object(
                HEALER.manager_record_reader,
                "read_broadcasts",
                return_value=broadcast_doc,
            ):
                report = HEALER.audit_broadcast(database, 24, "http://127.0.0.1:8091")
        self.assertTrue(report["valid"])
        self.assertEqual(report["manager"]["attempts"], 1)
        self.assertEqual(report["manager"]["success"], 1)
        self.assertEqual(report["manager"]["source"], "broadcast_manager_published_records")
        self.assertEqual(len(report["legacy"]["recent"]), 1)
        self.assertTrue(report["legacy"]["recent"][0]["published_by_manager"])

    def test_the_healer_never_selects_from_a_manager_owned_table(self):
        # The managers own these tables.  A direct SELECT would bind the
        # healer to a layout it does not own, so the source must not name
        # them at all.
        source = MODULE_PATH.read_text(encoding="utf-8-sig")
        for table in (
            "manager_v4_reviews",
            "manager_v4_closed_loops",
            "manager_cow_execution_attempts",
            "manager_v4_review_broadcast_requests",
        ):
            self.assertNotIn(f"FROM {table}", source, table)
            self.assertNotIn(f"JOIN {table}", source, table)
            self.assertNotIn(f"INTO {table}", source, table)
            self.assertNotIn(f"UPDATE {table}", source, table)

    def test_a_readiness_timeout_means_busy_and_not_dead(self):
        # The runtime answered the liveness probe and did not answer the
        # heavier readiness probe in time.  That is a runtime that is busy
        # measuring a review round, and restarting it would throw the
        # round away -- which is what used to happen, so no review ever
        # landed and the self-healing never converged.
        with mock.patch.object(
            HEALER,
            "api_get",
            side_effect=[
                {"ok": True, "status": 200, "payload": {"status": "ok"}},
                {"ok": False, "status": 0, "error": "timed out", "kind": "timeout"},
            ],
        ):
            health = HEALER.runtime_business_health("http://127.0.0.1:5000")
        self.assertTrue(health["ok"], health)
        self.assertTrue(health["busy"], health)
        self.assertEqual(health["stage"], "busy")

    def test_a_readiness_failure_that_is_not_a_timeout_stays_unhealthy(self):
        # A busy runtime answers late; a broken one answers with an error.
        # Only the second one may be restarted.
        with mock.patch.object(
            HEALER,
            "api_get",
            side_effect=[
                {"ok": True, "status": 200, "payload": {"status": "ok"}},
                {"ok": False, "status": 500, "error": "boom", "kind": "http"},
            ],
        ):
            health = HEALER.runtime_business_health("http://127.0.0.1:5000")
        self.assertFalse(health["ok"], health)
        self.assertNotIn("busy", health)

    def test_the_historical_positives_come_from_the_manager_library(self):
        # The record table is append only and carries the payload text of
        # every row; a scan of it costs minutes and the runtime never
        # decides on it.  The Manager library publishes the same reviews as
        # in-memory objects, so the candidates are read from there and the
        # record table is not opened at all.
        loops = [{"loop_key": "loop-a", "path_text": "ETH -> A -> ETH",
                  "path_symbols_json": json.dumps(["ETH", "A", "ETH"]),
                  "path_addresses_json": json.dumps(["0x0", "0xa", "0x0"])}]
        reviews = [
            {"loop_key": "loop-a", "reviewed_at": "2026-11-26T10:00:00Z",
             "signed_pnl_amount": 1.25, "signed_pnl_currency": "ETH",
             "signed_pnl_raw": "0x1", "review_status": "completed"},
            # A loop that is not in the current catalog is not a candidate.
            {"loop_key": "loop-b", "reviewed_at": "2026-11-26T11:00:00Z",
             "signed_pnl_amount": 9.0, "signed_pnl_currency": "ETH",
             "signed_pnl_raw": "0x9", "review_status": "completed"},
            # The newest review of the loop is a loss, so the newest
            # **positive** review is the one that ranks the candidate.
            {"loop_key": "loop-a", "reviewed_at": "2026-11-26T12:00:00Z",
             "signed_pnl_amount": -3.0, "signed_pnl_currency": "ETH",
             "signed_pnl_raw": "-0x3", "review_status": "not_executable"},
        ]

        def forbidden(*args, **kwargs):
            raise AssertionError("the legacy record table was read")

        with mock.patch.object(HEALER, "manager_loop_rows", return_value=loops), \
                mock.patch.object(HEALER, "manager_review_rows", return_value=reviews), \
                mock.patch.object(HEALER, "connect", side_effect=forbidden):
            candidates = HEALER.historical_positive_candidates(
                Path("unused.sqlite3"), 5, None, "http://127.0.0.1:8091")
        self.assertEqual([item["loopKey"] for item in candidates], ["loop-a"])
        self.assertEqual(candidates[0]["historicalProfit"], 1.25)
        self.assertEqual(candidates[0]["historicalCurrency"], "ETH")
        self.assertEqual(candidates[0]["historicalReviewedAt"], "2026-11-26T10:00:00Z")
        self.assertEqual(candidates[0]["historicalReviewSource"], "manager_library")

    def test_the_record_table_is_the_fallback_when_no_manager_library_exists(self):
        # A store that has not materialized its reviews yet is still audited
        # from the record table, so the fallback has to keep working.
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE cow_execution_reviews (id INTEGER,attempt_key TEXT,"
                "network TEXT,chain_id INTEGER,market_id TEXT,pair TEXT,tx_hash TEXT,"
                "created_at TEXT,category TEXT,payload_json TEXT,summary_json TEXT,"
                "updated_at TEXT,selected_profit_raw TEXT,selected_profit_amount REAL,"
                "selected_profit_amount_symbol TEXT,selected_profit_source TEXT,"
                "selected_profit_missing_reason TEXT)"
            )
            connection.execute(
                "INSERT INTO cow_execution_reviews (network,chain_id,created_at,category,"
                "payload_json,selected_profit_amount,selected_profit_amount_symbol) "
                "VALUES ('robinhood',4663,'2026-11-26 10:00:00','closed_loop',?,2.5,'ETH')",
                (json.dumps({"loopKey": "loop-a"}),),
            )
            connection.execute(
                "INSERT INTO cow_execution_reviews (network,chain_id,created_at,category,"
                "payload_json,selected_profit_amount,selected_profit_amount_symbol) "
                "VALUES ('robinhood',4663,'2026-11-26 11:00:00','closed_loop',?,-1.0,'ETH')",
                (json.dumps({"loopKey": "loop-a"}),),
            )
            connection.commit()
            connection.close()
            loops = [{"loop_key": "loop-a", "path_text": "ETH -> A -> ETH",
                      "path_symbols_json": json.dumps(["ETH", "A", "ETH"]),
                      "path_addresses_json": json.dumps(["0x0", "0xa", "0x0"])}]
            with mock.patch.object(HEALER, "manager_loop_rows", return_value=loops), \
                    mock.patch.object(HEALER, "manager_review_rows", return_value=[]), \
                    mock.patch.object(HEALER, "observation_started_at",
                                      return_value="2000-01-01 00:00:00"):
                candidates = HEALER.historical_positive_candidates(
                    database, 5, None, None)
        self.assertEqual([item["loopKey"] for item in candidates], ["loop-a"])
        self.assertEqual(candidates[0]["historicalProfit"], 2.5)
        self.assertEqual(candidates[0]["historicalReviewSource"], "legacy_record_table")

    def test_a_stock_token_is_not_compared_against_the_funding_allowlist(self):
        # The chain's funding allowlist and its stock registry are two
        # different token universes.  NET and DJT live in both, and the two
        # registries name two different contracts for each of them -- the
        # allowlist's NET is a nine decimal token named "NetNet", the
        # registry's NET is Cloudflare's eighteen decimal stock token, and
        # both are real contracts.  The stock registry is the authority for
        # the stock library, so comparing a stock row against the funding
        # allowlist would report a mismatch that does not exist, and the
        # batch repair would swap a working equity token of the core
        # library for a different token that merely shares its ticker.
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tokens.sqlite3"
            connection = sqlite3.connect(database)
            for table in ("token_core", "token_stocks", "token_test"):
                connection.execute(
                    f"CREATE TABLE {table} (network TEXT,chain_id INTEGER,token_symbol TEXT,"
                    "token_address TEXT,token_decimals INTEGER,admission_status TEXT,"
                    "metadata_source TEXT,admission_source TEXT,source_json TEXT)"
                )
            official = HEALER.official_funding_token_addresses(4663)
            registry = HEALER.stock_registry_provenance(4663)
            # A ticker that both registries carry, with two addresses.
            shared = sorted(set(official) & set(registry))
            self.assertTrue(shared, "the fixtures need a ticker in both registries")
            symbol = shared[0]
            stock_entry = registry[symbol]
            connection.execute(
                "INSERT INTO token_stocks VALUES (?,?,?,?,?,'admitted',?,?,?)",
                ("robinhood", 4663, symbol, stock_entry["address"], 18,
                 stock_entry["metadataSource"], "bootstrap:stock_registry",
                 json.dumps({"seed": HEALER.STOCK_REGISTRY_SEED})),
            )
            connection.commit()
            connection.close()
            defects = HEALER.token_defects(database)
            kinds = {(item["kind"], item.get("symbol")) for item in defects}
            self.assertNotIn(("funding_address_mismatch", symbol), kinds, defects)

    def test_a_funding_token_under_a_wrong_address_is_still_a_defect(self):
        # Scoping the comparison must not soften the funding libraries: a
        # funding token that is admitted under the wrong address is still a
        # defect that the healer has to repair.
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tokens.sqlite3"
            connection = sqlite3.connect(database)
            for table in ("token_core", "token_stocks", "token_test"):
                connection.execute(
                    f"CREATE TABLE {table} (network TEXT,chain_id INTEGER,token_symbol TEXT,"
                    "token_address TEXT,token_decimals INTEGER,admission_status TEXT,"
                    "metadata_source TEXT,admission_source TEXT,source_json TEXT)"
                )
            official = HEALER.official_funding_token_addresses(4663)
            symbol = "USDG"
            self.assertIn(symbol, official)
            connection.execute(
                "INSERT INTO token_core VALUES (?,?,?,?,?,'admitted','x','y','{}')",
                ("robinhood", 4663, symbol, "0x00000000000000000000000000000000000000ff", 18),
            )
            connection.commit()
            connection.close()
            defects = HEALER.token_defects(database)
            mismatches = [item for item in defects
                          if item["kind"] == "funding_address_mismatch"]
            self.assertEqual(len(mismatches), 1, defects)
            self.assertEqual(mismatches[0]["symbol"], symbol)
            self.assertEqual(mismatches[0]["expectedAddress"], official[symbol])

    def test_a_profitable_review_counts_as_executable_by_its_own_verdict(self):
        # The count of the profitable routes that may be broadcast used to
        # be taken from the ``category`` column, and that column holds
        # nothing but ``not_executable`` in the current library.  The count
        # was therefore structurally zero, the production state could never
        # leave ``market_no_executable_profit``, and the broadcast gate
        # could never open -- whatever the market did.  The verdict belongs
        # to the reviewed row: a Manager row carries the kernel's own
        # ``runArbitrage.executable`` flag.
        profitable = {
            "loop_key": "loop-a",
            "review_status": "completed",
            "signed_pnl_amount": 2.5,
            "signed_pnl_currency": "ETH",
            "signed_pnl_raw": "0x1",
            "signed_pnl_source": "native_rust_ladder",
            "runArbitrage": {"ok": True, "executable": True,
                             "signedPnl": {"amount": 2.5, "raw": "0x1",
                                           "currency": "ETH"}},
        }
        not_executable = {
            "loop_key": "loop-b",
            "review_status": "not_executable",
            "signed_pnl_amount": 1.5,
            "signed_pnl_currency": "ETH",
            "signed_pnl_raw": "0x1",
            "signed_pnl_source": "native_rust_ladder",
            "runArbitrage": {"ok": False, "executable": False,
                             "signedPnl": {"amount": 1.5, "raw": "0x1",
                                           "currency": "ETH"}},
        }
        stats = HEALER.pnl_stats([profitable, not_executable])
        self.assertEqual(stats["positiveAmount"], 2, stats)
        self.assertEqual(stats["positive"], 1, stats)
        self.assertEqual(stats["confirmedSum"], 2.5, stats)
        # The row that says the route was not executable is not a candidate
        # for the broadcast, even when its number is positive.
        self.assertFalse(HEALER.review_row_execution_verdict(not_executable))
        self.assertTrue(HEALER.review_row_execution_verdict(profitable))

    def test_a_legacy_row_without_the_executable_category_is_not_positive(self):
        # The legacy table kept its own convention, and a row that says
        # nothing but ``not_executable`` is not a candidate either.
        row = {"category": "not_executable", "selected_profit_amount": 4.0,
               "selected_profit_amount_symbol": "ETH",
               "selected_profit_raw": "0x1"}
        self.assertFalse(HEALER.review_row_execution_verdict(row))
        stats = HEALER.pnl_stats([row])
        self.assertEqual(stats["positiveAmount"], 1, stats)
        self.assertEqual(stats["positive"], 0, stats)


    def test_the_schedule_of_a_loop_decides_when_it_is_measured_again(self):
        now = dt.datetime(2026, 11, 27, 9, 0, tzinfo=dt.timezone.utc)
        future = (now + dt.timedelta(minutes=10)).isoformat()
        past = (now - dt.timedelta(minutes=10)).isoformat()

        # A loop that waits for a review is due, whatever its clock says.
        self.assertTrue(HEALER.review_is_due(
            {"reviewRequired": True, "nextReviewAt": future}, now))
        # A loop that was never measured publishes no moment: it is due.
        self.assertTrue(HEALER.review_is_due(
            {"reviewRequired": False, "nextReviewAt": None}, now))
        # A loop whose moment is in the future waits for it.
        self.assertFalse(HEALER.review_is_due(
            {"reviewRequired": False, "nextReviewAt": future}, now))
        # A loop whose moment has passed is due again.
        self.assertTrue(HEALER.review_is_due(
            {"reviewRequired": False, "nextReviewAt": past}, now))

    def test_the_review_clock_of_a_loop_decides_when_it_is_due(self):
        now = dt.datetime(2026, 11, 27, 9, 0, tzinfo=dt.timezone.utc)
        future = (now + dt.timedelta(seconds=60)).isoformat()

        # A loop whose clock is running waits for it: the loop is not due
        # while the clock still has seconds left.
        self.assertFalse(HEALER.review_is_due(
            {"reviewRequired": False, "runtimeStatus": "cooldown",
             "reviewWaitSeconds": 42, "nextReviewAt": future}, now))
        # A clock that ran out leaves nothing to wait for: the loop is due.
        self.assertTrue(HEALER.review_is_due(
            {"reviewRequired": False, "runtimeStatus": "cooldown",
             "reviewWaitSeconds": 0, "nextReviewAt": future}, now))
        # A loop in the review state waits for its next measurement.
        self.assertTrue(HEALER.review_is_due(
            {"reviewRequired": False, "runtimeStatus": "ready_for_review",
             "reviewWaitSeconds": 0, "nextReviewAt": None}, now))

    def test_a_loop_whose_round_is_running_is_not_due(self):
        # The round of the review is running right now: the loop is being
        # measured, so it is not due and it is not measured twice.
        now = dt.datetime(2026, 11, 27, 9, 0, tzinfo=dt.timezone.utc)
        past = (now - dt.timedelta(seconds=30)).isoformat()
        self.assertFalse(HEALER.review_is_due(
            {"reviewRequired": False, "runtimeStatus": "review_pending",
             "reviewWaitSeconds": 0, "nextReviewAt": past}, now))

    def test_the_review_cycle_is_started_through_the_manager(self):
        posted: dict[str, object] = {}

        def fake_post(base, path, body):
            posted.update({"base": base, "path": path, "body": body})
            return {"ok": True, "status": 200, "body": json.dumps(
                {"ok": True, "data": {"claimed": True,
                                       "reviewCycleStartedAt": "2026-11-27T01:00:00+00:00"}})}

        with mock.patch.object(HEALER, "api_post", side_effect=fake_post):
            result = HEALER.begin_review_cycle("http://manager", "loop-a")
        self.assertTrue(result["claimed"])
        self.assertEqual(posted["path"], "/v1/closed-loops/review-cycle")
        self.assertEqual(posted["body"]["loopKey"], "loop-a")

    def test_the_due_loops_are_read_from_the_manager(self):
        def fake_get(base, path, timeout=5):
            self.assertIn("chainId=4663", path)
            return {"ok": True, "status": 200, "payload": {"ok": True, "data": {
                "dueCount": 2,
                "loops": [{"loopKey": "loop-a", "runtimeStatus": "ready_for_review",
                            "nextReviewAt": "2026-11-27T01:00:00+00:00",
                            "reviewWaitedSeconds": 12},
                           {"loopKey": "loop-b", "runtimeStatus": "cooldown",
                            "nextReviewAt": "2026-11-27T00:59:00+00:00",
                            "reviewWaitedSeconds": 73}]}}}

        with mock.patch.object(HEALER, "api_get", side_effect=fake_get):
            loops = HEALER.due_review_loops("http://manager")
        self.assertEqual([row["loopKey"] for row in loops], ["loop-a", "loop-b"])
        self.assertEqual(loops[1]["reviewWaitedSeconds"], 73)

    def test_the_review_round_claims_the_cycle_of_the_loop(self):
        # The round of the review starts with the request, so the cycle of
        # the loop starts with it as well.
        claimed: list[str] = []
        with mock.patch.object(HEALER, "begin_review_cycle",
                               side_effect=lambda api, key, chain_id=None:
                               claimed.append(key) or {"ok": True, "claimed": True}), \
                mock.patch.object(HEALER, "api_post", return_value={"ok": True, "status": 200,
                                                                  "body": "{}"}), \
                mock.patch.object(HEALER, "collect_completed_review", return_value=None):
            HEALER.request_live_review(
                Path("unused.sqlite3"), "http://runtime",
                {"loopKey": "loop-a", "routeSymbols": [], "routeAddresses": []},
                True, True, "http://manager")
        self.assertEqual(claimed, ["loop-a"])

    def test_the_orchestrator_never_reads_the_review_interval(self):
        """The review interval belongs to the helper that schedules a loop.

        The interval says how long a loop waits before it is measured
        again, and that number belongs to exactly one place: the helper
        that writes the moment a loop is scheduled at.  Every other place
        that used it was a logical error -- it counted the **time the
        review itself took**, which is not time the market moved, so a
        healthy loop was declared quiet and a positive review was refused
        as stale.

        The orchestrator therefore has no interval of its own: the
        freshness of a review is judged by the moments of its loop.
        """
        self.assertFalse(hasattr(HEALER, "REVIEW_FRESHNESS_SECONDS"),
                         "the orchestrator must not keep a window of seconds")
        self.assertFalse(hasattr(HEALER, "review_interval_seconds"),
                         "the orchestrator must not read the review interval")
        self.assertFalse(hasattr(HEALER, "review_is_stale"),
                         "the orchestrator must not judge a review by its age")
        source = Path(HEALER.__file__).read_text(encoding="utf-8")
        self.assertNotIn("CLOSED_LOOP_REVIEW_INTERVAL_SECONDS", source,
                         "the orchestrator must not read the interval parameter")

    def test_the_review_freshness_is_judged_by_the_moments_of_the_loop(self):
        """A loop is current when it carries the measurement of its round.

        The moments are the loop's own: the moment it is scheduled at and
        the moment its round started.  A loop that is busy -- it is being
        measured right now, or it waits for the receipt of its trade --
        is measured or has traded, so it is current.  A loop whose round
        started after the measurement the store holds waits for the
        measurement of that round, and a loop that publishes no review
        time point cannot be proven to carry its current measurement.
        """
        rows = [{"loop_key": "loop-1", "reviewed_at": "2026-11-10T12:00:00Z"},
                {"loop_key": "loop-2", "reviewed_at": "2026-11-10T12:00:00Z"},
                {"loop_key": "loop-3", "reviewed_at": "2026-11-10T12:00:00Z"}]
        schedules = {
            # Measured inside the round that is running: current.
            "loop-1": {"nextReviewAt": "2026-11-10T12:00:00Z",
                       "reviewCycleStartedAt": "2026-11-10T11:00:00Z"},
            # The round started after the measurement: the round is not
            # measured yet.
            "loop-2": {"nextReviewAt": "2026-11-10T12:00:00Z",
                       "reviewCycleStartedAt": "2026-11-10T13:00:00Z"},
            # Busy: it is being measured right now, so it is working.
            "loop-3": {"nextReviewAt": HEALER.REVIEW_PENDING_SENTINEL_AT},
            # No review time point at all: nothing to compare with.
            "loop-4": {},
            # A schedule, but no measurement was ever published.
            "loop-5": {"nextReviewAt": "2026-11-10T12:00:00Z"},
        }
        known = {"loop-1", "loop-2", "loop-3", "loop-4", "loop-5"}
        freshness = HEALER.review_freshness_by_loop(rows, known, schedules)
        self.assertEqual(freshness["basis"], "loop_review_time_point")
        self.assertFalse(freshness["current"])
        reasons = {item["loopKey"]: item["reason"]
                   for item in freshness["notCurrentLoops"]}
        self.assertEqual(reasons.get("loop-2"), "round_not_measured")
        self.assertEqual(reasons.get("loop-4"), "no_review_time_point")
        self.assertEqual(reasons.get("loop-5"), "never_measured")
        self.assertNotIn("loop-1", reasons, "a measured round is current")
        self.assertNotIn("loop-3", reasons, "a busy loop is working")
        self.assertEqual(freshness["checkedLoops"], 5)
        self.assertEqual(freshness["currentLoops"], 2)

    def test_the_missing_review_candidates_follow_the_published_schedule(self):
        loop = {"loop_key": "loop-a", "path_text": "ETH -> SPY -> ETH",
                "path_symbols_json": json.dumps(["ETH", "SPY", "ETH"]),
                "path_addresses_json": json.dumps(["0x1", "0x2", "0x1"])}
        now = dt.datetime.now(dt.timezone.utc)
        future = (now + dt.timedelta(minutes=10)).isoformat()
        past = (now - dt.timedelta(minutes=10)).isoformat()

        with mock.patch.object(HEALER, "review_rows", return_value=[]), \
                mock.patch.object(HEALER, "manager_loop_rows", return_value=[loop]), \
                mock.patch.object(HEALER, "manager_review_schedules",
                                  return_value={"loop-a": {"reviewRequired": False,
                                                 "nextReviewAt": future}}):
            self.assertEqual(HEALER.missing_review_candidates(
                Path("unused.sqlite3"), 10, None, "http://127.0.0.1:8091"), [])

        with mock.patch.object(HEALER, "review_rows", return_value=[]), \
                mock.patch.object(HEALER, "manager_loop_rows", return_value=[loop]), \
                mock.patch.object(HEALER, "manager_review_schedules",
                                  return_value={"loop-a": {"reviewRequired": False,
                                                 "nextReviewAt": past}}):
            candidates = HEALER.missing_review_candidates(
                Path("unused.sqlite3"), 10, None, "http://127.0.0.1:8091")
            self.assertEqual([row["loopKey"] for row in candidates], ["loop-a"])

        # A loop that publishes **no** schedule at all cannot prove that
        # the review the store holds is still the current one of the loop:
        # there is no moment to compare it with.  Such a loop is measured
        # again, and the gate that reads its review fails closed until
        # then.
        with mock.patch.object(HEALER, "review_rows", return_value=[]), \
                mock.patch.object(HEALER, "manager_loop_rows", return_value=[loop]), \
                mock.patch.object(HEALER, "manager_review_schedules", return_value={}):
            candidates = HEALER.missing_review_candidates(
                Path("unused.sqlite3"), 10, None, "http://127.0.0.1:8091")
            self.assertEqual([row["loopKey"] for row in candidates], ["loop-a"])


class MainnetArbitrageHealerCascadeTests(unittest.TestCase):
    """The five-stage cascade of the self-healing orchestrator.

    A crash of a cycle must say **where** it died, a manager that is busy
    must not be read as a manager that answered, and a queue that could not
    be read must not be read as a queue that is empty.
    """

    def test_a_cycle_that_died_carries_the_stack_that_says_where(self):
        try:
            raise sqlite3.OperationalError("no such table: dex_candidate_pools")
        except sqlite3.OperationalError as error:
            event = HEALER.cycle_error_event(7, error)
        self.assertEqual(event["event"], "healing_cycle_error")
        self.assertEqual(event["sample"], 7)
        self.assertEqual(event["errorType"], "OperationalError")
        self.assertEqual(event["error"], "no such table: dex_candidate_pools")
        # The stack is what turns a bare sentence into a located failure.
        self.assertIn("Traceback", event["traceback"])
        self.assertIn("test_a_cycle_that_died_carries_the_stack_that_says_where",
                      event["traceback"])

    def test_a_busy_manager_is_retried_instead_of_ending_the_round(self):
        answers = [
            {"ok": False, "status": 503, "body": "busy"},
            {"ok": False, "status": 503, "body": "busy"},
            {"ok": True, "status": 200, "body": '{"data": {"dueCount": 3}}'},
        ]
        with mock.patch.object(HEALER, "api_post", side_effect=answers) as api_post, \
                mock.patch.object(HEALER.time, "sleep") as sleep:
            response = HEALER.api_post_transient(
                "http://manager", "/v1/closed-loops/review-clock", {"chainId": 4663})
        self.assertTrue(response["ok"])
        self.assertEqual(response["attempts"], 3)
        self.assertEqual(api_post.call_count, 3)
        # The backoff grows, so a busy manager is not hammered.
        self.assertEqual([call.args[0] for call in sleep.call_args_list],
                         [HEALER.TRANSIENT_API_BACKOFF_SECONDS,
                          HEALER.TRANSIENT_API_BACKOFF_SECONDS * 2])

    def test_a_refusal_by_the_manager_is_not_retried(self):
        with mock.patch.object(HEALER, "api_post",
                               return_value={"ok": False, "status": 400,
                                              "body": "bad request"}) as api_post, \
                mock.patch.object(HEALER.time, "sleep"):
            response = HEALER.api_post_transient(
                "http://manager", "/v1/closed-loops/review-clock", {"chainId": 4663})
        self.assertFalse(response["ok"])
        self.assertEqual(response["attempts"], 1)
        self.assertEqual(api_post.call_count, 1)

    def test_a_queue_that_could_not_be_read_is_not_read_as_an_empty_queue(self):
        with mock.patch.object(HEALER, "api_get",
                               return_value={"ok": False, "status": None,
                                              "kind": "connect", "error": "down"}), \
                mock.patch.object(HEALER.time, "sleep"):
            queue = HEALER.due_review_queue("http://manager")
            loops = HEALER.due_review_loops("http://manager")
        # The empty list is the same, but the queue says it could not be read.
        self.assertEqual(loops, [])
        self.assertFalse(queue["ok"])
        self.assertEqual(queue["dueCount"], 0)

    def test_the_review_clock_reports_how_many_times_it_was_retried(self):
        with mock.patch.object(HEALER, "api_post",
                               side_effect=[{"ok": False, "status": 503, "body": "busy"},
                                            {"ok": True, "status": 200,
                                             "body": '{"data": {"dueCount": 5}}'}]), \
                mock.patch.object(HEALER.time, "sleep"):
            outcome = HEALER.advance_review_clocks("http://manager")
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["dueCount"], 5)
        self.assertEqual(outcome["attempts"], 2)

    def test_the_healer_names_no_load_mode_of_its_own(self):
        # The healer used to carry a hard-coded load mode of its own and
        # wrote it into the persisted scope on every repair.  That mode
        # was narrower than the libraries the scope declares, so the
        # rebuild deleted the pools of the libraries it did not name:
        # the stock pools left the scope, the closed-loop catalog lost
        # every route through them, and their reviews became
        # unpriceable without a single pool ever leaving the pool table.
        # The healer therefore has no mode of its own at all.
        self.assertIsNone(HEALER.AUDITED_SCOPE_TOKEN_LOAD_MODE)

    def test_the_expected_scope_mode_is_read_from_the_audit(self):
        baseline = {"pools": {
            "coreScopeExpectedLibraries": ["core", "stocks"],
            "candidateScopeExpectedLibraries": ["core", "stocks", "test"],
            "coreScopeConfiguredLibraries": ["core"],
            "candidateScopeConfiguredLibraries": ["core", "stocks"],
        }}
        self.assertEqual(HEALER.scope_expected_modes(baseline),
                         {"core4": "core_stocks",
                          "candidate_exchange": "core_stocks_test"})
        self.assertEqual(HEALER.scope_configured_libraries(baseline),
                         {"core4": ["core"],
                          "candidate_exchange": ["core", "stocks"]})

    def test_a_scope_that_lost_a_declared_library_is_widened(self):
        # The scope carries only the core library while it declares the
        # core and the stock libraries: it has lost the stock pools, and
        # the repair asks the manager for the **declared** mode, which is
        # wider than the current one.
        baseline = {"pools": {
            "coreScopeExpectedLibraries": ["core", "stocks"],
            "candidateScopeExpectedLibraries": ["core", "stocks"],
            "coreScopeConfiguredLibraries": ["core"],
            "candidateScopeConfiguredLibraries": ["core"],
        }}
        with mock.patch.object(HEALER, "api_post",
                               return_value={"ok": True, "status": 200,
                                             "body": "{}"}) as api_post:
            outcome = HEALER.repair_scope_policy(
                "http://manager", True,
                token_load_modes=HEALER.scope_expected_modes(baseline),
                configured_libraries=HEALER.scope_configured_libraries(baseline))
        self.assertEqual(api_post.call_count, 2)
        for call in api_post.call_args_list:
            body = call.args[2]
            self.assertEqual(body["tokenLoadMode"], "core_stocks")
            self.assertFalse(body["dryRun"])
        self.assertEqual(len(outcome["results"]), 2)

    def test_the_healer_never_asks_for_a_narrower_mode(self):
        # The scope carries the core, the stock and the test library while
        # it declares the core and the stock libraries.  The declared
        # mode would drop the test library, so it is **narrower** than the
        # scope: asking for it would delete the test pools from the
        # scope.  The healer skips the repair instead of narrowing the
        # scope, and says why.
        baseline = {"pools": {
            "coreScopeExpectedLibraries": ["core", "stocks"],
            "candidateScopeExpectedLibraries": ["core", "stocks"],
            "coreScopeConfiguredLibraries": ["core", "stocks", "test"],
            "candidateScopeConfiguredLibraries": ["core", "stocks", "test"],
        }}
        with mock.patch.object(HEALER, "api_post",
                               return_value={"ok": True, "status": 200,
                                             "body": "{}"}) as api_post:
            outcome = HEALER.repair_scope_policy(
                "http://manager", True,
                token_load_modes=HEALER.scope_expected_modes(baseline),
                configured_libraries=HEALER.scope_configured_libraries(baseline))
        self.assertEqual(api_post.call_count, 0)
        self.assertEqual(len(outcome["results"]), 2)
        for result in outcome["results"]:
            self.assertIsNone(result["tokenLoadMode"])
            self.assertIn("skipped", result)

    def test_a_narrowing_mode_is_recognised_before_it_is_asked_for(self):
        self.assertTrue(HEALER.scope_mode_narrows(["core", "stocks", "test"],
                                                  ["core", "stocks"]))
        self.assertFalse(HEALER.scope_mode_narrows(["core"], ["core"]))
        # Widening is not narrowing: adding a library only adds pools.
        self.assertFalse(HEALER.scope_mode_narrows(["core"],
                                                   ["core", "stocks"]))

    # ------------------------------------------------------------------
    # The tick amount against the single-tick start
    # ------------------------------------------------------------------

    def _tick_review(self, best, initial, probes):
        """Build a review row that carries a measured amount ladder."""
        calculation = {
            "method": "native_rust_review_ladder",
            "bestAmountRaw": best,
            "initialAmountRaw": initial,
            "probeRecords": [{"amountRaw": amount} for amount in probes],
            "probeCount": len(probes),
            "bestAmountLadderStatus": "not_profitable_after_costs",
        }
        return {
            "loop_key": "loop-1",
            "reviewed_at": "2026-11-27T23:10:49Z",
            "signed_pnl_raw": "-0x1",
            "signed_pnl_amount": -0.5,
            "signed_pnl_currency": "ETH",
            "static_preview_json": json.dumps({
                "bestAmountCalculation": calculation,
                "measuredAt": "2026-11-27T23:10:49Z",
            }),
        }

    def test_the_tick_amount_is_compared_with_the_single_tick_start(self):
        """A negative expectation raises the question of the tick amount.

        The kernel measures a route with two searches: the single-tick
        search starts at the amount the single-tick capacity produces, and
        the cross-tick search continues from that optimum.  A route whose
        expected profit is not positive therefore raises a question the two
        searches alone do not answer: **was the tick-computed best amount
        ever above the single-tick start?**
        """
        bounds = {"singleTickCapacityRaw": "0xc8", "crossTickCapacityRaw": "0x12c"}
        below = HEALER.tick_amount_comparison(
            self._tick_review("0x0", "0x64", ["0x64", "0xc8"]), bounds)
        self.assertFalse(below["bestGreaterThanInitial"],
                         "a best amount of zero is not above the start")
        self.assertEqual(below["verdict"],
                         "best_not_above_start_executable_domain_covered")
        self.assertFalse(below["gap"],
                         "the ladder covered the executable domain")
        self.assertEqual(below["expectedProfit"], -0.5)
        self.assertTrue(below["crossTickCeilingAboveExecutable"],
                        "the depth ceiling is above the executable ceiling")

        above = HEALER.tick_amount_comparison(
            self._tick_review("0x12c", "0x64", ["0x64", "0xc8", "0x12c"]), bounds)
        self.assertTrue(above["bestGreaterThanInitial"],
                        "a best amount above the start is the answer the user asked for")
        self.assertEqual(above["verdict"], "best_above_single_tick_start")

    def test_an_executable_size_that_was_never_searched_is_a_gap(self):
        """A ladder that stopped below the executable ceiling is a gap.

        The ladder climbs from its start by doubling, so the size above the
        start it probes is twice that start.  A ceiling that the doubling
        never reaches leaves an executable size unmeasured, and a route
        that never searched it may not be called unprofitable.
        """
        bounds = {"singleTickCapacityRaw": "0x3e8", "crossTickCapacityRaw": "0x1388"}
        comparison = HEALER.tick_amount_comparison(
            self._tick_review("0x0", "0x64", ["0x64"]), bounds)
        self.assertTrue(comparison["gap"])
        self.assertEqual(comparison["verdict"],
                         "best_not_above_start_and_executable_size_unsearched")
        self.assertEqual(comparison["executableCeilingShortfallRaw"], 1000 - 200)

    def test_an_unknown_executable_ceiling_is_not_a_covered_domain(self):
        """A ceiling that is not published cannot be said to be covered."""
        comparison = HEALER.tick_amount_comparison(
            self._tick_review("0x0", "0x64", ["0x64"]), {})
        self.assertEqual(comparison["verdict"],
                         "best_not_above_start_executable_domain_unknown")
        self.assertFalse(comparison["gap"])

    # ------------------------------------------------------------------
    # The upstream generation of a review
    # ------------------------------------------------------------------

    GENERATION = {
        "closedLoopVersion": 72,
        "closedLoopChecksum": "sha256:closed",
        "candidateEdgeVersion": 17,
        "candidateEdgeChecksum": "sha256:edge",
        "observationVersion": 20,
        "observationChecksum": "sha256:observation",
        "poolScopeChecksum": "sha256:scope",
    }

    def _generation_row(self, **overrides):
        row = {
            "closed_loop_version": 72,
            "closed_loop_checksum": "sha256:closed",
            "candidate_edge_version": 17,
            "candidate_edge_checksum": "sha256:edge",
            "observation_version": 20,
            "observation_checksum": "sha256:observation",
            "pool_scope_checksum": "sha256:scope",
        }
        row.update(overrides)
        return row

    def test_a_review_of_an_older_generation_is_not_the_measurement_of_the_loop(self):
        """The loop key alone does not make a review the current one.

        The Review Manager stamps every review with the upstream
        generation it was materialized under.  A review of a loop that
        still exists but carries an **older** generation describes a route
        that was measured before the library was rebuilt, so it cannot
        prove anything about the route now.
        """
        current = HEALER.review_generation_status(self._generation_row(),
                                                   self.GENERATION)
        self.assertEqual(current["state"], "current")

        stale = HEALER.review_generation_status(
            self._generation_row(closed_loop_version=54,
                                 pool_scope_checksum="sha256:old-scope"),
            self.GENERATION)
        self.assertEqual(stale["state"], "stale")
        fields = {item["field"] for item in stale["mismatches"]}
        self.assertEqual(fields, {"closedLoopVersion", "poolScopeChecksum"})

        unknown = HEALER.review_generation_status({"loop_key": "loop-1"},
                                                   self.GENERATION)
        self.assertEqual(unknown["state"], "unknown")

        # A manager that publishes no generation cannot judge a review.
        self.assertEqual(
            HEALER.review_generation_status(self._generation_row(), {})["state"],
            "unknown")

    def test_a_stored_review_keeps_the_generation_it_was_measured_under(self):
        """A stored review must not be relabelled with the current generation.

        The Review Manager files a review under the generation its evidence
        names and falls back to the generation it reads when the evidence
        names none.  A stored review that names no generation was
        therefore relabelled with whatever generation happened to be
        current when it was written back, so an old measurement started to
        look like a fresh one.
        """
        published = [{
            "loopKey": "loop-1",
            "closedLoopVersion": 54,
            "closedLoopChecksum": "sha256:old",
            "candidateEdgeVersion": 11,
            "candidateEdgeChecksum": "sha256:old-edge",
            "observationVersion": 14,
            "observationChecksum": "sha256:old-observation",
            "poolScopeChecksum": "sha256:old-scope",
            "reviewedAt": "2026-11-26T19:29:00Z",
            "runArbitrage": {"ok": False, "executable": True,
                             "diagnostics": [],
                             "signedPnl": {"raw": "-0x1", "amount": -0.5,
                                           "currency": "ETH", "address": "0x0",
                                           "unit": "raw", "source": "native_rust_ladder"}},
        }]
        with mock.patch.object(HEALER.manager_record_reader, "read_reviews",
                               return_value=published):
            stored = HEALER.stored_review_evidence(HEALER.DEFAULT_DB,
                                                   "http://127.0.0.1:8091")
        self.assertEqual(len(stored), 1)
        generation = stored[0]["closedLoopGeneration"]
        self.assertEqual(generation["closedLoopVersion"], 54)
        self.assertEqual(generation["poolScopeChecksum"] if "poolScopeChecksum" in generation
                         else generation.get("closedLoopChecksum"), "sha256:old")

    def test_a_live_loop_without_a_current_review_is_not_measured(self):
        """The review layer is not valid while a live loop is unmeasured.

        A live loop whose only review was measured against an older
        structure has no measurement of the current structure at all, so
        the layer cannot be valid and the healer has to measure the loop
        again.
        """
        published = [
            {"loopKey": "loop-1", "reviewedAt": "2026-11-27T23:10:49Z",
             "closedLoopVersion": 72, "closedLoopChecksum": "sha256:closed",
             "candidateEdgeVersion": 17, "candidateEdgeChecksum": "sha256:edge",
             "observationVersion": 20, "observationChecksum": "sha256:observation",
             "poolScopeChecksum": "sha256:scope",
             "signedPnl": {"amount": -0.5, "currency": "ETH"}},
            {"loopKey": "loop-2", "reviewedAt": "2026-11-26T19:29:00Z",
             "closedLoopVersion": 54, "closedLoopChecksum": "sha256:old",
             "candidateEdgeVersion": 11, "candidateEdgeChecksum": "sha256:old-edge",
             "observationVersion": 14, "observationChecksum": "sha256:old-observation",
             "poolScopeChecksum": "sha256:old-scope",
             "signedPnl": {"amount": -0.5, "currency": "ETH"}},
        ]
        status = {
            "closedLoopVersion": 72, "materializedChecksum": "sha256:closed",
            "candidateEdgeVersion": 17, "candidateEdgeChecksum": "sha256:edge",
            "observationVersion": 20, "observationChecksum": "sha256:observation",
            "poolScopeChecksum": "sha256:scope",
        }
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reviews.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE manager_v4_reviews (loop_key TEXT)")
            connection.execute("CREATE TABLE cow_execution_reviews (id INTEGER, "
                               "network TEXT, chain_id INTEGER, created_at TEXT, "
                               "payload_json TEXT, selected_profit_raw TEXT, "
                               "selected_profit_amount TEXT, "
                               "selected_profit_missing_reason TEXT)")
            connection.commit()
            connection.close()
            with mock.patch.object(HEALER.manager_record_reader, "read_reviews",
                                   return_value=published), \
                    mock.patch.object(HEALER, "manager_read_closed_loops",
                                      return_value={"status": status,
                                                     "loops": [{"loopKey": "loop-1"},
                                                               {"loopKey": "loop-2"}]}), \
                    mock.patch.object(HEALER, "manager_review_schedules",
                                      return_value={
                                          "loop-1": {"nextReviewAt": "2026-11-27T23:20:49Z"},
                                          "loop-2": {"nextReviewAt": "2026-11-27T23:20:49Z"},
                                      }):
                report = HEALER.audit_reviews(database, 24,
                                             "http://127.0.0.1:8091")
        generation = report["generation"]
        self.assertTrue(generation["readable"])
        self.assertEqual(generation["currentGeneration"], 1)
        self.assertEqual(generation["staleGeneration"], 1)
        self.assertEqual(generation["loopsWithoutCurrentReview"], 1)
        self.assertFalse(report["valid"],
                         "a live loop without a current measurement is a gap")
        self.assertEqual(report["legacy"]["currentLoopStats"]["missingCurrentMeasurement"], 1)
        reasons = {item["loopKey"]: item["reason"]
                   for item in report["legacy"]["currentLoopFreshness"]["notCurrentLoops"]}
        self.assertEqual(reasons.get("loop-2"), "measured_in_older_generation")


if __name__ == "__main__":
    unittest.main()
