"""Capture the shape of one live manual-review response.

The healer materializes the runtime's manual-review answer into the
Review Manager library.  This probe prints the **structure** of that answer
so the fields the publication has to carry can be named exactly.
"""

from __future__ import annotations

import json
import uuid
import urllib.error
import urllib.request

RUNTIME = "http://127.0.0.1:5000"
CHAIN_ID = 4663

ROUTE_SYMBOLS = ["ETH", "SPY", "NVDA", "ETH"]
ROUTE_ADDRESSES = [
    "0x0000000000000000000000000000000000000000",
    "0x117cc2133c37b721f49de2a7a74833232b3b4c0c",
    "0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec",
    "0x0000000000000000000000000000000000000000",
]


def post(url: str, body: dict) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "X-Request-Id": f"probe-{uuid.uuid4().hex[:16]}",
                 "Idempotency-Key": f"probe-review-{uuid.uuid4().hex[:16]}"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        print(f"HTTP {error.code}: {error.read().decode('utf-8', 'replace')[:600]}")
        raise


def walk(value, prefix: str, out: list[str], depth: int = 0) -> None:
    if depth > 6:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(item, (dict, list)):
                out.append(f"{path}  <{type(item).__name__}>")
                walk(item, path, out, depth + 1)
            else:
                out.append(f"{path} = {str(item)[:70]}")
    elif isinstance(value, list):
        for index, item in enumerate(value[:2]):
            walk(item, f"{prefix}[{index}]", out, depth + 1)


def main() -> None:
    response = post(
        f"{RUNTIME}/api/dex/closed-loop-groups/manual-review?chain_id={CHAIN_ID}",
        {"routeSymbols": ROUTE_SYMBOLS, "routeAddresses": ROUTE_ADDRESSES,
         "mode": "deep", "dryRun": False})
    lines: list[str] = []
    walk(response, "", lines)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
