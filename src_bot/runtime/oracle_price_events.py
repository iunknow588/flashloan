from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from web3 import Web3


ANSWER_UPDATED_TOPIC = Web3.keccak(
    text="AnswerUpdated(int256,uint256,uint256)"
).hex()


@dataclass(frozen=True)
class OracleFeed:
    asset: str
    aggregator: str
    decimals: int = 8


def _topic_int(value: Any) -> int:
    if isinstance(value, bytes):
        return int.from_bytes(value, "big", signed=False)
    return int(value, 16) if isinstance(value, str) and value.startswith("0x") else int(value or 0)


def normalize_feeds(feeds: Iterable[OracleFeed | dict[str, Any]]) -> list[OracleFeed]:
    normalized: list[OracleFeed] = []
    for item in feeds:
        if isinstance(item, OracleFeed):
            normalized.append(item)
            continue
        asset = str(item.get("asset") or "").strip()
        aggregator = str(item.get("aggregator") or item.get("address") or "").strip()
        if asset and aggregator:
            normalized.append(
                OracleFeed(
                    asset=asset,
                    aggregator=Web3.to_checksum_address(aggregator),
                    decimals=max(0, int(item.get("decimals", 8))),
                )
            )
    return normalized


def parse_answer_updated_logs(
    logs: Iterable[dict[str, Any]],
    feeds: Iterable[OracleFeed | dict[str, Any]],
) -> list[dict[str, Any]]:
    by_aggregator = {
        feed.aggregator.lower(): feed
        for feed in normalize_feeds(feeds)
    }
    events: list[dict[str, Any]] = []
    for log in logs:
        topics = list(log.get("topics") or [])
        if not topics or str(topics[0]).lower() != ANSWER_UPDATED_TOPIC.lower() or len(topics) < 4:
            continue
        address = str(log.get("address") or "").lower()
        feed = by_aggregator.get(address)
        if feed is None:
            continue
        answer = _topic_int(topics[1])
        updated_at = _topic_int(topics[3])
        events.append(
            {
                "asset": feed.asset,
                "aggregator": feed.aggregator,
                "answer": answer,
                "price": answer / float(10 ** feed.decimals),
                "updated_at": updated_at,
                "block_number": int(log.get("blockNumber") or 0),
                "transaction_hash": str(log.get("transactionHash") or ""),
                "source": "chainlink_answer_updated",
            }
        )
    events.sort(key=lambda item: (item["block_number"], item["updated_at"]))
    return events


class OraclePriceEventPoller:
    def __init__(
        self,
        w3: Web3,
        feeds: Iterable[OracleFeed | dict[str, Any]],
        *,
        start_block: int | None = None,
        chunk_size: int = 2000,
    ) -> None:
        self.w3 = w3
        self.feeds = normalize_feeds(feeds)
        self.last_scanned_block = None if start_block is None else int(start_block) - 1
        self.chunk_size = max(1, int(chunk_size))

    def poll(self, to_block: int | str = "latest") -> list[dict[str, Any]]:
        latest = int(self.w3.eth.block_number if to_block == "latest" else to_block)
        from_block = latest if self.last_scanned_block is None else self.last_scanned_block + 1
        if from_block > latest or not self.feeds:
            return []
        events: list[dict[str, Any]] = []
        addresses = [feed.aggregator for feed in self.feeds]
        for chunk_start in range(from_block, latest + 1, self.chunk_size):
            chunk_end = min(latest, chunk_start + self.chunk_size - 1)
            logs = self.w3.eth.get_logs(
                {
                    "address": addresses,
                    "fromBlock": chunk_start,
                    "toBlock": chunk_end,
                    "topics": [ANSWER_UPDATED_TOPIC],
                }
            )
            events.extend(parse_answer_updated_logs(logs, self.feeds))
        self.last_scanned_block = latest
        return events
