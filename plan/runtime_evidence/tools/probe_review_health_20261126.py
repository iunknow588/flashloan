'''Read-only health probe for the review path (2026-11-26).

Answers one question: are the "not executable" verdicts a real market
condition or a systematic fault of the review path?  Prints the newest
reviews with their inner error text, a status histogram over time and the
count of reviews that carry a positive signed pnl.
'''

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


DB = Path(__file__).resolve().parents[2].parent / 'flashloan' / 'db' / 'local_store_4663.sqlite3'


def open_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    conn.execute('PRAGMA query_only=ON')
    return conn


def inner_error(blob: str | None) -> str:
    if not blob:
        return ''
    try:
        payload = json.loads(blob)
    except json.JSONDecodeError:
        return blob[:200]
    if isinstance(payload, dict):
        for key in ('error', 'message', 'reason', 'lastError', 'innerError'):
            if payload.get(key):
                return str(payload[key])[:300]
        run = payload.get('runArbitrage') or payload.get('run_arbitrage')
        if isinstance(run, dict):
            for key in ('error', 'message', 'reason'):
                if run.get(key):
                    return str(run[key])[:300]
    return json.dumps(payload, ensure_ascii=False)[:300]


def main() -> int:
    conn = open_ro()
    print('== status histogram ==')
    for row in conn.execute(
        'select review_status, count(*) from manager_v4_reviews group by review_status order by 2 desc'
    ):
        print(' ', row[0], row[1])

    print('== newest 10 reviews ==')
    for row in conn.execute(
        'select reviewed_at, review_status, review_reason, signed_pnl_raw, run_arbitrage_json'
        ' from manager_v4_reviews order by reviewed_at desc limit 10'
    ):
        reviewed_at, status, reason, pnl, run = row
        print(f'  {reviewed_at} | {status} | pnl={pnl} | {str(reason)[:80]}')
        print(f'      inner: {inner_error(run)}')

    print('== per 10 minute buckets ==')
    for row in conn.execute(
        "select substr(reviewed_at, 1, 15) || '0:00Z', review_status, count(*)"
        ' from manager_v4_reviews group by 1, 2 order by 1 desc, 2 limit 30'
    ):
        print(' ', row[0], row[1], row[2])

    print('== positive pnl rows ==')
    for row in conn.execute(
        "select reviewed_at, review_status, signed_pnl_raw, signed_pnl_amount, loop_key"
        " from manager_v4_reviews where signed_pnl_raw is not null"
        ' and signed_pnl_raw <> 0 order by reviewed_at desc limit 20'
    ):
        print(' ', row)

    conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
