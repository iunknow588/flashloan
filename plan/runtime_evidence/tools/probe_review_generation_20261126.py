'''Read-only probe: the newest review generation and its verdicts.

The review rows are replaced per closed loop, so the row count stays put
while the content turns over.  This probe groups by the newest generation
and counts the verdicts, then lists every completed review with its profit.
'''

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


DB = Path('flashloan/db/local_store_4663.sqlite3').resolve()


def main() -> int:
    connection = sqlite3.connect(f'file:{DB.as_posix()}?mode=ro', uri=True, timeout=60)
    connection.execute('PRAGMA query_only=ON')
    connection.row_factory = sqlite3.Row

    print('--- verdict distribution over the whole table ---')
    for row in connection.execute(
        'SELECT review_status, COUNT(*) AS n FROM manager_v4_reviews GROUP BY review_status'
    ):
        print(f'  {row["review_status"]}: {row["n"]}')

    print('--- newest reviewed_at per verdict (how fresh the table is) ---')
    for row in connection.execute(
        'SELECT review_status, MAX(reviewed_at) AS newest, MIN(reviewed_at) AS oldest, COUNT(*) AS n '
        'FROM manager_v4_reviews GROUP BY review_status'
    ):
        print(f'  {row["review_status"]}: n={row["n"]} newest={row["newest"]} oldest={row["oldest"]}')

    print('--- completed reviews (the only ones that may carry a request) ---')
    rows = connection.execute(
        'SELECT loop_key, review_status, review_reason, signed_pnl_raw, signed_pnl_amount, '
        'signed_pnl_currency, reviewed_at, hop_sqrt_price_limits_json '
        'FROM manager_v4_reviews WHERE review_status = \'completed\' ORDER BY reviewed_at DESC'
    ).fetchall()
    for row in rows:
        item = dict(row)
        limits = item.pop('hop_sqrt_price_limits_json')
        try:
            parsed = json.loads(limits) if limits else None
        except (TypeError, ValueError):
            parsed = None
        item['limits_count'] = len(parsed) if isinstance(parsed, list) else None
        print('  ' + json.dumps(item, ensure_ascii=False, default=str))

    print('--- not_executable reasons (newest generation) ---')
    for row in connection.execute(
        'SELECT review_reason, COUNT(*) AS n, MAX(reviewed_at) AS newest '
        'FROM manager_v4_reviews WHERE review_status <> \'completed\' '
        'GROUP BY review_reason ORDER BY n DESC LIMIT 15'
    ):
        print(f'  {row["n"]:>4}  {row["review_reason"]}  (newest {row["newest"]})')
    connection.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
