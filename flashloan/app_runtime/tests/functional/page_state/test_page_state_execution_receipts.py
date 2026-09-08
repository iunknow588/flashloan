from types import SimpleNamespace

from web import control_panel
from bot.page_state import PAGE_STATE_STORE, execution_state_payload


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
