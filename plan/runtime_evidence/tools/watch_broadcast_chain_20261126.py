'''Watch the live store for the broadcast chain, and keep watching.

The chain is: the review publishes a positive-profit request -> the
runtime reads the proof back -> the broadcast worker signs and sends ->
the record lands in the main store and, after the archiver runs, in the
monthly trade store.

The earlier watcher printed a step only when its counter went from zero to
something, so it went silent after the first request ever existed.  This
one keys every step on the **identity of the row**, so a request published
after this watcher started is announced, and it also reports the review
verdicts, because a route that is not profitable is the reason a step
does not happen.
'''

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path


DB = Path('flashloan/db/local_store_4663.sqlite3').resolve()
MONTHLY_ROOT = Path('flashloan/db/broadcast_transaction_db').resolve()


def main() -> int:
    deadline = time.time() + float(sys.argv[1] if len(sys.argv) > 1 else 5400)
    seen_requests: set[tuple] = set()
    seen_attempts: set[str] = set()
    seen_monthly: set[str] = set()
    seen_verdicts: dict[str, int] = {}
    print(f'[{time.strftime("%H:%M:%S")}] watching {DB}', flush=True)
    while time.time() < deadline:
        try:
            connection = sqlite3.connect(f'file:{DB.as_posix()}?mode=ro', uri=True, timeout=30)
            connection.execute('PRAGMA query_only=ON')
            connection.row_factory = sqlite3.Row

            for row in connection.execute(
                'SELECT loop_key, request_status, signed_pnl_raw, signed_pnl_amount, '
                'created_at, hop_sqrt_price_limits_json FROM '
                'manager_v4_review_broadcast_requests ORDER BY created_at'
            ):
                item = dict(row)
                key = (item['loop_key'], item['created_at'])
                if key in seen_requests:
                    continue
                seen_requests.add(key)
                try:
                    limits = json.loads(item.pop('hop_sqrt_price_limits_json') or '[]')
                except (TypeError, ValueError):
                    limits = []
                item['limits_count'] = len(limits)
                print(f'[{time.strftime("%H:%M:%S")}] broadcast request published: '
                      f'{item["request_status"]} pnl={item["signed_pnl_raw"]} '
                      f'({item["signed_pnl_amount"]}) limits={len(limits)}/3 '
                      f'at {item["created_at"]}', flush=True)
                print('   ' + json.dumps(item, ensure_ascii=False, default=str), flush=True)

            for row in connection.execute(
                'SELECT attempt_id, loop_key, status, signed_pnl_raw, nonce, created_at '
                'FROM manager_cow_execution_attempts ORDER BY created_at'
            ):
                item = dict(row)
                if item['attempt_id'] in seen_attempts:
                    continue
                seen_attempts.add(item['attempt_id'])
                print(f'[{time.strftime("%H:%M:%S")}] broadcast attempt: '
                      f'{item["status"]} pnl={item["signed_pnl_raw"]} '
                      f'at {item["created_at"]}', flush=True)
                print('   ' + json.dumps(item, ensure_ascii=False, default=str), flush=True)

            verdicts = {
                str(row['review_status']): int(row['n'])
                for row in connection.execute(
                    'SELECT review_status, COUNT(*) AS n FROM manager_v4_reviews '
                    'GROUP BY review_status'
                )
            }
            if verdicts != seen_verdicts:
                seen_verdicts = verdicts
                print(f'[{time.strftime("%H:%M:%S")}] review verdicts: {verdicts}', flush=True)
            connection.close()

            monthly = sorted(MONTHLY_ROOT.glob('**/trades.sqlite3')) if MONTHLY_ROOT.exists() else []
            for path in monthly:
                key = str(path)
                if key in seen_monthly:
                    continue
                seen_monthly.add(key)
                print(f'[{time.strftime("%H:%M:%S")}] monthly trade store: {key}', flush=True)
        except sqlite3.Error as error:
            print(f'[{time.strftime("%H:%M:%S")}] read error: {error}', flush=True)
        time.sleep(30)
    print(f'[{time.strftime("%H:%M:%S")}] watch window over', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
