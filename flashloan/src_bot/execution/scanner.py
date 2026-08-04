from __future__ import annotations

from typing import Any, Iterable

from web3 import Web3

from execution.liquidation_accounts import discover_borrower_addresses as _discover_borrower_addresses
from execution.liquidation_abis import BORROW_EVENT_TOPIC


def discover_borrower_addresses(*args: Any, **kwargs: Any) -> list[str]:
    kwargs.setdefault("event_topic", BORROW_EVENT_TOPIC)
    kwargs.setdefault("web3_class", Web3)
    return _discover_borrower_addresses(*args, **kwargs)


def normalize_accounts(accounts: Iterable[str], max_accounts: int) -> list[str]:
    result: list[str] = []
    for account in accounts:
        try:
            checksum = Web3.to_checksum_address(str(account))
        except ValueError:
            continue
        if checksum not in result:
            result.append(checksum)
    return result[: max(1, int(max_accounts))]
