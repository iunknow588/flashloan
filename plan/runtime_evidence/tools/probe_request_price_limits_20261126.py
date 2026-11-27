'''Read-only probe: does a published broadcast request carry the three limits?

The broadcast admission needs exactly three per-hop price limits out of the
published request.  If the review publishes a request without them, the
runtime refuses the route and no broadcast ever happens -- so the shape of
the published row is the decisive reading for the whole chain.
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
    columns = [row[1] for row in connection.execute('PRAGMA table_info(manager_v4_review_broadcast_requests)')]
    print('columns:', columns)
    out: list[dict] = []
    for row in connection.execute('SELECT * FROM manager_v4_review_broadcast_requests'):
        item = dict(row)
        raw = item.get('hop_sqrt_price_limits_json')
        try:
            parsed = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            parsed = None
        item['limits_count'] = len(parsed) if isinstance(parsed, list) else None
        item['limits_raw'] = raw
        out.append(item)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))

    reviews_columns = [row[1] for row in connection.execute('PRAGMA table_info(manager_v4_reviews)')]
    print('review columns:', reviews_columns)
    connection.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
