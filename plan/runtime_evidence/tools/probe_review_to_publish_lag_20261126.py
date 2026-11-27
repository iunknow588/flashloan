'''Read-only probe: how long between the review and the publish of its request?

The broadcast admission judges the request by the **review time** of the
review row it joins to, not by the request's own creation time.  A review
that takes minutes to compute and then publishes would therefore be
refused as stale.  This probe measures that lag on the live rows.
'''

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DB = Path('flashloan/db/local_store_4663.sqlite3').resolve()


def parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def main() -> int:
    connection = sqlite3.connect(f'file:{DB.as_posix()}?mode=ro', uri=True, timeout=60)
    connection.execute('PRAGMA query_only=ON')
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        'SELECT r.loop_key, r.request_status, r.created_at AS request_created_at, '
        'v.review_status, v.reviewed_at, v.created_at AS review_created_at, '
        'v.review_reason, v.signed_pnl_raw, v.signed_pnl_amount '
        'FROM manager_v4_review_broadcast_requests r '
        'LEFT JOIN manager_v4_reviews v '
        'ON v.network = r.network AND v.chain_id = r.chain_id '
        'AND v.protocol = r.protocol AND v.scope_key = r.scope_key '
        'AND v.loop_key = r.loop_key'
    ).fetchall()
    now = datetime.now(timezone.utc)
    out: list[dict] = []
    for row in rows:
        item = dict(row)
        reviewed = parse(item.get('reviewed_at'))
        created = parse(item.get('request_created_at'))
        item['review_to_publish_seconds'] = (
            round((created - reviewed).total_seconds(), 1)
            if reviewed and created else None
        )
        item['request_age_seconds_now'] = (
            round((now - created).total_seconds(), 1) if created else None
        )
        item['review_age_seconds_now'] = (
            round((now - reviewed).total_seconds(), 1) if reviewed else None
        )
        out.append(item)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))

    spread = connection.execute(
        'SELECT reviewed_at, created_at FROM manager_v4_reviews '
        'WHERE reviewed_at IS NOT NULL ORDER BY reviewed_at DESC LIMIT 12'
    ).fetchall()
    print('--- newest review rows: reviewed_at vs created_at ---')
    for row in spread:
        reviewed = parse(row['reviewed_at'])
        created = parse(row['created_at'])
        lag = (created - reviewed).total_seconds() if reviewed and created else None
        print(row['reviewed_at'], '|', row['created_at'], '| lag', lag)
    connection.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
