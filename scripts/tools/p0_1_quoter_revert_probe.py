"""Read-only P0-1 probe: reproduce a chain-side quoter revert across blocks.

The probe calls the deployed Uniswap V4 quoter directly with `eth_call` at
the exact block a shadow comparison pinned, and at a few later blocks, so a
chain verdict can be separated from a state-coherence artefact.

It sends no transaction and writes to no database.

Usage:
    python scripts/tools/p0_1_quoter_revert_probe.py <pool_id> <block> [rpc_url]

The RPC list is read from `flashloan/app_runtime/.env` when no URL is given.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
QUOTER = "0x8dc178efb8111bb0973dd9d722ebeff267c98f94"
SIGNATURE = "quoteExactInputSingle(((address,address,uint24,int24,address),bool,uint128,bytes))"

_ROUND_CONSTANTS = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
_ROTATIONS = [
    [0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56], [27, 20, 39, 8, 14],
]
_MASK = (1 << 64) - 1


def keccak256(data: bytes) -> bytes:
    """The Keccak-256 digest (the pre-standardisation padding)."""

    def rotate(value: int, shift: int) -> int:
        return ((value << shift) | (value >> (64 - shift))) & _MASK

    def permute(state: list[list[int]]) -> list[list[int]]:
        for round_index in range(24):
            columns = [
                state[x][0] ^ state[x][1] ^ state[x][2] ^ state[x][3] ^ state[x][4]
                for x in range(5)
            ]
            deltas = [columns[(x - 1) % 5] ^ rotate(columns[(x + 1) % 5], 1) for x in range(5)]
            for x in range(5):
                for y in range(5):
                    state[x][y] ^= deltas[x]
            rotated = [[0] * 5 for _ in range(5)]
            for x in range(5):
                for y in range(5):
                    rotated[y][(2 * x + 3 * y) % 5] = rotate(state[x][y], _ROTATIONS[x][y])
            for x in range(5):
                for y in range(5):
                    state[x][y] = rotated[x][y] ^ (
                        (~rotated[(x + 1) % 5][y]) & _MASK & rotated[(x + 2) % 5][y]
                    )
            state[0][0] ^= _ROUND_CONSTANTS[round_index]
        return state

    rate = 136
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate != 0:
        padded.append(0x00)
    padded[-1] |= 0x80
    state = [[0] * 5 for _ in range(5)]
    for offset in range(0, len(padded), rate):
        block = padded[offset : offset + rate]
        for index in range(rate // 8):
            state[index % 5][index // 5] ^= int.from_bytes(block[index * 8 : (index + 1) * 8], "little")
        state = permute(state)
    digest = b""
    for index in range(4):
        digest += state[index % 5][index // 5].to_bytes(8, "little")
    return digest


def function_selector(signature: str) -> bytes:
    return keccak256(signature.encode())[:4]


def word_address(value: str) -> bytes:
    return bytes(12) + bytes.fromhex(value[2:])


def word_uint(value: int) -> bytes:
    return int(value).to_bytes(32, "big")


def word_int(value: int) -> bytes:
    return int(value).to_bytes(32, "big", signed=True)


def build_quote_calldata(hop: dict) -> str:
    """Build the `quoteExactInputSingle` calldata of the deployed quoter.

    The deployed contract takes one **nested** struct argument, so the ABI
    layout has two levels: the outer argument is a dynamic tuple whose head
    carries an offset, and the tuple body holds the static `PoolKey`, the
    `bool`, the `uint128` and the offset of the dynamic `bytes` member.
    The flat layout has a different selector and reverts with empty data.
    """
    body = b""
    body += word_address(hop["token0"])
    body += word_address(hop["token1"])
    body += word_uint(hop["fee"])
    body += word_int(hop["tick_spacing"])
    body += word_address(hop["hooks"])
    body += word_uint(1 if hop["zeroForOne"] else 0)
    body += word_uint(hop["amountInRaw"])
    hook_data_offset = len(body) + 32
    body += word_uint(hook_data_offset)
    body += word_uint(0)
    data = function_selector(SIGNATURE) + word_uint(32) + body
    return "0x" + data.hex()


def rpc_urls() -> list[str]:
    env: dict[str, str] = {}
    env_path = REPO / "flashloan/app_runtime/.env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip()
    raw = env.get("ROBINHOOD_RPCS") or env.get("ROBINHOOD_MAINNET_RPC_URLS") or ""
    urls = [item.strip() for item in raw.split(",") if item.strip()]
    preferred = ("quiknode", "publicnode", "chain.robinhood.com")
    return sorted(urls, key=lambda item: next((i for i, key in enumerate(preferred) if key in item), len(preferred)))


def rpc(url: str, method: str, params: list) -> dict:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def try_rpc(url: str, method: str, params: list):
    try:
        return rpc(url, method, params), None
    except urllib.error.HTTPError as error:
        return None, f"HTTP {error.code}"
    except Exception as error:  # noqa: BLE001 - the probe reports every failure
        return None, repr(error)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    pool_id = sys.argv[1].strip().lower()
    pinned = int(sys.argv[2])
    urls = sys.argv[3:] or rpc_urls()

    import sqlite3

    connection = sqlite3.connect(f"file:{(REPO / 'flashloan/db/local_store_4663.sqlite3').as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT token0, token1, fee, tick_spacing, hooks FROM v4_exchange_pools WHERE pool_id = ?",
        (pool_id,),
    ).fetchone()
    connection.close()
    if row is None:
        print(f"unknown pool: {pool_id}")
        return 2

    chosen = None
    head = None
    for url in urls:
        result, _ = try_rpc(url, "eth_blockNumber", [])
        if result and "result" in result:
            chosen = url
            head = int(result["result"], 16)
            break
    if chosen is None:
        print("no working RPC endpoint")
        return 3

    hops = {
        True: {**dict(row), "zeroForOne": True, "amountInRaw": 10**15},
        False: {**dict(row), "zeroForOne": False, "amountInRaw": 10**15},
    }
    rows = []
    for block in [pinned, pinned + 1, pinned + 10, pinned + 100, pinned + 1000, head]:
        for zero_for_one in (True, False):
            calldata = build_quote_calldata(hops[zero_for_one])
            result, error = try_rpc(chosen, "eth_call", [{"to": QUOTER, "data": calldata}, hex(block)])
            entry = {"block": block, "zeroForOne": zero_for_one}
            if result is None:
                entry["outcome"] = "transport_error"
                entry["detail"] = error
            elif "error" in result:
                entry["outcome"] = "revert"
                entry["detail"] = result["error"]
            else:
                data = result.get("result", "")
                entry["outcome"] = "ok" if data and data != "0x" else "empty"
                entry["raw"] = data
            rows.append(entry)
            print(json.dumps(entry, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
