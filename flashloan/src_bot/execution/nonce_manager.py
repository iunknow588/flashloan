from __future__ import annotations
import threading
from typing import Any
from web3 import Web3


class NonceManager:
    def __init__(self, w3: Web3, sender: str):
        self._w3 = w3
        self._sender = Web3.to_checksum_address(sender)
        self._lock = threading.Lock()
        self._nonce: int | None = None
        self._released: list[int] = []

    def initialize(self) -> int:
        with self._lock:
            self._nonce = self._w3.eth.get_transaction_count(self._sender, 'pending')
            self._released.clear()
            return self._nonce

    def acquire(self) -> int:
        with self._lock:
            if self._nonce is None:
                raise RuntimeError("NonceManager not initialized; call initialize() first")
            if self._released:
                return self._released.pop(0)
            nonce = self._nonce
            self._nonce += 1
            return nonce

    def release(self, nonce: int) -> None:
        with self._lock:
            if nonce not in self._released:
                self._released.append(nonce)
                self._released.sort()

    def current(self) -> int | None:
        with self._lock:
            return self._nonce

    def reset(self) -> int:
        return self.initialize()
