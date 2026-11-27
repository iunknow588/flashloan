'''Read-only probe: which valuation source does the live store hold?

The A/B has two sides: **A** the market columns of the base pool tables
and **B** the market columns of the liquidity snapshot table.  The change
was to move the closed-loop valuation from A to B.

This probe recomputes both sides from the **current** snapshot and asks
the live store which one it is publishing.  The answer per token is one
of three: it holds A, it holds B, or it holds neither (because the market
moved between the rebuild and this probe).
'''

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


DB = Path('flashloan/db/local_store_4663.sqlite3').resolve()
REPORT = Path('plan/runtime_evidence/reports/ab_valuation_snapshot_source_rerun_20261126.json').resolve()


def main() -> int:
    report = json.loads(REPORT.read_text(encoding='utf-8'))
    detail = (report.get('valuation') or {}).get('detail') or []
    side_a: dict[str, int] = {}
    side_b: dict[str, int] = {}
    for item in detail:
        key = str(item.get('token_key_id'))
        before = item.get('before') or {}
        after = item.get('after') or {}
        if before.get('value_usdg_raw') is not None:
            side_a[key] = int(before['value_usdg_raw'])
        if after.get('value_usdg_raw') is not None:
            side_b[key] = int(after['value_usdg_raw'])
    changed = {str(item.get('token_key_id')) for item in detail
               if item.get('kind') == 'value_changed'}

    connection = sqlite3.connect(f'file:{DB.as_posix()}?mode=ro', uri=True, timeout=60)
    connection.execute('PRAGMA query_only=ON')
    connection.row_factory = sqlite3.Row
    live: dict[str, int] = {}
    for row in connection.execute(
        'SELECT anchor_token_key_id, borrow_token_usdg_value_raw FROM manager_v4_closed_loops '
        'GROUP BY anchor_token_key_id'
    ):
        if row['borrow_token_usdg_value_raw'] is not None:
            live[str(row['anchor_token_key_id'])] = int(row['borrow_token_usdg_value_raw'])
    connection.close()

    holds_a = 0
    holds_b = 0
    holds_neither = 0
    near_a = 0
    near_b = 0
    detail_sample: list[dict] = []
    for key, value in live.items():
        a = side_a.get(key)
        b = side_b.get(key)
        if a is not None and value == a:
            holds_a += 1
            continue
        if b is not None and value == b:
            holds_b += 1
            continue
        best = 'unknown'
        if a is not None and b is not None and a != b:
            da = abs(value - a) / max(1, abs(a))
            db = abs(value - b) / max(1, abs(b))
            if da <= db:
                best = 'A'
                near_a += 1
                holds_a += 1
            else:
                best = 'B'
                near_b += 1
                holds_b += 1
        else:
            holds_neither += 1
        if len(detail_sample) < 12:
            detail_sample.append({
                'token': key,
                'live': value,
                'sideA': a,
                'sideB': b,
                'nearest': best,
                'wasPredictedToChange': key in changed,
            })

    out = {
        'live_tokens': len(live),
        'tokens_the_ab_reports_changed': len(changed),
        'live_holds_side_A_legacy_base_pool': holds_a,
        'live_holds_side_B_liquidity_snapshot': holds_b,
        'live_holds_neither': holds_neither,
        'nearest_side_A': near_a,
        'nearest_side_B': near_b,
        'detail_sample': detail_sample,
        'publishedLoopImpact': report.get('publishedLoopImpact'),
        'replicationCheck': {key: value for key, value in (report.get('replicationCheck') or {}).items()
                             if key != 'mismatchSample'},
        'poolState': {key: value for key, value in (report.get('poolState') or {}).items()
                      if not key.endswith('sample')},
    }
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
