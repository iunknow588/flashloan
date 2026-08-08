from web import control_panel
from page_state import ExecutionStatus, PAGE_STATE_STORE, PageName, PageStateStore, execution_state_payload, store_page_state
from datetime import datetime, timezone
from types import SimpleNamespace


def test_debt_pool_state_emits_market_alert_once(monkeypatch, tmp_path):
    from web import control_panel
    from market_events import store as market_volatility_event_store

    PAGE_STATE_STORE._states.clear()
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    monkeypatch.setattr(market_volatility_event_store, "MARKET_VOLATILITY_EVENT_STORE_PATH", tmp_path / "market_events.jsonl")
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "load_liquidation_account_registry", lambda force=False: (["0x1"], "database"))
    monkeypatch.setattr(
        control_panel,
        "liquidation_account_registry_window",
        lambda: {"total_count": 1, "active_count": 1, "latest_scan_end_at": "2026-08-02T00:00:00+00:00"},
    )
    monkeypatch.setattr(
        control_panel,
        "latest_binance_extremes_file",
        lambda: {
            "observed_at": observed_at,
            "window_seconds": 1.0,
            "sample_count": 12,
            "active_sample_count": 6,
            "gainer_count": 3,
            "loser_count": 3,
            "market_divergence_index": 2.0,
            "min_change_percent": 0.3,
            "top": [{"symbol": "AVAXUSDT", "change_percent": 3.2}],
            "bottom": [{"symbol": "BTCUSDT", "change_percent": -2.4}],
        },
    )
    control_panel.LIQUIDATION_SCAN_CACHE.update({"running": False, "stage": "idle", "last_result": {}})

    client = control_panel.app.test_client()
    first = client.get("/api/debt-pool/state").get_json()
    PAGE_STATE_STORE._states.clear()
    second = client.get("/api/debt-pool/state").get_json()

    assert first["status"] == "MARKET_ALERT_RECEIVED"
    assert first["source_event_id"] == first["context"]["market_event"]["event_id"]
    assert first["context"]["route_intent"]["target_page"] == "debt_pool"
    assert first["context"]["market_event"]["consumed_at"]
    assert second["status"] == "IDLE_FRESH"
    assert second["context"]["market_event"]["store_status"] == "consumed"
    assert second["context"]["route_intent"] is None


def test_execution_state_payload_reflects_recorded_state():
    PAGE_STATE_STORE._states.clear()
    store_page_state(
        PageName.EXECUTION,
        ExecutionStatus.READY_TO_SUBMIT.value,
        message="ready",
        context={"tx_hash": "0xabc"},
    )

    payload = execution_state_payload(control_panel)

    assert payload["status"] == "READY_TO_SUBMIT"
    assert payload["message"] == "ready"
    assert payload["context"]["tx_hash"] == "0xabc"


def test_page_state_store_syncs_to_database_parameter_map(monkeypatch):
    from page_state import store as state_store

    database_url = "postgresql://example"
    stored = {}

    def fake_load(database_url_arg):
        return dict(stored)

    def fake_save(database_url_arg, values):
        stored.clear()
        stored.update(values)
        return dict(values)

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr(state_store, "load_page_state_parameter_map", fake_load)
    monkeypatch.setattr(state_store, "save_page_state_parameter_map", fake_save)

    store = PageStateStore()
    state = store.set(
        PageName.EXECUTION.value,
        ExecutionStatus.READY_TO_SUBMIT.value,
        message="ready",
        context={"tx_hash": "0xabc"},
    )

    assert stored[PageName.EXECUTION.value]["status"] == ExecutionStatus.READY_TO_SUBMIT.value
    assert state.status == ExecutionStatus.READY_TO_SUBMIT.value
    loaded = PageStateStore().get(PageName.EXECUTION.value, ExecutionStatus.IDLE.value)
    assert loaded.status == ExecutionStatus.READY_TO_SUBMIT.value


def test_execution_state_progress_can_be_recorded(monkeypatch):
    from web import control_panel_liquidation_execute as execute

    PAGE_STATE_STORE._states.clear()
    monkeypatch.setattr(execute, "liquidation_executor_address", lambda: "0x0000000000000000000000000000000000000001")
    monkeypatch.setattr(execute, "liquidation_account_payload", lambda account: {"account": account, "summary": {"status": "liquidatable"}})
    monkeypatch.setattr(
        execute,
        "build_liquidation_execution_payload",
        lambda report, **kwargs: {
            "executor": kwargs["executor_address"],
            "request": {"debtToCover": 1, "minProfitAmount": 1},
            "preflight": {},
            "account_report": report,
        },
    )
    monkeypatch.setattr(execute, "liquidation_execution_controls", lambda: {"require_static_call": True, "execution_enabled": True, "slippage_bps": 50, "max_debt_to_cover": 0, "min_profit_base": 0})
    monkeypatch.setattr(execute, "apply_liquidation_submission_state", lambda payload, mode="flashloan": {**payload, "submission_allowed": False, "block_level": "none", "blocked_reasons": [], "force_allowed": False, "state": "submission_blocked"})
    monkeypatch.setattr(execute, "dex_router_address", lambda: "0x0000000000000000000000000000000000000002")

    try:
        execute.liquidation_execution_payload_for_account("0xabc")
    except Exception:
        pass

    payload = execution_state_payload(control_panel)
    assert payload["status"] in {"READY_TO_SUBMIT", "READY_FOR_PREFLIGHT", "SOFT_BLOCKED", "HARD_BLOCKED"}
    assert payload["context"].get("phase") in {"ready_for_preflight", "building_payload", "building_quote", "building_prediction", "loading_account"}


def test_simulate_liquidation_static_call_records_ready_phase(monkeypatch):
    from web import control_panel_liquidation_execute as execute

    PAGE_STATE_STORE._states.clear()
    monkeypatch.setattr(execute, "liquidation_executor_owner_address", lambda: "0x0000000000000000000000000000000000000001")
    monkeypatch.setattr(execute, "scan_context_assets", lambda: ("http://example.invalid", None, None))
    monkeypatch.setattr(
        execute,
        "simulate_request_liquidation_static_call",
        lambda *args, **kwargs: {
            "status": "passed",
            "error": None,
            "parsed": {},
            "simulated_at": "2026-08-02T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        execute,
        "apply_liquidation_submission_state",
        lambda payload, mode="flashloan": {
            **payload,
            "submission_allowed": True,
            "block_level": "none",
            "blocked_reasons": [],
            "state": "submission_ready",
        },
    )

    result = execute.simulate_liquidation_static_call(
        {
            "executor": "0x0000000000000000000000000000000000000002",
            "request": {"user": "0x0000000000000000000000000000000000000003"},
            "preflight": {},
        }
    )

    payload = execution_state_payload(control_panel)
    assert result["execution_phase"] == "ready_to_submit"
    assert payload["status"] == "READY_TO_SUBMIT"
    assert payload["context"]["phase"] == "ready_to_submit"


def test_simulate_liquidation_static_call_records_error_phase(monkeypatch):
    from web import control_panel_liquidation_execute as execute

    PAGE_STATE_STORE._states.clear()
    monkeypatch.setattr(execute, "liquidation_executor_owner_address", lambda: "")

    try:
        execute.simulate_liquidation_static_call(
            {
                "executor": "0x0000000000000000000000000000000000000002",
                "request": {"user": "0x0000000000000000000000000000000000000003"},
                "preflight": {},
            }
        )
    except Exception:
        pass

    payload = execution_state_payload(control_panel)
    assert payload["status"] == "ERROR"
    assert payload["last_error"] == "missing LIQUIDATION_EXECUTOR_OWNER_ADDRESS"
    assert payload["context"]["phase"] == "preflighting"


def test_liquidation_execution_payload_missing_executor_records_error(monkeypatch):
    from web import control_panel_liquidation_execute as execute

    PAGE_STATE_STORE._states.clear()
    monkeypatch.setattr(execute, "liquidation_executor_address", lambda: "")

    try:
        execute.liquidation_execution_payload_for_account("0xabc")
    except Exception:
        pass

    payload = execution_state_payload(control_panel)
    assert payload["status"] == "ERROR"
    assert payload["last_error"] == "missing LIQUIDATION_EXECUTOR_ADDRESS"
    assert payload["context"]["phase"] == "context_received"


def _execution_controls(**overrides):
    controls = {
        "execution_enabled": True,
        "require_static_call": False,
        "flashloan_executor_configured": True,
        "owner_configured": True,
        "self_funded_ready": True,
        "max_debt_to_cover": 0,
        "min_profit_base": 0,
        "max_payload_age_seconds": 30,
        "max_quote_age_seconds": 15,
        "priority_fee_gwei": 0,
        "tx_timeout_seconds": 30,
    }
    controls.update(overrides)
    return controls


def _executable_payload(**overrides):
    payload = {
        "account": "0x0000000000000000000000000000000000000001",
        "executor": "0x0000000000000000000000000000000000000002",
        "request": {
            "user": "0x0000000000000000000000000000000000000001",
            "collateralAsset": "0x0000000000000000000000000000000000000003",
            "debtAsset": "0x0000000000000000000000000000000000000004",
            "debtToCover": "1000",
            "minCollateralSwapOut": "1",
            "minProfitAmount": "1",
            "deadline": "9999999999",
            "gasLimit": "0",
            "swapPath": [],
        },
        "preflight": {"static_call_passed": True, "static_call_status": "passed"},
        "account_report": {"summary": {"status": "liquidatable"}},
        "dex_quote": {},
    }
    payload.update(overrides)
    return payload


def test_flashloan_missing_private_key_records_submission_error_phase(monkeypatch):
    from web import control_panel_liquidation_execute as execute

    PAGE_STATE_STORE._states.clear()
    payload = _executable_payload()
    monkeypatch.setattr(execute, "liquidation_execution_controls", lambda: _execution_controls())
    monkeypatch.setattr(execute, "liquidation_executor_private_key", lambda: "")

    try:
        execute.execute_flashloan_liquidation_transaction(payload)
    except RuntimeError:
        pass

    state = execution_state_payload(control_panel)
    assert state["status"] == "ERROR"
    assert state["last_error"] == "missing LIQUIDATION_EXECUTION_PRIVATE_KEY"
    assert state["context"]["phase"] == "missing_private_key"
    assert payload["execution_phase"] == "missing_private_key"


def test_flashloan_pre_submit_hard_gate_errors_record_distinct_phases(monkeypatch):
    from web import control_panel_liquidation_execute as execute

    class FakeEth:
        chain_id = 1

    class FakeWeb3:
        eth = FakeEth()

        def __init__(self, provider):
            pass

        @staticmethod
        def HTTPProvider(url, request_kwargs=None):
            return object()

    monkeypatch.setattr(execute, "liquidation_execution_controls", lambda: _execution_controls())
    monkeypatch.setattr(execute, "liquidation_executor_private_key", lambda: "0x" + "1" * 64)

    PAGE_STATE_STORE._states.clear()
    missing_owner_payload = _executable_payload()
    monkeypatch.setattr(execute, "liquidation_executor_owner_address", lambda: "")
    try:
        execute.execute_flashloan_liquidation_transaction(missing_owner_payload)
    except RuntimeError:
        pass
    state = execution_state_payload(control_panel)
    assert state["last_error"] == "missing LIQUIDATION_EXECUTOR_OWNER_ADDRESS"
    assert state["context"]["phase"] == "missing_owner"
    assert missing_owner_payload["execution_phase"] == "missing_owner"

    PAGE_STATE_STORE._states.clear()
    missing_rpc_payload = _executable_payload()
    monkeypatch.setattr(execute, "liquidation_executor_owner_address", lambda: "0xowner")
    monkeypatch.setattr(execute, "scan_context_assets", lambda: ("", None, "rpc unavailable"))
    try:
        execute.execute_flashloan_liquidation_transaction(missing_rpc_payload)
    except RuntimeError:
        pass
    state = execution_state_payload(control_panel)
    assert state["last_error"] == "rpc unavailable"
    assert state["context"]["phase"] == "missing_rpc"
    assert missing_rpc_payload["execution_phase"] == "missing_rpc"

    PAGE_STATE_STORE._states.clear()
    config_payload = _executable_payload()
    monkeypatch.setattr(execute, "scan_context_assets", lambda: ("http://example.invalid", None, None))
    monkeypatch.setattr(execute, "Web3", FakeWeb3)
    monkeypatch.setattr(execute, "liquidation_config_health", lambda chain_id=None: {"valid": False, "errors": ["chain_id_mismatch"]})
    try:
        execute.execute_flashloan_liquidation_transaction(config_payload)
    except RuntimeError:
        pass
    state = execution_state_payload(control_panel)
    assert state["last_error"] == "submission blocked: chain_id_mismatch"
    assert state["context"]["phase"] == "config_blocked"
    assert config_payload["execution_phase"] == "config_blocked"


def test_self_funded_missing_pool_records_submission_error_phase(monkeypatch):
    from web import control_panel_liquidation_execute as execute

    PAGE_STATE_STORE._states.clear()
    payload = _executable_payload()
    monkeypatch.setattr(execute, "liquidation_execution_controls", lambda: _execution_controls())
    monkeypatch.setattr(execute, "scan_context_assets", lambda: ("http://example.invalid", None, None))
    monkeypatch.delenv("AAVE_POOL_ADDRESS", raising=False)

    try:
        execute._execute_self_funded_liquidation_for_key(payload, "0x" + "1" * 64)
    except RuntimeError:
        pass

    state = execution_state_payload(control_panel)
    assert state["status"] == "ERROR"
    assert state["last_error"] == "missing AAVE_POOL_ADDRESS"
    assert state["context"]["phase"] == "missing_pool"


def test_flashloan_submit_exception_records_submitting_phase(monkeypatch):
    from web import control_panel_liquidation_execute as execute

    class FakeNonceManager:
        def acquire(self):
            return 7

        def release(self, nonce):
            assert nonce == 7

    class FakeBuilder:
        def estimate_gas(self, tx):
            return 350000

        def build_transaction(self, tx):
            raise RuntimeError("broadcast rejected")

    class FakeContractFunctions:
        def requestLiquidation(self, request):
            return FakeBuilder()

    class FakeContract:
        functions = FakeContractFunctions()

    class FakeEth:
        chain_id = 43113
        gas_price = 1
        account = SimpleNamespace(sign_transaction=lambda *args, **kwargs: SimpleNamespace(raw_transaction=b"0xraw"))

        def contract(self, **kwargs):
            return FakeContract()

        def get_block(self, block):
            return SimpleNamespace(baseFeePerGas=0)

    class FakeWeb3:
        eth = FakeEth()

        def __init__(self, provider):
            pass

        @staticmethod
        def HTTPProvider(url, request_kwargs=None):
            return object()

        @staticmethod
        def to_checksum_address(value):
            return str(value)

        @staticmethod
        def to_wei(value, unit):
            return int(float(value) * 1_000_000_000)

    PAGE_STATE_STORE._states.clear()
    payload = _executable_payload()
    monkeypatch.setattr(execute, "liquidation_execution_controls", lambda: _execution_controls())
    monkeypatch.setattr(execute, "liquidation_executor_private_key", lambda: "0x" + "1" * 64)
    monkeypatch.setattr(execute, "liquidation_executor_owner_address", lambda: "0xsender")
    monkeypatch.setattr(execute, "scan_context_assets", lambda: ("http://example.invalid", None, None))
    monkeypatch.setattr(execute, "liquidation_config_health", lambda chain_id=None: {"valid": True})
    monkeypatch.setattr(execute.Account, "from_key", lambda private_key: SimpleNamespace(address="0xsender"))
    monkeypatch.setattr(execute, "Web3", FakeWeb3)
    monkeypatch.setattr(execute, "_nonce_manager", lambda w3, sender: FakeNonceManager())
    monkeypatch.setattr(execute, "simulate_liquidation_static_call", lambda payload: payload | {"preflight": {"static_call_passed": True}})

    try:
        execute.execute_flashloan_liquidation_transaction(payload)
    except RuntimeError:
        pass

    state = execution_state_payload(control_panel)
    assert state["status"] == "ERROR"
    assert state["last_error"] == "broadcast rejected"
    assert state["context"]["phase"] == "submitting"
    assert payload["execution_phase"] == "submitting"


def test_flashloan_submit_runs_required_fork_simulation_before_broadcast(monkeypatch):
    from web import control_panel_liquidation_execute as execute

    class FakeNonceManager:
        def acquire(self):
            return 7

        def release(self, nonce):
            assert nonce == 7

    class FakeBuilder:
        def estimate_gas(self, tx):
            return 350000

        def build_transaction(self, tx):
            raise RuntimeError("broadcast rejected")

    class FakeContractFunctions:
        def requestLiquidation(self, request):
            return FakeBuilder()

    class FakeContract:
        functions = FakeContractFunctions()

    class FakeEth:
        chain_id = 43113
        gas_price = 1
        account = SimpleNamespace(sign_transaction=lambda *args, **kwargs: SimpleNamespace(raw_transaction=b"0xraw"))

        def contract(self, **kwargs):
            return FakeContract()

        def get_block(self, block):
            return SimpleNamespace(baseFeePerGas=0)

    class FakeWeb3:
        eth = FakeEth()

        def __init__(self, provider):
            pass

        @staticmethod
        def HTTPProvider(url, request_kwargs=None):
            return object()

        @staticmethod
        def to_checksum_address(value):
            return str(value)

        @staticmethod
        def to_wei(value, unit):
            return int(float(value) * 1_000_000_000)

    calls = {"fork": 0}

    PAGE_STATE_STORE._states.clear()
    payload = _executable_payload()
    monkeypatch.setattr(
        execute,
        "liquidation_execution_controls",
        lambda: _execution_controls(require_static_call=True, require_fork_simulation=True, fork_simulation_timeout_seconds=30),
    )
    monkeypatch.setattr(execute, "liquidation_executor_private_key", lambda: "0x" + "1" * 64)
    monkeypatch.setattr(execute, "liquidation_executor_owner_address", lambda: "0xsender")
    monkeypatch.setattr(execute, "scan_context_assets", lambda: ("http://example.invalid", None, None))
    monkeypatch.setattr(execute, "liquidation_config_health", lambda chain_id=None: {"valid": True})
    monkeypatch.setattr(execute.Account, "from_key", lambda private_key: SimpleNamespace(address="0xsender"))
    monkeypatch.setattr(execute, "Web3", FakeWeb3)
    monkeypatch.setattr(execute, "_nonce_manager", lambda w3, sender: FakeNonceManager())
    monkeypatch.setattr(execute, "simulate_liquidation_static_call", lambda payload: payload | {"preflight": {"static_call_passed": True, "static_call_status": "passed"}})

    def fake_fork(payload, *, timeout_seconds=180):
        calls["fork"] += 1
        return {
            "fork_simulation_status": "passed",
            "fork_simulation_passed": True,
            "fork_simulation_at": "2026-08-04T00:00:00Z",
        }

    monkeypatch.setattr(execute, "run_liquidation_fork_simulation", fake_fork)

    try:
        execute.execute_flashloan_liquidation_transaction(payload)
    except RuntimeError:
        pass

    assert calls["fork"] == 1
    state = execution_state_payload(control_panel)
    assert state["status"] == "ERROR"
    assert state["last_error"] == "broadcast rejected"
    assert payload["execution_phase"] == "submitting"


def test_fork_simulation_runner_bridges_contract_env(monkeypatch, tmp_path):
    from web import control_panel_liquidation_execute as execute

    captured = {}
    contracts_dir = tmp_path / "contract" / "contracts-bot"
    contracts_dir.mkdir(parents=True)
    monkeypatch.setenv("USDC_ADDRESS", "")
    monkeypatch.setenv("AAVE_POOL_ADDRESS", "")
    monkeypatch.setenv("DEX_ROUTER_ADDRESS", "")
    monkeypatch.setenv("AVALANCHE_RPC_URL", "")
    monkeypatch.setenv("AVALANCHE_RPC", "")
    monkeypatch.delenv("SIMULATE_USE_CONFIGURED_EXECUTOR", raising=False)
    monkeypatch.delenv("LIQUIDATION_FORK_USE_CONFIGURED_EXECUTOR", raising=False)
    monkeypatch.setattr(execute, "liquidation_contracts_bot_dir", lambda: contracts_dir)
    monkeypatch.setattr(execute.shutil, "which", lambda name: "npm.cmd")
    monkeypatch.setattr(execute, "aave_pool_address", lambda: "0xpool")
    monkeypatch.setattr(execute, "dex_router_address", lambda: "0xrouter")
    monkeypatch.setattr(execute, "aave_rpc_urls", lambda: ["https://rpc.example"])

    def fake_run(command, cwd, env, text, capture_output, timeout):
        captured.update(command=command, cwd=cwd, env=env, timeout=timeout)
        return SimpleNamespace(returncode=0, stdout="fork liquidation transaction simulation passed", stderr="")

    monkeypatch.setattr(execute.subprocess, "run", fake_run)

    result = execute.run_liquidation_fork_simulation(
        {
            "executor": "0x0000000000000000000000000000000000000002",
            "request": _executable_payload()["request"],
        },
        timeout_seconds=12,
    )

    assert result["fork_simulation_passed"] is True
    assert captured["command"] == ["npm.cmd", "run", "simulate:liquidation"]
    assert captured["cwd"] == str(contracts_dir)
    assert captured["timeout"] == 12
    assert captured["env"]["AAVE_POOL_ADDRESS"] == "0xpool"
    assert captured["env"]["DEX_ROUTER_ADDRESS"] == "0xrouter"
    assert captured["env"]["USDC_ADDRESS"] == "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E"
    assert captured["env"]["AVALANCHE_RPC_URL"] == "https://rpc.example"
    assert captured["env"]["AVALANCHE_RPC"] == "https://rpc.example"
    assert captured["env"]["SIMULATE_USE_CONFIGURED_EXECUTOR"] == "true"
    assert captured["env"]["LIQUIDATION_PAYLOAD_PATH"].endswith("payload.json")


def test_fork_simulation_requires_configured_executor_when_enabled(monkeypatch, tmp_path):
    from web import control_panel_liquidation_execute as execute

    contracts_dir = tmp_path / "contract" / "contracts-bot"
    contracts_dir.mkdir(parents=True)
    monkeypatch.setenv("LIQUIDATION_FORK_USE_CONFIGURED_EXECUTOR", "true")
    monkeypatch.delenv("LIQUIDATION_EXECUTOR_ADDRESS", raising=False)
    monkeypatch.setattr(execute, "liquidation_contracts_bot_dir", lambda: contracts_dir)
    monkeypatch.setattr(execute.shutil, "which", lambda name: "npm.cmd")

    try:
        execute.run_liquidation_fork_simulation(
            {
                "executor": "",
                "request": _executable_payload()["request"],
            },
            timeout_seconds=12,
        )
    except RuntimeError as exc:
        assert "LIQUIDATION_FORK_USE_CONFIGURED_EXECUTOR=true requires" in str(exc)
    else:
        raise AssertionError("missing configured executor should block fork simulation")


def test_liquidation_payload_deadline_covers_required_fork_simulation(monkeypatch):
    from web import control_panel_liquidation_execute as execute

    captured = {}
    PAGE_STATE_STORE._states.clear()
    monkeypatch.setattr(execute.time, "time", lambda: 1_000)
    monkeypatch.setattr(execute, "liquidation_executor_address", lambda: "0x0000000000000000000000000000000000000004")
    monkeypatch.setattr(execute, "liquidation_account_payload", lambda account: {"account": account, "summary": {"status": "liquidatable"}})
    monkeypatch.setattr(execute, "dex_router_address", lambda: "0x0000000000000000000000000000000000000005")
    monkeypatch.setattr(
        execute,
        "liquidation_execution_controls",
        lambda: _execution_controls(
            require_static_call=True,
            require_fork_simulation=True,
            fork_simulation_timeout_seconds=180,
            min_deadline_remaining_seconds=60,
            slippage_bps=50,
            max_debt_to_cover=0,
            min_profit_base=0,
        ),
    )

    def fake_build(report, **kwargs):
        captured["deadline"] = kwargs["deadline"]
        return {
            "executor": kwargs["executor_address"],
            "request": {"debtToCover": "1", "minProfitAmount": "1", "deadline": str(kwargs["deadline"])},
            "preflight": {},
            "account_report": report,
        }

    monkeypatch.setattr(execute, "build_liquidation_execution_payload", fake_build)
    monkeypatch.setattr(
        execute,
        "apply_liquidation_submission_state",
        lambda payload, mode="flashloan": {
            **payload,
            "submission_allowed": False,
            "block_level": "none",
            "blocked_reasons": [],
            "force_allowed": False,
            "state": "submission_blocked",
        },
    )

    payload = execute.liquidation_execution_payload_for_account(
        "0x0000000000000000000000000000000000000001",
        deadline_seconds=30,
    )

    assert captured["deadline"] == 1_270
    assert payload["preflight"]["deadline_seconds_requested"] == 30
    assert payload["preflight"]["deadline_seconds_effective"] == 270


def test_flashloan_failed_fork_simulation_blocks_before_broadcast(monkeypatch):
    from web import control_panel_liquidation_execute as execute

    class FakeEth:
        chain_id = 43113

    class FakeWeb3:
        eth = FakeEth()

        def __init__(self, provider):
            pass

        @staticmethod
        def HTTPProvider(url, request_kwargs=None):
            return object()

        @staticmethod
        def to_checksum_address(value):
            return str(value)

    calls = {"fork": 0, "nonce": 0}

    PAGE_STATE_STORE._states.clear()
    payload = _executable_payload()
    monkeypatch.setattr(
        execute,
        "liquidation_execution_controls",
        lambda: _execution_controls(require_static_call=True, require_fork_simulation=True, fork_simulation_timeout_seconds=30),
    )
    monkeypatch.setattr(execute, "liquidation_executor_private_key", lambda: "0x" + "1" * 64)
    monkeypatch.setattr(execute, "liquidation_executor_owner_address", lambda: "0xsender")
    monkeypatch.setattr(execute, "scan_context_assets", lambda: ("http://example.invalid", None, None))
    monkeypatch.setattr(execute, "liquidation_config_health", lambda chain_id=None: {"valid": True})
    monkeypatch.setattr(execute.Account, "from_key", lambda private_key: SimpleNamespace(address="0xsender"))
    monkeypatch.setattr(execute, "Web3", FakeWeb3)
    monkeypatch.setattr(execute, "_nonce_manager", lambda w3, sender: calls.update(nonce=calls["nonce"] + 1))
    monkeypatch.setattr(execute, "simulate_liquidation_static_call", lambda payload: payload | {"preflight": {"static_call_passed": True, "static_call_status": "passed"}})

    def fail_fork(payload, *, timeout_seconds=180):
        calls["fork"] += 1
        raise RuntimeError("fork simulation failed")

    monkeypatch.setattr(execute, "run_liquidation_fork_simulation", fail_fork)

    try:
        execute.execute_flashloan_liquidation_transaction(payload)
    except RuntimeError:
        pass

    assert calls["fork"] == 1
    assert calls["nonce"] == 0
    state = execution_state_payload(control_panel)
    assert state["status"] == "HARD_BLOCKED"
    assert state["last_error"] == "submission blocked: fork_simulation_failed"


def test_flashloan_receipt_timeout_records_waiting_receipt_phase(monkeypatch):
    from web import control_panel_liquidation_execute as execute

    class FakeNonceManager:
        def acquire(self):
            return 9

        def release(self, nonce):
            pass

    class FakeBuilder:
        def estimate_gas(self, tx):
            return 350000

        def build_transaction(self, tx):
            return {"nonce": tx["nonce"]}

    class FakeContractFunctions:
        def requestLiquidation(self, request):
            return FakeBuilder()

    class FakeContract:
        functions = FakeContractFunctions()

    class FakeEth:
        chain_id = 43113
        gas_price = 1
        account = SimpleNamespace(sign_transaction=lambda *args, **kwargs: SimpleNamespace(raw_transaction=b"0xraw"))

        def contract(self, **kwargs):
            return FakeContract()

        def get_block(self, block):
            return SimpleNamespace(baseFeePerGas=0)

        def wait_for_transaction_receipt(self, tx_hash, timeout):
            raise TimeoutError("liquidation receipt timeout")

    class FakeWeb3:
        eth = FakeEth()

        def __init__(self, provider):
            pass

        @staticmethod
        def HTTPProvider(url, request_kwargs=None):
            return object()

        @staticmethod
        def to_checksum_address(value):
            return str(value)

        @staticmethod
        def to_wei(value, unit):
            return int(float(value) * 1_000_000_000)

    PAGE_STATE_STORE._states.clear()
    payload = _executable_payload()
    monkeypatch.setattr(execute, "liquidation_execution_controls", lambda: _execution_controls())
    monkeypatch.setattr(execute, "liquidation_executor_private_key", lambda: "0x" + "1" * 64)
    monkeypatch.setattr(execute, "liquidation_executor_owner_address", lambda: "0xsender")
    monkeypatch.setattr(execute, "scan_context_assets", lambda: ("http://example.invalid", None, None))
    monkeypatch.setattr(execute, "liquidation_config_health", lambda chain_id=None: {"valid": True})
    monkeypatch.setattr(execute.Account, "from_key", lambda private_key: SimpleNamespace(address="0xsender"))
    monkeypatch.setattr(execute, "Web3", FakeWeb3)
    monkeypatch.setattr(execute, "_nonce_manager", lambda w3, sender: FakeNonceManager())
    monkeypatch.setattr(execute, "send_raw_transaction_private_first", lambda raw_tx, public_w3=None: {"tx_hash": "0xflash"})
    monkeypatch.setattr(execute, "simulate_liquidation_static_call", lambda payload: payload | {"preflight": {"static_call_passed": True}})

    try:
        execute.execute_flashloan_liquidation_transaction(payload)
    except TimeoutError:
        pass

    state = execution_state_payload(control_panel)
    assert state["status"] == "ERROR"
    assert state["last_error"] == "liquidation receipt timeout"
    assert state["context"]["phase"] == "waiting_receipt"
    assert state["context"]["tx_hash"] == "0xflash"
    assert payload["execution_phase"] == "waiting_receipt"
    assert payload["tx_hash"] == "0xflash"


def test_self_funded_approval_receipt_timeout_records_phase(monkeypatch):
    from web import control_panel_liquidation_execute as execute

    class FakeNonceManager:
        def acquire(self):
            return 8

        def release(self, nonce):
            pass

    class FakeBuilder:
        def estimate_gas(self, tx):
            return 100000

        def build_transaction(self, tx):
            return {"nonce": tx["nonce"]}

    class FakeFunctions:
        def approve(self, pool_address, amount):
            return FakeBuilder()

        def liquidationCall(self, *args):
            return FakeBuilder()

    class FakeContract:
        functions = FakeFunctions()

    class FakeEth:
        chain_id = 43113
        gas_price = 1
        account = SimpleNamespace(sign_transaction=lambda *args, **kwargs: SimpleNamespace(raw_transaction=b"0xraw"))

        def contract(self, **kwargs):
            return FakeContract()

        def get_block(self, block):
            return SimpleNamespace(baseFeePerGas=0)

        def wait_for_transaction_receipt(self, tx_hash, timeout):
            raise TimeoutError("approval receipt timeout")

    class FakeWeb3:
        eth = FakeEth()

        def __init__(self, provider):
            pass

        @staticmethod
        def HTTPProvider(url, request_kwargs=None):
            return object()

        @staticmethod
        def to_checksum_address(value):
            return str(value)

        @staticmethod
        def to_wei(value, unit):
            return int(float(value) * 1_000_000_000)

    PAGE_STATE_STORE._states.clear()
    monkeypatch.setenv("AAVE_POOL_ADDRESS", "0xpool")
    monkeypatch.setattr(execute, "liquidation_execution_controls", lambda: _execution_controls())
    monkeypatch.setattr(execute, "scan_context_assets", lambda: ("http://example.invalid", None, None))
    monkeypatch.setattr(execute, "liquidation_config_health", lambda chain_id=None: {"valid": True})
    monkeypatch.setattr(execute.Account, "from_key", lambda private_key: SimpleNamespace(address="0xsender"))
    monkeypatch.setattr(execute, "Web3", FakeWeb3)
    monkeypatch.setattr(execute, "_nonce_manager", lambda w3, sender: FakeNonceManager())
    monkeypatch.setattr(execute, "send_raw_transaction_private_first", lambda raw_tx, public_w3=None: {"tx_hash": "0xapproval"})

    payload = _executable_payload()
    try:
        execute._execute_self_funded_liquidation_for_key(payload, "0x" + "1" * 64)
    except TimeoutError:
        pass

    state = execution_state_payload(control_panel)
    assert state["status"] == "ERROR"
    assert state["last_error"] == "approval receipt timeout"
    assert state["context"]["phase"] == "waiting_approval_receipt"
    assert state["context"]["tx_hash"] == "0xapproval"
    assert payload["execution_phase"] == "waiting_approval_receipt"
    assert payload["tx_hash"] == "0xapproval"


def test_self_funded_approval_receipt_status_zero_stops_before_liquidation(monkeypatch):
    from web import control_panel_liquidation_execute as execute

    class FakeNonceManager:
        def acquire(self):
            return 8

        def release(self, nonce):
            pass

    class FakeBuilder:
        def estimate_gas(self, tx):
            return 100000

        def build_transaction(self, tx):
            return {"nonce": tx["nonce"]}

    calls = {"liquidation": 0}

    class FakeFunctions:
        def approve(self, pool_address, amount):
            return FakeBuilder()

        def liquidationCall(self, *args):
            calls["liquidation"] += 1
            return FakeBuilder()

    class FakeContract:
        functions = FakeFunctions()

    class FakeEth:
        chain_id = 43113
        gas_price = 1
        account = SimpleNamespace(sign_transaction=lambda *args, **kwargs: SimpleNamespace(raw_transaction=b"0xraw"))

        def contract(self, **kwargs):
            return FakeContract()

        def get_block(self, block):
            return SimpleNamespace(baseFeePerGas=0)

        def wait_for_transaction_receipt(self, tx_hash, timeout):
            return SimpleNamespace(transactionHash=tx_hash, blockNumber=123, gasUsed=21000, effectiveGasPrice=1, status=0)

    class FakeWeb3:
        eth = FakeEth()

        def __init__(self, provider):
            pass

        @staticmethod
        def HTTPProvider(url, request_kwargs=None):
            return object()

        @staticmethod
        def to_checksum_address(value):
            return str(value)

        @staticmethod
        def to_wei(value, unit):
            return int(float(value) * 1_000_000_000)

    PAGE_STATE_STORE._states.clear()
    monkeypatch.setenv("AAVE_POOL_ADDRESS", "0xpool")
    monkeypatch.setattr(execute, "liquidation_execution_controls", lambda: _execution_controls())
    monkeypatch.setattr(execute, "scan_context_assets", lambda: ("http://example.invalid", None, None))
    monkeypatch.setattr(execute, "liquidation_config_health", lambda chain_id=None: {"valid": True})
    monkeypatch.setattr(execute.Account, "from_key", lambda private_key: SimpleNamespace(address="0xsender"))
    monkeypatch.setattr(execute, "Web3", FakeWeb3)
    monkeypatch.setattr(execute, "_nonce_manager", lambda w3, sender: FakeNonceManager())
    monkeypatch.setattr(execute, "send_raw_transaction_private_first", lambda raw_tx, public_w3=None: {"tx_hash": "0xapproval"})

    payload = _executable_payload()
    try:
        execute._execute_self_funded_liquidation_for_key(payload, "0x" + "1" * 64)
    except RuntimeError:
        pass

    state = execution_state_payload(control_panel)
    assert state["status"] == "ERROR"
    assert state["last_error"] == "approval transaction failed"
    assert state["context"]["phase"] == "approval_failed"
    assert state["context"]["tx_hash"] == "0xapproval"
    assert payload["execution_phase"] == "approval_failed"
    assert calls["liquidation"] == 0


def test_self_funded_liquidation_receipt_timeout_records_phase(monkeypatch):
    from web import control_panel_liquidation_execute as execute

    class FakeNonceManager:
        def __init__(self):
            self.next_nonce = 10

        def acquire(self):
            value = self.next_nonce
            self.next_nonce += 1
            return value

        def release(self, nonce):
            pass

    class FakeBuilder:
        def estimate_gas(self, tx):
            return 100000

        def build_transaction(self, tx):
            return {"nonce": tx["nonce"]}

    class FakeFunctions:
        def approve(self, pool_address, amount):
            return FakeBuilder()

        def liquidationCall(self, *args):
            return FakeBuilder()

    class FakeContract:
        functions = FakeFunctions()

    wait_calls = {"count": 0}

    class FakeEth:
        chain_id = 43113
        gas_price = 1
        account = SimpleNamespace(sign_transaction=lambda *args, **kwargs: SimpleNamespace(raw_transaction=b"0xraw"))

        def contract(self, **kwargs):
            return FakeContract()

        def get_block(self, block):
            return SimpleNamespace(baseFeePerGas=0)

        def wait_for_transaction_receipt(self, tx_hash, timeout):
            wait_calls["count"] += 1
            if wait_calls["count"] == 1:
                return SimpleNamespace(transactionHash=tx_hash, blockNumber=123, gasUsed=21000, effectiveGasPrice=1, status=1)
            raise TimeoutError("liquidation receipt timeout")

    class FakeWeb3:
        eth = FakeEth()

        def __init__(self, provider):
            pass

        @staticmethod
        def HTTPProvider(url, request_kwargs=None):
            return object()

        @staticmethod
        def to_checksum_address(value):
            return str(value)

        @staticmethod
        def to_wei(value, unit):
            return int(float(value) * 1_000_000_000)

    tx_hashes = iter(["0xapproval", "0xliquidation"])

    PAGE_STATE_STORE._states.clear()
    monkeypatch.setenv("AAVE_POOL_ADDRESS", "0xpool")
    monkeypatch.setattr(execute, "liquidation_execution_controls", lambda: _execution_controls())
    monkeypatch.setattr(execute, "scan_context_assets", lambda: ("http://example.invalid", None, None))
    monkeypatch.setattr(execute, "liquidation_config_health", lambda chain_id=None: {"valid": True})
    monkeypatch.setattr(execute.Account, "from_key", lambda private_key: SimpleNamespace(address="0xsender"))
    monkeypatch.setattr(execute, "Web3", FakeWeb3)
    monkeypatch.setattr(execute, "_nonce_manager", lambda w3, sender: FakeNonceManager())
    monkeypatch.setattr(execute, "send_raw_transaction_private_first", lambda raw_tx, public_w3=None: {"tx_hash": next(tx_hashes)})

    payload = _executable_payload()
    try:
        execute._execute_self_funded_liquidation_for_key(payload, "0x" + "1" * 64)
    except TimeoutError:
        pass

    state = execution_state_payload(control_panel)
    assert state["status"] == "ERROR"
    assert state["last_error"] == "liquidation receipt timeout"
    assert state["context"]["phase"] == "waiting_receipt"
    assert state["context"]["tx_hash"] == "0xliquidation"
    assert payload["execution_phase"] == "waiting_receipt"
    assert payload["tx_hash"] == "0xliquidation"
