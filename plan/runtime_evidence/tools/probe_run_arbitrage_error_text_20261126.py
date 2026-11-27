'''Read-only probe: the error text inside the failed runArbitrage payloads.

The review verdict only says "runArbitrage returned ok=false".  What
failed inside it is what decides whether the route can ever be priced, so
this probe opens the payload and groups the inner error strings.
'''

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path


DB = Path('flashloan/db/local_store_4663.sqlite3').resolve()


def walk(value: object, found: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in ('error', 'message', 'reason', 'detail', 'errormessage'):
                found.append(f'{key}={item}')
            walk(item, found)
    elif isinstance(value, list):
        for item in value:
            walk(item, found)


def main() -> int:
    connection = sqlite3.connect(f'file:{DB.as_posix()}?mode=ro', uri=True, timeout=60)
    connection.execute('PRAGMA query_only=ON')
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        'SELECT loop_key, reviewed_at, run_arbitrage_json FROM manager_v4_reviews '
        "WHERE review_reason = 'runArbitrage returned ok=false' ORDER BY reviewed_at DESC"
    ).fetchall()
    counter: Counter[str] = Counter()
    newest = None
    for row in rows:
        if newest is None:
            newest = row['reviewed_at']
        try:
            parsed = json.loads(row['run_arbitrage_json'] or 'null')
        except (TypeError, ValueError):
            parsed = None
        found: list[str] = []
        walk(parsed, found)
        for item in found:
            counter[item[:220]] += 1
    print(f'rows={len(rows)} newest_reviewed_at={newest}')
    print('--- inner error strings, grouped ---')
    for item, count in counter.most_common(20):
        print(f'  {count:>4}  {item}')
    print('--- one full payload (newest) ---')
    if rows:
        print(json.dumps(json.loads(rows[0]['run_arbitrage_json'] or 'null'),
                         ensure_ascii=False, indent=2)[:3000])
    connection.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
